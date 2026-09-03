# Experimentation Ledger & Benchmark Archive

This directory stores empirical telemetry, benchmark scorecards, generational trajectories, and resource utilization reports across evolutionary tournament runs.

## Experiments Index

| Experiment ID | Objective | Date | Infrastructure | Key Result | Report |
| :--- | :--- | :---: | :---: | :--- | :---: |
| **`exp-001-pilot`** | Strategic Architecture & Headcount Adaptation | Sep 2, 2026 | Single Cluster GKE (`e2-standard-4`) | Fitness gain: $93.00 \rightarrow 96.25$; Headcount expanded $31 \rightarrow 36$ agents | [Full Report](pilot_tournament_gke.md) |
| **`exp-002-parallel-east4`** | High-Throughput 10-Firm Parallel Tournament | Sep 3, 2026 | Regional GKE (`us-east4`, 5 parallel pods) | 10 firms (310 agents) in 22 min; Top firm: 77.50; Telemetry Gate PASS | [Full Report](parallel_tournament_east4.md) |
| **`exp-003-agent-sandbox`** | GKE Agent Sandbox Dynamic Verification | Active | Multi-Cluster GKE (`us-east4`, `us-west1`) | Sub-second warm pool sandboxed pytest execution | *In Progress* |

---

## Telemetry Artifact Schema

Each completed experiment generates:
1. `scorecards/`: JSON evaluation results for each competing firm.
2. `lineage/`: Directed acyclic graph (DAG) mapping genetic heritage and mutations.
3. `tokens/`: Token breakdown (Flash vs. Pro) and cost accounting ledger.
4. `deliverables/`: Full source code and strategic briefs emitted by the firms.
