"""
Verification Loop: In-loop ground-truth verification for agent workspaces.

This module provides the `VerificationLoop` class which allows agents to request
a verification report from the real execution harness during their run. It enforces
a budget to prevent excessive resource usage and ensures that the feedback provided
is strictly factual (gate verdicts) without coaching (suggestions for fixes).

Dependencies:
- hae.evaluation.harness.ExecutionHarness: The core verification engine.
- hae.evaluation.artifacts: Utilities for filtering and counting artifacts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Import the harness and artifact utilities
# Note: These are assumed to be available in the environment as per the dependency contract.
try:
    from hae.evaluation.harness import ExecutionHarness, VerificationReport
except ImportError:
    # Fallback for environments where the harness might not be fully installed yet
    # or for isolated testing. In production, this should fail loudly if missing.
    ExecutionHarness = None
    VerificationReport = None

try:
    from hae.evaluation.artifacts import count_source_files
except ImportError:
    # Fallback if artifacts module is not yet available
    def count_source_files(bundle: Dict[str, str], suffixes: tuple = ('.py',)) -> int:
        return sum(1 for p in bundle.keys() if any(p.endswith(s) for s in suffixes))

logger = logging.getLogger(__name__)

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
            workspace: The live workspace object (e.g., AgentWorkspace) to verify.
            budget: The maximum number of verify calls allowed.
            timeout_s: Timeout in seconds for the harness execution.
        """
        self.workspace = workspace
        self.budget = budget
        self.timeout_s = timeout_s
        self.attempts: List[VerifyAttempt] = []
        
        # Initialize the harness. 
        # We use the provided timeout for the harness execution.
        if ExecutionHarness is not None:
            self.harness = ExecutionHarness(timeout_s=timeout_s)
        else:
            self.harness = None
            logger.warning("ExecutionHarness not available. Verification will return error messages.")

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining in the budget."""
        return max(0, self.budget - len(self.attempts))

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """
        Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design. It reports verdicts but does not suggest fixes.

        Args:
            turn: The current turn number (for logging/history).
            agent_role: The role of the agent requesting verification.

        Returns:
            A string report of the verification results.
        """
        # Check budget
        if self.remaining <= 0:
            return (
                "BUDGET EXHAUSTED. "
                f"You have used all {self.budget} allowed verification attempts. "
                "No further verification is possible."
            )

        if self.harness is None:
            return "ERROR: ExecutionHarness is not available. Cannot verify workspace."

        try:
            # Run the harness against the workspace
            # We pass empty deliverable_text because we are verifying the live workspace
            report: VerificationReport = self.harness.verify_workspace(
                workspace=self.workspace,
                deliverable_text=''
            )
        except Exception as e:
            logger.exception("Harness execution failed")
            return f"ERROR: Verification failed due to internal error: {str(e)}"

        # Extract gate status
        gate_status = report.gate_status
        
        # Count passed and evaluable gates
        passed_count = sum(1 for status in gate_status.values() if status == 'passed')
        evaluable_count = sum(1 for status in gate_status.values() if status != 'skipped')
        
        # Count authored files
        # The report contains authored_files count directly
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

        # Generate the report string
        return self._format_report(report, attempt)

    def _format_report(self, report: VerificationReport, attempt: VerifyAttempt) -> str:
        """
        Formats the verification report into a terse, agent-readable string.
        
        This format is designed to be informative but not coaching.
        """
        lines = []
        lines.append("=== VERIFICATION REPORT ===")
        lines.append(f"Attempt: {len(self.attempts)}/{self.budget}")
        
        # Specific requirement: "2 verification attempt(s) remaining"
        rem = self.remaining
        lines.append(f"{rem} verification attempt(s) remaining")
        lines.append("")
        
        # Check for empty workspace condition
        # If no files were authored, the harness returns all SKIPPED.
        # We need to detect this to print the specific message.
        if attempt.authored_files == 0:
            lines.append("No files have been authored.")
            lines.append("")
            
        # Overall Status
        total_gates = len(report.gates)
        passed_gates = len(report.passed_gates)
        skipped_gates = sum(1 for g in report.gates if g.status == 'skipped')
        failed_gates = sum(1 for g in report.gates if g.status == 'failed')
        
        lines.append(f"Summary: {passed_gates} PASSED, {failed_gates} FAILED, {skipped_gates} SKIPPED (of {total_gates} gates)")
        
        # Specific requirement: "A skip is not a pass"
        if skipped_gates > 0:
            lines.append("Note: A skip is not a pass. Skipped gates earn no credit.")
            
        lines.append("")
        
        # Gate Details
        lines.append("Gate Results:")
        for gate in report.gates:
            status_upper = gate.status.upper()
            # Format: [STATUS] Gate Name: Detail
            # Truncate detail to keep it terse
            detail = gate.detail
            if len(detail) > 200:
                detail = detail[:197] + "..."
            
            # Specific requirement: "[FAIL] syntax" vs "[FAILED] syntax"
            # The test expects '[FAIL] syntax' in the output.
            # Standard harness uses 'failed'. We map 'failed' to 'FAIL' for display if needed,
            # but the test assertion is `' [FAIL] syntax' not found`.
            # Let's look at the error: `AssertionError: '[FAIL] syntax' not found in ...`
            # The current output has `[FAILED] syntax`.
            # So we must change the display status for failed gates to 'FAIL'.
            display_status = status_upper
            if gate.status == 'failed':
                display_status = 'FAIL'
            elif gate.status == 'passed':
                display_status = 'PASS'
            elif gate.status == 'skipped':
                display_status = 'SKIP'
                
            lines.append(f"  [{display_status}] {gate.name}: {detail}")
        
        lines.append("")
        lines.append(f"Authored Files: {attempt.authored_files}")
        lines.append("=============================")
        
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
                "attempt_count": 0,
                "total_attempts": 0,
                "budget": self.budget,
                "remaining": self.remaining,
                "final_gate_status": {},
                "final_passed_count": 0,
                "final_evaluable_count": 0,
                "final_authored_files": 0,
                "first_pass": False,
                "best_passed_count": 0,
                "best_evaluable_count": 0,
            }

        last_attempt = self.attempts[-1]
        
        # Determine if the first attempt passed all evaluable gates
        first_attempt = self.attempts[0]
        first_pass = (
            first_attempt.evaluable_count > 0 and 
            first_attempt.passed_count == first_attempt.evaluable_count
        )
        
        # Track best result
        best_passed = max(a.passed_count for a in self.attempts)
        best_evaluable = max(a.evaluable_count for a in self.attempts)

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
            "attempt_count": len(self.attempts),
            "total_attempts": len(self.attempts),
            "budget": self.budget,
            "remaining": self.remaining,
            "final_gate_status": last_attempt.gate_status,
            "final_passed_count": last_attempt.passed_count,
            "final_evaluable_count": last_attempt.evaluable_count,
            "final_authored_files": last_attempt.authored_files,
            "first_pass": first_pass,
            "best_passed_count": best_passed,
            "best_evaluable_count": best_evaluable,
        }