"""Unit tests for V13 Hierarchical Verification Harness + Agentic AB-MCTS (`hae/swebench/hierarchical_mcts.py`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import EpistemicState
from hae.swebench.agentic import PatchCheckpoint
from hae.swebench.executor import LocalExecutor
from hae.swebench.hierarchical_mcts import (
    AgenticMCTSNode,
    CausalBranchSpec,
    HierarchicalAgenticMCTSEngine,
    HierarchicalMCTSSweEngine,
    ReconBrief,
    VerificationContract,
    compute_node_empirical_value,
    parse_seed_architect_response,
)
from hae.swebench.task import SweTask


class _DummyRunner:
    def __init__(self, root: str) -> None:
        self.root = root
        self.model_name = "nvidia/Qwen3.8-Flash-Next-NVFP4"
        self.calls = 0

    def _may_call(self, reason: str = "") -> bool:
        self.calls += 1
        return self.calls <= 50

    def _account_tokens(self, **kwargs) -> None:
        pass

    def objective_text(self) -> str:
        return "PROBLEM STATEMENT: `add_items(a, b, extra=None)` ignores `extra` when both `b` and `extra` are provided."

    def repo_map(self):
        return ["pkg/calc.py", "tests/test_calc.py"]


class TestSeedArchitectParsingAndValue(unittest.TestCase):
    def test_parse_seed_architect_json_and_fallback(self):
        recon = ReconBrief(
            consumer_locus="pkg/calc.py:10 add_items",
            producer_upstream_locus="pkg/calc.py:2 normalize_input",
            conventions_and_siblings="Uses ValueError on bad input",
            inspected_files=["pkg/calc.py"],
        )
        raw_json = json.dumps({
            "verification_contract": {
                "invariants": ["Must sum a, b, and extra when extra is not None."],
                "edge_cases_to_probe": ["extra=0", "extra=5 with b=2"],
                "deprecation_or_message_rules": "Emit DeprecationWarning if legacy=True",
                "sibling_parity_targets": "sub_items",
            },
            "causal_branches": [
                {
                    "locus": "consumer_local",
                    "title": "Fix add_items return expression",
                    "target_files": ["pkg/calc.py"],
                    "mechanism_and_plan": "Add extra to a + b when extra is not None.",
                    "prior": 0.75,
                },
                {
                    "locus": "producer_upstream",
                    "title": "Move 37: Normalize extra in normalize_input",
                    "target_files": ["pkg/calc.py"],
                    "mechanism_and_plan": "Include extra in upstream tuple.",
                    "prior": 0.65,
                },
            ],
        })
        contract, branches = parse_seed_architect_response(raw_json, ["pkg/calc.py"], recon)
        self.assertEqual(len(contract.invariants), 1)
        self.assertIn("extra", contract.invariants[0])
        self.assertEqual(len(branches), 2)
        self.assertEqual(branches[0].locus, "consumer_local")
        self.assertEqual(branches[1].locus, "producer_upstream")

        # Fallback on non-JSON
        c2, b2 = parse_seed_architect_response("not json at all", ["pkg/calc.py"], recon)
        self.assertEqual(len(b2), 3)
        self.assertEqual(b2[0].locus, "consumer_local")
        self.assertEqual(b2[1].locus, "producer_upstream")
        self.assertEqual(b2[2].locus, "codebase_symmetry")

    def test_compute_node_value_and_ranking(self):
        cp_partial = PatchCheckpoint(
            diff="diff --git a/pkg/calc.py b/pkg/calc.py\n+return a + b\n",
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": "def add_items(a, b):\n    return a + b\n"},
            gatekeeper_ok=False,
            repro_ok=True,
            audit_ok=False,
            compile_ok=True,
            static_ok=True,
            repo_tests_ok=True,
            new_test_failures=0,
            specialist="L2_b1",
            turn=1,
            note="repro passed, falsifier failed",
        )
        v_partial = compute_node_empirical_value(cp_partial, falsifier_ran=True, falsifier_ok=False)
        v_cert = compute_node_empirical_value(cp_partial, falsifier_ran=True, falsifier_ok=True)
        self.assertGreater(v_cert, v_partial)
        self.assertAlmostEqual(v_cert, 1.0, places=2)


class TestABMCTSSelectionAndEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "pkg"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "tests"), exist_ok=True)
        with open(os.path.join(self.root, "pkg", "__init__.py"), "w") as f:
            f.write("")
        with open(os.path.join(self.root, "pkg", "calc.py"), "w") as f:
            f.write(
                "def add_items(a, b=0, extra=None):\n"
                "    return a + b\n"
            )
        with open(os.path.join(self.root, "tests", "test_calc.py"), "w") as f:
            f.write(
                "from pkg.calc import add_items\n\n"
                "def test_basic():\n"
                "    assert add_items(2, 3) == 5\n"
            )
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.root, check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mcts_selection_prefers_cont_on_near_miss_and_pivots_to_gen_on_stagnation(self):
        executor = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(
            instance_id="demo__calc-1",
            repo="demo/calc",
            base_commit="HEAD",
            version="1.0",
            problem_statement="Fix add_items(a, b, extra) when both b and extra are passed.",
        )
        state = EpistemicState(company_id="test_company")
        state.add_question("Root question", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(workspace=self.root, python_executable=sys.executable, executor=executor, logger=lambda _: None)
        runner = _DummyRunner(self.root)
        engine = HierarchicalAgenticMCTSEngine(runner, executor, task, state, gk, logger=lambda m: None)
        engine.branch_queue = [
            CausalBranchSpec("consumer_local", "Local fix", ["pkg/calc.py"], "plan 1"),
            CausalBranchSpec("producer_upstream", "Move 37 fix", ["pkg/calc.py"], "plan 2"),
        ]

        # 1. Empty tree -> selects GEN (consumer_local)
        op1, target1, spec1 = engine.select_next_mcts_operation(["pkg/calc.py"])
        self.assertEqual(op1, "GEN")
        self.assertEqual(target1, "root")
        self.assertEqual(spec1.locus, "consumer_local")

        # 2. Add a near-miss node b1 (repro_ok=True, falsifier_ok=False, 0 children) -> selects CONT(b1)
        cp1 = PatchCheckpoint(
            diff="diff --git a/pkg/calc.py b/pkg/calc.py\n+pass\n",
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": "def add_items(a, b=0, extra=None):\n    return a + (extra or b)\n"},
            gatekeeper_ok=False,
            repro_ok=True,
            audit_ok=False,
            compile_ok=True,
            static_ok=True,
            repo_tests_ok=True,
            new_test_failures=0,
            specialist="L2_b1",
            turn=1,
            note="repro passed",
        )
        engine.mcts_nodes["b1"] = AgenticMCTSNode(
            node_id="b1",
            parent_id="root",
            locus="consumer_local",
            hypothesis_claim="claim",
            checkpoint=cp1,
            falsifier_ran=True,
            falsifier_ok=False,
            falsifier_critique="Failed when both b and extra are non-zero",
            value=0.72,
            alpha=3.8,
            beta=2.1,
            stagnant_steps=0,
            episode_index=1,
        )
        op2, target2, _ = engine.select_next_mcts_operation(["pkg/calc.py"])
        self.assertEqual(op2, "CONT")
        self.assertEqual(target2, "b1")

        # 3. Mark b1 as stagnant (stagnant_steps=2) -> pivots to GEN (producer_upstream / Move 37)
        engine.mcts_nodes["b1"].stagnant_steps = 2
        op3, target3, spec3 = engine.select_next_mcts_operation(["pkg/calc.py"])
        self.assertEqual(op3, "GEN")
        self.assertEqual(spec3.locus, "producer_upstream")

    def test_end_to_end_recon_gen_falsify_cont_and_certify(self):
        executor = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(
            instance_id="demo__calc-1",
            repo="demo/calc",
            base_commit="HEAD",
            version="1.0",
            problem_statement="Fix add_items(a, b=0, extra=None) so extra is added to a + b when extra is not None.",
        )
        state = EpistemicState(company_id="test_company")
        state.add_question("Root question", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(workspace=self.root, python_executable=sys.executable, executor=executor, logger=lambda _: None)
        runner = _DummyRunner(self.root)
        engine = HierarchicalAgenticMCTSEngine(runner, executor, task, state, gk, logger=lambda m: None)

        responses = [
            # 1. L0 Seed Architect: JSON contract & branches (after fast deterministic L1-A Recon)
            json.dumps({
                "verification_contract": {
                    "invariants": ["add_items(a, b, extra) must sum a + b + extra when extra is not None"],
                    "edge_cases_to_probe": ["both b and extra non-zero: add_items(2, 3, extra=4) == 9"],
                },
                "causal_branches": [
                    {
                        "locus": "consumer_local",
                        "title": "Use extra or b",
                        "target_files": ["pkg/calc.py"],
                        "mechanism_and_plan": "Try using extra or b.",
                        "prior": 0.7,
                    }
                ],
            }),
            # 2. Episode 1 (GEN b1) turn 1: create reproduction script
            f"```bash\nmkdir -p .hae && cat << 'EOF' > .hae/reproduce_issue.py\nfrom pkg.calc import add_items\nassert add_items(2, 0, extra=5) == 7\nEOF\n{sys.executable} .hae/reproduce_issue.py\n```",
            # 3. Episode 1 (GEN b1) turn 2: applies a partial fix `return a + (extra if extra is not None else b)`
            "```str_replace\npath: pkg/calc.py\n<<<<\ndef add_items(a, b=0, extra=None):\n    return a + b\n====\ndef add_items(a, b=0, extra=None):\n    return a + (extra if extra is not None else b)\n>>>>\n```",
            # 4. Episode 1 (GEN b1) turn 3: submit
            "```submit\nApplied initial fix.\n```",
            # 5. L1-B Falsifier on b1 turn 1: writes falsification_suite.py testing BOTH b=3 and extra=4!
            f"```bash\ncat << 'EOF' > .hae/falsification_suite.py\nfrom pkg.calc import add_items\nassert add_items(2, 3, extra=4) == 9, f'Expected 9, got {{add_items(2, 3, extra=4)}}'\nEOF\n{sys.executable} .hae/falsification_suite.py\n```",
            # 6. L1-B Falsifier on b1 turn 2: submits FAIL verdict
            "```submit\nVERDICT: FAIL: When both b=3 and extra=4 are passed, add_items returns 6 instead of 9.\n```",
            # 7. Episode 2 (CONT b1 -> b1.1): Delta-Refinement fixes `pkg/calc.py` in place!
            "```str_replace\npath: pkg/calc.py\n<<<<\ndef add_items(a, b=0, extra=None):\n    return a + (extra if extra is not None else b)\n====\ndef add_items(a, b=0, extra=None):\n    return a + b + (extra if extra is not None else 0)\n>>>>\n```",
            # 8. Episode 2 (CONT b1.1) turn 2: submit
            "```submit\nFixed combined b + extra case.\n```",
            # 9. L1-B Falsifier on b1.1 turn 1: runs falsification_suite.py and certifies PASS!
            "```submit\nVERDICT: PASS\n```",
        ]
        idx = {"i": 0}

        def fake_call(role_name, messages, temperature=0.2, enable_thinking=True):
            i = idx["i"]
            idx["i"] += 1
            if i < len(responses):
                return responses[i], "concise thought"
            return "```submit\nVERDICT: PASS\n```", ""

        with patch.object(engine, "_call_specialist_turn", side_effect=fake_call):
            res = engine.run(candidate_modules=["pkg/calc.py"], budget_moves=36)

        self.assertTrue(res["resolved_by_self_oracle"])
        self.assertEqual(res["stop_reason"], "resolved_by_hierarchical_ab_mcts")
        self.assertIn("b1", res["mcts_tree"])
        self.assertIn("b1.1", res["mcts_tree"])
        self.assertFalse(res["mcts_tree"]["b1"]["certified"])
        self.assertTrue(res["mcts_tree"]["b1.1"]["certified"])
        with open(os.path.join(self.root, "pkg", "calc.py")) as f:
            final_code = f.read()
        self.assertIn("return a + b + (extra if extra is not None else 0)", final_code)

    def test_zero_hardcoded_benchmark_repo_names_in_prompts(self):
        executor = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(
            instance_id="demo__calc-2",
            repo="demo/calc",
            base_commit="HEAD",
            version="1.0",
            problem_statement=(
                'Traceback (most recent call last):\n'
                '  File "/workspace/pkg/calc.py", line 2, in add_items\n'
                'TypeError: unsupported operand type(s) for +: `add_items` with parent and diffuse'
            ),
        )
        state = EpistemicState(company_id="test_company")
        state.add_question("Root question", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(workspace=self.root, python_executable=sys.executable, executor=executor, logger=lambda _: None)
        runner = _DummyRunner(self.root)
        engine = HierarchicalMCTSSweEngine(runner, executor, task, state, gk, logger=lambda m: None)

        recon = engine.run_recon_episode(["pkg/calc.py"], runner.repo_map())
        captured_prompts = [
            engine._recon_system_prompt(),
            engine._recon_user_prompt(["pkg/calc.py"], runner.repo_map()),
            recon.to_compact_prompt_block(),
        ]

        def capture_seed_turn(role_name, messages, temperature=0.2, enable_thinking=False):
            for m in messages:
                captured_prompts.append(m.get("content", ""))
            return "{}", ""

        with patch.object(engine, "_call_specialist_turn", side_effect=capture_seed_turn):
            contract, branches = engine.formulate_seed_contract_and_branches(["pkg/calc.py"], recon)

        gen_prompt = engine._build_gen_executor_prompt(branches[0])
        cp = PatchCheckpoint(
            diff="diff --git a/pkg/calc.py b/pkg/calc.py\n+    return a + b\n",
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": "def add_items(a, b=0, extra=None):\n    return a + b\n"},
            repro_ok=True,
            repo_tests_ok=True,
        )
        parent_node = AgenticMCTSNode(
            node_id="b1",
            parent_id="root",
            locus="consumer_local",
            hypothesis_claim="claim",
            checkpoint=cp,
            falsifier_critique="Edge case failed",
            value=0.7,
        )
        cont_prompt = engine._build_cont_executor_prompt(parent_node)
        falsifier_sys = engine._falsifier_system_prompt()
        falsifier_user = engine._falsifier_user_prompt(cp)
        captured_prompts.extend([gen_prompt, cont_prompt, falsifier_sys, falsifier_user])

        forbidden = ("sqlfluff", "pydicom", "pyvista", "marshmallow", "astroid")
        combined_prompts = "\n".join(captured_prompts).lower()
        for repo_name in forbidden:
            self.assertNotIn(repo_name, combined_prompts, f"Found hardcoded repo hint {repo_name!r} in prompt!")

    def test_allow_new_files_checkpoint_and_switch_across_gen_and_cont(self):
        executor = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(
            instance_id="demo__greenfield-1",
            repo="demo/calc",
            base_commit="HEAD",
            version="1.0",
            problem_statement="Create pkg/multiplier.py with multiply(a, b).",
        )
        state = EpistemicState(company_id="test_company")
        state.add_question("Root question", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(workspace=self.root, python_executable=sys.executable, executor=executor, logger=lambda _: None)
        runner = _DummyRunner(self.root)
        engine = HierarchicalMCTSSweEngine(
            runner,
            executor,
            task,
            state,
            gk,
            logger=lambda m: None,
            allow_new_files=True,
        )

        # Branch b1 creates pkg/multiplier.py and tests/test_multiplier.py
        engine.execute_write_file_tool("pkg/multiplier.py", "def multiply(a, b):\n    return a * b\n")
        engine.execute_write_file_tool(
            "tests/test_multiplier.py",
            "from pkg.multiplier import multiply\n\ndef test_mul():\n    assert multiply(3, 4) == 12\n",
        )
        diff_b1, changed_b1, contents_b1 = engine._current_diff_and_files()
        self.assertIn("pkg/multiplier.py", changed_b1)
        self.assertIn("tests/test_multiplier.py", changed_b1)
        self.assertIn("def multiply(a, b):", diff_b1)
        cp_b1 = engine.checkpoints[-1]
        self.assertIn("pkg/multiplier.py", cp_b1.created_files)
        self.assertIn("tests/test_multiplier.py", cp_b1.created_files)

        # Switching to a new GEN branch (b2) via _reset_tracked_to_head cleans b1's created files
        engine._reset_tracked_to_head()
        self.assertFalse(executor.exists("pkg/multiplier.py"))
        self.assertFalse(executor.exists("tests/test_multiplier.py"))

        # Branch b2 creates a different file pkg/divider.py
        engine.execute_write_file_tool("pkg/divider.py", "def divide(a, b):\n    return a / b\n")
        self.assertTrue(executor.exists("pkg/divider.py"))

        # Switching back to CONT(b1) via _restore_checkpoint(cp_b1) removes pkg/divider.py and restores b1's files!
        engine._restore_checkpoint(cp_b1)
        engine._clean_untracked_outside_scratch()
        self.assertFalse(executor.exists("pkg/divider.py"))
        self.assertTrue(executor.exists("pkg/multiplier.py"))
        self.assertTrue(executor.exists("tests/test_multiplier.py"))
        diff_restored, changed_restored, _ = engine._current_diff_and_files()
        self.assertIn("pkg/multiplier.py", changed_restored)
        self.assertIn("def multiply(a, b):", diff_restored)

    def test_diff_bloat_threshold_and_custom_test_cmd(self):
        # 1. Verify diff_bloat_threshold regularization
        large_diff = "diff --git a/pkg/calc.py b/pkg/calc.py\n" + "\n".join(f"+line_{i} = {i}" for i in range(320))
        cp_large = PatchCheckpoint(
            diff=large_diff,
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": "x = 1\n"},
            gatekeeper_ok=True,
            repro_ok=True,
            audit_ok=True,
            compile_ok=True,
            static_ok=True,
            repo_tests_ok=True,
            new_test_failures=0,
        )
        v_default_120 = compute_node_empirical_value(cp_large, falsifier_ran=True, falsifier_ok=True, diff_bloat_threshold=120)
        v_milestone_500 = compute_node_empirical_value(cp_large, falsifier_ran=True, falsifier_ok=True, diff_bloat_threshold=500)
        self.assertLess(v_default_120, 0.95)
        self.assertAlmostEqual(v_milestone_500, 1.0, places=3)

        # 2. Verify custom_test_cmd execution in verify_current_workspace and _discover_targeted_test_files
        executor = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(
            instance_id="demo__custom-cmd-1",
            repo="demo/calc",
            base_commit="HEAD",
            version="1.0",
            problem_statement="Support extra parameter in add_items.",
        )
        state = EpistemicState(company_id="test_company")
        state.add_question("Root question", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(workspace=self.root, python_executable=sys.executable, executor=executor, logger=lambda _: None)
        runner = _DummyRunner(self.root)
        custom_cmd = (
            f"{sys.executable} -c 'from pkg.calc import add_items; assert add_items(2, 3, extra=4) == 9' "
            f"&& {sys.executable} -c 'import tests.test_calc as tc; tc.test_basic()'"
        )
        engine = HierarchicalMCTSSweEngine(
            runner,
            executor,
            task,
            state,
            gk,
            logger=lambda m: None,
            diff_bloat_threshold=500,
            custom_test_cmd=custom_cmd,
        )

        # Apply incomplete edit -> custom_test_cmd fails
        engine.execute_str_replace_tool(
            "pkg/calc.py",
            "def add_items(a, b=0, extra=None):\n    return a + b",
            "def add_items(a, b=0, extra=None):\n    return a + b + 0",
            "L2_test",
            1,
        )
        ok_bad, msg_bad, cp_bad = engine.verify_current_workspace("L2_test", 1)
        self.assertFalse(ok_bad)
        self.assertIn("Custom test command", msg_bad)
        self.assertIn("FAILED", msg_bad)

        # Apply passing edit -> custom_test_cmd passes
        engine.execute_str_replace_tool(
            "pkg/calc.py",
            "def add_items(a, b=0, extra=None):\n    return a + b + 0",
            "def add_items(a, b=0, extra=None):\n    return a + b + (extra if extra is not None else 0)",
            "L2_test",
            2,
        )
        ok_good, msg_good, cp_good = engine.verify_current_workspace("L2_test", 2)
        self.assertTrue(ok_good)
        self.assertTrue(cp_good.repro_ok)
        self.assertTrue(cp_good.repo_tests_ok)
        self.assertIn("PASSED", msg_good)
        tfiles = engine._discover_targeted_test_files(cp_good)
        self.assertIn("tests/test_calc.py", tfiles)

    def test_ablation_switches_and_zero_hints_enforcement(self):
        executor = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(
            instance_id="demo__ablation-1",
            repo="demo/calc",
            base_commit="HEAD",
            version="1.0",
            problem_statement="Support extra parameter in add_items.",
            hints_text="SECRET_HINT_FROM_ISSUE_DISCUSSION_SHOULD_BE_EXCLUDED",
        )
        state = EpistemicState(company_id="test_company")
        state.add_question("Root question", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(workspace=self.root, python_executable=sys.executable, executor=executor, logger=lambda _: None)
        runner = _DummyRunner(self.root)
        runner.include_hints = False

        # 1. Verify _task_text excludes hints_text when runner.include_hints is False
        engine_no_cont = HierarchicalMCTSSweEngine(
            runner,
            executor,
            task,
            state,
            gk,
            logger=lambda m: None,
            enable_recon=False,
            enable_falsifier=False,
            enable_cont_refinement=False,
        )
        self.assertNotIn("SECRET_HINT_FROM_ISSUE_DISCUSSION_SHOULD_BE_EXCLUDED", engine_no_cont._task_text())
        runner.include_hints = True
        self.assertIn("SECRET_HINT_FROM_ISSUE_DISCUSSION_SHOULD_BE_EXCLUDED", engine_no_cont._task_text())
        runner.include_hints = False

        # 2. Verify enable_cont_refinement=False forces GEN even when a near-miss repro_ok=True node exists
        cp_near = PatchCheckpoint(
            diff="diff --git a/pkg/calc.py b/pkg/calc.py\n+x = 1\n",
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": "x = 1\n"},
            compile_ok=True,
            static_ok=True,
            repro_ok=True,
            repo_tests_ok=False,
        )
        engine_no_cont.mcts_nodes["b1"] = AgenticMCTSNode(
            node_id="b1",
            parent_id="root",
            locus="consumer_local",
            hypothesis_claim="near miss",
            checkpoint=cp_near,
            value=0.70,
            alpha=3.8,
            beta=2.2,
            stagnant_steps=0,
        )
        op_kind, target_nid, branch_spec = engine_no_cont.select_next_mcts_operation(["pkg/calc.py"])
        self.assertEqual(op_kind, "GEN")
        self.assertEqual(target_nid, "root")
        self.assertIsNotNone(branch_spec)

    def test_domain_general_harness_improvements(self):
        executor = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(
            instance_id="demo__domain-general-1",
            repo="demo/calc",
            base_commit="HEAD",
            version="1.0",
            problem_statement="Running with `--verbose-calc` fails because `add_items(2, 3, extra=4)` ignores `extra`.",
        )
        state = EpistemicState(company_id="test_company")
        state.add_question("Root question", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(workspace=self.root, python_executable=sys.executable, executor=executor, logger=lambda _: None)
        runner = _DummyRunner(self.root)
        engine = HierarchicalMCTSSweEngine(runner, executor, task, state, gk, logger=lambda m: None)

        # 1. Rank key prioritizes repro_ok=True over repo_tests_ok=True with repro_ok=False
        cp_repro = PatchCheckpoint(
            diff="diff",
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": "x=1\n"},
            repro_ok=True,
            repo_tests_ok=False,
            compile_ok=True,
            static_ok=True,
        )
        cp_no_repro = PatchCheckpoint(
            diff="diff",
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": "x=1\n"},
            repro_ok=False,
            repo_tests_ok=True,
            compile_ok=True,
            static_ok=True,
        )
        self.assertGreater(cp_repro.rank_key(), cp_no_repro.rank_key())

        # 2. Clean-HEAD non-triviality check rejects trivial reproduce_issue.py that passes on HEAD
        executor.write_file(".hae/reproduce_issue.py", "print('trivial pass on HEAD')\n")
        engine.execute_str_replace_tool(
            "pkg/calc.py",
            "def add_items(a, b=0, extra=None):\n    return a + b",
            "def add_items(a, b=0, extra=None):\n    return a + b + (extra or 0)",
            "L2_test",
            1,
        )
        ok_triv, msg_triv, cp_triv = engine.verify_current_workspace("L2_test", 1)
        self.assertFalse(ok_triv)
        self.assertFalse(cp_triv.repro_ok)
        self.assertIn("exited 0 on clean head", msg_triv.lower())

        # Now write a genuine reproduce_issue.py that fails on clean HEAD and passes on the fix
        executor.write_file(
            ".hae/reproduce_issue.py",
            "from pkg.calc import add_items\nassert add_items(2, 3, extra=4) == 9\n",
        )
        ok_real, msg_real, cp_real = engine.verify_current_workspace("L2_test", 2)
        self.assertTrue(ok_real, msg_real)
        self.assertTrue(cp_real.repro_ok)

        # 3. Clean-HEAD baseline test failure diffing ignores pre-existing HEAD failure
        engine._head_test_baseline_cache["tests/test_calc.py::test_preexisting_broken"] = False
        engine._head_test_baseline_cache["tests/test_calc.py::test_newly_broken"] = True
        only_base, note, new_fails = engine._check_only_baseline_test_failures(
            ["tests/test_calc.py"],
            "FAILED tests/test_calc.py::test_preexisting_broken - ValueError\n1 failed, 5 passed in 0.12s\n",
            {},
        )
        self.assertTrue(only_base)
        self.assertEqual(new_fails, [])
        self.assertIn("pre-existing", note)

        only_base_new, _, new_fails_bad = engine._check_only_baseline_test_failures(
            ["tests/test_calc.py"],
            "FAILED tests/test_calc.py::test_newly_broken - AssertionError\n1 failed, 5 passed in 0.12s\n",
            {},
        )
        self.assertFalse(only_base_new)
        self.assertEqual(new_fails_bad, ["tests/test_calc.py::test_newly_broken"])


if __name__ == "__main__":
    unittest.main()
