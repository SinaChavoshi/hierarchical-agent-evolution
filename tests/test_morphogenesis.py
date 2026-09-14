"""Unit tests for MorphogenesisEngine and StructuralCrossoverEngine."""

import random
import unittest
from hae.genome.schema import CompanyGenome, AgentGenome, DepartmentGenome
from hae.runtime.company import is_technical_department
from hae.genome.morphogenesis import MorphogenesisEngine, StructuralCrossoverEngine, classify_department_role

class TestMorphogenesis(unittest.TestCase):
    def setUp(self):
        self.ceo = AgentGenome(role="CEO", goal="Lead", backstory="Executive", temperature=0.5, model_tier="executive")
        self.eng_agent = AgentGenome(role="Systems Architect", goal="Build", backstory="Eng", temperature=0.3, model_tier="worker")
        self.qa_agent = AgentGenome(role="QA Lead", goal="Test", backstory="QA", temperature=0.2, model_tier="worker")

        self.dept_eng = DepartmentGenome(
            dept_id="dept_systems_eng",
            name="Systems Engineering",
            mandate="Build distributed systems",
            manager=self.eng_agent,
            agents=[self.eng_agent]
        )
        self.dept_qa = DepartmentGenome(
            dept_id="dept_qa_redteam",
            name="QA & Red Team",
            mandate="Verify software quality",
            manager=self.qa_agent,
            agents=[self.qa_agent]
        )
        self.genome_a = CompanyGenome(
            company_id="parent_a",
            generation=8,
            parent_ids=[],
            mutation_history=["Seed A"],
            ceo=self.ceo,
            departments=[self.dept_eng, self.dept_qa]
        )
        self.genome_b = CompanyGenome(
            company_id="parent_b",
            generation=8,
            parent_ids=[],
            mutation_history=["Seed B"],
            ceo=self.ceo,
            departments=[self.dept_eng]
        )

    def test_classify_department(self):
        self.assertEqual(classify_department_role(self.dept_eng), "systems_eng")
        self.assertEqual(classify_department_role(self.dept_qa), "qa_testing")

    def test_morphogenesis_engine(self):
        engine = MorphogenesisEngine()
        morphed = engine.morph_genome_topology(self.genome_a, "Test Morph", 9, "child_morphed")
        self.assertEqual(morphed.generation, 9)
        self.assertEqual(morphed.company_id, "child_morphed")
        self.assertIn("Test Morph", morphed.mutation_history)
        self.assertTrue(len(morphed.departments) >= 2)

    def test_structural_crossover(self):
        crossover_engine = StructuralCrossoverEngine()
        child = crossover_engine.recombine(self.genome_a, self.genome_b, "child_recombinant", 9)
        self.assertEqual(child.generation, 9)
        self.assertEqual(child.company_id, "child_recombinant")
        self.assertEqual(len(child.parent_ids), 2)
        self.assertTrue(len(child.departments) >= 1)

    def test_technical_department_classification(self):
        """Engineering/verification pods get tools; pure-prose pods do not."""
        self.assertTrue(is_technical_department(self.dept_eng))
        self.assertTrue(is_technical_department(self.dept_qa))

        strategy_agent = AgentGenome(
            role="Market Analyst", goal="Analyze", backstory="Strategy",
            temperature=0.7, model_tier="worker"
        )
        dept_strategy = DepartmentGenome(
            dept_id="dept_market_strategy",
            name="Market & Strategic Intelligence",
            mandate="Assess competitive positioning and growth opportunities",
            manager=strategy_agent,
            agents=[strategy_agent]
        )
        self.assertFalse(is_technical_department(dept_strategy))

        # An explicit tools_enabled genome overrides the keyword heuristic.
        tooled_agent = AgentGenome(
            role="Market Analyst", goal="Analyze", backstory="Strategy",
            temperature=0.7, model_tier="worker", tools_enabled=True
        )
        dept_strategy.agents = [tooled_agent]
        self.assertTrue(is_technical_department(dept_strategy))

    def test_morphogenesis_spawns_tool_enabled_pods(self):
        """Every department morphogenesis can produce must be able to author files.

        Regression guard: synthesized pods previously had tools_enabled unset,
        making them phenotypically inert -- they burned tokens on prose while
        being unable to write a single artifact.
        """
        engine = MorphogenesisEngine()
        for seed in range(30):
            random.seed(seed)
            morphed = engine.morph_genome_topology(
                self.genome_a, "Tool Expression Probe", 9, f"child_{seed}"
            )
            for dept in morphed.departments:
                if dept.dept_id in ("dept_market_strategy", "dept_product_ux", "dept_finance_ops"):
                    continue
                self.assertTrue(
                    is_technical_department(dept),
                    f"seed={seed}: department '{dept.dept_id}' cannot write files"
                )

if __name__ == "__main__":
    unittest.main()
