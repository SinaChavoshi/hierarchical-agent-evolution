"""
Autonomous Morphogenesis & Dynamic Organizational Topologies Engine (Generation 9).

Synthesizes custom enterprise topologies and enables structural allelic crossover across
asymmetric organizational hierarchies.
"""

import copy
import json
import random
from typing import List, Dict, Any, Optional, Tuple

from .schema import CompanyGenome, DepartmentGenome, AgentGenome
from .llm_factory import call_vertex_gemini_rest

# Standard functional categories for structural allelic alignment
FUNCTIONAL_CATEGORIES = {
    "systems_eng": ["engineering", "systems", "architecture", "devops", "infrastructure"],
    "qa_testing": ["qa", "quality", "redteam", "verification", "testing", "security"],
    "product_ux": ["product", "ux", "design", "specification", "frontend"],
    "market_strategy": ["strategy", "market", "executive", "analysis", "growth"],
    "finance_ops": ["finance", "operations", "cost", "budget", "compliance"],
    "ai_acceleration": ["acceleration", "hardware", "kernels", "tpu", "gpu", "compiler"],
    "formal_verification": ["formal", "proof", "invariants", "correctness", "spec"]
}

def classify_department_role(dept: DepartmentGenome) -> str:
    """Classifies a department into a functional category based on id and mandate."""
    dept_text = (dept.dept_id + " " + dept.name + " " + dept.mandate).lower()
    for cat, keywords in FUNCTIONAL_CATEGORIES.items():
        if any(kw in dept_text for kw in keywords):
            return cat
    return "custom_specialized"

class MorphogenesisEngine:
    """Dynamically designs and morphs organizational topologies based on strategic objectives."""

    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model_name = model_name

    def morph_genome_topology(
        self,
        parent: CompanyGenome,
        mutation_name: str,
        target_generation: int,
        child_id: str
    ) -> CompanyGenome:
        """Morphs an existing genome by adding, pruning, or reshaping departmental pods."""
        child = copy.deepcopy(parent)
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history) + [mutation_name]

        current_depts = child.departments
        action = random.choice(["add_specialized_pod", "deepen_engineering", "lean_agile_prune"])

        if action == "add_specialized_pod" and len(current_depts) < 7:
            # Morphogenesis: Spawn a specialized pod
            new_pod = DepartmentGenome(
                dept_id="dept_formal_verification",
                name="Formal Verification & Runtime Invariants",
                mandate="Synthesize mathematically proven assertion guards, invariant monitors, and hermetic sandbox test fixtures.",
                manager=AgentGenome(
                    role="VP of Formal Verification & Invariant Engineering",
                    goal="Guarantee 100% test pass rates and zero runtime assertion crashes in sandboxed execution",
                    backstory="Veteran verification researcher specializing in formal methods, abstract interpretation, and automated test synthesis.",
                    backstory_traits=[
                        "Demands mathematically rigorous verification specs before execution",
                        "Applies property-based testing and automated invariant generation",
                        "Rejects ambiguous mock fixtures in favor of hermetic containers"
                    ],
                    temperature=0.2,
                    model_tier="executive"
                ),
                agents=[
                    AgentGenome(
                        role="Staff Invariant Synthesis Engineer",
                        goal="Write deterministic property-based unit tests and automated mock fixtures",
                        backstory="Compiler engineer and test fixture architect.",
                        backstory_traits=["Zero-tolerance for flakey assertions", "Generates comprehensive edge-case inputs"],
                        temperature=0.1,
                        model_tier="worker"
                    ),
                    AgentGenome(
                        role="Sandbox Fault-Injection Specialist",
                        goal="Stress-test code modules against simulated environment faults and missing dependencies",
                        backstory="SRE red-teamer dedicated to proving software resilience.",
                        backstory_traits=["Simulates network dropouts and missing packages", "Hardens exception recovery pathways"],
                        temperature=0.2,
                        model_tier="worker"
                    )
                ]
            )
            child.departments.append(new_pod)

        elif action == "deepen_engineering":
            # Expand systems engineering pod with specialized self-repair engineers
            for dept in child.departments:
                if dept.dept_id == "dept_systems_eng":
                    dept.agents.append(
                        AgentGenome(
                            role="Autonomous Self-Repair Core Architect",
                            goal="Inspect pytest tracebacks in container scratchpads and execute automated code patches",
                            backstory="Expert systems programmer with deep expertise in dynamic patching and AST rewriting.",
                            backstory_traits=[
                                "Reads raw stderr tracebacks with laser focus",
                                "Patches syntax errors and missing imports in single iterations",
                                "Never alters working tests, only fixes implementation deficiencies"
                            ],
                            temperature=0.1,
                            model_tier="worker"
                        )
                    )
                    break

        elif action == "lean_agile_prune" and len(current_depts) > 3:
            # Prune a non-critical department to optimize OpEx unit economics
            prune_candidates = [d for d in current_depts if d.dept_id not in ("dept_systems_eng", "dept_qa_redteam")]
            if prune_candidates:
                victim = random.choice(prune_candidates)
                child.departments = [d for d in current_depts if d.dept_id != victim.dept_id]

        return child

class StructuralCrossoverEngine:
    """Enables genetic crossover between two enterprises with asymmetric departmental topologies."""

    def recombine(
        self,
        parent_a: CompanyGenome,
        parent_b: CompanyGenome,
        child_id: str,
        target_generation: int,
        label: str = "Recombinant"
    ) -> CompanyGenome:
        """Aligns departments by functional role category and performs allelic crossover."""
        child = copy.deepcopy(parent_a)
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        child.mutation_history = [
            f"Gen {target_generation} Structural Morphogenesis: Recombined {parent_a.company_id} x {parent_b.company_id} ({label})"
        ]

        # CEO traits crossover
        ceo_a_traits = getattr(parent_a.ceo, "backstory_traits", []) or []
        ceo_b_traits = getattr(parent_b.ceo, "backstory_traits", []) or []
        combined_traits = list(dict.fromkeys(ceo_a_traits + ceo_b_traits))[:6]
        child.ceo.backstory_traits = combined_traits
        child.ceo.temperature = round((parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0, 2)

        # Map parent B departments by functional category
        b_cats: Dict[str, DepartmentGenome] = {}
        for dept in parent_b.departments:
            b_cats[classify_department_role(dept)] = dept

        # Crossover matching departments, and occasionally adopt unique departments from parent B
        for i, dept_a in enumerate(child.departments):
            cat = classify_department_role(dept_a)
            if cat in b_cats:
                dept_b = b_cats[cat]
                # Crossover manager traits
                mgr_a_traits = getattr(dept_a.manager, "backstory_traits", []) or []
                mgr_b_traits = getattr(dept_b.manager, "backstory_traits", []) or []
                dept_a.manager.backstory_traits = list(dict.fromkeys(mgr_a_traits + mgr_b_traits))[:6]
                dept_a.manager.temperature = round((dept_a.manager.temperature + dept_b.manager.temperature) / 2.0, 2)

                # Recombine specialist agents
                combined_agents = []
                max_len = max(len(dept_a.agents), len(dept_b.agents))
                for idx in range(max_len):
                    source = dept_a.agents if idx % 2 == 0 and idx < len(dept_a.agents) else (
                        dept_b.agents if idx < len(dept_b.agents) else dept_a.agents
                    )
                    if idx < len(source):
                        combined_agents.append(copy.deepcopy(source[idx]))
                dept_a.agents = combined_agents

        # Probabilistically inherit unique departments from parent B that child currently lacks
        existing_cats = {classify_department_role(d) for d in child.departments}
        for cat, dept_b in b_cats.items():
            if cat not in existing_cats and random.random() < 0.35 and len(child.departments) < 7:
                child.departments.append(copy.deepcopy(dept_b))

        return child
