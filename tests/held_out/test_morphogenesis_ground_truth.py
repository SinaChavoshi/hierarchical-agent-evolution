"""Held-out ground-truth test suite for `hae/genome/morphogenesis.py` (Level 3 RSI).

Exercises:
  1. Public API contract (`FUNCTIONAL_CATEGORIES`, `classify_department_role`,
     `MorphogenesisEngine`, `StructuralCrossoverEngine`).
  2. Department functional role classification across all standard categories
     and custom fallback.
  3. Morphogenesis immutability, lineage tracking, and `code_overlays` deep-copy
     isolation.
  4. Structural topology adaptation invariants (valid manager/agent schemas,
     temperature bounds, preservation of critical engineering/verification pods).
  5. Structural crossover allelic alignment, CEO trait deduplication/averaging,
     and specialist agent recombination.
  6. Level 3 RSI overlay inheritance (`code_overlays` merged across parents with
     Parent A taking precedence on key collisions).
  7. Edge-case resilience (empty backstory traits, asymmetric department counts,
     budget preservation).
"""

import copy
import unittest
from typing import List

from hae.genome.schema import AgentGenome, CompanyGenome, DepartmentGenome
import hae.genome.morphogenesis as morph


def _make_agent(role: str, tier: str = "worker", temp: float = 0.5, traits: List[str] = None) -> AgentGenome:
    return AgentGenome(
        role=role,
        goal=f"Execute {role} objectives",
        backstory=f"Specialist in {role}",
        backstory_traits=traits if traits is not None else [f"trait_{role}_1", f"trait_{role}_2"],
        temperature=temp,
        model_tier=tier,
        tools_enabled=(tier == "worker"),
    )


def _make_dept(dept_id: str, name: str, mandate: str, agent_count: int = 2) -> DepartmentGenome:
    return DepartmentGenome(
        dept_id=dept_id,
        name=name,
        mandate=mandate,
        manager=_make_agent(f"Manager of {name}", tier="executive", temp=0.4),
        agents=[_make_agent(f"Specialist {i+1} of {name}", tier="worker", temp=0.6) for i in range(agent_count)],
    )


def _make_company(company_id: str = "test_firm_1", generation: int = 6) -> CompanyGenome:
    return CompanyGenome(
        company_id=company_id,
        generation=generation,
        parent_ids=["ancestor_0"],
        mutation_history=["Initial seed"],
        ceo=_make_agent("Chief Executive Officer", tier="executive", temp=0.5, traits=["visionary", "disciplined"]),
        departments=[
            _make_dept("dept_systems_eng", "Systems Architecture & Engineering", "Build core distributed systems"),
            _make_dept("dept_qa_redteam", "Adversarial QA & Verification", "Stress-test and verify code"),
            _make_dept("dept_product_ux", "Product Design & UX", "Design intuitive user interfaces"),
            _make_dept("dept_finance_ops", "Financial Operations & Compliance", "Optimize unit economics and cost"),
        ],
        budget_usd=0.50,
        code_overlays={"hae/evaluation/artifacts.py": "# verified artifacts v1"},
    )


class TestPublicAPIContract(unittest.TestCase):
    """1. Verifies required constants, functions, and classes exist with expected signatures."""

    def test_exports_exist(self):
        self.assertTrue(hasattr(morph, "FUNCTIONAL_CATEGORIES"))
        self.assertIsInstance(morph.FUNCTIONAL_CATEGORIES, dict)
        for key in (
            "systems_eng",
            "qa_testing",
            "product_ux",
            "market_strategy",
            "finance_ops",
            "ai_acceleration",
            "formal_verification",
        ):
            self.assertIn(key, morph.FUNCTIONAL_CATEGORIES)

        self.assertTrue(callable(getattr(morph, "classify_department_role", None)))
        self.assertTrue(hasattr(morph, "MorphogenesisEngine"))
        self.assertTrue(hasattr(morph, "StructuralCrossoverEngine"))

        engine = morph.MorphogenesisEngine()
        self.assertTrue(callable(getattr(engine, "morph_genome_topology", None)))

        crossover = morph.StructuralCrossoverEngine()
        self.assertTrue(callable(getattr(crossover, "recombine", None)))


class TestClassifyDepartmentRole(unittest.TestCase):
    """2. Verifies department classification across all standard functional categories."""

    def test_standard_categories(self):
        d_eng = _make_dept("d1", "Core Infrastructure", "Cloud systems architecture")
        self.assertEqual(morph.classify_department_role(d_eng), "systems_eng")

        d_qa = _make_dept("d2", "Security Redteam", "Adversarial quality verification")
        self.assertEqual(morph.classify_department_role(d_qa), "qa_testing")

        d_ux = _make_dept("d3", "Frontend Studio", "User experience and product design")
        self.assertEqual(morph.classify_department_role(d_ux), "product_ux")

        d_strat = _make_dept("d4", "Growth & Market Analysis", "Executive strategy")
        self.assertEqual(morph.classify_department_role(d_strat), "market_strategy")

        d_fin = _make_dept("d5", "Budget & Compliance", "Cost operations")
        self.assertEqual(morph.classify_department_role(d_fin), "finance_ops")

        d_ai = _make_dept("d6", "TPU Kernels", "Hardware compiler acceleration")
        self.assertEqual(morph.classify_department_role(d_ai), "ai_acceleration")

        d_formal = _make_dept("d7", "Proof Invariants", "Formal correctness spec")
        self.assertEqual(morph.classify_department_role(d_formal), "formal_verification")

    def test_custom_fallback(self):
        d_custom = _make_dept("dept_quantum_bio", "Astrobiology Unit", "Study exoplanet atmospheres")
        self.assertEqual(morph.classify_department_role(d_custom), "custom_specialized")


class TestMorphogenesisImmutabilityAndLineage(unittest.TestCase):
    """3. Verifies deep-copy immutability, lineage tracking, and code_overlays preservation."""

    def test_immutability_and_lineage(self):
        parent = _make_company("gen_6_parent", generation=6)
        orig_parent_json = parent.to_json()

        engine = morph.MorphogenesisEngine()
        child = engine.morph_genome_topology(
            parent=parent,
            mutation_name="Gen 7 directed mutation",
            target_generation=7,
            child_id="gen_7_mutant_1",
        )

        # Parent must be completely untouched
        self.assertEqual(parent.to_json(), orig_parent_json)

        # Lineage fields
        self.assertEqual(child.company_id, "gen_7_mutant_1")
        self.assertEqual(child.generation, 7)
        self.assertEqual(child.parent_ids, ["gen_6_parent"])
        self.assertIn("Gen 7 directed mutation", child.mutation_history)

        # Code overlays preserved and isolated
        self.assertEqual(child.code_overlays, parent.code_overlays)
        child.code_overlays["new_file.py"] = "# test"
        self.assertNotIn("new_file.py", parent.code_overlays)


class TestMorphogenesisStructuralAdaptation(unittest.TestCase):
    """4. Verifies structural validity of morphed genomes across multiple invocations."""

    def test_structural_validity_and_invariants(self):
        parent = _make_company("gen_6_firm", generation=6)
        engine = morph.MorphogenesisEngine()

        for i in range(15):
            child = engine.morph_genome_topology(
                parent=parent,
                mutation_name=f"Mutation trial {i}",
                target_generation=7,
                child_id=f"gen_7_trial_{i}",
            )
            self.assertIsInstance(child, CompanyGenome)
            self.assertTrue(len(child.departments) >= 2)
            for dept in child.departments:
                self.assertIsNotNone(dept.manager)
                self.assertEqual(dept.manager.model_tier, "executive")
                self.assertTrue(0.0 <= dept.manager.temperature <= 2.0)
                for agent in dept.agents:
                    self.assertTrue(0.0 <= agent.temperature <= 2.0)

            # Core engineering and QA pods should never be pruned away
            dept_ids = {d.dept_id for d in child.departments}
            self.assertIn("dept_systems_eng", dept_ids)
            self.assertIn("dept_qa_redteam", dept_ids)


class TestStructuralCrossoverRecombination(unittest.TestCase):
    """5. Verifies allelic crossover alignment, CEO trait synthesis, and temperature averaging."""

    def test_crossover_mechanics(self):
        parent_a = _make_company("firm_a", generation=6)
        parent_a.ceo.temperature = 0.4
        parent_a.ceo.backstory_traits = ["trait_a1", "trait_a2", "shared_trait"]

        parent_b = _make_company("firm_b", generation=6)
        parent_b.ceo.temperature = 0.8
        parent_b.ceo.backstory_traits = ["shared_trait", "trait_b1", "trait_b2"]

        crossover = morph.StructuralCrossoverEngine()
        child = crossover.recombine(
            parent_a=parent_a,
            parent_b=parent_b,
            child_id="gen_7_crossover_1",
            target_generation=7,
            label="Test Crossover",
        )

        self.assertEqual(child.company_id, "gen_7_crossover_1")
        self.assertEqual(child.generation, 7)
        self.assertEqual(child.parent_ids, ["firm_a", "firm_b"])
        self.assertAlmostEqual(child.ceo.temperature, 0.6, places=2)
        self.assertLessEqual(len(child.ceo.backstory_traits), 6)
        self.assertIn("trait_a1", child.ceo.backstory_traits)
        self.assertIn("trait_b1", child.ceo.backstory_traits)
        # Deduplicated
        self.assertEqual(
            len(child.ceo.backstory_traits),
            len(set(child.ceo.backstory_traits)),
        )


class TestCrossoverOverlayInheritance(unittest.TestCase):
    """6. Verifies Level 3 RSI code_overlays inheritance during sexual crossover."""

    def test_overlay_merge_precedence(self):
        parent_a = _make_company("firm_a", generation=6)
        parent_a.code_overlays = {
            "hae/evaluation/artifacts.py": "# artifacts from A",
            "hae/genome/morphogenesis.py": "# morphogenesis from A",
        }

        parent_b = _make_company("firm_b", generation=6)
        parent_b.code_overlays = {
            "hae/evaluation/artifacts.py": "# artifacts from B",
            "hae/orchestration/worker.py": "# worker from B",
        }

        crossover = morph.StructuralCrossoverEngine()
        child = crossover.recombine(
            parent_a=parent_a,
            parent_b=parent_b,
            child_id="gen_7_crossover_overlay",
            target_generation=7,
        )

        # Should contain keys from both parents, with Parent A taking precedence
        self.assertEqual(
            child.code_overlays.get("hae/evaluation/artifacts.py"),
            "# artifacts from A",
        )
        self.assertEqual(
            child.code_overlays.get("hae/genome/morphogenesis.py"),
            "# morphogenesis from A",
        )
        self.assertEqual(
            child.code_overlays.get("hae/orchestration/worker.py"),
            "# worker from B",
        )


class TestEdgeCaseResilience(unittest.TestCase):
    """7. Verifies resilience to empty trait lists and asymmetric department counts."""

    def test_empty_traits_and_asymmetric_topologies(self):
        parent_a = _make_company("firm_sparse", generation=6)
        parent_a.ceo.backstory_traits = []
        parent_a.departments = parent_a.departments[:2]

        parent_b = _make_company("firm_dense", generation=6)
        parent_b.departments.append(
            _make_dept("dept_formal", "Formal Verification Unit", "Mathematical invariants")
        )

        crossover = morph.StructuralCrossoverEngine()
        child = crossover.recombine(
            parent_a=parent_a,
            parent_b=parent_b,
            child_id="gen_7_asymmetric",
            target_generation=7,
        )
        self.assertIsInstance(child, CompanyGenome)
        self.assertTrue(len(child.departments) >= 2)
        self.assertEqual(child.budget_usd, parent_a.budget_usd)


if __name__ == "__main__":
    unittest.main()
