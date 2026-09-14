# Experiment 003: Parallel Generation 1 Tournament & The Thin Persona Discovery

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (312 agents total).
* **Execution Infrastructure**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_1_elite_1`, `gen_1_elite_2` (Preserved Gen 0 winners).
  * **Consensus Offspring (3 firms)**: `gen_1_consensus_1..3` (Invariant reinforcement).
  * **Pareto Extremes (3 firms)**: `gen_1_pareto_bonus_1..3` (Technical & Strategic frontier amplification).
  * **Directed Mutants (2 firms)**: `gen_1_mutant_1..2` (Headcount expansion to 32 agents; injected packaging specialist).
* **Tournament Champion**: **`gen_1_elite_2`** (Score: **76.55**, cleared Telemetry Gate).

---

## 2. Genomic Artifacts & Scorecards
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion (`gen_1_elite_2`, Score: 76.55).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and scorecards of top 5 survivors.
* [`starting_population.json`](starting_population.json): Complete starting population.
* [`scorecards/`](scorecards/): Full JSON scorecards for all 10 firms.
* [`experiment_report.md`](experiment_report.md): Full empirical report with score breakdowns.

---

## 3. Generational Diff from Generation 0 (Exp 002)
* **First Full 3-Way Recombination Cycle**:
  * Group A (Consensus): Mined structural invariants across top Gen 0 survivors.
  * Group B (Pareto Extremes): Amplified technical feasibility and strategic depth extremes.
  * Group C (Directed Mutants): Injected a dedicated *Python Packaging & Test Automation Engineer* into the Systems Engineering pod ($31 \rightarrow 32$ headcount) to solve the Build and Test gate failures.
* **Hypothesis**: Expanding the engineering pod to include a packaging specialist will force the generation of `pyproject.toml` and eliminate the -25.0 pt sandbox penalty.

---

## 4. Organizational Culture & Behavioral Evolution Analysis
* **The "Thin Persona" Bottleneck Discovery**:
  * Despite adding packaging specialists to `gen_1_mutant_1` and `gen_1_mutant_2`, **0 files were emitted in final deliverables**.
  * Root Cause Analysis: The agent backstory was encoded as a single monolithic sentence: `"Staff DevOps & Build Engineer passionate about executable Python packages."`
  * Under standard LLM sampling, this brief description was completely diluted during multi-agent discussions. When the CEO synthesized the final deliverable, they defaulted to high-level architectural markdown rather than preserving raw code blocks.
* **Raw Potential vs. Grounded Execution**: `gen_1_mutant_1` generated the cohort's highest raw rubric score (**97.20 pts raw**), but was docked -25.0 pts, dropping to 72.20 pts.
* **Preservation of Telemetry Invariants**: Both `gen_1_elite_2` and `gen_1_pareto_bonus_1` successfully preserved the OpenTelemetry trace emission behavior inherited from Gen 0, clearing the Telemetry Gate.
* **Evolutionary Takeaway**: Monolithic persona strings fail to enforce structural formatting invariants. Personas must be discretized into granular behavioral axioms.

---

## 5. Reproduction Command
```bash
kubectl apply -f k8s/parallel-indexed-job-gen1-east4.yaml
```
