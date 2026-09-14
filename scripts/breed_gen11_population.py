"""Breeds the Generation 11 population: Execution-First Selection.

Ranked by the rebuilt fitness function, not by legacy net. This matters: the
legacy ranking put the wrong firm first in five of six generations, and in
Generation 9 it selected the *worst* firm in the cohort as the sole parent of
the next. Under the rebuilt rubric Generation 10's champion is
`gen_10_elite_1` (89.10, execution_integrity 70.0), which finished **sixth**
on the legacy board.

Survivor set, by rebuilt score:

    1. gen_10_elite_1        89.10   exec 70.0   (legacy #6)
    2. gen_10_mutant_3       86.35   exec 62.5   (legacy #1)
    3. gen_10_mutant_2       82.90   exec 55.0   (legacy #4)
    4. gen_10_consensus_1    81.00   exec 55.0   (legacy #3)
    5. gen_10_pareto_bonus_1 79.90   exec 40.0   (legacy #5)

Generation 11's capability is `src/verification_loop.py`: technical agents can
issue `Action: verify` and receive the exact gate report that will score them,
from the same harness the evaluator runs. Through Generation 10 the agents and
the evaluator were looking at different things, and 19 of 60 firms shipped
Python that does not parse.

Usage:
    PYTHONPATH=. python3 scripts/breed_gen11_population.py
"""

import copy
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.morphogenesis import MorphogenesisEngine, StructuralCrossoverEngine  # noqa: E402
from src.schema import CompanyGenome  # noqa: E402

GEN = 11
RESCORE_PATH = "experiments/rubric_rescore.json"
SCORECARD_DIR = "experiments/exp-012-parallel-gen10/scorecards"
OUT_FILE = "configs/generation_11_population.json"

# Every firm is told what its predecessors actually failed at. This is cohort
# context, not a fix: it names the gates, not the code.
EXECUTION_MANDATE = (
    "Generation 11 mandate -- verified execution over described execution. "
    "Across the 60 archived firms of Generations 5-10, 19 shipped Python that "
    "does not parse, the telemetry gate passed 55/60 on heuristics but only "
    "15/60 when actually executed, and no firm has ever passed all five "
    "gates. Your score is now 30% execution_integrity, measured by running "
    "your code. Use Action: verify to see the real gate report before you "
    "finish."
)


def load_survivors(top_n: int = 5) -> List[Dict[str, Any]]:
    """Returns the top Generation 10 firms ranked by rebuilt rubric score."""
    with open(RESCORE_PATH) as fh:
        rescore = json.load(fh)
    gen10 = next(g for g in rescore if g["generation"] == "Gen 10")
    ranked = sorted(gen10["firms"], key=lambda f: f["rubric_score"], reverse=True)

    survivors = []
    for entry in ranked[:top_n]:
        path = os.path.join(SCORECARD_DIR, f"{entry['company_id']}_result.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"missing scorecard for {entry['company_id']}")
        with open(path) as fh:
            card = json.load(fh)
        card["_rubric"] = entry
        survivors.append(card)
    return survivors


def tag(genome: CompanyGenome, note: str) -> None:
    genome.generation = GEN
    genome.mutation_history = list(genome.mutation_history) + [note]
    if getattr(genome, "ceo", None) is not None:
        existing = genome.ceo.system_instructions or ""
        if EXECUTION_MANDATE not in existing:
            genome.ceo.system_instructions = (existing + "\n" + EXECUTION_MANDATE).strip()


def breed_gen11() -> List[Dict[str, Any]]:
    survivors = load_survivors()
    print("Breeding Generation 11 from the top 5 Generation 10 survivors, "
          "ranked by the REBUILT rubric:")
    for idx, card in enumerate(survivors, 1):
        r = card["_rubric"]
        print(f" {idx}. {card['company_id']:<24} rubric {r['rubric_score']:<7} "
              f"exec {r['execution_integrity']:<6} (legacy net {r['legacy_net']}, "
              f"legacy rank #{r['legacy_rank']})")

    genomes = [CompanyGenome(**c["genome"]) for c in survivors]
    champ, runner_up, s3, s4, s5 = genomes

    morph = MorphogenesisEngine()
    crossover = StructuralCrossoverEngine()
    population: List[CompanyGenome] = []

    # 1. Elite clones. Preserve the two lineages that actually execute.
    elite_1 = copy.deepcopy(champ)
    elite_1.company_id = "gen_11_elite_1"
    elite_1.parent_ids = [champ.company_id]
    tag(elite_1, "Gen 11 Elite Clone: highest measured execution_integrity "
                 "lineage (70.0) with in-loop ground-truth verification")
    # Lower CEO temperature: this lineage's edge is correctness, not novelty.
    elite_1.ceo.temperature = max(0.2, elite_1.ceo.temperature - 0.05)
    population.append(elite_1)

    elite_2 = copy.deepcopy(runner_up)
    elite_2.company_id = "gen_11_elite_2"
    elite_2.parent_ids = [runner_up.company_id]
    tag(elite_2, "Gen 11 Elite Clone: highest-output lineage (18 audited files) "
                 "with in-loop ground-truth verification")
    elite_2.ceo.temperature = max(0.2, elite_2.ceo.temperature - 0.05)
    population.append(elite_2)

    # 2. Structural recombinants.
    population.append(crossover.recombine(
        champ, runner_up, "gen_11_consensus_1", GEN,
        label="Verified Execution x High-Density Authorship"))
    population.append(crossover.recombine(
        champ, s3, "gen_11_consensus_2", GEN,
        label="Verified Execution x Zero-Trust Mesh Consensus"))
    population.append(crossover.recombine(
        runner_up, s4, "gen_11_consensus_3", GEN,
        label="AST Self-Healing x Perfect Smoke Import"))

    # 3. Pareto extremes. One heavy, one lean, to keep the cost/quality
    #    frontier populated -- Gen 10 showed the three largest firms finishing
    #    7th, 8th and 10th, so headcount is not free.
    population.append(morph.morph_genome_topology(
        champ,
        "Gen 11 Pareto Morph: dedicated Verification Engineering pod owning "
        "the syntax and smoke gates end to end",
        GEN, "gen_11_pareto_bonus_1"))

    lean = copy.deepcopy(s5)
    lean.company_id = "gen_11_pareto_bonus_2"
    lean.parent_ids = [s5.company_id]
    tag(lean, "Gen 11 Pareto Morph: lean topology, verification budget spent "
              "on a single package rather than breadth")
    population.append(lean)

    # 4. Directed mutants, each aimed at a specific measured failure.
    population.append(morph.morph_genome_topology(
        s3,
        "Gen 11 Mutant: parse-before-write discipline -- every authored module "
        "is AST-validated before the pod moves on (19 of 60 archived firms "
        "shipped unparseable Python)",
        GEN, "gen_11_mutant_1"))
    population.append(morph.morph_genome_topology(
        s4,
        "Gen 11 Mutant: real OpenTelemetry instrumentation with live span call "
        "sites, not prose (telemetry fell 55/60 to 15/60 under execution)",
        GEN, "gen_11_mutant_2"))
    population.append(morph.morph_genome_topology(
        champ,
        "Gen 11 Mutant: collectable test suite as the primary deliverable -- "
        "only 3 of 60 archived firms have ever had a green pytest run",
        GEN, "gen_11_mutant_3"))

    for genome in population:
        tag(genome, f"Generation {GEN}: Execution-First Selection")

    payload = [g.model_dump() for g in population]
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w") as fh:
        json.dump(payload, fh, indent=2)

    print(f"\nBred {len(payload)} enterprises -> {OUT_FILE}")
    for idx, firm in enumerate(payload):
        depts = len(firm["departments"])
        headcount = sum(1 + len(d["agents"]) for d in firm["departments"]) + 1
        print(f" - [{idx}] {firm['company_id']:<24} Depts: {depts} | "
              f"Headcount: {headcount} | Parents: {firm['parent_ids']}")
    return payload


if __name__ == "__main__":
    breed_gen11()
