# Experiment 003: Parallel Generation 1 Tournament & Sandbox Execution

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (312 agents total).
* **Execution Runtime**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_1_elite_1`, `gen_1_elite_2` (Preserved top Gen 0 winners).
  * **Consensus Offspring (3 firms)**: `gen_1_consensus_1..3` (Invariant reinforcement).
  * **Pareto Extremes (3 firms)**: `gen_1_pareto_bonus_1..3` (Technical & Strategic frontier amplification).
  * **Directed Mutants (2 firms)**: `gen_1_mutant_1..2` (Headcount expansion to 32 agents; injected dedicated *Python Packaging & Test Automation Engineer*).
* **Tournament Champion**: **`gen_1_elite_2`** (Score: **76.55**, cleared Telemetry Gate).
* **Key Finding**: `gen_1_elite_2` and `gen_1_pareto_bonus_1` preserved technical telemetry traits from Gen 0. Directed mutant `gen_1_mutant_1` produced the cohort's highest raw strategic synthesis (97.20 pts raw), but revealed that single-sentence persona backstories are too thin to enforce strict executable code-block output formatting over high-level prose.

## 2. Genomic Artifacts & Scorecards
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion genome (`gen_1_elite_2`, Score: 76.55).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and scorecards for the top 5 breeding survivors.
* [`starting_population.json`](starting_population.json): Complete 10-firm starting population.
* [`scorecards/`](scorecards/): Full JSON result scorecards for all 10 evaluated firms.
* [`experiment_report.md`](experiment_report.md): Full empirical report with detailed score breakdowns.

## 3. Reproduction Command
To re-run the Generation 1 tournament:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen1-east4.yaml
```
