"""Autonomous Morphogenesis & Dynamic Organizational Topologies Engine (Generation 9).

Synthesizes custom enterprise topologies and enables structural allelic crossover across
asymmetric organizational hierarchies.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from hae.genome.schema import (
    AgentGenome,
    CompanyGenome,
    DepartmentGenome,
    GenomeValidationError,
)

# Functional categories for department classification.
# Order matters: first match wins.
FUNCTIONAL_CATEGORIES = {
    'systems_eng': ['engineering', 'systems', 'architecture', 'devops', 'infrastructure'],
    'qa_testing': ['qa', 'quality', 'redteam', 'verification', 'testing', 'security'],
    'product_ux': ['product', 'ux', 'design', 'specification', 'frontend'],
    'market_strategy': ['strategy', 'market', 'executive', 'analysis', 'growth'],
    'finance_ops': ['finance', 'operations', 'cost', 'budget', 'compliance'],
    'ai_acceleration': ['acceleration', 'hardware', 'kernels', 'tpu', 'gpu', 'compiler'],
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec'],
}

# Departments that must never be pruned.
PROTECTED_DEPT_IDS = {'dept_systems_eng', 'dept_qa_redteam'}

# Technical categories that require tools_enabled=True on spawned agents.
TECHNICAL_CATEGORIES = {'formal_verification', 'systems_eng', 'qa_testing', 'ai_acceleration'}


def classify_department_role(dept: DepartmentGenome) -> str:
    """Classifies a department into a functional category based on id, name, and mandate.

    Checks `(dept.dept_id + " " + dept.name + " " + dept.mandate).lower()` against
    the keyword lists in `FUNCTIONAL_CATEGORIES` in declaration order. Returns the
    first matching category key, or `"custom_specialized"` if no keywords match.
    """
    haystack = f"{dept.dept_id} {dept.name} {dept.mandate}".lower()
    for category, keywords in FUNCTIONAL_CATEGORIES.items():
        for keyword in keywords:
            if keyword in haystack:
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
        child_id: str,
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
        # Deep-copy the parent to ensure isolation.
        child = parent.copy()

        # Set lineage fields.
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history = list(parent.mutation_history) + [mutation_name]

        # Ensure code_overlays is an independent copy (already handled by deep copy,
        # but explicit for clarity).
        child.code_overlays = dict(parent.code_overlays)

        # Apply mutation logic based on mutation_name.
        # We implement a set of standard mutations. Unknown mutations are no-ops
        # beyond the lineage changes, which is safe.
        self._apply_mutation(child, mutation_name)

        # Enforce protected pods and minimum department count.
        self._enforce_invariants(child)

        return child

    def _apply_mutation(self, child: CompanyGenome, mutation_name: str) -> None:
        """Applies the named mutation to the child genome in-place."""
        mutation_lower = mutation_name.lower()

        if "add_formal_verification" in mutation_lower or "spawn_formal" in mutation_lower:
            self._spawn_department(
                child,
                dept_id="dept_formal_verification",
                name="Formal Verification Pod",
                mandate="Prove invariants and correctness properties of critical systems",
                category="formal_verification",
            )
        elif "add_ai_acceleration" in mutation_lower or "spawn_ai" in mutation_lower:
            self._spawn_department(
                child,
                dept_id="dept_ai_acceleration",
                name="AI Acceleration Pod",
                mandate="Optimize hardware kernels, TPU/GPU compilation, and inference latency",
                category="ai_acceleration",
            )
        elif "prune_redundant" in mutation_lower or "prune" in mutation_lower:
            self._prune_redundant_departments(child)
        elif "reshape" in mutation_lower or "restructure" in mutation_lower:
            self._reshape_departments(child)
        # Unknown mutations: no structural change beyond lineage.

    def _spawn_department(
        self,
        child: CompanyGenome,
        dept_id: str,
        name: str,
        mandate: str,
        category: str,
    ) -> None:
        """Spawns a new department with a manager and worker agents.

        If the category is technical, all worker agents get tools_enabled=True.
        """
        # Check if department already exists.
        existing_ids = {d.dept_id for d in child.departments}
        if dept_id in existing_ids:
            return

        manager = AgentGenome(
            role=f"{name} Manager",
            goal=f"Lead the {name} pod to fulfill its mandate",
            backstory=f"Experienced leader in {category.replace('_', ' ')}",
            backstory_traits=["strategic", "detail-oriented"],
            temperature=0.5,
            model_tier="executive",
            tools_enabled=False,
        )

        workers = []
        num_workers = 2
        for i in range(num_workers):
            worker = AgentGenome(
                role=f"{name} Specialist {i + 1}",
                goal=f"Execute tasks within the {name} pod",
                backstory=f"Skilled practitioner in {category.replace('_', ' ')}",
                backstory_traits=["technical", "collaborative"],
                temperature=0.7,
                model_tier="worker",
                tools_enabled=(category in TECHNICAL_CATEGORIES),
            )
            workers.append(worker)

        dept = DepartmentGenome(
            dept_id=dept_id,
            name=name,
            mandate=mandate,
            manager=manager,
            agents=workers,
        )
        child.departments.append(dept)

    def _prune_redundant_departments(self, child: CompanyGenome) -> None:
        """Prunes departments that are not protected and exceed the minimum count.

        Keeps at least 2 departments. Never prunes protected dept_ids.
        """
        if len(child.departments) <= 2:
            return

        # Identify prunable departments: not protected, and we need to keep at least 2.
        prunable = [
            d for d in child.departments
            if d.dept_id not in PROTECTED_DEPT_IDS
        ]

        # We want to keep at least 2 total. So we can prune at most len(departments) - 2.
        max_prune = len(child.departments) - 2
        if max_prune <= 0:
            return

        # Prune from the end of the prunable list (arbitrary but deterministic).
        to_prune = prunable[:max_prune]
        to_prune_ids = {d.dept_id for d in to_prune}

        child.departments = [
            d for d in child.departments
            if d.dept_id not in to_prune_ids
        ]

    def _reshape_departments(self, child: CompanyGenome) -> None:
        """Reshapes departments by merging small pods or splitting large ones.

        This is a simplified reshaping: if any department has more than 4 agents,
        split it into two. If any has fewer than 1 agent (just manager), merge with
        another non-protected department if possible.
        """
        # For simplicity, we just ensure no department is empty of workers if it's
        # not protected, and we don't do complex merges/splits in this stub.
        # A real implementation would use the LLM to decide reshaping.
        pass

    def _enforce_invariants(self, child: CompanyGenome) -> None:
        """Enforces structural invariants on the child genome."""
        # Ensure at least 2 departments.
        if len(child.departments) < 2:
            # If we have fewer than 2, we need to add one.
            # Add a generic custom department if needed.
            if len(child.departments) == 0:
                # This shouldn't happen if parent was valid, but guard anyway.
                self._spawn_department(
                    child,
                    dept_id="dept_general_ops",
                    name="General Operations",
                    mandate="Handle general operational tasks",
                    category="custom_specialized",
                )
            if len(child.departments) < 2:
                self._spawn_department(
                    child,
                    dept_id="dept_strategy",
                    name="Strategy & Growth",
                    mandate="Drive market strategy and growth initiatives",
                    category="market_strategy",
                )

        # Ensure protected departments exist. If they were somehow pruned, re-add them.
        existing_ids = {d.dept_id for d in child.departments}
        if "dept_systems_eng" not in existing_ids:
            self._spawn_department(
                child,
                dept_id="dept_systems_eng",
                name="Systems Engineering",
                mandate="Build and maintain core systems architecture",
                category="systems_eng",
            )
        if "dept_qa_redteam" not in existing_ids:
            self._spawn_department(
                child,
                dept_id="dept_qa_redteam",
                name="QA & Red Team",
                mandate="Ensure quality, security, and adversarial testing",
                category="qa_testing",
            )

        # Ensure tools_enabled is set correctly for technical departments.
        for dept in child.departments:
            category = classify_department_role(dept)
            if category in TECHNICAL_CATEGORIES:
                for agent in dept.agents:
                    if agent.model_tier == "worker":
                        agent.tools_enabled = True


class StructuralCrossoverEngine:
    """Enables genetic crossover between two enterprises with asymmetric departmental topologies."""

    def recombine(
        self,
        parent_a: CompanyGenome,
        parent_b: CompanyGenome,
        child_id: str,
        target_generation: int,
        label: str = 'Recombinant',
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
        # Deep-copy parents to avoid mutation.
        pa = parent_a.copy()
        pb = parent_b.copy()

        # Build child from scratch to ensure clean state.
        child = CompanyGenome(
            company_id=child_id,
            generation=target_generation,
            parent_ids=[parent_a.company_id, parent_b.company_id],
            mutation_history=[f"Crossover:{label}"],
            ceo=None,  # Will be set below.
            departments=[],
            executive_deliberation_rules=pa.executive_deliberation_rules,
            budget_usd=(pa.budget_usd + pb.budget_usd) / 2.0,
            code_overlays={},
        )

        # 3. Code Overlay Inheritance: parent_a takes precedence.
        child.code_overlays = {**pb.code_overlays, **pa.code_overlays}

        # 4. CEO Crossover.
        child.ceo = self._crossover_ceo(pa.ceo, pb.ceo)

        # 5. Department Alignment and Crossover.
        child.departments = self._crossover_departments(pa.departments, pb.departments)

        # Ensure at least 2 departments (invariant from morphogenesis, good practice).
        if len(child.departments) < 2:
            # Fallback: add generic departments if crossover produced too few.
            if len(child.departments) == 0:
                child.departments = [
                    DepartmentGenome(
                        dept_id="dept_general_ops",
                        name="General Operations",
                        mandate="Handle general operational tasks",
                        manager=AgentGenome(
                            role="General Ops Manager",
                            goal="Lead general operations",
                            backstory="Experienced generalist",
                            backstory_traits=["adaptable"],
                            temperature=0.6,
                            model_tier="executive",
                        ),
                        agents=[
                            AgentGenome(
                                role="General Ops Specialist",
                                goal="Execute general tasks",
                                backstory="Versatile practitioner",
                                backstory_traits=["flexible"],
                                temperature=0.7,
                                model_tier="worker",
                            )
                        ],
                    ),
                    DepartmentGenome(
                        dept_id="dept_strategy",
                        name="Strategy & Growth",
                        mandate="Drive market strategy and growth",
                        manager=AgentGenome(
                            role="Strategy Manager",
                            goal="Lead strategy pod",
                            backstory="Strategic thinker",
                            backstory_traits=["analytical"],
                            temperature=0.5,
                            model_tier="executive",
                        ),
                        agents=[
                            AgentGenome(
                                role="Strategy Specialist",
                                goal="Execute strategy tasks",
                                backstory="Market analyst",
                                backstory_traits=["insightful"],
                                temperature=0.7,
                                model_tier="worker",
                            )
                        ],
                    ),
                ]
            elif len(child.departments) == 1:
                # Add a second department.
                child.departments.append(
                    DepartmentGenome(
                        dept_id="dept_strategy",
                        name="Strategy & Growth",
                        mandate="Drive market strategy and growth",
                        manager=AgentGenome(
                            role="Strategy Manager",
                            goal="Lead strategy pod",
                            backstory="Strategic thinker",
                            backstory_traits=["analytical"],
                            temperature=0.5,
                            model_tier="executive",
                        ),
                        agents=[
                            AgentGenome(
                                role="Strategy Specialist",
                                goal="Execute strategy tasks",
                                backstory="Market analyst",
                                backstory_traits=["insightful"],
                                temperature=0.7,
                                model_tier="worker",
                            )
                        ],
                    )
                )

        return child

    def _crossover_ceo(self, ceo_a: AgentGenome, ceo_b: AgentGenome) -> AgentGenome:
        """Combines CEO traits and temperature."""
        # Combine and deduplicate backstory traits, preserving order, capped at 6.
        traits_a = list(ceo_a.backstory_traits)
        traits_b = list(ceo_b.backstory_traits)
        combined = []
        seen = set()
        for t in traits_a + traits_b:
            if t not in seen:
                combined.append(t)
                seen.add(t)
        combined = combined[:6]

        # Average temperature, rounded to 2 decimal places.
        avg_temp = round((ceo_a.temperature + ceo_b.temperature) / 2.0, 2)

        # Pick role/goal/backstory from parent_a (arbitrary but deterministic).
        return AgentGenome(
            role=ceo_a.role,
            goal=ceo_a.goal,
            backstory=ceo_a.backstory,
            backstory_traits=combined,
            temperature=avg_temp,
            model_tier="executive",
            tools_enabled=False,
            system_instructions=ceo_a.system_instructions,
        )

    def _crossover_departments(
        self,
        depts_a: List[DepartmentGenome],
        depts_b: List[DepartmentGenome],
    ) -> List[DepartmentGenome]:
        """Aligns departments by functional category and performs crossover."""
        # Group departments by category.
        groups_a: Dict[str, List[DepartmentGenome]] = {}
        for d in depts_a:
            cat = classify_department_role(d)
            groups_a.setdefault(cat, []).append(d)

        groups_b: Dict[str, List[DepartmentGenome]] = {}
        for d in depts_b:
            cat = classify_department_role(d)
            groups_b.setdefault(cat, []).append(d)

        # Collect all categories present in either parent.
        all_categories = set(groups_a.keys()) | set(groups_b.keys())

        child_depts: List[DepartmentGenome] = []
        dept_counter = 0

        for cat in sorted(all_categories):
            a_depts = groups_a.get(cat, [])
            b_depts = groups_b.get(cat, [])

            if a_depts and b_depts:
                # Both parents have departments in this category.
                # Pair them up for crossover.
                max_pairs = max(len(a_depts), len(b_depts))
                for i in range(max_pairs):
                    da = a_depts[i] if i < len(a_depts) else None
                    db = b_depts[i] if i < len(b_depts) else None

                    if da and db:
                        # Full crossover.
                        child_dept = self._crossover_department_pair(da, db, dept_counter)
                        child_depts.append(child_dept)
                        dept_counter += 1
                    elif da:
                        # Only parent_a has this department; copy it.
                        child_dept = da.copy()
                        child_dept.dept_id = f"dept_{cat}_{dept_counter}"
                        child_depts.append(child_dept)
                        dept_counter += 1
                    elif db:
                        # Only parent_b has this department; copy it.
                        child_dept = db.copy()
                        child_dept.dept_id = f"dept_{cat}_{dept_counter}"
                        child_depts.append(child_dept)
                        dept_counter += 1

            elif a_depts:
                # Only parent_a has departments in this category.
                for da in a_depts:
                    child_dept = da.copy()
                    child_dept.dept_id = f"dept_{cat}_{dept_counter}"
                    child_depts.append(child_dept)
                    dept_counter += 1

            elif b_depts:
                # Only parent_b has departments in this category.
                for db in b_depts:
                    child_dept = db.copy()
                    child_dept.dept_id = f"dept_{cat}_{dept_counter}"
                    child_depts.append(child_dept)
                    dept_counter += 1

        return child_depts

    def _crossover_department_pair(
        self,
        da: DepartmentGenome,
        db: DepartmentGenome,
        index: int,
    ) -> DepartmentGenome:
        """Performs allelic crossover between two departments of the same category."""
        # Crossover manager: combine traits and average temperature.
        manager = self._crossover_agent(da.manager, db.manager, is_executive=True)

        # Interleave specialist agents.
        agents_a = list(da.agents)
        agents_b = list(db.agents)
        interleaved = []
        max_agents = max(len(agents_a), len(agents_b))
        for i in range(max_agents):
            if i < len(agents_a):
                interleaved.append(agents_a[i].copy())
            if i < len(agents_b):
                interleaved.append(agents_b[i].copy())

        # Ensure tools_enabled is correct for technical categories.
        cat = classify_department_role(da)
        if cat in TECHNICAL_CATEGORIES:
            for agent in interleaved:
                if agent.model_tier == "worker":
                    agent.tools_enabled = True

        # Generate a new dept_id.
        new_dept_id = f"dept_{cat}_{index}"

        return DepartmentGenome(
            dept_id=new_dept_id,
            name=da.name,  # Take name from parent_a.
            mandate=da.mandate,  # Take mandate from parent_a.
            manager=manager,
            agents=interleaved,
            delegation_rules=da.delegation_rules,
        )

    def _crossover_agent(
        self,
        agent_a: AgentGenome,
        agent_b: AgentGenome,
        is_executive: bool = False,
    ) -> AgentGenome:
        """Combines two agents' traits and averages temperature."""
        traits_a = list(agent_a.backstory_traits)
        traits_b = list(agent_b.backstory_traits)
        combined = []
        seen = set()
        for t in traits_a + traits_b:
            if t not in seen:
                combined.append(t)
                seen.add(t)
        combined = combined[:6]

        avg_temp = round((agent_a.temperature + agent_b.temperature) / 2.0, 2)

        return AgentGenome(
            role=agent_a.role,
            goal=agent_a.goal,
            backstory=agent_a.backstory,
            backstory_traits=combined,
            temperature=avg_temp,
            model_tier="executive" if is_executive else "worker",
            tools_enabled=agent_a.tools_enabled or agent_b.tools_enabled,
            system_instructions=agent_a.system_instructions,
        )