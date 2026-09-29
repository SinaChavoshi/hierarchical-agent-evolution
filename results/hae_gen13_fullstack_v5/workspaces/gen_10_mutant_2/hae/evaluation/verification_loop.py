"""
hae.evaluation.verification_loop

Generation 11 capability: in-loop ground-truth verification.

This module provides the `VerificationLoop` class which allows agents to
request a verification of their current workspace state using the same
`ExecutionHarness` that will be used for final scoring.

Key constraints:
1. Rate-limited: Each agent has a limited budget of verify calls.
2. Non-coaching: The report states what is wrong (gate verdicts, tracebacks)
   but never suggests how to fix it.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import time

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
        """
        Initialize the verification loop.

        Args:
            workspace: The live workspace object to verify.
            budget: Maximum number of verify calls allowed.
            timeout_s: Timeout for the underlying harness execution.
        """
        self.workspace = workspace
        self.budget = budget
        self.timeout_s = timeout_s
        self.attempts: List[VerifyAttempt] = []
        
        # Lazy import to avoid circular dependencies or import errors if harness
        # is not yet available in the environment during module load.
        self._harness = None

    def _get_harness(self):
        """Lazily instantiate the ExecutionHarness."""
        if self._harness is None:
            try:
                from hae.evaluation.harness import ExecutionHarness
                self._harness = ExecutionHarness(timeout_s=self.timeout_s)
            except ImportError:
                # If harness is not available, we cannot verify.
                # This should ideally not happen in the target environment.
                raise RuntimeError("ExecutionHarness not available for verification.")
        return self._harness

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining."""
        return max(0, self.budget - len(self.attempts))

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.

        Args:
            turn: The current turn number (for logging).
            agent_role: The role of the agent requesting verification.

        Returns:
            A string report containing gate statuses and details.
        """
        if self.remaining <= 0:
            return "ERROR: Verification budget exhausted. No further verifications allowed."

        try:
            harness = self._get_harness()
            # Verify the workspace. We pass the workspace object directly.
            # The harness is expected to handle extracting files from the workspace.
            report = harness.verify_workspace(workspace=self.workspace)
            
            # Extract data for the attempt record
            gate_status = report.gate_status
            passed_count = len(report.passed_gates)
            evaluable_count = len(report.evaluated_gates)
            authored_files = report.authored_files
            
            # Record the attempt
            attempt = VerifyAttempt(
                turn=turn,
                agent_role=agent_role,
                gate_status=gate_status,
                passed_count=passed_count,
                evaluable_count=evaluable_count,
                authored_files=authored_files
            )
            self.attempts.append(attempt)
            
            # Format the report for the agent
            # We want to show the status of each gate and any failure details.
            # We do NOT show how to fix it.
            
            lines = []
            lines.append(f"Verification Report (Attempt {len(self.attempts)}/{self.budget})")
            lines.append(f"Authored Files: {authored_files}")
            lines.append(f"Evaluated Gates: {evaluable_count} | Passed: {passed_count}")
            lines.append("-" * 40)
            
            for gate in report.gates:
                status_str = gate.status.upper()
                lines.append(f"[{status_str}] {gate.name}")
                if gate.status == 'failed':
                    # Include detail/traceback if available, but keep it terse
                    if gate.detail:
                        # Truncate long details to avoid context bloat
                        detail = gate.detail
                        if len(detail) > 500:
                            detail = detail[:500] + "..."
                        lines.append(f"  Detail: {detail}")
                    if gate.evidence:
                        # Optionally include key evidence if it's short
                        pass 
                elif gate.status == 'skipped':
                    lines.append(f"  Reason: {gate.detail or 'Not evaluated'}")
            
            lines.append("-" * 40)
            lines.append(f"Remaining Verifications: {self.remaining}")
            
            return "\n".join(lines)

        except Exception as e:
            # If the harness itself crashes, we record a failed attempt
            # but return an error message.
            attempt = VerifyAttempt(
                turn=turn,
                agent_role=agent_role,
                gate_status={},
                passed_count=0,
                evaluable_count=0,
                authored_files=0
            )
            self.attempts.append(attempt)
            return f"ERROR: Verification failed due to internal error: {str(e)}"

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard.

        Returns:
            A dictionary containing the verification history and final state.
        """
        if not self.attempts:
            return {
                "total_attempts": 0,
                "budget": self.budget,
                "remaining": self.remaining,
                "final_gate_status": {},
                "final_passed_count": 0,
                "final_evaluable_count": 0,
                "final_authored_files": 0,
                "history": []
            }
        
        last_attempt = self.attempts[-1]
        
        return {
            "total_attempts": len(self.attempts),
            "budget": self.budget,
            "remaining": self.remaining,
            "final_gate_status": last_attempt.gate_status,
            "final_passed_count": last_attempt.passed_count,
            "final_evaluable_count": last_attempt.evaluable_count,
            "final_authored_files": last_attempt.authored_files,
            "history": [
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