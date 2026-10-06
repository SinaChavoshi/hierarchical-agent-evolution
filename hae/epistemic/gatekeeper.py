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
    certify a question the probes only supported, and reopen one the probes
    wrongly settled.

Oracle leakage, stated plainly: this gatekeeper never runs the held-out
suites. Its only contact with the oracle is the list of failing test names
the worker already shows the firm under V5.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, Q_EXHAUSTED, Q_OPEN, Q_RESOLVED, SUPPORTED, UNVERIFIED,
    EpistemicState, Evidence, GatekeeperAuthority, Hypothesis, Question,
)

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

INCONCLUSIVE = "inconclusive"
PROBE_DIR = "_probe"
MAX_PROBE_CHARS = 6000
OUTPUT_CAP = 4000

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


def failure_summary(failure: str, limit: int = 220) -> str:
    """The exception part of an oracle line, for a question's text."""
    if "->" in failure:
        tail = failure.split("->", 1)[1].strip()
    else:
        tail = failure.strip()
    return " ".join(tail.split())[:limit]


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
    """Runs probes, compares them to predictions, and writes verdicts."""

    def __init__(self, workspace: Any, timeout_s: int = 20, max_probe_lines: int = 40,
                 graded_modules: Optional[Iterable[str]] = None,
                 module_for_tag: Optional[Mapping[str, str]] = None,
                 repo_root: Optional[str] = None,
                 python_executable: Optional[str] = None,
                 isolate: bool = True, stage_reference: bool = True,
                 logger: Callable[[str], None] = print) -> None:
        self.workspace = workspace
        self.timeout_s = max(1, int(timeout_s))
        self.max_probe_lines = max(1, int(max_probe_lines))
        self.graded_modules = sorted({m.lstrip("./") for m in (graded_modules or [])})
        self.module_for_tag = dict(module_for_tag or {})
        self.stage_reference = stage_reference
        self.logger = logger
        self.authority = GatekeeperAuthority("EvidenceGatekeeper")
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

    def run_experiment(self, state: EpistemicState, hypothesis: Hypothesis) -> Evidence:
        """Runs a hypothesis' probe. Returns evidence; writes nothing yet."""
        eid = state.new_evidence_id()
        reason = self.validate_probe(hypothesis.probe_code)
        if reason:
            self.probes_rejected += 1
            return Evidence(
                evidence_id=eid, kind="probe_rejected",
                hypothesis_id=hypothesis.hypothesis_id, question_id=hypothesis.question_id,
                command="", exit_code=-2, prediction=dict(hypothesis.prediction),
                matched_prediction=False, detail=f"rejected: {reason}")
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
        return Evidence(
            evidence_id=eid, kind="probe",
            hypothesis_id=hypothesis.hypothesis_id, question_id=hypothesis.question_id,
            command=f"python {probe_rel}", exit_code=int(result.get("exit_code", -1)),
            stdout=str(result.get("stdout", ""))[:OUTPUT_CAP],
            stderr=str(result.get("stderr", ""))[-OUTPUT_CAP:],
            prediction=dict(hypothesis.prediction), matched_prediction=matched,
            detail=detail, network_isolated=result.get("network_isolated"))

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

    def verify_module(self, state: EpistemicState, module_path: str,
                      question_id: str = "", hypothesis_id: str = "") -> Evidence:
        """Compiles, imports and (if the firm wrote tests) tests a module in the stage."""
        eid = state.new_evidence_id()
        rel = module_path.replace("\\", "/").lstrip("./")
        dotted = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")
        stage = self._build_stage()
        steps: List[Tuple[str, List[str]]] = [
            ("py_compile", [self.python, "-m", "py_compile", rel]),
            ("import", [self.python, "-c", f"import importlib; importlib.import_module({dotted!r}); print('IMPORT_OK')"]),
        ]
        if os.path.isdir(os.path.join(stage, "tests")):
            steps.append(("firm_tests", [self.python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]))
        out_chunks: List[str] = []
        err_chunks: List[str] = []
        exit_code = 0
        failed_step = ""
        isolated: Optional[bool] = None
        try:
            if not os.path.exists(os.path.join(stage, rel)):
                exit_code, failed_step = 2, "missing"
                err_chunks.append(f"{rel} is not present in the workspace")
            else:
                for name, argv in steps:
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
        detail = ("module check passed: " + ", ".join(n for n, _ in steps)) if passed \
            else f"module check failed at {failed_step} (exit {exit_code})"
        return Evidence(
            evidence_id=eid, kind="module_check", hypothesis_id=hypothesis_id,
            question_id=question_id, command=f"verify_module {rel}", exit_code=exit_code,
            stdout="\n".join(out_chunks)[:OUTPUT_CAP], stderr="\n".join(err_chunks)[-OUTPUT_CAP:],
            prediction={"expect_exit_code": 0}, matched_prediction=passed,
            detail=detail, network_isolated=isolated)

    def apply_synthesis(self, state: EpistemicState, question: Question,
                        hypothesis: Hypothesis, evidence: Evidence) -> float:
        """Records a post-synthesis module check and moves the question to RESOLVED if it passed."""
        state.record_evidence(evidence, self.authority)
        if not evidence.matched_prediction:
            hypothesis.synthesis_failures += 1
            return 0.0
        hypothesis.patch_applied = True
        state.set_question_status(question.question_id, Q_RESOLVED, self.authority)
        return state.lower_uncertainty(question.question_id, RESOLVED_FLOOR, self.authority)

    # ------------------------------------------------------------------ #
    # Oracle reconciliation (the one source that outranks probes)
    # ------------------------------------------------------------------ #

    def reconcile_with_oracle(self, state: EpistemicState, failures: Iterable[str],
                              iteration: int = 0, default_module: str = "") -> Dict[str, Any]:
        """Certifies questions whose failure vanished, reopens those that did not, seeds new ones."""
        failures = [str(f) for f in (failures or []) if str(f).strip()]
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
            q.source_failure = now[q.source_failure_key]
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
        known = {q.source_failure_key for q in state.questions.values() if q.source_failure_key}
        for key, line in now.items():
            if key in known:
                continue
            _, tag, name = failure_key(line)
            module = self.module_for_tag.get(tag, default_module) if tag else default_module
            summary = failure_summary(line)
            if summary.startswith("[") and "]" in summary:
                summary = summary.split("]", 1)[1].strip()
            if name == SUITE_IMPORT_NAME:
                # The suite could not import its module at all -- usually the
                # file does not exist yet. The question must name the module so
                # that the synthesis move creates *that* file.
                text = (f"Why can the `{tag}` suite not import"
                        + (f" `{module}`" if module else " its module")
                        + f"? Oracle: {summary}")
            else:
                text = (f"Why does `{name or key}` fail"
                        + (f" in `{module}`" if module else "")
                        + f"? Oracle: {summary}")
            q = state.add_question(text=text, module=module, uncertainty=1.0,
                                   source_failure_tag=tag, source_failure_key=key, source_failure=line)
            seeded.append(q.question_id)
        return {"evidence_id": evidence.evidence_id, "certified": certified,
                "reopened": reopened, "seeded": seeded, "failing_now": len(now)}
