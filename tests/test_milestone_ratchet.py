"""Unit and integration tests for `hae.hierarchical` (`verifier_plugin` & `milestone_ratchet`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from hae.hierarchical import (
    GitCommitRatchet,
    L0MacroPlanner,
    MacroMilestoneRatchetEngine,
    MilestoneDAG,
    MilestoneSpec,
    PytestVerifierPlugin,
    ShellCommandVerifierPlugin,
    VerificationGateResult,
)
from hae.hierarchical.milestone_ratchet import (
    _DefaultMicroRunnerAdapter,
    _RatchetWorkspaceExecutorProxy,
)
from hae.swebench.executor import LocalExecutor


def _init_git_repo(root: str) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    readme = os.path.join(root, "README.md")
    with open(readme, "w", encoding="utf-8") as fh:
        fh.write("# Mini Compiler Project\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=root, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    return sha


class TestVerifierPlugins(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        _init_git_repo(self.root)
        self.executor = LocalExecutor(root=self.root, python=sys.executable)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pytest_verifier_plugin_pass_and_failure_gates(self) -> None:
        self.executor.write_file("math_mod.py", "def double(x):\n    return x * 2\n")
        self.executor.write_file(
            "test_math_mod.py",
            "from math_mod import double\n\ndef test_double():\n    assert double(3) == 6\n",
        )
        self.executor.write_file(
            ".hae/reproduce_issue.py",
            "from math_mod import double\nassert double(5) == 10\n",
        )
        self.executor.write_file(
            ".hae/falsification_suite.py",
            "from math_mod import double\nassert double(0) == 0\nassert double(-4) == -8\n",
        )

        plugin = PytestVerifierPlugin(test_targets=["test_math_mod.py"], require_repro=True)
        res = plugin.verify(self.executor, changed_files=["math_mod.py", "test_math_mod.py"])
        self.assertTrue(res.ok)
        self.assertTrue(res.compile_ok)
        self.assertTrue(res.repro_ok)
        self.assertTrue(res.tests_ok)
        self.assertTrue(res.falsifier_ok)
        self.assertEqual(res.new_failures, 0)
        self.assertAlmostEqual(res.score or 0.0, 1.0, places=2)

        # Introduce syntax error -> compile_ok=False
        self.executor.write_file("math_mod.py", "def double(x):\n    return x *\n")
        res_syn = plugin.verify(self.executor, changed_files=["math_mod.py"])
        self.assertFalse(res_syn.ok)
        self.assertFalse(res_syn.compile_ok)
        self.assertLessEqual(res_syn.score or 1.0, 0.1)

    def test_shell_command_verifier_plugin_language_agnostic(self) -> None:
        self.executor.write_file("app.py", "print('BUILD_AND_TEST_OK')\n")
        plugin_ok = ShellCommandVerifierPlugin(
            build_cmd=f"{sys.executable} -m py_compile app.py",
            test_cmd=f"{sys.executable} app.py | grep -q BUILD_AND_TEST_OK",
            falsifier_cmd=f"{sys.executable} -c \"import app\"",
        )
        res_ok = plugin_ok.verify(self.executor, changed_files=["app.py"])
        self.assertTrue(res_ok.ok)
        self.assertAlmostEqual(res_ok.score or 0.0, 1.0, places=2)

        plugin_fail = ShellCommandVerifierPlugin(
            build_cmd=f"{sys.executable} -m py_compile app.py",
            test_cmd=f"{sys.executable} -c \"import sys; sys.exit(1)\"",
        )
        res_fail = plugin_fail.verify(self.executor, changed_files=["app.py"])
        self.assertFalse(res_fail.ok)
        self.assertTrue(res_fail.compile_ok)
        self.assertFalse(res_fail.tests_ok)
        self.assertGreaterEqual(res_fail.new_failures, 1)


class TestMilestoneDAGAndPlanner(unittest.TestCase):
    def test_topological_sort_and_dependency_resolution(self) -> None:
        m1 = MilestoneSpec(milestone_id="m1_lexer", title="Lexer", objective="Build lexer")
        m2 = MilestoneSpec(
            milestone_id="m2_ast", title="AST", objective="Build AST", depends_on=["m1_lexer"]
        )
        m3 = MilestoneSpec(
            milestone_id="m3_parser",
            title="Parser",
            objective="Build parser",
            depends_on=["m1_lexer", "m2_ast"],
        )
        dag = MilestoneDAG([m3, m1, m2], system_objective="Build mini compiler")
        order = dag.validate_dag()
        self.assertEqual(order, ["m1_lexer", "m2_ast", "m3_parser"])

        # Initially only m1_lexer is ready
        nxt = dag.next_ready_milestone()
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.milestone_id, "m1_lexer")

        # Mark m1_lexer certified -> m2_ast becomes ready
        m1.status = "certified"
        nxt2 = dag.next_ready_milestone()
        self.assertIsNotNone(nxt2)
        self.assertEqual(nxt2.milestone_id, "m2_ast")

        # Round-trip serialization
        restored = MilestoneDAG.from_dict(dag.to_dict())
        self.assertEqual(restored.validate_dag(), ["m1_lexer", "m2_ast", "m3_parser"])
        self.assertEqual(restored.milestones["m1_lexer"].status, "certified")

    def test_cycle_detection_and_missing_dependency_errors(self) -> None:
        a = MilestoneSpec(milestone_id="a", title="A", objective="A", depends_on=["b"])
        b = MilestoneSpec(milestone_id="b", title="B", objective="B", depends_on=["a"])
        cyclic_dag = MilestoneDAG([a, b])
        with self.assertRaisesRegex(ValueError, "Cycle detected"):
            cyclic_dag.validate_dag()

        missing_dep = MilestoneDAG(
            [MilestoneSpec(milestone_id="m1", title="M1", objective="M1", depends_on=["nonexistent"])]
        )
        with self.assertRaisesRegex(ValueError, "unknown milestone"):
            missing_dep.validate_dag()

    def test_l0_macro_planner_json_and_fallback(self) -> None:
        llm_json = json.dumps(
            {
                "system_objective": "Build a bytecode VM",
                "milestones": [
                    {
                        "milestone_id": "m1_opcodes",
                        "title": "Opcode Definitions",
                        "objective": "Define bytecode opcodes",
                        "depends_on": [],
                        "target_files": ["vm/opcodes.py"],
                        "acceptance_cmd": "pytest -q",
                    },
                    {
                        "milestone_id": "m2_interp",
                        "title": "Stack Interpreter",
                        "objective": "Execute bytecode instructions",
                        "depends_on": ["m1_opcodes"],
                        "target_files": ["vm/interp.py"],
                        "acceptance_cmd": "pytest -q",
                    },
                ],
            }
        )
        planner = L0MacroPlanner(llm_callable=lambda msgs: llm_json, logger=lambda _: None)
        dag = planner.plan("Build a bytecode VM")
        self.assertEqual(dag.validate_dag(), ["m1_opcodes", "m2_interp"])

        # Deterministic fallback when LLM returns non-JSON
        fallback_planner = L0MacroPlanner(llm_callable=lambda msgs: "not json", logger=lambda _: None)
        fallback_dag = fallback_planner.plan(
            "1. Tokenizer: lex tokens\n2. Parser: parse expressions\n3. Evaluator: eval AST"
        )
        self.assertEqual(len(fallback_dag.milestones), 3)
        order = fallback_dag.validate_dag()
        self.assertEqual(fallback_dag.milestones[order[1]].depends_on, [order[0]])
        self.assertEqual(fallback_dag.milestones[order[2]].depends_on, [order[1]])


class TestMacroMilestoneRatchetIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.initial_sha = _init_git_repo(self.root)
        self.executor = LocalExecutor(root=self.root, python=sys.executable)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build_two_milestone_dag(self) -> MilestoneDAG:
        m1 = MilestoneSpec(
            milestone_id="m1_tokenizer",
            title="Tokenizer Subsystem",
            objective="Create compiler/tokenizer.py and tests/test_tokenizer.py",
            depends_on=[],
            target_files=["compiler/tokenizer.py", "tests/test_tokenizer.py"],
            acceptance_cmd=f"{sys.executable} -m unittest discover -s tests -p 'test_tokenizer.py'",
            max_diff_lines=500,
        )
        m2 = MilestoneSpec(
            milestone_id="m2_parser",
            title="Parser Subsystem",
            objective="Create compiler/parser.py importing compiler.tokenizer and tests/test_parser.py",
            depends_on=["m1_tokenizer"],
            target_files=["compiler/parser.py", "tests/test_parser.py"],
            acceptance_cmd=f"{sys.executable} -m unittest discover -s tests -p 'test_parser.py'",
            max_diff_lines=500,
        )
        return MilestoneDAG([m1, m2], system_objective="Build a two-stage expression compiler")

    def test_multi_milestone_git_ratchet_bounded_delta_and_cumulative_regression(self) -> None:
        dag = self._build_two_milestone_dag()
        observed_m2_diff: Dict[str, str] = {}

        def milestone_runner(
            milestone: MilestoneSpec,
            executor: CommandExecutor,
            ratchet: GitCommitRatchet,
        ) -> dict:
            if milestone.milestone_id == "m1_tokenizer":
                executor.write_file("compiler/__init__.py", "")
                executor.write_file(
                    "compiler/tokenizer.py",
                    "def tokenize(expr: str):\n"
                    "    return [tok for tok in expr.replace('+', ' + ').split() if tok]\n",
                )
                executor.write_file(
                    "tests/test_tokenizer.py",
                    "import unittest\n"
                    "from compiler.tokenizer import tokenize\n\n"
                    "class TestTokenizer(unittest.TestCase):\n"
                    "    def test_basic(self):\n"
                    "        self.assertEqual(tokenize('1+2'), ['1', '+', '2'])\n",
                )
                executor.write_file(
                    ".hae/falsification_suite.py",
                    "from compiler.tokenizer import tokenize\n"
                    "assert tokenize('  10 + 20 ') == ['10', '+', '20']\n",
                )
                return {"resolved_by_self_oracle": True}

            if milestone.milestone_id == "m2_parser":
                # Verify M1 is already committed on disk AND git diff HEAD is initially empty
                self.assertTrue(executor.exists("compiler/tokenizer.py"))
                self.assertEqual(ratchet.current_delta_diff().strip(), "")

                executor.write_file(
                    "compiler/parser.py",
                    "from compiler.tokenizer import tokenize\n\n"
                    "def parse_and_eval(expr: str) -> int:\n"
                    "    tokens = tokenize(expr)\n"
                    "    total = int(tokens[0])\n"
                    "    idx = 1\n"
                    "    while idx < len(tokens):\n"
                    "        op = tokens[idx]\n"
                    "        val = int(tokens[idx + 1])\n"
                    "        if op == '+':\n"
                    "            total += val\n"
                    "        idx += 2\n"
                    "    return total\n",
                )
                executor.write_file(
                    "tests/test_parser.py",
                    "import unittest\n"
                    "from compiler.parser import parse_and_eval\n\n"
                    "class TestParser(unittest.TestCase):\n"
                    "    def test_eval(self):\n"
                    "        self.assertEqual(parse_and_eval('1 + 2 + 3'), 6)\n",
                )
                executor.write_file(
                    ".hae/falsification_suite.py",
                    "from compiler.parser import parse_and_eval\n"
                    "assert parse_and_eval('40+2') == 42\n",
                )
                # Inspect `git diff HEAD` during M2: must ONLY show M2's files, NOT M1's tokenizer!
                observed_m2_diff["diff"] = ratchet.current_delta_diff()
                observed_m2_diff["files"] = ",".join(ratchet.current_delta_files())
                return {"resolved_by_self_oracle": True}

            return {"resolved_by_self_oracle": False}

        engine = MacroMilestoneRatchetEngine(
            executor=self.executor,
            dag=dag,
            milestone_runner=milestone_runner,
            logger=lambda _: None,
        )
        summary = engine.run(resume=False)

        self.assertTrue(summary["completed"])
        self.assertEqual(summary["certified_milestones"], ["m1_tokenizer", "m2_parser"])
        self.assertEqual(summary["failed_milestones"], [])

        # Verify bounded delta during M2 excluded M1's already-ratcheted files
        self.assertIn("compiler/parser.py", observed_m2_diff["diff"])
        self.assertNotIn("compiler/tokenizer.py", observed_m2_diff["diff"])

        # Verify both milestones produced distinct Git commits and tags
        m1_sha = dag.milestones["m1_tokenizer"].certified_commit_sha
        m2_sha = dag.milestones["m2_parser"].certified_commit_sha
        self.assertIsNotNone(m1_sha)
        self.assertIsNotNone(m2_sha)
        self.assertNotEqual(m1_sha, self.initial_sha)
        self.assertNotEqual(m1_sha, m2_sha)
        self.assertEqual(summary["head_commit_sha"], m2_sha)

        tags = self.executor.git(["tag", "-l"]).stdout.splitlines()
        self.assertIn("hae-milestone-m1_tokenizer", tags)
        self.assertIn("hae-milestone-m2_parser", tags)

        # Verify archived falsification suites exist on disk for both milestones
        self.assertTrue(self.executor.exists(".hae/ratchet_suites/falsify_m1_tokenizer.py"))
        self.assertTrue(self.executor.exists(".hae/ratchet_suites/falsify_m2_parser.py"))
        self.assertTrue(self.executor.exists(".hae/ratchet_state.json"))

    def test_rollback_on_failed_milestone_preserves_prior_ratcheted_commits(self) -> None:
        dag = self._build_two_milestone_dag()

        def failing_m2_runner(milestone: MilestoneSpec, executor: CommandExecutor) -> dict:
            if milestone.milestone_id == "m1_tokenizer":
                executor.write_file("compiler/__init__.py", "")
                executor.write_file(
                    "compiler/tokenizer.py",
                    "def tokenize(expr: str):\n"
                    "    return [tok for tok in expr.replace('+', ' + ').split() if tok]\n",
                )
                executor.write_file(
                    "tests/test_tokenizer.py",
                    "import unittest\n"
                    "from compiler.tokenizer import tokenize\n\n"
                    "class TestTokenizer(unittest.TestCase):\n"
                    "    def test_basic(self):\n"
                    "        self.assertEqual(tokenize('1+2'), ['1', '+', '2'])\n",
                )
                executor.write_file(
                    ".hae/falsification_suite.py",
                    "from compiler.tokenizer import tokenize\n"
                    "assert tokenize('1+2') == ['1', '+', '2']\n",
                )
                return {"resolved_by_self_oracle": True}

            # M2 corrupts M1's tokenizer AND writes broken parser code
            executor.write_file("compiler/tokenizer.py", "def tokenize(expr: str):\n    return []\n")
            executor.write_file("compiler/parser.py", "def broken_parser():\n    raise RuntimeError('broken')\n")
            executor.write_file(
                "tests/test_parser.py",
                "import unittest\nclass TestParser(unittest.TestCase):\n    def test_fail(self):\n        self.fail('bad parser')\n",
            )
            return {"resolved_by_self_oracle": False}

        engine = MacroMilestoneRatchetEngine(
            executor=self.executor,
            dag=dag,
            milestone_runner=failing_m2_runner,
            logger=lambda _: None,
        )
        summary = engine.run(resume=False)

        self.assertFalse(summary["completed"])
        self.assertEqual(summary["certified_milestones"], ["m1_tokenizer"])
        self.assertEqual(summary["failed_milestones"], ["m2_parser"])

        # HEAD must still be M1's certified commit
        m1_sha = dag.milestones["m1_tokenizer"].certified_commit_sha
        self.assertEqual(summary["head_commit_sha"], m1_sha)

        # M1's tokenizer must be restored to its working state, and M2's untracked files must be wiped
        self.assertTrue(self.executor.exists("compiler/tokenizer.py"))
        self.assertIn("expr.replace", self.executor.read_file("compiler/tokenizer.py"))
        self.assertFalse(self.executor.exists("compiler/parser.py"))
        self.assertFalse(self.executor.exists("tests/test_parser.py"))

    def test_crash_recovery_resumes_directly_at_milestone_2(self) -> None:
        dag_run1 = self._build_two_milestone_dag()
        m1_calls = {"count": 0}
        m2_calls = {"count": 0}

        def crash_on_m2_runner(milestone: MilestoneSpec, executor: CommandExecutor) -> dict:
            if milestone.milestone_id == "m1_tokenizer":
                m1_calls["count"] += 1
                executor.write_file("compiler/__init__.py", "")
                executor.write_file(
                    "compiler/tokenizer.py",
                    "def tokenize(expr: str):\n"
                    "    return [tok for tok in expr.replace('+', ' + ').split() if tok]\n",
                )
                executor.write_file(
                    "tests/test_tokenizer.py",
                    "import unittest\n"
                    "from compiler.tokenizer import tokenize\n\n"
                    "class TestTokenizer(unittest.TestCase):\n"
                    "    def test_basic(self):\n"
                    "        self.assertEqual(tokenize('1+2'), ['1', '+', '2'])\n",
                )
                return {"resolved_by_self_oracle": True}
            # Simulate a mid-milestone crash during M2 after leaving partial dirty file
            executor.write_file("compiler/partial_crash.py", "PARTIAL = True\n")
            raise RuntimeError("Simulated pod eviction / process crash during M2")

        engine1 = MacroMilestoneRatchetEngine(
            executor=self.executor,
            dag=dag_run1,
            milestone_runner=crash_on_m2_runner,
            logger=lambda _: None,
        )
        summary1 = engine1.run(resume=False)
        self.assertFalse(summary1["completed"])
        self.assertEqual(summary1["certified_milestones"], ["m1_tokenizer"])
        self.assertEqual(m1_calls["count"], 1)

        # Now resume (`resume=True`) with a fresh engine instance that loads `.hae/ratchet_state.json`
        def resume_runner(milestone: MilestoneSpec, executor: CommandExecutor) -> dict:
            if milestone.milestone_id == "m1_tokenizer":
                m1_calls["count"] += 1
                raise AssertionError("m1_tokenizer was already certified and must be skipped on resume=True!")
            if milestone.milestone_id == "m2_parser":
                m2_calls["count"] += 1
                executor.write_file(
                    "compiler/parser.py",
                    "from compiler.tokenizer import tokenize\n\n"
                    "def parse_and_eval(expr: str) -> int:\n"
                    "    tokens = tokenize(expr)\n"
                    "    return sum(int(t) for t in tokens if t != '+')\n",
                )
                executor.write_file(
                    "tests/test_parser.py",
                    "import unittest\n"
                    "from compiler.parser import parse_and_eval\n\n"
                    "class TestParser(unittest.TestCase):\n"
                    "    def test_eval(self):\n"
                    "        self.assertEqual(parse_and_eval('5 + 7'), 12)\n",
                )
                return {"resolved_by_self_oracle": True}
            return {"resolved_by_self_oracle": False}

        engine2 = MacroMilestoneRatchetEngine(
            executor=self.executor,
            dag=None,  # loaded automatically from .hae/ratchet_state.json
            milestone_runner=resume_runner,
            logger=lambda _: None,
        )
        summary2 = engine2.run(resume=True)

        self.assertTrue(summary2["completed"])
        self.assertEqual(m1_calls["count"], 1)  # M1 was NOT re-executed
        self.assertEqual(m2_calls["count"], 1)  # Resumed directly at M2
        self.assertEqual(summary2["certified_milestones"], ["m1_tokenizer", "m2_parser"])
        self.assertFalse(self.executor.exists("compiler/partial_crash.py"))
        self.assertTrue(self.executor.exists("compiler/parser.py"))

    def test_workspace_proxy_pytest_shim_and_token_telemetry(self) -> None:
        proxy = _RatchetWorkspaceExecutorProxy(self.executor)
        # Writing `/testbed/.hae/reproduce_issue.py` maps to `<workspace>/.hae/reproduce_issue.py`
        proxy.write_file("/testbed/.hae/reproduce_issue.py", "assert 2 + 2 == 4\n")
        self.assertTrue(self.executor.exists(".hae/reproduce_issue.py"))

        # Creating a stray top-level `falsification_suite.py` via bash relocates it into `.hae/`
        proxy.run(["bash", "-lc", "echo 'assert 3 * 3 == 9' > falsification_suite.py"])
        self.assertFalse(self.executor.exists("falsification_suite.py"))
        self.assertTrue(self.executor.exists(".hae/falsification_suite.py"))

        # Running `pytest -q` with `import pytest`, `pytest.raises`, and `@pytest.mark.parametrize`
        proxy.write_file(
            "test_shim_features.py",
            "import pytest\n\n"
            "@pytest.mark.parametrize('x,expected', [(2, 4), (3, 9), (5, 25)])\n"
            "def test_square(x, expected):\n"
            "    assert x * x == expected\n\n"
            "def test_zero_div():\n"
            "    with pytest.raises(ZeroDivisionError):\n"
            "        _ = 1 / 0\n",
        )
        res_pt = proxy.run(["bash", "-lc", "pytest -q test_shim_features.py"])
        self.assertTrue(res_pt.ok, msg=f"stdout={res_pt.stdout} stderr={res_pt.stderr}")
        self.assertIn("4 passed", res_pt.stdout)

        # Verify `_DefaultMicroRunnerAdapter` token accounting
        spec = MilestoneSpec(
            milestone_id="m1_demo",
            title="Demo",
            objective="Build demo",
            target_files=["demo.py"],
            acceptance_cmd="pytest -q test_shim_features.py",
        )
        adapter = _DefaultMicroRunnerAdapter(proxy, spec)
        adapter._account_tokens(
            usage={"prompt_tokens": 120, "output_tokens": 45, "thought_tokens": 30, "total_tokens": 165}
        )
        adapter._account_tokens(
            usage={"prompt_tokens": 80, "output_tokens": 25, "thought_tokens": 10, "total_tokens": 105}
        )
        tok = adapter.token_summary()
        self.assertEqual(tok["llm_calls"], 2)
        self.assertEqual(tok["prompt_tokens"], 200)
        self.assertEqual(tok["output_tokens"], 70)
        self.assertEqual(tok["thought_tokens"], 40)
        self.assertEqual(tok["total_tokens"], 270)

    def test_planner_think_tag_json_extraction_and_external_heldout_recovery(self) -> None:
        # 1. Verify <think>{...}</think> with nested braces before ```json ... ``` is parsed cleanly
        raw_llm_output = (
            "<think>\n"
            "Let's decompose the calculator package: {stage_1: lexer, stage_2: eval}.\n"
            "</think>\n"
            "```json\n"
            + json.dumps(
                {
                    "system_objective": "Build calc package",
                    "milestones": [
                        {
                            "milestone_id": "m1_add",
                            "title": "Addition Module",
                            "objective": "Create calc/ops.py with add(a, b)",
                            "depends_on": [],
                            "target_files": ["calc/__init__.py", "calc/ops.py", "tests/test_m1_add.py"],
                            "acceptance_cmd": "pytest -q",
                        },
                        {
                            "milestone_id": "m2_mul",
                            "title": "Multiplication & Division Module",
                            "objective": "Add mul(a, b) and div(a, b) to calc/ops.py",
                            "depends_on": ["m1_add"],
                            "target_files": ["calc/ops.py", "tests/test_m2_mul.py"],
                            "acceptance_cmd": "pytest -q",
                        },
                    ],
                }
            )
            + "\n```"
        )
        planner = L0MacroPlanner(
            llm_callable=lambda _msgs: raw_llm_output,
            max_diff_lines=400,
            budget_moves=20,
            logger=lambda _: None,
        )

        # Held-out external test file stored OUTSIDE the repository workspace
        ext_dir = tempfile.TemporaryDirectory()
        self.addCleanup(ext_dir.cleanup)
        ext_test_path = os.path.join(ext_dir.name, "test_external_oracle.py")
        with open(ext_test_path, "w", encoding="utf-8") as fh:
            fh.write(
                "import unittest\n"
                "from calc.ops import add, mul, div\n\n"
                "class TestExternalHeldOut(unittest.TestCase):\n"
                "    def test_arith(self):\n"
                "        self.assertEqual(add(10, 5), 15)\n"
                "        self.assertEqual(mul(6, 7), 42)\n"
                "    def test_div_by_zero_raises_value_error(self):\n"
                "        with self.assertRaises(ValueError):\n"
                "            div(10, 0)\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            )

        ext_plugin = ShellCommandVerifierPlugin(
            test_cmd=f"pytest -q {ext_test_path}",
            diff_bloat_threshold=400,
        )

        def runner_with_recovery(milestone: MilestoneSpec, executor: CommandExecutor) -> dict:
            if milestone.milestone_id == "m1_add":
                executor.write_file("calc/__init__.py", "from .ops import add\n")
                executor.write_file("calc/ops.py", "def add(a, b):\n    return a + b\n")
                executor.write_file(
                    "tests/test_m1_add.py",
                    "def test_add():\n    from calc.ops import add\n    assert add(2, 3) == 5\n",
                )
                return {"resolved_by_self_oracle": True}
            if milestone.milestone_id == "m2_mul":
                # Initially forgets to raise ValueError on b == 0 in div(a, b)
                executor.write_file("calc/__init__.py", "from .ops import add, mul, div\n")
                executor.write_file(
                    "calc/ops.py",
                    "def add(a, b):\n    return a + b\n\n"
                    "def mul(a, b):\n    return a * b\n\n"
                    "def div(a, b):\n    return a / b\n",
                )
                executor.write_file(
                    "tests/test_m2_mul.py",
                    "def test_mul_div():\n    from calc.ops import mul, div\n    assert mul(3, 4) == 12\n    assert div(8, 2) == 4\n",
                )
                return {"resolved_by_self_oracle": True}
            if milestone.milestone_id.startswith("m3_external_recovery"):
                # Recovery milestone fixes div(a, b) to raise ValueError on b == 0
                executor.write_file(
                    "calc/ops.py",
                    "def add(a, b):\n    return a + b\n\n"
                    "def mul(a, b):\n    return a * b\n\n"
                    "def div(a, b):\n"
                    "    if b == 0:\n"
                    "        raise ValueError('division by zero')\n"
                    "    return a / b\n",
                )
                executor.write_file(
                    f"tests/test_{milestone.milestone_id}.py",
                    "import pytest\nfrom calc.ops import div\ndef test_div_zero():\n    with pytest.raises(ValueError):\n        div(1, 0)\n",
                )
                return {"resolved_by_self_oracle": True}
            return {"resolved_by_self_oracle": False}

        engine = MacroMilestoneRatchetEngine(
            executor=self.executor,
            dag=None,  # Planner-driven!
            objective="Build calc package",
            planner=planner,
            milestone_runner=runner_with_recovery,
            external_verifier_plugin=ext_plugin,
            max_recovery_milestones=2,
            logger=lambda _: None,
        )
        summary = engine.run(resume=False)

        self.assertTrue(summary["completed"])
        self.assertEqual(
            summary["certified_milestones"],
            ["m1_add", "m2_mul", "m3_external_recovery_1"],
        )
        ext_info = summary["external_verification"]
        self.assertFalse(ext_info["initial"]["tests_ok"])
        self.assertTrue(ext_info["final"]["tests_ok"])
        self.assertTrue(ext_info["external_verified"])
        self.assertEqual(ext_info["recovery_milestones"], ["m3_external_recovery_1"])


if __name__ == "__main__":
    unittest.main()
