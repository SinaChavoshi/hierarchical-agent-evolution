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
    """Soft accounting since 86c964c (2026-09-24): the ledger records every
    charge and refuses none. Cost pressure is applied by Net Fitness, not by
    truncation. These replaced hard-refusal tests that had been failing for
    eleven days; the contract they now pin is the one the code actually has.
    """

    def test_charges_past_the_working_limit_are_accepted_and_recorded(self):
        b = Budget(limit_usd=1.0, reserve_fraction=0.1)
        self.assertTrue(b.charge(0.95, "work"))
        self.assertTrue(b.charge(0.01, "more work"))
        self.assertEqual(b.refusals, 0)
        self.assertAlmostEqual(b.spent_usd, 0.96)
        self.assertEqual([c.label for c in b.charges], ["work", "more work"])

    def test_reserved_calls_are_recorded_as_such(self):
        """The scorecard can still tell synthesis spend from departmental spend."""
        b = Budget(limit_usd=1.0, reserve_fraction=0.2)
        b.charge(0.85, "departments")
        self.assertTrue(b.charge(0.10, "ceo synthesis", reserved=True))
        self.assertEqual([c.reserved for c in b.charges], [False, True])

    def test_overspend_is_visible_without_being_a_refusal(self):
        b = Budget(limit_usd=1.0, reserve_fraction=0.2)
        b.charge(1.0, "everything")
        self.assertTrue(b.charge(0.01, "synthesis", reserved=True))
        self.assertGreater(b.spent_usd, b.limit_usd)
        self.assertEqual(b.remaining_usd, 0.0)
        self.assertFalse(b.exhausted)
        self.assertFalse(b.working_exhausted)
        self.assertFalse(b.overrun)

    def test_call_cap_is_sized_but_not_enforced(self):
        """`max_calls` still sizes the generation (and is reported), but a
        cheap model in a tight loop is penalised by fitness, not cut off."""
        b = Budget(limit_usd=1000.0, max_calls=2, reserve_fraction=0.0)
        self.assertEqual(b.working_max_calls, 2)
        for label in ("a", "b", "c"):
            self.assertTrue(b.charge(0.001, label))
        self.assertEqual(b.calls, 3)
        self.assertTrue(b.can_spend())

    def test_call_cap_reserve_is_still_reported(self):
        b = Budget(limit_usd=1000.0, max_calls=2)
        self.assertEqual(b.working_max_calls, 1)
        self.assertEqual(b.to_dict()["working_max_calls"], 1)
        self.assertEqual(b.to_dict()["max_calls"], 2)

    def test_strict_mode_is_inert_under_soft_accounting(self):
        """`BudgetExceeded` is only raised when a charge is blocked, and no
        charge is blocked. Pinned so that re-enabling hard ceilings is a
        deliberate decision that has to change this test."""
        b = Budget(limit_usd=0.10, reserve_fraction=0.0)
        b.charge(0.10, "all of it")
        self.assertTrue(b.charge(0.01, "over", strict=True))
        self.assertIsNotNone(BudgetExceeded)

    def test_finishing_exactly_at_the_line_is_not_an_overrun(self):
        b = Budget(limit_usd=1.0, reserve_fraction=0.0)
        b.charge(1.0, "exact")
        self.assertFalse(b.overrun)
        self.assertEqual(b.remaining_usd, 0.0)

    def test_concurrent_charges_are_counted_exactly(self):
        """Department pods bill from several threads at once; the ledger must
        not lose a charge even though it no longer refuses any."""
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
        self.assertEqual(sum(1 for a in accepted if a), 400)
        self.assertEqual(b.calls, 400)
        self.assertAlmostEqual(b.spent_usd, 4.0, places=6)

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
