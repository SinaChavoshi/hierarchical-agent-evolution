"""Gen 16 pilot finding #3: a refused probe must park the hypothesis, not feed PUCT forever.

Pass 2 of the pilot: the gatekeeper refused one probe (invalid Python) but the
hypothesis stayed UNVERIFIED with its visit count growing, so PUCT re-selected
it 17 times and three iterations burnt their whole budget on the same syntax
error, while a SUPPORTED hypothesis on a sibling question starved.

The fix: a refused probe moves the hypothesis to UNTESTABLE. That is not a
verdict (no belief changes, nothing is ruled out, the mechanism is not tabu),
it just leaves the untested pool until a proposer repairs the probe.
"""

import unittest

from hae.epistemic.audit import build_epistemic_audit
from hae.epistemic.ledger import (
    FALSIFIED, Q_RESOLVED, SUPPORTED, UNTESTABLE, UNVERIFIED, Evidence, LedgerError,
)
from hae.epistemic.mcts import STOP_EXHAUSTED, STOP_RESOLVED
from hae.epistemic.moves import MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE, HypothesisProposal
from hae.epistemic.value import calibration_report
from tests.test_epistemic_search import PROBE, FakeSystem1, SearchFixture, right, wrong

BROKEN_PROBE = "from mypkg.calc import add\nprint('ADD', add(2, 2)\n"   # '(' never closed


def broken_right(prior=0.5):
    """The true mechanism, but with a probe the gatekeeper will refuse to run."""
    return HypothesisProposal("add subtracts because the operator is minus", "operator bug", prior,
                              BROKEN_PROBE, {"expect_exit_code": 0, "expect_stdout_contains": "ADD 0"})


def broken_wrong(prior=0.4):
    return HypothesisProposal("add is correct and the caller passes bad args", "caller bug", prior,
                              BROKEN_PROBE, {"expect_exit_code": 0, "expect_stdout_contains": "ADD 4"})


class RefusedProbeParksTheHypothesisTests(SearchFixture):

    def test_refused_probe_costs_one_move_not_the_whole_budget(self):
        sys1 = FakeSystem1(self.ws, rounds=[[broken_right(0.5)]])
        res = self.loop(sys1, max_hypothesis_rounds=1, search_budget_moves=20).run()
        h = next(iter(self.state.hypotheses.values()))
        self.assertEqual(h.status, UNTESTABLE)
        self.assertFalse(h.tested)
        self.assertEqual(h.visits, 1)
        self.assertEqual(h.probe_rejections, 1)
        self.assertIn("not valid Python", h.last_rejection)
        # One propose + one refused experiment, then nothing left to do.
        self.assertEqual([m.move_type for m in res.trajectory], [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT])
        self.assertEqual(res.stop_reason, STOP_EXHAUSTED)
        self.assertEqual(res.stats["rejected_probes"], 1)
        # Not a verdict: nothing ruled out, nothing tabu, no uncertainty moved.
        self.assertEqual(self.state.ruled_out, [])
        self.assertFalse(self.state.is_tabu(h.mechanism_signature, self.q.question_id))
        self.assertEqual(res.delta_u_total, 0.0)
        self.assertEqual(self.state.untested_hypotheses(self.q.question_id), [])
        self.assertEqual([x.hypothesis_id for x in self.state.untestable_hypotheses(self.q.question_id)],
                         [h.hypothesis_id])

    def test_audit_does_not_count_a_refused_probe_as_tested(self):
        sys1 = FakeSystem1(self.ws, rounds=[[broken_right(0.5)]])
        res = self.loop(sys1, max_hypothesis_rounds=1).run()
        audit = build_epistemic_audit(self.state, res.stats, budget_moves=20)
        self.assertEqual(audit["hypotheses_tested"], 0)
        self.assertEqual(audit["hypotheses_untestable"], 1)
        self.assertEqual(audit["probes_refused"], 1)
        self.assertEqual(audit["probe_repairs"], 0)
        self.assertEqual(audit["calibration_tested"], 0)
        self.assertEqual(audit["status_inconsistencies"], 0)
        self.assertEqual(audit["unbacked_claims"], 0)

    def test_untestable_sibling_does_not_block_or_unlock_synthesis(self):
        # right(0.6) is SUPPORTED on the first experiment; the broken sibling
        # is tried once, refused, and then the loop synthesises instead of
        # re-running the refused probe until stagnation.
        sys1 = FakeSystem1(self.ws, rounds=[[right(0.6), broken_wrong(0.4)]])
        res = self.loop(sys1, max_hypothesis_rounds=1, min_hypotheses_before_synthesis=2).run()
        types = [m.move_type for m in res.trajectory]
        self.assertEqual(types, [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_RUN_EXPERIMENT,
                                 MOVE_SYNTHESIZE])
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        self.assertEqual(self.q.status, Q_RESOLVED)
        statuses = sorted(h.status for h in self.state.hypotheses.values())
        self.assertEqual(statuses, sorted([SUPPORTED, UNTESTABLE]))


class ProbeRepairTests(SearchFixture):

    def test_reproposing_the_mechanism_with_a_valid_probe_repairs_it(self):
        sys1 = FakeSystem1(self.ws, rounds=[[broken_right(0.5), wrong(0.8)], [right(0.5)]])
        res = self.loop(sys1, max_hypothesis_rounds=2, min_hypotheses_before_synthesis=2).run()
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        # Two hypotheses in total: the repair reused the parked one.
        self.assertEqual(len(self.state.hypotheses), 2)
        self.assertEqual(res.stats["hypotheses_accepted"], 2)
        self.assertEqual(res.stats["probe_repairs"], 1)
        self.assertEqual(res.stats["duplicate_rejections"], 0)
        repaired = next(h for h in self.state.hypotheses.values() if h.mechanism == "operator bug")
        self.assertEqual(repaired.status, SUPPORTED)
        self.assertEqual(repaired.probe_code, PROBE)
        self.assertEqual(repaired.probe_rejections, 1)
        self.assertAlmostEqual(repaired.prior, 0.5)      # the original belief is what gets scored
        other = next(h for h in self.state.hypotheses.values() if h.mechanism != "operator bug")
        self.assertEqual(other.status, FALSIFIED)
        self.assertEqual(calibration_report(self.state)["tested"], 2)
        # The proposer was told about the refused probe before its second round.
        self.assertEqual(sys1.propose_calls, 2)
        self.assertIn("PROBE REFUSED", sys1.seen_ledgers[1])
        self.assertNotIn("PROBE REFUSED", sys1.seen_ledgers[0])
        # The repair is visible in the move log.
        propose_notes = [m.note for m in res.trajectory if m.move_type == MOVE_PROPOSE_HYPOTHESIS]
        self.assertIn("1 repaired", propose_notes[1])

    def test_resubmitting_the_identical_broken_probe_is_a_duplicate_not_a_repair(self):
        sys1 = FakeSystem1(self.ws, rounds=[[broken_right(0.5)], [broken_right(0.5)]])
        res = self.loop(sys1, max_hypothesis_rounds=2).run()
        h = next(iter(self.state.hypotheses.values()))
        self.assertEqual(h.status, UNTESTABLE)
        self.assertEqual(res.stats["probe_repairs"], 0)
        self.assertEqual(res.stats["duplicate_rejections"], 1)
        self.assertEqual(res.stats["rejected_probes"], 1)
        self.assertEqual([m.move_type for m in res.trajectory],
                         [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_PROPOSE_HYPOTHESIS])
        self.assertEqual(res.stop_reason, STOP_EXHAUSTED)


class LedgerUntestableContractTests(SearchFixture):

    def _parked(self):
        h = self.state.add_hypothesis(self.q.question_id, "add subtracts because the operator is minus",
                                      "operator bug", 0.5, BROKEN_PROBE, {"expect_exit_code": 0})
        ev = self.gk.run_experiment(self.state, h)
        self.assertEqual(ev.kind, "probe_rejected")
        self.gk.apply(self.state, h, ev)
        self.assertEqual(h.status, UNTESTABLE)
        return h, ev

    def test_mark_untestable_needs_gatekeeper_authority(self):
        h = self.state.add_hypothesis(self.q.question_id, "x", "operator bug", 0.5, BROKEN_PROBE, {})
        ev = Evidence(evidence_id=self.state.new_evidence_id(), kind="probe_rejected",
                      hypothesis_id=h.hypothesis_id, question_id=self.q.question_id,
                      command="", exit_code=-2, prediction={}, matched_prediction=False, detail="rejected: x")
        self.state.record_evidence(ev, self.gk.authority)
        with self.assertRaises(LedgerError):
            self.state.mark_untestable(h.hypothesis_id, ev.evidence_id, "x", authority=object())
        self.assertEqual(h.status, UNVERIFIED)

    def test_mark_untestable_needs_probe_rejected_evidence_on_the_log(self):
        h = self.state.add_hypothesis(self.q.question_id, "x", "operator bug", 0.5, PROBE, {})
        with self.assertRaises(LedgerError):
            self.state.mark_untestable(h.hypothesis_id, "ev_missing", "x", self.gk.authority)
        ev = Evidence(evidence_id=self.state.new_evidence_id(), kind="probe",
                      hypothesis_id=h.hypothesis_id, question_id=self.q.question_id,
                      command="python p.py", exit_code=0, prediction={}, matched_prediction=True, detail="held")
        self.state.record_evidence(ev, self.gk.authority)
        with self.assertRaises(LedgerError):
            self.state.mark_untestable(h.hypothesis_id, ev.evidence_id, "x", self.gk.authority)
        self.assertEqual(h.status, UNVERIFIED)

    def test_repair_probe_only_applies_to_untestable_hypotheses(self):
        h, _ = self._parked()
        with self.assertRaises(LedgerError):
            self.state.repair_probe(h.hypothesis_id, "   ")
        self.assertEqual(h.status, UNTESTABLE)
        self.state.repair_probe(h.hypothesis_id, PROBE, {"expect_exit_code": 0, "expect_stdout_contains": "ADD 0"})
        self.assertEqual(h.status, UNVERIFIED)
        self.assertEqual(h.probe_code, PROBE)
        with self.assertRaises(LedgerError):          # not parked any more
            self.state.repair_probe(h.hypothesis_id, PROBE)
        ev = self.gk.run_experiment(self.state, h)
        self.assertGreater(self.gk.apply(self.state, h, ev), 0.0)
        self.assertEqual(h.status, SUPPORTED)

    def test_untestable_survives_a_serialisation_round_trip(self):
        h, _ = self._parked()
        clone = type(self.state).from_dict(self.state.to_dict())
        h2 = clone.hypotheses[h.hypothesis_id]
        self.assertEqual(h2.status, UNTESTABLE)
        self.assertEqual(h2.probe_rejections, 1)
        self.assertEqual(h2.last_rejection, h.last_rejection)
        self.assertEqual(clone.counts()["hypotheses_untestable"], 1)
        self.assertEqual(clone.state_hash(), self.state.state_hash())


if __name__ == "__main__":
    unittest.main()
