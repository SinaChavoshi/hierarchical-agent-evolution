"""Gen 16 pilot finding #4: the tabu memory was lexical enough to be paraphrased around.

gen_14_mutant_1 (pass 2) proposed 18 hypotheses and had all 18 falsified --
but seven of them restated a mechanism the ledger had already ruled out, with
the *same claim* and a reworded mechanism paragraph. Jaccard on the full
signature scored them 0.46-0.71, under the 0.8 tabu threshold, so roughly
ten of the eighteen experiments re-falsified known-dead mechanisms.

`same_mechanism` adds two tests that catch this without catching genuinely
distinct mechanisms on the same question: containment of one signature in
the other (overlap coefficient) and near-identical claims. The texts below
are the pilot's own hypotheses, abridged.
"""

import unittest

from hae.epistemic.ledger import (
    FALSIFIED, EpistemicState, GatekeeperAuthority, Evidence, mechanism_signature,
    same_mechanism, signature_overlap, signature_similarity,
)
from hae.epistemic.mcts import STOP_EXHAUSTED
from hae.epistemic.moves import HypothesisProposal
from tests.test_epistemic_search import PROBE, FakeSystem1, SearchFixture, wrong

# h7 / h10 / h15 of gen_14_mutant_1: identical claim, three mechanism paragraphs.
CLAIM_A = ("The `verify_directory` method calculates `authored_files` by counting all `.py` files "
           "found in the directory scan (including test files) instead of applying the exclusion filter.")
MECH_A1 = ("The `authored_files` integer in the `VerificationReport` is derived from a count of all Python "
           "files discovered during the scan; the basename check that should exclude `test_*.py` files "
           "appears to be applied only when computing the smoke gate, not when counting.")
MECH_A2 = ("The `verify_directory` method calculates `authored_files` by counting all `.py` files found in "
           "the directory scan. The test-file filter appears to be applied to the files passed to the "
           "smoke gate but not to the files that are counted, so the count includes the basename "
           "`test_*.py` entries.")
MECH_A3 = ("The `verify_directory` method calculates `authored_files` by counting all `.py` files found in "
           "the directory scan; the correct basename exclusion is applied elsewhere but not before the "
           "count, which is why the count appears two higher than expected.")

# h12 of the same firm: shares vocabulary with the above but names a different mechanism.
CLAIM_B = ("The `verify_directory` method calculates `authored_files` by counting all files in the "
           "directory scan (or the provided `authored` dict) without filtering by the `.py` extension, "
           "so non-Python files inflate the count.")
MECH_B = ("The count includes `.md`, `.txt` and `__init__` files because the filter only checks the "
          "basename for `test_` and never checks the file extension; the dict path has the same gap.")

# h2: a genuinely different mechanism on the same question.
CLAIM_C = ("The `count_source_files` function in `artifacts.py` is not used by the harness, which "
           "instead globs the tree itself and picks up `__pycache__` artefacts.")
MECH_C = "A naive `os.walk` in the harness counts compiled artefacts under `__pycache__` as sources."


class SameMechanismRuleTests(unittest.TestCase):

    def test_pilot_paraphrases_collide(self):
        sigs = [mechanism_signature(CLAIM_A, m) for m in (MECH_A1, MECH_A2, MECH_A3)]
        for i in range(3):
            for j in range(i + 1, 3):
                # The old rule (full-signature Jaccard >= 0.8) let these through...
                self.assertLess(signature_similarity(sigs[i], sigs[j]), 0.8)
                # ...the new rule catches them.
                self.assertTrue(same_mechanism(sigs[i], sigs[j], CLAIM_A, CLAIM_A), (i, j))

    def test_distinct_mechanisms_sharing_vocabulary_do_not_collide(self):
        a = mechanism_signature(CLAIM_A, MECH_A1)
        b = mechanism_signature(CLAIM_B, MECH_B)
        c = mechanism_signature(CLAIM_C, MECH_C)
        self.assertFalse(same_mechanism(a, b, CLAIM_A, CLAIM_B))
        self.assertFalse(same_mechanism(a, c, CLAIM_A, CLAIM_C))
        self.assertFalse(same_mechanism(b, c, CLAIM_B, CLAIM_C))
        # Margins, so a small wording change cannot flip the verdict.
        self.assertLess(signature_overlap(a, b), 0.7)
        self.assertLess(signature_similarity(mechanism_signature(CLAIM_A), mechanism_signature(CLAIM_B)), 0.6)

    def test_containment_catches_a_longer_elaboration_of_the_same_mechanism(self):
        short = mechanism_signature("the stdlib fallback runner never instantiates unittest.TestCase subclasses")
        long_ = mechanism_signature(
            "the stdlib fallback runner never instantiates unittest.TestCase subclasses",
            "because discovery only collects bare functions, every TestCase subclass is skipped and the "
            "suite reports failed instead of passed")
        self.assertLess(signature_similarity(short, long_), 0.8)
        self.assertGreaterEqual(signature_overlap(short, long_), 0.75)
        self.assertTrue(same_mechanism(short, long_))

    def test_a_trivially_short_signature_cannot_trigger_containment(self):
        long_ = mechanism_signature(CLAIM_A, MECH_A1)
        tiny = mechanism_signature("the count of the basename")   # 2 tokens, both in the long one
        self.assertEqual(signature_overlap(tiny, long_), 1.0)
        self.assertFalse(same_mechanism(tiny, long_))

    def test_empty_signatures_never_match(self):
        self.assertFalse(same_mechanism("", mechanism_signature(CLAIM_A)))
        self.assertFalse(same_mechanism(mechanism_signature(CLAIM_A), ""))


class LedgerTabuWithClaimTests(unittest.TestCase):

    def setUp(self):
        self.state = EpistemicState("tabu_firm")
        self.auth = GatekeeperAuthority()
        self.q = self.state.add_question("Why does test_generated_files_are_not_counted fail?", "hae/evaluation/harness.py")
        self.h = self.state.add_hypothesis(self.q.question_id, CLAIM_A, MECH_A1, 0.45, PROBE, {"expect_exit_code": 0})
        ev = Evidence(evidence_id=self.state.new_evidence_id(), kind="probe", hypothesis_id=self.h.hypothesis_id,
                      question_id=self.q.question_id, command="python p.py", exit_code=0, prediction={},
                      matched_prediction=False, detail="prediction failed")
        self.state.record_evidence(ev, self.auth)
        self.state.set_hypothesis_verdict(self.h.hypothesis_id, FALSIFIED, 0.0, ev.evidence_id, self.auth)

    def test_reworded_mechanism_with_the_same_claim_is_tabu(self):
        for mech in (MECH_A2, MECH_A3):
            sig = mechanism_signature(CLAIM_A, mech)
            self.assertTrue(self.state.is_tabu(sig, self.q.question_id, claim=CLAIM_A), mech[:40])
            # Even without the claim hint, containment catches two of the three.
        self.assertIsNotNone(self.state.tabu_match(mechanism_signature(CLAIM_A, MECH_A2), self.q.question_id, CLAIM_A))

    def test_different_mechanism_on_the_same_question_is_not_tabu(self):
        self.assertFalse(self.state.is_tabu(mechanism_signature(CLAIM_B, MECH_B), self.q.question_id, claim=CLAIM_B))
        self.assertFalse(self.state.is_tabu(mechanism_signature(CLAIM_C, MECH_C), self.q.question_id, claim=CLAIM_C))

    def test_question_scoping_is_preserved(self):
        q2 = self.state.add_question("Why does another test fail?", "hae/evaluation/harness.py")
        sig = mechanism_signature(CLAIM_A, MECH_A2)
        self.assertFalse(self.state.is_tabu(sig, q2.question_id, claim=CLAIM_A))
        self.assertTrue(self.state.is_tabu(sig, None, claim=CLAIM_A))


class SearchLoopTabuTests(SearchFixture):

    def test_paraphrased_re_proposal_of_a_falsified_mechanism_costs_no_experiment(self):
        falsified = wrong(0.8)                       # "add is correct and the caller passes bad args" / "caller bug"
        paraphrase = HypothesisProposal(
            falsified.claim,
            "the caller bug is in the harness: it builds the arguments from a config and passes them in the "
            "wrong order, so add receives bad args although its own code is correct",
            0.7, PROBE, {"expect_exit_code": 0, "expect_stdout_contains": "ADD 4"})
        self.assertLess(signature_similarity(falsified.signature, paraphrase.signature), 0.8)
        sys1 = FakeSystem1(self.ws, rounds=[[falsified], [paraphrase]])
        res = self.loop(sys1, max_hypothesis_rounds=2, min_hypotheses_before_synthesis=1).run()
        self.assertEqual(res.stats["tabu_rejections"], 1)
        self.assertEqual(res.stats["experiments"], 1)
        self.assertEqual(len(self.state.hypotheses), 1)
        self.assertEqual(res.stop_reason, STOP_EXHAUSTED)


if __name__ == "__main__":
    unittest.main()
