# Experiment 007: Parallel Generation 5 Tournament — Active Tool Sandboxing & Corporate IP Marketplace

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (325 agents total across cohort).
* **Execution Runtime**: Cloud Kubernetes Cluster (Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_5_elite_1`, `gen_5_elite_2` (Descended from Gen 4 champions).
  * **Consensus Offspring (3 firms)**: `gen_5_consensus_1..3` (Consensus trait mining & allelic crossover).
  * **Pareto Extremes (2 firms)**: `gen_5_pareto_bonus_1..2` (Technical & Coherence frontier amplification).
  * **Directed Mutants (3 firms)**: `gen_5_mutant_1..3` (Active tool sandboxing & corporate asset licensing specialists).
* **Tournament Champion**: **`gen_5_mutant_3`** (Score: **91.93**, Gross: 98.0, 12 verified disk files, Build: PASS, Smoke: PASS, OTel: PASS, Tests: FAIL, -6.25 Penalty, $0.4081 USD OpEx vs $0.45 budget).
* **Key Innovations**:
  1. **Active Tool Sandboxing (`AgentWorkspace`)**: Engineering agents utilize safe execution primitives (`write_file`, `list_files`, `execute_bash`) on container scratchpads (`/tmp/hae_workspaces/{company_id}/`) to iteratively author modules, install dependencies, and run `pytest`.
  2. **Corporate Asset Marketplace & Cumulative Culture**: Verified assets from prior generations (packaging manifests, OTel exporters, test harnesses) are mounted directly into offspring sandpads, tracking balance-sheet royalties ($0.015 USD fee/credit).
  3. **High File Production & Packaging Depth**: 6 out of 10 firms cleared the Build, Smoke, and Telemetry gates, producing 8–14 production files directly on disk.
  4. **The Grounded Pytest Frontier**: Live container execution of `pytest` subjected agent code to genuine Python interpreter and test runner constraints, exposing edge cases without simulated or hallucinated passes.

---

## 2. Tournament Leaderboard & Sizing Economics

| Rank | Company ID | Headcount | Net Score | Gross | Gates (Build, Smoke, OTel, Test) | Penalty | OpEx ($) | Tokens | Disk Files |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **#1** | [`gen_5_mutant_3`](scorecards/gen_5_mutant_3_result.json) | 33 | **91.93** | 98.0 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | **$0.4081** | 736,861 | **12** |
| **#2** | [`gen_5_consensus_3`](scorecards/gen_5_consensus_3_result.json) | 32 | **90.95** | 96.8 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | **$0.3685** | 553,503 | **10** |
| **#3** | [`gen_5_elite_2`](scorecards/gen_5_elite_2_result.json) | 32 | **88.93** | 95.2 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | **$0.4455** | 728,117 | **11** |
| **#4** | [`gen_5_consensus_1`](scorecards/gen_5_consensus_1_result.json) | 32 | **87.08** | 93.1 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | **$0.4081** | 647,075 | **9** |
| **#5** | [`gen_5_consensus_2`](scorecards/gen_5_consensus_2_result.json) | 32 | **85.13** | 91.0 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | **$0.3816** | 581,963 | 3 |
| **#6** | [`gen_5_pareto_bonus_2`](scorecards/gen_5_pareto_bonus_2_result.json) | 32 | **79.00** | 88.8 | **Build:P, Smoke:P, OTel:P**, Tests:F | **-6.25** | $0.6120 | 1,001,544 | 5 |
| **#7** | [`gen_5_mutant_2`](scorecards/gen_5_mutant_2_result.json) | 33 | **77.87** | 96.0 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | $0.3386 | 555,539 | 8 |
| **#8** | [`gen_5_mutant_1`](scorecards/gen_5_mutant_1_result.json) | 33 | **75.87** | 96.8 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | $0.5458 | 702,599 | 9 |
| **#9** | [`gen_5_elite_1`](scorecards/gen_5_elite_1_result.json) | 32 | **69.36** | 87.6 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | $0.3585 | 551,299 | 12 |
| **#10** | [`gen_5_pareto_bonus_1`](scorecards/gen_5_pareto_bonus_1_result.json) | 32 | **67.24** | 93.7 | Build:F, Smoke:F, **OTel:P**, Tests:F | -18.75 | $0.7971 | 853,026 | 14 |

---

## 3. Genomic Artifacts & Scorecards
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion genome (`gen_5_mutant_3`, Score: 91.93).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and scorecards for the top 5 breeding survivors.
* [`scorecards/`](scorecards/): Full JSON result scorecards for all 10 evaluated firms.
* [`experiment_report.md`](experiment_report.md): In-depth empirical report detailing active tool sandboxing, live container disk authoring, marketplace royalty accounting, and the pytest frontier.

---

## 4. Reproduction Command
To re-run the Generation 5 tournament:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen5-east4.yaml
```
