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
