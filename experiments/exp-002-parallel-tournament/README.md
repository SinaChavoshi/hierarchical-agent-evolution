# Experiment 002: Distributed Parallel Tournament (Generation 0)

## 1. Overview
* **Tournament Scale**: 10 parallel virtual enterprises (310 agents total).
* **Execution Infrastructure**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job).
* **Objective**: "Design and implement the production-ready 'agent-org' platform: a Python package providing hierarchical multi-agent orchestration, OpenTelemetry observability anchors, deterministic sandbox execution, and evolutionary search."
* **Deterministic Selection**: First tournament governed by the 4-Gate Deterministic Sandbox Verifier.
* **Key Finding**: Evaluated 10 enterprises in 22 minutes (4.1x speedup over serial execution). Ground-truth gates docked non-executable submissions, with `gen_0_firm_3` achieving #1 rank (77.50 pts) by clearing the Telemetry Gate.

## 2. Genomic Artifacts
* [`champion_firm_3_genome.json`](champion_firm_3_genome.json): Tournament Champion (`gen_0_firm_3`, Score: 77.50).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and diagnostic scorecards of the top 5 breeding survivors.
* [`bred_offspring_gen1.json`](bred_offspring_gen1.json): Full 10-firm Generation 1 population bred via 3-Way Recombination (Elites, Consensus, Pareto, Directed Mutants).
* [`experiment_report.md`](experiment_report.md): Detailed parallel performance report, wall-clock timing, and score distribution.

## 3. Reproduction Command
To re-run the 10-firm Generation 0 tournament on Kubernetes:
```bash
kubectl apply -f k8s/parallel-indexed-job-east4.yaml
```
