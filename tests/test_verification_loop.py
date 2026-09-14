"""Tests for Generation 11's in-loop ground-truth verification."""

import sys
import unittest

sys.path.insert(0, ".")

from hae.runtime.company import parse_tool_action
from hae.evaluation.harness import FAILED, PASSED, SKIPPED
from hae.evaluation.verification_loop import VERIFY_TOOL_GUIDE, VerificationLoop

WORKING_PACKAGE = {
    "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.0"\n',
    "demo/__init__.py": "",
    "demo/core.py": (
        "from opentelemetry import trace\n"
        "_t = trace.get_tracer(__name__)\n\n"
        "def add(a, b):\n"
        "    with _t.start_as_current_span('add'):\n"
        "        return a + b\n"
    ),
    "tests/test_core.py": "from demo.core import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
}

UNPARSEABLE = {"demo/core.py": "    def broken(self):\n        return 1\n"}

# The single most common real failure in the archive: a method body emitted
# without its enclosing class. 19 of 60 firms shipped something like this.
FRAGMENT = {"agent_org/core.py": "        self.state = {}\n        # ...\n"}


class FakeWorkspace:
    def __init__(self, files):
        self.files = dict(files)

    def export_bundle(self):
        return dict(self.files)


class ExplodingWorkspace:
    def export_bundle(self):
        raise RuntimeError("workspace gone")


class TestBudget(unittest.TestCase):

    def test_budget_is_consumed(self):
        loop = VerificationLoop(FakeWorkspace(UNPARSEABLE), budget=2, timeout_s=15)
        self.assertEqual(loop.remaining, 2)
        loop.verify()
        self.assertEqual(loop.remaining, 1)
        loop.verify()
        self.assertEqual(loop.remaining, 0)

    def test_exhausted_budget_refuses_without_running(self):
        loop = VerificationLoop(FakeWorkspace(UNPARSEABLE), budget=1, timeout_s=15)
        loop.verify()
        out = loop.verify()
        self.assertIn("BUDGET EXHAUSTED", out)
        # The refusal must not have recorded a second attempt.
        self.assertEqual(len(loop.attempts), 1)

    def test_empty_workspace_costs_an_attempt_but_records_none(self):
        # Charging for it stops an agent probing the harness for free; not
        # recording it keeps the attempt history meaningful.
        loop = VerificationLoop(FakeWorkspace({}), budget=2, timeout_s=15)
        out = loop.verify()
        self.assertIn("No files have been authored", out)
        self.assertEqual(loop.used, 1)
        self.assertEqual(loop.attempts, [])

    def test_unreadable_workspace_is_treated_as_empty(self):
        loop = VerificationLoop(ExplodingWorkspace(), budget=1, timeout_s=15)
        self.assertIn("No files have been authored", loop.verify())


class TestReportContent(unittest.TestCase):

    def test_broken_code_reports_every_gate_failing(self):
        loop = VerificationLoop(FakeWorkspace(UNPARSEABLE), budget=1, timeout_s=15)
        out = loop.verify()
        self.assertIn("[FAIL] syntax", out)
        self.assertIn("unexpected indent", out)
        self.assertIn("0/5 evaluable", out)

    def test_syntax_failure_is_called_out_explicitly(self):
        loop = VerificationLoop(FakeWorkspace(FRAGMENT), budget=1, timeout_s=15)
        out = loop.verify()
        self.assertIn("does not parse", out)

    def test_working_package_passes_syntax_and_telemetry(self):
        loop = VerificationLoop(FakeWorkspace(WORKING_PACKAGE), budget=1, timeout_s=25)
        loop.verify()
        status = loop.attempts[0].gate_status
        self.assertEqual(status["syntax"], PASSED)
        self.assertEqual(status["telemetry"], PASSED)

    def test_report_explains_that_skip_is_not_a_pass(self):
        # An agent that reads SKIP as success will stop working on the gate.
        loop = VerificationLoop(FakeWorkspace(WORKING_PACKAGE), budget=1, timeout_s=25)
        out = loop.verify()
        self.assertIn("A skip is not a", out)

    def test_report_states_remaining_budget(self):
        loop = VerificationLoop(FakeWorkspace(UNPARSEABLE), budget=3, timeout_s=15)
        self.assertIn("2 verification attempt(s) remaining", loop.verify())

    def test_report_does_not_prescribe_a_fix(self):
        # Selection pressure should come from the population. The harness
        # reports verdicts, it does not coach.
        loop = VerificationLoop(FakeWorkspace(UNPARSEABLE), budget=1, timeout_s=15)
        out = loop.verify().lower()
        for coaching in ("you should", "try adding", "rewrite it as", "suggestion:"):
            self.assertNotIn(coaching, out)

    def test_detail_is_truncated_to_keep_context_small(self):
        noisy = dict(UNPARSEABLE)
        noisy["tests/test_noise.py"] = "assert False, '" + "x" * 5000 + "'\n"
        loop = VerificationLoop(FakeWorkspace(noisy), budget=1, timeout_s=20)
        for line in loop.verify().split("\n"):
            self.assertLess(len(line), 1000)


class TestArtifactHygiene(unittest.TestCase):

    def test_build_byproducts_are_not_counted_as_authored(self):
        # Without this an agent could inflate its apparent output simply by
        # invoking pytest, pass or fail.
        polluted = dict(UNPARSEABLE)
        polluted[".pytest_cache/v/cache/nodeids"] = "[]"
        polluted["demo/__pycache__/core.cpython-311.pyc"] = "junk"
        loop = VerificationLoop(FakeWorkspace(polluted), budget=1, timeout_s=15)
        loop.verify()
        self.assertEqual(loop.attempts[0].authored_files, 1)


class TestSummary(unittest.TestCase):

    def test_summary_records_attempt_history(self):
        loop = VerificationLoop(FakeWorkspace(UNPARSEABLE), budget=3, timeout_s=15)
        loop.verify(turn=1, agent_role="Engineer")
        loop.verify(turn=2, agent_role="Engineer")
        summary = loop.summary()
        self.assertEqual(summary["attempt_count"], 2)
        self.assertEqual(summary["used"], 2)
        self.assertEqual(summary["attempts"][0]["agent_role"], "Engineer")
        self.assertEqual(summary["attempts"][1]["turn"], 2)

    def test_summary_is_empty_before_any_verification(self):
        summary = VerificationLoop(FakeWorkspace(WORKING_PACKAGE)).summary()
        self.assertEqual(summary["attempt_count"], 0)
        self.assertEqual(summary["final_gate_status"], {})

    def test_summary_tracks_the_best_result_not_just_the_last(self):
        # A firm that verifies, passes, then breaks its own code should not be
        # recorded as if the last attempt were its high-water mark.
        ws = FakeWorkspace(WORKING_PACKAGE)
        loop = VerificationLoop(ws, budget=3, timeout_s=25)
        loop.verify()
        best_after_good = loop.summary()["best_passed_count"]
        ws.files = dict(UNPARSEABLE)
        loop.verify()
        summary = loop.summary()
        self.assertEqual(summary["best_passed_count"], best_after_good)
        self.assertGreater(best_after_good, 0)
        self.assertFalse(summary["converged"], "regression should not count as converged")


class TestToolWiring(unittest.TestCase):

    def test_verify_action_is_parsed(self):
        self.assertEqual(
            parse_tool_action("Reasoning here.\nAction: verify\n"), ("verify", {}))

    def test_verify_action_is_case_insensitive(self):
        self.assertEqual(parse_tool_action("Action: VERIFY")[0], "verify")

    def test_other_actions_still_parse(self):
        for text, expected in (
            ("Action: list_files", "list_files"),
            ("Action: finish", "finish"),
            ("Action: read_file\nPath: a.py", "read_file"),
        ):
            self.assertEqual(parse_tool_action(text)[0], expected)

    def test_tool_guide_warns_that_skip_earns_nothing(self):
        self.assertIn("SKIP", VERIFY_TOOL_GUIDE)
        self.assertIn("Only PASS counts", VERIFY_TOOL_GUIDE)

    def test_runner_exposes_a_verification_loop(self):
        import inspect
        from hae.runtime import company
        source = inspect.getsource(company.HierarchicalCompanyRunner.__init__)
        self.assertIn("VerificationLoop", source)


if __name__ == "__main__":
    unittest.main()
