"""Breeds Generation 7 population (10 virtual enterprises) incorporating Priority 2 Multi-Platform Portability & Closed-Loop Sandbox Repair."""

import json
import copy
import random
import os
from src.schema import CompanyGenome, DepartmentGenome, AgentGenome, EvaluationResult

def breed_gen7():
    top_5_path = "/tmp/hierarchical-agent-evolution-export/experiments/exp-008-parallel-gen6/top_5_survivor_genomes.json"
    with open(top_5_path) as f:
        survivors_raw = json.load(f)

    survivors = [CompanyGenome(**d) for d in survivors_raw]
    print(f"Loaded {len(survivors)} Generation 6 survivors.")
    for idx, s in enumerate(survivors, 1):
        print(f"Survivor #{idx}: {s.company_id} ({s.total_agent_count} agents)")

    gen7_population = []

    # 1. Elites (2 firms)
    elite_1 = copy.deepcopy(survivors[0])
    elite_1.company_id = "gen_7_elite_1"
    elite_1.generation = 7
    elite_1.parent_ids = [survivors[0].company_id]
    elite_1.mutation_history = ["Gen 7 Elite Retention: Direct descendant of Gen 6 Tournament Champion gen_6_elite_1 (Net Fitness: 91.87, 15 files on disk)"]
    gen7_population.append(elite_1)

    elite_2 = copy.deepcopy(survivors[1])
    elite_2.company_id = "gen_7_elite_2"
    elite_2.generation = 7
    elite_2.parent_ids = [survivors[1].company_id]
    elite_2.mutation_history = ["Gen 7 Elite Retention: Direct descendant of Gen 6 Runner-Up gen_6_consensus_3 (Net Fitness: 86.13, 17 files on disk)"]
    gen7_population.append(elite_2)

    # 2. Consensus Offspring (3 firms) - Crossover of traits between top survivors
    # Consensus 1: gen_6_elite_1 x gen_6_consensus_3 (Efficiency + Record Packaging Depth)
    c1 = copy.deepcopy(survivors[0])
    c1.company_id = "gen_7_consensus_1"
    c1.generation = 7
    c1.parent_ids = [survivors[0].company_id, survivors[1].company_id]
    c1.mutation_history = [f"Gen 7 Consensus Allelic Mining: Recombined {survivors[0].company_id} (Cost Efficiency) x {survivors[1].company_id} (17 Files Packaging Depth)"]
    c1.ceo.backstory_traits.extend([
        "Enforce zero vendor lock-in: ensure all architectural modules run universally across Gemini, OpenAI, and local Ollama runtimes.",
        "Maintain maximum packaging density with verified pyproject.toml, entrypoint CLI, and unit test suites."
    ])
    gen7_population.append(c1)

    # Consensus 2: gen_6_consensus_3 x gen_6_consensus_2
    c2 = copy.deepcopy(survivors[1])
    c2.company_id = "gen_7_consensus_2"
    c2.generation = 7
    c2.parent_ids = [survivors[1].company_id, survivors[2].company_id]
    c2.mutation_history = [f"Gen 7 Consensus Allelic Mining: Recombined {survivors[1].company_id} x {survivors[2].company_id}"]
    c2.ceo.backstory_traits.append(
        "Mandate multi-provider runtime interoperability and verify execution using standard python urllib and CLI wrappers."
    )
    gen7_population.append(c2)

    # Consensus 3: gen_6_consensus_2 x gen_6_consensus_1
    c3 = copy.deepcopy(survivors[2])
    c3.company_id = "gen_7_consensus_3"
    c3.generation = 7
    c3.parent_ids = [survivors[2].company_id, survivors[3].company_id]
    c3.mutation_history = [f"Gen 7 Consensus Allelic Mining: Recombined {survivors[2].company_id} x {survivors[3].company_id}"]
    c3.ceo.backstory_traits.append(
        "Structure deliverables to clear all 4 deterministic sandbox gates: Build, Smoke, Telemetry, and Pytest assertions."
    )
    gen7_population.append(c3)

    # 3. Pareto Extremes (2 firms)
    # Pareto 1: Amplified Deep Implementation & Sandbox Packaging
    pareto_1 = copy.deepcopy(survivors[1])  # From 17-file champion
    pareto_1.company_id = "gen_7_pareto_bonus_1"
    pareto_1.generation = 7
    pareto_1.parent_ids = [survivors[1].company_id]
    pareto_1.mutation_history = ["Gen 7 Pareto: Amplified Deep Sandbox Package Engineering & 17-File Record Lineage"]
    gen7_population.append(pareto_1)

    # Pareto 2: Amplified Capital Efficiency & OpEx Discipline
    pareto_2 = copy.deepcopy(survivors[0])  # From lowest-cost champion ($0.3387)
    pareto_2.company_id = "gen_7_pareto_bonus_2"
    pareto_2.generation = 7
    pareto_2.parent_ids = [survivors[0].company_id]
    pareto_2.mutation_history = ["Gen 7 Pareto: Amplified Token Unit Economics, High-Density Modularity & Cost Discipline"]
    gen7_population.append(pareto_2)

    # 4. Directed Mutants (3 firms) - Priority 2 Focus
    # Mutant 1: Universal Provider Adapter & Multi-Platform Runtime Specialist
    m1 = copy.deepcopy(survivors[0])
    m1.company_id = "gen_7_mutant_1"
    m1.generation = 7
    m1.parent_ids = [survivors[0].company_id]
    m1.mutation_history = ["Gen 7 P2 Mutation: Universal Multi-Platform & LLM Provider Portability (Zero Cloud Lock-In)"]
    m1.ceo.backstory_traits.extend([
        "Architect the agent workforce for zero cloud lock-in, supporting Gemini API, OpenAI, Anthropic, and local Ollama.",
        "Ensure single-command portability across local threads, Docker Compose, and distributed Kubernetes clusters."
    ])
    # Add Universal Runtime Specialist to Systems Engineering
    systems_dept = next(d for d in m1.departments if "sys" in d.dept_id or "eng" in d.dept_id)
    systems_dept.agents.append(
        AgentGenome(
            role="Universal Multi-Platform & LLM Provider Architect",
            goal="Ensure zero cloud lock-in via pluggable providers (Gemini API, OpenAI, Anthropic, Ollama/vLLM) and containerized local runtimes.",
            backstory="Principal Systems Architect specialized in vendor-neutral API abstractions, local inference runtimes, and portable execution.",
            backstory_traits=[
                "Implement clean provider abstraction mapping executive/worker model tiers across OpenAI, Anthropic, Gemini, and Ollama.",
                "Ensure local orchestration works seamlessly via Docker Compose and multi-threaded Python execution without requiring cloud credentials.",
                "Verify standard urllib HTTP transport with automated retry backoff across all endpoint types."
            ],
            temperature=0.2,
            model_tier="worker"
        )
    )
    gen7_population.append(m1)

    # Mutant 2: Closed-Loop Sandbox Test Self-Repair Specialist
    m2 = copy.deepcopy(survivors[1])
    m2.company_id = "gen_7_mutant_2"
    m2.generation = 7
    m2.parent_ids = [survivors[1].company_id]
    m2.mutation_history = ["Gen 7 P2 Mutation: Closed-Loop Sandbox Test Failure Feedback & Automated Code Self-Repair"]
    m2.ceo.backstory_traits.extend([
        "Enforce closed-loop test execution: run pytest inside the container scratchpad, capture failure tracebacks, and repair code before submission.",
        "Guarantee zero assertion failures across all package test suites."
    ])
    systems_dept_2 = next(d for d in m2.departments if "sys" in d.dept_id or "eng" in d.dept_id)
    systems_dept_2.agents.append(
        AgentGenome(
            role="Closed-Loop Sandbox Test & Self-Repair Engineer",
            goal="Execute live pytest test runs, parse failure traces, and iteratively repair assertion mismatches in the scratchpad.",
            backstory="Senior Compiler and Test Automation Engineer dedicated to 100% passing test suites and self-healing software loops.",
            backstory_traits=[
                "Run 'pytest -v' via execute_bash in container scratchpad and analyze assertion errors and tracebacks.",
                "Iteratively patch implementation files until test suite executes with 0 errors.",
                "Ensure test suites include realistic inputs, proper imports, and mock fixtures to avoid environment discrepancies."
            ],
            temperature=0.2,
            model_tier="worker"
        )
    )
    gen7_population.append(m2)

    # Mutant 3: Elastic Morphogenesis & Dynamic Cross-Domain Swarm Specialist
    m3 = copy.deepcopy(survivors[0])
    m3.company_id = "gen_7_mutant_3"
    m3.generation = 7
    m3.parent_ids = [survivors[0].company_id]
    m3.mutation_history = ["Gen 7 P2 Mutation: Elastic Morphogenesis, Swarm Topologies & Dynamic Task Sizing"]
    m3.ceo.backstory_traits.extend([
        "Synthesize elastic organizational topologies dynamically based on objective complexity.",
        "Seamlessly cross-license verified corporate assets from the IP marketplace to optimize OpEx and build velocity."
    ])
    gen7_population.append(m3)

    out_file = "/tmp/hierarchical-agent-evolution-export/configs/generation_7_population.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w") as fp:
        json.dump([f.model_dump() for f in gen7_population], fp, indent=2)

    print(f"\n[SUCCESS] Bred Generation 7 Population ({len(gen7_population)} firms):")
    total_agents = sum(f.total_agent_count for f in gen7_population)
    print(f"Total Cohort Headcount: {total_agents} agents across 10 virtual enterprises.")
    for f in gen7_population:
        print(f" - {f.company_id}: {f.total_agent_count} agents ({f.mutation_history[0] if f.mutation_history else 'Elite'})")

if __name__ == "__main__":
    breed_gen7()
