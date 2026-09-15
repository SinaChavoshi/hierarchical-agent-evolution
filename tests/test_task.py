"""Task, Budget and Verifier.

These three exist to make the objective, its ground truth, its spend ceiling
and its action space into *parameters*. The tests below mostly check refusals,
because the value of the abstraction is in what it declines to do silently.
"""

import threading
import unittest

from hae.task import (
    Budget,
    BudgetExceeded,
    CompositeVerifier,
    ExecutionGateVerifier,
    NullVerifier,
    Submission,
    Task,
    TaskError,
    VerificationOutcome,
    Verifier,
    legacy_task,
)


class _StubVerifier(Verifier):
    def __init__(self, name, score, evaluable=True, gates=None):
        self.name = name
        self._score = score
        self._evaluable = evaluable
        self._gates = gates or {}

    def verify(self, submission):
        return VerificationOutcome(
            verifier=self.name, evaluable=self._evaluable,
            score=self._score if self._evaluable else None,
            gate_status=self._gates)


class BudgetCeilingTest(unittest.TestCase):

    def test_ordinary_calls_stop_at_the_working_limit(self):
        b = Budget(limit_usd=1.0, reserve_fraction=0.1)
        self.assertTrue(b.charge(0.95, "work"))
        self.assertFalse(b.charge(0.01, "more work"))
        self.assertEqual(b.refusals, 1)

    def test_reserve_keeps_the_final_synthesis_affordable(self):
        """A firm that overspends should return a degraded deliverable, not
        none at all: the work was done, and discarding the write-up wastes it."""
        b = Budget(limit_usd=1.0, reserve_fraction=0.2)
        b.charge(0.85, "departments")
        self.assertFalse(b.charge(0.01, "more departments"))
        self.assertTrue(b.charge(0.10, "ceo synthesis", reserved=True))

    def test_reserve_is_not_infinite(self):
        b = Budget(limit_usd=1.0, reserve_fraction=0.2)
        b.charge(1.0, "everything")
        self.assertFalse(b.charge(0.01, "synthesis", reserved=True))

    def test_call_cap_is_independent_of_dollars(self):
        """A cheap model in a tight loop burns quota without approaching a
        dollar limit.

        `reserve_fraction=0.0` isolates the raw ceiling. With a reserve
        configured, one of the two calls is held back for the synthesis --
        see `test_call_cap_holds_back_the_reserve`.
        """
        b = Budget(limit_usd=1000.0, max_calls=2, reserve_fraction=0.0)
        self.assertTrue(b.charge(0.001, "a"))
        self.assertTrue(b.charge(0.001, "b"))
        self.assertFalse(b.charge(0.001, "c"))

    def test_call_cap_holds_back_the_reserve(self):
        """With a reserve, the last call belongs to the synthesis.

        This is the ceiling that binds in practice: at the rate measured from
        Gen 11 (~$0.0077/call) a firm exhausts `max_calls` long before
        `limit_usd`, so without this the closing synthesis is the thing that
        gets cut.
        """
        b = Budget(limit_usd=1000.0, max_calls=2)
        self.assertTrue(b.charge(0.001, "a"))
        self.assertFalse(b.charge(0.001, "b"))              # ordinary: refused
        self.assertTrue(b.charge(0.001, "synthesis", reserved=True))
        self.assertFalse(b.charge(0.001, "c", reserved=True))  # now truly done

    def test_strict_mode_raises(self):
        b = Budget(limit_usd=0.10, reserve_fraction=0.0)
        b.charge(0.10, "all of it")
        with self.assertRaises(BudgetExceeded):
            b.charge(0.01, "over", strict=True)

    def test_overrun_is_distinct_from_exhausted(self):
        """Finishing exactly at the line is not the same as being stopped."""
        b = Budget(limit_usd=1.0, reserve_fraction=0.0)
        b.charge(1.0, "exact")
        self.assertTrue(b.exhausted)
        self.assertFalse(b.overrun)

    def test_concurrent_charges_do_not_race_past_the_ceiling(self):
        """Department pods bill from several threads at once."""
        b = Budget(limit_usd=1.0, reserve_fraction=0.0)
        accepted = []

        def worker():
            for _ in range(50):
                accepted.append(b.charge(0.01, "t"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 100 cents of headroom, so at most 100 charges may be accepted.
        self.assertLessEqual(sum(1 for a in accepted if a), 100)
        self.assertLessEqual(b.spent_usd, 1.0 + 1e-9)

    def test_rejects_nonsense_construction(self):
        with self.assertRaises(ValueError):
            Budget(limit_usd=0)
        with self.assertRaises(ValueError):
            Budget(limit_usd=1.0, reserve_fraction=1.0)
        with self.assertRaises(ValueError):
            Budget(limit_usd=1.0).charge(-1.0)


class VerifierTest(unittest.TestCase):

    def test_null_verifier_is_not_evaluable(self):
        """The absence of a measurement must never read as a measurement --
        this is the error behind the retracted Gen 9 and Gen 10 claims."""
        o = NullVerifier().verify(Submission())
        self.assertFalse(o.evaluable)
        self.assertIsNone(o.score)

    def test_evaluable_outcome_must_carry_a_score(self):
        with self.assertRaises(ValueError):
            VerificationOutcome(verifier="x", evaluable=True, score=None)

    def test_score_outside_range_is_rejected(self):
        with self.assertRaises(ValueError):
            VerificationOutcome(verifier="x", evaluable=True, score=101.0)

    def test_composite_renormalises_over_evaluable_members(self):
        """An unavailable member should reduce confidence, not drag the score
        toward zero."""
        c = CompositeVerifier(
            [_StubVerifier("a", 80.0), _StubVerifier("b", None, evaluable=False)],
            weights=[1.0, 3.0])
        o = c.verify(Submission())
        self.assertTrue(o.evaluable)
        self.assertEqual(o.score, 80.0)

    def test_composite_weights_are_honoured(self):
        c = CompositeVerifier(
            [_StubVerifier("a", 100.0), _StubVerifier("b", 0.0)],
            weights=[3.0, 1.0])
        self.assertEqual(c.verify(Submission()).score, 75.0)

    def test_composite_with_nothing_evaluable_is_not_evaluable(self):
        c = CompositeVerifier([_StubVerifier("a", None, evaluable=False)])
        self.assertFalse(c.verify(Submission()).evaluable)

    def test_composite_merges_gate_status_for_the_judge(self):
        c = CompositeVerifier([
            _StubVerifier("a", 50.0, gates={"syntax": "passed"}),
            _StubVerifier("b", 50.0, gates={"tests": "failed"}),
        ])
        self.assertEqual(c.verify(Submission()).gate_status,
                         {"syntax": "passed", "tests": "failed"})

    def test_empty_composite_is_rejected(self):
        with self.assertRaises(ValueError):
            CompositeVerifier([])


class TaskDeclarationTest(unittest.TestCase):

    def test_unknown_keys_are_rejected(self):
        """A silently ignored setting is a run that did not do what its config
        says it did."""
        with self.assertRaises(TaskError) as ctx:
            Task.from_dict({"task_id": "t", "objective": "o", "iterations": 5})
        self.assertIn("iterations", str(ctx.exception))

    def test_unknown_capability_is_rejected(self):
        with self.assertRaises(TaskError):
            Task(task_id="t", objective="o", capabilities=frozenset({"net.raw"}))

    def test_conflicting_verifier_declarations_are_rejected(self):
        with self.assertRaises(TaskError):
            Task.from_dict({"task_id": "t", "objective": "o",
                            "verifier": "execution-gates",
                            "benchmark_task": "artifacts"})

    def test_benchmark_verifier_requires_a_task_id(self):
        with self.assertRaises(TaskError):
            Task.from_dict({"task_id": "t", "objective": "o",
                            "verifier": "benchmark"})

    def test_unknown_verifier_name_is_rejected(self):
        with self.assertRaises(TaskError):
            Task.from_dict({"task_id": "t", "objective": "o",
                            "verifier": "vibes"})

    def test_empty_objective_is_rejected(self):
        with self.assertRaises(TaskError):
            Task(task_id="t", objective="   ")

    def test_default_verifier_is_the_v1_behaviour(self):
        t = Task.from_dict({"task_id": "t", "objective": "o"})
        self.assertIsInstance(t.verifier, ExecutionGateVerifier)
        self.assertTrue(t.is_verified)

    def test_null_verifier_marks_the_task_unverified(self):
        t = Task.from_dict({"task_id": "t", "objective": "o", "verifier": "none"})
        self.assertFalse(t.is_verified)

    def test_budget_is_built_from_the_declaration(self):
        t = Task.from_dict({"task_id": "t", "objective": "o",
                            "budget_usd": 2.5, "max_calls": 40})
        self.assertEqual(t.budget.limit_usd, 2.5)
        self.assertEqual(t.budget.max_calls, 40)

    def test_carry_artifacts_defaults_off(self):
        """It changes what a fitness trajectory means, so it must be opt-in."""
        self.assertFalse(Task.from_dict(
            {"task_id": "t", "objective": "o"}).carry_artifacts)

    def test_legacy_task_reproduces_v1(self):
        t = legacy_task()
        self.assertTrue(t.is_verified)
        self.assertEqual(t.max_iterations, 1)
        self.assertFalse(t.carry_artifacts)
        self.assertIsNone(t.budget)

    def test_capabilities_round_trip(self):
        t = Task.from_dict({"task_id": "t", "objective": "o",
                            "capabilities": ["workspace.read"]})
        self.assertTrue(t.allows("workspace.read"))
        self.assertFalse(t.allows("workspace.shell"))

    def test_benchmark_verifier_allows_firm_to_write_own_scratch_tests(self):
        """Regression test for Issue #8: BenchmarkVerifier projects submission
        files down to target_module so a firm writing tests/test_scratch.py in
        its workspace is graded on target_module rather than rejected."""
        from hae.task.verifier import BenchmarkVerifier, Submission
        with open("hae/evaluation/artifacts.py", "r", encoding="utf-8") as fh:
            real_artifacts = fh.read()
        verifier = BenchmarkVerifier("artifacts")
        submission = Submission(files={
            "hae/evaluation/artifacts.py": real_artifacts,
            "tests/test_my_scratch.py": "def test_scratch(): assert True\n",
            "README.md": "We wrote tests!",
        })
        outcome = verifier.verify(submission)
        self.assertTrue(outcome.evaluable)
        self.assertEqual(outcome.score, 100.0)


if __name__ == "__main__":
    unittest.main()
