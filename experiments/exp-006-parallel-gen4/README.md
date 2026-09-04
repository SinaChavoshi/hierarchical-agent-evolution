# Experiment 006: Parallel Generation 4 Tournament & OpEx Unit Economics Record

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (320 agents total).
* **Execution Runtime**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_4_elite_1`, `gen_4_elite_2` (Descended from Gen 3 record holders).
  * **Consensus Offspring (3 firms)**: `gen_4_consensus_1..3` (Consensus trait mining & allelic crossover).
  * **Pareto Extremes (3 firms)**: `gen_4_pareto_bonus_1..3` (Technical & Coherence frontier amplification).
  * **Directed Mutants (2 firms)**: `gen_4_mutant_1..2` (Autonomous sizing & OpEx budget optimization specialists).
* **Tournament Champion**: **`gen_4_elite_2`** (Score: **96.75**, 10 files extracted, 100% Deterministic Gate Pass, 0.00 Penalty, $0.0895 USD OpEx).
* **Key Innovations**:
  1. **Autonomous Sizing & Headcount Governance**: Dynamic allocation of departmental specialists (32 agents total).
  2. **Model Unit Economics**: Tiered compute matching (Gemini 2.5 Pro for executives, Gemini 2.5 Flash for specialists).
  3. **Token OpEx Budget Envelope**: $0.45 USD corporate budget ceiling with efficiency bonuses and profligacy penalties.

## 2. Genomic Artifacts & Scorecards
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion genome (`gen_4_elite_2`, Score: 96.75).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and scorecards for the top 5 breeding survivors.
* [`starting_population.json`](starting_population.json): Complete 10-firm starting population.
* [`scorecards/`](scorecards/): Full JSON result scorecards for all 10 evaluated firms.
* [`experiment_report.md`](experiment_report.md): Full empirical report with detailed score breakdowns, generational diffs, and cultural analysis.

## 3. Reproduction Command
To re-run the Generation 4 tournament:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen4-east4.yaml
```
