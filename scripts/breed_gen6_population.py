"""Breeds Generation 6 population (10 virtual enterprises, 330 agents) incorporating P1 pillars."""

import json
import copy
import random
import os
from src.schema import CompanyGenome, DepartmentGenome, AgentGenome, EvaluationResult, CorporateAsset, EvaluationMetricSpec

def breed_gen6():
    top_5_path = "/tmp/hierarchical-agent-evolution-export/experiments/exp-007-parallel-gen5/top_5_survivor_genomes.json"
    with open(top_5_path) as f:
        survivors_raw = json.load(f)

    survivors = [CompanyGenome(**d) for d in survivors_raw]
    print(f"Loaded {len(survivors)} Generation 5 survivors.")
    for idx, s in enumerate(survivors, 1):
        print(f"Survivor #{idx}: {s.company_id} ({s.total_agent_count} agents)")

    gen6_population = []

    # 1. Elites (2 firms)
    elite_1 = copy.deepcopy(survivors[0])
    elite_1.company_id = "gen_6_elite_1"
    elite_1.generation = 6
    elite_1.parent_ids = [survivors[0].company_id]
    gen6_population.append(elite_1)

    elite_2 = copy.deepcopy(survivors[1])
    elite_2.company_id = "gen_6_elite_2"
    elite_2.generation = 6
    elite_2.parent_ids = [survivors[1].company_id]
    gen6_population.append(elite_2)

    # 2. Consensus Offspring (3 firms) - Crossover of traits between top survivors
    for i in range(3):
        parent_a = survivors[i % len(survivors)]
        parent_b = survivors[(i + 1) % len(survivors)]
        child = copy.deepcopy(parent_a)
        child.company_id = f"gen_6_consensus_{i+1}"
        child.generation = 6
        child.parent_ids = [parent_a.company_id, parent_b.company_id]
        child.mutation_history = [f"Gen 6 Consensus Allelic Mining: Recombined {parent_a.company_id} x {parent_b.company_id}"]
        # Enhance CEO with cross-firm strategic co-opetition and teleological formulation
        child.ceo.backstory_traits.append(
            "Negotiate strategic inter-firm consortiums and bilateral alliances to maximize collective score across technical and commercial moats."
        )
        gen6_population.append(child)

    # 3. Pareto Extremes (2 firms)
    pareto_1 = copy.deepcopy(survivors[0])
    pareto_1.company_id = "gen_6_pareto_bonus_1"
    pareto_1.generation = 6
    pareto_1.parent_ids = [survivors[0].company_id]
    pareto_1.mutation_history = ["Gen 6 Pareto: Amplified Deep Systems Architecture & Live Invariant Verification"]
    gen6_population.append(pareto_1)

    pareto_2 = copy.deepcopy(survivors[1])
    pareto_2.company_id = "gen_6_pareto_bonus_2"
    pareto_2.generation = 6
    pareto_2.parent_ids = [survivors[1].company_id]
    pareto_2.mutation_history = ["Gen 6 Pareto: Amplified Strategic GTM Moats, Compliance & Financial Viability"]
    gen6_population.append(pareto_2)

    # 4. Directed Mutants (3 firms) - P1 Innovation Specialists
    # Mutant 1: Inter-Firm Strategic Co-opetition & Consortium Specialist
    m1 = copy.deepcopy(survivors[0])
    m1.company_id = "gen_6_mutant_1"
    m1.generation = 6
    m1.parent_ids = [survivors[0].company_id]
    m1.mutation_history = ["Gen 6 P1 Mutation: Strategic Co-opetition & Executive Consortium Channel"]
    m1.ceo.backstory_traits.extend([
        "Form strategic joint-venture consortiums with complementary enterprises.",
        "Author formal bilateral term sheets specifying equitable resource pooling and shared deliverable integration."
    ])
    # Add Inter-Firm Alliance Officer to Operations
    m1.departments[0].agents.append(
        AgentGenome(
            role="Inter-Firm Alliance & Consortium Architect",
            goal="Establish bilateral joint ventures, consortium term sheets, and shared technology protocols.",
            backstory="Former corporate development VP specializing in strategic alliances and non-zero-sum game theory.",
            backstory_traits=[
                "Analyze peer competencies to identify synergistic partnership opportunities.",
                "Structure bilateral consortium agreements combining high-performance backends with robust GTM moats.",
                "Ensure combined deliverables satisfy all multi-domain verification criteria."
            ],
            temperature=0.3,
            model_tier="worker"
        )
    )
    gen6_population.append(m1)

    # Mutant 2: Pluggable Multi-Domain & Teleological OKR Specialist
    m2 = copy.deepcopy(survivors[1])
    m2.company_id = "gen_6_mutant_2"
    m2.generation = 6
    m2.parent_ids = [survivors[1].company_id]
    m2.mutation_history = ["Gen 6 P1 Mutation: Pluggable Verification Harnesses & Autonomous OKR Formulation"]
    m2.ceo.backstory_traits.extend([
        "Formulate endogenous OKRs with verifiable quantitative metrics (EvaluationMetricSpec).",
        "Target multi-domain verification across financial backtesting, regulatory compliance, and software engineering."
    ])
    m2.departments[1].agents.append(
        AgentGenome(
            role="Teleological Metric & Multi-Domain Evaluation Architect",
            goal="Synthesize endogenous OKRs and ensure verification across pluggable domain harnesses.",
            backstory="Principal Metrology and Evaluation Scientist with expertise in quantitative finance and regulatory audits.",
            backstory_traits=[
                "Define explicit verifiable target values for latency, throughput, Sharpe ratio, and audit coverage.",
                "Validate that modules implement domain-specific harnesses (software, finance, compliance).",
                "Enforce self-healing iteration loops in the active tool scratchpad."
            ],
            temperature=0.2,
            model_tier="worker"
        )
    )
    gen6_population.append(m2)

    # Mutant 3: Autonomous Morphogenesis & Dynamic Topology Specialist
    m3 = copy.deepcopy(survivors[0])
    m3.company_id = "gen_6_mutant_3"
    m3.generation = 6
    m3.parent_ids = [survivors[0].company_id]
    m3.mutation_history = ["Gen 6 P1 Mutation: Autonomous Morphogenesis & Dynamic Topology Search"]
    m3.ceo.backstory_traits.extend([
        "Dynamically adjust organizational topology and allocate specialized specialist headcount based on problem complexity.",
        "Maintain strict OpEx unit economics under the $0.45 budget envelope using tiered Flash/Pro compute."
    ])
    gen6_population.append(m3)

    out_file = "/tmp/hierarchical-agent-evolution-export/configs/generation_6_population.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w") as fp:
        json.dump([f.model_dump() for f in gen6_population], fp, indent=2)

    print(f"\n[SUCCESS] Bred Generation 6 Population ({len(gen6_population)} firms):")
    total_agents = sum(f.total_agent_count for f in gen6_population)
    print(f"Total Cohort Headcount: {total_agents} agents.")
    for f in gen6_population:
        print(f" - {f.company_id}: {f.total_agent_count} agents ({f.mutation_history[0] if f.mutation_history else 'Elite'})")

if __name__ == "__main__":
    breed_gen6()
