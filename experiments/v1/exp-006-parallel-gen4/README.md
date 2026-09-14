# Experiment 006: Parallel Generation 4 Tournament & OpEx Unit Economics Record

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (320 agents total).
* **Execution Runtime**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_4_elite_1`, `gen_4_elite_2` (Descended from Gen 3 record holders).
  * **Consensus Offspring (3 firms)**: `gen_4_consensus_1..3` (Consensus trait mining & allelic crossover).
  * **Pareto Extremes (3 firms)**: `gen_4_pareto_bonus_1..3` (Technical & Coherence frontier amplification).
  * **Directed Mutants (2 firms)**: `gen_4_mutant_1..2` (Autonomous sizing & OpEx budget optimization specialists).
* **Tournament Champion**: **`gen_4_elite_2`** (Score: **96.75**, 10 files extracted, 100% Deterministic Gate Pass, 0.00 Penalty, $0.0305 USD OpEx vs $0.45 budget).
* **Key Innovations**:
  1. **Autonomous Sizing & Headcount Governance**: Asymmetric allocation of departmental specialists (32 agents total, concentrating 6 specialists in Systems Engineering).
  2. **Model Unit Economics**: Tiered compute matching (Gemini 2.5 Pro for executives, Gemini 2.5 Flash for specialists) achieving ~12x–16x OpEx compression.
  3. **Token OpEx Budget Envelope**: $0.45 USD corporate budget ceiling with efficiency bonuses and profligacy penalties.
  4. **The Lean Discipline Finding**: Lean architectures (24k–41k tokens) swept the top 3 spots with 0.00 sandbox penalty, while hyper-verbose architectures (116k tokens) suffered syntax drift and -12.50 pt penalties.

---

## 2. Tournament Leaderboard & Sizing Economics

| Rank | Company ID | Headcount | Net Score | Deterministic Gates | Penalty | OpEx ($ USD) | Tokens | Files |
| :---: | :--- | :---: | :---: | :--- | :---: | :---: | :---: | :---: |
| #1 | [`gen_4_elite_2`](scorecards/gen_4_elite_2_result.json) | 32 | **96.75** | **Build:P, Smoke:P, OTel:P, Tests:P** | **0.00** | **$0.0305** | 29,133 | **10** |
| #2 | [`gen_4_elite_1`](scorecards/gen_4_elite_1_result.json) | 32 | **94.75** | **Build:P, Smoke:P, OTel:P, Tests:P** | **0.00** | **$0.0427** | 40,751 | 7 |
| #3 | [`gen_4_mutant_1`](scorecards/gen_4_mutant_1_result.json) | 32 | **92.00** | **Build:P, Smoke:P, OTel:P, Tests:P** | **0.00** | **$0.0255** | 24,324 | 6 |
| #4 | [`gen_4_consensus_2`](scorecards/gen_4_consensus_2_result.json) | 32 | **85.00** | Build:F, Smoke:F, OTel:P, Tests:P | -12.50 | $0.0395 | 37,695 | 5 |
| #5 | [`gen_4_pareto_bonus_3`](scorecards/gen_4_pareto_bonus_3_result.json) | 32 | **81.90** | Build:F, Smoke:F, OTel:P, Tests:P | -12.50 | $0.0320 | 30,523 | 9 |
| #6 | [`gen_4_consensus_1`](scorecards/gen_4_consensus_1_result.json) | 32 | **81.50** | Build:F, Smoke:F, OTel:P, Tests:P | -12.50 | $0.0524 | 50,008 | 8 |
| #7 | [`gen_4_consensus_3`](scorecards/gen_4_consensus_3_result.json) | 32 | **79.50** | Build:F, Smoke:F, OTel:P, Tests:P | -12.50 | $0.1225 | 116,897 | 9 |
| #8 | [`gen_4_pareto_bonus_2`](scorecards/gen_4_pareto_bonus_2_result.json) | 32 | **79.05** | Build:F, Smoke:F, OTel:P, Tests:F | -18.75 | $0.0241 | 23,005 | 4 |
| #9 | [`gen_4_pareto_bonus_1`](scorecards/gen_4_pareto_bonus_1_result.json) | 32 | **77.35** | Build:F, Smoke:F, OTel:P, Tests:P | -12.50 | $0.0293 | 27,963 | 8 |
| #10 | [`gen_4_mutant_2`](scorecards/gen_4_mutant_2_result.json) | 32 | **70.60** | Build:F, Smoke:F, OTel:P, Tests:P | -12.50 | $0.0785 | 74,887 | 7 |

---

## 3. Genomic Artifacts & Scorecards
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion genome (`gen_4_elite_2`, Score: 96.75).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and scorecards for the top 5 breeding survivors.
* [`starting_population.json`](starting_population.json): Complete 10-firm starting population.
* [`scorecards/`](scorecards/): Full JSON result scorecards for all 10 evaluated firms.
* [`experiment_report.md`](experiment_report.md): In-depth empirical report detailing dynamic sizing, headcount morphogenesis, the Pro vs. Flash tiered compute model, and the lean modularist vs. hyper-verbose bureaucracy bifurcation.

---

## 4. Reproduction Command
To re-run the Generation 4 tournament:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen4-east4.yaml
```
