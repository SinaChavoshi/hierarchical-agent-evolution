"""
Execution-grounded verification of agent-authored workspaces.

Five gates, each of which either runs the code or performs AST analysis on it:
    syntax     Every authored .py file must parse (`ast.parse`). Empty
               workspaces (0 Python files) return `FAILED`.
    build      Packaging metadata (`pyproject.toml`, `setup.py`, or `setup.cfg`)
               must exist and be valid/installable. When `pyproject.toml` is
               present, validate it with `tomllib` (must parse as valid TOML and
               contain at least one of `[project]`, `[build-system]`, or
               `[tool]` tables; an empty or malformed `pyproject.toml` returns
               `FAILED`), and run `pip install -e . --no-deps --no-build-isolation`
               when `pip` is available.
    smoke      Non-test authored modules must import in a subprocess with both
               `workdir` and `os.path.join(workdir, "src")` on `PYTHONPATH`. If
               no importable modules exist or any module fails with a syntax or
               runtime error, return `FAILED`. If module imports fail solely
               with `ModuleNotFoundError` for uninstalled third-party packages
               (e.g. `opentelemetry`), return `SKIPPED` (not `FAILED`).
    tests      Discover test files (`"test" in os.path.basename(p).lower()`;
               if none exist, return `FAILED`) and run them with both `workdir`
               and `os.path.join(workdir, "src")` on `PYTHONPATH`. Use `pytest`
               if installed in `self.python`; otherwise run a stdlib fallback
               subprocess that imports each test file and collects/executes BOTH
               bare `def test_*()` functions (taking 0 required args) AND
               `unittest.TestCase` subclasses (because `unittest discover`
               ignores bare `def test_*()` functions). If 0 tests are collected
               because of `ModuleNotFoundError` for an uninstalled third-party
               package, return `SKIPPED`.
    telemetry  AST analysis of authored `.py` files must find BOTH (1) an actual
               `import opentelemetry` or `from opentelemetry ... import ...`
               node AND (2) at least one AST `Call` to a tracer/span method or
               function (`start_as_current_span`, `start_span`, `get_tracer`,
               `get_tracer_provider`, `set_tracer_provider`, `get_meter`). A
               comment/docstring mention or a bare `import opentelemetry`
               without any tracer/span call must return `FAILED`.

Each result carries the `method` used to reach it. A gate that could not be
evaluated returns `SKIPPED` rather than `PASSED`, so a missing tool can never be
mistaken for a success, and `SKIPPED` gates are excluded from the fitness
denominator (`evaluated_gates` and `score_penalty`) rather than scored as
failures (`score_penalty = failed_gate_count * penalty_per_gate`).
"""

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Import artifacts module for filtering logic
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
    # Fallback if artifacts is not yet available or in a different path structure
    # This ensures the harness can still run if artifacts is missing, though it's expected to be there.
    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        return bundle
    
    def partition_bundle(bundle: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
        return bundle, {}, {}
        
    def count_source_files(bundle: Dict[str, str], suffixes: Tuple[str, ...] = ('.py',)) -> int:
        return sum(1 for p in bundle if p.endswith(suffixes))

    def is_generated_path(path: str) -> bool:
        return False

    def is_malformed_path(path: str) -> bool:
        return False

    def sanitize_path(path: str) -> str:
        return path


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
        lines = [
            f"Verification Report (Source: {self.source}, Version: {self.harness_version})",
            f"Authored Files: {self.authored_files} (Python: {self.python_files})",
            f"Score Penalty: {self.score_penalty:.2f}",
            "-" * 40
        ]
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"[{status_icon}] {g.name.upper():<10} : {g.status.upper():<8} ({g.method})")
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
    
    # Regex to match fenced code blocks with optional language and optional filename hint
    # Pattern: ```[language] [filename]\n...\n```
    # We look for lines starting with ``` followed by optional lang and optional path
    pattern = r"```(?:\w+)?\s*([^\n]*)\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    
    bundle = {}
    for header, content in matches:
        header = header.strip()
        # Try to extract a filename from the header
        # Common patterns: "path/to/file.py", "file.py", "python file.py"
        parts = header.split()
        filename = None
        for part in parts:
            if '.' in part and not part.startswith('-'):
                # Heuristic: if it looks like a filename, take it
                # Avoid taking language identifiers like 'python' unless they have dots
                if part.endswith('.py') or part.endswith('.toml') or part.endswith('.cfg') or part.endswith('.txt'):
                    filename = part
                    break
        
        if not filename:
            # If no explicit filename, we might skip or generate a placeholder
            # But the spec says "path->content map", so we need a path.
            # If we can't determine a path, we can't really place it.
            # However, often agents write ```python\n...
            # In that case, we might not have a path. 
            # Let's assume if no path is found, we skip it or use a generic name?
            # The spec implies we recover a map. If we can't identify the path, we can't map it.
            # Let's try to infer from content if it's python? No, that's guessing.
            # We will only include blocks where we can identify a path.
            continue
            
        # Sanitize the path
        clean_path = sanitize_path(filename)
        if clean_path:
            bundle[clean_path] = content
            
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
        # Check if workspace is a directory path
        if isinstance(workspace, str) and os.path.isdir(workspace):
            return self.verify_directory(workspace)
        
        # Check if workspace is a bundle (dict)
        if isinstance(workspace, dict):
            return self.verify_bundle(workspace)
            
        # Fallback to deliverable text
        if deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            if bundle:
                return self.verify_bundle(bundle, source='deliverable')
        
        # If nothing found, return empty report with failures
        return self._empty_report(source='none')

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
        
        # 1. Identify Authored Files
        if authored is None:
            # Scan directory
            all_files = {}
            for root, dirs, files in os.walk(workdir):
                # Prune excluded dirs
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.join(root, d))]
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if not is_generated_path(rel_path) and not is_malformed_path(rel_path):
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                all_files[rel_path] = f.read()
                        except Exception:
                            pass
            authored = filter_bundle(all_files)
        
        # Filter again just in case
        authored = filter_bundle(authored)
        
        python_files = {p: c for p, c in authored.items() if p.endswith('.py')}
        authored_count = len(authored)
        python_count = len(python_files)
        
        gates = []
        
        # Gate 1: Syntax
        gates.append(self._gate_syntax(python_files))
        
        # Gate 2: Build
        gates.append(self._gate_build(workdir, authored))
        
        # Gate 3: Smoke
        gates.append(self._gate_smoke(workdir, python_files))
        
        # Gate 4: Tests
        gates.append(self._gate_tests(workdir, authored))
        
        # Gate 5: Telemetry
        gates.append(self._gate_telemetry(python_files))
        
        # Calculate Penalty
        failed_count = sum(1 for g in gates if g.status == FAILED)
        score_penalty = failed_count * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_count,
            python_files=python_count,
            source=source
        )

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=FAILED, method='scan', detail='No files found'),
            GateResult(name='build', status=FAILED, method='scan', detail='No files found'),
            GateResult(name='smoke', status=FAILED, method='scan', detail='No files found'),
            GateResult(name='tests', status=FAILED, method='scan', detail='No files found'),
            GateResult(name='telemetry', status=FAILED, method='scan', detail='No files found'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=5 * self.penalty_per_gate,
            authored_files=0,
            python_files=0,
            source=source
        )

    def _gate_syntax(self, python_files: Dict[str, str]) -> GateResult:
        start = time.time()
        if not python_files:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast_parse',
                detail='No Python files found',
                duration_s=time.time() - start
            )
        
        errors = []
        for path, content in python_files.items():
            try:
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
        
        duration = time.time() - start
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast_parse',
                detail='Syntax errors found: ' + '; '.join(errors[:5]),
                duration_s=duration,
                evidence={'errors': errors}
            )
        
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast_parse',
            detail=f"All {len(python_files)} Python files parsed successfully",
            duration_s=duration
        )

    def _gate_build(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Check for packaging metadata
        has_pyproject = 'pyproject.toml' in authored
        has_setup_py = 'setup.py' in authored
        has_setup_cfg = 'setup.cfg' in authored
        
        if not (has_pyproject or has_setup_py or has_setup_cfg):
            return GateResult(
                name='build',
                status=FAILED,
                method='file_check',
                detail='No packaging metadata found (pyproject.toml, setup.py, or setup.cfg)',
                duration_s=time.time() - start
            )
        
        # Validate pyproject.toml if present
        if has_pyproject:
            content = authored['pyproject.toml']
            try:
                data = tomllib.loads(content)
                # Must contain at least one of [project], [build-system], or [tool]
                if not any(key in data for key in ['project', 'build-system', 'tool']):
                    return GateResult(
                        name='build',
                        status=FAILED,
                        method='toml_validate',
                        detail='pyproject.toml is valid TOML but missing required tables ([project], [build-system], or [tool])',
                        duration_s=time.time() - start
                    )
            except Exception as e:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='toml_validate',
                    detail=f'pyproject.toml is malformed: {e}',
                    duration_s=time.time() - start
                )
        
        # Attempt pip install
        # Check if pip is available
        try:
            subprocess.run(
                [self.python_executable, '-m', 'pip', '--version'],
                capture_output=True,
                timeout=10
            )
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
            
            duration = time.time() - start
            if result.returncode == 0:
                return GateResult(
                    name='build',
                    status=PASSED,
                    method='pip_install',
                    detail='Package installed successfully',
                    duration_s=duration
                )
            else:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip_install',
                    detail=f'pip install failed: {result.stderr[:500]}',
                    duration_s=duration,
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
                detail=f'Error running pip install: {e}',
                duration_s=time.time() - start
            )

    def _gate_smoke(self, workdir: str, python_files: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Identify non-test modules
        # Test files usually have 'test' in the name
        non_test_modules = []
        for path in python_files:
            basename = os.path.basename(path).lower()
            if 'test' not in basename:
                # Convert path to module name
                # Remove .py
                mod_path = path[:-3]
                # Replace / with .
                mod_name = mod_path.replace('/', '.').replace('\\', '.')
                # Handle __init__.py
                if mod_name.endswith('.__init__'):
                    mod_name = mod_name[:-9]
                if mod_name:
                    non_test_modules.append((path, mod_name))
        
        if not non_test_modules:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import_check',
                detail='No non-test Python modules found',
                duration_s=time.time() - start
            )
        
        # Prepare environment
        env = os.environ.copy()
        src_dir = os.path.join(workdir, 'src')
        python_path = [workdir]
        if os.path.isdir(src_dir):
            python_path.append(src_dir)
        
        env['PYTHONPATH'] = os.pathsep.join(python_path)
        
        # Try importing each module
        import_errors = []
        module_not_found_errors = []
        
        for path, mod_name in non_test_modules:
            try:
                # Run a subprocess to import the module
                code = f"import {mod_name}"
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
                    if 'ModuleNotFoundError' in stderr:
                        # Check if it's a third-party package
                        # If the module itself is not found, it's a failure.
                        # If a dependency is not found, it might be skipped.
                        # The spec says: "If module imports fail solely with ModuleNotFoundError for uninstalled third-party packages... return SKIPPED"
                        # We need to distinguish if the module itself is missing or a dep.
                        # If the error is "No module named 'mod_name'", it's a failure.
                        # If it's "No module named 'opentelemetry'", it's a skip.
                        
                        # Simple heuristic: if the module name itself is in the error, it's a failure.
                        # Otherwise, assume it's a dependency issue.
                        if f"No module named '{mod_name}'" in stderr or f"No module named \"{mod_name}\"" in stderr:
                            import_errors.append(f"{mod_name}: Module not found")
                        else:
                            module_not_found_errors.append(f"{mod_name}: Dependency missing ({stderr.strip().splitlines()[-1]})")
                    else:
                        import_errors.append(f"{mod_name}: {stderr.strip().splitlines()[-1]}")
                        
            except Exception as e:
                import_errors.append(f"{mod_name}: Execution error {e}")
        
        duration = time.time() - start
        
        if import_errors:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import_check',
                detail='Import failures: ' + '; '.join(import_errors[:5]),
                duration_s=duration,
                evidence={'errors': import_errors}
            )
        
        if module_not_found_errors:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import_check',
                detail='Skipped due to missing third-party dependencies: ' + '; '.join(module_not_found_errors[:5]),
                duration_s=duration,
                evidence={'skipped_reasons': module_not_found_errors}
            )
        
        return GateResult(
            name='smoke',
            status=PASSED,
            method='import_check',
            detail=f"All {len(non_test_modules)} non-test modules imported successfully",
            duration_s=duration
        )

    def _gate_tests(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Discover test files
        test_files = []
        for path in authored:
            if path.endswith('.py'):
                basename = os.path.basename(path).lower()
                if 'test' in basename:
                    test_files.append(path)
        
        if not test_files:
            return GateResult(
                name='tests',
                status=FAILED,
                method='discover',
                detail='No test files found',
                duration_s=time.time() - start
            )
        
        # Prepare environment
        env = os.environ.copy()
        src_dir = os.path.join(workdir, 'src')
        python_path = [workdir]
        if os.path.isdir(src_dir):
            python_path.append(src_dir)
        env['PYTHONPATH'] = os.pathsep.join(python_path)
        
        # Check if pytest is available
        pytest_available = False
        try:
            subprocess.run(
                [self.python_executable, '-m', 'pytest', '--version'],
                capture_output=True,
                timeout=10
            )
            pytest_available = True
        except Exception:
            pass
        
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
                    # Check if it was skipped due to ModuleNotFoundError
                    if 'ModuleNotFoundError' in result.stderr or 'ModuleNotFoundError' in result.stdout:
                        # If no tests were collected because of import errors
                        if 'no tests ran' in result.stdout or 'collected 0 items' in result.stdout:
                            return GateResult(
                                name='tests',
                                status=SKIPPED,
                                method='pytest',
                                detail='Tests skipped due to missing dependencies',
                                duration_s=duration
                            )
                    
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='pytest',
                        detail='Tests failed',
                        duration_s=duration,
                        evidence={'stdout': result.stdout[-1000:], 'stderr': result.stderr[-1000:]}
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
            # We need to run a script that imports each test file and runs tests
            # This is complex to do in a single subprocess call robustly.
            # We'll write a temporary runner script.
            
            runner_code = """
import sys
import os
import unittest
import importlib.util
import inspect

def run_tests(test_files):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    module_not_found = False
    
    for test_file in test_files:
        try:
            # Load module
            spec = importlib.util.spec_from_file_location("test_module", test_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules["test_module"] = module
            spec.loader.exec_module(module)
            
            # Collect tests
            # 1. unittest.TestCase subclasses
            for name in dir(module):
                obj = getattr(module, name)
                if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj.__module__ == module.__name__:
                    suite.addTest(loader.loadTestsFromTestCase(obj))
            
            # 2. Bare def test_* functions
            for name in dir(module):
                if name.startswith('test_'):
                    obj = getattr(module, name)
                    if callable(obj) and not isinstance(obj, type):
                        # Check if it takes 0 required args
                        sig = inspect.signature(obj)
                        params = sig.parameters
                        # Allow self if it's a method? No, bare functions.
                        # Check for required args
                        required_args = [p for p in params.values() if p.default == inspect.Parameter.empty and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY)]
                        if len(required_args) == 0:
                            suite.addTest(unittest.FunctionTestCase(obj))
                            
        except ModuleNotFoundError as e:
            module_not_found = True
            print(f"ModuleNotFoundError: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Error loading {test_file}: {e}", file=sys.stderr)
            
    if module_not_found and suite.countTestCases() == 0:
        print("SKIPPED_DUE_TO_IMPORT_ERROR")
        sys.exit(0)
        
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
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                    f.write(runner_code)
                    runner_path = f.name
                
                # Run runner with test files
                # Convert relative paths to absolute
                abs_test_files = [os.path.join(workdir, tf) for tf in test_files]
                
                result = subprocess.run(
                    [self.python_executable, runner_path] + abs_test_files,
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s
                )
                
                duration = time.time() - start
                os.unlink(runner_path)
                
                if 'SKIPPED_DUE_TO_IMPORT_ERROR' in result.stdout:
                    return GateResult(
                        name='tests',
                        status=SKIPPED,
                        method='stdlib_fallback',
                        detail='Tests skipped due to missing dependencies',
                        duration_s=duration
                    )
                
                if result.returncode == 0:
                    return GateResult(
                        name='tests',
                        status=PASSED,
                        method='stdlib_fallback',
                        detail='All tests passed',
                        duration_s=duration
                    )
                else:
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='stdlib_fallback',
                        detail='Tests failed',
                        duration_s=duration,
                        evidence={'stdout': result.stdout[-1000:], 'stderr': result.stderr[-1000:]}
                    )
                    
            except Exception as e:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='stdlib_fallback',
                    detail=f'Error running tests: {e}',
                    duration_s=time.time() - start
                )

    def _gate_telemetry(self, python_files: Dict[str, str]) -> GateResult:
        start = time.time()
        
        if not python_files:
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
        
        for path, content in python_files.items():
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue
            
            for node in ast.walk(tree):
                # Check for imports
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
                    # Direct function call: get_tracer(...)
                    if isinstance(func, ast.Name) and func.id in tracer_methods:
                        has_call = True
                    # Method call: tracer.start_span(...)
                    elif isinstance(func, ast.Attribute) and func.attr in tracer_methods:
                        has_call = True
        
        duration = time.time() - start
        
        if has_import and has_call:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast_analysis',
                detail='OpenTelemetry import and tracer/span calls detected',
                duration_s=duration
            )
        
        if not has_import:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='No OpenTelemetry import found',
                duration_s=duration
            )
        
        if not has_call:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='OpenTelemetry imported but no tracer/span calls found',
                duration_s=duration
            )
        
        return GateResult(
            name='telemetry',
            status=FAILED,
            method='ast_analysis',
            detail='Unknown telemetry failure',
            duration_s=duration
        )