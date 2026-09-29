import ast
import os
import subprocess
import sys
import tempfile
import time
import re
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Dependency on artifacts module
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
    # Fallback implementations if artifacts.py is not yet available or import fails
    # These are minimal stubs to ensure harness.py can be tested in isolation if needed,
    # but in the real environment, artifacts.py should be present.
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
        passed_count = len(self.passed_gates)
        return passed_count / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source})"]
        lines.append(f"Authored Files: {self.authored_files} (Python: {self.python_files})")
        lines.append(f"Penalty: {self.score_penalty:.2f}")
        lines.append("Gates:")
        for g in self.gates:
            status_icon = "✓" if g.passed else ("✗" if g.status == FAILED else "-")
            lines.append(f"  [{status_icon}] {g.name}: {g.status} ({g.method}) - {g.detail}")
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
    # Pattern: ```[language] [filename]\ncontent\n```
    # We look for lines starting with ``` followed by optional lang and optional path
    pattern = r"```(?:\w+)?\s*(?:([^\n]+))?\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    
    bundle = {}
    for i, (filename_hint, content) in enumerate(matches):
        # Clean up filename hint
        if filename_hint:
            filename = filename_hint.strip()
            # Remove common prefixes like "File:", "Path:", etc.
            filename = re.sub(r"^(File|Path|Filename|Code)\s*:\s*", "", filename, flags=re.IGNORECASE)
            # If it looks like a path, use it. Otherwise, generate a name.
            if '/' in filename or '.' in filename:
                # Sanitize potential markdown artifacts in the filename hint
                filename = sanitize_path(filename)
            else:
                filename = f"extracted_block_{i}.py"
        else:
            filename = f"extracted_block_{i}.py"
            
        # Ensure unique filenames
        if filename in bundle:
            base, ext = os.path.splitext(filename)
            counter = 1
            while filename in bundle:
                filename = f"{base}_{counter}{ext}"
                counter += 1
        
        bundle[filename] = content
        
    return bundle


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
            elif isinstance(workspace, dict):
                bundle = workspace
                source = 'workspace_dict'
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace_attr'
        
        # Fallback to deliverable text if bundle is empty
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable_fallback'
            
        if not bundle:
            # No code found
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter bundle to remove generated/malformed paths
        authored_bundle = filter_bundle(bundle)
        
        if not authored_bundle:
            return self._empty_report(source)
            
        # Count files
        authored_files = len(authored_bundle)
        python_files = count_source_files(authored_bundle, suffixes=('.py',))
        
        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write files to disk
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
            gates.append(self._gate_build(tmpdir))
            
            # 3. Smoke Gate
            gates.append(self._gate_smoke(tmpdir, authored_bundle))
            
            # 4. Tests Gate
            gates.append(self._gate_tests(tmpdir))
            
            # 5. Telemetry Gate
            gates.append(self._gate_telemetry(authored_bundle))
            
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

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if not os.path.exists(workdir):
            return self._empty_report(source)
            
        # If authored dict is provided, use it for filtering/counting, else scan dir
        if authored is None:
            # Scan directory for files
            bundle = {}
            for root, dirs, files in os.walk(workdir):
                # Skip excluded dirs
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.join(root, d))]
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, workdir)
                    if is_generated_path(rel_path) or is_malformed_path(rel_path):
                        continue
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            bundle[rel_path] = f.read()
                    except Exception:
                        pass
            authored_bundle = filter_bundle(bundle)
        else:
            authored_bundle = filter_bundle(authored)
            
        if not authored_bundle:
            return self._empty_report(source)
            
        authored_files = len(authored_bundle)
        python_files = count_source_files(authored_bundle, suffixes=('.py',))
        
        gates = []
        gates.append(self._gate_syntax(workdir, authored_bundle))
        gates.append(self._gate_build(workdir))
        gates.append(self._gate_smoke(workdir, authored_bundle))
        gates.append(self._gate_tests(workdir))
        gates.append(self._gate_telemetry(authored_bundle))
        
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
            GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No files to check'),
            GateResult(name='build', status=SKIPPED, method='pip install', detail='No files to build'),
            GateResult(name='smoke', status=SKIPPED, method='import', detail='No files to import'),
            GateResult(name='tests', status=SKIPPED, method='pytest', detail='No files to test'),
            GateResult(name='telemetry', status=SKIPPED, method='ast.analysis', detail='No files to check'),
        ]
        return VerificationReport(
            gates=gates,
            score_penalty=0.0,
            authored_files=0,
            python_files=0,
            source=source
        )

    def _gate_syntax(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        errors = []
        for path, content in bundle.items():
            if not path.endswith('.py'):
                continue
            try:
                ast.parse(content)
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

    def _gate_build(self, workdir: str) -> GateResult:
        start = time.time()
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            duration = time.time() - start
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail='No setup.py or pyproject.toml found',
                duration_s=duration
            )
        
        try:
            # Use a separate venv to avoid polluting the system
            venv_dir = os.path.join(workdir, '.venv_build')
            if os.path.exists(venv_dir):
                shutil.rmtree(venv_dir)
                
            subprocess.run(
                [self.python_executable, '-m', 'venv', venv_dir],
                check=True,
                timeout=self.timeout_s,
                capture_output=True
            )
            
            pip_executable = os.path.join(venv_dir, 'bin', 'pip')
            if sys.platform == 'win32':
                pip_executable = os.path.join(venv_dir, 'Scripts', 'pip.exe')
                
            subprocess.run(
                [pip_executable, 'install', '-e', '.'],
                cwd=workdir,
                check=True,
                timeout=self.timeout_s,
                capture_output=True
            )
            
            duration = time.time() - start
            return GateResult(
                name='build',
                status=PASSED,
                method='pip install',
                detail='Package installed successfully',
                duration_s=duration
            )
        except subprocess.CalledProcessError as e:
            duration = time.time() - start
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else ''
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail=f'Build failed: {stderr[:200]}',
                duration_s=duration,
                evidence={'stderr': stderr}
            )
        except Exception as e:
            duration = time.time() - start
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install',
                detail=f'Build error: {str(e)}',
                duration_s=duration
            )

    def _gate_smoke(self, workdir: str, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Determine package name
        package_name = None
        setup_path = os.path.join(workdir, 'setup.py')
        pyproject_path = os.path.join(workdir, 'pyproject.toml')
        
        if os.path.exists(setup_path):
            try:
                with open(setup_path, 'r') as f:
                    content = f.read()
                    match = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", content)
                    if match:
                        package_name = match.group(1)
            except Exception:
                pass
        
        if not package_name and os.path.exists(pyproject_path):
            try:
                with open(pyproject_path, 'r') as f:
                    content = f.read()
                    match = re.search(r"\[project\].*?name\s*=\s*['\"]([^'\"]+)['\"]", content, re.DOTALL)
                    if match:
                        package_name = match.group(1)
            except Exception:
                pass

        import_success = False
        import_errors = []
        
        # Strategy: Use subprocess to import in a clean environment to avoid sys.path mutation
        # This prevents the "submission mutates sys.path" failure mode.
        
        try:
            if package_name:
                # Try to import the package using the venv python if available, else system python
                venv_dir = os.path.join(workdir, '.venv_build')
                python_exec = self.python_executable
                if os.path.exists(venv_dir):
                    python_exec = os.path.join(venv_dir, 'bin', 'python')
                    if sys.platform == 'win32':
                        python_exec = os.path.join(venv_dir, 'Scripts', 'python.exe')
                
                # Run a subprocess to import the package
                # We set PYTHONPATH to workdir to ensure the package is found if not installed
                env = os.environ.copy()
                env['PYTHONPATH'] = workdir + os.pathsep + env.get('PYTHONPATH', '')
                
                result = subprocess.run(
                    [python_exec, '-c', f'import {package_name}'],
                    cwd=workdir,
                    capture_output=True,
                    timeout=self.timeout_s,
                    env=env
                )
                
                if result.returncode == 0:
                    import_success = True
                else:
                    stderr = result.stderr.decode('utf-8', errors='ignore')
                    import_errors.append(f"Import {package_name} failed: {stderr[:200]}")
            else:
                # Fallback: Try to import any top-level .py file that isn't a test
                # We iterate through bundle keys to find potential modules
                for path in bundle:
                    if path.endswith('.py') and '__init__' not in path and 'test' not in path:
                        # Convert path to module name
                        # e.g. "src/module.py" -> "src.module"
                        module_name = path.replace('/', '.').replace('\\', '.').replace('.py', '')
                        
                        venv_dir = os.path.join(workdir, '.venv_build')
                        python_exec = self.python_executable
                        if os.path.exists(venv_dir):
                            python_exec = os.path.join(venv_dir, 'bin', 'python')
                            if sys.platform == 'win32':
                                python_exec = os.path.join(venv_dir, 'Scripts', 'python.exe')
                        
                        env = os.environ.copy()
                        env['PYTHONPATH'] = workdir + os.pathsep + env.get('PYTHONPATH', '')
                        
                        result = subprocess.run(
                            [python_exec, '-c', f'import {module_name}'],
                            cwd=workdir,
                            capture_output=True,
                            timeout=self.timeout_s,
                            env=env
                        )
                        
                        if result.returncode == 0:
                            import_success = True
                            break
                        else:
                            stderr = result.stderr.decode('utf-8', errors='ignore')
                            import_errors.append(f"Import {module_name} failed: {stderr[:200]}")
        except Exception as e:
            import_errors.append(str(e))

        duration = time.time() - start
        
        if import_success:
            return GateResult(
                name='smoke',
                status=PASSED,
                method='import',
                detail='Module imported successfully',
                duration_s=duration
            )
        else:
            # If we couldn't find a package name or import any module, it's FAILED
            # If no files were present, it would have been SKIPPED earlier.
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='Failed to import any module',
                duration_s=duration,
                evidence={'errors': import_errors}
            )

    def _gate_tests(self, workdir: str) -> GateResult:
        start = time.time()
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], check=True, capture_output=True, timeout=10)
        except Exception:
            duration = time.time() - start
            return GateResult(
                name='tests',
                status=SKIPPED,
                method='pytest',
                detail='pytest not available',
                duration_s=duration
            )
        
        # Run pytest
        try:
            # Use the venv if it exists from build step
            venv_dir = os.path.join(workdir, '.venv_build')
            python_exec = self.python_executable
            if os.path.exists(venv_dir):
                python_exec = os.path.join(venv_dir, 'bin', 'python')
                if sys.platform == 'win32':
                    python_exec = os.path.join(venv_dir, 'Scripts', 'python.exe')
            
            result = subprocess.run(
                [python_exec, '-m', 'pytest', '-q'],
                cwd=workdir,
                capture_output=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start
            if result.returncode == 0:
                return GateResult(
                    name='tests',
                    status=PASSED,
                    method='pytest',
                    detail='All tests passed',
                    duration_s=duration
                )
            else:
                stdout = result.stdout.decode('utf-8', errors='ignore')
                stderr = result.stderr.decode('utf-8', errors='ignore')
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f'Tests failed (exit {result.returncode})',
                    duration_s=duration,
                    evidence={'stdout': stdout[:500], 'stderr': stderr[:500]}
                )
        except subprocess.TimeoutExpired:
            duration = time.time() - start
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='Test execution timed out',
                duration_s=duration
            )
        except Exception as e:
            duration = time.time() - start
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail=f'Error running tests: {str(e)}',
                duration_s=duration
            )

    def _gate_telemetry(self, bundle: Dict[str, str]) -> GateResult:
        start = time.time()
        # Check if any authored .py file imports opentelemetry
        # Must be an actual import statement, not just text
        
        found_import = False
        import_files = []
        
        for path, content in bundle.items():
            if not path.endswith('.py'):
                continue
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if 'opentelemetry' in alias.name:
                                found_import = True
                                import_files.append(path)
                                break
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and 'opentelemetry' in node.module:
                            found_import = True
                            import_files.append(path)
                            break
            except SyntaxError:
                # Syntax gate already handled this, but just in case
                pass
                
        duration = time.time() - start
        
        if found_import:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast.analysis',
                detail=f'OpenTelemetry imported in: {", ".join(import_files[:3])}',
                duration_s=duration,
                evidence={'files': import_files}
            )
        else:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast.analysis',
                detail='No OpenTelemetry imports found in authored code',
                duration_s=duration
            )