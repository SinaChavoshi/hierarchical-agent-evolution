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

The V1 verifier this replaces executed nothing in three of its four gates:
`build` was a filename substring match, `smoke` was `len(files) >= 3`, and
`telemetry` searched text that included the CEO's markdown prose, so an essay
mentioning OpenTelemetry passed. Six generations were selected on those
signals. The whole design rule here is that no gate may be satisfiable by
writing about it.
"""

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Import artifacts module for path filtering
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
        passed = len(self.passed_gates)
        return passed / len(evaluated)

    def summary(self) -> str:
        lines = [
            f"Verification Report ({self.harness_version})",
            f"Source: {self.source}",
            f"Authored Files: {self.authored_files} (Python: {self.python_files})",
            f"Score Penalty: {self.score_penalty:.2f}",
            f"Pass Rate: {self.pass_rate:.2%}",
            "-" * 40,
        ]
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"[{status_icon}] {g.name:10s} | {g.status:7s} | {g.method}")
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
    
    blocks = {}
    # Match ```lang\n...\n```
    pattern = r"```(\w*)\n(.*?)\n```"
    matches = re.finditer(pattern, text, re.DOTALL)
    
    counter = 0
    for match in matches:
        lang = match.group(1)
        content = match.group(2)
        
        # Try to find a path in the content (e.g., first line comment) or use a generated name
        path = None
        
        # Heuristic 1: Check if the first line looks like a path comment
        lines = content.split('\n')
        if lines:
            first_line = lines[0].strip()
            # Common patterns: # path: foo.py, // path: foo.py, # foo.py
            path_match = re.match(r"(?:#|//)\s*(?:path:\s*)?([\w/\.\-]+\.py)", first_line)
            if path_match:
                path = path_match.group(1)
        
        if not path:
            # Heuristic 2: Use the language as extension if possible, or default to .py
            ext = ".py" if lang in ("python", "py", "") else f".{lang}"
            path = f"extracted_{counter}{ext}"
            counter += 1
            
        blocks[path] = content
        
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
            # Assume workspace has a method to get files or is a dict-like
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace'
                
        # Fallback to deliverable text if bundle is empty
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        if not bundle:
            # No code found
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter out generated/malformed paths
        authored_bundle = filter_bundle(bundle)
        
        if not authored_bundle:
            return self._empty_report(source)
            
        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to disk
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
        
        # Count files
        python_files = count_source_files(authored, suffixes=('.py',))
        authored_files = len(authored)
        
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._check_syntax(authored))
        
        # 2. Build Gate
        gates.append(self._check_build(workdir))
        
        # 3. Smoke Gate
        gates.append(self._check_smoke(workdir, authored))
        
        # 4. Tests Gate
        gates.append(self._check_tests(workdir))
        
        # 5. Telemetry Gate
        gates.append(self._check_telemetry(authored))
        
        # Calculate penalty
        failed_gates = [g for g in gates if g.status == FAILED]
        score_penalty = len(failed_gates) * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files,
            python_files=python_files,
            source=source
        )

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No files found'),
            GateResult(name='build', status=SKIPPED, method='pip install -e .', detail='No files found'),
            GateResult(name='smoke', status=SKIPPED, method='import', detail='No files found'),
            GateResult(name='tests', status=SKIPPED, method='pytest', detail='No files found'),
            GateResult(name='telemetry', status=SKIPPED, method='ast.parse', detail='No files found'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=0.0,
            authored_files=0,
            python_files=0,
            source=source
        )

    def _check_syntax(self, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        errors = []
        for path, content in bundle.items():
            if not path.endswith('.py'):
                continue
            try:
                ast.parse(content, filename=path)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
        
        duration = time.time() - start
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='; '.join(errors[:5]),
                duration_s=duration,
                evidence={'errors': errors}
            )
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast.parse',
            detail='All Python files parsed successfully',
            duration_s=duration
        )

    def _check_build(self, workdir: str) -> GateResult:
        start = time.time()
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail='No setup.py or pyproject.toml found',
                duration_s=time.time() - start
            )
        
        try:
            # Run pip install -e .
            # We use a temporary venv or just install to current? 
            # Spec says "The package must actually install". 
            # To avoid polluting the global env, we might want to use a venv, 
            # but for simplicity in this harness, we'll try installing to a temp target or just check if pip can resolve it.
            # Actually, `pip install -e .` in a temp dir is safe if we don't care about the global env, 
            # but it might fail if dependencies are missing.
            # Let's try installing to a temporary directory to avoid side effects.
            with tempfile.TemporaryDirectory() as install_dir:
                cmd = [
                    self.python_executable, '-m', 'pip', 'install', '-e', '.',
                    '--target', install_dir,
                    '--no-deps' # Skip deps to focus on the package itself building/installing
                ]
                result = subprocess.run(
                    cmd,
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
                        method='pip install -e .',
                        detail='Package installed successfully',
                        duration_s=duration
                    )
                else:
                    return GateResult(
                        name='build',
                        status=FAILED,
                        method='pip install -e .',
                        detail=f"Install failed: {result.stderr[:500]}",
                        duration_s=duration,
                        evidence={'stderr': result.stderr, 'stdout': result.stdout}
                    )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail='Install timed out',
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail=f"Error running pip: {str(e)}",
                duration_s=time.time() - start
            )

    def _check_smoke(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        # Find importable modules
        # We need to add workdir to sys.path temporarily
        sys.path.insert(0, workdir)
        
        imported_modules = []
        errors = []
        
        try:
            for path in bundle:
                if not path.endswith('.py'):
                    continue
                # Convert path to module name
                # e.g., src/module.py -> src.module
                # Remove .py
                module_path = path[:-3]
                # Replace slashes with dots
                module_name = module_path.replace('/', '.').replace('\\', '.')
                
                # Skip __init__.py as it's not a module itself in the same way
                if module_name.endswith('.__init__'):
                    continue
                    
                try:
                    __import__(module_name)
                    imported_modules.append(module_name)
                except Exception as e:
                    errors.append(f"{module_name}: {str(e)}")
        finally:
            sys.path.pop(0)
            
        duration = time.time() - start
        
        if not imported_modules and not errors:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import',
                detail='No importable modules found',
                duration_s=duration
            )
            
        if errors:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='; '.join(errors[:5]),
                duration_s=duration,
                evidence={'errors': errors, 'imported': imported_modules}
            )
            
        return GateResult(
            name='smoke',
            status=PASSED,
            method='import',
            detail=f"Successfully imported {len(imported_modules)} modules",
            duration_s=duration,
            evidence={'imported': imported_modules}
        )

    def _check_tests(self, workdir: str) -> GateResult:
        start = time.time()
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], capture_output=True, timeout=10)
        except Exception:
            return GateResult(
                name='tests',
                status=SKIPPED,
                method='pytest',
                detail='pytest not available',
                duration_s=time.time() - start
            )
            
        # Check if there are test files
        test_files = []
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'venv', '.venv')]
            for file in files:
                if file.startswith('test_') or file.endswith('_test.py'):
                    test_files.append(os.path.join(root, file))
                    
        if not test_files:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='No test files found',
                duration_s=time.time() - start
            )
            
        try:
            cmd = [self.python_executable, '-m', 'pytest', '-v', '--tb=short']
            result = subprocess.run(
                cmd,
                cwd=workdir,
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
                    duration_s=duration,
                    evidence={'stdout': result.stdout[-1000:]}
                )
            else:
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
                detail=f"Error running pytest: {str(e)}",
                duration_s=time.time() - start
            )

    def _check_telemetry(self, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        found_telemetry = False
        telemetry_files = []
        
        for path, content in bundle.items():
            if not path.endswith('.py'):
                continue
            try:
                tree = ast.parse(content, filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if 'opentelemetry' in alias.name:
                                found_telemetry = True
                                telemetry_files.append(path)
                                break
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and 'opentelemetry' in node.module:
                            found_telemetry = True
                            telemetry_files.append(path)
                            break
            except SyntaxError:
                continue
                
        duration = time.time() - start
        
        if found_telemetry:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast.parse',
                detail=f"OpenTelemetry imported in {len(telemetry_files)} files",
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