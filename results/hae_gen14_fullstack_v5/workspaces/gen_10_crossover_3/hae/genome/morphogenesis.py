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
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec'],
}

# Departments that must never be pruned
PROTECTED_DEPT_IDS = {'dept_systems_eng', 'dept_qa_redteam'}

# Technical departments that require tool enablement
TECHNICAL_DEPARTMENTS = {'formal_verification', 'systems_eng', 'qa_testing', 'ai_acceleration'}


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
        # Deep copy to ensure isolation
        child = parent.copy()
        
        # Set lineage information
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history) + [mutation_name]
        
        # Ensure code_overlays is an independent copy (already handled by deep copy, 
        # but explicit for clarity)
        child.code_overlays = copy.deepcopy(parent.code_overlays)
        
        # Apply mutation logic based on mutation_name
        # This is a simplified implementation; real implementation would use LLM to determine
        # specific structural changes
        if "prune" in mutation_name.lower():
            # Prune non-protected departments, keeping at least 2
            protected_depts = [d for d in child.departments if d.dept_id in PROTECTED_DEPT_IDS]
            other_depts = [d for d in child.departments if d.dept_id not in PROTECTED_DEPT_IDS]
            
            # Keep protected departments and enough others to maintain minimum of 2
            if len(protected_depts) >= 2:
                child.departments = protected_depts[:2]
            else:
                # Keep all protected and add others to reach minimum of 2
                child.departments = protected_depts + other_depts[:2 - len(protected_depts)]
        
        elif "add" in mutation_name.lower() or "spawn" in mutation_name.lower():
            # Add a new technical department if not already present
            existing_categories = {classify_department_role(d) for d in child.departments}
            for tech_dept in TECHNICAL_DEPARTMENTS:
                if tech_dept not in existing_categories:
                    # Create a new department with this category
                    new_dept = DepartmentGenome(
                        dept_id=f"dept_{tech_dept}",
                        name=f"{tech_dept.replace('_', ' ').title()} Department",
                        mandate=f"Specialized {tech_dept.replace('_', ' ')} operations",
                        manager=AgentGenome(
                            role=f"{tech_dept.replace('_', ' ').title()} Manager",
                            goal=f"Lead {tech_dept.replace('_', ' ')} initiatives",
                            backstory="Experienced leader in technical operations",
                            model_tier="executive",
                            tools_enabled=True
                        ),
                        agents=[
                            AgentGenome(
                                role=f"{tech_dept.replace('_', ' ').title()} Specialist",
                                goal=f"Execute {tech_dept.replace('_', ' ')} tasks",
                                backstory="Skilled practitioner",
                                model_tier="worker",
                                tools_enabled=True
                            )
                        ]
                    )
                    child.departments.append(new_dept)
                    break
        
        # Ensure minimum of 2 departments
        if len(child.departments) < 2:
            # Add a generic department if needed
            if len(child.departments) == 0:
                child.departments = [
                    DepartmentGenome(
                        dept_id="dept_general_ops",
                        name="General Operations",
                        mandate="Core operational functions",
                        manager=AgentGenome(
                            role="Operations Manager",
                            goal="Manage general operations",
                            backstory="Versatile operations leader",
                            model_tier="executive",
                            tools_enabled=True
                        ),
                        agents=[
                            AgentGenome(
                                role="Operations Specialist",
                                goal="Execute operational tasks",
                                backstory="Generalist practitioner",
                                model_tier="worker",
                                tools_enabled=True
                            )
                        ]
                    ),
                    DepartmentGenome(
                        dept_id="dept_strategy",
                        name="Strategy & Planning",
                        mandate="Strategic direction and planning",
                        manager=AgentGenome(
                            role="Strategy Director",
                            goal="Drive strategic initiatives",
                            backstory="Strategic thinker",
                            model_tier="executive",
                            tools_enabled=True
                        ),
                        agents=[
                            AgentGenome(
                                role="Strategy Analyst",
                                goal="Analyze market and strategy",
                                backstory="Analytical specialist",
                                model_tier="worker",
                                tools_enabled=True
                            )
                        ]
                    )
                ]
            else:
                # Add one more department
                child.departments.append(
                    DepartmentGenome(
                        dept_id="dept_support",
                        name="Support Services",
                        mandate="Support and auxiliary functions",
                        manager=AgentGenome(
                            role="Support Lead",
                            goal="Provide support services",
                            backstory="Support specialist",
                            model_tier="executive",
                            tools_enabled=True
                        ),
                        agents=[
                            AgentGenome(
                                role="Support Specialist",
                                goal="Deliver support",
                                backstory="Helpful practitioner",
                                model_tier="worker",
                                tools_enabled=True
                            )
                        ]
                    )
                )
        
        # Ensure protected departments are present
        existing_dept_ids = {d.dept_id for d in child.departments}
        for protected_id in PROTECTED_DEPT_IDS:
            if protected_id not in existing_dept_ids:
                # Add protected department if missing
                if protected_id == "dept_systems_eng":
                    child.departments.append(
                        DepartmentGenome(
                            dept_id="dept_systems_eng",
                            name="Systems Engineering",
                            mandate="Core systems and infrastructure engineering",
                            manager=AgentGenome(
                                role="Systems Engineering Lead",
                                goal="Lead systems engineering efforts",
                                backstory="Senior systems engineer",
                                model_tier="executive",
                                tools_enabled=True
                            ),
                            agents=[
                                AgentGenome(
                                    role="Systems Engineer",
                                    goal="Build and maintain systems",
                                    backstory="Skilled systems engineer",
                                    model_tier="worker",
                                    tools_enabled=True
                                )
                            ]
                        )
                    )
                elif protected_id == "dept_qa_redteam":
                    child.departments.append(
                        DepartmentGenome(
                            dept_id="dept_qa_redteam",
                            name="QA & Red Team",
                            mandate="Quality assurance and security testing",
                            manager=AgentGenome(
                                role="QA & Security Lead",
                                goal="Ensure quality and security",
                                backstory="QA and security expert",
                                model_tier="executive",
                                tools_enabled=True
                            ),
                            agents=[
                                AgentGenome(
                                    role="QA Engineer",
                                    goal="Test and verify quality",
                                    backstory="QA specialist",
                                    model_tier="worker",
                                    tools_enabled=True
                                )
                            ]
                        )
                    )
        
        # Enable tools for all agents in technical departments
        for dept in child.departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_DEPARTMENTS:
                if dept.manager:
                    dept.manager.tools_enabled = True
                for agent in dept.agents:
                    agent.tools_enabled = True
        
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
        # Deep copy parents to ensure isolation
        pa = parent_a.copy()
        pb = parent_b.copy()
        
        # CEO Crossover
        if pa.ceo and pb.ceo:
            # Combine and deduplicate backstory traits, preserving order, capped at 6
            combined_traits = []
            seen = set()
            for trait in pa.ceo.backstory_traits + pb.ceo.backstory_traits:
                if trait not in seen:
                    combined_traits.append(trait)
                    seen.add(trait)
                    if len(combined_traits) >= 6:
                        break
            
            # Average temperature
            avg_temp = round((pa.ceo.temperature + pb.ceo.temperature) / 2.0, 2)
            
            child_ceo = AgentGenome(
                role=pa.ceo.role,
                goal=pa.ceo.goal,
                backstory=pa.ceo.backstory,
                backstory_traits=combined_traits,
                temperature=avg_temp,
                model_tier=pa.ceo.model_tier,
                tools_enabled=pa.ceo.tools_enabled or pb.ceo.tools_enabled,
                system_instructions=pa.ceo.system_instructions
            )
        elif pa.ceo:
            child_ceo = pa.ceo.copy()
        elif pb.ceo:
            child_ceo = pb.ceo.copy()
        else:
            # Fallback CEO
            child_ceo = AgentGenome(
                role="CEO",
                goal="Lead the organization",
                backstory="Recombinant leader",
                backstory_traits=["Adaptive", "Strategic"],
                temperature=0.7,
                model_tier="executive",
                tools_enabled=True
            )
        
        # Department Alignment and Crossover
        # Group departments by functional category
        pa_by_category: Dict[str, List[DepartmentGenome]] = {}
        pb_by_category: Dict[str, List[DepartmentGenome]] = {}
        
        for dept in pa.departments:
            cat = classify_department_role(dept)
            pa_by_category.setdefault(cat, []).append(dept)
        
        for dept in pb.departments:
            cat = classify_department_role(dept)
            pb_by_category.setdefault(cat, []).append(dept)
        
        # Get all categories present in either parent
        all_categories = set(pa_by_category.keys()) | set(pb_by_category.keys())
        
        child_departments = []
        
        for category in all_categories:
            pa_depts = pa_by_category.get(category, [])
            pb_depts = pb_by_category.get(category, [])
            
            if pa_depts and pb_depts:
                # Both parents have departments in this category - perform crossover
                # Take the first department from each parent for simplicity
                dept_a = pa_depts[0]
                dept_b = pb_depts[0]
                
                # Crossover manager traits and temperature
                if dept_a.manager and dept_b.manager:
                    combined_manager_traits = []
                    seen = set()
                    for trait in dept_a.manager.backstory_traits + dept_b.manager.backstory_traits:
                        if trait not in seen:
                            combined_manager_traits.append(trait)
                            seen.add(trait)
                            if len(combined_manager_traits) >= 6:
                                break
                    
                    avg_manager_temp = round((dept_a.manager.temperature + dept_b.manager.temperature) / 2.0, 2)
                    
                    new_manager = AgentGenome(
                        role=dept_a.manager.role,
                        goal=dept_a.manager.goal,
                        backstory=dept_a.manager.backstory,
                        backstory_traits=combined_manager_traits,
                        temperature=avg_manager_temp,
                        model_tier=dept_a.manager.model_tier,
                        tools_enabled=dept_a.manager.tools_enabled or dept_b.manager.tools_enabled,
                        system_instructions=dept_a.manager.system_instructions
                    )
                elif dept_a.manager:
                    new_manager = dept_a.manager.copy()
                elif dept_b.manager:
                    new_manager = dept_b.manager.copy()
                else:
                    new_manager = AgentGenome(
                        role=f"{category.replace('_', ' ').title()} Manager",
                        goal=f"Lead {category.replace('_', ' ')} operations",
                        backstory="Recombinant manager",
                        backstory_traits=["Adaptive"],
                        temperature=0.7,
                        model_tier="executive",
                        tools_enabled=True
                    )
                
                # Interleave specialist agents
                all_agents = []
                max_agents = max(len(dept_a.agents), len(dept_b.agents))
                for i in range(max_agents):
                    if i < len(dept_a.agents):
                        all_agents.append(dept_a.agents[i].copy())
                    if i < len(dept_b.agents):
                        all_agents.append(dept_b.agents[i].copy())
                
                # Limit to reasonable number of agents
                all_agents = all_agents[:8]
                
                new_dept = DepartmentGenome(
                    dept_id=dept_a.dept_id,  # Use parent A's dept_id
                    name=dept_a.name,
                    mandate=dept_a.mandate,
                    manager=new_manager,
                    agents=all_agents,
                    delegation_rules=dept_a.delegation_rules
                )
                child_departments.append(new_dept)
                
            elif pa_depts:
                # Only parent A has departments in this category
                for dept in pa_depts:
                    child_departments.append(dept.copy())
            elif pb_depts:
                # Only parent B has departments in this category
                for dept in pb_depts:
                    child_departments.append(dept.copy())
        
        # Ensure at least 2 departments
        if len(child_departments) < 2:
            # Add generic departments if needed
            while len(child_departments) < 2:
                idx = len(child_departments)
                child_departments.append(
                    DepartmentGenome(
                        dept_id=f"dept_general_{idx}",
                        name=f"General Operations {idx}",
                        mandate="General operational functions",
                        manager=AgentGenome(
                            role=f"Operations Manager {idx}",
                            goal="Manage operations",
                            backstory="Operations leader",
                            model_tier="executive",
                            tools_enabled=True
                        ),
                        agents=[
                            AgentGenome(
                                role=f"Operations Specialist {idx}",
                                goal="Execute tasks",
                                backstory="Practitioner",
                                model_tier="worker",
                                tools_enabled=True
                            )
                        ]
                    )
                )
        
        # Create child genome with all required fields
        child = CompanyGenome(
            company_id=child_id,
            generation=target_generation,
            parent_ids=[parent_a.company_id, parent_b.company_id],
            mutation_history=[f"Crossover: {label}"],
            ceo=child_ceo,
            departments=child_departments,
            executive_deliberation_rules=pa.executive_deliberation_rules,
            budget_usd=(pa.budget_usd + pb.budget_usd) / 2.0,
            code_overlays={**pb.code_overlays, **pa.code_overlays},
            extra={}
        )
        
        return child