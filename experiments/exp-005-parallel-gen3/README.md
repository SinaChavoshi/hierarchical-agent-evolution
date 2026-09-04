# Experiment 005: Parallel Generation 3 Tournament & Consensus Recombination Record

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (318 agents total).
* **Execution Runtime**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_3_elite_1` (from `gen_2_mutant_2`), `gen_3_elite_2` (from `gen_2_pareto_bonus_3`).
  * **Consensus Offspring (3 firms)**: `gen_3_consensus_1..3` (Consensus trait mining & allelic crossover).
  * **Pareto Extremes (3 firms)**: `gen_3_pareto_bonus_1..3` (Technical & Coherence frontier amplification).
  * **Directed Mutants (2 firms)**: `gen_3_mutant_1..2` (Injected *Python Packaging & Pytest Test Harness Specialist*).
* **Tournament Champion**: **`gen_3_consensus_2`** (Score: **96.75**, 5 files extracted, 100% Deterministic Gate Pass, 0.00 Penalty).
* **Key Finding**: Allelic consensus mining combined with structured persona traits set an all-time tournament record of **96.75 pts**. 100% of firms emitted valid code files, 4 firms passed all 4 sandbox gates with 0.00 penalty, and cohort mean climbed to **86.47 pts**.

## 2. Genomic Artifacts & Scorecards
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion genome (`gen_3_consensus_2`, Score: 96.75).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and scorecards for the top 5 breeding survivors.
* [`starting_population.json`](starting_population.json): Complete 10-firm starting population.
* [`scorecards/`](scorecards/): Full JSON result scorecards for all 10 evaluated firms.
* [`experiment_report.md`](experiment_report.md): Full empirical report with detailed score breakdowns.

## 3. Reproduction Command
To re-run the Generation 3 tournament:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen3-east4.yaml
```
