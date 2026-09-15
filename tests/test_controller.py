"""The unattended generation loop.

The launch and harvest steps are injected, so these run without a cluster. The
interesting behaviour is refusal: when the loop declines to breed from what it
just measured, and when it stops spending.
"""

import json
import os
import tempfile
import unittest

from hae.orchestration.controller import (
    CompletenessGate,
    ControllerError,
    GenerationController,
    GenerationOutcome,
    StoppingCriteria,
    operator_class,
)
from hae.task import legacy_task


def _outcome(gen, best):
    return GenerationOutcome(
        generation=gen, population_file="p",
        completeness=CompletenessGate().check(["gen_1_elite_1"], ["gen_1_elite_1"]),
        best_score=best)


class OperatorClassTest(unittest.TestCase):

    def test_recognises_the_breeder_naming_scheme(self):
        self.assertEqual(operator_class("gen_11_mutant_2"), "mutant")
        self.assertEqual(operator_class("gen_11_pareto_bonus_1"), "pareto")
        self.assertEqual(operator_class("gen_9_elite_1"), "elite")
        self.assertIsNone(operator_class("some_seed_firm"))


class CompletenessGateTest(unittest.TestCase):
    """Generation 11 is the specification for this class."""

    GEN11_LAUNCHED = [
        "gen_11_elite_1", "gen_11_elite_2",
        "gen_11_crossover_1", "gen_11_crossover_2", "gen_11_crossover_3",
        "gen_11_consensus_1", "gen_11_consensus_2",
        "gen_11_pareto_bonus_1", "gen_11_mutant_1", "gen_11_mutant_2",
    ]

    def test_rejects_the_actual_gen11_outcome(self):
        survivors = [c for c in self.GEN11_LAUNCHED
                     if "mutant" not in c and "pareto" not in c]
        report = CompletenessGate().check(self.GEN11_LAUNCHED, survivors)
        self.assertFalse(report.ok)
        self.assertEqual(report.completion_rate, 0.7)

    def test_rejects_a_wiped_class_even_at_an_acceptable_rate(self):
        """The check that a completion-rate threshold alone would miss."""
        launched = self.GEN11_LAUNCHED + ["gen_11_crossover_4"]
        survivors = [c for c in launched if c != "gen_11_mutant_1"]
        survivors = [c for c in survivors if c != "gen_11_mutant_2"]
        report = CompletenessGate(min_completion_rate=0.8).check(launched, survivors)
        self.assertGreaterEqual(report.completion_rate, 0.8)
        self.assertFalse(report.ok)
        self.assertEqual(report.missing_classes, ["mutant"])
        self.assertIn("biased sample", report.reason)

    def test_accepts_a_partial_loss_that_spares_every_class(self):
        survivors = [c for c in self.GEN11_LAUNCHED if c != "gen_11_mutant_1"]
        report = CompletenessGate().check(self.GEN11_LAUNCHED, survivors)
        self.assertTrue(report.ok)

    def test_full_completion_passes(self):
        report = CompletenessGate().check(self.GEN11_LAUNCHED, self.GEN11_LAUNCHED)
        self.assertTrue(report.ok)
        self.assertEqual(report.completion_rate, 1.0)

    def test_class_check_can_be_disabled_deliberately(self):
        survivors = [c for c in self.GEN11_LAUNCHED if "mutant" not in c]
        report = CompletenessGate(min_completion_rate=0.5,
                                  require_all_classes=False).check(
            self.GEN11_LAUNCHED, survivors)
        self.assertTrue(report.ok)


class StoppingCriteriaTest(unittest.TestCase):

    def test_stops_at_max_generations(self):
        c = StoppingCriteria(max_generations=2)
        self.assertTrue(c.evaluate([_outcome(1, 50), _outcome(2, 60)], 0, 0))

    def test_stops_when_spend_ceiling_reached(self):
        c = StoppingCriteria(max_generations=99, max_total_usd=10.0)
        d = c.evaluate([], 0, 10.5)
        self.assertTrue(d)
        self.assertIn("ceiling", d.reason)

    def test_stops_on_plateau(self):
        c = StoppingCriteria(max_generations=99, plateau_generations=3,
                             plateau_delta=0.5)
        history = [_outcome(i, 70.0 + i * 0.05) for i in range(1, 6)]
        d = c.evaluate(history, 0, 0)
        self.assertTrue(d)
        self.assertIn("no improvement", d.reason)

    def test_does_not_stop_while_still_improving(self):
        c = StoppingCriteria(max_generations=99, plateau_generations=3)
        history = [_outcome(i, 70.0 + i * 3) for i in range(1, 6)]
        self.assertFalse(c.evaluate(history, 0, 0))

    def test_wall_clock_ceiling(self):
        c = StoppingCriteria(max_generations=99, max_wall_seconds=60)
        self.assertTrue(c.evaluate([], 61, 0))


class ControllerLoopTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.spec = os.path.join(self.tmp, "gen1.json")
        with open(self.spec, "w") as fh:
            json.dump({"generation": 1, "objective": "o", "elite": 1,
                       "crossover": 0, "pareto": 0, "mutant": 0}, fh)

    def test_requires_at_least_one_spec(self):
        with self.assertRaises(ControllerError):
            GenerationController(task=legacy_task(), spec_paths=[],
                                 launch=lambda *a: None, harvest=lambda g: [])

    def test_failed_preflight_refuses_to_launch(self):
        launched = []

        controller = GenerationController(
            task=legacy_task(),
            spec_paths=[self.spec],
            launch=lambda *a: launched.append(a),
            harvest=lambda g: [],
            preflight=lambda: False,
        )
        with self.assertRaises(ControllerError) as ctx:
            controller.run()
        self.assertIn("preflight", str(ctx.exception))
        self.assertEqual(launched, [], "must not launch after a failed preflight")


if __name__ == "__main__":
    unittest.main()
