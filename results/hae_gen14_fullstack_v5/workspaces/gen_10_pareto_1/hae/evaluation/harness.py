import ast
import os
import re
import subprocess
import sys
import tempfile
import shutil
import time
import importlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Iterable

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency contract: hae/evaluation/artifacts.py
# Since it is being implemented in parallel, we assume the functions exist.
# If they don't, we provide a fallback implementation to ensure the harness works
# even if the dependency module is missing or incomplete during testing.
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
    # Fallback implementation for artifacts.py if not available
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
        if '*' in path or '`' in path:
            return True
        if path.startswith('-'):
            return True
        if path.endswith('.') or path.endswith('~'):
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
        passed_count = len(self.passed_gates)
        return passed_count / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files} (Python: {self.python_files})")
        lines.append(f"Score Penalty: {self.score_penalty:.2f}")
        lines.append("-" * 40)
        for g in self.gates:
            status_icon = "PASS" if g.passed else ("SKIP" if g.status == SKIPPED else "FAIL")
            lines.append(f"[{status_icon}] {g.name}: {g.detail}")
        lines.append("-" * 40)
        lines.append(f"Pass Rate: {self.pass_rate:.2%}")
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
            "pass_rate": self.pass_rate
        }


def extract_code_blocks(text: str) -> Dict[str, str]:
    """Recovers a path->content map from fenced code blocks in a deliverable.

    A last resort, used only when a firm produced no live workspace. Only the
    contents of fenced blocks are returned; the surrounding prose is discarded
    and never reaches a gate.
    """
    if not text:
        return {}
    
    # Regex to find fenced code blocks with optional language and filename
    # Matches: ```python filename.py ... ``` or ``` filename.py ... ```
    # We look for blocks that might contain a filename hint.
    # Common patterns:
    # ```python
    # path/to/file.py
    # ...
    # ```
    # Or sometimes the filename is in the fence info string: ```python path/to/file.py
    
    blocks = {}
    # Pattern 1: Fence info contains filename
    # ```lang path/to/file.py
    pattern1 = re.compile(r"```(?:\w+)?\s+([^\s`]+\.py)\s*\n(.*?)\n```", re.DOTALL)
    for match in pattern1.finditer(text):
        path = match.group(1).strip()
        content = match.group(2)
        if path:
            blocks[path] = content

    # Pattern 2: Filename is the first line of the block content
    # ```python
    # path/to/file.py
    # ...
    # ```
    pattern2 = re.compile(r"```(?:\w+)?\s*\n([^\n]+\.py)\n(.*?)\n```", re.DOTALL)
    for match in pattern2.finditer(text):
        path = match.group(1).strip()
        content = match.group(2)
        # Heuristic: if the first line looks like a path (contains / or .py) and isn't code
        if path.endswith('.py') and not path.startswith('import') and not path.startswith('def') and not path.startswith('class'):
            if path not in blocks:
                blocks[path] = content

    # Pattern 3: Generic fenced blocks where we might guess the path from context or just use a generic name
    # This is risky, but if no filename is found, we might skip or use a placeholder.
    # The spec says "Recovers a path->content map". If no path is identifiable, we can't really map it.
    # However, often agents write:
    # ```python
    # # file: path/to/file.py
    # ...
    # ```
    pattern3 = re.compile(r"```(?:\w+)?\s*\n(?:#\s*file:\s*([^\s]+\.py)\s*\n)?(.*?)\n```", re.DOTALL)
    for match in pattern3.finditer(text):
        path = match.group(1)
        content = match.group(2)
        if path:
            path = path.strip()
            if path not in blocks:
                blocks[path] = content

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

        if workspace is not None:
            # Assume workspace has a method to get files or is a dict-like object
            # Common patterns: workspace.files, workspace.list_files(), or workspace is a dict
            if hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace'
            elif hasattr(workspace, 'list_files'):
                # If it's a sandbox env, we might need to read contents
                # But usually verify_bundle expects path->content
                # Let's assume if it's a workspace object, we try to extract a bundle
                # If we can't, we fall back
                pass
            
            # If workspace is a dict, use it directly
            if isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace'
        
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'

        if not bundle:
            # No code found
            return self._empty_report(source)

        return self.verify_bundle(bundle, source=source)

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No files to parse'),
            GateResult(name='build', status=SKIPPED, method='pip install', detail='No files to build'),
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

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to only authored files
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files = len(authored_bundle)
        python_files = count_source_files(authored_bundle, suffixes=('.py',))

        if authored_files == 0:
            return self._empty_report(source)

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
            # Scan directory
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
        
        authored_files = len(authored)
        python_files = count_source_files(authored, suffixes=('.py',))

        gates = []

        # 1. Syntax Gate
        gates.append(self._check_syntax(workdir, authored))

        # 2. Build Gate
        gates.append(self._check_build(workdir))

        # 3. Smoke Gate
        gates.append(self._check_smoke(workdir, authored))

        # 4. Tests Gate
        gates.append(self._check_tests(workdir))

        # 5. Telemetry Gate
        gates.append(self._check_telemetry(workdir, authored))

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

    def _check_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'ast.parse'
        try:
            py_files = [p for p in authored if p.endswith('.py')]
            if not py_files:
                return GateResult(name='syntax', status=SKIPPED, method=method, detail='No Python files found', duration_s=time.time()-start)
            
            errors = []
            for path in py_files:
                full_path = os.path.join(workdir, path)
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        source = f.read()
                    ast.parse(source)
                except SyntaxError as e:
                    errors.append(f"{path}: {e}")
            
            duration = time.time() - start
            if errors:
                return GateResult(name='syntax', status=FAILED, method=method, detail='; '.join(errors), duration_s=duration)
            else:
                return GateResult(name='syntax', status=PASSED, method=method, detail=f'All {len(py_files)} Python files parsed successfully', duration_s=duration)
        except Exception as e:
            return GateResult(name='syntax', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _check_build(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'pip install -e .'
        try:
            # Check if setup.py or pyproject.toml exists
            has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
            has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
            
            if not has_setup and not has_pyproject:
                return GateResult(name='build', status=SKIPPED, method=method, detail='No setup.py or pyproject.toml found', duration_s=time.time()-start)
            
            # Run pip install
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.']
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start
            if result.returncode == 0:
                return GateResult(name='build', status=PASSED, method=method, detail='Package installed successfully', duration_s=duration)
            else:
                return GateResult(name='build', status=FAILED, method=method, detail=f'Pip install failed: {result.stderr}', duration_s=duration)
        except subprocess.TimeoutExpired:
            return GateResult(name='build', status=FAILED, method=method, detail='Build timed out', duration_s=time.time()-start)
        except Exception as e:
            return GateResult(name='build', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _check_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'import'
        try:
            # Find modules to import
            # We look for .py files that are not __init__.py and not test_*.py
            # And try to import them
            
            modules_to_import = []
            for path in authored:
                if path.endswith('.py') and not path.endswith('__init__.py') and not os.path.basename(path).startswith('test_'):
                    # Convert path to module name
                    # e.g., src/agent_org/routing.py -> src.agent_org.routing
                    # But we need to handle the package structure.
                    # If there's an __init__.py in the directory, it's a package.
                    parts = path.replace('\\', '/').split('/')
                    module_name = '.'.join(parts[:-1] + [parts[-1][:-3]])
                    modules_to_import.append(module_name)
            
            if not modules_to_import:
                return GateResult(name='smoke', status=SKIPPED, method=method, detail='No importable modules found', duration_s=time.time()-start)
            
            # Add workdir to sys.path temporarily
            old_path = sys.path.copy()
            sys.path.insert(0, workdir)
            
            errors = []
            imported_count = 0
            for module_name in modules_to_import:
                try:
                    importlib.import_module(module_name)
                    imported_count += 1
                except Exception as e:
                    errors.append(f"{module_name}: {e}")
            
            sys.path = old_path
            
            duration = time.time() - start
            if errors:
                return GateResult(name='smoke', status=FAILED, method=method, detail='; '.join(errors), duration_s=duration)
            else:
                return GateResult(name='smoke', status=PASSED, method=method, detail=f'Imported {imported_count} modules successfully', duration_s=duration)
        except Exception as e:
            return GateResult(name='smoke', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _check_tests(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'pytest'
        try:
            # Check if pytest is available
            try:
                import pytest
            except ImportError:
                return GateResult(name='tests', status=SKIPPED, method=method, detail='pytest not installed', duration_s=time.time()-start)
            
            # Run pytest
            cmd = [self.python_executable, '-m', 'pytest', '-v', '--tb=short']
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start
            if result.returncode == 0:
                return GateResult(name='tests', status=PASSED, method=method, detail='All tests passed', duration_s=duration)
            elif result.returncode == 5: # No tests collected
                return GateResult(name='tests', status=SKIPPED, method=method, detail='No tests collected', duration_s=duration)
            else:
                return GateResult(name='tests', status=FAILED, method=method, detail=f'Tests failed: {result.stdout[-500:]}', duration_s=duration)
        except subprocess.TimeoutExpired:
            return GateResult(name='tests', status=FAILED, method=method, detail='Tests timed out', duration_s=time.time()-start)
        except Exception as e:
            return GateResult(name='tests', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _check_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'ast.parse'
        try:
            py_files = [p for p in authored if p.endswith('.py')]
            if not py_files:
                return GateResult(name='telemetry', status=SKIPPED, method=method, detail='No Python files found', duration_s=time.time()-start)
            
            telemetry_found = False
            for path in py_files:
                full_path = os.path.join(workdir, path)
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        source = f.read()
                    tree = ast.parse(source)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if 'opentelemetry' in alias.name:
                                    telemetry_found = True
                                    break
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and 'opentelemetry' in node.module:
                                telemetry_found = True
                                break
                    if telemetry_found:
                        break
                except SyntaxError:
                    continue
            
            duration = time.time() - start
            if telemetry_found:
                return GateResult(name='telemetry', status=PASSED, method=method, detail='OpenTelemetry import found in code', duration_s=duration)
            else:
                return GateResult(name='telemetry', status=FAILED, method=method, detail='No OpenTelemetry import found in code', duration_s=duration)
        except Exception as e:
            return GateResult(name='telemetry', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)