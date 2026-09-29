"""
hae/evaluation/verification_loop.py

Generation 11 capability: in-loop ground-truth verification.

This module provides the `VerificationLoop` class which allows agents to request
a verification report from the real execution harness during their run.
It enforces a budget to prevent excessive resource usage and ensures that the
feedback provided is strictly factual (gate verdicts) without coaching.
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

# Dependency Imports
# These modules are expected to be present in the environment as per the spec.
try:
    from hae.evaluation.harness import ExecutionHarness, VerificationReport, GateResult
    from hae.evaluation.harness import PASSED, FAILED, SKIPPED
except ImportError:
    # Fallback for type checking or isolated testing if harness isn't fully available
    # In production, these must exist.
    ExecutionHarness = None
    VerificationReport = None
    GateResult = None
    PASSED = 'passed'
    FAILED = 'failed'
    SKIPPED = 'skipped'

try:
    from hae.evaluation.artifacts import filter_bundle, count_source_files
except ImportError:
    # Fallbacks if artifacts module isn't fully available
    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        return bundle
    
    def count_source_files(bundle: Dict[str, str], suffixes: Tuple[str, ...] = ('.py',)) -> int:
        return sum(1 for k in bundle.keys() if k.endswith(suffixes))


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
        
        # Initialize the harness. 
        # We use the provided timeout for the harness execution.
        if ExecutionHarness is not None:
            self.harness = ExecutionHarness(timeout_s=timeout_s)
        else:
            self.harness = None

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining in the budget."""
        return max(0, self.budget - len(self.attempts))

    @property
    def used(self) -> int:
        """Returns the number of verify calls used."""
        return len(self.attempts)

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """
        Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by design.
        
        Args:
            turn: The current turn number (for logging).
            agent_role: The role of the agent requesting verification (for logging).
            
        Returns:
            A string report of the gate statuses.
        """
        if self.remaining <= 0:
            return (
                "BUDGET EXHAUSTED. No further verification calls are permitted. "
                "Finalize your work based on previous feedback."
            )

        if self.harness is None:
            return "ERROR: ExecutionHarness not available. Verification cannot proceed."

        # Execute the harness
        # We pass the workspace directly. The harness handles materialization if needed.
        # deliverable_text is empty because we are verifying the live workspace state.
        try:
            report: VerificationReport = self.harness.verify_workspace(
                workspace=self.workspace, 
                deliverable_text=''
            )
        except Exception as e:
            # If the harness itself crashes, we report a failure but don't crash the loop
            error_msg = f"ERROR: Harness execution failed: {str(e)}"
            # Record this as a failed attempt with no gate data
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

        # Extract data for the attempt record
        gate_status = report.gate_status
        evaluated_gates = report.evaluated_gates
        passed_gates = report.passed_gates
        
        evaluable_count = len(evaluated_gates)
        passed_count = len(passed_gates)
        
        # Count authored files from the report
        # The report contains authored_files count
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

        # Generate the terse report string
        # Format:
        # VERIFICATION REPORT (Attempt X/Y)
        # Gate: Status
        # ...
        # Summary: P/E gates passed. Files: N.
        
        lines = []
        lines.append(f"VERIFICATION REPORT (Attempt {len(self.attempts)}/{self.budget})")
        
        # Check for empty workspace condition
        if authored_files == 0:
            lines.append("No files have been authored.")
            lines.append("")
            lines.append("Summary: 0/0 evaluated gates passed.")
            lines.append("Authored files: 0")
            if self.remaining > 0:
                lines.append(f"{self.remaining} verification attempt(s) remaining")
            else:
                lines.append("Verify budget exhausted.")
            return "\n".join(lines)

        # Sort gates for deterministic output
        gate_names = sorted(gate_status.keys())
        for name in gate_names:
            status = gate_status[name]
            # Map internal status to display status
            # PASSED -> PASS, FAILED -> FAIL, SKIPPED -> SKIP
            display_status = "PASS"
            if status == FAILED:
                display_status = "FAIL"
            elif status == SKIPPED:
                display_status = "SKIP"
            
            lines.append(f"  {name}: {display_status}")
            
            # Include detail if it's a failure and detail is present
            # The spec says "echoes real tracebacks". 
            # GateResult has 'detail'. We need to access the specific gate result.
            if status == FAILED:
                gate_result = report.gate(name)
                if gate_result and gate_result.detail:
                    # Truncate detail to keep it terse but informative
                    detail = gate_result.detail
                    if len(detail) > 500:
                        detail = detail[:500] + "..."
                    lines.append(f"    Detail: {detail}")

        lines.append("")
        lines.append(f"Summary: {passed_count}/{evaluable_count} evaluated gates passed.")
        lines.append(f"Authored files: {authored_files}")
        
        if self.remaining > 0:
            lines.append(f"{self.remaining} verification attempt(s) remaining")
        else:
            lines.append("Verify budget exhausted.")

        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """
        Verification history for the scorecard.
        
        Returns:
            A dictionary containing the history of verification attempts.
        """
        best_passed = 0
        final_gate_status = {}
        
        if self.attempts:
            best_passed = max(a.passed_count for a in self.attempts)
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