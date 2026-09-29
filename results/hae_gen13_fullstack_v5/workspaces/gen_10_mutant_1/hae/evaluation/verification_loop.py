"""
Verification Loop: In-loop ground-truth verification for agent workspaces.

This module provides the `VerificationLoop` class which allows agents to request
a verification of their current workspace state against the real grading harness.
It enforces a budget to prevent excessive resource usage and ensures that the
feedback provided is strictly factual (gate verdicts) without coaching.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging

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
            workspace: The live workspace object (typically an AgentWorkspace)
                       that contains the files to be verified.
            budget: The maximum number of verify calls allowed.
            timeout_s: Timeout in seconds for the underlying harness execution.
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
            except ImportError as e:
                logger.error(f"Failed to import ExecutionHarness: {e}")
                raise
        return self._harness

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining in the budget."""
        return max(0, self.budget - len(self.attempts))

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """
        Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design. It reports verdicts without coaching.

        Args:
            turn: The current turn number (for logging/scorecard).
            agent_role: The role of the agent requesting verification.

        Returns:
            A string report containing gate statuses and details.
        """
        if self.remaining <= 0:
            return (
                "ERROR: Verification budget exhausted. "
                "No further verification calls are permitted. "
                "Finalize your work based on previous feedback."
            )

        try:
            harness = self._get_harness()
        except Exception as e:
            return f"ERROR: Verification system unavailable: {str(e)}"

        try:
            # Execute the harness against the live workspace
            # The harness expects a workspace object or a bundle.
            # verify_workspace handles the logic of extracting files from the workspace.
            report = harness.verify_workspace(workspace=self.workspace, deliverable_text='')
        except Exception as e:
            # If the harness itself crashes, we report the error but do not
            # necessarily consume the budget if it's a system error? 
            # Spec says "budget also preserves the thing being measured".
            # Usually, a crash is a failure of the agent's code or environment.
            # We will consume the budget to prevent infinite retry loops on broken envs.
            self._record_attempt(turn, agent_role, {}, 0, 0, 0)
            return f"ERROR: Verification failed with exception: {str(e)}"

        # Extract data for the attempt record
        gate_status = report.gate_status
        passed_count = len(report.passed_gates)
        evaluable_count = len(report.evaluated_gates)
        authored_files = report.authored_files

        # Record the attempt
        self._record_attempt(turn, agent_role, gate_status, passed_count, evaluable_count, authored_files)

        # Format the report for the agent
        return self._format_report(report)

    def _record_attempt(self, turn: int, agent_role: str, gate_status: Dict[str, str], 
                        passed_count: int, evaluable_count: int, authored_files: int):
        """Records a verification attempt in the history."""
        attempt = VerifyAttempt(
            turn=turn,
            agent_role=agent_role,
            gate_status=gate_status,
            passed_count=passed_count,
            evaluable_count=evaluable_count,
            authored_files=authored_files
        )
        self.attempts.append(attempt)

    def _format_report(self, report) -> str:
        """
        Formats the VerificationReport into a terse, agent-readable string.
        
        Rules:
        - State what is wrong (verdicts + details).
        - Do NOT suggest fixes.
        - Terse.
        """
        lines = []
        lines.append("=== VERIFICATION REPORT ===")
        
        # Summary line
        total_evaluated = len(report.evaluated_gates)
        total_passed = len(report.passed_gates)
        lines.append(f"Status: {total_passed}/{total_evaluated} evaluated gates passed.")
        
        if report.authored_files == 0:
            lines.append("Warning: No authored files detected.")

        # Gate details
        for gate in report.gates:
            status_upper = gate.status.upper()
            # Only show details for failed or skipped gates to keep it terse?
            # Spec says "echoes real tracebacks and real gate verdicts".
            # Usually, agents need to know WHY it failed.
            
            line = f"[{status_upper}] {gate.name}"
            
            if gate.status == 'failed':
                # Include detail if available, truncated if too long
                detail = gate.detail.strip()
                if detail:
                    # Truncate very long tracebacks to keep context manageable
                    if len(detail) > 1000:
                        detail = detail[:1000] + "\n... [truncated]"
                    line += f"\n  Detail: {detail}"
                else:
                    line += "\n  Detail: No specific detail provided."
            elif gate.status == 'skipped':
                line += f"\n  Reason: {gate.detail or 'Not evaluated'}"
            
            lines.append(line)

        lines.append("===========================")
        lines.append(f"Remaining verify budget: {self.remaining}")
        
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """
        Verification history for the scorecard.

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