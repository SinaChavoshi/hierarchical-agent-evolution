"""The search loop: priors steer exploration but never prune; evidence is the only reward."""

import shutil
import tempfile
import unittest

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, Q_OPEN, Q_RESOLVED, SUPPORTED, UNVERIFIED, EpistemicState,
)
from hae.epistemic.mcts import (
    STOP_BUDGET, STOP_EXHAUSTED, STOP_RESOLVED, STOP_STAGNATION, STOP_TOKENS,
    EpistemicSearchLoop,
)
from hae.epistemic.moves import (
    MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE, HypothesisProposal,
)
from hae.epistemic.value import FEATURE_NAMES, EpistemicValueFunction
from hae.genome.schema import EpistemicPolicyGene
from hae.runtime.workspace import AgentWorkspace

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
PROBE = "from mypkg.calc import add\nprint('ADD', add(2, 2))\n"


_WRONG_VARIANTS = (
    ("add is correct and the caller passes bad args", "caller bug"),
    ("add is correct but the test harness passes string arguments", "argument types"),
    ("add is correct but the import resolves to a stale cached copy", "stale bytecode cache"),
)


def wrong(prior=0.8, variant=0):
    claim, mechanism = _WRONG_VARIANTS[variant]
    return HypothesisProposal(claim, mechanism, prior, PROBE,
                              {"expect_exit_code": 0, "expect_stdout_contains": "ADD 4"})


def right(prior=0.2):
    return HypothesisProposal("add subtracts because the operator is minus", "operator bug", prior, PROBE,
                              {"expect_exit_code": 0, "expect_stdout_contains": "ADD 0"})


class FakeSystem1:
    """Deterministic proposer/synthesiser that records what it was asked."""

    def __init__(self, ws, rounds, synth_fix=True):
        self.ws = ws
        self.rounds = list(rounds)
        self.synth_fix = synth_fix
        self.propose_calls = 0
        self.synth_calls = 0
        self.seen_ledgers = []

    def propose(self, question, state, k):
        self.propose_calls += 1
        self.seen_ledgers.append(state.summary(question.question_id))
        if not self.rounds:
            return []
        return self.rounds.pop(0)

    def synthesize(self, question, hypothesis, state):
        self.synth_calls += 1
        if self.synth_fix:
            self.ws.write_file("mypkg/calc.py", FIXED)
            return {"written": True, "path": "mypkg/calc.py", "summary": "fixed"}
        return {"written": False, "path": "mypkg/calc.py", "summary": "did nothing"}


class SearchFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_search_test_")
        self.ws = AgentWorkspace("search_firm", base_dir=self.tmp)
        self.ws.write_file("mypkg/__init__.py", "")
        self.ws.write_file("mypkg/calc.py", BUGGY)
        self.state = EpistemicState("search_firm")
        self.gk = EvidenceGatekeeper(self.ws, timeout_s=15, isolate=False, stage_reference=False,
                                     module_for_tag={"calc": "mypkg/calc.py"})
        self.gk.reconcile_with_oracle(
            self.state, ["[calc] FAIL: test_add (t.T.test_add) -> AssertionError: 0 != 4"], iteration=1)
        self.q = next(iter(self.state.questions.values()))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def loop(self, sys1, **policy_kw):
        kw = dict(enabled=True, search_budget_moves=20, min_hypotheses_before_synthesis=2,
                  low_prior_quota=0.0, max_stagnant_moves=6)
        kw.update(policy_kw)
        policy = EpistemicPolicyGene(**kw)
        return EpistemicSearchLoop(self.state, self.gk, EpistemicValueFunction(0.6), policy,
                                   sys1.propose, sys1.synthesize, rng_seed=7, logger=lambda s: None)


class CoreSearchTests(SearchFixture):

    def test_low_prior_truth_wins_after_high_prior_is_falsified(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        res = self.loop(sys1).run()
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        statuses = {h.claim[:12]: h.status for h in self.state.hypotheses.values()}
        self.assertEqual(statuses["add is corre"], FALSIFIED)
        self.assertEqual(statuses["add subtract"], SUPPORTED)
        self.assertEqual(self.q.status, Q_RESOLVED)
        self.assertEqual(res.synthesized_paths, ["mypkg/calc.py"])
        self.assertEqual(self.ws.read_file("mypkg/calc.py")["content"], FIXED)
        self.assertGreater(res.delta_u_total, 0.9)
        # High prior was tested first (PUCT order), then the low one.
        experiments = [m for m in res.trajectory if m.move_type == MOVE_RUN_EXPERIMENT]
        self.assertEqual([self.state.hypotheses[m.hypothesis_id].prior for m in experiments], [0.8, 0.2])

    def test_synthesis_waits_for_competing_hypotheses(self):
        sys1 = FakeSystem1(self.ws, rounds=[[right(0.9), wrong(0.1)]])
        res = self.loop(sys1, min_hypotheses_before_synthesis=2).run()
        types = [m.move_type for m in res.trajectory]
        # propose, experiment(right, supported), experiment(wrong) THEN synthesize.
        self.assertEqual(types[:4], [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT,
                                     MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE])

    def test_forced_low_prior_quota_jumps_over_the_plausible_middle(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.9, 1), wrong(0.6, 2), right(0.05)]])
        res = self.loop(sys1, low_prior_quota=0.8, branching_k=3).run()
        experiments = [m for m in res.trajectory if m.move_type == MOVE_RUN_EXPERIMENT]
        # First expansion follows the prior (0.9); the quota then forces the
        # *least* plausible (0.05) before the still-untested 0.6 sibling.
        self.assertFalse(experiments[0].forced_low_prior)
        self.assertAlmostEqual(self.state.hypotheses[experiments[0].hypothesis_id].prior, 0.9)
        self.assertTrue(experiments[1].forced_low_prior)
        self.assertAlmostEqual(self.state.hypotheses[experiments[1].hypothesis_id].prior, 0.05)
        middle = [h for h in self.state.hypotheses.values() if abs(h.prior - 0.6) < 1e-9][0]
        self.assertEqual(middle.status, UNVERIFIED)
        self.assertEqual(res.stats["forced_low_prior_picks"], 1)
        self.assertEqual(res.stats["forced_low_prior_wins"], 1)
        self.assertEqual(res.stop_reason, STOP_RESOLVED)

    def test_tabu_proposals_are_rejected_without_spending_an_experiment(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8)], [wrong(0.8, 0), right(0.3)]])
        res = self.loop(sys1, max_hypothesis_rounds=2).run()
        self.assertEqual(res.stats["tabu_rejections"], 1)
        self.assertEqual(res.stats["experiments"], 2)  # wrong once, right once -- never wrong twice
        self.assertEqual(len(self.state.hypotheses), 2)
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        # The second proposal round saw the ruled-out mechanism in its ledger view.
        self.assertIn("RULED OUT", sys1.seen_ledgers[1])

    def test_proposer_sees_ledger_not_raw_history(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        self.loop(sys1).run()
        self.assertIn("OPEN QUESTIONS", sys1.seen_ledgers[0])
        self.assertIn(self.q.question_id, sys1.seen_ledgers[0])


class StoppingTests(SearchFixture):

    def test_budget_stop(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        res = self.loop(sys1, search_budget_moves=4).run(budget_moves=2)
        self.assertEqual(res.stop_reason, STOP_BUDGET)
        self.assertEqual(res.moves_used, 2)

    def test_token_budget_stop(self):
        calls = {"n": 0}

        def may_continue():
            calls["n"] += 1
            return calls["n"] <= 2
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        policy = EpistemicPolicyGene(enabled=True, low_prior_quota=0.0)
        loop = EpistemicSearchLoop(self.state, self.gk, EpistemicValueFunction(), policy,
                                   sys1.propose, sys1.synthesize, may_continue=may_continue,
                                   logger=lambda s: None)
        res = loop.run()
        self.assertEqual(res.stop_reason, STOP_TOKENS)
        self.assertEqual(res.moves_used, 2)

    def test_exhausted_when_all_hypotheses_falsified_and_rounds_spent(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8, 1)], [wrong(0.7, 2)]])
        res = self.loop(sys1, max_hypothesis_rounds=2).run()
        self.assertEqual(res.stop_reason, STOP_EXHAUSTED)
        self.assertEqual(res.stats["falsified"], 2)
        self.assertTrue(self.q.is_open)

    def test_stagnation_stop_when_proposer_offers_nothing(self):
        sys1 = FakeSystem1(self.ws, rounds=[])
        res = self.loop(sys1, max_hypothesis_rounds=5, max_stagnant_moves=3).run()
        self.assertEqual(res.stop_reason, STOP_STAGNATION)
        self.assertEqual(res.moves_used, 3)

    def test_proposer_exception_is_a_recorded_move_not_a_crash(self):
        class Boom:
            def propose(self, q, s, k):
                raise RuntimeError("vLLM down")

            def synthesize(self, q, h, s):
                return {}
        policy = EpistemicPolicyGene(enabled=True, max_stagnant_moves=2, max_hypothesis_rounds=3)
        loop = EpistemicSearchLoop(self.state, self.gk, EpistemicValueFunction(), policy,
                                   Boom().propose, Boom().synthesize, logger=lambda s: None)
        res = loop.run()
        self.assertEqual(res.stats["proposer_errors"], 2)
        self.assertIn("proposer error", res.trajectory[0].note)

    def test_unwritten_synthesis_does_not_resolve(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]], synth_fix=False)
        res = self.loop(sys1).run()
        self.assertEqual(res.stats["syntheses_unwritten"], 2)
        self.assertEqual(self.q.status, Q_OPEN)
        self.assertEqual(res.synthesized_paths, [])


class TrajectoryTests(SearchFixture):

    def test_every_move_carries_features_hashes_and_values(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        res = self.loop(sys1).run()
        self.assertGreaterEqual(len(res.trajectory), 4)
        for m in res.trajectory:
            self.assertEqual(set(m.features), set(FEATURE_NAMES))
            self.assertTrue(m.state_hash_before and m.state_hash_after)
            self.assertGreaterEqual(m.value_after, 0.0)
        # Experiments change the belief hash; the ledger ends resolved with a higher value.
        exp = [m for m in res.trajectory if m.move_type == MOVE_RUN_EXPERIMENT][0]
        self.assertNotEqual(exp.state_hash_before, exp.state_hash_after)
        self.assertGreater(res.trajectory[-1].value_after, res.trajectory[0].value_before)
        self.assertEqual(len(self.state.move_log), len(res.trajectory))

    def test_result_serialises(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        d = self.loop(sys1).run().to_dict()
        self.assertEqual(d["stop_reason"], STOP_RESOLVED)
        self.assertEqual(len(d["trajectory"]), d["moves_used"])
        self.assertIn("forced_low_prior_picks", d["stats"])


if __name__ == "__main__":
    unittest.main()
