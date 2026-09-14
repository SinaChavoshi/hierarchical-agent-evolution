"""Unit test suite for Active Tool Sandboxing and Workspace Execution."""

import unittest
import os
import shutil
from hae.runtime.workspace import AgentWorkspace
from hae.runtime.company import parse_tool_action
from hae.evaluation.harness import ExecutionHarness

class TestActiveToolSandboxing(unittest.TestCase):

    def setUp(self):
        self.test_cid = "test_enterprise_sandboxing"
        self.workspace = AgentWorkspace(self.test_cid, base_dir="/tmp/hae_test_workspaces")

    def tearDown(self):
        self.workspace.cleanup()
        if os.path.exists("/tmp/hae_test_workspaces"):
            shutil.rmtree("/tmp/hae_test_workspaces", ignore_errors=True)

    def test_file_writing_and_reading(self):
        write_res = self.workspace.write_file("src/module.py", "def greet(): return 'hello'\n")
        self.assertEqual(write_res["status"], "ok")
        self.assertEqual(write_res["path"], "src/module.py")

        read_res = self.workspace.read_file("src/module.py")
        self.assertEqual(read_res["status"], "ok")
        self.assertEqual(read_res["content"], "def greet(): return 'hello'\n")

    def test_path_traversal_protection(self):
        with self.assertRaises(ValueError):
            self.workspace._resolve_path("../../etc/passwd")
        res = self.workspace.write_file("../../etc/passwd", "malicious content")
        self.assertEqual(res["status"], "error")
        self.assertIn("Path traversal", res["error"])

    def test_bash_execution(self):
        self.workspace.write_file("test_calc.py", "print(2 + 2)\n")
        res = self.workspace.execute_bash("python3 test_calc.py")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(res["stdout"].strip(), "4")

    def test_tool_action_parsing(self):
        text = """
I will create the system runtime now.
Action: write_file
Path: src/runtime.py
```python
class Runtime:
    pass
```
"""
        parsed = parse_tool_action(text)
        self.assertIsNotNone(parsed)
        action, args = parsed
        self.assertEqual(action, "write_file")
        self.assertEqual(args["path"], "src/runtime.py")
        self.assertIn("class Runtime", args["content"])


    def test_live_sandbox_verification(self):
        """A self-contained, working package clears the executable gates."""
        self.workspace.write_file("pyproject.toml", "[project]\nname='agent-org'\nversion='0.1.0'\n")
        self.workspace.write_file("src/orchestrator.py", "class Orchestrator:\n    def run(self):\n        return 'ok'\n")
        self.workspace.write_file("tests/test_basic.py", "def test_ok():\n    assert 1 == 1\n")

        report = ExecutionHarness().verify_workspace(
            self.workspace, "Deliverable text")

        self.assertEqual(report.source, "live workspace")
        self.assertTrue(report.gate("syntax").passed)
        self.assertTrue(report.gate("build").passed)
        self.assertTrue(report.gate("smoke").passed)
        self.assertTrue(report.gate("tests").passed)
        # No instrumentation was written, so telemetry must not pass.
        self.assertFalse(report.gate("telemetry").passed)

    def test_prose_cannot_satisfy_telemetry_gate(self):
        """Regression: the old verifier searched text that included the CEO's prose."""
        self.workspace.write_file("pyproject.toml", "[project]\nname='agent-org'\nversion='0.1.0'\n")
        self.workspace.write_file("src/orchestrator.py", "class Orchestrator:\n    pass\n")
        self.workspace.write_file("tests/test_basic.py", "def test_ok():\n    assert 1 == 1\n")

        report = ExecutionHarness().verify_workspace(
            self.workspace,
            "Our platform is fully instrumented with OpenTelemetry and uses "
            "tracer.start_as_current_span throughout for distributed tracing.")
        self.assertFalse(report.gate("telemetry").passed)

    def test_bare_import_without_spans_fails_telemetry(self):
        """Importing the package but never creating a span is not observability."""
        self.workspace.write_file("pyproject.toml", "[project]\nname='agent-org'\nversion='0.1.0'\n")
        self.workspace.write_file("src/telemetry.py", "import opentelemetry\n\nVALUE = 1\n")
        self.workspace.write_file("tests/test_basic.py", "def test_ok():\n    assert 1 == 1\n")

        report = ExecutionHarness().verify_workspace(self.workspace, "")
        self.assertFalse(report.gate("telemetry").passed)

    def test_broken_code_cannot_pass_syntax_gate(self):
        """The old verifier had no syntax gate at all."""
        self.workspace.write_file("pyproject.toml", "[project]\nname='agent-org'\nversion='0.1.0'\n")
        self.workspace.write_file("src/orchestrator.py", "class Orchestrator\n    pass\n")
        self.workspace.write_file("tests/test_basic.py", "def test_ok():\n    assert 1 == 1\n")

        report = ExecutionHarness().verify_workspace(self.workspace, "")
        self.assertFalse(report.gate("syntax").passed)
        self.assertGreater(report.score_penalty, 0.0)

    def test_opex_breakdown_schema_and_extras(self):
        from hae.genome.schema import OpExBreakdown
        # An undeclared field is a typo until proven otherwise, so the
        # constructor rejects it. V1 accepted anything and silently attached it.
        with self.assertRaises(TypeError):
            OpExBreakdown(flash_input_tokens=100, arbitrary_future_field=123.45)

        # Deserialising archived data is different: unknown keys are preserved
        # in `extra` and survive a round trip, but are never promoted to
        # attributes, so code cannot come to depend on them.
        opex = OpExBreakdown.from_dict({
            "flash_input_tokens": 100,
            "flash_output_tokens": 200,
            "arbitrary_future_field": 123.45,
        })
        self.assertEqual(opex.flash_input_tokens, 100)
        self.assertFalse(hasattr(opex, "arbitrary_future_field"))
        self.assertEqual(opex.extra["arbitrary_future_field"], 123.45)
        d = opex.to_dict()
        self.assertEqual(d["arbitrary_future_field"], 123.45)

if __name__ == "__main__":
    unittest.main()
