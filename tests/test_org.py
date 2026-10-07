"""Tests for the shared V8 organisational types (hae/epistemic/org.py)."""

import json
import random
import unittest

from hae.epistemic.org import (CEOPolicyGene, OrgState, RoleAllele, TaskFeatures,
                               search_initial_organization, sigmoid, tag_overlap)
from hae.genome.schema import GenomeValidationError


def _lib():
    return [
        RoleAllele(name="Import Resolution Specialist", domain_tags=["importerror", "harness", "modulenotfounderror"],
                   kind="probe", uses=10, mean_delta_u=0.2),
        RoleAllele(name="Minimal Patch Synthesiser", domain_tags=["harness", "verification_loop"], kind="synthesis",
                   uses=10, mean_delta_u=0.15),
        RoleAllele(name="Generalist Debugger", domain_tags=["python"], kind="both", uses=20, mean_delta_u=0.05),
        RoleAllele(name="Fresh Allele", domain_tags=["artifacts"], kind="both"),
        RoleAllele(name="Expensive Architect", domain_tags=["morphogenesis"], kind="both", model_tier="executive",
                   uses=5, mean_delta_u=0.0),
    ]


class RoleAlleleTests(unittest.TestCase):
    def test_defaults_ids_and_validation(self):
        r = RoleAllele(name="Traceback Localiser", domain_tags=["Harness", "harness", " NameError "])
        self.assertEqual(r.role_id, "r_traceback_localiser")
        self.assertEqual(r.domain_tags, ["harness", "nameerror"])
        self.assertTrue(r.can("probe") and r.can("synthesis"))
        with self.assertRaises(GenomeValidationError):
            RoleAllele(name="")
        with self.assertRaises(GenomeValidationError):
            RoleAllele(name="x", kind="manager")

    def test_round_trip_and_agent_genome(self):
        r = _lib()[0]
        again = RoleAllele.from_dict(json.loads(json.dumps(r.to_dict())))
        self.assertEqual(again.to_dict(), r.to_dict())
        agent = r.to_agent_genome()
        self.assertEqual(agent.role, r.name)
        self.assertEqual(agent.extra["role_id"], r.role_id)

    def test_quality_uses_optimistic_prior_only_when_unused(self):
        fresh, used = _lib()[3], _lib()[0]
        self.assertAlmostEqual(fresh.quality(0.3), 0.3)
        self.assertAlmostEqual(used.quality(0.3), 0.2 / 0.3)


class CEOPolicyTests(unittest.TestCase):
    def test_defaults_disabled_and_bounds(self):
        p = CEOPolicyGene()
        self.assertFalse(p.enabled)
        self.assertEqual(p.from_dict(p.to_dict()).to_dict(), p.to_dict())
        with self.assertRaises(GenomeValidationError):
            CEOPolicyGene(min_initial_roles=4, max_initial_roles=2)
        with self.assertRaises(GenomeValidationError):
            CEOPolicyGene(recruit_mode="hire")
        with self.assertRaises(GenomeValidationError):
            CEOPolicyGene(headcount_lambda=5)


class TaskFeatureTests(unittest.TestCase):
    def test_from_failures(self):
        fails = ["[held_out] test_x (tests.test_h) ... ImportError: cannot import name 'run' from hae/evaluation/harness.py",
                 "[held_out] test_y ... AssertionError: 1 != 2"]
        f = TaskFeatures.from_failures(fails, locations={fails[0]: "hae/evaluation/harness.py:212 in _run_pytest"})
        self.assertEqual(f.n_failures, 2)
        self.assertTrue(f.has_import_error and f.has_assertion)
        self.assertIn("harness", f.tags)
        self.assertIn("importerror", f.tags)
        self.assertTrue(0.0 < f.complexity < 1.0)
        self.assertEqual(f.source, "failures")

    def test_from_problem_statement(self):
        f = TaskFeatures.from_problem_statement(
            "QuerySet.alias() crashes in django/db/models/sql/compiler.py with FieldError", repo="django/django")
        self.assertIn("django", f.tags)
        self.assertIn("compiler", f.tags)
        self.assertIn("fielderror", f.tags)
        self.assertEqual(f.n_paths_mentioned, 1)
        self.assertEqual(f.source, "problem_statement")
        self.assertIn("complexity", f.to_dict())


class OrgSearchTests(unittest.TestCase):
    def test_simple_task_gets_a_small_team_with_probe_and_synthesis(self):
        task = TaskFeatures.from_failures(["[held_out] t ... ImportError in hae/evaluation/harness.py"])
        team, audit = search_initial_organization(_lib(), task, CEOPolicyGene(headcount_lambda=0.6, exploration_c=0.0))
        ids = [r.role_id for r in team]
        self.assertEqual(len(team), 2, audit)
        self.assertTrue(any(r.can("probe") for r in team) and any(r.can("synthesis") for r in team))
        self.assertIn("r_import_resolution_specialist", ids)
        self.assertEqual(audit["chosen"], sorted(ids))
        self.assertGreater(audit["candidates"], 0)

    def test_complex_task_and_low_lambda_grow_the_team(self):
        fails = [f"[held_out] t{i} ... {e} in hae/{m}.py" for i, (e, m) in enumerate(
            [("ImportError", "evaluation/harness"), ("NameError", "evaluation/verification_loop"),
             ("TypeError", "runtime/artifacts"), ("AttributeError", "genome/morphogenesis")] * 3)]
        task = TaskFeatures.from_failures(fails)
        team, _ = search_initial_organization(_lib(), task, CEOPolicyGene(headcount_lambda=0.0, max_initial_roles=4))
        self.assertEqual(len(team), 4)

    def test_deterministic_without_temperature_and_sampled_with(self):
        task = TaskFeatures.from_failures(["[held_out] t ... ImportError in hae/evaluation/harness.py"])
        a, _ = search_initial_organization(_lib(), task, CEOPolicyGene())
        b, _ = search_initial_organization(_lib(), task, CEOPolicyGene())
        self.assertEqual([r.role_id for r in a], [r.role_id for r in b])
        seen = set()
        for seed in range(12):
            t, _ = search_initial_organization(_lib(), task, CEOPolicyGene(temperature=1.0), random.Random(seed))
            seen.add(tuple(r.role_id for r in t))
        self.assertGreater(len(seen), 1)

    def test_empty_library(self):
        team, audit = search_initial_organization([], TaskFeatures(), CEOPolicyGene())
        self.assertEqual(team, [])
        self.assertEqual(audit["candidates"], 0)


class OrgStateTests(unittest.TestCase):
    def setUp(self):
        lib = _lib()
        self.policy = CEOPolicyGene(stall_delta_u=0.05, stall_moves=2, recruit_cooldown_moves=2,
                                    max_initial_roles=3, max_active_roles=4)
        self.org = OrgState(lib[:2], self.policy, library=lib)

    def test_credit_and_stall_counter(self):
        pid = self.org.active_ids[0]
        self.org.credit(pid, 0.0, "FALSIFIED", move_index=0)
        self.org.credit(pid, 0.01, "UNTESTABLE", move_index=1)
        self.assertEqual(self.org.stall_counter, 2)
        self.assertEqual(self.org.stats[pid].falsified, 1)
        self.org.credit(pid, 0.3, "SUPPORTED", move_index=2)
        self.assertEqual(self.org.stall_counter, 0)
        self.assertEqual(self.org.stats[pid].supported, 1)
        self.assertEqual(self.org.stats[pid].consecutive_stalls, 0)

    def test_unmatched_modules_and_recruit_prior_rise_together(self):
        p0 = self.org.recruit_prior()
        new = self.org.observe_modules(["hae/evaluation/harness.py", "hae/runtime/artifacts.py"])
        self.assertEqual(new, ["hae/runtime/artifacts.py"])  # harness is covered by an active role's tag
        self.assertGreater(self.org.recruit_prior(), p0)
        self.org.credit(self.org.active_ids[0], 0.0, "FALSIFIED", move_index=0)
        self.org.credit(self.org.active_ids[0], 0.0, "FALSIFIED", move_index=1)
        self.assertGreater(self.org.recruit_prior(), sigmoid(self.policy.recruit_bias))

    def test_recruit_from_library_resets_pressure_and_routes_next_move(self):
        self.org.observe_modules(["hae/runtime/artifacts.py"])
        self.org.credit(self.org.active_ids[0], 0.0, "FALSIFIED", move_index=0)
        self.assertTrue(self.org.can_recruit(5))
        ranked = self.org.library_candidates(task_tags=["artifacts"])
        best = ranked[0][1]
        self.assertEqual(best.name, "Fresh Allele")
        self.org.recruit(best, move_index=5, reason="unmatched artifacts", source="library")
        self.assertEqual(self.org.stall_counter, 0)
        self.assertEqual(self.org.unmatched_modules, [])
        self.assertEqual(self.org.pick_role("probe").role_id, best.role_id)  # optimistic routing
        self.assertFalse(self.org.can_recruit(6))  # cooldown
        self.assertTrue(self.org.can_recruit(7))
        self.org.recruit(_lib()[4], move_index=7, reason="x", source="library")
        self.assertFalse(self.org.can_recruit(20))  # max_active_roles
        with self.assertRaises(ValueError):
            self.org.recruit(best, move_index=9, reason="dup", source="library")

    def test_routing_respects_kind_and_round_trip(self):
        self.assertEqual(self.org.pick_role("synthesis").name, "Minimal Patch Synthesiser")
        self.assertEqual(self.org.pick_role("probe").name, "Import Resolution Specialist")
        self.org.credit(self.org.active_ids[0], 0.3, "SUPPORTED", move_index=0)
        again = OrgState.from_dict(json.loads(json.dumps(self.org.to_dict())), library=_lib())
        self.assertEqual(again.to_dict(), self.org.to_dict())
        self.assertEqual(again.stats[self.org.active_ids[0]].supported, 1)


class HelperTests(unittest.TestCase):
    def test_overlap_and_sigmoid(self):
        self.assertEqual(tag_overlap(["a", "b"], ["b", "c", "d"]), 0.5)
        self.assertEqual(tag_overlap([], ["x"]), 0.0)
        self.assertAlmostEqual(sigmoid(0.0), 0.5)
        self.assertLess(sigmoid(-50), 1e-10)
        self.assertGreater(sigmoid(50), 1 - 1e-10)


if __name__ == "__main__":
    unittest.main()
