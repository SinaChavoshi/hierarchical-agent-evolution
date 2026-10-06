"""End-to-end: one epistemic repair iteration through the real runner.

Everything below the LLM is real -- the ledger, the gatekeeper's sandboxed
probes, the PUCT loop, the tool-loop patch write, module verification and the
oracle reconciliation. Only `call_llm` is faked, with a System 1 that proposes
one true and one false mechanism, so the test can check that the firm's
beliefs were set by evidence rather than by the proposer's priors (the false
mechanism is given the *higher* prior on purpose).
"""

import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from hae.epistemic.ledger import FALSIFIED, Q_CERTIFIED, Q_RESOLVED, SUPPORTED
from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.evaluation.judge import composite_score
from hae.genome.schema import AgentGenome, CompanyGenome, DepartmentGenome
from hae.runtime.company import SUITE_TAG_TO_MODULE, HierarchicalCompanyRunner
from hae.evaluation.verification_loop import VerificationLoop
from hae.runtime.workspace import AgentWorkspace

BUGGY = '''"""Tiny calculator used by the epistemic runner test."""


def add(a, b):
    """Return the sum of a and b. (The implementation below is wrong on purpose:
    it subtracts, which is the mechanism the true hypothesis names.)"""
    return a - b


def scale(x, factor=1):
    """Multiply x by factor; unrelated to the failure and must be preserved."""
    return x * factor
'''

FIXED = '''"""Tiny calculator used by the epistemic runner test."""


def add(a, b):
    """Return the sum of a and b. Repaired: the operator was `-`, which
    returned -1 for add(2, 3); the oracle's test expects 5."""
    return a + b


def scale(x, factor=1):
    """Multiply x by factor; unrelated to the failure and must be preserved."""
    return x * factor
'''

OBJECTIVE = (
    "Implement the module `mypkg/calc.py` exposing add(a, b) and scale(x, factor=1).\n\n"
    "ITERATION 2/3 — GROUND-TRUTH VERIFIER FEEDBACK\n"
    "Failing ground-truth checks:\n"
    "  - FAIL: test_add (tests.test_calc.TestCalc) -> AssertionError: -1 != 5\n"
)
FAILURES = ["FAIL: test_add (tests.test_calc.TestCalc) -> AssertionError: -1 != 5"]


class FakeSystem1:
    """Stands in for `call_llm`. Proposes, then patches; nothing else."""

    def __init__(self):
        self.calls = []

    def __call__(self, prompt, model_name, temperature, system_instruction,
                 usage_sink=None, response_format=None, max_tokens=None, **_):
        kind = "other"
        if "PROPOSE" in prompt and "HYPOTHESES" in prompt:
            kind = "hypothesis"
        elif "SYNTHESIZE a repair" in prompt:
            kind = "synthesis"
        self.calls.append({"kind": kind, "model": model_name,
                           "schema": (response_format or {}).get("json_schema", {}).get("name"),
                           "max_tokens": max_tokens})
        if usage_sink is not None:
            usage_sink.update({"measured": True, "prompt_tokens": 1200, "output_tokens": 300})

        if kind == "hypothesis":
            qid = re.search(r'Set `question_id` to "([^"]+)"', prompt).group(1)
            return json.dumps({
                "packet": "HYPOTHESIS_SET",
                "question_id": qid,
                "hypotheses": [
                    {   # Wrong, and deliberately the proposer's favourite.
                        "claim": "add multiplies its operands",
                        "mechanism": "the return statement uses the star operator so the result is a product",
                        "prior": 0.7,
                        "probe_code": "from mypkg.calc import add\nprint('PRODUCT', add(2, 3))\n",
                        "prediction": {"expect_exit_code": 0, "expect_stdout_contains": "PRODUCT 6"},
                    },
                    {   # Right, and given the lower prior.
                        "claim": "add subtracts its operands",
                        "mechanism": "the return statement uses the minus operator instead of plus",
                        "prior": 0.3,
                        "probe_code": "from mypkg.calc import add\nprint('RESULT', add(2, 3))\n",
                        "prediction": {"expect_exit_code": 0, "expect_stdout_contains": "RESULT -1"},
                    },
                ],
            })
        if kind == "synthesis":
            return "Action: write_file\nPath: mypkg/calc.py\n```python\n" + FIXED + "```\n"
        return json.dumps({"packet": "SPECIALIST", "summary": "n/a"})


def _genome(enabled=True):
    engineer = AgentGenome(role="Lead Implementation Engineer", goal="Fix code",
                           backstory="Systems", temperature=0.2, model_tier="worker",
                           tools_enabled=True)
    tester = AgentGenome(role="QA Analyst", goal="Verify", backstory="QA",
                         temperature=0.2, model_tier="worker")
    return CompanyGenome(
        company_id="epi_runner_firm", generation=16, parent_ids=[], mutation_history=[],
        ceo=AgentGenome(role="CEO", goal="Lead", backstory="Exec", temperature=0.4, model_tier="executive"),
        departments=[
            DepartmentGenome(dept_id="dept_systems_eng", name="Systems Engineering",
                             mandate="Build and repair modules", manager=engineer, agents=[engineer]),
            DepartmentGenome(dept_id="dept_qa_redteam", name="QA & Verification",
                             mandate="Verify behaviour", manager=tester, agents=[tester]),
        ],
        epistemic_policy={"enabled": enabled, "search_budget_moves": 10, "branching_k": 2,
                          "low_prior_quota": 0.0, "min_hypotheses_before_synthesis": 1,
                          "experiment_timeout_s": 20},
    )


class EpistemicRunnerTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_epi_runner_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runner = HierarchicalCompanyRunner(_genome())
        # Point the firm at a scratch workspace rather than /tmp/hae_workspaces.
        self.runner.workspace = AgentWorkspace("epi_runner_firm", base_dir=self.tmp)
        self.runner.verification_loop = VerificationLoop(self.runner.workspace)
        self.runner.workspace.write_file("mypkg/__init__.py", "")
        self.runner.workspace.write_file("mypkg/calc.py", BUGGY)
        # Probes run against the scratch workspace directly; the production
        # stage/isolation path is covered by the gatekeeper tests.
        self.runner._make_gatekeeper = lambda policy=None: EvidenceGatekeeper(
            self.runner.workspace, timeout_s=20, max_probe_lines=40,
            graded_modules=self.runner._required_modules, module_for_tag=SUITE_TAG_TO_MODULE,
            isolate=False, stage_reference=False)
        self.llm = FakeSystem1()

    def _search(self):
        with mock.patch("hae.runtime.company.call_llm", self.llm):
            return self.runner.run_epistemic_search(OBJECTIVE, FAILURES, iteration=2, max_iterations=3)

    def test_evidence_not_priors_decides_and_the_module_is_repaired(self):
        out = self._search()
        state = self.runner.epistemic_state
        self.assertIsNotNone(state)

        # One question, seeded from the oracle failure and bound to the module.
        self.assertEqual(len(state.questions), 1)
        question = next(iter(state.questions.values()))
        self.assertEqual(question.module, "mypkg/calc.py")
        self.assertEqual(question.status, Q_RESOLVED)

        # The high-prior mechanism was falsified; the low-prior one supported.
        by_claim = {h.claim: h for h in state.hypotheses.values()}
        self.assertEqual(by_claim["add multiplies its operands"].status, FALSIFIED)
        self.assertEqual(by_claim["add subtracts its operands"].status, SUPPORTED)
        self.assertTrue(by_claim["add subtracts its operands"].patch_applied)
        self.assertEqual(len(state.ruled_out), 1)

        # The patch came from the tool loop and is in the returned bundle.
        self.assertIn("mypkg/calc.py", out["workspace_files"])
        self.assertIn("return a + b", out["workspace_files"]["mypkg/calc.py"])
        self.assertEqual(out["epistemic_search"]["synthesized_paths"], ["mypkg/calc.py"])
        self.assertEqual(out["epistemic_iteration"], 2)

        # Every belief write cites evidence that is on the log.
        for h in state.hypotheses.values():
            self.assertTrue(h.evidence_ids, h.claim)
            for eid in h.evidence_ids:
                self.assertIsNotNone(state.evidence_by_id(eid))

    def test_run_output_keeps_the_v5_record_shape(self):
        out = self._search()
        for key in ("final_deliverable", "departmental_briefs", "elapsed_seconds", "token_usage",
                    "verification_loop", "token_accounting", "workspace_files", "workspace_tree",
                    "workspace_path", "inherited_files", "authored_files", "budget",
                    "budget_exhausted", "opex"):
            self.assertIn(key, out, key)
        self.assertIn("epistemic_ledger", out)
        self.assertEqual(out["epistemic_ledger"]["company_id"], "epi_runner_firm")
        self.assertIn("V6 Epistemic Ledger", out["final_deliverable"])
        self.assertGreater(out["token_usage"], 0)

    def test_system1_is_only_ever_asked_to_propose_or_patch(self):
        self._search()
        kinds = [c["kind"] for c in self.llm.calls]
        self.assertIn("hypothesis", kinds)
        self.assertIn("synthesis", kinds)
        self.assertNotIn("other", kinds)
        hyp = next(c for c in self.llm.calls if c["kind"] == "hypothesis")
        if os.environ.get("V5_TYPESAFE_PROTOCOL", "1") == "1":
            self.assertEqual(hyp["schema"], "HypothesisSetPacket")
        self.assertEqual(hyp["model"], "gemini-2.5-flash")   # engineering is a worker tier

    def test_oracle_reconciliation_certifies_and_the_audit_scores_it(self):
        self._search()
        # The next oracle run reports no failures: the resolved question is
        # certified by oracle evidence, not by the search's own say-so.
        rec = self.runner.reconcile_epistemic_state([], iteration=3)
        state = self.runner.epistemic_state
        question = next(iter(state.questions.values()))
        self.assertEqual(question.status, Q_CERTIFIED)
        self.assertEqual(len(rec["certified"]), 1)
        self.assertEqual(len(state.settled_knowledge), 1)

        audit = self.runner.epistemic_audit(token_usage=1500)
        self.assertEqual(audit["questions"], 1)
        self.assertEqual(audit["questions_certified"], 1)
        self.assertEqual(audit["hypotheses_tested"], 2)
        self.assertEqual(audit["status_inconsistencies"], 0)
        self.assertEqual(audit["evidence_backed_fraction"], 1.0)
        self.assertGreaterEqual(audit["moves_used"], 3)
        self.assertEqual(audit["budget_moves"], 10)
        # Priors were anti-correlated with the truth, so calibration is poor
        # and that must show up as a worse score than a perfectly calibrated
        # trail would get -- but the trail is still evidence-backed and certified.
        self.assertGreater(audit["calibration_brier"], 0.25)
        score = composite_score({}, 100.0, epistemic_audit=audit)
        self.assertGreater(score, 60.0)
        self.assertLess(score, 100.0)

    def test_oracle_still_failing_reopens_and_falsifies_the_patch(self):
        self._search()
        # The oracle disagrees with the search: the failure is still there.
        rec = self.runner.reconcile_epistemic_state(FAILURES, iteration=3)
        state = self.runner.epistemic_state
        question = next(iter(state.questions.values()))
        self.assertEqual(len(rec["reopened"]), 1)
        self.assertNotEqual(question.status, Q_RESOLVED)
        self.assertGreater(question.uncertainty, 0.5)
        patched = next(h for h in state.hypotheses.values() if h.patch_applied)
        self.assertEqual(patched.status, FALSIFIED)
        self.assertEqual(self.runner.epistemic_audit()["questions_certified"], 0)

    def test_disabled_policy_leaves_run_untouched(self):
        runner = HierarchicalCompanyRunner(_genome(enabled=False))
        self.assertFalse(runner.epistemic_policy.enabled)
        self.assertIsNone(runner.epistemic_state)
        self.assertIsNone(runner.epistemic_audit())


if __name__ == "__main__":
    unittest.main()
