"""
Generation 10 Population Breeding Script: Cross-Cloud Federated Mesh & Self-Evolving Evaluation Rubrics.

Bred from top 5 Generation 9 survivors.
Produces 10 virtual enterprises equipped with FederatedMeshRouter directives and Self-Evolving Rubric invariants.
"""

import os
import json
import glob
import copy
from typing import List, Dict, Any

from src.schema import CompanyGenome
from src.morphogenesis import MorphogenesisEngine, StructuralCrossoverEngine

def breed_gen10():
    sc_files = sorted(glob.glob("experiments/v1/exp-011-parallel-gen9/scorecards/*.json"))
    if not sc_files:
        raise FileNotFoundError("No Generation 9 scorecards found.")

    scorecards = []
    for f in sc_files:
        with open(f) as fp:
            scorecards.append(json.load(fp))

    scorecards.sort(key=lambda x: x.get("overall_score", 0.0), reverse=True)
    top_survivors = scorecards[:5]
    print(f"Breeding Generation 10 from top {len(top_survivors)} Gen 9 survivors:")
    for idx, s in enumerate(top_survivors, 1):
        print(f" {idx}. {s['company_id']}: Net Fitness {s.get('overall_score')}")

    morph_engine = MorphogenesisEngine()
    crossover_engine = StructuralCrossoverEngine()

    gen10_pop: List[Dict[str, Any]] = []

    # 1. Elite Clones (Preserve top 2 proven lineages)
    champ_genome = CompanyGenome(**top_survivors[0]["genome"])
    runnerup_genome = CompanyGenome(**top_survivors[1]["genome"])

    elite_1 = copy.deepcopy(champ_genome)
    elite_1.company_id = "gen_10_elite_1"
    elite_1.generation = 10
    elite_1.parent_ids = [champ_genome.company_id]
    elite_1.mutation_history = list(champ_genome.mutation_history) + [
        "Gen 10 Elite Clone: Preserved Gen 9 Champion Topology with Federated Mesh mTLS Routing"
    ]
    elite_1.ceo.temperature = max(0.2, elite_1.ceo.temperature - 0.05)
    gen10_pop.append(elite_1.model_dump())

    elite_2 = copy.deepcopy(runnerup_genome)
    elite_2.company_id = "gen_10_elite_2"
    elite_2.generation = 10
    elite_2.parent_ids = [runnerup_genome.company_id]
    elite_2.mutation_history = list(runnerup_genome.mutation_history) + [
        "Gen 10 Elite Clone: Preserved 6-Pod Formal Verification Topology with Self-Evolving Rubric Invariants"
    ]
    elite_2.ceo.temperature = max(0.2, elite_2.ceo.temperature - 0.05)
    gen10_pop.append(elite_2.model_dump())

    # 2. Structural Recombinant Hybrids
    s3_genome = CompanyGenome(**top_survivors[2]["genome"])
    s4_genome = CompanyGenome(**top_survivors[3]["genome"])
    s5_genome = CompanyGenome(**top_survivors[4]["genome"])

    hyb_1 = crossover_engine.recombine(
        champ_genome, runnerup_genome, "gen_10_consensus_1", 10, label="Federated Mesh x Formal Verification Apex"
    )
    gen10_pop.append(hyb_1.model_dump())

    hyb_2 = crossover_engine.recombine(
        champ_genome, s3_genome, "gen_10_consensus_2", 10, label="High-Density Packaging x Federated Mesh"
    )
    gen10_pop.append(hyb_2.model_dump())

    hyb_3 = crossover_engine.recombine(
        runnerup_genome, s4_genome, "gen_10_consensus_3", 10, label="Self-Evolving Rubric x Test-Pass Lineage"
    )
    gen10_pop.append(hyb_3.model_dump())

    # 3. Pareto Extremes
    pareto_1 = morph_engine.morph_genome_topology(
        champ_genome,
        "Gen 10 Pareto Morph: 6-Pod Cross-Cloud Federated Mesh Conglomerate",
        10,
        "gen_10_pareto_bonus_1"
    )
    gen10_pop.append(pareto_1.model_dump())

    pareto_2 = copy.deepcopy(s5_genome)
    pareto_2.company_id = "gen_10_pareto_bonus_2"
    pareto_2.generation = 10
    pareto_2.parent_ids = [s5_genome.company_id]
    pareto_2.mutation_history = list(s5_genome.mutation_history) + [
        "Gen 10 Pareto Morph: Ultra-Lean 3-Pod Federated Edge Topology ($0.28 USD OpEx Lineage)"
    ]
    gen10_pop.append(pareto_2.model_dump())

    # 4. Directed Mutants
    mutant_1 = morph_engine.morph_genome_topology(
        s3_genome,
        "Gen 10 Mutant: Endogenous Adversarial Rubric Synthesis Specialist Pod",
        10,
        "gen_10_mutant_1"
    )
    gen10_pop.append(mutant_1.model_dump())

    mutant_2 = morph_engine.morph_genome_topology(
        s4_genome,
        "Gen 10 Mutant: Cross-Cloud Zero-Trust mTLS Gossip Consensus Pod",
        10,
        "gen_10_mutant_2"
    )
    gen10_pop.append(mutant_2.model_dump())

    mutant_3 = morph_engine.morph_genome_topology(
        champ_genome,
        "Gen 10 Mutant: Hermetic AST Self-Healing & Property-Based Fuzzing Core",
        10,
        "gen_10_mutant_3"
    )
    gen10_pop.append(mutant_3.model_dump())

    out_file = "configs/generation_10_population.json"
    with open(out_file, "w") as fp:
        json.dump(gen10_pop, fp, indent=2)

    print(f"\nSuccessfully bred Generation 10 population ({len(gen10_pop)} virtual enterprises) -> {out_file}")
    for idx, f in enumerate(gen10_pop):
        depts_count = len(f["departments"])
        agent_count = sum(1 + len(d["agents"]) for d in f["departments"]) + 1
        print(f" - [{idx}] {f['company_id']:<24} Depts: {depts_count} | Headcount: {agent_count} | Parents: {f['parent_ids']}")

if __name__ == "__main__":
    breed_gen10()
