import ast
import os
import subprocess
import sys
import tempfile
import shutil
import time
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Iterable

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency contract: hae/evaluation/artifacts.py
# Since this module is being implemented in parallel, we assume it exists or provide fallbacks if needed.
# However, the prompt implies we should just use it. If it's not available, we might need to inline logic.
# Given the strict instruction "Do NOT call execute_bash or list_files first", and "All allowed import modules... are already provided inline below",
# it implies standard library and potentially the artifacts module are available.
# If artifacts.py is not yet on disk, importing it will fail.
# The prompt says "Dependency contract: hae/evaluation/artifacts.py (being implemented in parallel...)".
# Usually in these tasks, if a dependency is mentioned as "being implemented in parallel", it might not be present yet.
# However, the instruction says "Implement the complete Python module hae/evaluation/harness.py".
# Let's assume we can import it. If not, we should probably implement the logic locally or handle ImportError.
# But looking at the "Public API Contract" for artifacts.py, it defines specific functions.
# To be safe and self-contained if artifacts.py isn't ready, I will try to import it, and if it fails, use local implementations.
# Actually, the prompt says "All allowed import modules ... are already provided inline below". This usually refers to the spec text.
# It does NOT say artifacts.py is provided inline.
# Let's look at the usage. `filter_bundle` is needed.
# I will implement a fallback for the artifacts functions if the import fails, to ensure robustness.

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
    # Fallback implementation based on the spec provided in the prompt
    EXCLUDED_DIR_SEGMENTS = ('.pytest_cache', '__pycache__', 'venv', '.venv', '.git', '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.ipynb_checkpoints')
    EXCLUDED_DIR_SUFFIXES = ('.egg-info', '.dist-info')

    def is_generated_path(path: str) -> bool:
        """True if the path lives inside a machine-generated directory."""
        parts = path.replace('\\', '/').split('/')
        for part in parts:
            if part in EXCLUDED_DIR_SEGMENTS:
                return True
            if any(part.endswith(suffix) for suffix in EXCLUDED_DIR_SUFFIXES):
                return True
        return False

    def is_malformed_path(path: str) -> bool:
        """True if markdown formatting, stray whitespace, or invalid punctuation leaked into the filename."""
        if not path:
            return True
        # Check for markdown decoration
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
        # Remove backticks and asterisks
        clean = path.replace('`', '').replace('*', '')
        # Strip whitespace
        clean = clean.strip()
        # Remove leading hyphens? The spec says "leading hyphens ... are malformed". 
        # Sanitize usually tries to fix. If it starts with -, maybe remove it?
        # Spec: "Applied at write time so the artifact lands at its intended location".
        # If the agent wrote `-file.py`, they probably meant `file.py`.
        if clean.startswith('-'):
            clean = clean[1:]
        # Remove trailing dots/tilde
        while clean.endswith('.') or clean.endswith('~'):
            clean = clean[:-1]
        return clean

    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        """Returns only genuinely agent-authored files from a path->content map."""
        return {k: v for k, v in bundle.items() if not is_generated_path(k) and not is_malformed_path(k)}

    def partition_bundle(bundle: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
        """Splits a bundle into (authored, generated, malformed) for reporting."""
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
        """Counts authored files matching the given suffixes."""
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
        passed = len(self.passed_gates)
        return passed / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files} (Python: {self.python_files})")
        lines.append(f"Score Penalty: {self.score_penalty:.2f}")
        lines.append("-" * 40)
        for g in self.gates:
            status_icon = "✅" if g.passed else ("⏭️" if g.status == SKIPPED else "❌")
            lines.append(f"{status_icon} {g.name}: {g.status} ({g.method})")
            if g.detail:
                lines.append(f"   Detail: {g.detail}")
        lines.append("-" * 40)
        lines.append(f"Pass Rate: {self.pass_rate:.2%}")
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
            "pass_rate": self.pass_rate
        }


def extract_code_blocks(text: str) -> Dict[str, str]:
    """Recovers a path->content map from fenced code blocks in a deliverable.

    A last resort, used only when a firm produced no live workspace. Only the
    contents of fenced blocks are returned; the surrounding prose is discarded
    and never reaches a gate.
    """
    # Regex to match fenced code blocks with optional language and optional filename hint
    # Common patterns:
    # ```python
    # ```path/to/file.py
    # ```python path/to/file.py
    # We need to extract content and try to infer path.
    
    blocks = {}
    # Pattern: ```(lang)?\s*(path)?\n(content)\n```
    # This is tricky because standard markdown doesn't enforce path in fence.
    # However, agents often use:
    # ```python
    # # path: src/main.py
    # ...
    # ```
    # Or:
    # ```src/main.py
    # ...
    # ```
    
    # Let's use a robust regex for fenced blocks
    pattern = r"```(\w+)?\s*([^\n]*)\n(.*?)```"
    matches = re.finditer(pattern, text, re.DOTALL)
    
    for match in matches:
        lang = match.group(1)
        hint = match.group(2).strip()
        content = match.group(3)
        
        path = None
        
        # Try to extract path from hint
        if hint:
            # If hint looks like a path (contains / or .py etc)
            if '/' in hint or '.' in hint:
                path = hint
            # If hint is just a filename
            elif hint.endswith('.py') or hint.endswith('.txt') or hint.endswith('.md'):
                path = hint
        
        # If no path in hint, try to find a comment in the first few lines
        if not path:
            lines = content.split('\n')
            for line in lines[:5]:
                # Look for # path: ... or # file: ...
                m = re.search(r"#\s*(?:path|file|filename)\s*:\s*(.+)", line)
                if m:
                    path = m.group(1).strip()
                    break
        
        # If still no path, generate a dummy one based on index or lang
        if not path:
            # Use a counter or hash to avoid collisions
            import hashlib
            h = hashlib.md5(content.encode()).hexdigest()[:8]
            ext = ".py" if lang == "python" or lang is None else f".{lang}" if lang else ".txt"
            path = f"extracted_{h}{ext}"
            
        # Sanitize the path
        path = sanitize_path(path)
        
        # Only add if it's not malformed after sanitization (or if sanitization made it valid)
        # If it's still malformed, we might skip it or keep it? 
        # The spec says "Recovers a path->content map". 
        # If the path is malformed, it might not be usable. 
        # But let's keep it and let the gates decide.
        if path:
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
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
            elif isinstance(workspace, dict):
                bundle = workspace
            else:
                # Try to treat it as a directory path if it's a string
                if isinstance(workspace, str) and os.path.isdir(workspace):
                    return self.verify_directory(workspace, source='directory')
        
        # If bundle is empty, try extracting from deliverable_text
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        if not bundle:
            # No code found
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def _empty_report(self, source: str) -> VerificationReport:
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
            authored_files=0,
            python_files=0,
            source=source
        )

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to get authored files
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files = len(authored_bundle)
        python_files = count_source_files(authored_bundle, suffixes=('.py',))
        
        if authored_files == 0:
            return self._empty_report(source)
            
        # Create temp dir
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files
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
            
        # Scan directory
        bundle = {}
        for root, dirs, files in os.walk(workdir):
            # Prune excluded dirs
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, workdir)
                
                if is_generated_path(rel_path) or is_malformed_path(rel_path):
                    continue
                    
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    bundle[rel_path] = content
                except Exception:
                    # Skip binary or unreadable files
                    continue
        
        authored_files = len(bundle)
        python_files = count_source_files(bundle, suffixes=('.py',))
        
        if authored_files == 0:
            return self._empty_report(source)
            
        return self._run_gates(workdir, authored_files, python_files, source)

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
        # Penalty is applied for each FAILED gate? Or SKIPPED?
        # Spec: "SKIPPED gates are excluded from the fitness denominator rather than scored as failures."
        # "DEFAULT_PENALTY_PER_GATE = 6.25"
        # Usually penalty is for failures.
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
                for file in files:
                    if file.endswith('.py'):
                        rel_path = os.path.relpath(os.path.join(root, file), workdir)
                        if not is_malformed_path(rel_path):
                            py_files.append(os.path.join(root, file))
            
            if not py_files:
                return GateResult(name='syntax', status=SKIPPED, method=method, detail='No python files found', duration_s=time.time()-start)
            
            errors = []
            for f in py_files:
                try:
                    with open(f, 'r', encoding='utf-8') as fp:
                        ast.parse(fp.read())
                except SyntaxError as e:
                    errors.append(f"{f}: {e}")
            
            duration = time.time() - start
            if errors:
                return GateResult(name='syntax', status=FAILED, method=method, detail='; '.join(errors), duration_s=duration)
            else:
                return GateResult(name='syntax', status=PASSED, method=method, detail=f'{len(py_files)} files parsed', duration_s=duration)
                
        except Exception as e:
            return GateResult(name='syntax', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _gate_build(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'pip_install'
        
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            # Try to infer if it's a package? 
            # If no build config, we can't "build" in the pip sense.
            # Should this be SKIPPED or FAILED?
            # "A gate that could not be evaluated returns SKIPPED"
            # If there's no build system, we can't evaluate the build gate.
            return GateResult(name='build', status=SKIPPED, method=method, detail='No setup.py or pyproject.toml found', duration_s=time.time()-start)
        
        try:
            # Run pip install -e .
            # Use a temporary venv? Or just install to current env?
            # Installing to current env might pollute. 
            # But the spec says "The package must actually install (pip install -e .)".
            # We'll run it in the workdir.
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.']
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
                return GateResult(name='build', status=FAILED, method=method, detail=result.stderr, duration_s=duration)
                
        except subprocess.TimeoutExpired:
            return GateResult(name='build', status=FAILED, method=method, detail='Timeout', duration_s=time.time()-start)
        except Exception as e:
            return GateResult(name='build', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _gate_smoke(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'import'
        
        # Find top-level packages/modules
        # We need to import them.
        # We'll try to import all .py files that look like modules.
        
        modules_to_import = []
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for file in files:
                if file.endswith('.py') and not file.startswith('__'):
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if is_malformed_path(rel_path):
                        continue
                    # Convert path to module name
                    # src/pkg/mod.py -> pkg.mod (if src is in path)
                    # We'll add workdir to sys.path temporarily
                    module_name = rel_path.replace(os.sep, '.').replace('/', '.').replace('.py', '')
                    modules_to_import.append((module_name, os.path.join(root, file)))
        
        if not modules_to_import:
            return GateResult(name='smoke', status=SKIPPED, method=method, detail='No modules to import', duration_s=time.time()-start)
        
        # Add workdir to sys.path
        old_path = sys.path.copy()
        sys.path.insert(0, workdir)
        
        errors = []
        imported_count = 0
        
        try:
            for module_name, file_path in modules_to_import:
                try:
                    # Import the module
                    __import__(module_name)
                    imported_count += 1
                except Exception as e:
                    errors.append(f"{module_name}: {e}")
        finally:
            sys.path = old_path
            
        duration = time.time() - start
        
        if errors:
            return GateResult(name='smoke', status=FAILED, method=method, detail='; '.join(errors), duration_s=duration)
        else:
            return GateResult(name='smoke', status=PASSED, method=method, detail=f'{imported_count} modules imported', duration_s=duration)

    def _gate_tests(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'pytest'
        
        # Check if pytest is available
        try:
            import pytest
        except ImportError:
            return GateResult(name='tests', status=SKIPPED, method=method, detail='pytest not installed', duration_s=time.time()-start)
        
        # Find test files
        test_files = []
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for file in files:
                if file.startswith('test_') or file.endswith('_test.py'):
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if not is_malformed_path(rel_path):
                        test_files.append(os.path.join(root, file))
        
        if not test_files:
            return GateResult(name='tests', status=SKIPPED, method=method, detail='No test files found', duration_s=time.time()-start)
        
        try:
            # Run pytest
            # We need to ensure the package is importable. 
            # If build passed, it should be installed. If not, we might need to add to path.
            # Let's assume build gate handled installation. If build was skipped/failed, tests might fail due to import errors.
            
            cmd = [self.python_executable, '-m', 'pytest', '-v', '--tb=short']
            # Add test files explicitly? Or let pytest discover?
            # Letting pytest discover is safer.
            
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
                # Check if it was a collection error or test failure
                # If returncode is 1, tests failed. If 2, interrupted. If 3, internal error. If 4, usage error.
                # If 5, no tests collected.
                if result.returncode == 5:
                    return GateResult(name='tests', status=SKIPPED, method=method, detail='No tests collected', duration_s=time.time()-start)
                return GateResult(name='tests', status=FAILED, method=method, detail=result.stdout + result.stderr, duration_s=duration)
                
        except subprocess.TimeoutExpired:
            return GateResult(name='tests', status=FAILED, method=method, detail='Timeout', duration_s=time.time()-start)
        except Exception as e:
            return GateResult(name='tests', status=FAILED, method=method, detail=str(e), duration_s=time.time()-start)

    def _gate_telemetry(self, workdir: str) -> GateResult:
        start = time.time()
        method = 'ast_import'
        
        # Check if OpenTelemetry is imported in any authored python file
        # We need to parse AST and look for imports of opentelemetry
        
        py_files = []
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if not is_generated_path(os.path.relpath(os.path.join(root, d), workdir))]
            for file in files:
                if file.endswith('.py'):
                    rel_path = os.path.relpath(os.path.join(root, file), workdir)
                    if not is_malformed_path(rel_path):
                        py_files.append(os.path.join(root, file))
        
        if not py_files:
            return GateResult(name='telemetry', status=SKIPPED, method=method, detail='No python files found', duration_s=time.time()-start)
        
        found_telemetry = False
        evidence = {}
        
        for f in py_files:
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    tree = ast.parse(fp.read())
                
                # Walk AST for imports
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith('opentelemetry'):
                                found_telemetry = True
                                evidence[f] = f"import {alias.name}"
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.module.startswith('opentelemetry'):
                            found_telemetry = True
                            evidence[f] = f"from {node.module} import ..."
            except SyntaxError:
                # Already handled by syntax gate, skip here
                continue
            except Exception:
                continue
        
        duration = time.time() - start
        
        if found_telemetry:
            return GateResult(name='telemetry', status=PASSED, method=method, detail='OpenTelemetry imports found', evidence=evidence, duration_s=duration)
        else:
            return GateResult(name='telemetry', status=FAILED, method=method, detail='No OpenTelemetry imports found in authored code', duration_s=duration)