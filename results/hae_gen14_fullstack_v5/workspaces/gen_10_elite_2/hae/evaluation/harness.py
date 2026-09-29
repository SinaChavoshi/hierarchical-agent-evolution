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

# Dependency on artifacts module (assumed available in environment or implemented in parallel)
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
    # Fallback minimal implementation if artifacts module is not yet available
    # This ensures the harness can run independently for testing if needed,
    # though the spec implies it's a dependency.
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

    def count_source_files(bundle: Dict[str, str], suffixes: Tuple[str, ...] = ('.py',)) -> int:
        count = 0
        for path in bundle:
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
        lines = [
            f"Verification Report ({self.harness_version})",
            f"Source: {self.source}",
            f"Authored Files: {self.authored_files} (Python: {self.python_files})",
            f"Score Penalty: {self.score_penalty:.2f}",
            f"Pass Rate: {self.pass_rate:.2%}",
            "-" * 40
        ]
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"[{status_icon}] {g.name:10} | {g.status:7} | {g.method:15} | {g.detail[:50]}")
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
    # Pattern: ```[language] [filename]\n...\n```
    # We look for blocks that might contain a filename in the info string or just assume sequential naming if not present.
    # However, the spec implies a path->content map. Usually, agents write:
    # ```python
    # # path: src/main.py
    # ...
    # ```
    # OR
    # ```python src/main.py
    # ...
    # ```
    
    blocks = {}
    # Match ``` followed by optional info string, then content, then ```
    pattern = r"```([^\n]*)\n(.*?)```"
    matches = re.finditer(pattern, text, re.DOTALL)
    
    counter = 0
    for match in matches:
        info = match.group(1).strip()
        content = match.group(2)
        
        # Try to extract filename from info string
        # Common patterns: "python", "python src/main.py", "src/main.py"
        filename = None
        if info:
            parts = info.split()
            # If the first part looks like a path (contains / or .py), use it
            # Otherwise, check subsequent parts
            for part in parts:
                if '/' in part or part.endswith('.py') or part.endswith('.txt') or part.endswith('.md'):
                    filename = part
                    break
        
        if not filename:
            # Fallback: look for a comment in the first few lines like "# path: ..."
            lines = content.split('\n')
            for line in lines[:5]:
                if line.strip().startswith('#') and 'path:' in line.lower():
                    # Extract path after 'path:'
                    path_part = line.split('path:', 1)[1].strip()
                    if path_part:
                        filename = path_part
                        break
            
        if not filename:
            # Last resort: generate a name
            filename = f"extracted_block_{counter}.py"
            counter += 1
            
        # Sanitize the filename if needed
        filename = sanitize_path(filename)
        
        # Avoid overwriting if duplicate names occur (append counter)
        if filename in blocks:
            base, ext = os.path.splitext(filename)
            filename = f"{base}_{counter}{ext}"
            counter += 1
            
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
        
        # Check if workspace is a directory path
        if isinstance(workspace, str) and os.path.isdir(workspace):
            return self.verify_directory(workspace, source='directory')
        
        # Check if workspace is a dict (bundle)
        if isinstance(workspace, dict):
            bundle = workspace
            source = 'bundle'
        elif workspace is not None:
            # Try to treat it as a directory-like object or path
            try:
                if hasattr(workspace, 'list_files'):
                    # Mocking a workspace object
                    files = workspace.list_files()
                    for f in files:
                        if hasattr(workspace, 'read_file'):
                            bundle[f] = workspace.read_file(f)
                    source = 'workspace_object'
                else:
                    # Assume it's a path string if not dict
                    if os.path.isdir(str(workspace)):
                        return self.verify_directory(str(workspace), source='directory')
            except Exception:
                pass

        # Fallback to deliverable text if bundle is empty
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable_text'
            
        if not bundle:
            # No code found
            return self._empty_report(source)

        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to remove generated/malformed paths
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files = len(authored_bundle)
        python_files = count_source_files(authored_bundle, suffixes=('.py',))
        
        if authored_files == 0:
            return self._empty_report(source, authored_files=0, python_files=0)

        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to temp dir
            for path, content in authored_bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            # Run gates
            return self._run_gates(tmpdir, authored_files, python_files, source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if not os.path.isdir(workdir):
            return self._empty_report(source)
            
        # Collect files from directory
        bundle = {}
        for root, dirs, files in os.walk(workdir):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            
            for file in files:
                rel_path = os.path.relpath(os.path.join(root, file), workdir)
                if is_generated_path(rel_path) or is_malformed_path(rel_path):
                    continue
                try:
                    with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                        bundle[rel_path] = f.read()
                except Exception:
                    pass
        
        authored_files = len(bundle)
        python_files = count_source_files(bundle, suffixes=('.py',))
        
        if authored_files == 0:
            return self._empty_report(source, authored_files=0, python_files=0)
            
        return self._run_gates(workdir, authored_files, python_files, source)

    def _empty_report(self, source: str, authored_files: int = 0, python_files: int = 0) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=SKIPPED, method='ast_parse', detail='No files to check'),
            GateResult(name='build', status=SKIPPED, method='pip_install', detail='No files to build'),
            GateResult(name='smoke', status=SKIPPED, method='import', detail='No files to import'),
            GateResult(name='tests', status=SKIPPED, method='pytest', detail='No files to test'),
            GateResult(name='telemetry', status=SKIPPED, method='ast_import', detail='No files to check'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=0.0,
            authored_files=authored_files,
            python_files=python_files,
            source=source
        )

    def _run_gates(self, workdir: str, authored_files: int, python_files: int, source: str) -> VerificationReport:
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._gate_syntax(workdir))
        
        # 2. Build Gate
        gates.append(self._gate_build(workdir))
        
        # 3. Smoke Gate
        gates.append(self._gate_smoke(workdir))
        
        # 4. Tests Gate
        gates.append(self._gate_tests(workdir))
        
        # 5. Telemetry Gate
        gates.append(self._gate_telemetry(workdir))
        
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

    def _gate_syntax(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'ast_parse'
        try:
            py_files = []
            for root, dirs, files in os.walk(workdir):
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
                for f in files:
                    if f.endswith('.py'):
                        rel_path = os.path.relpath(os.path.join(root, f), workdir)
                        if not is_malformed_path(rel_path):
                            py_files.append(os.path.join(root, f))
            
            if not py_files:
                return GateResult(name='syntax', status=SKIPPED, method=method, detail='No python files found', duration_s=time.time()-start)
            
            errors = []
            for f in py_files:
                try:
                    with open(f, 'r', encoding='utf-8') as fh:
                        ast.parse(fh.read())
                except SyntaxError as e:
                    errors.append(f"{f}: {e}")
            
            if errors:
                return GateResult(name='syntax', status=FAILED, method=method, detail='; '.join(errors[:5]), duration_s=time.time()-start)
            
            return GateResult(name='syntax', status=PASSED, method=method, detail=f'{len(py_files)} files parsed', duration_s=time.time()-start)
        except Exception as e:
            return GateResult(name='syntax', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _gate_build(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'pip_install'
        
        # Check if setup.py or pyproject.toml exists
        setup_py = os.path.join(workdir, 'setup.py')
        pyproject_toml = os.path.join(workdir, 'pyproject.toml')
        
        if not os.path.exists(setup_py) and not os.path.exists(pyproject_toml):
            return GateResult(name='build', status=FAILED, method=method, detail='No setup.py or pyproject.toml found', duration_s=time.time()-start)
        
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
            
            if result.returncode == 0:
                return GateResult(name='build', status=PASSED, method=method, detail='Installed successfully', duration_s=time.time()-start)
            else:
                return GateResult(name='build', status=FAILED, method=method, detail=result.stderr[:500], duration_s=time.time()-start)
        except subprocess.TimeoutExpired:
            return GateResult(name='build', status=FAILED, method=method, detail='Timeout', duration_s=time.time()-start)
        except Exception as e:
            return GateResult(name='build', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _gate_smoke(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'import'
        
        # Find top-level packages/modules
        # We try to import the package name derived from the directory or setup.py
        # For simplicity, we try to import all top-level .py files and packages
        
        # Get list of top-level items
        items = os.listdir(workdir)
        modules_to_import = []
        
        for item in items:
            full_path = os.path.join(workdir, item)
            if os.path.isdir(full_path):
                if os.path.exists(os.path.join(full_path, '__init__.py')):
                    modules_to_import.append(item)
            elif item.endswith('.py') and item != 'setup.py':
                modules_to_import.append(item[:-3])
        
        if not modules_to_import:
            return GateResult(name='smoke', status=SKIPPED, method=method, detail='No importable modules found', duration_s=time.time()-start)
        
        # Try to import them
        # We need to add workdir to sys.path temporarily
        old_path = sys.path.copy()
        sys.path.insert(0, workdir)
        
        errors = []
        imported_count = 0
        try:
            for mod in modules_to_import:
                try:
                    __import__(mod)
                    imported_count += 1
                except Exception as e:
                    errors.append(f"{mod}: {e}")
        finally:
            sys.path = old_path
            
        if errors:
            return GateResult(name='smoke', status=FAILED, method=method, detail='; '.join(errors[:5]), duration_s=time.time()-start)
        
        return GateResult(name='smoke', status=PASSED, method=method, detail=f'{imported_count} modules imported', duration_s=time.time()-start)

    def _gate_tests(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'pytest'
        
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], capture_output=True, timeout=10)
        except Exception:
            return GateResult(name='tests', status=SKIPPED, method=method, detail='pytest not available', duration_s=time.time()-start)
        
        # Check if tests directory or test_*.py files exist
        has_tests = False
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for f in files:
                if f.startswith('test_') and f.endswith('.py'):
                    has_tests = True
                    break
            if has_tests:
                break
            if 'tests' in dirs:
                has_tests = True
                break
        
        if not has_tests:
            return GateResult(name='tests', status=FAILED, method=method, detail='No tests found', duration_s=time.time()-start)
        
        try:
            cmd = [self.python_executable, '-m', 'pytest', '-q']
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            if result.returncode == 0:
                return GateResult(name='tests', status=PASSED, method=method, detail='Tests passed', duration_s=time.time()-start)
            else:
                return GateResult(name='tests', status=FAILED, method=method, detail=result.stdout[-500:] + result.stderr[-500:], duration_s=time.time()-start)
        except subprocess.TimeoutExpired:
            return GateResult(name='tests', status=FAILED, method=method, detail='Timeout', duration_s=time.time()-start)
        except Exception as e:
            return GateResult(name='tests', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _gate_telemetry(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'ast_import'
        
        # Check if OpenTelemetry is imported in any authored python file
        # We look for 'import opentelemetry' or 'from opentelemetry'
        
        py_files = []
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for f in files:
                if f.endswith('.py'):
                    rel_path = os.path.relpath(os.path.join(root, f), workdir)
                    if not is_malformed_path(rel_path):
                        py_files.append(os.path.join(root, f))
        
        if not py_files:
            return GateResult(name='telemetry', status=SKIPPED, method=method, detail='No python files found', duration_s=time.time()-start)
        
        found = False
        for f in py_files:
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    content = fh.read()
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if 'opentelemetry' in alias.name:
                                    found = True
                                    break
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and 'opentelemetry' in node.module:
                                found = True
                                break
                        if found:
                            break
                if found:
                    break
            except Exception:
                continue
        
        if found:
            return GateResult(name='telemetry', status=PASSED, method=method, detail='OpenTelemetry import found', duration_s=time.time()-start)
        else:
            return GateResult(name='telemetry', status=FAILED, method=method, detail='No OpenTelemetry import found in code', duration_s=time.time()-start)