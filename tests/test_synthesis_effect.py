"""The post-synthesis effect check: a write that passes the module gate must still change what a probe sees.

Defect 3 of the first V9 live run (results/swebench/v9_ab_mcts_2026-10-08):
two function rewrites that only deleted docstrings and reworded messages
passed `verify_module`, were recorded as RESOLVED, entered the patch and one
of them broke a test outside the proximity selection. The fix has two parts;
this file covers the first (`EvidenceGatekeeper.check_synthesis_effect` and
how `EpistemicSearchLoop._do_synthesize` acts on its verdict). The second,
the calibrated whole-suite `repo_tests` mode, is in
tests/test_swebench_gatekeeper.py (`WholeSuiteTests`).

The fixture is the staged-mode `mypkg/calc.py` bug used by the search tests:
`right()` predicts the buggy output `ADD 0`, the real fix makes the probe
print `ADD 4`, and a cosmetic rewrite leaves it at `ADD 0`.
"""

import shutil
import tempfile
import unittest

from hae.epistemic.gatekeeper import (
    EFFECT_CHECK_COMMAND, EFFECT_EFFECTIVE, EFFECT_INCONCLUSIVE, EFFECT_INEFFECTIVE, POST_SYNTHESIS_RERUN,
    EvidenceGatekeeper,
)
from hae.epistemic.ledger import Q_OPEN, Q_RESOLVED, SUPPORTED, EpistemicState
from hae.epistemic.mcts import STOP_RESOLVED, EpistemicSearchLoop
from hae.epistemic.moves import MOVE_SYNTHESIZE, HypothesisProposal
from hae.epistemic.value import EpistemicValueFunction
from hae.genome.schema import EpistemicPolicyGene
from hae.runtime.workspace import AgentWorkspace
from tests.test_ab_mcts import MeanRng
from tests.test_epistemic_search import BUGGY, FIXED, PROBE, FakeSystem1, right, wrong

# Same behaviour as BUGGY: a docstring and a renamed local, nothing a probe can see.
COSMETIC = 'def add(a, b):\n    """Return the sum of a and b."""\n    first, second = a, b\n    return first - second\n'
# Same behaviour as FIXED: what a cosmetic rewrite after the real fix leaves behind.
FIXED_COSMETIC = 'def add(a, b):\n    """Return the sum of a and b."""\n    return a + b\n'
CRASHING = "def add(a, b):\n    raise RuntimeError('boom')\n"
# A probe whose prediction is a structural fact, true before and after any fix.
STRUCTURAL_PROBE = "import mypkg.calc\nprint('IMPORT_OK')\n"
STRUCTURAL = HypothesisProposal("the module imports", "import path", 0.3, STRUCTURAL_PROBE,
                                {"expect_exit_code": 0, "expect_stdout_contains": "IMPORT_OK"})


class ScriptedSynthesiser(FakeSystem1):
    """Writes the scripted module texts in order; records the hypothesis' evidence at each call."""

    def __init__(self, ws, rounds, writes):
        super().__init__(ws, rounds, synth_fix=True)
        self.writes = list(writes)
        self.evidence_seen = []

    def synthesize(self, question, hypothesis, state):
        self.synth_calls += 1
        self.evidence_seen.append([state.evidence_by_id(e) for e in hypothesis.evidence_ids])
        previous = self.ws.read_file("mypkg/calc.py")["content"]
        body = self.writes.pop(0) if self.writes else FIXED
        self.ws.write_file("mypkg/calc.py", body)
        return {"written": True, "path": "mypkg/calc.py", "summary": "scripted", "mode": "function",
                "previous_source": previous}


class PerQuestionSynthesiser(ScriptedSynthesiser):
    """Proposals and writes keyed by question id, for searches over more than one question."""

    def __init__(self, ws, rounds_by_question, writes_by_question):
        super().__init__(ws, rounds=[], writes=[])
        self.rounds_by_question = {k: list(v) for k, v in rounds_by_question.items()}
        self.writes_by_question = {k: list(v) for k, v in writes_by_question.items()}
        self.synth_questions = []

    def propose(self, question, state, k):
        self.propose_calls += 1
        rounds = self.rounds_by_question.get(question.question_id, [])
        return rounds.pop(0) if rounds else []

    def synthesize(self, question, hypothesis, state):
        self.synth_questions.append(question.question_id)
        self.writes = self.writes_by_question.get(question.question_id, [])
        return super().synthesize(question, hypothesis, state)


class EffectFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_effect_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = AgentWorkspace("effect_firm", base_dir=self.tmp)
        self.ws.write_file("mypkg/__init__.py", "")
        self.ws.write_file("mypkg/calc.py", BUGGY)
        self.state = EpistemicState("effect_firm")
        self.gk = EvidenceGatekeeper(self.ws, timeout_s=15, isolate=False, stage_reference=False,
                                     module_for_tag={"calc": "mypkg/calc.py"})
        self.gk.reconcile_with_oracle(
            self.state, ["[calc] FAIL: test_add (t.T.test_add) -> AssertionError: 0 != 4"], iteration=1)
        self.q = next(iter(self.state.questions.values()))

    def loop(self, sys1, root_question_id=None, **policy_kw):
        kw = dict(enabled=True, search_budget_moves=20, min_hypotheses_before_synthesis=2,
                  low_prior_quota=0.0, max_stagnant_moves=6)
        kw.update(policy_kw)
        policy = EpistemicPolicyGene(**kw)
        return EpistemicSearchLoop(self.state, self.gk, EpistemicValueFunction(0.6), policy,
                                   sys1.propose, sys1.synthesize, rng_seed=7, logger=lambda s: None,
                                   revert_patch=lambda path, src: self.ws.write_file(path, src),
                                   root_question_id=root_question_id)

    def calc(self):
        return self.ws.read_file("mypkg/calc.py")["content"]

    def synth_moves(self, res):
        return [m for m in res.trajectory if m.move_type == MOVE_SYNTHESIZE]


class LoopVerdictTests(EffectFixture):

    def test_cosmetic_rewrite_is_reverted_and_the_question_stays_open(self):
        sys1 = ScriptedSynthesiser(self.ws, rounds=[[wrong(0.8), right(0.2)]], writes=[COSMETIC, COSMETIC])
        res = self.loop(sys1, max_hypothesis_rounds=1).run()
        h = next(h for h in self.state.hypotheses.values() if h.status == SUPPORTED)
        # Two attempts (MAX_SYNTHESIS_FAILURES), both ineffective, both reverted.
        self.assertEqual(res.stats["syntheses"], 2)
        self.assertEqual(res.stats["syntheses_ineffective"], 2)
        self.assertEqual(res.stats["syntheses_reverted"], 2)
        self.assertEqual(res.stats["syntheses_verified"], 0)
        self.assertEqual(res.synthesized_paths, [])
        self.assertEqual(h.synthesis_failures, 2)
        self.assertFalse(h.patch_applied)
        self.assertEqual(self.q.status, Q_OPEN)
        self.assertNotEqual(res.stop_reason, STOP_RESOLVED)
        self.assertEqual(self.calc(), BUGGY)
        moves = self.synth_moves(res)
        self.assertEqual(len(moves), 2)
        self.assertEqual(moves[0].delta_u, 0.0)
        self.assertIn("module check passed", moves[0].note)
        self.assertIn("effect: ineffective (0/1 re-run probe(s) no longer reproduce the bug)", moves[0].note)
        self.assertIn("reverted to pre-synthesis module", moves[0].note)
        self.assertIn("(failure 1/2)", moves[0].note)
        self.assertIn("(failure 2/2)", moves[1].note)

    def test_the_next_attempt_is_prompted_with_the_verdict(self):
        sys1 = ScriptedSynthesiser(self.ws, rounds=[[wrong(0.8), right(0.2)]], writes=[COSMETIC, FIXED])
        res = self.loop(sys1).run()
        self.assertEqual(sys1.synth_calls, 2)
        # The second synthesis prompt carries, in ledger order: the passed
        # module check, the re-run that still printed ADD 0, and the summary.
        seen = [e for e in sys1.evidence_seen[1] if e is not None]
        commands = [e.command for e in seen]
        self.assertEqual(commands[-3][:13], "verify_module")
        self.assertTrue(commands[-2].startswith(POST_SYNTHESIS_RERUN + ": "), commands)
        self.assertEqual(commands[-1], EFFECT_CHECK_COMMAND)
        self.assertTrue(seen[-2].matched_prediction)        # ADD 0 still there
        self.assertIn("ADD 0", seen[-2].stdout)
        self.assertFalse(seen[-1].matched_prediction)
        self.assertIn("ineffective", seen[-1].detail)
        # And the real fix then resolves the question as before.
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        self.assertEqual(self.q.status, Q_RESOLVED)
        self.assertEqual(self.calc(), FIXED)
        self.assertEqual(res.stats["syntheses_ineffective"], 1)
        self.assertEqual(res.stats["syntheses_verified"], 1)
        self.assertEqual(res.synthesized_paths, ["mypkg/calc.py"])
        self.assertIn("effect: effective (1/1 re-run probe(s) no longer reproduce the bug)",
                      self.synth_moves(res)[1].note)

    def test_real_fix_keeps_the_ledger_in_id_order(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        res = self.loop(sys1).run()
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        ids = [e.evidence_id for e in self.state.evidence_log]
        self.assertEqual(ids, sorted(ids, key=lambda s: int(s[1:])))
        kinds = [(e.kind, e.command.split(" ")[0]) for e in self.state.evidence_log[-3:]]
        self.assertEqual(kinds, [("module_check", "verify_module"), ("probe", POST_SYNTHESIS_RERUN.split(" ")[0]),
                                 ("module_check", EFFECT_CHECK_COMMAND.split(" ")[0])])
        # The module check is recorded exactly once although two methods could have recorded it.
        self.assertEqual(sum(1 for e in self.state.evidence_log if e.command.startswith("verify_module")), 1)

    def test_a_write_that_makes_the_probe_crash_is_inconclusive_and_kept(self):
        sys1 = ScriptedSynthesiser(self.ws, rounds=[[wrong(0.8), right(0.2)]], writes=[CRASHING])
        res = self.loop(sys1).run()
        # Module compiles and imports; the probe now exits non-zero: something
        # changed, the self-oracle decides at the end of the iteration.
        self.assertEqual(res.stats["syntheses_verified"], 1)
        self.assertEqual(res.stats["syntheses_unconfirmed"], 1)
        self.assertNotIn("syntheses_ineffective", res.stats)
        self.assertEqual(self.q.status, Q_RESOLVED)
        self.assertEqual(self.calc(), CRASHING)
        self.assertIn("effect: inconclusive", self.synth_moves(res)[0].note)

    def test_ab_mcts_arm_earns_nothing_for_an_ineffective_write(self):
        sys1 = ScriptedSynthesiser(self.ws, rounds=[[right(0.9)], [wrong(0.1)]], writes=[COSMETIC, FIXED])
        loop = self.loop(sys1, search_algorithm="ab_mcts", min_hypotheses_before_synthesis=1)
        loop.ab.rng = MeanRng()
        res = loop.run()
        moves = self.synth_moves(res)
        self.assertEqual(len(moves), 2)
        self.assertEqual(moves[0].extra["ab_y"], 0.0)           # cosmetic: no dU, not verified
        self.assertGreater(moves[1].extra["ab_y"], 0.5)         # the fix: dU plus the verification bonus
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        self.assertEqual(self.calc(), FIXED)

    def test_a_cosmetic_write_after_the_real_fix_is_reverted_even_though_the_root_probe_is_gone(self):
        # The marshmallow-1810 shape: the root question is fixed first (the
        # `Field.root` write), then a sub-question whose supported probe only
        # states a fact about the code gets a cosmetic rewrite. The root probe
        # no longer reproduces the bug, but it stopped before this write, so
        # the write is ineffective and must not enter the patch.
        sub = self.state.add_question("Where: mypkg/calc.py", module="mypkg/calc.py", uncertainty=0.8,
                                      source_failure_key="sub:calc")
        sys1 = PerQuestionSynthesiser(
            self.ws,
            rounds_by_question={self.q.question_id: [[right(0.9)]], sub.question_id: [[STRUCTURAL]]},
            writes_by_question={self.q.question_id: [FIXED], sub.question_id: [FIXED_COSMETIC, FIXED_COSMETIC]})
        res = self.loop(sys1, root_question_id=self.q.question_id, min_hypotheses_before_synthesis=1,
                        max_hypothesis_rounds=1, frontier_size=1).run()
        self.assertEqual(sys1.synth_questions[0], self.q.question_id)
        self.assertEqual(sys1.synth_questions[1:], [sub.question_id, sub.question_id])
        self.assertEqual(self.q.status, Q_RESOLVED)
        self.assertNotEqual(sub.status, Q_RESOLVED)
        self.assertEqual(self.calc(), FIXED)                         # the cosmetic rewrite is not in the patch
        self.assertEqual(res.stats["syntheses_verified"], 1)
        self.assertEqual(res.stats["syntheses_ineffective"], 2)
        self.assertEqual(res.stats["syntheses_reverted"], 2)
        moves = self.synth_moves(res)
        self.assertEqual(len(moves), 3)
        self.assertIn("effect: effective (1/1", moves[0].note)
        for m in moves[1:]:
            self.assertIn("effect: ineffective (0/2 re-run probe(s) no longer reproduce the bug, "
                          "1 had already stopped before this write)", m.note)
            self.assertIn("reverted to pre-synthesis module", m.note)
            self.assertEqual(m.delta_u, 0.0)
        sub_h = next(h for h in self.state.hypotheses_for(sub.question_id))
        self.assertEqual(sub_h.synthesis_failures, 2)
        self.assertFalse(sub_h.patch_applied)


class GatekeeperEffectTests(EffectFixture):

    def supported(self, proposal, question=None):
        question = question or self.q
        h = self.state.add_hypothesis(question.question_id, proposal.claim, proposal.mechanism, proposal.prior,
                                      proposal.probe_code, proposal.prediction)
        ev = self.gk.run_experiment(self.state, h)
        self.gk.apply(self.state, h, ev)
        self.assertEqual(h.status, SUPPORTED, ev.detail)
        return h

    def test_verdicts_and_summary_evidence(self):
        h = self.supported(right(0.5))
        self.ws.write_file("mypkg/calc.py", COSMETIC)
        out = self.gk.check_synthesis_effect(self.state, self.q, h)
        self.assertEqual(out["verdict"], EFFECT_INEFFECTIVE)
        self.assertEqual(out["probes_rerun"], 1)
        self.assertEqual(out["bug_gone_hypotheses"], [])
        summary = self.state.evidence_by_id(out["evidence_id"])
        self.assertEqual(summary.kind, "module_check")
        self.assertEqual(summary.command, EFFECT_CHECK_COMMAND)
        self.assertFalse(summary.matched_prediction)
        self.assertEqual(summary.exit_code, 1)
        self.assertIn(summary.evidence_id, h.evidence_ids)
        self.assertEqual(h.status, SUPPORTED)                     # re-runs never re-judge
        self.ws.write_file("mypkg/calc.py", FIXED)
        out2 = self.gk.check_synthesis_effect(self.state, self.q, h)
        self.assertEqual(out2["verdict"], EFFECT_EFFECTIVE)
        self.assertEqual(out2["bug_gone_hypotheses"], [h.hypothesis_id])
        self.assertTrue(self.state.evidence_by_id(out2["evidence_id"]).matched_prediction)
        self.ws.write_file("mypkg/calc.py", CRASHING)
        out3 = self.gk.check_synthesis_effect(self.state, self.q, h)
        self.assertEqual(out3["verdict"], EFFECT_INCONCLUSIVE)
        self.assertFalse(out3["reruns"][0]["completed"] and out3["reruns"][0]["matched_prediction"])
        self.assertNotEqual(out3["reruns"][0]["exit_code"], 0)

    def test_root_question_probes_can_make_a_structural_sub_question_fix_effective(self):
        root_h = self.supported(right(0.5))                       # the reproduction probe, on the root question
        sub = self.state.add_question("Where: mypkg/calc.py", module="mypkg/calc.py", uncertainty=0.8,
                                      source_failure_key="sub:calc")
        structural = HypothesisProposal("the module imports", "import path", 0.3, STRUCTURAL_PROBE,
                                        {"expect_exit_code": 0, "expect_stdout_contains": "IMPORT_OK"})
        sub_h = self.supported(structural, question=sub)
        self.ws.write_file("mypkg/calc.py", FIXED)
        alone = self.gk.check_synthesis_effect(self.state, sub, sub_h)
        self.assertEqual(alone["verdict"], EFFECT_INEFFECTIVE)    # IMPORT_OK is true before and after
        with_root = self.gk.check_synthesis_effect(self.state, sub, sub_h, root_question_id=self.q.question_id)
        self.assertEqual(with_root["verdict"], EFFECT_EFFECTIVE)
        self.assertEqual(with_root["bug_gone_hypotheses"], [root_h.hypothesis_id])
        self.assertEqual([r["hypothesis_id"] for r in with_root["reruns"]], [sub_h.hypothesis_id, root_h.hypothesis_id])

    def test_a_probe_an_earlier_write_already_flipped_cannot_vouch_for_a_later_one(self):
        root_h = self.supported(right(0.5))
        sub = self.state.add_question("Where: mypkg/calc.py", module="mypkg/calc.py", uncertainty=0.8,
                                      source_failure_key="sub:calc")
        structural = HypothesisProposal("the module imports", "import path", 0.3, STRUCTURAL_PROBE,
                                        {"expect_exit_code": 0, "expect_stdout_contains": "IMPORT_OK"})
        sub_h = self.supported(structural, question=sub)
        # The first write is the fix; its effect check sees the root probe flip.
        self.ws.write_file("mypkg/calc.py", FIXED)
        first = self.gk.check_synthesis_effect(self.state, self.q, root_h)
        self.assertEqual(first["verdict"], EFFECT_EFFECTIVE)
        self.assertTrue(self.gk.probe_already_gone(self.state, root_h))
        self.assertFalse(self.gk.probe_already_gone(self.state, sub_h))
        # A later cosmetic write on the sub-question (the marshmallow case:
        # `_invoke_field_validators` rewritten after `Field.root` was fixed).
        # Nothing it touches changes a probe, and the root probe was already
        # gone before it, so it must not be credited for that.
        self.ws.write_file("mypkg/calc.py", FIXED_COSMETIC)
        later = self.gk.check_synthesis_effect(self.state, sub, sub_h, root_question_id=self.q.question_id)
        self.assertEqual(later["verdict"], EFFECT_INEFFECTIVE)
        self.assertEqual(later["bug_gone_hypotheses"], [])
        self.assertEqual(later["already_gone_hypotheses"], [root_h.hypothesis_id])
        self.assertEqual([r["hypothesis_id"] for r in later["reruns"]], [sub_h.hypothesis_id, root_h.hypothesis_id])
        self.assertTrue(later["reruns"][1]["already_gone_before_write"])
        self.assertIn("1 had already stopped reproducing before this write", later["detail"])
        self.assertIn("(already before this write)", self.state.evidence_by_id(later["evidence_id"]).stdout)

    def test_a_second_write_for_a_hypothesis_whose_bug_is_already_gone_is_ineffective(self):
        h = self.supported(right(0.5))
        self.ws.write_file("mypkg/calc.py", FIXED)
        self.assertEqual(self.gk.check_synthesis_effect(self.state, self.q, h)["verdict"], EFFECT_EFFECTIVE)
        self.ws.write_file("mypkg/calc.py", FIXED_COSMETIC)
        out = self.gk.check_synthesis_effect(self.state, self.q, h)
        self.assertEqual(out["verdict"], EFFECT_INEFFECTIVE)
        self.assertEqual(out["already_gone_hypotheses"], [h.hypothesis_id])
        self.assertIn("had already stopped reproducing before this write", out["detail"])
        self.assertIn("an earlier write fixed it", out["detail"])

    def test_apply_synthesis_without_an_effect_keeps_the_old_contract(self):
        h = self.supported(right(0.5))
        self.ws.write_file("mypkg/calc.py", COSMETIC)
        good = self.gk.verify_module(self.state, "mypkg/calc.py", self.q.question_id, h.hypothesis_id)
        self.assertTrue(good.matched_prediction)
        effect = self.gk.check_synthesis_effect(self.state, self.q, h, module_evidence=good)
        self.assertEqual(self.gk.apply_synthesis(self.state, self.q, h, good, effect=effect), 0.0)
        self.assertEqual(h.synthesis_failures, 1)
        self.assertFalse(h.patch_applied)
        self.assertEqual(self.q.status, Q_OPEN)
        self.assertEqual(sum(1 for e in self.state.evidence_log if e.evidence_id == good.evidence_id), 1)
        # Callers that do not run the effect check get the pre-existing behaviour.
        again = self.gk.verify_module(self.state, "mypkg/calc.py", self.q.question_id, h.hypothesis_id)
        self.assertGreater(self.gk.apply_synthesis(self.state, self.q, h, again), 0.0)
        self.assertTrue(h.patch_applied)
        self.assertEqual(self.q.status, Q_RESOLVED)


if __name__ == "__main__":
    unittest.main()
