# Generation 16 — V6 cohort, run 3 (October 7, 2026): synthesis ladder, oracle reconciliation, V7 heads in shadow mode

Run 3 re-ran the same 26 firm-slots as [run 1](../hae_gen16_v6_cohort/README.md)
and [run 2](../hae_gen16_v6_cohort_r2/README.md) with one change: the image
(`v6-gen16-cohort-r3` = `main @ bdfa962`). Relative to run 2 it adds

- the A4 synthesis ladder (fuzzy-anchored repair plan -> `FUNCTION_REWRITE` ->
  at most one whole-module rewrite, revert on a failed module check),
- oracle reconciliation (a question whose failure changes shape is refreshed
  instead of kept; traceback locations shown to the proposer; a `static_check`
  gate before the oracle sees a synthesis),
- the V7 state-value and hypothesis-prior heads loaded in shadow mode (they
  record what they would have said in `MoveRecord.extra`; no decision changed).

Same population file (`configs/generation_16_r3_population.json`, which only
adds the head paths to each genome's `epistemic_policy.extra`), task, oracle,
serving stack and node pools as run 2. Job manifest:
[`k8s/hae-gen16-v6-cohort-r3-job.yaml`](../../k8s/hae-gen16-v6-cohort-r3-job.yaml).
Pods ran 18:58-23:04 UTC on four vLLM replicas (Qwen3.8-Flash-Next NVFP4) that
were partly preempted during the run; pod wall-clock was 7-183 min, mean 45.

The Job's `ttlSecondsAfterFinished` deleted the pods before they were
harvested, so this directory was rebuilt from Cloud Logging with
`scripts/harvest_from_cloud_logging.py` (26/26 output tarballs recovered;
`harvest_status.json`). Layout as in runs 1 and 2: `outputs/`, `traces/`,
`summary_tables.md`, `generation_16_cohort_summary.json`,
`value_telemetry.csv`, `value_telemetry_summary.json`.

Read all three runs by first-pass class: iteration 1 is the unchanged V5 pass
and is re-drawn every run, so lineage-level comparisons between runs are
mostly comparisons of that draw.

## 1. Outcome by first-pass class, runs 1-3

| first pass (V5) | run 1 (`b80f543`) | run 2 (`6665432`) | run 3 (`bdfa962`) |
|---|---|---|---|
| <= 35 (catastrophic) | 7 runs, 0 reached 50, final mean 26.4, 1 recovered to >= 45 | 9 runs, 2 reached 50, final mean 40.2, 6 recovered to >= 45 | 9 runs, 3 reached 50 (`[21, 50]`, `[32, 32, 50]`, `[10, 49, 49, 50]`), final mean 43.8, 8 recovered to >= 45; one never moved (`elite_1__s1` `[8 x5]`) |
| 44-49 (plateau) | 16 runs, 2 reached 50, final mean 48.6 | 16 runs, 4 reached 50, final mean 48.3 | 11 runs, 5 reached 50 (`[47, 48, 50]`, `[47, 50]`, `[49, 50]`, `[48, 48, 48, 50]`, `[48, 48, 50]`), final mean 49.2 |
| 50 (converged at 1) | 3 | 1 | 6 |

Converged runs in total: 14 of 26 (6 at iteration 1). Per lineage the mean of
the final held-out count over seeds is 47.7-50.0 for every non-converged Gen 15
lineage except `elite_1` (35.0, pulled down by the `[8 x5]` run); the two
controls that converged under V5 drew 49 and stayed there
(`summary_tables.md`). The plateau conversions are 5 of 11 against 2 of 16 and
4 of 16 in the earlier runs; with these counts and a different first-pass mix
per run this is a direction, not a measured rate.

## 2. Mechanics (pooled over the runs that searched)

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| runs that searched / moves / experiments | 23 / 874 / 529 | 25 / 907 / 532 | 20 / 805 / 456 |
| probes refused (`UNTESTABLE`) | 195 (37%) | 126 (24%) | 139 (30%) |
| experiments supported / falsified | 107 / 227 | - | 128 / 189 |
| syntheses: plan / function / authored / whole-module rewrite / unwritten | 0 / - / - / - / 27 of 122 | 44 / - / 5 / 102 / 28 of 151 | 48 / 29 / 5 / 36 / 29 of 147 |
| syntheses verified / reverted after a failed module check | 95 / - | 123 / - | 96 / 19 |
| whole-module rewrites skipped by the double-no-op rule | - | - | 0 |
| synthesis wall-clock | - | 18.8 h | 12.0 h |
| forced low-prior picks -> supported | 115 -> 19 (17%) | 133 -> 37 (28%) | 119 -> 37 (31%) |
| questions deferred to the oracle | 159 | 295 | 184 |
| tabu / duplicate rejections | - | - | 62 / 34 |
| tokens / model cost | 11.5 M / $1.82 | 13.9 M / $2.13 | 13.1 M / $1.93 |

What the ladder did: the whole-module rewrite went from the default (102 of
151 syntheses in run 2) to the last resort (36 of 147), 29 syntheses were
written by `FUNCTION_REWRITE`, and total synthesis time fell by a third with
the same number of syntheses. The double-no-op skip never fired: in run 3 the
plan and the function rewrite were never both no-ops on the same hypothesis,
so the rule cost nothing and saved nothing. 19 syntheses were reverted by the
static gate / module check before the oracle saw them (7 in
`crossover_1__s3`, 5 in `pareto_2__s2`).

## 3. Calibration and the V7 heads

`value_telemetry_summary.json`, 317 tested hypotheses: hit rate by prior bin
0.25 / 0.24 / 0.11 / 0.14 / 0.33 (0-0.2 ... 0.8-1.0), Brier 0.264 against
0.176 for the base-rate predictor. The proposer's stated prior is still
uninformative, but the pooled hit rate is 23% against 7% in run 1 and 11% in
run 2: more hypotheses survived their experiment, which is consistent with
questions no longer describing a symptom that had already changed.

Run 3 is the first data the V7 heads did not train on (they were fit on the 48
run-1/run-2 trees). Scored on the recorded shadow predictions in
`value_telemetry.csv` (`x_head_p`, `x_head_v`; labels are end-of-tree status,
as in training):

| head | rows | base rate | head Brier / AUC | comparison |
|---|---|---|---|---|
| hypothesis prior (`x_head_p`) on experiment rows with a verdict | 369 | 0.241 | 0.203 / 0.594 | stated prior 0.279 / 0.485; always-base-rate 0.183 |
| state value (`x_head_v`) on all moves | 805 | 0.585 | 0.270 / 0.809 | V6 heuristic value 0.457 / 0.32; always-base-rate 0.243 |

Both heads rank better than what they replace (the prior head's AUC 0.59
against 0.49 for the LLM's own prior; the value head's 0.81 against 0.32 for
the hand-built heuristic, which is anti-correlated with certification here),
and both are miscalibrated on this run because its base rates are two to three
times those of the training runs. Neither beats the always-base-rate Brier.
Head and stated prior agreed on the top-ranked hypothesis in 143 of 456
experiment moves. These are single-run, end-of-tree-labelled numbers; the
decision they inform is whether to turn `prior_head_weight` up in a later
cohort, not a claim about the heads' value.

## 4. Traces worth reading

- `traces/gen_14_elite_1__s1_epistemic_trace.txt`: `[8, 8, 8, 8, 8]`, 90 moves,
  47 hypotheses (14 tabu, 15 duplicate rejections), 15 syntheses with 7
  verified and none that moved the oracle. The one run the ladder and the
  reconciliation did not help.
- `traces/gen_14_crossover_1__s2_epistemic_trace.txt`: `[21, 50]` in 12 moves,
  one authored module.
- `traces/gen_14_crossover_1__s3_epistemic_trace.txt`: `[23, 32, 48, 48, 48]`,
  18 syntheses of which 7 were reverted by the module check and 6 written by
  `FUNCTION_REWRITE`; 92 minutes of synthesis.
- `traces/gen_14_pareto_2__s3_epistemic_trace.txt`: `[48, 49, 49, 49, 49]` with
  166 minutes of synthesis for 10 syntheses (4 verified): the slow tail is
  still whole-module generation at single-stream decode speed.

## 5. Limits

Everything in the run 1 and run 2 limits applies. In addition: three things
changed at once between run 2 and run 3 (ladder, reconciliation, static gate),
so none of the mechanical shifts above can be attributed to one of them; the
first-pass mix differs from both earlier runs (6 converged at iteration 1
against 3 and 1); vLLM replicas were preempted and rescheduled during the run,
which affects wall-clock but not outcomes; and the head evaluation uses
end-of-tree labels, which credit every move on a question that was eventually
certified.
