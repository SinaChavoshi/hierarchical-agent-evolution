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
        
        # 2. Lineage updates
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(child.mutation_history) + [mutation_name]
        
        # Ensure code_overlays is an independent copy (already handled by deep copy, 
        # but explicit re-assignment ensures independence if copy() behavior changes)
        child.code_overlays = dict(parent.code_overlays)
        
        # 4. Protected pods enforcement
        # Identify protected departments
        protected_depts = [d for d in child.departments if d.dept_id in PROTECTED_DEPT_IDS]
        non_protected_depts = [d for d in child.departments if d.dept_id not in PROTECTED_DEPT_IDS]
        
        # Simulate mutation logic:
        # For this engine, we assume the mutation_name might imply adding/removing departments.
        # Since we don't have an LLM call here to decide specific structural changes dynamically 
        # without external context, we implement a deterministic structural morph that respects invariants.
        # 
        # Strategy:
        # - If mutation_name suggests "add" or "spawn", we might add a new department.
        # - If mutation_name suggests "prune" or "remove", we might remove a non-protected department.
        # - We must ensure at least 2 departments remain.
        
        # Heuristic for mutation type based on name
        mutation_lower = mutation_name.lower()
        
        # Determine if we should add or remove
        should_add = any(k in mutation_lower for k in ['add', 'spawn', 'create', 'new', 'expand'])
        should_remove = any(k in mutation_lower for k in ['prune', 'remove', 'delete', 'shrink', 'merge'])
        
        new_departments = list(protected_depts)
        
        if should_remove and len(non_protected_depts) > 0:
            # Keep at least 1 non-protected if possible to maintain variety, 
            # but strictly ensure total >= 2.
            # If we have many non-protected, remove one.
            if len(child.departments) > 2:
                # Remove the last non-protected department as a simple pruning strategy
                # In a real system, this would be driven by fitness or LLM.
                # Here we just ensure invariants hold.
                pass # We will rebuild the list below
            
            # Rebuild list: Keep all protected, keep some non-protected
            # Simple strategy: Keep all protected, keep first N non-protected such that total >= 2
            # If we want to prune, we drop some non-protected.
            # Let's drop the last non-protected department if we have more than 2 total.
            if len(child.departments) > 2:
                # Drop the last non-protected department
                if non_protected_depts:
                    non_protected_depts = non_protected_depts[:-1]
        
        new_departments.extend(non_protected_depts)
        
        if should_add:
            # Spawn a new technical department if not already present or if expanding
            # Check if we already have a department of a technical category
            existing_categories = {classify_department_role(d) for d in new_departments}
            
            # Try to add a missing technical category
            for cat in TECHNICAL_CATEGORIES:
                if cat not in existing_categories:
                    # Create a new department for this category
                    new_dept_id = f"dept_{cat}_{child_id}"
                    new_dept_name = f"{cat.replace('_', ' ').title()} Pod"
                    new_dept_mandate = f"Execute {cat.replace('_', ' ')} operations"
                    
                    # Create Manager
                    manager = AgentGenome(
                        role=f"{cat.replace('_', ' ').title()} Lead",
                        goal=f"Lead {cat.replace('_', ' ')} initiatives",
                        backstory=f"Expert in {cat.replace('_', ' ')}",
                        backstory_traits=["expert", "leader"],
                        temperature=0.7,
                        model_tier="executive",
                        tools_enabled=False # Managers usually don't write files directly in this schema context unless specified
                    )
                    
                    # Create Worker Agents
                    workers = []
                    for i in range(2):
                        worker = AgentGenome(
                            role=f"{cat.replace('_', ' ').title()} Specialist {i+1}",
                            goal=f"Perform {cat.replace('_', ' ')} tasks",
                            backstory=f"Specialized in {cat.replace('_', ' ')}",
                            backstory_traits=["specialist", "technical"],
                            temperature=0.7,
                            model_tier="worker",
                            tools_enabled=True # 3. Tool enablement for technical workers
                        )
                        workers.append(worker)
                    
                    new_dept = DepartmentGenome(
                        dept_id=new_dept_id,
                        name=new_dept_name,
                        mandate=new_dept_mandate,
                        manager=manager,
                        agents=workers
                    )
                    new_departments.append(new_dept)
                    break # Add only one new department per mutation
        
        # Ensure minimum 2 departments
        if len(new_departments) < 2:
            # If we pruned too much, we need to add back or ensure we didn't prune protected ones.
            # Since protected ones are always kept, if we have < 2, it means we had < 2 protected 
            # and pruned all non-protected.
            # We must add a dummy department or restore one.
            # Let's add a generic "custom_specialized" department if we are short.
            if len(new_departments) < 2:
                # Create a filler department
                filler_id = f"dept_filler_{child_id}"
                filler_name = "General Operations"
                filler_mandate = "General operational support"
                
                manager = AgentGenome(
                    role="Operations Lead",
                    goal="Support operations",
                    backstory="Generalist",
                    backstory_traits=["generalist"],
                    temperature=0.7,
                    model_tier="executive",
                    tools_enabled=False
                )
                worker = AgentGenome(
                    role="Operations Specialist",
                    goal="Support tasks",
                    backstory="Generalist",
                    backstory_traits=["generalist"],
                    temperature=0.7,
                    model_tier="worker",
                    tools_enabled=False
                )
                filler_dept = DepartmentGenome(
                    dept_id=filler_id,
                    name=filler_name,
                    mandate=filler_mandate,
                    manager=manager,
                    agents=[worker]
                )
                new_departments.append(filler_dept)

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
        # We create a new child genome. We need to construct it carefully.
        # Start with a copy of parent_a to get basic structure, then overwrite.
        child = parent_a.copy()
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        # Mutation history: typically crossover is recorded. 
        # The spec doesn't explicitly say to append to mutation_history for crossover, 
        # but it's good practice. However, strict adherence to spec:
        # Spec says: "Sets child.company_id..., child.generation..., child.parent_ids..."
        # It does NOT explicitly say to modify mutation_history for crossover, unlike morph.
        # But usually, history is preserved. Let's keep parent_a's history or merge?
        # Spec doesn't specify. I will preserve parent_a's history as a base, 
        # or perhaps merge unique entries. 
        # Given "Deep-copy isolation" and "Lineage", I'll assume mutation_history 
        # is inherited from parent_a (since we copied it) or merged. 
        # Let's merge unique entries to be safe and informative.
        merged_history = list(dict.fromkeys(parent_a.mutation_history + parent_b.mutation_history))
        child.mutation_history = merged_history

        # 3. Level 3 RSI Code Overlay Inheritance
        # parent_a takes precedence
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # 4. CEO Crossover
        # Combine and deduplicate backstory_traits from both parents, preserving order, capped at 6.
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
        
        # Temperature: rounded average
        avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0
        child.ceo.temperature = round(avg_temp, 2)
        
        # Other CEO fields: Inherit from parent_a (since we copied it) or mix?
        # Spec only specifies traits and temperature. We'll leave others as parent_a's.

        # 5. Department Alignment
        # Align departments by classify_department_role
        
        # Group departments by category
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
        
        all_categories = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())
        
        new_departments = []
        
        for cat in all_categories:
            depts_a = depts_a_by_cat.get(cat, [])
            depts_b = depts_b_by_cat.get(cat, [])
            
            # Recombine departments in this category
            # Strategy: 
            # - If both have departments, create a hybrid department.
            # - If only one has, copy it (deep copy).
            
            if depts_a and depts_b:
                # Take the first department from each as representatives for crossover
                # Or merge all? Spec says "Aligns departments... to recombine manager traits/temperatures and interleave specialist agents."
                # It implies creating a new department per category that combines the best of both.
                
                # Use the first dept from A and first from B as primary sources
                rep_a = depts_a[0]
                rep_b = depts_b[0]
                
                # Create new department ID
                new_dept_id = f"dept_{cat}_{child_id}"
                new_dept_name = f"{cat.replace('_', ' ').title()} Hybrid Pod"
                new_dept_mandate = f"Hybrid mandate for {cat.replace('_', ' ')}"
                
                # Manager Crossover
                # Traits: Combine and dedup, cap at 6
                mgr_traits_a = rep_a.manager.backstory_traits
                mgr_traits_b = rep_b.manager.backstory_traits
                mgr_combined = []
                mgr_seen = set()
                for t in mgr_traits_a:
                    if t not in mgr_seen:
                        mgr_combined.append(t)
                        mgr_seen.add(t)
                for t in mgr_traits_b:
                    if t not in mgr_seen:
                        mgr_combined.append(t)
                        mgr_seen.add(t)
                mgr_combined = mgr_combined[:6]
                
                # Temperature: Average
                mgr_temp = round((rep_a.manager.temperature + rep_b.manager.temperature) / 2.0, 2)
                
                # Model Tier: Keep executive
                new_manager = AgentGenome(
                    role=f"{cat.replace('_', ' ').title()} Hybrid Lead",
                    goal=f"Lead hybrid {cat.replace('_', ' ')} initiatives",
                    backstory=f"Hybrid expert in {cat.replace('_', ' ')}",
                    backstory_traits=mgr_combined,
                    temperature=mgr_temp,
                    model_tier="executive",
                    tools_enabled=False
                )
                
                # Specialist Agents: Interleave
                # Collect all agents from all departments in this category from both parents
                agents_a = []
                for d in depts_a:
                    agents_a.extend(d.agents)
                
                agents_b = []
                for d in depts_b:
                    agents_b.extend(d.agents)
                
                # Interleave: Take one from A, one from B, etc.
                interleaved_agents = []
                max_len = max(len(agents_a), len(agents_b))
                for i in range(max_len):
                    if i < len(agents_a):
                        # Deep copy agent to avoid mutation
                        agent_copy = agents_a[i].copy()
                        # Ensure tools_enabled is correct for technical categories
                        if cat in TECHNICAL_CATEGORIES:
                            agent_copy.tools_enabled = True
                        interleaved_agents.append(agent_copy)
                    if i < len(agents_b):
                        agent_copy = agents_b[i].copy()
                        if cat in TECHNICAL_CATEGORIES:
                            agent_copy.tools_enabled = True
                        interleaved_agents.append(agent_copy)
                
                # Limit number of agents to prevent explosion? 
                # Spec doesn't specify a cap, but "interleave" implies combining.
                # We'll keep all interleaved agents.
                
                new_dept = DepartmentGenome(
                    dept_id=new_dept_id,
                    name=new_dept_name,
                    mandate=new_dept_mandate,
                    manager=new_manager,
                    agents=interleaved_agents
                )
                new_departments.append(new_dept)
                
            elif depts_a:
                # Only A has this category. Copy all departments from A.
                for d in depts_a:
                    new_d = d.copy()
                    # Update ID to reflect child? 
                    # Spec doesn't explicitly say to rename dept_ids for crossover, 
                    # but keeping parent IDs might cause collisions if we merge lists.
                    # However, dept_id is unique within a company. 
                    # If we just copy, the ID remains. 
                    # To be safe and unique, we might rename, but spec doesn't require it.
                    # Let's keep the original ID to preserve identity, assuming no collision 
                    # because we are grouping by category and creating new depts only for mixed.
                    # Wait, if we just copy, we might have multiple depts with same category.
                    # The spec says "Aligns departments... to recombine". 
                    # If only one parent has it, we just inherit it.
                    new_departments.append(new_d)
                    
            elif depts_b:
                # Only B has this category. Copy all departments from B.
                for d in depts_b:
                    new_d = d.copy()
                    new_departments.append(new_d)
        
        child.departments = new_departments
        
        # Ensure at least 1 department (schema requires non-empty)
        if not child.departments:
            # Fallback: create a generic department
            generic_mgr = AgentGenome(
                role="General Lead",
                goal="General operations",
                backstory="Generalist",
                backstory_traits=["generalist"],
                temperature=0.7,
                model_tier="executive",
                tools_enabled=False
            )
            generic_worker = AgentGenome(
                role="General Specialist",
                goal="General tasks",
                backstory="Generalist",
                backstory_traits=["generalist"],
                temperature=0.7,
                model_tier="worker",
                tools_enabled=False
            )
            generic_dept = DepartmentGenome(
                dept_id=f"dept_generic_{child_id}",
                name="General Operations",
                mandate="General operational support",
                manager=generic_mgr,
                agents=[generic_worker]
            )
            child.departments.append(generic_dept)
            
        return child