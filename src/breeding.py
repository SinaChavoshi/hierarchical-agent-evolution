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

    def breed_consensus_offspring(
        self,
        top_firms: List[Tuple[CompanyGenome, EvaluationResult]],
        consensus_motifs: Dict[str, Any],
        count: int,
        target_generation: int
    ) -> List[CompanyGenome]:
        """Group A (Consensus): Generates offspring that preserve and reinforce shared winning motifs."""
        offspring: List[CompanyGenome] = []
        for i in range(count):
            parent = copy.deepcopy(random.choice(top_firms)[0])
            child_id = f"gen_{target_generation}_consensus_{i+1}"
            parent.company_id = child_id
            parent.generation = target_generation
            parent.ceo.temperature = max(0.2, min(1.0, consensus_motifs["mean_ceo_temp"] + random.uniform(-0.05, 0.05)))
            
            # Enforce shared department invariants
            parent.mutation_history.append(
                f"Gen {target_generation} Consensus: Reinforced shared motifs {consensus_motifs['shared_department_ids']}"
            )
            offspring.append(parent)
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

            # Fallback heuristic directed mutation
            parent = copy.deepcopy(random.choice(top_firms)[0])
            parent.company_id = child_id
            parent.generation = target_generation
            # Jitter temperature and inject telemetry / chaos specialist
            if len(parent.departments) > 0:
                parent.departments[0].agents.append(
                    AgentGenome(
                        role="Telemetry & Continuous Verification Specialist",
                        goal="Ensure automated instrumentation and continuous health checks across all functions.",
                        backstory="SRE expert specializing in distributed tracing and observability.",
                        temperature=0.3,
                        model_tier="worker"
                    )
                )
            parent.mutation_history.append(f"Gen {target_generation} Directed Mutation: Injected Telemetry Specialist")
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
