# Generation 10 Empirical Experiment Report: Cross-Cloud Federated Mesh & Self-Evolving Rubrics

## 1. Executive Summary & Core Empirical Findings

Generation 10 evaluated ten bred firms in parallel on GKE (`us-east4`), each on
the identical business objective with an isolated live sandbox. It is the
strongest cohort the benchmark has produced, and it simultaneously provides the
clearest evidence to date that the **fitness function itself has become the
limiting factor**.

### Key Empirical Findings

**1. Cohort-wide performance records.**
Mean net fitness reached **84.52**, surpassing the previous best of 81.34
(Gen 5) and improving on Gen 9 by **+11.28**. Mean artifact production reached
**13.6 files/firm** (previous record 12.3, Gen 9), and the champion authored
**29 files** — a 53% increase over the prior all-time single-firm record of 19.

**2. First zero-penalty four-gate clearance in benchmark history.**
`gen_10_consensus_1` passed Build, Smoke, Tests, and Telemetry simultaneously
(`score_penalty = 0.0`, 4/4 tests, pass rate 1.0). Across Generations 1–9, no
firm had ever cleared all four gates. Its lineage is a structural recombination
of `gen_9_consensus_1 × gen_9_mutant_1` carrying the federated-mesh trait.

**3. Artifact production decoupled from cost.**
The champion `gen_10_mutant_3` produced the most files (29) at the
second-lowest cost in the cohort ($0.3140) and the second-lowest token count
(519,318). Firms that spent the most did not build the most: `gen_10_elite_2`
consumed 866,522 tokens and $0.5495 to produce only 6 files. Cohort mean cost
($0.4337) held roughly flat against Gen 8 ($0.4128) and Gen 9 ($0.4289) despite
a 32% increase in mean artifact output over Gen 9.

**4. The Tests gate remains the hard ceiling.**
Only 2 of 10 firms passed live `pytest`/`unittest` execution — the same count as
Gen 8, and the only two generations ever to register a pass. Build recovered to
7/10 (best since Gen 5), but see §4: the Build gate does not execute anything.

**5. Larger topologies did not win.**
The three highest-headcount firms (`gen_10_consensus_3` at 41 agents,
`gen_10_elite_2` and `gen_10_mutant_1` at 37) finished 7th, 8th, and 10th. The
top four finishers all ran the 5-pod / 34-agent configuration. The leanest firm
(`gen_10_pareto_bonus_2`, 3 pods / 21 agents) placed 9th but produced 19 files
at the cohort's lowest cost of $0.2941, confirming that artifact throughput and
organizational size are largely independent.

## 2. Complete Official Tournament Leaderboard

| Rank | Company ID | Net | Gross | Penalty | B/S/T/Tel | Pods | Agents | Files | Tokens | Cost | Runtime |
|---:|---|---:|---:|---:|:---:|---:|---:|---:|---:|---:|---:|
| 1 | `gen_10_mutant_3` | **91.26** | 96.8 | −6.25 | P/P/F/P | 5 | 34 | **29** | 519,318 | $0.3140 | 18 min |
| 2 | `gen_10_consensus_2` | 89.74 | 95.5 | −6.25 | P/P/F/P | 5 | 34 | 9 | 726,546 | $0.3616 | 19 min |
| 3 | `gen_10_consensus_1` | 89.23 | 92.8 | **−0.00** | **P/P/P/P** | 5 | 34 | 15 | 910,254 | $0.6085 | 30 min |
| 4 | `gen_10_mutant_2` | 89.19 | 95.2 | −6.25 | P/P/F/P | 5 | 34 | 17 | 786,947 | $0.4155 | 25 min |
| 5 | `gen_10_pareto_bonus_1` | 89.01 | 97.0 | −6.25 | P/P/F/P | 6 | 36 | 12 | 924,692 | $0.5304 | 17 min |
| 6 | `gen_10_elite_1` | 85.44 | 97.5 | −12.50 | F/F/P/P | 5 | 33 | 8 | 524,632 | $0.3712 | 13 min |
| 7 | `gen_10_consensus_3` | 84.57 | 96.8 | −12.50 | P/P/F/F | 6 | 41 | 11 | 648,223 | $0.3930 | 18 min |
| 8 | `gen_10_elite_2` | 76.84 | 97.8 | −18.75 | F/F/F/P | 6 | 37 | 6 | 866,522 | $0.5495 | 14 min |
| 9 | `gen_10_pareto_bonus_2` | 75.52 | 93.4 | −18.75 | F/F/F/P | 3 | 21 | 19 | 529,544 | $0.2941 | 24 min |
| 10 | `gen_10_mutant_1` | 74.36 | 81.7 | −6.25 | P/P/F/P | 6 | 37 | 10 | 865,580 | $0.4991 | 19 min |

## 3. Longitudinal Trajectory (Generations 5–10)

| Gen | n | Net avg | Net max | Gross σ | Files avg | Files max | Tests | Build | Tel | 4-gate clean | Cost avg |
|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|:---:|---:|
| 5 | 10 | 81.34 | 91.93 | 3.35 | 9.3 | 14 | 0/10 | 6/10 | 10/10 | 0/10 | $0.4664 |
| 6 | 10 | 72.25 | 91.87 | 8.09 | 11.0 | 17 | 0/10 | 4/10 | 10/10 | 0/10 | $0.5052 |
| 7 | 10 | 70.36 | 82.70 | 9.23 | 8.8 | 13 | 0/10 | 3/10 | 10/10 | 0/10 | $0.6182 |
| 8 | 10 | 68.98 | 79.89 | 7.05 | 10.3 | 16 | 2/10 | 3/10 | 7/10 | 0/10 | $0.4128 |
| 9 | 10 | 73.24 | 87.68 | 4.84 | 12.3 | 17 | 0/10 | 3/10 | 9/10 | 0/10 | $0.4289 |
| **10** | **10** | **84.52** | 91.26 | 4.54 | **13.6** | **29** | 2/10 | **7/10** | 9/10 | **1/10** | $0.4337 |

Artifact production shows a clean monotonic trend from Gen 7 onward
(8.8 → 10.3 → 12.3 → 13.6 files/firm). This is the benchmark's most credible
progress signal, because file count is directly observed rather than inferred
from either the judge or the heuristic gates.

## 4. Validity Analysis: The Fitness Function Is Now the Bottleneck

> [!WARNING]
> The findings in this section materially qualify the scores reported above and
> in all prior experiment records.

### 4.1 Three of the four deterministic gates do not execute code

`src/sandbox_verifier.py` (lines 57–82) implements the gates as follows:

| Gate | Actual implementation | Executes code? |
|---|---|:---:|
| **Build** | Substring check that `pyproject.toml` appears in a filename, AND some file matches `runtime\|company\|engine\|orchestrator\|core`. `pip install -e .` is never invoked. | No |
| **Smoke** | `has_runtime and len(files) >= 3`. Nothing is imported or run. | No |
| **Telemetry** | Case-insensitive search for `opentelemetry` across concatenated code **plus `deliverable_text`** — the CEO's markdown prose. Writing the word once in an essay passes. | No |
| **Tests** | Genuinely runs `pytest` / `unittest` in the container. | **Yes** |

Consequently the "7/10 Build" and "9/10 Telemetry" figures above measure
filename conventions and vocabulary, not working software. Only the 2/10 Tests
figure reflects executable output.

### 4.2 Selection pressure is majority-driven by the non-executing gates

Decomposing net-fitness variance across the Gen 10 cohort:

| Component | σ | Share of net variance |
|---|---:|---:|
| Gross (LLM judge) | 4.54 | 38% |
| Penalty (gates) | 5.76 | **62%** |

Since three of the four gates are heuristics, the majority of the selection
signal now derives from proxies rather than measurements.

### 4.3 The LLM judge has saturated

Gross-score dispersion has compressed steadily: σ = 9.23 (Gen 7) → 7.05 (Gen 8)
→ 4.84 (Gen 9) → 4.54 (Gen 10). Excluding the single low outlier
(`gen_10_mutant_1`, 81.7), the remaining nine firms span just **92.8–97.8 — a
5.0-point spread with σ = 1.69**.

Per-dimension breakdown reveals where the signal has collapsed:

| Judge dimension | Weight | Mean | σ | Range |
|---|---:|---:|---:|---|
| Strategic depth | 25% | 94.5 | 2.01 | 90–98 |
| Technical feasibility | 25% | 90.2 | **10.93** | 60–98 |
| Cross-functional coherence | 20% | 99.0 | **1.67** | 95–100 |
| Risk mitigation | 15% | 90.8 | 7.44 | 70–97 |
| Actionability | 15% | 99.0 | **1.67** | 95–100 |

**Coherence and actionability — 35% of the gross weight combined — are pinned at
a mean of 99.0 and contribute almost no discriminative information.** Technical
feasibility is carrying nearly all of the judge's remaining signal.

### 4.4 The judge is blind to artifact production

Correlating judge output against what firms actually built:

- `corr(files_authored, gross_score) = **+0.06**` — effectively zero.
- `corr(files_authored, net_score) = +0.34` — the weak positive relationship
  enters only through the penalty term, not the judge.
- `corr(deliverable_length, gross_score) = −0.48`.

A firm that wrote 29 working files and one that wrote 6 are scored nearly
identically by the judge. The rubric in `src/evaluator.py` (lines 96–99) contains
no dimension that measures execution, so this is expected behavior rather than a
judging failure — but it means gross score cannot be read as a measure of
engineering output.

### 4.5 Silent judge fallback contaminates the breeding pool

`src/evaluator.py` (lines 77–87) catches **any** exception from the judge —
including JSON parse failures — and silently substitutes the fixed vector
`70/70/70/65/70`, yielding a free gross score of ≈69.25. A firm whose evaluation
failed outright is therefore indistinguishable from a legitimate mid-ranker and
remains eligible for selection.

### 4.6 Morphogenesis was phenotypically inert during this run

`src/company.py` gated filesystem and shell tools on a hardcoded list of two
`dept_id` values. The `dept_formal_verification` pods that `MorphogenesisEngine`
spawned in **4 of the 10 Gen 10 firms** (`elite_2`, `consensus_3`,
`pareto_bonus_1`, `mutant_1`) therefore fell through to the no-tools path and
**could not write a single file**, despite mandates explicitly calling for test
fixtures and invariant guards. Those agents produced prose that the judge
rewarded while contributing nothing to the gates.

Measured across the Gen 10 population, **12 of 279 operational agents were
affected** (tool-enabled headcount 127 → 139 once corrected). Notably, three of
the four affected firms finished 7th, 8th, and 10th.

This has since been fixed: tool access is now derived from department
capability (`is_technical_department()`) rather than a hardcoded id list, and
`MorphogenesisEngine` sets `tools_enabled=True` on the agents it synthesizes. A
regression test fuzzes 30 morphogenesis seeds and asserts every synthesized pod
can author files. **The Gen 10 results reported here predate that fix.**

### 4.7 Token counts and costs are estimates, not measurements

`src/company.py` (lines 129, 142) derives token counts as `len(text) / 4.0`. The
Gemini API returns exact `usageMetadata.promptTokenCount` and
`candidatesTokenCount`, but `src/llm_factory.py` discards them. Because the
efficiency bonus and cost penalty feed into net fitness, this introduces a
systematic and uncorrected bias into the selection signal. All cost figures in
this report should be treated as approximations.

## 5. Recommended Remediation Before Generation 11

Ordered by impact on benchmark validity:

1. **Replace the verification layer with a real execution harness.** Implement
   genuine venv install, import smoke test, `pytest --json-report`, and live
   OpenTelemetry span counting. This is the single highest-value change.
2. **Backfill all 60 archived scorecards** (Gens 5–10) against the new harness.
   Every scorecard retains its full `run_output.workspace_files` payload, so
   re-scoring requires **no re-runs and no additional inference cost** — only
   compute. This will produce the first honest cross-generation trajectory.
3. **Add an execution dimension to the judge rubric** and remove or re-weight the
   two saturated dimensions.
4. **Remove the silent judge fallback**; mark failed evaluations explicitly and
   exclude them from selection.
5. **Record real token usage** from `usageMetadata` instead of `len/4`.
6. **Fix Vertex token refresh** (Workload Identity via metadata server, cache
   with expiry, 401 → force re-fetch) to eliminate the mid-tournament failures
   that required manual retries in both Gen 9 and Gen 10.

Until items 1 and 2 land, cross-generation net-fitness comparisons should be read
as provisional.
