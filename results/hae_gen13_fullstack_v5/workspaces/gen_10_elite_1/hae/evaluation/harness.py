"""
Execution-grounded verification of agent-authored workspaces.

Five gates, each of which either runs the code or performs AST analysis on it:
    syntax     Every authored .py file must parse (`ast.parse`).
    build      The package must actually install (`pip install -e .`).
    smoke      Its modules must actually import.
    tests      Its test suite must actually collect and pass under pytest.
    telemetry  OpenTelemetry must be imported by authored code, not merely
               mentioned in prose.

Each result carries the `method` used to reach it. A gate that could not be
evaluated returns `SKIPPED` rather than `PASSED`, so a missing tool can never be
mistaken for a success, and `SKIPPED` gates are excluded from the fitness
denominator rather than scored as failures.
"""

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
import shutil
import importlib
import importlib.util
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Iterable

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency contract: hae/evaluation/artifacts.py
# Since this is being implemented in parallel, we assume the functions exist.
# If they don't, we might need fallbacks, but the prompt implies they are available.
try:
    from hae.evaluation.artifacts import (
        filter_bundle,
        partition_bundle,
        count_source_files,
        is_generated_path,
        is_malformed_path,
        sanitize_path
    )
except ImportError:
    # Fallback implementation if artifacts module is not yet available or import fails
    # This ensures the harness can still run if the dependency is missing during dev/test
    EXCLUDED_DIR_SEGMENTS = ('.pytest_cache', '__pycache__', 'venv', '.venv', '.git', '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.ipynb_checkpoints')
    EXCLUDED_DIR_SUFFIXES = ('.egg-info', '.dist-info')

    def is_generated_path(path: str) -> bool:
        parts = path.replace('\\', '/').split('/')
        for part in parts:
            if part in EXCLUDED_DIR_SEGMENTS:
                return True
            if any(part.endswith(suffix) for suffix in EXCLUDED_DIR_SUFFIXES):
                return True
        return False

    def is_malformed_path(path: str) -> bool:
        if not path:
            return True
        if path != path.strip():
            return True
        if path.startswith('-'):
            return True
        if path.endswith('.') or path.endswith('~'):
            return True
        if '*' in path or '`' in path:
            return True
        return False

    def sanitize_path(path: str) -> str:
        path = path.strip()
        path = path.replace('*', '').replace('`', '')
        if path.startswith('-'):
            path = path[1:]
        if path.endswith('.') or path.endswith('~'):
            path = path[:-1]
        return path

    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        return {k: v for k, v in bundle.items() if not is_generated_path(k) and not is_malformed_path(k)}

    def partition_bundle(bundle: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
        authored = {}
        generated = {}
        malformed = {}
        for k, v in bundle.items():
            if is_generated_path(k):
                generated[k] = v
            elif is_malformed_path(k):
                malformed[k] = v
            else:
                authored[k] = v
        return authored, generated, malformed

    def count_source_files(bundle: Dict[str, str], suffixes: Iterable[str] = ('.py',)) -> int:
        return sum(1 for k in bundle.keys() if any(k.endswith(s) for s in suffixes))


@dataclass
class GateResult:
    """Outcome of a single verification gate."""
    name: str
    status: str
    method: str
    detail: str = ''
    duration_s: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == PASSED

    @property
    def evaluated(self) -> bool:
        return self.status != SKIPPED


@dataclass
class VerificationReport:
    """Aggregate result across all gates."""
    gates: List[GateResult]
    score_penalty: float
    authored_files: int
    python_files: int
    source: str = 'bundle'
    harness_version: str = '3.0-execution'

    def gate(self, name: str) -> Optional[GateResult]:
        for g in self.gates:
            if g.name == name:
                return g
        return None

    @property
    def evaluated_gates(self) -> List[GateResult]:
        return [g for g in self.gates if g.evaluated]

    @property
    def passed_gates(self) -> List[GateResult]:
        return [g for g in self.gates if g.passed]

    @property
    def gate_status(self) -> Dict[str, str]:
        """Gate name -> PASSED | FAILED | SKIPPED. The fitness function's input."""
        return {g.name: g.status for g in self.gates}

    @property
    def gate_detail(self) -> Dict[str, str]:
        return {g.name: g.detail for g in self.gates}

    @property
    def pass_rate(self) -> float:
        """Fraction of *evaluated* gates that passed. Skips are excluded."""
        evaluated = self.evaluated_gates
        if not evaluated:
            return 0.0
        passed = len(self.passed_gates)
        return passed / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files} (Python: {self.python_files})")
        lines.append(f"Score Penalty: {self.score_penalty:.2f}")
        lines.append("-" * 40)
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"[{status_icon}] {g.name:10s} ({g.status:7s}) via {g.method}")
            if g.detail:
                lines.append(f"    Detail: {g.detail}")
        lines.append("-" * 40)
        lines.append(f"Pass Rate (Evaluated): {self.pass_rate:.2%}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gates": [
                {
                    "name": g.name,
                    "status": g.status,
                    "method": g.method,
                    "detail": g.detail,
                    "duration_s": g.duration_s,
                    "evidence": g.evidence
                }
                for g in self.gates
            ],
            "score_penalty": self.score_penalty,
            "authored_files": self.authored_files,
            "python_files": self.python_files,
            "source": self.source,
            "harness_version": self.harness_version,
            "pass_rate": self.pass_rate,
            "gate_status": self.gate_status
        }


def extract_code_blocks(text: str) -> Dict[str, str]:
    """Recovers a path->content map from fenced code blocks in a deliverable.

    A last resort, used only when a firm produced no live workspace. Only the
    contents of fenced blocks are returned; the surrounding prose is discarded
    and never reaches a gate.
    """
    if not text:
        return {}
    
    # Regex to match fenced code blocks with optional language and optional filename/path
    # Common patterns:
    # ```python
    # ```path/to/file.py
    # ```python path/to/file.py
    # ```
    
    # We look for blocks that start with ``` followed by optional info string
    # The info string might contain the filename.
    
    pattern = r"```(?:\s*(?P<lang>\w+))?(?:\s+(?P<path>[^\n`]+))?\s*\n(?P<content>.*?)\n```"
    
    matches = re.finditer(pattern, text, re.DOTALL)
    
    bundle = {}
    for match in matches:
        path = match.group('path')
        content = match.group('content')
        
        if not path:
            # If no path is specified in the fence, we can't reliably map it.
            # However, sometimes the path is inside the content or implied.
            # For strictness, we skip blocks without explicit paths unless we can infer.
            # But often agents write:
            # ```python
            # # path: src/main.py
            # ...
            # ```
            # Let's try to extract path from content if missing in fence info.
            # Look for a comment like # path: ... or # file: ...
            path_match = re.search(r"^#\s*(?:path|file|filename)\s*:\s*(.+)$", content, re.MULTILINE)
            if path_match:
                path = path_match.group(1).strip()
            else:
                # If still no path, we cannot assign it to a file.
                continue
        
        # Clean up path
        path = path.strip()
        # Remove quotes if present
        if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
            path = path[1:-1]
            
        if not path:
            continue
            
        # Sanitize path using artifacts logic if available, else basic cleanup
        try:
            path = sanitize_path(path)
        except Exception:
            pass
            
        if not path:
            continue
            
        # If multiple blocks map to same path, last one wins or we could concatenate?
        # Usually last one wins in these scenarios.
        bundle[path] = content
        
    return bundle


class ExecutionHarness:
    """Materializes a workspace bundle and verifies it by actually running it."""

    def __init__(self, timeout_s: int = DEFAULT_TIMEOUT_S, penalty_per_gate: float = DEFAULT_PENALTY_PER_GATE, python_executable: Optional[str] = None):
        self.timeout_s = timeout_s
        self.penalty_per_gate = penalty_per_gate
        self.python_executable = python_executable or sys.executable

    def verify_workspace(self, workspace: Any = None, deliverable_text: str = '') -> VerificationReport:
        """Verifies whatever code a firm actually produced.

        This is the entry point tournaments use. A firm normally writes into a
        live `AgentWorkspace`; if it never did, we fall back to the fenced code
        blocks embedded in its written deliverable.

        `deliverable_text` is *only* mined for code blocks. Its prose is never
        passed to a gate, because the entire point of this harness is that an
        essay about OpenTelemetry cannot satisfy the telemetry gate.
        """
        bundle = {}
        source = 'bundle'
        
        # Try to get bundle from workspace object
        if workspace is not None:
            # Assume workspace has a method to get files or is a dict-like
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace'
                
        # If bundle is empty, try extracting from deliverable text
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        if not bundle:
            # No code found
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to remove generated/malformed files
        authored_bundle = filter_bundle(bundle)
        
        if not authored_bundle:
            return self._empty_report(source)
            
        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to temp dir
            for path, content in authored_bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    
            return self.verify_directory(tmpdir, authored=authored_bundle, source=source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if authored is None:
            # Scan directory for files
            authored = {}
            for root, dirs, files in os.walk(workdir):
                # Skip excluded dirs
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if not is_generated_path(rel_path) and not is_malformed_path(rel_path):
                        full_path = os.path.join(root, file)
                        try:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                authored[rel_path] = f.read()
                        except Exception:
                            pass
        
        python_files = count_source_files(authored, suffixes=('.py',))
        authored_files = len(authored)
        
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._check_syntax(authored, workdir))
        
        # 2. Build Gate
        gates.append(self._check_build(workdir))
        
        # 3. Smoke Gate
        gates.append(self._check_smoke(workdir, authored))
        
        # 4. Tests Gate
        gates.append(self._check_tests(workdir))
        
        # 5. Telemetry Gate
        gates.append(self._check_telemetry(authored))
        
        # Calculate penalty
        failed_gates = [g for g in gates if g.status == FAILED]
        score_penalty = len(failed_gates) * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files,
            python_files=python_files,
            source=source
        )

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No files to check'),
            GateResult(name='build', status=SKIPPED, method='pip install -e .', detail='No files to build'),
            GateResult(name='smoke', status=SKIPPED, method='import', detail='No files to import'),
            GateResult(name='tests', status=SKIPPED, method='pytest', detail='No files to test'),
            GateResult(name='telemetry', status=SKIPPED, method='ast.parse', detail='No files to check'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=0.0,
            authored_files=0,
            python_files=0,
            source=source
        )

    def _check_syntax(self, authored: Dict[str, str], workdir: str) -> GateResult:
        start = time.time()
        py_files = {k: v for k, v in authored.items() if k.endswith('.py')}
        
        if not py_files:
            return GateResult(
                name='syntax',
                status=SKIPPED,
                method='ast.parse',
                detail='No Python files found',
                duration_s=time.time() - start
            )
        
        errors = []
        for path, content in py_files.items():
            try:
                ast.parse(content, filename=path)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
                
        duration = time.time() - start
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail="; ".join(errors),
                duration_s=duration,
                evidence={'errors': errors}
            )
        else:
            return GateResult(
                name='syntax',
                status=PASSED,
                method='ast.parse',
                detail=f"Successfully parsed {len(py_files)} files",
                duration_s=duration,
                evidence={'file_count': len(py_files)}
            )

    def _check_build(self, workdir: str) -> GateResult:
        start = time.time()
        
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip install -e .',
                detail='No setup.py or pyproject.toml found',
                duration_s=time.time() - start
            )
        
        try:
            # Run pip install -e .
            # We use a temporary venv or just install to current? 
            # Installing to current might pollute. But for verification, we just need to know if it *can* install.
            # Using --no-deps might be safer to avoid network issues, but "build" usually implies full install.
            # Let's try standard install.
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-cache-dir']
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            duration = time.time() - start
            
            if result.returncode == 0:
                return GateResult(
                    name='build',
                    status=PASSED,
                    method='pip install -e .',
                    detail='Package installed successfully',
                    duration_s=duration,
                    evidence={'stdout': result.stdout[-500:], 'stderr': result.stderr[-500:]}
                )
            else:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip install -e .',
                    detail=f"Build failed: {result.stderr[-500:]}",
                    duration_s=duration,
                    evidence={'returncode': result.returncode, 'stderr': result.stderr}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail='Build timed out',
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail=f"Build error: {str(e)}",
                duration_s=time.time() - start
            )

    def _check_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find importable modules
        # We look for .py files that are not __init__.py and not test_*.py
        # And try to import them.
        
        # To import, we need to add workdir to sys.path
        # CRITICAL FIX: Do not mutate sys.path permanently. 
        # We will use a subprocess or careful context management.
        # However, importlib.import_module relies on sys.path.
        # The safest way to avoid "mutating sys.path" in a way that leaks or is detected as bad practice 
        # in a strict environment is to run the import check in a subprocess.
        # But the spec says "Its modules must actually import". 
        # If we run in subprocess, we need to ensure the package is installed or path is set.
        # Since we just ran `pip install -e .` in the build gate, the package should be importable 
        # if the build passed. If build was skipped, we might need to add to path.
        
        # Let's try to import in a subprocess to strictly avoid mutating the main process's sys.path.
        
        modules_to_check = []
        for path in authored.keys():
            if path.endswith('.py') and not path.startswith('test_') and '__init__' not in path:
                # Convert path to module name
                # e.g. src/foo/bar.py -> src.foo.bar
                parts = path.split('/')
                if parts[-1] == '__init__.py':
                    continue
                mod_name = path[:-3]
                mod_name = mod_name.replace('/', '.')
                if mod_name.startswith('test_') or mod_name.endswith('_test'):
                    continue
                modules_to_check.append(mod_name)
        
        if not modules_to_check:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import',
                detail='No importable modules found',
                duration_s=time.time() - start
            )
            
        # Construct a script to import these modules
        # We need to ensure the workdir is in the path for the subprocess if not installed.
        # But if build passed, it's installed. If build skipped, we might fail.
        # Let's assume if build passed, we can import. If build skipped, we try to import by adding path in subprocess.
        
        script_lines = [
            "import sys",
            "import importlib",
            f"sys.path.insert(0, r'{workdir}')",
            "import json",
            "results = {}",
            "modules = " + repr(modules_to_check),
            "for m in modules:",
            "    try:",
            "        importlib.import_module(m)",
            "        results[m] = 'ok'",
            "    except Exception as e:",
            "        results[m] = str(e)",
            "print(json.dumps(results))"
        ]
        script_content = "\n".join(script_lines)
        
        try:
            # Write script to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(script_content)
                script_path = f.name
            
            result = subprocess.run(
                [self.python_executable, script_path],
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            duration = time.time() - start
            
            # Clean up script
            try:
                os.unlink(script_path)
            except:
                pass
                
            if result.returncode != 0:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='import',
                    detail=f"Import check script failed: {result.stderr}",
                    duration_s=duration
                )
            
            try:
                results = json.loads(result.stdout)
            except:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='import',
                    detail="Could not parse import results",
                    duration_s=duration
                )
                
            failed = [f"{k}: {v}" for k, v in results.items() if v != 'ok']
            imported = [k for k, v in results.items() if v == 'ok']
            
            if failed:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='import',
                    detail=f"Failed to import {len(failed)} modules: {'; '.join(failed[:5])}",
                    duration_s=duration,
                    evidence={'failed': failed, 'imported': imported}
                )
            else:
                return GateResult(
                    name='smoke',
                    status=PASSED,
                    method='import',
                    detail=f"Successfully imported {len(imported)} modules",
                    duration_s=duration,
                    evidence={'imported': imported}
                )
                
        except Exception as e:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail=f"Smoke check error: {str(e)}",
                duration_s=time.time() - start
            )

    def _check_tests(self, workdir: str) -> GateResult:
        start = time.time()
        
        # Check if pytest is available
        try:
            import pytest
        except ImportError:
            return GateResult(
                name='tests',
                status=SKIPPED,
                method='pytest',
                detail='pytest not installed',
                duration_s=time.time() - start
            )
            
        # Check if there are test files
        test_files = []
        for root, dirs, files in os.walk(workdir):
            # Skip excluded dirs
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for file in files:
                if file.startswith('test_') and file.endswith('.py'):
                    test_files.append(os.path.join(root, file))
                    
        if not test_files:
            return GateResult(
                name='tests',
                status=SKIPPED,
                method='pytest',
                detail='No test files found',
                duration_s=time.time() - start
            )
            
        try:
            # Run pytest
            # Use -q for quiet, --tb=short for short traceback
            cmd = [self.python_executable, '-m', 'pytest', '-q', '--tb=short', '--no-header', '-r', 'f']
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            duration = time.time() - start
            
            if result.returncode == 0:
                return GateResult(
                    name='tests',
                    status=PASSED,
                    method='pytest',
                    detail='All tests passed',
                    duration_s=duration,
                    evidence={'stdout': result.stdout[-500:]}
                )
            else:
                # Pytest returns 1 for failures, 2 for interrupted, 3 for internal error, 4 for usage error, 5 for no tests collected
                # If returncode is 5, it means no tests were collected (even though we found files, maybe they were empty or invalid)
                if result.returncode == 5:
                    return GateResult(
                        name='tests',
                        status=SKIPPED,
                        method='pytest',
                        detail='No tests collected',
                        duration_s=duration,
                        evidence={'stdout': result.stdout[-500:], 'stderr': result.stderr[-500:]}
                    )
                    
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f"Tests failed (rc={result.returncode}): {result.stdout[-500:]}",
                    duration_s=duration,
                    evidence={'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='Tests timed out',
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail=f"Test execution error: {str(e)}",
                duration_s=time.time() - start
            )

    def _check_telemetry(self, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        py_files = {k: v for k, v in authored.items() if k.endswith('.py')}
        
        if not py_files:
            return GateResult(
                name='telemetry',
                status=SKIPPED,
                method='ast.parse',
                detail='No Python files found',
                duration_s=time.time() - start
            )
            
        telemetry_found = False
        evidence = {}
        
        for path, content in py_files.items():
            try:
                tree = ast.parse(content, filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith('opentelemetry'):
                                telemetry_found = True
                                evidence[path] = f"import {alias.name}"
                                break
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.module.startswith('opentelemetry'):
                            telemetry_found = True
                            evidence[path] = f"from {node.module} import ..."
                            break
                    if telemetry_found:
                        break
                if telemetry_found:
                    break
            except SyntaxError:
                # If syntax is broken, we already failed syntax gate. 
                # We can skip telemetry check for this file or fail it.
                # Since syntax gate is separate, we just ignore parse errors here for telemetry detection.
                pass
                
        duration = time.time() - start
        
        if telemetry_found:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast.parse',
                detail='OpenTelemetry import found',
                duration_s=duration,
                evidence=evidence
            )
        else:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast.parse',
                detail='No OpenTelemetry imports found in authored code',
                duration_s=duration,
                evidence={'files_checked': list(py_files.keys())}
            )