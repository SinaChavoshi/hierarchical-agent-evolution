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

# Functional categories for department classification
FUNCTIONAL_CATEGORIES = {
    'systems_eng': ['engineering', 'systems', 'architecture', 'devops', 'infrastructure'],
    'qa_testing': ['qa', 'quality', 'redteam', 'verification', 'testing', 'security'],
    'product_ux': ['product', 'ux', 'design', 'specification', 'frontend'],
    'market_strategy': ['strategy', 'market', 'executive', 'analysis', 'growth'],
    'finance_ops': ['finance', 'operations', 'cost', 'budget', 'compliance'],
    'ai_acceleration': ['acceleration', 'hardware', 'kernels', 'tpu', 'gpu', 'compiler'],
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec']
}

# Technical departments that require tool enablement when spawned
TECHNICAL_DEPARTMENTS = {
    'formal_verification',
    'systems_eng',
    'qa_testing',
    'ai_acceleration'
}

# Protected department IDs that must never be pruned
PROTECTED_DEPT_IDS = {'dept_systems_eng', 'dept_qa_redteam'}


def classify_department_role(dept: DepartmentGenome) -> str:
    """Classifies a department into a functional category based on id, name, and mandate.

    Checks `(dept.dept_id + " " + dept.name + " " + dept.mandate).lower()` against
    the keyword lists in `FUNCTIONAL_CATEGORIES` in declaration order. Returns the
    first matching category key, or `"custom_specialized"` if no keywords match.
    """
    search_text = f"{dept.dept_id} {dept.name} {dept.mandate}".lower()
    
    for category, keywords in FUNCTIONAL_CATEGORIES.items():
        for keyword in keywords:
            if keyword in search_text:
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
        
        # Ensure code_overlays is an independent copy
        child.code_overlays = copy.deepcopy(parent.code_overlays)

        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history)
        child.mutation_history.append(mutation_name)

        # 3. Tool enablement for newly spawned technical departments
        # We identify departments that are technical and ensure tools are enabled.
        # Since we don't have explicit "spawn" logic in this generic morph, we apply
        # the invariant to all technical departments in the child to ensure compliance
        # for any that might have been added or reshaped.
        
        for dept in child.departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_DEPARTMENTS:
                for agent in dept.agents:
                    if agent.model_tier == "worker":
                        agent.tools_enabled = True
                if dept.manager and dept.manager.model_tier == "worker":
                    dept.manager.tools_enabled = True

        # 4. Protected pods: Must retain at least 2 departments and must never prune
        # `dept_systems_eng` or `dept_qa_redteam`.
        
        # Ensure protected depts are present if they were in parent
        parent_protected = {d.dept_id for d in parent.departments if d.dept_id in PROTECTED_DEPT_IDS}
        child_protected = {d.dept_id for d in child.departments if d.dept_id in PROTECTED_DEPT_IDS}
        
        for pid in parent_protected:
            if pid not in child_protected:
                for p_dept in parent.departments:
                    if p_dept.dept_id == pid:
                        child.departments.append(p_dept.copy())
                        break
        
        # Ensure at least 2 departments
        if len(child.departments) < 2:
            if len(parent.departments) >= 2:
                child.departments = [d.copy() for d in parent.departments]
            else:
                # If parent also had < 2, we try to restore what we can.
                # If still < 2, we raise error as invariant cannot be met.
                child.departments = [d.copy() for d in parent.departments]
                if len(child.departments) < 2:
                    raise GenomeValidationError("Cannot satisfy invariant: at least 2 departments required.")

        return child


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
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]

        # 3. Level 3 RSI Code Overlay Inheritance
        merged_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}
        child.code_overlays = copy.deepcopy(merged_overlays)

        # 4. CEO Crossover
        traits_a = parent_a.ceo.backstory_traits
        traits_b = parent_b.ceo.backstory_traits
        
        combined_traits = []
        seen = set()
        for t in traits_a:
            if t not in seen:
                combined_traits.append(t)
                seen.add(t)
        for t in traits_b:
            if t not in seen:
                combined_traits.append(t)
                seen.add(t)
        
        child.ceo.backstory_traits = combined_traits[:6]
        
        avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0
        child.ceo.temperature = round(avg_temp, 2)

        # 5. Department Alignment
        depts_a_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for d in parent_a.departments:
            cat = classify_department_role(d)
            if cat not in depts_a_by_cat:
                depts_a_by_cat[cat] = []
            depts_a_by_cat[cat].append(d)
            
        depts_b_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for d in parent_b.departments:
            cat = classify_department_role(d)
            if cat not in depts_b_by_cat:
                depts_b_by_cat[cat] = []
            depts_b_by_cat[cat].append(d)
        
        all_cats = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())
        
        new_departments = []
        
        for cat in all_cats:
            list_a = depts_a_by_cat.get(cat, [])
            list_b = depts_b_by_cat.get(cat, [])
            
            if not list_a:
                for d_b in list_b:
                    new_departments.append(d_b.copy())
                continue
            if not list_b:
                for d_a in list_a:
                    new_departments.append(d_a.copy())
                continue
            
            max_len = max(len(list_a), len(list_b))
            
            for i in range(max_len):
                d_a = list_a[i] if i < len(list_a) else None
                d_b = list_b[i] if i < len(list_b) else None
                
                if d_a and d_b:
                    new_dept = self._recombine_departments(d_a, d_b, cat)
                    new_departments.append(new_dept)
                elif d_a:
                    new_departments.append(d_a.copy())
                elif d_b:
                    new_departments.append(d_b.copy())
                    
        child.departments = new_departments
        
        if not child.departments:
            child.departments = [d.copy() for d in parent_a.departments]
            
        return child

    def _recombine_departments(
        self, 
        d_a: DepartmentGenome, 
        d_b: DepartmentGenome, 
        category: str
    ) -> DepartmentGenome:
        """Helper to recombine two departments of the same category."""
        
        new_dept = DepartmentGenome(
            dept_id=d_a.dept_id,
            name=d_a.name,
            mandate=d_a.mandate,
            delegation_rules=d_a.delegation_rules,
            extra={}
        )
        
        mgr_a = d_a.manager
        mgr_b = d_b.manager
        
        if mgr_a and mgr_b:
            traits_a = mgr_a.backstory_traits
            traits_b = mgr_b.backstory_traits
            combined_traits = []
            seen = set()
            for t in traits_a:
                if t not in seen:
                    combined_traits.append(t)
                    seen.add(t)
            for t in traits_b:
                if t not in seen:
                    combined_traits.append(t)
                    seen.add(t)
            
            mgr_new = AgentGenome(
                role=mgr_a.role,
                goal=mgr_a.goal,
                backstory=mgr_a.backstory,
                backstory_traits=combined_traits[:6],
                temperature=round((mgr_a.temperature + mgr_b.temperature) / 2.0, 2),
                model_tier=mgr_a.model_tier,
                tools_enabled=mgr_a.tools_enabled or mgr_b.tools_enabled,
                system_instructions=mgr_a.system_instructions,
                extra={}
            )
            new_dept.manager = mgr_new
        elif mgr_a:
            new_dept.manager = mgr_a.copy()
        elif mgr_b:
            new_dept.manager = mgr_b.copy()
        else:
            raise GenomeValidationError("Department has no manager")

        agents_a = d_a.agents
        agents_b = d_b.agents
        
        interleaved_agents = []
        max_agents = max(len(agents_a), len(agents_b))
        
        for i in range(max_agents):
            if i < len(agents_a):
                interleaved_agents.append(agents_a[i].copy())
            if i < len(agents_b):
                interleaved_agents.append(agents_b[i].copy())
                
        new_dept.agents = interleaved_agents
        
        return new_dept