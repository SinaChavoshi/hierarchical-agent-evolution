# Generation 16 — `V6` Epistemic-Search Cohort (October 6, 2026)

Two runs of the same 26 firm-slots, differing **only in the container image**:

| run | image | commit | what differs | launched (UTC) | status |
|---|---|---|---|---|---|
| **run 1** | `v6-gen16-cohort` | `b80f543` | pilot fixes + A1 frontier/triage + A2 anchored synthesis | 17:07:36 | 26/26 done |
| **run 2** | `v6-gen16-cohort-r2` | `6665432` | run 1 + A3 discipline term + **finding 7 fix** (`ee80df0`) | 17:55:52 | see [§6](#6-run-2--the-same-26-slots-with-the-finding-7-fix) |

Everything in this directory is harvested from the pods' stdout: `outputs/<run>/generation_16/*_result.json` (scorecard, iteration history, token usage), `*_epistemic_tree.json` (ledger + every search trajectory + audit + the policy), `traces/<run>_epistemic_trace.txt` (the `[epistemic]` log lines), `summary_tables.md` / `generation_16_cohort_summary.json` (from [`scripts/summarize_gen16_cohort.py`](../../scripts/summarize_gen16_cohort.py)), `value_telemetry.csv` + `_summary.json` (from [`scripts/extract_value_telemetry.py`](../../scripts/extract_value_telemetry.py)). Run 2 lives in [`../hae_gen16_v6_cohort_r2/`](../hae_gen16_v6_cohort_r2/) with the same layout. Pod logs (`pod_logs/*.log`, base64 tarballs) are git-ignored.

> [!IMPORTANT]
> **How to read the numbers.** Iteration 1 is the unchanged `V5` pass in both modes — `V6` only acts from iteration 2 on. Each lineage's first pass is a fresh draw every time and it varies enormously (the same genome drew 50, 47 and 50 in one lineage; 48, 7 and 7 in another), so **the unit of comparison is the first-pass class, not the lineage**: given a plateau start (44–49) or a catastrophic start (≤ 35), what does the repair loop do next? Nets are not comparable between `V5` and `V6` (the ledger composite re-weights fitness `60/25/15`), so compare held-out trajectories. `n = 3` seeds per lineage; nothing here is a statistically established lift.

---

## 1. Design

- **Population** ([`configs/generation_16_population.json`](../../configs/generation_16_population.json), built by [`scripts/make_gen16_cohort_population.py`](../../scripts/make_gen16_cohort_population.py) from [`configs/generations/gen16_cohort.json`](../../configs/generations/gen16_cohort.json)): the **8 non-converged Gen 15 lineages × 3 seeds** (`crossover_2`, `elite_1`, `crossover_1`, `mutant_2`, `mutant_1`, `crossover_3`, `pareto_2`, `elite_2`) plus the **2 converged lineages × 1 seed as controls** (`mutant_3`, `pareto_1`). Genomes are the Gen 14 parents byte-for-byte except `epistemic_policy = {enabled, search_budget_moves 60, frontier_size 3}` (k = 3, c_puct 1.4, low-prior quota 0.34, role bindings inherited). "Seed" is a replicate label — the LLM is sampled, so each slot is an independent draw.
- **Task / oracle:** `self-hosting-fullstack-iter5`, verifier `self-hosting-benchmark:full_stack_hae`, 50 held-out tests over the four graded modules (`artifacts.py`, `morphogenesis.py`, `harness.py`, `verification_loop.py`), 5 iterations max, `carry_artifacts=False`.
- **Serving:** 4 × vLLM (`Qwen3.8-Flash-Next` NVFP4, TP = 2) behind `llmd-inference-gateway` on **spot** `g4-standard-96` nodes; the 26 firm pods on an on-demand CPU pool (`firm-pool`, `e2-standard-32`, 4 vCPU / 8 Gi per pod) so that GPU preemptions cost only LLM retries. Manifests: [`k8s/hae-gen16-v6-cohort-job.yaml`](../../k8s/hae-gen16-v6-cohort-job.yaml), [`k8s/hae-gen16-v6-cohort-r2-job.yaml`](../../k8s/hae-gen16-v6-cohort-r2-job.yaml), [`k8s/gen16-cohort-serving.yaml`](../../k8s/gen16-cohort-serving.yaml), [`k8s/cluster-setup.sh`](../../k8s/cluster-setup.sh), [`k8s/teardown.sh`](../../k8s/teardown.sh).

---

## 2. Run 1 — headline tables

### 2.1 Outcome by first-pass class (the comparison that matters)

| first pass (`V5`, iteration 1) | runs | reached 50 later | final mean | mean gain | trajectories |
|---|---|---|---|---|---|
| 50 (converged at 1) | 3 | — | 50.0 | — | `[50]` × 3 |
| 44–49 (plateau) | 16 | **2** | 48.6 | +0.7 | `[47,47,49,49,50]`, `[48,48,48,48,50]`, and 14 flat lines at 47–49 |
| ≤ 35 (catastrophic) | 7 | **0** | 26.4 | +9.6 | `[7,7,48,48,48]`, `[7,7,7,7,7]`, `[8,8,21,21,21]`, `[23,32,23,33,33]`, `[27,27,27,27,30]`, `[23,23,23,23,23]` × 2 |

Gen 15 (`V5`, same lineages, `n = 1` each) for reference: of six plateau starts four stayed flat, one gained two tests (`[47, 49, …]`) and one converged (`mutant_3`, `[48, 50]`); the three catastrophic starts recovered to 44–48 by iteration 2–3 (`[7,44,46,48,48]`, `[7,48,…]`, `[11,47,…]`).

### 2.2 Per lineage

| lineage | Gen 15 (`V5`) | run 1 first pass per seed | run 1 final per seed | mean (min–max) | converged |
|---|---|---|---|---|---|
| `crossover_2` | `[47, 49, 49, 49, 49]` | `[48, 47, 49]` | `[48, 48, 49]` | 48.3 (48–49) | 0/3 |
| `elite_1` | `[49, 49, 49, 49, 49]` | `[48, 7, 7]` | `[48, 48, 7]` | 34.3 (7–48) | 0/3 |
| `crossover_1` | `[49, 49, 49, 49, 49]` | `[8, 49, 48]` | `[21, 49, 48]` | 39.3 (21–49) | 0/3 |
| `mutant_2` | `[7, 44, 46, 48, 48]` | `[48, 49, 48]` | `[48, 49, 49]` | 48.7 (48–49) | 0/3 |
| `mutant_1` | `[48, 48, 48, 48, 48]` | `[50, 23, 48]` | `[50, 33, 49]` | 44.0 (33–50) | 1/3 (iter 1) |
| `crossover_3` | `[7, 48, 48, 48, 48]` | `[50, 47, 50]` | `[50, 50, 50]` | 50.0 | 3/3 (iters 1, 5, 1) |
| `pareto_2` | `[11, 47, 47, 47, 47]` | `[27, 23, 23]` | `[30, 23, 23]` | 25.3 (23–30) | 0/3 |
| `elite_2` | `[46, 46, 46, 46, 46]` | `[47, 47, 48]` | `[48, 49, 50]` | 49.0 (48–50) | 1/3 (iter 5) |
| `mutant_3` (control) | `[48, 50]` | `[48]` | `[48]` | 48 | 0/1 |
| `pareto_1` (control) | `[50]` | `[48]` | `[48]` | 48 | 0/1 |

Per-run scorecards (net, gross, in-pod vs A3-recomputed integrity, tokens, cost, minutes) and the per-run mechanics table are in [`summary_tables.md`](summary_tables.md). `elite_2` finals: `[48, 49, 50]`; `elite_2__s1` ended `[47, 48, 48, 48, 48]`.

### 2.3 What the search did (pooled over the 23 runs that searched; 3 converged at iteration 1)

`874` moves · `223` proposal rounds → `453` hypotheses accepted (`96` tabu, `10` duplicate, `101` probe repairs) · `529` experiments: **`107` supported / `227` falsified / `195` refused** (`37 %` of probes were `UNTESTABLE` — `__file__`, `importlib.util.find_spec`, paths) · `122` syntheses: `95` module-check-verified, `27` unwritten (`35` no-op retries, `8` recovered) · `115` forced low-prior picks, `19` of them supported · `174` frontier admissions, `159` questions deferred to the oracle.

**Calibration** ([`value_telemetry_summary.json`](value_telemetry_summary.json), 23 trees, `334` tested hypotheses): the hit rate (still `SUPPORTED`/`CERTIFIED` at the end of the tree) is **`7 %` and flat across prior bins** — `0.082` at prior 0–0.2, `0.094` at 0.2–0.4, `0.036` at 0.4–0.6, `0.025` at 0.6–0.8, `0.087` at 0.8–1.0. Brier `0.196` against `0.067` for always saying the base rate. The proposer's stated confidence carries no information about which mechanism will survive — the empirical case for the `V7` value head, now with `874` labelled moves instead of 95.

Note also the gap between probe-time and end-of-tree verdicts: `107` hypotheses were `SUPPORTED` by their probe, but only `24` were still standing when the tree closed. A probe tests *a prediction of the mechanism*; the oracle tests *the fix*. Most "supported" mechanisms produced a synthesis the oracle rejected, and the next reconciliation reopened the question.

---

## 3. Finding 7 — why the catastrophic starts did not recover in run 1

Every non-recovering run-1 trajectory with a missing-module shape is one defect, visible in the traces ([`traces/gen_14_elite_1__s3_epistemic_trace.txt`](traces/gen_14_elite_1__s3_epistemic_trace.txt), [`traces/gen_14_pareto_2__s3_epistemic_trace.txt`](traces/gen_14_pareto_2__s3_epistemic_trace.txt)):

1. The first pass never wrote one or more graded modules. The oracle reports `[execution_harness] suite failed to import: ModuleNotFoundError: No module named 'hae.evaluation.harness'` (and the same for `verification_loop`, `morphogenesis`).
2. That line did not match the gatekeeper's failure-key regex, so the seeded question had **module `""`** and cluster key `|[execution_harness] suite failed to import…`.
3. The search did its job: `h4 prior=0.85 → SUPPORTED 'FILE_MISSING'` (`pareto_2__s3`), `'harness.py is missing'` (`elite_1__s3`) — correct diagnosis, with evidence, inside 2–3 moves.
4. The synthesis move then **fell back to the first required module, `artifacts.py`**, because the question had no module and the synthesiser can only rewrite an existing file. The oracle saw an unrelated edit (`[REGRESSION] … restoring best workspace snapshot`) and the question came back open. `elite_1__s3` spent `81` moves, `49` experiments and `13` syntheses this way and ended at `7/50` with the same three suites still failing to import; `pareto_2__s3` ended at `23/50` with two.
5. `23` of its `49` probes were refused (`UNTESTABLE`) because the proposer kept reaching for `__file__` / `importlib.util.find_spec` to check whether the file exists — the one thing the stage forbids.

`elite_1__s2` (`[7, 7, 48, 48, 48]`) shows the same first pass recovering when the proposer happened to name the right file; the other three catastrophic starts (`mutant_1__s2`, `pareto_2__s1`, `crossover_1__s1`) are not missing-module cases but a `harness.py` that exists and is wrong in many places (15–20 failures at the end), where the search ground out small gains (`23 → 33`, `27 → 30`, `8 → 21`).

**Fix** (`ee80df0`, in run 2's image, not run 1's): `[tag] suite failed to import` → failure key `<tag>:suite_import` with the tag's module as the question target; a synthesis whose target is absent **authors** it (`mode = author`, stat `syntheses_authored`); the proposer prompt lists the refused constructs and the try/except-ImportError idiom. Tests: `tests/test_epistemic_gatekeeper.py`, `tests/test_epistemic_synthesis.py`.

---

## 4. The plateau — what 48/50 and 49/50 are

Every one of the 19 runs that ended on the plateau ended on the same handful of `execution_harness` held-out tests: `test_pytest_style_functions_are_collected` (10 runs), `test_unittest_style_classes_are_collected` (8), `test_uninstalled_dependency_skips_rather_than_fails` (3), `test_generated_files_are_not_counted` (1). Gen 15's `V5` plateau was the same ceiling. In `elite_1__s1` the search filed `11` hypotheses on the first of these, falsified `9` with evidence (every variant of "the stdlib fallback runner does not collect bare `test_*` functions"), left `2` unverified and never proposed the mechanism the hidden test actually exercises. That is the honest shape of the `V6` limit: **System 2 can only rule out what System 1 proposes**. The two run-1 conversions (`crossover_3__s2` at iteration 5 after `6` verified syntheses, `elite_2__s3` at iteration 5) show it is reachable, late.

The controls are a `V6` cost signal, not a win: `mutant_3` went `48 → 50` in one `V5` iteration in Gen 15 and sat at `48` for four `V6` iterations here (`n = 1` each; its `4` syntheses all passed the module check and none moved the oracle).

---

## 5. Infrastructure, timeline and cost

| | |
|---|---|
| cluster | `chavoshi-v6-cohort`, `gemle-gke-dev`, `us-central1-b`; created 16:54 UTC |
| GPU | `g4-pool`: 4 × spot `g4-standard-96`, inserted 16:55 UTC. Two preemptions in the first 15 min (`jl1z` 17:00 → back 17:03; `gbbt` 17:07 → back 17:17) and one `ZONE_RESOURCE_POOL_EXHAUSTED` window; a `g4-ondemand` pool (2 nodes) requested at 17:11 as a hedge **never provisioned** (repair loop → `ERROR`, 0 instances ever ran, deleted 17:51) |
| CPU | `default-pool` 1 × `e2-standard-8` (gateway); `firm-pool` 4 × `e2-standard-32` on-demand from 17:04, resized to 8 at 17:54 so run 2 could overlap run 1 |
| run 1 | launched 17:07:36 UTC, last worker exit 18:52; firm wall-clock mean `48` min, max `99` min; **`11.5 M` tokens, `$1.82` LLM cost** at the vLLM list price used by `opex` (`$0.02–0.12` and `57 k–918 k` tokens per firm) |
| GPU node-hours (both runs) | **`17.2` spot `g4-standard-96` node-hours** from `gcloud compute operations` timestamps ([`scripts/gpu_node_hours.py`](../../scripts/gpu_node_hours.py)): 3 nodes × `3.5–3.65` h (16:55 → 20:34 UTC, deleted via the MIG once one firm was left) + 1 node × `6.38` h (16:55 → 23:20 UTC). Of the last node's time, ≈ `1.3` h served the final run-2 firm alone and ≈ `1.5` h (≈ 21:50 → 23:20) was **idle before the teardown was issued** — an operator delay, counted here, not hidden. On-demand GPU: `0` h (every insert failed). CPU: `firm-pool` 4 × `e2-standard-32` 17:04 → 20:34 plus 4 more 17:54 → 20:34 and 1 until 23:20; `default-pool` 1 × `e2-standard-8` 16:54 → 23:21 |

A vLLM pod that restarts after a partial model download crash-loops (`SafetensorError: incomplete metadata`); the fix is `kubectl delete pod` (fresh `emptyDir`). The LLM client retries 5× with backoff and the gateway re-discovers replicas every 5 s, so firm pods survived both preemptions without a failed iteration.

---

## 6. Run 2 — the same 26 slots with the finding 7 fix

Full report: [`../hae_gen16_v6_cohort_r2/README.md`](../hae_gen16_v6_cohort_r2/README.md). Run 2 differs from run 1 only in the image (`6665432`: A3 + the finding 7 fix), so the two outcome tables are the controlled comparison:

| first pass (`V5`) | run 1 (`b80f543`) | run 2 (fix) |
|---|---|---|
| **≤ 35 (catastrophic)** | 7 runs · 0 reached 50 · recovered to ≥ 45: **1** · final mean 26.4 | 9 runs · **2** reached 50 (`[7, 50]`, `[8, 50]`) · recovered to ≥ 45: **6** · final mean 40.2 |
| 44–49 (plateau) | 16 runs · 2 reached 50 · final mean 48.6 | 16 runs · 4 reached 50 · final mean 48.3 |
| 50 at iteration 1 | 3 | 1 |

The two run-1-shaped starts in run 2 (`crossover_2__s2`, control `pareto_1__s1`: `7/50`, three suites failing to import) seed **3 questions instead of 18**, and the first move is `h1 prior=0.85 → SUPPORTED 'IMPORT_FAIL_MODULE_NOT_FOUND' → synthesized hae/evaluation/harness.py` — the missing module **authored**, then `7 → 48` at the next oracle call. The trace to read is [`../hae_gen16_v6_cohort_r2/traces/gen_14_mutant_2__s1_epistemic_trace.txt`](../hae_gen16_v6_cohort_r2/traces/gen_14_mutant_2__s1_epistemic_trace.txt): **`7/50 → 50/50` in one iteration, 21 moves**, two of the four verified syntheses from `0.05`-prior hypotheses (one a forced low-prior pick). The three catastrophic starts that did not recover in run 2 are the other shape — `harness.py` exists and is wrong in 15–20 places — which the fix does not address.

Mechanically: refused probes `24 %` vs `37 %` of verdicts, no-op syntheses `19 %` vs `22 %`, forced low-prior picks supported `28 %` vs `17 %`, `syntheses_authored = 5`, questions deferred `295` vs `159`.

> [!WARNING]
> `6/9` vs `1/7` is a clear direction, not a measured rate; the fix changed three things at once (attribution, authoring, proposer prompt); and the plateau row did not move in mean (`n = 16`, different first-pass draws). Read the run-2 report's limits before quoting.

## 7. Limits of this cohort

1. `n = 3` per lineage, and the first-pass draw dominates every trajectory; compare classes, not lineages, and do not quote lifts.
2. Run 1's image predates A3 and the finding 7 fix: its `epistemic_integrity` uses the old discipline term (the summariser reports the A3 recomputation beside it), and its catastrophic-start results measure the bug, not the design.
3. `V5`/`V6` nets are not comparable; Gen 15 is `n = 1` per lineage.
4. The synthesis "verification" is `py_compile` + import, not the failing test; a verified synthesis can and often does leave the oracle unchanged (controls, §4).
5. The one known oracle leak (`V4` monotonic write guard) applies identically to both modes and is documented in the `V6` README.
6. Costs are vLLM list-price estimates from `opex`; GPU time is the dominant real cost and is reported as node-hours, including the idle minutes before the first pod and after the last.

---

## Teardown (verified)

`JOB=hae-gen16-v6-cohort-r2 k8s/teardown.sh` at 23:20:31 UTC: Job → serving overlay → `gcloud container clusters delete chavoshi-v6-cohort` (done 23:24:52), then the script's own check and an independent re-run of `k8s/teardown.sh --verify`:

```
+ gcloud container clusters list --project=gemle-gke-dev --filter=name=chavoshi-v6-cohort
+ gcloud compute instances list --project=gemle-gke-dev --filter=name~chavoshi-v6-cohort
== OK: no GCE instances belong to chavoshi-v6-cohort; nothing is billing.
```

Partial scale-down earlier, once a single firm was left (20:34 UTC): vLLM `4 → 1` with a `pod-deletion-cost` annotation protecting the busy replica, then the three idle GPU nodes and seven idle firm nodes removed by name through their managed instance groups (`gcloud compute instance-groups managed delete-instances`), so the running firm was never evicted.

