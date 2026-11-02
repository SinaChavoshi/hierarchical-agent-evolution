"""Unit tests for V18 Collaborative Sub-Blackboards, Compound Turns, Pre-Edit Huddle, Synthesis Roundtable, and Candidate-Pool Ranker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Tuple

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import EpistemicState
from hae.genome.schema import CEOPolicyGene, RoleAllele
from hae.swebench.agentic import (
    PatchCheckpoint,
    _strip_rfl_artifacts,
    parse_agent_action,
)
from hae.swebench.executor import LocalExecutor
from hae.swebench.hierarchical_mcts import (
    CausalBranchSpec,
    HierarchicalAgenticMCTSEngine,
    ReconBrief,
    VerificationContract,
)
from hae.swebench.org_binding import (
    COLLAB_SPACE,
    SUB_BLACKBOARDS,
    V18_COLLAB_REDTEAM,
    HarnessOrg,
    infer_blackboard_channel,
    resolve_redteam,
    score_candidate_patch,
)
from hae.swebench.task import SweTask

LAYERS = ("recon", "architect", "executor", "refiner", "falsifier")


def make_library() -> List[RoleAllele]:
    roles = [RoleAllele(name=f"G-{l}", layer=l, is_generalist=True) for l in LAYERS]
    roles += [
        RoleAllele(
            name="Boundary Falsifier",
            layer="falsifier",
            goal="Reject partial fixes.",
            playbook=["Run reproduce_issue.py.", "Probe boundary values.", "Run pytest on module tests."],
            failure_modes=["false_pass"],
            domain_tags=["negative"],
        ),
        RoleAllele(
            name="Sibling Parity Auditor",
            layer="falsifier",
            goal="Check sibling classes.",
            playbook=["Inspect sibling methods.", "Run existing test suite.", "Emit FAIL on mismatch."],
            failure_modes=["regression_in_sibling"],
            domain_tags=["negative"],
        ),
    ]
    return roles


class _Runner:
    def __init__(self, root: str) -> None:
        self.root = root
        self.model_name = "m"

    def _may_call(self, reason: str = "") -> bool:
        return True

    def _account_tokens(self, **kwargs: Any) -> None:
        pass

    def objective_text(self) -> str:
        return "PROBLEM STATEMENT: `add_items(a, b)` fails when `b` is None."

    def repo_map(self) -> List[str]:
        return ["pkg/calc.py", "tests/test_calc.py"]


class V18CompoundTurnAndParserTests(unittest.TestCase):
    """Tests for V18 substrate fixes in agentic.py."""

    def test_strip_rfl_artifacts_removes_embedded_tags(self) -> None:
        raw = (
            "def f(x):\n"
            "    return x + 1\n"
            "</rfl:parameter>\n"
            "</rfl:function>\n"
            "</rfl:tool_call>"
        )
        cleaned = _strip_rfl_artifacts(raw)
        self.assertEqual(cleaned, "def f(x):\n    return x + 1")

    def test_compound_multiple_view_file_and_blackboard(self) -> None:
        text = (
            "Let's inspect three files and post our hypothesis to the blackboard.\n"
            "```blackboard\n"
            '{"channel": "recon-map", "type": "finding", "content": "Checking pkg/a.py and pkg/b.py"}\n'
            "```\n"
            "```view_file\n"
            "path: pkg/a.py\n"
            "start_line: 1\n"
            "end_line: 50\n"
            "```\n"
            "```view_file\n"
            "path: pkg/b.py\n"
            "start_line: 10\n"
            "end_line: 80\n"
            "```\n"
            "```view_file\n"
            "path: pkg/c.py\n"
            "start_line: 5\n"
            "end_line: 45\n"
            "```\n"
        )
        action = parse_agent_action(text)
        self.assertEqual(action.kind, "view_file")
        self.assertEqual(action.path, "pkg/a.py")
        self.assertEqual((action.start_line, action.end_line), (1, 50))
        self.assertEqual(
            action.extra_views,
            [("pkg/b.py", 10, 80), ("pkg/c.py", 5, 45)],
        )
        self.assertEqual(len(action.blackboard_blocks), 1)
        self.assertIn("Checking pkg/a.py", action.blackboard_blocks[0])

    def test_bash_with_extra_view_and_blackboard(self) -> None:
        text = (
            '```blackboard\n{"type": "hypothesis", "content": "Running pytest and viewing helper."}\n```\n'
            "```bash\npytest -q tests/test_mod.py\n```\n"
            "```view_file\npath: pkg/helper.py\nstart_line: 1\nend_line: 40\n```\n"
        )
        action = parse_agent_action(text)
        self.assertEqual(action.kind, "bash")
        self.assertEqual(action.command, "pytest -q tests/test_mod.py")
        self.assertEqual(action.extra_views, [("pkg/helper.py", 1, 40)])
        self.assertEqual(len(action.blackboard_blocks), 1)

    def test_standalone_blackboard_action(self) -> None:
        text = (
            "I want to record our shared finding before editing.\n"
            "```blackboard\n"
            '{"type": "finding", "content": "Producer in pkg/caller.py strips metadata when value is 0."}\n'
            "```\n"
        )
        action = parse_agent_action(text)
        self.assertEqual(action.kind, "blackboard")
        self.assertEqual(len(action.blackboard_blocks), 1)


class V18CollaborativeHarnessTests(unittest.TestCase):
    """Tests for 4 scoped sub-blackboards, fixed Q&A tool execution, Pre-Edit Builder Huddle, Synthesis Roundtable, and Candidate-Pool Ranker."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "pkg"))
        os.makedirs(os.path.join(root, "tests"))
        os.makedirs(os.path.join(root, ".hae"))
        with open(os.path.join(root, "pkg", "calc.py"), "w", encoding="utf-8") as fh:
            fh.write("def add_items(a, b):\n    return a + b\n")
        with open(os.path.join(root, "tests", "test_calc.py"), "w", encoding="utf-8") as fh:
            fh.write(
                "from pkg.calc import add_items\n\n"
                "def test_basic():\n"
                "    assert add_items(2, 3) == 5\n"
            )
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"],
            cwd=root,
            check=True,
        )
        self.root = root

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _engine(self) -> HierarchicalAgenticMCTSEngine:
        ex = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(
            instance_id="demo__calc-1",
            repo="demo/calc",
            base_commit="HEAD",
            version="1.0",
            problem_statement="`add_items(a, b)` fails when `b` is None.",
        )
        st = EpistemicState(company_id="t")
        st.add_question("Root", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(
            workspace=self.root,
            python_executable=sys.executable,
            executor=ex,
            logger=lambda _: None,
        )
        return HierarchicalAgenticMCTSEngine(
            _Runner(self.root), ex, task, st, gk, logger=lambda _: None
        )

    def test_scoped_sub_blackboards_isolation_and_char_limit(self) -> None:
        eng = self._engine()
        org = HarnessOrg(
            make_library(),
            CEOPolicyGene(),
            "static",
            ["negative"],
            logger=lambda _: None,
            redteam=V18_COLLAB_REDTEAM,
        )
        org.attach(eng)

        self.assertEqual(set(SUB_BLACKBOARDS), {"recon-map", "builders-room", "redteam-chamber", "verdicts-and-repros"})
        for k in COLLAB_SPACE:
            self.assertIn(k, V18_COLLAB_REDTEAM)

        long_finding = "F" * 1500
        e_recon = org.post_blackboard("finding", long_finding, author="recon")
        self.assertIsNotNone(e_recon)
        assert e_recon is not None
        self.assertEqual(e_recon["channel"], "recon-map")
        self.assertEqual(len(e_recon["content"]), 1500)  # 1800 char limit in V18, not 320!

        e_builder = org.post_blackboard(
            "hypothesis",
            "Secret builder hypothesis about caller normalization",
            author="executor",
            channel="builders-room",
        )
        self.assertIsNotNone(e_builder)

        e_red = org.post_blackboard(
            "critique",
            "Red-team private attack plan on float('nan')",
            author="falsifier",
            channel="redteam-chamber",
        )
        self.assertIsNotNone(e_red)

        e_verdict = org.post_blackboard(
            "failing_case",
            "Patch failed on add_items(1, None) -> TypeError",
            author="falsifier",
            channel="verdicts-and-repros",
        )
        self.assertIsNotNone(e_verdict)

        # Builder view sees #recon-map, #builders-room, #verdicts-and-repros, NOT #redteam-chamber
        builder_view = org.render_blackboard_for("L2_BranchExecutor_b1_consumer_local")
        self.assertIn("Secret builder hypothesis", builder_view)
        self.assertIn("Patch failed on add_items(1, None)", builder_view)
        self.assertNotIn("Red-team private attack plan", builder_view)

        # Falsifier view sees #recon-map, #redteam-chamber, #verdicts-and-repros, NOT builder hypothesis
        falsifier_view = org.render_blackboard_for("L1_AdversarialFalsifier")
        self.assertIn("Red-team private attack plan", falsifier_view)
        self.assertIn("Patch failed on add_items(1, None)", falsifier_view)
        self.assertNotIn("Secret builder hypothesis", falsifier_view)

        # Verify disk persistence into .hae/blackboards/<channel>.jsonl
        for ch in SUB_BLACKBOARDS:
            self.assertTrue(eng.executor.exists(f".hae/blackboards/{ch}.jsonl"))

    def test_auto_index_views_and_recon_brief_extraction(self) -> None:
        eng = self._engine()
        org = HarnessOrg(
            make_library(),
            CEOPolicyGene(),
            "static",
            ["negative"],
            logger=lambda _: None,
            redteam=V18_COLLAB_REDTEAM,
        )
        org.attach(eng)

        # View pkg/calc.py -> should auto-index into #recon-map with def add_items symbol
        obs = eng.execute_view_file_tool("pkg/calc.py", 1, 10)
        self.assertIn("def add_items", obs)
        recon_entries = [e for e in org.blackboard if e.get("channel") == "recon-map"]
        self.assertEqual(len(recon_entries), 1)
        self.assertIn("Indexed `pkg/calc.py` lines 1-10", recon_entries[0]["content"])
        self.assertIn("def add_items", recon_entries[0]["content"])

        # Edit pkg/calc.py -> should auto-index surgical edit into #builders-room
        sr_obs = eng.execute_str_replace_tool(
            "pkg/calc.py",
            "    return a + b",
            "    return a + (0 if b is None else b)",
            "L2_b1",
            1,
        )
        self.assertIn("SUCCESS", sr_obs)
        builder_entries = [e for e in org.blackboard if e.get("channel") == "builders-room"]
        self.assertEqual(len(builder_entries), 1)
        self.assertIn("Applied surgical edit to `pkg/calc.py`", builder_entries[0]["content"])

    def test_answer_pending_questions_executes_tool_and_strips_raw_fences(self) -> None:
        eng = self._engine()
        org = HarnessOrg(
            make_library(),
            CEOPolicyGene(),
            "static",
            ["negative"],
            logger=lambda _: None,
            redteam=V18_COLLAB_REDTEAM,
        )
        responses = [
            # Turn 0: Responder emits a bash block AND prose together
            "Let me run pytest first.\n```bash\npytest -q tests/test_calc.py\n```\nChecking test output.",
            # Turn 1: Responder states final factual answer after receiving bash output
            "Confirmed via `pytest -q` (1 passed): `add_items(2, 3)` returns 5 and `b=None` should default to 0.",
        ]
        eng._call_specialist_turn = lambda role, msgs, **kw: (responses.pop(0), 50)  # type: ignore[assignment]
        org.attach(eng)

        org.post_blackboard(
            "question",
            "What does `tests/test_calc.py` currently assert?",
            author="executor",
            to="recon",
        )
        answered = org.answer_pending_questions()
        self.assertEqual(answered, 1)
        ans_entries = [e for e in org.blackboard if e.get("type") == "answer"]
        self.assertEqual(len(ans_entries), 1)
        self.assertNotIn("```bash", ans_entries[0]["content"])
        self.assertIn("Confirmed via `pytest -q`", ans_entries[0]["content"])

    def test_pre_edit_builder_huddle_overrides_contract_and_branches(self) -> None:
        eng = self._engine()
        org = HarnessOrg(
            make_library(),
            CEOPolicyGene(),
            "static",
            ["negative"],
            logger=lambda _: None,
            redteam=V18_COLLAB_REDTEAM,
        )
        llm_replies = [
            # 1. Seed Architect response
            json.dumps({
                "verification_contract": {
                    "invariants": ["Old flawed invariant: raise ValueError on None"],
                    "edge_cases_to_probe": ["b is None"],
                },
                "causal_branches": [
                    {
                        "locus": "consumer_local",
                        "title": "Primary fix",
                        "target_files": ["pkg/calc.py"],
                        "mechanism_and_plan": "Original b1 plan",
                        "prior": 0.7,
                    },
                    {
                        "locus": "producer_upstream",
                        "title": "Upstream fix",
                        "target_files": ["pkg/calc.py"],
                        "mechanism_and_plan": "Original b2 plan",
                        "prior": 0.6,
                    },
                ],
            }),
            # 2. Pre-Edit Builder Huddle: Builder-A (Upstream)
            json.dumps({
                "diagnosis_critique": "L0 misread the spec: None should coerce to 0, not raise ValueError.",
                "branch1_target_files": ["pkg/calc.py"],
                "branch1_plan": "Coerce None to 0 in add_items",
            }),
            # 3. Pre-Edit Builder Huddle: Builder-B (Surgeon with Contract-Override Authority)
            json.dumps({
                "override_contract": True,
                "amended_invariants": [
                    "add_items(a, None) returns a",
                    "add_items(None, b) returns b",
                ],
                "branch1_plan": "Coerce both a and b when None in pkg/calc.py",
                "branch2_target_files": ["pkg/calc.py", "tests/test_calc.py"],
                "branch2_plan": "Normalize None inputs and add unit test in tests/test_calc.py",
            }),
        ]
        eng._call_specialist_turn = lambda role, msgs, **kw: (llm_replies.pop(0), 60)  # type: ignore[assignment]
        org.attach(eng)

        recon = ReconBrief(
            consumer_locus="pkg/calc.py:add_items",
            producer_upstream_locus="pkg/calc.py",
            inspected_files=["pkg/calc.py"],
            raw_summary="def add_items(a, b): return a + b",
        )
        eng.recon_brief = recon
        contract, branches = eng.formulate_seed_contract_and_branches(["pkg/calc.py"], recon)

        self.assertEqual(org.contract_overrides, 1)
        self.assertEqual(
            contract.invariants,
            ["add_items(a, None) returns a", "add_items(None, b) returns b"],
        )
        self.assertEqual(
            branches[0].mechanism_and_plan,
            "Coerce both a and b when None in pkg/calc.py",
        )
        self.assertEqual(
            branches[1].mechanism_and_plan,
            "Normalize None inputs and add unit test in tests/test_calc.py",
        )
        self.assertEqual(branches[1].target_files, ["pkg/calc.py", "tests/test_calc.py"])

    def test_candidate_pool_ranker_and_synthesis_roundtable(self) -> None:
        eng = self._engine()
        org = HarnessOrg(
            make_library(),
            CEOPolicyGene(),
            "static",
            ["negative"],
            logger=lambda _: None,
            redteam=V18_COLLAB_REDTEAM,
        )
        org.attach(eng)

        # Create reproduction script that checks both add_items(2, None) and add_items(None, 3)
        eng.executor.write_file(
            eng.repro_script_rel,
            "from pkg.calc import add_items\nassert add_items(2, None) == 2\nassert add_items(None, 3) == 3\n",
        )

        # Candidate b1: handles b is None, passes repro + repo_tests, 1/2 panel pass
        cp1_code = "def add_items(a, b):\n    if b is None:\n        b = 0\n    return a + b\n"
        cp1 = PatchCheckpoint(
            specialist="L2_BranchExecutor_b1_consumer_local",
            turn=2,
            diff=(
                "diff --git a/pkg/calc.py b/pkg/calc.py\n"
                "--- a/pkg/calc.py\n+++ b/pkg/calc.py\n"
                "@@ -1,2 +1,4 @@\n def add_items(a, b):\n+    if b is None:\n+        b = 0\n     return a + b\n"
            ),
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": cp1_code},
            created_files=[],
            compile_ok=True,
            static_ok=True,
            repro_ok=True,
            repo_tests_ok=True,
            audit_ok=False,
            gatekeeper_ok=False,
            new_test_failures=0,
            note="b1",
        )
        org.register_candidate_in_pool(
            node_id="b1",
            locus="consumer_local",
            cp=cp1,
            panel_results=[("fals1", True, "VERDICT: PASS", True), ("fals2", False, "VERDICT: FAIL: a=None fails", True)],
            expanded_ok=True,
        )

        # Candidate b2: handles a is None, passes repro + repo_tests, 1/2 panel pass
        cp2_code = "def add_items(a, b):\n    if a is None:\n        a = 0\n    return a + b\n"
        cp2 = PatchCheckpoint(
            specialist="L2_BranchExecutor_b2_producer_upstream",
            turn=2,
            diff=(
                "diff --git a/pkg/calc.py b/pkg/calc.py\n"
                "--- a/pkg/calc.py\n+++ b/pkg/calc.py\n"
                "@@ -1,2 +1,4 @@\n def add_items(a, b):\n+    if a is None:\n+        a = 0\n     return a + b\n"
            ),
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": cp2_code},
            created_files=[],
            compile_ok=True,
            static_ok=True,
            repro_ok=True,
            repo_tests_ok=True,
            audit_ok=False,
            gatekeeper_ok=False,
            new_test_failures=0,
            note="b2",
        )
        org.register_candidate_in_pool(
            node_id="b2",
            locus="producer_upstream",
            cp=cp2,
            panel_results=[("fals1", False, "VERDICT: FAIL: b=None fails", True), ("fals2", True, "VERDICT: PASS", True)],
            expanded_ok=True,
        )

        self.assertEqual(len(org.distinct_pool_candidates()), 2)

        # Mock specialist loop for synthesis roundtable to merge b1 and b2 into handling both a=None and b=None
        synth_code = (
            "def add_items(a, b):\n"
            "    if a is None:\n"
            "        a = 0\n"
            "    if b is None:\n"
            "        b = 0\n"
            "    return a + b\n"
        )

        def _fake_synth_loop(**kwargs: Any) -> None:
            eng.executor.write_file("pkg/calc.py", synth_code)

        eng.run_specialist_loop = _fake_synth_loop  # type: ignore[assignment]
        eng.enable_falsifier = False  # let synthesis verify via gatekeeper

        syn_entry = org.run_synthesis_roundtable(eng)
        self.assertIsNotNone(syn_entry)
        assert syn_entry is not None
        self.assertEqual(syn_entry["node_id"], "b_synth")
        self.assertTrue(syn_entry["repro_ok"])
        self.assertTrue(syn_entry["repo_tests_ok"])
        self.assertTrue(syn_entry["is_synthesis"])

        # Now dirty the worktree with a broken edit and verify apply_best_pool_candidate restores b_synth!
        eng.executor.write_file("pkg/calc.py", "def add_items(a, b):\n    return 999\n")
        winner = org.apply_best_pool_candidate(eng)
        self.assertIsNotNone(winner)
        assert winner is not None
        self.assertEqual(winner["node_id"], "b_synth")
        self.assertEqual(eng.executor.read_file("pkg/calc.py"), synth_code)


if __name__ == "__main__":
    unittest.main()
