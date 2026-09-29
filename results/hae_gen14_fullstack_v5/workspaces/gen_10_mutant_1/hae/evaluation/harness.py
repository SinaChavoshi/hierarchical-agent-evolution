import ast
import os
import re
import subprocess
import sys
import tempfile
import shutil
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

# Import artifacts module if available, otherwise define fallbacks to ensure standalone execution
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
    # Fallback definitions if artifacts.py is not yet available or import fails
    EXCLUDED_DIR_SEGMENTS = ('.pytest_cache', '__pycache__', 'venv', '.venv', '.git', '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.ipynb_checkpoints')
    EXCLUDED_DIR_SUFFIXES = ('.egg-info', '.dist-info')

    def is_generated_path(path: str) -> bool:
        parts = path.replace('\\', '/').split('/')
        for part in parts:
            if part in EXCLUDED_DIR_SEGMENTS:
                return True
            if any(part.endswith(s) for s in EXCLUDED_DIR_SUFFIXES):
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
            if not is_generated_path(path) and not is_malformed_path(path):
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
        passed = len(self.passed_gates)
        return passed / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files}, Python Files: {self.python_files}")
        lines.append(f"Pass Rate: {self.pass_rate:.2%}")
        lines.append(f"Score Penalty: {self.score_penalty}")
        lines.append("-" * 40)
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"[{status_icon}] {g.name}: {g.status} ({g.method})")
            if g.detail:
                lines.append(f"    Detail: {g.detail}")
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
    
    # Regex to find fenced code blocks with optional language and optional filename hint
    # Pattern: ```[language] [filename]\ncontent\n```
    # We look for blocks that might contain a filename in the info string or just assume sequential naming if not present.
    # However, the spec implies a path->content map. Usually, agents write:
    # ```python
    # # path: src/main.py
    # ...
    # ```
    # OR
    # ```python src/main.py
    # ...
    # ```
    
    blocks = {}
    # Match ```lang filename ... ``` or ```lang ... ```
    # We'll try to extract filename from the info string if it looks like a path, or from a comment inside.
    
    pattern = r'```(\w+)?\s*([^\n]*)\n(.*?)```'
    matches = re.finditer(pattern, text, re.DOTALL)
    
    counter = 0
    for match in matches:
        lang = match.group(1) or ''
        info = match.group(2).strip()
        content = match.group(3)
        
        filename = None
        
        # Try to extract filename from info string (e.g., "python src/main.py")
        if info:
            parts = info.split()
            for part in parts:
                if '.' in part and not part.startswith('-'):
                    # Heuristic: if it looks like a file path
                    filename = part
                    break
        
        # If no filename in info, try to find a comment like "# path: ..." or "# file: ..." in the first few lines
        if not filename:
            lines = content.split('\n')
            for line in lines[:5]:
                line_stripped = line.strip()
                if line_stripped.startswith('#'):
                    # Check for path: or file:
                    if 'path:' in line_stripped.lower():
                        filename = line_stripped.split(':', 1)[1].strip()
                        break
                    elif 'file:' in line_stripped.lower():
                        filename = line_stripped.split(':', 1)[1].strip()
                        break
        
        if not filename:
            # Fallback: generate a name based on language and counter
            ext = '.py' if lang == 'python' or lang == 'py' else '.txt'
            filename = f"extracted_{counter}{ext}"
            counter += 1
            
        # Sanitize the filename
        filename = sanitize_path(filename)
        
        if filename:
            blocks[filename] = content
            
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
        bundle = None
        source = 'bundle'
        
        # Try to get bundle from workspace object
        if workspace is not None:
            # Assume workspace has a method to get files or is a dict-like object
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace_dict'
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace_attr'
        
        # Fallback to deliverable text
        if not bundle:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        if not bundle:
            # Empty bundle
            return self._create_empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def _create_empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=FAILED, method='ast_parse', detail='No files to check'),
            GateResult(name='build', status=FAILED, method='pip_install', detail='No files to build'),
            GateResult(name='smoke', status=FAILED, method='import', detail='No files to import'),
            GateResult(name='tests', status=FAILED, method='pytest', detail='No files to test'),
            GateResult(name='telemetry', status=FAILED, method='ast_import', detail='No files to check'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=5 * self.penalty_per_gate,
            authored_files=0,
            python_files=0,
            source=source
        )

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to only authored files
        authored_bundle = filter_bundle(bundle)
        
        if not authored_bundle:
            return self._create_empty_report(source)
            
        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files
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
        
        python_files = [p for p in authored if p.endswith('.py')]
        authored_files_count = len(authored)
        python_files_count = len(python_files)
        
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._check_syntax(workdir, python_files))
        
        # 2. Build Gate
        gates.append(self._check_build(workdir))
        
        # 3. Smoke Gate
        gates.append(self._check_smoke(workdir, python_files))
        
        # 4. Tests Gate
        gates.append(self._check_tests(workdir))
        
        # 5. Telemetry Gate
        gates.append(self._check_telemetry(workdir, python_files))
        
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

    def _check_syntax(self, workdir: str, python_files: List[str]) -> GateResult:
        start_time = time.time()
        errors = []
        for rel_path in python_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    code = f.read()
                ast.parse(code)
            except SyntaxError as e:
                errors.append(f"{rel_path}: {e}")
            except Exception as e:
                errors.append(f"{rel_path}: {e}")
        
        duration = time.time() - start_time
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast_parse',
                detail='; '.join(errors[:5]),
                duration_s=duration,
                evidence={'errors': errors}
            )
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast_parse',
            detail=f"All {len(python_files)} python files parsed successfully.",
            duration_s=duration
        )

    def _check_build(self, workdir: str) -> GateResult:
        start_time = time.time()
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip_install',
                detail='No setup.py or pyproject.toml found.',
                duration_s=time.time() - start_time
            )
        
        try:
            # Run pip install -e .
            result = subprocess.run(
                [self.python_executable, '-m', 'pip', 'install', '-e', '.'],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            duration = time.time() - start_time
            if result.returncode == 0:
                return GateResult(
                    name='build',
                    status=PASSED,
                    method='pip_install',
                    detail='Package installed successfully.',
                    duration_s=duration
                )
            else:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip_install',
                    detail=f"Install failed: {result.stderr[:500]}",
                    duration_s=duration,
                    evidence={'stderr': result.stderr, 'stdout': result.stdout}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip_install',
                detail='Install timed out.',
                duration_s=time.time() - start_time
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip_install',
                detail=f"Error running install: {str(e)}",
                duration_s=time.time() - start_time
            )

    def _check_smoke(self, workdir: str, python_files: List[str]) -> GateResult:
        start_time = time.time()
        if not python_files:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='No python files to import.',
                duration_s=time.time() - start_time
            )
        
        # Try to import the main package or all modules
        # We'll try to import each module individually to see if any fail
        import_errors = []
        imported_count = 0
        
        # Add workdir to sys.path temporarily
        old_path = sys.path.copy()
        sys.path.insert(0, workdir)
        
        try:
            for rel_path in python_files:
                # Convert path to module name
                # e.g., src/main.py -> src.main
                module_name = rel_path.replace('/', '.').replace('\\', '.').replace('.py', '')
                # Handle __init__.py
                if module_name.endswith('.__init__'):
                    module_name = module_name[:-9]
                
                try:
                    __import__(module_name)
                    imported_count += 1
                except ImportError as e:
                    # Check if it's a missing dependency issue vs code issue
                    # If the error is about a missing module that isn't part of the workspace, it might be a dependency issue
                    # But for simplicity, we treat any import error as a failure unless it's clearly a missing external dep
                    # The spec says "uninstalled dependency skips rather than fails"
                    # We need to distinguish between "module not found" (external dep) and "syntax error" or "internal import error"
                    
                    # If the error message contains "No module named" and the module name is not in our workspace, it's likely a dep
                    # This is a heuristic.
                    err_str = str(e)
                    if "No module named" in err_str:
                        # Extract module name
                        # "No module named 'foo'"
                        match = re.search(r"No module named '([^']+)'", err_str)
                        if match:
                            missing_mod = match.group(1)
                            # Check if missing_mod is part of our workspace
                            # If it's not in our workspace, it's an external dependency
                            is_internal = False
                            for p in python_files:
                                if p.startswith(missing_mod) or p == missing_mod + '.py' or p == missing_mod + '/__init__.py':
                                    is_internal = True
                                    break
                            
                            if not is_internal:
                                # It's an external dependency. Skip this gate?
                                # The spec says "uninstalled dependency skips rather than fails"
                                # So if we hit a missing external dep, we should skip the smoke gate.
                                return GateResult(
                                    name='smoke',
                                    status=SKIPPED,
                                    method='import',
                                    detail=f"Missing external dependency: {missing_mod}",
                                    duration_s=time.time() - start_time
                                )
                    
                    import_errors.append(f"{module_name}: {str(e)}")
                except Exception as e:
                    import_errors.append(f"{module_name}: {str(e)}")
        finally:
            sys.path = old_path
        
        duration = time.time() - start_time
        if import_errors:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail=f"Failed to import {len(import_errors)} modules. First error: {import_errors[0]}",
                duration_s=duration,
                evidence={'errors': import_errors}
            )
        
        if imported_count == 0:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='No modules imported.',
                duration_s=duration
            )
            
        return GateResult(
            name='smoke',
            status=PASSED,
            method='import',
            detail=f"Successfully imported {imported_count} modules.",
            duration_s=duration
        )

    def _check_tests(self, workdir: str) -> GateResult:
        start_time = time.time()
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], capture_output=True, timeout=10)
        except Exception:
            return GateResult(
                name='tests',
                status=SKIPPED,
                method='pytest',
                detail='Pytest not available.',
                duration_s=time.time() - start_time
            )
        
        # Check if there are any test files
        test_files = []
        for root, dirs, files in os.walk(workdir):
            # Skip generated dirs
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for file in files:
                if file.startswith('test_') or file.endswith('_test.py'):
                    test_files.append(os.path.relpath(os.path.join(root, file), workdir))
        
        if not test_files:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='No test files found.',
                duration_s=time.time() - start_time
            )
        
        try:
            result = subprocess.run(
                [self.python_executable, '-m', 'pytest', '-v', '--tb=short'],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            duration = time.time() - start_time
            if result.returncode == 0:
                return GateResult(
                    name='tests',
                    status=PASSED,
                    method='pytest',
                    detail='All tests passed.',
                    duration_s=duration
                )
            else:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f"Tests failed. Exit code: {result.returncode}",
                    duration_s=duration,
                    evidence={'stdout': result.stdout[-1000:], 'stderr': result.stderr[-1000:]}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='Tests timed out.',
                duration_s=time.time() - start_time
            )
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail=f"Error running tests: {str(e)}",
                duration_s=time.time() - start_time
            )

    def _check_telemetry(self, workdir: str, python_files: List[str]) -> GateResult:
        start_time = time.time()
        if not python_files:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_import',
                detail='No python files to check.',
                duration_s=time.time() - start_time
            )
        
        telemetry_found = False
        telemetry_files = []
        
        for rel_path in python_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    code = f.read()
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if 'opentelemetry' in alias.name.lower():
                                telemetry_found = True
                                telemetry_files.append(rel_path)
                                break
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and 'opentelemetry' in node.module.lower():
                            telemetry_found = True
                            telemetry_files.append(rel_path)
                            break
            except Exception:
                pass
        
        duration = time.time() - start_time
        if telemetry_found:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast_import',
                detail=f"OpenTelemetry imported in {len(telemetry_files)} files.",
                duration_s=duration,
                evidence={'files': telemetry_files}
            )
        else:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_import',
                detail='No OpenTelemetry imports found in authored code.',
                duration_s=duration
            )