# Generation 16 — `V6` Cohort, Run 2 (October 6, 2026): the same 26 slots with the finding 7 fix

Run 2 re-ran the 26 firm-slots of [run 1](../hae_gen16_v6_cohort/README.md) with **one change: the image** — `v6-gen16-cohort-r2` = `main @ 6665432` = run 1's image (`b80f543`) plus the A3 discipline term in the fitness audit and the **finding 7 fix** (`ee80df0`): a graded suite that cannot import is attributed to its module, a synthesis whose target module is absent *authors* it, and the proposer prompt names the probe constructs the gatekeeper refuses. Same population file, task, oracle, serving stack and node pools; launched 17:55:52 UTC while run 1 was finishing (firm-pool resized 4 → 8), so the two runs shared the four vLLM replicas for an hour. Layout as in run 1 (`outputs/`, `traces/`, `summary_tables.md`, `generation_16_cohort_summary.json`, `value_telemetry.csv`); manifest [`k8s/hae-gen16-v6-cohort-r2-job.yaml`](../../k8s/hae-gen16-v6-cohort-r2-job.yaml).

> [!IMPORTANT]
> Read both runs by **first-pass class**. Iteration 1 is the unchanged `V5` pass and is re-drawn every run (the control `pareto_1` drew `48` in run 1 and `7` in run 2), so lineage-level comparisons between the runs are mostly comparisons of luck. The question the two runs answer together is: *given the same kind of start, what does the repair loop do next, with and without the fix?*

## 1. Run 1 vs run 2 by first-pass class

| first pass (`V5`) | run 1 (`b80f543`) | run 2 (`6665432`, fix) |
|---|---|---|
| **≤ 35 (catastrophic)** | 7 runs · **0** reached 50 · final mean 26.4 · gain +9.6 · recovered to ≥ 45: **1** (`[7,7,48,48,48]`) | 9 runs · **2** reached 50 (`[7, 50]`, `[8, 50]`) · final mean 40.2 · gain +25.6 · recovered to ≥ 45: **6** (`[7,48,…]` ×2, `[11,49,…]`, `[21,21,21,21,45]`, plus the two 50s) |
| **44–49 (plateau)** | 16 runs · 2 reached 50 (both at iteration 5) · final mean 48.6 · gain +0.7 | 16 runs · 4 reached 50 (`[45,50]`, `[48,50]`, `[49,49,50]`, `[46,48,50]`) · final mean 48.3 · gain +1.2 |
| **50 (converged at 1)** | 3 | 1 |

The catastrophic row is the one the fix was built for, and it moved: six of nine starts recovered instead of one of seven. The three that did not (`crossover_1__s1` `[26 ×5]`, the control `mutant_3__s1` `[23 ×5]`, `elite_2__s3` `[22, 23, 23 …]`) are the other catastrophic shape — `harness.py` *exists* and is wrong in 15–20 places — where the search grinds (e.g. `mutant_3__s1`: 60-move iterations, dozens of supported-then-overturned hypotheses) without finding the one rewrite that flips many tests. The plateau row barely moved in mean and should not be read as a lift: `n = 16`, the two runs drew different first passes (run 2 has three `[47 ×5]` lines from one lineage), and the fix also changed the proposer prompt, so even the 2 → 4 conversions cannot be attributed to a single mechanism.

## 2. What changed mechanically (pooled over the runs that searched)

| | run 1 | run 2 |
|---|---|---|
| runs that searched / moves / experiments | 23 / 874 / 529 | 25 / 907 / 532 |
| probes refused (`UNTESTABLE`) | 195 of 529 = **37 %** | 126 of 532 = **24 %** |
| syntheses: verified / unwritten (no-op) | 95 / 27 of 122 (22 % no-op) | 123 / 28 of 151 (19 % no-op) |
| forced low-prior picks → supported | 115 → 19 (17 %) | 133 → 37 (28 %) |
| `syntheses_authored` (missing module written) | n/a (stat did not exist; 0 by construction) | 5 |
| questions deferred to the oracle | 159 | 295 |
| hypotheses still standing at end of tree / tested | 24 / 334 (7 %) | 46 / 406 (11 %) |
| tokens / model cost | 11.5 M / $1.82 | 13.9 M / $2.13 |

Fewer refused probes is the prompt change; fewer no-op syntheses and more deferrals are the attribution fix (a question that names the right module gets a repair plan whose anchors exist, and its cluster siblings can be deferred once it resolves). The authored-module path fired five times; in `crossover_2__s2` and `pareto_1__s1` (both `7/50` with three suites failing to import, the exact run-1 shape) it was the first move of iteration 2 and took the firm to `48/50` at the next oracle call.

## 3. Traces worth reading

- [`traces/gen_14_mutant_2__s1_epistemic_trace.txt`](traces/gen_14_mutant_2__s1_epistemic_trace.txt) — **`7/50 → 50/50` in one iteration, 21 moves.** 18 oracle failures clustered into 4; `q1/h1` (prior 0.85) supported → `harness.py` synthesised → 14 siblings deferred; then **two 0.05-prior hypotheses** (one a forced low-prior pick) supported and synthesised (`morphogenesis.py` by anchored edit, `verification_loop.py`); oracle certifies all 18. Audit Brier `0.51`: the hypotheses that worked were the ones the proposer believed least.
- [`traces/gen_14_mutant_1__s3_epistemic_trace.txt`](traces/gen_14_mutant_1__s3_epistemic_trace.txt) — `8/50 → 50/50`, same shape.
- [`traces/gen_14_crossover_2__s2_epistemic_trace.txt`](traces/gen_14_crossover_2__s2_epistemic_trace.txt) and [`traces/gen_14_pareto_1__s1_epistemic_trace.txt`](traces/gen_14_pareto_1__s1_epistemic_trace.txt) — the authored-module cases: 3 questions seeded (not 18), `h1 prior=0.85 → SUPPORTED 'IMPORT_FAIL_MODULE_NOT_FOUND' → synthesized hae/evaluation/harness.py`, oracle `7 → 48`.
- [`traces/gen_14_crossover_3__s1_epistemic_trace.txt`](traces/gen_14_crossover_3__s1_epistemic_trace.txt) — `[21, 21, 21, 21, 45]`: four flat iterations, then the rewrite; what "late" looks like.
- [`traces/gen_14_mutant_3__s1_epistemic_trace.txt`](traces/gen_14_mutant_3__s1_epistemic_trace.txt) — the control that drew `23/50` and never left it: the exists-but-wrong shape the fix does not address.

## 4. Calibration

Same picture as run 1 — [`value_telemetry_summary.json`](value_telemetry_summary.json), 406 tested hypotheses: hit rate `0.14 / 0.10 / 0.08 / 0.10 / 0.15` across the five prior bins (0–0.2 … 0.8–1.0), i.e. the lowest-prior bin does as well as the highest; Brier `0.220` against `0.100` for the base-rate predictor. Both runs' CSVs together are the first `V7` value-head dataset (one row per move, features prefixed `f_`, `terminal_outcome` = end-of-tree status).

## 5. Cost

Run 2 ran on the same cluster as run 1 (`17.2` spot GPU node-hours for the whole day, teardown verified 23:24 UTC — both in the run-1 report); firm wall-clock was longer (mean `63` min; the slowest run, `mutant_2__s3` `[44, 46, 46, 46, 46]`, took `209` min) because catastrophic-start and plateau searches spend their full 60-move budget and each whole-module synthesis is a 6–7-minute generation at the model's single-stream decode speed — a no-op synthesis chain (re-emit twice, re-prompt, give up) costs 14–26 minutes for nothing. That latency, not tokens, is the cost to attack next (targeted-edit synthesis first, whole-module rewrite only as fallback). Tokens and model cost per run are in `summary_tables.md`.

## 6. Limits

Everything in run 1's limits applies. In addition: run 2's improvement on catastrophic starts is `6/9` vs `1/7` — a clear direction with these counts, not a measured rate; the fix changed three things at once; and run 2's plateau conversions carry the same `n` and prompt-change caveat.
