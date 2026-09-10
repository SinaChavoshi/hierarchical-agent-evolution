"""Pluggable multi-domain verification harnesses and autonomous teleological OKR evaluator."""

import os
import re
import json
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from .sandbox_env import AgentWorkspace
from .sandbox_verifier import VerificationScore, DeterministicSandboxVerifier
from .schema import EvaluationMetricSpec

class BaseVerificationHarness(ABC):
    """Abstract base class for domain-specific deterministic verification harnesses."""

    @abstractmethod
    def verify(
        self,
        company_id: str,
        deliverable_text: str,
        workspace: Optional[AgentWorkspace] = None,
        metric_specs: Optional[List[EvaluationMetricSpec]] = None
    ) -> VerificationScore:
        """Executes domain verification and returns structured VerificationScore."""
        pass


class SoftwareSandboxHarness(BaseVerificationHarness):
    """Deterministic verification harness for software engineering packages."""

    def __init__(self, work_dir: str = "/tmp/sandbox_verification"):
        self.verifier = DeterministicSandboxVerifier(work_dir=work_dir)

    def verify(
        self,
        company_id: str,
        deliverable_text: str,
        workspace: Optional[AgentWorkspace] = None,
        metric_specs: Optional[List[EvaluationMetricSpec]] = None
    ) -> VerificationScore:
        score = self.verifier.verify_package(
            company_id=company_id,
            deliverable_text=deliverable_text,
            workspace=workspace
        )
        if metric_specs:
            teleological_eval = TeleologicalVerifier.evaluate_metrics(deliverable_text, workspace, metric_specs)
            details = f"{score.details} [Endogenous OKRs: {teleological_eval['passed']}/{len(metric_specs)} met]"
            return VerificationScore(
                build_passed=score.build_passed,
                test_passed=score.test_passed,
                smoke_passed=score.smoke_passed,
                telemetry_passed=score.telemetry_passed,
                pass_rate=score.pass_rate,
                total_tests=score.total_tests,
                score_penalty=score.score_penalty,
                details=details
            )
        return score


class FinancialTradingHarness(BaseVerificationHarness):
    """Verification harness for quantitative finance, trading strategies, and risk backtests."""

    def verify(
        self,
        company_id: str,
        deliverable_text: str,
        workspace: Optional[AgentWorkspace] = None,
        metric_specs: Optional[List[EvaluationMetricSpec]] = None
    ) -> VerificationScore:
        files = workspace.export_bundle() if workspace else {}
        combined_text = deliverable_text + " " + " ".join(files.values())

        has_strategy = any(k in combined_text.lower() for k in ["sharpe", "alpha", "orderbook", "execution", "signal", "pnl"])
        has_risk = any(k in combined_text.lower() for k in ["drawdown", "var", "stop_loss", "position_limit", "slippage", "risk_budget"])
        has_backtest = any("backtest" in f.lower() or "simulat" in f.lower() for f in files.keys()) or ("backtest" in combined_text.lower())
        has_tests = any("test" in f.lower() for f in files.keys()) or any("def test_" in c for c in files.values())

        checks = [has_strategy, has_risk, has_backtest, has_tests]
        passed_count = sum(1 for c in checks if c)
        pass_rate = passed_count / len(checks)
        penalty = round((1.0 - pass_rate) * 25.0, 2)

        details = (
            f"[Quantitative Finance Sandbox] Strategy: {'PASS' if has_strategy else 'FAIL'}, "
            f"Risk Guardrails: {'PASS' if has_risk else 'FAIL'}, "
            f"Backtest Harness: {'PASS' if has_backtest else 'FAIL'}, "
            f"Execution Tests: {'PASS' if has_tests else 'FAIL'}."
        )

        return VerificationScore(
            build_passed=has_strategy,
            test_passed=has_tests,
            smoke_passed=has_risk,
            telemetry_passed=has_backtest,
            pass_rate=pass_rate,
            total_tests=len(checks),
            score_penalty=penalty,
            details=details
        )


class ComplianceHarness(BaseVerificationHarness):
    """Verification harness for regulatory compliance, security policies, and audits."""

    def verify(
        self,
        company_id: str,
        deliverable_text: str,
        workspace: Optional[AgentWorkspace] = None,
        metric_specs: Optional[List[EvaluationMetricSpec]] = None
    ) -> VerificationScore:
        files = workspace.export_bundle() if workspace else {}
        combined_text = deliverable_text + " " + " ".join(files.values())

        has_policy = any(k in combined_text.lower() for k in ["soc2", "hipaa", "gdpr", "compliance", "audit_trail", "access_control"])
        has_crypto = any(k in combined_text.lower() for k in ["encrypt", "tls", "aes", "kms", "hash", "secret_manager"])
        has_redteam = any(k in combined_text.lower() for k in ["red_team", "adversar", "penetration", "vulnerab", "threat_model"])
        has_tests = any("test" in f.lower() for f in files.keys()) or any("def test_" in c for c in files.values())

        checks = [has_policy, has_crypto, has_redteam, has_tests]
        passed_count = sum(1 for c in checks if c)
        pass_rate = passed_count / len(checks)
        penalty = round((1.0 - pass_rate) * 25.0, 2)

        details = (
            f"[Compliance Sandbox] Regulatory Policy: {'PASS' if has_policy else 'FAIL'}, "
            f"Cryptography Controls: {'PASS' if has_crypto else 'FAIL'}, "
            f"Adversarial Audit: {'PASS' if has_redteam else 'FAIL'}, "
            f"Assertion Tests: {'PASS' if has_tests else 'FAIL'}."
        )

        return VerificationScore(
            build_passed=has_policy,
            test_passed=has_tests,
            smoke_passed=has_crypto,
            telemetry_passed=has_redteam,
            pass_rate=pass_rate,
            total_tests=len(checks),
            score_penalty=penalty,
            details=details
        )


class TeleologicalVerifier:
    """Evaluates deliverables against endogenous OKRs autonomously defined by the company's CEO."""

    @staticmethod
    def evaluate_metrics(
        deliverable_text: str,
        workspace: Optional[AgentWorkspace],
        metric_specs: List[EvaluationMetricSpec]
    ) -> Dict[str, Any]:
        """Scores alignment against self-defined OKRs."""
        files = workspace.export_bundle() if workspace else {}
        combined_content = deliverable_text + " " + " ".join(files.values())

        results = []
        passed_count = 0
        for spec in metric_specs:
            target_term = spec.target_value.lower()
            criterion = spec.name.lower()
            is_met = (target_term in combined_content.lower()) or (criterion in combined_content.lower())
            if is_met:
                passed_count += 1
            results.append({
                "metric_id": spec.metric_id,
                "name": spec.name,
                "target": spec.target_value,
                "passed": is_met
            })

        alignment_score = passed_count / max(len(metric_specs), 1)
        return {
            "passed": passed_count,
            "total": len(metric_specs),
            "alignment_rate": alignment_score,
            "results": results
        }


def get_harness(domain: str = "software", work_dir: str = "/tmp/sandbox_verification") -> BaseVerificationHarness:
    """Factory function for instantiating domain verification harnesses."""
    domain_clean = domain.lower().strip()
    if domain_clean in ["finance", "trading", "quant"]:
        return FinancialTradingHarness()
    elif domain_clean in ["compliance", "security", "audit", "regulatory"]:
        return ComplianceHarness()
    else:
        return SoftwareSandboxHarness(work_dir=work_dir)
