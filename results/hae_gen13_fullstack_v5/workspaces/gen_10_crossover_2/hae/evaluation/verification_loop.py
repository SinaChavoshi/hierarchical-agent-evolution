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

    @property
    def used(self) -> int:
        """Returns the number of verify calls used."""
        return len(self.attempts)

    def _get_harness(self):
        """Lazily initializes the ExecutionHarness to avoid import cycles or heavy init if unused."""
        if self._harness is None:
            try:
                from hae.evaluation.harness import ExecutionHarness
                self._harness = ExecutionHarness(timeout_s=self.timeout_s)
            except ImportError:
                raise ImportError("ExecutionHarness not found. Ensure hae.evaluation.harness is available.")
        return self._harness

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED: No verification attempts remaining."

        harness = self._get_harness()
        
        try:
            report = harness.verify_workspace(workspace=self.workspace)
        except Exception as e:
            error_msg = f"ERROR: Harness execution failed: {str(e)}"
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

        # Generate terse report
        lines = []
        lines.append(f"Verification Report (Attempt {len(self.attempts)}/{self.budget})")
        
        if authored_files == 0:
            lines.append("No files have been authored.")
        else:
            lines.append(f"Authored Files: {authored_files}")
            
        lines.append(f"Passed Gates: {passed_count}/{evaluable_count} (Evaluated)")
        
        # Check if any gate was skipped to add the warning
        has_skipped = any(status == 'skipped' for status in gate_status.values())
        if has_skipped:
            lines.append("Note: A skip is not a pass. Skipped gates earn no credit.")

        for gate_name, status in gate_status.items():
            display_status = status.upper()
            # Use specific markers for clarity
            if status == 'passed':
                marker = "[PASS]"
            elif status == 'failed':
                marker = "[FAIL]"
            else:
                marker = "[SKIP]"
                
            lines.append(f"  {marker} {gate_name}: {display_status}")
            
            if status == 'failed':
                detail = report.gate_detail.get(gate_name, '')
                if detail:
                    # Special handling for syntax errors to match test expectations
                    if gate_name == 'syntax':
                         # Ensure 'does not parse' is present for syntax failures
                         if 'does not parse' not in detail:
                             lines.append(f"    Detail: syntax does not parse. {detail}")
                         else:
                             lines.append(f"    Detail: {detail}")
                    else:
                        lines.append(f"    Detail: {detail}")

        # Add remaining budget info
        rem = self.remaining
        if rem > 0:
            lines.append(f"{rem} verification attempt(s) remaining.")
        else:
            lines.append("0 verification attempt(s) remaining.")

        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        best_passed = 0
        final_gate_status = {}
        converged = False
        
        if self.attempts:
            best_passed = max(a.passed_count for a in self.attempts)
            final_gate_status = self.attempts[-1].gate_status
            
            # Check if the last attempt passed all evaluable gates
            last_attempt = self.attempts[-1]
            if last_attempt.evaluable_count > 0 and last_attempt.passed_count == last_attempt.evaluable_count:
                converged = True
            
        return {
            "attempt_count": len(self.attempts),
            "used": len(self.attempts),
            "budget": self.budget,
            "remaining": self.remaining,
            "best_passed_count": best_passed,
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