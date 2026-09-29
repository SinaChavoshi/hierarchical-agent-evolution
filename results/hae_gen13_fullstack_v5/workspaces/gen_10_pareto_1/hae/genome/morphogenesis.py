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
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec'],
}

# Technical categories that require tool enablement
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
        
        # Ensure code_overlays is an independent copy (deepcopy already handles this, 
        # but explicit check ensures we don't share references if copy() was shallow in some edge case)
        child.code_overlays = copy.deepcopy(parent.code_overlays)

        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history)
        child.mutation_history.append(mutation_name)

        # 4. Protected pods: Ensure we retain at least 2 departments and protected ones
        # Identify protected departments
        protected_depts = [
            d for d in child.departments 
            if d.dept_id in PROTECTED_DEPT_IDS
        ]
        
        # If we have fewer than 2 departments, we need to add some.
        # If we are missing protected departments, we must add them.
        
        # Strategy: 
        # - If mutation implies pruning, we must ensure protected ones remain.
        # - If mutation implies adding, we add new ones.
        # - Since the spec doesn't define specific mutation logic beyond invariants,
        #   we assume the mutation_name might hint at action, but primarily we must 
        #   enforce the invariants on the resulting structure.
        
        # Let's assume a generic morphing strategy:
        # 1. Keep all existing departments unless explicitly pruned by logic not defined here.
        #    However, the invariant says "Must retain at least 2 departments".
        #    If the parent has < 2, we must add.
        #    If the parent has >= 2, we keep them, but ensure protected ones are present.
        
        # Check if protected departments are present
        existing_protected_ids = {d.dept_id for d in child.departments if d.dept_id in PROTECTED_DEPT_IDS}
        missing_protected = PROTECTED_DEPT_IDS - existing_protected_ids
        
        # If we are missing protected departments, we must add them.
        for missing_id in missing_protected:
            new_dept = self._create_default_department(missing_id)
            child.departments.append(new_dept)
            
        # Ensure at least 2 departments
        if len(child.departments) < 2:
            # Add a generic department if we still don't have enough
            # Try to add a non-protected one first if possible, or just another default
            generic_id = f"dept_generic_{len(child.departments)}"
            new_dept = self._create_default_department(generic_id)
            child.departments.append(new_dept)

        # 3. Tool enablement for newly spawned technical departments
        # We need to identify which departments are "newly spawned" or if we should 
        # enforce tool enablement on ALL technical departments in the child?
        # The spec says "Any newly spawned technical department... must set tools_enabled=True".
        # Since we don't track "newly spawned" explicitly in the state, we interpret this as:
        # Any department in the child that falls into a technical category AND was not in the parent
        # OR simply any technical department in the child should have tools enabled if it's considered "spawned" by this morph.
        # Given the ambiguity, a safe interpretation is: 
        # If a department is technical, ensure its worker agents have tools_enabled=True.
        # This is a conservative approach that satisfies the requirement for "newly spawned" 
        # (since they are in the child) and doesn't violate existing ones (enabling tools is usually safe).
        
        for dept in child.departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_CATEGORIES:
                # Enable tools for worker agents in this department
                for agent in dept.agents:
                    if agent.model_tier == "worker":
                        agent.tools_enabled = True
                # Also enable for manager if it's a worker? Usually managers are executive.
                # Spec says "worker AgentGenome instances".
                if dept.manager and dept.manager.model_tier == "worker":
                    dept.manager.tools_enabled = True

        return child

    def _create_default_department(self, dept_id: str) -> DepartmentGenome:
        """Creates a default department with a manager and one worker agent."""
        # Determine category based on ID to set appropriate defaults
        category = classify_department_role(DepartmentGenome(dept_id=dept_id, name="", mandate=""))
        
        manager = AgentGenome(
            role=f"Manager of {dept_id}",
            goal="Lead the department",
            backstory="Experienced leader",
            backstory_traits=["leadership", "strategy"],
            temperature=0.7,
            model_tier="executive",
            tools_enabled=False
        )
        
        worker = AgentGenome(
            role=f"Specialist in {dept_id}",
            goal="Execute tasks",
            backstory="Skilled specialist",
            backstory_traits=["technical", "execution"],
            temperature=0.7,
            model_tier="worker",
            tools_enabled=True  # Default to True for new spawns
        )
        
        return DepartmentGenome(
            dept_id=dept_id,
            name=f"Department {dept_id}",
            mandate=f"Operate {dept_id}",
            manager=manager,
            agents=[worker]
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
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        # Mutation history: typically empty or inherited? Spec doesn't specify, 
        # but usually crossover is a new event. We'll keep parent_a's history or clear it.
        # Let's keep parent_a's history as a base, or maybe combine? 
        # Spec doesn't say. We'll leave it as copied from parent_a for now.
        
        # 3. Level 3 RSI Code Overlay Inheritance
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
        child.ceo.temperature = round(avg_temp, 2) # Rounded average
        
        # 5. Department Alignment
        # Group departments by category
        depts_a_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for d in parent_a.departments:
            cat = classify_department_role(d)
            depts_a_by_cat.setdefault(cat, []).append(d)
            
        depts_b_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for d in parent_b.departments:
            cat = classify_department_role(d)
            depts_b_by_cat.setdefault(cat, []).append(d)
        
        # All categories present in either parent
        all_cats = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())
        
        new_departments = []
        
        for cat in all_cats:
            depts_a = depts_a_by_cat.get(cat, [])
            depts_b = depts_b_by_cat.get(cat, [])
            
            # Recombine departments in this category
            # Strategy: 
            # - If both have departments, create a hybrid department for each pair? 
            #   Or just take one from each? 
            # - Spec says "Aligns departments... to recombine manager traits/temperatures and interleave specialist agents."
            # - This implies creating new departments that are hybrids.
            
            # Let's assume we create one hybrid department per category if possible,
            # or multiple if there are multiple in one parent.
            
            # Simple strategy: 
            # If both have at least one, create one hybrid from the first of each.
            # If only one has, copy it (deep copy).
            
            if depts_a and depts_b:
                # Create hybrid from first of each
                hybrid_dept = self._recombine_departments(depts_a[0], depts_b[0], cat)
                new_departments.append(hybrid_dept)
                
                # What about the rest? 
                # If there are more, we might just include them as-is or ignore.
                # To be safe and preserve structure, let's include remaining from A and B as-is?
                # Or maybe just stick to one per category to keep it clean?
                # The spec doesn't forbid multiple departments per category.
                # Let's include the rest from A and B as-is (deep copied).
                for d in depts_a[1:]:
                    new_departments.append(d.copy())
                for d in depts_b[1:]:
                    new_departments.append(d.copy())
                    
            elif depts_a:
                # Only A has this category
                for d in depts_a:
                    new_departments.append(d.copy())
            elif depts_b:
                # Only B has this category
                for d in depts_b:
                    new_departments.append(d.copy())
                    
        child.departments = new_departments
        
        # Ensure we have at least 2 departments? 
        # The spec for Morphogenesis had this invariant, but not explicitly for Crossover.
        # However, CompanyGenome validation requires at least 1 department.
        # If we end up with 0, we should add one.
        if not child.departments:
            # Add a default department
            child.departments.append(self._create_default_department("dept_default"))
            
        return child

    def _recombine_departments(
        self,
        dept_a: DepartmentGenome,
        dept_b: DepartmentGenome,
        category: str
    ) -> DepartmentGenome:
        """Creates a hybrid department from two parents in the same category."""
        # Manager Crossover
        # Combine traits, average temperature
        traits_a = dept_a.manager.backstory_traits
        traits_b = dept_b.manager.backstory_traits
        
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
        
        new_manager = dept_a.manager.copy()
        new_manager.backstory_traits = combined_traits[:6]
        new_manager.temperature = round((dept_a.manager.temperature + dept_b.manager.temperature) / 2.0, 2)
        
        # Agent Interleaving
        # Combine agents from both, interleaving them
        agents_a = dept_a.agents
        agents_b = dept_b.agents
        
        new_agents = []
        max_len = max(len(agents_a), len(agents_b))
        for i in range(max_len):
            if i < len(agents_a):
                new_agents.append(agents_a[i].copy())
            if i < len(agents_b):
                new_agents.append(agents_b[i].copy())
                
        # Ensure tools_enabled for technical categories
        if category in TECHNICAL_CATEGORIES:
            for agent in new_agents:
                if agent.model_tier == "worker":
                    agent.tools_enabled = True
            if new_manager.model_tier == "worker":
                new_manager.tools_enabled = True

        # Create new department
        # Use ID from A, or generate a new one? 
        # Using A's ID might cause collisions if we have multiple hybrids.
        # Let's use A's ID for the first hybrid, and maybe suffix for others?
        # For simplicity, we'll use A's ID. If collisions occur, they are handled by the list structure.
        new_dept = DepartmentGenome(
            dept_id=dept_a.dept_id,
            name=f"{dept_a.name} (Hybrid)",
            mandate=f"{dept_a.mandate} | {dept_b.mandate}",
            manager=new_manager,
            agents=new_agents
        )
        
        return new_dept

    def _create_default_department(self, dept_id: str) -> DepartmentGenome:
        """Creates a default department with a manager and one worker agent."""
        manager = AgentGenome(
            role=f"Manager of {dept_id}",
            goal="Lead the department",
            backstory="Experienced leader",
            backstory_traits=["leadership", "strategy"],
            temperature=0.7,
            model_tier="executive",
            tools_enabled=False
        )
        
        worker = AgentGenome(
            role=f"Specialist in {dept_id}",
            goal="Execute tasks",
            backstory="Skilled specialist",
            backstory_traits=["technical", "execution"],
            temperature=0.7,
            model_tier="worker",
            tools_enabled=True
        )
        
        return DepartmentGenome(
            dept_id=dept_id,
            name=f"Department {dept_id}",
            mandate=f"Operate {dept_id}",
            manager=manager,
            agents=[worker]
        )