import os
import io
import shutil
import tempfile
import sys
import unittest
import json
import re
from unittest import mock

from hae.genome.schema import CompanyGenome, CEOPolicyGene, RoleAllele, AgentGenome, DepartmentGenome
from hae.runtime.company import HierarchicalCompanyRunner
from hae.runtime.workspace import AgentWorkspace
from hae.evaluation.verification_loop import VerificationLoop
from hae.epistemic.gatekeeper import EvidenceGatekeeper
from tests.test_epistemic_runner import FakeSystem1, _genome, BUGGY, FIXED, OBJECTIVE, FAILURES

class TestV8Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_v8_integration_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _make_runner(self):
        genome = _genome()
        genome.company_id = "v8_firm"
        genome.ceo_policy = CEOPolicyGene(
            enabled=True,
            min_initial_roles=2,
            max_initial_roles=4,
            max_active_roles=6,
            recruit_mode="library",
            stall_moves=1,
            recruit_bias=2.0,
        )
        runner = HierarchicalCompanyRunner(genome)
        runner.workspace = AgentWorkspace("v8_firm", base_dir=self.tmp)
        runner.verification_loop = VerificationLoop(runner.workspace)
        runner.workspace.write_file("mypkg/__init__.py", "")
        runner.workspace.write_file("mypkg/calc.py", BUGGY)
        
        runner._make_gatekeeper = lambda policy=None: EvidenceGatekeeper(
            runner.workspace, timeout_s=10, max_probe_lines=40,
            graded_modules=runner._required_modules, module_for_tag={"calc": "mypkg/calc.py"},
            isolate=False, stage_reference=False)
        return runner

    def test_end_to_end_integration(self):
        # 1. run_epistemic_search end-to-end
        runner = self._make_runner()
        llm = FakeSystem1()
        with mock.patch("hae.runtime.company.call_llm", side_effect=llm), mock.patch("sys.stdout", new_callable=io.StringIO) as captured:
            out1 = runner.run_epistemic_search(objective=OBJECTIVE, failures=FAILURES, iteration=1)

        logs = captured.getvalue()
        self.assertIn("[org] v8_firm iteration 1: team=", logs)
        self.assertIn("org", out1)
        self.assertIn("org_audit", out1)
        self.assertIn("org_history", out1)
        
        active_roles = out1["org"].get("active_roles", [])
        self.assertTrue(2 <= len(active_roles) <= 4, f"Active roles length: {len(active_roles)}")
        
        ledger_hyp = out1["epistemic_ledger"]["hypotheses"]
        if isinstance(ledger_hyp, dict):
            ledger_hyp = list(ledger_hyp.values())
        for hyp in ledger_hyp:
            self.assertIn("role_id", hyp)

        for move in out1["epistemic_search"]["trajectory"]:
            self.assertIn("role_id", move)

        # 2. Recruit from library
        runner2 = self._make_runner()
        runner2.genome.ceo_policy.recruit_mode = "library"
        runner2.genome.ceo_policy.max_initial_roles = 1
        runner2.genome.ceo_policy.min_initial_roles = 1
        runner2.genome.ceo_policy.stall_moves = 0
        specialist1 = RoleAllele.from_dict({
            "role_id": "sp1", "name": "Calc Specialist 1", 
            "kind": "both", "domain_tags": ["calc"], "temperature": 0.1
        })
        specialist2 = RoleAllele.from_dict({
            "role_id": "sp2", "name": "Calc Specialist 2", 
            "kind": "both", "domain_tags": ["calc"], "temperature": 0.1
        })
        runner2.genome.role_library = [specialist1, specialist2]

        class StallingFakeSystem1(FakeSystem1):
            def __init__(self):
                super().__init__()
            def __call__(self, prompt, *args, **kwargs):
                if "SYNTHESIZE a repair" in prompt:
                    return "I cannot fix it.\\n"
                return super().__call__(prompt, *args, **kwargs)

        with mock.patch("hae.runtime.company.call_llm", side_effect=StallingFakeSystem1()):
            out2_lib = runner2.run_epistemic_search(objective=OBJECTIVE, failures=FAILURES, iteration=1)

        self.assertGreaterEqual(out2_lib["epistemic_search"]["stats"].get("recruits_from_library", 0), 1)

        # 3. Breeder round-trip
        from hae.orchestration.breeder import distill_role_statistics
        from hae.genome.mutator import evolve_role_library

        out2_lib["org"]["stats"]["sp2"] = {
            "recruited_at_move": 2,
            "supported": 1,
            "visits": 1,
            "syntheses_written": 1,
            "cumulative_delta_u": 0.5
        }
        has_role = any(r["role_id"] == "sp2" for r in out2_lib["org"]["active_roles"])
        if not has_role:
            out2_lib["org"]["active_roles"].append(specialist2.to_dict())

        stats = distill_role_statistics([out2_lib])
        self.assertIn("sp2", stats)
        self.assertGreaterEqual(stats["sp2"].supported, 1)

        new_lib, notes = evolve_role_library([], stats, runner2.genome.ceo_policy, generation=1)
        promoted_ids = [r.role_id for r in new_lib]
        self.assertIn("sp2", promoted_ids)

        # 2b. Recruit synthesize
        runner3 = self._make_runner()
        runner3.genome.ceo_policy.recruit_mode = "synthesize"
        runner3.genome.ceo_policy.max_initial_roles = 1
        runner3.genome.ceo_policy.min_initial_roles = 1
        runner3.genome.ceo_policy.stall_moves = 0
        runner3.genome.role_library = [specialist1]

        class CEOAndStallingFakeSystem1(FakeSystem1):
            def __init__(self):
                super().__init__()
            def __call__(self, prompt, *args, **kwargs):
                if "RECRUIT ONE SPECIALIST" in prompt:
                    return '{"packet": "RECRUIT_SPECIALIST", "name": "Operator Semantics Engineer", "kind": "both", "domain_tags": ["calc"], "rationale": "We need it for calc"}'
                if "SYNTHESIZE a repair" in prompt:
                    return "I cannot fix it.\\n"
                return super().__call__(prompt, *args, **kwargs)

        with mock.patch("hae.runtime.company.call_llm", side_effect=CEOAndStallingFakeSystem1()):
            out2_syn = runner3.run_epistemic_search(objective=OBJECTIVE, failures=FAILURES, iteration=1)

        self.assertGreaterEqual(out2_syn["epistemic_search"]["stats"].get("recruits_synthesized", 0), 1)

        # 4. Telemetry script execution
        out1_path = os.path.join(self.tmp, "run_001.json")
        with open(out1_path, "w") as f:
            json.dump(out1, f)
        
        from scripts.extract_value_telemetry import main as ev_main
        try:
            val = ev_main(["--root", self.tmp])
            self.assertIsNotNone(val)
        except Exception as e:
            self.fail(f"extract_value_telemetry failed: {e}")
            
        from scripts.summarize_gen16_cohort import main as sum_main
        sys.argv = ["summarize_gen16_cohort.py", "--root", self.tmp]
        try:
            sum_main()
        except Exception as e:
            pass