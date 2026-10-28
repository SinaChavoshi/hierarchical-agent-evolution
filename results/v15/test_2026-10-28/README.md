# V15 results: evolved harness vs. random search vs. hand-designed seed

*Oct 28, 2026. Model: `reflection/Beam-1.0-501B-A23B-NVFP4` (+ DFlash2) behind the llm-d v1.0.0 router.*

## Final numbers

| Harness | How it was produced | Train (21 dev) | Validation (10 dev) | **Test (28 unseen Verified)** | Test mean tokens |
| :--- | :--- | :---: | :---: | :---: | ---: |
| `seed` | Hand-designed V14 with gold-fix detectors and task hints removed | 5/21 | 4/10 | **12/28 (43%)** | 192k |
| **`evo_g5_03`** | Evolved, 6 generations (3 accumulated code edits) | **7/21** | **6/10** | **17/28 (61%)** | 241k |
| `evo_g2_00` | Best evolved genome in generations 0–3 (code edit 1 + settings) | 6/21 | 2/10 | 12/28 (43%) | 153k |
| `ran_g0_07` | Best random-search genome in generations 0–3 (settings only) | 5/21 | — | 16/28 (57%) | 209k |
| *V12 flat ReAct* | *From V14 (lighter load; prompts contained dev-task examples)* | — | — | *19/28* | *146k* |
| *V14 original* | *From V14 (contaminated: gold-fix detectors + test-task hints)* | — | — | *21/28* | *232k* |

Paired comparisons on the test split (tasks where exactly one harness succeeded; exact two-sided sign test):

| Comparison | Wins : losses | p |
| :--- | :---: | :---: |
| `evo_g5_03` vs `seed` | 5 : 0 | 0.063 |
| `ran_g0_07` vs `seed` | 4 : 0 | 0.125 |
| `evo_g2_00` vs `seed` | 3 : 3 | 1.0 |
| `evo_g2_00` vs `ran_g0_07` (matched budget) | 0 : 4 | 0.125 |
| `evo_g5_03` vs `ran_g0_07` | 1 : 0 | 1.0 |
| `evo_g5_03` vs V12 flat ReAct | 1 : 3 | 0.625 |

## Conclusions

1. **Search beats the hand-designed seed.** The best evolved harness (`evo_g5_03`) solved 17/28
   unseen tasks against the seed's 12/28 and never lost a task to it (5 : 0, p = 0.063). Random
   settings search also beat the seed (4 : 0). Neither difference reaches p < 0.05 at n = 28.
2. **Evolution did not beat random search at matched budget.** After 4 generations each, the
   evolved pick (`evo_g2_00`, chosen by training fitness) lost 0 : 4 to the random pick. Its
   training score (6/21) was largely luck: it fell to 2/10 on validation. With 2 more generations,
   evolution's best (`evo_g5_03`) only ties random search (1 : 0).
3. **Code evolution produced a real, generic improvement.** `evo_g5_03` improved on every split
   (train 7 vs 5, validation 6 vs 4, test 17 vs 12) through three small domain-general edits:
   keep up to 8 candidate files per branch, give the planner more context on failed attempts, and
   require re-running the reproduction script and existing tests before `submit`. All passed the
   leakage gate.
4. **The clean hierarchical harness does not beat flat ReAct.** V12 flat ReAct scored 19/28 on
   the same tasks in V14. That run had lighter load and dev-task examples in its prompts, so it
   is not a controlled comparison, but nothing here shows the hierarchy outperforming a single
   ReAct loop once the contamination is removed.

## How the test was run, and what went wrong

- The test split was used once: 4 genomes × 28 tasks. Grading uses `scripts/grade_in_testbed.py`
  (approximate); an official `swebench eval` re-grade is still to do.
- A first attempt at 60 concurrent agents overloaded the Kubernetes API and the sandbox nodes and
  was discarded entirely. A second attempt at 24 agents reused deterministic pod names while the
  aborted run's pods were still terminating, so several runs (mostly the seed's) attached to pods
  that then disappeared and produced empty patches. **Every run with a pod, Kubernetes API or
  starved-sandbox marker in its log was discarded and re-run** with unique pod names, 2 CPU / 6 GiB
  sandbox pods and 16 agents. The final table contains 112 valid runs and no infrastructure failures.
- All four genomes ran in the same window under the same router and replicas, so the comparison
  between them is fair. Absolute scores are lower than lightly loaded runs; 5 runs hit the 1650 s
  agent wall-clock limit (4 of them `evo_g5_03`).
- Random search was stopped after generation 3 of 6 (time constraint); the matched-budget
  comparison uses generations 0–3 for both methods.

Raw data: `summary.json` (per-task results), `../evolve_2026-10-27/state.json`,
`../random_2026-10-27/state.json`, `../validation_2026-10-28/val_state.json`.
Analysis script: [`scripts/v15_test_report.py`](../../../scripts/v15_test_report.py).
