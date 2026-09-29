import ast
import os
import re
import subprocess
import sys
import tempfile
import shutil
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency contract: hae/evaluation/artifacts.py
# Since it is being implemented in parallel, we assume it exists or we implement a fallback.
# The prompt says "Dependency contract: hae/evaluation/artifacts.py (being implemented in parallel by another department)"
# and provides the API. We should try to import it. If it fails, we might need a fallback, 
# but usually in these tasks, the dependency is available or we should implement the logic if it's small.
# However, the prompt asks to implement `hae/evaluation/harness.py`. 
# Let's try to import from artifacts. If it's not there, we might fail.
# But wait, the prompt says "Current files in your workspace: (Empty workspace)".
# This implies I might need to create artifacts.py too? 
# "Implement the module `hae/evaluation/harness.py`."
# "Dependency contract: hae/evaluation/artifacts.py ... Canonical definition..."
# Usually, if a dependency is specified with a full API contract, it might be expected to be present or I should implement it.
# Given "Empty workspace", I will implement `hae/evaluation/artifacts.py` as well to ensure the harness works.

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
    # Fallback implementation if artifacts.py is not yet available in the environment
    # This ensures the harness is self-contained if the other department hasn't pushed yet.
    
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
        # Remove backticks and asterisks
        clean = path.replace('`', '').replace('*', '')
        # Strip whitespace
        clean = clean.strip()
        # Remove leading hyphens? The spec says "leading hyphens" make it malformed, 
        # but sanitize is "Applied at write time so the artifact lands at its intended location".
        # Usually sanitize tries to fix. If it starts with -, maybe strip it?
        # The spec for is_malformed says leading hyphen is malformed.
        # Let's assume sanitize removes the offending characters.
        if clean.startswith('-'):
            clean = clean[1:]
        if clean.endswith('.') or clean.endswith('~'):
            clean = clean[:-1]
        return clean

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
        lines.append(f"Score Penalty: {self.score_penalty}")
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
    
    # Regex to find fenced code blocks
    # Matches ```lang\ncontent\n```
    # We want to extract the content and try to infer a path if possible, 
    # or just use a generic name if not.
    # The spec says "path->content map". 
    # Often agents write:
    # ```python
    # # path: src/foo.py
    # ...
    # ```
    # Or just:
    # ```python
    # ...
    # ```
    # If no path is provided, we might need to generate one or skip.
    # However, the prompt says "Recovers a path->content map".
    # Let's look for common patterns.
    
    blocks = {}
    # Pattern: ```(python|py|...)?\n(.*?)\n```
    # We need to handle nested backticks? Usually not in simple markdown.
    
    # Split by ```
    parts = text.split('```')
    
    # parts[0] is prose before first block
    # parts[1] is code block 1 (with optional lang header on first line)
    # parts[2] is prose between blocks
    # parts[3] is code block 2
    # ...
    
    for i in range(1, len(parts), 2):
        block_content = parts[i]
        if not block_content:
            continue
            
        lines = block_content.split('\n')
        if not lines:
            continue
            
        # First line might be language identifier
        lang_line = lines[0].strip()
        code_lines = lines[1:]
        
        # If the first line looks like a language (e.g. python, py, bash), remove it
        # If it looks like a path comment, keep it?
        # Let's assume standard markdown: ```python\n...
        # If lang_line is empty, it might be just ```\n...
        
        # Try to detect path from comments in the code
        # Common patterns:
        # # path: foo/bar.py
        # # File: foo/bar.py
        # // path: foo/bar.py
        
        path = None
        for line in code_lines:
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('//'):
                comment = stripped.lstrip('#/ ').strip()
                # Check for path indicators
                if comment.lower().startswith('path:'):
                    path = comment[5:].strip()
                    break
                elif comment.lower().startswith('file:'):
                    path = comment[5:].strip()
                    break
                elif comment.lower().startswith('filename:'):
                    path = comment[9:].strip()
                    break
        
        if not path:
            # If no path found, we can't really map it to a path.
            # Maybe use the language as a hint? Or skip?
            # The spec says "path->content map". If we can't determine path, we might skip.
            # But if we skip, we might miss code.
            # Let's try to infer from the language if it's a single file?
            # Or just use a placeholder?
            # "A last resort... Only the contents of fenced blocks are returned"
            # If we can't determine path, we can't put it in a bundle that expects paths.
            # Let's assume if no path is found, we skip this block or use a generic name.
            # Using a generic name might cause collisions.
            # Let's try to use the first line of code as a hint? No.
            # Let's just skip blocks without identifiable paths to avoid garbage.
            continue
            
        # Clean up path
        path = sanitize_path(path)
        if not path:
            continue
            
        content = '\n'.join(code_lines)
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
                # Some workspaces might have list_files
                try:
                    files = workspace.list_files()
                    # If list_files returns paths, we need to read them?
                    # Or maybe it returns a dict?
                    # Let's assume if it's not a dict, we try to read.
                    if isinstance(files, dict):
                        bundle = files
                    else:
                        # Try to read each file
                        for f in files:
                            if hasattr(workspace, 'read_file'):
                                bundle[f] = workspace.read_file(f)
                    source = 'workspace'
                except Exception:
                    pass
        
        # If bundle is empty, try deliverable text
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to remove generated/malformed
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files = len(authored_bundle)
        python_files = count_source_files(authored_bundle, suffixes=('.py',))
        
        if authored_files == 0:
            # No files to verify
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

        # Create temp dir
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files
            for path, content in authored_bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            return self.verify_directory(tmpdir, authored=authored_bundle, source=source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if authored is None:
            # Scan directory
            authored = {}
            for root, dirs, files in os.walk(workdir):
                # Filter out excluded dirs
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
        
        authored_files = len(authored)
        python_files = count_source_files(authored, suffixes=('.py',))
        
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._check_syntax(workdir, authored))
        
        # 2. Build Gate
        gates.append(self._check_build(workdir, authored))
        
        # 3. Smoke Gate
        gates.append(self._check_smoke(workdir, authored))
        
        # 4. Tests Gate
        gates.append(self._check_tests(workdir, authored))
        
        # 5. Telemetry Gate
        gates.append(self._check_telemetry(workdir, authored))
        
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

    def _check_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'ast.parse'
        errors = []
        checked = 0
        
        for path, content in authored.items():
            if not path.endswith('.py'):
                continue
            checked += 1
            try:
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
        
        duration = time.time() - start
        
        if checked == 0:
            return GateResult(name='syntax', status=SKIPPED, method=method, detail='No Python files found', duration_s=duration)
        
        if errors:
            return GateResult(
                name='syntax', 
                status=FAILED, 
                method=method, 
                detail=f"Syntax errors in {len(errors)} files: {errors[0]}", 
                duration_s=duration,
                evidence={'errors': errors}
            )
        
        return GateResult(
            name='syntax', 
            status=PASSED, 
            method=method, 
            detail=f"All {checked} Python files parsed successfully", 
            duration_s=duration
        )

    def _check_build(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'pip install -e .'
        
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            return GateResult(
                name='build', 
                status=SKIPPED, 
                method=method, 
                detail='No setup.py or pyproject.toml found', 
                duration_s=time.time() - start
            )
        
        try:
            # Run pip install -e .
            # We use a timeout
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
                    method=method, 
                    detail='Package installed successfully', 
                    duration_s=duration
                )
            else:
                return GateResult(
                    name='build', 
                    status=FAILED, 
                    method=method, 
                    detail=f"Build failed: {result.stderr[:500]}", 
                    duration_s=duration,
                    evidence={'stderr': result.stderr, 'stdout': result.stdout}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build', 
                status=FAILED, 
                method=method, 
                detail=f"Build timed out after {self.timeout_s}s", 
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='build', 
                status=FAILED, 
                method=method, 
                detail=f"Build error: {str(e)}", 
                duration_s=time.time() - start
            )

    def _check_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'import'
        
        # Find modules to import
        # We look for .py files that are not tests and not __init__.py (though __init__ is imported when package is imported)
        # Strategy: Try to import the top-level package if it exists, or individual modules.
        
        # Identify potential packages/modules
        # If there's a setup.py, we might know the package name.
        # Otherwise, we look for directories with __init__.py
        
        modules_to_import = []
        
        # Simple heuristic: find all .py files that are not tests
        for path in authored:
            if path.endswith('.py') and 'test' not in path.lower():
                # Convert path to module name
                # e.g. src/foo/bar.py -> src.foo.bar
                # But we need to be careful with sys.path
                modules_to_import.append(path)
        
        if not modules_to_import:
            return GateResult(
                name='smoke', 
                status=SKIPPED, 
                method=method, 
                detail='No importable modules found', 
                duration_s=time.time() - start
            )
        
        # We need to run imports in a subprocess to avoid polluting the current process
        # and to handle timeouts properly.
        
        import_script = """
import sys
import os
import importlib

# Add workdir to path
sys.path.insert(0, os.getcwd())

modules = {}
errors = []

for path in sys.argv[1:]:
    # Convert path to module name
    # Remove .py
    mod_name = path.replace('.py', '').replace('/', '.').replace('\\\\', '.')
    # Remove leading dot if any
    if mod_name.startswith('.'):
        mod_name = mod_name[1:]
    
    try:
        importlib.import_module(mod_name)
        modules[path] = 'ok'
    except Exception as e:
        errors.append(f"{path}: {str(e)}")

print("IMPORTS_OK")
for e in errors:
    print(f"ERROR: {e}")
"""
        
        script_path = os.path.join(workdir, '_smoke_test_script.py')
        with open(script_path, 'w') as f:
            f.write(import_script)
        
        try:
            # Pass module paths as arguments
            args = [self.python_executable, script_path] + modules_to_import
            result = subprocess.run(
                args,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            duration = time.time() - start
            
            # Clean up script
            try:
                os.remove(script_path)
            except:
                pass
            
            if result.returncode != 0:
                return GateResult(
                    name='smoke', 
                    status=FAILED, 
                    method=method, 
                    detail=f"Import process failed: {result.stderr[:500]}", 
                    duration_s=duration
                )
            
            output = result.stdout
            if "IMPORTS_OK" not in output:
                return GateResult(
                    name='smoke', 
                    status=FAILED, 
                    method=method, 
                    detail="Import check did not complete", 
                    duration_s=duration
                )
            
            errors = [line for line in output.split('\n') if line.startswith('ERROR:')]
            if errors:
                return GateResult(
                    name='smoke', 
                    status=FAILED, 
                    method=method, 
                    detail=f"Import errors: {errors[0]}", 
                    duration_s=duration,
                    evidence={'errors': errors}
                )
            
            return GateResult(
                name='smoke', 
                status=PASSED, 
                method=method, 
                detail=f"All {len(modules_to_import)} modules imported successfully", 
                duration_s=duration
            )
            
        except subprocess.TimeoutExpired:
            return GateResult(
                name='smoke', 
                status=FAILED, 
                method=method, 
                detail=f"Import timed out after {self.timeout_s}s", 
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='smoke', 
                status=FAILED, 
                method=method, 
                detail=f"Smoke check error: {str(e)}", 
                duration_s=time.time() - start
            )

    def _check_tests(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'pytest'
        
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], capture_output=True, timeout=10)
        except Exception:
            return GateResult(
                name='tests', 
                status=SKIPPED, 
                method=method, 
                detail='pytest not available', 
                duration_s=time.time() - start
            )
        
        # Check if there are test files
        test_files = [p for p in authored if 'test' in p.lower() and p.endswith('.py')]
        if not test_files:
            return GateResult(
                name='tests', 
                status=SKIPPED, 
                method=method, 
                detail='No test files found', 
                duration_s=time.time() - start
            )
        
        try:
            result = subprocess.run(
                [self.python_executable, '-m', 'pytest', '-q', '--tb=short'],
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
                    method=method, 
                    detail='All tests passed', 
                    duration_s=duration
                )
            else:
                # Check if it was a collection error or actual failure
                # If no tests were collected, it might be skipped?
                # But if tests exist and fail, it's failed.
                return GateResult(
                    name='tests', 
                    status=FAILED, 
                    method=method, 
                    detail=f"Tests failed: {result.stdout[-500:]}", 
                    duration_s=duration,
                    evidence={'stdout': result.stdout, 'stderr': result.stderr}
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='tests', 
                status=FAILED, 
                method=method, 
                detail=f"Tests timed out after {self.timeout_s}s", 
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='tests', 
                status=FAILED, 
                method=method, 
                detail=f"Test execution error: {str(e)}", 
                duration_s=time.time() - start
            )

    def _check_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'ast.parse'
        
        # Check if any Python file imports OpenTelemetry
        # We look for `import opentelemetry` or `from opentelemetry import ...`
        
        found = False
        checked = 0
        
        for path, content in authored.items():
            if not path.endswith('.py'):
                continue
            checked += 1
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
                if found:
                    break
            except SyntaxError:
                # If syntax error, we already failed syntax gate, but we can still check text?
                # The spec says "OpenTelemetry must be imported by authored code, not merely mentioned in prose."
                # If it doesn't parse, it can't be imported. So it's not found.
                pass
        
        duration = time.time() - start
        
        if checked == 0:
            return GateResult(
                name='telemetry', 
                status=SKIPPED, 
                method=method, 
                detail='No Python files to check', 
                duration_s=duration
            )
        
        if found:
            return GateResult(
                name='telemetry', 
                status=PASSED, 
                method=method, 
                detail='OpenTelemetry import found', 
                duration_s=duration
            )
        else:
            return GateResult(
                name='telemetry', 
                status=FAILED, 
                method=method, 
                detail='No OpenTelemetry import found in authored code', 
                duration_s=duration
            )