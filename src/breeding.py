"""3-Way Evolutionary Breeding Engine (Consensus, Pareto Extremes, Directed Exploration)."""

import copy
import json
import random
import re
from typing import List, Dict, Any, Tuple
from .schema import CompanyGenome, DepartmentGenome, AgentGenome, EvaluationResult
from .llm_factory import call_vertex_gemini_rest

META_ARCHITECT_SYSTEM_PROMPT = """You are an Elite Meta-Architect and Evolutionary Systems Designer.
Your task is to review the collective bottlenecks, failure modes, and architectural strengths of the top-performing
virtual organizations in a competition, and formulate BOLD, HYPOTHESIS-DRIVEN STRUCTURAL MUTATIONS for next-generation firms.

You do not inject random noise. You formulate concrete structural hypotheses, such as:
- Creating specialized new roles to solve discovered bottlenecks (e.g. SRE Chaos Engineer, Regulatory Compliance Officer, OpenAPI Contract Architect).
- Modifying delegation rules from consensus to adversarial dialectic or milestone-gated review.
- Adjusting cognitive backstories to enforce first-principles thinking, inversion mental models, or strict unit economics.

Return ONLY valid JSON matching the CompanyGenome schema with a "mutation_hypothesis" field explaining the intended improvement."""

class ThreeWayBreedingEngine:
    """Implements the 50-firm evolutionary search pipeline across 3 balanced breeding archetypes."""

    def __init__(self, top_k: int = 5, total_population: int = 50):
        self.top_k = top_k
        self.total_population = total_population

    def extract_consensus_motifs(self, top_firms: List[Tuple[CompanyGenome, EvaluationResult]]) -> Dict[str, Any]:
        """Group A: Analyzes the top 5 winners to find common structural paradigms and invariant motifs."""
        # Extract common departments present across majority of winners
        dept_frequencies: Dict[str, int] = {}
        for firm, _ in top_firms:
            for dept in firm.departments:
                dept_frequencies[dept.dept_id] = dept_frequencies.get(dept.dept_id, 0) + 1

        shared_dept_ids = [k for k, v in dept_frequencies.items() if v >= (len(top_firms) * 0.6)]
        
        # Calculate mean temperatures across winners
        ceo_temps = [firm.ceo.temperature for firm, _ in top_firms]
        mean_ceo_temp = sum(ceo_temps) / max(1, len(ceo_temps))

        return {
            "shared_department_ids": shared_dept_ids,
            "mean_ceo_temp": round(mean_ceo_temp, 2),
            "common_deliberation_rule": top_firms[0][0].executive_deliberation_rules
        }

    def extract_consensus_traits(self, top_firms: List[Tuple[CompanyGenome, EvaluationResult]]) -> Dict[str, List[str]]:
        """Extracts high-frequency trait alleles across corresponding roles in the winning firms."""
        role_traits: Dict[str, Dict[str, int]] = {}
        for firm, _ in top_firms:
            # CEO traits
            ceo_role = firm.ceo.role
            role_traits.setdefault(ceo_role, {})
            for t in getattr(firm.ceo, "backstory_traits", []) or []:
                role_traits[ceo_role][t] = role_traits[ceo_role].get(t, 0) + 1
            
            # Department agents traits
            for dept in firm.departments:
                mgr_role = dept.manager.role
                role_traits.setdefault(mgr_role, {})
                for t in getattr(dept.manager, "backstory_traits", []) or []:
                    role_traits[mgr_role][t] = role_traits[mgr_role].get(t, 0) + 1
                for a in dept.agents:
                    role_traits.setdefault(a.role, {})
                    for t in getattr(a, "backstory_traits", []) or []:
                        role_traits[a.role][t] = role_traits[a.role].get(t, 0) + 1

        consensus_traits: Dict[str, List[str]] = {}
        threshold = max(1, int(len(top_firms) * 0.4))
        for role, trait_counts in role_traits.items():
            consensus_traits[role] = [t for t, count in trait_counts.items() if count >= threshold]

        return consensus_traits

    def crossover_agent_traits(self, parent_a_traits: List[str], parent_b_traits: List[str]) -> List[str]:
        """Performs allelic crossover between two trait sets, interleaving non-redundant operational axioms."""
        combined = []
        seen = set()
        max_len = max(len(parent_a_traits or []), len(parent_b_traits or []))
        for i in range(max_len):
            if parent_a_traits and i < len(parent_a_traits) and parent_a_traits[i] not in seen:
                combined.append(parent_a_traits[i])
                seen.add(parent_a_traits[i])
            if parent_b_traits and i < len(parent_b_traits) and parent_b_traits[i] not in seen:
                combined.append(parent_b_traits[i])
                seen.add(parent_b_traits[i])
        return combined[:6]

    def breed_consensus_offspring(
        self,
        top_firms: List[Tuple[CompanyGenome, EvaluationResult]],
        consensus_motifs: Dict[str, Any],
        count: int,
        target_generation: int
    ) -> List[CompanyGenome]:
        """Group A (Consensus): Generates offspring that preserve and reinforce shared winning motifs and consensus traits."""
        consensus_traits = self.extract_consensus_traits(top_firms)
        offspring: List[CompanyGenome] = []
        for i in range(count):
            parent_a = random.choice(top_firms)[0]
            parent_b = random.choice(top_firms)[0]
            child = copy.deepcopy(parent_a)
            child_id = f"gen_{target_generation}_consensus_{i+1}"
            child.company_id = child_id
            child.generation = target_generation
            child.parent_ids = [parent_a.company_id, parent_b.company_id]
            child.ceo.temperature = max(0.2, min(1.0, consensus_motifs["mean_ceo_temp"] + random.uniform(-0.05, 0.05)))
            
            # Crossover CEO traits
            child.ceo.backstory_traits = self.crossover_agent_traits(
                getattr(parent_a.ceo, "backstory_traits", []) or [],
                getattr(parent_b.ceo, "backstory_traits", []) or []
            )
            # Add role consensus traits if available
            for ct in consensus_traits.get(child.ceo.role, []):
                if ct not in child.ceo.backstory_traits and len(child.ceo.backstory_traits) < 6:
                    child.ceo.backstory_traits.append(ct)

            # Crossover departmental traits
            for d_idx, dept in enumerate(child.departments):
                b_dept = parent_b.departments[d_idx] if d_idx < len(parent_b.departments) else None
                if b_dept:
                    dept.manager.backstory_traits = self.crossover_agent_traits(
                        getattr(dept.manager, "backstory_traits", []) or [],
                        getattr(b_dept.manager, "backstory_traits", []) or []
                    )
                    for a_idx, agent in enumerate(dept.agents):
                        b_agent = b_dept.agents[a_idx] if a_idx < len(b_dept.agents) else None
                        if b_agent:
                            agent.backstory_traits = self.crossover_agent_traits(
                                getattr(agent, "backstory_traits", []) or [],
                                getattr(b_agent, "backstory_traits", []) or []
                            )

            # Enforce shared department invariants
            child.mutation_history.append(
                f"Gen {target_generation} Consensus: Recombined traits from {parent_a.company_id} & {parent_b.company_id}"
            )
            offspring.append(child)
        return offspring

    def breed_pareto_extremes(
        self,
        top_firms: List[Tuple[CompanyGenome, EvaluationResult]],
        count: int,
        target_generation: int
    ) -> List[CompanyGenome]:
        """Group B (Pareto Extremes): Amplifies dimension champions to push the multi-objective frontier."""
        dimensions = [
            ("technical_feasibility", "Extreme Technical Rigor"),
            ("risk_mitigation", "Extreme Adversarial Red-Teaming"),
            ("cross_functional_coherence", "Extreme Systems Coherence"),
            ("actionability_and_synthesis", "Extreme Execution Velocity & Actionability"),
            ("strategic_depth", "Extreme Strategic Moats & Innovation")
        ]

        offspring: List[CompanyGenome] = []
        firms_per_dimension = count // len(dimensions)

        for dim_key, dim_label in dimensions:
            # Find the #1 champion for this specific dimension
            best_firm = max(top_firms, key=lambda x: getattr(x[1].fitness, dim_key, 0.0))[0]
            
            for k in range(firms_per_dimension):
                child = copy.deepcopy(best_firm)
                child_id = f"gen_{target_generation}_pareto_{dim_key[:4]}_{k+1}"
                child.company_id = child_id
                child.generation = target_generation
                
                # Apply dimension-specific amplification
                if "tech" in dim_key:
                    for dept in child.departments:
                        if "eng" in dept.dept_id or "tech" in dept.dept_id:
                            dept.manager.temperature = 0.2
                            for a in dept.agents:
                                a.temperature = 0.2
                elif "risk" in dim_key:
                    child.executive_deliberation_rules = "Strict adversarial review: mandatory red-team veto power on all unverified claims."
                
                child.mutation_history.append(f"Gen {target_generation} Pareto Specialist: Amplified for {dim_label}")
                offspring.append(child)

        # Fill any remainder up to count
        while len(offspring) < count:
            child = copy.deepcopy(top_firms[0][0])
            child.company_id = f"gen_{target_generation}_pareto_bonus_{len(offspring)+1}"
            child.generation = target_generation
            offspring.append(child)

        return offspring

    def breed_directed_mutations(
        self,
        top_firms: List[Tuple[CompanyGenome, EvaluationResult]],
        count: int,
        target_generation: int
    ) -> List[CompanyGenome]:
        """Group C (Directed Exploration): Injects hypothesis-driven mutations to explore novel territory."""
        offspring: List[CompanyGenome] = []
        
        # Aggregate top bottlenecks from winners
        collective_bottlenecks = []
        for _, res in top_firms:
            collective_bottlenecks.extend(res.fitness.identified_bottlenecks)

        prompt = f"""COLLECTIVE EVALUATION BOTTLENECKS FROM TOP 5 WINNERS:
{json.dumps(collective_bottlenecks[:8], indent=2)}

BASELINE CHAMPION GENOME:
{top_firms[0][0].model_dump_json(indent=2)}

Formulate a mutated enterprise genome with novel specialist roles, adjusted delegation protocols, or altered team structures
that directly solves these collective vulnerabilities. Return valid JSON only."""

        for i in range(count):
            child_id = f"gen_{target_generation}_mutant_{i+1}"
            try:
                # Every 5th mutant receives an LLM meta-architect generation, others receive heuristic directed mutations
                if i % 3 == 0:
                    raw = call_vertex_gemini_rest(
                        prompt=prompt,
                        model_name="gemini-2.5-pro",
                        temperature=0.8,
                        system_instruction=META_ARCHITECT_SYSTEM_PROMPT
                    )
                    cleaned = raw.strip()
                    if "```json" in cleaned:
                        cleaned = re.search(r'```json\s*(.*?)\s*```', cleaned, re.DOTALL).group(1)
                    elif "```" in cleaned:
                        cleaned = re.search(r'```\s*(.*?)\s*```', cleaned, re.DOTALL).group(1)
                    data = json.loads(cleaned)
                    
                    # Reconstruct CompanyGenome
                    depts = []
                    for d in data.get("departments", []):
                        mgr = AgentGenome(**d["manager"])
                        agents = [AgentGenome(**a) for a in d.get("agents", [])]
                        depts.append(DepartmentGenome(
                            dept_id=d["dept_id"],
                            name=d["name"],
                            mandate=d["mandate"],
                            manager=mgr,
                            agents=agents,
                            delegation_rules=d.get("delegation_rules", "Dialectic review")
                        ))
                    child = CompanyGenome(
                        company_id=child_id,
                        generation=target_generation,
                        parent_ids=[top_firms[0][0].company_id],
                        mutation_history=[f"Gen {target_generation} Meta-Architect: {data.get('mutation_hypothesis', 'Hypothesis-driven mutation')}"],
                        ceo=AgentGenome(**data["ceo"]),
                        departments=depts,
                        executive_deliberation_rules=data.get("executive_deliberation_rules", "Dialectic review")
                    )
                    offspring.append(child)
                    continue
            except Exception:
                pass

            # Fallback heuristic directed mutation with structured traits
            parent = copy.deepcopy(random.choice(top_firms)[0])
            parent.company_id = child_id
            parent.generation = target_generation
            
            # Inject strict deliverable trait into CEO
            if not hasattr(parent.ceo, "backstory_traits") or not parent.ceo.backstory_traits:
                parent.ceo.backstory_traits = []
            parent.ceo.backstory_traits.append(
                "Mandate complete inclusion of executable files, pyproject.toml, and pytest suites from engineering pods using '### File: <path>' headers."
            )

            # Jitter temperature and inject packaging / telemetry specialist
            if len(parent.departments) > 0:
                parent.departments[0].agents.append(
                    AgentGenome(
                        role="Python Packaging & Deterministic Sandbox Specialist",
                        goal="Ensure 100% executable pyproject.toml, pytest test suites, and clean directory structure.",
                        backstory="Staff DevOps & Build Engineer passionate about executable Python packages.",
                        backstory_traits=[
                            "Always author complete, production-ready code files formatted strictly as '### File: <path>' with fenced code blocks.",
                            "Every module must be paired with unit tests in 'tests/test_<module>.py' containing assertions.",
                            "Specify clean 'pyproject.toml' manifests with build-system and runtime dependencies.",
                            "Ensure OpenTelemetry trace spans are embedded across all service interfaces."
                        ],
                        temperature=0.2,
                        model_tier="worker"
                    )
                )
            parent.mutation_history.append(f"Gen {target_generation} Directed Mutation: Injected Packaging & Sandbox Specialist with structured traits")
            offspring.append(parent)

        return offspring

    def produce_next_generation(
        self,
        ranked_population: List[Tuple[CompanyGenome, EvaluationResult]],
        target_generation: int
    ) -> List[CompanyGenome]:
        """Produces exactly 50 companies for the next generation using the 3-way mixture."""
        top_survivors = ranked_population[:self.top_k]
        consensus_motifs = self.extract_consensus_motifs(top_survivors)

        # 1. Elites (5 firms): Preserved top winners
        elites: List[CompanyGenome] = []
        for rank, (firm, _) in enumerate(top_survivors):
            elite = copy.deepcopy(firm)
            elite.company_id = f"gen_{target_generation}_elite_{rank+1}"
            elite.generation = target_generation
            elites.append(elite)

        # 2. Group A: Consensus (15 firms)
        group_a = self.breed_consensus_offspring(top_survivors, consensus_motifs, count=15, target_generation=target_generation)

        # 3. Group B: Pareto Extremes (15 firms)
        group_b = self.breed_pareto_extremes(top_survivors, count=15, target_generation=target_generation)

        # 4. Group C: Directed Mutants (15 firms)
        group_c = self.breed_directed_mutations(top_survivors, count=15, target_generation=target_generation)

        next_generation = elites + group_a + group_b + group_c
        while len(next_generation) < self.total_population:
            filler = copy.deepcopy(top_survivors[0][0])
            filler.company_id = f"gen_{target_generation}_filler_{len(next_generation)+1}"
            filler.generation = target_generation
            next_generation.append(filler)
        return next_generation[:self.total_population]
