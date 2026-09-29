"""
Generation 11 capability: in-loop ground-truth verification.

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

import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List

# Constants
DEFAULT_VERIFY_BUDGET = 3

VERIFY_TOOL_GUIDE = (
    '- To check your work against the REAL grading gates:\n'
    '  Action: verify\n'
    '  This parses, installs, imports, tests and inspects your workspace with\n'
    '  the same harness that will score your firm, and returns the verdict.\n'
    '  It is expensive and strictly limited -- you get a small number of\n'
    '  attempts for the whole assignment, so write your code first and verify\n'
    '  when you believe it is complete.\n'
    '  Note: a gate reported as SKIP was not evaluated and earns no credit.\n'
    '  Only PASS counts.\n'
)


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

    def __init__(self, workspace: Any, budget: int = DEFAULT_VERIFY_BUDGET, timeout_s: int = 45):
        self.workspace = workspace
        self.budget = budget
        self.timeout_s = timeout_s
        self.attempts: List[VerifyAttempt] = []
        self._harness = None

    @property
    def remaining(self) -> int:
        return max(0, self.budget - len(self.attempts))

    def _get_harness(self):
        if self._harness is None:
            try:
                from hae.evaluation.harness import ExecutionHarness
                self._harness = ExecutionHarness(timeout_s=self.timeout_s)
            except ImportError:
                # Fallback if harness is not available or fails to import
                self._harness = None
        return self._harness

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return "VERIFY BUDGET EXHAUSTED. No further verification calls allowed."

        harness = self._get_harness()
        if harness is None:
            return "ERROR: ExecutionHarness unavailable. Cannot verify."

        try:
            # Attempt to get deliverable text if workspace supports it, else empty
            deliverable_text = ""
            if hasattr(self.workspace, 'get_deliverable_text'):
                deliverable_text = self.workspace.get_deliverable_text()
            
            report = harness.verify_workspace(self.workspace, deliverable_text)
            
            gate_status = report.gate_status
            passed_count = len(report.passed_gates)
            evaluable_count = len(report.evaluated_gates)
            authored_files = report.authored_files
            
            attempt = VerifyAttempt(
                turn=turn,
                agent_role=agent_role,
                gate_status=gate_status,
                passed_count=passed_count,
                evaluable_count=evaluable_count,
                authored_files=authored_files
            )
            self.attempts.append(attempt)
            
            # Construct terse report
            lines = []
            lines.append(f"VERIFY REPORT (Attempt {len(self.attempts)}/{self.budget})")
            lines.append(f"Authored Files: {authored_files}")
            lines.append(f"Gates Passed: {passed_count}/{evaluable_count} (Evaluated)")
            
            for name, status in gate_status.items():
                # Map internal status to display status
                display_status = status.upper()
                if display_status == 'PASSED':
                    display_status = 'PASS'
                elif display_status == 'FAILED':
                    display_status = 'FAIL'
                elif display_status == 'SKIPPED':
                    display_status = 'SKIP'
                
                lines.append(f"  [{display_status}] {name}")
                
                # Include detail if failed or skipped to help agent understand state
                # But strictly no coaching. Just the raw detail from the harness.
                if status in ('failed', 'skipped'):
                    detail = report.gate_detail.get(name, '')
                    if detail:
                        # Truncate long details
                        short_detail = detail[:200] + "..." if len(detail) > 200 else detail
                        lines.append(f"      Detail: {short_detail}")
            
            return "\n".join(lines)

        except Exception as e:
            # If the harness itself crashes, we record a failed attempt
            error_msg = f"VERIFY ERROR: {str(e)}\n{traceback.format_exc()}"
            attempt = VerifyAttempt(
                turn=turn,
                agent_role=agent_role,
                gate_status={},
                passed_count=0,
                evaluable_count=0,
                authored_files=0
            )
            self.attempts.append(attempt)
            return error_msg

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        return {
            "total_attempts": len(self.attempts),
            "budget": self.budget,
            "remaining": self.remaining,
            "attempts": [
                {
                    "turn": a.turn,
                    "agent_role": a.agent_role,
                    "gate_status": a.gate_status,
                    "passed_count": a.passed_count,
                    "evaluable_count": a.evaluable_count,
                    "authored_files": a.authored_files
                }
                for a in self.attempts
            ]
        }