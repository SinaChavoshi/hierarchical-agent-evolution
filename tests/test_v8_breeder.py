"""V8 breeding: cross-company role distillation, promotion, pruning, crossover
and mutation of the organisation genes, and their integration in `Breeder`.

What is locked down: the thresholds in `hae.genome.mutator` (a recruited role
is promoted on one SUPPORTED verdict, +0.20 cumulative dU or a resolved task;
a role is pruned after six net-zero uses, never below the CEO's
`max_initial_roles`, never the last probe/synthesis role, never a role that
resolved a task); determinism of every operator from the breeder's seeds; and
the invariant that V5/V6 lineages -- no library, disabled gene -- breed
exactly as they did before V8, so the existing cohorts stay comparable.
"""

import json
import os
import random
import shutil
import tempfile
import unittest

from hae.genome.mutator import (
    PROMOTE_MIN_DELTA_U,
    PRUNE_MIN_USES,
    RoleStatsSummary,
    cap_library,
    crossover_ceo_policy,
    crossover_role_library,
    evolve_role_library,
    mutate_ceo_policy,
    mutate_role_library,
    promoted_roles,
    prune_roles,
    update_role_statistics,
)
from hae.genome.role_seeds import seed_role_library
from hae.genome.schema import CEO_POLICY_BOUNDS, CEOPolicyGene, CompanyGenome, RoleAllele
from hae.orchestration.breeder import (
    Breeder,
    BreedingError,
    GenerationSpec,
    distill_role_statistics,
    distill_tag_pool,
)
from tests.test_epistemic_breeder import ALL_PASS, GENOME, PARENT_A, PARENT_B, scorecard


def _role(role_id, kind="both", **kw):
    data = dict(role_id=role_id, name=role_id.replace("_", " ").title(), goal="g", backstory="b",
                domain_tags=["harness"], kind=kind)
    data.update(kw)
    return RoleAllele.from_dict(data)


def _stats(visits=0, delta_u=0.0, supported=0, falsified=0, untestable=0, written=0, recruited_at=-1):
    return {"visits": visits, "cumulative_delta_u": delta_u, "supported": supported, "falsified": falsified,
            "untestable": untestable, "syntheses_written": written, "consecutive_stalls": 0,
            "recruited_at_move": recruited_at}


def _org(active, stats, recruit_log=(), unmatched=(), policy=None):
    return {"active_roles": [r.to_dict() for r in active], "stats": stats, "stall_counter": 0,
            "unmatched_modules": list(unmatched), "recruit_log": list(recruit_log), "recruit_prior": 0.1,
            "policy": (policy or CEOPolicyGene(enabled=True)).to_dict()}


SEEDS = seed_role_library("legacy")
PROBE = next(r for r in SEEDS if r.kind == "probe")
SYNTH = next(r for r in SEEDS if r.kind == "synthesis")
BOTH = next(r for r in SEEDS if r.kind == "both")
RECRUIT = _role("r_fixture_fixer", kind="both", domain_tags=["harness", "fixture"], origin="recruited:llm")


def v8_record(company_id, resolved=True, orgs=None, judged=90.0, library=None, ceo_policy=None):
    """A worker scorecard for a V8 firm: V6 card + `run_output.org_history`."""
    card = scorecard(company_id, gates=ALL_PASS if resolved else dict(ALL_PASS, tests="failed"),
                     judged=judged, policy=PARENT_A)
    card["genome"]["ceo_policy"] = ceo_policy or {"enabled": True}
    card["genome"]["role_library"] = [r.to_dict() for r in (library if library is not None else SEEDS)]
    if orgs is not None:
        card["run_output"] = {
            "org_history": [{"iteration": i + 2, "org": o,
                             "audit": {"task": {"tags": ["harness", "assertionerror", "widget"]}}}
                            for i, o in enumerate(orgs)],
            "org": orgs[-1],
        }
    return card


# --------------------------------------------------------------------------- #
# Distillation
# --------------------------------------------------------------------------- #

class DistillationTest(unittest.TestCase):

    def test_sums_per_role_evidence_across_firms_and_iterations(self):
        rec_a = v8_record("a", resolved=False, orgs=[
            _org([PROBE, SYNTH], {PROBE.role_id: _stats(3, 0.3, supported=1),
                                  SYNTH.role_id: _stats(1, 0.0, written=1)}),
        ])
        rec_b = v8_record("b", resolved=True, orgs=[
            _org([PROBE, SYNTH], {PROBE.role_id: _stats(2, -0.1), SYNTH.role_id: _stats(0)}),
            _org([PROBE, SYNTH, RECRUIT],
                 {PROBE.role_id: _stats(4, 0.2), SYNTH.role_id: _stats(1, 0.05, written=1),
                  RECRUIT.role_id: _stats(2, 0.4, supported=1, recruited_at=7)},
                 recruit_log=[{"move_index": 7, "role_id": RECRUIT.role_id, "source": "llm"}],
                 unmatched=["hae/evaluation/harness.py"]),
        ])
        stats = distill_role_statistics([rec_a, rec_b, scorecard("v6", policy=PARENT_B)])
        p, s, r = stats[PROBE.role_id], stats[SYNTH.role_id], stats[RECRUIT.role_id]
        self.assertEqual((p.uses, p.supported, p.firms, p.recruited, p.tasks_resolved), (9, 1, 2, 0, 0))
        self.assertAlmostEqual(p.cumulative_delta_u, 0.4)
        self.assertEqual((s.uses, s.syntheses_written, s.firms, s.tasks_resolved), (2, 2, 2, 1))
        self.assertEqual((r.uses, r.supported, r.firms, r.recruited, r.tasks_resolved), (2, 1, 1, 1, 1))
        self.assertEqual(r.allele["origin"], "recruited:llm")
        self.assertEqual(r.name, RECRUIT.name)
        self.assertAlmostEqual(r.mean_delta_u, 0.2)
        self.assertNotIn("v6", stats)
        self.assertEqual(set(stats), {PROBE.role_id, SYNTH.role_id, RECRUIT.role_id})
        self.assertIn("firms", r.to_dict())

    def test_v6_records_contribute_nothing_and_move_level_role_ids_are_a_fallback(self):
        self.assertEqual(distill_role_statistics([scorecard("v6", policy=PARENT_B)]), {})
        card = scorecard("legacy_trace", policy=PARENT_B)
        card["run_output"] = {"epistemic_search": {"trajectory": [
            {"role_id": "r_x", "delta_u": 0.2}, {"extra": {"role_id": "r_x"}, "delta_u": -0.1}, {"delta_u": 1.0}]}}
        stats = distill_role_statistics([card])
        self.assertEqual(stats["r_x"].uses, 2)
        self.assertAlmostEqual(stats["r_x"].cumulative_delta_u, 0.1)
        self.assertEqual(stats["r_x"].firms, 1)

    def test_tag_pool_is_unmatched_module_stems_plus_task_tags(self):
        rec = v8_record("a", orgs=[_org([BOTH], {BOTH.role_id: _stats(1)},
                                       unmatched=["hae/evaluation/harness.py", "hae/genome/morphogenesis.py"])])
        pool = distill_tag_pool([rec, scorecard("v6", policy=PARENT_B)])
        self.assertEqual(pool, sorted(set(pool)))
        for tag in ("harness", "morphogenesis", "evaluation", "genome", "hae", "widget", "assertionerror"):
            self.assertIn(tag, pool)
        self.assertNotIn("py", pool)


# --------------------------------------------------------------------------- #
# Operators
# --------------------------------------------------------------------------- #

class StatisticsUpdateTest(unittest.TestCase):

    def test_running_mean_weighted_by_uses(self):
        role = _role("r_a", uses=10, mean_delta_u=0.1, support_rate=0.2, tasks_resolved=1)
        stats = {"r_a": RoleStatsSummary("r_a", uses=5, cumulative_delta_u=1.0, supported=3, tasks_resolved=2)}
        out, notes = update_role_statistics([role, _role("r_b")], stats)
        a = out[0]
        self.assertEqual(a.uses, 15)
        self.assertAlmostEqual(a.mean_delta_u, (0.1 * 10 + 1.0) / 15, places=4)
        self.assertAlmostEqual(a.support_rate, (0.2 * 10 + 3) / 15, places=4)
        self.assertEqual(a.tasks_resolved, 3)
        self.assertEqual(out[1].uses, 0)  # untouched: no evidence
        self.assertEqual(len(notes), 1)
        self.assertEqual(role.uses, 10)  # pure: the input is not mutated


class PromotionTest(unittest.TestCase):

    def _summary(self, **kw):
        base = dict(role_id=RECRUIT.role_id, name=RECRUIT.name, uses=2, recruited=1, allele=RECRUIT.to_dict())
        base.update(kw)
        return RoleStatsSummary(**base)

    def test_thresholds(self):
        self.assertEqual(len(promoted_roles({RECRUIT.role_id: self._summary(supported=1)}, 17)), 1)
        self.assertEqual(len(promoted_roles({RECRUIT.role_id: self._summary(cumulative_delta_u=PROMOTE_MIN_DELTA_U)}, 17)), 1)
        self.assertEqual(len(promoted_roles({RECRUIT.role_id: self._summary(tasks_resolved=1)}, 17)), 1)
        # Not earned: no verdict, little dU, nothing resolved.
        self.assertEqual(promoted_roles({RECRUIT.role_id: self._summary(cumulative_delta_u=0.1)}, 17), [])
        # Never promoted: a turn-0 role (not recruited), a role without an allele, a role already in the library.
        self.assertEqual(promoted_roles({RECRUIT.role_id: self._summary(supported=1, recruited=0)}, 17), [])
        self.assertEqual(promoted_roles({RECRUIT.role_id: self._summary(supported=1, allele=None)}, 17), [])
        self.assertEqual(promoted_roles({RECRUIT.role_id: self._summary(supported=1)}, 17, existing_ids=[RECRUIT.role_id]), [])

    def test_promoted_allele_carries_its_evidence(self):
        allele = dict(RECRUIT.to_dict(), origin="llm")  # origin is normalised to recruited:*
        [r] = promoted_roles({RECRUIT.role_id: self._summary(uses=4, cumulative_delta_u=0.8, supported=2,
                                                             tasks_resolved=1, allele=allele)}, 17)
        self.assertEqual(r.origin, "recruited:llm")
        self.assertEqual((r.uses, r.tasks_resolved, r.created_generation), (4, 1, 17))
        self.assertAlmostEqual(r.mean_delta_u, 0.2)
        self.assertAlmostEqual(r.support_rate, 0.5)


class PruningTest(unittest.TestCase):

    def setUp(self):
        self.policy = CEOPolicyGene(enabled=True, max_initial_roles=2)
        self.library = [_role("r_p1", kind="probe"), _role("r_p2", kind="probe"),
                        _role("r_s", kind="synthesis"), _role("r_b", kind="both")]

    def test_prunes_net_zero_roles_down_to_the_floor(self):
        stats = {"r_p1": RoleStatsSummary("r_p1", uses=PRUNE_MIN_USES, cumulative_delta_u=0.0),
                 "r_p2": RoleStatsSummary("r_p2", uses=PRUNE_MIN_USES, cumulative_delta_u=-0.3),
                 "r_b": RoleStatsSummary("r_b", uses=PRUNE_MIN_USES - 1, cumulative_delta_u=-1.0)}
        kept, notes = prune_roles(self.library, stats, self.policy)
        # Worst first (r_p2), then r_p1; r_b is under the use threshold.
        self.assertEqual([r.role_id for r in kept], ["r_s", "r_b"])
        self.assertEqual(len(notes), 2)
        self.assertIn("pruned r_p2", notes[0])

    def test_protections(self):
        stats = {rid: RoleStatsSummary(rid, uses=PRUNE_MIN_USES, cumulative_delta_u=0.0)
                 for rid in ("r_p1", "r_p2", "r_s", "r_b")}
        # Floor: never below max_initial_roles.
        kept, _ = prune_roles(self.library, stats, CEOPolicyGene(enabled=True, max_initial_roles=4))
        self.assertEqual(len(kept), 4)
        # The last synthesis-capable and the last probe-capable role survive.
        kept, _ = prune_roles([_role("r_p1", kind="probe"), _role("r_s", kind="synthesis")], stats,
                              CEOPolicyGene(enabled=True, min_initial_roles=1, max_initial_roles=1))
        self.assertEqual({r.role_id for r in kept}, {"r_p1", "r_s"})
        # A role that resolved a task is never pruned, nor a protected id.
        stats["r_p1"].tasks_resolved = 1
        kept, _ = prune_roles(self.library, stats, self.policy, protected_ids=["r_p2"])
        self.assertIn("r_p1", {r.role_id for r in kept})
        self.assertIn("r_p2", {r.role_id for r in kept})
        # Positive dU is never pruned.
        stats = {"r_p1": RoleStatsSummary("r_p1", uses=20, cumulative_delta_u=0.01)}
        self.assertEqual(len(prune_roles(self.library, stats, self.policy)[0]), 4)


class EvolveLibraryTest(unittest.TestCase):

    def test_update_prune_promote_cap_in_that_order(self):
        policy = CEOPolicyGene(enabled=True, max_initial_roles=2)
        library = [_role("r_p", kind="probe"), _role("r_s", kind="synthesis"), _role("r_dead", kind="both")]
        stats = {"r_p": RoleStatsSummary("r_p", uses=3, cumulative_delta_u=0.6, supported=2),
                 "r_dead": RoleStatsSummary("r_dead", uses=PRUNE_MIN_USES, cumulative_delta_u=0.0),
                 RECRUIT.role_id: RoleStatsSummary(RECRUIT.role_id, uses=2, cumulative_delta_u=0.5, supported=1,
                                                   recruited=1, allele=RECRUIT.to_dict())}
        out, notes = evolve_role_library(library, stats, policy, 17)
        ids = [r.role_id for r in out]
        self.assertEqual(ids, ["r_p", "r_s", RECRUIT.role_id])
        self.assertEqual(out[0].uses, 3)
        self.assertAlmostEqual(out[0].mean_delta_u, 0.2)
        self.assertTrue(any(n.startswith("pruned r_dead") for n in notes))
        self.assertTrue(any(n.startswith(f"promoted {RECRUIT.role_id}") for n in notes))
        self.assertEqual(library[0].uses, 0)  # pure
        summarised = evolve_role_library(library, stats, policy, 17, summarise_updates=True)[1]
        self.assertIn("statistics updated for 2 role(s)", summarised)

    def test_cap_keeps_probe_and_synthesis(self):
        roles = [_role(f"r_{i}", kind="probe", uses=10, mean_delta_u=0.3) for i in range(3)]
        roles.append(_role("r_s", kind="synthesis", uses=10, mean_delta_u=0.0))
        capped = cap_library(roles, CEOPolicyGene(), cap=2)
        self.assertEqual(len(capped), 2)
        self.assertIn("r_s", {r.role_id for r in capped})
        self.assertEqual(len(cap_library(roles, cap=4)), 4)
        out, notes = evolve_role_library(roles, {}, CEOPolicyGene(), 17, cap=2)
        self.assertEqual(len(out), 2)
        self.assertTrue(any("capped" in n for n in notes))


class CrossoverTest(unittest.TestCase):

    def test_union_by_id_better_evidenced_allele_wins(self):
        a = [_role("r_1", uses=5, mean_delta_u=0.1), _role("r_2")]
        b = [_role("r_1", uses=9, mean_delta_u=0.3), _role("r_3")]
        child = crossover_role_library(a, b, random.Random(1))
        self.assertEqual(sorted(r.role_id for r in child), ["r_1", "r_2", "r_3"])
        self.assertEqual(next(r for r in child if r.role_id == "r_1").uses, 9)
        # Tie on uses -> rng decides, reproducibly.
        a2, b2 = [_role("r_1", uses=5, mean_delta_u=0.1)], [_role("r_1", uses=5, mean_delta_u=0.9)]
        picks = {crossover_role_library(a2, b2, random.Random(s))[0].mean_delta_u for s in range(30)}
        self.assertEqual(picks, {0.1, 0.9})
        self.assertEqual(crossover_role_library(a2, b2, random.Random(3))[0].mean_delta_u,
                         crossover_role_library(a2, b2, random.Random(3))[0].mean_delta_u)
        self.assertEqual(crossover_role_library([], [], random.Random(1)), [])
        # The cap applies to the union.
        many = [_role(f"r_{i}", kind="both") for i in range(20)]
        more = [_role(f"r_{i}", kind="both") for i in range(15, 30)]
        self.assertEqual(len(crossover_role_library(many, more, random.Random(1))), 24)

    def test_ceo_policy_uniform_crossover_inherits_enabled_from_a(self):
        a = CEOPolicyGene(enabled=True, headcount_lambda=0.1, exploration_c=0.2, recruit_mode="library")
        b = CEOPolicyGene(enabled=False, headcount_lambda=0.9, exploration_c=1.8, recruit_mode="synthesize")
        seen = set()
        for seed in range(40):
            child = crossover_ceo_policy(a, b, random.Random(seed))
            self.assertTrue(child.enabled)
            self.assertIn(child.headcount_lambda, (0.1, 0.9))
            self.assertIn(child.exploration_c, (0.2, 1.8))
            self.assertIn(child.recruit_mode, ("library", "synthesize"))
            seen.add((child.headcount_lambda, child.exploration_c))
        self.assertGreater(len(seen), 1)
        self.assertFalse(crossover_ceo_policy(b, a, random.Random(0)).enabled)
        self.assertEqual(crossover_ceo_policy(None, None, random.Random(0)).to_dict(), CEOPolicyGene().to_dict())
        # Ordering constraints are repaired, never violated.
        lo = CEOPolicyGene(min_initial_roles=1, max_initial_roles=1, max_active_roles=1)
        hi = CEOPolicyGene(min_initial_roles=4, max_initial_roles=6, max_active_roles=10)
        for seed in range(40):
            crossover_ceo_policy(lo, hi, random.Random(seed))  # would raise if invalid


class MutationTest(unittest.TestCase):

    def test_ceo_policy_jitter_is_bounded_deterministic_and_never_flips_enabled(self):
        base = CEOPolicyGene(enabled=True)
        changed = 0
        for seed in range(40):
            gene, notes = mutate_ceo_policy(base, random.Random(seed))
            again, _ = mutate_ceo_policy(base, random.Random(seed))
            self.assertEqual(gene.to_dict(), again.to_dict())
            self.assertTrue(gene.enabled)
            d = gene.to_dict()
            for name, (lo, hi, _is_int) in CEO_POLICY_BOUNDS.items():
                self.assertTrue(lo <= d[name] <= hi, f"{name}={d[name]}")
            self.assertLessEqual(d["min_initial_roles"], d["max_initial_roles"])
            self.assertLessEqual(d["max_initial_roles"], d["max_active_roles"])
            changed += bool(notes)
            for n in notes:
                self.assertTrue(n.startswith("ceo_policy."), n)
        self.assertGreater(changed, 30)
        self.assertFalse(mutate_ceo_policy(CEOPolicyGene(enabled=False), random.Random(1))[0].enabled)
        self.assertEqual(base.to_dict(), CEOPolicyGene(enabled=True).to_dict())  # pure

    def test_role_tag_mutation_changes_exactly_one_role(self):
        library = [_role("r_a", domain_tags=["harness"]), _role("r_b", domain_tags=["artifacts", "io"])]
        out, notes = mutate_role_library(library, random.Random(7), ["widget", "fixture"], 17)
        self.assertEqual(len(notes), 1)
        before = {r.role_id: r.domain_tags for r in library}
        after = {r.role_id: r.domain_tags for r in out}
        differing = [rid for rid in before if before[rid] != after[rid]]
        self.assertEqual(len(differing), 1)
        new_tags = set(after[differing[0]]) - set(before[differing[0]])
        self.assertTrue(new_tags <= {"widget", "fixture"})
        self.assertIn("generation pool", notes[0])
        self.assertEqual(mutate_role_library(library, random.Random(7), ["widget", "fixture"], 17)[1], notes)
        # Empty pool: tags recombine between roles.
        out2, notes2 = mutate_role_library(library, random.Random(3), [], 17)
        self.assertIn("library pool", notes2[0])
        all_tags = {t for r in library for t in r.domain_tags}
        self.assertTrue({t for r in out2 for t in r.domain_tags} <= all_tags)
        self.assertEqual(mutate_role_library([], random.Random(1), ["x"]), ([], []))
        self.assertEqual(library[0].domain_tags, ["harness"])  # pure


# --------------------------------------------------------------------------- #
# Breeder integration
# --------------------------------------------------------------------------- #

class _BreedCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hae_v8_breed_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cards = os.path.join(self.root, "cards")
        os.makedirs(self.cards)

    def write_cards(self, *cards):
        for card in cards:
            with open(os.path.join(self.cards, f"{card['company_id']}.json"), "w", encoding="utf-8") as fh:
                json.dump(card, fh)

    def spec(self, **overrides):
        base = dict(generation=17, name="V8", parent_scorecards="cards", survivors=2,
                    elite=1, crossover=1, pareto=1, mutant=1, objective="repair")
        base.update(overrides)
        return GenerationSpec(**base)

    @staticmethod
    def by_kind(population):
        return {g.company_id.split("_")[2]: g for g in population}


class V8BreedingTest(_BreedCase):

    def setUp(self):
        super().setUp()
        dead = next(r for r in SEEDS if r.kind == "both" and r is not BOTH)
        lib_a = SEEDS
        lib_b = SEEDS[:5] + [_role("r_only_in_b", kind="both", domain_tags=["verification_loop"])]
        # Firm a (best): recruited r_fixture_fixer, which earned a SUPPORTED and the task resolved.
        # Firm b: used `dead` six times for nothing. Firm c: V8 but evaluation failed -> not bred, still distilled.
        self.write_cards(
            v8_record("a", resolved=True, judged=90.0, library=lib_a,
                      ceo_policy={"enabled": True, "headcount_lambda": 0.1, "exploration_c": 0.3}, orgs=[
                          _org([PROBE, SYNTH, RECRUIT],
                               {PROBE.role_id: _stats(4, 0.5, supported=2), SYNTH.role_id: _stats(1, 0.2, written=1),
                                RECRUIT.role_id: _stats(2, 0.4, supported=1, recruited_at=6)},
                               recruit_log=[{"move_index": 6, "role_id": RECRUIT.role_id, "source": "llm"}],
                               unmatched=["hae/evaluation/harness.py"])]),
            v8_record("b", resolved=False, judged=80.0, library=lib_b,
                      ceo_policy={"enabled": True, "headcount_lambda": 0.5, "exploration_c": 1.5}, orgs=[
                          _org([dead, SYNTH], {dead.role_id: _stats(PRUNE_MIN_USES, 0.0, falsified=3),
                                               SYNTH.role_id: _stats(1, 0.0)})]),
        )
        self.dead = dead

    def test_children_carry_evolved_libraries_and_enabled_genes(self):
        population = Breeder(self.spec(), repo_root=self.root).breed()
        self.assertEqual(len(population), 4)
        kinds = self.by_kind(population)
        for genome in population:
            self.assertTrue(genome.org_enabled, genome.company_id)
            self.assertTrue(genome.role_library, genome.company_id)
            self.assertTrue(any(r.can("probe") for r in genome.role_library))
            self.assertTrue(any(r.can("synthesis") for r in genome.role_library))
            self.assertTrue(any("role library" in h for h in genome.mutation_history), genome.company_id)
            CompanyGenome.from_dict(genome.to_dict())  # serialisable and valid
        # The recruit that paid off is promoted into every child descending from a library it was not in.
        elite = kinds["elite"]
        ids = {r.role_id for r in elite.role_library}
        self.assertIn(RECRUIT.role_id, ids)
        promoted = next(r for r in elite.role_library if r.role_id == RECRUIT.role_id)
        self.assertEqual(promoted.origin, "recruited:llm")
        self.assertEqual((promoted.uses, promoted.tasks_resolved, promoted.created_generation), (2, 1, 17))
        # Statistics were folded in: the probe seed now has 4 uses at +0.125/move.
        probe = next(r for r in elite.role_library if r.role_id == PROBE.role_id)
        self.assertEqual(probe.uses, 4)
        self.assertAlmostEqual(probe.mean_delta_u, 0.125)
        self.assertEqual(probe.tasks_resolved, 1)
        # The net-zero role was pruned from the elite (library of 8 is above the floor of 4).
        self.assertNotIn(self.dead.role_id, ids)
        self.assertTrue(any("pruned" in h for h in elite.mutation_history))

    def test_crossover_is_the_union_and_the_gene_recombines(self):
        child = self.by_kind(Breeder(self.spec(), repo_root=self.root).breed())["crossover"]
        ids = {r.role_id for r in child.role_library}
        self.assertIn("r_only_in_b", ids)
        self.assertIn(PROBE.role_id, ids)
        self.assertIn(RECRUIT.role_id, ids)
        self.assertTrue(child.ceo_policy.enabled)
        self.assertIn(child.ceo_policy.headcount_lambda, (0.1, 0.5))
        self.assertIn(child.ceo_policy.exploration_c, (0.3, 1.5))
        self.assertTrue(any("union of a" in h for h in child.mutation_history))

    def test_mutant_is_jittered_within_bounds_and_gets_one_tag(self):
        child = self.by_kind(Breeder(self.spec(), repo_root=self.root).breed())["mutant"]
        d = child.ceo_policy.to_dict()
        self.assertTrue(d["enabled"])
        for name, (lo, hi, _is_int) in CEO_POLICY_BOUNDS.items():
            self.assertTrue(lo <= d[name] <= hi, name)
        history = " ".join(child.mutation_history)
        self.assertIn("Generation 17 role library", history)
        self.assertTrue("tag" in history or "ceo_policy." in history)
        # The parent's library is untouched.
        self.assertEqual(SEEDS[0].uses, 0)

    def test_breeding_is_deterministic(self):
        # Topology morphogenesis draws from the global `random` (pre-V8 and
        # unchanged here), so the check is on what this track adds: the
        # organisation genes and the history lines that describe them.
        def v8_view(population):
            return [(g.company_id, g.ceo_policy.to_dict(), [r.to_dict() for r in g.role_library],
                     [h for h in g.mutation_history if "role library" in h]) for g in population]
        first = v8_view(Breeder(self.spec(), repo_root=self.root).breed())
        second = v8_view(Breeder(self.spec(), repo_root=self.root).breed())
        self.assertEqual(first, second)

    def test_distillation_covers_every_breedable_firm(self):
        breeder = Breeder(self.spec(survivors=1), repo_root=self.root)
        breeder.survivors()
        self.assertIn(self.dead.role_id, breeder.role_stats)  # from firm b, which is not a survivor
        self.assertEqual(breeder.role_stats[RECRUIT.role_id].recruited, 1)
        self.assertIn("harness", breeder.tag_pool)


class V6ParentsStayV6Test(_BreedCase):

    def test_children_of_v6_parents_carry_no_v8_genes(self):
        self.write_cards(scorecard("pa", judged=90.0, policy=PARENT_A),
                         scorecard("pb", judged=85.0, policy=PARENT_B))
        population = Breeder(self.spec(), repo_root=self.root).breed()
        self.assertEqual(len(population), 4)
        for genome in population:
            out = genome.to_dict()
            self.assertNotIn("role_library", out, genome.company_id)
            self.assertNotIn("ceo_policy", out, genome.company_id)
            self.assertFalse(genome.org_enabled)
            self.assertFalse(any("role library" in h for h in genome.mutation_history), genome.company_id)

    def test_ceo_override_switches_the_cohort_on_and_seeds_the_library(self):
        self.write_cards(scorecard("pa", judged=90.0, policy=PARENT_A),
                         scorecard("pb", judged=85.0, policy=PARENT_B))
        population = Breeder(self.spec(ceo_policy={"enabled": True, "headcount_lambda": 0.25},
                                       role_library_seed="swebench"), repo_root=self.root).breed()
        for genome in population:
            self.assertTrue(genome.org_enabled, genome.company_id)
            self.assertEqual(genome.ceo_policy.headcount_lambda, 0.25)
            self.assertEqual(len(genome.role_library), 10)
            self.assertTrue(all(r.origin == "seed:swebench" for r in genome.role_library))
            self.assertTrue(any("V8 CEO policy enabled" in h and "seeded from 'swebench'" in h
                                for h in genome.mutation_history), genome.company_id)
            CompanyGenome.from_dict(genome.to_dict())
        # Default flavour is legacy, and an existing library is not replaced.
        legacy = Breeder(self.spec(ceo_policy={"enabled": True}), repo_root=self.root).breed()
        self.assertEqual(len(legacy[0].role_library), 8)

    def test_unknown_override_keys_and_flavours_are_refused(self):
        self.write_cards(scorecard("pa", judged=90.0, policy=PARENT_A),
                         scorecard("pb", judged=85.0, policy=PARENT_B))
        with self.assertRaises(BreedingError):
            Breeder(self.spec(ceo_policy={"enabled": True, "headcount": 3}), repo_root=self.root).breed()
        with self.assertRaises(BreedingError):
            Breeder(self.spec(ceo_policy={"enabled": True, "headcount_lambda": 9.0}), repo_root=self.root).breed()
        path = os.path.join(self.root, "gen17.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"generation": 17, "name": "V8", "parent_scorecards": "cards", "objective": "repair",
                       "ceo_policy": {"enabled": True}, "role_library_seed": "bogus"}, fh)
        with self.assertRaises(BreedingError):
            GenerationSpec.load(path)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"generation": 17, "name": "V8", "parent_scorecards": "cards", "objective": "repair",
                       "ceo_policy": {"enabled": True}, "role_library_seed": "legacy"}, fh)
        self.assertEqual(GenerationSpec.load(path).role_library_seed, "legacy")

    def test_seeded_generation_honours_the_ceo_override(self):
        seed_path = os.path.join(self.root, "seed.json")
        with open(seed_path, "w", encoding="utf-8") as fh:
            json.dump(GENOME, fh)
        population = Breeder(self.spec(parent_scorecards="", seed_template="seed.json",
                                       ceo_policy={"enabled": True}), repo_root=self.root).breed()
        self.assertEqual(len(population), 4)
        for genome in population:
            self.assertTrue(genome.org_enabled)
            self.assertEqual(len(genome.role_library), 8)


if __name__ == "__main__":
    unittest.main()
