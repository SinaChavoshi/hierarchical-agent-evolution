"""Unit test suite for Active Tool Sandboxing and Workspace Execution."""

import unittest
import os
import shutil
from src.sandbox_env import AgentWorkspace
from src.company import parse_tool_action
from src.sandbox_verifier import DeterministicSandboxVerifier

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

    def test_asset_mounting(self):
        assets = [
            {"name": "pyproject.toml", "content": "[project]\nname='test-pkg'\n"},
            {"name": "src/core.py", "content": "VERSION = '1.0.0'\n"}
        ]
        mounted = self.workspace.mount_assets(assets)
        self.assertEqual(len(mounted), 2)
        self.assertTrue(os.path.exists(os.path.join(self.workspace.workspace_dir, "pyproject.toml")))
        self.assertTrue(os.path.exists(os.path.join(self.workspace.workspace_dir, "src/core.py")))

    def test_live_sandbox_verification(self):
        self.workspace.write_file("pyproject.toml", "[project]\nname='agent-org'\n")
        self.workspace.write_file("src/orchestrator.py", "class Orchestrator: pass\n")
        self.workspace.write_file("src/telemetry.py", "import opentelemetry; pass\n")
        self.workspace.write_file("tests/test_basic.py", "def test_ok(): assert 1 == 1\n")

        verifier = DeterministicSandboxVerifier()
        score = verifier.verify_package(self.test_cid, "Deliverable with OpenTelemetry spans", workspace=self.workspace)
        self.assertTrue(score.build_passed)
        self.assertTrue(score.smoke_passed)
        self.assertTrue(score.telemetry_passed)
        self.assertTrue(score.test_passed)
        self.assertEqual(score.score_penalty, 0.0)

if __name__ == "__main__":
    unittest.main()
