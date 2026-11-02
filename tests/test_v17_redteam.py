"""V17 unit tests: red-team foundation, high-budget knobs, verdict labelling, fitness, and evolution."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import EpistemicState
from hae.evolution import org_evolution as orgevo
from hae.evolution.evolve import EvolutionConfig, EvolutionRun
from hae.evolution.genome import HarnessGenome, TaskResult
from hae.genome.schema import CEOPolicyGene, RoleAllele
from hae.swebench.agentic import PatchCheckpoint
from hae.swebench.executor import LocalExecutor
from hae.swebench.harness_knobs import V17_HIGH_BUDGET_KNOBS, resolve_knobs
from hae.swebench.hierarchical_mcts import (
    AgenticMCTSNode,
    HierarchicalAgenticMCTSEngine,
    ReconBrief,
)
from hae.swebench.org_binding import (
    REDTEAM_SPACE,
    V17_DEFAULT_REDTEAM,
    V17_STRICT_REDTEAM,
    HarnessOrg,
    crossover_redteam,
    mutate_redteam,
    resolve_redteam,
    sample_redteam,
)
from hae.swebench.task import SweTask

LAYERS = ("recon", "architect", "executor", "refiner", "falsifier")


def make_library():
    roles = [RoleAllele(name=f"G-{l}", layer=l, is_generalist=True).to_dict() for l in LAYERS]
    roles += [
        RoleAllele(
            name="Boundary Falsifier",
            layer="falsifier",
            goal="Reject partial fixes.",
            playbook=["Run reproduce_issue.py.", "Probe boundary values.", "Run pytest on module tests."],
            failure_modes=["false_pass"],
            domain_tags=["raretagxyz"],
        ).to_dict(),
        RoleAllele(
            name="Sibling Parity Auditor",
            layer="falsifier",
            goal="Check sibling classes.",
            playbook=["Inspect sibling methods.", "Run existing test suite.", "Emit FAIL on mismatch."],
            failure_modes=["regression_in_sibling"],
            domain_tags=["anotherraretag"],
        ).to_dict(),
    ]
    return roles


class _Runner:
    def __init__(self, root: str):
        self.root = root
        self.model_name = "m"

    def _may_call(self, reason: str = "") -> bool:
        return True

    def _account_tokens(self, **kwargs):
        pass

    def objective_text(self) -> str:
        return "PROBLEM STATEMENT: `add_items(a, b)` fails when `b` is negative."

    def repo_map(self):
        return ["pkg/calc.py", "tests/test_calc.py"]


class V17KnobAndGeneTests(unittest.TestCase):
    def test_high_budget_knobs_resolve_unclipped(self):
        k = resolve_knobs(V17_HIGH_BUDGET_KNOBS)
        self.assertEqual(k["min_budget_moves"], 250)
        self.assertEqual(k["max_episodes"], 12)
        self.assertEqual(k["gen1_turns_max"], 40)
        self.assertEqual(k["falsifier_max_turns"], 12)

    def test_redteam_space_clip_mutate_crossover(self):
        r = resolve_redteam({"panel_size": 99, "max_turns": 2, "no_edit": "true"})
        self.assertEqual(r["panel_size"], 3)
        self.assertEqual(r["max_turns"], 6)
        self.assertTrue(r["no_edit"])
        rng = random.Random(7)
        for _ in range(30):
            s = sample_redteam(rng)
            m, note = mutate_redteam(s, rng, n_genes=2)
            c = crossover_redteam(s, m, rng)
            self.assertIn("redteam_mutate", note)
            for name, spec in REDTEAM_SPACE.items():
                if spec.kind != "bool":
                    self.assertGreaterEqual(c[name], spec.low)
                    self.assertLessEqual(c[name], spec.high)


class V17RedTeamHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "pkg"))
        os.makedirs(os.path.join(root, ".hae"))
        with open(os.path.join(root, "pkg", "calc.py"), "w") as fh:
            fh.write("def add_items(a, b):\n    return a + b\n")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"], cwd=root, check=True)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def _engine(self):
        ex = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(instance_id="demo__calc-1", repo="demo/calc", base_commit="HEAD", version="1.0",
                       problem_statement="`add_items` fails on negative input.")
        st = EpistemicState(company_id="t")
        st.add_question("Root", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(workspace=self.root, python_executable=sys.executable, executor=ex, logger=lambda _: None)
        return HierarchicalAgenticMCTSEngine(_Runner(self.root), ex, task, st, gk, logger=lambda _: None)

    def test_ucb_picks_falsifier_without_keyword_overlap(self):
        lib = [RoleAllele.from_dict(r) for r in make_library()]
        # Give Boundary Falsifier higher measured quality than generalist, with zero tag overlap on ["negative", "input"]
        for r in lib:
            if r.is_generalist and r.layer == "falsifier":
                r.uses, r.mean_delta_u = 10, 0.05
            elif r.name == "Boundary Falsifier":
                r.uses, r.mean_delta_u = 10, 0.25
            elif r.name == "Sibling Parity Auditor":
                r.uses, r.mean_delta_u = 10, 0.10
        org = HarnessOrg(lib, CEOPolicyGene(), "static", ["negative", "input"],
                         logger=lambda _: None, redteam=V17_DEFAULT_REDTEAM)
        self.assertEqual(org.team["falsifier"].name, "Boundary Falsifier")

    def test_no_edit_and_lock_repro_test(self):
        eng = self._engine()
        lib = [RoleAllele.from_dict(r) for r in make_library()]
        org = HarnessOrg(lib, CEOPolicyGene(), "static", ["negative"],
                         logger=lambda _: None, redteam=V17_STRICT_REDTEAM)
        org.attach(eng)

        # Lock reproduction test
        repro_rel = eng.repro_script_rel
        orig_repro = "import sys\nsys.exit(1)\n"
        eng.executor.write_file(repro_rel, orig_repro)
        org._locked_repro = orig_repro

        # Attempt by builder to weaken reproduce_issue.py via str_replace or write_file is refused
        obs_sr = eng.execute_str_replace_tool(repro_rel, "sys.exit(1)", "sys.exit(0)", "L2_b1", 1)
        self.assertIn("refused by V17 lock_repro_test", obs_sr)
        obs_wf = eng.execute_write_file_tool(repro_rel, "print('ok')\n")
        self.assertIn("refused by V17 lock_repro_test", obs_wf)
        self.assertEqual(eng.executor.read_file(repro_rel), orig_repro)

        # If overwritten via bash, verify_current_workspace restores the locked script before checking
        eng.executor.write_file(repro_rel, "print('cheated')\n")
        eng.verify_current_workspace("L2_b1", 1)
        self.assertEqual(eng.executor.read_file(repro_rel), orig_repro)
        self.assertGreaterEqual(org.repro_restorations, 3)

        # During falsifier, editing pkg/calc.py is refused, but .hae/falsification_suite.py is allowed
        org._in_falsifier = True
        refused = eng.execute_str_replace_tool("pkg/calc.py", "return a + b", "return a - b", "L1_AdversarialFalsifier", 1)
        self.assertIn("refused by V17 Red-Team no_edit", refused)
        allowed = eng.execute_write_file_tool(".hae/falsification_suite.py", "assert 1 == 1\n", run_after=False)
        self.assertNotIn("refused", allowed)
        org._in_falsifier = False

    def test_skeptic_default_and_panel_size(self):
        eng = self._engine()
        lib = [RoleAllele.from_dict(r) for r in make_library()]
        org = HarnessOrg(lib, CEOPolicyGene(), "static", ["negative"],
                         logger=lambda _: None, redteam={**V17_STRICT_REDTEAM, "panel_size": 2, "max_turns": 6})
        org.attach(eng)

        cp = PatchCheckpoint(
            diff="--- a/pkg/calc.py\n+++ b/pkg/calc.py\n@@ -1,2 +1,2 @@\n def add_items(a, b):\n-    return a + b\n+    return a + b + 0\n",
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": "def add_items(a, b):\n    return a + b + 0\n"},
            compile_ok=True, static_ok=True, repro_ok=True, repo_tests_ok=True, gatekeeper_ok=True,
        )

        # Case 1: Falsifier submits VERDICT: PASS without running bash or creating falsification_suite -> rejected by skeptic_default
        eng2 = self._engine()
        call_seq = iter([
            ("```submit\nVERDICT: PASS\n```", ""),
        ])
        eng2._call_specialist_turn = lambda role, msgs, **kw: next(call_seq)
        org2 = HarnessOrg(lib, CEOPolicyGene(), "static", ["negative"],
                          logger=lambda _: None, redteam={**V17_STRICT_REDTEAM, "panel_size": 2, "max_turns": 6})
        org2.attach(eng2)
        ok1, msg1 = eng2.run_falsifier_episode(cp)
        self.assertFalse(ok1)
        self.assertIn("skeptic_default", msg1)
        self.assertEqual(len(org2.verdicts), 1)
        self.assertEqual(len(org2.verdicts[0]["panel"]), 1)  # stopped after panelist 1 failed

        # Case 2: Panelist 1 runs bash + VERDICT: PASS, Panelist 2 runs bash + VERDICT: FAIL -> overall FAIL
        eng3 = self._engine()
        call_seq3 = iter([
            ("```bash\npython3 -c 'print(1)'\n```", ""),
            ("```submit\nVERDICT: PASS\n```", ""),
            ("```bash\npython3 -c 'print(2)'\n```", ""),
            ("```submit\nVERDICT: FAIL: boundary case b=-1 fails\n```", ""),
        ])
        eng3._call_specialist_turn = lambda role, msgs, **kw: next(call_seq3)
        org3 = HarnessOrg(lib, CEOPolicyGene(), "static", ["negative"],
                          logger=lambda _: None, redteam={**V17_STRICT_REDTEAM, "panel_size": 2, "max_turns": 6})
        org3.attach(eng3)
        ok2, msg2 = eng3.run_falsifier_episode(cp)
        self.assertFalse(ok2)
        self.assertEqual(len(org3.verdicts), 1)
        self.assertEqual([p["passed"] for p in org3.verdicts[0]["panel"]], [True, False])

        # Case 3: Both panelists run bash + VERDICT: PASS -> overall PASS
        eng4 = self._engine()
        call_seq4 = iter([
            ("```bash\npython3 -c 'print(1)'\n```", ""),
            ("```submit\nVERDICT: PASS\n```", ""),
            ("```bash\npython3 -c 'print(2)'\n```", ""),
            ("```submit\nVERDICT: PASS\n```", ""),
        ])
        eng4._call_specialist_turn = lambda role, msgs, **kw: next(call_seq4)
        org4 = HarnessOrg(lib, CEOPolicyGene(), "static", ["negative"],
                          logger=lambda _: None, redteam={**V17_STRICT_REDTEAM, "panel_size": 2, "max_turns": 6})
        org4.attach(eng4)
        ok3, _ = eng4.run_falsifier_episode(cp)
        self.assertTrue(ok3)
        self.assertEqual([p["passed"] for p in org4.verdicts[0]["panel"]], [True, True])

    def test_continuous_build_prefers_cont(self):
        eng = self._engine()
        lib = [RoleAllele.from_dict(r) for r in make_library()]
        org = HarnessOrg(lib, CEOPolicyGene(), "static", ["negative"],
                         logger=lambda _: None, redteam={**V17_DEFAULT_REDTEAM, "continuous_build": True})
        org.attach(eng)
        cp = PatchCheckpoint(diff="+x\n", changed_files=["pkg/calc.py"], file_contents={}, compile_ok=True)
        eng.mcts_nodes["b1"] = AgenticMCTSNode(
            node_id="b1", parent_id="root", locus="consumer_local",
            hypothesis_claim="c", checkpoint=cp, value=0.4, episode_index=1,
        )
        op, target, _ = eng.select_next_mcts_operation(["pkg/calc.py"])
        self.assertEqual((op, target), ("CONT", "b1"))

    def test_blackboard_dedup_falsifier_independence_and_questions(self):
        from hae.swebench.org_binding import V17_BLACKBOARD_QUESTIONS

        eng = self._engine()
        lib = [RoleAllele.from_dict(r) for r in make_library()]
        org = HarnessOrg(
            lib, CEOPolicyGene(), "static", ["negative"],
            logger=lambda _: None,
            redteam={**V17_BLACKBOARD_QUESTIONS, "max_questions": 1, "answer_turns": 2},
        )
        org.attach(eng)

        # Post finding, hypothesis, and decision
        e1 = org.post_blackboard("finding", "pkg/calc.py:1 add_items does not validate negative b", author="recon")
        self.assertIsNotNone(e1)
        # Duplicate finding is ignored
        self.assertIsNone(org.post_blackboard("finding", "pkg/calc.py:1 add_items does not validate negative b", author="recon"))
        org.post_blackboard("hypothesis", "Executor claims adding +0 fixes everything", author="executor")
        org.post_blackboard("decision", "Target consumer_local in pkg/calc.py", author="architect")
        org.post_blackboard("failing_case", "add_items(1, -2) -> AssertionError", author="falsifier")

        # Verify disk sync to /testbed/.hae/blackboard.jsonl
        self.assertTrue(eng.executor.exists(".hae/blackboard.jsonl"))
        disk_lines = [json.loads(line) for line in eng.executor.read_file(".hae/blackboard.jsonl").splitlines() if line.strip()]
        self.assertEqual(len(disk_lines), 4)

        # Falsifier independence: falsifier sees finding, decision, failing_case, NEVER executor hypothesis
        fals_bb = org.render_blackboard_for("L1_AdversarialFalsifier")
        self.assertIn("FINDING", fals_bb)
        self.assertIn("DECISION", fals_bb)
        self.assertIn("FAILING_CASE", fals_bb)
        self.assertNotIn("Executor claims adding +0 fixes everything", fals_bb)

        # Refiner sees everything including hypothesis and failing_case
        ref_bb = org.render_blackboard_for("L2_DeltaRefinement_b1")
        self.assertIn("Executor claims adding +0 fixes everything", ref_bb)
        self.assertIn("add_items(1, -2) -> AssertionError", ref_bb)

        # Directed question: refiner asks falsifier a question; controller wakes falsifier to answer (budget=1)
        qa_replies = iter([
            ("```bash\npython3 -c 'print(42)'\n```", ""),
            ("add_items(0, -999) raises ValueError instead of returning -999", ""),
            ("should not be called because max_questions=1", ""),
        ])
        org._orig_call_ref = lambda role, msgs, temperature=0.2, enable_thinking=True: next(qa_replies)
        new_qs = org.ingest_blackboard_text(
            '```blackboard\n{"type": "question", "to": "falsifier", "content": "Which input breaks b1?"}\n```',
            author="refiner",
        )
        self.assertEqual(len(new_qs), 1)
        answered = org.answer_pending_questions()
        self.assertEqual(answered, 1)
        self.assertEqual(org.questions_asked, 1)
        self.assertEqual(org.questions_answered, 1)

        # Second question exceeds max_questions=1 and is not dispatched
        org.ingest_blackboard_text(
            '```blackboard\n{"type": "question", "to": "recon", "content": "Second question over budget?"}\n```',
            author="refiner",
        )
        self.assertEqual(org.answer_pending_questions(), 0)
        self.assertEqual(org.questions_asked, 1)

    def test_duplicate_patch_rate_and_failing_case_sharing(self):
        from hae.swebench.org_binding import V17_BLACKBOARD_ONLY

        eng = self._engine()
        call_seq = iter([
            ("```bash\npython3 -c 'print(1)'\n```", ""),
            ("```submit\nVERDICT: FAIL: fails for b=-5\n```", ""),
            ("```bash\npython3 -c 'print(1)'\n```", ""),
            ("```submit\nVERDICT: FAIL: still fails for b=-5\n```", ""),
        ])
        eng._call_specialist_turn = lambda role, msgs, **kw: next(call_seq)
        lib = [RoleAllele.from_dict(r) for r in make_library()]
        org = HarnessOrg(
            lib, CEOPolicyGene(), "static", ["negative"],
            logger=lambda _: None,
            redteam={**V17_BLACKBOARD_ONLY, "panel_size": 1, "max_turns": 6},
        )
        org.attach(eng)
        cp = PatchCheckpoint(
            diff="--- a/pkg/calc.py\n+++ b/pkg/calc.py\n@@ -1,2 +1,2 @@\n def add_items(a, b):\n-    return a + b\n+    return a + b + 0\n",
            changed_files=["pkg/calc.py"],
            file_contents={"pkg/calc.py": "def add_items(a, b):\n    return a + b + 0\n"},
            compile_ok=True, static_ok=True, repro_ok=True, repo_tests_ok=True, gatekeeper_ok=True,
        )
        ok1, _ = eng.run_falsifier_episode(cp)
        ok2, _ = eng.run_falsifier_episode(cp)
        self.assertFalse(ok1)
        self.assertFalse(ok2)
        self.assertEqual(len(org.candidate_shas), 2)
        self.assertEqual(org.duplicate_patches, 1)
        self.assertAlmostEqual(org.duplicate_patch_rate(), 0.5)
        self.assertGreaterEqual(org.failing_cases_shared, 1)
        bb_types = [e["type"] for e in org.blackboard]
        self.assertIn("failing_case", bb_types)
        self.assertIn("ruled_out", bb_types)


class V17FitnessAndEvolutionTests(unittest.TestCase):
    def test_red_team_score_and_fitness(self):
        # Lenient genome: 4 TP, 8 FP (false pass!), 1 TN, 0 FN
        lenient = HarnessGenome(genome_id="lenient", redteam=V17_DEFAULT_REDTEAM)
        lenient.results["t1"] = TaskResult("t1", resolved=True, tokens=100_000, rt_tp=4, rt_fp=8, rt_tn=1, rt_fn=0)
        # Strict genome: 4 TP, 1 FP, 8 TN, 1 FN
        strict = HarnessGenome(genome_id="strict", redteam=V17_STRICT_REDTEAM)
        strict.results["t1"] = TaskResult("t1", resolved=True, tokens=100_000, rt_tp=4, rt_fp=1, rt_tn=8, rt_fn=1)

        self.assertGreater(strict.red_team_score(), lenient.red_team_score() + 0.25)
        self.assertGreater(
            strict.fitness(redteam_weight=0.3),
            lenient.fitness(redteam_weight=0.3) + 0.07,
        )

    def test_verdict_role_credit_and_evolve(self):
        lib = make_library()
        bf_id = next(r["role_id"] for r in lib if r["name"] == "Boundary Falsifier")
        sp_id = next(r["role_id"] for r in lib if r["name"] == "Sibling Parity Auditor")
        recs = [
            {
                "resolved": False,
                "org": {"team": {"falsifier": {"role_id": bf_id}}, "credits": {}},
                "verdicts_grade": {
                    "rt_tp": 0, "rt_fp": 1, "rt_tn": 1, "rt_fn": 0,
                    "labelled_verdicts": [
                        {
                            "ground_truth_resolved": False,
                            "falsifier_ok": False,
                            "panel": [
                                {"role_id": bf_id, "passed": True, "critique": "Looks good to me"},
                                {"role_id": sp_id, "passed": False, "critique": "Sibling method broken"},
                            ],
                        }
                    ],
                },
            }
        ]
        stats = orgevo.aggregate_role_stats(recs)
        self.assertEqual((stats[bf_id]["fp"], stats[sp_id]["tn"]), (1, 1))
        self.assertLess(stats[bf_id]["sum_delta_v"], 0.0)     # -2x penalty for False PASS
        self.assertGreater(stats[sp_id]["sum_delta_v"], 0.0)  # +1 reward for True REJECT
        rt_dig = orgevo.redteam_digest(lib, stats, recs)
        self.assertIn("False PASS (FP)=1", rt_dig)
        self.assertIn("False PASS rationale", rt_dig)

        # EvolutionRun breeds redteam_mutate and redteam_rewrite children
        class _FakeEval:
            def __init__(self, out_root):
                self.worktrees = None
                self.evals_root = os.path.join(out_root, "evals")

            def evaluate(self, genomes, tasks):
                for g in genomes:
                    for t in tasks:
                        if t not in g.results:
                            g.results[t] = TaskResult(
                                instance_id=t,
                                resolved=bool(g.redteam.get("panel_size", 1) >= 2),
                                rt_tp=2,
                                rt_fp=0 if g.redteam.get("no_edit") else 3,
                                rt_tn=3,
                                rt_fn=0,
                            )

            def org_records(self, gid, tasks=None):
                return recs

            def log_tail(self, gid, t, n_chars=6000):
                return ""

        with tempfile.TemporaryDirectory() as tmp:
            cfg = EvolutionConfig(
                mode="evolve", population=4, generations=2, promote_k=2,
                redteam_weight=0.3, p_redteam=0.9, p_org=0.1, p_code=0.0, p_knob=0.0, p_cross=0.0,
                stage1_tasks=["t1"], train_tasks=["t1", "t2"],
            )
            seed = HarnessGenome(
                genome_id="seed", org_mode="dynamic", roles=lib,
                ceo_policy=CEOPolicyGene(enabled=True).to_dict(),
                redteam=V17_DEFAULT_REDTEAM,
            )
            run = EvolutionRun(cfg, _FakeEval(tmp), tmp, seed_genome=seed, logger=lambda _: None)
            run.run()
            origins = {g.origin for g in run.genomes.values()}
            self.assertIn("redteam_mutate", origins)
            self.assertTrue(all(g.has_redteam for g in run.genomes.values()))

    def test_v17_splits_and_experiment_arms(self):
        import sys
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(repo_root, "scripts"))
        import v17_experiment
        import v17_select_train_tasks

        splits = v17_experiment.load_splits()
        train_set = set(splits["train"])
        val_set = set(splits["val"])
        test_set = set(splits["test"])
        forbid = v17_select_train_tasks.forbidden_test_ids()

        self.assertEqual(len(train_set & val_set), 0)
        self.assertEqual(len(train_set & test_set), 0)
        self.assertEqual(len(val_set & test_set), 0)
        self.assertEqual(len(train_set & forbid), 0)
        self.assertGreaterEqual(len(splits["train"]), 40)
        self.assertEqual(len(splits["stage1"]), 10)
        self.assertTrue(set(splits["stage1"]).issubset(train_set))

        a17 = v17_experiment.arm_genome("A17")
        s_arm = v17_experiment.arm_genome("S")
        b17 = v17_experiment.arm_genome("B17")
        q17 = v17_experiment.arm_genome("Q17")
        self.assertEqual(a17.knobs["min_budget_moves"], 250)
        self.assertEqual(a17.redteam["no_edit"], False)
        self.assertEqual(a17.redteam["blackboard_on"], False)
        self.assertEqual(s_arm.redteam["no_edit"], True)
        self.assertEqual(s_arm.redteam["blackboard_on"], False)
        self.assertEqual(b17.redteam["blackboard_on"], True)
        self.assertEqual(b17.redteam["max_questions"], 0)
        self.assertEqual(q17.redteam["blackboard_on"], True)
        self.assertEqual(q17.redteam["max_questions"], 3)


if __name__ == "__main__":
    unittest.main()
