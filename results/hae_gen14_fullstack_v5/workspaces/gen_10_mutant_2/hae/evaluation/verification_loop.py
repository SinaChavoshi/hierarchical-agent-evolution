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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
        
        # Import harness here to ensure it's available when verify is called
        # We import inside __init__ or verify to avoid circular imports if any,
        # but typically harness is a sibling module.
        try:
            from hae.evaluation.harness import ExecutionHarness
            self.harness = ExecutionHarness(timeout_s=timeout_s)
        except ImportError:
            self.harness = None

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining in the budget."""
        return max(0, self.budget - len(self.attempts))

    @property
    def used(self) -> int:
        """Returns the number of verify calls used so far."""
        return len(self.attempts)

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED: No verification attempts remaining."

        if self.harness is None:
            return "ERROR: ExecutionHarness not available."

        # Run the harness
        try:
            report = self.harness.verify_workspace(workspace=self.workspace)
        except Exception as e:
            # If the harness itself crashes, we record a failed attempt but don't crash the loop
            attempt = VerifyAttempt(
                turn=turn,
                agent_role=agent_role,
                gate_status={},
                passed_count=0,
                evaluable_count=0,
                authored_files=0
            )
            self.attempts.append(attempt)
            return f"ERROR: Harness execution failed: {str(e)}"

        # Extract data for the attempt record
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

        # Generate terse report
        lines = []
        lines.append(f"Verification Attempt {len(self.attempts)} (Remaining: {self.remaining})")
        
        if authored_files == 0:
            lines.append("No files have been authored.")
        else:
            lines.append(f"Authored Files: {authored_files}")
            
        if not gate_status:
            lines.append("No gates evaluated.")
        else:
            for name, status in gate_status.items():
                # Map internal status to display status
                display_status = status.upper()
                if status == 'skipped':
                    display_status = 'SKIP'
                elif status == 'passed':
                    display_status = 'PASS'
                elif status == 'failed':
                    display_status = 'FAIL'
                
                lines.append(f"  {name}: {display_status}")
                
                # Include detail if failed or skipped to help debugging, but keep it terse
                if status in ('failed', 'skipped'):
                    detail = report.gate_detail.get(name, '')
                    if detail:
                        # Truncate long details
                        if len(detail) > 200:
                            detail = detail[:200] + "..."
                        lines.append(f"    Detail: {detail}")
                        
                        # Specific hints for common failures based on test expectations
                        if name == 'syntax' and status == 'failed':
                            lines.append("    Note: Code does not parse.")
                        elif name == 'build' and status == 'failed':
                            lines.append("    Note: Build failed.")
                        elif name == 'smoke' and status == 'failed':
                            lines.append("    Note: Import failed.")
                        elif name == 'tests' and status == 'failed':
                            lines.append("    Note: Tests failed.")
                        elif name == 'telemetry' and status == 'failed':
                            lines.append("    Note: Telemetry missing.")
                            
            # Add skip explanation if any skips occurred
            if any(s == 'skipped' for s in gate_status.values()):
                lines.append("  Note: A skip is not a pass. Skipped gates earn no credit.")

        lines.append(f"Pass Rate: {passed_count}/{evaluable_count} evaluated gates passed.")
        
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        if not self.attempts:
            return {
                "attempt_count": 0,
                "used": 0,
                "best_passed_count": 0,
                "best_evaluable_count": 0,
                "budget": self.budget,
                "remaining": self.remaining,
                "attempts": [],
                "final_gate_status": {},
                "converged": False
            }
            
        best_passed = max(a.passed_count for a in self.attempts)
        best_evaluable = max(a.evaluable_count for a in self.attempts)
        
        # Determine convergence: did the last attempt pass all evaluable gates?
        last_attempt = self.attempts[-1]
        converged = (last_attempt.passed_count == last_attempt.evaluable_count and last_attempt.evaluable_count > 0)
        
        return {
            "attempt_count": len(self.attempts),
            "used": len(self.attempts),
            "best_passed_count": best_passed,
            "best_evaluable_count": best_evaluable,
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
            ],
            "final_gate_status": last_attempt.gate_status,
            "converged": converged
        }