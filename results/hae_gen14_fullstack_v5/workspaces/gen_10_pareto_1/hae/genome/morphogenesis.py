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

# Functional categories mapping keywords to category keys.
# Order matters for classification priority.
FUNCTIONAL_CATEGORIES = {
    'systems_eng': ['engineering', 'systems', 'architecture', 'devops', 'infrastructure'],
    'qa_testing': ['qa', 'quality', 'redteam', 'verification', 'testing', 'security'],
    'product_ux': ['product', 'ux', 'design', 'specification', 'frontend'],
    'market_strategy': ['strategy', 'market', 'executive', 'analysis', 'growth'],
    'finance_ops': ['finance', 'operations', 'cost', 'budget', 'compliance'],
    'ai_acceleration': ['acceleration', 'hardware', 'kernels', 'tpu', 'gpu', 'compiler'],
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec'],
}

# Departments that are protected from pruning
PROTECTED_DEPT_IDS = {'dept_systems_eng', 'dept_qa_redteam'}

# Technical departments that require tool enablement for workers
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
        
        # Ensure code_overlays is an independent copy (deepcopy handles this, 
        # but explicit check ensures we don't share references if copy() was shallow)
        child.code_overlays = copy.deepcopy(parent.code_overlays)

        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history)
        child.mutation_history.append(mutation_name)

        # 3. & 4. Topology Morphing Logic
        
        # Identify current departments and their categories
        current_depts = child.departments
        
        # Ensure protected departments exist. If they were pruned in parent (shouldn't happen 
        # if parent is valid, but defensive), we might need to add them back? 
        # The spec says "must never prune", implying we shouldn't remove them if they exist.
        # If they don't exist in parent, we don't necessarily add them unless the mutation implies it.
        # However, "retain at least 2 departments" is a hard constraint.
        
        # Simulate a mutation:
        # Strategy: 
        # 1. Identify non-protected departments.
        # 2. If we have > 2 departments, we can potentially prune one non-protected one.
        # 3. If we have < 2 departments, we must add one.
        # 4. If we have exactly 2, we might swap one or add one.
        
        # For a deterministic "morph" that satisfies the contract without external LLM calls 
        # (since none are specified in the API contract for this method, only model_name in init),
        # we implement a structural heuristic.
        
        # Let's assume the mutation_name might hint at the action, but without specific 
        # mutation definitions, we apply a generic "optimization" or "expansion" logic.
        
        # Check for protected departments presence
        has_sys_eng = any(d.dept_id == 'dept_systems_eng' for d in current_depts)
        has_qa = any(d.dept_id == 'dept_qa_redteam' for d in current_depts)
        
        # If protected depts are missing, we should probably add them to satisfy "never prune" 
        # in the sense that the resulting topology should ideally contain them if they are core.
        # But strictly, "never prune" means don't remove them. If they aren't there, we can't prune them.
        # However, a valid enterprise usually has them. Let's ensure they are present if possible.
        
        new_departments = []
        
        # Copy existing departments, ensuring protected ones are kept
        for dept in current_depts:
            # Deep copy each department to ensure isolation
            new_dept = dept.copy()
            new_departments.append(new_dept)
            
        # Logic to add/remove based on "morph"
        # If we have fewer than 2 departments, add a new one.
        # If we have more than 2, we might remove a non-protected one if it's redundant, 
        # but "morph" usually implies change. Let's add a new department if we have space, 
        # or replace a non-protected one.
        
        # To be safe and deterministic:
        # 1. Ensure at least 2 departments.
        # 2. If we have < 2, add a generic "strategy" or "product" dept.
        # 3. If we have >= 2, we can add a new "ai_acceleration" or "formal_verification" 
        #    if not present, to simulate "morphing" towards technical depth.
        
        existing_ids = {d.dept_id for d in new_departments}
        
        # Helper to create a new department
        def _create_new_dept(dept_id: str, name: str, mandate: str, category: str) -> DepartmentGenome:
            manager = AgentGenome(
                role=f"Manager of {name}",
                goal=f"Lead {name} operations",
                backstory=f"Experienced leader in {category}",
                backstory_traits=["strategic", "technical"],
                temperature=0.7,
                model_tier="executive",
                tools_enabled=False
            )
            agents = []
            # Add some worker agents
            for i in range(2):
                agent = AgentGenome(
                    role=f"Specialist {i+1} in {name}",
                    goal=f"Execute tasks in {name}",
                    backstory=f"Expert in {category}",
                    backstory_traits=["skilled", "focused"],
                    temperature=0.7,
                    model_tier="worker",
                    tools_enabled=False # Will be set to True if technical
                )
                agents.append(agent)
            
            dept = DepartmentGenome(
                dept_id=dept_id,
                name=name,
                mandate=mandate,
                manager=manager,
                agents=agents
            )
            return dept

        # If we have less than 2 departments, add one
        if len(new_departments) < 2:
            # Try to add a systems_eng if missing, else product_ux
            if 'dept_systems_eng' not in existing_ids:
                new_dept = _create_new_dept(
                    'dept_systems_eng', 
                    'Systems Engineering', 
                    'Build and maintain core infrastructure', 
                    'systems_eng'
                )
                new_departments.append(new_dept)
                existing_ids.add('dept_systems_eng')
            elif 'dept_product_ux' not in existing_ids:
                new_dept = _create_new_dept(
                    'dept_product_ux', 
                    'Product & UX', 
                    'Design user experiences', 
                    'product_ux'
                )
                new_departments.append(new_dept)
                existing_ids.add('dept_product_ux')
            else:
                # Add a generic one
                new_dept = _create_new_dept(
                    'dept_market_strategy', 
                    'Market Strategy', 
                    'Analyze market trends', 
                    'market_strategy'
                )
                new_departments.append(new_dept)
                existing_ids.add('dept_market_strategy')

        # If we have 2 or more, let's try to add a technical department if not present, 
        # to simulate "morphing" towards higher capability.
        # But we must respect the "at least 2" constraint. We can have more.
        
        # Let's check if we should add 'ai_acceleration' or 'formal_verification'
        if 'dept_ai_acceleration' not in existing_ids and len(new_departments) < 5:
            new_dept = _create_new_dept(
                'dept_ai_acceleration',
                'AI Acceleration',
                'Optimize hardware and kernels',
                'ai_acceleration'
            )
            new_departments.append(new_dept)
            existing_ids.add('dept_ai_acceleration')
        elif 'dept_formal_verification' not in existing_ids and len(new_departments) < 5:
            new_dept = _create_new_dept(
                'dept_formal_verification',
                'Formal Verification',
                'Prove system correctness',
                'formal_verification'
            )
            new_departments.append(new_dept)
            existing_ids.add('dept_formal_verification')

        # Apply Tool Enablement for Technical Departments
        for dept in new_departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_DEPARTMENTS:
                # Set tools_enabled=True for worker agents
                for agent in dept.agents:
                    if agent.model_tier == "worker":
                        agent.tools_enabled = True
                # Managers are executives, usually don't need write tools in this context?
                # Spec says "worker AgentGenome instances".
                if dept.manager and dept.manager.model_tier == "worker":
                    dept.manager.tools_enabled = True

        child.departments = new_departments

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
        # Mutation history? Spec doesn't explicitly say to append label, but usually crossover 
        # is a mutation. Let's append label to mutation_history if it exists.
        child.mutation_history = list(parent_a.mutation_history)
        child.mutation_history.append(label)

        # 3. Code Overlay Inheritance
        # parent_a takes precedence
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # 4. CEO Crossover
        # Combine and deduplicate backstory traits, preserving order, capped at 6
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
        
        # Average temperature
        avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0
        child.ceo.temperature = round(avg_temp, 2) # "rounded average" - usually 2 decimals for floats

        # 5. Department Alignment
        # Group departments by category
        def _group_by_category(depts: List[DepartmentGenome]) -> Dict[str, List[DepartmentGenome]]:
            groups = {}
            for d in depts:
                cat = classify_department_role(d)
                if cat not in groups:
                    groups[cat] = []
                groups[cat].append(d)
            return groups

        groups_a = _group_by_category(parent_a.departments)
        groups_b = _group_by_category(parent_b.departments)
        
        all_categories = set(groups_a.keys()) | set(groups_b.keys())
        
        new_departments = []
        
        for category in all_categories:
            depts_a = groups_a.get(category, [])
            depts_b = groups_b.get(category, [])
            
            # If both have departments in this category, we recombine them.
            # If only one has, we take that one (deep copied).
            
            if not depts_a and not depts_b:
                continue
                
            if not depts_a:
                # Take from B
                for d in depts_b:
                    new_departments.append(d.copy())
                continue
                
            if not depts_b:
                # Take from A
                for d in depts_a:
                    new_departments.append(d.copy())
                continue
            
            # Both have departments. 
            # Strategy: 
            # 1. If counts are equal, pair them up.
            # 2. If unequal, pair up min(count) and append the rest from the larger side.
            
            count_a = len(depts_a)
            count_b = len(depts_b)
            min_count = min(count_a, count_b)
            
            # Pair up
            for i in range(min_count):
                dept_a = depts_a[i]
                dept_b = depts_b[i]
                
                # Create a new department for the child
                # Use dept_a's ID and Name as base? Or merge?
                # Spec says "Aligns departments... to recombine manager traits/temperatures and interleave specialist agents."
                # It doesn't explicitly say how to handle dept_id/name conflicts.
                # We'll use dept_a's ID and Name as the primary identity for the slot.
                
                new_dept = dept_a.copy()
                
                # Recombine Manager
                # Traits: Combine and dedupe, cap at 6? Spec doesn't specify cap for managers, 
                # but CEO had cap 6. Let's assume similar logic or just combine.
                # "recombine manager traits/temperatures"
                
                mgr_a = dept_a.manager
                mgr_b = dept_b.manager
                
                if mgr_a and mgr_b:
                    # Traits
                    traits_m_a = mgr_a.backstory_traits
                    traits_m_b = mgr_b.backstory_traits
                    combined_mgr_traits = []
                    seen_m = set()
                    for t in traits_m_a:
                        if t not in seen_m:
                            combined_mgr_traits.append(t)
                            seen_m.add(t)
                    for t in traits_m_b:
                        if t not in seen_m:
                            combined_mgr_traits.append(t)
                            seen_m.add(t)
                    new_dept.manager.backstory_traits = combined_mgr_traits[:6] # Cap at 6 for consistency
                    
                    # Temperature
                    avg_mgr_temp = (mgr_a.temperature + mgr_b.temperature) / 2.0
                    new_dept.manager.temperature = round(avg_mgr_temp, 2)
                    
                    # Goal/Backstory? Spec doesn't say. Keep A's.
                    
                # Interleave Specialist Agents
                agents_a = dept_a.agents
                agents_b = dept_b.agents
                
                # Interleave: A1, B1, A2, B2...
                interleaved_agents = []
                max_agents = max(len(agents_a), len(agents_b))
                for j in range(max_agents):
                    if j < len(agents_a):
                        interleaved_agents.append(agents_a[j].copy())
                    if j < len(agents_b):
                        interleaved_agents.append(agents_b[j].copy())
                
                new_dept.agents = interleaved_agents
                
                new_departments.append(new_dept)
            
            # Append remaining from A
            if count_a > min_count:
                for i in range(min_count, count_a):
                    new_departments.append(depts_a[i].copy())
                    
            # Append remaining from B
            if count_b > min_count:
                for i in range(min_count, count_b):
                    new_departments.append(depts_b[i].copy())

        child.departments = new_departments
        
        # Ensure at least 2 departments? 
        # The spec for Morphogenesis had this constraint. 
        # For Crossover, it doesn't explicitly state a minimum, but a valid CompanyGenome 
        # requires at least 1 department (schema validation). 
        # If the crossover results in 0 departments (unlikely if parents have depts), 
        # we might need to handle it. 
        # Given parents are valid, they have >=1 dept. 
        # If they have disjoint categories, we get sum of depts. 
        # If they share categories, we merge. 
        # It's possible to end up with 1 dept if both parents had 1 dept of same category.
        # Schema allows 1 dept. So we are fine.

        return child