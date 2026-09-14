# Generation 8 Empirical Experiment Report: Closed-Loop Sandbox Test Verification & Automated Code Self-Repair

**Tournament ID**: `exp-010-parallel-gen8`  
**Execution Runtime**: Cloud Kubernetes Engine (GKE `us-east4-a`), 10-Worker Indexed Batch Architecture (`parallel-firms-gen8-east4`, namespace `agent-evolution`)  
**Evaluator**: Ground-Truth 4-Gate Live Sandbox (`pytest` Execution Engine) + Multi-Objective LLM Judge (`gemini-2.5-pro`)  
**Population**: 10 Virtual Enterprises (333 Total Specialized Agents)  
**Date**: September 2026  

---

## 1. Executive Summary & Historic Breakthrough

Generation 8 marks a transformative milestone in the Hierarchical Agent Evolution (HAE) project: the breakthrough from open-loop code emission into **Closed-Loop Sandbox Test Verification & Automated Self-Repair**.

In previous generations (Gens 0–7), virtual enterprises emitted code and test files in a single pass. While physical packaging artifacts grew from 0 to 17 files, 0% of enterprises ever passed physical pytest execution in the sandbox. Generation 8 introduced **Step 2.5: Closed-Loop Self-Repair**, executing pytest directly within the live container scratchpad (`/tmp/hae_workspaces/{company_id}/`), capturing exact stdout/stderr failure tracebacks, and invoking multi-turn tool-assisted repair sessions.

### Key Empirical Findings:

1. **Historic Breakthrough — Double Live `Tests: PASS`**:
   For the first time in project history, virtual enterprises achieved clean, zero-failure `pytest` execution in the sandbox:
   * **`gen_8_mutant_2` (Rank #2, Net: 74.42)**: Passed 100% of live sandbox unit tests via AST self-repair. 11 physical files on disk.
   * **`gen_8_consensus_2` (Rank #4, Net: 71.79)**: Passed 100% of live sandbox unit tests via closed-loop fixture repair. 12 physical files on disk.

2. **Champion Emergence — `gen_8_pareto_bonus_2`**:
   * **Net Fitness: 79.89** (Gross: 85.8, Penalty: -6.25 [Build: PASS, Smoke: PASS, Telemetry: PASS, Tests: FAIL], Cost: $0.3802 USD, +0.39 Bonus, 635,264 tokens).
   * Champion authored **13 physical files on disk**, balancing deep modular architecture with exceptional OpEx discipline.

3. **High Physical File Packaging Across Cohort**:
   * Average files authored per firm: **10.3 files** (maximum 16 files by `gen_8_elite_2`).
   * 100% of enterprises successfully executed within live Linux container scratchpads.

4. **Critical Bug Discoveries & Resolution**:
   * **Dict Membership Bug**: `list_files()` returned dictionary objects. Testing `"test" in f` evaluated dict keys, completely bypassing test discovery. Corrected to `"test" in f.get("path", "").lower()`.
   * **Virtualenv Payload Bloat**: Systems engineer creating `python -m venv venv` generated 1,913 files, exceeding Vertex AI HTTP 400 payload limits. Resolved by filtering out `venv`, `.venv`, `__pycache__`, and `.pytest_cache` during bundle exports.

---

## 2. Complete Official Tournament Leaderboard

| Rank | Company ID | Net Fitness | Gross | Verification Gates (Build, Smoke, OTel, Test) | Penalty | Files on Disk | Tokens | Cost (USD) | Lineage Focus |
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

## 3. Transition to Generation 9 (Autonomous Morphogenesis & Dynamic Topologies)

The top 5 survivors from Generation 8 (`gen_8_pareto_bonus_2`, `gen_8_mutant_2`, `gen_8_elite_1`, `gen_8_consensus_2`, `gen_8_consensus_3`) form the genomic foundation for Generation 9:
* **Dynamic Department Sizing**: Morphing from 3 up to 7 specialized pods based on architectural scope.
* **Specialized Functional Units**: Spawning dedicated Formal Verification Pods and AST Rewriting Cores.
* **Structural Crossover**: Recombining asymmetric topologies across differing departmental schemas.
