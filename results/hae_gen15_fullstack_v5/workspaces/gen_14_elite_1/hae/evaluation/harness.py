"""
Execution-grounded verification of agent-authored workspaces.

This module implements the ExecutionHarness which verifies code by actually
running it (parsing, installing, importing, testing) rather than relying on
textual analysis of prose.
"""

import ast
import os
import sys
import subprocess
import tempfile
import shutil
import time
import re
import json
import tomllib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Iterable

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Import artifacts module if available, otherwise define fallbacks
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
        for part in parts[:-1]:
            if part in EXCLUDED_DIR_SEGMENTS:
                return True
            if any(part.endswith(s) for s in EXCLUDED_DIR_SUFFIXES):
                return True
        return False

    def is_malformed_path(path: str) -> bool:
        if not path:
            return True
        if '*' in path or '`' in path:
            return True
        if path != path.strip():
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
            path = path.rstrip('.~')
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
        passed = [g for g in evaluated if g.passed]
        return len(passed) / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files} (Python: {self.python_files})")
        lines.append(f"Score Penalty: {self.score_penalty:.2f}")
        lines.append(f"Pass Rate: {self.pass_rate:.2%}")
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
    
    # Regex to match fenced code blocks with optional language and optional filename hint
    # Pattern: ```[lang] [filename]\n content \n```
    # We look for blocks that might contain a filename in the info string or just assume sequential naming if not present.
    # However, the spec implies we recover a path->content map. 
    # Common convention in these deliverables is ```python path/to/file.py
    
    blocks = {}
    # Match ``` followed by optional info string, then content, then ```
    pattern = r'```([^\n]*)\n(.*?)```'
    matches = re.finditer(pattern, text, re.DOTALL)
    
    counter = 0
    for match in matches:
        info = match.group(1).strip()
        content = match.group(2)
        
        # Try to extract filename from info string
        # e.g., "python src/main.py" or just "src/main.py"
        filename = None
        parts = info.split()
        for part in parts:
            if '.' in part and not part.startswith('-'):
                # Heuristic: if it looks like a filename
                if part.endswith('.py') or part.endswith('.toml') or part.endswith('.cfg') or part.endswith('.txt') or part.endswith('.md'):
                    filename = part
                    break
        
        if not filename:
            # Fallback: generate a name
            filename = f"extracted_block_{counter}.py"
            counter += 1
            
        # Sanitize the path
        filename = sanitize_path(filename)
        if not filename:
            continue
            
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
        
        if workspace is not None:
            # Assume workspace is an object with a method to get files or is a dict
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace'
            elif hasattr(workspace, 'list_files'):
                # Some workspaces might return list of paths, need to read them
                # But usually get_files is preferred. If list_files returns paths, we need to read.
                # For now, assume if it's not a dict, we try to extract from deliverable if workspace is weird
                pass
        
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to get authored files
        authored_bundle = filter_bundle(bundle)
        python_files = count_source_files(authored_bundle, ('.py',))
        authored_count = len(authored_bundle)
        
        if not authored_bundle:
            # Empty workspace
            gates = [
                GateResult("syntax", FAILED, "empty", "No authored files found"),
                GateResult("build", FAILED, "empty", "No authored files found"),
                GateResult("smoke", FAILED, "empty", "No authored files found"),
                GateResult("tests", FAILED, "empty", "No authored files found"),
                GateResult("telemetry", FAILED, "empty", "No authored files found"),
            ]
            failed_count = sum(1 for g in gates if g.status == FAILED)
            return VerificationReport(
                gates=gates,
                score_penalty=failed_count * self.penalty_per_gate,
                authored_files=0,
                python_files=0,
                source=source
            )

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
            bundle = {}
            for root, dirs, files in os.walk(workdir):
                # Filter out generated dirs during walk
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if not is_generated_path(rel_path) and not is_malformed_path(rel_path):
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                bundle[rel_path] = f.read()
                        except Exception:
                            pass
            authored = bundle
            
        python_files = count_source_files(authored, ('.py',))
        authored_count = len(authored)
        
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
        
        return VerificationReport(
            gates=gates,
            score_penalty=failed_count * self.penalty_per_gate,
            authored_files=authored_count,
            python_files=python_files,
            source=source
        )

    def _gate_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        py_files = [p for p in authored if p.endswith('.py')]
        if not py_files:
            return GateResult("syntax", FAILED, "ast_parse", "No Python files found", time.time() - start)
        
        errors = []
        for path in py_files:
            full_path = os.path.join(workdir, path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
            except Exception as e:
                errors.append(f"{path}: {e}")
                
        if errors:
            return GateResult("syntax", FAILED, "ast_parse", "; ".join(errors), time.time() - start)
            
        return GateResult("syntax", PASSED, "ast_parse", f"Successfully parsed {len(py_files)} files", time.time() - start)

    def _gate_build(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Check for packaging metadata
        has_pyproject = 'pyproject.toml' in authored
        has_setup_py = 'setup.py' in authored
        has_setup_cfg = 'setup.cfg' in authored
        
        if not (has_pyproject or has_setup_py or has_setup_cfg):
            return GateResult("build", FAILED, "metadata_check", "No packaging metadata found", time.time() - start)
            
        if has_pyproject:
            try:
                with open(os.path.join(workdir, 'pyproject.toml'), 'rb') as f:
                    data = tomllib.load(f)
                # Must contain at least one of [project], [build-system], or [tool]
                if not any(k in data for k in ['project', 'build-system', 'tool']):
                    return GateResult("build", FAILED, "toml_validation", "pyproject.toml missing required tables", time.time() - start)
            except Exception as e:
                return GateResult("build", FAILED, "toml_validation", f"Invalid pyproject.toml: {e}", time.time() - start)
        
        # Try pip install
        try:
            # Check if pip is available
            subprocess.run([self.python_executable, '-m', 'pip', '--version'], 
                           capture_output=True, timeout=10, check=True)
            
            # Run pip install
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps', '--no-build-isolation']
            result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=self.timeout_s)
            
            if result.returncode != 0:
                return GateResult("build", FAILED, "pip_install", f"pip install failed: {result.stderr}", time.time() - start)
                
            return GateResult("build", PASSED, "pip_install", "Package installed successfully", time.time() - start)
            
        except FileNotFoundError:
            return GateResult("build", SKIPPED, "pip_install", "pip not available", time.time() - start)
        except subprocess.TimeoutExpired:
            return GateResult("build", FAILED, "pip_install", "pip install timed out", time.time() - start)
        except Exception as e:
            return GateResult("build", FAILED, "pip_install", f"Error running pip: {e}", time.time() - start)

    def _gate_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find non-test modules
        modules = []
        for path in authored:
            if not path.endswith('.py'):
                continue
            basename = os.path.basename(path).lower()
            if 'test' in basename:
                continue
            # Convert path to module name
            # src/foo/bar.py -> foo.bar (if src is on path)
            # We will add workdir and workdir/src to PYTHONPATH
            modules.append(path)
            
        if not modules:
            return GateResult("smoke", FAILED, "import_check", "No importable modules found", time.time() - start)
            
        # Prepare environment
        env = os.environ.copy()
        python_path = env.get('PYTHONPATH', '')
        new_paths = [workdir, os.path.join(workdir, 'src')]
        if python_path:
            env['PYTHONPATH'] = os.pathsep.join(new_paths + [python_path])
        else:
            env['PYTHONPATH'] = os.pathsep.join(new_paths)
            
        # Try importing each module in a subprocess
        # We do this one by one to isolate failures, or all at once?
        # Spec says: "If module imports fail solely with ModuleNotFoundError for uninstalled third-party packages... return SKIPPED"
        # This implies we need to distinguish between syntax/runtime errors and missing deps.
        
        import_errors = []
        missing_deps = []
        
        for mod_path in modules:
            # Determine module name relative to workdir or src
            # If path is src/foo/bar.py, module is foo.bar
            # If path is foo/bar.py, module is foo.bar
            
            rel_path = mod_path
            if rel_path.startswith('src/'):
                rel_path = rel_path[4:]
            
            # Convert to module name
            mod_name = rel_path.replace('/', '.').replace('\\', '.')
            if mod_name.endswith('.py'):
                mod_name = mod_name[:-3]
                
            # Skip __init__.py as a module to import directly? 
            # Usually importing the package is enough. But spec says "Non-test authored modules must import".
            # If it's __init__.py, importing the package name works.
            if mod_name.endswith('.__init__'):
                mod_name = mod_name[:-9]
            elif mod_name == '__init__':
                continue # Root init?
                
            if not mod_name:
                continue

            # Run import check
            code = f"import {mod_name}"
            try:
                result = subprocess.run(
                    [self.python_executable, '-c', code],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode != 0:
                    stderr = result.stderr
                    if 'ModuleNotFoundError' in stderr:
                        # Check if it's a third party package
                        # If the error is about the module itself, it's a failure.
                        # If it's about a dependency, it's skipped.
                        # Heuristic: if the module name is in the error, it's a failure.
                        # If the error is about something else, it's a dep.
                        if mod_name in stderr or f"No module named '{mod_name}'" in stderr:
                            import_errors.append(f"{mod_name}: {stderr.strip()}")
                        else:
                            missing_deps.append(f"{mod_name}: {stderr.strip()}")
                    else:
                        import_errors.append(f"{mod_name}: {stderr.strip()}")
            except Exception as e:
                import_errors.append(f"{mod_name}: {e}")
                
        if import_errors:
            return GateResult("smoke", FAILED, "import_check", "; ".join(import_errors), time.time() - start)
            
        if missing_deps:
            return GateResult("smoke", SKIPPED, "import_check", f"Missing dependencies: {missing_deps[0]}", time.time() - start)
            
        return GateResult("smoke", PASSED, "import_check", f"Successfully imported {len(modules)} modules", time.time() - start)

    def _gate_tests(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find test files
        test_files = []
        for path in authored:
            if not path.endswith('.py'):
                continue
            basename = os.path.basename(path).lower()
            if 'test' in basename:
                test_files.append(path)
                
        if not test_files:
            return GateResult("tests", FAILED, "test_discovery", "No test files found", time.time() - start)
            
        # Prepare environment
        env = os.environ.copy()
        python_path = env.get('PYTHONPATH', '')
        new_paths = [workdir, os.path.join(workdir, 'src')]
        if python_path:
            env['PYTHONPATH'] = os.pathsep.join(new_paths + [python_path])
        else:
            env['PYTHONPATH'] = os.pathsep.join(new_paths)
            
        # Check if pytest is available
        pytest_available = False
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], 
                           capture_output=True, timeout=10, check=True)
            pytest_available = True
        except Exception:
            pass
            
        if pytest_available:
            # Run pytest
            # We need to run pytest on the test files
            # Use absolute paths or relative? Relative to workdir is safer.
            cmd = [self.python_executable, '-m', 'pytest', '-v'] + test_files
            try:
                result = subprocess.run(
                    cmd,
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s
                )
                if result.returncode == 0:
                    return GateResult("tests", PASSED, "pytest", "All tests passed", time.time() - start)
                else:
                    # Check if it failed due to missing deps
                    if 'ModuleNotFoundError' in result.stderr or 'ModuleNotFoundError' in result.stdout:
                        # Heuristic: if the error is about a test dependency, skip
                        # But if tests failed, it's failed.
                        # Spec: "If 0 tests are collected because of ModuleNotFoundError... return SKIPPED"
                        if 'collected 0 items' in result.stdout or 'no tests ran' in result.stdout:
                             return GateResult("tests", SKIPPED, "pytest", "No tests collected due to missing deps", time.time() - start)
                    return GateResult("tests", FAILED, "pytest", f"Tests failed: {result.stderr[-500:]}", time.time() - start)
            except subprocess.TimeoutExpired:
                return GateResult("tests", FAILED, "pytest", "Tests timed out", time.time() - start)
            except Exception as e:
                return GateResult("tests", FAILED, "pytest", f"Error running pytest: {e}", time.time() - start)
        else:
            # Stdlib fallback
            # Run a script that imports each test file and collects/executes tests
            # This is complex to do in a single subprocess call robustly.
            # We'll write a temporary runner script.
            
            runner_code = """
import sys
import os
import unittest
import importlib.util
import inspect

def run_tests(test_files):
    suite = unittest.TestSuite()
    collected = 0
    errors = []
    
    for tf in test_files:
        try:
            # Add directory to path
            dir_path = os.path.dirname(tf)
            if dir_path not in sys.path:
                sys.path.insert(0, dir_path)
            
            # Import module
            spec = importlib.util.spec_from_file_location("test_module", tf)
            if spec is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Collect unittest.TestCase subclasses
            for name, obj in module.__dict__.items():
                if isinstance(obj, type) and issubclass(obj, unittest.TestCase):
                    suite.addTest(unittest.makeSuite(obj))
                    collected += 1
                    
            # Collect bare test_* functions
            for name, obj in module.__dict__.items():
                if name.startswith('test_') and callable(obj):
                    # Check if it takes 0 required args
                    try:
                        sig = inspect.signature(obj)
                        params = sig.parameters
                        required_args = [p for p in params.values() if p.default is inspect.Parameter.empty and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)]
                        if len(required_args) == 0:
                            # Wrap in a TestCase
                            class TestWrapper(unittest.TestCase):
                                def test_wrapper(self):
                                    obj()
                            TestWrapper.__name__ = f"Test_{name}"
                            suite.addTest(TestWrapper())
                            collected += 1
                    except Exception:
                        pass
        except Exception as e:
            errors.append(f"{tf}: {e}")
            
    if collected == 0:
        if errors:
             # Check if errors are ModuleNotFoundError
             if any('ModuleNotFoundError' in str(e) for e in errors):
                 print("SKIPPED")
                 sys.exit(0)
        print("FAILED: No tests collected")
        sys.exit(1)
        
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("PASSED")
        sys.exit(0)
    else:
        print("FAILED")
        sys.exit(1)

if __name__ == '__main__':
    run_tests(sys.argv[1:])
"""
            runner_path = os.path.join(workdir, '_test_runner.py')
            with open(runner_path, 'w') as f:
                f.write(runner_code)
                
            try:
                result = subprocess.run(
                    [self.python_executable, runner_path] + test_files,
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s
                )
                if result.returncode == 0:
                    if 'SKIPPED' in result.stdout:
                        return GateResult("tests", SKIPPED, "stdlib_fallback", "No tests collected due to missing deps", time.time() - start)
                    return GateResult("tests", PASSED, "stdlib_fallback", "All tests passed", time.time() - start)
                else:
                    return GateResult("tests", FAILED, "stdlib_fallback", f"Tests failed: {result.stderr[-500:]}", time.time() - start)
            except Exception as e:
                return GateResult("tests", FAILED, "stdlib_fallback", f"Error running tests: {e}", time.time() - start)
            finally:
                if os.path.exists(runner_path):
                    os.remove(runner_path)

    def _gate_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        py_files = [p for p in authored if p.endswith('.py')]
        if not py_files:
            return GateResult("telemetry", FAILED, "ast_analysis", "No Python files found", time.time() - start)
            
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
            full_path = os.path.join(workdir, path)
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
                        # Handle method calls: obj.method()
                        if isinstance(func, ast.Attribute):
                            if func.attr in tracer_methods:
                                has_call = True
                        # Handle function calls: func()
                        elif isinstance(func, ast.Name):
                            if func.id in tracer_methods:
                                has_call = True
                                
            except Exception:
                continue
                
        if not has_import:
            return GateResult("telemetry", FAILED, "ast_analysis", "No opentelemetry import found", time.time() - start)
            
        if not has_call:
            return GateResult("telemetry", FAILED, "ast_analysis", "No tracer/span method calls found", time.time() - start)
            
        return GateResult("telemetry", PASSED, "ast_analysis", "OpenTelemetry usage detected", time.time() - start)