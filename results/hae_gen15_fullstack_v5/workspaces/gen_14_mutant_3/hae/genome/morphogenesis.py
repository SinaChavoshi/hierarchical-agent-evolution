"""Autonomous Morphogenesis & Dynamic Organizational Topologies Engine (Generation 9).

Synthesizes custom enterprise topologies and enables structural allelic crossover across
asymmetric organizational hierarchies.
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Any

from hae.genome.schema import (
    AgentGenome,
    DepartmentGenome,
    CompanyGenome,
    GenomeValidationError,
)

# Functional categories mapping keywords to category keys.
# Order matters: first match wins.
FUNCTIONAL_CATEGORIES: Dict[str, List[str]] = {
    'systems_eng': ['engineering', 'systems', 'architecture', 'devops', 'infrastructure'],
    'qa_testing': ['qa', 'quality', 'redteam', 'verification', 'testing', 'security'],
    'product_ux': ['product', 'ux', 'design', 'specification', 'frontend'],
    'market_strategy': ['strategy', 'market', 'executive', 'analysis', 'growth'],
    'finance_ops': ['finance', 'operations', 'cost', 'budget', 'compliance'],
    'ai_acceleration': ['acceleration', 'hardware', 'kernels', 'tpu', 'gpu', 'compiler'],
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec'],
}

# Technical categories that require tools_enabled=True on spawned agents
TECHNICAL_CATEGORIES = {
    'formal_verification',
    'systems_eng',
    'qa_testing',
    'ai_acceleration',
}

# Protected department IDs that must never be pruned
PROTECTED_DEPT_IDS = {
    'dept_systems_eng',
    'dept_qa_redteam',
}


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
        child_id: str,
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
        # Deep copy to ensure isolation
        child = parent.copy()

        # Set lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history) + [mutation_name]

        # Ensure code_overlays is an independent copy (already handled by deep copy,
        # but explicit for clarity)
        child.code_overlays = dict(parent.code_overlays)

        # Apply mutation logic based on mutation_name
        # For now, we implement a generic morph that preserves protected pods
        # and ensures minimum department count.

        # Identify protected departments
        protected_depts = [
            d for d in child.departments
            if d.dept_id in PROTECTED_DEPT_IDS
        ]

        # If we have fewer than 2 departments, we need to add some
        # This is a simplified morph: if under minimum, add a generic department
        if len(child.departments) < 2:
            # Add a placeholder department if needed to meet minimum
            if len(child.departments) == 0:
                # This shouldn't happen due to schema validation, but defensive
                new_dept = DepartmentGenome(
                    dept_id="dept_general_ops",
                    name="General Operations",
                    mandate="General operational tasks",
                    manager=AgentGenome(
                        role="Manager",
                        goal="Manage operations",
                        backstory="Experienced manager",
                        model_tier="executive",
                    ),
                    agents=[],
                )
                child.departments.append(new_dept)
            if len(child.departments) < 2:
                new_dept = DepartmentGenome(
                    dept_id="dept_support",
                    name="Support Team",
                    mandate="Support tasks",
                    manager=AgentGenome(
                        role="Support Lead",
                        goal="Provide support",
                        backstory="Support specialist",
                        model_tier="executive",
                    ),
                    agents=[],
                )
                child.departments.append(new_dept)

        # Ensure protected pods are present
        # If a protected pod was somehow removed, re-add it
        existing_protected_ids = {d.dept_id for d in child.departments if d.dept_id in PROTECTED_DEPT_IDS}
        for protected_id in PROTECTED_DEPT_IDS:
            if protected_id not in existing_protected_ids:
                # Re-add protected department with default structure
                if protected_id == "dept_systems_eng":
                    new_dept = DepartmentGenome(
                        dept_id="dept_systems_eng",
                        name="Systems Engineering",
                        mandate="Engineering and infrastructure",
                        manager=AgentGenome(
                            role="Engineering Manager",
                            goal="Lead engineering",
                            backstory="Senior engineer",
                            model_tier="executive",
                        ),
                        agents=[
                            AgentGenome(
                                role="Engineer",
                                goal="Build systems",
                                backstory="Software engineer",
                                model_tier="worker",
                                tools_enabled=True,
                            )
                        ],
                    )
                elif protected_id == "dept_qa_redteam":
                    new_dept = DepartmentGenome(
                        dept_id="dept_qa_redteam",
                        name="QA & Red Team",
                        mandate="Quality assurance and security testing",
                        manager=AgentGenome(
                            role="QA Lead",
                            goal="Ensure quality",
                            backstory="QA specialist",
                            model_tier="executive",
                        ),
                        agents=[
                            AgentGenome(
                                role="QA Engineer",
                                goal="Test systems",
                                backstory="QA engineer",
                                model_tier="worker",
                                tools_enabled=True,
                            )
                        ],
                    )
                else:
                    continue
                child.departments.append(new_dept)

        # Apply tool enablement for technical departments
        for dept in child.departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_CATEGORIES:
                # Set tools_enabled=True on all worker agents in this department
                for agent in dept.agents:
                    if agent.model_tier == "worker":
                        agent.tools_enabled = True
                # Also set on manager if it's a worker tier (unlikely but defensive)
                if dept.manager and dept.manager.model_tier == "worker":
                    dept.manager.tools_enabled = True

        return child


class StructuralCrossoverEngine:
    """Enables genetic crossover between two enterprises with asymmetric departmental topologies."""

    def recombine(
        self,
        parent_a: CompanyGenome,
        parent_b: CompanyGenome,
        child_id: str,
        target_generation: int,
        label: str = 'Recombinant',
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
        # Deep copy parents to ensure isolation
        pa = parent_a.copy()
        pb = parent_b.copy()

        # 4. CEO Crossover
        ceo_a = parent_a.ceo
        ceo_b = parent_b.ceo

        # Combine and deduplicate backstory traits, preserving order, capped at 6
        combined_traits = []
        seen = set()
        for trait in ceo_a.backstory_traits:
            if trait not in seen:
                combined_traits.append(trait)
                seen.add(trait)
        for trait in ceo_b.backstory_traits:
            if trait not in seen:
                combined_traits.append(trait)
                seen.add(trait)
        combined_traits = combined_traits[:6]

        # Average temperature, rounded
        avg_temp = round((ceo_a.temperature + ceo_b.temperature) / 2.0, 2)

        # Create new CEO
        child_ceo = AgentGenome(
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
        for dept in pa.departments:
            cat = classify_department_role(dept)
            depts_a_by_cat.setdefault(cat, []).append(dept)

        depts_b_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for dept in pb.departments:
            cat = classify_department_role(dept)
            depts_b_by_cat.setdefault(cat, []).append(dept)

        # Get all unique categories
        all_categories = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())

        child_departments = []

        # For each category, perform crossover
        for cat in all_categories:
            depts_a = depts_a_by_cat.get(cat, [])
            depts_b = depts_b_by_cat.get(cat, [])

            if not depts_a and not depts_b:
                continue

            # If only one parent has departments in this category, take them as-is
            if not depts_a:
                for dept in depts_b:
                    child_departments.append(dept.copy())
                continue
            if not depts_b:
                for dept in depts_a:
                    child_departments.append(dept.copy())
                continue

            # Both parents have departments in this category
            # Take the first department from each and perform crossover
            dept_a = depts_a[0]
            dept_b = depts_b[0]

            # Create new department with crossover
            # Use dept_a's id and name as base
            new_dept_id = dept_a.dept_id
            new_dept_name = dept_a.name
            new_mandate = dept_a.mandate

            # Crossover manager traits and temperature
            mgr_a = dept_a.manager
            mgr_b = dept_b.manager

            # Combine manager backstory traits, deduplicate, cap at 6
            mgr_combined_traits = []
            mgr_seen = set()
            for trait in mgr_a.backstory_traits:
                if trait not in mgr_seen:
                    mgr_combined_traits.append(trait)
                    mgr_seen.add(trait)
            for trait in mgr_b.backstory_traits:
                if trait not in mgr_seen:
                    mgr_combined_traits.append(trait)
                    mgr_seen.add(trait)
            mgr_combined_traits = mgr_combined_traits[:6]

            # Average manager temperature
            mgr_avg_temp = round((mgr_a.temperature + mgr_b.temperature) / 2.0, 2)

            new_manager = AgentGenome(
                role=mgr_a.role,
                goal=mgr_a.goal,
                backstory=mgr_a.backstory,
                backstory_traits=mgr_combined_traits,
                temperature=mgr_avg_temp,
                model_tier=mgr_a.model_tier,
                tools_enabled=mgr_a.tools_enabled,
                system_instructions=mgr_a.system_instructions,
            )

            # Interleave specialist agents from both parents
            agents_a = dept_a.agents
            agents_b = dept_b.agents
            interleaved_agents = []
            max_len = max(len(agents_a), len(agents_b))
            for i in range(max_len):
                if i < len(agents_a):
                    interleaved_agents.append(agents_a[i].copy())
                if i < len(agents_b):
                    interleaved_agents.append(agents_b[i].copy())

            new_dept = DepartmentGenome(
                dept_id=new_dept_id,
                name=new_dept_name,
                mandate=new_mandate,
                manager=new_manager,
                agents=interleaved_agents,
                delegation_rules=dept_a.delegation_rules,
            )

            child_departments.append(new_dept)

            # If there are additional departments in this category from either parent,
            # add them as-is (capped to avoid explosion)
            for extra_dept in depts_a[1:]:
                child_departments.append(extra_dept.copy())
            for extra_dept in depts_b[1:]:
                child_departments.append(extra_dept.copy())

        # Ensure we have at least one department (schema requirement)
        if not child_departments:
            # Fallback: take first department from parent_a
            if parent_a.departments:
                child_departments.append(parent_a.departments[0].copy())
            elif parent_b.departments:
                child_departments.append(parent_b.departments[0].copy())

        # 3. Code Overlay Inheritance: parent_a takes precedence
        merged_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # Create child with basic lineage and populated fields
        child = CompanyGenome(
            company_id=child_id,
            generation=target_generation,
            parent_ids=[parent_a.company_id, parent_b.company_id],
            mutation_history=[f"Crossover: {label}"],
            ceo=child_ceo,
            departments=child_departments,
            executive_deliberation_rules=parent_a.executive_deliberation_rules,
            budget_usd=parent_a.budget_usd,
            code_overlays=merged_overlays,
        )

        return child