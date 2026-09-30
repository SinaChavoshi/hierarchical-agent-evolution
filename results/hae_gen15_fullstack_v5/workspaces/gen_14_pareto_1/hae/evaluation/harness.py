"""
Execution-grounded verification of agent-authored workspaces.

This module implements the ExecutionHarness which verifies code by actually
running it (parsing, installing, importing, testing) rather than relying on
textual analysis or file counts.
"""

import ast
import os
import sys
import subprocess
import tempfile
import shutil
import time
import tomllib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Iterable

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Import artifacts module if available, otherwise define minimal fallbacks
# to ensure this module can run in isolation if artifacts.py is not yet present
# or if we need to be robust against import errors during grading.
try:
    from hae.evaluation.artifacts import (
        filter_bundle,
        count_source_files,
        is_generated_path,
        is_malformed_path,
        sanitize_path,
        partition_bundle
    )
except ImportError:
    # Minimal fallback implementation for artifacts logic if module is missing
    # This ensures the harness can still function for basic verification
    EXCLUDED_DIR_SEGMENTS = ('.pytest_cache', '__pycache__', 'venv', '.venv', '.git', '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.ipynb_checkpoints')
    EXCLUDED_DIR_SUFFIXES = ('.egg-info', '.dist-info')

    def is_generated_path(path: str) -> bool:
        parts = path.replace('\\', '/').split('/')
        for part in parts[:-1]:
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
        lines.append(f"Pass Rate: {self.pass_rate:.2%}")
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
    
    # Regex to find fenced code blocks with optional language and optional filename
    # Matches: ```python filename.py ... ``` or ``` filename.py ... ```
    # We look for blocks that start with ``` followed by optional lang, then optional filename on same line or next
    # Simplified approach: Look for ```python ... ``` and try to infer filename from context or just use generic names?
    # The spec says "path->content map". Usually, agents write:
    # ```python
    # # path: src/foo.py
    # ...
    # ```
    # OR
    # ```python src/foo.py
    # ...
    # ```
    
    blocks = {}
    # Pattern to capture fenced blocks
    # Group 1: Language/Filename hint
    # Group 2: Content
    pattern = r"```(?:python|py)?\s*([^\n]*)\n(.*?)```"
    
    matches = re.finditer(pattern, text, re.DOTALL)
    
    counter = 0
    for match in matches:
        header = match.group(1).strip()
        content = match.group(2)
        
        # Try to extract filename from header
        # Headers might be "python", "python src/foo.py", "src/foo.py", etc.
        filename = None
        parts = header.split()
        for part in parts:
            if part.endswith('.py'):
                filename = part
                break
        
        if not filename:
            # Fallback: generate a unique name
            filename = f"extracted_block_{counter}.py"
            counter += 1
            
        # Sanitize the filename if it came from text
        filename = sanitize_path(filename)
        
        # Avoid overwriting if duplicate names occur (append counter)
        if filename in blocks:
            base, ext = os.path.splitext(filename)
            filename = f"{base}_{counter}{ext}"
            counter += 1
            
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
        bundle = {}
        source = 'bundle'
        
        # Try to get bundle from workspace object
        if workspace is not None:
            # Assume workspace has a method to get files or is a dict
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
            elif isinstance(workspace, dict):
                bundle = workspace
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
            
        # If bundle is empty, try extracting from deliverable text
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        if not bundle:
            # Empty workspace
            return self._create_empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to remove generated/malformed paths
        authored_bundle = filter_bundle(bundle)
        
        if not authored_bundle:
            return self._create_empty_report(source)
            
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
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                authored[rel_path] = f.read()
                        except Exception:
                            pass
        
        python_files = [p for p in authored if p.endswith('.py')]
        authored_count = len(authored)
        python_count = len(python_files)
        
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._gate_syntax(workdir, python_files))
        
        # 2. Build Gate
        gates.append(self._gate_build(workdir))
        
        # 3. Smoke Gate
        gates.append(self._gate_smoke(workdir, python_files))
        
        # 4. Tests Gate
        gates.append(self._gate_tests(workdir, python_files))
        
        # 5. Telemetry Gate
        gates.append(self._gate_telemetry(workdir, python_files))
        
        # Calculate penalty
        failed_count = sum(1 for g in gates if g.status == FAILED)
        score_penalty = failed_count * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_count,
            python_files=python_count,
            source=source
        )

    def _create_empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=FAILED, method='ast.parse', detail='No Python files found'),
            GateResult(name='build', status=FAILED, method='metadata_check', detail='No build metadata found'),
            GateResult(name='smoke', status=FAILED, method='import_check', detail='No modules to import'),
            GateResult(name='tests', status=FAILED, method='pytest', detail='No test files found'),
            GateResult(name='telemetry', status=FAILED, method='ast_analysis', detail='No Python files to analyze')
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=5 * self.penalty_per_gate,
            authored_files=0,
            python_files=0,
            source=source
        )

    def _gate_syntax(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        if not python_files:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='No Python files found',
                duration_s=time.time() - start
            )
        
        errors = []
        for rel_path in python_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"{rel_path}: {e}")
            except Exception as e:
                errors.append(f"{rel_path}: {e}")
                
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='Syntax errors found: ' + '; '.join(errors),
                duration_s=time.time() - start,
                evidence={'errors': errors}
            )
            
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast.parse',
            detail=f"All {len(python_files)} Python files parsed successfully",
            duration_s=time.time() - start
        )

    def _gate_build(self, workdir: str) -> GateResult:
        start = time.time()
        
        # Check for metadata files
        metadata_files = ['pyproject.toml', 'setup.py', 'setup.cfg']
        found_metadata = None
        
        for mf in metadata_files:
            if os.path.exists(os.path.join(workdir, mf)):
                found_metadata = mf
                break
                
        if not found_metadata:
            return GateResult(
                name='build',
                status=FAILED,
                method='metadata_check',
                detail='No packaging metadata found (pyproject.toml, setup.py, or setup.cfg)',
                duration_s=time.time() - start
            )
            
        # If pyproject.toml is present, validate it
        if found_metadata == 'pyproject.toml':
            try:
                with open(os.path.join(workdir, 'pyproject.toml'), 'rb') as f:
                    data = tomllib.load(f)
                # Must contain at least one of [project], [build-system], or [tool]
                if not any(key in data for key in ['project', 'build-system', 'tool']):
                    return GateResult(
                        name='build',
                        status=FAILED,
                        method='tomllib_validation',
                        detail='pyproject.toml is valid TOML but missing required tables ([project], [build-system], or [tool])',
                        duration_s=time.time() - start
                    )
            except Exception as e:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='tomllib_validation',
                    detail=f'pyproject.toml is malformed: {e}',
                    duration_s=time.time() - start
                )
                
        # Try to install
        # Check if pip is available
        try:
            subprocess.run([self.python_executable, '-m', 'pip', '--version'], 
                           capture_output=True, timeout=10, check=True)
        except Exception:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip_install',
                detail='pip not available or failed to start',
                duration_s=time.time() - start
            )
            
        try:
            # Run pip install -e . --no-deps --no-build-isolation
            result = subprocess.run(
                [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps', '--no-build-isolation'],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            if result.returncode == 0:
                return GateResult(
                    name='build',
                    status=PASSED,
                    method='pip_install',
                    detail='Package installed successfully',
                    duration_s=time.time() - start
                )
            else:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip_install',
                    detail=f'pip install failed: {result.stderr}',
                    duration_s=time.time() - start,
                    evidence={'stderr': result.stderr, 'stdout': result.stdout}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip_install',
                detail='pip install timed out',
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip_install',
                detail=f'pip install error: {e}',
                duration_s=time.time() - start
            )

    def _gate_smoke(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        
        # Identify non-test modules
        # Test files usually have 'test' in basename
        non_test_modules = []
        for rel_path in python_files:
            basename = os.path.basename(rel_path).lower()
            if 'test' not in basename:
                non_test_modules.append(rel_path)
                
        if not non_test_modules:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import_check',
                detail='No non-test modules found to import',
                duration_s=time.time() - start
            )
            
        # Prepare PYTHONPATH
        src_dir = os.path.join(workdir, 'src')
        env = os.environ.copy()
        current_pythonpath = env.get('PYTHONPATH', '')
        new_paths = [workdir, src_dir]
        if current_pythonpath:
            new_paths.append(current_pythonpath)
        env['PYTHONPATH'] = os.pathsep.join(new_paths)
        
        # We need to import each module. 
        # To avoid side effects of importing everything at once, we can try importing them one by one in a subprocess
        # or just try importing the package structure.
        # The spec says: "Non-test authored modules must import in a subprocess"
        
        # Let's try to import each module individually to isolate failures
        failed_imports = []
        skipped_imports = [] # For ModuleNotFoundError of third-party
        
        for rel_path in non_test_modules:
            # Convert path to module name
            # e.g. src/foo/bar.py -> foo.bar
            # e.g. foo/bar.py -> foo.bar
            # We need to handle the src/ prefix if present
            
            module_path = rel_path
            if module_path.startswith('src/'):
                module_path = module_path[4:]
                
            if module_path.endswith('.py'):
                module_name = module_path[:-3].replace('/', '.').replace('\\', '.')
            else:
                continue
                
            # Skip __init__.py as they are usually empty or just imports
            if module_name.endswith('.__init__'):
                continue
                
            # Run subprocess to import
            code = f"import {module_name}"
            try:
                result = subprocess.run(
                    [self.python_executable, '-c', code],
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode != 0:
                    stderr = result.stderr
                    # Check for ModuleNotFoundError for third-party packages
                    if 'ModuleNotFoundError' in stderr:
                        # Check if it's a standard library or local module
                        # If it's a local module, it should have been found via PYTHONPATH
                        # If it's a third-party package, we skip
                        # Heuristic: if the missing module is not in the bundle, it's third-party
                        # Extract missing module name
                        match = re.search(r"No module named '([^']+)'", stderr)
                        if match:
                            missing_mod = match.group(1)
                            # Check if missing_mod is part of our bundle
                            # If it's a top-level package we authored, it should be found
                            # If it's a sub-module, it might be a dependency
                            # Simple check: if the root package is not in our authored files, it's likely third-party
                            root_pkg = missing_mod.split('.')[0]
                            is_local = False
                            for p in python_files:
                                if p.startswith(root_pkg + '/') or p == root_pkg + '.py':
                                    is_local = True
                                    break
                            if not is_local:
                                skipped_imports.append((module_name, missing_mod))
                                continue
                    failed_imports.append((module_name, stderr))
            except Exception as e:
                failed_imports.append((module_name, str(e)))
                
        if failed_imports:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import_check',
                detail=f"Import failures: {failed_imports}",
                duration_s=time.time() - start,
                evidence={'failures': failed_imports}
            )
            
        if skipped_imports:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import_check',
                detail=f"Skipped due to missing third-party dependencies: {skipped_imports}",
                duration_s=time.time() - start,
                evidence={'skipped': skipped_imports}
            )
            
        return GateResult(
            name='smoke',
            status=PASSED,
            method='import_check',
            detail=f"All {len(non_test_modules)} non-test modules imported successfully",
            duration_s=time.time() - start
        )

    def _gate_tests(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        
        # Discover test files
        test_files = []
        for rel_path in python_files:
            basename = os.path.basename(rel_path).lower()
            if 'test' in basename:
                test_files.append(rel_path)
                
        if not test_files:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='No test files found',
                duration_s=time.time() - start
            )
            
        # Prepare PYTHONPATH
        src_dir = os.path.join(workdir, 'src')
        env = os.environ.copy()
        current_pythonpath = env.get('PYTHONPATH', '')
        new_paths = [workdir, src_dir]
        if current_pythonpath:
            new_paths.append(current_pythonpath)
        env['PYTHONPATH'] = os.pathsep.join(new_paths)
        
        # Check if pytest is installed
        pytest_available = False
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], 
                           capture_output=True, timeout=10, check=True)
            pytest_available = True
        except Exception:
            pytest_available = False
            
        if pytest_available:
            # Run pytest
            try:
                result = subprocess.run(
                    [self.python_executable, '-m', 'pytest', '-v', '--tb=short'],
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s
                )
                if result.returncode == 0:
                    return GateResult(
                        name='tests',
                        status=PASSED,
                        method='pytest',
                        detail='All tests passed',
                        duration_s=time.time() - start
                    )
                else:
                    # Check if failure was due to ModuleNotFoundError
                    if 'ModuleNotFoundError' in result.stderr or 'ModuleNotFoundError' in result.stdout:
                        # Check if it's a third-party package
                        # This is tricky with pytest output. 
                        # If pytest collected 0 tests due to import errors, it might return non-zero.
                        # Let's check if any tests were collected.
                        if 'collected 0 items' in result.stdout or 'no tests ran' in result.stdout:
                             # Likely import error
                             # Check if the missing module is third-party
                             # We'll assume SKIPPED if we see ModuleNotFoundError and no tests ran
                             return GateResult(
                                name='tests',
                                status=SKIPPED,
                                method='pytest',
                                detail='Tests skipped due to missing third-party dependencies',
                                duration_s=time.time() - start,
                                evidence={'stdout': result.stdout, 'stderr': result.stderr}
                            )
                    
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='pytest',
                        detail='Tests failed',
                        duration_s=time.time() - start,
                        evidence={'stdout': result.stdout, 'stderr': result.stderr}
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
                    detail=f'Pytest execution error: {e}',
                    duration_s=time.time() - start
                )
        else:
            # Stdlib fallback
            # Run a subprocess that imports each test file and collects/executes tests
            # We'll write a small runner script
            
            runner_code = """
import sys
import os
import unittest
import importlib.util

def run_tests(test_files):
    suite = unittest.TestSuite()
    errors = []
    skipped = []
    
    for test_file in test_files:
        try:
            # Add to path
            sys.path.insert(0, os.path.dirname(test_file))
            sys.path.insert(0, os.path.join(os.path.dirname(test_file), 'src'))
            
            # Import module
            module_name = os.path.basename(test_file).replace('.py', '')
            spec = importlib.util.spec_from_file_location(module_name, test_file)
            if spec is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Collect unittest.TestCase subclasses
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, unittest.TestCase) and attr.__module__ == module_name:
                    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(attr))
                    
            # Collect bare test_* functions
            for attr_name in dir(module):
                if attr_name.startswith('test_'):
                    attr = getattr(module, attr_name)
                    if callable(attr) and not isinstance(attr, type):
                        # Check if it takes 0 required args
                        import inspect
                        sig = inspect.signature(attr)
                        params = [p for p in sig.parameters.values() if p.default is inspect.Parameter.empty]
                        if len(params) == 0:
                            # Wrap in a TestCase
                            class DynamicTest(unittest.TestCase):
                                def test_dynamic(self):
                                    attr()
                            DynamicTest.__name__ = f'Test_{attr_name}'
                            suite.addTest(DynamicTest('test_dynamic'))
        except ModuleNotFoundError as e:
            skipped.append(str(e))
        except Exception as e:
            errors.append(f"{test_file}: {e}")
            
    if errors:
        print("ERRORS:", errors)
        sys.exit(1)
        
    if skipped and not suite.countTestCases():
        print("SKIPPED:", skipped)
        sys.exit(2) # Custom exit code for skipped
        
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == '__main__':
    test_files = sys.argv[1:]
    run_tests(test_files)
"""
            try:
                # Write runner to temp file
                runner_path = os.path.join(workdir, '_test_runner.py')
                with open(runner_path, 'w') as f:
                    f.write(runner_code)
                
                # Run it
                result = subprocess.run(
                    [self.python_executable, runner_path] + test_files,
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s
                )
                
                # Cleanup
                try:
                    os.remove(runner_path)
                except:
                    pass
                
                if result.returncode == 0:
                    return GateResult(
                        name='tests',
                        status=PASSED,
                        method='stdlib_fallback',
                        detail='All tests passed',
                        duration_s=time.time() - start
                    )
                elif result.returncode == 2:
                    return GateResult(
                        name='tests',
                        status=SKIPPED,
                        method='stdlib_fallback',
                        detail='Tests skipped due to missing dependencies',
                        duration_s=time.time() - start,
                        evidence={'stdout': result.stdout, 'stderr': result.stderr}
                    )
                else:
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='stdlib_fallback',
                        detail='Tests failed',
                        duration_s=time.time() - start,
                        evidence={'stdout': result.stdout, 'stderr': result.stderr}
                    )
            except Exception as e:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='stdlib_fallback',
                    detail=f'Fallback runner error: {e}',
                    duration_s=time.time() - start
                )

    def _gate_telemetry(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        
        if not python_files:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='No Python files to analyze',
                duration_s=time.time() - start
            )
            
        has_import = False
        has_call = False
        
        # Tracer/span methods
        target_methods = {
            'start_as_current_span',
            'start_span',
            'get_tracer',
            'get_tracer_provider',
            'set_tracer_provider',
            'get_meter'
        }
        
        for rel_path in python_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                tree = ast.parse(content)
                
                for node in ast.walk(tree):
                    # Check for import opentelemetry
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name == 'opentelemetry' or alias.name.startswith('opentelemetry.'):
                                has_import = True
                    elif isinstance(node, ast.ImportFrom):
                        if node.module == 'opentelemetry' or (node.module and node.module.startswith('opentelemetry.')):
                            has_import = True
                            
                    # Check for calls
                    if isinstance(node, ast.Call):
                        func = node.func
                        # Direct call: get_tracer()
                        if isinstance(func, ast.Name) and func.id in target_methods:
                            has_call = True
                        # Attribute call: tracer.start_span() or otel.get_tracer()
                        elif isinstance(func, ast.Attribute):
                            if func.attr in target_methods:
                                has_call = True
                                
            except Exception:
                continue
                
        if has_import and has_call:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast_analysis',
                detail='OpenTelemetry import and tracer/span calls found',
                duration_s=time.time() - start
            )
        elif has_import:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='OpenTelemetry import found but no tracer/span calls',
                duration_s=time.time() - start
            )
        elif has_call:
            # Calls without import? Unlikely but possible if imported elsewhere or dynamic
            # Spec says "BOTH ... import ... AND ... Call"
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='Tracer/span calls found but no OpenTelemetry import',
                duration_s=time.time() - start
            )
        else:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='No OpenTelemetry usage found',
                duration_s=time.time() - start
            )