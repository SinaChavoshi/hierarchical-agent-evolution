"""Execution-grounded verification of agent-authored workspaces.

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

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIPPED = 'skipped'
PASSED = 'passed'
FAILED = 'failed'
DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


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
        return len(self.passed_gates) / len(evaluated)

    def summary(self) -> str:
        parts = []
        for g in self.gates:
            parts.append(f"{g.name}={g.status}")
        return (
            f"VerificationReport(source={self.source}, "
            f"authored_files={self.authored_files}, "
            f"python_files={self.python_files}, "
            f"pass_rate={self.pass_rate:.2f}, "
            f"penalty={self.score_penalty:.2f}, "
            f"gates=[{', '.join(parts)}])"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'gates': [
                {
                    'name': g.name,
                    'status': g.status,
                    'method': g.method,
                    'detail': g.detail,
                    'duration_s': g.duration_s,
                    'evidence': g.evidence,
                }
                for g in self.gates
            ],
            'score_penalty': self.score_penalty,
            'authored_files': self.authored_files,
            'python_files': self.python_files,
            'source': self.source,
            'harness_version': self.harness_version,
            'pass_rate': self.pass_rate,
            'gate_status': self.gate_status,
            'gate_detail': self.gate_detail,
        }


# ---------------------------------------------------------------------------
# Code block extraction
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(
    r'^```(?:python|py)?\s*\n(.*?)^```',
    re.MULTILINE | re.DOTALL,
)

_PATH_COMMENT_RE = re.compile(
    r'^#\s*(?:path|file|filename)\s*[:=]\s*(.+?)\s*$',
    re.IGNORECASE,
)


def extract_code_blocks(text: str) -> Dict[str, str]:
    """Recovers a path->content map from fenced code blocks in a deliverable.

    A last resort, used only when a firm produced no live workspace. Only the
    contents of fenced blocks are returned; the surrounding prose is discarded
    and never reaches a gate.
    """
    if not text:
        return {}

    result: Dict[str, str] = {}
    blocks = _FENCE_RE.findall(text)

    for i, block in enumerate(blocks):
        # Try to find a path comment at the start of the block
        lines = block.split('\n')
        path = None
        content_start = 0

        for j, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            m = _PATH_COMMENT_RE.match(stripped)
            if m:
                path = m.group(1).strip().strip('`').strip('"').strip("'")
                content_start = j + 1
                break
            # If the first non-empty line isn't a path comment, no path
            break

        if path is None:
            # Generate a synthetic path
            path = f"extracted_block_{i}.py"

        # Strip leading/trailing whitespace from content
        content = '\n'.join(lines[content_start:])
        content = content.strip()

        if content:
            result[path] = content

    return result


# ---------------------------------------------------------------------------
# Path filtering helpers (mirrors artifacts.py contract)
# ---------------------------------------------------------------------------

_EXCLUDED_DIR_SEGMENTS = (
    '.pytest_cache', '__pycache__', 'venv', '.venv', '.git',
    '.mypy_cache', '.ruff_cache', '.tox', 'node_modules',
    '.ipynb_checkpoints',
)

_EXCLUDED_DIR_SUFFIXES = ('.egg-info', '.dist-info')


def _is_generated_path(path: str) -> bool:
    """True if the path lives inside a machine-generated directory."""
    parts = path.replace('\\', '/').split('/')
    for part in parts[:-1]:  # exclude the filename itself
        if part in _EXCLUDED_DIR_SEGMENTS:
            return True
        for suffix in _EXCLUDED_DIR_SUFFIXES:
            if part.endswith(suffix):
                return True
    return False


def _is_malformed_path(path: str) -> bool:
    """True if markdown formatting, stray whitespace, or invalid punctuation leaked into the filename."""
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


def _sanitize_path(path: str) -> str:
    """Strips markdown decoration from an agent-supplied path."""
    p = path.strip()
    p = p.replace('*', '').replace('`', '')
    p = p.strip()
    if p.startswith('-'):
        p = p.lstrip('-')
    if p.endswith('.') or p.endswith('~'):
        p = p.rstrip('.~')
    return p


def _filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
    """Returns only genuinely agent-authored files from a path->content map."""
    result = {}
    for path, content in bundle.items():
        if _is_generated_path(path):
            continue
        if _is_malformed_path(path):
            continue
        result[path] = content
    return result


def _partition_bundle(bundle: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """Splits a bundle into (authored, generated, malformed) for reporting."""
    authored = {}
    generated = {}
    malformed = {}
    for path, content in bundle.items():
        if _is_generated_path(path):
            generated[path] = content
        elif _is_malformed_path(path):
            malformed[path] = content
        else:
            authored[path] = content
    return authored, generated, malformed


def _count_source_files(bundle: Dict[str, str], suffixes: Tuple[str, ...] = ('.py',)) -> int:
    """Counts authored files matching the given suffixes."""
    filtered = _filter_bundle(bundle)
    return sum(1 for p in filtered if p.endswith(suffixes))


# ---------------------------------------------------------------------------
# ExecutionHarness
# ---------------------------------------------------------------------------


class ExecutionHarness:
    """Materializes a workspace bundle and verifies it by actually running it."""

    def __init__(
        self,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        penalty_per_gate: float = DEFAULT_PENALTY_PER_GATE,
        python_executable: Optional[str] = None,
    ):
        self.timeout_s = timeout_s
        self.penalty_per_gate = penalty_per_gate
        self.python_executable = python_executable or sys.executable

    def verify_workspace(
        self,
        workspace: Any = None,
        deliverable_text: str = '',
    ) -> VerificationReport:
        """Verifies whatever code a firm actually produced.

        This is the entry point tournaments use. A firm normally writes into a
        live `AgentWorkspace`; if it never did, we fall back to the fenced code
        blocks embedded in its written deliverable.

        `deliverable_text` is *only* mined for code blocks. Its prose is never
        passed to a gate, because the entire point of this harness is that an
        essay about OpenTelemetry cannot satisfy the telemetry gate.
        """
        # Try to get a bundle from the workspace object
        bundle: Optional[Dict[str, str]] = None
        source = 'bundle'

        if workspace is not None:
            # Try common workspace interfaces
            if hasattr(workspace, 'files'):
                bundle = dict(workspace.files)
                source = 'workspace'
            elif hasattr(workspace, 'list_files'):
                try:
                    files = workspace.list_files()
                    if isinstance(files, dict):
                        bundle = dict(files)
                    elif isinstance(files, list):
                        bundle = {}
                        for f in files:
                            if isinstance(f, str):
                                try:
                                    content = workspace.read_file(f) if hasattr(workspace, 'read_file') else ''
                                    bundle[f] = content
                                except Exception:
                                    bundle[f] = ''
                            elif isinstance(f, dict) and 'path' in f:
                                bundle[f['path']] = f.get('content', '')
                    source = 'workspace'
                except Exception:
                    pass
            elif hasattr(workspace, 'get_files'):
                try:
                    bundle = dict(workspace.get_files())
                    source = 'workspace'
                except Exception:
                    pass
            elif isinstance(workspace, dict):
                bundle = dict(workspace)
                source = 'workspace'

        if bundle is None or not bundle:
            # Fall back to extracting code blocks from deliverable text
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'

        if not bundle:
            # No code at all — return a report with all gates FAILED
            # Empty workspace is a failure, not a skip.
            gates = [
                GateResult(name='syntax', status=FAILED, method='ast.parse', detail='No files to check'),
                GateResult(name='build', status=FAILED, method='pip install -e .', detail='No files to build'),
                GateResult(name='smoke', status=FAILED, method='import', detail='No files to import'),
                GateResult(name='tests', status=FAILED, method='pytest', detail='No files to test'),
                GateResult(name='telemetry', status=FAILED, method='ast.parse', detail='No files to check'),
            ]
            return VerificationReport(
                gates=gates,
                score_penalty=5 * self.penalty_per_gate,
                authored_files=0,
                python_files=0,
                source=source,
            )

        return self.verify_bundle(bundle, source=source)

    def verify_bundle(
        self,
        bundle: Dict[str, str],
        source: str = 'bundle',
    ) -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        tmpdir = tempfile.mkdtemp(prefix='hae_harness_')
        try:
            return self._verify_in_dir(tmpdir, bundle, source)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def verify_directory(
        self,
        workdir: str,
        authored: Optional[Dict[str, str]] = None,
        source: str = 'directory',
    ) -> VerificationReport:
        """Verifies an existing workspace directory."""
        return self._verify_in_dir(workdir, authored, source)

    def _verify_in_dir(
        self,
        workdir: str,
        bundle: Optional[Dict[str, str]],
        source: str,
    ) -> VerificationReport:
        """Core verification logic operating on a directory."""
        # Write bundle to disk if provided
        if bundle is not None:
            for rel_path, content in bundle.items():
                # Sanitize path
                clean_path = _sanitize_path(rel_path)
                if not clean_path:
                    continue
                full_path = os.path.join(workdir, clean_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)

        # Collect all files in the directory
        all_files: Dict[str, str] = {}
        for root, dirs, files in os.walk(workdir):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIR_SEGMENTS]
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, workdir)
                try:
                    with open(full, 'r', encoding='utf-8', errors='replace') as f:
                        all_files[rel] = f.read()
                except Exception:
                    all_files[rel] = ''

        # Partition
        authored, generated, malformed = _partition_bundle(all_files)
        authored_files_count = len(authored)
        python_files_count = sum(1 for p in authored if p.endswith('.py'))

        # Run gates
        gates: List[GateResult] = []

        # Gate 1: syntax
        gates.append(self._gate_syntax(authored))

        # Gate 2: build
        gates.append(self._gate_build(workdir, authored))

        # Gate 3: smoke
        gates.append(self._gate_smoke(workdir, authored))

        # Gate 4: tests
        gates.append(self._gate_tests(workdir, authored))

        # Gate 5: telemetry
        gates.append(self._gate_telemetry(authored))

        # Calculate penalty
        failed_count = sum(1 for g in gates if g.status == FAILED)
        score_penalty = failed_count * self.penalty_per_gate

        return VerificationReport(
            gates=gates,
            score_penalty=score_penalty,
            authored_files=authored_files_count,
            python_files=python_files_count,
            source=source,
        )

    # -----------------------------------------------------------------------
    # Gate implementations
    # -----------------------------------------------------------------------

    def _gate_syntax(self, authored: Dict[str, str]) -> GateResult:
        """Every authored .py file must parse."""
        start = time.time()
        py_files = {p: c for p, c in authored.items() if p.endswith('.py')}

        if not py_files:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='No Python files to check',
                duration_s=time.time() - start,
            )

        errors: List[str] = []
        for path, content in py_files.items():
            try:
                ast.parse(content, filename=path)
            except SyntaxError as e:
                errors.append(f"{path}: {e}")

        duration = time.time() - start
        if errors:
            return GateResult(
                name='syntax',
                status=FAILED,
                method='ast.parse',
                detail='; '.join(errors[:10]),
                duration_s=duration,
                evidence={'errors': errors, 'files_checked': len(py_files)},
            )
        return GateResult(
            name='syntax',
            status=PASSED,
            method='ast.parse',
            detail=f"All {len(py_files)} Python files parse successfully",
            duration_s=duration,
            evidence={'files_checked': len(py_files)},
        )

    def _gate_build(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        """The package must actually install (`pip install -e .`)."""
        start = time.time()

        # Check if there's a setup.py or pyproject.toml
        has_setup = os.path.exists(os.path.join(workdir, 'setup.py'))
        has_pyproject = os.path.exists(os.path.join(workdir, 'pyproject.toml'))

        if not has_setup and not has_pyproject:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail='No setup.py or pyproject.toml found',
                duration_s=time.time() - start,
            )

        try:
            result = subprocess.run(
                [self.python_executable, '-m', 'pip', 'install', '-e', '.', '--quiet', '--no-deps'],
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
                    method='pip install -e .',
                    detail='Package installed successfully',
                    duration_s=duration,
                    evidence={'stdout': result.stdout[:500], 'stderr': result.stderr[:500]},
                )
            else:
                return GateResult(
                    name='build',
                    status=FAILED,
                    method='pip install -e .',
                    detail=f"pip install failed: {result.stderr[:500]}",
                    duration_s=duration,
                    evidence={'returncode': result.returncode, 'stderr': result.stderr[:500]},
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail=f'pip install timed out after {self.timeout_s}s',
                duration_s=time.time() - start,
            )
        except FileNotFoundError:
            return GateResult(
                name='build',
                status=SKIPPED,
                method='pip install -e .',
                detail='pip not available',
                duration_s=time.time() - start,
            )
        except Exception as e:
            return GateResult(
                name='build',
                status=FAILED,
                method='pip install -e .',
                detail=f'Build error: {e}',
                duration_s=time.time() - start,
            )

    def _gate_smoke(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        """Its modules must actually import."""
        start = time.time()

        py_files = {p: c for p, c in authored.items() if p.endswith('.py')}
        if not py_files:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='No Python files to import',
                duration_s=time.time() - start,
            )

        # Determine top-level packages/modules to import
        # Look for __init__.py files to identify packages
        packages_to_import: List[str] = []
        modules_to_import: List[str] = []

        for path in py_files:
            parts = path.replace('\\', '/').split('/')
            if path.endswith('__init__.py'):
                # This is a package
                pkg_parts = parts[:-1]
                if pkg_parts:
                    pkg_name = '.'.join(pkg_parts)
                    if pkg_name not in packages_to_import:
                        packages_to_import.append(pkg_name)
            else:
                # This is a module
                mod_parts = parts[:-1] + [parts[-1].replace('.py', '')]
                mod_name = '.'.join(mod_parts)
                if mod_name not in modules_to_import:
                    modules_to_import.append(mod_name)

        # If no packages found, try importing top-level modules
        if not packages_to_import and not modules_to_import:
            # Try to import any .py file as a module
            for path in py_files:
                mod_name = path.replace('\\', '/').replace('/', '.').replace('.py', '')
                if mod_name not in modules_to_import:
                    modules_to_import.append(mod_name)

        if not packages_to_import and not modules_to_import:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='No importable modules identified',
                duration_s=time.time() - start,
            )

        # Add workdir to sys.path temporarily
        old_path = sys.path.copy()
        sys.path.insert(0, workdir)

        import_errors: List[str] = []
        imported_count = 0

        try:
            for pkg in packages_to_import:
                try:
                    importlib.import_module(pkg)
                    imported_count += 1
                except Exception as e:
                    import_errors.append(f"{pkg}: {e}")

            for mod in modules_to_import:
                try:
                    importlib.import_module(mod)
                    imported_count += 1
                except Exception as e:
                    import_errors.append(f"{mod}: {e}")
        finally:
            sys.path[:] = old_path

        duration = time.time() - start
        total_to_import = len(packages_to_import) + len(modules_to_import)

        if import_errors:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='; '.join(import_errors[:10]),
                duration_s=duration,
                evidence={
                    'errors': import_errors,
                    'imported': imported_count,
                    'total': total_to_import,
                },
            )

        if imported_count == 0:
            return GateResult(
                name='smoke',
                status=FAILED,
                method='import',
                detail='No modules were successfully imported',
                duration_s=duration,
            )

        return GateResult(
            name='smoke',
            status=PASSED,
            method='import',
            detail=f"Successfully imported {imported_count} module(s)",
            duration_s=duration,
            evidence={'imported': imported_count, 'total': total_to_import},
        )

    def _gate_tests(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        """Its test suite must actually collect and pass under pytest."""
        start = time.time()

        # Check if pytest is available
        try:
            import pytest  # noqa: F401
        except ImportError:
            return GateResult(
                name='tests',
                status=SKIPPED,
                method='pytest',
                detail='pytest not available',
                duration_s=time.time() - start,
            )

        # Check if there are test files
        test_files = [p for p in authored if 'test' in p.lower() and p.endswith('.py')]
        if not test_files:
            # Also check for tests/ directory
            tests_dir = os.path.join(workdir, 'tests')
            if os.path.isdir(tests_dir):
                test_files = [
                    os.path.relpath(os.path.join(tests_dir, f), workdir)
                    for f in os.listdir(tests_dir)
                    if f.endswith('.py') and 'test' in f.lower()
                ]

        if not test_files:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail='No test files found',
                duration_s=time.time() - start,
            )

        try:
            result = subprocess.run(
                [self.python_executable, '-m', 'pytest', '--tb=short', '-q', '--no-header'],
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
                    method='pytest',
                    detail='All tests passed',
                    duration_s=duration,
                    evidence={'stdout': result.stdout[:500], 'stderr': result.stderr[:500]},
                )
            else:
                # Check if it was a collection error vs actual test failure
                output = result.stdout + result.stderr
                if 'no tests ran' in output.lower() or 'collected 0 items' in output.lower():
                    return GateResult(
                        name='tests',
                        status=FAILED,
                        method='pytest',
                        detail='No tests collected',
                        duration_s=duration,
                        evidence={'stdout': result.stdout[:500], 'stderr': result.stderr[:500]},
                    )
                return GateResult(
                    name='tests',
                    status=FAILED,
                    method='pytest',
                    detail=f'Tests failed: {result.stderr[:500]}',
                    duration_s=duration,
                    evidence={
                        'returncode': result.returncode,
                        'stdout': result.stdout[:500],
                        'stderr': result.stderr[:500],
                    },
                )
        except subprocess.TimeoutExpired:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail=f'pytest timed out after {self.timeout_s}s',
                duration_s=time.time() - start,
            )
        except Exception as e:
            return GateResult(
                name='tests',
                status=FAILED,
                method='pytest',
                detail=f'pytest error: {e}',
                duration_s=time.time() - start,
            )

    def _gate_telemetry(self, authored: Dict[str, str]) -> GateResult:
        """OpenTelemetry must be imported by authored code, not merely mentioned in prose."""
        start = time.time()

        py_files = {p: c for p, c in authored.items() if p.endswith('.py')}
        if not py_files:
            return GateResult(
                name='telemetry',
                status=FAILED,
                method='ast.parse',
                detail='No Python files to check',
                duration_s=time.time() - start,
            )

        # Look for actual import statements of opentelemetry
        otel_import_found = False
        otel_files: List[str] = []

        for path, content in py_files.items():
            try:
                tree = ast.parse(content, filename=path)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith('opentelemetry'):
                            otel_import_found = True
                            otel_files.append(path)
                            break
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith('opentelemetry'):
                        otel_import_found = True
                        otel_files.append(path)
                        break

            if otel_import_found:
                break

        duration = time.time() - start

        if otel_import_found:
            return GateResult(
                name='telemetry',
                status=PASSED,
                method='ast.parse',
                detail=f'OpenTelemetry imported in: {", ".join(otel_files[:5])}',
                duration_s=duration,
                evidence={'files': otel_files},
            )

        return GateResult(
            name='telemetry',
            status=FAILED,
            method='ast.parse',
            detail='No OpenTelemetry import found in authored Python files',
            duration_s=duration,
            evidence={'files_checked': len(py_files)},
        )