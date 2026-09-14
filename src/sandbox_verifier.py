"""Deterministic sandbox verification for candidate platform packages.

This module now delegates to `src/execution_harness.py`, which verifies a
workspace by actually parsing, importing, and running it. The previous
implementation was heuristic in three of its four gates:

    Build     -> filename substring match; `pip install` was never invoked.
    Smoke     -> `has_runtime and len(files) >= 3`; nothing was imported.
    Telemetry -> substring search over text that *included the CEO's prose*, so
                 an essay mentioning OpenTelemetry passed.
    Tests     -> ran pytest, but when pytest was unavailable it fell back to
                 `any("def test_" in c or "assert " in c ...)` -- a string match
                 that could report PASS for code which does not even parse.

`VerificationScore` retains its original field names so existing readers and
archived scorecards remain compatible.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .artifacts import filter_bundle
from .execution_harness import ExecutionHarness, FAILED, PASSED, SKIPPED
from .sandbox_env import AgentWorkspace


@dataclass
class VerificationScore:
    build_passed: bool
    test_passed: bool
    smoke_passed: bool
    telemetry_passed: bool
    pass_rate: float
    total_tests: int
    score_penalty: float
    details: str
    # Richer detail from the harness; older readers can ignore these.
    syntax_passed: bool = False
    gate_status: Dict[str, str] = field(default_factory=dict)
    gate_detail: Dict[str, str] = field(default_factory=dict)
    authored_files: int = 0
    python_files: int = 0


class DeterministicSandboxVerifier:
    """Verifies a firm's package by executing it."""

    def __init__(self, work_dir: str = "/tmp/sandbox_verification",
                 timeout_s: int = 60):
        self.work_dir = work_dir
        os.makedirs(self.work_dir, exist_ok=True)
        self.harness = ExecutionHarness(timeout_s=timeout_s)

    def extract_code_blocks(self, text: str) -> Dict[str, str]:
        """Extracts files and code blocks embedded in a firm's deliverable text.

        Only used when no live workspace is available.
        """
        files: Dict[str, str] = {}
        pattern = (r'(?:###\s*File:\s*[`"]?([a-zA-Z0-9_\-\./]+)[`"]?'
                   r'|```(?:python|yaml|toml)\s*#?\s*([a-zA-Z0-9_\-\./]+)?)\n(.*?)```')
        for m in re.findall(pattern, text, re.DOTALL):
            filename = m[0] or m[1]
            if filename and "." in filename:
                files[filename.strip()] = m[2].strip()
        return files

    def verify_package(
        self,
        company_id: str,
        deliverable_text: str,
        workspace: Optional[AgentWorkspace] = None,
    ) -> VerificationScore:
        """Runs execution-grounded verification on the firm's code."""
        if workspace and workspace.list_files():
            bundle = workspace.export_bundle()
            source_label = "Live Sandbox"
        else:
            # Deliberately does NOT include deliverable_text in the gate inputs;
            # prose must never be able to satisfy a gate.
            bundle = self.extract_code_blocks(deliverable_text)
            source_label = "Extracted"

        bundle = filter_bundle(bundle)
        report = self.harness.verify_bundle(bundle)

        def st(name: str) -> str:
            g = report.gate(name)
            return g.status if g else SKIPPED

        gate_status = {g.name: g.status for g in report.gates}
        gate_detail = {g.name: g.detail for g in report.gates}

        evaluated = [g for g in report.gates if g.evaluated]
        passed = [g for g in report.gates if g.passed]
        pass_rate = (len(passed) / len(evaluated)) if evaluated else 0.0

        marks = []
        for g in report.gates:
            mark = {PASSED: "PASS", FAILED: "FAIL", SKIPPED: "SKIP"}[g.status]
            marks.append(f"{g.name.capitalize()}: {mark}")

        details = (f"[{source_label} / Execution Harness] "
                   f"{report.authored_files} authored files "
                   f"({report.python_files} .py). " + ", ".join(marks) + ".")

        return VerificationScore(
            build_passed=st("build") == PASSED,
            test_passed=st("tests") == PASSED,
            smoke_passed=st("smoke") == PASSED,
            telemetry_passed=st("telemetry") == PASSED,
            syntax_passed=st("syntax") == PASSED,
            pass_rate=round(pass_rate, 3),
            total_tests=len(report.gates),
            score_penalty=report.score_penalty,
            details=details,
            gate_status=gate_status,
            gate_detail={k: v[:300] for k, v in gate_detail.items()},
            authored_files=report.authored_files,
            python_files=report.python_files,
        )
