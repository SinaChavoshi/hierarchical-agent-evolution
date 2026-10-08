"""The Evidence Gatekeeper: the only writer of beliefs, and it never talks to an LLM.

Graepel's rule, operationalised: System 1 may propose, System 2 must check,
and the check must be *independent of the proposer*. This module is that
check. It takes a hypothesis with a probe and a prediction, runs the probe
in a sandbox against the firm's own code, compares what happened to what was
predicted, and writes the verdict into the ledger. It has no model, no prompt
and no opinion; the same probe against the same code always yields the same
verdict.

Where probes run
----------------
A probe runs in a throw-away *stage*: a temporary directory containing this
repository's `hae/` tree **with the graded target modules removed**, over
which the firm's workspace is copied. So `import hae.evaluation.harness`
inside a probe resolves to the firm's implementation if it has written one,
and to nothing if it has not -- never to the reference. The stage gets the
same hygiene as `AgentWorkspace.execute_bash`: an empty network namespace
where the host allows one, credentials scrubbed from the environment, and the
grader's stdlib-only interpreter so the probe sees what the oracle will see.

What counts as evidence
-----------------------
  * A probe whose prediction held  -> the hypothesis is SUPPORTED.
  * A probe whose prediction failed -> the hypothesis is FALSIFIED and its
    mechanism is tabu for that question.
  * A probe that could not run (timeout, sandbox error, rejected for trying
    to leave the stage) -> *no verdict*. Failure to test is not evidence.
  * A prediction that predicts nothing is vacuous and can support nothing.
  * The held-out oracle outranks every probe: `reconcile_with_oracle` can
    certify a question the probes only supported, reopen one the probes
    wrongly settled, and *refresh* one whose failure has changed shape since
    the last run (new text, new epoch; what was falsified against the old
    symptom stops being tabu).
  * A synthesised module must compile, import, and pass a static
    undefined-name check (`static_check`) before its question is RESOLVED:
    `py_compile` and `import` never execute a function body, and the Gen 16
    cohort lost three runs to a `NameError` that only the oracle saw.
  * A synthesised module that passes that gate must also *change what the
    supported probes observe* (`check_synthesis_effect`): the hypothesis'
    own probe, its question's and the root question's other supported probes
    are re-run, and when every one of them either still matches its
    prediction of the buggy behaviour or had already stopped reproducing it
    before this write (an earlier kept write flipped it, so it cannot vouch
    for this one) the write is recorded as `ineffective` -- a synthesis
    failure, not a RESOLVED question. The V9 run on marshmallow-1810 resolved
    two questions with rewrites that only deleted docstrings and reworded
    messages; one of them broke a test the proximity selection did not run.

Oracle leakage, stated plainly: this gatekeeper never runs the held-out
suites. Its only contact with the oracle is the list of failing test names
the worker already shows the firm under V5, plus -- for V6 only -- where each
failure's traceback ended inside the firm's own code.

Executor mode (SWE-bench)
-------------------------
Given an `executor` (`hae.swebench.executor.CommandExecutor`) the repository
under test is not this one and not on this disk: it is a task repository in a
container, reached only through `executor.run / read_file / write_file`. The
container *is* the sandbox, so there is no staged copy -- probes are written
to `<repo_root>/.hae/probe_<hid>.py` and run in place, and a synthesised
module is edited in place (the runner keeps `previous_source` for the
revert). `verify_module` then compiles, imports, statically checks, and runs
a *proximity* selection of the repository's own tests (`_proximity_tests`:
file-name similarity only) -- or, when `calibrate_whole_suite` found on the
clean checkout that the whole suite finishes within its budget, the whole
suite, failing the step only on failures that were not already present at
the base commit. The hidden FAIL_TO_PASS / PASS_TO_PASS lists are never read
here or anywhere on the firm's path. `reconcile_with_reproduction` is the
self-oracle: it re-runs the reproduction probes after a patch and is weaker
than the hidden tests by construction (a probe can only disprove the
behaviour it encoded). With `executor=None` nothing in this section runs and
behaviour is byte-identical to before it existed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, Q_EXHAUSTED, Q_OPEN, Q_RESOLVED, SUPPORTED, UNVERIFIED,
    EpistemicState, Evidence, GatekeeperAuthority, Hypothesis, Question,
    failure_summary,
)
from hae.epistemic.static_check import describe_findings, undefined_names
from hae.swebench.executor import SCRATCH_DIR

# How strongly one matching probe moves a posterior toward 1. A single
# experiment is strong evidence but not certification; only the oracle
# (or a module check after synthesis) gets a question below 0.05.
SUPPORT_STRENGTH = 0.7
# Fraction of a question's initial uncertainty that elimination alone can
# remove. Ruling hypotheses out narrows the search; it does not answer the
# question, so it can never take uncertainty to zero by itself.
ELIMINATION_CREDIT = 0.5
# Residual uncertainty after support / after a verified synthesis.
SUPPORTED_FLOOR = 0.05
RESOLVED_FLOOR = 0.02
# Where an oracle-reopened question lands: most of the way back to unknown.
REOPEN_FRACTION = 0.75
# Exit code reported for the in-process static check (the subprocess steps
# report their own; 1 and 2 are taken by unittest and py_compile).
STATIC_CHECK_EXIT = 3

INCONCLUSIVE = "inconclusive"
PROBE_DIR = "_probe"
MAX_PROBE_CHARS = 6000
OUTPUT_CAP = 4000

# Executor mode: a task repository's own tests may be slow (and compile
# extensions); the self-oracle command string is matched by tests and docs.
REPO_TESTS_TIMEOUT_S = 300
MODULE_CHECK_TIMEOUT_S = 180
PROXIMITY_LIMIT = 3
SELF_ORACLE_COMMAND = "self-oracle: reproduction probe re-run after patch (NOT the hidden tests)"
# Post-synthesis effect check (`check_synthesis_effect`): the command prefix of
# every probe re-run it records, the command of its summary evidence, the cap
# on probes re-run per synthesis and the three verdicts.
POST_SYNTHESIS_RERUN = "post-synthesis re-run"
EFFECT_CHECK_COMMAND = "post-synthesis effect check: supported probes re-run after the write (NOT the hidden tests)"
EFFECT_CHECK_PROBE_LIMIT = 6
EFFECT_EFFECTIVE = "effective"
EFFECT_INEFFECTIVE = "ineffective"
EFFECT_INCONCLUSIVE = "inconclusive"
# Whole-suite `repo_tests` mode (`calibrate_whole_suite`): the default budget
# the clean checkout's suite must finish within to qualify, the floor and the
# baseline multiple of the per-synthesis timeout, and how many new failures
# are re-run to filter flakiness.
WHOLE_SUITE_MAX_S = 60.0
WHOLE_SUITE_MIN_TIMEOUT_S = 60.0
WHOLE_SUITE_TIMEOUT_FACTOR = 3.0
WHOLE_SUITE_RERUN_LIMIT = 20
_TEST_DIR_NAMES = ("tests", "test", "testing")
_SRC_DIR_NAMES = ("src", "lib", "python")
# pytest's `-rfE` short summary: `FAILED tests/t.py::test_x - AssertionError: ...`, `ERROR tests/t.py`.
_SUMMARY_LINE_RE = re.compile(r"^(FAILED|ERROR)\s+(\S.*?)(?:\s+-\s.*)?$")
# Runs inside the task environment: the syntax gate of `py_compile` without
# its side effect (a .pyc written into the task repository, which an
# un-ignored `__pycache__/` would then drag into the prediction).
_COMPILE_SNIPPET = (
    "import sys\n"
    "p = sys.argv[1]\n"
    "with open(p, 'rb') as fh:\n"
    "    compile(fh.read(), p, 'exec')\n"
    "print('COMPILE_OK', p)\n"
)
# Runs inside the task environment: tries the dotted name of a path (with a
# leading src/-style directory stripped first), skips instead of failing when
# the path simply is not importable as a module, and fails on any other error.
_IMPORT_SNIPPET = (
    "import importlib, sys\n"
    "rel = sys.argv[1]\n"
    "base = rel[:-3] if rel.endswith('.py') else rel\n"
    "parts = [p for p in base.split('/') if p]\n"
    "if parts and parts[-1] == '__init__':\n"
    "    parts = parts[:-1]\n"
    "cands = []\n"
    f"if len(parts) > 1 and parts[0] in {_SRC_DIR_NAMES!r}:\n"
    "    cands.append('.'.join(parts[1:]))\n"
    "if parts:\n"
    "    cands.append('.'.join(parts))\n"
    "for name in [c for c in cands if c]:\n"
    "    try:\n"
    "        importlib.import_module(name)\n"
    "        print('IMPORT_OK', name)\n"
    "        sys.exit(0)\n"
    "    except ModuleNotFoundError as exc:\n"
    "        missing = getattr(exc, 'name', '') or ''\n"
    "        if missing and (missing == name or name.startswith(missing + '.') or missing == name.split('.')[0]):\n"
    "            continue\n"
    "        raise\n"
    "print('IMPORT_SKIPPED: no importable dotted name for', rel)\n"
)

# Same escape surface as `hae.runtime.company._touches_reference_tree`,
# duplicated rather than imported so this module never depends on the
# runner (the runner depends on it). Plus: sys.path mutation, the held-out
# suite, and raw network modules.
_PROBE_ESCAPE_RE = re.compile(
    r"(?:^|[\s'\"=(:,])/(?:app|usr|opt|root|home|proc|etc|var|tmp/hae_benchmark|tmp/hae_grader)\b"
    r"|\.\./|site-packages|dist-packages|importlib\.util\.find_spec|inspect\.getsource|__file__"
    r"|sys\.path\s*\.\s*(?:insert|append|extend)|sys\.path\s*[+]?=|PYTHONPATH"
    r"|held_out|\bsocket\b|urllib|http\.client|requests\.|ftplib|smtplib|telnetlib|xmlrpc",
    re.IGNORECASE,
)

# Two oracle shapes: a named test (`[tag] FAIL: test_x (...) -> Exc: msg`) and a
# whole suite that could not even be imported (`[tag] suite failed to import:
# ModuleNotFoundError: No module named 'hae.evaluation.harness'`). The second
# is what a first pass that never wrote the module looks like; Gen 16 cohort
# finding #7: without this branch it carried no tag, so its question had no
# module and every synthesis landed on the wrong file.
_FAILURE_KEY_RE = re.compile(
    r"^\s*(?:\[(?P<tag>[^\]]+)\]\s*)?"
    r"(?:(?:FAIL|ERROR):\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<suite>suite failed to import)\b)")
SUITE_IMPORT_NAME = "suite_import"


def failure_key(failure: str) -> Tuple[str, str, str]:
    """(key, tag, test_name) for an oracle failure line.

    Oracle lines look like `[execution_harness] FAIL: test_x (...) -> AssertionError: ...`
    or `[execution_harness] suite failed to import: ModuleNotFoundError: ...` (name
    `suite_import`). The key is stable across iterations even when the message
    changes, so a question seeded by a failure can be matched to the same
    failure later.
    """
    m = _FAILURE_KEY_RE.match(failure or "")
    if m:
        tag = (m.group("tag") or "").strip()
        name = m.group("name") or (SUITE_IMPORT_NAME if m.group("suite") else "")
        return (f"{tag}:{name}" if tag else name, tag, name)
    norm = " ".join((failure or "").split())[:120].lower()
    return (f"raw:{norm}", "", "")


def question_text(failure: str, module: str = "") -> str:
    """The text of an oracle-seeded question, rebuilt whenever the oracle line changes.

    One builder for seeding *and* refresh (Gen 16 cohort run 2, finding #8:
    the refresh path used to update `source_failure` only, so a question read
    `Oracle: UnboundLocalError ...` for three iterations after the oracle had
    moved on to `NameError: name 'mnf_err' is not defined`).
    """
    key, tag, name = failure_key(failure)
    summary = failure_summary(failure)
    if summary.startswith("[") and "]" in summary:
        summary = summary.split("]", 1)[1].strip()
    if name == SUITE_IMPORT_NAME:
        # The suite could not import its module at all -- usually the file does
        # not exist yet. The question must name the module so that the
        # synthesis move creates *that* file.
        return (f"Why can the `{tag}` suite not import"
                + (f" `{module}`" if module else " its module")
                + f"? Oracle: {summary}")
    return (f"Why does `{name or key}` fail"
            + (f" in `{module}`" if module else "")
            + f"? Oracle: {summary}")


_SIG_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/[^\s'\"():,]+")
_SIG_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_SIG_HEX_RE = re.compile(r"0x[0-9a-f]+")
_SIG_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_SIG_EXC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)\s*:\s*(.*)$", re.S)


def failure_signature(failure: str, limit: int = 80) -> str:
    """A normalised `ExceptionClass: message` for grouping failures by likely cause.

    Two oracle lines get the same signature when they differ only in numbers,
    quoted literals, hex addresses or file paths -- `AssertionError: 0 != 4`
    and `AssertionError: 7 != 12` are one signature, `TypeError: ...` on the
    same module is another. Deliberately conservative: it never merges
    different exception classes or differently-shaped messages, because the
    search loop uses it to *defer* sibling questions once one of them is
    resolved, and an over-merge costs those siblings a whole iteration.
    Returns "" when the line carries no exception text at all.
    """
    failure = failure or ""
    if "->" not in failure and _FAILURE_KEY_RE.match(failure):
        return ""  # `[tag] FAIL: test_x` with no exception text: nothing to group on
    summary = failure_summary(failure, limit=400)
    if not summary:
        return ""
    m = _SIG_EXC_RE.match(summary)
    exc, msg = (m.group(1), m.group(2)) if m else ("", summary)
    msg = msg.lower()
    msg = _SIG_PATH_RE.sub("/…", msg)
    msg = _SIG_QUOTED_RE.sub("'?'", msg)
    msg = _SIG_HEX_RE.sub("#", msg)
    msg = _SIG_NUMBER_RE.sub("#", msg)
    msg = " ".join(msg.split())[:limit]
    return f"{exc}: {msg}".strip(": ") if exc else msg


class EvidenceGatekeeper:
    """Runs probes, compares them to predictions, and writes verdicts.

    `executor` / `test_command` switch on executor mode (see the module
    docstring): `executor` is a `hae.swebench.executor.CommandExecutor` whose
    `root` and `python` become the defaults for `repo_root` and
    `python_executable`; `test_command` (argv list, or a string run with
    `sh -c`) replaces the proximity heuristic in `verify_module`'s
    `repo_tests` step. Both default to None, which is the pre-existing path.
    """

    def __init__(self, workspace: Any, timeout_s: int = 20, max_probe_lines: int = 40,
                 graded_modules: Optional[Iterable[str]] = None,
                 module_for_tag: Optional[Mapping[str, str]] = None,
                 repo_root: Optional[str] = None,
                 python_executable: Optional[str] = None,
                 isolate: bool = True, stage_reference: bool = True,
                 logger: Callable[[str], None] = print,
                 executor: Optional[Any] = None,
                 test_command: Optional[Union[str, Sequence[str]]] = None) -> None:
        self.workspace = workspace
        self.timeout_s = max(1, int(timeout_s))
        self.max_probe_lines = max(1, int(max_probe_lines))
        self.graded_modules = sorted({m.lstrip("./") for m in (graded_modules or [])})
        self.module_for_tag = dict(module_for_tag or {})
        self.stage_reference = stage_reference
        self.logger = logger
        self.authority = GatekeeperAuthority("EvidenceGatekeeper")
        self.executor = executor
        self.test_command = test_command
        if executor is not None:
            repo_root = repo_root or getattr(executor, "root", None)
            python_executable = python_executable or getattr(executor, "python", None)
        if repo_root is None:
            from hae.evaluation.benchmark import REPO_ROOT
            repo_root = REPO_ROOT
        self.repo_root = repo_root
        if python_executable:
            self.python = python_executable
        elif isolate:
            from hae.evaluation.benchmark import SelfHostingBenchmark
            self.python = SelfHostingBenchmark._stdlib_only_python()
        else:
            self.python = sys.executable
        self.probes_run = 0
        self.probes_rejected = 0
        # Executor mode only: set by `calibrate_whole_suite` when the clean
        # checkout's suite finishes within budget. None means the proximity
        # selection (the pre-existing `repo_tests` behaviour).
        self.whole_suite: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ #
    # Probe hygiene
    # ------------------------------------------------------------------ #

    def validate_probe(self, code: str) -> str:
        """Returns a rejection reason, or "" if the probe may run."""
        if not code or not code.strip():
            return "empty probe"
        if len(code) > MAX_PROBE_CHARS:
            return f"probe exceeds {MAX_PROBE_CHARS} characters"
        lines = [l for l in code.splitlines() if l.strip()]
        if len(lines) > self.max_probe_lines:
            return f"probe has {len(lines)} non-blank lines; the policy allows {self.max_probe_lines}"
        m = _PROBE_ESCAPE_RE.search(code)
        if m:
            return f"probe reaches outside the stage ({m.group(0).strip()!r})"
        if self.repo_root and self.repo_root in code:
            return "probe references the repository root"
        try:
            compile(code, "<probe>", "exec")
        except SyntaxError as exc:
            return f"probe is not valid Python ({exc.msg} at line {exc.lineno})"
        return ""

    # ------------------------------------------------------------------ #
    # Staging and sandboxed execution
    # ------------------------------------------------------------------ #

    def _build_stage(self) -> str:
        stage = tempfile.mkdtemp(prefix="hae_epistemic_")
        if self.stage_reference and self.repo_root:
            for name in ("hae", "pyproject.toml"):
                src = os.path.join(self.repo_root, name)
                dst = os.path.join(stage, name)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                elif os.path.exists(src):
                    shutil.copy2(src, dst)
            for rel in self.graded_modules:
                target = os.path.join(stage, rel)
                if os.path.exists(target):
                    os.remove(target)
        bundle: Dict[str, str] = {}
        try:
            bundle = self.workspace.export_bundle()
        except Exception:
            bundle = {}
        for rel, body in bundle.items():
            clean = rel.replace("\\", "/").lstrip("./")
            if not clean or clean.startswith(PROBE_DIR + "/") or ".." in clean.split("/"):
                continue
            full = os.path.join(stage, clean)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(body if isinstance(body, str) else str(body))
        return stage

    def _sandbox_env(self, stage: str) -> Dict[str, str]:
        env_fn = getattr(self.workspace, "_sandbox_env", None)
        env = dict(env_fn()) if callable(env_fn) else {
            k: v for k, v in os.environ.items() if "TOKEN" not in k and "KEY" not in k and "CREDENTIALS" not in k}
        env["PYTHONPATH"] = stage
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTEST_ADDOPTS", None)
        return env

    @staticmethod
    def _netns_available() -> bool:
        try:
            from hae.runtime.workspace import AgentWorkspace
            return bool(AgentWorkspace._netns_available())
        except Exception:
            return False

    def _run_in_stage(self, stage: str, argv: List[str], timeout: Optional[int] = None) -> Dict[str, Any]:
        isolated = self._netns_available()
        required = os.getenv("HAE_REQUIRE_NETWORK_ISOLATION") == "1"
        if required and not isolated:
            return {"status": "error", "exit_code": -1, "stdout": "",
                    "stderr": "HAE_REQUIRE_NETWORK_ISOLATION=1 but no network namespace is available",
                    "network_isolated": False}
        full = (["unshare", "-rn"] if isolated else []) + list(argv)
        try:
            res = subprocess.run(full, cwd=stage, capture_output=True, text=True,
                                 timeout=timeout or self.timeout_s, env=self._sandbox_env(stage))
            return {"status": "ok" if res.returncode == 0 else "failed",
                    "exit_code": res.returncode, "stdout": res.stdout or "",
                    "stderr": res.stderr or "", "network_isolated": isolated}
        except subprocess.TimeoutExpired as exc:
            return {"status": "timeout", "exit_code": -1,
                    "stdout": (exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout) or "",
                    "stderr": f"timed out after {timeout or self.timeout_s}s",
                    "network_isolated": isolated}
        except Exception as exc:  # missing interpreter, OS error
            return {"status": "error", "exit_code": -1, "stdout": "",
                    "stderr": f"{type(exc).__name__}: {exc}", "network_isolated": isolated}

    # ------------------------------------------------------------------ #
    # Prediction matching
    # ------------------------------------------------------------------ #

    @staticmethod
    def match_prediction(prediction: Mapping[str, Any], result: Mapping[str, Any]) -> Tuple[bool, str]:
        """(matched, detail). Inconclusive runs are reported with the INCONCLUSIVE prefix."""
        if result.get("status") in ("timeout", "error") or int(result.get("exit_code", -1)) == -1:
            return False, f"{INCONCLUSIVE}: probe did not complete ({result.get('status')}: {str(result.get('stderr', ''))[:160]})"
        checks: List[Tuple[str, bool]] = []
        stdout = str(result.get("stdout", ""))
        stderr = str(result.get("stderr", ""))
        if "expect_exit_code" in prediction:
            try:
                want = int(prediction["expect_exit_code"])
                checks.append((f"exit_code=={want}", int(result.get("exit_code")) == want))
            except (TypeError, ValueError):
                pass
        sub = prediction.get("expect_stdout_contains")
        if isinstance(sub, str) and sub.strip():
            checks.append((f"stdout contains {sub!r}", sub in stdout))
        rx = prediction.get("expect_stdout_regex")
        if isinstance(rx, str) and rx.strip():
            try:
                checks.append((f"stdout matches /{rx}/", re.search(rx, stdout) is not None))
            except re.error:
                checks.append((f"stdout matches /{rx}/ (invalid regex)", False))
        esub = prediction.get("expect_stderr_contains")
        if isinstance(esub, str) and esub.strip():
            checks.append((f"stderr contains {esub!r}", esub in stderr))
        if not checks:
            return False, "vacuous prediction: nothing to check, so nothing can be supported"
        failed = [name for name, ok in checks if not ok]
        if failed:
            return False, "prediction failed: " + "; ".join(failed)
        return True, "prediction held: " + "; ".join(name for name, _ in checks)

    # ------------------------------------------------------------------ #
    # Experiments
    # ------------------------------------------------------------------ #

    def run_experiment(self, state: EpistemicState, hypothesis: Hypothesis,
                       command_prefix: str = "") -> Evidence:
        """Runs a hypothesis' probe. Returns evidence; writes nothing yet.

        `command_prefix` labels the recorded command (the effect check's
        re-runs carry `POST_SYNTHESIS_RERUN`) so a re-run after a write is
        never read as the verdict run.
        """
        eid = state.new_evidence_id()
        reason = self.validate_probe(hypothesis.probe_code)
        if reason:
            self.probes_rejected += 1
            return Evidence(
                evidence_id=eid, kind="probe_rejected",
                hypothesis_id=hypothesis.hypothesis_id, question_id=hypothesis.question_id,
                command="", exit_code=-2, prediction=dict(hypothesis.prediction),
                matched_prediction=False, detail=f"rejected: {reason}")
        if self.executor is not None:
            probe_rel, result = self._run_probe_exec(hypothesis)
        else:
            stage = self._build_stage()
            probe_rel = f"{PROBE_DIR}/{hypothesis.hypothesis_id}.py"
            try:
                os.makedirs(os.path.join(stage, PROBE_DIR), exist_ok=True)
                with open(os.path.join(stage, probe_rel), "w", encoding="utf-8") as fh:
                    fh.write(hypothesis.probe_code)
                result = self._run_in_stage(stage, [self.python, probe_rel])
            finally:
                shutil.rmtree(stage, ignore_errors=True)
        self.probes_run += 1
        matched, detail = self.match_prediction(hypothesis.prediction, result)
        label = f"{command_prefix}: " if command_prefix else ""
        return Evidence(
            evidence_id=eid, kind="probe",
            hypothesis_id=hypothesis.hypothesis_id, question_id=hypothesis.question_id,
            command=f"{label}python {probe_rel}", exit_code=int(result.get("exit_code", -1)),
            stdout=str(result.get("stdout", ""))[:OUTPUT_CAP],
            stderr=str(result.get("stderr", ""))[-OUTPUT_CAP:],
            prediction=dict(hypothesis.prediction), matched_prediction=matched,
            detail=detail, network_isolated=result.get("network_isolated"))

    @staticmethod
    def rerun_outcome(evidence: Evidence) -> Tuple[bool, bool]:
        """(completed, bug_no_longer_reproduces) for a supported probe re-run after a write.

        The one definition shared by the self-oracle and the effect check: the
        re-run completed (not rejected, no timeout, no transport error) AND
        its prediction of the buggy behaviour did NOT hold any more AND the
        probe exited 0. A probe that now crashes proves only that something
        changed; a probe that still matches says the bug is still there.
        """
        completed = evidence.kind == "probe" and not evidence.detail.startswith(INCONCLUSIVE)
        gone = bool(completed and not evidence.matched_prediction and evidence.exit_code == 0)
        return completed, gone

    def apply(self, state: EpistemicState, hypothesis: Hypothesis, evidence: Evidence) -> float:
        """Deterministic belief revision. Returns the uncertainty resolved (>= 0)."""
        state.record_evidence(evidence, self.authority)
        if evidence.kind == "probe_rejected":
            # The probe never ran, so nothing was learnt about the mechanism
            # and no belief moves. But the hypothesis must leave the untested
            # pool: left UNVERIFIED with a growing visit count, PUCT re-selected
            # one such hypothesis 17 times in the Gen 16 pilot. UNTESTABLE
            # parks it until a proposer repairs the probe (ledger.repair_probe).
            if hypothesis.status == UNVERIFIED:
                state.mark_untestable(hypothesis.hypothesis_id, evidence.evidence_id,
                                      evidence.detail, self.authority)
            return 0.0
        if evidence.detail.startswith(INCONCLUSIVE):
            return 0.0
        q = state.questions[hypothesis.question_id]
        if evidence.matched_prediction:
            posterior = 1.0 - (1.0 - hypothesis.prior) * (1.0 - SUPPORT_STRENGTH)
            state.set_hypothesis_verdict(hypothesis.hypothesis_id, SUPPORTED, posterior,
                                         evidence.evidence_id, self.authority)
            new_u = max(SUPPORTED_FLOOR, q.initial_uncertainty * (1.0 - posterior))
            if q.status == Q_EXHAUSTED:
                state.set_question_status(q.question_id, Q_OPEN, self.authority)
        else:
            state.set_hypothesis_verdict(hypothesis.hypothesis_id, FALSIFIED, 0.0,
                                         evidence.evidence_id, self.authority)
            siblings = state.hypotheses_for(q.question_id)
            mass = sum(h.prior for h in siblings) or 1.0
            dead = sum(h.prior for h in siblings if h.status == FALSIFIED)
            new_u = q.initial_uncertainty * (1.0 - ELIMINATION_CREDIT * dead / mass)
            live = [h for h in siblings if h.status in (UNVERIFIED, SUPPORTED, CERTIFIED)]
            if not live and q.status == Q_OPEN:
                state.set_question_status(q.question_id, Q_EXHAUSTED, self.authority)
        return state.lower_uncertainty(q.question_id, new_u, self.authority)

    # ------------------------------------------------------------------ #
    # Module checks after synthesis
    # ------------------------------------------------------------------ #

    def static_check(self, source: str, filename: str = "<module>") -> Tuple[int, List[str], str]:
        """The in-process undefined-name gate. Returns (exit_code, stderr_lines, note).

        `py_compile` and `import` never execute a function body, so a name
        that does not exist -- `mnf_err` in an f-string, `e` used after its
        `except` handler -- passes both and only the oracle sees it (Gen 16
        cohort run 2, finding #8). The checker is a reader, not a runner: no
        subprocess, no sandbox. If the checker itself crashes the step passes
        with a note; a broken gate must not block every synthesis.
        """
        try:
            findings = undefined_names(source, filename=filename)
        except Exception as exc:  # the gate is best-effort
            return 0, [], f"checker error ({type(exc).__name__}: {str(exc)[:120]}); step skipped"
        if not findings:
            return 0, [], f"no undefined names ({len(source.splitlines())} lines)"
        return STATIC_CHECK_EXIT, describe_findings(findings), ""

    def verify_module(self, state: EpistemicState, module_path: str,
                      question_id: str = "", hypothesis_id: str = "") -> Evidence:
        """Compiles, imports, statically checks and (if the firm wrote tests) tests a module in the stage.

        In executor mode (see module docstring) the same gate runs in place in
        the task repository, with the repository's own nearby tests instead of
        the firm's `tests/`; see `_verify_module_exec`.
        """
        if self.executor is not None:
            return self._verify_module_exec(state, module_path, question_id, hypothesis_id)
        eid = state.new_evidence_id()
        rel = module_path.replace("\\", "/").lstrip("./")
        dotted = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")
        stage = self._build_stage()
        steps: List[Tuple[str, Optional[List[str]]]] = [
            ("py_compile", [self.python, "-m", "py_compile", rel]),
            ("import", [self.python, "-c", f"import importlib; importlib.import_module({dotted!r}); print('IMPORT_OK')"]),
            ("static_check", None),   # in-process, see `static_check`
        ]
        if os.path.isdir(os.path.join(stage, "tests")):
            steps.append(("firm_tests", [self.python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]))
        out_chunks: List[str] = []
        err_chunks: List[str] = []
        exit_code = 0
        failed_step = ""
        failure_note = ""
        isolated: Optional[bool] = None
        try:
            if not os.path.exists(os.path.join(stage, rel)):
                exit_code, failed_step = 2, "missing"
                err_chunks.append(f"{rel} is not present in the workspace")
            else:
                for name, argv in steps:
                    if argv is None:
                        with open(os.path.join(stage, rel), "r", encoding="utf-8", errors="replace") as fh:
                            source = fh.read()
                        code, err_lines, note = self.static_check(source, filename=rel)
                        out_chunks.append(f"[{name}] {note}")
                        if err_lines:
                            err_chunks.append(f"[{name}] " + "\n".join(err_lines))
                        if code != 0:
                            exit_code, failed_step = code, name
                            failure_note = err_lines[0] + (f" (+{len(err_lines) - 1} more)" if len(err_lines) > 1 else "")
                            break
                        continue
                    res = self._run_in_stage(stage, argv)
                    isolated = res.get("network_isolated")
                    out_chunks.append(f"[{name}] {res.get('stdout', '')[:800]}")
                    if res.get("stderr"):
                        err_chunks.append(f"[{name}] {res.get('stderr', '')[-1200:]}")
                    if int(res.get("exit_code", -1)) != 0:
                        exit_code, failed_step = int(res.get("exit_code", -1)), name
                        break
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        passed = exit_code == 0
        if passed:
            detail = "module check passed: " + ", ".join(n for n, _ in steps)
        elif failure_note:
            detail = f"module check failed at {failed_step}: {failure_note}"
        else:
            detail = f"module check failed at {failed_step} (exit {exit_code})"
        return Evidence(
            evidence_id=eid, kind="module_check", hypothesis_id=hypothesis_id,
            question_id=question_id, command=f"verify_module {rel}", exit_code=exit_code,
            stdout="\n".join(out_chunks)[:OUTPUT_CAP], stderr="\n".join(err_chunks)[-OUTPUT_CAP:],
            prediction={"expect_exit_code": 0}, matched_prediction=passed,
            detail=detail, network_isolated=isolated)

    @staticmethod
    def last_probe_run(state: EpistemicState, hypothesis: Hypothesis) -> Optional[Evidence]:
        """The most recent recorded run of the hypothesis' probe (first run, effect-check or self-oracle re-run)."""
        for ev in reversed(state.evidence_log):
            if ev.hypothesis_id == hypothesis.hypothesis_id and ev.kind == "probe":
                return ev
        return None

    def probe_already_gone(self, state: EpistemicState, hypothesis: Hypothesis) -> bool:
        """True when the probe's latest recorded run already no longer reproduced the bug.

        Such a probe says nothing about the next write: an earlier kept write
        flipped it. Counting it again would make every synthesis after the
        first real fix look effective, which is how a cosmetic rewrite of an
        unrelated function would re-enter the patch.
        """
        ev = self.last_probe_run(state, hypothesis)
        return ev is not None and self.rerun_outcome(ev)[1]

    def check_synthesis_effect(self, state: EpistemicState, question: Question, hypothesis: Hypothesis,
                               root_question_id: Optional[str] = None,
                               module_evidence: Optional[Evidence] = None) -> Dict[str, Any]:
        """Did the write change what the supported probes observe? Run after a passed module check.

        A SUPPORTED hypothesis is a probe that matched its prediction *of the
        buggy behaviour*; a patch for its mechanism has to change that
        outcome. The hypothesis' own probe, the other SUPPORTED / CERTIFIED
        probes of its question and those of the root question (at most
        `EFFECT_CHECK_PROBE_LIMIT`, own probe first, then by posterior) are
        re-run unchanged and recorded; `apply` is not called, so no verdict
        moves. A probe only vouches for THIS write if its latest recorded run
        before the write still reproduced the bug (`probe_already_gone`);
        one that an earlier kept write already flipped is re-run for the
        record (a regression shows up as "matched again") but cannot make the
        write effective. With `rerun_outcome`'s definition of "gone":

            effective    -- at least one probe that still reproduced the bug
                            before the write no longer does;
            ineffective  -- every probe completed and either still matches its
                            buggy prediction or had already stopped
                            reproducing before the write: the write changed
                            nothing a probe can see (a cosmetic rewrite, the
                            wrong function, a dead branch, a mechanism an
                            earlier write already fixed), so it is not a fix;
            inconclusive -- nothing flipped, but some probe did not complete or
                            now exits non-zero: something changed, and the
                            iteration-end self-oracle decides.

        One summary `Evidence` of kind "module_check" carrying
        `EFFECT_CHECK_COMMAND` is recorded and attached to the hypothesis, so
        the next synthesis prompt for it quotes the verdict. `module_evidence`,
        when given, is recorded first (once) so the ledger reads module check,
        re-runs, summary. `apply_synthesis` turns "ineffective" into a
        synthesis failure instead of a RESOLVED question.
        """
        if module_evidence is not None and state.evidence_by_id(module_evidence.evidence_id) is None:
            state.record_evidence(module_evidence, self.authority)

        def supported(qid: str) -> List[Hypothesis]:
            return sorted((h for h in state.hypotheses_for(qid)
                           if h.status in (SUPPORTED, CERTIFIED) and h.hypothesis_id != hypothesis.hypothesis_id),
                          key=lambda h: (-h.posterior, h.hypothesis_id))

        probes: List[Hypothesis] = [hypothesis] + supported(question.question_id)
        if root_question_id and root_question_id != question.question_id and root_question_id in state.questions:
            seen = {h.hypothesis_id for h in probes}
            probes.extend(h for h in supported(root_question_id) if h.hypothesis_id not in seen)
        probes = probes[:EFFECT_CHECK_PROBE_LIMIT]
        reruns: List[Dict[str, Any]] = []
        gone: List[str] = []
        already: List[str] = []
        unsettled = 0
        for h in probes:
            was_gone = self.probe_already_gone(state, h)
            ev = self.run_experiment(state, h, command_prefix=POST_SYNTHESIS_RERUN)
            state.record_evidence(ev, self.authority)
            completed, is_gone = self.rerun_outcome(ev)
            reruns.append({"hypothesis_id": h.hypothesis_id, "question_id": h.question_id,
                           "evidence_id": ev.evidence_id, "completed": completed,
                           "matched_prediction": bool(ev.matched_prediction), "exit_code": int(ev.exit_code),
                           "bug_no_longer_reproduces": is_gone, "already_gone_before_write": was_gone,
                           "detail": ev.detail})
            if is_gone and was_gone:
                already.append(h.hypothesis_id)
            elif is_gone:
                gone.append(h.hypothesis_id)
            elif not (completed and ev.matched_prediction):
                unsettled += 1
        if gone:
            verdict = EFFECT_EFFECTIVE
            detail = (f"post-synthesis effect check: effective -- {len(gone)}/{len(probes)} re-run probe(s) "
                      f"no longer reproduce the bug ({', '.join(gone)})")
        elif unsettled == 0:
            verdict = EFFECT_INEFFECTIVE
            still = len(probes) - len(already)
            if not already:
                detail = (f"post-synthesis effect check: ineffective -- all {len(probes)} re-run probe(s) still "
                          "match their prediction of the buggy behaviour; the write changed nothing a probe can "
                          "observe, so it is not a fix of this mechanism")
            elif still == 0:
                detail = (f"post-synthesis effect check: ineffective -- the bug the {len(already)} re-run probe(s) "
                          f"describe had already stopped reproducing before this write ({', '.join(already)}); "
                          "an earlier write fixed it and this one changes nothing a probe can observe")
            else:
                detail = (f"post-synthesis effect check: ineffective -- {still} re-run probe(s) still match their "
                          f"prediction of the buggy behaviour and {len(already)} had already stopped reproducing "
                          f"before this write ({', '.join(already)}); the write changed nothing a probe can observe")
        else:
            verdict = EFFECT_INCONCLUSIVE
            detail = (f"post-synthesis effect check: inconclusive -- 0/{len(probes)} re-run probe(s) flipped, "
                      f"{unsettled} did not complete or now exit non-zero")
        summary = Evidence(
            evidence_id=state.new_evidence_id(), kind="module_check",
            hypothesis_id=hypothesis.hypothesis_id, question_id=question.question_id,
            command=EFFECT_CHECK_COMMAND, exit_code=0 if verdict != EFFECT_INEFFECTIVE else 1,
            stdout="\n".join(
                f"{r['hypothesis_id']} ({r['question_id']}): completed={r['completed']} "
                f"matched={r['matched_prediction']} exit={r['exit_code']} "
                f"bug_no_longer_reproduces={r['bug_no_longer_reproduces']}"
                + (" (already before this write)" if r["already_gone_before_write"] else "")
                for r in reruns)[:OUTPUT_CAP],
            prediction={"expect_exit_code": 0}, matched_prediction=verdict != EFFECT_INEFFECTIVE,
            detail=detail)
        state.record_evidence(summary, self.authority)  # attaches itself to the hypothesis
        return {"verdict": verdict, "evidence_id": summary.evidence_id, "detail": detail,
                "reruns": reruns, "bug_gone_hypotheses": gone, "already_gone_hypotheses": already,
                "probes_rerun": len(probes)}

    def apply_synthesis(self, state: EpistemicState, question: Question,
                        hypothesis: Hypothesis, evidence: Evidence,
                        effect: Optional[Mapping[str, Any]] = None) -> float:
        """Records a post-synthesis module check and moves the question to RESOLVED if it passed.

        `effect` is `check_synthesis_effect`'s result for the same write;
        an `ineffective` verdict counts as a synthesis failure (the question
        stays OPEN, `patch_applied` stays False) even though the module check
        passed. Recording the module evidence is idempotent: the effect check
        may already have put it on the log.
        """
        if state.evidence_by_id(evidence.evidence_id) is None:
            state.record_evidence(evidence, self.authority)
        # The next synthesis attempt is prompted with the hypothesis' evidence;
        # a failed module check must be in it even if the evidence was built
        # without the hypothesis id.
        if evidence.evidence_id not in hypothesis.evidence_ids:
            hypothesis.evidence_ids.append(evidence.evidence_id)
        if not evidence.matched_prediction:
            hypothesis.synthesis_failures += 1
            return 0.0
        if effect is not None and str(effect.get("verdict", "")) == EFFECT_INEFFECTIVE:
            hypothesis.synthesis_failures += 1
            return 0.0
        hypothesis.patch_applied = True
        state.set_question_status(question.question_id, Q_RESOLVED, self.authority)
        return state.lower_uncertainty(question.question_id, RESOLVED_FLOOR, self.authority)

    # ------------------------------------------------------------------ #
    # Oracle reconciliation (the one source that outranks probes)
    # ------------------------------------------------------------------ #

    @staticmethod
    def failure_changed(old: str, new: str) -> Tuple[bool, bool]:
        """(shape_changed, message_changed) between two oracle lines for one failure key.

        The *shape* is `failure_signature` -- exception class and message with
        numbers, quoted literals, paths and addresses normalised away. A new
        shape means the firm is now looking at a different error (finding #8:
        `UnboundLocalError: ... 'e'` became `NameError: name 'mnf_err' ...`)
        and starts a new epoch. A message that differs only in a literal
        (`0 != 4` -> `1 != 4`, a fresh temp path) is the same error with new
        numbers: the question text is updated so the proposer reads the
        current message, but nothing the firm learnt is thrown away --
        otherwise a sandbox path in an assertion would reset the tabu list
        every iteration. When neither line carries a signature (suite-import
        lines) the summary decides.
        """
        if not old:
            return False, False
        old_sig, new_sig = failure_signature(old), failure_signature(new)
        message_changed = failure_summary(old) != failure_summary(new)
        if old_sig or new_sig:
            return old_sig != new_sig, message_changed
        return message_changed, message_changed

    def reconcile_with_oracle(self, state: EpistemicState, failures: Iterable[str],
                              iteration: int = 0, default_module: str = "",
                              locations: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
        """Certifies questions whose failure vanished, reopens those that did not, seeds new ones.

        `locations` maps an oracle failure line to where its traceback ended
        inside the firm's code (`hae/evaluation/harness.py:212 in _run_pytest`,
        from `benchmark.failure_locations`); it is stored on the question at
        seeding and refresh and shown to the proposer. A question whose
        failure line changed *shape* since the last oracle run is refreshed
        (`refreshed`): new text, new epoch, uncertainty back up, OPEN again.
        """
        failures = [str(f) for f in (failures or []) if str(f).strip()]
        locations = dict(locations or {})
        now: Dict[str, str] = {}
        for f in failures:
            key, _, _ = failure_key(f)
            now.setdefault(key, f)
        evidence = Evidence(
            evidence_id=state.new_evidence_id(), kind="oracle",
            command=f"held-out oracle, iteration {iteration}",
            exit_code=0 if not failures else 1,
            stdout="\n".join(failures)[:OUTPUT_CAP],
            prediction={"expect_exit_code": 0}, matched_prediction=not failures,
            detail=f"{len(failures)} failing check(s)")
        state.record_evidence(evidence, self.authority)

        certified: List[str] = []
        reopened: List[str] = []
        refreshed: List[str] = []
        reworded: List[str] = []
        seeded: List[str] = []
        for q in list(state.questions.values()):
            if not q.source_failure_key or q.status == "CERTIFIED":
                continue
            if q.source_failure_key not in now:
                candidates = [h for h in state.hypotheses_for(q.question_id)
                              if h.status in (SUPPORTED, CERTIFIED)]
                best = max(candidates, key=lambda h: (h.patch_applied, h.posterior), default=None)
                statement = f"{q.source_failure_key} now passes the held-out oracle (iteration {iteration})"
                if best is not None:
                    statement += f" -- {best.claim}"
                state.certify(q.question_id, best.hypothesis_id if best else "", statement,
                              [evidence.evidence_id], certified_by="oracle", authority=self.authority)
                certified.append(q.question_id)
                continue
            line = now[q.source_failure_key]
            location = str(locations.get(line, "") or "")
            shape_changed, message_changed = self.failure_changed(q.source_failure, line)
            if q.status == Q_RESOLVED:
                # The patch landed and the module checks passed, yet the oracle
                # still fails: whatever was supported was not the whole story.
                for h in state.hypotheses_for(q.question_id):
                    if h.patch_applied and h.status == SUPPORTED:
                        state.set_hypothesis_verdict(h.hypothesis_id, FALSIFIED, 0.0,
                                                     evidence.evidence_id, self.authority)
                state.reopen_question(q.question_id, q.initial_uncertainty * REOPEN_FRACTION,
                                      evidence.evidence_id, self.authority)
                reopened.append(q.question_id)
            if shape_changed:
                # Finding #8: the error the firm is chasing is not the one in
                # its question any more. New text, new epoch, back to open. A
                # location from the old traceback is stale, so "" clears it.
                state.refresh_question(q.question_id, question_text(line, q.module), line,
                                       q.initial_uncertainty * REOPEN_FRACTION, evidence.evidence_id,
                                       self.authority, location=location)
                refreshed.append(q.question_id)
                continue
            if message_changed:
                q.text = " ".join(question_text(line, q.module).split())
                reworded.append(q.question_id)
            q.source_failure = line
            if location:
                q.location = location
        known = {q.source_failure_key for q in state.questions.values() if q.source_failure_key}
        for key, line in now.items():
            if key in known:
                continue
            _, tag, _ = failure_key(line)
            module = self.module_for_tag.get(tag, default_module) if tag else default_module
            q = state.add_question(text=question_text(line, module), module=module, uncertainty=1.0,
                                   source_failure_tag=tag, source_failure_key=key, source_failure=line,
                                   location=str(locations.get(line, "") or ""))
            seeded.append(q.question_id)
        return {"evidence_id": evidence.evidence_id, "certified": certified,
                "reopened": reopened, "refreshed": refreshed, "reworded": reworded,
                "seeded": seeded, "failing_now": len(now)}

    # ------------------------------------------------------------------ #
    # Executor mode (SWE-bench): the task repository is the sandbox
    # ------------------------------------------------------------------ #

    def _exec_env(self) -> Dict[str, str]:
        # `python .hae/probe.py` puts `.hae/` -- not the repo root -- first on
        # sys.path; PYTHONPATH makes `import <package>` resolve to the edited
        # checkout even where the package is not installed in the environment.
        return {"PYTHONPATH": self.repo_root}

    def _run_exec(self, argv: Sequence[str], timeout: Optional[float] = None,
                  env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
        """`executor.run` in the repository root, reported in `_run_in_stage`'s dict shape."""
        limit = float(timeout or self.timeout_s)
        merged = self._exec_env()
        if env:
            merged.update(env)
        try:
            res = self.executor.run(list(argv), cwd=self.repo_root, timeout_s=limit, env=merged)
        except Exception as exc:  # transport failure, not a verdict
            return {"status": "error", "exit_code": -1, "stdout": "",
                    "stderr": f"{type(exc).__name__}: {exc}", "network_isolated": None}
        if getattr(res, "timed_out", False):
            return {"status": "timeout", "exit_code": -1, "stdout": res.stdout or "",
                    "stderr": (res.stderr or "").strip() or f"timed out after {limit:.0f}s", "network_isolated": None}
        rc = int(res.rc)
        if rc == -1:
            return {"status": "error", "exit_code": -1, "stdout": res.stdout or "",
                    "stderr": res.stderr or "", "network_isolated": None}
        return {"status": "ok" if rc == 0 else "failed", "exit_code": rc,
                "stdout": res.stdout or "", "stderr": res.stderr or "", "network_isolated": None}

    def _rel_path(self, module_path: str) -> str:
        rel = str(module_path).replace("\\", "/")
        root = (self.repo_root or "").rstrip("/")
        if root and rel.startswith(root + "/"):
            rel = rel[len(root) + 1:]
        while rel.startswith("./"):
            rel = rel[2:]
        return rel.lstrip("/")

    def _run_probe_exec(self, hypothesis: Hypothesis) -> Tuple[str, Dict[str, Any]]:
        """Writes the probe under `.hae/` in the task repository and runs it there."""
        probe_rel = f"{SCRATCH_DIR}/probe_{hypothesis.hypothesis_id}.py"
        try:
            self.executor.write_file(probe_rel, hypothesis.probe_code)
        except Exception as exc:
            return probe_rel, {"status": "error", "exit_code": -1, "stdout": "",
                               "stderr": f"could not write probe: {type(exc).__name__}: {exc}",
                               "network_isolated": None}
        return probe_rel, self._run_exec([self.python, probe_rel])

    def _static_check_exec(self, rel: str) -> Tuple[int, List[str], str]:
        """`static_check` on the current source, failing only on findings not already at HEAD.

        Real repositories trip the conservative checker on pre-existing
        constructs (star imports, `exec`-populated globals); a synthesis must
        not be blocked by names it did not touch.
        """
        try:
            source = self.executor.read_file(rel)
        except Exception as exc:
            return 0, [], f"could not read {rel} ({type(exc).__name__}); step skipped"
        code, err_lines, note = self.static_check(source, filename=rel)
        if code == 0:
            return code, err_lines, note
        base = self._run_exec(["git", "show", f"HEAD:{rel}"], timeout=60)
        if int(base.get("exit_code", -1)) != 0:
            return code, err_lines, note  # new file: every finding is new
        try:
            baseline = {(f.name, f.scope, f.kind) for f in undefined_names(str(base.get("stdout", "")), filename=rel)}
            current = undefined_names(source, filename=rel)
        except Exception:
            return code, err_lines, note
        new = [f for f in current if (f.name, f.scope, f.kind) not in baseline]
        if not new:
            return 0, [], f"{len(current)} finding(s) already present at HEAD, none introduced"
        return STATIC_CHECK_EXIT, describe_findings(new), ""

    def _proximity_tests(self, rel: str, limit: int = PROXIMITY_LIMIT) -> List[str]:
        """Up to `limit` of the repository's own test files chosen by FILE-NAME proximity.

        Candidates are tracked `test_*.py` / `*_test.py` / `tests.py` files
        under a `tests/`, `test/` or `testing/` directory whose name contains
        the module's stem (`__init__.py` uses its package directory; `tests.py`
        matches on its directory names); shortest paths first. This is a
        regression smoke test, nothing more: it does NOT and MUST NOT consult
        the instance's FAIL_TO_PASS / PASS_TO_PASS lists or `test_patch` --
        those are grading information (`hae.swebench.dataset.GradingInfo`) and
        reading them here would be oracle leakage.
        """
        parts = [p for p in rel.split("/") if p]
        if not parts:
            return []
        stem = parts[-1][:-3] if parts[-1].endswith(".py") else parts[-1]
        if stem == "__init__":
            stem = parts[-2] if len(parts) > 1 else ""
        stem_l = stem.lower()
        if not stem_l:
            return []
        listing = self._run_exec(["git", "ls-files"], timeout=60)
        if int(listing.get("exit_code", -1)) != 0:
            return []
        found: List[str] = []
        for line in str(listing.get("stdout", "")).splitlines():
            path = line.strip()
            if not path.endswith(".py") or path == rel:
                continue
            ps = path.split("/")
            name, dirs = ps[-1][:-3], ps[:-1]
            if not any(d in _TEST_DIR_NAMES for d in dirs):
                continue
            if not (name.startswith("test") or name.endswith("_test") or name.endswith("_tests")):
                continue
            if name in ("tests", "test"):
                hay = [d.lower() for d in dirs]
                match = any(stem_l == d or stem_l in d.split("_") for d in hay)
            else:
                tokens = [t for t in name.lower().split("_") if t]
                match = stem_l in tokens or (len(stem_l) >= 4 and stem_l in name.lower())
            if match:
                found.append(path)
        found.sort(key=lambda p: (len(p), p))
        return found[:limit]

    def _repo_tests_argv(self, rel: str) -> Tuple[List[str], List[str], str]:
        """(argv, files, mode) for the `repo_tests` step; mode is test_command | whole_suite | proximity | none."""
        if self.test_command:
            if isinstance(self.test_command, str):
                return ["sh", "-c", self.test_command], [], "test_command"
            return list(self.test_command), [], "test_command"
        if self.whole_suite:
            return list(self.whole_suite["argv"]), [str(self.whole_suite["target"])], "whole_suite"
        files = self._proximity_tests(rel)
        if not files:
            return [], [], "none"
        return [self.python, "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider", *files], files, "proximity"

    @staticmethod
    def failing_test_ids(text: str) -> List[str]:
        """Test ids from pytest's `-rfE` short summary (`FAILED id - msg`, `ERROR id`), in order, de-duplicated."""
        ids: List[str] = []
        for line in text.splitlines():
            m = _SUMMARY_LINE_RE.match(line.strip())
            if m:
                ident = m.group(2).strip()
                if ident and ident not in ids:
                    ids.append(ident)
        return ids

    def _whole_suite_argv(self, target: str) -> List[str]:
        return [self.python, "-m", "pytest", "-q", "-rfE", "--no-header", "-p", "no:cacheprovider", target]

    def calibrate_whole_suite(self, budget_s: float = WHOLE_SUITE_MAX_S) -> Dict[str, Any]:
        """Decide whether `repo_tests` can run the repository's whole suite. Executor mode, LLM-free, once per run.

        Runs `pytest -q -rfE <tests dir>` (the first of `tests/`, `test/`,
        `testing/` that exists) on the clean checkout with `budget_s` as the
        timeout. If it finishes within budget the gate enters whole-suite
        mode: the ids that fail or error at the base commit are the baseline,
        a later synthesis fails the `repo_tests` step only on failures that
        are NOT in it, and its timeout is `WHOLE_SUITE_TIMEOUT_FACTOR` times
        the measured duration (at least `WHOLE_SUITE_MIN_TIMEOUT_S`). Over
        budget, no pytest, no tests directory, collection errors or a
        start-up failure on the clean checkout keep the proximity selection.
        An explicit `test_command` wins; `budget_s <= 0` disables. Returns a
        record for the run file: `{mode, reason, target, duration_s,
        baseline_failures, budget_s}`.

        The suite at the base commit is what any developer runs before
        opening a pull request; the instance's `test_patch` is not applied in
        the testbed, so the hidden FAIL_TO_PASS tests are not part of it.
        """
        info: Dict[str, Any] = {"mode": "proximity", "reason": "", "target": "", "duration_s": None,
                                "baseline_failures": 0, "budget_s": float(budget_s)}
        self.whole_suite = None
        if self.executor is None:
            info["reason"] = "no executor (staged mode runs the firm's own tests)"
            return info
        if self.test_command:
            info["reason"] = "explicit test_command"
            return info
        if float(budget_s) <= 0:
            info["reason"] = "disabled (budget 0)"
            return info
        target = ""
        for name in _TEST_DIR_NAMES:
            try:
                if self.executor.exists(name):
                    target = name
                    break
            except Exception:
                continue
        if not target:
            info["reason"] = "no tests/, test/ or testing/ directory"
            return info
        info["target"] = target
        argv = self._whole_suite_argv(target)
        t0 = time.monotonic()
        res = self._run_exec(argv, timeout=float(budget_s))
        text = str(res.get("stdout", "")) + str(res.get("stderr", ""))
        if int(res.get("exit_code", -1)) == 4 and "unrecognized arguments" in text:
            argv = [a for a in argv if a != "--no-header"]
            t0 = time.monotonic()
            res = self._run_exec(argv, timeout=float(budget_s))
            text = str(res.get("stdout", "")) + str(res.get("stderr", ""))
        duration = time.monotonic() - t0
        info["duration_s"] = round(duration, 2)
        rc = int(res.get("exit_code", -1))
        if res.get("status") == "timeout":
            info["reason"] = f"whole suite did not finish within {float(budget_s):.0f}s"
            return info
        if res.get("status") == "error":
            info["reason"] = f"could not run pytest ({str(res.get('stderr', ''))[:120]})"
            return info
        if "No module named pytest" in text:
            info["reason"] = "pytest is not installed in the task environment"
            return info
        if rc == 5:
            info["reason"] = f"no tests collected from {target}/"
            return info
        if rc not in (0, 1):
            info["reason"] = f"pytest exited {rc} on the clean checkout (collection or start-up errors)"
            return info
        baseline = self.failing_test_ids(text)
        tail = [l.strip() for l in str(res.get("stdout", "")).splitlines() if l.strip()]
        self.whole_suite = {
            "argv": argv, "target": target, "baseline": baseline, "duration_s": round(duration, 2),
            "timeout_s": max(WHOLE_SUITE_MIN_TIMEOUT_S, WHOLE_SUITE_TIMEOUT_FACTOR * duration),
            "summary": tail[-1][:200] if tail else "",
        }
        info.update(mode="whole_suite", baseline_failures=len(baseline), summary=self.whole_suite["summary"],
                    timeout_s=round(self.whole_suite["timeout_s"], 1))
        return info

    def _run_whole_suite(self, rel: str) -> Tuple[Dict[str, Any], str]:
        """The `repo_tests` step in whole-suite mode: fail only on failures the clean checkout did not have.

        A timeout at `timeout_s` (a multiple of the calibrated duration) FAILS
        the step -- the write is the most likely reason the suite no longer
        finishes. New failing ids are re-run once, alone, so a flaky test
        cannot revert a synthesis; the ones that fail again are regressions.
        Start-up failures use the same rule as the proximity mode.
        """
        ws = self.whole_suite or {}
        argv = list(ws.get("argv") or [])
        baseline = set(ws.get("baseline") or [])
        timeout = float(ws.get("timeout_s") or WHOLE_SUITE_MIN_TIMEOUT_S)
        label = f"{ws.get('target', 'tests')}/ (whole suite, {len(baseline)} baseline failure(s))"
        res = self._run_exec(argv, timeout=timeout)
        rc = int(res.get("exit_code", -1))
        text = str(res.get("stdout", "")) + str(res.get("stderr", ""))
        passed = dict(res, exit_code=0)
        if res.get("status") == "timeout":
            return (dict(res, exit_code=1),
                    f"whole suite did not finish within {timeout:.0f}s (clean checkout: "
                    f"{float(ws.get('duration_s') or 0):.1f}s); the write may have introduced a hang")
        if res.get("status") == "error":
            return passed, f"whole suite could not run ({str(res.get('stderr', ''))[:120]}); step skipped"
        if rc == 0:
            return passed, f"whole suite passed: {label}"
        if rc == 5:
            return passed, f"no tests collected from {label}; step skipped"
        if rc in (3, 4):
            frame = self._startup_failure_through(text, rel)
            if frame:
                error = next((l.strip() for l in text.splitlines() if l.strip().startswith("E ")), "")
                return (dict(res, exit_code=rc),
                        f"whole suite could not start (pytest exited {rc}) and the traceback runs through "
                        f"{rel}: {frame}" + (f" -- {error[:160]}" if error else ""))
            return passed, f"pytest exited {rc} (internal/usage error) on {label}; inconclusive, step skipped"
        new = [i for i in self.failing_test_ids(text) if i not in baseline]
        if not new:
            if rc == 1:
                return passed, f"whole suite: no new failure ({label})"
            return passed, f"pytest exited {rc} on {label} without a parsable new failure; inconclusive, step skipped"
        # Confirm: the new ids alone, once. Flaky tests must not revert a synthesis.
        again = self._run_exec(argv[:-1] + new[:WHOLE_SUITE_RERUN_LIMIT], timeout=timeout)
        rc2 = int(again.get("exit_code", -1))
        if rc2 == 0:
            return passed, (f"whole suite: {len(new)} new failure(s) did not reproduce when re-run alone "
                            f"(flaky): {', '.join(new[:3])}")
        if again.get("status") in ("ok", "failed") and rc2 in (1, 2):
            text2 = str(again.get("stdout", "")) + str(again.get("stderr", ""))
            still = [i for i in self.failing_test_ids(text2) if i in set(new)] or new
        else:
            still = new
        more = f" (+{len(still) - 3} more)" if len(still) > 3 else ""
        return (dict(res, exit_code=1),
                f"whole suite: {len(still)} new failure(s) not present on the clean checkout: "
                f"{', '.join(still[:3])}{more}")

    @staticmethod
    def _startup_failure_through(text: str, rel: str) -> str:
        """The first traceback frame of a pytest startup failure that lies in `rel`, else ''.

        pytest exits 3 (internal error) or 4 (usage error, which includes
        `ImportError while loading conftest`) before any test runs. When the
        environment is at fault that is inconclusive; when the traceback runs
        through the module that was just written, the write broke the test
        infrastructure and the gate has to fail. Both traceback styles are
        recognised: pytest's `path:line: in name` and Python's
        `File "path", line N`.
        """
        needle = re.escape(rel.replace("\\", "/"))
        frame = re.compile(rf"(?:^|[\s'\"/]){needle}:\d+: in |File \"[^\"]*{needle}\", line \d+")
        for line in text.splitlines():
            s = line.strip()
            if frame.search(s):
                return s[:200]
        return ""

    def _run_repo_tests(self, rel: str) -> Tuple[Dict[str, Any], str]:
        """Runs the repository's nearby tests. Returns (result with a pass/fail exit_code, note).

        Only an actual test failure (pytest exit 1, or any non-zero exit of an
        explicit `test_command`), or a pytest startup failure whose traceback
        runs through `rel`, fails the step. "Nothing collected", other pytest
        usage/internal errors, a missing pytest and a timeout are recorded as
        notes and pass: an inconclusive gate must not block every synthesis,
        and the self-oracle still has to be convinced afterwards. In
        whole-suite mode (`calibrate_whole_suite`) see `_run_whole_suite`:
        only failures absent from the clean checkout fail the step, and a
        timeout does fail it.
        """
        argv, files, mode = self._repo_tests_argv(rel)
        if mode == "none":
            return ({"status": "ok", "exit_code": 0, "stdout": "", "stderr": "", "network_isolated": None},
                    f"no test file near {rel} by the proximity heuristic; step skipped")
        if mode == "whole_suite":
            return self._run_whole_suite(rel)
        res = self._run_exec(argv, timeout=REPO_TESTS_TIMEOUT_S)
        rc = int(res.get("exit_code", -1))
        text = str(res.get("stdout", "")) + str(res.get("stderr", ""))
        if mode == "proximity" and rc == 4 and "unrecognized arguments" in text:
            argv = [a for a in argv if a not in ("--no-header",)]
            res = self._run_exec(argv, timeout=REPO_TESTS_TIMEOUT_S)
            rc = int(res.get("exit_code", -1))
            text = str(res.get("stdout", "")) + str(res.get("stderr", ""))
        label = " ".join(files) if files else (argv[-1] if mode == "test_command" and argv[:2] == ["sh", "-c"] else " ".join(argv))
        passed = dict(res, exit_code=0)
        if res.get("status") == "timeout":
            return passed, f"{mode} tests timed out after {REPO_TESTS_TIMEOUT_S}s; inconclusive, step skipped"
        if res.get("status") == "error":
            return passed, f"{mode} tests could not run ({str(res.get('stderr', ''))[:120]}); step skipped"
        if "No module named pytest" in text:
            return passed, "pytest is not installed in the task environment; step skipped"
        if rc == 0:
            return passed, f"{mode} tests passed: {label}"
        if mode == "proximity" and rc == 5:
            return passed, f"no tests collected from {label}; step skipped"
        if mode == "proximity" and rc in (3, 4):
            frame = self._startup_failure_through(text, rel)
            if frame:
                error = next((l.strip() for l in text.splitlines() if l.strip().startswith("E ")), "")
                return (dict(res, exit_code=rc),
                        f"{mode} tests could not start (pytest exited {rc}) and the traceback runs through "
                        f"{rel}: {frame}" + (f" -- {error[:160]}" if error else ""))
            return passed, f"pytest exited {rc} (internal/usage error) on {label}; inconclusive, step skipped"
        tail = [l.strip() for l in str(res.get("stdout", "")).splitlines() if l.strip()]
        summary = next((l for l in reversed(tail) if "failed" in l or "error" in l.lower()), tail[-1] if tail else "")
        return dict(res, exit_code=rc or 1), f"{mode} tests failed (exit {rc}): {label}" + (f" -- {summary[:160]}" if summary else "")

    def _verify_module_exec(self, state: EpistemicState, module_path: str,
                            question_id: str = "", hypothesis_id: str = "") -> Evidence:
        """The module gate, in place: py_compile, import, static_check (vs HEAD), repo_tests."""
        eid = state.new_evidence_id()
        rel = self._rel_path(module_path)
        step_names = ["py_compile", "import", "static_check", "repo_tests"]
        long_t = max(self.timeout_s, MODULE_CHECK_TIMEOUT_S)
        out_chunks: List[str] = []
        err_chunks: List[str] = []
        exit_code, failed_step, failure_note = 0, "", ""
        try:
            present = self.executor.exists(rel)
        except Exception as exc:
            present, failure_note = False, f"{type(exc).__name__}: {exc}"
        if not present:
            exit_code, failed_step = 2, "missing"
            err_chunks.append(f"{rel} is not present in the repository" + (f" ({failure_note})" if failure_note else ""))
            failure_note = ""
        else:
            for name in step_names:
                if name == "static_check":
                    code, err_lines, note = self._static_check_exec(rel)
                    out_chunks.append(f"[{name}] {note}")
                    if err_lines:
                        err_chunks.append(f"[{name}] " + "\n".join(err_lines))
                    if code != 0:
                        exit_code, failed_step = code, name
                        failure_note = err_lines[0] + (f" (+{len(err_lines) - 1} more)" if len(err_lines) > 1 else "")
                        break
                    continue
                if name == "repo_tests":
                    res, note = self._run_repo_tests(rel)
                    out_chunks.append(f"[{name}] {note}\n" + str(res.get("stdout", ""))[-800:])
                    if res.get("stderr"):
                        err_chunks.append(f"[{name}] " + str(res.get("stderr", ""))[-1200:])
                    if int(res.get("exit_code", 0)) != 0:
                        exit_code, failed_step, failure_note = int(res.get("exit_code", 1)), name, note
                        break
                    continue
                argv = [self.python, "-c", _COMPILE_SNIPPET, rel] if name == "py_compile" \
                    else [self.python, "-c", _IMPORT_SNIPPET, rel]
                res = self._run_exec(argv, timeout=long_t)
                out_chunks.append(f"[{name}] {str(res.get('stdout', ''))[:800]}")
                if res.get("stderr"):
                    err_chunks.append(f"[{name}] " + str(res.get("stderr", ""))[-1200:])
                if int(res.get("exit_code", -1)) != 0:
                    exit_code, failed_step = int(res.get("exit_code", -1)), name
                    err_tail = [l.strip() for l in str(res.get("stderr", "")).splitlines() if l.strip()]
                    failure_note = err_tail[-1][:200] if err_tail else ""
                    break
        passed = exit_code == 0
        if passed:
            detail = "module check passed: " + ", ".join(step_names)
        elif failure_note:
            detail = f"module check failed at {failed_step}: {failure_note}"
        else:
            detail = f"module check failed at {failed_step} (exit {exit_code})"
        return Evidence(
            evidence_id=eid, kind="module_check", hypothesis_id=hypothesis_id,
            question_id=question_id, command=f"verify_module {rel} (in place via {self._executor_name()})",
            exit_code=exit_code, stdout="\n".join(out_chunks)[:OUTPUT_CAP],
            stderr="\n".join(err_chunks)[-OUTPUT_CAP:], prediction={"expect_exit_code": 0},
            matched_prediction=passed, detail=detail, network_isolated=None)

    def _executor_name(self) -> str:
        describe = getattr(self.executor, "describe", None)
        try:
            return str(describe()) if callable(describe) else type(self.executor).__name__
        except Exception:
            return type(self.executor).__name__

    def reconcile_with_reproduction(self, state: EpistemicState, root_question_id: str,
                                    iteration: int = 0,
                                    resolved_this_iteration: Iterable[str] = ()) -> Dict[str, Any]:
        """The self-oracle: re-runs the root question's reproduction probes after a patch.

        This is NOT the hidden test suite and is weaker than it. Each
        SUPPORTED / CERTIFIED hypothesis of the root question is a probe that,
        before the patch, matched its prediction *of the buggy behaviour*. The
        probe is re-run unchanged (via `run_experiment`; the verdict is
        recorded but `apply` is not called, so the hypothesis keeps its
        status) and

            bug_no_longer_reproduces(h) :=
                the re-run completed (not rejected, no timeout, no transport error)
                AND its prediction did NOT match any more
                AND the probe exited 0.

        A probe that now crashes (non-zero exit) proves nothing except that
        something changed; a probe that still matches says the bug is still
        there. If any root probe satisfies the definition the root question is
        RESOLVED through the gatekeeper's authority; otherwise every question
        in `resolved_this_iteration` (RESOLVED by a module check this
        iteration) is reopened and its applied hypotheses falsified, exactly
        as `reconcile_with_oracle` does when the held-out suite still fails.
        With no supported root probe the verdict is `inconclusive` and nothing
        moves. One summary `Evidence` of kind "oracle" (the only kind the
        ledger lets reopen a question) carries `SELF_ORACLE_COMMAND` so it can
        never be mistaken for a held-out result.
        """
        root = state.questions[root_question_id]
        probes = [h for h in state.hypotheses_for(root_question_id) if h.status in (SUPPORTED, CERTIFIED)]
        reruns: List[Dict[str, Any]] = []
        gone: List[str] = []
        for h in probes:
            ev = self.run_experiment(state, h)
            state.record_evidence(ev, self.authority)
            completed, is_gone = self.rerun_outcome(ev)
            reruns.append({"hypothesis_id": h.hypothesis_id, "evidence_id": ev.evidence_id,
                           "completed": completed, "matched_prediction": bool(ev.matched_prediction),
                           "exit_code": int(ev.exit_code), "bug_no_longer_reproduces": is_gone,
                           "detail": ev.detail})
            if is_gone:
                gone.append(h.hypothesis_id)
        if not probes:
            verdict = "inconclusive"
        elif gone:
            verdict = "resolved"
        else:
            verdict = "still_reproduces"
        summary = Evidence(
            evidence_id=state.new_evidence_id(), kind="oracle", question_id=root_question_id,
            command=SELF_ORACLE_COMMAND, exit_code=0 if verdict == "resolved" else 1,
            stdout="\n".join(
                f"{r['hypothesis_id']}: completed={r['completed']} matched={r['matched_prediction']} "
                f"exit={r['exit_code']} bug_no_longer_reproduces={r['bug_no_longer_reproduces']}"
                for r in reruns)[:OUTPUT_CAP],
            prediction={"expect_exit_code": 0}, matched_prediction=verdict == "resolved",
            detail=(f"self-oracle, iteration {iteration}: {verdict} "
                    f"({len(gone)}/{len(probes)} reproduction probe(s) no longer reproduce the bug)"))
        state.record_evidence(summary, self.authority)
        resolved: List[str] = []
        reopened: List[str] = []
        if verdict == "resolved":
            if root.status != Q_RESOLVED:
                state.set_question_status(root_question_id, Q_RESOLVED, self.authority)
            state.lower_uncertainty(root_question_id, RESOLVED_FLOOR, self.authority)
            resolved.append(root_question_id)
        elif verdict == "still_reproduces":
            for qid in resolved_this_iteration:
                q = state.questions.get(qid)
                if q is None or q.status != Q_RESOLVED:
                    continue
                for h in state.hypotheses_for(qid):
                    if h.patch_applied and h.status == SUPPORTED:
                        state.set_hypothesis_verdict(h.hypothesis_id, FALSIFIED, 0.0,
                                                     summary.evidence_id, self.authority)
                state.reopen_question(qid, q.initial_uncertainty * REOPEN_FRACTION,
                                      summary.evidence_id, self.authority)
                reopened.append(qid)
        return {"verdict": verdict, "evidence_id": summary.evidence_id, "reruns": reruns,
                "bug_gone_hypotheses": gone, "resolved": resolved, "reopened": reopened,
                "probes_rerun": len(probes)}
