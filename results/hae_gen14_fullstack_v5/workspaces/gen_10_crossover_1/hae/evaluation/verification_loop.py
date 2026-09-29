"""
In-loop ground-truth verification for agent workspaces.

This module provides the `VerificationLoop` class which allows agents to
request a verification of their current workspace state against the real
grading harness. It enforces a budget on these calls to prevent excessive
resource usage and ensures that the feedback provided is strictly factual
(gate verdicts and tracebacks) without coaching or suggestions.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import traceback

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
            workspace: The live workspace object to verify.
            budget: Maximum number of verify calls allowed.
            timeout_s: Timeout for the harness execution.
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
            except ImportError:
                # If harness is not available, we cannot verify.
                # This should ideally not happen in the target environment.
                raise RuntimeError("ExecutionHarness not available for verification.")
        return self._harness

    @property
    def remaining(self) -> int:
        """Returns the number of verify calls remaining."""
        return max(0, self.budget - len(self.attempts))

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """
        Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design. It reports verdicts and errors but does not suggest fixes.

        Args:
            turn: The current turn number.
            agent_role: The role of the agent requesting verification.

        Returns:
            A string report of the verification results.
        """
        if self.remaining <= 0:
            return (
                "BUDGET EXHAUSTED: Verification budget exhausted. "
                "No further verification calls are permitted. "
                "Finalize your work based on previous feedback."
            )

        try:
            harness = self._get_harness()
            # verify_workspace expects a workspace object and optional deliverable text.
            # We pass the live workspace.
            report = harness.verify_workspace(workspace=self.workspace, deliverable_text='')
            
            # Extract data for the attempt record
            gate_status = report.gate_status
            evaluated_gates = report.evaluated_gates
            passed_gates = report.passed_gates
            
            evaluable_count = len(evaluated_gates)
            passed_count = len(passed_gates)
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
            
            # Construct the terse report
            lines = []
            lines.append(f"Verification Attempt {len(self.attempts)} (Remaining: {self.remaining})")
            lines.append(f"Authored Files: {authored_files}")
            
            if authored_files == 0:
                lines.append("No files have been authored.")
                
            lines.append(f"Evaluated Gates: {evaluable_count} | Passed: {passed_count}")
            lines.append("-" * 40)
            
            for gate in report.gates:
                status_str = gate.status.upper()
                # Map 'failed' to 'FAIL' for test expectation if needed, but spec says PASSED/FAILED/SKIPPED
                # The test expects '[FAIL] syntax' in one case. Let's check the test failure message:
                # AssertionError: '[FAIL] syntax' not found in ... '[FAILED] syntax'
                # So we should use 'FAIL' for failed gates in the report string.
                if gate.status == 'failed':
                    status_str = 'FAIL'
                elif gate.status == 'passed':
                    status_str = 'PASS'
                elif gate.status == 'skipped':
                    status_str = 'SKIP'
                    
                lines.append(f"[{status_str}] {gate.name}")
                
                # Include detail if it's not empty and not just a generic pass message
                if gate.detail and gate.status != 'passed':
                    # Truncate very long details to keep context manageable
                    detail = gate.detail
                    if len(detail) > 500:
                        detail = detail[:500] + "... [truncated]"
                    lines.append(f"  Detail: {detail}")
                
                # Include evidence if it contains error traces or specific failures
                if gate.evidence:
                    # Evidence often contains stdout/stderr or tracebacks
                    for key, val in gate.evidence.items():
                        if isinstance(val, str) and val.strip():
                            # Only show evidence for failed or skipped gates to avoid noise
                            if gate.status != 'passed':
                                val_str = str(val)
                                if len(val_str) > 300:
                                    val_str = val_str[:300] + "... [truncated]"
                                lines.append(f"  Evidence ({key}): {val_str}")
            
            lines.append("-" * 40)
            
            # Add specific messaging for skips and syntax failures as per test expectations
            has_skip = any(g.status == 'skipped' for g in report.gates)
            has_syntax_fail = any(g.name == 'syntax' and g.status == 'failed' for g in report.gates)
            
            if has_skip:
                lines.append("Note: A skip is not a pass. Skipped gates earn no credit.")
                
            if has_syntax_fail:
                lines.append("Note: Code does not parse.")

            if passed_count == evaluable_count and evaluable_count > 0:
                lines.append("RESULT: ALL EVALUATED GATES PASSED")
            elif evaluable_count == 0:
                lines.append("RESULT: NO GATES EVALUATED (Check workspace structure)")
            else:
                lines.append(f"RESULT: {passed_count}/{evaluable_count} GATES PASSED")
                
            # Add remaining budget info explicitly if requested by tests
            # Test: '2 verification attempt(s) remaining'
            rem = self.remaining
            if rem > 0:
                lines.append(f"{rem} verification attempt(s) remaining")
                
            return "\n".join(lines)

        except Exception as e:
            # If the harness itself crashes, we record the attempt as failed
            # and return the error.
            error_trace = traceback.format_exc()
            attempt = VerifyAttempt(
                turn=turn,
                agent_role=agent_role,
                gate_status={},
                passed_count=0,
                evaluable_count=0,
                authored_files=0
            )
            self.attempts.append(attempt)
            
            return (
                f"ERROR: Verification system failure.\n"
                f"Exception: {str(e)}\n"
                f"Traceback:\n{error_trace}"
            )

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
                "final_gate_status": {},
                "final_passed_count": 0,
                "final_evaluable_count": 0,
                "final_authored_files": 0,
                "budget_used": 0,
                "budget_remaining": self.budget,
                "best_passed_count": 0,
                "best_evaluable_count": 0,
                "converged": False
            }
        
        last_attempt = self.attempts[-1]
        
        # Calculate best result
        best_passed = max((a.passed_count for a in self.attempts), default=0)
        best_evaluable = max((a.evaluable_count for a in self.attempts if a.passed_count == best_passed), default=0)
        
        # Check convergence: did the last attempt pass all evaluable gates?
        converged = (last_attempt.passed_count == last_attempt.evaluable_count and last_attempt.evaluable_count > 0)
        
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
            "final_gate_status": last_attempt.gate_status,
            "final_passed_count": last_attempt.passed_count,
            "final_evaluable_count": last_attempt.evaluable_count,
            "final_authored_files": last_attempt.authored_files,
            "budget_used": len(self.attempts),
            "budget_remaining": self.remaining,
            "best_passed_count": best_passed,
            "best_evaluable_count": best_evaluable,
            "converged": converged
        }