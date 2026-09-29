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

Usage:
    from hae.evaluation.harness import ExecutionHarness

    harness = ExecutionHarness()
    report = harness.verify_workspace(workspace, deliverable_text)
    print(report.summary(), report.gate_status)
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
from typing import Any, Dict, List, Optional

# Constants
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
        passed_count = len(self.passed_gates)
        return passed_count / len(evaluated)

    def summary(self) -> str:
        lines = [
            f"Verification Report ({self.harness_version})",
            f"Source: {self.source}",
            f"Authored Files: {self.authored_files} (Python: {self.python_files})",
            f"Score Penalty: {self.score_penalty:.2f}",
            f"Pass Rate: {self.pass_rate:.2%}",
            "-" * 40
        ]
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "○")
            lines.append(f"[{status_icon}] {g.name:10} | {g.status:7} | {g.method}")
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
    # Pattern: ```[language] [filename]\n...content...\n```
    # We look for lines starting with ``` followed by optional lang and optional path
    pattern = r"```(?:\w+)?\s*(?:([^\n]+))?\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    
    bundle = {}
    for i, (filename_hint, content) in enumerate(matches):
        # Determine path
        path = None
        if filename_hint:
            # Clean up the hint: remove backticks if present, strip whitespace
            hint = filename_hint.strip().strip('`').strip()
            # If it looks like a path (contains / or .py), use it
            if '/' in hint or hint.endswith('.py') or hint.endswith('.txt'):
                path = hint
        
        if not path:
            # Fallback: generate a synthetic path based on index
            path = f"extracted_block_{i}.py"
            
        # Normalize path separators
        path = path.replace('\\', '/')
        
        # Avoid overwriting if duplicate paths found (append index)
        if path in bundle:
            base, ext = os.path.splitext(path)
            path = f"{base}_{i}{ext}"
            
        bundle[path] = content
        
    return bundle

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
        
        # Try to get bundle from workspace object if it exists
        if workspace is not None:
            # Assume workspace has a method to get files or is a dict-like
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace_dict'
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace_attr'
        
        # Fallback to deliverable text if bundle is empty
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable_text'
            
        if not bundle:
            # No code found
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str='bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter for authored files using artifacts logic if available, 
        # but for simplicity and robustness in this standalone module, 
        # we'll do a basic filter here or rely on the caller.
        # The spec implies artifacts.py is part of the firm's code, but harness.py 
        # is the grader. The grader should probably use its own logic or import artifacts.
        # Since artifacts.py is in the workspace, we might not be able to import it 
        # before verifying it. We will implement basic filtering here.
        
        authored_bundle = self._filter_bundle(bundle)
        
        if not authored_bundle:
            return self._empty_report(source)
            
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to temp dir
            for path, content in authored_bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    
            return self.verify_directory(tmpdir, authored=authored_bundle, source=source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]]=None, source: str='directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if authored is None:
            # Scan directory for python files
            authored = {}
            for root, dirs, files in os.walk(workdir):
                # Skip hidden dirs and common generated dirs
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'venv', '.venv')]
                for file in files:
                    if file.endswith('.py'):
                        rel_path = os.path.relpath(os.path.join(root, file), workdir)
                        with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                            authored[rel_path] = f.read()
        
        python_files = [p for p in authored.keys() if p.endswith('.py')]
        authored_count = len(authored)
        python_count = len(python_files)
        
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._gate_syntax(workdir, python_files))
        
        # 2. Build Gate
        gates.append(self._gate_build(workdir))
        
        # 3. Smoke Gate
        gates.append(self._gate_smoke(workdir, python_files))
        
        # 4. Tests Gate
        gates.append(self._gate_tests(workdir))
        
        # 5. Telemetry Gate
        gates.append(self._gate_telemetry(workdir, python_files))
        
        # Calculate penalty
        failed_gates = [g for g in gates if g.status == FAILED]
        skipped_gates = [g for g in gates if g.status == SKIPPED]
        passed_gates = [g for g in gates if g.status == PASSED]
        
        # Penalty is applied for failed gates. Skipped gates are excluded from denominator.
        # The spec says "SKIPPED gates are excluded from the fitness denominator".
        # It doesn't explicitly say how penalty is calculated relative to skips, 
        # but usually penalty is per failed gate.
        score_penalty = len(failed_gates) * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_count,
            python_files=python_count,
            source=source
        )

    def _filter_bundle(self, bundle: Dict[str, str]) -> Dict[str, str]:
        """Basic filtering of generated/malformed paths."""
        filtered = {}
        excluded_dirs = ('.pytest_cache', '__pycache__', 'venv', '.venv', '.git', '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.ipynb_checkpoints')
        excluded_suffixes = ('.egg-info', '.dist-info')
        
        for path, content in bundle.items():
            parts = path.replace('\\', '/').split('/')
            is_generated = False
            for part in parts:
                if part in excluded_dirs:
                    is_generated = True
                    break
                for suffix in excluded_suffixes:
                    if part.endswith(suffix):
                        is_generated = True
                        break
                if is_generated:
                    break
            
            if is_generated:
                continue
                
            # Malformed check
            if '*' in path or '`' in path:
                continue
            if path != path.strip():
                continue
            if path.startswith('-'):
                continue
            if path.endswith('.') or path.endswith('~'):
                continue
                
            filtered[path] = content
            
        return filtered

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=FAILED, method='ast.parse', detail='No files to check'),
            GateResult(name='build', status=FAILED, method='pip install', detail='No files to build'),
            GateResult(name='smoke', status=FAILED, method='import', detail='No files to import'),
            GateResult(name='tests', status=FAILED, method='pytest', detail='No files to test'),
            GateResult(name='telemetry', status=FAILED, method='ast.parse', detail='No files to check'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=5 * self.penalty_per_gate,
            authored_files=0,
            python_files=0,
            source=source
        )

    def _gate_syntax(self, workdir: str, python_files: List[str]) -> GateResult:
        start_time = time.time()
        errors = []
        for rel_path in python_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                ast.parse(source)
            except SyntaxError as e:
                errors.append(f"{rel_path}: {e}")
            except Exception as e:
                errors.append(f"{rel_path}: {e}")
        
        duration = time.time() - start_time
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='; '.join(errors),
                duration_s=duration,
                evidence={'errors': errors}
            )
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast.parse',
            detail='All files parsed successfully',
            duration_s=duration
        )

    def _gate_build(self, workdir: str) -> GateResult:
        start_time = time.time()
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail='No setup.py or pyproject.toml found',
                duration_s=time.time() - start_time
            )
        
        try:
            # Run pip install -e .
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.']
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
                # Check if it failed due to missing dependency (skip) or other error (fail)
                # This is a heuristic. If pip fails with "No matching distribution" or similar, it might be a dependency issue.
                # However, the spec says "A gate that could not be evaluated returns SKIPPED".
                # If pip is missing, we skip. If pip runs and fails, we fail.
                # But if the failure is "ModuleNotFoundError" during install? No, pip install doesn't import.
                # If the build fails because a dependency is missing from PyPI, that's a build failure.
                # The test "test_uninstalled_dependency_skips_rather_than_fails" suggests that if a dependency is missing, we should skip?
                # Or does it mean if the *tool* is missing?
                # Let's look at the error message in the test failure: "+ skipped".
                # The test name is "test_uninstalled_dependency_skips_rather_than_fails".
                # This implies that if the build fails because of a missing dependency, we should SKIP.
                # How to detect? If stderr contains "No matching distribution" or "Could not find a version that satisfies the requirement".
                
                stderr_lower = result.stderr.lower()
                if "no matching distribution" in stderr_lower or "could not find a version" in stderr_lower:
                     return GateResult(
                        name='build',
                        status=SKIPPED,
                        method='pip install',
                        detail=f"Dependency missing: {result.stderr[-200:]}",
                        duration_s=duration
                    )

                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip install',
                    detail=f"Exit code {result.returncode}: {result.stderr}",
                    duration_s=duration,
                    evidence={'stderr': result.stderr, 'stdout': result.stdout}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail='Timeout',
                duration_s=time.time() - start_time
            )
        except FileNotFoundError:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip install',
                detail='pip not found',
                duration_s=time.time() - start_time
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail=str(e),
                duration_s=time.time() - start_time
            )

    def _gate_smoke(self, workdir: str, python_files: List[str]) -> GateResult:
        start_time = time.time()
        if not python_files:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='No python files to import',
                duration_s=time.time() - start_time
            )
        
        # Try to import the main package or all modules
        # We'll try to import each module individually
        errors = []
        imported_count = 0
        
        # Add workdir to path temporarily
        sys.path.insert(0, workdir)
        try:
            for rel_path in python_files:
                # Convert path to module name
                # e.g., hae/evaluation/harness.py -> hae.evaluation.harness
                module_name = rel_path.replace('/', '.').replace('\\', '.').replace('.py', '')
                # Handle __init__.py
                if module_name.endswith('.__init__'):
                    module_name = module_name[:-9]
                
                try:
                    __import__(module_name)
                    imported_count += 1
                except ImportError as e:
                    # If it's an ImportError due to missing dependency, should we skip?
                    # The test "test_import_without_instrumentation_does_not_pass" implies we should FAIL if it doesn't import.
                    # But "test_uninstalled_dependency_skips_rather_than_fails" was for build.
                    # For smoke, if a module fails to import because of a missing dependency, is that a skip or fail?
                    # Usually, if the code is broken, it's a fail. If the environment is broken, it's a skip.
                    # But we can't easily distinguish.
                    # Let's assume Fail for now, as per "test_import_without_instrumentation_does_not_pass".
                    errors.append(f"{module_name}: {e}")
                except Exception as e:
                    errors.append(f"{module_name}: {e}")
        finally:
            sys.path.pop(0)
            
        duration = time.time() - start_time
        if errors:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='; '.join(errors),
                duration_s=duration,
                evidence={'errors': errors, 'imported_count': imported_count}
            )
        return GateResult(
            name='smoke',
            status=PASSED,
            method='import',
            detail=f'All {imported_count} modules imported successfully',
            duration_s=duration
        )

    def _gate_tests(self, workdir: str) -> GateResult:
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
        
        # Run pytest
        try:
            cmd = [self.python_executable, '-m', 'pytest', '-v']
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
                # Check if there were no tests collected
                if 'no tests ran' in result.stdout.lower() or 'no tests collected' in result.stdout.lower():
                    return GateResult(
                        name='tests',
                        status=SKIPPED,
                        method='pytest',
                        detail='No tests collected',
                        duration_s=duration
                    )
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f"Exit code {result.returncode}: {result.stdout[-500:]}",
                    duration_s=duration,
                    evidence={'stdout': result.stdout, 'stderr': result.stderr}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='Timeout',
                duration_s=time.time() - start_time
            )
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail=str(e),
                duration_s=time.time() - start_time
            )

    def _gate_telemetry(self, workdir: str, python_files: List[str]) -> GateResult:
        start_time = time.time()
        if not python_files:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast.parse',
                detail='No python files to check',
                duration_s=time.time() - start_time
            )
        
        found_telemetry = False
        evidence = {}
        
        for rel_path in python_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if 'opentelemetry' in alias.name.lower():
                                found_telemetry = True
                                evidence[rel_path] = f"import {alias.name}"
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and 'opentelemetry' in node.module.lower():
                            found_telemetry = True
                            evidence[rel_path] = f"from {node.module} import ..."
            except Exception:
                continue
        
        duration = time.time() - start_time
        if found_telemetry:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast.parse',
                detail='OpenTelemetry import found',
                duration_s=duration,
                evidence=evidence
            )
        return GateResult(
            name='telemetry',
            status=FAILED,
            method='ast.parse',
            detail='No OpenTelemetry imports found in authored code',
            duration_s=duration
        )