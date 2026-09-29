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
import subprocess
import sys
import tempfile
import time
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Regex for extracting code blocks from markdown
CODE_BLOCK_RE = re.compile(
    r"```(?:python|py)?\s*\n(.*?)\n\s*```",
    re.DOTALL | re.IGNORECASE
)


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
            status_icon = "PASS" if g.passed else ("FAIL" if g.status == FAILED else "SKIP")
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
                    "evidence": g.evidence,
                }
                for g in self.gates
            ],
            "score_penalty": self.score_penalty,
            "authored_files": self.authored_files,
            "python_files": self.python_files,
            "source": self.source,
            "harness_version": self.harness_version,
            "pass_rate": self.pass_rate,
            "gate_status": self.gate_status,
        }


def extract_code_blocks(text: str) -> Dict[str, str]:
    """Recovers a path->content map from fenced code blocks in a deliverable.

    A last resort, used only when a firm produced no live workspace. Only the
    contents of fenced blocks are returned; the surrounding prose is discarded
    and never reaches a gate.
    """
    if not text:
        return {}
    
    blocks = CODE_BLOCK_RE.findall(text)
    result = {}
    
    for i, block in enumerate(blocks):
        lines = block.split('\n')
        path_hint = None
        for line in lines[:5]:
            match = re.search(r"(?:#|//)\s*([a-zA-Z0-9_/\-\.]+\.py)", line)
            if match:
                path_hint = match.group(1)
                break
        
        if path_hint:
            path_hint = path_hint.strip()
            if not path_hint.startswith('/'):
                path_hint = path_hint.lstrip('./')
            result[path_hint] = block
        else:
            result[f"extracted_block_{i}.py"] = block
            
    return result


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
            if hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace'
            elif hasattr(workspace, 'get_files'):
                try:
                    bundle = workspace.get_files()
                    source = 'workspace'
                except Exception:
                    pass
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace_dict'
        
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable_text'
            
        if not bundle:
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        if not bundle:
            return self._empty_report(source)
            
        try:
            from hae.evaluation.artifacts import filter_bundle
            filtered_bundle = filter_bundle(bundle)
        except ImportError:
            filtered_bundle = {k: v for k, v in bundle.items() if not k.startswith('.') and '__pycache__' not in k}
            
        if not filtered_bundle:
            return self._empty_report(source)
            
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
            authored = {}
            for root, dirs, files in os.walk(workdir):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'venv', '.venv', 'node_modules')]
                for file in files:
                    if file.endswith('.py'):
                        rel_path = os.path.relpath(os.path.join(root, file), workdir)
                        with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                            authored[rel_path] = f.read()
        
        python_files = [p for p in authored.keys() if p.endswith('.py')]
        authored_files_count = len(authored)
        python_files_count = len(python_files)
        
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
        failed_count = sum(1 for g in gates if g.status == FAILED)
        score_penalty = failed_count * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files_count,
            python_files=python_files_count,
            source=source,
            harness_version='3.0-execution'
        )

    def _empty_report(self, source: str) -> VerificationReport:
        """Empty workspace: all gates are FAILED because they were evaluable but found nothing."""
        gates = [
            GateResult(name='syntax', status=FAILED, method='ast.parse', detail='No python files found'),
            GateResult(name='build', status=FAILED, method='pip install -e .', detail='No package to install'),
            GateResult(name='smoke', status=FAILED, method='import', detail='No modules to import'),
            GateResult(name='tests', status=FAILED, method='pytest', detail='No test files found'),
            GateResult(name='telemetry', status=FAILED, method='ast.parse', detail='No OpenTelemetry imports found'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=5 * self.penalty_per_gate,
            authored_files=0,
            python_files=0,
            source=source,
            harness_version='3.0-execution'
        )

    def _gate_syntax(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        if not python_files:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='No python files found',
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
            detail=f"Successfully parsed {len(python_files)} files",
            duration_s=duration,
            evidence={'file_count': len(python_files)}
        )

    def _gate_build(self, workdir: str) -> GateResult:
        start = time.time()
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
            result = subprocess.run(
                [self.python_executable, '-m', 'pip', 'install', '-e', '.'],
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
                    duration_s=duration,
                    evidence={'stdout': result.stdout[-500:], 'stderr': result.stderr[-500:]}
                )
            else:
                # Check if it's a dependency issue - if so, SKIP rather than FAIL
                stderr_lower = result.stderr.lower()
                if 'no matching distribution' in stderr_lower or 'could not find a version' in stderr_lower or 'dependency' in stderr_lower:
                    return GateResult(
                        name='build',
                        status=SKIPPED,
                        method='pip install -e .',
                        detail=f'Dependency unavailable: {result.stderr[-300:]}',
                        duration_s=duration,
                        evidence={'returncode': result.returncode, 'stderr': result.stderr}
                    )
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip install -e .',
                    detail=f"Install failed: {result.stderr[-500:]}",
                    duration_s=duration,
                    evidence={'returncode': result.returncode, 'stderr': result.stderr}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail='Install timed out',
                duration_s=time.time() - start
            )
        except FileNotFoundError:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip install -e .',
                detail='pip not available',
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

    def _gate_smoke(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        if not python_files:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='No python files to import',
                duration_s=time.time() - start
            )
        
        errors = []
        imported_count = 0
        
        old_path = sys.path.copy()
        sys.path.insert(0, workdir)
        
        try:
            for rel_path in python_files:
                module_name = rel_path.replace('/', '.').replace('\\', '.').replace('.py', '')
                module_name = module_name.lstrip('.')
                
                try:
                    __import__(module_name)
                    imported_count += 1
                except ImportError as e:
                    errors.append(f"{module_name}: {e}")
                except Exception as e:
                    errors.append(f"{module_name}: {e}")
        finally:
            sys.path[:] = old_path
            
        duration = time.time() - start
        if errors:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='; '.join(errors[:5]),
                duration_s=duration,
                evidence={'errors': errors, 'imported_count': imported_count}
            )
        return GateResult(
            name='smoke',
            status=PASSED,
            method='import',
            detail=f"Successfully imported {imported_count} modules",
            duration_s=duration,
            evidence={'imported_count': imported_count}
        )

    def _gate_tests(self, workdir: str) -> GateResult:
        start = time.time()
        # Check if pytest is available
        try:
            subprocess.run(
                [self.python_executable, '-m', 'pytest', '--version'],
                capture_output=True,
                timeout=10
            )
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
            result = subprocess.run(
                [self.python_executable, '-m', 'pytest', '-v', '--tb=short'],
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
                    evidence={'stdout': result.stdout[-500:], 'stderr': result.stderr[-500:]}
                )
            else:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f"Tests failed: {result.stderr[-500:]}",
                    duration_s=duration,
                    evidence={'returncode': result.returncode, 'stderr': result.stderr}
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

    def _gate_telemetry(self, workdir: str, python_files: List[str]) -> GateResult:
        start = time.time()
        if not python_files:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast.parse',
                detail='No python files to check for OpenTelemetry imports',
                duration_s=time.time() - start
            )
        
        found_otel = False
        evidence = {}
        
        for rel_path in python_files:
            full_path = os.path.join(workdir, rel_path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                tree = ast.parse(content)
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if 'opentelemetry' in alias.name:
                                found_otel = True
                                evidence[rel_path] = f"import {alias.name}"
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and 'opentelemetry' in node.module:
                            found_otel = True
                            evidence[rel_path] = f"from {node.module} import ..."
            except Exception:
                continue
        
        duration = time.time() - start
        if found_otel:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast.parse',
                detail='OpenTelemetry import found',
                duration_s=duration,
                evidence=evidence
            )
        else:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast.parse',
                detail='No OpenTelemetry imports found in authored code',
                duration_s=duration,
                evidence={'checked_files': len(python_files)}
            )