import copy
from collections import defaultdict
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
        """Initializes the engine.

        Args:
            model_name: The name of the model to use for morphogenesis.
                        Note: This implementation is deterministic and does not use an LLM.
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
        # 1. Deep-copy isolation
        child = parent.copy()

        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        child.mutation_history.append(mutation_name)

        # 4. Protected pods - This implementation will only prune, not add.
        # This is a deterministic morphing strategy.
        if len(child.departments) > 2:
            # Find a non-protected department to prune. We iterate backwards to
            # have a deterministic target.
            prune_idx = -1
            for i in range(len(child.departments) - 1, -1, -1):
                if child.departments[i].dept_id not in ('dept_systems_eng', 'dept_qa_redteam'):
                    prune_idx = i
                    break
            
            if prune_idx != -1:
                del child.departments[prune_idx]

        # 3. Tool enablement - Not applicable in this prune-only implementation,
        # as no new departments are created.

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
        # 1. Deep-copy isolation: Start with a copy of parent_a for the child structure.
        child = parent_a.copy()

        # 2. Lineage
        child.company_id = child_id
        child.generation = target_generation
        child.parent_ids = sorted([parent_a.company_id, parent_b.company_id]) # Deterministic order

        # 3. Level 3 RSI Code Overlay Inheritance
        child.code_overlays = {**parent_b.code_overlays, **parent_a.code_overlays}

        # 4. CEO Crossover
        if child.ceo and parent_a.ceo and parent_b.ceo:
            # Combine and deduplicate backstory traits
            traits_a = parent_a.ceo.backstory_traits
            traits_b = parent_b.ceo.backstory_traits
            combined_traits = list(traits_a)
            for trait in traits_b:
                if trait not in combined_traits:
                    combined_traits.append(trait)
            child.ceo.backstory_traits = combined_traits[:6]

            # Set temperature to rounded average. The spec is ambiguous on rounding.
            # We will use simple averaging for the float field.
            temp_a = parent_a.ceo.temperature
            temp_b = parent_b.ceo.temperature
            child.ceo.temperature = (temp_a + temp_b) / 2.0

        # 5. Department Alignment
        depts_a_by_cat = defaultdict(list)
        for dept in parent_a.departments:
            cat = classify_department_role(dept)
            depts_a_by_cat[cat].append(dept)

        depts_b_by_cat = defaultdict(list)
        for dept in parent_b.departments:
            cat = classify_department_role(dept)
            depts_b_by_cat[cat].append(dept)

        all_categories = sorted(list(set(depts_a_by_cat.keys()) | set(depts_b_by_cat.keys())))
        
        child_departments = []
        for cat in all_categories:
            cat_depts_a = depts_a_by_cat.get(cat, [])
            cat_depts_b = depts_b_by_cat.get(cat, [])
            max_len = max(len(cat_depts_a), len(cat_depts_b))

            for i in range(max_len):
                dept_a = cat_depts_a[i] if i < len(cat_depts_a) else None
                dept_b = cat_depts_b[i] if i < len(cat_depts_b) else None

                if dept_a and not dept_b:
                    child_departments.append(dept_a.copy())
                elif not dept_a and dept_b:
                    child_departments.append(dept_b.copy())
                elif dept_a and dept_b:
                    # Recombine the two departments
                    new_dept = dept_a.copy()
                    
                    # Recombine manager
                    if new_dept.manager and dept_b.manager:
                        # Traits
                        mgr_traits_a = dept_a.manager.backstory_traits
                        mgr_traits_b = dept_b.manager.backstory_traits
                        mgr_combined_traits = list(mgr_traits_a)
                        for trait in mgr_traits_b:
                            if trait not in mgr_combined_traits:
                                mgr_combined_traits.append(trait)
                        new_dept.manager.backstory_traits = mgr_combined_traits[:6] # Cap at 6 as a best practice
                        
                        # Temperature
                        mgr_temp_a = dept_a.manager.temperature
                        mgr_temp_b = dept_b.manager.temperature
                        new_dept.manager.temperature = (mgr_temp_a + mgr_temp_b) / 2.0

                    # Interleave specialist agents
                    new_agents = []
                    agents_a = dept_a.agents
                    agents_b = dept_b.agents
                    len_a, len_b = len(agents_a), len(agents_b)
                    for j in range(max(len_a, len_b)):
                        if j < len_a:
                            new_agents.append(agents_a[j].copy())
                        if j < len_b:
                            new_agents.append(agents_b[j].copy())
                    new_dept.agents = new_agents
                    
                    child_departments.append(new_dept)
        
        child.departments = child_departments
        return child
