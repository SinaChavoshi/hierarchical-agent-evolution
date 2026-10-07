"""The run-3 population differs from the run-1/2 population only in `epistemic_policy.extra`."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hae.genome.schema import CompanyGenome  # noqa: E402
from scripts.make_gen16_r3_population import SHADOW_EXTRA, build  # noqa: E402

SOURCE = os.path.join(os.path.dirname(__file__), "..", "configs", "generation_16_population.json")


class MakeGen16R3PopulationTest(unittest.TestCase):
    def test_only_extra_differs_and_shadow_mode_is_off_policy(self):
        with open(SOURCE, "r", encoding="utf-8") as fh:
            source = json.load(fh)
        out = build(source)
        self.assertEqual(len(out["population"]), len(source["population"]))
        for before, after in zip(source["population"], out["population"]):
            a, b = dict(before), dict(after)
            pa, pb = dict(a.pop("epistemic_policy")), dict(b.pop("epistemic_policy"))
            self.assertEqual(a, b)  # byte-level: everything but the policy is identical
            extra = {k: pb.pop(k) for k in SHADOW_EXTRA}  # flat keys, as `_Model` serialises `extra`
            self.assertEqual(pa, pb)  # and the policy differs only in those keys
            self.assertEqual(extra, SHADOW_EXTRA)
            self.assertEqual(extra["prior_head_weight"], 0.0)
            self.assertFalse(extra["value_head_live"])
            # and they land in `EpistemicPolicyGene.extra` on load
            genome = CompanyGenome.from_dict(after)
            self.assertEqual({k: genome.epistemic_policy.extra.get(k) for k in SHADOW_EXTRA}, SHADOW_EXTRA)
        self.assertEqual(out["cohort_design"]["run"], 3)
        # the source is not mutated
        self.assertNotIn("prior_head_path", source["population"][0]["epistemic_policy"])


if __name__ == "__main__":
    unittest.main()
