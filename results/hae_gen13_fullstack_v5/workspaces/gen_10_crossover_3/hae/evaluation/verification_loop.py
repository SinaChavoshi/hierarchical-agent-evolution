"""
In-loop ground-truth verification for agent workspaces.

This module provides the `VerificationLoop` class which allows agents to
request a verification of their current workspace state against the real
grading harness. It enforces a budget on these expensive operations and
records the history for scoring purposes.

The report returned is terse and factual, echoing gate verdicts and tracebacks
without providing coaching or suggestions for fixes.
"""

import logging
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

logger = logging.getLogger(__name__)


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
            timeout_s: Timeout for the harness execution in seconds.
        """
        self.workspace = workspace
        self.budget = budget
        self.timeout_s = timeout_s
        self.attempts: List[VerifyAttempt] = []
        
        # Lazy import to avoid circular dependencies or import errors if harness
        # is not yet available in the environment during module load.
        self._harness = None

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining in the budget."""
        return max(0, self.budget - len(self.attempts))

    def _get_harness(self):
        """Lazily initializes and returns the ExecutionHarness."""
        if self._harness is None:
            try:
                from hae.evaluation.harness import ExecutionHarness
                self._harness = ExecutionHarness(timeout_s=self.timeout_s)
            except ImportError:
                logger.error("ExecutionHarness not available. Verification will fail.")
                return None
        return self._harness

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """
        Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.

        Args:
            turn: The current turn number.
            agent_role: The role of the agent requesting verification.

        Returns:
            A string report containing gate statuses and details.
        """
        if self.remaining <= 0:
            return (
                "ERROR: Verification budget exhausted. "
                f"Used {len(self.attempts)} of {self.budget} attempts. "
                "No further verification calls allowed."
            )

        harness = self._get_harness()
        if harness is None:
            return "ERROR: Verification harness unavailable."

        try:
            # Execute the harness against the live workspace
            # We pass empty deliverable_text because we are verifying the live workspace
            report = harness.verify_workspace(workspace=self.workspace, deliverable_text='')
        except Exception as e:
            logger.exception("Harness execution failed")
            return f"ERROR: Harness execution failed: {str(e)}"

        # Extract gate statuses
        gate_status = report.gate_status
        
        # Count passed and evaluable gates
        passed_count = 0
        evaluable_count = 0
        for name, status in gate_status.items():
            if status == 'passed':
                passed_count += 1
                evaluable_count += 1
            elif status == 'failed':
                evaluable_count += 1
            # skipped gates are not counted in evaluable_count for the denominator
            # but we track them in the status dict

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

        # Generate the report string
        lines = []
        lines.append(f"Verification Report (Attempt {len(self.attempts)}/{self.budget})")
        lines.append(f"Authored Files: {report.authored_files}")
        lines.append(f"Python Files: {report.python_files}")
        lines.append("")
        
        # Sort gates for consistent output
        gate_names = sorted(gate_status.keys())
        
        for name in gate_names:
            status = gate_status[name]
            # Map internal status to uppercase for display
            display_status = status.upper()
            lines.append(f"  {name}: {display_status}")
            
            # Include detail if available and not empty
            detail = report.gate_detail.get(name, '')
            if detail:
                # Truncate long details to keep report terse
                if len(detail) > 500:
                    detail = detail[:500] + "..."
                lines.append(f"    Detail: {detail}")

        lines.append("")
        lines.append(f"Remaining Verifications: {self.remaining}")
        
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """
        Verification history for the scorecard.

        Returns:
            A dictionary containing the verification history and final state.
        """
        if not self.attempts:
            return {
                "attempts": 0,
                "final_gate_status": {},
                "final_passed_count": 0,
                "final_evaluable_count": 0,
                "final_authored_files": 0,
                "history": []
            }

        last_attempt = self.attempts[-1]
        
        return {
            "attempts": len(self.attempts),
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