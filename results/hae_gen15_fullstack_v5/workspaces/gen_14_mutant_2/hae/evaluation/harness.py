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

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Try to import tomllib (Python 3.11+) or tomli (fallback)
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

from hae.evaluation.artifacts import filter_bundle, count_source_files

SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

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
        lines.append(f"Verification Report ({self.source})")
        lines.append(f"Authored files: {self.authored_files}, Python files: {self.python_files}")
        lines.append(f"Score Penalty: {self.score_penalty:.2f}")
        lines.append("")
        for g in self.gates:
            status_str = g.status.upper()
            lines.append(f"  [{status_str}] {g.name}: {g.detail}")
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
    
    # Regex to match fenced code blocks with optional language identifier
    # Pattern: ```[language]\n<content>\n```
    # We look for blocks that might contain file paths in the language identifier
    # e.g. ```python:hae/evaluation/harness.py
    # or just ```python
    
    blocks = {}
    # Find all fenced blocks
    pattern = r"```(?:([^\n]*)\n)?(.*?)```"
    matches = re.finditer(pattern, text, re.DOTALL)
    
    for match in matches:
        lang_line = match.group(1)
        content = match.group(2)
        
        if not content:
            continue
            
        # Try to extract path from language line
        # Common patterns: "python:path/to/file.py", "path/to/file.py", "python"
        path = None
        if lang_line:
            lang_line = lang_line.strip()
            if ':' in lang_line:
                parts = lang_line.split(':', 1)
                # If it looks like a path (contains / or .py), use it
                if '/' in parts[1] or parts[1].endswith('.py'):
                    path = parts[1].strip()
            elif lang_line.endswith('.py') or '/' in lang_line:
                path = lang_line.strip()
        
        if path:
            # Normalize path separators
            path = path.replace('\\', '/')
            blocks[path] = content
            
    return blocks

class ExecutionHarness:
    """Materializes a workspace bundle and verifies it by actually running it."""

    def __init__(self, timeout_s: int=DEFAULT_TIMEOUT_S, penalty_per_gate: float=DEFAULT_PENALTY_PER_GATE, python_executable: Optional[str]=None):
        self.timeout_s = timeout_s
        self.penalty_per_gate = penalty_per_gate
        self.python_executable = python_executable or sys.executable

    def verify_workspace(self, workspace: Any=None, deliverable_text: str='') -> VerificationReport:
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
            # Try to get bundle from workspace object
            if hasattr(workspace, 'export_bundle'):
                try:
                    bundle = workspace.export_bundle()
                except Exception:
                    bundle = {}
            elif isinstance(workspace, dict):
                bundle = workspace
                
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        if not bundle:
            # Empty workspace
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str='bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter out generated/malformed paths
        filtered_bundle = filter_bundle(bundle)
        
        if not filtered_bundle:
            return self._empty_report(source)
            
        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to temp dir
            for path, content in filtered_bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    
            return self.verify_directory(tmpdir, authored=filtered_bundle, source=source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]]=None, source: str='directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if authored is None:
            # Scan directory for files
            authored = {}
            for root, dirs, files in os.walk(workdir):
                # Skip hidden dirs and common junk
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'venv', '.venv')]
                for file in files:
                    if file.endswith('.py'):
                        rel_path = os.path.relpath(os.path.join(root, file), workdir)
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                authored[rel_path] = f.read()
                        except Exception:
                            pass
                            
        # Filter again just in case
        filtered_authored = filter_bundle(authored)
        
        python_files = count_source_files(filtered_authored, ('.py',))
        authored_files = len(filtered_authored)
        
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._gate_syntax(workdir, filtered_authored))
        
        # 2. Build Gate
        gates.append(self._gate_build(workdir, filtered_authored))
        
        # 3. Smoke Gate
        gates.append(self._gate_smoke(workdir, filtered_authored))
        
        # 4. Tests Gate
        gates.append(self._gate_tests(workdir, filtered_authored))
        
        # 5. Telemetry Gate
        gates.append(self._gate_telemetry(workdir, filtered_authored))
        
        # Calculate penalty
        failed_count = sum(1 for g in gates if g.status == FAILED)
        score_penalty = failed_count * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files,
            python_files=python_files,
            source=source,
            harness_version='3.0-execution'
        )

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=FAILED, method='empty', detail='No Python files found'),
            GateResult(name='build', status=FAILED, method='empty', detail='No Python files found'),
            GateResult(name='smoke', status=FAILED, method='empty', detail='No Python files found'),
            GateResult(name='tests', status=FAILED, method='empty', detail='No Python files found'),
            GateResult(name='telemetry', status=FAILED, method='empty', detail='No Python files found'),
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
        py_files = [p for p in bundle.keys() if p.endswith('.py')]
        
        if not py_files:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='No Python files to parse',
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
                detail='Syntax errors found: ' + '; '.join(errors),
                duration_s=time.time() - start
            )
            
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast.parse',
            detail=f"All {len(py_files)} Python files parsed successfully",
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
                detail='No packaging metadata (pyproject.toml, setup.py, or setup.cfg) found',
                duration_s=time.time() - start
            )
            
        # Validate pyproject.toml if present
        if has_pyproject:
            content = bundle['pyproject.toml']
            if tomllib is None:
                return GateResult(
                    name='build',
                    status=SKIPPED,
                    method='tomllib',
                    detail='tomllib/tomli not available for validation',
                    duration_s=time.time() - start
                )
                
            try:
                data = tomllib.loads(content)
                # Must contain at least one of these tables
                if not any(k in data for k in ('project', 'build-system', 'tool')):
                    return GateResult(
                        name='build',
                        status=FAILED,
                        method='tomllib',
                        detail='pyproject.toml missing required tables ([project], [build-system], or [tool])',
                        duration_s=time.time() - start
                    )
            except Exception as e:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='tomllib',
                    detail=f'pyproject.toml is malformed: {e}',
                    duration_s=time.time() - start
                )
                
        # Try pip install
        try:
            # Check if pip is available
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
                detail='pip not available',
                duration_s=time.time() - start
            )
            
        try:
            subprocess.run(
                [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps', '--no-build-isolation'],
                cwd=workdir,
                capture_output=True,
                timeout=self.timeout_s
            )
            return GateResult(
                name='build',
                status=PASSED,
                method='pip_install',
                detail='Package installed successfully',
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
                detail=f'pip install failed: {e}',
                duration_s=time.time() - start
            )

    def _gate_smoke(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find non-test modules
        modules = []
        for path in bundle.keys():
            if not path.endswith('.py'):
                continue
            basename = os.path.basename(path).lower()
            if 'test' in basename:
                continue
            # Convert path to module name
            # e.g. hae/evaluation/harness.py -> hae.evaluation.harness
            mod_name = path.replace('/', '.').replace('\\', '.').replace('.py', '')
            modules.append(mod_name)
            
        if not modules:
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
        current_pp = env.get('PYTHONPATH', '')
        new_pp = f"{workdir}:{src_dir}"
        if current_pp:
            new_pp = f"{new_pp}:{current_pp}"
        env['PYTHONPATH'] = new_pp
        
        # Try importing each module
        import_errors = []
        module_not_found_errors = []
        
        for mod_name in modules:
            try:
                result = subprocess.run(
                    [self.python_executable, '-c', f'import {mod_name}'],
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    timeout=10
                )
                if result.returncode != 0:
                    stderr = result.stderr.decode('utf-8', errors='replace')
                    if 'ModuleNotFoundError' in stderr:
                        # Check if it's a local module or third-party
                        # If the missing module is one of our own, it's a failure
                        # If it's something like 'opentelemetry', it's skipped
                        # We check if the missing module name appears in our list of modules
                        # This is a heuristic. If the error is about a module we are trying to import,
                        # it's a failure. If it's about a dependency, it's skipped.
                        # However, if we are importing 'hae.evaluation.harness' and it fails because
                        # 'opentelemetry' is missing, that's a skip.
                        # If we are importing 'hae.evaluation.harness' and it fails because
                        # 'hae.evaluation.artifacts' is missing, that's a failure.
                        
                        # Simple heuristic: if the missing module is in our bundle, it's a failure.
                        # Otherwise, it's a skip.
                        missing_mod = None
                        for line in stderr.splitlines():
                            if "No module named" in line:
                                # Extract module name
                                # "ModuleNotFoundError: No module named 'opentelemetry'"
                                parts = line.split("'")
                                if len(parts) > 1:
                                    missing_mod = parts[1]
                                    break
                        
                        if missing_mod and any(m.startswith(missing_mod) or missing_mod.startswith(m) for m in modules):
                            import_errors.append(f"{mod_name}: {stderr}")
                        else:
                            module_not_found_errors.append(f"{mod_name}: {stderr}")
                    else:
                        import_errors.append(f"{mod_name}: {stderr}")
            except subprocess.TimeoutExpired:
                import_errors.append(f"{mod_name}: timeout")
            except Exception as e:
                import_errors.append(f"{mod_name}: {e}")
                    
        if import_errors:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='Import errors: ' + '; '.join(import_errors),
                duration_s=time.time() - start
            )
            
        if module_not_found_errors:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import',
                detail='Third-party dependencies missing: ' + '; '.join(module_not_found_errors),
                duration_s=time.time() - start
            )
            
        return GateResult(
            name='smoke',
            status=PASSED,
            method='import',
            detail=f"All {len(modules)} modules imported successfully",
            duration_s=time.time() - start
        )

    def _gate_tests(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find test files
        test_files = []
        for path in bundle.keys():
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
                detail='No test files found',
                duration_s=time.time() - start
            )
            
        # Prepare PYTHONPATH
        src_dir = os.path.join(workdir, 'src')
        env = os.environ.copy()
        current_pp = env.get('PYTHONPATH', '')
        new_pp = f"{workdir}:{src_dir}"
        if current_pp:
            new_pp = f"{new_pp}:{current_pp}"
        env['PYTHONPATH'] = new_pp
        
        # Try pytest first
        try:
            subprocess.run(
                [self.python_executable, '-m', 'pytest', '--version'],
                capture_output=True,
                timeout=10
            )
            # Run pytest
            result = subprocess.run(
                [self.python_executable, '-m', 'pytest', '-v', '--tb=short'] + test_files,
                cwd=workdir,
                env=env,
                capture_output=True,
                timeout=self.timeout_s
            )
            stdout = result.stdout.decode('utf-8', errors='replace')
            stderr = result.stderr.decode('utf-8', errors='replace')
            
            if result.returncode == 0:
                return GateResult(
                    name='tests',
                    status=PASSED,
                    method='pytest',
                    detail='All tests passed',
                    duration_s=time.time() - start
                )
            else:
                # Check for ModuleNotFoundError
                if 'ModuleNotFoundError' in stderr or 'ModuleNotFoundError' in stdout:
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
                    detail=f'Tests failed: {stderr[:500]}',
                    duration_s=time.time() - start
                )
        except Exception:
            # Fallback to stdlib unittest
            pass
            
        # Stdlib fallback
        # We need to run a script that imports each test file and runs tests
        test_script = []
        test_script.append("import sys")
        test_script.append("import unittest")
        test_script.append("import importlib")
        test_script.append("import os")
        
        # Add paths
        test_script.append(f"sys.path.insert(0, r'{workdir}')")
        test_script.append(f"sys.path.insert(0, r'{src_dir}')")
        
        test_script.append("collected = 0")
        test_script.append("failed = 0")
        test_script.append("errors = []")
        
        for tf in test_files:
            mod_name = tf.replace('/', '.').replace('\\', '.').replace('.py', '')
            test_script.append(f"try:")
            test_script.append(f"    mod = importlib.import_module('{mod_name}')")
            test_script.append(f"    # Collect bare test functions")
            test_script.append(f"    for name in dir(mod):")
            test_script.append(f"        if name.startswith('test_'):")
            test_script.append(f"            func = getattr(mod, name)")
            test_script.append(f"            if callable(func) and func.__code__.co_argcount == 0:")
            test_script.append(f"                collected += 1")
            test_script.append(f"                try:")
            test_script.append(f"                    func()")
            test_script.append(f"                except Exception as e:")
            test_script.append(f"                    failed += 1")
            test_script.append(f"                    errors.append(f'{name}: {e}')")
            test_script.append(f"    # Collect TestCase subclasses")
            test_script.append(f"    for name in dir(mod):")
            test_script.append(f"        cls = getattr(mod, name)")
            test_script.append(f"        if isinstance(cls, type) and issubclass(cls, unittest.TestCase) and cls is not unittest.TestCase:")
            test_script.append(f"            suite = unittest.TestLoader().loadTestsFromTestCase(cls)")
            test_script.append(f"            for test in suite:")
            test_script.append(f"                collected += 1")
            test_script.append(f"                try:")
            test_script.append(f"                    test.run()")
            test_script.append(f"                except Exception as e:")
            test_script.append(f"                    failed += 1")
            test_script.append(f"                    errors.append(f'{name}: {e}')")
            test_script.append(f"except ModuleNotFoundError as e:")
            test_script.append(f"    errors.append(f'ModuleNotFoundError: {e}')")
            test_script.append(f"except Exception as e:")
            test_script.append(f"    errors.append(f'{mod_name}: {e}')")
            
        test_script.append("print(f'COLLECTED:{collected}')")
        test_script.append("print(f'FAILED:{failed}')")
        test_script.append("for e in errors:")
        test_script.append("    print(f'ERROR:{e}')")
        
        script_content = "\n".join(test_script)
        
        try:
            result = subprocess.run(
                [self.python_executable, '-c', script_content],
                cwd=workdir,
                env=env,
                capture_output=True,
                timeout=self.timeout_s
            )
            stdout = result.stdout.decode('utf-8', errors='replace')
            stderr = result.stderr.decode('utf-8', errors='replace')
            
            collected = 0
            failed = 0
            errors = []
            
            for line in stdout.splitlines():
                if line.startswith('COLLECTED:'):
                    collected = int(line.split(':')[1])
                elif line.startswith('FAILED:'):
                    failed = int(line.split(':')[1])
                elif line.startswith('ERROR:'):
                    errors.append(line[6:])
                    
            if collected == 0:
                if any('ModuleNotFoundError' in e for e in errors):
                    return GateResult(
                        name='tests',
                        status=SKIPPED,
                        method='unittest_fallback',
                        detail='No tests collected due to missing dependencies',
                        duration_s=time.time() - start
                    )
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='unittest_fallback',
                    detail='No tests collected',
                    duration_s=time.time() - start
                )
                
            if failed > 0:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='unittest_fallback',
                    detail=f'{failed} tests failed: ' + '; '.join(errors[:5]),
                    duration_s=time.time() - start
                )
                
            return GateResult(
                name='tests',
                status=PASSED,
                method='unittest_fallback',
                detail=f'{collected} tests passed',
                duration_s=time.time() - start
            )
            
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method='unittest_fallback',
                detail=f'Fallback runner failed: {e}',
                duration_s=time.time() - start
            )

    def _gate_telemetry(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        
        py_files = [p for p in bundle.keys() if p.endswith('.py')]
        
        if not py_files:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='No Python files to analyze',
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
                    if isinstance(func, ast.Attribute):
                        if func.attr in tracer_methods:
                            has_call = True
                    elif isinstance(func, ast.Name):
                        if func.id in tracer_methods:
                            has_call = True
                            
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
            status=PASSED,
            method='ast_analysis',
            detail='OpenTelemetry usage detected',
            duration_s=time.time() - start
        )