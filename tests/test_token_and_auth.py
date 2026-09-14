"""Tests for measured token accounting and OAuth token refresh.

Both of these were failure modes that cost real tournament runs: the estimate
biased the OpEx budget in every generation, and the non-refreshing token
stalled firms an hour into Generations 9 and 10.
"""

import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, ".")

from src import llm_factory as lf


class TestUsageCapture(unittest.TestCase):

    def test_usage_metadata_is_recorded(self):
        sink = {}
        lf.record_usage(sink, {"usageMetadata": {
            "promptTokenCount": 1200,
            "candidatesTokenCount": 340,
            "totalTokenCount": 1540,
        }})
        self.assertEqual(sink["prompt_tokens"], 1200)
        self.assertEqual(sink["output_tokens"], 340)
        self.assertEqual(sink["total_tokens"], 1540)
        self.assertTrue(sink["measured"])
        self.assertEqual(sink["calls"], 1)

    def test_reasoning_tokens_are_tracked_separately(self):
        # thoughtsTokenCount is already inside totalTokenCount, so it must be
        # recorded but not added to the total a second time.
        sink = {}
        lf.record_usage(sink, {"usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 50,
            "thoughtsTokenCount": 900,
            "totalTokenCount": 1050,
        }})
        self.assertEqual(sink["thought_tokens"], 900)
        self.assertEqual(sink["total_tokens"], 1050)

    def test_usage_accumulates_across_calls(self):
        sink = {}
        for _ in range(3):
            lf.record_usage(sink, {"usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            }})
        self.assertEqual(sink["total_tokens"], 45)
        self.assertEqual(sink["calls"], 3)

    def test_absent_metadata_leaves_sink_unmeasured(self):
        sink = {}
        lf.record_usage(sink, {"candidates": []})
        self.assertNotIn("measured", sink)

    def test_none_sink_is_a_noop(self):
        lf.record_usage(None, {"usageMetadata": {"totalTokenCount": 5}})  # must not raise

    def test_total_is_derived_when_provider_omits_it(self):
        sink = {}
        lf.record_usage(sink, {"usageMetadata": {
            "promptTokenCount": 7, "candidatesTokenCount": 3}})
        self.assertEqual(sink["total_tokens"], 10)


class TestCompanyTokenAccounting(unittest.TestCase):

    def setUp(self):
        from src.company import HierarchicalCompanyRunner
        self.runner = HierarchicalCompanyRunner.__new__(HierarchicalCompanyRunner)
        for attr in ("flash_input_tokens", "flash_output_tokens",
                     "pro_input_tokens", "pro_output_tokens",
                     "measured_calls", "estimated_calls", "thought_tokens"):
            setattr(self.runner, attr, 0)

    def test_measured_counts_are_used_verbatim(self):
        self.runner._account_tokens(
            is_pro=False, prompt_text="x" * 4000, system_text="",
            response_text="y" * 400,
            usage={"measured": True, "prompt_tokens": 17, "output_tokens": 3})
        # 17 and 3, not 1000 and 100.
        self.assertEqual(self.runner.flash_input_tokens, 17)
        self.assertEqual(self.runner.flash_output_tokens, 3)
        self.assertEqual(self.runner.measured_calls, 1)
        self.assertEqual(self.runner.estimated_calls, 0)

    def test_estimate_is_used_only_as_a_fallback(self):
        self.runner._account_tokens(
            is_pro=False, prompt_text="x" * 400, system_text="",
            response_text="y" * 40, usage={})
        self.assertEqual(self.runner.flash_input_tokens, 100)
        self.assertEqual(self.runner.flash_output_tokens, 10)
        self.assertEqual(self.runner.estimated_calls, 1)
        self.assertEqual(self.runner.measured_calls, 0)

    def test_reasoning_tokens_bill_as_output(self):
        self.runner._account_tokens(
            is_pro=True, prompt_text="", system_text="", response_text="",
            usage={"measured": True, "prompt_tokens": 10,
                   "output_tokens": 5, "thought_tokens": 900})
        self.assertEqual(self.runner.pro_output_tokens, 905)
        self.assertEqual(self.runner.thought_tokens, 900)

    def test_estimate_cannot_see_reasoning_tokens(self):
        # Documents why the estimate biases the efficiency bonus: a thinking
        # model that emits 900 reasoning tokens and a two-word answer is
        # charged for the two words.
        self.runner._account_tokens(
            is_pro=True, prompt_text="", system_text="",
            response_text="ok", usage={})
        self.assertEqual(self.runner.pro_output_tokens, 0)

    def test_pro_and_flash_are_billed_separately(self):
        self.runner._account_tokens(True, "", "", "",
                                    {"measured": True, "prompt_tokens": 1, "output_tokens": 2})
        self.runner._account_tokens(False, "", "", "",
                                    {"measured": True, "prompt_tokens": 4, "output_tokens": 8})
        self.assertEqual((self.runner.pro_input_tokens, self.runner.pro_output_tokens), (1, 2))
        self.assertEqual((self.runner.flash_input_tokens, self.runner.flash_output_tokens), (4, 8))


class TestTokenRefresh(unittest.TestCase):

    def setUp(self):
        lf.reset_token_cache()

    def tearDown(self):
        lf.reset_token_cache()

    def test_token_is_cached_between_calls(self):
        calls = []

        def fetch():
            calls.append(1)
            return ("tok-1", 3600.0)

        with mock.patch.object(lf, "_TOKEN_SOURCES", (("metadata", fetch),)):
            self.assertEqual(lf.get_adc_access_token(), "tok-1")
            self.assertEqual(lf.get_adc_access_token(), "tok-1")
        self.assertEqual(len(calls), 1, "second call should hit the cache")

    def test_token_is_refetched_near_expiry(self):
        counter = {"n": 0}

        def fetch():
            counter["n"] += 1
            # Short lifetime, well inside the 300s refresh skew.
            return (f"tok-{counter['n']}", 60.0)

        with mock.patch.object(lf, "_TOKEN_SOURCES", (("metadata", fetch),)):
            self.assertEqual(lf.get_adc_access_token(), "tok-1")
            self.assertEqual(lf.get_adc_access_token(), "tok-2")

    def test_force_refresh_evicts_a_rejected_token(self):
        counter = {"n": 0}

        def fetch():
            counter["n"] += 1
            return (f"tok-{counter['n']}", 3600.0)

        with mock.patch.object(lf, "_TOKEN_SOURCES", (("metadata", fetch),)):
            self.assertEqual(lf.get_adc_access_token(), "tok-1")
            self.assertEqual(lf.get_adc_access_token(force_refresh=True), "tok-2")

    def test_force_refresh_skips_a_source_that_returns_the_same_token(self):
        # This is the exact shape of the old bug: a static env var hands back
        # the same expired string forever. On a forced refresh the chain must
        # move past it.
        static = lambda: ("stale", 3600.0)
        rotating = lambda: ("fresh", 3600.0)

        with mock.patch.object(lf, "_TOKEN_SOURCES",
                               (("env", static), ("metadata", rotating))):
            self.assertEqual(lf.get_adc_access_token(), "stale")
            self.assertEqual(lf.get_adc_access_token(force_refresh=True), "fresh")

    def test_refreshable_sources_are_preferred_over_the_static_env_var(self):
        order = [name for name, _ in lf._TOKEN_SOURCES]
        self.assertEqual(order[-1], "env",
                         "the un-refreshable env token must be the last resort")
        self.assertEqual(order[0], "metadata",
                         "Workload Identity should be tried first")

    def test_all_sources_failing_returns_none(self):
        with mock.patch.object(lf, "_TOKEN_SOURCES", (("a", lambda: None),)):
            self.assertIsNone(lf.get_adc_access_token())

    def test_stale_token_is_returned_when_nothing_can_rotate(self):
        # Better to fail with the server's real error than with a confusing
        # "unable to obtain token".
        static = lambda: ("only-token", 3600.0)
        with mock.patch.object(lf, "_TOKEN_SOURCES", (("env", static),)):
            self.assertEqual(lf.get_adc_access_token(), "only-token")
            self.assertEqual(lf.get_adc_access_token(force_refresh=True), "only-token")

    def test_expiry_honours_the_provider_reported_lifetime(self):
        with mock.patch.object(lf, "_TOKEN_SOURCES",
                               (("metadata", lambda: ("t", 3600.0)),)):
            lf.get_adc_access_token()
        remaining = lf._TOKEN_CACHE["expires_at"] - time.time()
        self.assertGreater(remaining, 3500)
        self.assertLess(remaining, 3700)


if __name__ == "__main__":
    unittest.main()
