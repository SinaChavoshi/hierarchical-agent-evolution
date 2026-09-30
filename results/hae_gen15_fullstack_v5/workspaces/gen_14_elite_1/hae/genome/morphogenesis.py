"""Autonomous Morphogenesis & Dynamic Organizational Topologies Engine (Generation 9).

Synthesizes custom enterprise topologies and enables structural allelic crossover across
asymmetric organizational hierarchies.
"""

from __future__ import annotations

import copy
import random
from typing import Any, Dict, List, Optional

from hae.genome.schema import (
    AgentGenome,
    CompanyGenome,
    DepartmentGenome,
)

# ---------------------------------------------------------------------------
# Functional Categories
# ---------------------------------------------------------------------------

FUNCTIONAL_CATEGORIES = {
    'systems_eng': ['engineering', 'systems', 'architecture', 'devops', 'infrastructure'],
    'qa_testing': ['qa', 'quality', 'redteam', 'verification', 'testing', 'security'],
    'product_ux': ['product', 'ux', 'design', 'specification', 'frontend'],
    'market_strategy': ['strategy', 'market', 'executive', 'analysis', 'growth'],
    'finance_ops': ['finance', 'operations', 'cost', 'budget', 'compliance'],
    'ai_acceleration': ['acceleration', 'hardware', 'kernels', 'tpu', 'gpu', 'compiler'],
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec'],
}

# Departments that must never be pruned
PROTECTED_DEPT_IDS = {'dept_systems_eng', 'dept_qa_redteam'}

# Technical departments that require tool enablement on spawn
TECHNICAL_DEPARTMENTS = {'formal_verification', 'systems_eng', 'qa_testing', 'ai_acceleration'}


def classify_department_role(dept: DepartmentGenome) -> str:
    """Classifies a department into a functional category based on id, name, and mandate.

    Checks `(dept.dept_id + " " + dept.name + " " + dept.mandate).lower()` against
    the keyword lists in `FUNCTIONAL_CATEGORIES` in declaration order. Returns the
    first matching category key, or `"custom_specialized"` if no keywords match.
    """
    haystack = f"{dept.dept_id} {dept.name} {dept.mandate}".lower()
    for category, keywords in FUNCTIONAL_CATEGORIES.items():
        for keyword in keywords:
            if keyword in haystack:
                return category
    return "custom_specialized"


class MorphogenesisEngine:
    """Dynamically designs and morphs organizational topologies based on strategic objectives."""

    def __init__(self, model_name: str = 'gemini-2.5-flash'):
        self.model_name = model_name

    def morph_genome_topology(
        self,
        parent: CompanyGenome,
        mutation_name: str,
        target_generation: int,
        child_id: str
    ) -> CompanyGenome:
        """Morphs an existing genome by adding, pruning, or reshaping departmental pods.

        Invariants:
          1. Deep-copy isolation: Must never mutate `parent` in-place. `child.code_overlays`
             must be an independent copy of `parent.code_overlays`.
          2. Lineage: Sets `child.company_id = child_id`, `child.generation = target_generation`,
             `child.parent_ids = [parent.company_id]`, and appends `mutation_name` to
             `child.mutation_history`.
          3. Tool enablement: Any newly spawned technical department (`formal_verification`,
             `systems_eng`, `qa_testing`, `ai_acceleration`) must set `tools_enabled=True` on
             its worker `AgentGenome` instances so they can write files.
          4. Protected pods: Must retain at least 2 departments and must never prune
             `dept_systems_eng` or `dept_qa_redteam`.
        """
        # 1. Deep-copy isolation
        child = parent.copy()
        child.code_overlays = copy.deepcopy(parent.code_overlays)

        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history) + [mutation_name]

        # Determine mutation type from name
        mutation_lower = mutation_name.lower()

        # Identify departments to potentially prune (non-protected)
        prunable_depts = [
            d for d in child.departments
            if d.dept_id not in PROTECTED_DEPT_IDS
        ]

        # Mutation logic based on name
        if "prune" in mutation_lower or "reduce" in mutation_lower or "shrink" in mutation_lower:
            # Prune non-protected departments, but keep at least 2 total
            if len(child.departments) > 2 and prunable_depts:
                # Remove one prunable department
                dept_to_remove = prunable_depts[0]
                child.departments = [
                    d for d in child.departments if d.dept_id != dept_to_remove.dept_id
                ]

        elif "add" in mutation_lower or "spawn" in mutation_lower or "expand" in mutation_lower or "grow" in mutation_lower:
            # Add a new technical department if not already present
            existing_categories = {classify_department_role(d) for d in child.departments}
            new_dept = None

            if "formal" in mutation_lower or "verification" in mutation_lower:
                if "formal_verification" not in existing_categories:
                    new_dept = self._create_technical_department(
                        dept_id="dept_formal_verification",
                        name="Formal Verification",
                        mandate="Formal proof, invariants, correctness, spec verification"
                    )
            elif "ai" in mutation_lower or "acceleration" in mutation_lower or "hardware" in mutation_lower:
                if "ai_acceleration" not in existing_categories:
                    new_dept = self._create_technical_department(
                        dept_id="dept_ai_acceleration",
                        name="AI Acceleration",
                        mandate="Acceleration, hardware, kernels, tpu, gpu, compiler optimization"
                    )
            elif "systems" in mutation_lower or "engineering" in mutation_lower:
                if "systems_eng" not in existing_categories:
                    new_dept = self._create_technical_department(
                        dept_id="dept_systems_eng",
                        name="Systems Engineering",
                        mandate="Engineering, systems, architecture, devops, infrastructure"
                    )
            elif "qa" in mutation_lower or "testing" in mutation_lower or "quality" in mutation_lower:
                if "qa_testing" not in existing_categories:
                    new_dept = self._create_technical_department(
                        dept_id="dept_qa_redteam",
                        name="QA & Red Team",
                        mandate="QA, quality, redteam, verification, testing, security"
                    )
            else:
                # Default: add a generic technical department if space allows
                if len(child.departments) < 6:
                    new_dept = self._create_technical_department(
                        dept_id=f"dept_custom_{len(child.departments)}",
                        name="Custom Technical Pod",
                        mandate="Specialized technical operations"
                    )

            if new_dept is not None:
                child.departments.append(new_dept)

        elif "reshape" in mutation_lower or "restructure" in mutation_lower or "morph" in mutation_lower:
            # Reshape: ensure technical departments have tools enabled
            for dept in child.departments:
                category = classify_department_role(dept)
                if category in TECHNICAL_DEPARTMENTS:
                    for agent in dept.agents:
                        if agent.model_tier == "worker":
                            agent.tools_enabled = True
                    if dept.manager and dept.manager.model_tier == "worker":
                        dept.manager.tools_enabled = True

        else:
            # Default mutation: ensure at least 2 departments and enable tools on technical depts
            if len(child.departments) < 2:
                # Add a minimal technical department
                new_dept = self._create_technical_department(
                    dept_id="dept_systems_eng",
                    name="Systems Engineering",
                    mandate="Engineering, systems, architecture, devops, infrastructure"
                )
                child.departments.append(new_dept)

            # Enable tools on technical departments
            for dept in child.departments:
                category = classify_department_role(dept)
                if category in TECHNICAL_DEPARTMENTS:
                    for agent in dept.agents:
                        if agent.model_tier == "worker":
                            agent.tools_enabled = True
                    if dept.manager and dept.manager.model_tier == "worker":
                        dept.manager.tools_enabled = True

        # Final invariant check: ensure at least 2 departments
        if len(child.departments) < 2:
            # Add a fallback department
            fallback = self._create_technical_department(
                dept_id="dept_fallback_eng",
                name="Fallback Engineering",
                mandate="Engineering, systems, architecture"
            )
            child.departments.append(fallback)

        return child

    def _create_technical_department(
        self,
        dept_id: str,
        name: str,
        mandate: str
    ) -> DepartmentGenome:
        """Creates a new technical department with tools_enabled=True on workers."""
        manager = AgentGenome(
            role=f"{name} Manager",
            goal=f"Lead {name} operations",
            backstory=f"Experienced leader in {name.lower()}",
            backstory_traits=["strategic", "technical"],
            temperature=0.7,
            model_tier="executive",
            tools_enabled=False,
        )

        worker = AgentGenome(
            role=f"{name} Specialist",
            goal=f"Execute {name.lower()} tasks",
            backstory=f"Skilled practitioner in {name.lower()}",
            backstory_traits=["skilled", "detail-oriented"],
            temperature=0.7,
            model_tier="worker",
            tools_enabled=True,  # Tool enablement invariant
        )

        return DepartmentGenome(
            dept_id=dept_id,
            name=name,
            mandate=mandate,
            manager=manager,
            agents=[worker],
            delegation_rules="Sequential review with collaborative cross-questioning",
        )


class StructuralCrossoverEngine:
    """Enables genetic crossover between two enterprises with asymmetric departmental topologies."""

    def recombine(
        self,
        parent_a: CompanyGenome,
        parent_b: CompanyGenome,
        child_id: str,
        target_generation: int,
        label: str = 'Recombinant'
    ) -> CompanyGenome:
        """Aligns departments by functional role category and performs allelic crossover.

        Invariants:
          1. Deep-copy isolation: Must never mutate `parent_a` or `parent_b` in-place.
          2. Lineage: Sets `child.company_id = child_id`, `child.generation = target_generation`,
             `child.parent_ids = [parent_a.company_id, parent_b.company_id]`.
          3. Level 3 RSI Code Overlay Inheritance: Merges `code_overlays` from both parents
             such that `child.code_overlays` contains all keys from both parents, with
             `parent_a.code_overlays` taking precedence over `parent_b.code_overlays` on key
             collisions (`{**parent_b.code_overlays, **parent_a.code_overlays}`).
          4. CEO Crossover: Combines and deduplicates `ceo.backstory_traits` from both parents
             (preserving order, capped at 6 traits) and sets `ceo.temperature` to the rounded
             average of `parent_a.ceo.temperature` and `parent_b.ceo.temperature`.
          5. Department Alignment: Aligns departments by `classify_department_role` to recombine
             manager traits/temperatures and interleave specialist agents.
        """
        # 1. Deep-copy isolation
        child = parent_a.copy()
        child.code_overlays = copy.deepcopy(parent_a.code_overlays)

        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        child.mutation_history = list(parent_a.mutation_history) + [f"Crossover: {label}"]

        # 3. Code Overlay Inheritance: parent_a takes precedence
        merged_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}
        child.code_overlays = copy.deepcopy(merged_overlays)

        # 4. CEO Crossover
        ceo_a = parent_a.ceo
        ceo_b = parent_b.ceo

        # Combine and deduplicate backstory traits, preserving order, capped at 6
        combined_traits = []
        seen_traits = set()
        for trait in ceo_a.backstory_traits:
            if trait not in seen_traits:
                combined_traits.append(trait)
                seen_traits.add(trait)
        for trait in ceo_b.backstory_traits:
            if trait not in seen_traits:
                combined_traits.append(trait)
                seen_traits.add(trait)
        combined_traits = combined_traits[:6]

        # Average temperature, rounded
        avg_temp = round((ceo_a.temperature + ceo_b.temperature) / 2.0, 2)

        # Create new CEO
        child.ceo = AgentGenome(
            role=ceo_a.role,
            goal=ceo_a.goal,
            backstory=ceo_a.backstory,
            backstory_traits=combined_traits,
            temperature=avg_temp,
            model_tier=ceo_a.model_tier,
            tools_enabled=ceo_a.tools_enabled,
            system_instructions=ceo_a.system_instructions,
        )

        # 5. Department Alignment by functional category
        # Group departments from both parents by category
        depts_a_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for dept in parent_a.departments:
            cat = classify_department_role(dept)
            depts_a_by_cat.setdefault(cat, []).append(dept)

        depts_b_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for dept in parent_b.departments:
            cat = classify_department_role(dept)
            depts_b_by_cat.setdefault(cat, []).append(dept)

        # Collect all categories present in either parent
        all_categories = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())

        child_departments: List[DepartmentGenome] = []

        for cat in sorted(all_categories):
            depts_a = depts_a_by_cat.get(cat, [])
            depts_b = depts_b_by_cat.get(cat, [])

            if depts_a and depts_b:
                # Crossover: combine departments from both parents
                # Take the first department from each parent for this category
                dept_a = depts_a[0]
                dept_b = depts_b[0]

                # Crossover manager traits and temperature
                mgr_a = dept_a.manager
                mgr_b = dept_b.manager

                combined_mgr_traits = []
                seen = set()
                for t in mgr_a.backstory_traits:
                    if t not in seen:
                        combined_mgr_traits.append(t)
                        seen.add(t)
                for t in mgr_b.backstory_traits:
                    if t not in seen:
                        combined_mgr_traits.append(t)
                        seen.add(t)
                combined_mgr_traits = combined_mgr_traits[:6]

                mgr_avg_temp = round((mgr_a.temperature + mgr_b.temperature) / 2.0, 2)

                new_manager = AgentGenome(
                    role=mgr_a.role,
                    goal=mgr_a.goal,
                    backstory=mgr_a.backstory,
                    backstory_traits=combined_mgr_traits,
                    temperature=mgr_avg_temp,
                    model_tier=mgr_a.model_tier,
                    tools_enabled=mgr_a.tools_enabled,
                    system_instructions=mgr_a.system_instructions,
                )

                # Interleave specialist agents from both parents
                agents_a = list(dept_a.agents)
                agents_b = list(dept_b.agents)
                interleaved_agents = []
                max_len = max(len(agents_a), len(agents_b))
                for i in range(max_len):
                    if i < len(agents_a):
                        interleaved_agents.append(agents_a[i].copy())
                    if i < len(agents_b):
                        interleaved_agents.append(agents_b[i].copy())

                # Ensure tools_enabled for technical categories
                if cat in TECHNICAL_DEPARTMENTS:
                    for agent in interleaved_agents:
                        if agent.model_tier == "worker":
                            agent.tools_enabled = True
                    if new_manager.model_tier == "worker":
                        new_manager.tools_enabled = True

                new_dept = DepartmentGenome(
                    dept_id=dept_a.dept_id,  # Use parent_a's dept_id
                    name=dept_a.name,
                    mandate=dept_a.mandate,
                    manager=new_manager,
                    agents=interleaved_agents,
                    delegation_rules=dept_a.delegation_rules,
                )
                child_departments.append(new_dept)

            elif depts_a:
                # Only parent_a has this category: copy from parent_a
                for dept in depts_a:
                    child_departments.append(dept.copy())

            elif depts_b:
                # Only parent_b has this category: copy from parent_b
                for dept in depts_b:
                    child_departments.append(dept.copy())

        child.departments = child_departments

        # Ensure at least 2 departments
        if len(child.departments) < 2:
            # Add a fallback department
            fallback_mgr = AgentGenome(
                role="Fallback Manager",
                goal="Lead fallback operations",
                backstory="Generalist leader",
                backstory_traits=["adaptable"],
                temperature=0.7,
                model_tier="executive",
                tools_enabled=False,
            )
            fallback_worker = AgentGenome(
                role="Fallback Specialist",
                goal="Execute fallback tasks",
                backstory="Generalist practitioner",
                backstory_traits=["versatile"],
                temperature=0.7,
                model_tier="worker",
                tools_enabled=True,
            )
            fallback_dept = DepartmentGenome(
                dept_id="dept_fallback",
                name="Fallback Pod",
                mandate="General operations",
                manager=fallback_mgr,
                agents=[fallback_worker],
            )
            child.departments.append(fallback_dept)

        return child