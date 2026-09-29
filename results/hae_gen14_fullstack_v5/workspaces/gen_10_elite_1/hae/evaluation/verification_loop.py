"""
hae/evaluation/verification_loop.py

Generation 11 capability: in-loop ground-truth verification.

This module closes the loop between agent actions and evaluation gates.
It provides a `VerificationLoop` that agents can invoke to receive the exact
gate report that will determine their score, computed by the same
`ExecutionHarness` the evaluator uses.

Key constraints:
1. Verification is rate-limited (budgeted) to preserve measurement integrity.
2. The report states what is wrong, never how to fix it.
"""

import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
            workspace: The live agent workspace object. Expected to have
                       attributes or methods compatible with ExecutionHarness
                       (e.g., providing file bundles or directory paths).
            budget: Maximum number of verify calls allowed.
            timeout_s: Timeout for the underlying harness execution.
        """
        self.workspace = workspace
        self.budget = budget
        self.timeout_s = timeout_s
        self.attempts: List[VerifyAttempt] = []
        
        # Lazy import to avoid circular dependencies or missing deps at module load
        # if the harness is not yet available in the environment during initial setup.
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
        """Lazily initializes and returns the ExecutionHarness."""
        if self._harness is None:
            try:
                from hae.evaluation.harness import ExecutionHarness
                self._harness = ExecutionHarness(timeout_s=self.timeout_s)
            except ImportError:
                # Fallback or error handling if harness is missing
                raise RuntimeError("ExecutionHarness not available. Cannot verify.")
        return self._harness

    def _extract_bundle_from_workspace(self) -> Dict[str, str]:
        """
        Extracts a path->content map from the workspace.
        
        This method attempts to interface with the workspace object to get
        the current state of authored files. It relies on the workspace
        providing a way to list files and read their contents, or providing
        a bundle directly.
        """
        bundle = {}
        
        # Strategy 1: Workspace might have a 'get_bundle' or 'files' attribute
        if hasattr(self.workspace, 'get_bundle'):
            try:
                return self.workspace.get_bundle()
            except Exception:
                pass
        
        if hasattr(self.workspace, 'files'):
            try:
                # Assuming files is a dict of path -> content
                if isinstance(self.workspace.files, dict):
                    return dict(self.workspace.files)
            except Exception:
                pass

        # Strategy 2: Workspace might be a directory path or have a 'path' attribute
        # In this case, we rely on the harness to verify the directory directly
        # if it supports verify_directory. However, verify_workspace usually
        # expects a bundle or a workspace object it knows how to introspect.
        
        # Strategy 3: If workspace has 'list_files' and 'read_file'
        if hasattr(self.workspace, 'list_files') and hasattr(self.workspace, 'read_file'):
            try:
                files = self.workspace.list_files()
                for path in files:
                    try:
                        content = self.workspace.read_file(path)
                        bundle[path] = content
                    except Exception:
                        continue
                return bundle
            except Exception:
                pass

        # If we can't extract a bundle, return empty. The harness might handle
        # the workspace object directly if it's a known type, but usually
        # verify_workspace takes a workspace object.
        return bundle

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return (
                "VERIFY BUDGET EXHAUSTED.\n"
                "You have used all allowed verification attempts. "
                "No further verification is possible."
            )

        harness = self._get_harness()
        
        try:
            # Attempt to verify the workspace.
            # The harness.verify_workspace expects a workspace object.
            # If the workspace is a simple object, we might need to pass it directly.
            # If it's a path, we might need to use verify_directory.
            # The spec says: "Runs the real execution harness against a live workspace"
            # and "harness.verify_workspace(workspace, deliverable_text)"
            
            # We assume the workspace object is compatible with ExecutionHarness.verify_workspace
            report = harness.verify_workspace(self.workspace, deliverable_text='')
            
        except Exception as e:
            # If the harness itself fails to run (e.g. internal error), we report that.
            tb = traceback.format_exc()
            return f"VERIFICATION ERROR:\n{tb}"

        # Process the report into a VerifyAttempt
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

        # Format the report for the agent
        # Terse, states what is wrong, never how to fix it.
        lines = []
        lines.append(f"VERIFICATION REPORT (Attempt {len(self.attempts)}/{self.budget})")
        
        # Specific string required by tests: "2 verification attempt(s) remaining"
        rem = self.remaining
        lines.append(f"{rem} verification attempt(s) remaining")
        lines.append("")
        
        # Check for empty workspace condition
        if authored_files == 0:
            lines.append("No files have been authored.")
            lines.append("")
            lines.append("A skip is not a pass. Gates that could not be evaluated are marked SKIP.")
            lines.append("")
            # Still list gates as skipped if they were skipped due to no files
            for gate in report.gates:
                status = gate.status.upper()
                name = gate.name.upper()
                if status == 'SKIPPED':
                    lines.append(f"[SKIP] {name}")
                    if gate.detail:
                        lines.append(f"       Reason: {gate.detail}")
                elif status == 'FAILED':
                    lines.append(f"[FAIL] {name}")
                    if gate.detail:
                        lines.append(f"       Detail: {gate.detail}")
                else:
                    lines.append(f"[{status}] {name}")
            return "\n".join(lines)

        if not report.gates:
            lines.append("NO GATES EVALUATED.")
            return "\n".join(lines)

        for gate in report.gates:
            status = gate.status.upper()
            name = gate.name.upper()
            detail = gate.detail
            
            if status == 'PASSED':
                lines.append(f"[PASS] {name}")
            elif status == 'FAILED':
                lines.append(f"[FAIL] {name}")
                if detail:
                    # Truncate detail if too long, but keep it informative
                    # The spec says "echoes real tracebacks and real gate verdicts"
                    # but "terse by design".
                    # We include the detail as it is the "what is wrong".
                    # Truncate to 500 chars to keep context small
                    truncated_detail = detail[:500] + "..." if len(detail) > 500 else detail
                    lines.append(f"       Detail: {truncated_detail}")
            elif status == 'SKIPPED':
                lines.append(f"[SKIP] {name}")
                if detail:
                    lines.append(f"       Reason: {detail}")
            else:
                lines.append(f"[{status}] {name}")
                if detail:
                    lines.append(f"       Detail: {detail}")

        lines.append("")
        lines.append(f"Summary: {passed_count}/{evaluable_count} evaluated gates passed.")
        lines.append("A skip is not a pass. Only PASS counts.")
        
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        """Verification history for the scorecard."""
        best_passed = 0
        if self.attempts:
            best_passed = max(a.passed_count for a in self.attempts)
            
        # Determine final gate status from the last attempt
        final_gate_status = {}
        if self.attempts:
            final_gate_status = self.attempts[-1].gate_status
            
        # Determine if converged (all evaluated gates passed in the last attempt)
        converged = False
        if self.attempts:
            last_attempt = self.attempts[-1]
            if last_attempt.evaluable_count > 0 and last_attempt.passed_count == last_attempt.evaluable_count:
                converged = True
            elif last_attempt.evaluable_count == 0:
                # If nothing was evaluable, it's not converged in a meaningful way
                converged = False

        return {
            "attempt_count": len(self.attempts),
            "total_attempts": len(self.attempts),
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