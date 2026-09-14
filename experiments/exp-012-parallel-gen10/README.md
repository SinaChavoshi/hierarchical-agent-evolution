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

The **Authored** column counts only genuinely agent-authored files. The
**Raw** column is the figure the runtime originally reported, which also counted
`.pytest_cache/` and `__pycache__` byproducts plus paths where markdown
formatting leaked into the filename. See §4 for the audit.

| Rank | Company ID | Net | Gross | Penalty | B/S/T/Tel | Pods | Agents | Authored | `.py` | Raw | Tokens | Cost |
|---:|---|---:|---:|---:|:---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `gen_10_mutant_3` | **91.26** | 96.8 | −6.25 | P/P/F/P | 5 | 34 | **18** | 5 | 29 | 519,318 | $0.3140 |
| 2 | `gen_10_consensus_2` | 89.74 | 95.5 | −6.25 | P/P/F/P | 5 | 34 | 4 | 3 | 9 | 726,546 | $0.3616 |
| 3 | `gen_10_consensus_1` | 89.23 | 92.8 | **−0.00** | **P/P/P/P** | 5 | 34 | 7 | **6** | 15 | 910,254 | $0.6085 |
| 4 | `gen_10_mutant_2` | 89.19 | 95.2 | −6.25 | P/P/F/P | 5 | 34 | 9 | **6** | 17 | 786,947 | $0.4155 |
| 5 | `gen_10_pareto_bonus_1` | 89.01 | 97.0 | −6.25 | P/P/F/P | 6 | 36 | 5 | 2 | 12 | 924,692 | $0.5304 |
| 6 | `gen_10_elite_1` | 85.44 | 97.5 | −12.50 | F/F/P/P | 5 | 33 | 4 | 3 | 8 | 524,632 | $0.3712 |
| 7 | `gen_10_consensus_3` | 84.57 | 96.8 | −12.50 | P/P/F/F | 6 | 41 | 5 | 4 | 11 | 648,223 | $0.3930 |
| 8 | `gen_10_elite_2` | 76.84 | 97.8 | −18.75 | F/F/F/P | 6 | 37 | 6 | 1 | 6 | 866,522 | $0.5495 |
| 9 | `gen_10_pareto_bonus_2` | 75.52 | 93.4 | −18.75 | F/F/F/P | 3 | 21 | 14 | 4 | 19 | 529,544 | $0.2941 |
| 10 | `gen_10_mutant_1` | 74.36 | 81.7 | −6.25 | P/P/F/P | 6 | 37 | 5 | 2 | 10 | 865,580 | $0.4991 |

**Champion: `gen_10_mutant_3`** — Net 91.26, mutation lineage *"Hermetic AST
Self-Healing & Property-Based Fuzzing Core"*. It authored **18 genuine
workspace files** (of 29 raw entries), the highest audited count in the
benchmark's history — the previous best was 10 (`gen_8`) — at the second-lowest
cost in the cohort ($0.3140).

**Zero-penalty milestone: `gen_10_consensus_1`** — the first firm across all ten
generations to pass Build, Smoke, Tests, *and* Telemetry simultaneously
(penalty −0.00, 4/4 tests passing, pass rate 1.0). It also wrote the most Python
modules in the cohort (6) alongside `gen_10_mutant_2`.

## 3. Cohort Statistics vs. Prior Generations

Artifact columns are audited authored counts; see §4 for the raw-vs-authored
reconciliation.

| Gen | n | Net avg | Net max | Gross σ | Authored avg | `.py` avg | Authored max | Tests | Build | Tel | 4-gate clean | Cost avg |
|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|:---:|---:|
| 5 | 10 | 81.34 | 91.93 | 3.35 | 4.3 | 2.8 | 7 | 0/10 | 6/10 | 10/10 | 0/10 | $0.4664 |
| 6 | 10 | 72.25 | 91.87 | 8.09 | 4.7 | 2.6 | 6 | 0/10 | 4/10 | 10/10 | 0/10 | $0.5052 |
| 7 | 10 | 70.36 | 82.70 | 9.23 | 4.7 | 2.6 | 7 | 0/10 | 3/10 | 10/10 | 0/10 | $0.6182 |
| 8 | 10 | 68.98 | 79.89 | 7.05 | 6.0 | 3.6 | 10 | 2/10 | 3/10 | 7/10 | 0/10 | $0.4128 |
| 9 | 10 | 73.24 | 87.68 | 4.84 | 5.1 | 3.5 | 7 | 0/10 | 3/10 | 9/10 | 0/10 | $0.4289 |
| **10** | **10** | **84.52** | 91.26 | 4.54 | **7.7** | **3.6** | **18** | 2/10 | **7/10** | 9/10 | **1/10** | $0.4337 |

Generation 10 sets cohort records for mean net fitness (84.52), mean authored
artifacts (7.7 files/firm), and peak authored artifacts (18) — while holding mean
cost roughly flat against Gens 8 and 9.

> [!NOTE]
> Under audited counts the artifact trend is **flatter and noisier** than the raw
> figures suggested. Gen 9's previously headlined "12.3 files/firm record" is
> 5.1 authored — *below* Gen 8's 6.0. Generation 10 remains a genuine step up,
> but the clean monotonic growth story from Gen 7 onward does not survive the
> audit.

## 4. Artifact Integrity Audit

`company.run()` applied its junk-exclusion list only to the text appended to the
CEO deliverable, then returned the **unfiltered** bundle as
`run_output["workspace_files"]`. That unfiltered map is what produced every
published file count and what the verifier's smoke gate counted.

| Gen | Reported avg | Authored avg | `.py` avg | Test files avg | Generated avg | Malformed avg | Inflation |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 9.3 | 4.3 | 2.8 | 0.7 | 3.6 | 1.4 | 53.8% |
| 6 | 11.0 | 4.7 | 2.6 | 0.6 | 5.2 | 1.1 | 57.3% |
| 7 | 8.8 | 4.7 | 2.6 | 0.8 | 2.9 | 1.2 | 46.6% |
| 8 | 10.3 | 6.0 | 3.6 | 0.6 | 3.5 | 0.8 | 41.7% |
| 9 | 12.3 | 5.1 | 3.5 | 0.7 | 6.7 | 0.5 | 58.5% |
| 10 | 13.6 | 7.7 | 3.6 | 1.1 | 4.8 | 1.1 | 43.4% |

Two contamination classes:

- **Generated** — `.pytest_cache/CACHEDIR.TAG`, `__pycache__/*.pyc`,
  `*.egg-info/`, virtualenv trees. Machine byproducts, not deliverables.
- **Malformed** — paths where markdown decoration survived the ReAct text
  protocol into the filename, e.g. `src/agent_org/routing.py**`. These are not
  importable and usually duplicate a correctly named sibling.

Reproduce with `PYTHONPATH=. python3 scripts/audit_artifact_integrity.py`;
results are archived in `../artifact_integrity_audit.json`.

**Fixed for future generations**: `src/artifacts.py` is now the single source of
truth for what counts as an artifact, consumed by the runtime, the verifier, and
the analysis scripts. Agent-supplied paths are sanitized at write time, so
`routing.py**` now lands at `routing.py`. Seven regression tests cover the rules.

> [!IMPORTANT]
> The Generation 10 tournament ran **before** these fixes, on a container image
> that also predated the repo's cache-exclusion fix in `sandbox_env.list_files()`
> — see §6. The audited counts above are therefore a post-hoc correction of this
> run, not a property of it.

## 5. Genomic Artifacts & Scorecards

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

## 6. Reproduction Notes

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

## 7. Transition to Generation 11

The top five survivors seed the Generation 11 breeding pool. However, the
cross-generation statistics above surface a structural problem that should be
addressed before further capability work: gross-score standard deviation has
compressed from 9.23 (Gen 7) to 4.54 (Gen 10) while the penalty term's spread has
remained flat, meaning selection pressure is increasingly dominated by gates that
are — with the exception of Tests — not actually measuring execution.

The recommended next step is therefore to rebuild the verification layer on a real
execution harness and backfill all archived scorecards before breeding Gen 11.
