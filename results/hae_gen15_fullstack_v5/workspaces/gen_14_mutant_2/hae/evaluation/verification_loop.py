"""
hae/evaluation/verification_loop.py

Generation 11 capability: in-loop ground-truth verification.

This module closes the loop between agent actions and evaluation gates.
It provides a `VerificationLoop` that allows agents to request a real
verification report from the `ExecutionHarness` within a limited budget.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Import dependencies from parallel departments
# These are assumed to be available in the environment as per the spec
try:
    from hae.evaluation.artifacts import filter_bundle
except ImportError:
    # Fallback for isolated testing if artifacts module is not yet available
    # In production, this should strictly fail or be mocked.
    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        return bundle

try:
    from hae.evaluation.harness import ExecutionHarness, PASSED, FAILED, SKIPPED
except ImportError:
    # Fallbacks for isolated testing
    PASSED = 'passed'
    FAILED = 'failed'
    SKIPPED = 'skipped'
    
    class ExecutionHarness:
        def __init__(self, timeout_s: int = 60):
            self.timeout_s = timeout_s
        def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle'):
            raise NotImplementedError("ExecutionHarness not available")

# Constants
DEFAULT_VERIFY_BUDGET = 3
_MAX_DETAIL_CHARS = 400

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
        self.used = 0
        self.attempts: List[VerifyAttempt] = []
        
        # Initialize the harness with the specified timeout
        self.harness = ExecutionHarness(timeout_s=timeout_s)

    @property
    def remaining(self) -> int:
        """Returns the number of verification attempts remaining."""
        return max(0, self.budget - self.used)

    def _format_report(self, report: Any, file_count: int) -> str:
        """Formats the VerificationReport into an agent-readable string.
        
        Args:
            report: The VerificationReport object from the harness.
            file_count: Number of authored files in the bundle.
            
        Returns:
            A formatted string report.
        """
        # Extract gate status and details
        gate_status = report.gate_status
        gate_detail = report.gate_detail
        
        # Calculate passed and evaluable counts
        # Evaluable means status is not SKIPPED
        evaluable_count = 0
        passed_count = 0
        
        for name, status in gate_status.items():
            if status != SKIPPED:
                evaluable_count += 1
                if status == PASSED:
                    passed_count += 1

        # Header
        lines = []
        lines.append(f"{passed_count}/{evaluable_count} evaluable gate(s) passing across {file_count} authored file(s).")
        
        # Gate lines
        # Order: syntax, build, smoke, tests, telemetry
        gate_order = ("syntax", "build", "smoke", "tests", "telemetry")
        
        for name in gate_order:
            status = gate_status.get(name, SKIPPED)
            detail = gate_detail.get(name, "")
            
            # Truncate detail
            if len(detail) > _MAX_DETAIL_CHARS:
                detail = detail[:_MAX_DETAIL_CHARS]
            
            # Map status to display string
            if status == PASSED:
                display_status = "PASS"
            elif status == FAILED:
                display_status = "FAIL"
            else:
                display_status = "SKIP"
                
            lines.append(f"  [{display_status}] {name}: {detail}")
            
            # Special case for syntax failure
            if name == "syntax" and status == FAILED:
                lines.append("A file does not parse.")

        # Footer
        lines.append("SKIP means the gate could not be evaluated in this environment (usually an uninstalled third-party package). A skip is not a pass and earns no credit.")
        lines.append(f"({self.remaining} verification attempt(s) remaining.)")
        
        return "\n".join(lines)

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        # 1. Check Budget
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED"

        # 2. Get Bundle
        try:
            raw_bundle = self.workspace.export_bundle()
        except Exception:
            raw_bundle = {}
            
        filtered_bundle = filter_bundle(raw_bundle)
        
        # 3. Check Empty Bundle
        if not filtered_bundle:
            # Consume attempt
            self.used += 1
            return f"No files have been authored yet. Nothing to verify. ({self.remaining} verification attempt(s) remaining.)"

        # 4. Run Harness
        # Consume attempt
        self.used += 1
        
        try:
            report = self.harness.verify_bundle(filtered_bundle)
        except Exception as e:
            # If harness fails catastrophically, we still consumed the attempt.
            # We should probably report an error, but the spec implies we return a report.
            # However, if the harness itself crashes, we can't generate a standard report.
            # For robustness, we might return an error string, but let's assume harness handles internal errors.
            # If it raises, we catch it here.
            return f"Verification failed due to internal error: {str(e)}"

        # 5. Process Report
        gate_status = report.gate_status
        gate_detail = report.gate_detail
        
        # Calculate counts for the attempt record
        evaluable_count = 0
        passed_count = 0
        for name, status in gate_status.items():
            if status != SKIPPED:
                evaluable_count += 1
                if status == PASSED:
                    passed_count += 1
        
        authored_files = len(filtered_bundle)
        
        # Append to attempts
        attempt = VerifyAttempt(
            turn=turn,
            agent_role=agent_role,
            gate_status=gate_status,
            passed_count=passed_count,
            evaluable_count=evaluable_count,
            authored_files=authored_files
        )
        self.attempts.append(attempt)
        
        # Format and return report
        return self._format_report(report, authored_files)

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
            # Converged if the final attempt's passed count is >= the best passed count seen so far
            # Note: Since best_passed_count is the max over all attempts, 
            # final.passed_count >= best_passed_count implies final.passed_count == best_passed_count
            converged = bool(final_attempt.passed_count >= best_passed_count)
            
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