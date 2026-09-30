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
    GenomeValidationError
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

# Technical departments that require tools_enabled=True on spawn
TECHNICAL_DEPARTMENTS = {'formal_verification', 'systems_eng', 'qa_testing', 'ai_acceleration'}

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

    def _create_default_agent(self, role: str, goal: str, backstory: str, tools_enabled: bool = False) -> AgentGenome:
        """Helper to create a standard worker agent."""
        return AgentGenome(
            role=role,
            goal=goal,
            backstory=backstory,
            backstory_traits=["adaptive", "resilient"],
            temperature=0.7,
            model_tier="worker",
            tools_enabled=tools_enabled
        )

    def _create_default_manager(self, role: str, goal: str, backstory: str) -> AgentGenome:
        """Helper to create a standard manager agent."""
        return AgentGenome(
            role=role,
            goal=goal,
            backstory=backstory,
            backstory_traits=["strategic", "decisive"],
            temperature=0.5,
            model_tier="executive",
            tools_enabled=False
        )

    def _spawn_department(self, category: str, dept_id: str, name: str, mandate: str) -> DepartmentGenome:
        """Spawns a new department pod with appropriate agents and tool settings."""
        is_tech = category in TECHNICAL_DEPARTMENTS
        tools_enabled = is_tech
        
        manager = self._create_default_manager(
            role=f"{name} Lead",
            goal=f"Oversee {mandate}",
            backstory=f"Experienced leader in {category.replace('_', ' ')}."
        )
        
        agents = [
            self._create_default_agent(
                role=f"{name} Specialist",
                goal=f"Execute {mandate} tasks",
                backstory=f"Expert in {category.replace('_', ' ')} operations.",
                tools_enabled=tools_enabled
            )
        ]
        
        return DepartmentGenome(
            dept_id=dept_id,
            name=name,
            mandate=mandate,
            manager=manager,
            agents=agents
        )

    def morph_genome_topology(self, parent: CompanyGenome, mutation_name: str, target_generation: int, child_id: str) -> CompanyGenome:
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
        
        # 3 & 4. Topology Morphing Logic
        
        # Identify current departments and their categories
        current_depts = child.departments
        dept_categories = {d.dept_id: classify_department_role(d) for d in current_depts}
        
        # Determine which categories are present
        present_categories = set(dept_categories.values())
        
        # Strategy: 
        # - If we have fewer than 2 departments, add one.
        # - If we have protected departments, ensure they exist.
        # - Randomly prune non-protected departments if we have > 2, ensuring we keep at least 2.
        # - Randomly add a new technical department if missing.
        
        # Ensure protected departments exist
        protected_present = {pid for pid in PROTECTED_DEPT_IDS if pid in dept_categories}
        
        # If protected depts are missing, spawn them
        for pid in PROTECTED_DEPT_IDS:
            if pid not in dept_categories:
                # Determine category for protected dept
                if pid == 'dept_systems_eng':
                    cat = 'systems_eng'
                    name = "Systems Engineering"
                    mandate = "Core infrastructure and system architecture"
                else: # dept_qa_redteam
                    cat = 'qa_testing'
                    name = "QA & Red Team"
                    mandate = "Quality assurance and security verification"
                
                new_dept = self._spawn_department(cat, pid, name, mandate)
                child.departments.append(new_dept)
                dept_categories[pid] = cat
                present_categories.add(cat)
        
        # Pruning logic: Keep at least 2 departments. Never prune protected ones.
        # We can prune non-protected departments if we have more than 2.
        non_protected_depts = [d for d in child.departments if d.dept_id not in PROTECTED_DEPT_IDS]
        
        # If we have too many departments, prune some non-protected ones
        # Target: Keep protected + at least 1 other if possible, or just protected if only 2 protected exist.
        # Simple heuristic: If len(departments) > 3, prune one random non-protected dept.
        if len(child.departments) > 3 and non_protected_depts:
            # Pick a random non-protected dept to remove
            dept_to_remove = random.choice(non_protected_depts)
            child.departments.remove(dept_to_remove)
            # Update local tracking
            if dept_to_remove.dept_id in dept_categories:
                del dept_categories[dept_to_remove.dept_id]
                # Recalculate present categories
                present_categories = set(dept_categories.values())
        
        # Ensure we have at least 2 departments
        if len(child.departments) < 2:
            # Add a generic department
            new_dept = self._spawn_department(
                'product_ux', 
                'dept_product_ux', 
                'Product & UX', 
                'User experience and product specification'
            )
            child.departments.append(new_dept)
            dept_categories[new_dept.dept_id] = 'product_ux'
            present_categories.add('product_ux')
            
        # Add a new technical department if missing and we have room (optional mutation)
        # Let's add 'ai_acceleration' if it's missing and we have < 4 departments
        if 'ai_acceleration' not in present_categories and len(child.departments) < 4:
            new_dept = self._spawn_department(
                'ai_acceleration',
                'dept_ai_accel',
                'AI Acceleration',
                'Hardware optimization and kernel compilation'
            )
            child.departments.append(new_dept)
            dept_categories[new_dept.dept_id] = 'ai_acceleration'
            present_categories.add('ai_acceleration')
            
        # Ensure all technical departments have tools_enabled=True on their agents
        # This covers both newly spawned and potentially existing ones that might have been misconfigured
        for dept in child.departments:
            cat = classify_department_role(dept)
            if cat in TECHNICAL_DEPARTMENTS:
                # Enable tools for all worker agents in this department
                for agent in dept.agents:
                    if agent.model_tier == "worker":
                        agent.tools_enabled = True
                # Managers usually don't need tools, but spec says "worker AgentGenome instances"
                # So we only touch workers.

        return child


class StructuralCrossoverEngine:
    """Enables genetic crossover between two enterprises with asymmetric departmental topologies."""

    def recombine(self, parent_a: CompanyGenome, parent_b: CompanyGenome, child_id: str, target_generation: int, label: str = 'Recombinant') -> CompanyGenome:
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
        # We start with a copy of parent_a to maintain structure, then merge B into it
        child = parent_a.copy()
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        child.mutation_history = list(parent_a.mutation_history) + list(parent_b.mutation_history)
        # Optional: Add label to mutation history if desired, but spec doesn't explicitly require it for crossover
        # child.mutation_history.append(f"Crossover:{label}")
        
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
        child.ceo.temperature = round(avg_temp, 2)
        
        # 5. Department Alignment
        # Group departments from both parents by functional category
        depts_a_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for d in parent_a.departments:
            cat = classify_department_role(d)
            depts_a_by_cat.setdefault(cat, []).append(d)
            
        depts_b_by_cat: Dict[str, List[DepartmentGenome]] = {}
        for d in parent_b.departments:
            cat = classify_department_role(d)
            depts_b_by_cat.setdefault(cat, []).append(d)
        
        # All categories present in either parent
        all_categories = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())
        
        new_departments: List[DepartmentGenome] = []
        
        for cat in all_categories:
            list_a = depts_a_by_cat.get(cat, [])
            list_b = depts_b_by_cat.get(cat, [])
            
            # If a category exists in both, we perform crossover on the first matching dept
            # If it exists in only one, we inherit it (deep copied)
            
            if list_a and list_b:
                # Crossover between first dept of A and first dept of B
                dept_a = list_a[0]
                dept_b = list_b[0]
                
                # Create a new department for the child
                # Use ID from A, but merge properties
                new_dept = DepartmentGenome(
                    dept_id=dept_a.dept_id,
                    name=dept_a.name, # Could blend names, but keeping A's is safe
                    mandate=dept_a.mandate,
                    delegation_rules=dept_a.delegation_rules,
                    extra={}
                )
                
                # Manager Crossover
                # Combine traits, average temp
                mgr_a = dept_a.manager
                mgr_b = dept_b.manager
                
                mgr_traits = []
                seen_m = set()
                for t in mgr_a.backstory_traits:
                    if t not in seen_m:
                        mgr_traits.append(t)
                        seen_m.add(t)
                for t in mgr_b.backstory_traits:
                    if t not in seen_m:
                        mgr_traits.append(t)
                        seen_m.add(t)
                
                mgr_temp = round((mgr_a.temperature + mgr_b.temperature) / 2.0, 2)
                
                new_manager = AgentGenome(
                    role=mgr_a.role,
                    goal=mgr_a.goal,
                    backstory=mgr_a.backstory,
                    backstory_traits=mgr_traits[:6],
                    temperature=mgr_temp,
                    model_tier=mgr_a.model_tier, # Keep A's tier
                    tools_enabled=mgr_a.tools_enabled
                )
                new_dept.manager = new_manager
                
                # Agent Interleaving
                # Combine agents from both, deduplicate by role? Or just interleave.
                # Spec says "interleave specialist agents"
                agents_a = dept_a.agents
                agents_b = dept_b.agents
                
                interleaved_agents = []
                max_len = max(len(agents_a), len(agents_b))
                for i in range(max_len):
                    if i < len(agents_a):
                        # Deep copy agent from A
                        ag_a = agents_a[i].copy()
                        interleaved_agents.append(ag_a)
                    if i < len(agents_b):
                        # Deep copy agent from B
                        ag_b = agents_b[i].copy()
                        interleaved_agents.append(ag_b)
                
                # Ensure tools_enabled is correct for technical categories
                if cat in TECHNICAL_DEPARTMENTS:
                    for ag in interleaved_agents:
                        if ag.model_tier == "worker":
                            ag.tools_enabled = True
                            
                new_dept.agents = interleaved_agents
                new_departments.append(new_dept)
                
            elif list_a:
                # Inherit from A
                dept = list_a[0].copy()
                # Ensure tools enabled if technical
                if cat in TECHNICAL_DEPARTMENTS:
                    for ag in dept.agents:
                        if ag.model_tier == "worker":
                            ag.tools_enabled = True
                new_departments.append(dept)
                
            elif list_b:
                # Inherit from B
                dept = list_b[0].copy()
                # Ensure tools enabled if technical
                if cat in TECHNICAL_DEPARTMENTS:
                    for ag in dept.agents:
                        if ag.model_tier == "worker":
                            ag.tools_enabled = True
                new_departments.append(dept)
        
        child.departments = new_departments
        
        # Ensure we have at least 2 departments (invariant from morphogenesis, likely applies here too for validity)
        if len(child.departments) < 2:
            # If crossover resulted in too few, duplicate one or add a generic one
            # For safety, if we have 1, we can't easily add without knowing context.
            # However, since we iterate all categories from both parents, and parents must have >=1 dept,
            # we should have at least 1 category. If both parents had only 1 dept of same category, we have 1.
            # We need to ensure validity.
            if len(child.departments) == 1:
                # Duplicate the single department with a new ID to satisfy "at least 2"
                # Or add a generic one. Let's add a generic one.
                generic_dept = DepartmentGenome(
                    dept_id="dept_generic_ops",
                    name="General Operations",
                    mandate="General operational support",
                    manager=AgentGenome(
                        role="Ops Manager",
                        goal="Support operations",
                        backstory="Generalist manager",
                        backstory_traits=["flexible"],
                        temperature=0.7,
                        model_tier="executive"
                    ),
                    agents=[AgentGenome(
                        role="Ops Specialist",
                        goal="Execute tasks",
                        backstory="Generalist worker",
                        backstory_traits=["hardworking"],
                        temperature=0.7,
                        model_tier="worker"
                    )]
                )
                child.departments.append(generic_dept)

        return child