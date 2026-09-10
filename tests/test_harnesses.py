"""Unit tests for pluggable verification harnesses and teleological OKR verifier."""

import unittest
from src.schema import EvaluationMetricSpec
from src.sandbox_env import AgentWorkspace
from src.harnesses import (
    get_harness,
    SoftwareSandboxHarness,
    FinancialTradingHarness,
    ComplianceHarness,
    TeleologicalVerifier
)

class TestPluggableHarnesses(unittest.TestCase):

    def test_factory_selection(self):
        self.assertIsInstance(get_harness("software"), SoftwareSandboxHarness)
        self.assertIsInstance(get_harness("finance"), FinancialTradingHarness)
        self.assertIsInstance(get_harness("trading"), FinancialTradingHarness)
        self.assertIsInstance(get_harness("compliance"), ComplianceHarness)

    def test_financial_trading_harness(self):
        harness = FinancialTradingHarness()
        deliverable = """
        Alpha strategy model implementing Sharpe ratio maximization with 15% annual target.
        Risk guardrails enforce maximum drawdown limit of 5% and strict stop_loss limits.
        Backtest harness simulates orderbook execution with tick-level slippage modeling.
        """
        workspace = AgentWorkspace(company_id="test_hedge_fund")
        workspace.write_file("tests/test_strategy.py", "def test_alpha(): assert True")

        score = harness.verify("test_hedge_fund", deliverable, workspace)
        self.assertTrue(score.build_passed)
        self.assertTrue(score.smoke_passed)
        self.assertTrue(score.telemetry_passed)
        self.assertTrue(score.test_passed)
        self.assertEqual(score.score_penalty, 0.0)
        workspace.cleanup()

    def test_compliance_harness(self):
        harness = ComplianceHarness()
        deliverable = """
        SOC2 and HIPAA compliance audit trail with role-based access_control.
        AES-256 encryption at rest and TLS 1.3 in transit with KMS integration.
        Adversarial red_team penetration testing against threat_model vectors.
        """
        workspace = AgentWorkspace(company_id="test_compliance_corp")
        workspace.write_file("tests/test_audit.py", "def test_audit(): assert True")

        score = harness.verify("test_compliance_corp", deliverable, workspace)
        self.assertTrue(score.build_passed)
        self.assertTrue(score.smoke_passed)
        self.assertTrue(score.telemetry_passed)
        self.assertTrue(score.test_passed)
        self.assertEqual(score.score_penalty, 0.0)
        workspace.cleanup()

    def test_teleological_okr_verifier(self):
        okrs = [
            EvaluationMetricSpec(metric_id="m1", name="Latency", target_value="sub-10ms latency"),
            EvaluationMetricSpec(metric_id="m2", name="Throughput", target_value="100k qps"),
            EvaluationMetricSpec(metric_id="m3", name="Coverage", target_value="100% test coverage")
        ]
        text = "Our microservice guarantees sub-10ms latency and 100k qps sustained throughput under peak load."
        res = TeleologicalVerifier.evaluate_metrics(text, None, okrs)
        self.assertEqual(res["passed"], 2)
        self.assertAlmostEqual(res["alignment_rate"], 2/3, places=2)

if __name__ == "__main__":
    unittest.main()
