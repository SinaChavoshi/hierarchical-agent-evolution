# Experimentation Ledger & Benchmark Archive

This directory stores empirical telemetry, benchmark scorecards, generational trajectories, and resource utilization reports across evolutionary tournament runs.

## Experiments Index

| Experiment ID | Objective | Date | Infrastructure | Key Result | Report |
| :--- | :--- | :---: | :---: | :--- | :---: |
| **`exp-001-baseline`** | Strategic Architecture & Autonomous Headcount Adaptation | Sep 2, 2026 | Cloud Kubernetes (`e2-standard-4`) | Fitness gain: $93.00 \rightarrow 96.25$; Headcount expanded $31 \rightarrow 36$ agents | [Full Report](pilot_tournament_baseline.md) |
| **`exp-002-parallel`** | High-Throughput 10-Firm Parallel Tournament | Sep 3, 2026 | Cloud Kubernetes (5 Parallel Pods) | 10 firms (310 agents) evaluated in 22 min; Top firm: 77.50; Telemetry Gate PASS | [Full Report](parallel_tournament_distributed.md) |
| **`exp-003-agent-sandbox`** | Kubernetes Agent Sandbox Dynamic Verification | Active | Multi-Node Kubernetes with gVisor | Sub-second warm pool sandboxed `pytest` execution | *In Progress* |

---

## Telemetry Artifact Schema

Each completed experiment generates:
1. `scorecards/`: JSON evaluation results for each competing firm.
2. `lineage/`: Directed acyclic graph (DAG) mapping genetic heritage and mutations.
3. `tokens/`: Token breakdown (Flash vs. Pro) and cost accounting ledger.
4. `deliverables/`: Full source code, test suites, and architectural packages emitted by the firms.
