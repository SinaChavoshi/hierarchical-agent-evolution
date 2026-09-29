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
import importlib
import importlib.util
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Iterable

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency contract: hae/evaluation/artifacts.py
# We implement the required functions locally to ensure self-containment 
# if the external module is not yet available or to match the spec exactly.

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
    
    # Check for markdown decoration or invalid chars
    if '*' in path or '`' in path:
        return True
    
    # Check for leading/trailing whitespace
    if path != path.strip():
        return True
        
    # Check for leading hyphen
    if path.startswith('-'):
        return True
        
    # Check for trailing dots or tildes
    if path.endswith('.') or path.endswith('~'):
        return True
        
    return False

def sanitize_path(path: str) -> str:
    """Strips markdown decoration from an agent-supplied path."""
    # Remove leading/trailing whitespace
    path = path.strip()
    # Remove markdown bold/italic markers
    path = path.replace('*', '')
    # Remove backticks
    path = path.replace('`', '')
    # Remove leading hyphens if they look like markdown list items? 
    # Spec says "leading hyphens" are malformed, but sanitize should fix it.
    # Usually leading hyphens in paths are errors. We strip them if they are at the start.
    if path.startswith('-'):
        path = path[1:]
    # Remove trailing dots or tildes
    while path.endswith('.') or path.endswith('~'):
        path = path[:-1]
        
    return path

def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
    """Returns only genuinely agent-authored files from a path->content map."""
    filtered = {}
    for path, content in bundle.items():
        if not is_generated_path(path) and not is_malformed_path(path):
            filtered[path] = content
    return filtered

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
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"[{status_icon}] {g.name:10} : {g.status:8} ({g.method})")
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
    
    # Regex to find fenced code blocks with optional language and optional filename
    # Pattern: ```[language] [filename]\n...content...\n```
    # We look for blocks that might have a filename hint.
    # Common patterns:
    # ```python
    # path/to/file.py
    # ...
    # ```
    # OR
    # ```python path/to/file.py
    # ...
    # ```
    
    blocks = {}
    # Match ``` followed by optional lang, optional space, optional filename, then newline
    # The filename heuristic is tricky. We'll try to detect if the first line of the block looks like a path.
    
    pattern = re.compile(r'```(\w+)?\s*([^\n]*)\n(.*?)\n```', re.DOTALL)
    
    for match in pattern.finditer(text):
        lang = match.group(1)
        header = match.group(2).strip()
        content = match.group(3)
        
        # Determine path
        path = None
        if header:
            # If header looks like a path (contains / or .py etc), use it
            if '/' in header or header.endswith('.py') or header.endswith('.txt'):
                path = header
            else:
                # Maybe the first line of content is the path?
                lines = content.split('\n')
                if lines and ('/' in lines[0] or lines[0].endswith('.py')):
                    path = lines[0].strip()
                    # Remove the path line from content if it was just a header
                    # But usually code blocks don't have the path as the first line of code unless it's a comment
                    # Let's assume if header is empty, we might need to guess or skip.
                    # For safety, if we can't determine a path, we might skip or use a generic name.
                    # However, the spec says "path->content map".
                    # If no path is found, we can't really map it.
                    pass
        
        if path:
            # Sanitize the path
            clean_path = sanitize_path(path)
            if clean_path and not is_malformed_path(clean_path):
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
            # Assume workspace has a method to get files or is a dict-like
            if hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
            elif isinstance(workspace, dict):
                bundle = workspace
            else:
                # Try to iterate if it's a list of paths?
                # For now, if we can't extract, we fall back
                pass
        
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        if not bundle:
            # Empty workspace
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No files to check'),
            GateResult(name='build', status=SKIPPED, method='pip install', detail='No files to build'),
            GateResult(name='smoke', status=SKIPPED, method='import', detail='No files to import'),
            GateResult(name='tests', status=SKIPPED, method='pytest', detail='No files to test'),
            GateResult(name='telemetry', status=SKIPPED, method='ast.scan', detail='No files to scan'),
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
        authored_files_count = len(authored_bundle)
        python_files_count = count_source_files(authored_bundle, suffixes=('.py',))
        
        if authored_files_count == 0:
            return self._empty_report(source)
            
        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to temp dir
            for path, content in authored_bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    
            # Run gates
            gates = []
            
            # 1. Syntax Gate
            gates.append(self._gate_syntax(tmpdir, authored_bundle))
            
            # 2. Build Gate
            gates.append(self._gate_build(tmpdir, authored_bundle))
            
            # 3. Smoke Gate
            gates.append(self._gate_smoke(tmpdir, authored_bundle))
            
            # 4. Tests Gate
            gates.append(self._gate_tests(tmpdir, authored_bundle))
            
            # 5. Telemetry Gate
            gates.append(self._gate_telemetry(tmpdir, authored_bundle))
            
            # Calculate penalty
            # Penalty is applied for FAILED gates. SKIPPED gates are excluded from denominator.
            # The spec says: "SKIPPED gates are excluded from the fitness denominator rather than scored as failures."
            # And "score_penalty" is likely the total penalty.
            # If a gate fails, we add penalty_per_gate.
            
            failed_count = sum(1 for g in gates if g.status == FAILED)
            score_penalty = failed_count * self.penalty_per_gate
            
            return VerificationReport(
                gates=gates,
                score_penalty=score_penalty,
                authored_files=authored_files_count,
                python_files=python_files_count,
                source=source
            )

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        # If authored map is provided, use it for filtering logic, but verify on disk?
        # The spec says "Verifies an existing workspace directory".
        # We should probably scan the directory for files.
        
        bundle = {}
        if authored:
            bundle = authored
        else:
            # Scan directory
            for root, dirs, files in os.walk(workdir):
                # Skip excluded dirs
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIR_SEGMENTS and not any(d.endswith(s) for s in EXCLUDED_DIR_SUFFIXES)]
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, workdir)
                    if not is_generated_path(rel_path) and not is_malformed_path(rel_path):
                        try:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                bundle[rel_path] = f.read()
                        except Exception:
                            pass # Skip binary or unreadable files
        
        if not bundle:
            return self._empty_report(source)
            
        # We can reuse verify_bundle logic but pointing to the existing dir?
        # verify_bundle writes to a temp dir. verify_directory should probably verify in place or copy?
        # To be safe and consistent, let's just use verify_bundle on the scanned content.
        # But verify_bundle creates a NEW temp dir. This is fine for isolation.
        return self.verify_bundle(bundle, source=source)

    def _gate_syntax(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'ast.parse'
        py_files = [p for p in bundle if p.endswith('.py')]
        
        if not py_files:
            return GateResult(name='syntax', status=SKIPPED, method=method, detail='No Python files found', duration_s=time.time() - start_time)
            
        errors = []
        for path in py_files:
            full_path = os.path.join(workdir, path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                ast.parse(source)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
            except Exception as e:
                errors.append(f"{path}: {e}")
                
        duration = time.time() - start_time
        if errors:
            return GateResult(name='syntax', status=FAILED, method=method, detail='; '.join(errors), duration_s=duration)
        else:
            return GateResult(name='syntax', status=PASSED, method=method, detail=f'{len(py_files)} files parsed', duration_s=duration)

    def _gate_build(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'pip install -e .'
        
        # Check if setup.py or pyproject.toml exists
        has_setup = 'setup.py' in bundle or 'pyproject.toml' in bundle
        
        if not has_setup:
            # Try to infer if it's a package? 
            # If no build config, we can't "build" in the pip sense.
            # Should this be SKIPPED or FAILED?
            # "A gate that could not be evaluated returns SKIPPED"
            # If there's no build system, we can't evaluate the build gate.
            return GateResult(name='build', status=SKIPPED, method=method, detail='No setup.py or pyproject.toml found', duration_s=time.time() - start_time)
            
        # Run pip install
        try:
            # Use the python executable
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps']
            # We might need to handle dependencies? The spec says "The package must actually install".
            # Usually -e . installs the package. --no-deps might be safer to avoid network issues in sandbox, 
            # but if deps are missing, import might fail later. 
            # Let's try without --no-deps first? No, network might be blocked.
            # Let's stick to --no-deps to test the package structure itself.
            
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start_time
            
            if result.returncode == 0:
                return GateResult(name='build', status=PASSED, method=method, detail='Installed successfully', duration_s=duration)
            else:
                return GateResult(name='build', status=FAILED, method=method, detail=f'Exit code {result.returncode}: {result.stderr}', duration_s=duration)
                
        except subprocess.TimeoutExpired:
            return GateResult(name='build', status=FAILED, method=method, detail='Timeout', duration_s=time.time() - start_time)
        except Exception as e:
            return GateResult(name='build', status=FAILED, method=method, detail=str(e), duration_s=time.time() - start_time)

    def _gate_smoke(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'import'
        
        # Find modules to import
        # We look for .py files that are not tests and not __init__.py (though __init__ is imported when package is imported)
        # Strategy: Try to import the top-level package if it exists, or individual modules.
        
        # Identify potential package roots
        # If there's a src/ or a top-level dir with __init__.py
        
        # Simple approach: Try to import every .py file as a module? 
        # No, that's messy. 
        # Let's try to find the main package.
        
        # If setup.py exists, we might know the package name.
        # Otherwise, look for directories with __init__.py
        
        package_dirs = set()
        for path in bundle:
            if path.endswith('__init__.py'):
                # The directory containing this is a package
                parts = path.split('/')
                if len(parts) > 1:
                    package_dirs.add('/'.join(parts[:-1]))
                else:
                    package_dirs.add('.')
                    
        if not package_dirs:
            # No packages found. Maybe just scripts?
            # If no packages, we can't really "smoke test" imports in a structured way.
            # But we can try to import individual files?
            # Let's try to import any .py file that isn't a test.
            non_test_py = [p for p in bundle if p.endswith('.py') and 'test' not in p.lower()]
            if not non_test_py:
                return GateResult(name='smoke', status=SKIPPED, method=method, detail='No importable modules found', duration_s=time.time() - start_time)
            
            # Try importing them as top-level modules?
            # This is risky. Let's return SKIPPED if no clear package structure.
            return GateResult(name='smoke', status=SKIPPED, method=method, detail='No package structure detected', duration_s=time.time() - start_time)
            
        # Try to import the packages
        # We need to add workdir to sys.path
        
        # FIX: Do not mutate sys.path. Use subprocess to import in isolation.
        # This prevents the harness from accidentally importing the reference implementation
        # or polluting the global state.
        
        errors = []
        imported_count = 0
        
        # We'll run a small python script in a subprocess that adds the workdir to path and imports the modules
        # This is safer than mutating sys.path in the current process.
        
        # Construct the list of modules to import
        modules_to_import = []
        for pkg_dir in package_dirs:
            if pkg_dir == '.':
                continue # Root package?
            
            # Convert path to module name
            # e.g. src/mypackage -> src.mypackage? Or just mypackage if src is in path?
            # If we added workdir to path, and pkg_dir is 'src/mypackage', 
            # we need to import 'src.mypackage' if src is a package too.
            
            # Let's check if parent dirs are packages
            parts = pkg_dir.split('/')
            module_name = '.'.join(parts)
            modules_to_import.append(module_name)
            
        if not modules_to_import:
             return GateResult(name='smoke', status=SKIPPED, method=method, detail='No packages imported', duration_s=time.time() - start_time)

        # Create a temporary script to run imports
        script_content = f"""
import sys
import os
sys.path.insert(0, r'{workdir}')
import importlib
errors = []
imported = 0
for mod in {modules_to_import!r}:
    try:
        importlib.import_module(mod)
        imported += 1
    except Exception as e:
        errors.append(f"{{mod}}: {{e}}")
print(f"IMPORTED:{{imported}}")
if errors:
    print("ERRORS:")
    for e in errors:
        print(e)
"""
        
        try:
            # Write script to temp file
            script_path = os.path.join(workdir, '_smoke_test_script.py')
            with open(script_path, 'w') as f:
                f.write(script_content)
            
            # Run script
            result = subprocess.run(
                [self.python_executable, script_path],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start_time
            
            # Clean up script
            try:
                os.remove(script_path)
            except:
                pass
                
            if result.returncode != 0:
                return GateResult(name='smoke', status=FAILED, method=method, detail=f'Script failed: {result.stderr}', duration_s=duration)
            
            stdout = result.stdout
            imported_count = 0
            errors = []
            
            for line in stdout.splitlines():
                if line.startswith("IMPORTED:"):
                    imported_count = int(line.split(":")[1])
                elif line == "ERRORS:":
                    continue
                elif line:
                    errors.append(line)
                    
            if errors:
                return GateResult(name='smoke', status=FAILED, method=method, detail='; '.join(errors), duration_s=duration)
            elif imported_count > 0:
                return GateResult(name='smoke', status=PASSED, method=method, detail=f'{imported_count} packages imported', duration_s=duration)
            else:
                return GateResult(name='smoke', status=SKIPPED, method=method, detail='No packages imported', duration_s=duration)
                
        except Exception as e:
            return GateResult(name='smoke', status=FAILED, method=method, detail=str(e), duration_s=time.time() - start_time)

    def _gate_tests(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'pytest'
        
        # Check if pytest is available
        try:
            import pytest
        except ImportError:
            return GateResult(name='tests', status=SKIPPED, method=method, detail='pytest not installed', duration_s=time.time() - start_time)
            
        # Find test files
        test_files = [p for p in bundle if p.endswith('.py') and ('test_' in p or '_test.py' in p or 'tests/' in p)]
        
        if not test_files:
            return GateResult(name='tests', status=SKIPPED, method=method, detail='No test files found', duration_s=time.time() - start_time)
            
        # Run pytest
        try:
            # We run pytest in the workdir
            # --tb=short for concise output
            cmd = [self.python_executable, '-m', 'pytest', '-v', '--tb=short', '-q']
            
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start_time
            
            if result.returncode == 0:
                return GateResult(name='tests', status=PASSED, method=method, detail='All tests passed', duration_s=duration)
            else:
                # Extract some error info
                stderr = result.stderr
                stdout = result.stdout
                detail = f'Exit code {result.returncode}'
                if stderr:
                    detail += f': {stderr[:500]}'
                elif stdout:
                    detail += f': {stdout[:500]}'
                return GateResult(name='tests', status=FAILED, method=method, detail=detail, duration_s=duration)
                
        except subprocess.TimeoutExpired:
            return GateResult(name='tests', status=FAILED, method=method, detail='Timeout', duration_s=time.time() - start_time)
        except Exception as e:
            return GateResult(name='tests', status=FAILED, method=method, detail=str(e), duration_s=time.time() - start_time)

    def _gate_telemetry(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start_time = time.time()
        method = 'ast.scan'
        
        # Check if OpenTelemetry is imported in any authored .py file
        # We look for `import opentelemetry` or `from opentelemetry ...`
        
        py_files = [p for p in bundle if p.endswith('.py')]
        if not py_files:
            return GateResult(name='telemetry', status=SKIPPED, method=method, detail='No Python files found', duration_s=time.time() - start_time)
            
        found = False
        evidence = {}
        
        for path in py_files:
            full_path = os.path.join(workdir, path)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                tree = ast.parse(source)
                
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
                            
            except Exception:
                continue
                
        duration = time.time() - start_time
        
        if found:
            return GateResult(name='telemetry', status=PASSED, method=method, detail='OpenTelemetry imports found', evidence=evidence, duration_s=duration)
        else:
            return GateResult(name='telemetry', status=FAILED, method=method, detail='No OpenTelemetry imports found in authored code', duration_s=duration)