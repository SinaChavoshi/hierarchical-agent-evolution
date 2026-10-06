"""The Gen 16 cohort is a replicated within-genome comparison, and the test pins that down.

What must hold for the comparison to mean anything:
  * every replica is byte-identical to its Gen 14 source genome except for
    `company_id`, `epistemic_policy` and one appended `mutation_history` line
    -- the gene is the *only* treatment;
  * the spec's selection is applied exactly (8 non-converged lineages x 3
    seeds + 2 converged controls x 1 seed = 26 firm-runs), in seed-major
    order so the first ten Job indices cover every lineage once;
  * the committed `configs/generation_16_population.json` is what the spec
    produces today -- a stale population would silently test the wrong thing;
  * malformed overrides refuse the way the breeder refuses.
"""

import copy
import json
import os
import unittest

from hae.genome.schema import CompanyGenome
from scripts.make_gen16_cohort_population import (
    REPLICA_FIELDS, CohortError, build, load_spec, validated_policy,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(REPO, "configs", "generations", "gen16_cohort.json")
COMMITTED = os.path.join(REPO, "configs", "generation_16_population.json")


class Gen16CohortTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.spec = load_spec(SPEC)
        cls.envelope = build(cls.spec, repo_root=REPO)
        with open(os.path.join(REPO, cls.spec["source_population"])) as fh:
            raw = json.load(fh)
        cls.source = {g["company_id"]: g for g in (raw["population"] if isinstance(raw, dict) else raw)}

    def test_roster_matches_the_spec(self):
        design = self.envelope["cohort_design"]
        sel = self.spec["selection"]
        roles = [info["role"] for info in design["lineages"].values()]
        self.assertEqual(roles.count("non_converged"), sel["non_converged"])
        self.assertEqual(roles.count("control"), sel["converged_controls"])
        expected_runs = (sel["non_converged"] * sel["non_converged_seeds"]
                         + sel["converged_controls"] * sel["converged_control_seeds"])
        self.assertEqual(design["firm_runs"], expected_runs)
        self.assertEqual(len(self.envelope["population"]), 26)
        for info in design["lineages"].values():
            self.assertEqual(len(info["replicas"]), info["seeds"])
            self.assertTrue(info["gen15_trajectory"], info)
            if info["role"] == "control":
                self.assertEqual(info["gen15_trajectory"][-1], 50)
            else:
                self.assertLess(info["gen15_trajectory"][-1], 50)

    def test_replicas_differ_from_their_source_only_in_the_treatment(self):
        for genome in self.envelope["population"]:
            lineage, seed = genome["company_id"].rsplit("__s", 1)
            src = self.source[lineage]
            for key in set(src) | set(genome):
                if key in REPLICA_FIELDS:
                    continue
                self.assertEqual(genome.get(key), src.get(key), f"{genome['company_id']}.{key} drifted from {lineage}")
            # The appended history line is the only history change.
            self.assertEqual(genome["mutation_history"][:-1], list(src.get("mutation_history") or []))
            self.assertIn(f"seed {seed}/", genome["mutation_history"][-1])
            policy = CompanyGenome.from_dict(genome).epistemic_policy
            self.assertTrue(policy.enabled)
            for field, value in self.spec["epistemic_policy"].items():
                self.assertEqual(getattr(policy, field), value, field)
            # Source genomes carried no (or a disabled) gene: the treatment is additive.
            self.assertFalse((src.get("epistemic_policy") or {}).get("enabled", False), lineage)

    def test_population_is_seed_major(self):
        ids = [g["company_id"] for g in self.envelope["population"]]
        lineages = list(self.envelope["cohort_design"]["lineages"])
        first_wave = ids[:len(lineages)]
        self.assertEqual([i.rsplit("__s", 1)[0] for i in first_wave], lineages)
        self.assertTrue(all(i.endswith("__s1") for i in first_wave))
        seeds = [int(i.rsplit("__s", 1)[1]) for i in ids]
        self.assertEqual(seeds, sorted(seeds))            # s1 block, then s2, then s3
        self.assertEqual(len(set(ids)), len(ids))

    def test_committed_population_is_what_the_spec_produces(self):
        with open(COMMITTED) as fh:
            committed = json.load(fh)
        self.assertEqual(committed["population"], self.envelope["population"])
        self.assertEqual(committed["cohort_design"], self.envelope["cohort_design"])

    def test_malformed_overrides_refuse(self):
        genome = next(iter(self.source.values()))
        with self.assertRaises(CohortError):
            validated_policy(genome, {"enabled": True, "not_a_field": 1})
        with self.assertRaises(CohortError):
            validated_policy(genome, {"enabled": True, "frontier_size": 99})
        spec = copy.deepcopy(self.spec)
        spec["selection"]["non_converged"] = 50
        with self.assertRaises(CohortError):
            build(spec, repo_root=REPO)


if __name__ == "__main__":
    unittest.main()
