"""The V8 smoke population is four run-3 lineages with only the organisation genes added."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hae.genome.role_seeds import seed_role_library  # noqa: E402
from hae.genome.schema import CompanyGenome  # noqa: E402
from scripts.make_v8_population import GENERATION, SEED_FLAVOUR, SMOKE_SLOTS, build  # noqa: E402

SOURCE = os.path.join(os.path.dirname(__file__), "..", "configs", "generation_16_r3_population.json")
OUT = os.path.join(os.path.dirname(__file__), "..", "configs", "generation_v8_smoke_population.json")
ADDED = {"ceo_policy", "role_library"}
RELABELLED = {"company_id", "generation", "parent_ids", "mutation_history"}


class MakeV8PopulationTest(unittest.TestCase):

    def setUp(self):
        with open(SOURCE, "r", encoding="utf-8") as fh:
            self.source = json.load(fh)
        self.out = build(self.source)

    def test_four_distinct_lineages_differ_from_run3_only_in_the_organisation_genes(self):
        firms = self.out["population"]
        self.assertEqual(len(firms), 4)
        by_id = {f["company_id"]: f for f in self.source["population"]}
        seed_ids = [r.role_id for r in seed_role_library(SEED_FLAVOUR)]
        self.assertEqual(len(set(seed_ids)), 8)
        lambdas, cs = set(), set()
        for firm, (src_id, lam, c) in zip(firms, SMOKE_SLOTS):
            before, after = dict(by_id[src_id]), dict(firm)
            self.assertEqual(set(after) - set(before), ADDED)
            for key in RELABELLED | ADDED:
                before.pop(key, None)
                after.pop(key, None)
            self.assertEqual(before, after, src_id)  # everything else is the run-3 genome, dict for dict
            self.assertEqual(firm["parent_ids"], [src_id])
            self.assertEqual(firm["generation"], GENERATION)
            self.assertTrue(firm["ceo_policy"]["enabled"])
            self.assertEqual(firm["ceo_policy"]["headcount_lambda"], lam)
            self.assertEqual(firm["ceo_policy"]["exploration_c"], c)
            self.assertEqual([r["role_id"] for r in firm["role_library"]], seed_ids)
            self.assertEqual(firm["mutation_history"][:-1], by_id[src_id]["mutation_history"])
            self.assertIn("V8 smoke cohort", firm["mutation_history"][-1])
            # The run-3 epistemic settings are untouched (enabled, 60 moves, frontier 3, shadow heads).
            policy = firm["epistemic_policy"]
            self.assertTrue(policy["enabled"])
            self.assertEqual((policy["search_budget_moves"], policy["frontier_size"]), (60, 3))
            self.assertEqual(policy["prior_head_weight"], 0.0)
            self.assertFalse(policy["value_head_live"])
            lambdas.add(lam)
            cs.add(c)
        self.assertEqual(len({f["company_id"] for f in firms}), 4)
        self.assertEqual(len({f["parent_ids"][0].split("__")[0] for f in firms}), 4)  # distinct lineages
        self.assertEqual(len(lambdas), 4)
        self.assertEqual(len(cs), 4)
        # The source is not mutated.
        self.assertNotIn("ceo_policy", self.source["population"][0])

    def test_loads_round_trips_and_is_deterministic(self):
        for firm in self.out["population"]:
            genome = CompanyGenome.from_dict(firm)
            self.assertTrue(genome.org_enabled)
            self.assertEqual(len(genome.role_library), 8)
            self.assertTrue(any(r.can("probe") for r in genome.role_library))
            self.assertTrue(any(r.can("synthesis") for r in genome.role_library))
            self.assertEqual(genome.to_dict(), firm)
        self.assertEqual(build(self.source), self.out)

    def test_checked_in_file_matches_the_builder(self):
        with open(OUT, "r", encoding="utf-8") as fh:
            checked_in = json.load(fh)
        self.assertEqual(checked_in, self.out)


if __name__ == "__main__":
    unittest.main()
