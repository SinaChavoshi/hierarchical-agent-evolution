"""The epistemic policy gene: bounded, heritable, and off by default."""

import json
import random
import unittest

from hae.epistemic.genes import (
    NUMERIC_FIELDS, ROLE_VOCABULARY, crossover_epistemic_policy, mutate_epistemic_policy,
    policy_distance,
)
from hae.genome.schema import (
    DEFAULT_EPISTEMIC_ROLE_BINDINGS, EPISTEMIC_POLICY_BOUNDS, CompanyGenome,
    EpistemicPolicyGene, FitnessScore, GenomeValidationError,
)


def _genome(**kw):
    base = dict(
        company_id="g", ceo={"role": "CEO", "model_tier": "executive"},
        departments=[{"dept_id": "dept_systems_eng", "name": "Systems Engineering",
                      "manager": {"role": "Lead"}, "agents": [{"role": "Engineer", "tools_enabled": True}]}],
    )
    base.update(kw)
    return CompanyGenome(**base)


class GeneDefaultsAndBoundsTests(unittest.TestCase):

    def test_disabled_by_default(self):
        gene = EpistemicPolicyGene()
        self.assertFalse(gene.enabled)
        self.assertEqual(gene.role_bindings, DEFAULT_EPISTEMIC_ROLE_BINDINGS)

    def test_bounds_are_enforced(self):
        for name, (lo, hi, _) in EPISTEMIC_POLICY_BOUNDS.items():
            with self.assertRaises(GenomeValidationError, msg=name):
                EpistemicPolicyGene(**{name: hi + 1})
            with self.assertRaises(GenomeValidationError, msg=name):
                EpistemicPolicyGene(**{name: lo - 1})
            EpistemicPolicyGene(**{name: lo})
            EpistemicPolicyGene(**{name: hi})

    def test_integers_are_coerced(self):
        gene = EpistemicPolicyGene(search_budget_moves="12", branching_k=3.4)
        self.assertEqual(gene.search_budget_moves, 12)
        self.assertEqual(gene.branching_k, 3)

    def test_non_numeric_rejected(self):
        with self.assertRaises(GenomeValidationError):
            EpistemicPolicyGene(c_puct="lots")

    def test_unknown_role_binding_rejected_and_partial_bindings_filled(self):
        with self.assertRaises(GenomeValidationError):
            EpistemicPolicyGene(role_bindings={"dance": "qa"})
        gene = EpistemicPolicyGene(role_bindings={"synthesis": "platform"})
        self.assertEqual(gene.role_bindings["synthesis"], "platform")
        self.assertEqual(gene.role_bindings["question"], DEFAULT_EPISTEMIC_ROLE_BINDINGS["question"])

    def test_round_trip(self):
        gene = EpistemicPolicyGene(enabled=True, c_puct=2.5, role_bindings={"experiment": "redteam"})
        clone = EpistemicPolicyGene.from_dict(json.loads(gene.to_json()))
        self.assertEqual(clone.to_dict(), gene.to_dict())


class CompanyGenomeEmbeddingTests(unittest.TestCase):

    def test_old_genome_without_key_gets_disabled_policy(self):
        g = _genome()
        self.assertIsInstance(g.epistemic_policy, EpistemicPolicyGene)
        self.assertFalse(g.epistemic_policy.enabled)

    def test_mapping_is_coerced_and_round_trips(self):
        g = _genome(epistemic_policy={"enabled": True, "search_budget_moves": 60})
        self.assertTrue(g.epistemic_policy.enabled)
        self.assertEqual(g.epistemic_policy.search_budget_moves, 60)
        again = CompanyGenome.from_dict(json.loads(g.to_json()))
        self.assertEqual(again.epistemic_policy.to_dict(), g.epistemic_policy.to_dict())

    def test_invalid_policy_fails_genome_load(self):
        with self.assertRaises(GenomeValidationError):
            _genome(epistemic_policy={"branching_k": 99})
        with self.assertRaises(GenomeValidationError):
            _genome(epistemic_policy=42)

    def test_copy_is_deep(self):
        g = _genome(epistemic_policy={"enabled": True})
        c = g.copy()
        c.epistemic_policy.c_puct = 3.0
        self.assertNotEqual(g.epistemic_policy.c_puct, 3.0)


class OperatorTests(unittest.TestCase):

    def test_mutation_stays_in_bounds_and_changes_something(self):
        rng = random.Random(3)
        parent = EpistemicPolicyGene(enabled=True)
        changed = 0
        for _ in range(50):
            child = mutate_epistemic_policy(parent, rng)
            for name, (lo, hi, _) in EPISTEMIC_POLICY_BOUNDS.items():
                self.assertGreaterEqual(getattr(child, name), lo)
                self.assertLessEqual(getattr(child, name), hi)
            self.assertTrue(child.enabled)
            changed += int(policy_distance(parent, child) > 0 or child.role_bindings != parent.role_bindings)
        self.assertGreater(changed, 40)

    def test_mutation_never_flips_enabled(self):
        rng = random.Random(1)
        off = EpistemicPolicyGene(enabled=False)
        self.assertTrue(all(not mutate_epistemic_policy(off, rng).enabled for _ in range(30)))

    def test_mutation_is_reproducible_from_seed(self):
        a = mutate_epistemic_policy(EpistemicPolicyGene(), random.Random(11))
        b = mutate_epistemic_policy(EpistemicPolicyGene(), random.Random(11))
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_mutation_from_none_uses_defaults(self):
        child = mutate_epistemic_policy(None, random.Random(2))
        self.assertIsInstance(child, EpistemicPolicyGene)

    def test_rebinding_uses_vocabulary(self):
        rng = random.Random(5)
        seen = set()
        for _ in range(200):
            child = mutate_epistemic_policy(EpistemicPolicyGene(), rng, rebind_probability=1.0)
            seen.update(child.role_bindings.values())
        self.assertTrue(seen <= set(ROLE_VOCABULARY) | set(DEFAULT_EPISTEMIC_ROLE_BINDINGS.values()))
        self.assertGreater(len(seen), 4)

    def test_crossover_takes_every_field_from_a_parent(self):
        a = EpistemicPolicyGene(enabled=True, c_puct=0.5, branching_k=2, search_budget_moves=10,
                                role_bindings={"synthesis": "platform"})
        b = EpistemicPolicyGene(enabled=False, c_puct=4.0, branching_k=6, search_budget_moves=300,
                                role_bindings={"synthesis": "formal"})
        rng = random.Random(9)
        for _ in range(20):
            child = crossover_epistemic_policy(a, b, rng)
            for name in NUMERIC_FIELDS:
                self.assertIn(getattr(child, name), (getattr(a, name), getattr(b, name)), name)
            self.assertIn(child.role_bindings["synthesis"], ("platform", "formal"))
            self.assertTrue(child.enabled)  # inherited from parent a

    def test_distance_zero_for_identical(self):
        self.assertEqual(policy_distance(EpistemicPolicyGene(), EpistemicPolicyGene()), 0.0)


class FitnessScoreFieldsTests(unittest.TestCase):

    def test_new_fields_default_and_validate(self):
        f = FitnessScore()
        self.assertFalse(f.epistemic_evaluable)
        self.assertEqual(f.epistemic_integrity, 0.0)
        with self.assertRaises(GenomeValidationError):
            FitnessScore(epistemic_integrity=140)
        with self.assertRaises(GenomeValidationError):
            FitnessScore(epistemic_efficiency=-1)

    def test_old_scorecards_still_load(self):
        f = FitnessScore.from_dict({"strategic_depth": 80, "fitness_score": 50})
        self.assertEqual(f.epistemic_integrity, 0.0)


if __name__ == "__main__":
    unittest.main()
