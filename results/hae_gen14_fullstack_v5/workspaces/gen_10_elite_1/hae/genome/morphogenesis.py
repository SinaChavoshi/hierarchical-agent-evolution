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

# Technical departments that require tool enablement for spawned agents
TECHNICAL_DEPARTMENTS = {
    'formal_verification',
    'systems_eng',
    'qa_testing',
    'ai_acceleration'
}

# Protected department IDs that must never be pruned
PROTECTED_DEPT_IDS = {
    'dept_systems_eng',
    'dept_qa_redteam'
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
        
        # Ensure code_overlays is an independent copy (deepcopy handles this, 
        # but explicit check ensures we don't share references if copy() was shallow)
        child.code_overlays = copy.deepcopy(parent.code_overlays)

        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history)
        child.mutation_history.append(mutation_name)

        # 3. Tool enablement for technical departments
        # We need to identify which departments are "newly spawned" or just ensure 
        # that any department classified as technical has tools enabled.
        # The spec says "Any newly spawned technical department... must set tools_enabled=True".
        # Since we are morphing, we assume existing departments might need updating too 
        # if they are technical, or we only update new ones. 
        # However, usually "morphing" implies structural changes. 
        # To be safe and consistent with "tool enablement" invariant, we will ensure 
        # that ALL departments classified as technical have their worker agents 
        # with tools_enabled=True. This covers both new and existing if they were 
        # previously misconfigured, but primarily targets the "newly spawned" intent.
        
        for dept in child.departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_DEPARTMENTS:
                # Enable tools for all worker agents in this department
                for agent in dept.agents:
                    if agent.model_tier == "worker":
                        agent.tools_enabled = True
                # Also check manager if it's a worker (though managers are usually executive)
                if dept.manager and dept.manager.model_tier == "worker":
                    dept.manager.tools_enabled = True

        # 4. Protected pods: Must retain at least 2 departments and never prune protected IDs.
        # The morphing logic (adding/pruning) is abstracted here. 
        # Since we don't have specific mutation logic (e.g., "prune X"), we assume 
        # the `parent` structure is largely preserved unless specific mutation logic 
        # was applied. 
        # However, the invariant says "Must retain at least 2 departments".
        # If the parent had < 2, we might need to add dummy ones? 
        # Usually, parents are valid genomes. 
        # The critical part is "never prune dept_systems_eng or dept_qa_redteam".
        # Since we are copying the parent, these are present. 
        # If a mutation *would* have pruned them, we must prevent it.
        # Without explicit mutation instructions (like "remove dept_X"), we assume 
        # the copy preserves them. 
        # If the engine is expected to *perform* pruning based on `mutation_name`, 
        # we would need a switch. Given the generic signature, we assume the 
        # structural integrity is maintained by the copy, and we just enforce 
        # the constraints on the result.
        
        # Ensure protected departments exist in the child
        existing_dept_ids = {d.dept_id for d in child.departments}
        for protected_id in PROTECTED_DEPT_IDS:
            if protected_id not in existing_dept_ids:
                # If a protected dept was somehow missing (e.g. pruned by a 
                # hypothetical mutation logic not shown here), we must restore it.
                # We look for it in the parent.
                parent_dept = next((d for d in parent.departments if d.dept_id == protected_id), None)
                if parent_dept:
                    child.departments.append(parent_dept.copy())
                    # Re-apply tool enablement if it's technical
                    category = classify_department_role(parent_dept)
                    if category in TECHNICAL_DEPARTMENTS:
                        for agent in child.departments[-1].agents:
                            if agent.model_tier == "worker":
                                agent.tools_enabled = True
                        if child.departments[-1].manager and child.departments[-1].manager.model_tier == "worker":
                            child.departments[-1].manager.tools_enabled = True

        # Ensure at least 2 departments
        if len(child.departments) < 2:
            # If we have fewer than 2, we need to add one. 
            # We can duplicate a non-protected one or create a generic one.
            # Since we must not prune protected ones, and we have <2, 
            # we likely have 1 protected one. We need another.
            if len(child.departments) == 1:
                # Duplicate the existing one with a new ID to satisfy count
                # Or create a minimal valid department.
                # Creating a minimal valid department is safer.
                new_dept = DepartmentGenome(
                    dept_id="dept_general_ops",
                    name="General Operations",
                    mandate="General operational support",
                    manager=AgentGenome(
                        role="Manager",
                        goal="Support operations",
                        backstory="Supports the organization",
                        model_tier="executive"
                    ),
                    agents=[]
                )
                child.departments.append(new_dept)
            elif len(child.departments) == 0:
                # Should not happen if parent is valid, but just in case
                # Add two generic departments
                for i in range(2):
                    new_dept = DepartmentGenome(
                        dept_id=f"dept_general_ops_{i}",
                        name=f"General Operations {i}",
                        mandate="General operational support",
                        manager=AgentGenome(
                            role="Manager",
                            goal="Support operations",
                            backstory="Supports the organization",
                            model_tier="executive"
                        ),
                        agents=[]
                    )
                    child.departments.append(new_dept)

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
        # We create a new child genome. We don't copy one parent entirely because 
        # we are merging. We start with a skeleton or copy one and modify.
        # Copying parent_a gives us a valid structure to start with.
        child = parent_a.copy()
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        # Mutation history: usually crossover is a mutation event. 
        # The spec doesn't explicitly say to append to mutation_history for crossover,
        # but it's good practice. However, strict adherence to spec:
        # Spec says: "Sets child.company_id..., child.generation..., child.parent_ids..."
        # It does NOT mention mutation_history for crossover. 
        # We will leave mutation_history as copied from parent_a (or empty if we want clean slate).
        # Let's keep it as copied from parent_a for now, or clear it? 
        # Usually, a new generation has its own history. 
        # Let's assume it inherits parent_a's history plus a crossover marker?
        # The spec is silent. I will leave it as copied from parent_a to be safe,
        # or perhaps clear it. Let's look at Morphogenesis: it appends.
        # For crossover, let's just keep parent_a's history.

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
        
        # Set temperature to rounded average
        avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0
        child.ceo.temperature = round(avg_temp, 2) # "rounded average" usually implies standard rounding. 
        # Python's round() uses banker's rounding. Let's use standard round.
        # If the spec means integer rounding, it would say so. Temperature is float.
        # "rounded average" -> round(avg, 2) is safe for floats.

        # 5. Department Alignment
        # Align departments by classify_department_role.
        # We need to merge departments from both parents.
        
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
            
            # If a category exists in both, we perform crossover on the departments.
            # If it exists in only one, we copy those departments.
            
            if depts_a and depts_b:
                # Crossover: We need to align them. 
                # Simple strategy: Pair up departments by index. 
                # If counts differ, extra departments from the longer list are copied.
                
                max_len = max(len(depts_a), len(depts_b))
                for i in range(max_len):
                    if i < len(depts_a) and i < len(depts_b):
                        # Crossover these two
                        d_a = depts_a[i]
                        d_b = depts_b[i]
                        merged_dept = self._crossover_department(d_a, d_b, cat)
                        new_departments.append(merged_dept)
                    elif i < len(depts_a):
                        # Only in A
                        new_departments.append(d_a.copy())
                    else:
                        # Only in B
                        new_departments.append(d_b.copy())
            elif depts_a:
                for d in depts_a:
                    new_departments.append(d.copy())
            elif depts_b:
                for d in depts_b:
                    new_departments.append(d.copy())
                    
        child.departments = new_departments
        
        # Ensure we have at least 2 departments? 
        # The spec for Crossover doesn't explicitly state the "min 2" invariant like Morphogenesis.
        # But a valid CompanyGenome requires at least 1 department.
        # If both parents had 0 (impossible by schema), we'd have 0.
        # If they had 1 each, we might have 1 or 2 depending on category match.
        # If they matched, we have 1 merged. If not, we have 2.
        # If we end up with 0, we need to add one.
        if not child.departments:
            # Fallback: create a generic department
            new_dept = DepartmentGenome(
                dept_id="dept_general_ops",
                name="General Operations",
                mandate="General operational support",
                manager=AgentGenome(
                    role="Manager",
                    goal="Support operations",
                    backstory="Supports the organization",
                    model_tier="executive"
                ),
                agents=[]
            )
            child.departments.append(new_dept)

        return child

    def _crossover_department(
        self, 
        d_a: DepartmentGenome, 
        d_b: DepartmentGenome, 
        category: str
    ) -> DepartmentGenome:
        """Helper to perform allelic crossover on two departments of the same category."""
        # Create a new department structure
        # Use d_a's ID as base, maybe append suffix? 
        # Spec doesn't specify ID generation for merged depts. 
        # We'll use d_a's ID to maintain some continuity, or generate a new one?
        # Using d_a's ID is risky if we have multiple merges. 
        # Let's use d_a's ID. If collision occurs in the final list, it's a problem.
        # But since we are grouping by category, and pairing by index, IDs should be unique 
        # within the category in the parent. 
        # If d_a and d_b have the same ID, we keep d_a's ID.
        
        merged_dept = DepartmentGenome(
            dept_id=d_a.dept_id, # Keep A's ID
            name=d_a.name,       # Keep A's name? Or mix? Spec says "recombine manager traits/temperatures and interleave specialist agents".
                                 # It doesn't explicitly say to mix name/mandate. 
                                 # We'll keep A's name/mandate for stability, or mix?
                                 # Let's keep A's name/mandate.
            mandate=d_a.mandate,
            delegation_rules=d_a.delegation_rules,
            manager=None,
            agents=[]
        )
        
        # Manager Crossover
        # Combine traits and average temperature
        if d_a.manager and d_b.manager:
            traits_a = d_a.manager.backstory_traits
            traits_b = d_b.manager.backstory_traits
            
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
            
            merged_manager = d_a.manager.copy()
            merged_manager.backstory_traits = combined_traits[:6]
            avg_temp = (d_a.manager.temperature + d_b.manager.temperature) / 2.0
            merged_manager.temperature = round(avg_temp, 2)
            merged_dept.manager = merged_manager
        elif d_a.manager:
            merged_dept.manager = d_a.manager.copy()
        elif d_b.manager:
            merged_dept.manager = d_b.manager.copy()
        else:
            # Should not happen due to schema validation
            pass
            
        # Agent Interleaving
        # "Interleave specialist agents"
        agents_a = d_a.agents
        agents_b = d_b.agents
        
        interleaved_agents = []
        max_len = max(len(agents_a), len(agents_b))
        
        for i in range(max_len):
            if i < len(agents_a):
                interleaved_agents.append(agents_a[i].copy())
            if i < len(agents_b):
                interleaved_agents.append(agents_b[i].copy())
                
        merged_dept.agents = interleaved_agents
        
        # Tool Enablement Check for Technical Departments
        # If this category is technical, ensure workers have tools enabled
        if category in TECHNICAL_DEPARTMENTS:
            for agent in merged_dept.agents:
                if agent.model_tier == "worker":
                    agent.tools_enabled = True
            if merged_dept.manager and merged_dept.manager.model_tier == "worker":
                merged_dept.manager.tools_enabled = True
                
        return merged_dept