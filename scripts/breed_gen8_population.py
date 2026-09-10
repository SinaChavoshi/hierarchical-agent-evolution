"""
Breeds Generation 8 Population (10 virtual enterprises / ~330 agents).
Focus: Closed-Loop Sandbox Test Feedback & Automated Code Self-Repair.
"""

import json
import copy
import os
from src.schema import CompanyGenome, DepartmentGenome, AgentGenome

def breed_gen8():
    top_5_path = "experiments/exp-009-parallel-gen7/top_5_survivor_genomes.json"
    if not os.path.exists(top_5_path):
        # Fallback to loading directly from harvested scorecards
        import glob
        files = sorted(glob.glob("experiments/exp-009-parallel-gen7/scorecards/*.json"))
        data = []
        for f in files:
            with open(f) as fp:
                data.append(json.load(fp))
        data.sort(key=lambda x: x.get("overall_score", 0.0), reverse=True)
        survivors_raw = [d["genome"] for d in data[:5]]
    else:
        with open(top_5_path) as f:
            survivors_raw = json.load(f)

    survivors = [CompanyGenome(**d) for d in survivors_raw]
    print(f"Loaded {len(survivors)} Generation 7 top survivors.")
    for idx, s in enumerate(survivors, 1):
        print(f"Survivor #{idx}: {s.company_id} ({s.total_agent_count} agents)")

    gen8_population = []

    # 1. Elites (2 firms)
    elite_1 = copy.deepcopy(survivors[0])
    elite_1.company_id = "gen_8_elite_1"
    elite_1.generation = 8
    elite_1.parent_ids = [survivors[0].company_id]
    elite_1.mutation_history = [f"Gen 8 Elite Retention: Descendant of Gen 7 Tournament Champion {survivors[0].company_id}"]
    elite_1.ceo.backstory_traits.append("Enforce closed-loop test self-repair: zero unverified deliverables permitted.")
    gen8_population.append(elite_1)

    elite_2 = copy.deepcopy(survivors[1])
    elite_2.company_id = "gen_8_elite_2"
    elite_2.generation = 8
    elite_2.parent_ids = [survivors[1].company_id]
    elite_2.mutation_history = [f"Gen 8 Elite Retention: Descendant of Gen 7 Runner-Up {survivors[1].company_id}"]
    elite_2.ceo.backstory_traits.append("Mandate 4/4 deterministic gate verification: Build, Smoke, Telemetry, and Pytest.")
    gen8_population.append(elite_2)

    # 2. Consensus Offspring (3 firms)
    # Consensus 1: Top 1 x Top 2
    c1 = copy.deepcopy(survivors[0])
    c1.company_id = "gen_8_consensus_1"
    c1.generation = 8
    c1.parent_ids = [survivors[0].company_id, survivors[1].company_id]
    c1.mutation_history = [f"Gen 8 Consensus Allelic Mining: Recombined {survivors[0].company_id} x {survivors[1].company_id}"]
    c1.ceo.backstory_traits.extend([
        "Integrate universal multi-provider portability with active closed-loop pytest self-healing.",
        "Ensure all test assertion failures are introspected and repaired before deliverable submission."
    ])
    gen8_population.append(c1)

    # Consensus 2: Top 2 x Top 3
    c2 = copy.deepcopy(survivors[1])
    c2.company_id = "gen_8_consensus_2"
    c2.generation = 8
    c2.parent_ids = [survivors[1].company_id, survivors[2].company_id]
    c2.mutation_history = [f"Gen 8 Consensus Allelic Mining: Recombined {survivors[1].company_id} x {survivors[2].company_id}"]
    c2.ceo.backstory_traits.append("Deploy automated test-driven repair loops inside the container scratchpad.")
    gen8_population.append(c2)

    # Consensus 3: Top 1 x Top 4
    c3 = copy.deepcopy(survivors[0])
    c3.company_id = "gen_8_consensus_3"
    c3.generation = 8
    c3.parent_ids = [survivors[0].company_id, survivors[3].company_id]
    c3.mutation_history = [f"Gen 8 Consensus Allelic Mining: Recombined {survivors[0].company_id} x {survivors[3].company_id}"]
    c3.ceo.backstory_traits.append("Pair deep modular packaging with rigorous automated test traceback repair.")
    gen8_population.append(c3)

    # 3. Pareto Extremes (2 firms)
    # Pareto 1: Amplified Closed-Loop Self-Repair & Zero-Tolerance Test Verification
    pareto_1 = copy.deepcopy(survivors[0])
    pareto_1.company_id = "gen_8_pareto_bonus_1"
    pareto_1.generation = 8
    pareto_1.parent_ids = [survivors[0].company_id]
    pareto_1.mutation_history = ["Gen 8 Pareto: Amplified Closed-Loop Traceback Self-Repair & 100% Pytest Pass Guarantee"]
    gen8_population.append(pareto_1)

    # Pareto 2: Amplified Cost Discipline & Rapid Lean Convergence
    pareto_2 = copy.deepcopy(survivors[1])
    pareto_2.company_id = "gen_8_pareto_bonus_2"
    pareto_2.generation = 8
    pareto_2.parent_ids = [survivors[1].company_id]
    pareto_2.mutation_history = ["Gen 8 Pareto: Ultra-Lean OpEx Budgeting & Efficient High-Speed Self-Repair"]
    gen8_population.append(pareto_2)

    # 4. Directed Mutants (3 firms) - Focus: Automated Code Self-Repair Specialists
    # Mutant 1: Lead Self-Repair Systems Architect
    m1 = copy.deepcopy(survivors[0])
    m1.company_id = "gen_8_mutant_1"
    m1.generation = 8
    m1.parent_ids = [survivors[0].company_id]
    m1.mutation_history = ["Gen 8 Mutation: Autonomous Test Failure Introspection & Automated Patch Engine"]
    sys_dept = next(d for d in m1.departments if "sys" in d.dept_id or "eng" in d.dept_id)
    repair_specialist = AgentGenome(
        role="Automated Code Self-Repair Specialist",
        goal="Parse pytest error tracebacks and rewrite failing code or assertion fixtures until 100% pass rate",
        backstory="Principal SRE & Code Synthesis Repair Engineer with deep mastery of pytest, AST patching, and tracebacks.",
        temperature=0.15,
        model_tier="worker",
        backstory_traits=[
            "Directly inspects pytest failure logs to pinpoint line-level assertion errors and broken mock signatures.",
            "Patches modules and verifies execution using execute_bash inside the live sandbox workspace."
        ]
    )
    sys_dept.agents.append(repair_specialist)
    gen8_population.append(m1)

    # Mutant 2: Hermetic Test Assertion Architect
    m2 = copy.deepcopy(survivors[1])
    m2.company_id = "gen_8_mutant_2"
    m2.generation = 8
    m2.parent_ids = [survivors[1].company_id]
    m2.mutation_history = ["Gen 8 Mutation: Hermetic Test Assertion Architect & Fixture Isolation"]
    qa_dept = next(d for d in m2.departments if "qa" in d.dept_id or "red" in d.dept_id)
    fixture_specialist = AgentGenome(
        role="Hermetic Test Fixture Architect",
        goal="Design robust, hermetic test fixtures and mock frameworks that eliminate false-positive test failures",
        backstory="Senior QA Automation Specialist dedicated to writing deterministic, unbreakable test harnesses.",
        temperature=0.2,
        model_tier="worker",
        backstory_traits=[
            "Writes self-contained mock objects and pytest fixtures that mirror real production interfaces.",
            "Verifies test compatibility against both pytest and standard library unittest runners."
        ]
    )
    qa_dept.agents.append(fixture_specialist)
    gen8_population.append(m2)

    # Mutant 3: Self-Healing Teleological Orchestrator
    m3 = copy.deepcopy(survivors[0])
    m3.company_id = "gen_8_mutant_3"
    m3.generation = 8
    m3.parent_ids = [survivors[0].company_id]
    m3.mutation_history = ["Gen 8 Mutation: Autonomous Teleological OKR Self-Healing Controller"]
    m3.ceo.backstory_traits.extend([
        "Operate as an autonomous self-healing virtual enterprise: continuously reconcile reality with strategic intent.",
        "Trigger iterative repair loops whenever physical verification metrics deviate from target OKRs."
    ])
    gen8_population.append(m3)

    out_file = "configs/generation_8_population.json"
    os.makedirs("configs", exist_ok=True)
    with open(out_file, "w") as f:
        json.dump([f.model_dump() for f in gen8_population], f, indent=2)
    print(f"\n[SUCCESS] Successfully bred Generation 8 population (10 virtual enterprises, {sum(f.total_agent_count for f in gen8_population)} agents).")
    print(f"Serialized to {out_file}")

if __name__ == "__main__":
    breed_gen8()
