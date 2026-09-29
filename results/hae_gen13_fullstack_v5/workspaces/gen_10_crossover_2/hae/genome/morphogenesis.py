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

    def _create_default_agent(self, role: str, goal: str = "", backstory: str = "", 
                              temperature: float = 0.7, tools_enabled: bool = False,
                              model_tier: str = "worker") -> AgentGenome:
        """Helper to create a new AgentGenome with defaults."""
        return AgentGenome(
            role=role,
            goal=goal or f"Execute {role} tasks",
            backstory=backstory or f"Specialized in {role}",
            backstory_traits=[f"expert_{role}"],
            temperature=temperature,
            model_tier=model_tier,
            tools_enabled=tools_enabled
        )

    def _create_default_department(self, dept_id: str, name: str, mandate: str, 
                                   category: str = "custom_specialized") -> DepartmentGenome:
        """Helper to create a new DepartmentGenome with a default manager and agents."""
        is_technical = category in TECHNICAL_DEPARTMENTS
        
        manager = self._create_default_agent(
            role=f"{name} Manager",
            goal=f"Lead {name} operations",
            backstory=f"Experienced leader in {name}",
            temperature=0.5,
            tools_enabled=is_technical,
            model_tier="executive"
        )
        
        agents = [
            self._create_default_agent(
                role=f"{name} Specialist",
                goal=f"Perform {name} tasks",
                backstory=f"Skilled practitioner in {name}",
                temperature=0.7,
                tools_enabled=is_technical,
                model_tier="worker"
            )
        ]
        
        return DepartmentGenome(
            dept_id=dept_id,
            name=name,
            mandate=mandate,
            manager=manager,
            agents=agents
        )

    def morph_genome_topology(self, parent: CompanyGenome, mutation_name: str, 
                              target_generation: int, child_id: str) -> CompanyGenome:
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
        
        # Ensure code_overlays is an independent copy (deepcopy handles this, but explicit check)
        child.code_overlays = copy.deepcopy(parent.code_overlays)
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history)
        child.mutation_history.append(mutation_name)
        
        # Identify current departments and their categories
        current_depts = child.departments
        dept_categories = {d.dept_id: classify_department_role(d) for d in current_depts}
        
        # Determine mutation type based on name or random choice if ambiguous
        # For simplicity, we implement a generic morph that ensures invariants are met.
        # A real implementation might parse mutation_name for specific instructions.
        
        # Ensure protected pods exist
        protected_present = {d.dept_id for d in current_depts if d.dept_id in PROTECTED_DEPT_IDS}
        
        # If we need to add departments to meet minimum count or fill gaps
        # Minimum 2 departments required
        if len(current_depts) < 2:
            # Add a default department if missing
            if 'dept_systems_eng' not in protected_present:
                new_dept = self._create_default_department(
                    dept_id='dept_systems_eng',
                    name='Systems Engineering',
                    mandate='Core infrastructure and systems development',
                    category='systems_eng'
                )
                current_depts.append(new_dept)
            
            if len(current_depts) < 2 and 'dept_qa_redteam' not in protected_present:
                new_dept = self._create_default_department(
                    dept_id='dept_qa_redteam',
                    name='QA & Red Team',
                    mandate='Quality assurance and security verification',
                    category='qa_testing'
                )
                current_depts.append(new_dept)
            
            if len(current_depts) < 2:
                # Add a generic one
                new_dept = self._create_default_department(
                    dept_id='dept_general_ops',
                    name='General Operations',
                    mandate='General operational support',
                    category='custom_specialized'
                )
                current_depts.append(new_dept)

        # Ensure tool enablement for technical departments
        for dept in current_depts:
            category = classify_department_role(dept)
            if category in TECHNICAL_DEPARTMENTS:
                # Enable tools for manager and agents
                if dept.manager:
                    dept.manager.tools_enabled = True
                for agent in dept.agents:
                    agent.tools_enabled = True

        # Update child departments
        child.departments = current_depts
        
        return child


class StructuralCrossoverEngine:
    """Enables genetic crossover between two enterprises with asymmetric departmental topologies."""

    def _merge_traits(self, traits_a: List[str], traits_b: List[str], max_traits: int = 6) -> List[str]:
        """Combines and deduplicates traits, preserving order, capped at max_traits."""
        seen = set()
        merged = []
        for t in traits_a:
            if t not in seen:
                seen.add(t)
                merged.append(t)
        for t in traits_b:
            if t not in seen:
                seen.add(t)
                merged.append(t)
        return merged[:max_traits]

    def _average_temperature(self, temp_a: float, temp_b: float) -> float:
        """Calculates rounded average temperature."""
        avg = (temp_a + temp_b) / 2.0
        return round(avg, 2)

    def _recombine_agents(self, agents_a: List[AgentGenome], agents_b: List[AgentGenome]) -> List[AgentGenome]:
        """Interleaves specialist agents from both parents."""
        combined = []
        max_len = max(len(agents_a), len(agents_b))
        
        for i in range(max_len):
            if i < len(agents_a):
                combined.append(agents_a[i].copy())
            if i < len(agents_b):
                combined.append(agents_b[i].copy())
        
        return combined

    def _recombine_manager(self, mgr_a: Optional[AgentGenome], mgr_b: Optional[AgentGenome]) -> Optional[AgentGenome]:
        """Recombines manager traits and temperatures."""
        if mgr_a is None and mgr_b is None:
            return None
        if mgr_a is None:
            return mgr_b.copy()
        if mgr_b is None:
            return mgr_a.copy()
        
        # Start with a copy of A
        new_mgr = mgr_a.copy()
        
        # Merge traits
        new_mgr.backstory_traits = self._merge_traits(
            mgr_a.backstory_traits, 
            mgr_b.backstory_traits
        )
        
        # Average temperature
        new_mgr.temperature = self._average_temperature(mgr_a.temperature, mgr_b.temperature)
        
        return new_mgr

    def recombine(self, parent_a: CompanyGenome, parent_b: CompanyGenome, 
                  child_id: str, target_generation: int, label: str = 'Recombinant') -> CompanyGenome:
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
        # We construct a new child from scratch or copy one parent and modify.
        # Copying parent_a is a good base.
        child = parent_a.copy()
        
        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        child.mutation_history = list(parent_a.mutation_history)
        child.mutation_history.append(f"Crossover_{label}")
        
        # 3. Level 3 RSI Code Overlay Inheritance
        # parent_a takes precedence
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}
        
        # 4. CEO Crossover
        if parent_a.ceo and parent_b.ceo:
            child.ceo = parent_a.ceo.copy()
            child.ceo.backstory_traits = self._merge_traits(
                parent_a.ceo.backstory_traits,
                parent_b.ceo.backstory_traits,
                max_traits=6
            )
            child.ceo.temperature = self._average_temperature(
                parent_a.ceo.temperature,
                parent_b.ceo.temperature
            )
        elif parent_a.ceo:
            child.ceo = parent_a.ceo.copy()
        elif parent_b.ceo:
            child.ceo = parent_b.ceo.copy()
        else:
            # Should not happen if schema validation passed, but safe guard
            child.ceo = AgentGenome(role="CEO", goal="Lead", backstory="Recombinant CEO")

        # 5. Department Alignment
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
        
        # Get all unique categories
        all_categories = set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())
        
        new_departments = []
        
        for cat in all_categories:
            list_a = depts_a_by_cat.get(cat, [])
            list_b = depts_b_by_cat.get(cat, [])
            
            # If both have departments in this category, recombine them
            if list_a and list_b:
                # For simplicity, we recombine the first department of each list
                # A more complex strategy might recombine all pairs or merge lists
                dept_a = list_a[0]
                dept_b = list_b[0]
                
                # Create a new department ID to avoid collision if we keep both?
                # The spec says "Aligns departments... to recombine". 
                # Usually crossover produces one offspring per aligned pair.
                # We'll create one merged department per category present in both.
                
                new_dept_id = f"dept_{cat}_recomb"
                new_name = f"{dept_a.name} / {dept_b.name}"
                new_mandate = f"Merged mandate: {dept_a.mandate} | {dept_b.mandate}"
                
                new_manager = self._recombine_manager(dept_a.manager, dept_b.manager)
                new_agents = self._recombine_agents(dept_a.agents, dept_b.agents)
                
                new_dept = DepartmentGenome(
                    dept_id=new_dept_id,
                    name=new_name,
                    mandate=new_mandate,
                    manager=new_manager,
                    agents=new_agents
                )
                new_departments.append(new_dept)
                
                # If there are extra departments in either list, we might want to include them
                # to preserve diversity, but strict crossover usually reduces or maintains count.
                # Let's include remaining departments from A and B as-is to ensure no loss of capability
                # unless we want strict reduction. The spec doesn't explicitly forbid keeping extras.
                # However, "Aligns... and performs allelic crossover" implies merging.
                # To be safe and preserve structure, we'll add the remaining ones as copies.
                for extra_a in list_a[1:]:
                    new_departments.append(extra_a.copy())
                for extra_b in list_b[1:]:
                    new_departments.append(extra_b.copy())
                    
            elif list_a:
                # Only in A
                for d in list_a:
                    new_departments.append(d.copy())
            elif list_b:
                # Only in B
                for d in list_b:
                    new_departments.append(d.copy())
        
        child.departments = new_departments
        
        # Ensure at least one department exists (schema requirement)
        if not child.departments:
            # Fallback if both parents had empty departments (shouldn't happen)
            child.departments = [DepartmentGenome(
                dept_id="dept_fallback",
                name="Fallback",
                mandate="Fallback",
                manager=AgentGenome(role="Manager", goal="Manage", backstory="Fallback")
            )]

        return child