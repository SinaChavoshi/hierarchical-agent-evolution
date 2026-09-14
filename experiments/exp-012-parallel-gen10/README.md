# Experiment 012: Parallel Generation 10 Tournament — Cross-Cloud Federated Mesh & Self-Evolving Rubrics

## 1. Overview

Generation 10 is the tenth parallel tournament in the Hierarchical Agent Evolution
benchmark. Ten bred firms were evaluated concurrently as single-pod GKE jobs in
`us-east4`, each receiving the identical business objective and an isolated live
sandbox workspace.

Two new architectural capabilities were introduced for this generation:

- **Cross-Cloud Federated Mesh** (`src/federated_mesh.py`) — `FederatedMeshRouter`
  and `MeshPeerNode` provide deterministic pod routing across notional cloud
  regions, authenticated with HMAC `HAE-MESH-v1` peer tokens.
- **Self-Evolving Rubrics** (`src/rubric_evolution.py`) — `SelfEvolvingRubricEngine`
  scores a workspace against four structural invariants (AST well-formedness,
  security posture, assertion density, type coverage).

Generation 10 produced the strongest cohort measured to date and the **first firm
in the history of the benchmark to clear all four verification gates with zero
penalty**.

> [!IMPORTANT]
> **Gate validity caveat.** Of the four deterministic gates, only the **Tests**
> gate physically executes code. As of this experiment, `src/sandbox_verifier.py`
> implements Build as a filename-substring match, Smoke as a file-count
> threshold, and Telemetry as a case-insensitive search for the string
> `opentelemetry` across the concatenated deliverable — which includes the CEO's
> prose. Build / Smoke / Telemetry results in the table below should therefore be
> read as **heuristic proxies, not proof of execution**. See
> `experiment_report.md` §4 for the full analysis and the planned remediation.

## 2. Official Tournament Leaderboard

Net fitness = gross LLM-judge composite − deterministic verification penalty.
Gate column order is Build / Smoke / Tests / Telemetry.

| Rank | Company ID | Net | Gross | Penalty | B/S/T/Tel | Pods | Agents | Files | Tokens | Cost |
|---:|---|---:|---:|---:|:---:|---:|---:|---:|---:|---:|
| 1 | `gen_10_mutant_3` | **91.26** | 96.8 | −6.25 | P/P/F/P | 5 | 34 | **29** | 519,318 | $0.3140 |
| 2 | `gen_10_consensus_2` | 89.74 | 95.5 | −6.25 | P/P/F/P | 5 | 34 | 9 | 726,546 | $0.3616 |
| 3 | `gen_10_consensus_1` | 89.23 | 92.8 | **−0.00** | **P/P/P/P** | 5 | 34 | 15 | 910,254 | $0.6085 |
| 4 | `gen_10_mutant_2` | 89.19 | 95.2 | −6.25 | P/P/F/P | 5 | 34 | 17 | 786,947 | $0.4155 |
| 5 | `gen_10_pareto_bonus_1` | 89.01 | 97.0 | −6.25 | P/P/F/P | 6 | 36 | 12 | 924,692 | $0.5304 |
| 6 | `gen_10_elite_1` | 85.44 | 97.5 | −12.50 | F/F/P/P | 5 | 33 | 8 | 524,632 | $0.3712 |
| 7 | `gen_10_consensus_3` | 84.57 | 96.8 | −12.50 | P/P/F/F | 6 | 41 | 11 | 648,223 | $0.3930 |
| 8 | `gen_10_elite_2` | 76.84 | 97.8 | −18.75 | F/F/F/P | 6 | 37 | 6 | 866,522 | $0.5495 |
| 9 | `gen_10_pareto_bonus_2` | 75.52 | 93.4 | −18.75 | F/F/F/P | 3 | 21 | 19 | 529,544 | $0.2941 |
| 10 | `gen_10_mutant_1` | 74.36 | 81.7 | −6.25 | P/P/F/P | 6 | 37 | 10 | 865,580 | $0.4991 |

**Champion: `gen_10_mutant_3`** — Net 91.26, mutation lineage *"Hermetic AST
Self-Healing & Property-Based Fuzzing Core"*. It authored **29 workspace files**,
beating the previous all-time record of 19, at the second-lowest cost in the
cohort ($0.3140).

**Zero-penalty milestone: `gen_10_consensus_1`** — the first firm across all ten
generations to pass Build, Smoke, Tests, *and* Telemetry simultaneously
(penalty −0.00, 4/4 tests passing, pass rate 1.0).

## 3. Cohort Statistics vs. Prior Generations

| Gen | n | Net avg | Net max | Gross σ | Files avg | Files max | Tests | Build | Tel | 4-gate clean | Cost avg |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|:---:|---:|
| 5 | 10 | 81.34 | 91.93 | 3.35 | 9.3 | 14 | 0/10 | 6/10 | 10/10 | 0/10 | $0.4664 |
| 6 | 10 | 72.25 | 91.87 | 8.09 | 11.0 | 17 | 0/10 | 4/10 | 10/10 | 0/10 | $0.5052 |
| 7 | 10 | 70.36 | 82.70 | 9.23 | 8.8 | 13 | 0/10 | 3/10 | 10/10 | 0/10 | $0.6182 |
| 8 | 10 | 68.98 | 79.89 | 7.05 | 10.3 | 16 | 2/10 | 3/10 | 7/10 | 0/10 | $0.4128 |
| 9 | 10 | 73.24 | 87.68 | 4.84 | 12.3 | 17 | 0/10 | 3/10 | 9/10 | 0/10 | $0.4289 |
| **10** | **10** | **84.52** | 91.26 | 4.54 | **13.6** | **29** | 2/10 | **7/10** | 9/10 | **1/10** | $0.4337 |

Generation 10 sets cohort records for mean net fitness (84.52), mean artifact
count (13.6 files/firm), peak artifact count (29), and Build gate pass rate since
Gen 5 — while holding mean cost roughly flat against Gens 8 and 9.

## 4. Genomic Artifacts & Scorecards

| Artifact | Path |
|---|---|
| Full per-firm scorecards (10) | `scorecards/*_result.json` |
| Champion genome | `winning_champion_genome.json` |
| Top-5 survivors (Gen 11 breeding stock) | `top_5_survivor_genomes.json` |
| Population definition | `../../configs/generation_10_population.json` |
| Tournament manifest | `../../k8s/parallel-indexed-job-gen10-east4.yaml` |
| Single-firm retry manifests | `../../k8s/job-gen10-firm{7,8,9}.yaml` |

Each scorecard retains the complete `run_output.workspace_files` payload, so the
cohort can be re-scored against a stricter verifier without re-running any firm.

## 5. Reproduction Notes

Firms 7, 8, and 9 did not complete in the initial indexed tournament: the Vertex
AI OAuth token mounted at job start expires after approximately one hour, and
`src/llm_factory.py` neither refreshes it nor retries on HTTP 401. Those three
firms were re-dispatched as individual single-pod jobs after refreshing the
`vertex-token` secret. This is a recurring operational failure across Gens 9 and
10 and is tracked as a P1 remediation (prefer the metadata server via Workload
Identity, cache with expiry, and treat 401 as a force-refresh trigger).

Generation 10 also mounted four Python modules (`company.py`, `morphogenesis.py`,
`federated_mesh.py`, `rubric_evolution.py`) over the baked container image via the
`gen10-code` ConfigMap. The published image tag therefore does not by itself
reproduce this run; the ConfigMap contents are required. Folding these into the
image is tracked as a reproducibility fix.

## 6. Transition to Generation 11

The top five survivors seed the Generation 11 breeding pool. However, the
cross-generation statistics above surface a structural problem that should be
addressed before further capability work: gross-score standard deviation has
compressed from 9.23 (Gen 7) to 4.54 (Gen 10) while the penalty term's spread has
remained flat, meaning selection pressure is increasingly dominated by gates that
are — with the exception of Tests — not actually measuring execution.

The recommended next step is therefore to rebuild the verification layer on a real
execution harness and backfill all archived scorecards before breeding Gen 11.
