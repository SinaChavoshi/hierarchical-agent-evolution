# V2: ground-truth self-hosting, Generations 1-6

V2 is the second experiment set of Hierarchical Agent Evolution (HAE). It replaced V1's open-ended prose objectives with a ground-truth benchmark: each competing multi-agent organisation (a "firm" of 31-45 LLM agents) re-implements a module of this repository, [`hae/evaluation/artifacts.py`](../../hae/evaluation/artifacts.py), from its specification and is graded against a held-out unit test suite the agents never see. This document covers Generations 1-6: 6/6 generations complete, 60/60 firms completed, $28.18 total spend. Generations 7-9 (V3) are documented in [`../v3_rsi/README.md`](../v3_rsi/README.md); they appear in the table and chart below because they share the pipeline, and Finding 6 records a seeding bug in their runs.

## Setup

Common to all six generations:

- Target: `hae/evaluation/artifacts.py`, built from an empty workspace (`carry_artifacts: false`); 0/10 firms per generation inherit the target file.
- Grading: the held-out suite [`tests/test_artifacts.py`](../../tests/test_artifacts.py) (7 tests) runs in a sandbox with no network (`unshare -rn`). The fraction passed is the firm's execution integrity score, weighted at 30% of composite fitness.
- Budget: $5.00 per firm, `max_calls: 650`.
- Ranking: net fitness is the gross rubric score minus an operating-expense (OpEx) penalty of 20 points per dollar spent above the $0.50 single-iteration baseline.
- Population: 10 firms per generation, bred from the previous top 5 by four operators: elite (unmodified clone of a parent; the control), crossover (recombination of two parents), pareto (a parent amplified on one scoring dimension) and mutant (topology mutation by `MorphogenesisEngine`).

The two campaigns differ in one setting:

- Campaign 1 (Gen 1-3): one pass per firm (`max_iterations: 1`).
- Campaign 2 (Gen 4-6; "Option C" in the task config): up to ten passes (`max_iterations: 10`). After each pass the firm receives the failing test names and `AssertionError` or import exception messages (`FAIL: <test> -> <AssertionError / ImportError>`), not the test source, and repairs until the score reaches 100% or the budget is exhausted. Campaign 2 also introduced the Monotonic Verification Guard (Finding 5).

## Results

Campaign 1 raised mean elite-control fitness by 14.56 points (76.53 to 91.09), but no firm (0/30) passed all seven tests in one pass; the best single-pass score was 94.16 net at 6/7. In Campaign 2 **all 30 firms passed 7/7**, peak net fitness reached **98.81** (`gen_5_pareto_2`), mean iterations to converge fell from 2.20 to 1.30, the first-iteration 7/7 rate rose from 0% to 70%, and generation spend fell 33% ($7.26 to $4.87). The findings below give the details.

| Gen | Mode | Spec | Best net fitness (firm) | Cohort mean | Firms at 7/7 | Mean exec. score | Mean iterations | Spend | Target inherited |
| :-: | :-- | :-- | :-- | :-: | :-: | :-: | :-: | :-: | :-: |
| 1 | Campaign 1, 1 pass | [`gen1.json`](../../configs/generations/gen1.json) | 94.03 (`gen_1_mutant_5`) | 81.86 | 0/10 | 60.0% | 1.00 | $3.45 | 0/10 |
| 2 | Campaign 1, 1 pass | [`gen2.json`](../../configs/generations/gen2.json) | 94.16 (`gen_2_crossover_3`) | 71.35 | 0/10 | 41.4% | 1.00 | $3.38 | 0/10 |
| 3 | Campaign 1, 1 pass | [`gen3.json`](../../configs/generations/gen3.json) | 93.94 (`gen_3_elite_1`) | 70.42 | 0/10 | 51.4% | 1.00 | $3.39 | 0/10 |
| 4 | Campaign 2, 10 passes | [`gen4.json`](../../configs/generations/gen4.json) | 98.14 (`gen_4_crossover_3`) | 86.76 | 10/10 | 100.0% | 2.20 | $7.26 | 0/10 |
| 5 | Campaign 2, 10 passes | [`gen5.json`](../../configs/generations/gen5.json) | 98.81 (`gen_5_pareto_2`) | 79.40 | 10/10 | 100.0% | 1.60 | $5.83 | 0/10 |
| 6 | Campaign 2, 10 passes | [`gen6.json`](../../configs/generations/gen6.json) | 98.39 (`gen_6_crossover_3`) | 87.48 | 10/10 | 100.0% | 1.30 | $4.87 | 0/10 |
| 7 | V3 seed, `morphogenesis.py`, zero-seed | [`gen7.json`](../../configs/generations/gen7.json) | 96.83 (`gen_7_pareto_1`) | 71.77 | 10/10 | 100.0% | 1.50 | $6.69 | 0/10 |
| 8 | V3 closed-loop bootstrap 1, overlay-seeded* | [`gen8.json`](../../configs/generations/gen8.json) | 97.92 (`gen_8_pareto_2`) | 74.52 (median 79.00) | 10/10 | 100.0% | 1.30* | $6.34 | 10/10* |
| 9 | V3 closed-loop bootstrap 2, overlay-seeded* | [`gen9.json`](../../configs/generations/gen9.json) | 94.89 (`gen_9_crossover_1`) | 72.71 (median 71.91) | 10/10 | 100.0% | 1.00* | $5.88 | 10/10* |

Every generation launched 10 firms and all 10 completed. Rows marked * started with the parent's passing `morphogenesis.py` already in the workspace, so their iteration counts do not measure blank-slate synthesis (Finding 6). The per-generation ledgers for the V2 rows are [`ledger.json`](ledger.json) (Gen 1-3) and [`ledger_iter10.json`](ledger_iter10.json) (Gen 4-6).

![V2 and V3 trajectory: ground-truth self-hosting and closed-loop RSI across Generations 1-9](assets/v2_v3_evolutionary_trajectory.png)

## Findings

### 1. Selection removed the seed controls, then a fragile winner (Gen 1-2)

Generation 1 was seeded with two unmodified copies of the seed genome (`gen_1_elite_1`, `gen_1_elite_2`) and eight `MorphogenesisEngine` topology mutants (`mutant_1` to `mutant_8`). All five survivor slots went to mutants (`mutant_5` 94.03, `mutant_3` 94.01, `mutant_1` 93.76, `mutant_8` 93.61, `mutant_2` 90.87); both controls were eliminated.

Because every firm rebuilds the module from scratch, Generation 2 separated heritable competence from one-run luck. All three direct offspring of the Gen 1 winner `gen_1_mutant_5` (`gen_2_elite_1`, `gen_2_pareto_1`, `gen_2_crossover_1`) failed on cross-department coordination and scored 0.0% execution integrity. `gen_2_crossover_3`, a structural crossover of `gen_1_mutant_1` and `gen_1_mutant_8`, led at 94.16 (6/7 tests, 85.71%).

### 2. Elite fitness compounded in Generation 3

After Gen 2 dropped the `gen_1_mutant_5` lineage and bred forward `gen_2_crossover_3` and `gen_2_pareto_2`, both Gen 3 elite controls reproduced 85.71% execution integrity (`gen_3_elite_1` at 93.94, `gen_3_elite_2` at 88.23). Mean elite-control fitness rose from 76.53 in Gen 1 to 91.09 in Gen 3, a gain of **14.56 points**.

### 3. Ten-iteration self-repair reached 7/7 in every firm (Gen 4-7)

In Campaign 1 no firm (0/30) passed all seven tests; the misses were two edge cases in `is_malformed_path`, a trailing dot (`notes.`) and a leading hyphen (`-flag`). With ten iterations and the failure diagnostics above, all 40 zero-seed firms in Generations 4-7 (Gen 4-6 on `artifacts.py`, Gen 7 on `morphogenesis.py`) built the target from an empty workspace (0/10 target files in `inherited_files`) and passed 7/7.

Repair iterations recovered firms from small assertion failures and from module-level import or truncation errors. In Gen 4, `gen_4_pareto_2` converged on iteration 2 (91.70 net), `gen_4_mutant_1` on iteration 3 (90.62), `gen_4_mutant_2` on iteration 4 (75.30) and `gen_4_elite_1` on iteration 7 (77.40). In Gen 5, `gen_5_pareto_2` set the campaign peak of 98.81 net (98.10 gross rubric, 7/7, $0.3574, iteration 1).

### 4. Selection favoured firms that converge early (Gen 4-6)

The OpEx penalty makes late convergence expensive: `gen_4_elite_1` spent $2.28 over 7 iterations, which cut its 92.40 gross rubric to 77.40 net and dropped it out of the top-5 breeding pool. The breeder therefore selected for genomes that pass on iteration 1 or 2:

- Mean iterations to converge fell 2.20 (Gen 4), 1.60 (Gen 5), 1.30 (Gen 6), and spend fell $7.26, $5.83, $4.87, a 33% drop.
- The first-iteration 7/7 rate was 0% in Campaign 1 and 70% (7/10) in Gen 6, with the other 3/10 converging on iteration 2. No Gen 6 firm needed more than two iterations.
- Cohort mean net fitness reached 87.48 in Gen 6, the highest in the table, with five firms above 97.10.

### 5. Monotonic Verification Guard

Telemetry during Campaign 2 showed a failure mode of multi-department firms: an upstream engineering department (Systems Engineering and Infrastructure Architecture) wrote an `artifacts.py` that passed 7/7, and a downstream audit department (Adversarial Audit and Red Team Verification, or Formal Verification) then overwrote it with a mock stub or an inline test script at the end of the pass.

The fix is a check in `AgentWorkspace.write_file` ([`hae/runtime/workspace.py`](../../hae/runtime/workspace.py)): a write to a self-hosting target module that would lower its verification score is rejected, and the agent is told to put red-team or test code in a separate file such as `tests/test_artifacts.py`. The overwrites did not recur in the remaining runs.

### 6. V3 (Gen 7-9) and the overlay pre-seeding erratum

In V3 the firms re-implemented HAE's breeding engine, `hae/genome/morphogenesis.py` (`MorphogenesisEngine` and `StructuralCrossoverEngine`). Implementations that passed 100% were promoted into the firm's `genome.code_overlays` and loaded by the breeder through `hae/runtime/overlay.py:get_overlay_class` to breed Generations 8 and 9; this is the Level-3 closed-loop RSI setting, in which evolved code breeds the next generation.

Generation 7 was zero-seed: `code_overlays` held only `hae/evaluation/artifacts.py`, so all 10 firms started with no `morphogenesis.py` on disk. 10/10 passed 7/7, 6/10 (60%) converged on iteration 1, mean iterations were 1.50 and peak net fitness was 96.83.

Generations 8 and 9 were not. A post-run audit of agent deliberation transcripts (`dept_product_ux` in `gen_9_crossover_1_result.json`) and of `run_output.inherited_files` explains their 80% (Gen 8) and 100% (Gen 9) first-iteration convergence:

- In [`hae/orchestration/worker.py`](../../hae/orchestration/worker.py), the filter meant to exclude the benchmark's `target_module` from `seed_files` when `carry_artifacts=False` read `getattr(task.verifier, "target_module", None)`. `BenchmarkVerifier` stores the value at `verifier.benchmark.task(verifier.task_id).target_module`, so the lookup returned `None` and the filter never applied.
- Once Gen 7 promoted `morphogenesis.py` into `code_overlays`, `worker.py` copied the parent's passing `morphogenesis.py` into `seed_files` at the start of Gen 8 and Gen 9 (target in `inherited_files`: 10/10), and the Monotonic Verification Guard then blocked any agent from replacing it with a lower-scoring draft.

Generations 1-7 (70 firms) were genuine zero-seed synthesis (0/10 inherited), and the Gen 7 -> 8 -> 9 breeding did execute the evolved `MorphogenesisEngine` and `StructuralCrossoverEngine` overlays. The Gen 8 and Gen 9 workspace scores (80% and 100% iteration-1 convergence), however, measure overlay-seeded verification, not blank-slate synthesis. The bug was fixed in `hae/orchestration/worker.py`, commit `71a7da4`.

## Standings by generation

Per-firm result files are in `generation_N_results/` under this directory. Operator is the breeding operator that produced the firm; parents are firm IDs from the previous generation. All 30 firms in Generations 4-6 scored 100% execution integrity (7/7 tests), so those two columns are omitted from their tables; the iterations column is the number of repair passes used, out of a maximum of 10.

### Generation 1 (`generation_1_results/`)

All Gen 1 firms descend from the `default_company` seed.

| Rank | Firm | Operator | Exec. integrity | Tests | Composite fitness | Cost | Tokens |
| :-: | :-- | :-- | :-: | :-: | :-: | :-: | :-: |
| 1 | `gen_1_mutant_5` | Mutant | 85.71 | 6/7 | 94.03 | $0.3162 | 349,314 |
| 2 | `gen_1_mutant_3` | Mutant | 85.71 | 6/7 | 94.01 | $0.2995 | 410,280 |
| 3 | `gen_1_mutant_1` | Mutant | 85.71 | 6/7 | 93.76 | $0.3107 | 334,343 |
| 4 | `gen_1_mutant_8` | Mutant | 85.71 | 6/7 | 93.61 | $0.3603 | 387,044 |
| 5 | `gen_1_mutant_2` | Mutant | 71.43 | 5/7 | 90.87 | $0.2925 | 320,751 |
| 6 | `gen_1_elite_1` | Elite | 85.71 | 6/7 | 87.80 | $0.3429 | 463,919 |
| 7 | `gen_1_mutant_7` | Mutant | 42.86 | 3/7 | 76.75 | $0.4225 | 600,609 |
| 8 | `gen_1_mutant_4` | Mutant | 57.14 | 4/7 | 73.90 | $0.3884 | 594,486 |
| 9 | `gen_1_elite_2` | Elite | 0.00 | 0/7 | 65.25 | $0.3707 | 541,796 |
| 10 | `gen_1_mutant_6` | Mutant | 0.00 | 0/7 | 48.57 | $0.3456 | 435,985 |

### Generation 2 (`generation_2_results/`)

| Rank | Firm | Operator | Parents | Exec. integrity | Tests | Composite fitness | Cost | Tokens |
| :-: | :-- | :-- | :-- | :-: | :-: | :-: | :-: | :-: |
| 1 | `gen_2_crossover_3` | Crossover | `mutant_1` × `mutant_8` | 85.71 | 6/7 | 94.16 | $0.3098 | 401,512 |
| 2 | `gen_2_pareto_2` | Pareto | `gen_1_mutant_3` | 71.43 | 5/7 | 90.31 | $0.3438 | 433,190 |
| 3 | `gen_2_crossover_2` | Crossover | `mutant_3` × `mutant_1` | 85.71 | 6/7 | 84.60 | $0.3811 | 508,912 |
| 4 | `gen_2_elite_2` | Elite | `gen_1_mutant_3` | 85.71 | 6/7 | 84.55 | $0.2926 | 409,996 |
| 5 | `gen_2_elite_1` | Elite | `gen_1_mutant_5` | 0.00 | 0/7 | 67.41 | $0.3372 | 440,120 |
| 6 | `gen_2_crossover_1` | Crossover | `mutant_5` × `mutant_3` | 0.00 | 0/7 | 66.21 | $0.3788 | 512,440 |
| 7 | `gen_2_pareto_1` | Pareto | `gen_1_mutant_5` | 0.00 | 0/7 | 64.58 | $0.3636 | 489,102 |
| 8 | `gen_2_mutant_3` | Mutant | `gen_1_mutant_1` | 0.00 | 0/7 | 59.05 | $0.3904 | 541,208 |
| 9 | `gen_2_mutant_1` | Mutant | `gen_1_mutant_5` | 85.71 | 6/7 | 57.69 | $0.3032 | 412,880 |
| 10 | `gen_2_mutant_2` | Mutant | `gen_1_mutant_3` | 0.00 | 0/7 | 44.98 | $0.2838 | 388,115 |

### Generation 3 (`generation_3_results/`)

| Rank | Firm | Operator | Parents | Exec. integrity | Tests | Composite fitness | Cost | Tokens |
| :-: | :-- | :-- | :-- | :-: | :-: | :-: | :-: | :-: |
| 1 | `gen_3_elite_1` | Elite | `gen_2_crossover_3` | 85.71 | 6/7 | 93.94 | $0.4144 | 561,024 |
| 2 | `gen_3_elite_2` | Elite | `gen_2_pareto_2` | 85.71 | 6/7 | 88.23 | $0.2569 | 348,910 |
| 3 | `gen_3_pareto_1` | Pareto | `gen_2_crossover_3` | 85.71 | 6/7 | 84.98 | $0.3057 | 419,882 |
| 4 | `gen_3_crossover_3` | Crossover | `crossover_2` × `elite_2` | 85.71 | 6/7 | 84.29 | $0.2835 | 391,004 |
| 5 | `gen_3_crossover_1` | Crossover | `crossover_3` × `pareto_2` | 85.71 | 6/7 | 76.85 | $0.3913 | 528,412 |
| 6 | `gen_3_mutant_3` | Mutant | `gen_2_crossover_2` | 0.00 | 0/7 | 60.09 | $0.3628 | 495,110 |
| 7 | `gen_3_mutant_1` | Mutant | `gen_2_crossover_3` | 0.00 | 0/7 | 58.77 | $0.4064 | 552,190 |
| 8 | `gen_3_pareto_2` | Pareto | `gen_2_crossover_3` | 0.00 | 0/7 | 58.64 | $0.3921 | 531,008 |
| 9 | `gen_3_mutant_2` | Mutant | `gen_2_pareto_2` | 85.71 | 6/7 | 54.77 | $0.2877 | 394,120 |
| 10 | `gen_3_crossover_2` | Crossover | `pareto_2` × `crossover_2` | 0.00 | 0/7 | 43.65 | $0.2902 | 398,770 |

### Generation 4 (`generation_4_results/`)

| Rank | Firm | Operator | Gross rubric | Iterations | Net fitness | Cost |
| :-: | :-- | :-- | :-: | :-: | :-: | :-: |
| 1 | `gen_4_crossover_3` | Crossover | 97.50 | 1 | 98.14 | $0.3711 |
| 2 | `gen_4_mutant_3` | Mutant | 95.80 | 1 | 96.68 | $0.3249 |
| 3 | `gen_4_crossover_2` | Crossover | 93.00 | 1 | 94.01 | $0.2987 |
| 4 | `gen_4_pareto_1` | Pareto | 92.70 | 1 | 92.89 | $0.4624 |
| 5 | `gen_4_pareto_2` | Pareto | 96.00 | 2 | 91.70 | $0.7151 |
| 6 | `gen_4_mutant_1` | Mutant | 95.80 | 3 | 90.62 | $0.7591 |
| 7 | `gen_4_elite_2` | Elite | 78.60 | 1 | 79.38 | $0.3431 |
| 8 | `gen_4_elite_1` | Elite | 92.40 | 7 | 77.40 | $2.2800 |
| 9 | `gen_4_mutant_2` | Mutant | 90.30 | 4 | 75.30 | $1.3233 |
| 10 | `gen_4_crossover_1` | Crossover | 70.90 | 1 | 71.49 | $0.3823 |

### Generation 5 (`generation_5_results/`)

| Rank | Firm | Operator | Gross rubric | Iterations | Net fitness | Cost |
| :-: | :-- | :-- | :-: | :-: | :-: | :-: |
| 1 | `gen_5_pareto_2` | Pareto | 98.10 | 1 | 98.81 | $0.3574 |
| 2 | `gen_5_crossover_1` | Crossover | 97.80 | 1 | 97.90 | $0.4804 |
| 3 | `gen_5_elite_1` | Elite | 97.10 | 1 | 97.60 | $0.4001 |
| 4 | `gen_5_mutant_1` | Mutant | 93.60 | 1 | 93.84 | $0.4525 |
| 5 | `gen_5_crossover_3` | Crossover | 92.90 | 2 | 90.77 | $0.6066 |
| 6 | `gen_5_pareto_1` | Pareto | 73.40 | 1 | 74.09 | $0.3623 |
| 7 | `gen_5_mutant_3` | Mutant | 73.00 | 1 | 74.07 | $0.2856 |
| 8 | `gen_5_elite_2` | Elite | 72.00 | 4 | 57.00 | $1.3321 |
| 9 | `gen_5_mutant_2` | Mutant | 61.00 | 2 | 56.49 | $0.7257 |
| 10 | `gen_5_crossover_2` | Crossover | 60.00 | 2 | 53.46 | $0.8269 |

### Generation 6 (`generation_6_results/`)

| Rank | Firm | Operator | Gross rubric | Iterations | Net fitness | Cost |
| :-: | :-- | :-- | :-: | :-: | :-: | :-: |
| 1 | `gen_6_crossover_3` | Crossover | 97.80 | 1 | 98.39 | $0.3821 |
| 2 | `gen_6_mutant_3` | Mutant | 97.90 | 1 | 98.33 | $0.4134 |
| 3 | `gen_6_mutant_1` | Mutant | 97.00 | 1 | 97.50 | $0.4000 |
| 4 | `gen_6_elite_1` | Elite | 96.60 | 1 | 97.20 | $0.3803 |
| 5 | `gen_6_crossover_2` | Crossover | 96.50 | 1 | 97.16 | $0.3680 |
| 6 | `gen_6_crossover_1` | Crossover | 96.50 | 2 | 92.41 | $0.7045 |
| 7 | `gen_6_elite_2` | Elite | 88.00 | 1 | 88.69 | $0.3611 |
| 8 | `gen_6_mutant_2` | Mutant | 85.60 | 1 | 86.33 | $0.3550 |
| 9 | `gen_6_pareto_1` | Pareto | 74.50 | 2 | 69.82 | $0.7340 |
| 10 | `gen_6_pareto_2` | Pareto | 54.50 | 2 | 49.02 | $0.7742 |
