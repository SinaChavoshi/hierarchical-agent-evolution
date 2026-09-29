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
        """Lazily imports and initializes the ExecutionHarness."""
        if self._harness is None:
            try:
                from hae.evaluation.harness import ExecutionHarness
                self._harness = ExecutionHarness(timeout_s=self.timeout_s)
            except ImportError:
                # If harness is not available, we cannot verify.
                # This should ideally not happen in the target environment.
                raise RuntimeError("ExecutionHarness not available")
        return self._harness

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED. No further verification allowed."

        try:
            harness = self._get_harness()
            # We assume the workspace object is compatible with the harness
            # or that the harness can extract the bundle from it.
            # The harness signature is verify_workspace(workspace, deliverable_text)
            # We pass empty string for deliverable_text as we are verifying the live workspace.
            report = harness.verify_workspace(self.workspace, deliverable_text='')
            
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
            
            # Construct terse report
            lines = []
            lines.append(f"Verification Attempt {len(self.attempts)} (Turn {turn})")
            
            if authored_files == 0:
                lines.append("No files have been authored.")
            else:
                lines.append(f"Authored Files: {authored_files}")
                
            lines.append(f"Gates Passed: {passed_count}/{evaluable_count}")
            
            # Add explanation for skips if any gate was skipped
            has_skipped = any(s == 'skipped' for s in gate_status.values())
            if has_skipped:
                lines.append("A skip is not a pass.")

            for name, status in gate_status.items():
                # Map status to uppercase for readability if needed, 
                # but contract says gate_status returns PASSED | FAILED | SKIPPED
                # The harness constants are lowercase 'passed', 'failed', 'skipped'
                # The contract for gate_status property says: "Gate name -> PASSED | FAILED | SKIPPED"
                # Let's check the harness contract again.
                # harness.py contract: gate_status property returns Dict[str, str] "Gate name -> PASSED | FAILED | SKIPPED"
                # But the constants are SKIPPED = 'skipped', PASSED = 'passed', FAILED = 'failed'
                # Usually these properties normalize to uppercase or use the constants.
                # The prompt says: "Gate name -> PASSED | FAILED | SKIPPED".
                # I will assume the harness returns the strings 'PASSED', 'FAILED', 'SKIPPED' 
                # OR I should normalize them here if they are lowercase.
                # Given the constants are lowercase, but the docstring says uppercase, 
                # I will display them as they come from the report, but ensure they are clear.
                # Actually, looking at the harness contract:
                # SKIPPED = 'skipped'
                # PASSED = 'passed'
                # FAILED = 'failed'
                # And gate_status property docstring: "Gate name -> PASSED | FAILED | SKIPPED"
                # This implies the property might return uppercase strings, or the docstring is descriptive.
                # To be safe and terse, I'll just print the status.
                
                # Normalize status for display to match test expectations if they expect uppercase
                # The tests check for '[FAIL] syntax' or 'syntax: failed'. 
                # The previous failure showed 'syntax: failed' was present but test expected '[FAIL] syntax'.
                # Wait, the test failure said:
                # AssertionError: '[FAIL] syntax' not found in '... syntax: failed ...'
                # So the test expects '[FAIL] syntax'.
                
                status_upper = status.upper()
                if status_upper == 'PASSED':
                    icon = '[PASS]'
                elif status_upper == 'FAILED':
                    icon = '[FAIL]'
                else:
                    icon = '[SKIP]'
                    
                lines.append(f"  {icon} {name}: {status}")
                
            # Add remaining budget info
            rem = self.remaining
            if rem > 0:
                lines.append(f"{rem} verification attempt(s) remaining.")
            else:
                lines.append("No verification attempts remaining.")
                
            # Add specific detail for syntax failure if present
            if gate_status.get('syntax') == 'failed':
                # Try to get detail from report if possible, but report object might not be stored
                # We can re-access the report object if we stored it, but we didn't.
                # The harness report has gate_detail.
                # Let's assume the harness report is available in the scope or we can extract it.
                # Actually, we have `report` in scope.
                detail = report.gate_detail.get('syntax', '')
                if detail:
                    lines.append(f"Syntax error: {detail}")
                else:
                    lines.append("Syntax error: code does not parse.")

            return "\n".join(lines)

        except Exception as e:
            # If verification fails catastrophically (e.g. harness crash), 
            # we still record an attempt? The spec says "One verification call, recorded".
            # If it crashes, we might not have gate_status.
            # Let's record a failed attempt with empty status.
            attempt = VerifyAttempt(
                turn=turn,
                agent_role=agent_role,
                gate_status={},
                passed_count=0,
                evaluable_count=0,
                authored_files=0
            )
            self.attempts.append(attempt)
            return f"ERROR: Verification failed with exception: {str(e)}"

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        best_passed = 0
        if self.attempts:
            best_passed = max(a.passed_count for a in self.attempts)
            
        # Determine final gate status from the last attempt
        final_gate_status = {}
        if self.attempts:
            final_gate_status = self.attempts[-1].gate_status
            
        # Determine if converged (all evaluable gates passed in the last attempt)
        converged = False
        if self.attempts:
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