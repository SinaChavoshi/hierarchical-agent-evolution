# Experiment 002: Distributed Parallel Tournament & Deterministic Gate Introduction (Gen 0)

## 1. Overview
* **Tournament Scale**: 10 parallel virtual enterprises (310 agents total).
* **Execution Infrastructure**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job).
* **Objective**: "Design and implement the production-ready 'agent-org' platform: a Python package providing hierarchical multi-agent orchestration, OpenTelemetry observability anchors, deterministic sandbox execution, and evolutionary search."
* **Selection Mechanism**: Composite Multi-Objective Rubric + 4-Gate Deterministic Sandbox Verifier.
* **Tournament Champion**: **`gen_0_firm_3`** (Score: **77.50**, only firm to clear the Telemetry Gate).

---

## 2. Genomic Artifacts & Scorecards
* [`champion_firm_3_genome.json`](champion_firm_3_genome.json): Tournament Champion genome (`gen_0_firm_3`, Score: 77.50).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes and scorecards of top 5 breeding survivors.
* [`bred_offspring_gen1.json`](bred_offspring_gen1.json): Gen 1 population bred via 3-Way Recombination.
* [`scorecards/`](scorecards/): Full JSON scorecards for all 10 firms.
* [`experiment_report.md`](experiment_report.md): Wall-clock performance report and score breakdown.

---

## 3. Generational Diff from Experiment 001
* **Transition to High-Throughput Distributed Architecture**: Replaced local sequential execution with a cloud-native **Kubernetes Batch Indexed Job** running 5 parallel pods. Reduced tournament runtime from 91 minutes to **22 minutes (4.1x speedup)**.
* **The Ground-Truth Deterministic Sandbox Verifier**: Introduced the 4-Gate Sandbox Verifier:
  * *Build Gate* (-6.25 pts): Requires valid `pyproject.toml` or `setup.py`.
  * *Smoke Gate* (-6.25 pts): Requires $\ge 3$ distinct functional code modules.
  * *Telemetry Gate* (-6.25 pts): Requires OpenTelemetry span or metric hooks.
  * *Test Gate* (-6.25 pts): Requires executable `pytest` test suites.
* **Hypothesis**: Grounding LLM judging in deterministic execution prevents verbose hallucination and forces agents to produce working code.

---

## 4. Organizational Culture & Behavioral Evolution Analysis
* **The Sandbox Penalty Reality Check**: In unanchored evaluations (Exp 001), firms scored 93–96 based on prose elegance. When subjected to deterministic execution, **9 out of 10 firms received the maximum -25.0 pt penalty** because they wrote narrative architectural essays without raw code blocks.
* **Emergence of Telemetry Invariants**: `gen_0_firm_3` was the sole enterprise whose Systems Engineering pod embedded concrete OpenTelemetry trace spans (`TracerProvider`, `trace.get_tracer`). This cleared the Telemetry Gate, docking only -18.75 pts and earning #1 overall (77.50 pts).
* **Cultural Shift**: The organization learned that strategic elegance without executable artifacts is severely penalized in a production environment.

---

## 5. Reproduction Command
```bash
kubectl apply -f k8s/parallel-indexed-job-east4.yaml
```
