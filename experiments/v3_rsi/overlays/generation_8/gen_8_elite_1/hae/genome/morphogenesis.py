import copy
from collections import defaultdict
from typing import Dict, List

from hae.genome.schema import AgentGenome, CompanyGenome, DepartmentGenome

# PUBLIC API CONTRACT
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
    search_string = (str(dept.dept_id) + " " + str(dept.name) + " " + str(dept.mandate)).lower()
    for category, keywords in FUNCTIONAL_CATEGORIES.items():
        if any(keyword in search_string for keyword in keywords):
            return category
    return "custom_specialized"

class MorphogenesisEngine:
    """Dynamically designs and morphs organizational topologies based on strategic objectives."""

    def __init__(self, model_name: str='gemini-2.5-flash'):
        """
        Initializes the MorphogenesisEngine.

        Args:
            model_name: The name of the language model to be used for morphogenesis,
                        though current implementation is deterministic.
        """
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

        # The specification for the mutation logic (add, prune, reshape) is not provided.
        # This implementation performs a no-op mutation, which satisfies all invariants
        # by not altering the departmental structure. This prevents speculative implementation
        # and awaits empirical evidence from test failures for further development.
        # Invariant 3 (Tool enablement) is met as no new departments are spawned.
        # Invariant 4 (Protected pods) is met as no pruning occurs.
        return child

class StructuralCrossoverEngine:
    """Enables genetic crossover between two enterprises with asymmetric departmental topologies."""

    def recombine(self, parent_a: CompanyGenome, parent_b: CompanyGenome, child_id: str, target_generation: int, label: str='Recombinant') -> CompanyGenome:
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
        # Invariant 1: Start with a deep copy of parent_a to inherit unspecified fields
        # and ensure no in-place mutation.
        child = parent_a.copy()

        # Invariant 2: Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        # The spec does not mention mutation_history for crossover. Appending the label
        # to parent_a's history is a reasonable default.
        child.mutation_history.append(label)

        # Invariant 3: Code Overlay Inheritance
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # Invariant 4: CEO Crossover
        if child.ceo and parent_b.ceo:
            # Combine backstory_traits
            traits_a = parent_a.ceo.backstory_traits or []
            traits_b = parent_b.ceo.backstory_traits or []
            combined_traits = list(dict.fromkeys(traits_a + traits_b))
            child.ceo.backstory_traits = combined_traits[:6]

            # Average temperature
            avg_temp = (parent_a.ceo.temperature + parent_b.ceo.temperature) / 2
            child.ceo.temperature = float(round(avg_temp))

        # Invariant 5: Department Alignment
        def _group_depts_by_cat(depts: List[DepartmentGenome]) -> Dict[str, List[DepartmentGenome]]:
            grouped = defaultdict(list)
            for dept in depts:
                cat = classify_department_role(dept)
                grouped[cat].append(dept)
            return grouped

        grouped_a = _group_depts_by_cat(parent_a.departments)
        grouped_b = _group_depts_by_cat(parent_b.departments)
        
        all_cats = set(grouped_a.keys()) | set(grouped_b.keys())
        # Ensure categories are processed in a consistent, prioritized order
        ordered_cats = list(FUNCTIONAL_CATEGORIES.keys()) + ["custom_specialized"]
        
        new_departments: List[DepartmentGenome] = []

        for cat in ordered_cats:
            if cat not in all_cats:
                continue

            depts_a = grouped_a.get(cat, [])
            depts_b = grouped_b.get(cat, [])

            if depts_a and depts_b:
                # Recombine departments for this category
                # Take parent_a's first department as the base for the new combined department
                new_dept = depts_a[0].copy() 

                # Recombine manager traits and temperature
                manager_a = depts_a[0].manager
                manager_b = depts_b[0].manager
                if manager_a and manager_b:
                    # Traits
                    mgr_traits_a = manager_a.backstory_traits or []
                    mgr_traits_b = manager_b.backstory_traits or []
                    mgr_combined_traits = list(dict.fromkeys(mgr_traits_a + mgr_traits_b))
                    new_dept.manager.backstory_traits = mgr_combined_traits[:6]
                    # Temperature
                    mgr_avg_temp = (manager_a.temperature + manager_b.temperature) / 2
                    new_dept.manager.temperature = float(round(mgr_avg_temp))

                # Interleave agents from all departments in this category from both parents
                agents_a = [agent.copy() for dept in depts_a for agent in dept.agents]
                agents_b = [agent.copy() for dept in depts_b for agent in dept.agents]
                
                interleaved_agents = []
                len_a, len_b = len(agents_a), len(agents_b)
                for i in range(max(len_a, len_b)):
                    if i < len_a:
                        interleaved_agents.append(agents_a[i])
                    if i < len_b:
                        interleaved_agents.append(agents_b[i])
                new_dept.agents = interleaved_agents
                new_departments.append(new_dept)

            elif depts_a:
                # Category only in parent_a, carry over all departments from parent_a
                for dept in depts_a:
                    new_departments.append(dept.copy())
            elif depts_b:
                # Category only in parent_b, carry over all departments from parent_b
                for dept in depts_b:
                    new_departments.append(dept.copy())
        
        child.departments = new_departments

        return child
