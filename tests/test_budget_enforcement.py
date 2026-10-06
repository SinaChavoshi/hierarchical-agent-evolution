"""The budget is an accounting ledger, not a circuit breaker.

History, because the tests here changed meaning once and must not silently do
so again. V1's `budget_usd` adjusted the fitness score after the money was
gone. V2 made `Budget` a hard ceiling that refused calls. Commit 86c964c
(2026-09-24, "remove hard call/token truncation") reversed that for self-hosted
clusters: `can_spend` is always True, nothing is ever refused, and efficiency
pressure comes from Net Fitness (`cost_penalty` / `efficiency_bonus`) instead.

The tests below pin the soft-accounting contract. The ones that asserted hard
refusals sat failing -- and one of them, `while b.can_spend(): charge()`,
spinning forever -- for eleven days, which is how long the full suite was
un-runnable without anyone noticing. A test that encodes a contract the code
has abandoned is worse than no test.
"""

import unittest

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


class SoftAccountingTest(unittest.TestCase):
    """Spend is recorded faithfully and never blocks."""

    def test_calls_are_not_refused_past_the_ceiling(self):
        r = _Runner(Budget(limit_usd=0.001, reserve_fraction=0.0))
        r._account_tokens(is_pro=True, prompt_text="", system_text="",
                          response_text="", label="ceo",
                          usage={"measured": True, "prompt_tokens": 100000,
                                 "output_tokens": 100000})
        self.assertGreater(r.budget.spent_usd, r.budget.limit_usd)
        self.assertTrue(r._may_call("next agent"))
        self.assertEqual(r.budget.refusals, 0)

    def test_reserved_and_ordinary_calls_are_treated_alike(self):
        b = Budget(limit_usd=1.0, reserve_fraction=0.5)
        r = _Runner(b)
        b.charge(0.6, "departments")
        self.assertTrue(r._may_call("another department"))
        self.assertTrue(r._may_call("ceo synthesis", reserved=True))

    def test_no_budget_means_no_ceiling(self):
        r = _Runner(None)
        self.assertTrue(r._may_call("anything"))

    def test_overrun_is_visible_in_the_ledger_not_in_refusals(self):
        """The scorecard must still be able to see that a firm overspent."""
        b = Budget(limit_usd=0.0001, reserve_fraction=0.0)
        b.charge(0.01, "over")
        self.assertFalse(b.overrun)
        self.assertFalse(b.exhausted)
        self.assertFalse(b.working_exhausted)
        self.assertEqual(b.refusals, 0)
        self.assertEqual(b.remaining_usd, 0.0)
        self.assertGreater(b.spent_usd, b.limit_usd)
        self.assertEqual(len(b.charges), 1)

    def test_charge_never_spins_a_can_spend_loop(self):
        """Regression: `while b.can_spend(): charge()` was an infinite loop.

        `can_spend` is unconditionally True, so any caller that loops on it
        must bound the loop itself. This pins the property the old test
        tripped over so nobody writes that loop again.
        """
        b = Budget(limit_usd=0.10, max_calls=10_000)
        for _ in range(50):
            self.assertTrue(b.can_spend())
            b.charge(0.01, label="worker")
        self.assertTrue(b.can_spend())
        self.assertEqual(b.calls, 50)
        self.assertAlmostEqual(b.spent_usd, 0.50, places=6)

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

    def test_negative_charges_are_rejected(self):
        with self.assertRaises(ValueError):
            Budget(limit_usd=1.0).charge(-0.01)


class ReserveArithmeticTest(unittest.TestCase):
    """The reserve is still computed and reported, even though nothing is
    refused on it any more. Scorecards and generation sizing read it.
    """

    def test_reserve_never_rounds_away(self):
        """`int()` on `3 * 0.9` is 2, but `int()` on `1 * 0.9` is 0 -- and a
        reserve of zero would misreport the ceiling a firm was sized for."""
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

    def test_working_limit_holds_back_the_reserve(self):
        b = Budget(limit_usd=1.0, reserve_fraction=0.25)
        self.assertAlmostEqual(b.working_limit_usd, 0.75)

    def test_reported_in_to_dict(self):
        """Scorecards read this. A ceiling that is sized but not reported
        makes an expensive run indistinguishable from a cheap one."""
        b = Budget(limit_usd=3.0, max_calls=400)
        self.assertEqual(b.to_dict()["working_max_calls"], 360)


if __name__ == "__main__":
    unittest.main()
