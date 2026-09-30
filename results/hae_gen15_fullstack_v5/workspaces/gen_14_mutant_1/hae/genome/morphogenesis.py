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
        # We need to identify which departments are protected
        protected_depts = []
        other_depts = []
        
        for dept in child.departments:
            if dept.dept_id in PROTECTED_DEPT_IDS:
                protected_depts.append(dept)
            else:
                other_depts.append(dept)
        
        # If we have fewer than 2 departments total, we need to ensure we have at least 2.
        # The spec says "Must retain at least 2 departments". 
        # If the parent had < 2, we might need to spawn one? 
        # However, CompanyGenome validation requires at least 1 department.
        # The invariant says "Must retain at least 2 departments".
        # If parent has 1, we must add one. If parent has 0, it's invalid.
        
        # Let's assume the mutation logic might have pruned things. 
        # We enforce the minimum count here.
        
        current_depts = protected_depts + other_depts
        
        if len(current_depts) < 2:
            # Need to spawn a new department to meet the minimum of 2
            # We'll spawn a generic technical department if possible, or a custom one.
            # Let's spawn a 'systems_eng' department if it's not already there, 
            # otherwise a 'qa_testing' one, otherwise a generic one.
            
            existing_categories = {classify_department_role(d) for d in current_depts}
            
            new_dept = None
            if 'systems_eng' not in existing_categories:
                new_dept = self._spawn_department('systems_eng', child_id)
            elif 'qa_testing' not in existing_categories:
                new_dept = self._spawn_department('qa_testing', child_id)
            else:
                new_dept = self._spawn_department('custom_specialized', child_id)
            
            if new_dept:
                current_depts.append(new_dept)
        
        # Update child departments
        child.departments = current_depts

        # 3. Tool enablement for newly spawned technical departments
        # We need to identify which departments are "newly spawned".
        # Since we deep-copied, we can't easily tell which are new unless we track them.
        # However, the invariant says "Any newly spawned technical department... must set tools_enabled=True".
        # In a morph, we might have added departments. 
        # Let's re-evaluate all departments. If a department is technical, should we enable tools?
        # The spec says "newly spawned". 
        # If we spawned a department in the step above, it's new.
        # If we didn't spawn any, and just kept existing ones, we don't change them.
        
        # To be safe and robust, let's assume any department that was added in this step 
        # (i.e., not in the original parent's dept_ids) is "newly spawned".
        
        parent_dept_ids = {d.dept_id for d in parent.departments}
        
        for dept in child.departments:
            if dept.dept_id not in parent_dept_ids:
                # This is a newly spawned department
                category = classify_department_role(dept)
                if category in TECHNICAL_CATEGORIES:
                    # Set tools_enabled=True on worker agents
                    for agent in dept.agents:
                        if agent.model_tier == 'worker':
                            agent.tools_enabled = True
                    # Also check manager? Spec says "worker AgentGenome instances".
                    # Managers are usually executive, but if a manager is a worker, should we enable?
                    # Spec says "worker ... instances". So only workers.
                    if dept.manager and dept.manager.model_tier == 'worker':
                        dept.manager.tools_enabled = True

        return child

    def _spawn_department(self, category: str, parent_id: str) -> Optional[DepartmentGenome]:
        """Helper to spawn a new department based on category."""
        # Generate a unique ID
        import uuid
        dept_id = f"dept_{category}_{uuid.uuid4().hex[:8]}"
        
        # Create a manager
        manager = AgentGenome(
            role=f"{category}_manager",
            goal=f"Manage {category} operations",
            backstory=f"Experienced manager in {category}",
            backstory_traits=["leadership", "technical"],
            temperature=0.7,
            model_tier="executive",
            tools_enabled=False
        )
        
        # Create some worker agents
        agents = []
        for i in range(2):
            agent = AgentGenome(
                role=f"{category}_worker_{i}",
                goal=f"Execute {category} tasks",
                backstory=f"Skilled worker in {category}",
                backstory_traits=["technical", "detail-oriented"],
                temperature=0.7,
                model_tier="worker",
                tools_enabled=False # Will be set to True by morph logic if technical
            )
            agents.append(agent)
        
        dept = DepartmentGenome(
            dept_id=dept_id,
            name=f"{category.replace('_', ' ').title()} Pod",
            mandate=f"Handle {category.replace('_', ' ')} functions",
            manager=manager,
            agents=agents,
            delegation_rules="Sequential review with collaborative cross-questioning"
        )
        
        return dept


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
        child.mutation_history = list(parent_a.mutation_history)
        # Optionally add label to mutation history? Spec doesn't explicitly say, 
        # but usually crossover is a mutation. Let's add it.
        child.mutation_history.append(f"Crossover_{label}")

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
            for trait in traits_a:
                if trait not in seen:
                    combined_traits.append(trait)
                    seen.add(trait)
            for trait in traits_b:
                if trait not in seen:
                    combined_traits.append(trait)
                    seen.add(trait)
            
            # Cap at 6 traits
            child.ceo.backstory_traits = combined_traits[:6]
            
            # Average temperature
            avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0
            child.ceo.temperature = round(avg_temp, 2)
            
            # Other CEO attributes? 
            # Role, goal, backstory? 
            # Spec doesn't specify, so we keep parent_a's (since we copied from A)
            # Or should we mix? "CEO Crossover" implies mixing.
            # Let's mix role and goal if they differ? 
            # Spec only mentions traits and temperature. We'll stick to that.

        # 5. Department Alignment
        # Align departments by classify_department_role
        
        # Group departments by category
        depts_a_by_cat = {}
        for dept in parent_a.departments:
            cat = classify_department_role(dept)
            if cat not in depts_a_by_cat:
                depts_a_by_cat[cat] = []
            depts_a_by_cat[cat].append(dept)
            
        depts_b_by_cat = {}
        for dept in parent_b.departments:
            cat = classify_department_role(dept)
            if cat not in depts_b_by_cat:
                depts_b_by_cat[cat] = []
            depts_b_by_cat[cat].append(dept)
        
        # Get all unique categories
        all_cats = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())
        
        new_departments = []
        
        for cat in all_cats:
            depts_a = depts_a_by_cat.get(cat, [])
            depts_b = depts_b_by_cat.get(cat, [])
            
            # Recombine departments in this category
            # If both have departments, we merge them.
            # If only one has, we take that one (deep copied).
            
            if not depts_a and not depts_b:
                continue
                
            if not depts_a:
                # Take from B
                for dept_b in depts_b:
                    new_departments.append(dept_b.copy())
                continue
                
            if not depts_b:
                # Take from A
                for dept_a in depts_a:
                    new_departments.append(dept_a.copy())
                continue
            
            # Both have departments. We need to recombine.
            # Strategy: 
            # 1. Take the first department from A as the base.
            # 2. Merge manager traits/temperature from B's first department.
            # 3. Interleave agents from A and B.
            
            base_dept = depts_a[0].copy()
            other_dept = depts_b[0]
            
            # Recombine Manager
            if base_dept.manager and other_dept.manager:
                # Combine traits
                traits_a = base_dept.manager.backstory_traits
                traits_b = other_dept.manager.backstory_traits
                
                combined_traits = []
                seen = set()
                for trait in traits_a:
                    if trait not in seen:
                        combined_traits.append(trait)
                        seen.add(trait)
                for trait in traits_b:
                    if trait not in seen:
                        combined_traits.append(trait)
                        seen.add(trait)
                
                base_dept.manager.backstory_traits = combined_traits[:6]
                
                # Average temperature
                avg_temp = (base_dept.manager.temperature + other_dept.manager.temperature) / 2.0
                base_dept.manager.temperature = round(avg_temp, 2)
            
            # Interleave Agents
            agents_a = base_dept.agents
            agents_b = other_dept.agents
            
            interleaved_agents = []
            max_len = max(len(agents_a), len(agents_b))
            for i in range(max_len):
                if i < len(agents_a):
                    interleaved_agents.append(agents_a[i].copy())
                if i < len(agents_b):
                    interleaved_agents.append(agents_b[i].copy())
            
            base_dept.agents = interleaved_agents
            
            new_departments.append(base_dept)
            
            # What about remaining departments in A or B for this category?
            # If A had 2 and B had 1, we merged the first ones. 
            # Should we keep the second one from A?
            # Spec says "Aligns departments... to recombine". 
            # It doesn't explicitly say to drop extras. 
            # Let's add the remaining ones from A and B as separate departments.
            
            for dept_a in depts_a[1:]:
                new_departments.append(dept_a.copy())
            for dept_b in depts_b[1:]:
                new_departments.append(dept_b.copy())

        child.departments = new_departments
        
        # Ensure we have at least 1 department (CompanyGenome validation)
        if not child.departments:
            # Fallback: create a dummy department
            import uuid
            dummy_dept = DepartmentGenome(
                dept_id=f"dept_dummy_{uuid.uuid4().hex[:8]}",
                name="Dummy Pod",
                mandate="Placeholder",
                manager=AgentGenome(
                    role="dummy_manager",
                    goal="Placeholder",
                    backstory="Placeholder",
                    model_tier="executive"
                ),
                agents=[]
            )
            child.departments = [dummy_dept]

        return child