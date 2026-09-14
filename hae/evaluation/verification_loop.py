"""Generation 11 capability: in-loop ground-truth verification.

Through Generation 10 the agents and the evaluator were looking at different
things. Agents could run `execute_bash` and see raw pytest output, but the
score was set by gates they never saw -- and those gates were heuristics, so
even an agent that had somehow inspected them would have learned the wrong
lesson. The measured consequences across 60 archived firms:

    19 of 60 firms shipped Python that does not parse
    telemetry passed 55/60 on heuristics, 15/60 when actually executed
    no firm has ever passed all five gates

This module closes that loop. A technical agent can issue

    Action: verify

and receive back exactly the gate report that will determine its score,
computed by the same `ExecutionHarness` the evaluator uses. There is no second
implementation to drift.

Two deliberate constraints.

**Verification is rate-limited.** Each department pod gets a small budget of
verify calls. The harness installs packages and runs test suites, so it is the
most expensive tool available, and an unbudgeted agent will spin on it. The
budget also preserves the thing being measured: a firm that needs twenty
attempts to produce parseable code is not equivalent to one that gets it right
the first time, and the report says which happened.

**The report states what is wrong, never how to fix it.** It echoes real
tracebacks and real gate verdicts. It does not suggest edits. Selection
pressure is supposed to come from the population, not from the harness
coaching every firm toward the same answer.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hae.evaluation.artifacts import filter_bundle
from hae.evaluation.harness import FAILED, PASSED, SKIPPED, ExecutionHarness

# Verification is the most expensive action an agent can take. Enough attempts
# to fix a syntax error and re-check; not enough to brute-force.
DEFAULT_VERIFY_BUDGET = 3

# Keep observations small: they are re-sent with every subsequent turn.
_MAX_DETAIL_CHARS = 400


@dataclass
class VerifyAttempt:
    """One verification call, recorded for the scorecard."""
    turn: int
    agent_role: str
    gate_status: Dict[str, str] = field(default_factory=dict)
    passed_count: int = 0
    evaluable_count: int = 0
    authored_files: int = 0


class VerificationLoop:
    """Runs the real execution harness against a live workspace, on request.

    Holds the per-firm verify budget and the attempt history, so a scorecard
    can report not just the final gate state but how many attempts it took --
    a firm that verifies once and passes is measurably different from one that
    needed three rounds.
    """

    def __init__(self, workspace: Any, budget: int = DEFAULT_VERIFY_BUDGET,
                 timeout_s: int = 45):
        self.workspace = workspace
        self.budget = budget
        self.used = 0
        self.attempts: List[VerifyAttempt] = []
        self.harness = ExecutionHarness(timeout_s=timeout_s)

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    def _bundle(self) -> Dict[str, str]:
        """Current authored files, with build byproducts excluded.

        Uses the same filter as the evaluator, so an agent cannot inflate its
        apparent output by invoking pytest and having `.pytest_cache/` counted.
        """
        try:
            raw = self.workspace.export_bundle()
        except Exception:
            return {}
        return filter_bundle(raw or {})

    def verify(self, turn: int = 0, agent_role: str = "") -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return ("Observation (verify): BUDGET EXHAUSTED. "
                    f"All {self.budget} verification attempts have been used. "
                    "Submit your work as it stands.")

        bundle = self._bundle()
        if not bundle:
            self.used += 1
            return ("Observation (verify): No files have been authored yet. "
                    "Nothing to verify. "
                    f"({self.remaining} verification attempt(s) remaining.)")

        self.used += 1
        report = self.harness.verify_bundle(bundle)
        # `report.gates` is a list of GateResult; index it by name.
        by_name = {gate.name: gate for gate in report.gates}
        status = {name: gate.status for name, gate in by_name.items()}

        passed = sum(1 for s in status.values() if s == PASSED)
        evaluable = sum(1 for s in status.values() if s != SKIPPED)
        self.attempts.append(VerifyAttempt(
            turn=turn,
            agent_role=agent_role,
            gate_status=dict(status),
            passed_count=passed,
            evaluable_count=evaluable,
            authored_files=len(bundle),
        ))

        return self._format(by_name, status, passed, evaluable, len(bundle))

    def _format(self, by_name, status, passed, evaluable, file_count) -> str:
        lines = [
            "Observation (verify): GROUND-TRUTH EXECUTION REPORT",
            f"These are the exact gates your firm will be scored on. "
            f"{passed}/{evaluable} evaluable gate(s) passing across "
            f"{file_count} authored file(s).",
            "",
        ]
        for name in ("syntax", "build", "smoke", "tests", "telemetry"):
            gate = by_name.get(name)
            if gate is None:
                continue
            marker = {PASSED: "PASS", FAILED: "FAIL", SKIPPED: "SKIP"}.get(
                gate.status, gate.status.upper())
            detail = (gate.detail or "").strip().replace("\n", " ")
            if len(detail) > _MAX_DETAIL_CHARS:
                detail = detail[:_MAX_DETAIL_CHARS] + " ..."
            lines.append(f"  [{marker}] {name}: {detail}")

        lines.append("")
        if status.get("syntax") == FAILED:
            lines.append("A file does not parse. Nothing downstream of syntax "
                         "can be trusted until that is fixed.")
        lines.append(
            "SKIP means the gate could not be evaluated in this environment "
            "(usually an uninstalled third-party package). A skip is not a "
            "pass and earns no credit."
        )
        lines.append(f"({self.remaining} verification attempt(s) remaining.)")
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        best = max((a.passed_count for a in self.attempts), default=0)
        final = self.attempts[-1] if self.attempts else None
        return {
            "budget": self.budget,
            "used": self.used,
            "attempt_count": len(self.attempts),
            "best_passed_count": best,
            "final_gate_status": dict(final.gate_status) if final else {},
            # True when the firm's last self-check was also its best: it
            # improved or held, rather than regressing after verifying.
            "converged": bool(final and final.passed_count >= best),
            "attempts": [
                {
                    "turn": a.turn,
                    "agent_role": a.agent_role,
                    "gate_status": a.gate_status,
                    "passed": a.passed_count,
                    "evaluable": a.evaluable_count,
                    "authored_files": a.authored_files,
                }
                for a in self.attempts
            ],
        }


VERIFY_TOOL_GUIDE = (
    "- To check your work against the REAL grading gates:\n"
    "  Action: verify\n"
    "  This parses, installs, imports, tests and inspects your workspace with\n"
    "  the same harness that will score your firm, and returns the verdict.\n"
    "  It is expensive and strictly limited -- you get a small number of\n"
    "  attempts for the whole assignment, so write your code first and verify\n"
    "  when you believe it is complete.\n"
    "  Note: a gate reported as SKIP was not evaluated and earns no credit.\n"
    "  Only PASS counts.\n"
)
