"""Ground truth, behind one interface.

V1 had exactly one way to check a firm's work: five Python-specific execution
gates, hardcoded into `ExecutionHarness`, plus an LLM judge scoring prose. Any
task that was not "write a Python package" had no ground truth at all, and fell
back to the judge -- which saturates. By Generation 10 the judge was returning
99.0 (sigma 1.67) on coherence and actionability, and gross sigma across the
whole population had collapsed from 9.23 to 4.54. A scorer that cannot
distinguish its candidates is not measuring them.

So the long-term requirement -- a user supplies a task, the platform checks the
result -- is really the requirement that *the verifier is a parameter of the
task*, not a constant of the platform.

This module is that seam. It changes no behaviour today: `ExecutionGateVerifier`
wraps the harness we already run, and `BenchmarkVerifier` wraps the self-hosting
benchmark we already run. What it buys is that adding a verifier family later is
an implementation of an interface rather than an edit to the tournament runner.

`NullVerifier` is deliberately loud. Prose-only scoring is a legitimate thing to
do while exploring, and an illegitimate thing to do by accident, so it reports
`evaluable=False` and every consumer must decide what to do about that rather
than receiving a plausible-looking number.
"""

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hae.evaluation.benchmark import BenchmarkError, SelfHostingBenchmark
from hae.evaluation.harness import ExecutionHarness, VerificationReport


@dataclass
class Submission:
    """What a firm produced, in the two forms a verifier might want.

    `files` is authoritative. `deliverable_text` exists because a firm can
    describe code it never wrote, and the harness mines fenced code blocks out
    of it as a fallback -- but only ever as *code*, never as evidence. An essay
    about OpenTelemetry has never satisfied the telemetry gate and must not
    start now.
    """

    files: Dict[str, str] = field(default_factory=dict)
    deliverable_text: str = ""
    workspace: Any = None

    @property
    def is_empty(self) -> bool:
        return not self.files and not self.deliverable_text.strip()


@dataclass
class VerificationOutcome:
    """A verifier's verdict.

    `score` is Optional and `evaluable` is explicit, because "the verifier could
    not reach a verdict" and "the verifier scored this zero" are different
    facts. V1 conflated them: a firm that produced nothing and a firm whose
    gates all failed both arrived at the fitness function as zeros, and the
    difference only surfaced during the execution-grounded correction.
    """

    verifier: str
    evaluable: bool
    score: Optional[float] = None          # 0-100 when evaluable
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    # Per-gate verdicts, when the verifier has that shape. The rubric's
    # `execution_integrity` dimension consumes this.
    gate_status: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.evaluable and self.score is None:
            raise ValueError(
                f"{self.verifier}: evaluable outcome must carry a score")
        if self.score is not None and not 0.0 <= self.score <= 100.0:
            raise ValueError(
                f"{self.verifier}: score {self.score} outside [0, 100]")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verifier": self.verifier,
            "evaluable": self.evaluable,
            "score": self.score,
            "detail": self.detail,
            "gate_status": self.gate_status,
            "evidence": self.evidence,
        }


class Verifier(abc.ABC):
    """Decides, from evidence, how well a submission met a task.

    Implementations must never consult an LLM. The judge is a separate,
    deliberately separate, signal: the whole point of a verifier is to be the
    part of the score that cannot be talked into a different answer.
    """

    #: Stable identifier recorded on every scorecard.
    name: str = "verifier"

    @abc.abstractmethod
    def verify(self, submission: Submission) -> VerificationOutcome:
        """Returns a verdict. Must not raise for a bad submission -- a
        submission that cannot be graded is a result, not an error."""

    def describe(self) -> str:
        """One line for the run log and the scorecard."""
        return self.name


class ExecutionGateVerifier(Verifier):
    """The five execution gates: syntax, build, smoke, tests, telemetry.

    Wraps `ExecutionHarness` unchanged. The score is the fraction of *evaluated*
    gates that passed; skipped gates are excluded from the denominator rather
    than counted as failures, so a task with no tests to run is not punished for
    the absence.
    """

    name = "execution-gates"

    def __init__(self, harness: Optional[ExecutionHarness] = None):
        self.harness = harness or ExecutionHarness()

    def verify(self, submission: Submission) -> VerificationOutcome:
        report: VerificationReport = self.harness.verify_workspace(
            workspace=submission.workspace,
            deliverable_text=submission.deliverable_text,
        )
        evaluated = report.evaluated_gates
        if not evaluated:
            return VerificationOutcome(
                verifier=self.name,
                evaluable=False,
                detail="no gate could be evaluated; nothing runnable was produced",
                gate_status=report.gate_status,
                evidence=report.to_dict(),
            )
        return VerificationOutcome(
            verifier=self.name,
            evaluable=True,
            score=round(100.0 * len(report.passed_gates) / len(evaluated), 2),
            detail=report.summary(),
            gate_status=report.gate_status,
            evidence=report.to_dict(),
        )

    def describe(self) -> str:
        return f"{self.name} (5 gates, {self.harness.timeout_s}s timeout)"


class BenchmarkVerifier(Verifier):
    """Grades against a held-out test suite from the self-hosting benchmark."""

    name = "self-hosting-benchmark"

    def __init__(self, task_id: str,
                 benchmark: Optional[SelfHostingBenchmark] = None):
        self.task_id = task_id
        self.benchmark = benchmark or SelfHostingBenchmark()

    def verify(self, submission: Submission) -> VerificationOutcome:
        try:
            task = self.benchmark.task(self.task_id)
            # Fulfil the contract in the task prompt: "Nothing you write outside
            # <target_module> is read." A firm running pytest inside its
            # workspace will write scratch unit tests (`tests/test_*.py`); if
            # the entire workspace bundle were forwarded, the benchmark's
            # anti-tampering check would reject every firm that tested its own
            # code -- penalising the exact behaviour V2 exists to select for.
            target = task.target_module
            candidate = ({target: submission.files[target]}
                         if target in submission.files else {})
            result = self.benchmark.evaluate(self.task_id, candidate)
        except BenchmarkError as exc:
            # The reference suite is not green, so the benchmark cannot grade
            # anyone. That is our bug, not the firm's, and it must not be
            # recorded as the firm scoring zero.
            return VerificationOutcome(
                verifier=self.name, evaluable=False,
                detail=f"benchmark unavailable: {exc}",
                evidence={"task_id": self.task_id})
        return VerificationOutcome(
            verifier=self.name,
            evaluable=True,
            score=result.score,
            detail=(result.rejection_reason if result.rejected else
                    f"{result.tests_passed}/{result.reference_total} held-out tests"),
            evidence=result.to_dict(),
        )

    def describe(self) -> str:
        return f"{self.name}:{self.task_id}"


class NullVerifier(Verifier):
    """No ground truth. Scoring falls entirely to the LLM judge.

    Legitimate for exploratory prose objectives. Reports `evaluable=False` so
    that no consumer can mistake the absence of a measurement for a measurement,
    which is precisely the error that produced the retracted Generation 9 and 10
    headline numbers.
    """

    name = "none"

    def __init__(self, reason: str = "task declares no ground-truth verifier"):
        self.reason = reason

    def verify(self, submission: Submission) -> VerificationOutcome:
        return VerificationOutcome(
            verifier=self.name, evaluable=False, detail=self.reason)

    def describe(self) -> str:
        return f"{self.name} (UNVERIFIED: {self.reason})"


class CompositeVerifier(Verifier):
    """Several verifiers, combined by weight over those that could evaluate.

    Renormalises over evaluable members, so one unavailable verifier degrades
    the confidence of the result rather than silently dragging the score toward
    zero.
    """

    name = "composite"

    def __init__(self, members: List[Verifier],
                 weights: Optional[List[float]] = None):
        if not members:
            raise ValueError("CompositeVerifier needs at least one member")
        if weights is not None and len(weights) != len(members):
            raise ValueError("weights must match members")
        self.members = members
        self.weights = weights or [1.0] * len(members)

    def verify(self, submission: Submission) -> VerificationOutcome:
        outcomes = [m.verify(submission) for m in self.members]
        usable = [(o, w) for o, w in zip(outcomes, self.weights) if o.evaluable]
        gate_status: Dict[str, str] = {}
        for o in outcomes:
            gate_status.update(o.gate_status)

        if not usable:
            return VerificationOutcome(
                verifier=self.name, evaluable=False,
                detail="no member verifier could evaluate this submission",
                gate_status=gate_status,
                evidence={"members": [o.to_dict() for o in outcomes]})

        total_w = sum(w for _, w in usable)
        score = sum(o.score * w for o, w in usable) / total_w
        return VerificationOutcome(
            verifier=self.name,
            evaluable=True,
            score=round(score, 2),
            detail="; ".join(f"{o.verifier}={o.score}" for o, _ in usable),
            gate_status=gate_status,
            evidence={"members": [o.to_dict() for o in outcomes]})

    def describe(self) -> str:
        return f"{self.name}[{', '.join(m.describe() for m in self.members)}]"
