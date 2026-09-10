# Experiment 008: Parallel Generation 6 Tournament — Inter-Firm Consortiums, Teleological OKRs & Pluggable Multi-Domain Harnesses

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (327 agents total across cohort).
* **Execution Runtime**: Cloud Kubernetes Cluster (Indexed Job) on GKE (`parallel-firms-gen6-east4`).
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_6_elite_1`, `gen_6_elite_2` (Descended from Gen 5 champions).
  * **Consensus Offspring (3 firms)**: `gen_6_consensus_1..3` (Consensus trait mining & allelic crossover).
  * **Pareto Extremes (2 firms)**: `gen_6_pareto_bonus_1..2` (Technical & Coherence frontier amplification).
  * **Directed Mutants (3 firms)**: `gen_6_mutant_1..3` (Autonomous teleological OKR & inter-firm consortium specialists).
* **Tournament Champion**: **`gen_6_elite_1`** (Net Score: **91.87**, Gross: 97.5, 15 verified disk files, Build: PASS, Smoke: PASS, Telemetry: PASS, Tests: FAIL, -6.25 Penalty, $0.3387 USD OpEx vs $0.45 budget, +0.62 efficiency bonus).
* **Key Innovations**:
  1. **Inter-Firm Strategic Co-opetition & Consortium Hub (`src/consortium.py`)**: Structured protocol enabling independent virtual enterprises to negotiate bilateral term sheets, form joint ventures or licensing alliances, and cross-merge architectural deliverables.
  2. **Pluggable Multi-Domain Evaluation Harnesses (`src/harnesses.py`)**: Extensible evaluation harnesses tailored for distinct operational domains: Software Sandbox, Financial Quantitative Trading, and Regulatory Compliance, moving beyond single-task benchmarks.
  3. **Autonomous Teleological OKRs**: Enterprises autonomously define internal quantitative success metrics and validation criteria via `EvaluationMetricSpec` and `TeleologicalVerifier`.
  4. **Active Tool Sandboxing & Artifact Production**: All 10 enterprises produced verified disk files (ranging from 6 to 17 files), with top contenders generating complete Python packages, egg-info metadata, and automated test runners.

---

## 2. Tournament Leaderboard & Sizing Economics

| Rank | Company ID | Headcount | Net Score | Gross | Gates (Build, Smoke, OTel, Test) | Penalty | OpEx ($) | Tokens | Disk Files |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **#1** | [`gen_6_elite_1`](scorecards/gen_6_elite_1_result.json) | 33 | **91.87** | 97.5 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | **$0.3387** | 499,818 | **15** |
| **#2** | [`gen_6_consensus_3`](scorecards/gen_6_consensus_3_result.json) | 32 | **86.13** | 96.0 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | $0.6128 | 907,816 | **17** |
| **#3** | [`gen_6_consensus_2`](scorecards/gen_6_consensus_2_result.json) | 32 | **82.42** | 95.5 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | $0.7572 | 1,134,437 | **12** |
| **#4** | [`gen_6_consensus_1`](scorecards/gen_6_consensus_1_result.json) | 33 | **77.24** | 97.5 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | $0.5178 | 710,493 | 6 |
| **#5** | [`gen_6_mutant_3`](scorecards/gen_6_mutant_3_result.json) | 33 | **67.93** | 74.1 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | **$0.4355** | 744,811 | **12** |
| **#6** | [`gen_6_mutant_1`](scorecards/gen_6_mutant_1_result.json) | 34 | **66.63** | 85.0 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | **$0.3824** | 626,107 | 9 |
| **#7** | [`gen_6_mutant_2`](scorecards/gen_6_mutant_2_result.json) | 32 | **64.81** | 83.1 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | **$0.3676** | 581,870 | 8 |
| **#8** | [`gen_6_pareto_bonus_2`](scorecards/gen_6_pareto_bonus_2_result.json) | 32 | **64.27** | 83.8 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | $0.4874 | 787,842 | 11 |
| **#9** | [`gen_6_elite_2`](scorecards/gen_6_elite_2_result.json) | 32 | **62.96** | 86.8 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | $0.6814 | 945,335 | 9 |
| **#10** | [`gen_6_pareto_bonus_1`](scorecards/gen_6_pareto_bonus_1_result.json) | 33 | **58.29** | 77.5 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | $0.4709 | 712,081 | 11 |

---

## 3. Genomic Artifacts & Scorecards
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion genome (`gen_6_elite_1`, Score: 91.87).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and scorecards for the top 5 breeding survivors.
* [`scorecards/`](scorecards/): Full JSON result scorecards for all 10 evaluated firms.
* [`experiment_report.md`](experiment_report.md): In-depth empirical report detailing consortium dynamics, pluggable verification harnesses, teleological self-assessment, and grounded code execution.

---

## 4. Reproduction Command
To re-run the Generation 6 tournament:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen6-east4.yaml
```
