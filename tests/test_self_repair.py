"""Unit tests for Closed-Loop Sandbox Test Verification & Automated Code Self-Repair."""

import os
import json
import unittest
from unittest.mock import patch, MagicMock

from src.schema import CompanyGenome, AgentGenome, DepartmentGenome
from src.company import HierarchicalCompanyRunner
from src.sandbox_env import AgentWorkspace

class TestSelfRepair(unittest.TestCase):
    def setUp(self):
        self.workspace_dir = "/tmp/test_self_repair_ws"
        os.makedirs(self.workspace_dir, exist_ok=True)
        self.workspace = AgentWorkspace(company_id="test_repair_firm", base_dir=self.workspace_dir)

        # Minimal genome
        self.ceo = AgentGenome(role="CEO", goal="Lead", backstory="Executive", temperature=0.5, model_tier="executive")
        self.specialist = AgentGenome(role="Staff Systems Engineer", goal="Fix code", backstory="DevOps expert", temperature=0.2, model_tier="worker")
        self.dept = DepartmentGenome(
            dept_id="dept_systems_eng",
            name="Systems Engineering",
            mandate="Build systems",
            manager=self.specialist,
            agents=[self.specialist]
        )
        self.genome = CompanyGenome(
            company_id="test_repair_firm",
            generation=8,
            parent_ids=[],
            mutation_history=[],
            ceo=self.ceo,
            departments=[self.dept]
        )

    def test_self_repair_flow(self):
        """Simulate a broken test that is repaired by the specialist agent."""
        # 1. Write an initial failing test in the workspace
        self.workspace.write_file("src/math_mod.py", "def add(a, b):\n    return a - b  # Bug!\n")
        self.workspace.write_file("tests/test_math.py", (
            "import unittest\n"
            "from src.math_mod import add\n"
            "class TestMath(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n"
        ))

        # Initial test execution fails
        res1 = self.workspace.execute_bash("python3 -m unittest discover -s tests/ -p 'test_*.py'")
        self.assertNotEqual(res1.get("exit_code"), 0)
        self.assertIn("AssertionError", res1.get("stderr", "") + res1.get("stdout", ""))

        # 2. Simulate repair agent patching the file
        patch_content = "def add(a, b):\n    return a + b  # Fixed!\n"
        self.workspace.write_file("src/math_mod.py", patch_content)

        # 3. Re-execution passes cleanly
        res2 = self.workspace.execute_bash("python3 -m unittest discover -s tests/ -p 'test_*.py'")
        self.assertEqual(res2.get("exit_code"), 0)

if __name__ == "__main__":
    unittest.main()
