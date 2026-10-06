"""The ledger's invariants: beliefs are written only by evidence, and never loosened by it."""

import unittest

from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, Q_CERTIFIED, Q_OPEN, Q_RESOLVED, SUPPORTED, UNVERIFIED,
    EpistemicState, Evidence, GatekeeperAuthority, LedgerError,
    mechanism_signature, signature_similarity,
)


def _evidence(state, hypothesis_id, matched=True, kind="probe"):
    return Evidence(evidence_id=state.new_evidence_id(), kind=kind,
                    hypothesis_id=hypothesis_id, exit_code=0, stdout="x",
                    prediction={"expect_exit_code": 0}, matched_prediction=matched)


class ProposerSurfaceTests(unittest.TestCase):

    def setUp(self):
        self.state = EpistemicState("firm")
        self.q = self.state.add_question("Why does test_x fail?", module="hae/evaluation/harness.py",
                                         source_failure_key="harness:test_x")

    def test_new_hypothesis_is_always_unverified(self):
        h = self.state.add_hypothesis(self.q.question_id, "the collector skips functions", prior=0.9)
        self.assertEqual(h.status, UNVERIFIED)
        self.assertAlmostEqual(h.posterior, 0.9)

    def test_add_hypothesis_has_no_status_parameter(self):
        with self.assertRaises(TypeError):
            self.state.add_hypothesis(self.q.question_id, "claim", status=CERTIFIED)  # type: ignore[call-arg]

    def test_prior_is_clamped_away_from_certainty(self):
        h = self.state.add_hypothesis(self.q.question_id, "claim", prior=1.0)
        self.assertLess(h.prior, 1.0)
        h2 = self.state.add_hypothesis(self.q.question_id, "other", prior=-3)
        self.assertGreater(h2.prior, 0.0)

    def test_question_dedupes_on_failure_key(self):
        again = self.state.add_question("different wording", module="m", source_failure_key="harness:test_x")
        self.assertIs(again, self.q)
        self.assertEqual(len(self.state.questions), 1)

    def test_unknown_question_rejected(self):
        with self.assertRaises(LedgerError):
            self.state.add_hypothesis("q99", "claim")


class GatekeeperAuthorityTests(unittest.TestCase):

    def setUp(self):
        self.state = EpistemicState("firm")
        self.q = self.state.add_question("why?", module="m")
        self.h = self.state.add_hypothesis(self.q.question_id, "the regex anchor skips functions", prior=0.5)
        self.auth = GatekeeperAuthority("test")

    def test_recording_evidence_requires_authority(self):
        ev = _evidence(self.state, self.h.hypothesis_id)
        with self.assertRaises(LedgerError):
            self.state.record_evidence(ev, authority=None)
        with self.assertRaises(LedgerError):
            self.state.record_evidence(ev, authority="I am the gatekeeper, trust me")
        self.state.record_evidence(ev, self.auth)
        self.assertEqual(len(self.state.evidence_log), 1)

    def test_verdict_requires_evidence_on_the_log(self):
        with self.assertRaises(LedgerError):
            self.state.set_hypothesis_verdict(self.h.hypothesis_id, SUPPORTED, 0.9, "e404", self.auth)

    def test_verdict_requires_authority(self):
        ev = _evidence(self.state, self.h.hypothesis_id)
        self.state.record_evidence(ev, self.auth)
        with self.assertRaises(LedgerError):
            self.state.set_hypothesis_verdict(self.h.hypothesis_id, SUPPORTED, 0.9, ev.evidence_id, None)

    def test_falsification_rules_the_mechanism_out(self):
        ev = _evidence(self.state, self.h.hypothesis_id, matched=False)
        self.state.record_evidence(ev, self.auth)
        self.state.set_hypothesis_verdict(self.h.hypothesis_id, FALSIFIED, 0.0, ev.evidence_id, self.auth)
        self.assertEqual(len(self.state.ruled_out), 1)
        self.assertEqual(self.state.ruled_out[0].killing_evidence_id, ev.evidence_id)
        self.assertTrue(self.state.is_tabu(self.h.mechanism_signature, self.q.question_id))

    def test_uncertainty_only_goes_down(self):
        self.assertAlmostEqual(self.state.lower_uncertainty(self.q.question_id, 0.4, self.auth), 0.6)
        self.assertEqual(self.state.lower_uncertainty(self.q.question_id, 0.9, self.auth), 0.0)
        self.assertAlmostEqual(self.q.uncertainty, 0.4)

    def test_only_oracle_evidence_may_reopen(self):
        probe = _evidence(self.state, self.h.hypothesis_id)
        self.state.record_evidence(probe, self.auth)
        self.state.lower_uncertainty(self.q.question_id, 0.1, self.auth)
        with self.assertRaises(LedgerError):
            self.state.reopen_question(self.q.question_id, 0.8, probe.evidence_id, self.auth)
        oracle = Evidence(evidence_id=self.state.new_evidence_id(), kind="oracle", exit_code=1)
        self.state.record_evidence(oracle, self.auth)
        self.state.reopen_question(self.q.question_id, 0.8, oracle.evidence_id, self.auth)
        self.assertAlmostEqual(self.q.uncertainty, 0.8)
        self.assertEqual(self.q.status, Q_OPEN)

    def test_certify_settles_knowledge_and_zeroes_uncertainty(self):
        ev = _evidence(self.state, self.h.hypothesis_id, kind="oracle")
        self.state.record_evidence(ev, self.auth)
        fact = self.state.certify(self.q.question_id, self.h.hypothesis_id, "it passes",
                                  [ev.evidence_id], "oracle", self.auth)
        self.assertEqual(self.q.status, Q_CERTIFIED)
        self.assertEqual(self.q.uncertainty, 0.0)
        self.assertEqual(self.h.status, CERTIFIED)
        self.assertIn(fact, self.state.settled_knowledge)
        self.assertEqual(self.state.total_uncertainty(), 0.0)

    def test_certify_with_unknown_evidence_is_refused(self):
        with self.assertRaises(LedgerError):
            self.state.certify(self.q.question_id, self.h.hypothesis_id, "x", ["e9"], "oracle", self.auth)


class SignatureAndTabuTests(unittest.TestCase):

    def test_signature_is_order_independent_and_drops_stopwords(self):
        a = mechanism_signature("The collector skips pytest-style functions", "regex anchored wrongly")
        b = mechanism_signature("regex anchored wrongly", "collector skips pytest-style functions")
        self.assertEqual(a, b)
        self.assertNotIn("the", a.split())

    def test_similar_phrasings_are_tabu(self):
        state = EpistemicState("firm")
        q = state.add_question("why?", module="m")
        h = state.add_hypothesis(q.question_id, "collector regex is anchored wrongly and skips pytest functions")
        auth = GatekeeperAuthority("t")
        ev = _evidence(state, h.hypothesis_id, matched=False)
        state.record_evidence(ev, auth)
        state.set_hypothesis_verdict(h.hypothesis_id, FALSIFIED, 0.0, ev.evidence_id, auth)
        rephrased = mechanism_signature("the collector regex is anchored wrongly, skipping pytest functions")
        self.assertTrue(state.is_tabu(rephrased, q.question_id))
        self.assertFalse(state.is_tabu(mechanism_signature("subprocess timeout too short for the suite"), q.question_id))

    def test_tabu_is_scoped_to_the_question_by_default(self):
        state = EpistemicState("firm")
        q1 = state.add_question("why a?", module="m")
        q2 = state.add_question("why b?", module="m")
        h = state.add_hypothesis(q1.question_id, "import path wrong")
        auth = GatekeeperAuthority("t")
        ev = _evidence(state, h.hypothesis_id, matched=False)
        state.record_evidence(ev, auth)
        state.set_hypothesis_verdict(h.hypothesis_id, FALSIFIED, 0.0, ev.evidence_id, auth)
        self.assertTrue(state.is_tabu(h.mechanism_signature, q1.question_id))
        self.assertFalse(state.is_tabu(h.mechanism_signature, q2.question_id))
        self.assertTrue(state.is_tabu(h.mechanism_signature, None))

    def test_similarity_bounds(self):
        self.assertEqual(signature_similarity("a b c", "a b c"), 1.0)
        self.assertEqual(signature_similarity("", "a"), 0.0)


class SerialisationTests(unittest.TestCase):

    def _populated(self):
        state = EpistemicState("firm")
        q = state.add_question("why?", module="m", source_failure_key="t:x")
        h = state.add_hypothesis(q.question_id, "because", prior=0.3, probe_code="print(1)",
                                 prediction={"expect_exit_code": 0})
        auth = GatekeeperAuthority("t")
        ev = _evidence(state, h.hypothesis_id)
        state.record_evidence(ev, auth)
        state.set_hypothesis_verdict(h.hypothesis_id, SUPPORTED, 0.8, ev.evidence_id, auth)
        state.lower_uncertainty(q.question_id, 0.2, auth)
        state.mark_visit(h.hypothesis_id, 0.8)
        return state

    def test_round_trip_preserves_beliefs_and_hash(self):
        state = self._populated()
        clone = EpistemicState.from_dict(state.to_dict())
        self.assertEqual(clone.state_hash(), state.state_hash())
        self.assertEqual(clone.total_uncertainty(), state.total_uncertainty())
        self.assertEqual(clone.hypotheses["h1"].status, SUPPORTED)
        self.assertEqual(clone.hypotheses["h1"].visits, 1)
        # New ids continue after the restored ones.
        self.assertEqual(clone.add_question("new", module="m").question_id, "q2")

    def test_hash_changes_with_beliefs_not_with_visits(self):
        state = self._populated()
        before = state.state_hash()
        state.mark_visit("h1", 0.1)
        self.assertEqual(state.state_hash(), before)
        auth = GatekeeperAuthority("t")
        state.lower_uncertainty("q1", 0.1, auth)
        self.assertNotEqual(state.state_hash(), before)

    def test_summary_names_ruled_out_mechanisms(self):
        state = EpistemicState("firm")
        q = state.add_question("why?", module="m")
        h = state.add_hypothesis(q.question_id, "the wrong regex anchor")
        auth = GatekeeperAuthority("t")
        ev = _evidence(state, h.hypothesis_id, matched=False)
        state.record_evidence(ev, auth)
        state.set_hypothesis_verdict(h.hypothesis_id, FALSIFIED, 0.0, ev.evidence_id, auth)
        text = state.summary()
        self.assertIn("RULED OUT", text)
        self.assertIn("the wrong regex anchor", text)

    def test_counts(self):
        state = self._populated()
        c = state.counts()
        self.assertEqual(c["questions"], 1)
        self.assertEqual(c["hypotheses_supported"], 1)
        self.assertEqual(c["evidence"], 1)


if __name__ == "__main__":
    unittest.main()
