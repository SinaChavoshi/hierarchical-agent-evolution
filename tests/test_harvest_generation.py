"""Tests for the unified generation harvester.

The behaviour under test is mostly a refusal: the harvester must not produce a
ranking when it has no execution data. An earlier draft did, and it confidently
named `gen_10_elite_2` -- a firm that passes zero of five gates -- as the
Generation 10 champion, because composite_score() renormalises the judged
weights when handed nothing and returns a plausible number.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, ".")

_SPEC = importlib.util.spec_from_file_location(
    "harvest_generation", os.path.join("scripts", "harvest_generation.py"))
harvest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harvest)

GATES_3_OF_5 = {"syntax": "passed", "build": "passed", "smoke": "passed",
                "tests": "failed", "telemetry": "failed"}
GATES_0_OF_5 = {g: "failed" for g in
                ("syntax", "build", "smoke", "tests", "telemetry")}


def card(company_id, overall, gates=None, judged=95.0, failed=False):
    fitness = {
        "strategic_depth": judged,
        "technical_feasibility": judged,
        "cross_functional_coherence": judged,
        "risk_mitigation": judged,
        "actionability_and_synthesis": judged,
        "evaluation_failed": failed,
    }
    out = {
        "company_id": company_id,
        "fitness_score": overall,
        "gross_score": overall,
        "evaluation": {"fitness": fitness},
        "verification": {"authored_files": 4},
        "opex": {"estimated_cost_usd": 0.4, "headcount": 30},
        "run_output": {},
        "genome": {"company_id": company_id, "generation": 99, "departments": []},
    }
    if gates is not None:
        out["verification"]["gate_status"] = gates
    return out


class TestGateResolution(unittest.TestCase):

    def test_live_gate_status_is_used(self):
        self.assertEqual(
            harvest.gate_status(card("f", 80, GATES_3_OF_5), {}), GATES_3_OF_5)

    def test_backfill_fills_in_for_pre_harness_scorecards(self):
        self.assertEqual(
            harvest.gate_status(card("f", 80), {"f": GATES_0_OF_5}), GATES_0_OF_5)

    def test_live_status_wins_over_backfill(self):
        self.assertEqual(
            harvest.gate_status(card("f", 80, GATES_3_OF_5), {"f": GATES_0_OF_5}),
            GATES_3_OF_5)

    def test_no_data_anywhere_returns_none(self):
        # Must be None, not an empty dict that scores as zero and not a
        # coerced version of the legacy booleans.
        self.assertIsNone(harvest.gate_status(card("f", 80), {}))

    def test_legacy_booleans_are_not_mistaken_for_gate_status(self):
        stale = card("f", 80)
        stale["verification"].update(
            {"build_passed": True, "smoke_passed": True,
             "test_passed": True, "telemetry_passed": True})
        self.assertIsNone(harvest.gate_status(stale, {}))


class TestSummarise(unittest.TestCase):

    def test_execution_integrity_is_computed_from_gates(self):
        row = harvest.summarise(card("f", 80, GATES_3_OF_5), {})
        self.assertEqual(row["gates_passed"], 3)
        self.assertEqual(row["execution_integrity"], 55.0)

    def test_ungated_firm_reports_none_not_zero(self):
        # Zero would mean "measured and failed". None means "not measured".
        self.assertIsNone(harvest.summarise(card("f", 80), {})["execution_integrity"])

    def test_failed_evaluation_is_surfaced(self):
        self.assertTrue(
            harvest.summarise(card("f", 0, GATES_0_OF_5, failed=True), {})["evaluation_failed"])


class TestRefusalToRank(unittest.TestCase):
    """The core safety property."""

    def setUp(self):
        # Kept alive for the duration of the test so assertions can inspect
        # what the harvester wrote (or declined to write).
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def _run(self, cards, backfill=None):
        sc_dir = os.path.join(self.tmp, "scorecards")
        os.makedirs(sc_dir, exist_ok=True)
        for c in cards:
            with open(os.path.join(sc_dir, f"{c['company_id']}.json"), "w") as fh:
                json.dump(c, fh)
        with mock.patch.dict(harvest.EXPERIMENT_DIRS, {99: self.tmp}), \
             mock.patch.object(harvest, "load_backfill", lambda: backfill or {}), \
             mock.patch.object(sys, "argv",
                               ["harvest", "--generation", "99", "--no-download"]):
            return harvest.main(), self.tmp

    def test_refuses_when_any_firm_lacks_gate_data(self):
        rc, tmp = self._run([card("a", 90, GATES_3_OF_5), card("b", 95)])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(tmp, "rubric_standings.json")),
                         "must not write a ranking it refused to produce")

    def test_ranks_when_every_firm_has_gates(self):
        rc, tmp = self._run([card("a", 90, GATES_3_OF_5), card("b", 95, GATES_0_OF_5)])
        self.assertEqual(rc, 0)
        with open(os.path.join(tmp, "rubric_standings.json")) as fh:
            standings = json.load(fh)
        # `b` outscores `a` on prose and legacy net but passes nothing.
        self.assertEqual(standings["rubric_champion"], "a")
        self.assertEqual(standings["legacy_champion"], "b")

    def test_backfill_satisfies_the_requirement(self):
        rc, _ = self._run([card("a", 90), card("b", 95)],
                          backfill={"a": GATES_3_OF_5, "b": GATES_0_OF_5})
        self.assertEqual(rc, 0)

    def test_failed_evaluations_are_excluded_from_survivors(self):
        rc, tmp = self._run([
            card("good", 70, GATES_3_OF_5),
            card("broken", 0, GATES_0_OF_5, failed=True),
        ])
        self.assertEqual(rc, 0)
        with open(os.path.join(tmp, "top_5_rubric_survivors.json")) as fh:
            survivors = [s["company_id"] for s in json.load(fh)]
        self.assertNotIn("broken", survivors)

    def test_historical_champion_file_is_never_overwritten(self):
        historical = os.path.join(self.tmp, "winning_champion_genome.json")
        with open(historical, "w") as fh:
            fh.write('{"company_id": "the_original_record"}')

        rc, _ = self._run([card("a", 90, GATES_3_OF_5), card("b", 95, GATES_0_OF_5)])
        self.assertEqual(rc, 0)
        with open(historical) as fh:
            self.assertIn("the_original_record", fh.read())


if __name__ == "__main__":
    unittest.main()
