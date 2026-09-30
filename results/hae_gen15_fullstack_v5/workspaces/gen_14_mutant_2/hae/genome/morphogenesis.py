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

# Categories that require tools_enabled=True on spawned agents
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
        
        # Ensure code_overlays is an independent copy (deepcopy handles this, 
        # but explicit check ensures no shared reference if copy() was shallow)
        child.code_overlays = copy.deepcopy(parent.code_overlays)

        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history)
        child.mutation_history.append(mutation_name)

        # 4. Protected pods & Minimum departments
        # Identify protected departments
        protected_depts = [
            d for d in child.departments 
            if d.dept_id in PROTECTED_DEPT_IDS
        ]
        
        # Identify non-protected departments
        non_protected_depts = [
            d for d in child.departments 
            if d.dept_id not in PROTECTED_DEPT_IDS
        ]

        # Ensure we retain at least 2 departments.
        # If we have fewer than 2 protected depts, we must keep some non-protected ones.
        # If we have >= 2 protected depts, we can prune all non-protected ones if desired,
        # but the spec says "must retain at least 2 departments".
        
        # Strategy: Keep all protected. Keep enough non-protected to reach min 2 total.
        # If protected count >= 2, we can prune all non-protected.
        # If protected count < 2, we must keep (2 - protected_count) non-protected.
        
        min_total = 2
        num_protected = len(protected_depts)
        
        if num_protected >= min_total:
            # We can prune all non-protected departments
            child.departments = protected_depts
        else:
            # Need to keep some non-protected
            needed_non_protected = min_total - num_protected
            # Keep the first 'needed_non_protected' non-protected departments
            # (Order preservation from parent copy)
            kept_non_protected = non_protected_depts[:needed_non_protected]
            child.departments = protected_depts + kept_non_protected

        # 3. Tool enablement for newly spawned technical departments
        # "Newly spawned" implies departments that were not in the parent or are newly added.
        # However, the spec says "Any newly spawned technical department... must set tools_enabled=True".
        # Since we are morphing, we might be adding new departments.
        # The current implementation copies parent departments. 
        # If the mutation logic (not fully specified here, but implied by "adding... pods") 
        # adds new departments, we need to handle them.
        # 
        # Looking at the signature, `morph_genome_topology` doesn't take a list of new departments.
        # It takes `mutation_name`. This suggests the mutation logic might be internal or 
        # the "newly spawned" refers to departments that are *created* during the morph process.
        # 
        # Wait, the prompt says "adding, pruning, or reshaping". 
        # If we are just copying and pruning, we aren't adding.
        # However, often "morphing" implies creating new structures.
        # Let's look at the constraint: "Any newly spawned technical department...".
        # If no new departments are spawned, this is a no-op.
        # 
        # But typically, a morph engine might inject new departments based on the mutation name.
        # Since the API doesn't provide the new departments, I must assume that either:
        # A) The mutation logic is external and I just handle the result? No, I return the child.
        # B) I need to simulate the "spawning" based on the mutation name?
        # C) The "newly spawned" refers to departments that are *present* in the child but 
        #    were not in the parent?
        # 
        # Let's re-read carefully: "Morphs an existing genome by adding...".
        # If I don't add anything, I'm just pruning.
        # 
        # Let's assume the standard behavior for such engines: 
        # If a department is *new* (i.e., its ID was not in parent.dept_ids), and it falls into 
        # TECHNICAL_CATEGORIES, enable tools.
        #
        # Since I don't have logic to *create* new departments from scratch without more info,
        # I will assume that any department in `child.departments` that was NOT in `parent.departments`
        # is "newly spawned".
        
        parent_dept_ids = {d.dept_id for d in parent.departments}
        
        for dept in child.departments:
            if dept.dept_id not in parent_dept_ids:
                # This is a newly spawned department
                category = classify_department_role(dept)
                if category in TECHNICAL_CATEGORIES:
                    # Set tools_enabled=True on worker agents
                    for agent in dept.agents:
                        if agent.model_tier == "worker":
                            agent.tools_enabled = True
                    # Also check manager? Spec says "worker AgentGenome instances".
                    # Usually managers are executive. If manager is worker, enable too.
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
        # Create a new child genome. We can't just copy one parent because we need to merge.
        # Start with a copy of parent_a to get the structure, then modify.
        child = parent_a.copy()
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        # Mutation history? Spec doesn't explicitly say to append label, but usually crossover 
        # is a mutation event. However, invariant 2 only lists company_id, generation, parent_ids.
        # I will leave mutation_history as inherited from parent_a (via copy) unless specified.
        # Actually, looking at Morphogenesis, it appends. Here, it's a crossover.
        # I'll leave it as is from parent_a copy, or maybe clear it? 
        # The spec doesn't mandate changing mutation_history for crossover, only lineage fields.
        
        # 3. Level 3 RSI Code Overlay Inheritance
        # {**parent_b.code_overlays, **parent_a.code_overlays}
        # This means parent_a wins on collisions.
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # 4. CEO Crossover
        if parent_a.ceo and parent_b.ceo:
            # Combine and deduplicate backstory_traits, preserving order, capped at 6
            traits_a = parent_a.ceo.backstory_traits or []
            traits_b = parent_b.ceo.backstory_traits or []
            
            combined_traits = []
            seen = set()
            
            # Add from A first (preserving order)
            for t in traits_a:
                if t not in seen:
                    combined_traits.append(t)
                    seen.add(t)
            
            # Add from B (preserving order, skipping duplicates)
            for t in traits_b:
                if t not in seen:
                    combined_traits.append(t)
                    seen.add(t)
            
            # Cap at 6
            child.ceo.backstory_traits = combined_traits[:6]
            
            # Average temperature
            avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0
            child.ceo.temperature = round(avg_temp, 2) # "rounded average" usually implies 2 decimals or int? 
            # Schema uses float. "Rounded" usually means standard rounding. 
            # Let's use round(x, 2) to be safe, or just round(x) if integer? 
            # Temperature is float. I'll use round(x, 2).
            
            # Other CEO fields? Spec only mentions traits and temperature.
            # The copy from parent_a already has other fields.

        # 5. Department Alignment
        # Align departments by classify_department_role
        
        # Group parent_a departments by category
        depts_a_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for d in parent_a.departments:
            cat = classify_department_role(d)
            if cat not in depts_a_by_cat:
                depts_a_by_cat[cat] = []
            depts_a_by_cat[cat].append(d)
            
        # Group parent_b departments by category
        depts_b_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for d in parent_b.departments:
            cat = classify_department_role(d)
            if cat not in depts_b_by_cat:
                depts_b_by_cat[cat] = []
            depts_b_by_cat[cat].append(d)
            
        # Get all unique categories
        all_cats = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())
        
        new_departments: List[DepartmentGenome] = []
        
        for cat in all_cats:
            list_a = depts_a_by_cat.get(cat, [])
            list_b = depts_b_by_cat.get(cat, [])
            
            # Recombine departments in this category
            # Strategy: 
            # 1. If both have departments, pair them up (zip) and crossover.
            # 2. If one has more, the extras are inherited as-is (deep copied).
            
            max_len = max(len(list_a), len(list_b))
            
            for i in range(max_len):
                dept_a = list_a[i] if i < len(list_a) else None
                dept_b = list_b[i] if i < len(list_b) else None
                
                if dept_a and dept_b:
                    # Crossover
                    new_dept = self._crossover_department(dept_a, dept_b)
                    new_departments.append(new_dept)
                elif dept_a:
                    # Inherit from A
                    new_departments.append(dept_a.copy())
                elif dept_b:
                    # Inherit from B
                    new_departments.append(dept_b.copy())
                    
        child.departments = new_departments
        
        return child

    def _crossover_department(
        self, 
        dept_a: DepartmentGenome, 
        dept_b: DepartmentGenome
    ) -> DepartmentGenome:
        """Performs allelic crossover between two departments of the same functional category."""
        # Create a new department. 
        # We need a dept_id. We can't just use A's or B's if they are different.
        # Usually, we might generate a new ID or use a convention.
        # Since the spec doesn't specify ID generation for crossover, 
        # I will use dept_a's ID as the base, or maybe a combination?
        # Let's stick to dept_a's ID to maintain some stability, or perhaps 
        # the caller expects unique IDs. 
        # Given the constraints, I'll use dept_a.dept_id. If collision occurs in the final list,
        # that's a problem, but since we are grouping by category and pairing, 
        # and categories are distinct, IDs from different categories won't collide.
        # Within a category, if we have multiple pairs, we might have ID collisions if 
        # A and B have same IDs. 
        # Let's assume IDs are unique within a parent. 
        # If dept_a.dept_id == dept_b.dept_id, we keep it.
        # If they differ, we pick one. Let's pick dept_a.dept_id.
        
        # Determine manager
        new_manager = None
        if dept_a.manager and dept_b.manager:
            new_manager = self._crossover_agent(dept_a.manager, dept_b.manager)
        elif dept_a.manager:
            new_manager = dept_a.manager.copy()
        elif dept_b.manager:
            new_manager = dept_b.manager.copy()
        
        # If no manager exists in either, we must create a default one to satisfy schema validation
        if new_manager is None:
            new_manager = AgentGenome(
                role="Manager",
                goal="Manage department",
                backstory="Default manager",
                backstory_traits=[],
                temperature=0.7,
                model_tier="executive",
                tools_enabled=False
            )

        # Agents Interleaving
        # "Interleave specialist agents"
        agents_a = dept_a.agents or []
        agents_b = dept_b.agents or []
        
        interleaved_agents = []
        max_agents = max(len(agents_a), len(agents_b))
        
        for i in range(max_agents):
            if i < len(agents_a):
                interleaved_agents.append(agents_a[i].copy())
            if i < len(agents_b):
                interleaved_agents.append(agents_b[i].copy())
                
        new_dept = DepartmentGenome(
            dept_id=dept_a.dept_id,
            name=dept_a.name, # Or mix? Spec doesn't say. Keep A's name.
            mandate=dept_a.mandate, # Keep A's mandate.
            manager=new_manager,
            agents=interleaved_agents,
            delegation_rules=dept_a.delegation_rules,
        )
        
        return new_dept

    def _crossover_agent(
        self, 
        agent_a: AgentGenome, 
        agent_b: AgentGenome
    ) -> AgentGenome:
        """Crossovers two agents (e.g., managers)."""
        # Combine traits
        traits_a = agent_a.backstory_traits or []
        traits_b = agent_b.backstory_traits or []
        
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
                
        # Cap at 6? Spec says CEO capped at 6. Does it apply to managers?
        # "Combines and deduplicates ceo.backstory_traits... capped at 6".
        # It doesn't explicitly say managers are capped. 
        # However, for consistency, I'll cap at 6.
        final_traits = combined_traits[:6]
        
        # Average temperature
        avg_temp = (agent_a.temperature + agent_b.temperature) / 2.0
        final_temp = round(avg_temp, 2)
        
        # Create new agent
        # Role, Goal, Backstory? 
        # Spec says "recombine manager traits/temperatures".
        # It doesn't mention role/goal. I'll keep A's role/goal/backstory.
        
        new_agent = AgentGenome(
            role=agent_a.role,
            goal=agent_a.goal,
            backstory=agent_a.backstory,
            backstory_traits=final_traits,
            temperature=final_temp,
            model_tier=agent_a.model_tier, # Keep A's tier? Or mix? 
            # Usually tier is structural. Keep A's.
            tools_enabled=agent_a.tools_enabled, # Keep A's?
            system_instructions=agent_a.system_instructions,
        )
        
        return new_agent