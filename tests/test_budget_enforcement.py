"""The budget is enforced by the runner, not merely scored afterwards.

V1's `budget_usd` adjusted the fitness score after the money was gone. These
tests pin the difference: calls are actually refused.
"""

import unittest
from unittest import mock

from hae.runtime.company import HierarchicalCompanyRunner
from hae.task import Budget


class _Runner(HierarchicalCompanyRunner):
    """Constructed without a workspace; we only exercise the spend path."""

    def __init__(self, budget):
        self.budget = budget
        for attr in ("flash_input_tokens", "flash_output_tokens",
                     "pro_input_tokens", "pro_output_tokens",
                     "measured_calls", "estimated_calls", "thought_tokens"):
            setattr(self, attr, 0)


class BudgetEnforcementTest(unittest.TestCase):

    def test_calls_are_refused_once_the_ceiling_is_reached(self):
        r = _Runner(Budget(limit_usd=0.001, reserve_fraction=0.0))
        r._account_tokens(is_pro=True, prompt_text="", system_text="",
                          response_text="", label="ceo",
                          usage={"measured": True, "prompt_tokens": 100000,
                                 "output_tokens": 100000})
        self.assertFalse(r._may_call("next agent"))

    def test_reserved_calls_survive_the_working_limit(self):
        b = Budget(limit_usd=1.0, reserve_fraction=0.5)
        r = _Runner(b)
        b.charge(0.6, "departments")
        self.assertFalse(r._may_call("another department"))
        self.assertTrue(r._may_call("ceo synthesis", reserved=True))

    def test_no_budget_means_no_ceiling(self):
        r = _Runner(None)
        self.assertTrue(r._may_call("anything"))

    def test_refusals_are_counted_not_swallowed(self):
        b = Budget(limit_usd=0.0001, reserve_fraction=0.0)
        r = _Runner(b)
        b.charge(0.01, "over")
        r._may_call("a")
        r._may_call("b")
        self.assertEqual(b.refusals, 2)
        self.assertTrue(b.overrun)

    def test_budget_notice_is_not_mistakable_for_a_finding(self):
        """A truncated run must not read as an agent's considered output."""
        notice = HierarchicalCompanyRunner._budget_notice("the CFO's analysis")
        self.assertIn("BUDGET EXHAUSTED", notice)
        self.assertIn("not a finding", notice)

    def test_pro_and_flash_bill_at_different_rates(self):
        pro = _Runner(Budget(limit_usd=100.0))
        flash = _Runner(Budget(limit_usd=100.0))
        usage = {"measured": True, "prompt_tokens": 10000, "output_tokens": 10000}
        pro._account_tokens(is_pro=True, prompt_text="", system_text="",
                            response_text="", usage=dict(usage))
        flash._account_tokens(is_pro=False, prompt_text="", system_text="",
                              response_text="", usage=dict(usage))
        self.assertGreater(pro.budget.spent_usd, flash.budget.spent_usd)


if __name__ == "__main__":
    unittest.main()


class CallCeilingReserveTest(unittest.TestCase):
    """The call ceiling must hold back a reserve, like the dollar ceiling.

    Found while sizing budgets for V2 Generation 1. At the rate measured from
    Gen 11 scorecards (~$0.0077/call) a firm hits `max_calls` long before it
    approaches `limit_usd`, so the call ceiling is the one that binds in
    practice. It had no reserve, which meant the mechanism guaranteeing the
    CEO's closing synthesis survives an overrun was guarding the ceiling that
    never fires and not the one that always does.
    """

    def test_synthesis_survives_the_call_ceiling(self):
        """The original symptom: dollars to spare, deliverable discarded."""
        b = Budget(limit_usd=10.0, max_calls=5)
        while b.can_spend():
            b.charge(0.01, label="worker")
        self.assertGreater(b.remaining_usd, 9.0)     # nowhere near the money
        self.assertTrue(b.can_spend(reserved=True))  # but synthesis proceeds
        b.charge(0.01, label="ceo-synthesis", reserved=True)
        self.assertTrue(b.exhausted)

    def test_reserve_never_rounds_away(self):
        """A small `max_calls` must still hold back a call. `int()` on
        `3 * 0.9` is 2, but `int()` on `1 * 0.9` is 0 -- and a reserve of zero
        is the bug this test exists to prevent."""
        for max_calls in (1, 2, 3, 10, 120, 400):
            b = Budget(limit_usd=5.0, max_calls=max_calls)
            held = max_calls - b.working_max_calls
            self.assertGreaterEqual(
                held, 1, f"no call held back at max_calls={max_calls}")

    def test_no_reserve_configured_means_no_holdback(self):
        b = Budget(limit_usd=5.0, max_calls=10, reserve_fraction=0.0)
        self.assertEqual(b.working_max_calls, 10)

    def test_absent_call_ceiling_is_unaffected(self):
        b = Budget(limit_usd=1.0)
        self.assertIsNone(b.working_max_calls)
        self.assertFalse(b.working_exhausted)

    def test_dollar_ceiling_still_independently_binds(self):
        """Raising `max_calls` must not disable the money ceiling."""
        b = Budget(limit_usd=0.10, max_calls=10_000)
        while b.can_spend():
            b.charge(0.01, label="worker")
        self.assertFalse(b.can_spend())
        self.assertLess(b.calls, 100)

    def test_reported_in_to_dict(self):
        """Scorecards read this. A ceiling that is enforced but not reported
        makes a truncated run indistinguishable from a short one."""
        b = Budget(limit_usd=3.0, max_calls=400)
        self.assertEqual(b.to_dict()["working_max_calls"], 360)
