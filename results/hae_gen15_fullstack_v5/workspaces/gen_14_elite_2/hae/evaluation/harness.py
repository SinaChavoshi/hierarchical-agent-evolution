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
        for path in bundle.keys():
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
        lines.append("-" * 40)
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"[{status_icon}] {g.name}: {g.status} ({g.method}) - {g.detail}")
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
    
    # Regex to find fenced code blocks with optional language and filename hint
    # Matches: ```python filename.py\ncontent\n```
    # Or: ```filename.py\ncontent\n```
    # Or: ```python\ncontent\n``` (no filename, will be skipped or assigned generic name)
    
    pattern = r'```(?:python|py)?\s*(?:([^\n`]+\.py))?\s*\n(.*?)\n```'
    matches = re.findall(pattern, text, re.DOTALL)
    
    bundle = {}
    for i, (filename, content) in enumerate(matches):
        if filename:
            # Clean up filename if it has extra spaces or backticks
            clean_name = filename.strip().strip('`').strip()
            if clean_name:
                bundle[clean_name] = content
        else:
            # If no filename provided, we can't reliably place it. 
            # However, the spec says "path->content map". 
            # If no path is given, we might skip it or assign a generic name.
            # Given the strictness, if no path is provided, it's likely not intended as a file.
            # But to be safe, we might assign a generic name if it looks like code.
            # Let's assign a generic name based on index if it's clearly python code.
            # Actually, without a path, we can't verify it properly. 
            # Let's skip blocks without explicit filenames to avoid false positives.
            pass
            
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
        bundle = {}
        source = 'bundle'
        
        # Try to get bundle from workspace object
        if workspace is not None:
            # Check if workspace has a method to get files
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace'
            elif hasattr(workspace, 'list_files'):
                # Some workspaces might have list_files returning paths, and read_file
                # This is complex, assume dict-like or get_files for now
                pass
        
        # If bundle is empty, try extracting from deliverable text
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to remove generated/malformed files
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files = len(authored_bundle)
        python_files = count_source_files(authored_bundle, suffixes=('.py',))
        
        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to temp dir
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
        
        authored_files = len(authored)
        python_files = count_source_files(authored, suffixes=('.py',))
        
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
        
        # Calculate penalty
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
                method='ast_parse',
                detail='No Python files found',
                duration_s=time.time() - start
            )
        
        errors = []
        for path in py_files:
            content = authored[path]
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
                detail='; '.join(errors),
                duration_s=duration
            )
        
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast_parse',
            detail=f"All {len(py_files)} Python files parsed successfully",
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
                method='metadata_check',
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
                        method='toml_validation',
                        detail='pyproject.toml is valid TOML but missing required tables ([project], [build-system], or [tool])',
                        duration_s=time.time() - start
                    )
            except Exception as e:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='toml_validation',
                    detail=f'pyproject.toml is malformed: {e}',
                    duration_s=time.time() - start
                )
        
        # Try to install
        try:
            # Check if pip is available
            subprocess.run([self.python_executable, '-m', 'pip', '--version'], 
                           capture_output=True, timeout=10, check=True)
            
            # Run pip install
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps', '--no-build-isolation']
            result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=self.timeout_s)
            
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
                    detail=f'pip install failed: {result.stderr}',
                    duration_s=duration
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
                detail=f'Error during build: {e}',
                duration_s=time.time() - start
            )

    def _gate_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find non-test authored modules
        modules = []
        for path in authored.keys():
            if not path.endswith('.py'):
                continue
            basename = os.path.basename(path).lower()
            if 'test' in basename:
                continue
            # Convert path to module name
            # e.g., src/foo/bar.py -> foo.bar
            rel_path = path
            if rel_path.startswith('src/'):
                rel_path = rel_path[4:]
            if rel_path.endswith('.py'):
                rel_path = rel_path[:-3]
            if rel_path.endswith('__init__'):
                rel_path = rel_path[:-9]
            
            module_name = rel_path.replace('/', '.').replace('\\', '.')
            if module_name:
                modules.append(module_name)
        
        if not modules:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='subprocess_import',
                detail='No importable non-test modules found',
                duration_s=time.time() - start
            )
        
        # Prepare environment
        env = os.environ.copy()
        python_path = env.get('PYTHONPATH', '')
        new_paths = [workdir, os.path.join(workdir, 'src')]
        env['PYTHONPATH'] = os.pathsep.join(new_paths + ([python_path] if python_path else []))
        
        # Try importing each module
        failures = []
        skipped_modules = []
        
        for module in modules:
            try:
                cmd = [self.python_executable, '-c', f'import {module}']
                result = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=self.timeout_s)
                
                if result.returncode != 0:
                    stderr = result.stderr
                    # Check if it's just a ModuleNotFoundError for third-party packages
                    if 'ModuleNotFoundError' in stderr:
                        # Check if it's a standard library or local module issue
                        # If the module itself is not found, it's a failure.
                        # If a dependency is not found, it's skipped.
                        # Heuristic: if the error says "No module named 'opentelemetry'" etc.
                        # We'll assume if the module name itself is not in the error, it's a dep issue.
                        if f"No module named '{module}'" not in stderr:
                            skipped_modules.append(module)
                        else:
                            failures.append(f"{module}: {stderr}")
                    else:
                        failures.append(f"{module}: {stderr}")
            except Exception as e:
                failures.append(f"{module}: {e}")
        
        duration = time.time() - start
        
        if failures:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='subprocess_import',
                detail='; '.join(failures),
                duration_s=duration
            )
        
        if skipped_modules and not failures:
            # If all modules were skipped due to missing deps, return SKIPPED
            if len(skipped_modules) == len(modules):
                return GateResult(
                    name='smoke',
                    status=SKIPPED,
                    method='subprocess_import',
                    detail=f"All modules skipped due to missing dependencies: {', '.join(skipped_modules)}",
                    duration_s=duration
                )
            # If some passed and some skipped, it's a pass (the ones that could run did)
            # But spec says: "If module imports fail solely with ModuleNotFoundError... return SKIPPED"
            # This implies if ANY module fails with ModuleNotFoundError, we might skip?
            # "If no importable modules exist or any module fails with a syntax or runtime error, return FAILED."
            # "If module imports fail solely with ModuleNotFoundError for uninstalled third-party packages... return SKIPPED"
            # This suggests if we have a mix, it's ambiguous. But usually, if we can't import, we can't verify.
            # Let's interpret: If we couldn't verify because of missing deps, return SKIPPED.
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='subprocess_import',
                detail=f"Some modules skipped due to missing dependencies: {', '.join(skipped_modules)}",
                duration_s=duration
            )
            
        return GateResult(
            name='smoke',
            status=PASSED,
            method='subprocess_import',
            detail=f"All {len(modules)} modules imported successfully",
            duration_s=duration
        )

    def _gate_tests(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find test files
        test_files = []
        for path in authored.keys():
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
        
        # Prepare environment
        env = os.environ.copy()
        python_path = env.get('PYTHONPATH', '')
        new_paths = [workdir, os.path.join(workdir, 'src')]
        env['PYTHONPATH'] = os.pathsep.join(new_paths + ([python_path] if python_path else []))
        
        # Try pytest first
        try:
            # Check if pytest is installed
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], 
                           capture_output=True, timeout=10, check=True)
            
            # Run pytest
            cmd = [self.python_executable, '-m', 'pytest', '-v'] + test_files
            result = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=self.timeout_s)
            
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
                # Check if it's a collection error due to missing modules
                if 'ModuleNotFoundError' in result.stderr or 'ModuleNotFoundError' in result.stdout:
                    # Check if it's a third-party package
                    # Heuristic: if the error is about a package not in the workspace
                    return GateResult(
                        name='tests',
                        status=SKIPPED,
                        method='pytest',
                        detail=f'Tests skipped due to missing dependencies: {result.stderr}',
                        duration_s=duration
                    )
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f'Tests failed: {result.stderr}',
                    duration_s=duration
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
            # pytest failed to run, use fallback
            pass
        
        # Fallback: stdlib unittest
        # We need to run a script that imports each test file and collects/executes tests
        # This is complex to do in a single subprocess call for multiple files.
        # We'll write a temporary runner script.
        
        runner_script = os.path.join(workdir, '_test_runner.py')
        with open(runner_script, 'w') as f:
            f.write("""
import sys
import unittest
import importlib
import os

def run_tests(test_files):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    for test_file in test_files:
        # Convert path to module name
        rel_path = test_file
        if rel_path.startswith('src/'):
            rel_path = rel_path[4:]
        if rel_path.endswith('.py'):
            rel_path = rel_path[:-3]
        module_name = rel_path.replace('/', '.').replace('\\', '.')
        
        try:
            module = importlib.import_module(module_name)
            # Add unittest.TestCase subclasses
            suite.addTests(loader.loadTestsFromModule(module))
            
            # Add bare test_* functions
            for attr in dir(module):
                if attr.startswith('test_'):
                    func = getattr(module, attr)
                    if callable(func):
                        # Check if it takes 0 required arguments
                        try:
                            sig = __import__('inspect').signature(func)
                            # Count parameters that don't have defaults
                            required_args = 0
                            for param in sig.parameters.values():
                                if param.default is __import__('inspect').Parameter.empty:
                                    required_args += 1
                            
                            if required_args == 0:
                                # Wrap in a TestCase
                                class TestWrapper(unittest.TestCase):
                                    def runTest(self):
                                        func()
                                TestWrapper.__name__ = f'Test_{attr}'
                                suite.addTest(TestWrapper())
                        except Exception:
                            # If inspection fails, assume it might be valid or skip
                            pass
        except ModuleNotFoundError as e:
            print(f"ModuleNotFoundError: {e}", file=sys.stderr)
            sys.exit(2) # Special exit code for skipped
        except Exception as e:
            print(f"Error importing {module_name}: {e}", file=sys.stderr)
            sys.exit(1)
            
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == '__main__':
    test_files = sys.argv[1:]
    run_tests(test_files)
""")
        
        try:
            cmd = [self.python_executable, runner_script] + test_files
            result = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=self.timeout_s)
            
            duration = time.time() - start
            
            if result.returncode == 0:
                return GateResult(
                    name='tests',
                    status=PASSED,
                    method='unittest_fallback',
                    detail='All tests passed',
                    duration_s=duration
                )
            elif result.returncode == 2:
                return GateResult(
                    name='tests',
                    status=SKIPPED,
                    method='unittest_fallback',
                    detail=f'Tests skipped due to missing dependencies: {result.stderr}',
                    duration_s=duration
                )
            else:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='unittest_fallback',
                    detail=f'Tests failed: {result.stderr}',
                    duration_s=duration
                )
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method='unittest_fallback',
                detail=f'Error running tests: {e}',
                duration_s=time.time() - start
            )
        finally:
            if os.path.exists(runner_script):
                os.remove(runner_script)

    def _gate_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        py_files = [p for p in authored.keys() if p.endswith('.py')]
        
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
        
        for path in py_files:
            content = authored[path]
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
                    if node.module == 'opentelemetry' or (node.module and node.module.startswith('opentelemetry.')):
                        has_import = True
                
                # Check for calls to tracer/span methods
                if isinstance(node, ast.Call):
                    func = node.func
                    # Handle method calls like tracer.start_span()
                    if isinstance(func, ast.Attribute):
                        attr_name = func.attr
                        if attr_name in ['start_as_current_span', 'start_span', 'get_tracer', 'get_tracer_provider', 'set_tracer_provider', 'get_meter']:
                            has_call = True
                    # Handle function calls like get_tracer()
                    elif isinstance(func, ast.Name):
                        func_name = func.id
                        if func_name in ['start_as_current_span', 'start_span', 'get_tracer', 'get_tracer_provider', 'set_tracer_provider', 'get_meter']:
                            has_call = True
        
        duration = time.time() - start
        
        if has_import and has_call:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast_analysis',
                detail='OpenTelemetry import and tracer/span calls found',
                duration_s=duration
            )
        elif has_import:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='OpenTelemetry import found but no tracer/span calls',
                duration_s=duration
            )
        elif has_call:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='Tracer/span calls found but no OpenTelemetry import',
                duration_s=duration
            )
        else:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast_analysis',
                detail='No OpenTelemetry usage found',
                duration_s=duration
            )