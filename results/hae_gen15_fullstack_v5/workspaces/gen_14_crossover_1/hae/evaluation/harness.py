import ast
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency: hae/evaluation/artifacts.py
try:
    from hae.evaluation.artifacts import (
        is_generated_path,
        is_malformed_path,
        filter_bundle,
        partition_bundle,
        count_source_files,
        EXCLUDED_DIR_SEGMENTS,
        EXCLUDED_DIR_SUFFIXES
    )
except ImportError:
    # Fallback implementation if artifacts.py is not available in the immediate context
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

    def count_source_files(bundle: Dict[str, str], suffixes: Tuple[str, ...] = ('.py',)) -> int:
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
        passed = [g for g in evaluated if g.passed]
        return len(passed) / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files}, Python Files: {self.python_files}")
        lines.append(f"Score Penalty: {self.score_penalty:.2f}")
        lines.append(f"Pass Rate: {self.pass_rate:.2%}")
        lines.append("Gates:")
        for g in self.gates:
            lines.append(f"  {g.name}: {g.status.upper()} ({g.method}) - {g.detail}")
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
    blocks = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Look for start of code block
        if line.strip().startswith("```"):
            # Extract language if present, e.g. ```python
            lang = line.strip()[3:].strip()
            # Look for end of code block
            j = i + 1
            content_lines = []
            found_end = False
            while j < len(lines):
                if lines[j].strip().startswith("```"):
                    found_end = True
                    break
                content_lines.append(lines[j])
                j += 1
            
            if found_end:
                content = "\n".join(content_lines)
                # Try to guess filename from context or just use a generic name
                # The spec says "path->content map". 
                # If no path is provided in the block, we might need to infer it.
                # Often agents write: ```python
                # # path: src/main.py
                # ...
                # Let's look for a comment indicating path
                path = None
                for cl in content_lines:
                    stripped = cl.strip()
                    if stripped.startswith("# path:") or stripped.startswith("# file:"):
                        path = stripped.split(":", 1)[1].strip()
                        break
                    if stripped.startswith("# filename:"):
                        path = stripped.split(":", 1)[1].strip()
                        break
                
                if not path:
                    # Fallback: use a generic name based on index or language
                    path = f"extracted_block_{i}.py" if lang == "python" else f"extracted_block_{i}.txt"
                
                # Sanitize path
                path = sanitize_path(path)
                if path:
                    blocks[path] = content
            i = j + 1
        else:
            i += 1
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
            # Assume workspace has a method to get files or is a dict
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace'
            else:
                # Try to treat it as a directory path if it's a string
                if isinstance(workspace, str) and os.path.isdir(workspace):
                    return self.verify_directory(workspace, source='directory')
        
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'

        if not bundle:
            # Empty workspace
            return self._create_empty_report(source)

        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write bundle to temp dir
            for path, content in bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            return self.verify_directory(tmpdir, authored=bundle, source=source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        start_time = time.time()
        
        # Determine authored files
        if authored is None:
            # Scan directory
            all_files = {}
            for root, dirs, files in os.walk(workdir):
                # Filter out generated dirs during walk for efficiency? 
                # No, we need to filter later using artifacts logic
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    try:
                        with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                            all_files[rel_path] = f.read()
                    except Exception:
                        pass
            authored = all_files
        
        # Filter bundle using artifacts logic
        filtered_bundle = filter_bundle(authored)
        
        # Count files
        authored_files_count = len(filtered_bundle)
        python_files_count = count_source_files(filtered_bundle, suffixes=('.py',))
        
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._gate_syntax(workdir, filtered_bundle))
        
        # 2. Build Gate
        gates.append(self._gate_build(workdir, filtered_bundle))
        
        # 3. Smoke Gate
        gates.append(self._gate_smoke(workdir, filtered_bundle))
        
        # 4. Tests Gate
        gates.append(self._gate_tests(workdir, filtered_bundle))
        
        # 5. Telemetry Gate
        gates.append(self._gate_telemetry(workdir, filtered_bundle))
        
        # Calculate penalty
        failed_count = sum(1 for g in gates if g.status == FAILED)
        score_penalty = failed_count * self.penalty_per_gate
        
        duration = time.time() - start_time
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files_count,
            python_files=python_files_count,
            source=source,
            harness_version='3.0-execution'
        )

    def _create_empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=FAILED, method='ast.parse', detail='No Python files found'),
            GateResult(name='build', status=FAILED, method='metadata_check', detail='No packaging metadata found'),
            GateResult(name='smoke', status=FAILED, method='import_check', detail='No importable modules'),
            GateResult(name='tests', status=FAILED, method='pytest', detail='No test files found'),
            GateResult(name='telemetry', status=FAILED, method='ast_analysis', detail='No telemetry usage found'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=5 * self.penalty_per_gate,
            authored_files=0,
            python_files=0,
            source=source,
            harness_version='3.0-execution'
        )

    def _gate_syntax(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        py_files = [p for p in bundle if p.endswith('.py')]
        
        if not py_files:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='No Python files found',
                duration_s=time.time() - start
            )
        
        errors = []
        for path in py_files:
            content = bundle[path]
            try:
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
        
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='Syntax errors: ' + '; '.join(errors),
                duration_s=time.time() - start
            )
        
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast.parse',
            detail=f'{len(py_files)} files parsed successfully',
            duration_s=time.time() - start
        )

    def _gate_build(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Check for packaging metadata
        has_pyproject = 'pyproject.toml' in bundle
        has_setup_py = 'setup.py' in bundle
        has_setup_cfg = 'setup.cfg' in bundle
        
        if not (has_pyproject or has_setup_py or has_setup_cfg):
            return GateResult(
                name='build',
                status=FAILED,
                method='metadata_check',
                detail='No packaging metadata (pyproject.toml, setup.py, setup.cfg) found',
                duration_s=time.time() - start
            )
        
        # Validate pyproject.toml if present
        if has_pyproject:
            try:
                content = bundle['pyproject.toml']
                data = tomllib.loads(content)
                # Must contain at least one of [project], [build-system], or [tool]
                if not any(key in data for key in ['project', 'build-system', 'tool']):
                    return GateResult(
                        name='build',
                        status=FAILED,
                        method='tomllib',
                        detail='pyproject.toml is valid TOML but missing required tables ([project], [build-system], or [tool])',
                        duration_s=time.time() - start
                    )
            except Exception as e:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='tomllib',
                    detail=f'pyproject.toml is invalid: {e}',
                    duration_s=time.time() - start
                )
        
        # Try pip install
        try:
            # Check if pip is available
            subprocess.run([self.python_executable, '-m', 'pip', '--version'], 
                           capture_output=True, timeout=10, check=True)
            
            # Run pip install -e .
            result = subprocess.run(
                [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps', '--no-build-isolation'],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            if result.returncode != 0:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip_install',
                    detail=f'pip install failed: {result.stderr}',
                    duration_s=time.time() - start
                )
            
            return GateResult(
                name='build',
                status=PASSED,
                method='pip_install',
                detail='Package installed successfully',
                duration_s=time.time() - start
            )
            
        except FileNotFoundError:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip_install',
                detail='pip not available',
                duration_s=time.time() - start
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
                detail=f'Error running pip install: {e}',
                duration_s=time.time() - start
            )

    def _gate_smoke(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find non-test authored modules
        # Exclude test files
        modules = []
        for path in bundle:
            if not path.endswith('.py'):
                continue
            basename = os.path.basename(path).lower()
            if 'test' in basename:
                continue
            # Convert path to module name
            # e.g. src/foo/bar.py -> foo.bar
            # We need to handle src/ prefix if present
            
            # Determine if it's importable
            # We will try to import it in a subprocess
            
            # Calculate module name
            rel_path = path
            if rel_path.startswith('src/'):
                rel_path = rel_path[4:]
            
            if rel_path.endswith('.py'):
                module_name = rel_path[:-3].replace('/', '.').replace('\\', '.')
                if module_name:
                    modules.append((path, module_name))
        
        if not modules:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import_check',
                detail='No importable non-test modules found',
                duration_s=time.time() - start
            )
        
        # Prepare PYTHONPATH
        env = os.environ.copy()
        python_path = [workdir, os.path.join(workdir, 'src')]
        if 'PYTHONPATH' in env:
            python_path.append(env['PYTHONPATH'])
        env['PYTHONPATH'] = os.pathsep.join(python_path)
        
        # Try importing each module
        import_errors = []
        module_not_found_errors = []
        
        for path, module_name in modules:
            try:
                # Run a subprocess to import the module
                code = f"import {module_name}"
                result = subprocess.run(
                    [self.python_executable, '-c', code],
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s
                )
                
                if result.returncode != 0:
                    stderr = result.stderr
                    if 'ModuleNotFoundError' in stderr:
                        # Check if it's a third-party package
                        # If the module itself is not found, it's a failure.
                        # If a dependency is not found, it might be skipped.
                        # The spec says: "If module imports fail solely with ModuleNotFoundError for uninstalled third-party packages... return SKIPPED"
                        # This implies if the module itself imports fine but a dependency fails, it's skipped.
                        # But if the module itself is not found, it's failed.
                        
                        # How to distinguish?
                        # If the error is "No module named 'foo'" where foo is the module we tried to import, it's failed.
                        # If it's "No module named 'opentelemetry'", it's skipped.
                        
                        # Simple heuristic: if the module name is in the error, it's failed.
                        if module_name in stderr:
                            import_errors.append(f"{module_name}: {stderr}")
                        else:
                            module_not_found_errors.append(f"{module_name}: {stderr}")
                    else:
                        import_errors.append(f"{module_name}: {stderr}")
                        
            except Exception as e:
                import_errors.append(f"{module_name}: {e}")
        
        if import_errors:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import_check',
                detail='Import errors: ' + '; '.join(import_errors),
                duration_s=time.time() - start
            )
        
        if module_not_found_errors:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import_check',
                detail='Skipped due to missing third-party dependencies: ' + '; '.join(module_not_found_errors),
                duration_s=time.time() - start
            )
        
        return GateResult(
            name='smoke',
            status=PASSED,
            method='import_check',
            detail=f'{len(modules)} modules imported successfully',
            duration_s=time.time() - start
        )

    def _gate_tests(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Discover test files
        test_files = []
        for path in bundle:
            if not path.endswith('.py'):
                continue
            basename = os.path.basename(path).lower()
            if 'test' in basename:
                test_files.append(path)
        
        if not test_files:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='No test files found',
                duration_s=time.time() - start
            )
        
        # Prepare PYTHONPATH
        env = os.environ.copy()
        python_path = [workdir, os.path.join(workdir, 'src')]
        if 'PYTHONPATH' in env:
            python_path.append(env['PYTHONPATH'])
        env['PYTHONPATH'] = os.pathsep.join(python_path)
        
        # Check if pytest is installed
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], 
                           capture_output=True, timeout=10, check=True)
            use_pytest = True
        except Exception:
            use_pytest = False
        
        if use_pytest:
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
                    # Check if it's a collection error due to missing modules
                    if 'ModuleNotFoundError' in result.stderr or 'ModuleNotFoundError' in result.stdout:
                        # Check if it's a third-party package
                        # If the test file itself fails to import due to missing third-party, skip
                        # If tests fail, it's failed.
                        # Pytest exit code 2 is collection error.
                        if result.returncode == 2:
                            return GateResult(
                                name='tests',
                                status=SKIPPED,
                                method='pytest',
                                detail='Tests skipped due to missing dependencies',
                                duration_s=time.time() - start
                            )
                    
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='pytest',
                        detail=f'Tests failed: {result.stderr}',
                        duration_s=time.time() - start
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
                    detail=f'Error running pytest: {e}',
                    duration_s=time.time() - start
                )
        else:
            # Stdlib fallback
            # Run a subprocess that imports each test file and collects/executes tests
            # This is complex. We'll write a small script.
            
            script = """
import sys
import unittest
import importlib
import traceback
import inspect

test_files = sys.argv[1:]
loader = unittest.TestLoader()
suite = unittest.TestSuite()

collected = 0
errors = []

for tf in test_files:
    try:
        # Convert path to module name
        # This is tricky. We assume the test file is importable.
        # We'll try to import it directly.
        module_name = tf.replace('/', '.').replace('\\\\', '.')
        if module_name.endswith('.py'):
            module_name = module_name[:-3]
        
        # Try to import
        mod = importlib.import_module(module_name)
        
        # Collect unittest.TestCase subclasses
        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj is not unittest.TestCase:
                suite.addTest(loader.loadTestsFromTestCase(obj))
                collected += 1
        
        # Collect bare test_* functions
        for name in dir(mod):
            if name.startswith('test_'):
                obj = getattr(mod, name)
                if callable(obj) and not isinstance(obj, type):
                    # Check if it takes 0 required args
                    sig = inspect.signature(obj)
                    params = sig.parameters
                    # Check if all params have defaults or are *args/**kwargs
                    has_required = False
                    for p in params.values():
                        if p.default is inspect.Parameter.empty and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                            has_required = True
                            break
                    
                    if not has_required:
                        # Wrap it in a TestCase
                        class TestWrapper(unittest.TestCase):
                            def test_wrapper(self):
                                obj()
                        TestWrapper.__name__ = f'Test_{name}'
                        suite.addTest(TestWrapper())
                        collected += 1
                        
    except ModuleNotFoundError as e:
        errors.append(f"ModuleNotFoundError: {e}")
    except Exception as e:
        errors.append(f"Error importing {tf}: {e}")

if errors:
    # If all errors are ModuleNotFoundError, skip
    if all('ModuleNotFoundError' in e for e in errors):
        print("SKIPPED")
        sys.exit(0)
    else:
        print("FAILED")
        for e in errors:
            print(e)
        sys.exit(1)

if collected == 0:
    print("FAILED")
    print("No tests collected")
    sys.exit(1)

runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)

if result.wasSuccessful():
    print("PASSED")
    sys.exit(0)
else:
    print("FAILED")
    sys.exit(1)
"""
            try:
                # Write script to temp file
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                    f.write(script)
                    script_path = f.name
                
                result = subprocess.run(
                    [self.python_executable, script_path] + test_files,
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s
                )
                
                os.unlink(script_path)
                
                if result.returncode == 0:
                    if 'SKIPPED' in result.stdout:
                        return GateResult(
                            name='tests',
                            status=SKIPPED,
                            method='stdlib_fallback',
                            detail='Tests skipped due to missing dependencies',
                            duration_s=time.time() - start
                        )
                    return GateResult(
                        name='tests',
                        status=PASSED,
                        method='stdlib_fallback',
                        detail='All tests passed',
                        duration_s=time.time() - start
                    )
                else:
                    if 'SKIPPED' in result.stdout:
                        return GateResult(
                            name='tests',
                            status=SKIPPED,
                            method='stdlib_fallback',
                            detail='Tests skipped due to missing dependencies',
                            duration_s=time.time() - start
                        )
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='stdlib_fallback',
                        detail=f'Tests failed: {result.stderr}',
                        duration_s=time.time() - start
                    )
            except Exception as e:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='stdlib_fallback',
                    detail=f'Error running tests: {e}',
                    duration_s=time.time() - start
                )

    def _gate_telemetry(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        
        py_files = [p for p in bundle if p.endswith('.py')]
        
        if not py_files:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='No Python files found',
                duration_s=time.time() - start
            )
        
        has_import = False
        has_call = False
        
        tracer_methods = {
            'start_as_current_span',
            'start_span',
            'get_tracer',
            'get_tracer_provider',
            'set_tracer_provider',
            'get_meter'
        }
        
        for path in py_files:
            content = bundle[path]
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue
            
            for node in ast.walk(tree):
                # Check for import opentelemetry or from opentelemetry ... import ...
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == 'opentelemetry' or alias.name.startswith('opentelemetry.'):
                            has_import = True
                elif isinstance(node, ast.ImportFrom):
                    if node.module == 'opentelemetry' or node.module.startswith('opentelemetry.'):
                        has_import = True
                
                # Check for calls to tracer/span methods
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute):
                        if func.attr in tracer_methods:
                            has_call = True
                    elif isinstance(func, ast.Name):
                        if func.id in tracer_methods:
                            has_call = True
        
        if has_import and has_call:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast_analysis',
                detail='Telemetry usage detected',
                duration_s=time.time() - start
            )
        
        if not has_import:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='No opentelemetry import found',
                duration_s=time.time() - start
            )
        
        if not has_call:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='No tracer/span method calls found',
                duration_s=time.time() - start
            )
        
        return GateResult(
            name='telemetry',
            status=FAILED,
            method='ast_analysis',
            detail='Telemetry usage not fully detected',
            duration_s=time.time() - start
        )