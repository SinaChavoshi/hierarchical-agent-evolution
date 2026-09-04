# Experiment 005: Parallel Generation 3 Tournament & Hermetic Pytest Assertion Search

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (318 agents total).
* **Execution Runtime**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_3_elite_1` (descendant of all-time champion `gen_2_mutant_2`), `gen_3_elite_2` (descendant of `gen_2_pareto_bonus_3`).
  * **Consensus Offspring (3 firms)**: `gen_3_consensus_1..3` (Consensus trait mining & allelic crossover).
  * **Pareto Extremes (3 firms)**: `gen_3_pareto_bonus_1..3` (Frontier amplification on Technical Feasibility & System Coherence).
  * **Directed Mutants (2 firms)**: `gen_3_mutant_1..2` (Injected *Python Packaging & Pytest Test Harness Specialist* to eliminate remaining test assertion failure modes).
* **Selection Focus**: Consolidate zero-penalty sandbox passes while pushing strategic synthesis beyond 95 pts.

## 2. Genomic Artifacts & Scorecards
* [`starting_population.json`](starting_population.json): Complete 10-firm starting population with structured trait schemas.
* [`scorecards/`](scorecards/): Output directory for tournament scorecards.

## 3. Reproduction Command
To launch the Generation 3 tournament:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen3-east4.yaml
```
