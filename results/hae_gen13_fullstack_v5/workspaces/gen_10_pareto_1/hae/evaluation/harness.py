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

from hae.evaluation.artifacts import (
    filter_bundle,
    partition_bundle,
    count_source_files,
    is_generated_path,
    is_malformed_path,
)

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
            "",
            "Gates:",
        ]
        for g in self.gates:
            status_icon = "✅" if g.passed else ("⏭️" if g.status == SKIPPED else "❌")
            lines.append(f"  {status_icon} {g.name}: {g.status} ({g.method})")
            if g.detail:
                lines.append(f"     Detail: {g.detail}")
        
        lines.append("")
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

    blocks = {}
    
    # Find all fenced blocks
    # Pattern: ```[language]\n(content)\n```
    # We look for blocks that might contain file paths in the language identifier
    # or just standard python blocks.
    # Common pattern in agent outputs: ```python: path/to/file.py
    # Or just ```python
    
    pattern = r"```(?:([^\n]*)\n)?(.*?)```"
    matches = re.finditer(pattern, text, re.DOTALL)
    
    for match in matches:
        lang_line = match.group(1)
        content = match.group(2)
        
        if not content:
            continue
            
        # Try to extract path from the language line
        # Formats: "python", "python: path/to/file.py", "path/to/file.py"
        path = None
        if lang_line:
            lang_line = lang_line.strip()
            if ':' in lang_line:
                parts = lang_line.split(':', 1)
                # If it looks like a path (contains / or .py), use it
                if '/' in parts[1] or parts[1].endswith('.py'):
                    path = parts[1].strip()
                elif '/' in parts[0] or parts[0].endswith('.py'):
                    path = parts[0].strip()
            elif lang_line.endswith('.py') or '/' in lang_line:
                path = lang_line.strip()
        
        # If no path found, we can't reliably map it to a file structure
        # unless we assume a default name, but the spec implies recovering a map.
        # If we can't determine a path, we skip it or assign a generic one?
        # The spec says "Recovers a path->content map". If path is unknown, 
        # we might skip it or use a placeholder. 
        # However, usually agents put the path in the fence.
        if path:
            # Clean up path
            path = path.strip()
            if path.startswith('./'):
                path = path[2:]
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
        
        # Check if workspace is a directory path
        if isinstance(workspace, str):
            if os.path.isdir(workspace):
                return self.verify_directory(workspace, source='directory')
            elif os.path.isfile(workspace):
                # Treat as a single file? Unlikely for "workspace". 
                # Fall through to deliverable text if provided, or error.
                pass
        
        # Check if workspace is a dict (bundle)
        if isinstance(workspace, dict):
            bundle = workspace
            source = 'bundle'
        elif workspace is not None:
            # Try to extract files from workspace object if it has a method
            # Common pattern: workspace.files or workspace.get_files()
            if hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace'
            elif hasattr(workspace, 'get_files'):
                bundle = workspace.get_files()
                source = 'workspace'
            else:
                # Fallback to deliverable text
                bundle = extract_code_blocks(deliverable_text)
                source = 'deliverable'
        else:
            # No workspace, use deliverable text
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'

        if not bundle:
            # If still empty, return a report with all skipped/failed?
            # Or just verify an empty bundle.
            pass

        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter out generated/malformed files
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files_count = len(authored_bundle)
        python_files_count = count_source_files(authored_bundle, suffixes=('.py',))
        
        if not authored_bundle:
            # No authored files. All gates skipped? Or failed?
            # If no files, syntax/build/smoke/tests/telemetry cannot be evaluated.
            # Spec: "A gate that could not be evaluated returns SKIPPED"
            gates = [
                GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No authored files'),
                GateResult(name='build', status=SKIPPED, method='pip install -e .', detail='No authored files'),
                GateResult(name='smoke', status=SKIPPED, method='import', detail='No authored files'),
                GateResult(name='tests', status=SKIPPED, method='pytest', detail='No authored files'),
                GateResult(name='telemetry', status=SKIPPED, method='ast.parse', detail='No authored files'),
            ]
            return VerificationReport(
                gates=gates,
                score_penalty=0.0, # No gates evaluated, so no penalty? Or max penalty?
                # Spec: "SKIPPED gates are excluded from the fitness denominator"
                # So penalty is likely 0 if nothing evaluated.
                authored_files=authored_files_count,
                python_files=python_files_count,
                source=source,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to temp dir
            for path, content in authored_bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            return self.verify_directory(tmpdir, authored=authored_bundle, source=source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if authored is None:
            # Scan directory for authored files
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
                                content = f.read()
                            authored[rel_path] = content
                        except Exception:
                            pass
        
        authored_files_count = len(authored)
        python_files_count = count_source_files(authored, suffixes=('.py',))
        
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
        # Penalty is applied for FAILED gates. SKIPPED are excluded.
        failed_count = sum(1 for g in gates if g.status == FAILED)
        score_penalty = failed_count * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files_count,
            python_files=python_files_count,
            source=source,
        )

    def _check_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'ast.parse'
        errors = []
        
        py_files = [p for p in authored if p.endswith('.py')]
        
        if not py_files:
            return GateResult(
                name='syntax',
                status=SKIPPED,
                method=method,
                detail='No Python files to parse',
                duration_s=time.time() - start,
            )
        
        for path in py_files:
            content = authored[path]
            try:
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
        
        duration = time.time() - start
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method=method,
                detail='; '.join(errors),
                duration_s=duration,
                evidence={'errors': errors},
            )
        else:
            return GateResult(
                name='syntax',
                status=PASSED,
                method=method,
                detail=f"Successfully parsed {len(py_files)} Python files",
                duration_s=duration,
                evidence={'file_count': len(py_files)},
            )

    def _check_build(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'pip install -e .'
        
        # Check if setup.py or pyproject.toml exists
        has_setup = 'setup.py' in authored or 'pyproject.toml' in authored
        if not has_setup:
            # Check if there's a package structure that might be installable?
            # Usually requires setup.py or pyproject.toml for -e .
            return GateResult(
                name='build',
                status=SKIPPED,
                method=method,
                detail='No setup.py or pyproject.toml found',
                duration_s=time.time() - start,
            )
        
        try:
            # Run pip install -e . in the workdir
            # We use a temporary venv or just the current env? 
            # Spec says "The package must actually install".
            # Using current env might pollute. But for verification, we just need to know it *can* install.
            # Let's try installing into a temporary target dir to avoid polluting global site-packages?
            # Or just run it and see if it succeeds.
            # pip install -e . --target <tmp> might be safer but slower.
            # Let's stick to standard `pip install -e .` but maybe with `--no-deps` to speed up?
            # No, dependencies matter for build success.
            
            # To avoid polluting the main environment, we can use a temporary virtual environment
            # or install to a temporary directory.
            # However, creating a venv is slow.
            # Let's try installing to a temporary directory using --target
            
            with tempfile.TemporaryDirectory() as target_dir:
                cmd = [
                    self.python_executable, '-m', 'pip', 'install', '-e', '.',
                    '--target', target_dir,
                    '--no-deps', # Skip deps for speed? If deps fail, build fails. 
                                # But if deps are missing in env, it might fail.
                                # Let's try without --no-deps first, but with timeout.
                ]
                # Actually, --target with -e is tricky. 
                # Better: pip install . (not -e) to a target? 
                # Spec says `pip install -e .`.
                # Let's try standard install but in a subprocess with timeout.
                
                # To prevent global pollution, we can use a temporary venv.
                # But that's heavy.
                # Alternative: Check if it *would* install by parsing setup.py? No, spec says "actually install".
                
                # Let's try installing to a temp dir using --prefix or --target.
                # pip install -e . --target <dir> works for pure python packages.
                
                cmd = [
                    self.python_executable, '-m', 'pip', 'install', '-e', '.',
                    '--target', target_dir,
                    '--no-cache-dir',
                ]
                
                result = subprocess.run(
                    cmd,
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                )
                
                duration = time.time() - start
                
                if result.returncode == 0:
                    return GateResult(
                        name='build',
                        status=PASSED,
                        method=method,
                        detail='Package installed successfully',
                        duration_s=duration,
                        evidence={'stdout': result.stdout[-500:]},
                    )
                else:
                    return GateResult(
                        name='build',
                        status=FAILED,
                        method=method,
                        detail=f"Build failed: {result.stderr[-500:]}",
                        duration_s=duration,
                        evidence={'stderr': result.stderr[-500:], 'returncode': result.returncode},
                    )
                    
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method=method,
                detail='Build timed out',
                duration_s=time.time() - start,
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method=method,
                detail=f"Build error: {str(e)}",
                duration_s=time.time() - start,
            )

    def _check_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'import'
        
        # Find top-level packages/modules
        # We need to import the main package.
        # How to determine the package name?
        # Look for __init__.py in top-level dirs?
        # Or look for setup.py to find package name?
        
        # Simple heuristic: Try to import any top-level .py file or package dir
        # that isn't a test or script.
        
        # Let's try to import the package defined in setup.py if possible,
        # or just try importing all top-level modules.
        
        # Collect top-level modules/packages
        top_level_modules = set()
        for path in authored:
            if path.endswith('.py'):
                parts = path.split('/')
                if len(parts) == 1:
                    # Top level script
                    module_name = parts[0][:-3]
                    if module_name != '__init__':
                        top_level_modules.add(module_name)
                elif len(parts) > 1:
                    # Inside a package
                    pkg_name = parts[0]
                    if pkg_name not in ('tests', 'test', 'docs', 'scripts'):
                        top_level_modules.add(pkg_name)
        
        if not top_level_modules:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method=method,
                detail='No importable modules found',
                duration_s=time.time() - start,
            )
        
        # Try to import them
        # We need to add workdir to sys.path temporarily
        # CRITICAL FIX: Do not mutate sys.path globally. Use subprocess or isolated import.
        # Since we are in a temp dir, we can run a subprocess to import.
        
        imported = []
        failed_imports = []
        
        # Create a small script to import modules
        # We use a subprocess to avoid mutating sys.path in the current process
        import_script = f"""
import sys
import os
# Insert workdir at the beginning of sys.path for this subprocess only
sys.path.insert(0, r'{workdir}')
modules = {list(top_level_modules)!r}
imported = []
failed = []
for m in modules:
    try:
        __import__(m)
        imported.append(m)
    except Exception as e:
        failed.append(f"{{m}}: {{str(e)}}")
print("IMPORTED:" + ",".join(imported))
print("FAILED:" + ",".join(failed))
"""
        
        try:
            result = subprocess.run(
                [self.python_executable, '-c', import_script],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
            
            duration = time.time() - start
            
            stdout = result.stdout
            imported = []
            failed_imports = []
            
            for line in stdout.splitlines():
                if line.startswith("IMPORTED:"):
                    imported = line[len("IMPORTED:"):].split(',') if line[len("IMPORTED:"):] else []
                elif line.startswith("FAILED:"):
                    failed_imports = line[len("FAILED:"):].split(',') if line[len("FAILED:"):] else []
            
            if failed_imports:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method=method,
                    detail='; '.join(failed_imports),
                    duration_s=duration,
                    evidence={'failed': failed_imports, 'imported': imported},
                )
            else:
                return GateResult(
                    name='smoke',
                    status=PASSED,
                    method=method,
                    detail=f"Successfully imported {len(imported)} modules",
                    duration_s=duration,
                    evidence={'imported': imported},
                )
                
        except Exception as e:
            return GateResult(
                name='smoke',
                status=FAILED,
                method=method,
                detail=f"Smoke test execution error: {str(e)}",
                duration_s=time.time() - start,
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
                duration_s=time.time() - start,
            )
        
        # Check if there are test files
        test_files = [p for p in authored if p.startswith('test_') or p.endswith('_test.py') or '/tests/' in p or '/test/' in p]
        
        if not test_files:
            return GateResult(
                name='tests',
                status=SKIPPED,
                method=method,
                detail='No test files found',
                duration_s=time.time() - start,
            )
        
        try:
            # Run pytest
            # Use -q for quiet, --tb=short for short traceback
            cmd = [
                self.python_executable, '-m', 'pytest',
                '-q',
                '--tb=short',
                '--no-header',
                '--no-summary',
            ]
            
            result = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
            
            duration = time.time() - start
            
            if result.returncode == 0:
                return GateResult(
                    name='tests',
                    status=PASSED,
                    method=method,
                    detail='All tests passed',
                    duration_s=duration,
                    evidence={'stdout': result.stdout[-500:]},
                )
            else:
                # Check if it was a collection error or actual failure
                # Returncode 1 usually means tests failed.
                # Returncode 2 means usage error.
                # Returncode 5 means no tests collected.
                
                if result.returncode == 5:
                    return GateResult(
                        name='tests',
                        status=SKIPPED,
                        method=method,
                        detail='No tests collected',
                        duration_s=duration,
                        evidence={'stdout': result.stdout[-500:]},
                    )
                
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method=method,
                    detail=f"Tests failed: {result.stdout[-500:]}",
                    duration_s=duration,
                    evidence={'stdout': result.stdout[-500:], 'stderr': result.stderr[-500:], 'returncode': result.returncode},
                )
                
        except subprocess.TimeoutExpired:
            return GateResult(
                name='tests',
                status=FAILED,
                method=method,
                detail='Tests timed out',
                duration_s=time.time() - start,
            )
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method=method,
                detail=f"Test execution error: {str(e)}",
                duration_s=time.time() - start,
            )

    def _check_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        method = 'ast.parse'
        
        # Check if OpenTelemetry is imported in any authored Python file
        # We look for `import opentelemetry` or `from opentelemetry import ...`
        
        py_files = [p for p in authored if p.endswith('.py')]
        
        if not py_files:
            return GateResult(
                name='telemetry',
                status=SKIPPED,
                method=method,
                detail='No Python files to analyze',
                duration_s=time.time() - start,
            )
        
        found_telemetry = False
        evidence_files = []
        
        for path in py_files:
            content = authored[path]
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith('opentelemetry'):
                                found_telemetry = True
                                evidence_files.append(path)
                                break
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.module.startswith('opentelemetry'):
                            found_telemetry = True
                            evidence_files.append(path)
                            break
            except SyntaxError:
                # If syntax is broken, we might have already failed syntax gate.
                # But we can still check textually? No, spec says AST analysis.
                # If it doesn't parse, we can't AST analyze it.
                pass
        
        duration = time.time() - start
        
        if found_telemetry:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method=method,
                detail=f"OpenTelemetry imported in {len(evidence_files)} files",
                duration_s=duration,
                evidence={'files': evidence_files},
            )
        else:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method=method,
                detail='No OpenTelemetry imports found in authored code',
                duration_s=duration,
                evidence={'files_checked': len(py_files)},
            )