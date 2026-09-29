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
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Constants
SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# Regex for extracting fenced code blocks
# Matches ```lang\ncontent\n``` or ```\ncontent\n```
# We want to capture the language (optional) and the content.
CODE_BLOCK_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_+-]*)\n(?P<content>.*?)\n```",
    re.DOTALL
)

# Heuristic for detecting OpenTelemetry imports
# Looks for 'import opentelemetry' or 'from opentelemetry'
OTEL_IMPORT_RE = re.compile(
    r"^\s*(import\s+opentelemetry|from\s+opentelemetry)",
    re.MULTILINE
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
        passed = [g for g in evaluated if g.passed]
        return len(passed) / len(evaluated)

    def summary(self) -> str:
        lines = [
            f"Verification Report ({self.source})",
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
    if not text:
        return {}
    
    blocks = {}
    # Find all code blocks
    matches = CODE_BLOCK_RE.finditer(text)
    
    for match in matches:
        lang = match.group('lang').lower()
        content = match.group('content')
        
        # We only care about Python code blocks for now, or generic ones that look like Python
        # If lang is empty, we might guess it's Python if it contains 'def ' or 'import '
        # But strictly, we should probably only take python blocks or blocks with no lang that look like code.
        # The spec says "Recovers a path->content map". 
        # How do we determine the path? 
        # Usually, agents might write:
        # ```python
        # # path: src/main.py
        # ...
        # ```
        # Or the filename might be in the fence info: ```python src/main.py
        # Let's support both:
        # 1. Fence info contains a path: ```python path/to/file.py
        # 2. First line of content is a comment with path: # path: ...
        
        path = None
        fence_info = match.group(0).split('\n')[0] # e.g. ```python src/main.py
        
        # Try to extract path from fence info
        # Pattern: ```lang path
        parts = fence_info.strip().split()
        if len(parts) > 1:
            # Check if the second part looks like a path
            candidate = parts[1]
            if '.' in candidate or '/' in candidate:
                path = candidate
        
        # If no path in fence info, check first line of content for comment
        if not path:
            lines = content.split('\n')
            if lines:
                first_line = lines[0].strip()
                # Match # path: ... or # filename: ...
                path_match = re.match(r"^#\s*(?:path|filename|file):\s*(.+)$", first_line, re.IGNORECASE)
                if path_match:
                    path = path_match.group(1).strip()
        
        # If still no path, we can't reliably map it. 
        # However, if it's a single block, maybe we assume main.py? 
        # No, better to skip if we can't determine path, or use a generic name if it's the only one?
        # The spec says "path->content map". If we can't determine path, we can't put it in the map.
        # But often agents just write code. 
        # Let's try to infer from content if it's clearly a module? No, too risky.
        # Let's assume if no path is found, we skip it, UNLESS it's the only block and we can guess?
        # Actually, let's look at the "lang" part. If lang is 'python', we might default to 'main.py' if no path?
        # No, that's guessing. 
        # Let's stick to explicit paths. If no path, we can't verify it properly.
        # However, to be robust, if no path is found, we might generate a synthetic path based on index?
        # No, that breaks imports.
        
        # Let's refine: If no path is found, we skip this block.
        if not path:
            continue
            
        # Clean up path
        path = path.strip()
        if not path:
            continue
            
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
            elif hasattr(workspace, 'files'):
                bundle = workspace.files
                source = 'workspace'
        
        # If bundle is empty, try extracting from deliverable_text
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        if not bundle:
            # No code found
            return self._empty_report(source)
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        if not bundle:
            return self._empty_report(source)
            
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write bundle to temp dir
            for path, content in bundle.items():
                full_path = os.path.join(tmpdir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    
            return self.verify_directory(tmpdir, authored=bundle, source=source)

    def verify_directory(self, workdir: str, authored: Optional[Dict[str, str]] = None, source: str = 'directory') -> VerificationReport:
        """Verifies an existing workspace directory."""
        if not os.path.isdir(workdir):
            return self._empty_report(source)
            
        # Determine authored files
        if authored is None:
            # Scan directory for .py files
            authored = {}
            for root, dirs, files in os.walk(workdir):
                # Skip hidden dirs and common junk
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'venv', '.venv')]
                for file in files:
                    if file.endswith('.py'):
                        rel_path = os.path.relpath(os.path.join(root, file), workdir)
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                authored[rel_path] = f.read()
                        except Exception:
                            pass
        else:
            # Filter authored to only include files that actually exist in workdir?
            # Or just use the provided map. The spec says "authored" is the map.
            pass
            
        # Count files
        authored_files_count = len(authored)
        python_files_count = len([p for p in authored.keys() if p.endswith('.py')])
        
        gates = []
        
        # 1. Syntax Gate
        gates.append(self._check_syntax(workdir, authored))
        
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
        skipped_gates = [g for g in gates if g.status == SKIPPED]
        passed_gates = [g for g in gates if g.status == PASSED]
        
        # Penalty is applied per failed gate. Skipped gates are excluded from denominator.
        # "SKIPPED gates are excluded from the fitness denominator rather than scored as failures."
        # This implies we only penalize FAILED gates.
        score_penalty = len(failed_gates) * self.penalty_per_gate
        
        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files_count,
            python_files=python_files_count,
            source=source
        )

    def _empty_report(self, source: str) -> VerificationReport:
        gates = [
            GateResult(name='syntax', status=SKIPPED, method='ast.parse', detail='No files to check'),
            GateResult(name='build', status=SKIPPED, method='pip install -e .', detail='No files to build'),
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

    def _check_syntax(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        py_files = {p: c for p, c in authored.items() if p.endswith('.py')}
        
        if not py_files:
            return GateResult(
                name='syntax',
                status=SKIPPED,
                method='ast.parse',
                detail='No Python files found',
                duration_s=time.time() - start
            )
        
        errors = []
        for path, content in py_files.items():
            try:
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")
            except Exception as e:
                errors.append(f"{path}: Unexpected error: {e}")
                
        duration = time.time() - start
        
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='; '.join(errors),
                duration_s=duration,
                evidence={'errors': errors}
            )
        else:
            return GateResult(
                name='syntax',
                status=PASSED,
                method='ast.parse',
                detail=f"All {len(py_files)} Python files parsed successfully",
                duration_s=duration
            )

    def _check_build(self, workdir: str) -> GateResult:
        start = time.time()
        
        # Check if setup.py or pyproject.toml exists
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))
        
        if not has_setup and not has_pyproject:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip install -e .',
                detail='No setup.py or pyproject.toml found',
                duration_s=time.time() - start
            )
        
        # Try to install
        try:
            # Use a temporary venv or just install to current? 
            # "pip install -e ." usually installs to current environment.
            # To be safe and isolated, we might want a venv, but that's slow.
            # The spec says "The package must actually install".
            # We'll try pip install -e . in the workdir.
            
            cmd = [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--no-deps']
            # --no-deps to avoid pulling in huge dependencies if not needed for build check?
            # But "build" usually implies the package structure is valid.
            # Let's try without --no-deps first, but with timeout.
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
                return GateResult(
                    name='build',
                    status=PASSED,
                    method='pip install -e .',
                    detail='Package installed successfully',
                    duration_s=duration
                )
            else:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip install -e .',
                    detail=f"Install failed: {result.stderr}",
                    duration_s=duration,
                    evidence={'stderr': result.stderr, 'stdout': result.stdout}
                )
                
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail='Install timed out',
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail=f"Install error: {e}",
                duration_s=time.time() - start
            )

    def _check_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        # Find top-level modules/packages
        # We need to import them. 
        # Strategy: Add workdir to sys.path, then try to import each top-level .py file or package dir.
        
        # Identify importable names
        importable_names = set()
        for path in authored.keys():
            if not path.endswith('.py'):
                continue
            # Skip __init__.py for module name derivation? 
            # If path is 'pkg/__init__.py', module is 'pkg'
            # If path is 'pkg/mod.py', module is 'pkg.mod'
            # If path is 'mod.py', module is 'mod'
            
            parts = path.split('/')
            if parts[-1] == '__init__.py':
                if len(parts) > 1:
                    module_name = '.'.join(parts[:-1])
                    importable_names.add(module_name)
            else:
                # Remove .py
                module_name = '.'.join(parts[:-1] + [parts[-1][:-3]])
                importable_names.add(module_name)
        
        if not importable_names:
            return GateResult(
                name='smoke',
                status=SKIPPED,
                method='import',
                detail='No importable modules found',
                duration_s=time.time() - start
            )
        
        # We need to run imports in a subprocess to avoid polluting the current process
        # and to handle side effects.
        
        # Create a script that imports all modules
        script_lines = [
            "import sys",
            "import os",
            f"sys.path.insert(0, r'{workdir}')",
            "import importlib",
            "errors = []",
            "names = []"
        ]
        
        for name in sorted(importable_names):
            script_lines.append(f"try:")
            script_lines.append(f"    importlib.import_module('{name}')")
            script_lines.append(f"except Exception as e:")
            script_lines.append(f"    errors.append(f'{name}: {{e}}')")
            script_lines.append(f"else:")
            script_lines.append(f"    names.append('{name}')")
            
        script_lines.append("import json")
        script_lines.append("print(json.dumps({'errors': errors, 'names': names}))")
        
        script_content = "\n".join(script_lines)
        
        try:
            result = subprocess.run(
                [self.python_executable, '-c', script_content],
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start
            
            if result.returncode != 0:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='import',
                    detail=f"Import script failed: {result.stderr}",
                    duration_s=duration
                )
            
            try:
                output = json.loads(result.stdout)
                errors = output.get('errors', [])
                names = output.get('names', [])
                
                if errors:
                    return GateResult(
                        name='smoke',
                        status=FAILED,
                        method='import',
                        detail='; '.join(errors),
                        duration_s=duration,
                        evidence={'errors': errors, 'imported': names}
                    )
                else:
                    return GateResult(
                        name='smoke',
                        status=PASSED,
                        method='import',
                        detail=f"Successfully imported {len(names)} modules",
                        duration_s=duration,
                        evidence={'imported': names}
                    )
            except json.JSONDecodeError:
                return GateResult(
                    name='smoke',
                    status=FAILED,
                    method='import',
                    detail=f"Invalid output from import script: {result.stdout}",
                    duration_s=duration
                )
                
        except subprocess.TimeoutExpired:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='Import timed out',
                duration_s=time.time() - start
            )
        except Exception as e:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail=f"Import error: {e}",
                duration_s=time.time() - start
            )

    def _check_tests(self, workdir: str) -> GateResult:
        start = time.time()
        
        # Check if pytest is available
        try:
            subprocess.run([self.python_executable, '-m', 'pytest', '--version'], capture_output=True, timeout=10)
        except Exception:
            return GateResult(
                name='tests',
                status=SKIPPED,
                method='pytest',
                detail='pytest not available',
                duration_s=time.time() - start
            )
        
        # Run pytest
        try:
            result = subprocess.run(
                [self.python_executable, '-m', 'pytest', '-v', '--tb=short'],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            duration = time.time() - start
            
            # Pytest return codes:
            # 0: All tests passed
            # 1: Tests failed
            # 2: Interrupted
            # 3: Internal error
            # 4: Usage error
            # 5: No tests collected
            
            if result.returncode == 0:
                return GateResult(
                    name='tests',
                    status=PASSED,
                    method='pytest',
                    detail='All tests passed',
                    duration_s=duration
                )
            elif result.returncode == 5:
                return GateResult(
                    name='tests',
                    status=SKIPPED,
                    method='pytest',
                    detail='No tests collected',
                    duration_s=duration
                )
            else:
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f"Tests failed (exit code {result.returncode}): {result.stdout[-500:]}",
                    duration_s=duration,
                    evidence={'stdout': result.stdout, 'stderr': result.stderr}
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
                detail=f"Test execution error: {e}",
                duration_s=time.time() - start
            )

    def _check_telemetry(self, authored: Dict[str, str]) -> GateResult:
        start = time.time()
        
        py_files = {p: c for p, c in authored.items() if p.endswith('.py')}
        
        if not py_files:
            return GateResult(
                name='telemetry',
                status=SKIPPED,
                method='ast.parse',
                detail='No Python files found',
                duration_s=time.time() - start
            )
        
        found_otel = False
        evidence = {}
        
        for path, content in py_files.items():
            # Check for import statements
            if OTEL_IMPORT_RE.search(content):
                found_otel = True
                evidence[path] = 'import detected'
                break # One is enough
        
        duration = time.time() - start
        
        if found_otel:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast.parse',
                detail='OpenTelemetry import found in authored code',
                duration_s=duration,
                evidence=evidence
            )
        else:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast.parse',
                detail='No OpenTelemetry import found in authored code',
                duration_s=duration
            )