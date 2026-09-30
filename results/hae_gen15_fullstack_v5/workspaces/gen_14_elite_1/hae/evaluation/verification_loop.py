"""
hae/evaluation/verification_loop.py

Implements the in-loop ground-truth verification capability (Generation 11).
This module allows agents to request a verification report from the same
ExecutionHarness used by the evaluator, subject to a strict budget.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Import dependencies from parallel modules
# These are assumed to be available in the environment as per the contract
try:
    from hae.evaluation.artifacts import filter_bundle
except ImportError:
    # Fallback for isolated testing if artifacts module is not yet present
    def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
        return bundle

try:
    from hae.evaluation.harness import ExecutionHarness, PASSED, FAILED, SKIPPED
except ImportError:
    # Fallbacks for isolated testing if harness module is not yet present
    PASSED = 'passed'
    FAILED = 'failed'
    SKIPPED = 'skipped'
    
    class ExecutionHarness:
        def __init__(self, timeout_s: int = 60):
            self.timeout_s = timeout_s
        def verify_bundle(self, bundle: Dict[str, str], source: str = 'bundle') -> Any:
            # Mock implementation for type checking if harness is missing
            raise NotImplementedError("ExecutionHarness not available")

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
        self.used = 0
        self.attempts: List[VerifyAttempt] = []
        self.harness = ExecutionHarness(timeout_s=timeout_s)

    @property
    def remaining(self) -> int:
        """Returns the number of verification attempts remaining."""
        return self.budget - self.used

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        # 1. Check Budget
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED"

        # 2. Get Workspace Bundle
        try:
            raw_bundle = self.workspace.export_bundle()
        except Exception:
            raw_bundle = {}
        
        # Filter out generated/malformed files
        bundle = filter_bundle(raw_bundle)
        
        # 3. Check for Empty Workspace
        if not bundle:
            # Consume attempt but do not record VerifyAttempt
            self.used += 1
            return (
                f"No files have been authored yet. Nothing to verify. "
                f"({self.remaining} verification attempt(s) remaining.)"
            )

        # 4. Run Harness
        self.used += 1
        report = self.harness.verify_bundle(bundle)
        
        # 5. Process Results
        gate_status = report.gate_status
        gate_detail = report.gate_detail
        
        # Count passed and evaluable gates
        passed_count = 0
        evaluable_count = 0
        
        # Define gate order for reporting
        gate_names = ("syntax", "build", "smoke", "tests", "telemetry")
        
        for name in gate_names:
            status = gate_status.get(name, SKIPPED)
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
            authored_files=len(bundle)
        )
        self.attempts.append(attempt)

        # 6. Format Report
        file_count = len(bundle)
        header = f"{passed_count}/{evaluable_count} evaluable gate(s) passing across {file_count} authored file(s)."
        
        lines = [header]
        
        for name in gate_names:
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

        # Special handling for syntax failure
        if gate_status.get("syntax") == FAILED:
            lines.append("A file does not parse.")

        # Standard footer
        lines.append(
            "SKIP means the gate could not be evaluated in this environment "
            "(usually an uninstalled third-party package). A skip is not a pass "
            "and earns no credit."
        )
        lines.append(f"({self.remaining} verification attempt(s) remaining.)")

        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        if not self.attempts:
            best_passed_count = 0
            final_gate_status = {}
            converged = False
            attempts_list = []
        else:
            best_passed_count = max(a.passed_count for a in self.attempts)
            final_attempt = self.attempts[-1]
            final_gate_status = final_attempt.gate_status
            # Converged if the final attempt's passed count is at least the best seen so far
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