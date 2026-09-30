"""Autonomous Morphogenesis & Dynamic Organizational Topologies Engine (Generation 9).

Synthesizes custom enterprise topologies and enables structural allelic crossover across
asymmetric organizational hierarchies.
"""

from __future__ import annotations

import copy
import random
from typing import Dict, List, Optional, Any

from hae.genome.schema import (
    AgentGenome,
    DepartmentGenome,
    CompanyGenome,
    GenomeValidationError,
)

# ---------------------------------------------------------------------------
# Constants
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

# Technical departments that require tools_enabled=True on spawn
TECHNICAL_CATEGORIES = {'formal_verification', 'systems_eng', 'qa_testing', 'ai_acceleration'}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Morphogenesis Engine
# ---------------------------------------------------------------------------

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
        # Invariant 1: Deep-copy isolation
        child = parent.copy()

        # Invariant 2: Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history) + [mutation_name]

        # Determine which mutation to apply based on mutation_name
        mutation_lower = mutation_name.lower()

        if "prune" in mutation_lower or "remove" in mutation_lower:
            # Prune non-protected departments, keeping at least 2
            protected = [d for d in child.departments if d.dept_id in PROTECTED_DEPT_IDS]
            non_protected = [d for d in child.departments if d.dept_id not in PROTECTED_DEPT_IDS]

            # Keep all protected + enough non-protected to have at least 2 total
            min_non_protected = max(0, 2 - len(protected))
            if len(non_protected) > min_non_protected:
                # Remove some non-protected departments
                to_keep = non_protected[:min_non_protected] if min_non_protected > 0 else []
                child.departments = protected + to_keep
            # If already at or below minimum, keep all

        elif "add" in mutation_lower or "spawn" in mutation_lower or "create" in mutation_lower:
            # Add a new department based on mutation_name hints
            new_dept = self._create_new_department(mutation_name, child)
            if new_dept is not None:
                child.departments.append(new_dept)

        elif "reshape" in mutation_lower or "restructure" in mutation_lower or "morph" in mutation_lower:
            # Reshape: reclassify and potentially reorganize
            # For now, ensure all departments have valid structure
            pass

        # Invariant 4: Ensure at least 2 departments
        if len(child.departments) < 2:
            # This shouldn't happen if protected pods are retained, but as a safety net
            # we don't add departments here since the spec says "must retain at least 2"
            # implying the parent should already have them. If not, we leave as-is
            # since we can't fabricate departments without more context.
            pass

        # Invariant 3: Tool enablement for newly spawned technical departments
        # We need to identify which departments are "newly spawned" vs inherited.
        # Since we deep-copied, all departments are technically "new" objects.
        # The spec says "newly spawned technical department" - this applies to departments
        # that were added during this morph operation.
        # For pruned/reshaped, existing departments retain their tools_enabled state.
        # For added departments, we set tools_enabled=True on worker agents.

        # Identify departments that were added (not in parent)
        parent_dept_ids = {d.dept_id for d in parent.departments}
        for dept in child.departments:
            if dept.dept_id not in parent_dept_ids:
                # This is a newly spawned department
                category = classify_department_role(dept)
                if category in TECHNICAL_CATEGORIES:
                    # Set tools_enabled=True on worker AgentGenome instances
                    for agent in dept.agents:
                        if agent.model_tier == "worker":
                            agent.tools_enabled = True
                    # Also check manager if it's a worker
                    if dept.manager and dept.manager.model_tier == "worker":
                        dept.manager.tools_enabled = True

        return child

    def _create_new_department(self, mutation_name: str, parent: CompanyGenome) -> Optional[DepartmentGenome]:
        """Creates a new department based on mutation name hints."""
        mutation_lower = mutation_name.lower()

        # Determine category from mutation name
        category = None
        for cat, keywords in FUNCTIONAL_CATEGORIES.items():
            for kw in keywords:
                if kw in mutation_lower:
                    category = cat
                    break
            if category:
                break

        if category is None:
            # Default to a generic specialized department
            category = "custom_specialized"

        # Generate dept_id
        existing_ids = {d.dept_id for d in parent.departments}
        base_id = f"dept_{category}"
        dept_id = base_id
        counter = 1
        while dept_id in existing_ids:
            dept_id = f"{base_id}_{counter}"
            counter += 1

        # Create manager
        manager = AgentGenome(
            role=f"{category.replace('_', ' ').title()} Lead",
            goal=f"Lead {category.replace('_', ' ')} operations",
            backstory=f"Experienced leader in {category.replace('_', ' ')}",
            backstory_traits=["leadership", "technical_depth"],
            temperature=0.7,
            model_tier="executive",
            tools_enabled=False,
        )

        # Create worker agents
        agents = []
        num_workers = 2
        for i in range(num_workers):
            agent = AgentGenome(
                role=f"{category.replace('_', ' ').title()} Specialist {i + 1}",
                goal=f"Execute {category.replace('_', ' ')} tasks",
                backstory=f"Skilled specialist in {category.replace('_', ' ')}",
                backstory_traits=["expertise", "execution"],
                temperature=0.7,
                model_tier="worker",
                tools_enabled=True if category in TECHNICAL_CATEGORIES else False,
            )
            agents.append(agent)

        dept = DepartmentGenome(
            dept_id=dept_id,
            name=f"{category.replace('_', ' ').title()} Pod",
            mandate=f"Handle all {category.replace('_', ' ')} responsibilities",
            manager=manager,
            agents=agents,
        )

        return dept


# ---------------------------------------------------------------------------
# Structural Crossover Engine
# ---------------------------------------------------------------------------

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
        # Invariant 1: Deep-copy isolation - work on copies
        pa = parent_a.copy()
        pb = parent_b.copy()

        # Invariant 4: CEO Crossover
        ceo_a = pa.ceo
        ceo_b = pb.ceo

        # Combine and deduplicate backstory_traits, preserving order, capped at 6
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

        # Temperature: rounded average
        avg_temp = round((ceo_a.temperature + ceo_b.temperature) / 2.0, 2)

        # Create new CEO by combining attributes
        # Use parent_a's CEO as base, override with crossover values
        new_ceo = ceo_a.copy()
        new_ceo.backstory_traits = combined_traits
        new_ceo.temperature = avg_temp
        # Keep role, goal, backstory, model_tier from parent_a (or could blend)
        # For now, inherit from parent_a as the primary parent

        # Invariant 5: Department Alignment
        # Classify all departments from both parents
        depts_a = [(d, classify_department_role(d)) for d in pa.departments]
        depts_b = [(d, classify_department_role(d)) for d in pb.departments]

        # Group by category
        cats_a: Dict[str, List[DepartmentGenome]] = {}
        cats_b: Dict[str, List[DepartmentGenome]] = {}

        for dept, cat in depts_a:
            cats_a.setdefault(cat, []).append(dept)
        for dept, cat in depts_b:
            cats_b.setdefault(cat, []).append(dept)

        # Get all unique categories
        all_categories = set(cats_a.keys()) | set(cats_b.keys())

        child_departments = []

        for cat in sorted(all_categories):
            a_depts = cats_a.get(cat, [])
            b_depts = cats_b.get(cat, [])

            if a_depts and b_depts:
                # Both parents have departments in this category - perform crossover
                # Take the first from each for simplicity, or interleave
                # For now, create one merged department per category
                merged = self._crossover_departments(a_depts[0], b_depts[0], cat)
                child_departments.append(merged)
            elif a_depts:
                # Only parent_a has this category - inherit from a
                child_departments.append(a_depts[0].copy())
            elif b_depts:
                # Only parent_b has this category - inherit from b
                child_departments.append(b_depts[0].copy())

        # Invariant 2: Lineage
        # Invariant 3: Code overlay inheritance
        # parent_a takes precedence over parent_b
        merged_overlays = {**pb.code_overlays, **pa.code_overlays}

        child = CompanyGenome(
            company_id=child_id,
            generation=target_generation,
            parent_ids=[parent_a.company_id, parent_b.company_id],
            mutation_history=[f"Crossover:{label}"],
            ceo=new_ceo,
            departments=child_departments,
            executive_deliberation_rules=pa.executive_deliberation_rules,
            budget_usd=pa.budget_usd,
            code_overlays=merged_overlays,
        )

        return child

    def _crossover_departments(
        self,
        dept_a: DepartmentGenome,
        dept_b: DepartmentGenome,
        category: str,
    ) -> DepartmentGenome:
        """Performs allelic crossover between two departments of the same category."""
        # Manager crossover: combine traits and average temperature
        mgr_a = dept_a.manager
        mgr_b = dept_b.manager

        if mgr_a and mgr_b:
            # Combine and deduplicate manager traits
            combined_mgr_traits = []
            seen = set()
            for trait in mgr_a.backstory_traits:
                if trait not in seen:
                    combined_mgr_traits.append(trait)
                    seen.add(trait)
            for trait in mgr_b.backstory_traits:
                if trait not in seen:
                    combined_mgr_traits.append(trait)
                    seen.add(trait)
            combined_mgr_traits = combined_mgr_traits[:6]

            avg_mgr_temp = round((mgr_a.temperature + mgr_b.temperature) / 2.0, 2)

            new_mgr = mgr_a.copy()
            new_mgr.backstory_traits = combined_mgr_traits
            new_mgr.temperature = avg_mgr_temp
        elif mgr_a:
            new_mgr = mgr_a.copy()
        elif mgr_b:
            new_mgr = mgr_b.copy()
        else:
            # Should not happen due to schema validation, but safety
            new_mgr = AgentGenome(
                role=f"{category.replace('_', ' ').title()} Lead",
                goal=f"Lead {category.replace('_', ' ')} operations",
                backstory=f"Leader in {category.replace('_', ' ')}",
                backstory_traits=["leadership"],
                temperature=0.7,
                model_tier="executive",
            )

        # Agent interleaving: take agents from both parents, alternating
        agents_a = dept_a.agents
        agents_b = dept_b.agents

        interleaved_agents = []
        max_len = max(len(agents_a), len(agents_b))
        for i in range(max_len):
            if i < len(agents_a):
                interleaved_agents.append(agents_a[i].copy())
            if i < len(agents_b):
                interleaved_agents.append(agents_b[i].copy())

        # Create merged department
        merged = DepartmentGenome(
            dept_id=dept_a.dept_id,  # Use parent_a's dept_id as primary
            name=dept_a.name,
            mandate=dept_a.mandate,
            manager=new_mgr,
            agents=interleaved_agents,
            delegation_rules=dept_a.delegation_rules,
        )

        return merged