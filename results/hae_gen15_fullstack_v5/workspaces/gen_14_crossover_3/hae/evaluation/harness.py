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

The V1 verifier this replaces executed nothing in three of its four gates:
`build` was a filename substring match, `smoke` was `len(files) >= 3`, and
`telemetry` searched text that included the CEO's markdown prose, so an essay
mentioning OpenTelemetry passed. Six generations were selected on those
signals. The whole design rule here is that no gate may be satisfiable by
writing about it.

Usage:
    from hae.evaluation.harness import ExecutionHarness

    harness = ExecutionHarness()
    report = harness.verify_workspace(workspace, deliverable_text)
    print(report.summary(), report.gate_status)
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hae.evaluation.artifacts import (
    filter_bundle,
    is_generated_path,
    is_malformed_path,
    partition_bundle,
)

SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Telemetry gate constants
TELEMETRY_TRACER_METHODS = {
    'start_as_current_span',
    'start_span',
    'get_tracer',
    'get_tracer_provider',
    'set_tracer_provider',
    'get_meter',
}


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
        lines = []
        lines.append(f"Verification Report ({self.source}, {self.harness_version})")
        lines.append(f"Authored files: {self.authored_files}, Python files: {self.python_files}")
        lines.append(f"Score penalty: {self.score_penalty:.2f}")
        lines.append("")
        for g in self.gates:
            status_str = g.status.upper()
            lines.append(f"  [{status_str}] {g.name}: {g.detail}")
        lines.append("")
        lines.append(f"Pass rate: {self.pass_rate:.2%} of evaluated gates")
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
                    "evidence": g.evidence,
                }
                for g in self.gates
            ],
            "score_penalty": self.score_penalty,
            "authored_files": self.authored_files,
            "python_files": self.python_files,
            "source": self.source,
            "harness_version": self.harness_version,
            "gate_status": self.gate_status,
            "gate_detail": self.gate_detail,
            "pass_rate": self.pass_rate,
        }


def extract_code_blocks(text: str) -> Dict[str, str]:
    """Recovers a path->content map from fenced code blocks in a deliverable.

    A last resort, used only when a firm produced no live workspace. Only the
    contents of fenced blocks are returned; the surrounding prose is discarded
    and never reaches a gate.
    """
    if not text:
        return {}

    # Pattern to match fenced code blocks with optional language and optional filename
    # Matches: ```python\n...\n``` or ```path/to/file.py\n...\n```
    # We look for blocks that start with ``` followed by optional language/filename
    # and end with ```
    
    blocks = {}
    # Regex to find fenced code blocks
    # Group 1: optional language/filename line
    # Group 2: content
    pattern = r'```([^\n]*)\n(.*?)```'
    
    for match in re.finditer(pattern, text, re.DOTALL):
        header = match.group(1).strip()
        content = match.group(2)
        
        # Try to determine path from header
        # If header looks like a path (contains / or .py), use it
        # Otherwise, if it's just a language tag like 'python', we can't determine path
        # In that case, we might skip it or use a generic name, but the spec says
        # "path->content map". If we can't determine path, we can't map it.
        # However, often agents write ```python\n# path/to/file.py\n...
        # Let's try to extract path from header first.
        
        path = None
        if header:
            # Check if header contains a path-like string
            # Common patterns: "python", "path/to/file.py", "file.py"
            parts = header.split()
            for part in parts:
                if '.' in part and '/' in part or part.endswith('.py'):
                    path = part
                    break
            
            # If no path found in header, check first line of content for a comment
            if not path:
                first_line = content.split('\n')[0].strip()
                # Look for patterns like "# path/to/file.py" or "# file.py"
                if first_line.startswith('#'):
                    comment = first_line[1:].strip()
                    if '.' in comment and ('/' in comment or comment.endswith('.py')):
                        path = comment
        
        if path:
            # Sanitize path slightly (remove leading/trailing whitespace)
            path = path.strip()
            if path:
                blocks[path] = content

    return blocks


class ExecutionHarness:
    """Materializes a workspace bundle and verifies it by actually running it."""

    def __init__(self, timeout_s: int = DEFAULT_TIMEOUT_S, penalty_per_gate: float = DEFAULT_PENALTY_PER_GATE, python_executable: Optional[str] = None):
        self.timeout_s = timeout_s
        self.penalty_per_gate = penalty_per_gate
        self.python = python_executable or sys.executable

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
            # Try to get bundle from workspace
            try:
                if hasattr(workspace, 'export_bundle'):
                    bundle = workspace.export_bundle()
                    source = 'workspace'
                elif hasattr(workspace, 'files'):
                    # Assume it's a dict-like or has a files attribute
                    bundle = dict(workspace.files)
                    source = 'workspace'
                elif isinstance(workspace, dict):
                    bundle = workspace
                    source = 'workspace'
            except Exception:
                bundle = {}

        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'

        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter out generated and malformed paths
        filtered_bundle = filter_bundle(bundle)
        
        # Count authored files
        authored_files = len(filtered_bundle)
        python_files = sum(1 for p in filtered_bundle if p.endswith('.py'))

        if authored_files == 0:
            # Empty workspace
            gates = [
                GateResult(name='syntax', status=FAILED, method='count', detail='No authored files found.'),
                GateResult(name='build', status=FAILED, method='count', detail='No authored files found.'),
                GateResult(name='smoke', status=FAILED, method='count', detail='No authored files found.'),
                GateResult(name='tests', status=FAILED, method='count', detail='No authored files found.'),
                GateResult(name='telemetry', status=FAILED, method='count', detail='No authored files found.'),
            ]
            failed_count = sum(1 for g in gates if g.status == FAILED)
            return VerificationReport(
                gates=gates,
                score_penalty=failed_count * self.penalty_per_gate,
                authored_files=0,
                python_files=0,
                source=source,
            )

        # Create temp dir and write files
        with tempfile.TemporaryDirectory() as tmpdir:
            for path, content in filtered_bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)

            return self.verify_directory(tmpdir, authored=filtered_bundle, source=source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if authored is None:
            # Scan directory for authored files
            authored = {}
            for root, dirs, files in os.walk(workdir):
                # Skip generated dirs
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

        authored_files = len(authored)
        python_files = sum(1 for p in authored if p.endswith('.py'))

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
            authored_files=authored_files,
            python_files=python_files,
            source=source,
        )

    def _gate_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        py_files = [p for p in authored if p.endswith('.py')]
        
        if not py_files:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='No Python files found.',
                duration_s=time.time() - start,
            )

        errors = []
        for path in py_files:
            full_path = os.path.join(workdir, path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                ast.parse(content, filename=path)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
            except Exception as e:
                errors.append(f"{path}: {e}")

        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='Syntax errors found: ' + '; '.join(errors),
                duration_s=time.time() - start,
                evidence={'errors': errors},
            )

        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast.parse',
            detail=f"All {len(py_files)} Python files parsed successfully.",
            duration_s=time.time() - start,
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
                method='metadata_check',
                detail='No packaging metadata (pyproject.toml, setup.py, or setup.cfg) found.',
                duration_s=time.time() - start,
            )

        # Validate pyproject.toml if present
        if has_pyproject:
            try:
                with open(os.path.join(workdir, 'pyproject.toml'), 'r', encoding='utf-8') as f:
                    content = f.read()
                data = tomllib.loads(content)
                if not any(key in data for key in ('project', 'build-system', 'tool')):
                    return GateResult(
                        name='build',
                        status=FAILED,
                        method='tomllib',
                        detail='pyproject.toml is valid TOML but missing [project], [build-system], or [tool] tables.',
                        duration_s=time.time() - start,
                    )
            except Exception as e:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='tomllib',
                    detail=f'pyproject.toml is invalid: {e}',
                    duration_s=time.time() - start,
                )

        # Try to install
        try:
            # Check if pip is available
            subprocess.run(
                [self.python, '-m', 'pip', '--version'],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip_install',
                detail='pip not available or failed to check version.',
                duration_s=time.time() - start,
            )

        try:
            subprocess.run(
                [self.python, '-m', 'pip', 'install', '-e', '.', '--no-deps', '--no-build-isolation'],
                cwd=workdir,
                capture_output=True,
                timeout=self.timeout_s,
            )
            return GateResult(
                name='build',
                status=PASSED,
                method='pip_install',
                detail='Package installed successfully.',
                duration_s=time.time() - start,
            )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip_install',
                detail='pip install timed out.',
                duration_s=time.time() - start,
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip_install',
                detail=f'pip install failed: {e}',
                duration_s=time.time() - start,
            )

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
            # Remove .py extension
            module_path = path[:-3]
            # Replace slashes with dots
            module_name = module_path.replace('/', '.').replace('\\', '.')
            # Handle __init__.py
            if module_name.endswith('.__init__'):
                module_name = module_name[:-9]
            if module_name:
                modules.append(module_name)

        if not modules:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='No non-test Python modules found.',
                duration_s=time.time() - start,
            )

        # Try to import each module in a subprocess
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join([workdir, os.path.join(workdir, 'src')])
        
        import_errors = []
        module_not_found_errors = []
        
        for module_name in modules:
            try:
                result = subprocess.run(
                    [self.python, '-c', f'import {module_name}'],
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    timeout=self.timeout_s,
                )
                if result.returncode != 0:
                    stderr = result.stderr.decode('utf-8', errors='replace')
                    if 'ModuleNotFoundError' in stderr:
                        # Check if it's a third-party package
                        # If the module itself is not found, it's a failure.
                        # If a dependency is not found, it might be skipped.
                        # The spec says: "If module imports fail solely with ModuleNotFoundError for uninstalled third-party packages... return SKIPPED"
                        # This implies if the module itself is found but its deps are not.
                        # If the module itself is not found, it's a failure.
                        # How to distinguish? If the error is "No module named 'module_name'", it's a failure.
                        # If it's "No module named 'opentelemetry'", it's a skip.
                        if f"No module named '{module_name}'" in stderr:
                            import_errors.append(f"{module_name}: Module not found")
                        else:
                            module_not_found_errors.append(f"{module_name}: {stderr.strip()}")
                    else:
                        import_errors.append(f"{module_name}: {stderr.strip()}")
            except subprocess.TimeoutExpired:
                import_errors.append(f"{module_name}: Timeout")
            except Exception as e:
                import_errors.append(f"{module_name}: {e}")

        if import_errors:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='Import errors: ' + '; '.join(import_errors),
                duration_s=time.time() - start,
                evidence={'errors': import_errors},
            )

        if module_not_found_errors:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import',
                detail='Modules imported but dependencies missing: ' + '; '.join(module_not_found_errors),
                duration_s=time.time() - start,
                evidence={'missing_deps': module_not_found_errors},
            )

        return GateResult(
            name='smoke',
            status=PASSED,
            method='import',
            detail=f"All {len(modules)} non-test modules imported successfully.",
            duration_s=time.time() - start,
        )

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
            return GateResult(
                name='tests',
                status=FAILED,
                method='discover',
                detail='No test files found.',
                duration_s=time.time() - start,
            )

        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join([workdir, os.path.join(workdir, 'src')])

        # Check if pytest is available
        try:
            subprocess.run(
                [self.python, '-m', 'pytest', '--version'],
                capture_output=True,
                timeout=10,
            )
            has_pytest = True
        except Exception:
            has_pytest = False

        if has_pytest:
            try:
                result = subprocess.run(
                    [self.python, '-m', 'pytest', '-v', '--tb=short'],
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    timeout=self.timeout_s,
                )
                stdout = result.stdout.decode('utf-8', errors='replace')
                stderr = result.stderr.decode('utf-8', errors='replace')
                
                if result.returncode == 0:
                    return GateResult(
                        name='tests',
                        status=PASSED,
                        method='pytest',
                        detail='All tests passed.',
                        duration_s=time.time() - start,
                    )
                else:
                    # Check if it's a collection error due to missing modules
                    if 'ModuleNotFoundError' in stderr or 'ModuleNotFoundError' in stdout:
                        # If no tests were collected, it might be skipped
                        if 'no tests ran' in stdout or 'collected 0 items' in stdout:
                            return GateResult(
                                name='tests',
                                status=SKIPPED,
                                method='pytest',
                                detail='Tests not collected due to missing dependencies.',
                                duration_s=time.time() - start,
                            )
                    
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='pytest',
                        detail='Tests failed.',
                        duration_s=time.time() - start,
                        evidence={'stdout': stdout, 'stderr': stderr},
                    )
            except subprocess.TimeoutExpired:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail='Tests timed out.',
                    duration_s=time.time() - start,
                )
            except Exception as e:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f'Pytest execution failed: {e}',
                    duration_s=time.time() - start,
                )
        else:
            # Stdlib fallback
            # Run a script that imports test files and collects/executes tests
            script = """
import sys
import os
import unittest
import importlib.util

def run_tests():
    test_files = sys.argv[1:]
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    for test_file in test_files:
        try:
            spec = importlib.util.spec_from_file_location("test_module", test_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules["test_module"] = module
            spec.loader.exec_module(module)
            
            # Collect unittest.TestCase subclasses
            for name in dir(module):
                obj = getattr(module, name)
                if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj is not unittest.TestCase:
                    suite.addTests(loader.loadTestsFromTestCase(obj))
            
            # Collect bare test_* functions
            for name in dir(module):
                if name.startswith('test_'):
                    obj = getattr(module, name)
                    if callable(obj) and not isinstance(obj, type):
                        # Check if it takes 0 required args
                        import inspect
                        sig = inspect.signature(obj)
                        params = sig.parameters
                        # Check if all params have defaults or are *args/**kwargs
                        has_required = False
                        for p in params.values():
                            if p.default is inspect.Parameter.empty and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                                has_required = True
                                break
                        if not has_required:
                            suite.addTest(unittest.FunctionTestCase(obj))
        except Exception as e:
            print(f"Error loading {test_file}: {e}", file=sys.stderr)
            if 'ModuleNotFoundError' in str(e):
                print("MODULE_NOT_FOUND", file=sys.stderr)
                sys.exit(2)
            sys.exit(1)
            
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == '__main__':
    run_tests()
"""
        script_path = os.path.join(workdir, '_run_tests.py')
        with open(script_path, 'w') as f:
            f.write(script)
        
        try:
            result = subprocess.run(
                [self.python, script_path] + test_files,
                cwd=workdir,
                env=env,
                capture_output=True,
                timeout=self.timeout_s,
            )
            stdout = result.stdout.decode('utf-8', errors='replace')
            stderr = result.stderr.decode('utf-8', errors='replace')
            
            if result.returncode == 0:
                return GateResult(
                    name='tests',
                    status=PASSED,
                    method='stdlib_fallback',
                    detail='All tests passed.',
                    duration_s=time.time() - start,
                )
            elif result.returncode == 2:
                return GateResult(
                    name='tests',
                    status=SKIPPED,
                    method='stdlib_fallback',
                    detail='Tests not collected due to missing dependencies.',
                    duration_s=time.time() - start,
                )
            else:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='stdlib_fallback',
                    detail='Tests failed.',
                    duration_s=time.time() - start,
                    evidence={'stdout': stdout, 'stderr': stderr},
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='tests',
                status=FAILED,
                method='stdlib_fallback',
                detail='Tests timed out.',
                duration_s=time.time() - start,
            )
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method='stdlib_fallback',
                detail=f'Test execution failed: {e}',
                duration_s=time.time() - start,
            )
        finally:
            if os.path.exists(script_path):
                os.remove(script_path)

    def _gate_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        py_files = [p for p in authored if p.endswith('.py')]
        if not py_files:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast',
                detail='No Python files found.',
                duration_s=time.time() - start,
            )

        has_import = False
        has_call = False

        for path in py_files:
            full_path = os.path.join(workdir, path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                tree = ast.parse(content, filename=path)
                
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
                        if isinstance(func, ast.Attribute):
                            if func.attr in TELEMETRY_TRACER_METHODS:
                                has_call = True
                        elif isinstance(func, ast.Name):
                            if func.id in TELEMETRY_TRACER_METHODS:
                                has_call = True
                                
            except Exception:
                pass

        if not has_import:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast',
                detail='No import of opentelemetry found.',
                duration_s=time.time() - start,
            )
        
        if not has_call:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast',
                detail='No call to tracer/span methods found.',
                duration_s=time.time() - start,
            )

        return GateResult(
            name='telemetry',
            status=PASSED,
            method='ast',
            detail='OpenTelemetry import and tracer/span calls found.',
            duration_s=time.time() - start,
        )