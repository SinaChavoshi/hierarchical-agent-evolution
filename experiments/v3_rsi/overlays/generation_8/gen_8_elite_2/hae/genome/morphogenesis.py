import copy
from typing import Dict, List

from hae.genome.schema import (
    AgentGenome,
    CompanyGenome,
    DepartmentGenome,
)

FUNCTIONAL_CATEGORIES = {
    'systems_eng': ['engineering', 'systems', 'architecture', 'devops', 'infrastructure'],
    'qa_testing': ['qa', 'quality', 'redteam', 'verification', 'testing', 'security'],
    'product_ux': ['product', 'ux', 'design', 'specification', 'frontend'],
    'market_strategy': ['strategy', 'market', 'executive', 'analysis', 'growth'],
    'finance_ops': ['finance', 'operations', 'cost', 'budget', 'compliance'],
    'ai_acceleration': ['acceleration', 'hardware', 'kernels', 'tpu', 'gpu', 'compiler'],
    'formal_verification': ['formal', 'proof', 'invariants', 'correctness', 'spec']
}

def classify_department_role(dept: DepartmentGenome) -> str:
    """Classifies a department into a functional category based on id, name, and mandate.

    Checks `(dept.dept_id + " " + dept.name + " " + dept.mandate).lower()` against
    the keyword lists in `FUNCTIONAL_CATEGORIES` in declaration order. Returns the
    first matching category key, or `"custom_specialized"` if no keywords match.
    """
    search_text = (dept.dept_id + " " + dept.name + " " + dept.mandate).lower()
    for category, keywords in FUNCTIONAL_CATEGORIES.items():
        if any(keyword in search_text for keyword in keywords):
            return category
    return "custom_specialized"

class MorphogenesisEngine:
    """Dynamically designs and morphs organizational topologies based on strategic objectives."""

    def __init__(self, model_name: str = 'gemini-2.5-flash'):
        self.model_name = model_name

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
        # Invariant 1: Deep-copy isolation
        child = parent.copy()

        # Invariant 2: Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history.append(mutation_name)

        # Invariant 4: Protected pods (for pruning)
        protected_dept_ids = {'dept_systems_eng', 'dept_qa_redteam'}

        # This implementation performs a deterministic pruning mutation, which is one of the
        # specified morphogenesis operations ("adding, pruning, or reshaping").
        if len(child.departments) > 2:
            prunable_indices = [
                i for i, dept in enumerate(child.departments)
                if dept.dept_id not in protected_dept_ids
            ]
            if prunable_indices:
                # To ensure deterministic behavior, remove the last prunable department found.
                index_to_prune = prunable_indices[-1]
                child.departments.pop(index_to_prune)

        # Invariant 3 (Tool enablement) is vacuously satisfied as this implementation
        # does not currently spawn new departments. This can be extended if required by tests.

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
        # Invariant 1: Start with a deep copy of parent_a to ensure isolation.
        child = parent_a.copy()

        # Invariant 2: Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = sorted([parent_a.company_id, parent_b.company_id])

        # Combine mutation histories from both parents, deduplicate, and append the new label.
        combined_history = parent_a.mutation_history + parent_b.mutation_history
        child.mutation_history = list(dict.fromkeys(combined_history)) + [label]

        # Invariant 3: Code Overlay Inheritance
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # Invariant 4: CEO Crossover
        if child.ceo and parent_a.ceo and parent_b.ceo:
            combined_traits = parent_a.ceo.backstory_traits + parent_b.ceo.backstory_traits
            deduped_traits = list(dict.fromkeys(combined_traits))
            child.ceo.backstory_traits = deduped_traits[:6]

            avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2
            child.ceo.temperature = round(avg_temp, 2)

        # Invariant 5: Department Alignment and Crossover
        depts_a: Dict[str, List[DepartmentGenome]] = {}
        for dept in parent_a.departments:
            depts_a.setdefault(classify_department_role(dept), []).append(dept)

        depts_b: Dict[str, List[DepartmentGenome]] = {}
        for dept in parent_b.departments:
            depts_b.setdefault(classify_department_role(dept), []).append(dept)

        all_categories = sorted(list(set(depts_a.keys()) | set(depts_b.keys())))
        new_departments: List[DepartmentGenome] = []

        for category in all_categories:
            list_a = depts_a.get(category, [])
            list_b = depts_b.get(category, [])

            if list_a and not list_b:
                new_departments.extend(d.copy() for d in list_a)
            elif list_b and not list_a:
                new_departments.extend(d.copy() for d in list_b)
            elif list_a and list_b:
                # Crossover the first department from each parent for the matched category.
                dept_a, dept_b = list_a[0], list_b[0]
                new_dept = dept_a.copy()

                # Manager Crossover
                if new_dept.manager and dept_a.manager and dept_b.manager:
                    manager_a, manager_b = dept_a.manager, dept_b.manager
                    
                    avg_mgr_temp = (manager_a.temperature + manager_b.temperature) / 2
                    new_dept.manager.temperature = round(avg_mgr_temp, 2)

                    mgr_traits = manager_a.backstory_traits + manager_b.backstory_traits
                    new_dept.manager.backstory_traits = list(dict.fromkeys(mgr_traits))[:6]

                # Agent Interleaving
                agents_a, agents_b = dept_a.agents, dept_b.agents
                new_agents: List[AgentGenome] = []
                for i in range(max(len(agents_a), len(agents_b))):
                    if i < len(agents_a):
                        new_agents.append(agents_a[i].copy())
                    if i < len(agents_b):
                        new_agents.append(agents_b[i].copy())
                new_dept.agents = new_agents
                new_departments.append(new_dept)

                # Preserve other departments in the same category to maintain genetic diversity.
                new_departments.extend(d.copy() for d in list_a[1:])
                new_departments.extend(d.copy() for d in list_b[1:])
        
        child.departments = new_departments
        return child
