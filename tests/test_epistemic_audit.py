"""Fitness reads the reasoning trail: the Epistemic Integrity Audit and the V6 composite."""

import unittest
from unittest.mock import patch

from hae.epistemic.audit import build_epistemic_audit
from hae.epistemic.ledger import (
    FALSIFIED, SUPPORTED, EpistemicState, Evidence, GatekeeperAuthority,
)
from hae.epistemic.value import (
    FEATURE_NAMES, EpistemicValueFunction, LearnedValueHead, brier_score,
    calibration_report, epistemic_features, heuristic_value,
)
from hae.evaluation.judge import (
    EPISTEMIC_WEIGHTS, RUBRIC_WEIGHTS, StrategicFitnessEvaluator, composite_score,
    epistemic_efficiency_score, epistemic_integrity_score, proposal_discipline,
)

JUDGED = {"strategic_depth": 90, "technical_feasibility": 90, "cross_functional_coherence": 90,
          "risk_mitigation": 90, "actionability_and_synthesis": 90}


def _ledger(certify=True):
    """A small honest ledger: one wrong high-prior hypothesis, one right low-prior one."""
    state = EpistemicState("audit_firm")
    auth = GatekeeperAuthority("t")
    q = state.add_question("why?", module="m", source_failure_key="t:x")
    h_wrong = state.add_hypothesis(q.question_id, "wrong plausible mechanism", prior=0.8)
    h_right = state.add_hypothesis(q.question_id, "right implausible mechanism", prior=0.2)
    e1 = Evidence(evidence_id=state.new_evidence_id(), kind="probe", hypothesis_id=h_wrong.hypothesis_id,
                  exit_code=0, prediction={"expect_exit_code": 1}, matched_prediction=False)
    state.record_evidence(e1, auth)
    state.set_hypothesis_verdict(h_wrong.hypothesis_id, FALSIFIED, 0.0, e1.evidence_id, auth)
    state.lower_uncertainty(q.question_id, 0.6, auth)
    e2 = Evidence(evidence_id=state.new_evidence_id(), kind="probe", hypothesis_id=h_right.hypothesis_id,
                  exit_code=0, prediction={"expect_exit_code": 0}, matched_prediction=True)
    state.record_evidence(e2, auth)
    state.set_hypothesis_verdict(h_right.hypothesis_id, SUPPORTED, 0.76, e2.evidence_id, auth)
    state.lower_uncertainty(q.question_id, 0.24, auth)
    if certify:
        e3 = Evidence(evidence_id=state.new_evidence_id(), kind="oracle", exit_code=0, matched_prediction=True)
        state.record_evidence(e3, auth)
        state.certify(q.question_id, h_right.hypothesis_id, "passes", [e3.evidence_id], "oracle", auth)
    return state


class AuditTests(unittest.TestCase):

    def test_audit_is_recomputed_from_records(self):
        state = _ledger()
        audit = build_epistemic_audit(state, {"moves_used": 4, "budget_moves": 20, "tabu_rejections": 1,
                                              "hypotheses_accepted": 2}, budget_moves=20)
        self.assertEqual(audit["questions"], 1)
        self.assertEqual(audit["questions_certified"], 1)
        self.assertEqual(audit["hypotheses_tested"], 2)
        self.assertEqual(audit["evidence_backed_fraction"], 1.0)
        self.assertEqual(audit["status_inconsistencies"], 0)
        self.assertEqual(audit["proposed_total"], 3)
        self.assertEqual(audit["resolved_fraction"], 1.0)
        self.assertEqual(audit["low_prior_wins"], 1)
        self.assertEqual(audit["overconfident_falsified"], 1)
        self.assertEqual(audit["ledger_hash"], state.state_hash())

    def test_tampered_ledger_shows_unbacked_claims(self):
        state = _ledger()
        # Someone edits a status by hand, bypassing the gatekeeper.
        h = state.add_hypothesis("q1", "sneaky claim", prior=0.5)
        h.status = SUPPORTED
        audit = build_epistemic_audit(state)
        self.assertLess(audit["evidence_backed_fraction"], 1.0)
        self.assertEqual(audit["unbacked_claims"], 1)
        state.hypotheses["h1"].status = FALSIFIED  # already in ruled_out: consistent
        state.ruled_out.clear()
        self.assertGreater(build_epistemic_audit(state)["status_inconsistencies"], 0)


class ScoreFunctionTests(unittest.TestCase):

    def test_no_ledger_means_no_epistemic_score(self):
        self.assertIsNone(epistemic_integrity_score(None))
        self.assertIsNone(epistemic_integrity_score({}))
        self.assertIsNone(epistemic_integrity_score({"questions": 0}))

    def test_honest_certified_ledger_scores_high(self):
        audit = build_epistemic_audit(_ledger(), {"moves_used": 4, "budget_moves": 20}, budget_moves=20)
        integrity = epistemic_integrity_score(audit)
        self.assertGreaterEqual(integrity, 75.0)
        efficiency = epistemic_efficiency_score(audit)
        self.assertEqual(efficiency, 100.0)

    def test_uncertified_ledger_scores_lower_than_certified(self):
        certified = epistemic_integrity_score(build_epistemic_audit(_ledger(True), {"moves_used": 3, "budget_moves": 20}))
        uncertified = epistemic_integrity_score(build_epistemic_audit(_ledger(False), {"moves_used": 3, "budget_moves": 20}))
        self.assertGreater(certified, uncertified)

    def test_inconsistent_ledger_loses_backing_credit(self):
        audit = build_epistemic_audit(_ledger())
        clean = epistemic_integrity_score(audit)
        audit["status_inconsistencies"] = 1
        self.assertLess(epistemic_integrity_score(audit), clean)

    def test_tabu_reproposals_cost_discipline(self):
        audit = build_epistemic_audit(_ledger(), {"tabu_rejections": 0, "hypotheses_accepted": 2})
        disciplined = epistemic_integrity_score(audit)
        audit["tabu_rejections"], audit["proposed_total"] = 4, 6
        self.assertLess(epistemic_integrity_score(audit), disciplined)

    def test_refused_and_repaired_probes_cost_discipline(self):
        # Pilot finding: a refused probe (UNTESTABLE) or a probe the gatekeeper
        # had to repair in place was free. Full weight for refused, half for
        # repaired and for duplicates caught before they cost a move.
        base = {"proposed_total": 8, "tabu_rejections": 0, "duplicate_rejections": 0,
                "probes_refused": 0, "probe_repairs": 0}
        self.assertEqual(proposal_discipline(base), 1.0)
        self.assertEqual(proposal_discipline(dict(base, probes_refused=2)), 0.75)
        self.assertEqual(proposal_discipline(dict(base, probe_repairs=2)), 0.875)
        self.assertEqual(proposal_discipline(dict(base, duplicate_rejections=2)), 0.875)
        self.assertEqual(proposal_discipline(dict(base, tabu_rejections=2, probes_refused=2)), 0.5)
        # Bounded: the term cannot go negative however bad the proposer was.
        self.assertEqual(proposal_discipline(dict(base, tabu_rejections=8, probes_refused=8)), 0.0)
        # Nothing proposed, nothing to penalise.
        self.assertEqual(proposal_discipline({"proposed_total": 0, "probes_refused": 3}), 1.0)
        # And it flows into integrity: refused probes lower the score, by at most the 15% share.
        audit = build_epistemic_audit(_ledger(), {"hypotheses_accepted": 2})
        clean = epistemic_integrity_score(audit)
        audit["probes_refused"] = audit["proposed_total"]
        penalised = epistemic_integrity_score(audit)
        self.assertLess(penalised, clean)
        self.assertAlmostEqual(clean - penalised, 15.0, places=1)

    def test_audits_without_the_new_counters_score_as_before(self):
        audit = build_epistemic_audit(_ledger(), {"hypotheses_accepted": 2})
        with_keys = epistemic_integrity_score(audit)
        for key in ("probes_refused", "probe_repairs", "duplicate_rejections"):
            audit.pop(key, None)
        self.assertEqual(epistemic_integrity_score(audit), with_keys)

    def test_calibration_credit_requires_tested_hypotheses(self):
        audit = build_epistemic_audit(_ledger())
        audit["calibration_tested"], audit["calibration_brier"] = 0, 0.0
        no_tests = epistemic_integrity_score(audit)
        audit["calibration_tested"], audit["calibration_brier"] = 2, 0.0
        self.assertGreater(epistemic_integrity_score(audit), no_tests)

    def test_efficiency_rewards_resolution_per_budget(self):
        self.assertEqual(epistemic_efficiency_score({"moves_used": 0}), 0.0)
        fast = epistemic_efficiency_score({"moves_used": 5, "budget_moves": 40, "resolved_fraction": 0.5})
        slow = epistemic_efficiency_score({"moves_used": 40, "budget_moves": 40, "resolved_fraction": 0.5})
        self.assertGreater(fast, slow)
        nothing = epistemic_efficiency_score({"moves_used": 40, "budget_moves": 40, "resolved_fraction": 0.0})
        self.assertEqual(nothing, 0.0)


class CompositeTests(unittest.TestCase):

    def test_v5_composite_unchanged_without_audit(self):
        self.assertAlmostEqual(composite_score(JUDGED, 100.0), 95.0, places=2)
        self.assertAlmostEqual(composite_score(JUDGED, None), 90.0, places=2)
        self.assertLessEqual(composite_score(JUDGED, 0.0), 10.0)
        self.assertAlmostEqual(sum(RUBRIC_WEIGHTS.values()), 1.0, places=9)

    def test_epistemic_weights_sum_to_one_and_execution_dominates(self):
        self.assertAlmostEqual(sum(EPISTEMIC_WEIGHTS.values()), 1.0, places=9)
        self.assertEqual(max(EPISTEMIC_WEIGHTS, key=EPISTEMIC_WEIGHTS.get), "execution_integrity")

    def test_prose_has_no_weight_under_the_audit(self):
        audit = build_epistemic_audit(_ledger(), {"moves_used": 4, "budget_moves": 20}, budget_moves=20)
        perfect_prose = composite_score(JUDGED, 80.0, epistemic_audit=audit)
        zero_prose = composite_score({k: 0 for k in JUDGED}, 80.0, epistemic_audit=audit)
        self.assertEqual(perfect_prose, zero_prose)

    def test_audit_composite_matches_weights(self):
        audit = build_epistemic_audit(_ledger(), {"moves_used": 4, "budget_moves": 20}, budget_moves=20)
        expected = (0.60 * 80.0 + 0.25 * epistemic_integrity_score(audit)
                    + 0.15 * epistemic_efficiency_score(audit))
        self.assertAlmostEqual(composite_score(JUDGED, 80.0, epistemic_audit=audit), round(expected, 2), places=2)

    def test_empty_audit_falls_back_to_v5(self):
        self.assertEqual(composite_score(JUDGED, 100.0, epistemic_audit={}), composite_score(JUDGED, 100.0))


class EvaluatorIntegrationTests(unittest.TestCase):

    JUDGE_REPLY = ('{"strategic_depth": 90, "technical_feasibility": 90, "cross_functional_coherence": 90, '
                   '"risk_mitigation": 90, "actionability_and_synthesis": 90, '
                   '"qualitative_feedback": "fine", "identified_bottlenecks": []}')

    def test_evaluate_sets_epistemic_fields_when_audit_given(self):
        audit = build_epistemic_audit(_ledger(), {"moves_used": 4, "budget_moves": 20}, budget_moves=20)
        with patch("hae.evaluation.judge.call_llm", return_value=self.JUDGE_REPLY):
            res = StrategicFitnessEvaluator().evaluate(
                "f", 16, "obj", "deliverable", {}, verification={"evaluable": True, "score": 80.0},
                epistemic_audit=audit)
        self.assertTrue(res.fitness.epistemic_evaluable)
        self.assertGreater(res.fitness.epistemic_integrity, 0.0)
        self.assertEqual(res.fitness.fitness_score, composite_score(JUDGED, 80.0, epistemic_audit=audit))
        self.assertEqual(res.fitness.strategic_depth, 90.0)  # still recorded

    def test_evaluate_without_audit_is_v5(self):
        with patch("hae.evaluation.judge.call_llm", return_value=self.JUDGE_REPLY):
            res = StrategicFitnessEvaluator().evaluate(
                "f", 16, "obj", "deliverable", {}, verification={"evaluable": True, "score": 100.0})
        self.assertFalse(res.fitness.epistemic_evaluable)
        self.assertAlmostEqual(res.fitness.fitness_score, 95.0, places=2)

    def test_failed_judge_keeps_measured_epistemic_fields(self):
        audit = build_epistemic_audit(_ledger(), {"moves_used": 4, "budget_moves": 20}, budget_moves=20)
        with patch("hae.evaluation.judge.call_llm", return_value="not json"):
            res = StrategicFitnessEvaluator(repair_attempts=0).evaluate(
                "f", 16, "obj", "d", {}, verification={"evaluable": True, "score": 80.0}, epistemic_audit=audit)
        self.assertTrue(res.fitness.evaluation_failed)
        self.assertEqual(res.fitness.fitness_score, 0.0)
        self.assertTrue(res.fitness.epistemic_evaluable)
        self.assertGreater(res.fitness.epistemic_integrity, 0.0)


class ValueTests(unittest.TestCase):

    def test_features_are_complete_and_bounded(self):
        feats = epistemic_features(_ledger(False))
        self.assertEqual(tuple(feats), FEATURE_NAMES)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in feats.values()))
        self.assertEqual(epistemic_features(EpistemicState("empty"))["resolved_fraction"], 0.0)

    def test_value_rises_with_resolution(self):
        vf = EpistemicValueFunction(alpha=0.6)
        self.assertGreater(vf.value(_ledger(True)), vf.value(_ledger(False)))
        self.assertGreater(vf.value(_ledger(False)), vf.value(EpistemicState("x")))

    def test_alpha_one_is_pure_resolution(self):
        state = _ledger(False)
        self.assertAlmostEqual(EpistemicValueFunction(alpha=1.0).value(state),
                               epistemic_features(state)["resolved_fraction"], places=6)

    def test_heuristic_bounded(self):
        self.assertAlmostEqual(heuristic_value({}), 0.15, places=9)  # only the stagnation and options terms contribute
        self.assertLessEqual(heuristic_value({k: 1.0 for k in FEATURE_NAMES}), 1.0)

    def test_learned_head_fits_and_round_trips(self):
        pos = [({"resolved_fraction": 0.9, "certified_ratio": 1.0}, 1.0)] * 20
        neg = [({"resolved_fraction": 0.1, "certified_ratio": 0.0}, 0.0)] * 20
        head = LearnedValueHead().fit(pos + neg, epochs=200, lr=0.5)
        self.assertGreater(head.predict(pos[0][0]), 0.7)
        self.assertLess(head.predict(neg[0][0]), 0.3)
        clone = LearnedValueHead.from_json(head.to_json())
        self.assertAlmostEqual(clone.predict(pos[0][0]), head.predict(pos[0][0]), places=9)
        vf = EpistemicValueFunction(alpha=0.0, head=head)
        self.assertGreater(vf.value_from_features(pos[0][0]), vf.value_from_features(neg[0][0]))

    def test_brier_and_calibration(self):
        self.assertEqual(brier_score([1.0, 0.0], [1.0, 0.0]), 0.0)
        self.assertEqual(brier_score([0.5, 0.5], [1.0, 0.0]), 0.25)
        rep = calibration_report(_ledger())
        self.assertEqual(rep["tested"], 2)
        self.assertEqual(rep["low_prior_wins"], 1)
        self.assertGreater(rep["brier"], 0.25)  # confidently wrong, diffidently right


if __name__ == "__main__":
    unittest.main()
