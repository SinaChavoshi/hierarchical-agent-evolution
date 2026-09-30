"""Generation 11 capability: in-loop ground-truth verification.

Through Generation 10 the agents and the evaluator were looking at different
things. Agents could run `execute_bash` and see raw pytest output, but the
score was set by gates they never saw -- and those gates were heuristics, so
even an agent that had somehow inspected them would have learned the wrong
lesson. The measured consequences across 60 archived firms:

    19 of 60 firms shipped Python that does not parse
    telemetry passed 55/60 on heuristics, 15/60 when actually executed
    no firm has ever passed all five gates

This module closes that loop. A technical agent can issue

    Action: verify

and receive back exactly the gate report that will determine its score,
computed by the same `ExecutionHarness` the evaluator uses. There is no second
implementation to drift.

Two deliberate constraints.

**Verification is rate-limited.** Each department pod gets a small budget of
verify calls. The harness installs packages and runs test suites, so it is the
most expensive tool available, and an unbudgeted agent will spin on it. The
budget also preserves the thing being measured: a firm that needs twenty
attempts to produce parseable code is not equivalent to one that gets it right
the first time, and the report says which happened.

  - If `self.remaining <= 0`, `verify()` returns an observation containing
    `"BUDGET EXHAUSTED"` without incrementing `self.used` or appending to
    `self.attempts`.
  - Workspace files are read via `filter_bundle(self.workspace.export_bundle())`
    (treating any exception from `export_bundle()` as `{}`). If the filtered
    bundle is empty (`0` authored files), `verify()` still consumes `1` attempt
    (`self.used += 1`) and returns an observation stating `"No files have been
    authored yet. Nothing to verify. ({self.remaining} verification attempt(s)
    remaining.)"` WITHOUT appending a `VerifyAttempt` to `self.attempts`.
  - When authored files exist, `verify()` increments `self.used += 1`, runs
    `self.harness.verify_bundle(bundle)`, appends a `VerifyAttempt(turn,
    agent_role, gate_status, passed_count, evaluable_count, authored_files)` to
    `self.attempts` (where `evaluable_count` counts gates whose status is not
    `SKIPPED`), and formats the report.

**The report states what is wrong, never how to fix it.** It echoes real
tracebacks and real gate verdicts. It does not suggest edits. Selection
pressure is supposed to come from the population, not from the harness
coaching every firm toward the same answer.

  - The formatted report header states `"{passed}/{evaluable} evaluable gate(s)
    passing across {file_count} authored file(s)."` followed by one line per
    gate in `("syntax", "build", "smoke", "tests", "telemetry")` formatted as
    `"  [{PASS|FAIL|SKIP}] {name}: {detail}"`, with single-line `detail`
    truncated to `_MAX_DETAIL_CHARS` (`400`) characters.
  - When `syntax` is `FAILED`, the report explicitly includes `"A file does not
    parse."`
  - The report always explains `"SKIP means the gate could not be evaluated in
    this environment (usually an uninstalled third-party package). A skip is not
    a pass and earns no credit."` and ends with `"({self.remaining}
    verification attempt(s) remaining.)"`. It must never contain coaching
    phrases (`"you should"`, `"try adding"`, `"rewrite it as"`, `"suggestion:"`).
  - `summary()` returns `{"budget", "used", "attempt_count",
    "best_passed_count", "final_gate_status", "converged", "attempts"}`, where
    `best_passed_count` tracks the high-water mark (`max(a.passed_count for a in
    self.attempts)`), `converged` is `bool(final and final.passed_count >=
    best_passed_count)`, and each item in `attempts` has keys `turn`,
    `agent_role`, `gate_status`, `passed`, `evaluable`, and `authored_files`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hae.evaluation.artifacts import filter_bundle
from hae.evaluation.harness import ExecutionHarness, SKIPPED, PASSED, FAILED

DEFAULT_VERIFY_BUDGET = 3
_MAX_DETAIL_CHARS = 400

VERIFY_TOOL_GUIDE = '- To check your work against the REAL grading gates:\n  Action: verify\n  This parses, installs, imports, tests and inspects your workspace with\n  the same harness that will score your firm, and returns the verdict.\n  It is expensive and strictly limited -- you get a small number of\n  attempts for the whole assignment, so write your code first and verify\n  when you believe it is complete.\n  Note: a gate reported as SKIP was not evaluated and earns no credit.\n  Only PASS counts.\n'

GATE_ORDER = ("syntax", "build", "smoke", "tests", "telemetry")


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
        return max(0, self.budget - self.used)

    def verify(self, turn: int = 0, agent_role: str = '') -> str:
        """Runs every gate and returns an agent-readable report.

        The string is what lands in the agent's context, so it is terse by
        design.
        """
        if self.remaining <= 0:
            return "BUDGET EXHAUSTED"

        # Read workspace files
        try:
            raw_bundle = self.workspace.export_bundle()
        except Exception:
            raw_bundle = {}
        
        bundle = filter_bundle(raw_bundle)
        authored_files = len(bundle)

        if authored_files == 0:
            self.used += 1
            return f"No files have been authored yet. Nothing to verify. ({self.remaining} verification attempt(s) remaining.)"

        self.used += 1
        
        # Run harness
        report = self.harness.verify_bundle(bundle)
        
        gate_status = report.gate_status
        gate_detail = report.gate_detail
        
        passed_count = sum(1 for s in gate_status.values() if s == PASSED)
        evaluable_count = sum(1 for s in gate_status.values() if s != SKIPPED)
        
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
        
        for name in GATE_ORDER:
            status = gate_status.get(name, SKIPPED)
            detail = gate_detail.get(name, "")
            
            # Truncate detail to single line and max chars
            detail = detail.replace('\n', ' ').replace('\r', ' ')
            if len(detail) > _MAX_DETAIL_CHARS:
                detail = detail[:_MAX_DETAIL_CHARS]
                
            status_icon = "PASS" if status == PASSED else ("SKIP" if status == SKIPPED else "FAIL")
            lines.append(f"  [{status_icon}] {name}: {detail}")
            
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
            final = self.attempts[-1]
            final_gate_status = final.gate_status
            converged = bool(final.passed_count >= best_passed_count)
            
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