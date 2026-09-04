# Experiment 003: Parallel Generation 1 Tournament & Sandbox Execution

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (312 agents total).
* **Execution Runtime**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_1_elite_1`, `gen_1_elite_2` (Preserved top Gen 0 winners).
  * **Consensus Offspring (3 firms)**: `gen_1_consensus_1..3` (Invariant reinforcement).
  * **Pareto Extremes (3 firms)**: `gen_1_pareto_bonus_1..3` (Technical & Strategic frontier amplification).
  * **Directed Mutants (2 firms)**: `gen_1_mutant_1..2` (Headcount expansion to 32 agents; injected dedicated *Python Packaging & Test Automation Engineer*).
* **Core Hypothesis**: Directed mutant enterprises with packaging specialists will successfully emit complete `pyproject.toml` and `pytest` suites, clearing the Build and Test gates to reduce sandbox penalties from -25.0 to 0.

## 2. Genomic Artifacts
* [`starting_population.json`](starting_population.json): Complete Generation 1 population of 10 evolved enterprise genomes.

## 3. Reproduction Command
To execute the Generation 1 tournament:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen1-east4.yaml
```
