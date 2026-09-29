"""
Generation 11 capability: in-loop ground-truth verification.

This module closes the loop between agent actions and evaluator scoring.
It provides a `VerificationLoop` that agents can invoke to receive the exact
gate report that will determine their score, computed by the same
`ExecutionHarness` the evaluator uses.

Key constraints:
1. Verification is rate-limited (budgeted) to prevent spinning on expensive checks.
2. The report states what is wrong (verdicts/tracebacks), never how to fix it.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Import dependencies from parallel departments
# These are expected to be available in the environment
try:
    from hae.evaluation.harness import ExecutionHarness, VerificationReport, PASSED, FAILED, SKIPPED
except ImportError:
    # Fallback for isolated testing if harness isn't fully wired yet, 
    # though in production this should fail fast or be mocked.
    # For the purpose of this implementation, we assume the contract holds.
    pass

try:
    from hae.evaluation.artifacts import filter_bundle, partition_bundle
except ImportError:
    pass

logger = logging.getLogger(__name__)

DEFAULT_VERIFY_BUDGET = 3

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
        """
        Initialize the verification loop.
        
        Args:
            workspace: The live agent workspace object (must support bundle extraction or directory access).
            budget: Number of allowed verify calls.
            timeout_s: Timeout for the underlying harness execution.
        """
        self.workspace = workspace
        self.budget = budget
        self.timeout_s = timeout_s
        self.attempts: List[VerifyAttempt] = []
        
        # Initialize the harness once. 
        # Note: The harness is stateless regarding the workspace, it takes it as an argument.
        self.harness = ExecutionHarness(timeout_s=timeout_s)

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining in the budget."""
        return max(0, self.budget - len(self.attempts))

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design. It echoes real tracebacks and real gate verdicts. It does not
        suggest edits.

        Args:
            turn: The current turn number (for logging/scorecard).
            agent_role: The role of the agent requesting verification.

        Returns:
            A string report of the verification status.
        """
        if self.remaining <= 0:
            return "VERIFY BUDGET EXHAUSTED. No further verification calls allowed."

        # Execute the harness against the current workspace state
        # We pass the workspace directly. The harness knows how to extract bundles/dirs.
        try:
            report: VerificationReport = self.harness.verify_workspace(workspace=self.workspace)
        except Exception as e:
            # If the harness itself crashes, we report that failure.
            # This is a critical failure mode.
            error_msg = f"HARNESS ERROR: {str(e)}"
            logger.exception("Harness execution failed")
            return error_msg

        # Extract gate statuses for the attempt record
        gate_status = report.gate_status
        
        # Count passed and evaluable gates
        # Evaluable excludes SKIPPED
        evaluable_count = len(report.evaluated_gates)
        passed_count = len(report.passed_gates)
        
        # Record the attempt
        attempt = VerifyAttempt(
            turn=turn,
            agent_role=agent_role,
            gate_status=gate_status,
            passed_count=passed_count,
            evaluable_count=evaluable_count,
            authored_files=report.authored_files
        )
        self.attempts.append(attempt)

        # Generate the agent-readable report
        # The report must be terse and factual.
        lines = []
        lines.append(f"VERIFICATION REPORT (Attempt {len(self.attempts)}/{self.budget})")
        lines.append(f"Authored Files: {report.authored_files}")
        lines.append(f"Python Files: {report.python_files}")
        lines.append(f"Pass Rate: {report.pass_rate:.2%} ({passed_count}/{evaluable_count} evaluated gates passed)")
        lines.append("-" * 40)
        
        for gate in report.gates:
            status_str = gate.status.upper()
            # Only show detail if it's not passed, or if it's skipped (to explain why)
            if gate.status == PASSED:
                lines.append(f"[{status_str}] {gate.name}")
            elif gate.status == SKIPPED:
                lines.append(f"[{status_str}] {gate.name}: {gate.detail}")
            else: # FAILED
                lines.append(f"[{status_str}] {gate.name}")
                if gate.detail:
                    # Truncate long tracebacks/details to keep context small
                    detail = gate.detail
                    if len(detail) > 500:
                        detail = detail[:500] + "... [truncated]"
                    lines.append(f"  Detail: {detail}")
        
        lines.append("-" * 40)
        lines.append(f"Remaining Verifications: {self.remaining}")
        
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard.
        
        Returns:
            A dictionary containing the verification history and final state.
        """
        if not self.attempts:
            return {
                "attempts": [],
                "total_attempts": 0,
                "final_gate_status": {},
                "final_passed_count": 0,
                "final_evaluable_count": 0,
                "final_authored_files": 0,
                "budget_used": 0,
                "budget_remaining": self.budget
            }

        last_attempt = self.attempts[-1]
        
        return {
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
            "total_attempts": len(self.attempts),
            "final_gate_status": last_attempt.gate_status,
            "final_passed_count": last_attempt.passed_count,
            "final_evaluable_count": last_attempt.evaluable_count,
            "final_authored_files": last_attempt.authored_files,
            "budget_used": len(self.attempts),
            "budget_remaining": self.remaining
        }

VERIFY_TOOL_GUIDE = '- To check your work against the REAL grading gates:\n  Action: verify\n  This parses, installs, imports, tests and inspects your workspace with\n  the same harness that will score your firm, and returns the verdict.\n  It is expensive and strictly limited -- you get a small number of\n  attempts for the whole assignment, so write your code first and verify\n  when you believe it is complete.\n  Note: a gate reported as SKIP was not evaluated and earns no credit.\n  Only PASS counts.\n'