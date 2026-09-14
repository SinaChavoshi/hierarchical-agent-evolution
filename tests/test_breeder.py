"""Tests for the unified generation breeder.

The behaviour worth locking down is mostly refusal. V1's breeding scripts were
copies of each other, and the copy that ran Generation 9 ranked by legacy net
score and bred forward the worst firm in its cohort. Every test below exists
because something like that actually happened.
"""

import json
import os
import shutil
import tempfile
import unittest

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
        {"dept_id": "eng", "name": "Engineering", "mandate": "Build",
         "manager": {"role": "Eng Manager", "model_tier": "worker"},
         "agents": [{"role": "Engineer", "model_tier": "worker"}]},
        {"dept_id": "fin", "name": "Finance", "mandate": "Model",
         "manager": {"role": "Finance Manager", "model_tier": "worker"},
         "agents": [{"role": "Analyst", "model_tier": "worker"}]},
    ],
}


def scorecard(company_id, gates, judged=95.0, legacy=90.0, failed=False):
    return {
        "company_id": company_id,
        "fitness_score": legacy,
        "strategic_depth": judged,
        "technical_feasibility": judged,
        "cross_functional_coherence": judged,
        "risk_mitigation": judged,
        "actionability": judged,
        "evaluation_failed": failed,
        "verification": {"gate_status": gates},
        "genome": dict(GENOME, company_id=company_id),
    }


ALL_PASS = {"syntax": "passed", "build": "passed", "smoke": "passed",
            "tests": "passed", "telemetry": "passed"}
NONE_PASS = {"syntax": "failed", "build": "failed", "smoke": "failed",
             "tests": "failed", "telemetry": "failed"}


class TestRanking(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hae_breeder_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, card):
        path = os.path.join(self.dir, f"{card['company_id']}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(card, fh)

    def test_execution_outranks_prose(self):
        """The V1 failure in one assertion."""
        # `talker` wins on every judged dimension and on legacy net score, but
        # its code does not run. `builder` is judged slightly worse and works.
        self.write(scorecard("talker", NONE_PASS, judged=99.0, legacy=97.0))
        self.write(scorecard("builder", ALL_PASS, judged=80.0, legacy=82.0))

        ranked = rank_scorecards(self.dir)
        self.assertEqual(ranked[0].company_id, "builder")
        self.assertEqual(ranked[0].gates_passed, 5)
        self.assertLess(ranked[1].rubric_score, ranked[0].rubric_score)
        # And the legacy ordering was the opposite, which is the point.
        self.assertGreater(ranked[1].legacy_net, ranked[0].legacy_net)

    def test_refuses_to_rank_without_gate_data(self):
        self.write(scorecard("gated", ALL_PASS))
        card = scorecard("ungated", ALL_PASS)
        del card["verification"]
        self.write(card)
        with self.assertRaises(BreedingError) as ctx:
            rank_scorecards(self.dir)
        self.assertIn("no execution gate data", str(ctx.exception))

    def test_legacy_booleans_are_not_accepted_as_gates(self):
        """Heuristic verdicts must not be laundered into measurements."""
        card = scorecard("legacy", ALL_PASS)
        card["verification"] = {"build_passed": True, "test_passed": True,
                                "smoke_passed": True, "telemetry_passed": True}
        self.write(card)
        with self.assertRaises(BreedingError):
            rank_scorecards(self.dir)

    def test_failed_evaluations_are_excluded(self):
        self.write(scorecard("ok", ALL_PASS))
        self.write(scorecard("judge_died", ALL_PASS, failed=True))
        ranked = rank_scorecards(self.dir)
        self.assertEqual([r.company_id for r in ranked], ["ok"])

    def test_all_failed_is_an_error_not_an_empty_population(self):
        self.write(scorecard("a", ALL_PASS, failed=True))
        with self.assertRaises(BreedingError):
            rank_scorecards(self.dir)

    def test_empty_directory_is_an_error(self):
        with self.assertRaises(BreedingError):
            rank_scorecards(self.dir)


class TestGenerationSpec(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hae_spec_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, data):
        path = os.path.join(self.dir, "gen.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_unknown_keys_are_rejected(self):
        """A typo must not silently become a generation that did nothing."""
        path = self.write({"generation": 1, "name": "x", "objective": "y",
                           "populaton_size": 10})
        with self.assertRaises(BreedingError) as ctx:
            GenerationSpec.load(path)
        self.assertIn("populaton_size", str(ctx.exception))

    def test_an_objective_is_required(self):
        path = self.write({"generation": 1, "name": "x"})
        with self.assertRaises(BreedingError):
            GenerationSpec.load(path)

    def test_objective_and_benchmark_are_mutually_exclusive(self):
        path = self.write({"generation": 1, "name": "x", "objective": "y",
                           "benchmark_task": "artifacts"})
        with self.assertRaises(BreedingError):
            GenerationSpec.load(path)

    def test_population_size_is_the_sum_of_its_parts(self):
        path = self.write({"generation": 2, "name": "x", "objective": "y",
                           "elite": 2, "crossover": 3, "pareto": 2, "mutant": 3})
        self.assertEqual(GenerationSpec.load(path).population_size, 10)


class TestBreeding(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hae_breed_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cards = os.path.join(self.root, "cards")
        os.makedirs(self.cards)
        for i, gates in enumerate((ALL_PASS, ALL_PASS, NONE_PASS), start=1):
            card = scorecard(f"parent_{i}", gates, judged=90.0 - i)
            with open(os.path.join(self.cards, f"p{i}.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(card, fh)
        self.spec = GenerationSpec(
            generation=2, name="Test", parent_scorecards="cards",
            survivors=3, elite=2, crossover=0, pareto=2, mutant=0,
            objective="do the thing",
            mandate="Verified execution over described execution.")

    def test_population_has_the_declared_shape(self):
        breeder = Breeder(self.spec, repo_root=self.root)
        population = breeder.breed()
        self.assertEqual(len(population), self.spec.population_size)
        self.assertEqual(sorted(g.company_id for g in population),
                         ["gen_2_elite_1", "gen_2_elite_2",
                          "gen_2_pareto_1", "gen_2_pareto_2"])

    def test_every_firm_carries_the_generation_and_its_lineage(self):
        population = Breeder(self.spec, repo_root=self.root).breed()
        for genome in population:
            self.assertEqual(genome.generation, 2)
            self.assertTrue(genome.parent_ids)
            self.assertTrue(genome.mutation_history)

    def test_the_mandate_reaches_every_ceo(self):
        population = Breeder(self.spec, repo_root=self.root).breed()
        for genome in population:
            self.assertIn("Verified execution",
                          genome.ceo.system_instructions or "")

    def test_parents_are_not_mutated_by_breeding(self):
        """A shared genome object silently couples siblings."""
        breeder = Breeder(self.spec, repo_root=self.root)
        parents = breeder.survivors()
        original = parents[0].genome.ceo.system_instructions
        breeder.breed()
        self.assertEqual(parents[0].genome.ceo.system_instructions, original)

    def test_written_population_round_trips(self):
        breeder = Breeder(self.spec, repo_root=self.root)
        path = breeder.write(breeder.breed())
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["generation"], 2)
        self.assertEqual(len(payload["population"]), 4)

        from hae.genome.schema import CompanyGenome
        for entry in payload["population"]:
            self.assertEqual(CompanyGenome.from_dict(entry).generation, 2)

    def test_a_seeded_generation_must_say_so(self):
        spec = GenerationSpec(generation=1, name="Seed", objective="x")
        with self.assertRaises(BreedingError):
            Breeder(spec, repo_root=self.root).survivors()


if __name__ == "__main__":
    unittest.main()
