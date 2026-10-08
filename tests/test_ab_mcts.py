"""V9: Adaptive Branching MCTS (AB-MCTS-A) in the epistemic search loop.

Paper: Inoue et al., "Wider or Deeper? Scaling LLM Inference-Time Compute with
Adaptive Branching Tree Search", arXiv:2503.04412.

Four things are pinned down here:

  1. `search_algorithm == "puct"` (the default) is the V8 loop. Three seeded
     searches -- no organisation, an organisation with a stall-triggered
     recruit, a two-round falsification -- are compared move for move against
     a golden fixture recorded from the code *before* AB-MCTS existed
     (tests/golden/puct_v8_trajectories.json). Regenerate only on purpose:
     HAE_UPDATE_GOLDEN=1 python3 -m unittest tests.test_ab_mcts.PuctIdentityTests
  2. Easy bug, width 1: GEN -> EXPERIMENT -> SYNTHESIZE in three moves.
  3. Hard bug: hypotheses keep being falsified; the loop widens past the
     V8 cap of max_hypothesis_rounds * branching_k = 6 without "exhausted".
  4. Specialist decay: a role whose hypotheses keep failing loses its GEN arm's
     posterior to the CEO arm; the recruit happens inside the tree policy and
     the recruit's GEN arm takes over proposing.

The behavioural tests replace the controller's rng with `MeanRng`, whose
Beta "draw" is the posterior mean, so the chosen arm is the one with the
largest posterior mean and the move sequence is a function of the rewards
alone. A few assertions also run the real seeded Thompson sampling, loosely.
"""

import io
import json
import os
import random
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from hae.epistemic.ab_mcts import (
    ARM_EXPERIMENT, ARM_GEN_HYPOTHESIS, ARM_GEN_SPECIALIST, ARM_SYNTHESIZE, CEO_IDENT,
    GEN_EMPTY_LIMIT, UNNAMED_GEN_PRIOR, AdaptiveBranchingController, BetaArm, hypothesis_reward,
    synthesis_reward,
)
from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.genes import crossover_epistemic_policy, mutate_epistemic_policy
from hae.epistemic.ledger import FALSIFIED, SUPPORTED, UNVERIFIED, EpistemicState
from hae.epistemic.mcts import STOP_EXHAUSTED, STOP_RESOLVED
from hae.epistemic.moves import (
    MOVE_PROPOSE_HYPOTHESIS, MOVE_RECRUIT_SPECIALIST, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE,
    HypothesisProposal,
)
from hae.epistemic.org import OrgState
from hae.evaluation.verification_loop import VerificationLoop
from hae.genome.schema import (
    DEFAULT_AB_PRIOR_STRENGTH, EPISTEMIC_POLICY_BOUNDS, EpistemicPolicyGene, GenomeValidationError,
)
from hae.runtime.company import HierarchicalCompanyRunner
from hae.runtime.workspace import AgentWorkspace
from tests.test_epistemic_runner import BUGGY as RUNNER_BUGGY, OBJECTIVE, _genome
from tests.test_epistemic_search import PROBE, FakeSystem1, right, wrong
from tests.test_recruit_move import (
    LoopFixture, RecordingRecruiter, RoleAwareSystem1, ScriptedCEO, ceo_policy, refused, role, strip_volatile,
)

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "puct_v8_trajectories.json")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

class MeanRng:
    """A stand-in rng whose Beta draw is the posterior mean (greedy Thompson sampling)."""

    def betavariate(self, alpha, beta):
        return alpha / (alpha + beta)


class KRecordingSystem1(FakeSystem1):
    """Records the `k` each PROPOSE asked for."""

    def __init__(self, ws, rounds, synth_fix=True):
        super().__init__(ws, rounds, synth_fix)
        self.ks = []

    def propose(self, question, state, k):
        self.ks.append(k)
        return super().propose(question, state, k)


class ByRoleSystem1(RoleAwareSystem1):
    """Scripted per role: what a role proposes depends on who is asked."""

    def __init__(self, ws, rounds_by_role, synth_fix=True):
        super().__init__(ws, rounds=[], synth_fix=synth_fix)
        self.rounds_by_role = {k: list(v) for k, v in rounds_by_role.items()}

    def propose(self, question, state, k, role=None):
        self.propose_calls += 1
        self.propose_roles.append(role.role_id if role is not None else None)
        rounds = self.rounds_by_role.get(role.role_id if role is not None else None, [])
        return rounds.pop(0) if rounds else []


# Eight distinct wrong mechanisms: enough to outgrow the V8 cap of
# max_hypothesis_rounds (2) * branching_k (3) = 6 on one question.
_HARD_WRONG = (
    ("the caller passes the arguments swapped", "argument order"),
    ("the test fixture imports a shadowed module", "import shadowing"),
    ("integer overflow wraps the sum", "overflow"),
    ("locale settings change number parsing", "locale parsing"),
    ("a decorator rewrites the return value", "decorator rewrite"),
    ("conftest monkeypatches the function", "monkeypatch"),
    ("the bytecode cache holds an older build", "stale cache"),
    ("thread scheduling reorders the operands", "race condition"),
)


def hard_wrong(i, prior=0.8):
    claim, mechanism = _HARD_WRONG[i]
    return HypothesisProposal(claim, mechanism, prior, PROBE,
                              {"expect_exit_code": 0, "expect_stdout_contains": "ADD 4"})


def ab_fields(move):
    return {k: v for k, v in move.extra.items() if k.startswith("ab_")}


# --------------------------------------------------------------------------- #
# 1. PUCT identity
# --------------------------------------------------------------------------- #

class PuctIdentityTests(LoopFixture):
    """With the default `search_algorithm` the loop is the V8 loop, move for move."""

    def scenario_no_org(self):
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        return self.loop(sys1).run()

    def scenario_recruit_under_stall(self):
        generalist = role("Generalist")
        org = OrgState([generalist], ceo_policy())
        hire = role("Arithmetic Debugger", tags=("calc", "arithmetic"),
                    origin="recruited:test", extra={"source": "synthesized"})
        recruiter = RecordingRecruiter([(hire, "synthesized by CEO: operators")])
        sys1 = RoleAwareSystem1(self.ws, rounds=[[refused(0.8, 0), refused(0.7, 1)], [right(0.3)]])
        return self.loop(sys1, org=org, recruiter=recruiter, max_hypothesis_rounds=2).run()

    def scenario_two_rounds(self):
        analyst = role("Analyst", kind="probe")
        writer = role("Writer", kind="synthesis")
        org = OrgState([analyst, writer], ceo_policy(recruit_bias=-8.0))
        sys1 = RoleAwareSystem1(self.ws, rounds=[[wrong(0.8, 0), wrong(0.7, 1)], [wrong(0.6, 2), right(0.3)]])
        return self.loop(sys1, org=org, recruiter=None, max_hypothesis_rounds=2).run()

    SCENARIOS = ("scenario_no_org", "scenario_recruit_under_stall", "scenario_two_rounds")

    def snapshot(self, result):
        return {"stop_reason": result.stop_reason,
                "trajectory": [strip_volatile(m.to_dict()) for m in result.trajectory],
                "stats": result.stats,
                "hypotheses": [h.to_dict() for h in result.final_state.hypotheses.values()]}

    @staticmethod
    def _scrub(snap):
        """Drop wall-clock fields; everything else must match exactly."""
        out = json.loads(json.dumps(snap, sort_keys=True, default=str))
        for h in out["hypotheses"]:
            h.pop("created_at", None)
        return out

    def test_default_policy_matches_the_pre_ab_mcts_golden(self):
        snaps = {}
        for name in self.SCENARIOS:
            self.setUp()                       # a fresh ledger and workspace per scenario
            snaps[name] = self._scrub(self.snapshot(getattr(self, name)()))
        if os.environ.get("HAE_UPDATE_GOLDEN"):
            os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
            with open(GOLDEN, "w", encoding="utf-8") as fh:
                json.dump(snaps, fh, indent=1, sort_keys=True)
                fh.write("\n")
        with open(GOLDEN, "r", encoding="utf-8") as fh:
            golden = json.load(fh)
        self.assertEqual(set(golden), set(snaps))
        for name in self.SCENARIOS:
            self.assertEqual(golden[name]["stop_reason"], snaps[name]["stop_reason"], name)
            self.assertEqual(golden[name]["trajectory"], snaps[name]["trajectory"], name)
            self.assertEqual(golden[name]["stats"], snaps[name]["stats"], name)
            self.assertEqual(golden[name]["hypotheses"], snaps[name]["hypotheses"], name)
        self.assertEqual(snaps["scenario_no_org"]["stop_reason"], STOP_RESOLVED)
        self.assertIn(MOVE_RECRUIT_SPECIALIST, [m["move_type"] for m in snaps["scenario_recruit_under_stall"]["trajectory"]])

    def test_puct_loop_has_no_controller_and_no_v9_stats_keys(self):
        loop = self.loop(FakeSystem1(self.ws, rounds=[[right(0.9)]]))
        self.assertIsNone(loop.ab)
        result = loop.run()
        self.assertNotIn("search_algorithm", result.stats)
        self.assertNotIn("ab_mcts", result.stats)
        self.assertFalse(any(ab_fields(m) for m in result.trajectory))


# --------------------------------------------------------------------------- #
# The gene
# --------------------------------------------------------------------------- #

class GeneTests(unittest.TestCase):

    def test_defaults_and_validation(self):
        gene = EpistemicPolicyGene()
        self.assertEqual(gene.search_algorithm, "puct")
        self.assertEqual(gene.ab_prior_strength, DEFAULT_AB_PRIOR_STRENGTH)
        self.assertEqual(EpistemicPolicyGene(search_algorithm=" AB_MCTS ").search_algorithm, "ab_mcts")
        with self.assertRaises(GenomeValidationError):
            EpistemicPolicyGene(search_algorithm="alphazero")
        self.assertIn("ab_prior_strength", EPISTEMIC_POLICY_BOUNDS)

    def test_puct_genes_serialise_without_the_v9_keys(self):
        # Archived populations must round-trip byte-identically (tests/test_v8_genome.py).
        d = EpistemicPolicyGene(enabled=True).to_dict()
        self.assertNotIn("search_algorithm", d)
        self.assertNotIn("ab_prior_strength", d)
        # A tuned prior strength is genetic material and survives even under puct.
        self.assertEqual(EpistemicPolicyGene(ab_prior_strength=2.5).to_dict().get("ab_prior_strength"), 2.5)
        d = EpistemicPolicyGene(enabled=True, search_algorithm="ab_mcts").to_dict()
        self.assertEqual(d["search_algorithm"], "ab_mcts")
        self.assertEqual(d["ab_prior_strength"], DEFAULT_AB_PRIOR_STRENGTH)
        again = EpistemicPolicyGene.from_dict(d)
        self.assertEqual(again.search_algorithm, "ab_mcts")
        self.assertEqual(again.to_dict(), d)

    def test_operators_never_flip_the_algorithm(self):
        rng = random.Random(3)
        ab = EpistemicPolicyGene(enabled=True, search_algorithm="ab_mcts", ab_prior_strength=8.0)
        puct = EpistemicPolicyGene(enabled=True)
        for _ in range(25):
            m = mutate_epistemic_policy(ab, rng)
            self.assertEqual(m.search_algorithm, "ab_mcts")
            lo, hi, _ = EPISTEMIC_POLICY_BOUNDS["ab_prior_strength"]
            self.assertTrue(lo <= m.ab_prior_strength <= hi)
            self.assertEqual(mutate_epistemic_policy(puct, rng).search_algorithm, "puct")
        self.assertEqual(crossover_epistemic_policy(ab, puct, rng).search_algorithm, "ab_mcts")
        self.assertEqual(crossover_epistemic_policy(puct, ab, rng).search_algorithm, "puct")
        strengths = {crossover_epistemic_policy(ab, puct, random.Random(i)).ab_prior_strength for i in range(30)}
        self.assertEqual(strengths, {8.0, DEFAULT_AB_PRIOR_STRENGTH})


# --------------------------------------------------------------------------- #
# The controller
# --------------------------------------------------------------------------- #

class BetaArmTests(unittest.TestCase):

    def test_prior_is_a_pseudo_count_split_by_the_prior_mean(self):
        a = BetaArm(kind=ARM_EXPERIMENT, ident="h1", prior_mean=0.9, tau=4.0)
        self.assertAlmostEqual(a.alpha, 1.0 + 3.6)
        self.assertAlmostEqual(a.beta, 1.0 + 0.4)
        self.assertAlmostEqual(a.mean, 4.6 / 6.0)
        flat = BetaArm(kind=ARM_EXPERIMENT, ident="h2", prior_mean=0.9, tau=0.0)
        self.assertAlmostEqual(flat.mean, 0.5)   # tau 0: the prior carries no weight

    def test_observe_accumulates_clamped_rewards(self):
        a = BetaArm(kind=ARM_GEN_HYPOTHESIS, ident="r", prior_mean=0.5, tau=4.0)
        self.assertEqual(a.observe(1.7), 1.0)
        self.assertEqual(a.observe(-0.2), 0.0)
        a.observe(0.25)
        self.assertAlmostEqual(a.wins, 1.25)
        self.assertAlmostEqual(a.losses, 1.75)
        self.assertEqual(a.observations, 3)
        self.assertEqual(a.history, [1.0, 0.0, 0.25])
        self.assertAlmostEqual(a.mean, (1 + 2 + 1.25) / (1 + 2 + 1.25 + 1 + 2 + 1.75))

    def test_sample_uses_the_given_rng(self):
        a = BetaArm(kind=ARM_EXPERIMENT, ident="h1", prior_mean=0.7, tau=4.0)
        self.assertAlmostEqual(a.sample(MeanRng()), a.mean)
        self.assertAlmostEqual(a.last_theta, a.mean)
        theta = a.sample(random.Random(1))
        self.assertTrue(0.0 <= theta <= 1.0)

    def test_rewards(self):
        self.assertAlmostEqual(hypothesis_reward(0.3, SUPPORTED), 0.8)
        self.assertAlmostEqual(hypothesis_reward(0.3, FALSIFIED), 0.3)
        self.assertEqual(hypothesis_reward(0.9, SUPPORTED), 1.0)
        self.assertEqual(hypothesis_reward(-0.1, FALSIFIED), 0.0)
        self.assertAlmostEqual(synthesis_reward(0.1, True), 0.6)
        self.assertAlmostEqual(synthesis_reward(0.1, False), 0.1)


class ControllerTests(LoopFixture):
    """Arm sets and bookkeeping against a real ledger (one open question)."""

    def controller(self, tau=4.0):
        return AdaptiveBranchingController(tau, MeanRng())

    def add(self, prop, role_id=""):
        return self.state.add_hypothesis(self.q.question_id, prop.claim, prop.mechanism, prop.prior,
                                         prop.probe_code, prop.prediction, proposed_by="t", role_id=role_id)

    def kinds(self, arms):
        return [(a.kind, a.ident) for a in arms]

    def test_without_an_organisation_there_is_one_unnamed_gen_arm_and_never_a_ceo_arm(self):
        ab = self.controller()
        arms = ab.candidate_arms(self.q, self.state, None, recruit_eligible=True)
        self.assertEqual(self.kinds(arms), [(ARM_GEN_HYPOTHESIS, "")])
        self.assertEqual(arms[0].prior_mean, UNNAMED_GEN_PRIOR)
        h = self.add(wrong(0.8))
        arms = ab.candidate_arms(self.q, self.state, None, recruit_eligible=True)
        self.assertEqual(self.kinds(arms), [(ARM_EXPERIMENT, h.hypothesis_id), (ARM_GEN_HYPOTHESIS, "")])
        self.assertEqual(arms[0].prior_mean, 0.8)

    def test_deepen_arms_follow_the_ledger(self):
        ab = self.controller()
        h = self.add(right(0.6))
        h.status, h.posterior = SUPPORTED, 0.9
        arms = ab.candidate_arms(self.q, self.state, None, recruit_eligible=False)
        self.assertEqual(self.kinds(arms), [(ARM_SYNTHESIZE, h.hypothesis_id), (ARM_GEN_HYPOTHESIS, "")])
        self.assertEqual(arms[0].prior_mean, 0.9)
        h.posterior = 0.95                      # the synthesis arm re-reads the posterior
        self.assertEqual(ab.candidate_arms(self.q, self.state, None, False)[0].prior_mean, 0.95)
        h.patch_applied = True                  # patched: nothing left to deepen
        self.assertEqual(self.kinds(ab.candidate_arms(self.q, self.state, None, False)), [(ARM_GEN_HYPOTHESIS, "")])
        h.patch_applied, h.synthesis_failures = False, 2
        self.assertEqual(self.kinds(ab.candidate_arms(self.q, self.state, None, False)), [(ARM_GEN_HYPOTHESIS, "")])
        self.assertEqual(h.status, SUPPORTED)
        self.assertEqual(UNVERIFIED, "UNVERIFIED")

    def test_with_an_organisation_gen_arms_are_per_probing_role_and_the_ceo_arm_is_gated(self):
        prober = role("Prober", kind="probe", uses=10, support_rate=0.75)
        novice = role("Novice", kind="both")
        writer = role("Writer", kind="synthesis")
        org = OrgState([prober, novice, writer], ceo_policy(optimistic_prior=0.3))
        ab = self.controller()
        arms = ab.candidate_arms(self.q, self.state, org, recruit_eligible=False)
        self.assertEqual(self.kinds(arms), [(ARM_GEN_HYPOTHESIS, prober.role_id), (ARM_GEN_HYPOTHESIS, novice.role_id)])
        self.assertEqual(arms[0].prior_mean, 0.75)      # breeder data
        self.assertEqual(arms[1].prior_mean, 0.3)       # no data: the CEO's optimistic prior
        arms = ab.candidate_arms(self.q, self.state, org, recruit_eligible=True)
        self.assertEqual(arms[-1].key, (ARM_GEN_SPECIALIST, CEO_IDENT))
        self.assertAlmostEqual(arms[-1].prior_mean, org.recruit_prior())
        # A team with no role able to probe falls back to the unnamed arm (the bound proposer).
        only_writer = OrgState([writer], ceo_policy())
        self.assertEqual(self.kinds(ab.candidate_arms(self.q, self.state, only_writer, False)), [(ARM_GEN_HYPOTHESIS, "")])

    def test_selection_is_argmax_and_ties_fall_to_the_earlier_arm(self):
        ab = self.controller()
        a = BetaArm(ARM_EXPERIMENT, "h1", 0.5, 4.0)
        b = BetaArm(ARM_EXPERIMENT, "h2", 0.5, 4.0)
        c = BetaArm(ARM_GEN_HYPOTHESIS, "", 0.6, 4.0)
        chosen, draws = ab.select([a, b, c])
        self.assertIs(chosen, c)
        self.assertEqual(set(draws), {"experiment:h1", "experiment:h2", "gen_hypothesis:-"})
        chosen, _ = ab.select([a, b])
        self.assertIs(chosen, a)
        self.assertEqual((a.pulls, b.pulls, c.pulls), (1, 0, 1))
        self.assertEqual(ab.counts["steps"], 2)
        with self.assertRaises(ValueError):
            ab.select([])

    def test_empty_gen_draws_exhaust_a_question_and_a_hire_resets_it(self):
        ab = self.controller()
        arm = ab.candidate_arms(self.q, self.state, None, False)[0]
        for i in range(GEN_EMPTY_LIMIT):
            self.assertFalse(ab.gen_exhausted(self.q.question_id))
            self.assertTrue(ab.can_widen(self.q, recruit_eligible=False))
            ab.observe_generation(self.q, "", added=0, width=0)
        self.assertTrue(ab.gen_exhausted(self.q.question_id))
        self.assertFalse(ab.can_widen(self.q, recruit_eligible=False))
        self.assertTrue(ab.can_widen(self.q, recruit_eligible=True))
        self.assertEqual(arm.history, [0.0] * GEN_EMPTY_LIMIT)
        self.assertEqual(ab.counts["gen_exhausted_questions"], 1)
        # No GEN arm is offered on an exhausted question, only deepen arms (none here) and a recruit.
        self.assertEqual(ab.candidate_arms(self.q, self.state, None, False), [])
        ab.observe_recruit(self.q, hired=True)
        self.assertFalse(ab.gen_exhausted(self.q.question_id))
        ab.observe_generation(self.q, "", added=1, width=3)
        self.assertEqual(ab.gen_empty[self.q.question_id], 0)
        self.assertEqual(ab.counts["widest_question"], 3)

    def test_experiment_reward_reaches_the_proposer_and_only_a_recruits_reach_the_ceo(self):
        veteran = role("Veteran", kind="probe")
        hire = role("Hire", kind="probe")
        org = OrgState([veteran], ceo_policy())
        ab = self.controller()
        ab.candidate_arms(self.q, self.state, org, recruit_eligible=True)   # creates GEN(veteran) and CEO arms
        org.recruit(hire, 3, reason="test", source="library")
        h_vet = self.add(wrong(0.8, 0), role_id=veteran.role_id)
        h_new = self.add(right(0.4), role_id=hire.role_id)
        ab.candidate_arms(self.q, self.state, org, recruit_eligible=False)  # experiment arms + GEN(hire)
        h_vet.status = FALSIFIED
        self.assertEqual(ab.observe_experiment(self.q, h_vet, 0.0, org), 0.0)
        h_new.status = SUPPORTED
        self.assertEqual(ab.observe_experiment(self.q, h_new, 0.3, org), 0.8)
        by_key = {(k, i): a for (_q, k, i), a in ab._arms.items()}
        self.assertEqual(by_key[(ARM_GEN_HYPOTHESIS, veteran.role_id)].history, [0.0])
        self.assertEqual(by_key[(ARM_GEN_HYPOTHESIS, hire.role_id)].history, [0.8])
        self.assertEqual(by_key[(ARM_GEN_SPECIALIST, CEO_IDENT)].history, [0.8])
        self.assertEqual(by_key[(ARM_EXPERIMENT, h_vet.hypothesis_id)].history, [0.0])
        ab.observe_recruit(self.q, hired=False)
        self.assertEqual(by_key[(ARM_GEN_SPECIALIST, CEO_IDENT)].history, [0.8, 0.0])
        d = ab.to_dict()
        self.assertEqual(d["algorithm"], "ab_mcts")
        self.assertEqual(d["prior_strength"], 4.0)
        self.assertEqual(set(d), {"algorithm", "prior_strength", "counts", "gen_empty", "arms", "trace"})
        self.assertEqual(len(d["arms"][self.q.question_id]), 5)


# --------------------------------------------------------------------------- #
# 2. Easy bug: width one
# --------------------------------------------------------------------------- #

class FastPathTests(LoopFixture):

    def test_one_hypothesis_is_proposed_tested_and_patched_in_three_moves(self):
        sys1 = KRecordingSystem1(self.ws, rounds=[[right(0.9)]])
        loop = self.loop(sys1, search_algorithm="ab_mcts", min_hypotheses_before_synthesis=3, branching_k=3)
        self.assertIsNotNone(loop.ab)
        loop.ab.rng = MeanRng()
        result = loop.run()
        self.assertEqual(result.stop_reason, STOP_RESOLVED)
        self.assertEqual([m.move_type for m in result.trajectory],
                         [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE])
        self.assertEqual(sys1.ks, [1])                       # one hypothesis at a time, not branching_k
        self.assertEqual(len(result.final_state.hypotheses), 1)
        # The waterfall's `min_hypotheses_before_synthesis` (3 here) is not consulted.
        self.assertEqual(result.stats["syntheses_verified"], 1)
        self.assertEqual(result.stats["search_algorithm"], "ab_mcts")
        counts = result.stats["ab_mcts"]["counts"]
        self.assertEqual((counts["steps"], counts["widen_hypothesis"], counts["deepen_experiment"],
                          counts["deepen_synthesize"], counts["widen_specialist"]), (3, 1, 1, 1, 0))
        for move in result.trajectory:
            self.assertEqual(set(ab_fields(move)), {"ab_theta", "ab_mean", "ab_arms", "ab_y"})
        gen, exp, syn = result.trajectory
        self.assertEqual(gen.extra["ab_arms"], 1.0)           # only the GEN arm existed
        self.assertEqual(exp.extra["ab_arms"], 2.0)           # experiment(h1) beat GEN
        self.assertAlmostEqual(exp.extra["ab_theta"], (1 + 4 * 0.9) / 6.0, places=5)
        self.assertEqual(exp.extra["ab_y"], 1.0)              # dU 0.95 + 0.5 bonus, clamped
        self.assertEqual(len(result.stats["ab_mcts"]["trace"]), 3)

    def test_the_seeded_thompson_sampler_also_resolves_the_easy_bug_quickly(self):
        for seed in range(6):
            self.setUp()
            sys1 = FakeSystem1(self.ws, rounds=[[right(0.9)]])
            loop = self.loop(sys1, search_algorithm="ab_mcts")
            loop.ab.rng = random.Random(seed)
            result = loop.run()
            self.assertEqual(result.stop_reason, STOP_RESOLVED, seed)
            self.assertLessEqual(result.moves_used, 5, seed)


# --------------------------------------------------------------------------- #
# 3. Hard bug: widen past the V8 cap
# --------------------------------------------------------------------------- #

class WideningTests(LoopFixture):

    def test_falsifications_keep_widening_until_the_right_mechanism_appears(self):
        rounds = [[hard_wrong(i)] for i in range(7)] + [[right(0.3)]]
        sys1 = KRecordingSystem1(self.ws, rounds=rounds)
        loop = self.loop(sys1, search_algorithm="ab_mcts", search_budget_moves=30, max_stagnant_moves=50,
                         max_hypothesis_rounds=2, branching_k=3)
        loop.ab.rng = MeanRng()
        result = loop.run()
        self.assertEqual(result.stop_reason, STOP_RESOLVED)
        self.assertNotEqual(result.stop_reason, STOP_EXHAUSTED)
        self.assertEqual(len(result.final_state.hypotheses), 8)
        self.assertGreater(len(result.final_state.hypotheses), 6)
        self.assertEqual(set(sys1.ks), {1})
        types = [m.move_type for m in result.trajectory]
        self.assertEqual(types, [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT] * 8 + [MOVE_SYNTHESIZE])
        self.assertEqual(result.stats["falsified"], 7)
        counts = result.stats["ab_mcts"]["counts"]
        self.assertEqual(counts["widest_question"], 8)
        self.assertEqual(counts["gen_empty_draws"], 0)
        # Each new hypothesis is tested as soon as it exists: with an untested
        # h (prior 0.8 -> mean 0.7) on the table, deepening beats a GEN arm
        # whose posterior is sinking under falsifications that resolve nothing.
        gen_means = [m.extra["ab_mean"] for m in result.trajectory if m.move_type == MOVE_PROPOSE_HYPOTHESIS]
        self.assertEqual(gen_means, sorted(gen_means, reverse=True))
        self.assertLess(gen_means[-1], 0.3)
        gen_arm = [a for a in result.stats["ab_mcts"]["arms"][self.q.question_id]
                   if a["kind"] == ARM_GEN_HYPOTHESIS][0]
        self.assertEqual(gen_arm["pulls"], 8)
        self.assertEqual(gen_arm["rewards"][1:7], [0.0] * 6)   # late falsifications earn nothing
        self.assertGreater(gen_arm["rewards"][7], 0.5)         # the SUPPORTED one does

    def test_the_puct_loop_stops_exhausted_on_the_same_proposals(self):
        rounds = [[hard_wrong(0), hard_wrong(1), hard_wrong(2)], [hard_wrong(3), hard_wrong(4), hard_wrong(5)],
                  [hard_wrong(6), right(0.3)]]
        sys1 = FakeSystem1(self.ws, rounds=rounds)
        result = self.loop(sys1, search_budget_moves=30, max_stagnant_moves=50,
                           max_hypothesis_rounds=2, branching_k=3).run()
        self.assertEqual(result.stop_reason, STOP_EXHAUSTED)
        self.assertEqual(len(result.final_state.hypotheses), 6)
        self.assertEqual(len(sys1.rounds), 1)                  # the right mechanism was never asked for

    def test_a_proposer_that_finds_nothing_exhausts_the_question_without_a_round_cap(self):
        sys1 = KRecordingSystem1(self.ws, rounds=[])
        loop = self.loop(sys1, search_algorithm="ab_mcts", max_hypothesis_rounds=5)
        loop.ab.rng = MeanRng()
        result = loop.run()
        self.assertEqual(result.stop_reason, STOP_EXHAUSTED)
        self.assertEqual([m.move_type for m in result.trajectory], [MOVE_PROPOSE_HYPOTHESIS] * GEN_EMPTY_LIMIT)
        counts = result.stats["ab_mcts"]["counts"]
        self.assertEqual((counts["gen_empty_draws"], counts["gen_exhausted_questions"]), (GEN_EMPTY_LIMIT, 1))


# --------------------------------------------------------------------------- #
# 4. Specialist decay: the CEO arm wins inside the tree policy
# --------------------------------------------------------------------------- #

class DecayRecruitTests(LoopFixture):

    def setUp(self):
        super().setUp()
        self.analyst = role("Analyst", uses=10, support_rate=0.6)
        self.hire = role("Operator Semantics Engineer", tags=("calc", "operators"),
                         origin="recruited:test", extra={"source": "synthesized"})
        # No stall or coverage term: the CEO arm's prior is sigmoid(-0.4 * headcount),
        # 0.40 with one role, so only the analyst's decay can let it win.
        self.policy = ceo_policy(recruit_bias=0.0, recruit_w_stall=0.0, recruit_w_unmatched=0.0,
                                 recruit_w_headcount=0.4, optimistic_prior=0.6)

    def system1(self):
        return ByRoleSystem1(self.ws, {self.analyst.role_id: [[refused(0.8, 0)], [refused(0.7, 1)]],
                                       self.hire.role_id: [[right(0.7)]]})

    def test_the_recruit_fires_when_the_roles_gen_posterior_sinks_below_the_ceo_arm(self):
        org = OrgState([self.analyst], self.policy)
        recruiter = RecordingRecruiter([(self.hire, "synthesized by CEO: operators")])
        sys1 = self.system1()
        loop = self.loop(sys1, org=org, recruiter=recruiter, search_algorithm="ab_mcts")
        loop.ab.rng = MeanRng()
        result = loop.run()
        self.assertEqual(result.stop_reason, STOP_RESOLVED)
        self.assertEqual([m.move_type for m in result.trajectory], [
            MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT,     # analyst: probe refused, dU 0
            MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT,     # analyst again: refused again
            MOVE_RECRUIT_SPECIALIST,                           # CEO arm now beats GEN(analyst)
            MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE])   # the hire resolves it
        self.assertEqual(sys1.propose_roles, [self.analyst.role_id, self.analyst.role_id, self.hire.role_id])
        self.assertEqual([c["move_index"] for c in recruiter.calls], [5])
        self.assertEqual(result.stats["recruits"], 1)
        # The decision, from the trace: GEN(analyst) had decayed from its prior
        # mean 0.567 to 0.425 after two refusals; the CEO arm's mean was 0.433.
        trace = result.stats["ab_mcts"]["trace"]
        decision = trace[4]
        self.assertEqual(decision["chosen"], f"{ARM_GEN_SPECIALIST}:{CEO_IDENT}")
        gen_key = f"{ARM_GEN_HYPOTHESIS}:{self.analyst.role_id}"
        self.assertAlmostEqual(trace[0]["draws"][gen_key], (1 + 4 * 0.6) / 6.0, places=5)
        self.assertLess(decision["draws"][gen_key], decision["draws"][f"{ARM_GEN_SPECIALIST}:{CEO_IDENT}"])
        self.assertAlmostEqual(decision["draws"][gen_key], (1 + 2.4) / (6.0 + 2), places=5)
        recruit = result.trajectory[4]
        self.assertEqual(set(ab_fields(recruit)), {"ab_theta", "ab_mean", "ab_arms", "ab_y"})
        self.assertIn("recruit_prior", recruit.extra)
        self.assertEqual(recruit.role_id, self.hire.role_id)
        # Node aggregation: the hire's SUPPORTED hypothesis is credited to the CEO arm too.
        arms = {(a["kind"], a["ident"]): a for a in result.stats["ab_mcts"]["arms"][self.q.question_id]}
        self.assertEqual(arms[(ARM_GEN_SPECIALIST, CEO_IDENT)]["rewards"], [1.0])
        self.assertEqual(arms[(ARM_GEN_HYPOTHESIS, self.hire.role_id)]["rewards"], [1.0])
        self.assertEqual(arms[(ARM_GEN_HYPOTHESIS, self.analyst.role_id)]["rewards"], [0.0, 0.0])
        self.assertEqual(result.stats["ab_mcts"]["counts"]["widen_specialist"], 1)
        # The recruit went through the organisation exactly as the V8 move does.
        self.assertEqual(result.org["stats"][self.hire.role_id]["recruited_at_move"], 5)

    def test_without_a_recruit_callback_there_is_no_ceo_arm(self):
        org = OrgState([self.analyst], self.policy)
        sys1 = self.system1()
        loop = self.loop(sys1, org=org, recruiter=None, search_algorithm="ab_mcts")
        loop.ab.rng = MeanRng()
        result = loop.run()
        self.assertNotIn(MOVE_RECRUIT_SPECIALIST, [m.move_type for m in result.trajectory])
        self.assertNotEqual(result.stop_reason, STOP_RESOLVED)
        self.assertEqual(result.stats["recruits"], 0)
        kinds = {a["kind"] for a in result.stats["ab_mcts"]["arms"][self.q.question_id]}
        self.assertNotIn(ARM_GEN_SPECIALIST, kinds)

    def test_a_declined_recruit_lowers_the_ceo_arm(self):
        org = OrgState([self.analyst], self.policy)
        recruiter = RecordingRecruiter([(None, "no candidate"), (self.hire, "second thoughts")])
        sys1 = self.system1()
        # Every move until the hire's probe has dU 0, so the stagnation stop
        # must be wider than the V8 fixture's 8 for the scenario to play out.
        loop = self.loop(sys1, org=org, recruiter=recruiter, search_algorithm="ab_mcts",
                         search_budget_moves=30, max_stagnant_moves=20)
        loop.ab.rng = MeanRng()
        result = loop.run()
        self.assertEqual(result.stats["recruit_declined"], 1)
        self.assertEqual(result.stats["recruits"], 1)
        self.assertEqual(result.stop_reason, STOP_RESOLVED)
        ceo = [a for a in result.stats["ab_mcts"]["arms"][self.q.question_id]
               if a["kind"] == ARM_GEN_SPECIALIST][0]
        self.assertEqual(ceo["rewards"][0], 0.0)
        self.assertEqual(ceo["rewards"][-1], 1.0)
        recruits = [i for i, m in enumerate(result.trajectory) if m.move_type == MOVE_RECRUIT_SPECIALIST]
        self.assertEqual(len(recruits), 2)
        # The cooldown (3 moves) and the no-two-in-a-row rule still hold inside the tree policy.
        self.assertGreaterEqual(recruits[1] - recruits[0], 3)

    def test_the_cooldown_is_waived_when_a_recruit_is_the_only_move_left(self):
        # Same decay as above, but the CEO declines at move 5 and the cooldown
        # is 5 moves. The analyst's GEN arm is drawn twice more (moves 6, 7),
        # comes back empty and is exhausted; at move 8 nothing but a recruit
        # can move the question and the cooldown (8 - 5 < 5) would block it.
        policy = ceo_policy(recruit_bias=0.0, recruit_w_stall=0.0, recruit_w_unmatched=0.0,
                            recruit_w_headcount=0.4, optimistic_prior=0.6, recruit_cooldown_moves=5)
        org = OrgState([self.analyst], policy)
        recruiter = RecordingRecruiter([(None, "no candidate"), (self.hire, "second thoughts")])
        sys1 = self.system1()
        loop = self.loop(sys1, org=org, recruiter=recruiter, search_algorithm="ab_mcts",
                         search_budget_moves=30, max_stagnant_moves=20)
        loop.ab.rng = MeanRng()
        result = loop.run()
        types = [m.move_type for m in result.trajectory]
        self.assertEqual(result.stop_reason, STOP_RESOLVED, types)
        self.assertEqual([c["move_index"] for c in recruiter.calls], [5, 8])
        self.assertEqual(types[4:8], [MOVE_RECRUIT_SPECIALIST, MOVE_PROPOSE_HYPOTHESIS, MOVE_PROPOSE_HYPOTHESIS,
                                      MOVE_RECRUIT_SPECIALIST])
        self.assertLess(8 - 5, policy.recruit_cooldown_moves)      # the cooldown alone would have blocked it
        self.assertEqual(result.stats["ab_mcts"]["counts"]["gen_exhausted_questions"], 1)
        self.assertEqual(sys1.propose_roles[-1], self.hire.role_id)
        self.assertEqual((result.stats["recruit_declined"], result.stats["recruits"]), (1, 1))

    def test_the_seeded_thompson_sampler_recruits_and_resolves(self):
        for seed in range(4):
            self.setUp()
            org = OrgState([self.analyst], self.policy)
            recruiter = RecordingRecruiter([(self.hire, "synthesized by CEO: operators")])
            loop = self.loop(self.system1(), org=org, recruiter=recruiter, search_algorithm="ab_mcts",
                             search_budget_moves=30, max_stagnant_moves=20)
            loop.ab.rng = random.Random(seed)
            result = loop.run()
            self.assertEqual(result.stop_reason, STOP_RESOLVED, seed)
            self.assertEqual(result.stats["recruits"], 1, seed)


# --------------------------------------------------------------------------- #
# The runner's proposer with k = 1
# --------------------------------------------------------------------------- #

class SingleHypothesisAdapterTests(unittest.TestCase):
    """`propose(..., k=1)`: one NEW mechanism, conditioned on the probes that already ran."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_ab_adapter_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runner = HierarchicalCompanyRunner(_genome())
        self.runner.workspace = AgentWorkspace("epi_runner_firm", base_dir=self.tmp)
        self.runner.verification_loop = VerificationLoop(self.runner.workspace)
        self.runner.workspace.write_file("mypkg/__init__.py", "")
        self.runner.workspace.write_file("mypkg/calc.py", RUNNER_BUGGY)
        self.runner._init_required_modules_for_objective(OBJECTIVE)
        self.state = EpistemicState("epi_runner_firm")
        gk = EvidenceGatekeeper(self.runner.workspace, timeout_s=15, isolate=False, stage_reference=False,
                                module_for_tag={"calc": "mypkg/calc.py"})
        gk.reconcile_with_oracle(self.state, ["[calc] FAIL: test_add (t.T.test_add) -> AssertionError: -1 != 5"],
                                 iteration=1)
        self.q = next(iter(self.state.questions.values()))
        p = wrong(0.8, 0)
        self.h = self.state.add_hypothesis(self.q.question_id, p.claim, p.mechanism, p.prior, p.probe_code,
                                           p.prediction, proposed_by="t")
        gk.apply(self.state, self.h, gk.run_experiment(self.state, self.h))   # FALSIFIED: prints ADD 0
        self.agents = self.runner._bind_epistemic_agents(self.runner.epistemic_policy)
        self.propose = self.runner._propose_hypotheses_adapter(self.agents["hypothesis"], OBJECTIVE,
                                                               self.runner.epistemic_policy)

    def test_k_one_asks_for_one_new_mechanism_with_the_probe_record(self):
        self.assertEqual(self.h.status, FALSIFIED)
        llm = ScriptedCEO(json.dumps({"packet": "HYPOTHESIS_SET", "question_id": self.q.question_id, "hypotheses": [
            {"claim": "add subtracts because the operator is minus", "mechanism": "operator bug", "prior": 0.7,
             "probe_lines": ["from mypkg.calc import add", "print('ADD', add(2, 2))"],
             "prediction": {"expect_exit_code": 0, "expect_stdout_contains": "ADD 0"}},
            {"claim": "a second one that must be dropped", "mechanism": "extra", "prior": 0.2,
             "probe_lines": ["print('x')"], "prediction": {"expect_exit_code": 0}},
        ]}))
        with mock.patch("hae.runtime.company.call_llm", llm), redirect_stdout(io.StringIO()):
            proposals = self.propose(self.q, self.state, 1)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].mechanism, "operator bug")
        prompt = llm.calls[0]["prompt"]
        self.assertIn("PROPOSE 1 NEW FALSIFIABLE HYPOTHESIS", prompt)
        self.assertNotIn("MUTUALLY-EXCLUSIVE", prompt)
        self.assertIn("The mechanism must be NEW", prompt)
        self.assertIn("RULED OUT (falsified; do NOT propose again):", prompt)
        self.assertIn(self.h.claim, prompt)
        self.assertIn("PROBE OUTCOMES ON THIS QUESTION", prompt)
        self.assertIn(f"{self.h.hypothesis_id} FALSIFIED", prompt)
        self.assertIn("expect_stdout_contains='ADD 4'", prompt)
        self.assertIn("observed: exit 0, ADD 0", prompt)
        self.assertEqual(llm.calls[0]["schema"], "HypothesisSetPacket")

    def test_k_two_is_the_v8_prompt_without_the_probe_record(self):
        llm = ScriptedCEO(json.dumps({"packet": "HYPOTHESIS_SET", "question_id": self.q.question_id, "hypotheses": []}))
        with mock.patch("hae.runtime.company.call_llm", llm), redirect_stdout(io.StringIO()):
            self.propose(self.q, self.state, 2)
            self.propose(self.q, self.state, 0)      # anything other than exactly 1 is the old max(2, k)
        for call in llm.calls:
            prompt = call["prompt"]
            self.assertIn("PROPOSE 2 MUTUALLY-EXCLUSIVE HYPOTHESES", prompt)
            self.assertIn("Hypotheses must be mutually exclusive, and at least one must be a mechanism you consider UNLIKELY.",
                          prompt)
            self.assertNotIn("PROBE OUTCOMES ON THIS QUESTION", prompt)
            self.assertNotIn("must be NEW", prompt)

    def test_probe_record_covers_refused_probes_and_is_empty_without_probes(self):
        self.assertEqual(HierarchicalCompanyRunner._probe_outcomes_block(EpistemicState("x"), "q1"), "")
        gk = EvidenceGatekeeper(self.runner.workspace, timeout_s=15, isolate=False, stage_reference=False,
                                module_for_tag={"calc": "mypkg/calc.py"})
        p = refused(0.6, 1)
        h2 = self.state.add_hypothesis(self.q.question_id, p.claim, p.mechanism, p.prior, p.probe_code,
                                       p.prediction, proposed_by="t")
        gk.apply(self.state, h2, gk.run_experiment(self.state, h2))
        block = HierarchicalCompanyRunner._probe_outcomes_block(self.state, self.q.question_id)
        lines = block.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn(f"{h2.hypothesis_id} UNTESTABLE", lines[2])
        self.assertIn("probe refused:", lines[2])
        self.assertEqual(HierarchicalCompanyRunner._probe_outcomes_block(self.state, "q_other"), "")


if __name__ == "__main__":
    unittest.main()
