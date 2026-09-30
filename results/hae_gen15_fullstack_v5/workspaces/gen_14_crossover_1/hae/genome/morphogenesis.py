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

# Departments that must never be pruned
PROTECTED_DEPT_IDS = {'dept_systems_eng', 'dept_qa_redteam'}

# Technical departments that require tool enablement
TECHNICAL_CATEGORIES = {'formal_verification', 'systems_eng', 'qa_testing', 'ai_acceleration'}


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
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history)
        child.mutation_history.append(mutation_name)
        
        # Ensure code_overlays is an independent copy (already handled by copy(), 
        # but explicit re-assignment ensures independence if copy() behavior changes)
        child.code_overlays = dict(parent.code_overlays)
        
        # 4. Protected pods: Identify protected departments
        protected_depts = []
        other_depts = []
        
        for dept in child.departments:
            if dept.dept_id in PROTECTED_DEPT_IDS:
                protected_depts.append(dept)
            else:
                other_depts.append(dept)
        
        # Determine which departments to keep.
        # We must retain at least 2 departments total.
        # Protected departments are always kept.
        # If we have fewer than 2 protected departments, we need to keep some others.
        
        # Strategy: Keep all protected departments.
        # If total protected < 2, keep enough non-protected to reach 2.
        # Otherwise, we can prune non-protected departments.
        
        # For this implementation, we'll keep all protected departments.
        # If we have < 2 protected, we keep the first (2 - len(protected)) non-protected ones.
        # If we have >= 2 protected, we can prune all non-protected (or keep some based on mutation logic).
        
        # Since the spec doesn't define specific pruning logic beyond constraints,
        # we'll implement a conservative approach:
        # - Always keep protected departments.
        # - If total departments < 2, add a new technical department if possible.
        # - If we have > 2 departments, we might prune non-protected ones.
        
        # Let's implement a simple morph:
        # 1. Keep all protected departments.
        # 2. If we have < 2 departments, add a new technical department.
        # 3. If we have > 2 departments, we can prune non-protected ones (keep at least 2 total).
        
        new_departments = list(protected_depts)
        
        # If we don't have enough departments, add new ones
        if len(new_departments) < 2:
            # Add a new technical department
            new_dept = self._create_new_technical_department(child_id, target_generation)
            if new_dept:
                new_departments.append(new_dept)
        
        # If we still don't have 2, add another
        if len(new_departments) < 2:
            new_dept = self._create_new_technical_department(child_id, target_generation, category='systems_eng')
            if new_dept:
                new_departments.append(new_dept)
        
        # If we have more than 2, we can prune non-protected departments
        # For now, we'll keep all departments to be safe, but ensure we have at least 2
        if len(new_departments) < 2:
            # Fallback: keep original departments if they meet constraints
            new_departments = list(child.departments)
        
        child.departments = new_departments
        
        # 3. Tool enablement: Ensure technical departments have tools_enabled=True
        for dept in child.departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_CATEGORIES:
                # Enable tools for all worker agents in this department
                if dept.manager and dept.manager.model_tier == 'worker':
                    dept.manager.tools_enabled = True
                for agent in dept.agents:
                    if agent.model_tier == 'worker':
                        agent.tools_enabled = True
        
        return child
    
    def _create_new_technical_department(
        self,
        company_id: str,
        generation: int,
        category: Optional[str] = None
    ) -> Optional[DepartmentGenome]:
        """Creates a new technical department with tools enabled."""
        if category is None:
            # Pick a random technical category
            categories = list(TECHNICAL_CATEGORIES)
            category = random.choice(categories)
        
        dept_id = f"dept_{category}_{company_id}_{generation}"
        
        # Create manager
        manager = AgentGenome(
            role=f"{category.replace('_', ' ').title()} Lead",
            goal=f"Lead {category.replace('_', ' ')} initiatives",
            backstory=f"Experienced leader in {category.replace('_', ' ')}",
            backstory_traits=["strategic", "technical"],
            temperature=0.7,
            model_tier="executive",
            tools_enabled=False
        )
        
        # Create worker agents with tools enabled
        agents = [
            AgentGenome(
                role=f"{category.replace('_', ' ').title()} Specialist",
                goal=f"Execute {category.replace('_', ' ')} tasks",
                backstory=f"Skilled practitioner in {category.replace('_', ' ')}",
                backstory_traits=["skilled", "detail-oriented"],
                temperature=0.7,
                model_tier="worker",
                tools_enabled=True
            )
            for _ in range(2)
        ]
        
        dept = DepartmentGenome(
            dept_id=dept_id,
            name=f"{category.replace('_', ' ').title()} Pod",
            mandate=f"Drive {category.replace('_', ' ')} excellence",
            manager=manager,
            agents=agents
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
        child.mutation_history.append(f"Crossover with {parent_b.company_id}")
        
        # 3. Level 3 RSI Code Overlay Inheritance
        # parent_a takes precedence over parent_b
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}
        
        # 4. CEO Crossover
        if parent_a.ceo and parent_b.ceo:
            # Combine and deduplicate backstory traits, preserving order, capped at 6
            combined_traits = []
            seen = set()
            for trait in parent_a.ceo.backstory_traits + parent_b.ceo.backstory_traits:
                if trait not in seen:
                    combined_traits.append(trait)
                    seen.add(trait)
            combined_traits = combined_traits[:6]
            
            # Average temperature
            avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2.0
            rounded_temp = round(avg_temp, 2)
            
            # Create new CEO
            child.ceo = AgentGenome(
                role=parent_a.ceo.role,
                goal=parent_a.ceo.goal,
                backstory=f"{label} CEO combining traits from {parent_a.company_id} and {parent_b.company_id}",
                backstory_traits=combined_traits,
                temperature=rounded_temp,
                model_tier=parent_a.ceo.model_tier,
                tools_enabled=parent_a.ceo.tools_enabled,
                system_instructions=parent_a.ceo.system_instructions
            )
        
        # 5. Department Alignment
        # Group departments by functional category
        depts_a_by_category: Dict[str, List[DepartmentGenome]] = {}
        for dept in parent_a.departments:
            category = classify_department_role(dept)
            if category not in depts_a_by_category:
                depts_a_by_category[category] = []
            depts_a_by_category[category].append(dept)
        
        depts_b_by_category: Dict[str, List[DepartmentGenome]] = {}
        for dept in parent_b.departments:
            category = classify_department_role(dept)
            if category not in depts_b_by_category:
                depts_b_by_category[category] = []
            depts_b_by_category[category].append(dept)
        
        # Get all unique categories
        all_categories = set(depts_a_by_category.keys()) | set(depts_b_by_category.keys())
        
        new_departments = []
        dept_counter = 0
        
        for category in sorted(all_categories):
            depts_a = depts_a_by_category.get(category, [])
            depts_b = depts_b_by_category.get(category, [])
            
            # Recombine departments in this category
            max_depts = max(len(depts_a), len(depts_b))
            
            for i in range(max_depts):
                dept_a = depts_a[i] if i < len(depts_a) else None
                dept_b = depts_b[i] if i < len(depts_b) else None
                
                if dept_a and dept_b:
                    # Both parents have a department in this category
                    new_dept = self._recombine_departments(dept_a, dept_b, child_id, target_generation, dept_counter)
                    new_departments.append(new_dept)
                    dept_counter += 1
                elif dept_a:
                    # Only parent_a has this department
                    new_dept = dept_a.copy()
                    new_dept.dept_id = f"dept_{category}_{child_id}_{target_generation}_{dept_counter}"
                    new_departments.append(new_dept)
                    dept_counter += 1
                elif dept_b:
                    # Only parent_b has this department
                    new_dept = dept_b.copy()
                    new_dept.dept_id = f"dept_{category}_{child_id}_{target_generation}_{dept_counter}"
                    new_departments.append(new_dept)
                    dept_counter += 1
        
        child.departments = new_departments
        
        return child
    
    def _recombine_departments(
        self,
        dept_a: DepartmentGenome,
        dept_b: DepartmentGenome,
        child_id: str,
        generation: int,
        index: int
    ) -> DepartmentGenome:
        """Recombines two departments of the same category."""
        category = classify_department_role(dept_a)
        dept_id = f"dept_{category}_{child_id}_{generation}_{index}"
        
        # Recombine manager
        new_manager = self._recombine_agents(dept_a.manager, dept_b.manager, f"{category} Manager")
        
        # Interleave specialist agents
        agents_a = dept_a.agents
        agents_b = dept_b.agents
        
        # Interleave: take one from A, one from B, etc.
        interleaved_agents = []
        max_agents = max(len(agents_a), len(agents_b))
        
        for i in range(max_agents):
            if i < len(agents_a):
                agent_copy = agents_a[i].copy()
                interleaved_agents.append(agent_copy)
            if i < len(agents_b):
                agent_copy = agents_b[i].copy()
                interleaved_agents.append(agent_copy)
        
        # Ensure tools are enabled for technical categories
        if category in TECHNICAL_CATEGORIES:
            for agent in interleaved_agents:
                if agent.model_tier == 'worker':
                    agent.tools_enabled = True
            if new_manager and new_manager.model_tier == 'worker':
                new_manager.tools_enabled = True
        
        new_dept = DepartmentGenome(
            dept_id=dept_id,
            name=f"{category.replace('_', ' ').title()} Pod (Recombinant)",
            mandate=f"Combined mandate from {dept_a.dept_id} and {dept_b.dept_id}",
            manager=new_manager,
            agents=interleaved_agents,
            delegation_rules=dept_a.delegation_rules
        )
        
        return new_dept
    
    def _recombine_agents(
        self,
        agent_a: Optional[AgentGenome],
        agent_b: Optional[AgentGenome],
        role_hint: str
    ) -> Optional[AgentGenome]:
        """Recombines two agents, combining traits and averaging temperature."""
        if not agent_a and not agent_b:
            return None
        if not agent_a:
            return agent_b.copy()
        if not agent_b:
            return agent_a.copy()
        
        # Combine and deduplicate backstory traits, preserving order, capped at 6
        combined_traits = []
        seen = set()
        for trait in agent_a.backstory_traits + agent_b.backstory_traits:
            if trait not in seen:
                combined_traits.append(trait)
                seen.add(trait)
        combined_traits = combined_traits[:6]
        
        # Average temperature
        avg_temp = (agent_a.temperature + agent_b.temperature) / 2.0
        rounded_temp = round(avg_temp, 2)
        
        # Use agent_a's role and goal as base
        new_agent = AgentGenome(
            role=agent_a.role,
            goal=agent_a.goal,
            backstory=f"Recombinant {role_hint} from {agent_a.role} and {agent_b.role}",
            backstory_traits=combined_traits,
            temperature=rounded_temp,
            model_tier=agent_a.model_tier,
            tools_enabled=agent_a.tools_enabled or agent_b.tools_enabled,
            system_instructions=agent_a.system_instructions
        )
        
        return new_agent