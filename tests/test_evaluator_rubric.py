"""Tests for the rebuilt fitness function.

These lock two properties the previous evaluator did not have: that a failed
judging call is visible rather than silently scored 70, and that whether the
code actually runs materially moves the score.
"""

import json
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")

from hae.evaluation import judge as ev
from hae.evaluation.judge import (
    GATE_WEIGHTS,
    JUDGED_DIMENSIONS,
    RUBRIC_WEIGHTS,
    StrategicFitnessEvaluator,
    composite_score,
    execution_integrity,
)

ALL_GATES = ("syntax", "build", "smoke", "tests", "telemetry")

GOOD_JUDGE_REPLY = json.dumps({
    "strategic_depth": 90.0,
    "technical_feasibility": 88.0,
    "cross_functional_coherence": 95.0,
    "risk_mitigation": 80.0,
    "actionability_and_synthesis": 92.0,
    "qualitative_feedback": "Solid.",
    "identified_bottlenecks": ["b1"],
})


def gates(**overrides):
    status = {g: "skipped" for g in ALL_GATES}
    status.update(overrides)
    return status


def run_eval(judge_replies, verification=None, repair_attempts=1):
    """Runs an evaluation with the LLM call stubbed to yield `judge_replies`."""
    replies = list(judge_replies)

    def fake_call(**kwargs):
        if not replies:
            raise AssertionError("judge called more times than expected")
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    with mock.patch.object(ev, "call_llm", side_effect=fake_call):
        e = StrategicFitnessEvaluator(repair_attempts=repair_attempts)
        result = e.evaluate(
            company_id="test_firm",
            generation=11,
            objective="obj",
            final_deliverable="deliverable",
            departmental_briefs={"eng": "brief"},
            verification=verification,
        )
    return result, replies


class VerificationStub:
    def __init__(self, gate_status):
        self.gate_status = gate_status


class TestWeights(unittest.TestCase):

    def test_rubric_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(RUBRIC_WEIGHTS.values()), 1.0, places=9)

    def test_execution_is_the_single_heaviest_dimension(self):
        heaviest = max(RUBRIC_WEIGHTS, key=RUBRIC_WEIGHTS.get)
        self.assertEqual(heaviest, "execution_integrity")

    def test_saturated_dimensions_were_demoted(self):
        # Coherence and actionability were pinned at a mean of 99.0 (sigma
        # 1.67) across Generation 10, contributing 35% of the weight and
        # almost no signal. They are now 20% combined.
        saturated = (RUBRIC_WEIGHTS["cross_functional_coherence"]
                     + RUBRIC_WEIGHTS["actionability_and_synthesis"])
        self.assertAlmostEqual(saturated, 0.20, places=9)

    def test_gate_weights_sum_to_one_hundred(self):
        self.assertAlmostEqual(sum(GATE_WEIGHTS.values()), 100.0, places=9)


class TestExecutionIntegrity(unittest.TestCase):

    def test_all_gates_passed_is_one_hundred(self):
        self.assertEqual(execution_integrity(gates(**{g: "passed" for g in ALL_GATES})), 100.0)

    def test_all_gates_failed_is_zero(self):
        self.assertEqual(execution_integrity(gates(**{g: "failed" for g in ALL_GATES})), 0.0)

    def test_skipped_gates_leave_the_denominator(self):
        # Two gates evaluated, one passed. A skip must not be scored as either
        # a pass or a fail.
        score = execution_integrity(gates(syntax="passed", build="failed"))
        expected = 100.0 * GATE_WEIGHTS["syntax"] / (GATE_WEIGHTS["syntax"] + GATE_WEIGHTS["build"])
        self.assertAlmostEqual(score, round(expected, 2), places=2)

    def test_nothing_evaluable_returns_none(self):
        self.assertIsNone(execution_integrity(gates()))
        self.assertIsNone(execution_integrity(None))
        self.assertIsNone(execution_integrity({}))

    def test_absent_gate_key_is_treated_as_skipped_not_passed(self):
        # A malformed or partial gate map must never earn credit.
        self.assertEqual(execution_integrity({"syntax": "failed"}), 0.0)

    def test_gen_10_consensus_1_scores_fifty_five(self):
        # The retracted "zero-penalty four-gate clearance": syntax, build and
        # smoke genuinely pass; tests error on collection and there is no
        # opentelemetry import anywhere.
        score = execution_integrity(gates(
            syntax="passed", build="passed", smoke="passed",
            tests="failed", telemetry="failed"))
        self.assertEqual(score, 55.0)

    def test_gen_9_champion_scores_zero(self):
        # gen_9_consensus_1 was bred forward at net 87.68 and passes nothing.
        self.assertEqual(
            execution_integrity(gates(**{g: "failed" for g in ALL_GATES})), 0.0)


class TestComposite(unittest.TestCase):

    def setUp(self):
        self.judged = {d: 95.0 for d in JUDGED_DIMENSIONS}

    def test_execution_moves_the_score_thirty_points(self):
        dead = composite_score(self.judged, 0.0)
        alive = composite_score(self.judged, 100.0)
        self.assertAlmostEqual(alive - dead, 30.0, places=2)

    def test_prose_only_renormalises_to_the_judged_score(self):
        # With no evaluable gates the judged weights are rescaled to sum to 1,
        # so a uniform 95 comes back as 95 rather than being diluted to 66.5.
        self.assertAlmostEqual(composite_score(self.judged, None), 95.0, places=2)

    def test_perfect_prose_with_dead_code_cannot_reach_ninety(self):
        perfect = {d: 100.0 for d in JUDGED_DIMENSIONS}
        self.assertLessEqual(composite_score(perfect, 0.0), 70.0)


class TestJudgeFailureIsNotSilent(unittest.TestCase):

    def test_unparseable_reply_retries_then_fails_loudly(self):
        result, leftover = run_eval(["not json at all", "still not json"])
        f = result.fitness
        self.assertTrue(f.evaluation_failed)
        self.assertEqual(f.fitness_score, 0.0)
        self.assertEqual(leftover, [], "both attempts should have been consumed")

    def test_failure_never_returns_the_old_seventy_fallback(self):
        # The removed fallback was literally 70/70/70/65/70. Assert that no
        # dimension comes back at those values on a failure path.
        result, _ = run_eval(["garbage", "garbage"])
        f = result.fitness
        for dim in JUDGED_DIMENSIONS:
            self.assertEqual(getattr(f, dim), 0.0,
                             f"{dim} should be 0.0 on a failed evaluation")
        self.assertNotEqual(f.fitness_score, 70.0)

    def test_failure_reason_is_recorded(self):
        result, _ = run_eval(["garbage", "garbage"])
        self.assertIn("EVALUATION FAILED", result.fitness.qualitative_feedback)
        self.assertTrue(result.fitness.identified_bottlenecks)

    def test_repair_attempt_can_succeed(self):
        result, leftover = run_eval(["```\nnope\n```", GOOD_JUDGE_REPLY])
        self.assertFalse(result.fitness.evaluation_failed)
        self.assertEqual(result.fitness.strategic_depth, 90.0)
        self.assertEqual(leftover, [])

    def test_transport_exception_is_a_failure_not_a_score(self):
        result, _ = run_eval([RuntimeError("401 Unauthorized"),
                              RuntimeError("401 Unauthorized")])
        self.assertTrue(result.fitness.evaluation_failed)
        self.assertEqual(result.fitness.fitness_score, 0.0)

    def test_reply_missing_a_dimension_is_rejected(self):
        partial = json.dumps({"strategic_depth": 90.0, "technical_feasibility": 90.0})
        result, _ = run_eval([partial, partial])
        self.assertTrue(result.fitness.evaluation_failed)

    def test_empty_reply_is_rejected(self):
        result, _ = run_eval(["", "   "])
        self.assertTrue(result.fitness.evaluation_failed)


class TestEvaluateWiring(unittest.TestCase):

    def test_verification_gates_reach_the_score(self):
        v = VerificationStub(gates(**{g: "passed" for g in ALL_GATES}))
        good, _ = run_eval([GOOD_JUDGE_REPLY], verification=v)

        v_dead = VerificationStub(gates(**{g: "failed" for g in ALL_GATES}))
        bad, _ = run_eval([GOOD_JUDGE_REPLY], verification=v_dead)

        self.assertEqual(good.fitness.execution_integrity, 100.0)
        self.assertEqual(bad.fitness.execution_integrity, 0.0)
        self.assertAlmostEqual(
            good.fitness.fitness_score - bad.fitness.fitness_score, 30.0, places=2)

    def test_identical_prose_is_no_longer_scored_identically(self):
        # This is the whole point of the rebuild: under the old rubric these
        # two firms were indistinguishable.
        v_good = VerificationStub(gates(**{g: "passed" for g in ALL_GATES}))
        v_bad = VerificationStub(gates(**{g: "failed" for g in ALL_GATES}))
        a, _ = run_eval([GOOD_JUDGE_REPLY], verification=v_good)
        b, _ = run_eval([GOOD_JUDGE_REPLY], verification=v_bad)
        self.assertNotAlmostEqual(a.fitness.fitness_score, b.fitness.fitness_score)

    def test_missing_verification_marks_execution_unevaluable(self):
        result, _ = run_eval([GOOD_JUDGE_REPLY], verification=None)
        self.assertFalse(result.fitness.execution_evaluable)

    def test_verification_accepts_a_plain_mapping(self):
        result, _ = run_eval(
            [GOOD_JUDGE_REPLY],
            verification={"gate_status": gates(**{g: "passed" for g in ALL_GATES})})
        self.assertTrue(result.fitness.execution_evaluable)
        self.assertEqual(result.fitness.execution_integrity, 100.0)

    def test_out_of_range_judge_scores_are_clamped(self):
        wild = json.dumps({
            "strategic_depth": 140.0,
            "technical_feasibility": -20.0,
            "cross_functional_coherence": 90.0,
            "risk_mitigation": 90.0,
            "actionability_and_synthesis": 90.0,
            "qualitative_feedback": "",
            "identified_bottlenecks": [],
        })
        result, _ = run_eval([wild])
        self.assertEqual(result.fitness.strategic_depth, 100.0)
        self.assertEqual(result.fitness.technical_feasibility, 0.0)

    def test_fenced_json_is_parsed(self):
        result, _ = run_eval([f"Here you go:\n```json\n{GOOD_JUDGE_REPLY}\n```\nDone."])
        self.assertFalse(result.fitness.evaluation_failed)
        self.assertEqual(result.fitness.risk_mitigation, 80.0)


if __name__ == "__main__":
    unittest.main()
