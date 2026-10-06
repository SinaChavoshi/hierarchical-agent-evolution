"""Gen 16 pilot finding #6: the search must triage questions, not sweep them.

Pass 3 of the pilot seeded eighteen oracle questions for `elite_1`; the loop
picked the most uncertain one each move, so it proposed breadth-first across
all eighteen and ran out of budget before a single experiment. In pass 2,
`crossover_2` had a supported hypothesis on `q1` but wandered off to a fresh
`q2` (uncertainty 1.0 beats 0.105) and lost an iteration before synthesising.

The fix is a *frontier*: at most `frontier_size` questions are worked on at
once, one per cluster (same module, same normalised oracle failure), and the
next move goes to the question closest to paying off. Once a question is
resolved, its cluster siblings are deferred to the oracle, which certifies
them next iteration for free.
"""

import unittest

from hae.epistemic.gatekeeper import EvidenceGatekeeper, failure_signature
from hae.epistemic.ledger import CERTIFIED, Q_OPEN, Q_RESOLVED, SUPPORTED, EpistemicState, Question
from hae.epistemic.mcts import (
    STOP_BUDGET, STOP_DEFERRED, STOP_EXHAUSTED, STOP_RESOLVED, STOP_STAGNATION,
    EpistemicSearchLoop, question_cluster,
)
from hae.epistemic.moves import MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE
from hae.epistemic.value import EpistemicValueFunction
from hae.epistemic.genes import crossover_epistemic_policy, mutate_epistemic_policy
from hae.genome.schema import EPISTEMIC_POLICY_BOUNDS, EpistemicPolicyGene, GenomeValidationError
from tests.test_epistemic_search import FakeSystem1, SearchFixture, right, wrong

import random

PROPOSE, EXPERIMENT, SYNTHESIZE = MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE
ORIGINAL = "[calc] FAIL: test_add (t.T.test_add) -> AssertionError: 0 != 4"
FMT_FAILURE = "[fmt] FAIL: test_fmt (t.T.test_fmt) -> AssertionError: 'a' != 'b'"


def calc_failure(name, message="AssertionError: 0 != 4"):
    return f"[calc] FAIL: {name} (t.T.{name}) -> {message}"


class RoutedSystem1(FakeSystem1):
    """Proposal rounds keyed by the test name that seeded the question."""

    def __init__(self, ws, rounds_by_test, synth_fix=True):
        super().__init__(ws, rounds=[], synth_fix=synth_fix)
        self.rounds_by_test = {k: list(v) for k, v in rounds_by_test.items()}
        self.asked = []

    def propose(self, question, state, k):
        self.propose_calls += 1
        self.asked.append(question.question_id)
        for name, rounds in self.rounds_by_test.items():
            if f": {name} (" in question.source_failure:
                return rounds.pop(0) if rounds else []
        return []


class FrontierFixture(SearchFixture):
    """`SearchFixture` plus more oracle-seeded questions on top of `test_add`."""

    def seed(self, lines, iteration=1):
        self.gk.module_for_tag["fmt"] = "mypkg/fmt.py"
        self.gk.reconcile_with_oracle(self.state, [ORIGINAL] + list(lines), iteration=iteration)

    def loop_with_log(self, sys1, log, **policy_kw):
        kw = dict(enabled=True, search_budget_moves=20, min_hypotheses_before_synthesis=2,
                  low_prior_quota=0.0, max_stagnant_moves=6)
        kw.update(policy_kw)
        return EpistemicSearchLoop(self.state, self.gk, EpistemicValueFunction(0.6), EpistemicPolicyGene(**kw),
                                   sys1.propose, sys1.synthesize, rng_seed=7, logger=log.append)


class OneClusterTests(FrontierFixture):

    def test_eighteen_siblings_resolve_depth_first_and_defer_the_rest(self):
        self.seed([calc_failure(f"test_add_{i}") for i in range(2, 19)])
        self.assertEqual(len(self.state.questions), 18)
        self.assertEqual(len({question_cluster(q) for q in self.state.questions.values()}), 1)
        sys1 = RoutedSystem1(self.ws, {"test_add": [[wrong(0.8), right(0.2)]]})
        res = self.loop(sys1, search_budget_moves=40).run()
        # Before: 18 proposal rounds before the first experiment. Now: one
        # question is taken to resolution in four moves and the loop stops.
        self.assertEqual([m.move_type for m in res.trajectory], [PROPOSE, EXPERIMENT, EXPERIMENT, SYNTHESIZE])
        self.assertEqual({m.question_id for m in res.trajectory}, {self.q.question_id})
        self.assertEqual(res.stop_reason, STOP_DEFERRED)
        self.assertEqual(res.stats["frontier_admissions"], 1)
        self.assertEqual(res.stats["questions_deferred"], 17)
        self.assertEqual(sys1.propose_calls, 1)
        self.assertEqual(self.q.status, Q_RESOLVED)
        siblings = [q for q in self.state.questions.values() if q.question_id != self.q.question_id]
        self.assertTrue(all(q.status == Q_OPEN and q.uncertainty == 1.0 and not q.hypothesis_ids
                            for q in siblings))
        # Next iteration the oracle sees the fix and certifies all eighteen for free.
        out = self.gk.reconcile_with_oracle(self.state, [], iteration=2)
        self.assertEqual(len(out["certified"]), 18)
        self.assertTrue(all(q.status == CERTIFIED for q in self.state.questions.values()))

    def test_deferral_is_per_run_and_an_overturned_fix_frees_the_siblings(self):
        lines = [calc_failure(f"test_add_{i}") for i in range(2, 6)]
        self.seed(lines)
        first = self.loop(RoutedSystem1(self.ws, {"test_add": [[wrong(0.8), right(0.2)]]})).run()
        self.assertEqual(first.stop_reason, STOP_DEFERRED)
        self.assertEqual(first.stats["questions_deferred"], 4)
        # The oracle disagrees next iteration: the same five tests still fail.
        out = self.gk.reconcile_with_oracle(self.state, [ORIGINAL] + lines, iteration=2)
        self.assertEqual(out["reopened"], [self.q.question_id])
        second = self.loop(RoutedSystem1(self.ws, {}), max_stagnant_moves=4).run()
        # Nothing was resolved in this run, so nothing is deferred on last run's account.
        self.assertEqual(second.stats["questions_deferred"], 0)
        self.assertGreaterEqual(second.stats["frontier_admissions"], 1)
        self.assertGreater(second.moves_used, 0)
        self.assertEqual(second.stop_reason, STOP_STAGNATION)


class TwoClusterTests(FrontierFixture):

    def test_frontier_admits_one_open_question_per_cluster(self):
        self.seed([calc_failure("test_add_big", "AssertionError: 10 != 40"), FMT_FAILURE])
        q1, q2, q3 = sorted(self.state.questions)
        self.assertEqual(question_cluster(self.state.questions[q1]), question_cluster(self.state.questions[q2]))
        self.assertNotEqual(question_cluster(self.state.questions[q1]), question_cluster(self.state.questions[q3]))
        sys1 = RoutedSystem1(self.ws, {"test_add": [[wrong(0.8), right(0.2)]]})
        res = self.loop(sys1, frontier_size=3, max_hypothesis_rounds=1).run()
        # q1 (calc) and q3 (fmt) are admitted; q2 waits behind its calc sibling
        # and is deferred once q1 resolves. q3 gets its proposal round and
        # yields nothing, so the loop stops with q2 handed to the oracle.
        self.assertEqual(res.stats["frontier_admissions"], 2)
        self.assertEqual(res.stats["questions_deferred"], 1)
        self.assertEqual([(m.move_type, m.question_id) for m in res.trajectory],
                         [(PROPOSE, q1), (EXPERIMENT, q1), (EXPERIMENT, q1), (SYNTHESIZE, q1), (PROPOSE, q3)])
        self.assertEqual(sys1.asked, [q1, q3])
        self.assertEqual(res.stop_reason, STOP_DEFERRED)
        self.assertEqual(self.state.questions[q2].hypothesis_rounds, 0)

    def test_a_question_in_another_cluster_is_not_deferred(self):
        self.seed([FMT_FAILURE])
        q1, q2 = sorted(self.state.questions)
        sys1 = RoutedSystem1(self.ws, {"test_add": [[right(0.9), wrong(0.1)]]})
        res = self.loop(sys1, min_hypotheses_before_synthesis=2).run()
        moves = [(m.move_type, m.question_id) for m in res.trajectory]
        # crossover_2's shape: q1's supported hypothesis is carried through to
        # synthesis before the fresh, fully-uncertain q2 gets a single move.
        self.assertEqual(moves[:4], [(PROPOSE, q1), (EXPERIMENT, q1), (EXPERIMENT, q1), (SYNTHESIZE, q1)])
        self.assertEqual(self.state.questions[q1].status, Q_RESOLVED)
        # q2 is a different cluster (other module): it is worked, not deferred.
        self.assertEqual(moves[4:], [(PROPOSE, q2), (PROPOSE, q2)])
        self.assertEqual(res.stats["questions_deferred"], 0)
        self.assertEqual(res.stop_reason, STOP_EXHAUSTED)

    def test_supported_but_unsynthesised_question_from_last_run_goes_first(self):
        self.seed([FMT_FAILURE])
        q1, q2 = sorted(self.state.questions)
        sys1 = RoutedSystem1(self.ws, {"test_add": [[right(0.9), wrong(0.1)]]})
        first = self.loop(sys1).run(budget_moves=2)   # propose, experiment(right) -> SUPPORTED, budget
        self.assertEqual(first.stop_reason, STOP_BUDGET)
        supported = [h for h in self.state.hypotheses_for(q1) if h.status == SUPPORTED]
        self.assertEqual(len(supported), 1)
        self.assertFalse(supported[0].patch_applied)
        # q1 is now the *least* uncertain open question (elite_1: u=0.105 vs 1.0).
        self.assertLess(self.state.questions[q1].uncertainty, self.state.questions[q2].uncertainty)
        second = self.loop(RoutedSystem1(self.ws, {})).run()
        # The old most-uncertain-first rule spent the run on q2. Now q1 is finished first.
        self.assertEqual([(m.move_type, m.question_id) for m in second.trajectory[:2]],
                         [(EXPERIMENT, q1), (SYNTHESIZE, q1)])
        self.assertEqual(self.state.questions[q1].status, Q_RESOLVED)

    def test_frontier_size_one_admits_the_next_cluster_only_after_the_first_resolves(self):
        self.seed([FMT_FAILURE])
        q1, q2 = sorted(self.state.questions)
        wide_log, narrow_log = [], []
        wide = self.loop_with_log(RoutedSystem1(self.ws, {"test_add": [[wrong(0.8), right(0.2)]]}), wide_log,
                                  frontier_size=3, max_hypothesis_rounds=1).run()
        self.assertEqual(wide.stats["frontier_admissions"], 2)
        admitted = [i for i, line in enumerate(wide_log) if "frontier +=" in line]
        synthesized = [i for i, line in enumerate(wide_log) if "synthesized" in line][0]
        self.assertTrue(all(i < synthesized for i in admitted))   # both admitted up front
        # Fresh ledger, same scenario, frontier of one.
        self.setUp()
        self.seed([FMT_FAILURE])
        narrow = self.loop_with_log(RoutedSystem1(self.ws, {"test_add": [[wrong(0.8), right(0.2)]]}), narrow_log,
                                    frontier_size=1, max_hypothesis_rounds=1).run()
        self.assertEqual(narrow.stats["frontier_admissions"], 2)
        admitted = [i for i, line in enumerate(narrow_log) if "frontier +=" in line]
        synthesized = [i for i, line in enumerate(narrow_log) if "synthesized" in line][0]
        self.assertLess(admitted[0], synthesized)
        self.assertGreater(admitted[1], synthesized)                # q2 admitted only after q1 resolved
        self.assertEqual([m.question_id for m in narrow.trajectory], [q1] * 4 + [q2])
        self.assertIn(f"frontier size 1: 2 open question(s) in 2 cluster(s)", narrow_log[0])

    def test_single_question_behaviour_is_unchanged(self):
        """The existing single-question suites pin this; one explicit check here."""
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        res = self.loop(sys1).run()
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        self.assertEqual(res.stats["frontier_admissions"], 1)
        self.assertEqual(res.stats["questions_deferred"], 0)


class FailureSignatureTests(unittest.TestCase):

    def test_numbers_do_not_split_a_cluster(self):
        a = failure_signature(calc_failure("test_a", "AssertionError: 0 != 4"))
        b = failure_signature(calc_failure("test_b", "AssertionError: 7 != 12"))
        self.assertEqual(a, b)
        self.assertEqual(a, "AssertionError: # != #")

    def test_exception_class_and_message_shape_do_split(self):
        a = failure_signature(calc_failure("test_a", "AssertionError: 0 != 4"))
        t = failure_signature(calc_failure("test_a", "TypeError: unsupported operand type(s) for +: 'int' and 'str'"))
        lists = failure_signature(calc_failure("test_a", "AssertionError: [] != [1, 2]"))
        self.assertNotEqual(a, t)
        self.assertNotEqual(a, lists)
        self.assertTrue(t.startswith("TypeError: "))

    def test_paths_quotes_and_addresses_are_masked(self):
        a = failure_signature("[x] ERROR: test_a (m) -> ImportError: cannot import name 'foo' from 'mypkg.bar' (/app/mypkg/bar.py)")
        b = failure_signature("[x] ERROR: test_b (m) -> ImportError: cannot import name 'baz' from 'mypkg.bar' (/tmp/w/mypkg/bar.py)")
        self.assertEqual(a, b)
        self.assertNotIn("foo", a)
        self.assertNotIn("/app", a)
        c = failure_signature("[x] FAIL: t (m) -> AssertionError: <A at 0x7f3a10> != <A at 0x7f3b20>")
        d = failure_signature("[x] FAIL: t (m) -> AssertionError: <A at 0x10> != <A at 0x20>")
        self.assertEqual(c, d)

    def test_lines_without_an_exception_have_no_signature(self):
        self.assertEqual(failure_signature("[calc] FAIL: test_a (t.T.test_a)"), "")
        self.assertEqual(failure_signature(""), "")
        self.assertEqual(failure_signature("[calc] FAIL: test_a (t.T.test_a) -> "), "")

    def test_question_cluster_keys(self):
        seeded = Question("q1", "why?", module="mypkg/calc.py", source_failure=calc_failure("test_a"))
        twin = Question("q2", "why else?", module="mypkg/calc.py", source_failure=calc_failure("test_b", "AssertionError: 1 != 9"))
        other_module = Question("q3", "why?", module="mypkg/fmt.py", source_failure=calc_failure("test_a"))
        asked = Question("q4", "Is the cache stale?", module="mypkg/calc.py")
        self.assertEqual(question_cluster(seeded), "mypkg/calc.py|AssertionError: # != #")
        self.assertEqual(question_cluster(seeded), question_cluster(twin))
        self.assertNotEqual(question_cluster(seeded), question_cluster(other_module))
        self.assertEqual(question_cluster(asked), "q:q4")


class FrontierGeneTests(unittest.TestCase):

    def test_default_and_bounds(self):
        self.assertEqual(EpistemicPolicyGene().frontier_size, 3)
        self.assertEqual(EPISTEMIC_POLICY_BOUNDS["frontier_size"], (1, 8, True))
        for bad in (0, 9, -1):
            with self.assertRaises(GenomeValidationError):
                EpistemicPolicyGene(frontier_size=bad)
        self.assertEqual(EpistemicPolicyGene(frontier_size=2.6).frontier_size, 3)

    def test_legacy_genomes_without_the_field_deserialise_with_the_default(self):
        gene = EpistemicPolicyGene.from_dict({"enabled": True, "search_budget_moves": 60, "branching_k": 3})
        self.assertEqual(gene.frontier_size, 3)
        self.assertEqual(gene.to_dict()["frontier_size"], 3)
        self.assertEqual(EpistemicPolicyGene.from_dict(gene.to_dict()), gene)

    def test_operators_keep_frontier_size_in_bounds(self):
        rng = random.Random(3)
        a = EpistemicPolicyGene(enabled=True, frontier_size=1)
        b = EpistemicPolicyGene(enabled=True, frontier_size=8)
        for _ in range(200):
            child = mutate_epistemic_policy(crossover_epistemic_policy(a, b, rng), rng, strength=0.5)
            self.assertTrue(1 <= child.frontier_size <= 8)
            self.assertIsInstance(child.frontier_size, int)


if __name__ == "__main__":
    unittest.main()
