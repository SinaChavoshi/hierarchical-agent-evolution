"""
Generation 11 capability: in-loop ground-truth verification.

This module closes the loop between agent actions and evaluation gates.
It provides a `VerificationLoop` that agents can invoke to get the exact
gate report that will determine their score, computed by the same
`ExecutionHarness` the evaluator uses.

Key constraints:
1. Verification is rate-limited (budgeted) to prevent spinning.
2. The report states what is wrong, never how to fix it.
"""

import os
import sys
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
            budget: Maximum number of verify calls allowed.
            timeout_s: Timeout for the underlying harness execution.
        """
        self.workspace = workspace
        self.budget = budget
        self.timeout_s = timeout_s
        self.attempts: List[VerifyAttempt] = []
        self._harness = None  # Lazy import to avoid circular deps or missing deps at init

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining."""
        return max(0, self.budget - len(self.attempts))

    @property
    def used(self) -> int:
        """Returns the number of verify calls used."""
        return len(self.attempts)

    def _get_harness(self):
        """Lazily imports and instantiates the ExecutionHarness."""
        if self._harness is None:
            try:
                from hae.evaluation.harness import ExecutionHarness
                self._harness = ExecutionHarness(timeout_s=self.timeout_s)
            except ImportError:
                # Fallback or error handling if harness is not available
                raise RuntimeError("ExecutionHarness not available. Ensure hae.evaluation.harness is installed.")
        return self._harness

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by design.
        It reports verdicts without coaching.

        Args:
            turn: The current turn number (for logging).
            agent_role: The role of the agent requesting verification.

        Returns:
            A string report of the gate statuses.
        """
        if self.remaining <= 0:
            return "VERIFY BUDGET EXHAUSTED. No further verification calls allowed."

        try:
            harness = self._get_harness()
            # Verify the workspace. We assume the workspace object is compatible
            # with ExecutionHarness.verify_workspace.
            report = harness.verify_workspace(workspace=self.workspace)
            
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
            lines = []
            lines.append(f"Verification Attempt {len(self.attempts)}")
            
            # Budget info: "X verification attempt(s) remaining"
            rem = self.remaining
            lines.append(f"{rem} verification attempt(s) remaining")
                
            lines.append(f"Authored Files: {authored_files}")
            
            # Pass rate format: "X/Y evaluable"
            lines.append(f"{passed_count}/{evaluable_count} evaluable")
            
            # Special case for empty workspace
            if authored_files == 0:
                lines.append("No files have been authored")
                
            lines.append("Gate Status:")
            
            has_skipped = False
            for gate_name, status in gate_status.items():
                # Map internal status to uppercase for clarity
                display_status = status.upper() if status else "UNKNOWN"
                if display_status == "SKIPPED":
                    has_skipped = True
                
                # Specific formatting for failures to match test expectations
                if display_status == "FAILED":
                    lines.append(f"[FAIL] {gate_name}")
                else:
                    lines.append(f"  {gate_name}: {display_status}")
                
            # Add detail for failures if available
            if report.gate_detail:
                for gate_name, detail in report.gate_detail.items():
                    if gate_status.get(gate_name) == 'failed' and detail:
                        # Truncate long details
                        short_detail = detail[:200] + "..." if len(detail) > 200 else detail
                        lines.append(f"  {gate_name} detail: {short_detail}")
            
            # Add skip explanation if any gate was skipped
            if has_skipped:
                lines.append("A skip is not a pass.")
                
            return "\n".join(lines)

        except Exception as e:
            # If the harness itself crashes, we should report that clearly
            # but still count it as an attempt if possible? 
            # The spec implies verify runs the harness. If harness fails to init/run,
            # it's a system error.
            err_msg = f"Verification failed due to system error: {str(e)}"
            # We might want to record this as a failed attempt with no gates?
            # For now, just return the error.
            return err_msg

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard.

        Returns:
            A dictionary containing the history of verification attempts.
        """
        best_passed = 0
        best_evaluable = 0
        best_gate_status = {}
        
        for a in self.attempts:
            if a.passed_count > best_passed:
                best_passed = a.passed_count
                best_evaluable = a.evaluable_count
                best_gate_status = a.gate_status
            elif a.passed_count == best_passed and a.evaluable_count > best_evaluable:
                # Tie-breaker: more evaluable gates is better if passed count is same?
                # Or just keep the first one. Let's keep the first one for simplicity unless specified.
                pass

        # Determine final gate status (from last attempt)
        final_gate_status = {}
        if self.attempts:
            final_gate_status = self.attempts[-1].gate_status
            
        # Determine convergence: all evaluable gates passed in the last attempt
        converged = False
        if self.attempts:
            last = self.attempts[-1]
            if last.evaluable_count > 0 and last.passed_count == last.evaluable_count:
                converged = True

        return {
            "attempt_count": len(self.attempts),
            "used": len(self.attempts),
            "budget": self.budget,
            "remaining": self.remaining,
            "best_passed_count": best_passed,
            "best_evaluable_count": best_evaluable,
            "best_gate_status": best_gate_status,
            "final_gate_status": final_gate_status,
            "converged": converged,
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