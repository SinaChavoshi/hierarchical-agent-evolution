# Experiment 004: Parallel Generation 2 Tournament & Persona Discretization Breakthrough

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (314 agents total).
* **Execution Runtime**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_2_elite_1`, `gen_2_elite_2` (Preserved Gen 1 survivors with trait upgrades).
  * **Consensus Offspring (3 firms)**: `gen_2_consensus_1..3` (Consensus trait mining & allelic crossover).
  * **Pareto Extremes (3 firms)**: `gen_2_pareto_bonus_1..3` (Technical & Strategic frontier amplification).
  * **Directed Mutants (2 firms)**: `gen_2_mutant_1..2` (Headcount expansion to 32 agents; dedicated *Python Packaging & Deterministic Sandbox Specialist*).
* **Tournament Champion**: **`gen_2_mutant_2`** (Score: **94.50**, 10 files extracted, 100% Deterministic Gate Pass, 0.00 Penalty).
* **Key Finding**: Discretizing agent personas into structured behavioral trait alleles (`backstory_traits`) completely solved the deliverable execution bottleneck. 90% of firms generated executable code, 3 firms passed all 4 sandbox gates, and the population mean leapt by **+11.92 pts** (from 69.60 to 81.52).

## 2. Genomic Artifacts & Scorecards
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion genome (`gen_2_mutant_2`, Score: 94.50).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and scorecards for the top 5 breeding survivors.
* [`starting_population.json`](starting_population.json): Complete 10-firm starting population with structured trait schemas.
* [`scorecards/`](scorecards/): Full JSON result scorecards for all 10 evaluated firms.
* [`experiment_report.md`](experiment_report.md): Full empirical report with detailed score breakdowns.

## 3. Reproduction Command
To re-run the Generation 2 tournament:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen2-east4.yaml
```
