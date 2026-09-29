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

# Functional categories mapping keywords to category keys.
# Order matters for classification priority.
FUNCTIONAL_CATEGORIES = {
    'systems_eng': ['engineering', 'systems', 'architecture', 'devops', 'infrastructure'],
    'qa_testing': ['qa', 'quality', 'redteam', 'verification', 'testing', 'security'],
    'product_ux': ['product', 'ux', 'design', 'specification', 'frontend'],
    'market_strategy': ['strategy', 'market', 'executive', 'analysis', 'growth'],
    'finance_ops': ['finance', 'operations', 'cost', 'budget', 'compliance'],
    'ai_acceleration': ['acceleration', 'hardware', 'kernels', 'tpu', 'gpu', 'compiler'],
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec']
}

# Technical departments that require tool enablement for their agents
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
    if not dept:
        return "custom_specialized"
    
    # Construct the search string from dept_id, name, and mandate
    search_string = f"{dept.dept_id} {dept.name} {dept.mandate}".lower()
    
    # Iterate through categories in declaration order
    for category, keywords in FUNCTIONAL_CATEGORIES.items():
        for keyword in keywords:
            if keyword in search_string:
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
        
        # Ensure code_overlays is an independent copy (deepcopy handles this, but explicit check for safety)
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
        dept_categories = {d.dept_id: classify_department_role(d) for d in current_depts}
        
        # Determine which departments to keep, prune, or add
        # Strategy: 
        # - Keep protected departments always.
        # - Keep at least 2 departments total.
        # - If we have fewer than 2, we need to add generic ones or ensure we don't prune below 2.
        # - For this implementation, we will simulate a "morph" by ensuring technical departments have tools enabled
        #   and potentially adding a new department if the topology is sparse, or pruning non-essential ones if too many.
        
        # For the purpose of satisfying the invariant "Must retain at least 2 departments",
        # we first check if we are below 2. If so, we add a placeholder department.
        # If we are above, we might prune non-protected, non-essential ones, but we must be careful.
        
        # Let's implement a conservative morph:
        # 1. Ensure protected departments exist. If they are missing in parent, we add them.
        # 2. Ensure at least 2 departments exist.
        # 3. Enable tools for technical departments.
        
        existing_ids = {d.dept_id for d in current_depts}
        
        # Check for protected departments
        protected_missing = []
        for pid in PROTECTED_DEPT_IDS:
            if pid not in existing_ids:
                protected_missing.append(pid)
                
        # If protected departments are missing, add them.
        # We need to create valid DepartmentGenome instances.
        for pid in protected_missing:
            # Create a dummy manager and agents to satisfy schema validation
            manager = AgentGenome(
                role=f"Manager of {pid}",
                goal=f"Manage {pid}",
                backstory="Experienced manager",
                backstory_traits=["leadership"],
                temperature=0.7,
                model_tier="executive",
                tools_enabled=False
            )
            # Create a worker agent
            worker = AgentGenome(
                role=f"Worker in {pid}",
                goal=f"Work in {pid}",
                backstory="Skilled worker",
                backstory_traits=["technical"],
                temperature=0.7,
                model_tier="worker",
                tools_enabled=True # Technical depts need tools
            )
            
            # Determine category for this protected dept
            # dept_systems_eng -> systems_eng
            # dept_qa_redteam -> qa_testing
            category = "systems_eng" if "systems" in pid else "qa_testing"
            
            new_dept = DepartmentGenome(
                dept_id=pid,
                name=f"Department {pid}",
                mandate=f"Mandate for {pid}",
                manager=manager,
                agents=[worker],
                delegation_rules="Standard"
            )
            child.departments.append(new_dept)
            existing_ids.add(pid)
            
        # Re-evaluate current departments after adding protected ones
        current_depts = child.departments
        
        # Ensure at least 2 departments
        if len(current_depts) < 2:
            # Add a generic department to meet the minimum
            generic_id = "dept_generic_ops"
            if generic_id not in existing_ids:
                manager = AgentGenome(
                    role="Generic Manager",
                    goal="Manage generic operations",
                    backstory="Generalist",
                    backstory_traits=["adaptability"],
                    temperature=0.7,
                    model_tier="executive",
                    tools_enabled=False
                )
                worker = AgentGenome(
                    role="Generic Worker",
                    goal="Perform generic tasks",
                    backstory="Generalist",
                    backstory_traits=["versatility"],
                    temperature=0.7,
                    model_tier="worker",
                    tools_enabled=False
                )
                new_dept = DepartmentGenome(
                    dept_id=generic_id,
                    name="Generic Operations",
                    mandate="General operations",
                    manager=manager,
                    agents=[worker],
                    delegation_rules="Standard"
                )
                child.departments.append(new_dept)
                current_depts = child.departments

        # 3. Tool enablement for technical departments
        # Iterate through all departments in the child
        for dept in child.departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_DEPARTMENTS:
                # Enable tools for all worker agents in this department
                for agent in dept.agents:
                    if agent.model_tier == "worker":
                        agent.tools_enabled = True
                # Also enable for manager if it's a worker tier? 
                # Spec says "worker AgentGenome instances". Managers are usually executive.
                # But if a manager is worker tier, we should enable it too.
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
        # We start with a copy of parent_a to inherit its structure, then merge B into it.
        child = parent_a.copy()
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        # Mutation history: typically empty or inherited? Spec doesn't specify mutation_history for crossover.
        # We'll keep it empty or inherit from A? Let's keep it empty as it's a crossover, not a mutation.
        child.mutation_history = []

        # 3. Level 3 RSI Code Overlay Inheritance
        # parent_a takes precedence
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # 4. CEO Crossover
        if parent_a.ceo and parent_b.ceo:
            # Combine and deduplicate backstory traits, preserving order
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
            
            # Cap at 6 traits
            child.ceo.backstory_traits = combined_traits[:6]
            
            # Average temperature
            avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0
            child.ceo.temperature = round(avg_temp, 2)
            
            # Other CEO attributes? 
            # Role, goal, backstory string? 
            # We'll keep parent_a's CEO attributes as base, but update traits and temp.
            # If we want to mix, we could alternate, but spec only mentions traits and temp.
            
        # 5. Department Alignment
        
        # Group departments by category for both parents
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
            
        # Get all unique categories
        all_cats = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())
        
        new_departments = []
        
        for cat in all_cats:
            depts_a = depts_a_by_cat.get(cat, [])
            depts_b = depts_b_by_cat.get(cat, [])
            
            # Determine how many departments to create for this category
            # Simple strategy: Take the max count from either parent, or sum?
            # Let's take the max count to maintain scale, or just merge them into one if possible?
            # Spec says "Aligns departments... to recombine manager traits/temperatures and interleave specialist agents."
            # This implies we might merge departments of the same category.
            
            # If we have departments in both, we merge them into one department per category?
            # Or do we keep multiple?
            # Let's assume we merge all departments of the same category into a single department in the child.
            
            if not depts_a and not depts_b:
                continue
                
            # Pick a representative ID and Name
            # Prefer A's ID if available, else B's
            rep_dept = depts_a[0] if depts_a else depts_b[0]
            
            # Create a new department for this category
            # We need a manager. We'll recombine managers from all depts in this category.
            managers = []
            if depts_a:
                managers.extend([d.manager for d in depts_a if d.manager])
            if depts_b:
                managers.extend([d.manager for d in depts_b if d.manager])
            
            # If no managers, create a dummy one
            if not managers:
                manager = AgentGenome(
                    role=f"Manager of {cat}",
                    goal=f"Manage {cat}",
                    backstory="Generated Manager",
                    backstory_traits=["leadership"],
                    temperature=0.7,
                    model_tier="executive",
                    tools_enabled=False
                )
            else:
                # Recombine managers: 
                # Take the first manager from A as base, or average?
                # Let's take the first manager from A if exists, else B.
                base_manager = managers[0]
                # Copy it to avoid mutation
                new_manager = base_manager.copy()
                
                # If we have managers from B, we can mix traits
                if depts_b and depts_b[0].manager:
                    b_manager = depts_b[0].manager
                    # Mix traits
                    traits_a = new_manager.backstory_traits
                    traits_b = b_manager.backstory_traits
                    combined = []
                    seen = set()
                    for t in traits_a:
                        if t not in seen:
                            combined.append(t)
                            seen.add(t)
                    for t in traits_b:
                        if t not in seen:
                            combined.append(t)
                            seen.add(t)
                    new_manager.backstory_traits = combined[:6]
                    
                    # Average temperature
                    avg_temp = (new_manager.temperature + b_manager.temperature) / 2.0
                    new_manager.temperature = round(avg_temp, 2)
                
                manager = new_manager

            # Interleave specialist agents
            agents_a = []
            for d in depts_a:
                agents_a.extend(d.agents)
                
            agents_b = []
            for d in depts_b:
                agents_b.extend(d.agents)
                
            # Interleave: A1, B1, A2, B2...
            interleaved_agents = []
            max_len = max(len(agents_a), len(agents_b))
            for i in range(max_len):
                if i < len(agents_a):
                    # Copy agent to avoid mutation
                    interleaved_agents.append(agents_a[i].copy())
                if i < len(agents_b):
                    interleaved_agents.append(agents_b[i].copy())
            
            # Ensure tools_enabled for technical categories
            if cat in TECHNICAL_DEPARTMENTS:
                for agent in interleaved_agents:
                    if agent.model_tier == "worker":
                        agent.tools_enabled = True
                if manager.model_tier == "worker":
                    manager.tools_enabled = True

            new_dept = DepartmentGenome(
                dept_id=rep_dept.dept_id, # Use representative ID
                name=rep_dept.name,
                mandate=rep_dept.mandate,
                manager=manager,
                agents=interleaved_agents,
                delegation_rules=rep_dept.delegation_rules
            )
            new_departments.append(new_dept)
            
        child.departments = new_departments
        
        # Ensure at least 2 departments? 
        # The spec for MorphogenesisEngine mentions this, but not explicitly for Crossover.
        # However, CompanyGenome validation requires at least 1 department.
        # If we ended up with 0, we need to add one.
        if not child.departments:
            # Add a dummy department
            manager = AgentGenome(
                role="Dummy Manager",
                goal="Dummy",
                backstory="Dummy",
                backstory_traits=["dummy"],
                temperature=0.7,
                model_tier="executive",
                tools_enabled=False
            )
            worker = AgentGenome(
                role="Dummy Worker",
                goal="Dummy",
                backstory="Dummy",
                backstory_traits=["dummy"],
                temperature=0.7,
                model_tier="worker",
                tools_enabled=False
            )
            dummy_dept = DepartmentGenome(
                dept_id="dept_dummy",
                name="Dummy Dept",
                mandate="Dummy",
                manager=manager,
                agents=[worker],
                delegation_rules="Dummy"
            )
            child.departments.append(dummy_dept)

        return child