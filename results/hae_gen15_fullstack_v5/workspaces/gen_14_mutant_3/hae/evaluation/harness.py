import ast
import os
import sys
import subprocess
import tempfile
import shutil
import tomllib
import re
import time
import unittest
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency on artifacts module (assumed available in environment)
try:
    from hae.evaluation.artifacts import (
        filter_bundle,
        is_generated_path,
        is_malformed_path,
        sanitize_path,
        partition_bundle,
        count_source_files
    )
except ImportError:
    # Fallback minimal implementation if artifacts is not yet available in path
    # This ensures the harness can run in isolation if needed, though spec says it's parallel.
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
        passed = [g for g in evaluated if g.passed]
        return len(passed) / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files}, Python Files: {self.python_files}")
        lines.append(f"Score Penalty: {self.score_penalty:.2f}")
        lines.append("-" * 40)
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"[{status_icon}] {g.name}: {g.status.upper()} ({g.method})")
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
    
    # Regex to find fenced code blocks with optional language and optional filename hint
    # Pattern: ```lang filename\ncontent\n```
    # Or just: ```lang\ncontent\n```
    # We look for lines starting with ```
    
    blocks = {}
    lines = text.split('\n')
    in_block = False
    current_content = []
    current_path = None
    
    # Simple state machine
    fence_pattern = re.compile(r'^```(\w+)?\s*(.*)$')
    
    for line in lines:
        if not in_block:
            match = fence_pattern.match(line)
            if match:
                in_block = True
                current_content = []
                # Try to extract path from the info string
                info = match.group(2)
                # Heuristic: if info contains a dot and looks like a filename, use it
                # Otherwise, we might need to guess or skip. 
                # Spec says "path->content map". If no path, we can't map it reliably.
                # However, often agents write ```python src/main.py
                parts = info.split()
                if parts:
                    # Check if any part looks like a path
                    for p in parts:
                        if '.' in p and not p.startswith('-'):
                            current_path = p
                            break
                if not current_path:
                    current_path = None # Will be skipped or auto-named? Spec implies map.
        else:
            if line.startswith('```'):
                in_block = False
                if current_path:
                    blocks[current_path] = '\n'.join(current_content)
                current_path = None
            else:
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
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=FAILED, method='ast.parse', detail='No Python files found'),
            GateResult(name='build', status=FAILED, method='metadata', detail='No build metadata found'),
            GateResult(name='smoke', status=FAILED, method='import', detail='No modules to import'),
            GateResult(name='tests', status=FAILED, method='pytest', detail='No tests found'),
            GateResult(name='telemetry', status=FAILED, method='ast', detail='No telemetry usage found'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=len(gates) * self.penalty_per_gate,
            authored_files=0,
            python_files=0,
            source=source
        )

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle using artifacts logic
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files = len(authored_bundle)
        python_files = count_source_files(authored_bundle, ('.py',))
        
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
                # Filter dirs in-place to avoid walking into excluded dirs
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.join(root, d))]
                
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, workdir)
                    if not is_generated_path(rel_path) and not is_malformed_path(rel_path):
                        try:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                            authored[rel_path] = content
                        except Exception:
                            pass
        
        authored_files = len(authored)
        python_files = count_source_files(authored, ('.py',))
        
        if authored_files == 0:
            return self._empty_report(source)

        gates = []
        
        # 1. Syntax Gate
        gates.append(self._gate_syntax(workdir, authored))
        
        # 2. Build Gate
        gates.append(self._gate_build(workdir, authored))
        
        # 3. Smoke Gate
        gates.append(self._gate_smoke(workdir, authored))
        
        # 4. Tests Gate
        gates.append(self._gate_tests(workdir, authored))
        
        # 5. Telemetry Gate
        gates.append(self._gate_telemetry(workdir, authored))
        
        failed_count = sum(1 for g in gates if g.status == FAILED)
        score_penalty = failed_count * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files,
            python_files=python_files,
            source=source
        )

    def _gate_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        py_files = [p for p in authored.keys() if p.endswith('.py')]
        
        if not py_files:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='No Python files found',
                duration_s=time.time() - start
            )
        
        errors = []
        for rel_path in py_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                ast.parse(source)
            except SyntaxError as e:
                errors.append(f"{rel_path}: {e}")
            except Exception as e:
                errors.append(f"{rel_path}: {e}")
        
        duration = time.time() - start
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='Syntax errors found: ' + '; '.join(errors[:5]),
                duration_s=duration,
                evidence={'errors': errors}
            )
        
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast.parse',
            detail=f"All {len(py_files)} Python files parsed successfully",
            duration_s=duration
        )

    def _gate_build(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Check for metadata files
        metadata_files = ['pyproject.toml', 'setup.py', 'setup.cfg']
        found_metadata = None
        
        for mf in metadata_files:
            if mf in authored:
                found_metadata = mf
                break
        
        if not found_metadata:
            return GateResult(
                name='build',
                status=FAILED,
                method='metadata',
                detail='No build metadata (pyproject.toml, setup.py, setup.cfg) found',
                duration_s=time.time() - start
            )
        
        # Validate pyproject.toml if present
        if found_metadata == 'pyproject.toml':
            full_path = os.path.join(workdir, 'pyproject.toml')
            try:
                with open(full_path, 'rb') as f:
                    data = tomllib.load(f)
                # Must contain at least one of [project], [build-system], [tool]
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
            
            # Run pip install -e . --no-deps --no-build-isolation
            result = subprocess.run(
                [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps', '--no-build-isolation'],
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
                    method='pip install',
                    detail='Package installed successfully',
                    duration_s=duration
                )
            else:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip install',
                    detail=f'pip install failed: {result.stderr[:500]}',
                    duration_s=duration,
                    evidence={'stderr': result.stderr, 'stdout': result.stdout}
                )
        except FileNotFoundError:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip install',
                detail='pip not available',
                duration_s=time.time() - start
            )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail='pip install timed out',
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail=f'Error running pip install: {e}',
                duration_s=time.time() - start
            )

    def _gate_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find non-test authored modules
        # Exclude test files
        non_test_modules = []
        for path in authored.keys():
            if path.endswith('.py'):
                basename = os.path.basename(path).lower()
                if 'test' not in basename:
                    non_test_modules.append(path)
        
        if not non_test_modules:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='No non-test modules found',
                duration_s=time.time() - start
            )
        
        # Prepare PYTHONPATH
        src_dir = os.path.join(workdir, 'src')
        env = os.environ.copy()
        existing_path = env.get('PYTHONPATH', '')
        new_paths = [workdir, src_dir]
        if existing_path:
            new_paths.append(existing_path)
        env['PYTHONPATH'] = os.pathsep.join(new_paths)
        
        # Try importing each module
        failed_imports = []
        skipped_imports = []
        
        for mod_path in non_test_modules:
            # Convert path to module name
            # e.g., src/foo/bar.py -> foo.bar
            rel_path = mod_path
            if rel_path.startswith('src/'):
                rel_path = rel_path[4:]
            
            module_name = rel_path.replace('/', '.').replace('\\', '.')
            if module_name.endswith('.py'):
                module_name = module_name[:-3]
            
            # Run import in subprocess
            code = f"import {module_name}"
            try:
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
                    # Check for ModuleNotFoundError for third-party packages
                    if 'ModuleNotFoundError' in stderr:
                        # Check if it's a third-party package (not part of the workspace)
                        # Simple heuristic: if the missing module is not in our authored files
                        # This is tricky. If the import fails because of a missing dep, we skip.
                        # If it fails because of syntax/runtime error in our code, we fail.
                        
                        # Extract missing module name
                        match = re.search(r"No module named '([^']+)'", stderr)
                        if match:
                            missing_mod = match.group(1)
                            # If the missing module is not one of our authored modules, it's likely third-party
                            # Check if missing_mod is a prefix of any authored module
                            is_ours = False
                            for auth_mod in non_test_modules:
                                auth_name = auth_mod.replace('/', '.').replace('\\', '.')
                                if auth_name.endswith('.py'):
                                    auth_name = auth_name[:-3]
                                if auth_name.startswith(missing_mod) or missing_mod.startswith(auth_name):
                                    is_ours = True
                                    break
                            
                            if not is_ours:
                                skipped_imports.append(f"{module_name}: {missing_mod}")
                                continue
                    
                    failed_imports.append(f"{module_name}: {stderr[:200]}")
            except subprocess.TimeoutExpired:
                failed_imports.append(f"{module_name}: Timeout")
            except Exception as e:
                failed_imports.append(f"{module_name}: {e}")
        
        duration = time.time() - start
        
        if failed_imports:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='Import failures: ' + '; '.join(failed_imports[:5]),
                duration_s=duration,
                evidence={'failed': failed_imports, 'skipped': skipped_imports}
            )
        
        if skipped_imports:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import',
                detail='Imports skipped due to missing third-party dependencies: ' + '; '.join(skipped_imports[:5]),
                duration_s=duration,
                evidence={'skipped': skipped_imports}
            )
        
        return GateResult(
            name='smoke',
            status=PASSED,
            method='import',
            detail=f"All {len(non_test_modules)} non-test modules imported successfully",
            duration_s=duration
        )

    def _gate_tests(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find test files
        test_files = []
        for path in authored.keys():
            if path.endswith('.py'):
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
        src_dir = os.path.join(workdir, 'src')
        env = os.environ.copy()
        existing_path = env.get('PYTHONPATH', '')
        new_paths = [workdir, src_dir]
        if existing_path:
            new_paths.append(existing_path)
        env['PYTHONPATH'] = os.pathsep.join(new_paths)
        
        # Try pytest first
        try:
            # Check if pytest is installed
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], 
                           capture_output=True, timeout=10, check=True)
            
            # Run pytest
            result = subprocess.run(
                [self.python_executable, '-m', 'pytest', '-v', '--tb=short'],
                cwd=workdir,
                env=env,
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
                    duration_s=duration
                )
            else:
                # Check if failure is due to ModuleNotFoundError
                if 'ModuleNotFoundError' in result.stderr or 'ModuleNotFoundError' in result.stdout:
                    # Check if it's a third-party package
                    # Simple heuristic: if the error mentions a package not in our authored files
                    # This is complex. For now, if pytest fails, we check if it's a collection error due to missing deps.
                    # If tests ran but failed, it's FAILED.
                    # If tests couldn't be collected due to missing deps, it's SKIPPED.
                    
                    # Look for "ImportError while importing test module" or similar
                    if 'ImportError' in result.stderr or 'ModuleNotFoundError' in result.stderr:
                         # Check if the missing module is third-party
                         # This is a rough heuristic. If pytest fails to collect, we might skip.
                         # But if it collects and fails, it's FAILED.
                         # Let's assume if returncode != 0 and ModuleNotFoundError is present, it might be skipped.
                         # However, if tests actually ran and failed, we should fail.
                         # Pytest returncode 1 means tests failed. Returncode 2 means interrupted. Returncode 3 means internal error.
                         # If it's a collection error, it might be 2 or 3.
                         
                         # Let's check if any tests were collected.
                         if 'collected 0 items' in result.stdout or 'no tests ran' in result.stdout.lower():
                             return GateResult(
                                name='tests',
                                status=SKIPPED,
                                method='pytest',
                                detail='Tests skipped due to missing dependencies',
                                duration_s=duration,
                                evidence={'stderr': result.stderr[:500]}
                            )
                
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail='Tests failed',
                    duration_s=duration,
                    evidence={'stdout': result.stdout[:500], 'stderr': result.stderr[:500]}
                )
                
        except FileNotFoundError:
            # pytest not installed, use fallback
            pass
        except subprocess.TimeoutExpired:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='Tests timed out',
                duration_s=time.time() - start
            )
        except Exception:
            # pytest check failed, use fallback
            pass
        
        # Fallback: stdlib unittest
        # We need to run a script that imports each test file and runs tests
        # This is complex to do in a single subprocess call for multiple files.
        # We'll write a temporary runner script.
        
        runner_code = """
import sys
import unittest
import importlib
import os

# Add paths
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

test_files = sys.argv[1:]
suite = unittest.TestSuite()

for tf in test_files:
    try:
        # Convert path to module name
        mod_name = tf.replace('/', '.').replace('\\\\', '.')
        if mod_name.endswith('.py'):
            mod_name = mod_name[:-3]
        
        # Import module
        mod = importlib.import_module(mod_name)
        
        # Load tests from module
        loader = unittest.TestLoader()
        suite.addTests(loader.loadTestsFromModule(mod))
        
        # Also look for bare test_* functions
        for name in dir(mod):
            if name.startswith('test_'):
                func = getattr(mod, name)
                if callable(func):
                    # Wrap in a TestCase? Or just call?
                    # unittest doesn't handle bare functions well.
                    # We'll create a dummy TestCase
                    class DummyTest(unittest.TestCase):
                        def runTest(self):
                            func()
                    DummyTest.__name__ = f'Test_{name}'
                    suite.addTest(DummyTest())
                    
    except ModuleNotFoundError as e:
        print(f"SKIP: {tf}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: {tf}: {e}", file=sys.stderr)

runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)

if result.wasSuccessful():
    sys.exit(0)
else:
    sys.exit(1)
"""
        
        try:
            # Write runner script
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
            
            duration = time.time() - start
            
            # Clean up
            try:
                os.remove(runner_path)
            except:
                pass
            
            if result.returncode == 0:
                return GateResult(
                    name='tests',
                    status=PASSED,
                    method='unittest',
                    detail='All tests passed (fallback)',
                    duration_s=duration
                )
            else:
                # Check for skips
                if 'SKIP:' in result.stderr:
                    # If all tests were skipped, return SKIPPED
                    # If some ran and failed, return FAILED
                    # This is hard to distinguish. Let's assume if returncode != 0, it's FAILED unless explicitly skipped.
                    # But if the only output is SKIP, it's SKIPPED.
                    if 'ERROR:' not in result.stderr and 'FAIL:' not in result.stderr:
                         return GateResult(
                            name='tests',
                            status=SKIPPED,
                            method='unittest',
                            detail='Tests skipped due to missing dependencies',
                            duration_s=duration,
                            evidence={'stderr': result.stderr[:500]}
                        )
                
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='unittest',
                    detail='Tests failed (fallback)',
                    duration_s=duration,
                    evidence={'stdout': result.stdout[:500], 'stderr': result.stderr[:500]}
                )
                
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method='unittest',
                detail=f'Error running fallback tests: {e}',
                duration_s=time.time() - start
            )

    def _gate_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        py_files = [p for p in authored.keys() if p.endswith('.py')]
        
        if not py_files:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast',
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
        
        for rel_path in py_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                tree = ast.parse(source)
                
                for node in ast.walk(tree):
                    # Check for import opentelemetry or from opentelemetry ...
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name == 'opentelemetry' or alias.name.startswith('opentelemetry.'):
                                has_import = True
                    elif isinstance(node, ast.ImportFrom):
                        if node.module == 'opentelemetry' or (node.module and node.module.startswith('opentelemetry.')):
                            has_import = True
                    
                    # Check for calls to tracer methods
                    if isinstance(node, ast.Call):
                        func = node.func
                        # Handle method calls: obj.method()
                        if isinstance(func, ast.Attribute):
                            if func.attr in tracer_methods:
                                has_call = True
                        # Handle function calls: method()
                        elif isinstance(func, ast.Name):
                            if func.id in tracer_methods:
                                has_call = True
                                
            except Exception:
                pass
        
        duration = time.time() - start
        
        if has_import and has_call:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast',
                detail='OpenTelemetry import and tracer/span calls found',
                duration_s=duration
            )
        
        if not has_import:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast',
                detail='No OpenTelemetry import found',
                duration_s=duration
            )
        
        if not has_call:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast',
                detail='OpenTelemetry imported but no tracer/span calls found',
                duration_s=duration
            )
        
        return GateResult(
            name='telemetry',
            status=FAILED,
            method='ast',
            detail='Telemetry requirements not met',
            duration_s=duration
        )