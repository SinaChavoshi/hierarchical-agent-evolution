import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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

# Import dependencies
try:
    from hae.evaluation.artifacts import filter_bundle
except ImportError:
    # Fallback if artifacts module is not yet available or in a different path
    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        return bundle

try:
    from hae.evaluation.harness import ExecutionHarness, PASSED, FAILED, SKIPPED
except ImportError:
    # Fallback if harness module is not yet available
    PASSED = 'passed'
    FAILED = 'failed'
    SKIPPED = 'skipped'
    
    class ExecutionHarness:
        def __init__(self, timeout_s: int = 60):
            self.timeout_s = timeout_s
        def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> Any:
            # Mock implementation for type checking if harness is missing
            raise NotImplementedError("ExecutionHarness not available")

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
        self.harness = ExecutionHarness(timeout_s=timeout_s)

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
        
        bundle = filter_bundle(raw_bundle)
        
        # Check if empty
        if not bundle:
            self.used += 1
            return f"No files have been authored yet. Nothing to verify. ({self.remaining} verification attempt(s) remaining.)"

        # Increment usage
        self.used += 1
        
        # Run harness
        try:
            report = self.harness.verify_bundle(bundle)
        except Exception as e:
            # If harness fails catastrophically, we still need to return something
            # But typically verify_bundle should return a report object
            return f"Verification failed with error: {str(e)}"

        # Extract data from report
        gate_status = report.gate_status
        gate_detail = report.gate_detail
        
        # Count passed and evaluable
        passed_count = 0
        evaluable_count = 0
        
        for name in ("syntax", "build", "smoke", "tests", "telemetry"):
            status = gate_status.get(name, SKIPPED)
            if status != SKIPPED:
                evaluable_count += 1
                if status == PASSED:
                    passed_count += 1
        
        authored_files = report.authored_files
        
        # Record attempt
        attempt = VerifyAttempt(
            turn=turn,
            agent_role=agent_role,
            gate_status=gate_status,
            passed_count=passed_count,
            evaluable_count=evaluable_count,
            authored_files=authored_files
        )
        self.attempts.append(attempt)
        
        # Format report
        lines = []
        lines.append(f"{passed_count}/{evaluable_count} evaluable gate(s) passing across {authored_files} authored file(s).")
        
        for name in ("syntax", "build", "smoke", "tests", "telemetry"):
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
            
            # Specific requirement: When syntax is FAILED, include specific message
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
            # Note: 'final' here refers to the last attempt object
            converged = bool(final_attempt and final_attempt.passed_count >= best_passed_count)
        
        attempts_list = []
        for a in self.attempts:
            attempts_list.append({
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
            "attempts": attempts_list
        }