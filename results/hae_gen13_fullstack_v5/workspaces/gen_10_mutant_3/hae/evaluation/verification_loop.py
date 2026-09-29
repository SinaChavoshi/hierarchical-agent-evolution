"""
hae/evaluation/verification_loop.py

Generation 11 capability: in-loop ground-truth verification.

This module provides the `VerificationLoop` class which allows agents to
request a verification of their current workspace state against the real
grading harness. It enforces a budget on these calls to prevent excessive
resource usage and to preserve the integrity of the evaluation (distinguishing
between firms that get it right the first time vs. those that iterate).

The report returned is terse and factual, echoing gate verdicts and tracebacks
without providing coaching or fix suggestions.
"""

import os
import sys
import time
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
            workspace: The live workspace object (typically an AgentWorkspace).
            budget: Maximum number of verify calls allowed.
            timeout_s: Timeout for the harness execution in seconds.
        """
        self.workspace = workspace
        self.budget = budget
        self.timeout_s = timeout_s
        self.attempts: List[VerifyAttempt] = []
        self._harness = None  # Lazy import to avoid circular dependencies or import errors if harness is missing

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining in the budget."""
        return max(0, self.budget - len(self.attempts))

    def _get_harness(self):
        """Lazily imports and instantiates the ExecutionHarness.
        
        Crucially, this does NOT mutate sys.path. It relies on the standard
        import mechanism finding the module in the current environment.
        """
        if self._harness is None:
            try:
                # Standard import. Do not modify sys.path here.
                from hae.evaluation.harness import ExecutionHarness
                self._harness = ExecutionHarness(timeout_s=self.timeout_s)
            except ImportError:
                # If harness is not available, we cannot verify.
                raise RuntimeError("ExecutionHarness not available for verification.")
        return self._harness

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.

        Args:
            turn: The current turn number (for logging).
            agent_role: The role of the agent requesting verification.

        Returns:
            A string report of the verification results.
        """
        if self.remaining <= 0:
            return (
                "VERIFY BUDGET EXHAUSTED.\n"
                "No further verification calls are permitted for this firm.\n"
                "Final score will be based on the last verified state or initial state."
            )

        try:
            harness = self._get_harness()
        except Exception as e:
            return f"VERIFY ERROR: Could not initialize harness. {str(e)}"

        start_time = time.time()
        try:
            # Call the harness. 
            # The harness expects a workspace object or bundle. 
            # We pass the live workspace.
            report = harness.verify_workspace(workspace=self.workspace)
        except Exception as e:
            # Catch any unexpected errors during verification
            tb = traceback.format_exc()
            return f"VERIFY ERROR: Harness execution failed.\n{tb}"
        
        duration = time.time() - start_time

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
        return self._format_report(report, duration)

    def _format_report(self, report: Any, duration: float) -> str:
        """Formats the VerificationReport into a terse agent-readable string."""
        lines = []
        lines.append(f"VERIFICATION REPORT (Attempt {len(self.attempts)}/{self.budget})")
        lines.append(f"Duration: {duration:.2f}s")
        lines.append(f"Authored Files: {report.authored_files}")
        lines.append("-" * 40)
        
        # Gate Statuses
        for gate in report.gates:
            status_str = gate.status.upper()
            lines.append(f"[{status_str}] {gate.name}: {gate.detail}")
            
            # If failed, include evidence/traceback if available and short
            if gate.status == 'failed' and gate.evidence:
                # Truncate evidence to keep report terse
                evidence_str = str(gate.evidence)
                if len(evidence_str) > 500:
                    evidence_str = evidence_str[:500] + "..."
                lines.append(f"  Evidence: {evidence_str}")

        lines.append("-" * 40)
        lines.append(f"Summary: {report.summary()}")
        
        if report.pass_rate < 1.0:
            lines.append("NOTE: Some gates failed or were skipped. Only PASS counts.")
            
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
                "remaining_budget": self.budget,
                "final_gate_status": {},
                "final_passed_count": 0,
                "final_evaluable_count": 0,
                "final_authored_files": 0
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
            "remaining_budget": self.remaining,
            "final_gate_status": last_attempt.gate_status,
            "final_passed_count": last_attempt.passed_count,
            "final_evaluable_count": last_attempt.evaluable_count,
            "final_authored_files": last_attempt.authored_files
        }