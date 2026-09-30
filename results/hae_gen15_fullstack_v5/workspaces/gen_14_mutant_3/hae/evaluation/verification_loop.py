"""
Generation 11 capability: in-loop ground-truth verification.

This module closes the loop between agent actions and evaluation gates.
It provides a `VerificationLoop` that agents can invoke to get a real
verdict from the `ExecutionHarness`, subject to a strict budget.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List
from hae.evaluation.artifacts import filter_bundle
from hae.evaluation.harness import ExecutionHarness, PASSED, FAILED, SKIPPED

DEFAULT_VERIFY_BUDGET = 3

_MAX_DETAIL_CHARS = 400

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
        self.harness = ExecutionHarness(timeout_s=timeout_s)
        self.used = 0
        self.attempts: List[VerifyAttempt] = []

    @property
    def remaining(self) -> int:
        return self.budget - self.used

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED"

        # Get bundle from workspace
        try:
            raw_bundle = self.workspace.export_bundle()
        except Exception:
            raw_bundle = {}
        
        # Filter bundle to get authored files
        bundle = filter_bundle(raw_bundle)
        authored_files_count = len(bundle)

        # If no files authored, consume attempt but don't record VerifyAttempt
        if authored_files_count == 0:
            self.used += 1
            return f"No files have been authored yet. Nothing to verify. ({self.remaining} verification attempt(s) remaining.)"

        # Consume attempt
        self.used += 1
        
        # Run harness
        report = self.harness.verify_bundle(bundle)
        
        # Extract gate statuses
        gate_status = report.gate_status
        
        # Count passed and evaluable gates
        passed_count = 0
        evaluable_count = 0
        for name, status in gate_status.items():
            if status != SKIPPED:
                evaluable_count += 1
                if status == PASSED:
                    passed_count += 1
        
        # Record attempt
        attempt = VerifyAttempt(
            turn=turn,
            agent_role=agent_role,
            gate_status=gate_status,
            passed_count=passed_count,
            evaluable_count=evaluable_count,
            authored_files=authored_files_count
        )
        self.attempts.append(attempt)
        
        # Format report
        lines = []
        lines.append(f"{passed_count}/{evaluable_count} evaluable gate(s) passing across {authored_files_count} authored file(s).")
        
        gate_names = ("syntax", "build", "smoke", "tests", "telemetry")
        for name in gate_names:
            status = gate_status.get(name, SKIPPED)
            detail = report.gate_detail.get(name, "")
            
            # Truncate detail
            if len(detail) > _MAX_DETAIL_CHARS:
                detail = detail[:_MAX_DETAIL_CHARS]
            
            # Format status
            if status == PASSED:
                status_str = "PASS"
            elif status == FAILED:
                status_str = "FAIL"
            else:
                status_str = "SKIP"
                
            lines.append(f"  [{status_str}] {name}: {detail}")
            
            # Special case for syntax failure
            if name == "syntax" and status == FAILED:
                lines.append("A file does not parse.")

        lines.append("SKIP means the gate could not be evaluated in this environment (usually an uninstalled third-party package). A skip is not a pass and earns no credit.")
        lines.append(f"({self.remaining} verification attempt(s) remaining.)")
        
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        best_passed_count = 0
        if self.attempts:
            best_passed_count = max(a.passed_count for a in self.attempts)
        
        final_gate_status = {}
        converged = False
        
        if self.attempts:
            final_attempt = self.attempts[-1]
            final_gate_status = final_attempt.gate_status
            # converged is bool(final and final.passed_count >= best_passed_count)
            # Since final_attempt is the last one, its passed_count is the final one.
            # If it's the only one, it's equal. If it's better than previous, it's greater.
            # If it's worse, it's less.
            converged = final_attempt.passed_count >= best_passed_count
            
        attempts_data = []
        for a in self.attempts:
            attempts_data.append({
                "turn": a.turn,
                "agent_role": a.agent_role,
                "gate_status": a.gate_status,
                "passed": a.passed_count,
                "evaluable": a.evaluable_count,
                "authored_files": a.authored_files
            })
            
        return {
            "budget": self.budget,
            "used": self.used,
            "attempt_count": len(self.attempts),
            "best_passed_count": best_passed_count,
            "final_gate_status": final_gate_status,
            "converged": converged,
            "attempts": attempts_data
        }

VERIFY_TOOL_GUIDE = '- To check your work against the REAL grading gates:\n  Action: verify\n  This parses, installs, imports, tests and inspects your workspace with\n  the same harness that will score your firm, and returns the verdict.\n  It is expensive and strictly limited -- you get a small number of\n  attempts for the whole assignment, so write your code first and verify\n  when you believe it is complete.\n  Note: a gate reported as SKIP was not evaluated and earns no credit.\n  Only PASS counts.\n'