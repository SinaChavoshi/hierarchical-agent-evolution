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

# Functional categories used for department classification and crossover alignment.
FUNCTIONAL_CATEGORIES = {
    'systems_eng': ['engineering', 'systems', 'architecture', 'devops', 'infrastructure'],
    'qa_testing': ['qa', 'quality', 'redteam', 'verification', 'testing', 'security'],
    'product_ux': ['product', 'ux', 'design', 'specification', 'frontend'],
    'market_strategy': ['strategy', 'market', 'executive', 'analysis', 'growth'],
    'finance_ops': ['finance', 'operations', 'cost', 'budget', 'compliance'],
    'ai_acceleration': ['acceleration', 'hardware', 'kernels', 'tpu', 'gpu', 'compiler'],
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec'],
}

# Departments that must never be pruned during morphogenesis.
PROTECTED_DEPT_IDS = {'dept_systems_eng', 'dept_qa_redteam'}

# Technical departments that require tool enablement on spawn.
TECHNICAL_CATEGORIES = {'formal_verification', 'systems_eng', 'qa_testing', 'ai_acceleration'}


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
        # Deep-copy isolation
        child = parent.copy()

        # Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(child.mutation_history) + [mutation_name]

        # Ensure code_overlays is an independent copy (already handled by deep copy,
        # but explicit for clarity)
        child.code_overlays = dict(parent.code_overlays)

        # Determine which departments to keep/prune based on mutation semantics.
        # For a generic morph, we preserve all departments but ensure protected ones
        # are present and at least 2 departments exist.
        # If pruning were to occur, we'd filter here. For now, we assume the mutation
        # name might imply adding or reshaping, but we must guarantee invariants.

        # Ensure protected departments exist if they were in parent, or add them if missing
        # and the mutation implies structural integrity.
        existing_dept_ids = {d.dept_id for d in child.departments}

        # If we have fewer than 2 departments, we need to add some.
        # We'll add a systems_eng and qa_testing pod if missing.
        if len(child.departments) < 2:
            if 'dept_systems_eng' not in existing_dept_ids:
                sys_dept = self._create_default_department(
                    dept_id='dept_systems_eng',
                    name='Systems Engineering',
                    mandate='Core infrastructure and systems architecture',
                    category='systems_eng',
                )
                child.departments.append(sys_dept)
                existing_dept_ids.add('dept_systems_eng')

            if len(child.departments) < 2 and 'dept_qa_redteam' not in existing_dept_ids:
                qa_dept = self._create_default_department(
                    dept_id='dept_qa_redteam',
                    name='QA & Red Team',
                    mandate='Quality assurance, security testing, and red team exercises',
                    category='qa_testing',
                )
                child.departments.append(qa_dept)
                existing_dept_ids.add('dept_qa_redteam')

        # Ensure protected departments are never pruned. If they were somehow removed
        # in a prior step (not in this implementation, but defensive), re-add them.
        for protected_id in PROTECTED_DEPT_IDS:
            if protected_id not in existing_dept_ids:
                # Determine category from ID
                if protected_id == 'dept_systems_eng':
                    cat = 'systems_eng'
                    name = 'Systems Engineering'
                    mandate = 'Core infrastructure and systems architecture'
                elif protected_id == 'dept_qa_redteam':
                    cat = 'qa_testing'
                    name = 'QA & Red Team'
                    mandate = 'Quality assurance, security testing, and red team exercises'
                else:
                    continue

                dept = self._create_default_department(
                    dept_id=protected_id,
                    name=name,
                    mandate=mandate,
                    category=cat,
                )
                child.departments.append(dept)

        # Final check: must have at least 2 departments
        if len(child.departments) < 2:
            # Add a generic custom department if still short
            generic_dept = self._create_default_department(
                dept_id='dept_custom_ops',
                name='Custom Operations',
                mandate='General operational support',
                category='custom_specialized',
            )
            child.departments.append(generic_dept)

        # Tool enablement for technical departments
        for dept in child.departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_CATEGORIES:
                # Enable tools on all worker agents in this department
                if dept.manager and dept.manager.model_tier == 'worker':
                    dept.manager.tools_enabled = True
                for agent in dept.agents:
                    if agent.model_tier == 'worker':
                        agent.tools_enabled = True

        return child

    def _create_default_department(
        self,
        dept_id: str,
        name: str,
        mandate: str,
        category: str,
    ) -> DepartmentGenome:
        """Creates a default department with a manager and one worker agent."""
        manager = AgentGenome(
            role=f"{name} Manager",
            goal=f"Lead {name} operations",
            backstory=f"Experienced leader in {category.replace('_', ' ')}",
            backstory_traits=["strategic", "technical"],
            temperature=0.7,
            model_tier="executive",
            tools_enabled=False,
        )

        worker = AgentGenome(
            role=f"{name} Specialist",
            goal=f"Execute {name} tasks",
            backstory=f"Skilled practitioner in {category.replace('_', ' ')}",
            backstory_traits=["detail-oriented", "collaborative"],
            temperature=0.7,
            model_tier="worker",
            tools_enabled=True,  # Default to True for technical, will be enforced later
        )

        return DepartmentGenome(
            dept_id=dept_id,
            name=name,
            mandate=mandate,
            manager=manager,
            agents=[worker],
        )


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
        # Deep-copy isolation: start with a copy of parent_a
        child = parent_a.copy()

        # Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]

        # Code Overlay Inheritance: parent_a takes precedence
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # CEO Crossover
        if parent_a.ceo and parent_b.ceo:
            # Combine and deduplicate backstory traits, preserving order
            combined_traits = []
            seen = set()
            for trait in parent_a.ceo.backstory_traits:
                if trait not in seen:
                    combined_traits.append(trait)
                    seen.add(trait)
            for trait in parent_b.ceo.backstory_traits:
                if trait not in seen:
                    combined_traits.append(trait)
                    seen.add(trait)
            # Cap at 6 traits
            combined_traits = combined_traits[:6]

            # Average temperature, rounded to 2 decimal places
            avg_temp = round((parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0, 2)

            # Create new CEO genome
            child.ceo = AgentGenome(
                role=parent_a.ceo.role,
                goal=parent_a.ceo.goal,
                backstory=parent_a.ceo.backstory,
                backstory_traits=combined_traits,
                temperature=avg_temp,
                model_tier=parent_a.ceo.model_tier,
                tools_enabled=parent_a.ceo.tools_enabled,
                system_instructions=parent_a.ceo.system_instructions,
            )

        # Department Alignment and Crossover
        # Group departments by functional category
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

        for cat in all_categories:
            depts_a = depts_a_by_cat.get(cat, [])
            depts_b = depts_b_by_cat.get(cat, [])

            # If both parents have departments in this category, perform crossover
            if depts_a and depts_b:
                # Take the first department from each for simplicity, or merge all
                # For now, we'll create one merged department per category
                dept_a = depts_a[0]
                dept_b = depts_b[0]

                # Recombine manager traits and temperature
                if dept_a.manager and dept_b.manager:
                    combined_mgr_traits = []
                    seen = set()
                    for trait in dept_a.manager.backstory_traits:
                        if trait not in seen:
                            combined_mgr_traits.append(trait)
                            seen.add(trait)
                    for trait in dept_b.manager.backstory_traits:
                        if trait not in seen:
                            combined_mgr_traits.append(trait)
                            seen.add(trait)
                    combined_mgr_traits = combined_mgr_traits[:6]

                    avg_mgr_temp = round((dept_a.manager.temperature + dept_b.manager.temperature) / 2.0, 2)

                    new_manager = AgentGenome(
                        role=dept_a.manager.role,
                        goal=dept_a.manager.goal,
                        backstory=dept_a.manager.backstory,
                        backstory_traits=combined_mgr_traits,
                        temperature=avg_mgr_temp,
                        model_tier=dept_a.manager.model_tier,
                        tools_enabled=dept_a.manager.tools_enabled,
                        system_instructions=dept_a.manager.system_instructions,
                    )
                else:
                    new_manager = dept_a.manager or dept_b.manager

                # Interleave specialist agents
                all_agents_a = list(dept_a.agents)
                all_agents_b = list(dept_b.agents)

                # Interleave: alternate from A and B
                interleaved_agents = []
                max_len = max(len(all_agents_a), len(all_agents_b))
                for i in range(max_len):
                    if i < len(all_agents_a):
                        interleaved_agents.append(all_agents_a[i].copy())
                    if i < len(all_agents_b):
                        interleaved_agents.append(all_agents_b[i].copy())

                # Create merged department
                merged_dept = DepartmentGenome(
                    dept_id=dept_a.dept_id,  # Use A's ID as primary
                    name=dept_a.name,
                    mandate=dept_a.mandate,
                    manager=new_manager,
                    agents=interleaved_agents,
                    delegation_rules=dept_a.delegation_rules,
                )
                child_departments.append(merged_dept)

            elif depts_a:
                # Only A has this category, copy its departments
                for dept in depts_a:
                    child_departments.append(dept.copy())
            elif depts_b:
                # Only B has this category, copy its departments
                for dept in depts_b:
                    child_departments.append(dept.copy())

        child.departments = child_departments

        # Ensure at least 2 departments
        if len(child.departments) < 2:
            # Add a generic department if needed
            generic_dept = DepartmentGenome(
                dept_id='dept_custom_ops',
                name='Custom Operations',
                mandate='General operational support',
                manager=AgentGenome(
                    role="Operations Manager",
                    goal="Lead operations",
                    backstory="Experienced operations leader",
                    backstory_traits=["strategic", "efficient"],
                    temperature=0.7,
                    model_tier="executive",
                    tools_enabled=False,
                ),
                agents=[AgentGenome(
                    role="Operations Specialist",
                    goal="Execute operations tasks",
                    backstory="Skilled operations practitioner",
                    backstory_traits=["detail-oriented", "collaborative"],
                    temperature=0.7,
                    model_tier="worker",
                    tools_enabled=True,
                )],
            )
            child.departments.append(generic_dept)

        return child