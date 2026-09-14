# Experiment 010: Parallel Generation 8 Tournament — Closed-Loop Sandbox Test Verification & Automated Code Self-Repair

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (333 agents total across cohort).
* **Execution Runtime**: Cloud Kubernetes Cluster (Indexed Job) on GKE (`parallel-firms-gen8-east4`, namespace `agent-evolution`).
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_8_elite_1`, `gen_8_elite_2` (Descended from Gen 7 champion `gen_7_mutant_1` and runner-up `gen_7_consensus_2`).
  * **Consensus Offspring (3 firms)**: `gen_8_consensus_1..3` (Recombined traits and allelic mining).
  * **Pareto Extremes (2 firms)**: `gen_8_pareto_bonus_1..2` (Extreme technical packaging & OpEx discipline).
  * **Directed Mutants (3 firms)**: `gen_8_mutant_1..3` (Self-healing teleological controllers & AST self-repair specialists).
* **Tournament Champion**: **`gen_8_pareto_bonus_2`** (Net Score: **79.89**, Gross: 85.8, 13 physical files on disk, Build: PASS, Smoke: PASS, Telemetry: PASS, Tests: FAIL, -6.25 Penalty, OpEx Cost: $0.3802 USD, +0.39 Efficiency Bonus, 635,264 tokens).
* **Runner-Up**: **`gen_8_mutant_2`** (Net Score: **74.42**, Gross: 88.6, 11 physical files on disk, Build: FAIL, Smoke: FAIL, Telemetry: PASS, **Tests: PASS**, -12.50 Penalty, 797,693 tokens).
* **Historic Milestone**:
  * **First Live `Tests: PASS` in Evolutionary Project History**: Generation 8 broke through the physical execution barrier with **two separate firms** (`gen_8_mutant_2` and `gen_8_consensus_2`) achieving 100% clean physical pytest test execution in live container scratchpads via closed-loop automated self-repair.

---

## 2. Official Tournament Leaderboard

| Rank | Company ID | Net Fitness | Gross | Verification Gates (Build, Smoke, Telemetry, Tests) | Penalty | Files on Disk | Tokens | Cost (USD) | Lineage Focus |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **#1** | [`gen_8_pareto_bonus_2`](scorecards/gen_8_pareto_bonus_2_result.json) | **79.89** | 85.8 | **Build: PASS, Smoke: PASS, Telemetry: PASS**, Tests: FAIL | **-6.25** | 13 | 635,264 | $0.3802 | **OpEx Discipline & Deep Hardening (Champion)** |
| **#2** | [`gen_8_mutant_2`](scorecards/gen_8_mutant_2_result.json) | **74.42** | 88.6 | Build: FAIL, Smoke: FAIL, **Telemetry: PASS, Tests: PASS** | **-12.50** | 11 | 797,693 | $0.5255 | **AST Self-Repair Specialist (Live Tests PASS) (Runner-Up)** |
| **#3** | [`gen_8_elite_1`](scorecards/gen_8_elite_1_result.json) | **74.38** | 89.6 | **Build: PASS, Smoke: PASS**, Telemetry: FAIL, Tests: FAIL | -12.50 | 4 | 911,697 | $0.5726 | Elite Gen 7 Lineage |
| **#4** | [`gen_8_consensus_2`](scorecards/gen_8_consensus_2_result.json) | **71.79** | 83.8 | Build: FAIL, Smoke: FAIL, **Telemetry: PASS, Tests: PASS** | **-12.50** | 12 | 547,987 | $0.3706 | **Recombined Consensus (Live Tests PASS)** |
| **#5** | [`gen_8_consensus_3`](scorecards/gen_8_consensus_3_result.json) | **71.37** | 89.6 | Build: FAIL, Smoke: FAIL, **Telemetry: PASS**, Tests: FAIL | -18.75 | 11 | 626,322 | $0.3566 | Balanced Multi-Gate Offspring |
| **#6** | [`gen_8_mutant_3`](scorecards/gen_8_mutant_3_result.json) | **70.27** | 94.8 | Build: FAIL, Smoke: FAIL, Telemetry: FAIL, Tests: FAIL | -25.00 | 5 | 531,877 | $0.3565 | Autonomous Teleological Controller |
| **#7** | [`gen_8_elite_2`](scorecards/gen_8_elite_2_result.json) | **65.65** | 84.2 | Build: FAIL, Smoke: FAIL, **Telemetry: PASS**, Tests: FAIL | -18.75 | 16 | 699,080 | $0.4229 | High Artifact Count Variant (16 files) |
| **#8** | [`gen_8_pareto_bonus_1`](scorecards/gen_8_pareto_bonus_1_result.json) | **62.73** | 81.0 | Build: FAIL, Smoke: FAIL, **Telemetry: PASS**, Tests: FAIL | -18.75 | 13 | 564,595 | $0.3644 | Modular Architecture Variant |
| **#9** | [`gen_8_mutant_1`](scorecards/gen_8_mutant_1_result.json) | **60.90** | 67.0 | **Build: PASS, Smoke: PASS, Telemetry: PASS**, Tests: FAIL | **-6.25** | 6 | 639,467 | $0.4238 | Universal Self-Repair Prototype |
| **#10** | [`gen_8_consensus_1`](scorecards/gen_8_consensus_1_result.json) | **58.38** | 82.8 | Build: FAIL, Smoke: FAIL, Telemetry: FAIL, Tests: FAIL | -25.00 | 12 | 571,987 | $0.3551 | Consensus Baseline Offspring |

---

## 3. Engineering Innovations & Bug Diagnoses
1. **Closed-Loop Sandbox Test Verification (`src/company.py`)**:
   * Fixed critical test discovery bug: changed dictionary membership check (`"test" in f`) to path inspection (`"test" in f.get("path", "").lower()`).
   * Enabled live tool invocation during self-repair rounds, allowing the Systems Engineer agent to execute `read_file`, `write_file`, and `run_command` against failed pytest error tracebacks.
2. **Virtualenv Payload Guard (`src/company.py` & `src/sandbox_env.py`)**:
   * Pruned virtualenv (`venv/`, `.venv/`, `__pycache__/`, `.pytest_cache/`) and massive binaries from final deliverable exports.
   * Eliminated Vertex AI HTTP 400 payload errors caused by multi-thousand file virtualenv directory trees.
3. **Double Live Test Passing**:
   * Both `gen_8_mutant_2` and `gen_8_consensus_2` successfully introspected test failure logs, repaired syntax/import issues, and passed full pytest test suites cleanly.

---

## 4. Genomic Artifacts & Scorecards
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion genome (`gen_8_pareto_bonus_2`, Net Score: **79.89**).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes for the 5 breeding survivors (`gen_8_pareto_bonus_2`, `gen_8_mutant_2`, `gen_8_elite_1`, `gen_8_consensus_2`, `gen_8_consensus_3`).
* [`scorecards/`](scorecards/): Full verified result scorecards for all 10 evaluated enterprises.

---

## 5. Transition to Generation 9
The top 5 survivors from Generation 8 serve as the seed population for **Generation 9: Autonomous Morphogenesis & Dynamic Topologies**:
* Dynamic organizational department counts (varying dynamically from 3 to 7 departments).
* Cross-topology structural crossover recombining specialized sub-departments (Formal Verification Pods, AST Rewriting Cores).
