import time
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
        self._harness = None

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining in the budget."""
        return max(0, self.budget - len(self.attempts))

    def _get_harness(self):
        """Lazily initializes the ExecutionHarness to avoid import cycles or heavy init if unused."""
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

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return "ERROR: Verification budget exhausted. No further verify calls allowed."

        harness = self._get_harness()
        
        try:
            # Execute the verification
            # We pass the workspace directly. The harness handles materialization.
            report = harness.verify_workspace(workspace=self.workspace)
        except Exception as e:
            # If the harness itself crashes, we record a failed attempt but return an error string
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

        # Construct the agent-readable report
        # Terse, factual, no coaching.
        lines = []
        lines.append(f"Verification Attempt {len(self.attempts)} (Turn {turn})")
        lines.append(f"Remaining Budget: {self.remaining}")
        lines.append("")
        
        if not gate_status:
            lines.append("No gates evaluated.")
        else:
            for gate_name, status in gate_status.items():
                # Map internal status to uppercase for display consistency if needed, 
                # but contract says gate_status returns PASSED|FAILED|SKIPPED (uppercase in docstring, lowercase in constants?)
                # The harness constants are lowercase 'passed', 'failed', 'skipped'.
                # The docstring for gate_status says "PASSED | FAILED | SKIPPED".
                # Let's check the harness contract again.
                # SKIPPED = 'skipped'
                # PASSED = 'passed'
                # FAILED = 'failed'
                # The property gate_status returns Dict[str, str].
                # The docstring says "Gate name -> PASSED | FAILED | SKIPPED".
                # Usually, these strings are uppercase in the report for readability.
                # However, the constants are lowercase.
                # Let's assume the harness returns the constant values (lowercase) or uppercase.
                # The docstring for `gate_status` property in `VerificationReport` says:
                # "Gate name -> PASSED | FAILED | SKIPPED. The fitness function's input."
                # This implies uppercase.
                # But the constants are lowercase.
                # Let's look at `GateResult.status`. It uses the constants.
                # If `gate_status` property maps them, it might uppercase them.
                # To be safe and consistent with the "agent-readable" requirement, 
                # I will display them as they come from the report, but ensure they are clear.
                # If the harness returns lowercase, I'll uppercase for display.
                display_status = status.upper()
                lines.append(f"  {gate_name}: {display_status}")
        
        lines.append("")
        lines.append(f"Summary: {passed_count}/{evaluable_count} evaluated gates passed.")
        lines.append(f"Authored Files: {authored_files}")
        
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        return {
            "total_attempts": len(self.attempts),
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
            ]
        }