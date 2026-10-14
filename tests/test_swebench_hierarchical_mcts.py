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
            # 1. L1-A Recon turn 1: create reproduction script
            f"```bash\nmkdir -p .hae && cat << 'EOF' > .hae/reproduce_issue.py\nfrom pkg.calc import add_items\nassert add_items(2, 0, extra=5) == 7\nEOF\n{sys.executable} .hae/reproduce_issue.py\n```",
            # 2. L1-A Recon turn 2: submit brief
            "```submit\nCONSUMER_LOCUS: pkg/calc.py:1 add_items\nPRODUCER_UPSTREAM_LOCUS: pkg/calc.py:1 arguments\nCONVENTIONS_AND_SIBLINGS: standard arithmetic\n```",
            # 3. L0 Seed Architect: JSON contract & branches
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
            # 4. Episode 1 (GEN b1): applies a partial fix `return a + (extra if extra is not None else b)`
            "```str_replace\npath: pkg/calc.py\n<<<<\ndef add_items(a, b=0, extra=None):\n    return a + b\n====\ndef add_items(a, b=0, extra=None):\n    return a + (extra if extra is not None else b)\n>>>>\n```",
            # 5. Episode 1 (GEN b1) turn 2: submit
            "```submit\nApplied initial fix.\n```",
            # 6. L1-B Falsifier on b1 turn 1: writes falsification_suite.py testing BOTH b=3 and extra=4!
            f"```bash\ncat << 'EOF' > .hae/falsification_suite.py\nfrom pkg.calc import add_items\nassert add_items(2, 3, extra=4) == 9, f'Expected 9, got {{add_items(2, 3, extra=4)}}'\nEOF\n{sys.executable} .hae/falsification_suite.py\n```",
            # 7. L1-B Falsifier on b1 turn 2: submits FAIL verdict
            "```submit\nVERDICT: FAIL: When both b=3 and extra=4 are passed, add_items returns 6 instead of 9.\n```",
            # 8. Episode 2 (CONT b1 -> b1.1): Delta-Refinement fixes `pkg/calc.py` in place!
            "```str_replace\npath: pkg/calc.py\n<<<<\ndef add_items(a, b=0, extra=None):\n    return a + (extra if extra is not None else b)\n====\ndef add_items(a, b=0, extra=None):\n    return a + b + (extra if extra is not None else 0)\n>>>>\n```",
            # 9. Episode 2 (CONT b1.1) turn 2: submit
            "```submit\nFixed combined b + extra case.\n```",
            # 10. L1-B Falsifier on b1.1 turn 1: runs falsification_suite.py and certifies PASS!
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


if __name__ == "__main__":
    unittest.main()
