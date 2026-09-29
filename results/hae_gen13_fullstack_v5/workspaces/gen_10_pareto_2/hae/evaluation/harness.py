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
from typing import Any, Dict, List, Optional, Tuple, Iterable

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
        """True if the path lives inside a machine-generated directory."""
        if not path:
            return False
        parts = path.replace('\\', '/').split('/')
        for part in parts:
            if not part:
                continue
            if part in EXCLUDED_DIR_SEGMENTS:
                return True
            if any(part.endswith(s) for s in EXCLUDED_DIR_SUFFIXES):
                return True
        return False

    def is_malformed_path(path: str) -> bool:
        """True if markdown formatting, stray whitespace, or invalid punctuation leaked into the filename."""
        if not path:
            return True
        if '*' in path or '`' in path:
            return True
        if path != path.strip():
            return True
        if path.startswith('-'):
            return True
        if path.endswith('.') or path.endswith('~'):
            return True
        return False

    def sanitize_path(path: str) -> str:
        """Strips markdown decoration from an agent-supplied path."""
        if not path:
            return ""
        cleaned = path.strip()
        cleaned = cleaned.replace('*', '').replace('`', '')
        while cleaned.startswith('-'):
            cleaned = cleaned[1:]
        while cleaned.endswith('.') or cleaned.endswith('~'):
            cleaned = cleaned[:-1]
        return cleaned

    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        """Returns only genuinely agent-authored files from a path->content map."""
        return {
            k: v for k, v in bundle.items()
            if not is_generated_path(k) and not is_malformed_path(k)
        }

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

    def count_source_files(bundle: Dict[str, str], suffixes: Iterable[str] = ('.py',)) -> int:
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
            lines.append(f"[{status_icon}] {g.name:10} | {g.status:7} | {g.method}")
            if g.detail:
                lines.append(f"    Detail: {g.detail}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "harness_version": self.harness_version,
            "source": self.source,
            "authored_files": self.authored_files,
            "python_files": self.python_files,
            "score_penalty": self.score_penalty,
            "pass_rate": self.pass_rate,
            "gate_status": self.gate_status,
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
            ]
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
    # Regex to find fenced code blocks with optional language and optional filename hint
    # Common patterns:
    # ```python
    # ```path/to/file.py
    # ```python path/to/file.py
    # ```
    
    pattern = r"```(?:\s*([a-zA-Z0-9_\-]+))?(?:\s+([^\s`]+))?\s*\n(.*?)\n```"
    
    matches = re.finditer(pattern, text, re.DOTALL)
    
    for match in matches:
        lang = match.group(1)
        filename_hint = match.group(2)
        content = match.group(3)
        
        # Determine path
        path = None
        if filename_hint:
            # Clean up filename hint
            path = filename_hint.strip()
            # If it looks like a language tag was mistaken for filename, ignore
            if path in ('python', 'py', 'bash', 'sh', 'json', 'yaml', 'yml', 'md', 'markdown'):
                path = None
        
        if not path:
            # Try to infer from content or use a generic name
            # If lang is python, maybe it's a script?
            # For now, if no path is provided, we might skip or use a generic name.
            # However, the spec says "path->content map". 
            # If no path is given, we can't really map it. 
            # But often agents write:
            # ```python
            # import os
            # ```
            # This has no path. We should probably ignore these or assign a dummy path if we want to check syntax.
            # But `verify_bundle` expects paths. 
            # Let's assign a dummy path if it's python code, so it can be checked for syntax.
            if lang in ('python', 'py'):
                path = f"extracted_block_{len(blocks)}.py"
            else:
                continue # Skip non-python blocks without paths
        
        # Sanitize path
        path = sanitize_path(path)
        if not path:
            continue
            
        # If path already exists, append a suffix to avoid collision
        if path in blocks:
            base, ext = os.path.splitext(path)
            path = f"{base}_{len(blocks)}{ext}"
            
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
        
        # Try to get bundle from workspace
        if workspace is not None:
            # Assume workspace has a method to get files or is a dict
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace'
            elif hasattr(workspace, 'list_files'):
                # Some workspaces might have list_files returning paths, and read_file
                # But usually get_files is better. If not, we might need to iterate.
                # For now, if it's not a dict or has get_files, we might fail to get bundle.
                pass
        
        # If bundle is empty, try extracting from deliverable text
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        if not bundle:
            # No code found
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No files to check'),
            GateResult(name='build', status=SKIPPED, method='pip install', detail='No files to build'),
            GateResult(name='smoke', status=SKIPPED, method='import', detail='No files to import'),
            GateResult(name='tests', status=SKIPPED, method='pytest', detail='No files to test'),
            GateResult(name='telemetry', status=SKIPPED, method='ast.parse', detail='No files to check'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=0.0,
            authored_files=0,
            python_files=0,
            source=source
        )

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to only authored files
        authored_bundle = filter_bundle(bundle)
        
        if not authored_bundle:
            return self._empty_report(source)
            
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
                # Filter out generated dirs
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
        
        # Count files
        authored_files = len(authored)
        python_files = count_source_files(authored, suffixes=('.py',))
        
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

    def _check_syntax(self, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'ast.parse'
        errors = []
        
        for path, content in authored.items():
            if not path.endswith('.py'):
                continue
            try:
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
        
        duration = time.time() - start
        
        if not any(p.endswith('.py') for p in authored):
            return GateResult(name='syntax', status=SKIPPED, method=method, detail='No Python files', duration_s=duration)
        
        if errors:
            return GateResult(name='syntax', status=FAILED, method=method, detail='; '.join(errors[:5]), duration_s=duration)
        
        return GateResult(name='syntax', status=PASSED, method=method, detail='All Python files parsed', duration_s=duration)

    def _check_build(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'pip install -e .'
        
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            return GateResult(name='build', status=SKIPPED, method=method, detail='No setup.py or pyproject.toml', duration_s=time.time()-start)
        
        try:
            # Run pip install -e .
            # Use a virtual env or just user install? 
            # To avoid polluting global env, we might want to use --user or a venv.
            # But for simplicity in a temp dir, we can try --user or just install.
            # Actually, installing into the current environment might be risky.
            # Let's try to install into a temporary venv? That's slow.
            # Or just check if it *can* be installed?
            # The spec says "The package must actually install".
            
            # We'll run pip install -e . --no-deps to speed it up and avoid dependency hell if possible,
            # but usually we want deps. Let's try standard install.
            
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-cache-dir']
            # Add --user if we are not in a venv? 
            # In a temp dir, it's safer to use --user or a venv.
            # Let's try --user to avoid permission issues.
            cmd.append('--user')
            
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start
            
            if result.returncode == 0:
                return GateResult(name='build', status=PASSED, method=method, detail='Installed successfully', duration_s=duration)
            else:
                return GateResult(name='build', status=FAILED, method=method, detail=result.stderr[-500:], duration_s=duration)
                
        except subprocess.TimeoutExpired:
            return GateResult(name='build', status=FAILED, method=method, detail='Timeout', duration_s=time.time()-start)
        except Exception as e:
            return GateResult(name='build', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _check_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'import'
        
        # Find modules to import
        # Look for __init__.py or top-level .py files
        modules = []
        for path in authored:
            if path.endswith('.py'):
                # Convert path to module name
                # Remove .py
                mod = path[:-3]
                # Replace / with .
                mod = mod.replace('/', '.').replace('\\', '.')
                # Handle __init__
                if mod.endswith('.__init__'):
                    mod = mod[:-9]
                if mod:
                    modules.append(mod)
        
        if not modules:
            return GateResult(name='smoke', status=SKIPPED, method=method, detail='No modules to import', duration_s=time.time()-start)
        
        # Try to import them
        # We need to add workdir to sys.path temporarily
        old_path = sys.path.copy()
        sys.path.insert(0, workdir)
        
        errors = []
        imported_count = 0
        
        for mod in modules:
            try:
                __import__(mod)
                imported_count += 1
            except Exception as e:
                errors.append(f"{mod}: {e}")
        
        sys.path[:] = old_path
        duration = time.time() - start
        
        if errors:
            return GateResult(name='smoke', status=FAILED, method=method, detail='; '.join(errors[:5]), duration_s=duration)
        
        if imported_count == 0:
            return GateResult(name='smoke', status=FAILED, method=method, detail='No modules imported', duration_s=duration)
            
        return GateResult(name='smoke', status=PASSED, method=method, detail=f'Imported {imported_count} modules', duration_s=duration)

    def _check_tests(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'pytest'
        
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], capture_output=True, timeout=10)
        except Exception:
            return GateResult(name='tests', status=SKIPPED, method=method, detail='pytest not available', duration_s=time.time()-start)
        
        # Check if there are test files
        test_files = []
        for root, dirs, files in os.walk(workdir):
            # Skip generated dirs
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for file in files:
                if file.startswith('test_') or file.endswith('_test.py'):
                    test_files.append(os.path.join(root, file))
        
        if not test_files:
            return GateResult(name='tests', status=SKIPPED, method=method, detail='No test files found', duration_s=time.time()-start)
        
        try:
            cmd = [self.python_executable, '-m', 'pytest', '-q', '--tb=short']
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start
            
            if result.returncode == 0:
                return GateResult(name='tests', status=PASSED, method=method, detail='All tests passed', duration_s=duration)
            else:
                # Extract summary
                output = result.stdout + result.stderr
                return GateResult(name='tests', status=FAILED, method=method, detail=output[-500:], duration_s=duration)
                
        except subprocess.TimeoutExpired:
            return GateResult(name='tests', status=FAILED, method=method, detail='Timeout', duration_s=time.time()-start)
        except Exception as e:
            return GateResult(name='tests', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _check_telemetry(self, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'ast.parse'
        
        # Check if any python file imports opentelemetry
        found = False
        for path, content in authored.items():
            if not path.endswith('.py'):
                continue
            try:
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
            except SyntaxError:
                continue
            if found:
                break
        
        duration = time.time() - start
        
        if not any(p.endswith('.py') for p in authored):
            return GateResult(name='telemetry', status=SKIPPED, method=method, detail='No Python files', duration_s=duration)
        
        if found:
            return GateResult(name='telemetry', status=PASSED, method=method, detail='OpenTelemetry imported', duration_s=duration)
        else:
            return GateResult(name='telemetry', status=FAILED, method=method, detail='No OpenTelemetry imports found', duration_s=duration)