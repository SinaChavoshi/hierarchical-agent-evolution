import ast
import os
import subprocess
import sys
import tempfile
import shutil
import re
import time
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Iterable

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency: hae/evaluation/artifacts.py
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
    EXCLUDED_DIR_SEGMENTS = ('.pytest_cache', '__pycache__', 'venv', '.venv', '.git', '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.ipynb_checkpoints')
    EXCLUDED_DIR_SUFFIXES = ('.egg-info', '.dist-info')

    def is_generated_path(path: str) -> bool:
        parts = path.replace('\\', '/').split('/')
        for part in parts:
            if part in EXCLUDED_DIR_SEGMENTS:
                return True
            if any(part.endswith(suffix) for suffix in EXCLUDED_DIR_SUFFIXES):
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
        while path.startswith('-'):
            path = path[1:]
        while path.endswith('.') or path.endswith('~'):
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
        return sum(1 for k in bundle if any(k.endswith(s) for s in suffixes))


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
            "-" * 40
        ]
        for g in self.gates:
            status_icon = "✅" if g.passed else ("⏭️" if g.status == SKIPPED else "❌")
            lines.append(f"{status_icon} {g.name}: {g.status} ({g.method})")
            if g.detail:
                lines.append(f"   Detail: {g.detail}")
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
    blocks = {}
    pattern = r"```(\w+)?\s*([^\n]*)\n(.*?)```"
    
    matches = re.finditer(pattern, text, re.DOTALL)
    
    for match in matches:
        lang = match.group(1)
        header_rest = match.group(2).strip()
        content = match.group(3)
        
        filename = None
        
        # Case 1: Filename in header (e.g., ```python file.py)
        if header_rest:
            if '/' in header_rest or '.' in header_rest:
                filename = header_rest
        
        # Case 2: Filename in first line of content
        if not filename:
            lines = content.split('\n')
            if lines:
                first_line = lines[0].strip()
                if ('/' in first_line or '.' in first_line) and not first_line.startswith(('def ', 'class ', 'import ', 'from ', '#', '"""', "'''")):
                    if re.match(r'^[\w/\.\-\_]+$', first_line):
                        filename = first_line
                        content = '\n'.join(lines[1:])
        
        if filename:
            clean_filename = sanitize_path(filename)
            if clean_filename:
                blocks[clean_filename] = content
                
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
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace'
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace'
        
        # Fallback to deliverable text if bundle is empty
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to only authored files
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
        gates = []
        
        # Determine authored files count if not provided
        if authored is None:
            all_files = {}
            for root, dirs, files in os.walk(workdir):
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if not is_generated_path(rel_path) and not is_malformed_path(rel_path):
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                all_files[rel_path] = f.read()
                        except Exception:
                            pass
            authored = all_files
            
        authored_files = len(authored)
        python_files = count_source_files(authored, suffixes=('.py',))
        
        # Gate 1: Syntax
        gates.append(self._gate_syntax(workdir, authored))
        
        # Gate 2: Build
        gates.append(self._gate_build(workdir))
        
        # Gate 3: Smoke
        gates.append(self._gate_smoke(workdir))
        
        # Gate 4: Tests
        gates.append(self._gate_tests(workdir))
        
        # Gate 5: Telemetry
        gates.append(self._gate_telemetry(workdir, authored))
        
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

    def _gate_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = "ast.parse"
        errors = []
        
        py_files = [p for p in authored if p.endswith('.py')]
        
        for path in py_files:
            content = authored[path]
            try:
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
        
        duration = time.time() - start_time
        
        if not py_files:
            # Empty workspace: fail syntax since there's nothing valid
            return GateResult(
                name="syntax",
                status=FAILED,
                method=method,
                detail="No Python files to check",
                duration_s=duration
            )
            
        if errors:
            return GateResult(
                name="syntax",
                status=FAILED,
                method=method,
                detail="; ".join(errors),
                duration_s=duration,
                evidence={"errors": errors}
            )
            
        return GateResult(
            name="syntax",
            status=PASSED,
            method=method,
            detail="All Python files parsed successfully",
            duration_s=duration
        )

    def _gate_build(self, workdir: str) -> GateResult:
        start_time = time.time()
        method = "pip install -e ."
        
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            # Missing manifest: fail build
            return GateResult(
                name="build",
                status=FAILED,
                method=method,
                detail="No setup.py or pyproject.toml found",
                duration_s=time.time() - start_time
            )
            
        try:
            cmd = [self.python_executable, "-m", "pip", "install", "-e", "."]
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
                    name="build",
                    status=PASSED,
                    method=method,
                    detail="Package installed successfully",
                    duration_s=duration
                )
            else:
                return GateResult(
                    name="build",
                    status=FAILED,
                    method=method,
                    detail=f"Exit code {result.returncode}: {result.stderr}",
                    duration_s=duration,
                    evidence={"stderr": result.stderr, "stdout": result.stdout}
                )
                
        except subprocess.TimeoutExpired:
            return GateResult(
                name="build",
                status=FAILED,
                method=method,
                detail=f"Timeout after {self.timeout_s}s",
                duration_s=time.time() - start_time
            )
        except Exception as e:
            return GateResult(
                name="build",
                status=FAILED,
                method=method,
                detail=f"Exception: {str(e)}",
                duration_s=time.time() - start_time
            )

    def _gate_smoke(self, workdir: str) -> GateResult:
        start_time = time.time()
        method = "import modules"
        
        # Find all .py files in the package structure
        py_files = []
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for file in files:
                if file.endswith('.py'):
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if not is_generated_path(rel_path) and not is_malformed_path(rel_path):
                        py_files.append(rel_path)
        
        if not py_files:
            return GateResult(
                name="smoke",
                status=FAILED,
                method=method,
                detail="No Python files to import",
                duration_s=time.time() - start_time
            )
            
        # Try to import each module
        original_path = sys.path.copy()
        sys.path.insert(0, workdir)
        
        errors = []
        imported_count = 0
        
        for rel_path in py_files:
            module_path = rel_path[:-3]
            module_name = module_path.replace(os.sep, '.')
            
            if module_name.endswith('.__init__'):
                module_name = module_name[:-9]
            
            if not module_name:
                continue
                
            try:
                __import__(module_name)
                imported_count += 1
            except ImportError as e:
                # Uninstalled dependency: skip rather than fail
                errors.append(f"{module_name}: {str(e)}")
            except Exception as e:
                errors.append(f"{module_name}: {str(e)}")
                
        sys.path[:] = original_path
        
        duration = time.time() - start_time
        
        if errors:
            # Check if all errors are import errors (uninstalled deps)
            all_import_errors = all("No module named" in e or "cannot import name" in e for e in errors)
            if all_import_errors and imported_count == 0:
                return GateResult(
                    name="smoke",
                    status=SKIPPED,
                    method=method,
                    detail=f"Dependencies not installed: {errors[0]}",
                    duration_s=duration,
                    evidence={"errors": errors, "imported": imported_count}
                )
            return GateResult(
                name="smoke",
                status=FAILED,
                method=method,
                detail=f"Failed to import {len(errors)} modules: {errors[0]}",
                duration_s=duration,
                evidence={"errors": errors, "imported": imported_count}
            )
            
        return GateResult(
            name="smoke",
            status=PASSED,
            method=method,
            detail=f"Successfully imported {imported_count} modules",
            duration_s=duration
        )

    def _gate_tests(self, workdir: str) -> GateResult:
        start_time = time.time()
        method = "pytest"
        
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, "-m", "pytest", "--version"], capture_output=True, timeout=10)
        except Exception:
            return GateResult(
                name="tests",
                status=SKIPPED,
                method=method,
                detail="pytest not available",
                duration_s=time.time() - start_time
            )
            
        # Check if there are test files
        test_files = []
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for file in files:
                if file.startswith('test_') or file.endswith('_test.py'):
                    test_files.append(os.path.join(root, file))
                    
        if not test_files:
            # Missing tests: fail rather than skip
            return GateResult(
                name="tests",
                status=FAILED,
                method=method,
                detail="No test files found",
                duration_s=time.time() - start_time
            )
            
        try:
            cmd = [self.python_executable, "-m", "pytest", "-v", "--tb=short"]
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
                    name="tests",
                    status=PASSED,
                    method=method,
                    detail="All tests passed",
                    duration_s=duration
                )
            else:
                # Extract summary from output
                detail = result.stdout.splitlines()[-1] if result.stdout else "Tests failed"
                return GateResult(
                    name="tests",
                    status=FAILED,
                    method=method,
                    detail=detail,
                    duration_s=duration,
                    evidence={"stdout": result.stdout, "stderr": result.stderr}
                )
                
        except subprocess.TimeoutExpired:
            return GateResult(
                name="tests",
                status=FAILED,
                method=method,
                detail=f"Timeout after {self.timeout_s}s",
                duration_s=time.time() - start_time
            )
        except Exception as e:
            return GateResult(
                name="tests",
                status=FAILED,
                method=method,
                detail=f"Exception: {str(e)}",
                duration_s=time.time() - start_time
            )

    def _gate_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = "ast analysis for opentelemetry import"
        
        # Check if any authored python file imports opentelemetry
        found = False
        evidence = {}
        
        py_files = [p for p in authored if p.endswith('.py')]
        
        for path in py_files:
            content = authored[path]
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith('opentelemetry'):
                                found = True
                                evidence[path] = f"import {alias.name}"
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.module.startswith('opentelemetry'):
                            found = True
                            evidence[path] = f"from {node.module} import ..."
            except SyntaxError:
                continue
                
        duration = time.time() - start_time
        
        if not py_files:
            return GateResult(
                name="telemetry",
                status=FAILED,
                method=method,
                detail="No Python files to check",
                duration_s=duration
            )
            
        if found:
            return GateResult(
                name="telemetry",
                status=PASSED,
                method=method,
                detail="OpenTelemetry import found",
                duration_s=duration,
                evidence=evidence
            )
        else:
            return GateResult(
                name="telemetry",
                status=FAILED,
                method=method,
                detail="No OpenTelemetry import found in authored code",
                duration_s=duration
            )