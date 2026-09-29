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

# Functional categories mapping keywords to category keys
FUNCTIONAL_CATEGORIES: Dict[str, List[str]] = {
    'systems_eng': ['engineering', 'systems', 'architecture', 'devops', 'infrastructure'],
    'qa_testing': ['qa', 'quality', 'redteam', 'verification', 'testing', 'security'],
    'product_ux': ['product', 'ux', 'design', 'specification', 'frontend'],
    'market_strategy': ['strategy', 'market', 'executive', 'analysis', 'growth'],
    'finance_ops': ['finance', 'operations', 'cost', 'budget', 'compliance'],
    'ai_acceleration': ['acceleration', 'hardware', 'kernels', 'tpu', 'gpu', 'compiler'],
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec']
}

# Technical categories that require tool enablement for spawned agents
TECHNICAL_CATEGORIES = {
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
    text = f"{dept.dept_id} {dept.name} {dept.mandate}".lower()
    
    for category, keywords in FUNCTIONAL_CATEGORIES.items():
        for keyword in keywords:
            if keyword in text:
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
        dept_categories = {d.dept_id: classify_department_role(d) for d in current_depts}
        
        # Determine which categories are present
        present_categories = set(dept_categories.values())
        
        # Strategy: 
        # - Ensure protected pods exist. If missing, spawn them.
        # - Ensure at least 2 departments. If fewer, spawn generic ones.
        # - For newly spawned technical departments, enable tools.
        
        # Helper to create a default department for a category
        def _spawn_department(category: str, dept_id: Optional[str] = None) -> DepartmentGenome:
            if dept_id is None:
                dept_id = f"dept_{category}"
            
            # Create a manager agent
            manager = AgentGenome(
                role=f"{category.replace('_', ' ').title()} Manager",
                goal=f"Lead {category.replace('_', ' ')} operations",
                backstory="Experienced leader in this domain.",
                backstory_traits=["leadership", "strategy"],
                temperature=0.7,
                model_tier="executive",
                tools_enabled=False
            )
            
            # Create worker agents
            workers = []
            # Spawn 1-2 workers depending on category
            num_workers = 2 if category in TECHNICAL_CATEGORIES else 1
            
            for i in range(num_workers):
                worker = AgentGenome(
                    role=f"{category.replace('_', ' ').title()} Specialist {i+1}",
                    goal=f"Execute {category.replace('_', ' ')} tasks",
                    backstory="Skilled specialist.",
                    backstory_traits=["technical", "execution"],
                    temperature=0.7,
                    model_tier="worker",
                    tools_enabled=False # Default, will be updated if technical
                )
                workers.append(worker)
            
            dept = DepartmentGenome(
                dept_id=dept_id,
                name=f"{category.replace('_', ' ').title()} Pod",
                mandate=f"Handle {category.replace('_', ' ')} functions",
                manager=manager,
                agents=workers
            )
            return dept

        # Ensure Protected Pods Exist
        # If 'dept_systems_eng' is missing, spawn it
        if 'dept_systems_eng' not in child.dept_ids:
            new_dept = _spawn_department('systems_eng', 'dept_systems_eng')
            child.departments.append(new_dept)
            
        # If 'dept_qa_redteam' is missing, spawn it
        if 'dept_qa_redteam' not in child.dept_ids:
            new_dept = _spawn_department('qa_testing', 'dept_qa_redteam')
            child.departments.append(new_dept)

        # Ensure Minimum 2 Departments
        # If we still have fewer than 2 (unlikely given protected pods logic above, 
        # but if parent had 0 and we only added 1 protected one?), add another.
        # Note: The logic above adds both protected ones if missing. 
        # If parent had 1 non-protected, and we added 2 protected, we have 3.
        # If parent had 0, we have 2.
        # If parent had 1 protected, we have 1 or 2.
        
        if len(child.departments) < 2:
            # Spawn a generic 'market_strategy' or 'product_ux' pod
            new_dept = _spawn_department('market_strategy')
            child.departments.append(new_dept)

        # 3. Tool Enablement for Newly Spawned Technical Departments
        # We need to identify which departments were "newly spawned" in this morph.
        # Since we don't track "new" vs "old" explicitly in the object, we can infer:
        # Any department that is in a TECHNICAL_CATEGORY and was not in the parent's 
        # department IDs (or was added by us) should have tools enabled.
        
        parent_dept_ids = set(parent.dept_ids)
        
        for dept in child.departments:
            if dept.dept_id not in parent_dept_ids:
                # This is a newly spawned department
                category = classify_department_role(dept)
                if category in TECHNICAL_CATEGORIES:
                    # Enable tools for all worker agents in this department
                    for agent in dept.agents:
                        if agent.model_tier == "worker":
                            agent.tools_enabled = True
                    # Managers are executive, usually don't need write tools in this context,
                    # but spec says "worker AgentGenome instances".
            
            # Also, if an existing department was morphed/reshaped, we might need to 
            # ensure tools are enabled if it's technical. 
            # The spec says "Any newly spawned technical department". 
            # So we only touch the new ones.

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
        # We create a new child genome. We will construct it from scratch or copy one parent 
        # and modify. Copying parent_a is a good base.
        child = parent_a.copy()
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        child.mutation_history = list(parent_a.mutation_history)
        # Optionally add label to mutation history? Spec doesn't explicitly say, 
        # but "Recombinant" is a label. Let's append it to track the event.
        child.mutation_history.append(f"Crossover: {label}")

        # 3. Level 3 RSI Code Overlay Inheritance
        # parent_a takes precedence
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # 4. CEO Crossover
        # Combine and deduplicate backstory traits
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
        
        # Cap at 6
        child.ceo.backstory_traits = combined_traits[:6]
        
        # Average temperature
        avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0
        child.ceo.temperature = round(avg_temp, 2)
        
        # Keep other CEO attributes from parent_a (base copy)

        # 5. Department Alignment & Crossover
        
        # Group departments by category for both parents
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
        
        # Get all unique categories
        all_categories = set(groups_a.keys()) | set(groups_b.keys())
        
        new_departments = []
        
        for cat in all_categories:
            depts_a = groups_a.get(cat, [])
            depts_b = groups_b.get(cat, [])
            
            # Strategy for this category:
            # If both have departments, we merge/crossover them.
            # If only one has, we take that one (deep copied).
            
            if not depts_a and not depts_b:
                continue
                
            if not depts_a:
                # Only B has it. Take B's first dept (deep copied)
                # We already have a copy of A in child, so we need to copy B's dept
                new_dept = depts_b[0].copy()
                new_departments.append(new_dept)
                continue
                
            if not depts_b:
                # Only A has it. Take A's first dept (already in child copy, but we are rebuilding list)
                # We can just take the one from child's current departments if it matches?
                # Easier: Just copy from parent_a
                new_dept = depts_a[0].copy()
                new_departments.append(new_dept)
                continue
            
            # Both have departments. Perform Crossover.
            # Take the first department from each as the primary representatives for this category.
            # If there are multiple, we could merge them all, but spec implies aligning by role.
            # Let's assume we merge the first one from A and first one from B into one department.
            
            dept_a = depts_a[0]
            dept_b = depts_b[0]
            
            # Create a new department for this category
            # ID: Prefer A's ID if available, else B's.
            new_dept_id = dept_a.dept_id
            
            # Name/Mandate: Prefer A's
            new_name = dept_a.name
            new_mandate = dept_a.mandate
            
            # Manager Crossover
            # Combine traits and average temperature
            mgr_a = dept_a.manager
            mgr_b = dept_b.manager
            
            mgr_traits_a = mgr_a.backstory_traits
            mgr_traits_b = mgr_b.backstory_traits
            
            combined_mgr_traits = []
            seen_mgr = set()
            for t in mgr_traits_a:
                if t not in seen_mgr:
                    combined_mgr_traits.append(t)
                    seen_mgr.add(t)
            for t in mgr_traits_b:
                if t not in seen_mgr:
                    combined_mgr_traits.append(t)
                    seen_mgr.add(t)
            
            new_mgr = AgentGenome(
                role=mgr_a.role, # Keep A's role
                goal=mgr_a.goal,
                backstory=mgr_a.backstory,
                backstory_traits=combined_mgr_traits[:6],
                temperature=round((mgr_a.temperature + mgr_b.temperature) / 2.0, 2),
                model_tier=mgr_a.model_tier,
                tools_enabled=mgr_a.tools_enabled,
                system_instructions=mgr_a.system_instructions
            )
            
            # Specialist Agents Interleaving
            # Combine agents from A and B.
            # Interleave: A1, B1, A2, B2...
            agents_a = dept_a.agents
            agents_b = dept_b.agents
            
            new_agents = []
            max_len = max(len(agents_a), len(agents_b))
            
            for i in range(max_len):
                if i < len(agents_a):
                    # Copy agent from A
                    new_agents.append(agents_a[i].copy())
                if i < len(agents_b):
                    # Copy agent from B
                    new_agents.append(agents_b[i].copy())
            
            # Optional: Deduplicate agents? Spec says "interleave", doesn't explicitly say dedupe.
            # But if they are identical, we might want to dedupe. 
            # Let's stick to strict interleaving as per "interleave specialist agents".
            
            new_dept = DepartmentGenome(
                dept_id=new_dept_id,
                name=new_name,
                mandate=new_mandate,
                manager=new_mgr,
                agents=new_agents,
                delegation_rules=dept_a.delegation_rules
            )
            
            new_departments.append(new_dept)

        child.departments = new_departments
        
        # Ensure we have at least 1 department (should be guaranteed by logic above unless both parents empty, which is invalid)
        if not child.departments:
            # Fallback: Should not happen if parents are valid
            pass

        return child