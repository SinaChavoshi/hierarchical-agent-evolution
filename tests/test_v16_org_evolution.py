"""V16 Phase 3: evolvable organization (genome fields, role statistics, org operators, evolve loop)."""

from __future__ import annotations

import json
import os
import random
import tempfile
import unittest

from hae.evolution import org_evolution as orgevo
from hae.evolution.evolve import EvolutionConfig, EvolutionRun
from hae.evolution.genome import HarnessGenome, TaskResult
from hae.genome.schema import CEOPolicyGene, RoleAllele

LAYERS = ("recon", "architect", "executor", "refiner", "falsifier")


def library():
    roles = [RoleAllele(name=f"G-{l}", layer=l, is_generalist=True).to_dict() for l in LAYERS]
    roles += [RoleAllele(name=f"S-{l}", layer=l, goal="g", playbook=["a", "b", "c"], domain_tags=["x"]).to_dict()
              for l in ("executor", "refiner", "falsifier")]
    return roles


class GenomeOrgTest(unittest.TestCase):
    def test_v15_hash_unchanged_and_org_roundtrip(self):
        g = HarnessGenome(genome_id="a", knobs={}, patch="p")
        import hashlib
        legacy = hashlib.sha256(json.dumps({"knobs": g.knobs, "patch": "p"}, sort_keys=True).encode()).hexdigest()[:12]
        self.assertEqual(g.content_hash, legacy)
        o = HarnessGenome(genome_id="b", patch="p", org_mode="dynamic", roles=library(),
                          ceo_policy=CEOPolicyGene(enabled=True).to_dict())
        self.assertNotEqual(o.content_hash, g.content_hash)
        back = HarnessGenome.from_dict(o.to_dict())
        self.assertEqual((back.org_mode, back.roles, back.ceo_policy), (o.org_mode, o.roles, o.ceo_policy))
        # Statistics do not change identity.
        o2 = HarnessGenome.from_dict(o.to_dict())
        o2.roles[5]["uses"] = 9
        self.assertEqual(o2.content_hash, o.content_hash)
        self.assertEqual(HarnessGenome(genome_id="c", org_mode="weird").org_mode, "off")


class StatsTest(unittest.TestCase):
    def test_aggregate_and_apply(self):
        lib = library()
        ex = lib[5]["role_id"]
        fals = lib[4]["role_id"]
        recs = [{"resolved": True, "org": {"team": {"executor": {"role_id": ex}, "falsifier": {"role_id": fals}},
                                         "credits": {ex: {"episodes": 2, "sum_delta_v": 0.6}},
                                         "episodes": [{"falsifier_ok": True, "falsifier_role_id": fals}]}},
                {"resolved": False, "org": {"team": {"executor": {"role_id": ex}, "falsifier": {"role_id": fals}},
                                          "credits": {ex: {"episodes": 1, "sum_delta_v": -0.3}},
                                          "episodes": [{"falsifier_ok": True, "falsifier_role_id": fals}]}}]
        st = orgevo.aggregate_role_stats(recs)
        self.assertEqual(st[ex]["episodes"], 3)
        self.assertAlmostEqual(st[ex]["sum_delta_v"], 0.3)
        self.assertEqual(st[ex]["tasks"], 2)
        self.assertEqual(st[ex]["resolved"], 1)
        self.assertAlmostEqual(st[fals]["sum_delta_v"], 0.0)  # +0.1 then -0.1
        out = orgevo.apply_role_stats(lib, st)
        r = next(x for x in out if x["role_id"] == ex)
        self.assertEqual(r["uses"], 3)
        self.assertAlmostEqual(r["mean_delta_u"], 0.1)
        digest = orgevo.org_digest(out, st, recs)
        self.assertIn("1/2 training issues resolved", digest)


class OperatorTest(unittest.TestCase):
    def test_remove_never_touches_generalists(self):
        rng = random.Random(0)
        lib = library()
        for _ in range(10):
            out, _ = orgevo.role_remove(lib, rng)
            if out is None:
                break
            lib = out
            self.assertTrue(orgevo.generalists_present(lib))
        self.assertEqual(sum(1 for r in lib if not r["is_generalist"]), 0)

    def test_ceo_mutate_and_crossover_in_bounds(self):
        rng = random.Random(1)
        pol = CEOPolicyGene(enabled=True).to_dict()
        for _ in range(20):
            pol, mode, note = orgevo.ceo_mutate(pol, "dynamic", rng)
            CEOPolicyGene.from_dict(pol)
            self.assertIn(mode, ("static", "dynamic"))
        roles, pol2, _ = orgevo.org_crossover(library(), library()[:6], pol, CEOPolicyGene().to_dict(), rng)
        self.assertEqual(len(roles), len(library()))
        self.assertTrue(pol2["enabled"])

    def test_random_org_keeps_generalists(self):
        rng = random.Random(2)
        for _ in range(10):
            roles, pol, mode = orgevo.random_org(library(), CEOPolicyGene().to_dict(), rng)
            self.assertTrue(orgevo.generalists_present(roles))
            CEOPolicyGene.from_dict(pol)

    def test_llm_rewrite_is_gated(self):
        class Gates:
            def check(self, role):
                return [] if "Better" in role.get("name", "") else ["leakage: x"]

        replies = iter(['{"name": "Leaky", "goal": "g", "playbook": ["a","b","c"]}',
                        '{"name": "Better Executor", "goal": "g", "backstory": "", "playbook": ["a","b","c"], '
                        '"failure_modes": ["partial_fix"], "domain_tags": ["x"], "temperature": 0.2}'])
        m = orgevo.OrgMutator(Gates(), lambda s, u: next(replies), logger=lambda _: None)
        lib = library()
        lib[5]["uses"], lib[5]["mean_delta_u"] = 3, -0.2
        rng = random.Random(0)
        out, note = m.role_rewrite([r for r in lib if r["layer"] in ("recon", "architect", "executor", "refiner", "falsifier")][:6], "d", rng, 1)
        self.assertIsNotNone(out, note)
        new = next(r for r in out if r["role_id"] == lib[5]["role_id"])
        self.assertEqual(new["name"], "Better Executor")
        self.assertEqual(new["layer"], "executor")
        self.assertEqual(new["uses"], 0)


class _FakeEvaluator:
    def __init__(self, out_root):
        self.worktrees = None
        self.evals_root = os.path.join(out_root, "evals")

    def evaluate(self, genomes, tasks):
        for g in genomes:
            for t in tasks:
                if t not in g.results:
                    solved = (len(g.roles) + len(g.genome_id) + len(t)) % 3 == 0
                    g.results[t] = TaskResult(instance_id=t, resolved=solved)

    def org_records(self, gid, tasks=None):
        return []

    def log_tail(self, gid, t, n_chars=6000):
        return ""


class EvolveWithOrgTest(unittest.TestCase):
    def _run(self, mode, p_org):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = EvolutionConfig(mode=mode, population=4, generations=2, promote_k=2, p_org=p_org,
                                  p_code=0.0, p_knob=0.6, p_cross=0.4, stage1_tasks=["t1", "t2"],
                                  train_tasks=["t1", "t2", "t3"])
            seed = HarnessGenome(genome_id="champ", patch="", org_mode="dynamic", roles=library(),
                                 ceo_policy=CEOPolicyGene(enabled=True).to_dict())
            run = EvolutionRun(cfg, _FakeEvaluator(tmp), tmp, seed_genome=seed, logger=lambda _: None)
            run.run()
            return run

    def test_evolve_breeds_organizations(self):
        run = self._run("evolve", p_org=0.9)
        origins = {g.origin for g in run.genomes.values()}
        self.assertTrue(origins & {"mutate_ceo", "role_remove"}, origins)
        for g in run.genomes.values():
            self.assertTrue(orgevo.generalists_present(g.roles))
            self.assertIn(g.org_mode, ("static", "dynamic"))

    def test_random_control_samples_organizations(self):
        run = self._run("random", p_org=0.0)
        rnd = [g for g in run.genomes.values() if g.origin == "random"]
        self.assertTrue(rnd)
        self.assertTrue(all(g.has_org and orgevo.generalists_present(g.roles) for g in rnd))


if __name__ == "__main__":
    unittest.main()
