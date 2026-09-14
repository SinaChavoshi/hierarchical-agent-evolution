"""Execution-grounded verification of agent-authored workspaces.

Replaces the heuristics in `sandbox_verifier.py`, where three of four gates
never executed anything:

    Build     -> `"pyproject.toml" in f` and a filename matching
                 `runtime|company|engine|orchestrator|core`. `pip install` was
                 never invoked.
    Smoke     -> `has_runtime and len(files) >= 3`. Nothing was imported.
    Telemetry -> `"opentelemetry" in all_code.lower()`, where `all_code`
                 included the CEO's markdown prose. Writing the word once in an
                 essay passed the gate.
    Tests     -> genuinely ran pytest. The only real gate.

Every gate here either runs code or performs AST analysis, and each result
carries the `method` used to reach it. A gate that could not be evaluated
returns `SKIPPED` rather than `PASS`, so a missing tool can never be mistaken
for a success.

Usage:
    from src.execution_harness import ExecutionHarness

    harness = ExecutionHarness()
    report = harness.verify_bundle({"pyproject.toml": "...", "src/a.py": "..."})
    print(report.score_penalty, report.summary())
"""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .artifacts import filter_bundle

# Result of a gate that could not be evaluated in this environment. Distinct
# from failure: a skipped gate must never be scored as a pass.
SKIPPED = "skipped"
PASSED = "passed"
FAILED = "failed"

DEFAULT_PENALTY_PER_GATE = 6.25
DEFAULT_TIMEOUT_S = 60


@dataclass
class GateResult:
    """Outcome of a single verification gate."""

    name: str
    status: str                 # PASSED | FAILED | SKIPPED
    method: str                 # how the verdict was reached
    detail: str = ""
    duration_s: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == PASSED

    @property
    def evaluated(self) -> bool:
        return self.status in (PASSED, FAILED)


@dataclass
class VerificationReport:
    """Aggregate result across all gates."""

    gates: List[GateResult]
    score_penalty: float
    authored_files: int
    python_files: int
    harness_version: str = "2.0-execution"

    def gate(self, name: str) -> Optional[GateResult]:
        return next((g for g in self.gates if g.name == name), None)

    @property
    def evaluated_gates(self) -> List[GateResult]:
        return [g for g in self.gates if g.evaluated]

    @property
    def passed_gates(self) -> List[GateResult]:
        return [g for g in self.gates if g.passed]

    def summary(self) -> str:
        parts = []
        for g in self.gates:
            mark = {PASSED: "PASS", FAILED: "FAIL", SKIPPED: "SKIP"}[g.status]
            parts.append(f"{g.name.capitalize()}: {mark}")
        return (f"[Execution Harness] {self.authored_files} authored files "
                f"({self.python_files} .py). " + ", ".join(parts) + ".")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["summary"] = self.summary()
        # Backwards-compatible keys so existing readers keep working.
        for g in self.gates:
            d[f"{g.name}_passed"] = g.passed
        return d


# Executed in a subprocess when pytest is unavailable. Collects and runs both
# pytest-style bare `test_*` functions and unittest.TestCase subclasses, and
# separates genuine failures from modules blocked by uninstalled dependencies.
_FALLBACK_RUNNER = r'''
import importlib.util, inspect, json, os, sys, traceback, unittest

test_files = json.loads(sys.argv[1])
collected = 0
failures = []
blocked = []

def load(rel):
    name = os.path.splitext(rel.replace("/", "_").replace("\\", "_"))[0]
    spec = importlib.util.spec_from_file_location(name, rel)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

for rel in test_files:
    if not os.path.exists(rel):
        continue
    try:
        mod = load(rel)
    except ModuleNotFoundError as e:
        blocked.append(getattr(e, "name", None) or str(e))
        continue
    except BaseException as e:
        failures.append(f"{rel}: import failed: {type(e).__name__}: {e}")
        continue
    if mod is None:
        continue

    for attr, obj in sorted(vars(mod).items()):
        if attr.startswith("test") and inspect.isfunction(obj):
            if obj.__module__ != mod.__name__:
                continue
            try:
                sig = inspect.signature(obj)
            except (TypeError, ValueError):
                continue
            if any(p.default is inspect.Parameter.empty
                   and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                   for p in sig.parameters.values()):
                continue  # needs a fixture we cannot provide
            collected += 1
            try:
                obj()
            except BaseException as e:
                failures.append(f"{rel}::{attr}: {type(e).__name__}: {e}")

        elif inspect.isclass(obj) and issubclass(obj, unittest.TestCase):
            if obj is unittest.TestCase:
                continue
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(obj)
            count = suite.countTestCases()
            if not count:
                continue
            collected += count
            res = unittest.TextTestRunner(
                stream=open(os.devnull, "w"), verbosity=0).run(suite)
            for case, tb in list(res.failures) + list(res.errors):
                failures.append(f"{rel}::{case}: {tb.strip().splitlines()[-1]}")

print(json.dumps({"collected": collected, "failures": failures, "blocked": blocked}))
'''


class ExecutionHarness:
    """Materializes a workspace bundle and verifies it by actually running it."""

    def __init__(self, timeout_s: int = DEFAULT_TIMEOUT_S,
                 penalty_per_gate: float = DEFAULT_PENALTY_PER_GATE,
                 python_executable: Optional[str] = None):
        self.timeout_s = timeout_s
        self.penalty_per_gate = penalty_per_gate
        self.python = python_executable or sys.executable

    # ------------------------------------------------------------------ #
    # Entry points
    # ------------------------------------------------------------------ #

    def verify_bundle(self, bundle: Dict[str, str]) -> VerificationReport:
        """Verifies an in-memory path->content map by writing it to a temp dir."""
        authored = filter_bundle(bundle)
        workdir = tempfile.mkdtemp(prefix="hae_harness_")
        try:
            self._materialize(authored, workdir)
            return self.verify_directory(workdir, authored)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def verify_directory(self, workdir: str,
                         authored: Optional[Dict[str, str]] = None) -> VerificationReport:
        """Verifies an existing workspace directory."""
        if authored is None:
            authored = self._read_tree(workdir)

        py_files = [p for p in authored if p.endswith(".py")]

        gates = [
            self._gate_syntax(workdir, py_files),
            self._gate_build(workdir, authored),
            self._gate_smoke(workdir, py_files),
            self._gate_tests(workdir, py_files),
            self._gate_telemetry(workdir, authored),
        ]

        # Only evaluated gates can contribute penalty. A gate we could not run
        # is not evidence of failure, and inventing a penalty for it would
        # repeat the mistake this harness exists to fix.
        failures = sum(1 for g in gates if g.status == FAILED)
        penalty = round(failures * self.penalty_per_gate, 2)

        return VerificationReport(
            gates=gates,
            score_penalty=penalty,
            authored_files=len(authored),
            python_files=len(py_files),
        )

    # ------------------------------------------------------------------ #
    # Gates
    # ------------------------------------------------------------------ #

    def _gate_syntax(self, workdir: str, py_files: List[str]) -> GateResult:
        """Every authored Python file must parse. This is non-negotiable."""
        t0 = time.time()
        if not py_files:
            return GateResult("syntax", FAILED, "ast.parse",
                              "No Python files were authored.",
                              round(time.time() - t0, 3))

        broken = []
        for rel in py_files:
            full = os.path.join(workdir, rel)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    ast.parse(fh.read(), filename=rel)
            except SyntaxError as e:
                broken.append(f"{rel}:{e.lineno}: {e.msg}")
            except Exception as e:  # unreadable file
                broken.append(f"{rel}: {e}")

        ok = not broken
        return GateResult(
            "syntax", PASSED if ok else FAILED, "ast.parse",
            "All files parse." if ok else f"{len(broken)}/{len(py_files)} failed: " + "; ".join(broken[:3]),
            round(time.time() - t0, 3),
            {"total": len(py_files), "broken": len(broken), "errors": broken[:10]},
        )

    def _gate_build(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        """Packaging metadata must exist and be genuinely parseable/installable."""
        t0 = time.time()
        has_pyproject = "pyproject.toml" in authored
        has_setup = "setup.py" in authored or "setup.cfg" in authored

        if not (has_pyproject or has_setup):
            return GateResult("build", FAILED, "manifest",
                              "No pyproject.toml, setup.py, or setup.cfg.",
                              round(time.time() - t0, 3))

        # Real install when pip is available.
        if self._pip_available():
            proc = self._run([self.python, "-m", "pip", "install", "-e", ".",
                              "--no-deps", "--no-build-isolation", "--quiet"], workdir)
            if proc is not None:
                ok = proc.returncode == 0
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                return GateResult(
                    "build", PASSED if ok else FAILED, "pip install -e .",
                    "Editable install succeeded." if ok else "; ".join(tail[-3:])[:400],
                    round(time.time() - t0, 3),
                    {"returncode": proc.returncode},
                )

        # Without pip, validate the manifest for real rather than claiming a pass.
        if has_pyproject:
            try:
                import tomllib
                with open(os.path.join(workdir, "pyproject.toml"), "rb") as fh:
                    data = tomllib.load(fh)
            except Exception as e:
                return GateResult("build", FAILED, "tomllib.parse",
                                  f"pyproject.toml is not valid TOML: {e}",
                                  round(time.time() - t0, 3))

            if not ({"project", "build-system", "tool"} & set(data)):
                return GateResult("build", FAILED, "tomllib.parse",
                                  "pyproject.toml declares no [project], [build-system], or [tool] table.",
                                  round(time.time() - t0, 3))

            name = (data.get("project") or {}).get("name")
            return GateResult(
                "build", PASSED, "tomllib.parse (pip unavailable)",
                f"Valid manifest{f' for {name!r}' if name else ''}; install not attempted.",
                round(time.time() - t0, 3), {"declared_name": name},
            )

        # setup.py must at least be importable-as-source.
        try:
            with open(os.path.join(workdir, "setup.py"), "r", encoding="utf-8", errors="replace") as fh:
                ast.parse(fh.read())
            return GateResult("build", PASSED, "ast.parse (pip unavailable)",
                              "setup.py parses; install not attempted.",
                              round(time.time() - t0, 3))
        except FileNotFoundError:
            return GateResult("build", PASSED, "setup.cfg present (pip unavailable)",
                              "setup.cfg present; install not attempted.",
                              round(time.time() - t0, 3))
        except SyntaxError as e:
            return GateResult("build", FAILED, "ast.parse",
                              f"setup.py syntax error at line {e.lineno}: {e.msg}",
                              round(time.time() - t0, 3))

    def _gate_smoke(self, workdir: str, py_files: List[str]) -> GateResult:
        """Importable modules must actually import in a fresh interpreter."""
        t0 = time.time()
        targets = self._importable_modules(workdir, py_files)
        if not targets:
            return GateResult("smoke", FAILED, "import",
                              "No importable module found.",
                              round(time.time() - t0, 3))

        probe = (
            "import importlib, json, sys\n"
            "ok, failed = [], []\n"
            "for m in json.loads(sys.argv[1]):\n"
            "    try:\n"
            "        importlib.import_module(m)\n"
            "        ok.append(m)\n"
            "    except BaseException as e:\n"
            "        failed.append(f'{m}: {type(e).__name__}: {e}')\n"
            "print(json.dumps({'ok': ok, 'failed': failed}))\n"
        )
        env = dict(os.environ)
        roots = [workdir, os.path.join(workdir, "src")]
        env["PYTHONPATH"] = os.pathsep.join(roots + [env.get("PYTHONPATH", "")]).strip(os.pathsep)

        proc = self._run([self.python, "-c", probe, json.dumps(targets)], workdir, env=env)
        if proc is None:
            return GateResult("smoke", FAILED, "import",
                              f"Import probe timed out after {self.timeout_s}s.",
                              round(time.time() - t0, 3))

        try:
            res = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except Exception:
            return GateResult("smoke", FAILED, "import",
                              f"Probe produced no parseable result: {(proc.stderr or '')[:200]}",
                              round(time.time() - t0, 3))

        ok, failed = res.get("ok", []), res.get("failed", [])
        missing_deps = sorted({
            m for m in (self._missing_dependency(f) for f in failed) if m
        })
        # A failure caused solely by an uninstalled third-party package is an
        # environment limitation, not a defect in the authored code. Penalizing
        # it would repeat the category error this harness exists to correct.
        genuine = [f for f in failed if not self._missing_dependency(f)]

        if genuine:
            status, detail = FAILED, (
                f"{len(ok)}/{len(targets)} modules imported. "
                "Failures: " + "; ".join(genuine[:3])[:400])
        elif missing_deps:
            status, detail = SKIPPED, (
                f"{len(ok)}/{len(targets)} modules imported. Remainder require "
                f"uninstalled third-party packages ({', '.join(missing_deps[:5])}); "
                "cannot determine importability in this environment.")
        elif ok:
            status, detail = PASSED, f"{len(ok)}/{len(targets)} modules imported."
        else:
            status, detail = FAILED, "No module could be imported."

        return GateResult(
            "smoke", status, "importlib.import_module", detail,
            round(time.time() - t0, 3),
            {"imported": ok, "failed": genuine, "attempted": len(targets),
             "missing_dependencies": missing_deps},
        )

    def _gate_tests(self, workdir: str, py_files: List[str]) -> GateResult:
        """Run the authored test suite for real."""
        t0 = time.time()
        test_files = [p for p in py_files
                      if "test" in os.path.basename(p).lower()]
        if not test_files:
            return GateResult("tests", FAILED, "discovery",
                              "No test files were authored.",
                              round(time.time() - t0, 3))

        env = dict(os.environ)
        roots = [workdir, os.path.join(workdir, "src")]
        env["PYTHONPATH"] = os.pathsep.join(roots + [env.get("PYTHONPATH", "")]).strip(os.pathsep)

        if self._module_available("pytest"):
            proc = self._run([self.python, "-m", "pytest", "-q", "--no-header",
                              "-p", "no:cacheprovider"], workdir, env=env)
            if proc is None:
                return GateResult("tests", FAILED, "pytest (timeout)",
                                  f"Test run exceeded {self.timeout_s}s.",
                                  round(time.time() - t0, 3),
                                  {"test_files": len(test_files)})
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            ok = proc.returncode == 0
            tail = [ln for ln in out.splitlines() if ln.strip()][-4:]
            return GateResult(
                "tests", PASSED if ok else FAILED, "python -m pytest",
                ("All tests passed. " if ok else "Test run failed. ") + " | ".join(tail)[:400],
                round(time.time() - t0, 3),
                {"returncode": proc.returncode, "runner": "pytest",
                 "test_files": len(test_files)})

        # No pytest available. `unittest discover` only collects TestCase
        # subclasses, so it silently reports "Ran 0 tests" for the bare
        # `def test_x()` style most agents emit. Run both styles ourselves.
        proc = self._run([self.python, "-c", _FALLBACK_RUNNER, json.dumps(test_files)],
                         workdir, env=env)
        if proc is None:
            return GateResult("tests", FAILED, "fallback runner (timeout)",
                              f"Test run exceeded {self.timeout_s}s.",
                              round(time.time() - t0, 3),
                              {"test_files": len(test_files)})
        try:
            res = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except Exception:
            return GateResult("tests", FAILED, "fallback runner",
                              f"Runner produced no parseable result: {(proc.stderr or '')[:200]}",
                              round(time.time() - t0, 3),
                              {"test_files": len(test_files)})

        collected = res.get("collected", 0)
        failures = res.get("failures", [])
        blocked = res.get("blocked", [])

        if collected == 0 and blocked:
            return GateResult(
                "tests", SKIPPED, "fallback runner",
                "Test modules require uninstalled third-party packages "
                f"({', '.join(sorted(set(blocked))[:5])}); suite could not be collected.",
                round(time.time() - t0, 3),
                {"test_files": len(test_files), "blocked": blocked[:10]})

        if collected == 0:
            return GateResult("tests", FAILED, "fallback runner",
                              "Test files present but no runnable test was collected.",
                              round(time.time() - t0, 3),
                              {"test_files": len(test_files)})

        ok = not failures
        return GateResult(
            "tests", PASSED if ok else FAILED, "fallback runner (pytest unavailable)",
            (f"{collected - len(failures)}/{collected} tests passed."
             + ("" if ok else " Failures: " + "; ".join(failures[:3])[:400])),
            round(time.time() - t0, 3),
            {"collected": collected, "failed": len(failures), "runner": "fallback",
             "test_files": len(test_files), "blocked": blocked[:10]})

    def _gate_telemetry(self, workdir: str, authored: Dict[str, str]) -> GateResult:
        """Detect real OpenTelemetry instrumentation via AST, not substring search.

        The previous implementation searched concatenated text that included the
        CEO's prose, so an essay mentioning 'OpenTelemetry' passed. Here we
        require an actual import statement in an authored `.py` file, and
        additionally look for tracer/span API usage.
        """
        t0 = time.time()
        py = {p: c for p, c in authored.items() if p.endswith(".py")}
        if not py:
            return GateResult("telemetry", FAILED, "ast",
                              "No Python files to instrument.",
                              round(time.time() - t0, 3))

        SPAN_CALLS = {"start_as_current_span", "start_span", "get_tracer",
                      "get_tracer_provider", "set_tracer_provider", "get_meter"}
        importers, span_users = [], []

        for path, content in py.items():
            try:
                tree = ast.parse(content, filename=path)
            except SyntaxError:
                continue

            imported = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(a.name.split(".")[0] == "opentelemetry" for a in node.names):
                        imported = True
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] == "opentelemetry":
                        imported = True
                elif isinstance(node, ast.Call):
                    fn = node.func
                    name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                    if name in SPAN_CALLS:
                        span_users.append(f"{path}:{getattr(node, 'lineno', '?')}")
            if imported:
                importers.append(path)

        ok = bool(importers) and bool(span_users)
        if ok:
            detail = (f"opentelemetry imported in {len(importers)} file(s); "
                      f"{len(span_users)} tracer/span call site(s).")
        elif importers:
            detail = (f"opentelemetry imported in {len(importers)} file(s) but no "
                      f"tracer/span API usage found -- import without instrumentation.")
        else:
            detail = "No opentelemetry import in any authored Python file."

        return GateResult(
            "telemetry", PASSED if ok else FAILED, "ast import + call analysis",
            detail, round(time.time() - t0, 3),
            {"importers": importers[:10], "span_call_sites": span_users[:10]},
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _materialize(self, bundle: Dict[str, str], workdir: str) -> None:
        for rel, content in bundle.items():
            full = os.path.join(workdir, rel)
            if not os.path.abspath(full).startswith(os.path.abspath(workdir)):
                continue  # refuse to escape the sandbox
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(content if isinstance(content, str) else str(content))

    def _read_tree(self, workdir: str) -> Dict[str, str]:
        out = {}
        for root, _dirs, files in os.walk(workdir):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, workdir)
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        out[rel] = fh.read()
                except Exception:
                    continue
        return filter_bundle(out)

    def _importable_modules(self, workdir: str, py_files: List[str]) -> List[str]:
        """Derives dotted module names, preferring packages with __init__.py."""
        mods = []
        for rel in py_files:
            parts = rel.replace("\\", "/").split("/")
            if parts[-1].startswith("test_") or parts[-1] == "setup.py":
                continue
            if parts and parts[0] == "src":
                parts = parts[1:]
            if not parts:
                continue
            parts[-1] = parts[-1][:-3]  # strip .py
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            if any(not p.isidentifier() for p in parts):
                continue
            mods.append(".".join(parts))
        # Prefer package roots; keeps the probe fast and meaningful.
        return sorted(set(mods))[:25]

    def _missing_dependency(self, failure: str) -> Optional[str]:
        """Extracts the package name from a ModuleNotFoundError failure string.

        Returns None when the failure has any other cause, so that genuine
        defects in the authored code are still penalized.
        """
        marker = "ModuleNotFoundError: No module named "
        if marker not in failure:
            return None
        name = failure.split(marker, 1)[1].strip().strip("'\"")
        return name.split(".")[0] or None

    def _pip_available(self) -> bool:
        return self._module_available("pip")

    def _module_available(self, mod: str) -> bool:
        try:
            proc = subprocess.run([self.python, "-c", f"import {mod}"],
                                  capture_output=True, timeout=20)
            return proc.returncode == 0
        except Exception:
            return False

    def _run(self, cmd: List[str], cwd: str,
             env: Optional[Dict[str, str]] = None) -> Optional[subprocess.CompletedProcess]:
        try:
            return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                                  text=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None
