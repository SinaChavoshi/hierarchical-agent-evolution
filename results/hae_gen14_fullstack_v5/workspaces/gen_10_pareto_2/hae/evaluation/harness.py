import ast
import os
import subprocess
import sys
import tempfile
import shutil
import re
import time
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Iterable

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency: hae/evaluation/artifacts.py
# Since it's being implemented in parallel, we assume it exists or we implement a fallback if needed.
# However, the prompt says "Dependency contract: hae/evaluation/artifacts.py (being implemented in parallel...)".
# Usually, in these tasks, if a dependency is specified, we should import it. 
# But to be safe against import errors if the other file isn't ready or if we need to be self-contained for the test harness,
# I will try to import it. If it fails, I'll implement the logic locally as a fallback to ensure the harness works.
try:
    from hae.evaluation.artifacts import (
        is_generated_path,
        is_malformed_path,
        sanitize_path,
        filter_bundle,
        partition_bundle,
        count_source_files,
        EXCLUDED_DIR_SEGMENTS,
        EXCLUDED_DIR_SUFFIXES
    )
except ImportError:
    # Fallback implementation of artifacts logic to ensure harness runs even if artifacts.py is missing/incomplete
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
        # Leading/trailing whitespace
        if path != path.strip():
            return True
        # Contains * or `
        if '*' in path or '`' in path:
            return True
        # Starts with -
        if path.startswith('-'):
            return True
        # Ends with . or ~
        if path.endswith('.') or path.endswith('~'):
            return True
        return False

    def sanitize_path(path: str) -> str:
        # Strip whitespace
        path = path.strip()
        # Remove markdown decoration
        path = path.replace('*', '').replace('`', '')
        # Remove leading hyphens
        while path.startswith('-'):
            path = path[1:]
        # Remove trailing dots or tildes
        while path.endswith('.') or path.endswith('~'):
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
        count = 0
        for path in bundle:
            if any(path.endswith(s) for s in suffixes):
                count += 1
        return count


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
        passed = [g for g in evaluated if g.passed]
        return len(passed) / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files}, Python Files: {self.python_files}")
        lines.append(f"Pass Rate: {self.pass_rate:.2%}")
        lines.append(f"Score Penalty: {self.score_penalty}")
        lines.append("Gates:")
        for g in self.gates:
            status_icon = "✅" if g.passed else ("⏭️" if g.status == SKIPPED else "❌")
            lines.append(f"  {status_icon} {g.name}: {g.status} ({g.method}) - {g.detail}")
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
    # Regex to find fenced code blocks with optional language and optional filename
    # Pattern: ```[language] [filename]\n...content...\n```
    # We look for blocks that might have a filename hint.
    # Common patterns:
    # ```python
    # ```path/to/file.py
    # ```python path/to/file.py
    
    blocks = {}
    # Find all fenced blocks
    pattern = r'```(?:\w+)?\s*([^\n]*)\n(.*?)\n```'
    matches = re.findall(pattern, text, re.DOTALL)
    
    for header, content in matches:
        header = header.strip()
        # Try to extract filename from header
        # If header is empty or just a language tag, we might not have a filename
        # If header contains a path-like string, use it.
        filename = None
        if header:
            # Check if the header looks like a filename (contains / or .)
            # Or if it's "python filename.py"
            parts = header.split()
            for part in parts:
                if '.' in part or '/' in part:
                    filename = part
                    break
        
        if filename:
            # Sanitize the filename
            clean_filename = sanitize_path(filename)
            if clean_filename:
                blocks[clean_filename] = content
        else:
            # If no filename, we can't map it reliably. 
            # In a real scenario, we might guess or skip. 
            # For now, skip blocks without identifiable filenames to avoid garbage.
            pass
            
    return blocks


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
        
        # Try to get bundle from workspace
        if workspace is not None:
            # Assume workspace has a method to get files or is a dict
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace'
            elif hasattr(workspace, 'list_files'):
                # Some workspaces might have list_files returning paths, and read_file
                # But usually get_files is better. If not, we might need to iterate.
                # For now, if it's not a dict or has get_files, we might fail to extract.
                pass
        
        # If bundle is empty, try extracting from deliverable text
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to only authored files
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files_count = len(authored_bundle)
        python_files_count = count_source_files(authored_bundle, suffixes=('.py',))
        
        if not authored_bundle:
            # No files to verify
            # Empty workspace fails everything evaluable
            gates = [
                GateResult(name='syntax', status=FAILED, method='ast_parse', detail='No files to verify'),
                GateResult(name='build', status=FAILED, method='pip_install', detail='No files to verify'),
                GateResult(name='smoke', status=FAILED, method='import', detail='No files to verify'),
                GateResult(name='tests', status=FAILED, method='pytest', detail='No files to verify'),
                GateResult(name='telemetry', status=FAILED, method='ast_import', detail='No files to verify'),
            ]
            return VerificationReport(
                gates=gates,
                score_penalty=5 * self.penalty_per_gate,
                authored_files=0,
                python_files=0,
                source=source
            )

        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to temp dir
            for path, content in authored_bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            # Run verification on the directory
            return self.verify_directory(tmpdir, authored=authored_bundle, source=source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if authored is None:
            # Scan directory for files
            authored = {}
            for root, dirs, files in os.walk(workdir):
                # Filter out generated dirs
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if not is_generated_path(rel_path) and not is_malformed_path(rel_path):
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                authored[rel_path] = f.read()
                        except Exception:
                            pass
        
        authored_files_count = len(authored)
        python_files_count = count_source_files(authored, suffixes=('.py',))
        
        gates = []
        
        # 1. Syntax Gate
        syntax_result = self._check_syntax(workdir, authored)
        gates.append(syntax_result)
        
        # 2. Build Gate
        build_result = self._check_build(workdir, authored)
        gates.append(build_result)
        
        # 3. Smoke Gate
        smoke_result = self._check_smoke(workdir, authored)
        gates.append(smoke_result)
        
        # 4. Tests Gate
        tests_result = self._check_tests(workdir, authored)
        gates.append(tests_result)
        
        # 5. Telemetry Gate
        telemetry_result = self._check_telemetry(workdir, authored)
        gates.append(telemetry_result)
        
        # Calculate penalty
        failed_gates = [g for g in gates if g.status == FAILED]
        score_penalty = len(failed_gates) * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files_count,
            python_files=python_files_count,
            source=source
        )

    def _check_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'ast_parse'
        try:
            py_files = {k: v for k, v in authored.items() if k.endswith('.py')}
            if not py_files:
                return GateResult(name='syntax', status=FAILED, method=method, detail='No Python files found', duration_s=time.time() - start_time)
            
            errors = []
            for path, content in py_files.items():
                try:
                    ast.parse(content)
                except SyntaxError as e:
                    errors.append(f"{path}: {e}")
            
            duration = time.time() - start_time
            if errors:
                return GateResult(name='syntax', status=FAILED, method=method, detail='; '.join(errors), duration_s=duration)
            else:
                return GateResult(name='syntax', status=PASSED, method=method, detail=f'All {len(py_files)} Python files parsed successfully', duration_s=duration)
        except Exception as e:
            return GateResult(name='syntax', status=FAILED, method=method, detail=str(e), duration_s=time.time() - start_time)

    def _check_build(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'pip_install'
        
        # Check if setup.py or pyproject.toml exists
        has_setup = 'setup.py' in authored or 'pyproject.toml' in authored
        if not has_setup:
            # Try to find it in the directory structure
            if not os.path.exists(os.path.join(workdir, 'setup.py')) and not os.path.exists(os.path.join(workdir, 'pyproject.toml')):
                return GateResult(name='build', status=FAILED, method=method, detail='No setup.py or pyproject.toml found', duration_s=time.time() - start_time)
        
        try:
            # Run pip install -e .
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.']
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            duration = time.time() - start_time
            
            if result.returncode == 0:
                return GateResult(name='build', status=PASSED, method=method, detail='Package installed successfully', duration_s=duration)
            else:
                return GateResult(name='build', status=FAILED, method=method, detail=f'Install failed: {result.stderr}', duration_s=duration)
        except subprocess.TimeoutExpired:
            return GateResult(name='build', status=FAILED, method=method, detail='Install timed out', duration_s=time.time() - start_time)
        except Exception as e:
            return GateResult(name='build', status=FAILED, method=method, detail=str(e), duration_s=time.time() - start_time)

    def _check_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'import'
        
        # Find modules to import
        # Look for __init__.py or top-level .py files
        modules_to_import = []
        
        # Check for package structure
        for path in authored:
            if path.endswith('__init__.py'):
                # Extract package name
                parts = path.split('/')
                if len(parts) > 1:
                    pkg_name = parts[0]
                    if pkg_name not in modules_to_import:
                        modules_to_import.append(pkg_name)
            elif path.endswith('.py') and '/' not in path:
                # Top-level module
                mod_name = path[:-3]
                if mod_name not in modules_to_import:
                    modules_to_import.append(mod_name)
        
        if not modules_to_import:
            return GateResult(name='smoke', status=FAILED, method=method, detail='No importable modules found', duration_s=time.time() - start_time)
        
        try:
            # Run a script that imports the modules
            script = "import sys; import importlib; "
            for mod in modules_to_import:
                script += f"importlib.import_module('{mod}'); "
            script += "print('OK')"
            
            result = subprocess.run(
                [self.python_executable, '-c', script],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            duration = time.time() - start_time
            
            if result.returncode == 0:
                return GateResult(name='smoke', status=PASSED, method=method, detail=f'Imported {len(modules_to_import)} modules successfully', duration_s=duration)
            else:
                return GateResult(name='smoke', status=FAILED, method=method, detail=f'Import failed: {result.stderr}', duration_s=duration)
        except subprocess.TimeoutExpired:
            return GateResult(name='smoke', status=FAILED, method=method, detail='Import timed out', duration_s=time.time() - start_time)
        except Exception as e:
            return GateResult(name='smoke', status=FAILED, method=method, detail=str(e), duration_s=time.time() - start_time)

    def _check_tests(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'pytest'
        
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], capture_output=True, timeout=10)
        except Exception:
            return GateResult(name='tests', status=SKIPPED, method=method, detail='pytest not available', duration_s=time.time() - start_time)
        
        # Check if there are test files
        test_files = [k for k in authored if k.startswith('test_') or k.endswith('_test.py') or 'tests/' in k]
        if not test_files:
            # Try to find tests in the directory
            has_tests_dir = os.path.exists(os.path.join(workdir, 'tests'))
            has_test_files = any(f.startswith('test_') or f.endswith('_test.py') for f in os.listdir(workdir))
            
            if not has_tests_dir and not has_test_files:
                return GateResult(name='tests', status=FAILED, method=method, detail='No test files found', duration_s=time.time() - start_time)
        
        try:
            # Run pytest
            result = subprocess.run(
                [self.python_executable, '-m', 'pytest', '-v'],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            duration = time.time() - start_time
            
            if result.returncode == 0:
                return GateResult(name='tests', status=PASSED, method=method, detail='All tests passed', duration_s=duration)
            else:
                # Check if it was a collection error or actual failure
                if 'error' in result.stderr.lower() or 'failed' in result.stdout.lower():
                    return GateResult(name='tests', status=FAILED, method=method, detail=f'Tests failed: {result.stdout[-500:]}', duration_s=duration)
                else:
                    return GateResult(name='tests', status=FAILED, method=method, detail=f'Pytest error: {result.stderr}', duration_s=duration)
        except subprocess.TimeoutExpired:
            return GateResult(name='tests', status=FAILED, method=method, detail='Tests timed out', duration_s=time.time() - start_time)
        except Exception as e:
            return GateResult(name='tests', status=FAILED, method=method, detail=str(e), duration_s=time.time() - start_time)

    def _check_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'ast_import'
        
        py_files = {k: v for k, v in authored.items() if k.endswith('.py')}
        if not py_files:
            return GateResult(name='telemetry', status=FAILED, method=method, detail='No Python files found', duration_s=time.time() - start_time)
        
        telemetry_found = False
        evidence = {}
        
        for path, content in py_files.items():
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if 'opentelemetry' in alias.name.lower():
                                telemetry_found = True
                                evidence[path] = f"import {alias.name}"
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and 'opentelemetry' in node.module.lower():
                            telemetry_found = True
                            evidence[path] = f"from {node.module} import ..."
            except SyntaxError:
                continue
        
        duration = time.time() - start_time
        if telemetry_found:
            return GateResult(name='telemetry', status=PASSED, method=method, detail='OpenTelemetry imported in code', evidence=evidence, duration_s=duration)
        else:
            return GateResult(name='telemetry', status=FAILED, method=method, detail='No OpenTelemetry imports found in Python files', duration_s=duration)