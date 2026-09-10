"""
Generation 9 Population Breeding Script: Autonomous Morphogenesis & Dynamic Topologies.

Bred from top 5 Generation 8 survivors using StructuralCrossoverEngine and MorphogenesisEngine.
Produces 10 virtual enterprises across diverse, asymmetric organizational topologies.
"""

import os
import json
import glob
import copy
from typing import List, Dict, Any

from src.schema import CompanyGenome
from src.morphogenesis import MorphogenesisEngine, StructuralCrossoverEngine

def breed_gen9():
    sc_files = sorted(glob.glob("experiments/exp-010-parallel-gen8/scorecards/*.json"))
    if not sc_files:
        raise FileNotFoundError("No Generation 8 scorecards found.")

    scorecards = []
    for f in sc_files:
        with open(f) as fp:
            scorecards.append(json.load(fp))

    scorecards.sort(key=lambda x: x.get("overall_score", 0.0), reverse=True)
    top_survivors = scorecards[:5]
    print(f"Breading Generation 9 from top {len(top_survivors)} Gen 8 survivors:")
    for idx, s in enumerate(top_survivors, 1):
        print(f" {idx}. {s['company_id']}: Net Fitness {s.get('overall_score')}")

    morph_engine = MorphogenesisEngine()
    crossover_engine = StructuralCrossoverEngine()

    gen9_pop: List[Dict[str, Any]] = []

    # 1. Elite Clones (Preserve top 2 proven lineages)
    champ_genome = CompanyGenome(**top_survivors[0]["genome"])
    runnerup_genome = CompanyGenome(**top_survivors[1]["genome"])

    elite_1 = copy.deepcopy(champ_genome)
    elite_1.company_id = "gen_9_elite_1"
    elite_1.generation = 9
    elite_1.parent_ids = [champ_genome.company_id]
    elite_1.mutation_history = list(champ_genome.mutation_history) + [
        "Gen 9 Elite Clone: Preserved Champion Topology with Hardened Assertion Axioms"
    ]
    elite_1.ceo.temperature = max(0.2, elite_1.ceo.temperature - 0.05)
    gen9_pop.append(elite_1.model_dump())

    elite_2 = copy.deepcopy(runnerup_genome)
    elite_2.company_id = "gen_9_elite_2"
    elite_2.generation = 9
    elite_2.parent_ids = [runnerup_genome.company_id]
    elite_2.mutation_history = list(runnerup_genome.mutation_history) + [
        "Gen 9 Elite Clone: Preserved Runner-Up Topology with Hermetic Test Assertion Mining"
    ]
    elite_2.ceo.temperature = max(0.2, elite_2.ceo.temperature - 0.05)
    gen9_pop.append(elite_2.model_dump())

    # 2. Structural Recombinant Hybrids (Crossover across asymmetric topologies)
    s3_genome = CompanyGenome(**top_survivors[2]["genome"])
    s4_genome = CompanyGenome(**top_survivors[3]["genome"])
    s5_genome = CompanyGenome(**top_survivors[4]["genome"])

    # Hybrid 1: Champ x Runner-Up
    hyb_1 = crossover_engine.recombine(
        champ_genome, runnerup_genome, "gen_9_consensus_1", 9, label="Elite Apex Recombination"
    )
    gen9_pop.append(hyb_1.model_dump())

    # Hybrid 2: Champ x Survivor 3
    hyb_2 = crossover_engine.recombine(
        champ_genome, s3_genome, "gen_9_consensus_2", 9, label="Robust Systems x QA Dialectic"
    )
    gen9_pop.append(hyb_2.model_dump())

    # Hybrid 3: Runner-Up x Survivor 4
    hyb_3 = crossover_engine.recombine(
        runnerup_genome, s4_genome, "gen_9_consensus_3", 9, label="Agile Architecture x Teleological OKR"
    )
    gen9_pop.append(hyb_3.model_dump())

    # 3. Morphic Pareto Extremes (Topology morphs)
    # Pareto 1: Deep Engineering & Verification Conglomerate (Expanded 6-pod topology)
    pareto_1 = morph_engine.morph_genome_topology(
        champ_genome,
        "Gen 9 Pareto Morph: Deepened 6-Pod Formal Verification & Invariant Synthesis",
        9,
        "gen_9_pareto_bonus_1"
    )
    gen9_pop.append(pareto_1.model_dump())

    # Pareto 2: Ultra-Lean Agile Taskforce (Pruned 3-pod topology for hyper-efficiency)
    pareto_2 = copy.deepcopy(runnerup_genome)
    pareto_2.company_id = "gen_9_pareto_bonus_2"
    pareto_2.generation = 9
    pareto_2.parent_ids = [runnerup_genome.company_id]
    pareto_2.mutation_history = list(runnerup_genome.mutation_history) + [
        "Gen 9 Pareto Morph: Ultra-Lean 3-Pod Agile Topology for Capital-Efficient Zero-OpEx Clearance"
    ]
    # Keep only Systems Eng, QA, and Strategy
    pareto_2.departments = [d for d in pareto_2.departments if d.dept_id in ("dept_systems_eng", "dept_qa_redteam", "dept_market_strategy")]
    gen9_pop.append(pareto_2.model_dump())

    # 4. Directed Morphogenesis Mutants
    # Mutant 1: Spawning dedicated formal verification pod
    mutant_1 = morph_engine.morph_genome_topology(
        s3_genome,
        "Gen 9 Morphogenesis: Spawned Dedicated Formal Verification & Invariant Pod",
        9,
        "gen_9_mutant_1"
    )
    gen9_pop.append(mutant_1.model_dump())

    # Mutant 2: Systems engineering pod deepening with AST rewriting specialists
    mutant_2 = morph_engine.morph_genome_topology(
        s4_genome,
        "Gen 9 Morphogenesis: Augmented Systems Eng with Dedicated Autonomous AST Self-Repair Core",
        9,
        "gen_9_mutant_2"
    )
    gen9_pop.append(mutant_2.model_dump())

    # Mutant 3: Self-healing teleological controller mutation
    mutant_3 = morph_engine.morph_genome_topology(
        s5_genome,
        "Gen 9 Morphogenesis: Autonomous Teleological OKR Closed-Loop Self-Healing Controller",
        9,
        "gen_9_mutant_3"
    )
    gen9_pop.append(mutant_3.model_dump())

    out_file = "configs/generation_9_population.json"
    with open(out_file, "w") as fp:
        json.dump(gen9_pop, fp, indent=2)

    print(f"\nSuccessfully bred Generation 9 population ({len(gen9_pop)} virtual enterprises) -> {out_file}")
    for idx, f in enumerate(gen9_pop):
        depts_count = len(f["departments"])
        agent_count = sum(1 + len(d["agents"]) for d in f["departments"]) + 1
        print(f" - [{idx}] {f['company_id']:<24} Depts: {depts_count} | Headcount: {agent_count} | Parents: {f['parent_ids']}")

if __name__ == "__main__":
    breed_gen9()
