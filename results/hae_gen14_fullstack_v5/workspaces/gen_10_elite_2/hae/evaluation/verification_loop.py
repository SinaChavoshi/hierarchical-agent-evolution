"""
Generation 11 capability: in-loop ground-truth verification.

This module closes the loop between agent actions and evaluation gates.
It provides a `VerificationLoop` that allows agents to request a verification
report from the real execution harness, subject to a strict budget.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
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
        
        # Lazy import to avoid circular dependencies or import errors if harness is not ready
        # In a real scenario, this would be injected or imported at module level if stable.
        # For this implementation, we assume hae.evaluation.harness is available.
        try:
            from hae.evaluation.harness import ExecutionHarness
            self.harness = ExecutionHarness(timeout_s=timeout_s)
        except ImportError:
            # Fallback or error handling if harness is missing
            self.harness = None

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining."""
        return max(0, self.budget - len(self.attempts))

    @property
    def used(self) -> int:
        """Returns the number of verify calls used."""
        return len(self.attempts)

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.

        Args:
            turn: The current turn number.
            agent_role: The role of the agent requesting verification.

        Returns:
            A string report of the verification status.
        """
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED: Verification budget exhausted. No further checks allowed."

        if self.harness is None:
            return "ERROR: ExecutionHarness not available. Cannot verify."

        # Execute the harness
        try:
            # verify_workspace expects a workspace object and optional deliverable text
            # We pass the live workspace. Deliverable text is empty as we are verifying the live state.
            report = self.harness.verify_workspace(workspace=self.workspace, deliverable_text='')
        except Exception as e:
            # If the harness itself crashes, we record a failed attempt
            attempt = VerifyAttempt(
                turn=turn,
                agent_role=agent_role,
                gate_status={'error': 'failed'},
                passed_count=0,
                evaluable_count=0,
                authored_files=0
            )
            self.attempts.append(attempt)
            return f"ERROR: Verification failed with exception: {str(e)}"

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

        # Format the report for the agent
        # The report should be terse and state what is wrong, not how to fix it.
        lines = []
        lines.append(f"Verification Report (Attempt {len(self.attempts)}/{self.budget})")
        
        # Specific requirement: "No files have been authored" if 0 files
        if authored_files == 0:
            lines.append("No files have been authored")
        else:
            lines.append(f"Authored Files: {authored_files}")
            
        lines.append(f"Gates Passed: {passed_count}/{evaluable_count} (Evaluated)")
        
        if report.gate_status:
            lines.append("Gate Status:")
            for gate_name, status in report.gate_status.items():
                # Map internal status to uppercase for clarity if needed, 
                # but the spec says 'PASSED | FAILED | SKIPPED'
                # The harness returns 'passed', 'failed', 'skipped' (lowercase based on constants)
                # Let's normalize to uppercase for the report as per VERIFY_TOOL_GUIDE hint
                status_upper = status.upper()
                lines.append(f"  {gate_name}: {status_upper}")
                
                # If failed, include detail if available, but keep it terse
                if status == 'failed':
                    detail = report.gate_detail.get(gate_name, '')
                    if detail:
                        # Truncate long details
                        if len(detail) > 200:
                            detail = detail[:200] + "..."
                        lines.append(f"    Detail: {detail}")
        else:
            lines.append("No gates evaluated.")

        # Specific requirement: "A skip is not a pass"
        skipped_count = sum(1 for s in gate_status.values() if s == 'skipped')
        if skipped_count > 0:
            lines.append("Note: A skip is not a pass. Skipped gates earn no credit.")

        # Specific requirement: "does not parse" for syntax failure
        if gate_status.get('syntax') == 'failed':
            lines.append("Syntax error: code does not parse.")

        # Specific requirement: "2 verification attempt(s) remaining"
        rem = self.remaining
        if rem > 0:
            lines.append(f"{rem} verification attempt(s) remaining")
        else:
            lines.append("0 verification attempt(s) remaining")

        if passed_count == evaluable_count and evaluable_count > 0:
            lines.append("STATUS: ALL EVALUATED GATES PASSED")
        elif evaluable_count == 0:
            lines.append("STATUS: NO GATES EVALUATED (CHECK WORKSPACE)")
        else:
            lines.append("STATUS: SOME GATES FAILED OR SKIPPED")

        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        best_passed = 0
        for a in self.attempts:
            if a.passed_count > best_passed:
                best_passed = a.passed_count
                
        # Determine final gate status from the last attempt, or empty dict if no attempts
        final_gate_status = {}
        if self.attempts:
            final_gate_status = self.attempts[-1].gate_status
            
        return {
            "attempt_count": len(self.attempts),
            "used": len(self.attempts),
            "budget": self.budget,
            "remaining": self.remaining,
            "best_passed_count": best_passed,
            "final_gate_status": final_gate_status,
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