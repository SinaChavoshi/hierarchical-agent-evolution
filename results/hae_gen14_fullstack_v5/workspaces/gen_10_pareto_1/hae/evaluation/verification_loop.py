import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Constants
DEFAULT_VERIFY_BUDGET = 3

VERIFY_TOOL_GUIDE = '- To check your work against the REAL grading gates:\n  Action: verify\n  This parses, installs, imports, tests and inspects your workspace with\n  the same harness that will score your firm, and returns the verdict.\n  It is expensive and strictly limited -- you get a small number of\n  attempts for the whole assignment, so write your code first and verify\n  when you believe it is complete.\n  Note: a gate reported as SKIP was not evaluated and earns no credit.\n  Only PASS counts.\n'

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
        return max(0, self.budget - len(self.attempts))

    @property
    def used(self) -> int:
        return len(self.attempts)

    def _get_harness(self):
        if self._harness is None:
            try:
                from hae.evaluation.harness import ExecutionHarness
                self._harness = ExecutionHarness(timeout_s=self.timeout_s)
            except ImportError:
                # Fallback if harness is not available or fails to import
                self._harness = None
        return self._harness

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED. No further verification attempts allowed."

        harness = self._get_harness()
        if harness is None:
            return "ERROR: ExecutionHarness unavailable. Cannot verify."

        try:
            # Attempt to get deliverable text if workspace supports it, else empty
            deliverable_text = ""
            if hasattr(self.workspace, 'get_deliverable_text'):
                deliverable_text = self.workspace.get_deliverable_text()
            elif hasattr(self.workspace, 'deliverable_text'):
                deliverable_text = self.workspace.deliverable_text
            
            report = harness.verify_workspace(self.workspace, deliverable_text)
            
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
            lines = [f"Verification Attempt {len(self.attempts)} (Turn {turn})"]
            
            if authored_files == 0:
                lines.append("No files have been authored.")
            else:
                lines.append(f"Authored Files: {authored_files}")
                
            lines.append(f"Gates Evaluated: {evaluable_count}, Passed: {passed_count}")
            
            for name, status in gate_status.items():
                # Map internal status to display status
                display_status = status.upper()
                if status == 'skipped':
                    display_status = 'SKIP'
                elif status == 'passed':
                    display_status = 'PASS'
                elif status == 'failed':
                    display_status = 'FAIL'
                
                lines.append(f"  [{display_status}] {name}")
                
                # Include detail if failed or skipped to help agent understand state
                # But strictly no coaching. Just the raw detail from harness.
                if status != 'passed':
                    detail = report.gate_detail.get(name, "")
                    if detail:
                        # Truncate long details
                        if len(detail) > 200:
                            detail = detail[:200] + "..."
                        lines.append(f"    Detail: {detail}")
            
            # Add explicit note about skips if any exist
            if any(s == 'skipped' for s in gate_status.values()):
                lines.append("Note: A skip is not a pass. Skipped gates earn no credit.")
                
            # Add explicit note about syntax failure if it failed
            if gate_status.get('syntax') == 'failed':
                lines.append("Note: Code does not parse.")

            lines.append(f"{self.remaining} verification attempt(s) remaining")
            return "\n".join(lines)

        except Exception as e:
            # Record failed attempt
            attempt = VerifyAttempt(
                turn=turn,
                agent_role=agent_role,
                gate_status={},
                passed_count=0,
                evaluable_count=0,
                authored_files=0
            )
            self.attempts.append(attempt)
            
            err_msg = f"ERROR: Verification failed with exception: {str(e)}"
            tb = traceback.format_exc()
            # Include traceback in report so agent sees what went wrong in harness
            return f"{err_msg}\nTraceback:\n{tb}"

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        best_passed = 0
        best_evaluable = 0
        best_gate_status = {}
        
        for a in self.attempts:
            if a.passed_count > best_passed:
                best_passed = a.passed_count
                best_evaluable = a.evaluable_count
                best_gate_status = a.gate_status
            elif a.passed_count == best_passed and a.evaluable_count > best_evaluable:
                # Tie-breaker: prefer more evaluable gates if passed count is same
                best_evaluable = a.evaluable_count
                best_gate_status = a.gate_status

        # Determine convergence: did the last attempt pass all evaluable gates?
        converged = False
        if self.attempts:
            last = self.attempts[-1]
            if last.evaluable_count > 0 and last.passed_count == last.evaluable_count:
                converged = True

        # Final gate status is from the last attempt
        final_gate_status = self.attempts[-1].gate_status if self.attempts else {}

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