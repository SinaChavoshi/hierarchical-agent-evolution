"""Unit tests for the V12 Agentic Epistemic SWE Engine (`hae.swebench.agentic`)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import EpistemicState
from hae.swebench.agentic import (
    AgenticSweEngine,
    PatchCheckpoint,
    apply_str_replace_in_memory,
    parse_agent_action,
)
from hae.swebench.executor import LocalExecutor
from hae.swebench.runner import SweBenchCompanyRunner, load_genome
from hae.swebench.task import SweTask


class TestParseAgentAction(unittest.TestCase):
    def test_parse_bash_block(self) -> None:
        resp = "Let's search for the function.\n```bash\ngit grep -n \"def dumps\"\n```"
        act = parse_agent_action(resp)
        self.assertEqual(act.kind, "bash")
        self.assertEqual(act.command, 'git grep -n "def dumps"')
        self.assertEqual(act.error, "")

    def test_parse_view_file_block(self) -> None:
        resp = "```view_file\npath: src/marshmallow/fields.py\nstart_line: 100\nend_line: 180\n```"
        act = parse_agent_action(resp)
        self.assertEqual(act.kind, "view_file")
        self.assertEqual(act.path, "src/marshmallow/fields.py")
        self.assertEqual(act.start_line, 100)
        self.assertEqual(act.end_line, 180)

    def test_parse_str_replace_block(self) -> None:
        resp = (
            "```str_replace\n"
            "path: src/marshmallow/fields.py\n"
            "<<<<\n"
            "    if value is None:\n"
            "        return None\n"
            "====\n"
            "    if value is None:\n"
            "        return self.default\n"
            ">>>>\n"
            "```"
        )
        act = parse_agent_action(resp)
        self.assertEqual(act.kind, "str_replace")
        self.assertEqual(act.path, "src/marshmallow/fields.py")
        self.assertIn("return None", act.old_str)
        self.assertIn("return self.default", act.new_str)

    def test_parse_str_replace_with_nested_docstring_backticks(self) -> None:
        resp = (
            "```str_replace\n"
            "path: pyvista/plotting/_property.py\n"
            "<<<<\n"
            "    \"\"\"Example:\n"
            "    ```python\n"
            "    prop.diffuse = 0.5\n"
            "    ```\n"
            "    \"\"\"\n"
            "    return self._diffuse\n"
            "====\n"
            "    return float(self._diffuse)\n"
            ">>>>\n"
            "```"
        )
        act = parse_agent_action(resp)
        self.assertEqual(act.kind, "str_replace")
        self.assertEqual(act.error, "")
        self.assertEqual(act.path, "pyvista/plotting/_property.py")
        self.assertIn("prop.diffuse = 0.5", act.old_str)
        self.assertIn("return float(self._diffuse)", act.new_str)

    def test_parse_submit_block(self) -> None:
        resp = "```submit\nFixed None handling in Field._deserialize.\n```"
        act = parse_agent_action(resp)
        self.assertEqual(act.kind, "submit")
        self.assertIn("Fixed None handling", act.summary)

    def test_parse_xml_tool_call_bash(self) -> None:
        resp = (
            "<tool_call>\n"
            "<function=bash>\n"
            "<parameter=command>\n"
            "cd /testbed && git grep -n \"class Nested\"\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        act = parse_agent_action(resp)
        self.assertEqual(act.kind, "bash")
        self.assertEqual(act.error, "")
        self.assertEqual(act.command, 'cd /testbed && git grep -n "class Nested"')

    def test_parse_xml_tool_call_view_file_malformed_close_tag(self) -> None:
        resp = (
            "<tool_call>\n"
            "<function=view_file\n"
            "</parameter>\n"
            "<parameter=path>\n"
            "src/marshmallow/schema.py\n"
            "</parameter>\n"
            "<parameter=start_line>\n"
            "300\n"
            "</parameter>\n"
            "<parameter=end_line>\n"
            "420\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        act = parse_agent_action(resp)
        self.assertEqual(act.kind, "view_file")
        self.assertEqual(act.path, "src/marshmallow/schema.py")
        self.assertEqual(act.start_line, 300)
        self.assertEqual(act.end_line, 420)

    def test_parse_xml_tool_call_str_replace(self) -> None:
        resp = (
            "<tool_call>\n"
            "<function=str_replace>\n"
            "<parameter=path>\n"
            "pyvista/plotting/_property.py\n"
            "</parameter>\n"
            "<parameter=old_str>\n"
            "    def diffuse(self, value: float):\n"
            "        self.SetDiffuse(value)\n"
            "</parameter>\n"
            "<parameter=new_str>\n"
            "    def diffuse(self, value: float):\n"
            "        check_range(value, (0, 1), 'diffuse')\n"
            "        self.SetDiffuse(value)\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        act = parse_agent_action(resp)
        self.assertEqual(act.kind, "str_replace")
        self.assertEqual(act.path, "pyvista/plotting/_property.py")
        self.assertTrue(act.old_str.startswith("    def diffuse"))
        self.assertIn("check_range", act.new_str)

    def test_parse_xml_tool_call_str_replace_hybrid_conflict_markers(self) -> None:
        resp1 = (
            "<tool_call>\n"
            "<function=str_replace>\n"
            "<parameter=path>\n"
            "pydicom/encaps.py\n"
            "</parameter>\n"
            "<parameter=<<<<\n"
            "def generate_pixel_data_frame(bytestream):\n"
            "    for f in generate_pixel_data(bytestream):\n"
            "        yield f\n"
            "====\n"
            "def generate_pixel_data_frame(bytestream, nr_frames=None):\n"
            "    for f in generate_pixel_data(bytestream, nr_frames):\n"
            "        yield f\n"
            ">>>>\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        act1 = parse_agent_action(resp1)
        self.assertEqual(act1.kind, "str_replace")
        self.assertEqual(act1.error, "")
        self.assertEqual(act1.path, "pydicom/encaps.py")
        self.assertIn("def generate_pixel_data_frame(bytestream):", act1.old_str)
        self.assertIn("nr_frames=None", act1.new_str)

        resp2 = (
            "<tool_call>\n"
            "<function=str_replace>\n"
            "<parameter=path>\n"
            "pydicom/encaps.py\n"
            "<<<<\n"
            "    old_line = 1\n"
            "====\n"
            "    new_line = 2\n"
            ">>>>\n"
            "</function>\n"
            "</tool_call>"
        )
        act2 = parse_agent_action(resp2)
        self.assertEqual(act2.kind, "str_replace")
        self.assertEqual(act2.error, "")
        self.assertEqual(act2.path, "pydicom/encaps.py")
        self.assertEqual(act2.old_str, "    old_line = 1")
        self.assertEqual(act2.new_str, "    new_line = 2")

    def test_parse_xml_tool_call_str_replace_old_string_new_string(self) -> None:
        resp = (
            "<tool_call>\n"
            "<function=str_replace>\n"
            "<parameter=path>\n"
            "src/marshmallow/schema.py\n"
            "</parameter>\n"
            "<parameter=old_string>\n"
            "    return True\n"
            "</parameter>\n"
            "<parameter=new_string>\n"
            "    return False\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        act = parse_agent_action(resp)
        self.assertEqual(act.kind, "str_replace")
        self.assertEqual(act.error, "")
        self.assertEqual(act.path, "src/marshmallow/schema.py")
        self.assertEqual(act.old_str, "    return True")
        self.assertEqual(act.new_str, "    return False")


class TestApplyStrReplaceInMemory(unittest.TestCase):
    def test_exact_match(self) -> None:
        src = "def f(x):\n    return x + 1\n"
        updated, line_no, note = apply_str_replace_in_memory(src, "    return x + 1", "    return x + 2")
        self.assertEqual(updated, "def f(x):\n    return x + 2\n")
        self.assertEqual(line_no, 2)
        self.assertEqual(note, "exact match")

    def test_strips_accidental_line_numbers(self) -> None:
        src = "def f(x):\n    y = x * 2\n    return y\n"
        old_with_nums = "    2 |     y = x * 2\n    3 |     return y"
        new_with_nums = "    2 |     y = x * 3\n    3 |     return y"
        updated, line_no, note = apply_str_replace_in_memory(src, old_with_nums, new_with_nums)
        self.assertIsNotNone(updated)
        self.assertIn("y = x * 3", updated or "")
        self.assertEqual(line_no, 2)

    def test_indent_adjusted_match(self) -> None:
        src = "class A:\n    def m(self, x):\n        if x is None:\n            return 0\n        return x\n"
        # Model omitted outer 4-space class indent
        old_s = "if x is None:\n    return 0"
        new_s = "if x is None:\n    return -1"
        updated, line_no, note = apply_str_replace_in_memory(src, old_s, new_s)
        self.assertIsNotNone(updated)
        self.assertIn("        if x is None:\n            return -1", updated or "")
        self.assertEqual(line_no, 3)


class TestPatchCheckpointRanking(unittest.TestCase):
    def test_checkpoint_ranking_prefers_gatekeeper_and_repro(self) -> None:
        cp_partial = PatchCheckpoint(
            diff="diff1",
            changed_files=["a.py"],
            file_contents={"a.py": "x=1\n"},
            gatekeeper_ok=False,
            repro_ok=True,
            repo_tests_ok=False,
            new_test_failures=1,
        )
        cp_verified = PatchCheckpoint(
            diff="diff2",
            changed_files=["a.py"],
            file_contents={"a.py": "x=2\n"},
            gatekeeper_ok=True,
            repro_ok=True,
            repo_tests_ok=True,
            new_test_failures=0,
        )
        self.assertGreater(cp_verified.rank_key(), cp_partial.rank_key())


class TestAgenticSweEngineEndToEnd(unittest.TestCase):
    def test_end_to_end_repro_patch_verify_and_ledger(self) -> None:
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmpdir, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, check=True)

            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w") as f:
                f.write("")
            with open(os.path.join(tmpdir, "pkg", "calc.py"), "w") as f:
                f.write("def add_positive(a, b):\n    return a - b\n")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmpdir, check=True)

            executor = LocalExecutor(root=tmpdir, python=sys.executable)
            gatekeeper = EvidenceGatekeeper(workspace=tmpdir, python_executable=sys.executable, executor=executor, logger=lambda _: None)
            state = EpistemicState(company_id="test_company")
            state.add_question("Root: add_positive bug", module="pkg/calc.py", uncertainty=0.9)

            task = SweTask(
                instance_id="test__calc-1",
                repo="test/calc",
                base_commit="HEAD",
                version="1.0",
                problem_statement="add_positive(2, 3) returns -1 instead of 5.",
            )
            genome = load_genome("configs/genomes/v12_agentic_swebench_firm_0.json")
            runner = SweBenchCompanyRunner(
                genome=genome,
                task=task,
                executor=executor,
            )

            scripted_turns = [
                (
                    f"```bash\nmkdir -p {tmpdir}/.hae && cat << 'EOF' > {tmpdir}/.hae/reproduce_issue.py\n"
                    "from pkg.calc import add_positive\nassert add_positive(2, 3) == 5\nEOF\n"
                    f"{sys.executable} {tmpdir}/.hae/reproduce_issue.py\n```",
                    "Let's first write a reproduction script that fails.",
                ),
                (
                    "<tool_call>\n<function=str_replace>\n<parameter=path>\npkg/calc.py\n</parameter>\n"
                    "<parameter=old_str>\ndef add_positive(a, b):\n    return a - b\n</parameter>\n"
                    "<parameter=new_str>\ndef add_positive(a, b):\n    return a + b\n</parameter>\n"
                    "</function>\n</tool_call>",
                    "Now apply the surgical fix via XML tool_call.",
                ),
                (
                    "```submit\nFixed add_positive to use + instead of -.\n```",
                    "Submit verified fix.",
                ),
                (
                    f"```bash\ncat << 'EOF' > {tmpdir}/.hae/audit_edge_cases.py\n"
                    "from pkg.calc import add_positive\nassert add_positive(0, 0) == 0\nassert add_positive(10, 20) == 30\nEOF\n"
                    f"{sys.executable} {tmpdir}/.hae/audit_edge_cases.py\n```",
                    "Auditor writes edge case script.",
                ),
                (
                    "```submit\nEdge cases verified.\n```",
                    "Auditor submits.",
                ),
            ]
            turn_iter = iter(scripted_turns)

            engine = AgenticSweEngine(
                runner=runner,
                task=task,
                executor=executor,
                gatekeeper=gatekeeper,
                state=state,
                logger=lambda _: None,
            )
            with patch.object(
                engine,
                "_call_specialist_turn",
                side_effect=lambda *a, **kw: next(turn_iter, ("```submit\ndone\n```", "")),
            ):
                res = engine.run(candidate_modules=["pkg/calc.py"], budget_moves=15)

            self.assertTrue(res["resolved_by_self_oracle"])
            self.assertIn("pkg/calc.py", res["synthesized_paths"])
            self.assertIn("+    return a + b", executor.diff())
            self.assertGreaterEqual(len(state.hypotheses), 1)
            self.assertGreaterEqual(len(state.evidence_log), 2)


if __name__ == "__main__":
    unittest.main()
