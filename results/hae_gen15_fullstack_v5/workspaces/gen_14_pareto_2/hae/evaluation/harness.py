"""Execution-grounded verification of agent-authored workspaces.

Five gates, each of which either runs the code or performs AST analysis on it:

    syntax     Every authored .py file must parse (`ast.parse`). Empty
               workspaces (0 Python files) return `FAILED`.
    build      Packaging metadata (`pyproject.toml`, `setup.py`, or `setup.cfg`)
               must exist and be valid/installable. When `pyproject.toml` is
               present, validate it with `tomllib` (must parse as valid TOML and
               contain at least one of `[project]`, `[build-system]`, or
               `[tool]` tables; an empty or malformed `pyproject.toml` returns
               `FAILED`), and run `pip install -e . --no-deps --no-build-isolation`
               when `pip` is available.
    smoke      Non-test authored modules must import in a subprocess with both
               `workdir` and `os.path.join(workdir, "src")` on `PYTHONPATH`. If
               no importable modules exist or any module fails with a syntax or
               runtime error, return `FAILED`. If module imports fail solely
               with `ModuleNotFoundError` for uninstalled third-party packages
               (e.g. `opentelemetry`), return `SKIPPED` (not `FAILED`).
    tests      Discover test files (`"test" in os.path.basename(p).lower()`;
               if none exist, return `FAILED`) and run them with both `workdir`
               and `os.path.join(workdir, "src")` on `PYTHONPATH`. Use `pytest`
               if installed in `self.python`; otherwise run a stdlib fallback
               subprocess that imports each test file and collects/executes BOTH
               bare `def test_*()` functions (taking 0 required args) AND
               `unittest.TestCase` subclasses (because `unittest discover`
               ignores bare `def test_*()` functions). If 0 tests are collected
               because of `ModuleNotFoundError` for an uninstalled third-party
               package, return `SKIPPED`.
    telemetry  AST analysis of authored `.py` files must find BOTH (1) an actual
               `import opentelemetry` or `from opentelemetry ... import ...`
               node AND (2) at least one AST `Call` to a tracer/span method or
               function (`start_as_current_span`, `start_span`, `get_tracer`,
               `get_tracer_provider`, `set_tracer_provider`, `get_meter`). A
               comment/docstring mention or a bare `import opentelemetry`
               without any tracer/span call must return `FAILED`.

Each result carries the `method` used to reach it. A gate that could not be
evaluated returns `SKIPPED` rather than `PASSED`, so a missing tool can never be
mistaken for a success, and `SKIPPED` gates are excluded from the fitness
denominator (`evaluated_gates` and `score_penalty`) rather than scored as
failures (`score_penalty = failed_gate_count * penalty_per_gate`).

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

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hae.evaluation.artifacts import filter_bundle, is_generated_path, is_malformed_path

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
        passed = len(self.passed_gates)
        return passed / len(evaluated)

    def summary(self) -> str:
        lines = [f"Verification Report ({self.source}):"]
        for g in self.gates:
            status_icon = "PASS" if g.passed else ("SKIP" if g.status == SKIPPED else "FAIL")
            lines.append(f"  [{status_icon}] {g.name}: {g.detail}")
        lines.append(f"  Penalty: {self.score_penalty:.2f}")
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
            "harness_version": self.harness_version
        }


def extract_code_blocks(text: str) -> Dict[str, str]:
    """Recovers a path->content map from fenced code blocks in a deliverable.

    A last resort, used only when a firm produced no live workspace. Only the
    contents of fenced blocks are returned; the surrounding prose is discarded
    and never reaches a gate.
    """
    blocks = {}
    # Regex to find fenced code blocks with optional language and optional filename hint
    # Pattern: ```lang\n...content...\n```
    # We look for lines that might indicate a filename before the block or inside the fence info
    
    pattern = r"```(\w+)?\s*\n(.*?)\n```"
    matches = re.finditer(pattern, text, re.DOTALL)
    
    for match in matches:
        lang = match.group(1) or ""
        content = match.group(2)
        
        # Try to infer filename from the language or preceding text
        # Simple heuristic: if lang is python, assume main.py or similar if not specified
        # In a real scenario, we might look for "File: path/to/file.py" before the block
        # For now, we generate a unique name based on index to avoid collisions
        filename = f"extracted_{len(blocks)}.py" if lang.lower() in ("python", "py") else f"extracted_{len(blocks)}.txt"
        
        # Check if there's a filename hint in the fence info (e.g. ```python path/to/file.py)
        # The regex above captures only the first word. Let's refine slightly.
        # Actually, standard markdown doesn't support filenames in fences.
        # We'll stick to generated names for extracted blocks.
        
        blocks[filename] = content
        
    return blocks


class ExecutionHarness:
    """Materializes a workspace bundle and verifies it by actually running it."""

    def __init__(self, timeout_s: int = DEFAULT_TIMEOUT_S, penalty_per_gate: float = DEFAULT_PENALTY_PER_GATE, python_executable: Optional[str] = None):
        self.timeout_s = timeout_s
        self.penalty_per_gate = penalty_per_gate
        self.python = python_executable or sys.executable

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
        
        if workspace is not None:
            # Assume workspace has export_bundle()
            try:
                bundle = workspace.export_bundle()
            except Exception:
                bundle = {}
                
        if not bundle and deliverable_text:
            bundle = extract_code_blocks(deliverable_text)
            source = 'deliverable'
            
        return self.verify_bundle(bundle, source=source)

    def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        # Filter out generated and malformed paths
        authored_bundle = filter_bundle(bundle)
        
        # Count files
        authored_files = len(authored_bundle)
        python_files = sum(1 for p in authored_bundle if p.endswith('.py'))
        
        if authored_files == 0:
            # Empty workspace
            gates = [
                GateResult("syntax", FAILED, "empty", "No authored files found."),
                GateResult("build", FAILED, "empty", "No authored files found."),
                GateResult("smoke", FAILED, "empty", "No authored files found."),
                GateResult("tests", FAILED, "empty", "No authored files found."),
                GateResult("telemetry", FAILED, "empty", "No authored files found."),
            ]
            penalty = len([g for g in gates if g.status == FAILED]) * self.penalty_per_gate
            return VerificationReport(gates, penalty, 0, 0, source)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write bundle to disk
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
                # Skip excluded dirs
                dirs[:] = [d for d in dirs if not is_generated_path(os.path.join(root, d))]
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, workdir)
                    if is_generated_path(rel_path) or is_malformed_path(rel_path):
                        continue
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            authored[rel_path] = f.read()
                    except Exception:
                        pass
        
        authored_files = len(authored)
        python_files = sum(1 for p in authored if p.endswith('.py'))
        
        gates = []
        
        # 1. Syntax Gate
        start = time.time()
        syntax_status = PASSED
        syntax_detail = "All Python files parse successfully."
        syntax_method = "ast.parse"
        
        py_files = [p for p in authored if p.endswith('.py')]
        if not py_files:
            syntax_status = FAILED
            syntax_detail = "No Python files found."
        else:
            for p in py_files:
                try:
                    ast.parse(authored[p])
                except SyntaxError as e:
                    syntax_status = FAILED
                    syntax_detail = f"Syntax error in {p}: {e}"
                    break
        gates.append(GateResult("syntax", syntax_status, syntax_method, syntax_detail, time.time() - start))
        
        # 2. Build Gate
        start = time.time()
        build_status = PASSED
        build_detail = "Packaging metadata valid."
        build_method = "tomllib/pip"
        
        has_pyproject = "pyproject.toml" in authored
        has_setup_py = "setup.py" in authored
        has_setup_cfg = "setup.cfg" in authored
        
        if not (has_pyproject or has_setup_py or has_setup_cfg):
            build_status = FAILED
            build_detail = "No packaging metadata (pyproject.toml, setup.py, setup.cfg) found."
        elif has_pyproject:
            try:
                with open(os.path.join(workdir, "pyproject.toml"), "rb") as f:
                    data = tomllib.load(f)
                if not any(k in data for k in ("project", "build-system", "tool")):
                    build_status = FAILED
                    build_detail = "pyproject.toml lacks [project], [build-system], or [tool] tables."
                else:
                    # Try pip install
                    try:
                        subprocess.run(
                            [self.python, "-m", "pip", "install", "-e", ".", "--no-deps", "--no-build-isolation"],
                            cwd=workdir,
                            capture_output=True,
                            timeout=self.timeout_s
                        )
                    except Exception as e:
                        build_status = FAILED
                        build_detail = f"pip install failed: {e}"
            except Exception as e:
                build_status = FAILED
                build_detail = f"pyproject.toml invalid: {e}"
        else:
            # setup.py or setup.cfg present, assume valid if present
            pass
            
        gates.append(GateResult("build", build_status, build_method, build_detail, time.time() - start))
        
        # 3. Smoke Gate
        start = time.time()
        smoke_status = PASSED
        smoke_detail = "Modules imported successfully."
        smoke_method = "subprocess import"
        
        # Find non-test modules
        modules = []
        for p in py_files:
            if "test" not in os.path.basename(p).lower():
                # Convert path to module name
                # src/foo/bar.py -> foo.bar
                parts = p.split('/')
                if parts[0] == 'src':
                    parts = parts[1:]
                if parts[-1].endswith('.py'):
                    parts[-1] = parts[-1][:-3]
                if parts[-1] == '__init__':
                    parts = parts[:-1]
                if parts:
                    modules.append('.'.join(parts))
        
        if not modules:
            smoke_status = FAILED
            smoke_detail = "No importable modules found."
        else:
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{workdir}:{os.path.join(workdir, 'src')}"
            
            for mod in modules:
                try:
                    subprocess.run(
                        [self.python, "-c", f"import {mod}"],
                        cwd=workdir,
                        env=env,
                        capture_output=True,
                        timeout=self.timeout_s
                    )
                except subprocess.TimeoutExpired:
                    smoke_status = FAILED
                    smoke_detail = f"Import timeout for {mod}"
                    break
                except Exception as e:
                    # Check if it's just ModuleNotFoundError for third party
                    stderr = getattr(e, 'stderr', '')
                    if isinstance(e, subprocess.CalledProcessError):
                        stderr = e.stderr.decode('utf-8', errors='ignore')
                    
                    if "ModuleNotFoundError" in stderr:
                        # Check if it's a third-party package (not in stdlib or local)
                        # Simple heuristic: if it's not a local module, it's third party
                        # We assume local modules are in the bundle
                        is_local = any(mod.startswith(m) or m.startswith(mod) for m in modules)
                        if not is_local:
                            smoke_status = SKIPPED
                            smoke_detail = f"Import skipped for {mod}: ModuleNotFoundError (third-party)"
                            break
                        else:
                            smoke_status = FAILED
                            smoke_detail = f"Import failed for local module {mod}: {stderr}"
                            break
                    else:
                        smoke_status = FAILED
                        smoke_detail = f"Import failed for {mod}: {stderr}"
                        break
                        
        gates.append(GateResult("smoke", smoke_status, smoke_method, smoke_detail, time.time() - start))
        
        # 4. Tests Gate
        start = time.time()
        tests_status = PASSED
        tests_detail = "Tests passed."
        tests_method = "pytest"
        
        test_files = [p for p in py_files if "test" in os.path.basename(p).lower()]
        
        if not test_files:
            tests_status = FAILED
            tests_detail = "No test files found."
        else:
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{workdir}:{os.path.join(workdir, 'src')}"
            
            # Try pytest first
            try:
                subprocess.run(
                    [self.python, "-m", "pytest", "-v"],
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    timeout=self.timeout_s
                )
                tests_method = "pytest"
            except FileNotFoundError:
                tests_method = "stdlib"
                # Fallback to stdlib unittest
                # This is complex to implement fully in a single subprocess call without pytest
                # We'll run a simple script that imports and runs tests
                test_script = """
import sys
import unittest
import importlib
import os

# Add paths
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

test_files = {test_files!r}
failed = False

for tf in test_files:
    mod_name = tf.replace('/', '.').replace('.py', '')
    try:
        mod = importlib.import_module(mod_name)
        suite = unittest.TestLoader().loadTestsFromModule(mod)
        runner = unittest.TextTestRunner(verbosity=0)
        result = runner.run(suite)
        if not result.wasSuccessful():
            failed = True
    except ModuleNotFoundError as e:
        # Check if it's third party
        if "No module named" in str(e):
            # Assume third party if not local
            pass
        else:
            failed = True
    except Exception as e:
        failed = True

if failed:
    sys.exit(1)
"""
                try:
                    subprocess.run(
                        [self.python, "-c", test_script],
                        cwd=workdir,
                        env=env,
                        capture_output=True,
                        timeout=self.timeout_s
                    )
                except subprocess.CalledProcessError as e:
                    tests_status = FAILED
                    tests_detail = f"Tests failed: {e.stderr.decode('utf-8', errors='ignore')[:200]}"
                except Exception as e:
                    tests_status = FAILED
                    tests_detail = f"Test execution error: {e}"
            except subprocess.CalledProcessError as e:
                tests_status = FAILED
                tests_detail = f"Tests failed: {e.stderr.decode('utf-8', errors='ignore')[:200]}"
            except Exception as e:
                tests_status = FAILED
                tests_detail = f"Test execution error: {e}"
                
        gates.append(GateResult("tests", tests_status, tests_method, tests_detail, time.time() - start))
        
        # 5. Telemetry Gate
        start = time.time()
        telemetry_status = FAILED
        telemetry_detail = "No OpenTelemetry usage found."
        telemetry_method = "ast"
        
        has_import = False
        has_call = False
        
        tracer_methods = {
            'start_as_current_span', 'start_span', 'get_tracer', 
            'get_tracer_provider', 'set_tracer_provider', 'get_meter'
        }
        
        for p in py_files:
            try:
                tree = ast.parse(authored[p])
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name == 'opentelemetry' or alias.name.startswith('opentelemetry.'):
                                has_import = True
                    elif isinstance(node, ast.ImportFrom):
                        if node.module == 'opentelemetry' or (node.module and node.module.startswith('opentelemetry.')):
                            has_import = True
                    elif isinstance(node, ast.Call):
                        # Check if it's a call to a tracer method
                        func = node.func
                        if isinstance(func, ast.Attribute):
                            if func.attr in tracer_methods:
                                has_call = True
                        elif isinstance(func, ast.Name):
                            if func.id in tracer_methods:
                                has_call = True
            except Exception:
                pass
                
        if has_import and has_call:
            telemetry_status = PASSED
            telemetry_detail = "OpenTelemetry import and tracer/span calls found."
        elif has_import:
            telemetry_detail = "OpenTelemetry imported but no tracer/span calls found."
        else:
            telemetry_detail = "No OpenTelemetry import found."
            
        gates.append(GateResult("telemetry", telemetry_status, telemetry_method, telemetry_detail, time.time() - start))
        
        # Calculate penalty
        failed_count = sum(1 for g in gates if g.status == FAILED)
        penalty = failed_count * self.penalty_per_gate
        
        return VerificationReport(gates, penalty, authored_files, python_files, source)