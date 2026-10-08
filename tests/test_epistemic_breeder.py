"""V6: the `epistemic_policy` gene must survive breeding and the breeder must
rank V6 scorecards by the same audit-weighted composite the worker scored with.

Two failure modes are locked down here. First, a gene that silently resets to
its default on crossover or morphogenesis would make every V6 generation a
re-seed, so nothing about the search policy could evolve. Second, a generation
config that says `"epistemic_policy": {"enabled": true}` and is ignored would be
a generation that reports an experiment it never ran -- the exact class of
error the breeder's unknown-key refusal already exists to prevent.
"""

import json
import os
import shutil
import tempfile
import unittest

from hae.epistemic.audit import build_epistemic_audit
from hae.epistemic.ledger import EpistemicState
from hae.evaluation.judge import composite_score
from hae.genome.schema import EPISTEMIC_POLICY_BOUNDS, CompanyGenome
from hae.orchestration.breeder import (
    BreedingError,
    Breeder,
    GenerationSpec,
    rank_scorecards,
)

GENOME = {
    "company_id": "parent",
    "generation": 1,
    "ceo": {"role": "CEO", "goal": "Lead", "backstory": "Exec",
            "temperature": 0.4, "model_tier": "executive"},
    "departments": [
        {"dept_id": "dept_systems_eng", "name": "Engineering", "mandate": "Build",
         "manager": {"role": "Eng Manager", "model_tier": "worker"},
         "agents": [{"role": "Engineer", "model_tier": "worker", "tools_enabled": True}]},
        {"dept_id": "dept_qa_redteam", "name": "QA", "mandate": "Verify",
         "manager": {"role": "QA Manager", "model_tier": "worker"},
         "agents": [{"role": "Tester", "model_tier": "worker"}]},
    ],
}

ALL_PASS = {"syntax": "passed", "build": "passed", "smoke": "passed",
            "tests": "passed", "telemetry": "passed"}


def scorecard(company_id, gates=ALL_PASS, judged=90.0, policy=None, audit=None):
    genome = dict(GENOME, company_id=company_id)
    if policy is not None:
        genome["epistemic_policy"] = policy
    card = {
        "company_id": company_id,
        "fitness_score": judged,
        "strategic_depth": judged,
        "technical_feasibility": judged,
        "cross_functional_coherence": judged,
        "risk_mitigation": judged,
        "actionability": judged,
        "evaluation_failed": False,
        "verification": {"gate_status": gates},
        "genome": genome,
    }
    if audit is not None:
        card["epistemic_audit"] = audit
    return card


PARENT_A = {"enabled": True, "search_budget_moves": 20, "branching_k": 2,
            "c_puct": 1.0, "low_prior_quota": 0.2, "value_alpha": 0.5}
PARENT_B = {"enabled": True, "search_budget_moves": 60, "branching_k": 5,
            "c_puct": 2.0, "low_prior_quota": 0.4, "value_alpha": 0.7}


class _BreedingCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hae_epi_breed_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cards = os.path.join(self.root, "cards")
        os.makedirs(self.cards)

    def write_cards(self, *cards):
        for card in cards:
            with open(os.path.join(self.cards, f"{card['company_id']}.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(card, fh)

    def spec(self, **overrides):
        base = dict(generation=16, name="V6", parent_scorecards="cards", survivors=2,
                    elite=1, crossover=1, pareto=0, mutant=1, objective="repair")
        base.update(overrides)
        return GenerationSpec(**base)


class TestInheritance(_BreedingCase):

    def setUp(self):
        super().setUp()
        self.write_cards(scorecard("pa", judged=90.0, policy=PARENT_A),
                         scorecard("pb", judged=85.0, policy=PARENT_B))

    def _by_kind(self, population):
        return {g.company_id.split("_")[2]: g for g in population}

    def test_every_child_carries_an_enabled_gene(self):
        population = Breeder(self.spec(), repo_root=self.root).breed()
        self.assertEqual(len(population), 3)
        for genome in population:
            self.assertTrue(genome.epistemic_policy.enabled, genome.company_id)

    def test_crossover_blends_the_parents_rather_than_resetting(self):
        child = self._by_kind(Breeder(self.spec(), repo_root=self.root).breed())["crossover"]
        policy = child.epistemic_policy
        # Every numeric field lies within the parents' span; a reset to the
        # default (40 moves, k=3, c_puct=1.4) would also lie inside the span
        # for some fields, so check the ones where the default is outside it.
        self.assertTrue(20 <= policy.search_budget_moves <= 60)
        self.assertTrue(0.2 <= policy.low_prior_quota <= 0.4)
        self.assertTrue(0.5 <= policy.value_alpha <= 0.7)
        self.assertNotEqual(policy.to_dict(), CompanyGenome.from_dict(GENOME).epistemic_policy.to_dict())

    def test_mutant_is_a_bounded_perturbation_of_its_parent(self):
        child = self._by_kind(Breeder(self.spec(), repo_root=self.root).breed())["mutant"]
        policy = child.epistemic_policy.to_dict()
        # Read the bounds check off the gene: `to_dict` omits V9 fields that sit
        # at their defaults so archived populations stay byte-identical.
        for name, (lo, hi, _is_int) in EPISTEMIC_POLICY_BOUNDS.items():
            value = getattr(child.epistemic_policy, name)
            self.assertTrue(lo <= value <= hi, f"{name}={value} outside [{lo}, {hi}]")
        # The mutant descends from the top-ranked parent (pa). Its budget
        # should sit near 20, not near the 40 default or pb's 60.
        self.assertLess(abs(policy["search_budget_moves"] - PARENT_A["search_budget_moves"]), 12)

    def test_breeding_is_deterministic_from_the_spec(self):
        first = Breeder(self.spec(), repo_root=self.root).breed()
        second = Breeder(self.spec(), repo_root=self.root).breed()
        self.assertEqual([g.epistemic_policy.to_dict() for g in first],
                         [g.epistemic_policy.to_dict() for g in second])

    def test_parents_are_not_mutated(self):
        breeder = Breeder(self.spec(), repo_root=self.root)
        parents = breeder.survivors()
        before = [p.genome.epistemic_policy.to_dict() for p in parents]
        breeder.breed()
        self.assertEqual([p.genome.epistemic_policy.to_dict() for p in parents], before)

    def test_elite_clone_keeps_the_parent_policy_exactly(self):
        elite = self._by_kind(Breeder(self.spec(), repo_root=self.root).breed())["elite"]
        for key, value in PARENT_A.items():
            self.assertEqual(getattr(elite.epistemic_policy, key), value)

    def test_written_population_round_trips_the_gene(self):
        breeder = Breeder(self.spec(), repo_root=self.root)
        population = breeder.breed()
        path = breeder.write(population)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        for entry, genome in zip(payload["population"], population):
            loaded = CompanyGenome.from_dict(entry)
            self.assertEqual(loaded.epistemic_policy.to_dict(), genome.epistemic_policy.to_dict())


class TestLegacyParentsStayV5(_BreedingCase):
    """Gen 15 scorecards have no gene. Their children must not quietly become V6."""

    def setUp(self):
        super().setUp()
        self.write_cards(scorecard("old_a", judged=90.0), scorecard("old_b", judged=85.0))

    def test_children_of_legacy_parents_are_disabled(self):
        population = Breeder(self.spec(), repo_root=self.root).breed()
        for genome in population:
            self.assertFalse(genome.epistemic_policy.enabled, genome.company_id)
            self.assertNotIn("V6 epistemic search enabled",
                             " ".join(genome.mutation_history))

    def test_operators_never_flip_enabled_on_their_own(self):
        # Many slots, no override: not a single child may switch itself on.
        spec = self.spec(elite=0, crossover=6, pareto=0, mutant=6)
        population = Breeder(spec, repo_root=self.root).breed()
        self.assertEqual(len(population), 12)
        self.assertFalse(any(g.epistemic_policy.enabled for g in population))


class TestSpecOverrides(_BreedingCase):

    def setUp(self):
        super().setUp()
        self.write_cards(scorecard("old_a", judged=90.0), scorecard("old_b", judged=85.0))

    def test_generation_can_switch_the_search_on_for_the_whole_cohort(self):
        spec = self.spec(epistemic_policy={"enabled": True, "search_budget_moves": 12})
        population = Breeder(spec, repo_root=self.root).breed()
        for genome in population:
            self.assertTrue(genome.epistemic_policy.enabled)
            self.assertEqual(genome.epistemic_policy.search_budget_moves, 12)
            self.assertIn("V6 epistemic search enabled", genome.mutation_history[-1])

    def test_override_leaves_unlisted_fields_to_inheritance(self):
        self.write_cards(scorecard("pa", judged=95.0, policy=PARENT_A))
        spec = self.spec(survivors=1, epistemic_policy={"search_budget_moves": 33})
        population = Breeder(spec, repo_root=self.root).breed()
        elite = next(g for g in population if "elite" in g.company_id)
        self.assertEqual(elite.epistemic_policy.search_budget_moves, 33)
        self.assertEqual(elite.epistemic_policy.branching_k, PARENT_A["branching_k"])
        self.assertTrue(elite.epistemic_policy.enabled)

    def test_unknown_override_field_is_refused(self):
        spec = self.spec(epistemic_policy={"enabled": True, "serach_budget_moves": 12})
        with self.assertRaises(BreedingError) as ctx:
            Breeder(spec, repo_root=self.root).breed()
        self.assertIn("serach_budget_moves", str(ctx.exception))

    def test_out_of_range_override_is_refused(self):
        spec = self.spec(epistemic_policy={"enabled": True, "low_prior_quota": 0.95})
        with self.assertRaises(BreedingError):
            Breeder(spec, repo_root=self.root).breed()

    def test_seeded_generation_honours_the_override(self):
        template = os.path.join(self.root, "seed.json")
        with open(template, "w", encoding="utf-8") as fh:
            json.dump(GENOME, fh)
        spec = GenerationSpec(generation=1, name="Seed", seed_template="seed.json",
                              elite=1, crossover=0, pareto=0, mutant=2, objective="x",
                              epistemic_policy={"enabled": True})
        population = Breeder(spec, repo_root=self.root).seed_population()
        self.assertEqual(len(population), 3)
        self.assertTrue(all(g.epistemic_policy.enabled for g in population))

    def test_spec_file_accepts_the_key(self):
        path = os.path.join(self.root, "gen16.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"generation": 16, "name": "V6", "objective": "x",
                       "epistemic_policy": {"enabled": True}}, fh)
        self.assertEqual(GenerationSpec.load(path).epistemic_policy, {"enabled": True})


class TestRankingUsesTheAudit(_BreedingCase):

    @staticmethod
    def _audit(certified_fraction):
        from hae.epistemic.ledger import Evidence, GatekeeperAuthority
        state = EpistemicState("firm")
        for i in range(4):
            state.add_question(f"why does test_{i} fail?", "hae/x.py", 1.0,
                               source_failure_key=f"k{i}", source_failure=f"FAIL: test_{i}")
        token = GatekeeperAuthority()
        qids = list(state.questions)
        for n, qid in enumerate(qids[: int(certified_fraction * 4)]):
            ev = Evidence(evidence_id=f"ev{n}", kind="oracle", question_id=qid, exit_code=0,
                          matched_prediction=True, detail="oracle no longer reports it")
            state.record_evidence(ev, token)
            state.certify(qid, "", "oracle confirmed the repair", [ev.evidence_id], "oracle", token)
        return build_epistemic_audit(state, {"moves_used": 8, "proposed_total": 6, "tabu_rejections": 0},
                                     budget_moves=16, token_usage=0)

    def test_legacy_scorecards_rank_exactly_as_before(self):
        self.write_cards(scorecard("talker", gates={k: "failed" for k in ALL_PASS}, judged=99.0),
                         scorecard("builder", judged=80.0))
        ranked = rank_scorecards(self.cards)
        self.assertEqual([r.company_id for r in ranked], ["builder", "talker"])
        judged = {d: 80.0 for d in ("strategic_depth", "technical_feasibility",
                                    "cross_functional_coherence", "risk_mitigation",
                                    "actionability_and_synthesis")}
        self.assertEqual(ranked[0].rubric_score, composite_score(judged, 100.0))

    def test_v6_scorecards_rank_by_the_audit_composite(self):
        strong = self._audit(1.0)
        weak = self._audit(0.0)
        # Identical execution and prose; only the reasoning trail differs.
        self.write_cards(scorecard("honest", judged=80.0, policy=PARENT_A, audit=strong),
                         scorecard("lucky", judged=80.0, policy=PARENT_A, audit=weak))
        ranked = rank_scorecards(self.cards)
        self.assertEqual([r.company_id for r in ranked], ["honest", "lucky"])
        self.assertEqual(ranked[0].rubric_score, composite_score({}, 100.0, epistemic_audit=strong))
        self.assertGreater(ranked[0].rubric_score, ranked[1].rubric_score)

    def test_an_empty_audit_falls_back_to_the_v5_composite(self):
        card = scorecard("blank", judged=80.0, policy=PARENT_A, audit={})
        self.write_cards(card)
        ranked = rank_scorecards(self.cards)
        judged = {d: 80.0 for d in ("strategic_depth", "technical_feasibility",
                                    "cross_functional_coherence", "risk_mitigation",
                                    "actionability_and_synthesis")}
        self.assertEqual(ranked[0].rubric_score, composite_score(judged, 100.0))


if __name__ == "__main__":
    unittest.main()
