import ast
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import json
import inspect
import unittest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency contract: hae/evaluation/artifacts.py
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
    # Fallback implementation if artifacts.py is not yet available in the environment
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
        passed_count = sum(1 for g in evaluated if g.passed)
        return passed_count / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files}, Python Files: {self.python_files}")
        lines.append(f"Score Penalty: {self.score_penalty:.2f}")
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
            "gate_status": self.gate_status,
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
    
    blocks = {}
    lines = text.split('\n')
    in_block = False
    current_path = None
    current_content = []
    
    fence_marker = "```"
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(fence_marker):
            if in_block:
                # End of block
                if current_path:
                    blocks[current_path] = '\n'.join(current_content)
                in_block = False
                current_path = None
                current_content = []
            else:
                # Start of block
                in_block = True
                # Extract path from fence info
                info = stripped[len(fence_marker):].strip()
                # Heuristic: if it looks like a path, use it. Otherwise ignore or use generic?
                # Spec says "path->content map". If no path, we can't really map it.
                # Let's assume the info string IS the path if it contains a slash or dot.
                if info and ('/' in info or '.' in info):
                    current_path = info
                else:
                    current_path = None # Cannot determine path
                current_content = []
        elif in_block:
            current_content.append(line)
            
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
                # Try to treat as directory path if it's a string
                if isinstance(workspace, str) and os.path.isdir(workspace):
                    return self.verify_directory(workspace)
        
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
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
                # Filter out generated dirs during walk
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if not is_generated_path(rel_path) and not is_malformed_path(rel_path):
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                all_files[rel_path] = f.read()
                        except Exception:
                            pass
            authored = all_files
            
        # Filter authored to be safe
        clean_authored = filter_bundle(authored)
        
        python_files = [p for p in clean_authored if p.endswith('.py')]
        authored_count = len(clean_authored)
        python_count = len(python_files)
        
        gates = []
        
        # Gate 1: Syntax
        gates.append(self._gate_syntax(workdir, python_files))
        
        # Gate 2: Build
        gates.append(self._gate_build(workdir))
        
        # Gate 3: Smoke
        gates.append(self._gate_smoke(workdir, python_files))
        
        # Gate 4: Tests
        gates.append(self._gate_tests(workdir, python_files))
        
        # Gate 5: Telemetry
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

    def _gate_syntax(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        if not python_files:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='No Python files found.',
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
                duration_s=time.time() - start
            )
            
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast.parse',
            detail=f"All {len(python_files)} Python files parsed successfully.",
            duration_s=time.time() - start
        )

    def _gate_build(self, workdir: str) -> GateResult:
        start = time.time()
        
        # Check for packaging metadata
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        has_setup_py = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_setup_cfg = os.path.exists(os.path.join(workdir, 'setup.cfg'))
        
        if not (has_pyproject or has_setup_py or has_setup_cfg):
            return GateResult(
                name='build',
                status=FAILED,
                method='filesystem_check',
                detail='No packaging metadata (pyproject.toml, setup.py, setup.cfg) found.',
                duration_s=time.time() - start
            )
            
        # Validate pyproject.toml if present
        if has_pyproject:
            try:
                with open(os.path.join(workdir, 'pyproject.toml'), 'rb') as f:
                    data = tomllib.load(f)
                # Must contain at least one of [project], [build-system], or [tool]
                if not any(key in data for key in ['project', 'build-system', 'tool']):
                    return GateResult(
                        name='build',
                        status=FAILED,
                        method='tomllib_validation',
                        detail='pyproject.toml is valid TOML but missing required tables ([project], [build-system], or [tool]).',
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
        
        # Try pip install
        try:
            # Check if pip is available
            subprocess.run([self.python_executable, '-m', 'pip', '--version'], 
                           capture_output=True, timeout=10, check=True)
            
            # Run install
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps', '--no-build-isolation']
            result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=self.timeout_s)
            
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
                detail='Package installed successfully.',
                duration_s=time.time() - start
            )
            
        except FileNotFoundError:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip_install',
                detail='pip not available.',
                duration_s=time.time() - start
            )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip_install',
                detail='pip install timed out.',
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip_install',
                detail=f'Error running pip: {e}',
                duration_s=time.time() - start
            )

    def _gate_smoke(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        
        # Identify non-test modules
        # "Non-test authored modules"
        # Exclude files with 'test' in basename
        non_test_modules = []
        for rel_path in python_files:
            basename = os.path.basename(rel_path)
            if 'test' not in basename.lower():
                non_test_modules.append(rel_path)
                
        if not non_test_modules:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='subprocess_import',
                detail='No importable non-test modules found.',
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
        
        # Try to import each module
        # We need to convert file paths to module names
        # This is tricky. We'll try to import by file path using importlib in a subprocess
        
        script = """
import sys
import importlib.util
import os

def try_import(path):
    try:
        spec = importlib.util.spec_from_file_location("module", path)
        if spec is None:
            return False, "Spec is None"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return True, ""
    except ModuleNotFoundError as e:
        return False, f"ModuleNotFoundError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

paths = sys.argv[1:]
results = []
for p in paths:
    success, err = try_import(p)
    results.append((p, success, err))

import json
print(json.dumps(results))
"""
        
        # Write script to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(script)
            script_path = f.name
            
        try:
            # Pass absolute paths
            abs_paths = [os.path.join(workdir, p) for p in non_test_modules]
            result = subprocess.run(
                [self.python_executable, script_path] + abs_paths,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            if result.returncode != 0:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='subprocess_import',
                    detail=f'Import script failed: {result.stderr}',
                    duration_s=time.time() - start
                )
                
            try:
                results = json.loads(result.stdout)
            except json.JSONDecodeError:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='subprocess_import',
                    detail='Failed to parse import results.',
                    duration_s=time.time() - start
                )
                
            failures = []
            module_not_found = False
            
            for path, success, err in results:
                if not success:
                    if 'ModuleNotFoundError' in err:
                        module_not_found = True
                    else:
                        failures.append(f"{path}: {err}")
                        
            if failures:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='subprocess_import',
                    detail='Import failures: ' + '; '.join(failures),
                    duration_s=time.time() - start
                )
                
            if module_not_found:
                return GateResult(
                    name='smoke',
                    status=SKIPPED,
                    method='subprocess_import',
                    detail='Imports failed due to missing third-party packages.',
                    duration_s=time.time() - start
                )
                
            return GateResult(
                name='smoke',
                status=PASSED,
                method='subprocess_import',
                detail=f"All {len(non_test_modules)} non-test modules imported successfully.",
                duration_s=time.time() - start
            )
            
        finally:
            os.unlink(script_path)

    def _gate_tests(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        
        # Discover test files
        test_files = []
        for rel_path in python_files:
            basename = os.path.basename(rel_path)
            if 'test' in basename.lower():
                test_files.append(rel_path)
                
        if not test_files:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest_or_unittest',
                detail='No test files found.',
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
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], 
                           capture_output=True, timeout=10, check=True)
            has_pytest = True
        except Exception:
            has_pytest = False
            
        if has_pytest:
            # Run pytest
            abs_test_files = [os.path.join(workdir, p) for p in test_files]
            try:
                result = subprocess.run(
                    [self.python_executable, '-m', 'pytest', '-v'] + abs_test_files,
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
                        detail='All tests passed.',
                        duration_s=time.time() - start
                    )
                else:
                    # Check if it was just collection error due to missing deps
                    if 'ModuleNotFoundError' in result.stderr or 'ModuleNotFoundError' in result.stdout:
                         return GateResult(
                            name='tests',
                            status=SKIPPED,
                            method='pytest',
                            detail='Tests skipped due to missing dependencies.',
                            duration_s=time.time() - start
                        )
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='pytest',
                        detail=f'Tests failed: {result.stdout[-500:]}',
                        duration_s=time.time() - start
                    )
            except subprocess.TimeoutExpired:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail='Tests timed out.',
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
            # Run a script that imports each test file and collects/executes tests
            script = """
import sys
import unittest
import importlib.util
import os
import inspect

def run_tests(paths):
    suite = unittest.TestSuite()
    collected = 0
    errors = []
    
    for path in paths:
        try:
            spec = importlib.util.spec_from_file_location("test_module", path)
            if spec is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Collect unittest.TestCase subclasses
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, unittest.TestCase) and attr is not unittest.TestCase:
                    suite.addTest(unittest.makeSuite(attr))
                    collected += 1
                    
            # Collect bare def test_* functions
            for attr_name in dir(module):
                if attr_name.startswith('test_'):
                    attr = getattr(module, attr_name)
                    if callable(attr) and not isinstance(attr, type):
                        # Check if it takes 0 required args
                        sig = inspect.signature(attr)
                        params = list(sig.parameters.values())
                        # Allow self if it's a method? No, bare functions.
                        # If it has required args, skip.
                        has_required = False
                        for p in params:
                            if p.default is inspect.Parameter.empty and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                                has_required = True
                                break
                        if not has_required:
                            # Wrap in a TestCase to run it
                            class TestWrapper(unittest.TestCase):
                                def test_wrapper(self):
                                    attr()
                            TestWrapper.__name__ = f"Test_{attr_name}"
                            suite.addTest(TestWrapper())
                            collected += 1
                            
        except ModuleNotFoundError as e:
            errors.append(f"ModuleNotFoundError: {e}")
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
            
    if collected == 0:
        if errors:
            print(f"COLLECTION_ERROR:{';'.join(errors)}")
        else:
            print("NO_TESTS")
        return 0
        
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("SUCCESS")
        return 0
    else:
        print("FAILURE")
        return 1

paths = sys.argv[1:]
sys.exit(run_tests(paths))
"""
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(script)
                script_path = f.name
                
            try:
                abs_test_files = [os.path.join(workdir, p) for p in test_files]
                result = subprocess.run(
                    [self.python_executable, script_path] + abs_test_files,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s
                )
                
                stdout = result.stdout.strip()
                
                if stdout == "SUCCESS":
                    return GateResult(
                        name='tests',
                        status=PASSED,
                        method='stdlib_fallback',
                        detail='All tests passed.',
                        duration_s=time.time() - start
                    )
                elif stdout == "NO_TESTS":
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='stdlib_fallback',
                        detail='No tests collected.',
                        duration_s=time.time() - start
                    )
                elif stdout.startswith("COLLECTION_ERROR:"):
                    err_msg = stdout[len("COLLECTION_ERROR:"):]
                    if 'ModuleNotFoundError' in err_msg:
                        return GateResult(
                            name='tests',
                            status=SKIPPED,
                            method='stdlib_fallback',
                            detail=f'Tests skipped due to missing dependencies: {err_msg}',
                            duration_s=time.time() - start
                        )
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='stdlib_fallback',
                        detail=f'Test collection failed: {err_msg}',
                        duration_s=time.time() - start
                    )
                else:
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='stdlib_fallback',
                        detail=f'Tests failed. Output: {stdout}',
                        duration_s=time.time() - start
                    )
            finally:
                os.unlink(script_path)

    def _gate_telemetry(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        
        has_import = False
        has_call = False
        
        tracer_methods = {
            'start_as_current_span', 'start_span', 'get_tracer', 
            'get_tracer_provider', 'set_tracer_provider', 'get_meter'
        }
        
        for rel_path in python_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                tree = ast.parse(content)
                
                for node in ast.walk(tree):
                    # Check for import opentelemetry or from opentelemetry ... import ...
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name == 'opentelemetry' or alias.name.startswith('opentelemetry.'):
                                has_import = True
                    elif isinstance(node, ast.ImportFrom):
                        if node.module == 'opentelemetry' or (node.module and node.module.startswith('opentelemetry.')):
                            has_import = True
                            
                    # Check for calls to tracer/span methods
                    if isinstance(node, ast.Call):
                        func = node.func
                        # Handle method calls like tracer.start_span()
                        if isinstance(func, ast.Attribute):
                            if func.attr in tracer_methods:
                                has_call = True
                        # Handle direct function calls if imported directly? 
                        # The spec says "Call to a tracer/span method or function".
                        # Usually these are methods on objects.
                        # But if someone does `from opentelemetry import get_tracer`, then `get_tracer()` is a Name call.
                        elif isinstance(func, ast.Name):
                            if func.id in tracer_methods:
                                has_call = True
                                
            except Exception:
                continue
                
        if has_import and has_call:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast_analysis',
                detail='OpenTelemetry import and tracer/span calls found.',
                duration_s=time.time() - start
            )
        else:
            detail = []
            if not has_import:
                detail.append("No OpenTelemetry import found.")
            if not has_call:
                detail.append("No tracer/span method calls found.")
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail=' '.join(detail),
                duration_s=time.time() - start
            )