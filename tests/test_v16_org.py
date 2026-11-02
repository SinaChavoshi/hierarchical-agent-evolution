"""V16 Phase 2: role library / CEO policy binding to the hierarchical harness (`hae/swebench/org_binding.py`)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import EpistemicState
from hae.genome.schema import CEOPolicyGene, GenomeValidationError, RoleAllele
from hae.swebench.executor import LocalExecutor
from hae.swebench.hierarchical_mcts import HierarchicalAgenticMCTSEngine, ReconBrief
from hae.swebench.org_binding import (
    HarnessOrg,
    org_mode_from_policy,
    parse_specialist,
    render_persona,
)
from hae.swebench.task import SweTask

V15_PERSONAS = {
    "recon": "Layer-L1 Epistemic Reconnaissance & Reproduction Specialist",
    "architect": "Layer-L0 Seed Principal Architect & AB-MCTS Tree Master",
    "executor": "Layer-L2 Scoped Branch Executor",
    "refiner": "Layer-L2 Clean-Context Delta-Refinement Specialist",
    "falsifier": "Layer-L1B Independent Adversarial Falsifier & API-Contract Auditor",
}


def generalists():
    return [RoleAllele(name=n, layer=layer, is_generalist=True, origin="v16-generalist") for layer, n in V15_PERSONAS.items()]


def specialist(name, layer, tags=(), modes=(), playbook=("Do the first thing.", "Do the second thing.")):
    return RoleAllele(name=name, layer=layer, goal=f"{name} goal.", backstory="Seasoned engineer.",
                      domain_tags=list(tags), failure_modes=list(modes), playbook=list(playbook))


class _Runner:
    def __init__(self, root):
        self.root = root
        self.model_name = "m"
        self.calls = 0

    def _may_call(self, reason=""):
        self.calls += 1
        return True

    def _account_tokens(self, **kwargs):
        pass

    def objective_text(self):
        return "PROBLEM STATEMENT: `add_items(a, b, extra=None)` ignores `extra`; emit a DeprecationWarning for `legacy`."

    def repo_map(self):
        return ["pkg/calc.py", "tests/test_calc.py"]


class SchemaTest(unittest.TestCase):
    def test_new_fields_default_and_roundtrip(self):
        r = RoleAllele(name="Legacy")
        self.assertEqual((r.layer, r.playbook, r.failure_modes, r.is_generalist), ("", [], [], False))
        s = specialist("Deprecation Auditor", "falsifier", modes=["Deprecation_Warning"])
        back = RoleAllele.from_dict(s.to_dict())
        self.assertEqual(back.failure_modes, ["deprecation_warning"])
        self.assertEqual(back.playbook, s.playbook)

    def test_validation(self):
        with self.assertRaises(GenomeValidationError):
            RoleAllele(name="x", layer="ceo")
        with self.assertRaises(GenomeValidationError):
            RoleAllele(name="x", layer="executor", playbook=["s"] * 9)
        with self.assertRaises(GenomeValidationError):
            RoleAllele(name="x", layer="executor", playbook=["s" * 201])


class ModeAndParsingTest(unittest.TestCase):
    def test_mode(self):
        self.assertEqual(org_mode_from_policy({}), "off")
        self.assertEqual(org_mode_from_policy({"v16_org": "static"}), "static")
        self.assertEqual(org_mode_from_policy({"v16_org": {"mode": "dynamic"}}), "dynamic")
        self.assertEqual(org_mode_from_policy({"v16_org": "bogus"}), "off")

    def test_parse_specialist(self):
        self.assertEqual(parse_specialist("L2_BranchExecutor_b2_producer_upstream"), ("executor", "b2", "producer_upstream"))
        self.assertEqual(parse_specialist("L2_DeltaRefinement_b1.2"), ("refiner", "b1.2", ""))
        self.assertEqual(parse_specialist("L0_SeedArchitectNode")[0], "architect")
        self.assertEqual(parse_specialist("L1_AdversarialFalsifier")[0], "falsifier")
        self.assertEqual(parse_specialist("PrimaryRepairSpecialist")[0], "")

    def test_generalist_persona_is_empty(self):
        self.assertEqual(render_persona(generalists()[1]), "")
        txt = render_persona(specialist("Sibling Auditor", "refiner", modes=["regression_in_sibling"]))
        self.assertIn("Sibling Auditor", txt)
        self.assertIn("1. Do the first thing.", txt)
        self.assertIn("regression in sibling", txt)


class StaffingTest(unittest.TestCase):
    def test_static_staffing_one_role_per_layer_and_deterministic(self):
        lib = generalists() + [specialist("Deprecation Executor", "executor", tags=["deprecationwarning", "legacy"]),
                               specialist("Parser Executor", "executor", tags=["regex", "parsing"])]
        tags = ["deprecationwarning", "legacy", "add_items"]
        a = HarnessOrg(lib, CEOPolicyGene(), "static", tags, logger=lambda m: None)
        b = HarnessOrg(lib, CEOPolicyGene(), "static", tags, logger=lambda m: None)
        self.assertEqual(set(a.team), set(V15_PERSONAS))
        self.assertEqual(a.team["executor"].name, "Deprecation Executor")
        self.assertEqual({k: r.role_id for k, r in a.team.items()}, {k: r.role_id for k, r in b.team.items()})
        self.assertTrue(a.team["refiner"].is_generalist)  # no specialist -> generalist

    def test_no_overlap_keeps_generalist(self):
        lib = generalists() + [specialist("Parser Executor", "executor", tags=["regex", "parsing"])]
        org = HarnessOrg(lib, CEOPolicyGene(), "static", ["unrelated", "words"], logger=lambda m: None)
        self.assertTrue(org.team["executor"].is_generalist)

    def test_dynamic_per_branch_executor(self):
        lib = generalists() + [specialist("Upstream Executor", "executor", tags=["producer_upstream"])]
        org = HarnessOrg(lib, CEOPolicyGene(), "dynamic", ["foo"], logger=lambda m: None)
        self.assertEqual(org.role_for_call("L2_BranchExecutor_b1_consumer_local").name, V15_PERSONAS["executor"])
        self.assertEqual(org.role_for_call("L2_BranchExecutor_b2_producer_upstream").name, "Upstream Executor")
        static = HarnessOrg(lib, CEOPolicyGene(), "static", ["foo"], logger=lambda m: None)
        self.assertTrue(static.role_for_call("L2_BranchExecutor_b2_producer_upstream").is_generalist)

    def test_recruit_on_stall_swaps_refiner(self):
        lib = generalists() + [specialist("Edge-Case Refiner", "refiner", tags=["zzz"])]
        pol = CEOPolicyGene(enabled=True, stall_moves=2, stall_delta_u=0.05, recruit_bias=0.0,
                            recruit_w_stall=1.0, recruit_w_headcount=0.25, recruit_cooldown_moves=1)
        org = HarnessOrg(lib, pol, "dynamic", ["foo"], logger=lambda m: None)
        self.assertTrue(org.team["refiner"].is_generalist)

        class N:
            def __init__(self, v):
                self.value, self.falsifier_ok = v, None

        org.role_for_call("L2_DeltaRefinement_b1.1")
        org.after_episode("b1.1", "b1", 0.5, N(0.5), 2)
        self.assertEqual(org.swaps, [])
        org.role_for_call("L2_DeltaRefinement_b1.2")
        org.after_episode("b1.2", "b1", 0.5, N(0.48), 3)
        self.assertEqual(len(org.swaps), 1)
        self.assertEqual(org.team["refiner"].name, "Edge-Case Refiner")
        self.assertEqual(org.role_for_call("L2_DeltaRefinement_b1.3").name, "Edge-Case Refiner")
        self.assertEqual(org.credits[org.generalist("refiner").role_id]["episodes"], 2)

    def test_static_never_recruits(self):
        lib = generalists() + [specialist("Edge-Case Refiner", "refiner", tags=["zzz"])]
        pol = CEOPolicyGene(enabled=True, stall_moves=1, recruit_bias=2.0, recruit_cooldown_moves=1)
        org = HarnessOrg(lib, pol, "static", ["foo"], logger=lambda m: None)

        class N:
            value, falsifier_ok = 0.0, None

        for i in range(4):
            org.after_episode(f"b1.{i}", "b1", 0.5, N(), i)
        self.assertEqual(org.swaps, [])


class GoldenPromptTest(unittest.TestCase):
    """org_mode=off and generalist-only staffing must leave every prompt byte-identical to V15."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "pkg"))
        with open(os.path.join(root, "pkg", "calc.py"), "w") as fh:
            fh.write("def add_items(a, b, extra=None):\n    return a + b\n")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "x"], cwd=root, check=True)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def _engine(self):
        ex = LocalExecutor(root=self.root, python=sys.executable)
        task = SweTask(instance_id="demo__calc-1", repo="demo/calc", base_commit="HEAD", version="1.0",
                       problem_statement="`add_items` ignores `extra`; emit DeprecationWarning for legacy.")
        st = EpistemicState(company_id="t")
        st.add_question("Root", module="pkg/calc.py", uncertainty=0.9)
        gk = EvidenceGatekeeper(workspace=self.root, python_executable=sys.executable, executor=ex, logger=lambda _: None)
        eng = HierarchicalAgenticMCTSEngine(_Runner(self.root), ex, task, st, gk, logger=lambda m: None)
        captured = []

        def fake_call(role_name, messages, temperature=0.2, enable_thinking=True):
            captured.append((role_name, [dict(m) for m in messages], temperature))
            return "{}", ""

        eng._call_specialist_turn = fake_call
        return eng, captured

    def _drive(self, eng):
        recon = ReconBrief(consumer_locus="pkg/calc.py:1", inspected_files=["pkg/calc.py"])
        eng.formulate_seed_contract_and_branches(["pkg/calc.py"], recon)
        for name, sysmsg in (
            ("L2_BranchExecutor_b1_consumer_local", eng._system_prompt("Layer-L2 Scoped Branch Executor (consumer_local)", "g")),
            ("L2_DeltaRefinement_b1.1", eng._system_prompt("Layer-L2 Clean-Context Delta-Refinement Specialist (b1.1)", "g")),
            ("L1_AdversarialFalsifier", eng._falsifier_system_prompt()),
        ):
            eng._call_specialist_turn(name, [{"role": "system", "content": sysmsg}, {"role": "user", "content": "u"}],
                                      temperature=0.3, enable_thinking=False)

    def test_generalist_staffing_is_byte_identical_to_off(self):
        base, cap_off = self._engine()
        self._drive(base)
        eng, cap_org = self._engine()
        org = HarnessOrg(generalists(), CEOPolicyGene(), "static", ["deprecationwarning"], logger=lambda m: None)
        org.attach(eng)
        self._drive(eng)
        self.assertEqual(cap_off, cap_org)
        self.assertEqual(len(cap_off), 4)
        self.assertEqual(sum(v["calls"] for v in org.calls.values()), 4)

    def test_specialists_append_only_to_system_message(self):
        base, cap_off = self._engine()
        self._drive(base)
        lib = generalists() + [specialist(f"S-{l}", l, tags=["deprecationwarning"]) for l in ("architect", "executor", "refiner", "falsifier")]
        eng, cap_org = self._engine()
        org = HarnessOrg(lib, CEOPolicyGene(), "static", ["deprecationwarning"], logger=lambda m: None)
        org.attach(eng)
        self._drive(eng)
        for (n0, m0, t0), (n1, m1, t1) in zip(cap_off, cap_org):
            self.assertEqual((n0, t0), (n1, t1))  # temperature untouched
            self.assertTrue(m1[0]["content"].startswith(m0[0]["content"]))
            self.assertIn("## Organization assignment", m1[0]["content"][len(m0[0]["content"]):])
            self.assertEqual(m0[1:], m1[1:])


if __name__ == "__main__":
    unittest.main()
