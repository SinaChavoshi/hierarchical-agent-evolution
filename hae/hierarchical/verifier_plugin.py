"""Pluggable Workspace Verification Gates for Generalized HAE (`L0-Micro` & `L0-Macro`).

Decouples HAE's hierarchical verification architecture (`L0 -> L1 -> L2`) from
SWE-bench-specific assumptions so any repository, programming language, or
multi-milestone system build can be verified deterministically:

  - `WorkspaceVerifierPlugin` (ABC): uniform interface returning a structured
    `VerificationGateResult` with `score` in `[0.0, 1.0]`.
  - `PytestVerifierPlugin`: runs Python syntax compilation (`py_compile`),
    optional standalone reproduction/acceptance scripts, `pytest` target suites,
    and adversarial falsification suites.
  - `ShellCommandVerifierPlugin`: language-agnostic verifier for arbitrary build
    and test toolchains (Rust `cargo test`, Go `go test`, C++ `cmake`/`ctest`,
    TypeScript `npm test`, or custom milestone acceptance commands) with
    configurable `build_cmd`, `test_cmd`, `repro_cmd`, and `falsifier_cmd`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from hae.swebench.executor import CommandExecutor, SCRATCH_DIR


def compute_gate_score(
    compile_ok: bool,
    repro_ok: bool,
    tests_ok: bool,
    falsifier_ok: bool,
    new_failures: int = 0,
    diff_lines: int = 0,
    diff_bloat_threshold: int = 120,
) -> float:
    """Compute a normalized verification score in `[0.0, 1.0]`."""
    if not compile_ok:
        return 0.05
    score = 0.15
    if repro_ok:
        score += 0.30
    if tests_ok:
        score += 0.25
    else:
        partial = max(0.0, 0.20 - 0.05 * min(4, max(1, int(new_failures))))
        score += partial
    if falsifier_ok:
        score += 0.30
    elif repro_ok:
        score += 0.08

    if diff_lines > 0 and diff_bloat_threshold > 0:
        bloat_penalty = min(0.12, 0.0005 * max(0, int(diff_lines) - int(diff_bloat_threshold)))
        score -= bloat_penalty
    return max(0.0, min(1.0, round(score, 4)))


@dataclass
class VerificationGateResult:
    """Structured outcome of running a `WorkspaceVerifierPlugin` on a workspace."""

    compile_ok: bool = True
    repro_ok: bool = True
    tests_ok: bool = True
    falsifier_ok: bool = True
    new_failures: int = 0
    summary: str = ""
    score: Optional[float] = None

    def __post_init__(self) -> None:
        if self.score is None:
            self.score = compute_gate_score(
                compile_ok=self.compile_ok,
                repro_ok=self.repro_ok,
                tests_ok=self.tests_ok,
                falsifier_ok=self.falsifier_ok,
                new_failures=self.new_failures,
            )
        else:
            self.score = max(0.0, min(1.0, float(self.score)))

    @property
    def ok(self) -> bool:
        """True iff all verification gates passed with zero new failures."""
        return bool(
            self.compile_ok
            and self.repro_ok
            and self.tests_ok
            and self.falsifier_ok
            and self.new_failures == 0
        )

    @property
    def is_certified(self) -> bool:
        return self.ok

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compile_ok": bool(self.compile_ok),
            "repro_ok": bool(self.repro_ok),
            "tests_ok": bool(self.tests_ok),
            "falsifier_ok": bool(self.falsifier_ok),
            "new_failures": int(self.new_failures),
            "summary": str(self.summary),
            "score": float(self.score if self.score is not None else 0.0),
            "ok": self.ok,
        }


def _truncate(text: str, limit: int = 1500) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    half = (limit - 60) // 2
    return f"{text[:half]}\n... [truncated {len(text) - 2 * half} chars] ...\n{text[-half:]}"


def _parse_failure_count(output: str, default_on_error: int = 1) -> int:
    m = re.search(r"(\d+)\s+(?:failed|error|failures)", output or "", flags=re.IGNORECASE)
    if m:
        return max(1, int(m.group(1)))
    return default_on_error


class WorkspaceVerifierPlugin(ABC):
    """Abstract base class for pluggable workspace verification gates."""

    @abstractmethod
    def verify(
        self,
        executor: CommandExecutor,
        changed_files: Optional[Sequence[str]] = None,
    ) -> VerificationGateResult:
        """Verify the current state of `executor` and return a `VerificationGateResult`."""

    def describe(self) -> str:
        return type(self).__name__


class PytestVerifierPlugin(WorkspaceVerifierPlugin):
    """Python verifier running `py_compile`, optional reproduction script, `pytest`, and falsifier suite."""

    def __init__(
        self,
        test_targets: Optional[Sequence[str]] = None,
        repro_script: Optional[str] = f"{SCRATCH_DIR}/reproduce_issue.py",
        falsifier_script: Optional[str] = f"{SCRATCH_DIR}/falsification_suite.py",
        require_repro: bool = False,
        require_falsifier: bool = False,
        timeout_s: float = 60.0,
        diff_bloat_threshold: int = 120,
    ) -> None:
        self.test_targets = list(test_targets) if test_targets else []
        self.repro_script = repro_script
        self.falsifier_script = falsifier_script
        self.require_repro = require_repro
        self.require_falsifier = require_falsifier
        self.timeout_s = float(timeout_s)
        self.diff_bloat_threshold = int(diff_bloat_threshold)

    def verify(
        self,
        executor: CommandExecutor,
        changed_files: Optional[Sequence[str]] = None,
    ) -> VerificationGateResult:
        files = list(changed_files) if changed_files is not None else executor.changed_files()
        py_files = [
            f for f in files
            if f.endswith(".py")
            and not f.startswith(SCRATCH_DIR + "/")
            and executor.exists(f)
        ]
        notes: List[str] = []
        env = {"PYTHONPATH": executor.root}

        # 1. Syntax check (`py_compile`)
        compile_ok = True
        if py_files:
            res_c = executor.run(
                [executor.python, "-m", "py_compile", *py_files],
                cwd=executor.root,
                env=env,
                timeout_s=min(30.0, self.timeout_s),
            )
            if not res_c.ok:
                compile_ok = False
                out_c = ((res_c.stdout or "") + "\n" + (res_c.stderr or "")).strip()
                notes.append(f"py_compile FAILED: {_truncate(out_c, 600)}")
            else:
                notes.append(f"py_compile OK ({len(py_files)} file(s))")
        else:
            notes.append("py_compile OK (0 changed .py files)")

        # 2. Optional reproduction / acceptance script
        repro_ok = True
        if not compile_ok:
            repro_ok = False
        elif self.repro_script and executor.exists(self.repro_script):
            res_r = executor.run(
                [executor.python, self.repro_script],
                cwd=executor.root,
                env=env,
                timeout_s=self.timeout_s,
            )
            repro_ok = res_r.ok
            if repro_ok:
                notes.append(f"repro `{self.repro_script}` PASSED")
            else:
                out_r = ((res_r.stdout or "") + "\n" + (res_r.stderr or "")).strip()
                notes.append(f"repro `{self.repro_script}` FAILED (rc={res_r.rc}): {_truncate(out_r, 600)}")
        elif self.require_repro:
            repro_ok = False
            notes.append(f"repro `{self.repro_script}` MISSING (required)")

        # 3. Pytest target suite(s)
        tests_ok = True
        new_failures = 0
        if not compile_ok:
            tests_ok = False
            new_failures = 1
        else:
            targets = list(self.test_targets)
            res_t = executor.run(
                [
                    executor.python,
                    "-m",
                    "pytest",
                    "-q",
                    "--tb=short",
                    "--disable-warnings",
                    *targets,
                ],
                cwd=executor.root,
                env=env,
                timeout_s=self.timeout_s,
            )
            raw_t = ((res_t.stdout or "") + "\n" + (res_t.stderr or "")).strip()
            if "No module named pytest" in raw_t:
                fallback_script = (
                    "import glob, importlib.util, os, sys, traceback, unittest\n"
                    "targets = sys.argv[1:]\n"
                    "files = []\n"
                    "if not targets:\n"
                    "    for root_d, _, fnames in os.walk('.'):\n"
                    "        if '.hae' in root_d.split(os.sep) or '.git' in root_d.split(os.sep):\n"
                    "            continue\n"
                    "        for fn in sorted(fnames):\n"
                    "            if (fn.startswith('test_') or fn.endswith('_test.py')) and fn.endswith('.py'):\n"
                    "                files.append(os.path.join(root_d, fn))\n"
                    "else:\n"
                    "    for t in targets:\n"
                    "        t_path = t.split('::')[0]\n"
                    "        if os.path.isdir(t_path):\n"
                    "            for root_d, _, fnames in os.walk(t_path):\n"
                    "                for fn in sorted(fnames):\n"
                    "                    if (fn.startswith('test_') or fn.endswith('_test.py')) and fn.endswith('.py'):\n"
                    "                        files.append(os.path.join(root_d, fn))\n"
                    "        elif os.path.isfile(t_path):\n"
                    "            files.append(t_path)\n"
                    "passed, failed = 0, 0\n"
                    "for idx, fp in enumerate(files):\n"
                    "    spec = importlib.util.spec_from_file_location(f'_hae_test_{idx}', fp)\n"
                    "    mod = importlib.util.module_from_spec(spec)\n"
                    "    sys.modules[spec.name] = mod\n"
                    "    try:\n"
                    "        spec.loader.exec_module(mod)\n"
                    "    except Exception:\n"
                    "        failed += 1\n"
                    "        traceback.print_exc()\n"
                    "        continue\n"
                    "    suite = unittest.defaultTestLoader.loadTestsFromModule(mod)\n"
                    "    if suite.countTestCases() > 0:\n"
                    "        res = unittest.TextTestRunner(verbosity=0).run(suite)\n"
                    "        bad = len(res.failures) + len(res.errors)\n"
                    "        failed += bad\n"
                    "        passed += max(0, res.testsRun - bad)\n"
                    "    for name, obj in list(vars(mod).items()):\n"
                    "        if name.startswith('test_') and callable(obj) and not isinstance(obj, type):\n"
                    "            try:\n"
                    "                obj()\n"
                    "                passed += 1\n"
                    "            except Exception:\n"
                    "                failed += 1\n"
                    "                traceback.print_exc()\n"
                    "print(f'{failed} failed, {passed} passed')\n"
                    "sys.exit(1 if failed > 0 else 0)\n"
                )
                res_t = executor.run(
                    [executor.python, "-c", fallback_script, *targets],
                    cwd=executor.root,
                    env=env,
                    timeout_s=self.timeout_s,
                )
                raw_t = ((res_t.stdout or "") + "\n" + (res_t.stderr or "")).strip()

            # Pytest exit code 5 means "no tests collected" when no explicit targets were given
            if res_t.ok or (res_t.rc == 5 and not targets):
                tests_ok = True
                notes.append("pytest PASSED")
            else:
                tests_ok = False
                new_failures = _parse_failure_count(raw_t, default_on_error=1)
                notes.append(f"pytest FAILED (rc={res_t.rc}, failures={new_failures}): {_truncate(raw_t, 800)}")

        # 4. Adversarial falsifier script
        falsifier_ok = True
        if not compile_ok:
            falsifier_ok = False
        elif self.falsifier_script and executor.exists(self.falsifier_script):
            res_f = executor.run(
                [executor.python, self.falsifier_script],
                cwd=executor.root,
                env=env,
                timeout_s=self.timeout_s,
            )
            falsifier_ok = res_f.ok
            if falsifier_ok:
                notes.append(f"falsifier `{self.falsifier_script}` PASSED")
            else:
                out_f = ((res_f.stdout or "") + "\n" + (res_f.stderr or "")).strip()
                notes.append(
                    f"falsifier `{self.falsifier_script}` FAILED (rc={res_f.rc}): {_truncate(out_f, 600)}"
                )
        elif self.require_falsifier:
            falsifier_ok = False
            notes.append(f"falsifier `{self.falsifier_script}` MISSING (required)")

        diff_lines = len(executor.diff().splitlines())
        score = compute_gate_score(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            diff_lines=diff_lines,
            diff_bloat_threshold=self.diff_bloat_threshold,
        )
        return VerificationGateResult(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            summary="; ".join(notes),
            score=score,
        )


class ShellCommandVerifierPlugin(WorkspaceVerifierPlugin):
    """Language-agnostic verifier for any repository or build system.

    Supports Rust (`cargo check` / `cargo test`), Go (`go build` / `go test`),
    C/C++ (`cmake` / `ctest`), TypeScript/Node (`npm run build` / `npm test`),
    Python acceptance commands, or arbitrary shell pipelines.
    """

    def __init__(
        self,
        build_cmd: Optional[str] = None,
        test_cmd: Optional[str] = None,
        falsifier_cmd: Optional[str] = None,
        repro_cmd: Optional[str] = None,
        timeout_s: float = 120.0,
        env: Optional[Mapping[str, str]] = None,
        diff_bloat_threshold: int = 500,
    ) -> None:
        self.build_cmd = (build_cmd or "").strip() or None
        self.test_cmd = (test_cmd or "").strip() or None
        self.falsifier_cmd = (falsifier_cmd or "").strip() or None
        self.repro_cmd = (repro_cmd or "").strip() or None
        self.timeout_s = float(timeout_s)
        self.env = dict(env) if env else {}
        self.diff_bloat_threshold = int(diff_bloat_threshold)

    def _exec_shell(self, executor: CommandExecutor, cmd: str) -> Any:
        py_bin_dir = os.path.dirname(executor.python or "")
        merged_env = dict(self.env)
        merged_env.setdefault("PYTHONPATH", executor.root)
        path_prefix = f'export PATH="{py_bin_dir}:$PATH"; ' if py_bin_dir else ""
        wrapped = f'{path_prefix}export PYTHONPATH="{executor.root}:${{PYTHONPATH:-}}"; {cmd}'
        return executor.run(
            ["bash", "-lc", wrapped],
            cwd=executor.root,
            env=merged_env,
            timeout_s=self.timeout_s,
        )

    def verify(
        self,
        executor: CommandExecutor,
        changed_files: Optional[Sequence[str]] = None,
    ) -> VerificationGateResult:
        notes: List[str] = []

        # 1. Build / compile gate
        compile_ok = True
        if self.build_cmd:
            res_b = self._exec_shell(executor, self.build_cmd)
            compile_ok = res_b.ok
            if compile_ok:
                notes.append(f"build `{self.build_cmd}` PASSED")
            else:
                out_b = ((res_b.stdout or "") + "\n" + (res_b.stderr or "")).strip()
                notes.append(f"build `{self.build_cmd}` FAILED (rc={res_b.rc}): {_truncate(out_b, 700)}")
        else:
            notes.append("build OK (no build_cmd configured)")

        # 2. Reproduction / acceptance command gate
        repro_ok = compile_ok
        if compile_ok and self.repro_cmd:
            res_r = self._exec_shell(executor, self.repro_cmd)
            repro_ok = res_r.ok
            if repro_ok:
                notes.append(f"repro `{self.repro_cmd}` PASSED")
            else:
                out_r = ((res_r.stdout or "") + "\n" + (res_r.stderr or "")).strip()
                notes.append(f"repro `{self.repro_cmd}` FAILED (rc={res_r.rc}): {_truncate(out_r, 700)}")

        # 3. Primary test / acceptance command gate
        tests_ok = compile_ok
        new_failures = 0 if compile_ok else 1
        if compile_ok and self.test_cmd:
            res_t = self._exec_shell(executor, self.test_cmd)
            tests_ok = res_t.ok
            if tests_ok:
                new_failures = 0
                notes.append(f"test `{self.test_cmd}` PASSED")
            else:
                out_t = ((res_t.stdout or "") + "\n" + (res_t.stderr or "")).strip()
                new_failures = _parse_failure_count(out_t, default_on_error=1)
                notes.append(
                    f"test `{self.test_cmd}` FAILED (rc={res_t.rc}, failures={new_failures}): {_truncate(out_t, 800)}"
                )
            if self.repro_cmd is None:
                repro_ok = tests_ok

        # 4. Adversarial falsifier command gate
        falsifier_ok = compile_ok
        if compile_ok:
            if self.falsifier_cmd:
                res_f = self._exec_shell(executor, self.falsifier_cmd)
                falsifier_ok = res_f.ok
                if falsifier_ok:
                    notes.append(f"falsifier `{self.falsifier_cmd}` PASSED")
                else:
                    out_f = ((res_f.stdout or "") + "\n" + (res_f.stderr or "")).strip()
                    notes.append(
                        f"falsifier `{self.falsifier_cmd}` FAILED (rc={res_f.rc}): {_truncate(out_f, 700)}"
                    )
            elif executor.exists(f"{SCRATCH_DIR}/falsification_suite.py"):
                res_f = executor.run(
                    [executor.python, f"{SCRATCH_DIR}/falsification_suite.py"],
                    cwd=executor.root,
                    env={"PYTHONPATH": executor.root},
                    timeout_s=self.timeout_s,
                )
                falsifier_ok = res_f.ok
                if falsifier_ok:
                    notes.append("falsifier `.hae/falsification_suite.py` PASSED")
                else:
                    out_f = ((res_f.stdout or "") + "\n" + (res_f.stderr or "")).strip()
                    notes.append(
                        f"falsifier `.hae/falsification_suite.py` FAILED (rc={res_f.rc}): {_truncate(out_f, 700)}"
                    )

        diff_lines = len(executor.diff().splitlines())
        score = compute_gate_score(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            diff_lines=diff_lines,
            diff_bloat_threshold=self.diff_bloat_threshold,
        )
        return VerificationGateResult(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            summary="; ".join(notes),
            score=score,
        )
