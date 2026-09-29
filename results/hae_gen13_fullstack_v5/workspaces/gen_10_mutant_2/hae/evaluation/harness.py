"""
Execution-grounded verification of agent-authored workspaces.

Five gates, each of which either runs the code or performs AST analysis on it:
    syntax     Every authored .py file must parse (`ast.parse`).
    build      The package must actually install (`pip install -e .`).
    smoke      Its modules must actually import.
    tests      Its test suite must actually collect and pass under pytest.
    telemetry  OpenTelemetry must be imported by authored code, not merely
               mentioned in prose.

Each result carries the `method` used to reach it. A gate that could not be
evaluated returns `SKIPPED` rather than `PASSED`, so a missing tool can never be
mistaken for a success, and `SKIPPED` gates are excluded from the fitness
denominator rather than scored as failures.
"""

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
from typing import Dict, List, Optional, Any, Tuple

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
        count_source_files
    )
except ImportError:
    # Fallback definitions if artifacts.py is not yet available or import fails
    EXCLUDED_DIR_SEGMENTS = ('.pytest_cache', '__pycache__', 'venv', '.venv', '.git', '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.ipynb_checkpoints')
    EXCLUDED_DIR_SUFFIXES = ('.egg-info', '.dist-info')

    def is_generated_path(path: str) -> bool:
        """True if the path lives inside a machine-generated directory."""
        parts = path.replace('\\', '/').split('/')
        for part in parts[:-1]: # Check directories, not the filename itself usually, but segments
            if part in EXCLUDED_DIR_SEGMENTS:
                return True
            if any(part.endswith(s) for s in EXCLUDED_DIR_SUFFIXES):
                return True
        return False

    def is_malformed_path(path: str) -> bool:
        """True if markdown formatting, stray whitespace, or invalid punctuation leaked into the filename."""
        if not path:
            return True
        # Check for markdown chars
        if '*' in path or '`' in path:
            return True
        # Check for leading/trailing whitespace
        if path != path.strip():
            return True
        # Check for leading hyphen
        if path.startswith('-'):
            return True
        # Check for trailing dot or tilde
        if path.endswith('.') or path.endswith('~'):
            return True
        return False

    def sanitize_path(path: str) -> str:
        """Strips markdown decoration from an agent-supplied path."""
        if not path:
            return path
        # Strip whitespace
        path = path.strip()
        # Remove markdown bold/italic markers
        path = path.replace('**', '').replace('*', '')
        # Remove backticks
        path = path.replace('`', '')
        # Remove leading hyphens if they look like markdown list items? 
        # Spec says "leading hyphens" are malformed, so sanitize might just strip them if they are decoration?
        # The spec says sanitize "Strips markdown decoration". 
        # If a path starts with '-', it's malformed. Sanitize might remove it if it's clearly decoration.
        # Let's assume simple stripping of known decoration chars.
        if path.startswith('-'):
            path = path[1:]
        if path.endswith('.') or path.endswith('~'):
            path = path[:-1]
        return path

    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        """Returns only genuinely agent-authored files from a path->content map."""
        return {k: v for k, v in bundle.items() if not is_generated_path(k) and not is_malformed_path(k)}

    def partition_bundle(bundle: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
        """Splits a bundle into (authored, generated, malformed) for reporting."""
        authored = {}
        generated = {}
        malformed = {}
        for path, content in bundle.items():
            if is_generated_path(path):
                generated[path] = content
            elif is_malformed_path(path):
                malformed[path] = content
            else:
                authored[path] = content
        return authored, generated, malformed

    def count_source_files(bundle: Dict[str, str], suffixes: Tuple[str, ...] = ('.py',)) -> int:
        """Counts authored files matching the given suffixes."""
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
        lines.append(f"Authored Files: {self.authored_files} (Python: {self.python_files})")
        lines.append(f"Score Penalty: {self.score_penalty:.2f}")
        lines.append("-" * 40)
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"[{status_icon}] {g.name:10} : {g.status:7} ({g.method})")
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
    # Common patterns:
    # ```python
    # path/to/file.py
    # ... code ...
    # ```
    # Or just ```python ... ``` where we might guess path or use a default.
    # The spec implies we recover a path->content map. 
    # If no path is provided in the block, we might need to generate one or skip.
    # Let's look for a pattern where the first line inside the block is a path, or the fence info string contains it.
    
    blocks = {}
    # Match ```lang\ncontent\n```
    # We'll try to extract a path from the first line of the content if it looks like a path,
    # or from the fence info string.
    
    pattern = re.compile(r'```(\w+)?\n(.*?)\n```', re.DOTALL)
    matches = pattern.findall(text)
    
    for lang, content in matches:
        if not content.strip():
            continue
            
        lines = content.split('\n')
        path = None
        
        # Heuristic 1: First line is a path
        first_line = lines[0].strip()
        if first_line and ('/' in first_line or '\\' in first_line or first_line.endswith('.py')):
            # Check if it looks like a path (no spaces usually, or valid filename chars)
            # Avoid matching code that starts with a comment like # path/to/file
            if not first_line.startswith('#') and not first_line.startswith('//'):
                path = first_line
                # Remove the path line from content
                content = '\n'.join(lines[1:])
        
        # Heuristic 2: Fence info string contains path? e.g. ```python path/to/file.py
        # The regex above only captured the language. Let's refine regex if needed.
        # For now, if no path found, we might skip or use a dummy. 
        # The spec says "Recovers a path->content map". If we can't recover a path, we can't map it.
        # However, often agents write:
        # ```python
        # def foo(): ...
        # ```
        # Without a path. In that case, we might not be able to verify it properly as a file.
        # But let's assume if no path is found, we skip it or assign a generic name?
        # "Only the contents of fenced blocks are returned".
        # If we can't determine the path, we can't put it in the map.
        
        if path:
            # Sanitize the path
            clean_path = sanitize_path(path)
            if clean_path and not is_malformed_path(clean_path):
                blocks[clean_path] = content
            elif clean_path:
                # If malformed after sanitize, maybe skip? Or keep original?
                # Spec says sanitize is applied at write time. Here we are extracting.
                # Let's keep it if it's not generated.
                if not is_generated_path(clean_path):
                    blocks[clean_path] = content

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
        
        # Try to get bundle from workspace
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
            # No code found
            return self._empty_report(source)

        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to get authored files
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files_count = len(authored_bundle)
        python_files_count = count_source_files(authored_bundle, ('.py',))
        
        if authored_files_count == 0:
            return self._empty_report(source, authored_files=0, python_files=0)

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
            # Scan directory
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
        
        authored_files_count = len(authored)
        python_files_count = count_source_files(authored, ('.py',))
        
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
        failed_gates = [g for g in gates if g.status == FAILED]
        score_penalty = len(failed_gates) * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files_count,
            python_files=python_files_count,
            source=source
        )

    def _empty_report(self, source: str, authored_files: int = 0, python_files: int = 0) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No files to check'),
            GateResult(name='build', status=SKIPPED, method='pip install', detail='No files to build'),
            GateResult(name='smoke', status=SKIPPED, method='import', detail='No files to import'),
            GateResult(name='tests', status=SKIPPED, method='pytest', detail='No tests to run'),
            GateResult(name='telemetry', status=SKIPPED, method='ast.parse', detail='No files to check'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=0.0,
            authored_files=authored_files,
            python_files=python_files,
            source=source
        )

    def _gate_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        py_files = [p for p in authored if p.endswith('.py')]
        
        if not py_files:
            return GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No Python files found')
        
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
        
        duration = time.time() - start_time
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail=f"Syntax errors in {len(errors)} files",
                duration_s=duration,
                evidence={'errors': errors}
            )
        else:
            return GateResult(
                name='syntax',
                status=PASSED,
                method='ast.parse',
                detail=f"All {len(py_files)} Python files parsed successfully",
                duration_s=duration
            )

    def _gate_build(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            # If no build config, we can't really "build" in the pip sense.
            # But we can check if it's a valid package structure?
            # The spec says "The package must actually install (pip install -e .)".
            # If there's no setup.py, pip install -e . will fail.
            # So this should be FAILED if we expect a package, or SKIPPED if it's just scripts?
            # "A gate that could not be evaluated returns SKIPPED".
            # If there's no build system, we can't evaluate the build gate.
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip install',
                detail='No setup.py or pyproject.toml found',
                duration_s=time.time() - start_time
            )
        
        try:
            # Run pip install -e .
            # Use the specified python executable
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps']
            # We might want to capture output
            result = subprocess.run(
                cmd,
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
                    method='pip install',
                    detail='Package installed successfully',
                    duration_s=duration
                )
            else:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip install',
                    detail=f"Build failed: {result.stderr[:500]}",
                    duration_s=duration,
                    evidence={'stderr': result.stderr, 'stdout': result.stdout}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail='Build timed out',
                duration_s=time.time() - start_time
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail=f"Build error: {str(e)}",
                duration_s=time.time() - start_time
            )

    def _gate_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        
        # Find modules to import
        # We look for .py files that are not __init__.py and not test files
        # And try to import them
        
        modules_to_import = []
        for path in authored:
            if not path.endswith('.py'):
                continue
            if path.endswith('__init__.py'):
                continue
            if 'test' in path.lower():
                continue
            
            # Convert path to module name
            # e.g. src/module.py -> src.module
            # We need to be careful with package structure
            
            # Simple heuristic: if it's in a directory with __init__.py, it's a package
            # For now, let's try to import the top-level modules
            
            # Determine the module name relative to workdir
            rel_path = path.replace('\\', '/')
            if rel_path.endswith('.py'):
                module_name = rel_path[:-3].replace('/', '.')
                modules_to_import.append(module_name)
        
        if not modules_to_import:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import',
                detail='No importable modules found',
                duration_s=time.time() - start_time
            )
        
        # Try to import each module
        import_errors = []
        imported_count = 0
        
        # We need to run this in a subprocess to avoid polluting the current process
        # and to handle sys.path correctly
        
        import_script = f"""
import sys
import os
sys.path.insert(0, '{workdir}')
import importlib

modules = {modules_to_import}
errors = []
imported = 0
for mod in modules:
    try:
        importlib.import_module(mod)
        imported += 1
    except Exception as e:
        errors.append(f"{{mod}}: {{str(e)}}")

print(imported)
print(errors)
"""
        
        try:
            result = subprocess.run(
                [self.python_executable, '-c', import_script],
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start_time
            
            if result.returncode != 0:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='import',
                    detail=f"Import script failed: {result.stderr[:500]}",
                    duration_s=duration
                )
            
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                imported_count = int(lines[0])
                errors_str = lines[1]
                # Parse errors list
                try:
                    import_errors = eval(errors_str)
                except:
                    import_errors = [errors_str]
            
            if import_errors:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='import',
                    detail=f"Failed to import {len(import_errors)} modules",
                    duration_s=duration,
                    evidence={'errors': import_errors}
                )
            else:
                return GateResult(
                    name='smoke',
                    status=PASSED,
                    method='import',
                    detail=f"Successfully imported {imported_count} modules",
                    duration_s=duration
                )
                
        except subprocess.TimeoutExpired:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='Import timed out',
                duration_s=time.time() - start_time
            )
        except Exception as e:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail=f"Smoke test error: {str(e)}",
                duration_s=time.time() - start_time
            )

    def _gate_tests(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], capture_output=True, timeout=10)
        except Exception:
            return GateResult(
                name='tests',
                status=SKIPPED,
                method='pytest',
                detail='pytest not available',
                duration_s=time.time() - start_time
            )
        
        # Check if there are any test files
        test_files = [p for p in authored if 'test' in p.lower() and p.endswith('.py')]
        if not test_files:
            return GateResult(
                name='tests',
                status=SKIPPED,
                method='pytest',
                detail='No test files found',
                duration_s=time.time() - start_time
            )
        
        try:
            # Run pytest
            cmd = [self.python_executable, '-m', 'pytest', '-v', '--tb=short']
            result = subprocess.run(
                cmd,
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
                    detail='All tests passed',
                    duration_s=duration
                )
            else:
                # Check if it was a collection error or test failure
                # If returncode is 2, it's usually a usage error or collection error
                # If 1, tests failed
                # If 5, no tests collected
                
                detail = "Tests failed"
                if result.returncode == 5:
                    detail = "No tests collected"
                    return GateResult(
                        name='tests',
                        status=SKIPPED,
                        method='pytest',
                        detail=detail,
                        duration_s=duration
                    )
                
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f"{detail}: {result.stdout[-500:]}",
                    duration_s=duration,
                    evidence={'stdout': result.stdout, 'stderr': result.stderr}
                )
                
        except subprocess.TimeoutExpired:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='Tests timed out',
                duration_s=time.time() - start_time
            )
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail=f"Test execution error: {str(e)}",
                duration_s=time.time() - start_time
            )

    def _gate_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        
        py_files = [p for p in authored if p.endswith('.py')]
        if not py_files:
            return GateResult(
                name='telemetry',
                status=SKIPPED,
                method='ast.parse',
                detail='No Python files to check for telemetry',
                duration_s=time.time() - start_time
            )
        
        telemetry_found = False
        telemetry_files = []
        
        for path in py_files:
            full_path = os.path.join(workdir, path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Check for OpenTelemetry imports
                # We look for 'import opentelemetry' or 'from opentelemetry'
                # Using AST to be more robust? Or simple regex?
                # Spec says "OpenTelemetry must be imported by authored code, not merely mentioned in prose."
                # AST is better to avoid false positives in strings/comments.
                
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith('opentelemetry'):
                                telemetry_found = True
                                telemetry_files.append(path)
                                break
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.module.startswith('opentelemetry'):
                            telemetry_found = True
                            telemetry_files.append(path)
                            break
                
                if telemetry_found:
                    break # Found one, good enough? Or check all?
                    
            except SyntaxError:
                continue
            except Exception:
                continue
        
        duration = time.time() - start_time
        
        if telemetry_found:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast.parse',
                detail=f"OpenTelemetry imported in {telemetry_files}",
                duration_s=duration,
                evidence={'files': telemetry_files}
            )
        else:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast.parse',
                detail='No OpenTelemetry imports found in authored code',
                duration_s=duration
            )