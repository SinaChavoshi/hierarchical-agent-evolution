"""
In-loop ground-truth verification for agent workspaces.

This module provides the `VerificationLoop` class, which allows agents to
request a verification of their current workspace state against the real
grading harness (`ExecutionHarness`). It enforces a budget on these calls
to prevent excessive resource usage and ensures that the feedback provided
is strictly factual (gate verdicts) without coaching or suggestions.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Import dependencies from the parallel modules
# These are assumed to be available in the environment as per the spec
try:
    from hae.evaluation.artifacts import filter_bundle
except ImportError:
    # Fallback for isolated testing if artifacts module isn't fully linked yet
    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        return bundle

try:
    from hae.evaluation.harness import ExecutionHarness, PASSED, FAILED, SKIPPED
except ImportError:
    # Fallback constants if harness module isn't fully linked yet
    PASSED = 'passed'
    FAILED = 'failed'
    SKIPPED = 'skipped'
    
    class ExecutionHarness:
        def __init__(self, timeout_s: int = 45):
            self.timeout_s = timeout_s
        def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> Any:
            # Mock implementation for syntax check only if harness missing
            raise NotImplementedError("ExecutionHarness not available")

DEFAULT_VERIFY_BUDGET = 3

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

    _MAX_DETAIL_CHARS = 400
    _GATE_ORDER = ("syntax", "build", "smoke", "tests", "telemetry")
    _COACHING_PHRASES = ("you should", "try adding", "rewrite it as", "suggestion:")

    def __init__(self, workspace: Any, budget: int = DEFAULT_VERIFY_BUDGET, timeout_s: int = 45):
        self.workspace = workspace
        self.budget = budget
        self.timeout_s = timeout_s
        self.used = 0
        self.attempts: List[VerifyAttempt] = []
        self.harness = ExecutionHarness(timeout_s=timeout_s)

    @property
    def remaining(self) -> int:
        """Returns the number of verification attempts remaining."""
        return max(0, self.budget - self.used)

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        # 1. Check Budget
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED"

        # 2. Get Workspace Files
        try:
            raw_bundle = self.workspace.export_bundle()
        except Exception:
            raw_bundle = {}
        
        filtered_bundle = filter_bundle(raw_bundle)
        authored_files_count = len(filtered_bundle)

        # 3. Handle Empty Workspace
        if authored_files_count == 0:
            self.used += 1
            return (
                f"No files have been authored yet. Nothing to verify. "
                f"({self.remaining} verification attempt(s) remaining.)"
            )

        # 4. Run Harness
        self.used += 1
        report = self.harness.verify_bundle(filtered_bundle)
        
        # 5. Process Results
        gate_status = report.gate_status
        gate_detail = report.gate_detail
        
        # Count passed and evaluable gates
        passed_count = 0
        evaluable_count = 0
        
        for gate_name in self._GATE_ORDER:
            status = gate_status.get(gate_name, SKIPPED)
            if status != SKIPPED:
                evaluable_count += 1
                if status == PASSED:
                    passed_count += 1

        # Record Attempt
        attempt = VerifyAttempt(
            turn=turn,
            agent_role=agent_role,
            gate_status=gate_status,
            passed_count=passed_count,
            evaluable_count=evaluable_count,
            authored_files=authored_files_count
        )
        self.attempts.append(attempt)

        # 6. Format Report
        lines = []
        
        # Header
        header = f"{passed_count}/{evaluable_count} evaluable gate(s) passing across {authored_files_count} authored file(s)."
        lines.append(header)
        
        # Gate Details
        for gate_name in self._GATE_ORDER:
            status = gate_status.get(gate_name, SKIPPED)
            detail = gate_detail.get(gate_name, "")
            
            # Truncate detail
            if len(detail) > self._MAX_DETAIL_CHARS:
                detail = detail[:self._MAX_DETAIL_CHARS]
            
            # Map status to display string
            if status == PASSED:
                display_status = "PASS"
            elif status == FAILED:
                display_status = "FAIL"
            else:
                display_status = "SKIP"
                
            line = f"  [{display_status}] {gate_name}: {detail}"
            lines.append(line)
            
            # Specific requirement: If syntax is FAILED, include specific message
            if gate_name == "syntax" and status == FAILED:
                lines.append("A file does not parse.")

        # Footer / Explanation
        lines.append("SKIP means the gate could not be evaluated in this environment (usually an uninstalled third-party package). A skip is not a pass and earns no credit.")
        lines.append(f"({self.remaining} verification attempt(s) remaining.)")
        
        report_text = "\n".join(lines)
        
        # Safety check: Ensure no coaching phrases
        lower_report = report_text.lower()
        for phrase in self._COACHING_PHRASES:
            if phrase in lower_report:
                # This should theoretically never happen if harness details are clean,
                # but we enforce the contract strictly.
                raise ValueError(f"Coaching phrase detected in report: {phrase}")
                
        return report_text

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
            # Converged if the final attempt's passed count is >= the best passed count
            # (i.e., we didn't regress from our best performance)
            converged = bool(final_attempt.passed_count >= best_passed_count)
            
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

VERIFY_TOOL_GUIDE = '- To check your work against the REAL grading gates:\n  Action: verify\n  This parses, installs, imports, tests and inspects your workspace with\n  the same harness that will score your firm, and returns the verdict.\n  It is expensive and strictly limited -- you get a small number of\n  attempts for the whole assignment, so write your code first and verify\n  when you believe it is complete.\n  Note: a gate reported as SKIP was not evaluated and earns no credit.\n  Only PASS counts.\n'