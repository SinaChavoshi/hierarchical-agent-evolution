# V18 Results: Offline Verifier Calibration, Collaborative Sub-Blackboards & Compute-Matched Test-Time Scaling on Fresh $N = 80$ `SWE-bench Verified`

**Branch**: `v18-collab` (merged into `main`)  
**Benchmark Split**: `data/swebench/v18_splits.json` (`test` / `eval_80`: $N = 80$ fresh, never-touched `SWE-bench Verified` tasks; `eval_24_stratified`: $N = 24$ stratified Wave-1 subset; `dev_retired`: 81 retired tasks from V14–V17)  
**Model & Cluster**: `Beam-501B` (`Qwen3-Coder-Next-FP8`) on GKE `chavoshi-v6-cohort` (`llm-d` vLLM router)  
**Raw Artifacts**:
- `results/v18/ranker_calibration.json` (offline verifier & candidate-pool ranker calibration on $n = 2,525$ container-graded patches across $1,385$ V17 runs)
- `results/v18/showdown_2026-11-02/state.json` (complete per-task grades, token accounting, MCTS trees, candidate pools, and sub-blackboard telemetry across all $400$ evaluations)
- `results/v18/showdown_2026-11-02/report_24.md` (Wave-1 $N = 24$ stratified evaluation report)
- `results/v18/showdown_2026-11-02/report_80.md` (Full $N = 80$ evaluation report)

---

## 1. Executive Summary

Following the V14–V17 retrospective, V18 redesigned both our **experimental methodology** and our **multi-agent collaboration / verification architecture** to address five structural bottlenecks:
1. **Fresh Held-Out Benchmark ($N = 80$)**: All 81 tasks touched across V14, V15, V16, and V17 were permanently retired to `dev_retired`. We constructed a fresh 80-task `SWE-bench Verified` benchmark (`eval_80`) screened offline for standard `pytest` node IDs across 8 repositories (`astropy`, `matplotlib`, `psf/requests`, `pydata/xarray`, `pylint-dev/pylint`, `pytest-dev/pytest`, `scikit-learn`, `sphinx-doc`).
2. **Substrate Fixes (`agentic.py`)**: Added compound read-only turn execution (`execute_compound_readonly_turns`, up to 4 `view_file` / read-only `bash` inspection blocks plus a `blackboard` post in a single LLM turn) and full stripping of model-emitted `</rfl:parameter>` / `<rfl:...>` tags before `str_replace` matching.
3. **Offline Verifier Calibration & Candidate-Pool Ranker ($n = 2,525$ patches)**: Auditing all $2,525$ container-graded candidate patches from V17 (`TP=397, FP=834, TN=962, FN=332`) revealed that V17's binary Red-Team gate suffered a **45.5% False Rejection Rate (`FN / (TP + FN) = 332 / 729`)**, discarding every working patch in **12.4%** of resolvable task-runs (`Resolvable-Run Recall = 87.6%`). Replacing binary gating with the **V18 Candidate-Pool Ranker (`score_candidate_patch`)** achieved **100.0% pool retention** (`0` working patches discarded) and lifted **Resolvable-Run Top-1 Selection Accuracy from 87.6% to 96.0% (+8.4 pp)**.
4. **Structured Multi-Agent Collaboration (`V18_Collab`)**: Replaced flat truncated broadcast notes with **4 role-scoped sub-blackboards** (`#recon-map`, `#builders-room`, `#redteam-chamber`, `#verdicts-and-repros`, `entry_char_limit=1800`), a **Pre-Edit Builder Huddle** with contract-override authority (`74` overrides across 80 tasks), and a **Post-Prototype Synthesis Roundtable (`b_synth`)** (`30` roundtables across 80 tasks).
5. **Compute-Matched Test-Time Scaling Comparison**: Evaluated single-run `Flat_1x_V18` (`3×` seeds = 240 rollouts, `147k` tokens/task) and compute-matched `Flat_3x_Select` (`442k` tokens/task) head-to-head against `V18_Collab` (`555k` tokens/task) and `V18_Evolved` (`577k` tokens/task).

### Headline Showdown Results (`test` / `eval_80`, $N = 80$ Fresh Tasks)

| Arm | Tasks Completed | Resolved ($/ 80$) | Pass Rate (%) | Replicate Totals | Mean Tokens / Task | Efficiency (`Resolved / MTok`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`Flat_1x_V18`** | `80 / 80` | **`50.00`** | **`62.5%`** | `[51, 49, 50]` | `147,399` | **`4.24`** |
| **`Flat_3x_Select` (3x Parallel + Selector)** | `80 / 80` | **`53.00`** | **`66.2%`** | `[53]` | `442,196` | `1.50` |
| **`Flat_3x_PassAt3` (Oracle Union)** | `80 / 80` | **`54.00`** | **`67.5%`** | `[54]` | `442,196` | `1.53` |
| **`V18_Collab` (Generalist Hierarchy + Pool Ranker)** | `80 / 80` | **`52.00`** | **`65.0%`** | `[52]` | `555,277` | `1.17` |
| **`V18_Evolved` (Evolved Role Library + Pool Ranker)** | `80 / 80` | **`48.00`** | **`60.0%`** | `[48]` | `577,305` | `1.04` |

---

## 2. Offline Verifier & Candidate-Pool Ranker Calibration ($n = 2,525$ Patches)

Using all $2,525$ container-graded candidate patches collected across $1,385$ task-runs in V17 (`train_arms_2026-10-30`, `evolve_2026-10-30`, `random_2026-10-30`), we evaluated the V17 binary Red-Team gate against the continuous V18 Candidate-Pool Ranker (`score_candidate_patch`):

| Metric | V17 Binary Red-Team Gate | V18 Default Pool Ranker | V18 Calibrated Pool Ranker |
| :--- | :---: | :---: | :---: |
| **Container-Graded Patches ($n$)** | `2,525` (`1,385` runs) | `2,525` | `2,525` |
| **Confusion Matrix (`TP / FP / TN / FN`)** | `397 / 834 / 962 / 332` | Continuous score | Continuous score |
| **Precision (`TP / (TP + FP)`)** | `32.2%` (`FPR = 67.8%`) | — | — |
| **Recall (`TP / (TP + FN)`)** | `54.5%` (`FRR = 45.5%`) | **`100.0%`** (`0` pool discards) | **`100.0%`** (`0` pool discards) |
| **Specificity (`TN / (TN + FP)`)** | `53.6%` | — | — |
| **$F_1$ Score** | `0.405` | — | — |
| **Patch Ranking AUROC** | `0.540` | `0.549` | **`0.551`** |
| **Resolvable-Run Top-1 Accuracy ($n = 453$)** | `87.6%` (`397 / 453`) | **`96.0%`** (`435 / 453`) | **`96.0%`** (`435 / 453`) |
| **Discordant Multi-Patch Top-1 Accuracy ($n = 71$)** | `78.9%` | `74.7%` | `74.7%` |

- **Key Diagnostic**: V17's binary gate rejected $332$ out of $729$ genuinely correct patches (`FRR = 45.5%`). In $56$ out of $453$ resolvable runs (`12.4%`), the binary gate rejected *every* working patch produced during the run. By retaining all `repro_ok + repo_tests_ok` patches in the candidate pool and ranking them continuously (`ranker_falsifier_weight=0.45`, `ranker_simplicity_weight=0.05`, `ranker_synthesis_bonus=0.08`), V18 recovers $38$ of those $56$ lost task-runs (`+8.4 pp` on resolvable runs).

---

## 3. Wave-1 (`eval_24_stratified`, $N = 24$) & Full Benchmark (`test`, $N = 80$) Comparisons

### 3.1 Wave-1 Stratified Subset (`eval_24_stratified`, $N = 24$)

| Arm | Tasks Completed | Resolved ($/ 24$) | Pass Rate (%) | Replicate Totals | Mean Tokens / Task | Efficiency (`Resolved / MTok`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`Flat_1x_V18`** | `24 / 24` | **`15.33`** | `63.9%` | `[16, 15, 15]` | `164,505` | `3.88` |
| **`Flat_3x_Select`** | `24 / 24` | **`18.00`** | `75.0%` | `[18]` | `493,515` | `1.52` |
| **`Flat_3x_PassAt3` (Oracle Union)** | `24 / 24` | **`18.00`** | `75.0%` | `[18]` | `493,515` | `1.52` |
| **`V18_Collab`** | `24 / 24` | **`16.00`** | `66.7%` | `[16]` | `543,935` | `1.23` |
| **`V18_Evolved`** | `24 / 24` | **`14.00`** | `58.3%` | `[14]` | `730,239` | `0.80` |

### 3.2 Paired Bootstrap 95% Confidence Intervals & Exact McNemar Tests ($N = 80$)

| Hypothesis Comparison | Net Task Diff ($\Delta$) | 95% Bootstrap CI (Tasks) | Exact McNemar $p$ | Statistical Verdict ($\alpha = 0.05$) |
| :--- | :---: | :---: | :---: | :--- |
| **H1 (`Flat_3x_Select` vs `Flat_1x_V18`)** | **`+3.00`** (`+3.8 pp`) | `[+0.67, +5.67]` | `0.2500` | Positive 95% bootstrap CI (`[+0.67, +5.67]`), exact McNemar $p = 0.2500$ (not significant at $\alpha = 0.05$ due to high run-to-run agreement) |
| **H2 (`V18_Collab` vs `Flat_3x_Select`)** | **`-1.00`** (`-1.2 pp`) | `[-7.00, +5.00]` | `1.0000` | Statistically indistinguishable ($p = 1.0000$, `4` wins vs `5` losses) |
| **H2b (`V18_Collab` vs `Flat_1x_V18`)** | **`+2.00`** (`+2.5 pp`) | `[-3.33, +7.67]` | `0.7539` | Not statistically significant ($p = 0.7539$) |
| **H3 (`V18_Evolved` vs `V18_Collab`)** | **`-4.00`** (`-5.0 pp`) | `[-10.00, +2.00]` | `0.3438` | Not statistically significant; evolved specialist prompts underperformed generalist collaborative roles (`3` wins vs `7` losses) |
| **H3b (`V18_Evolved` vs `Flat_3x_Select`)** | **`-5.00`** (`-6.2 pp`) | `[-10.00, +0.00]` | `0.1250` | Not statistically significant ($p = 0.1250$) |
| **H3c (`V18_Evolved` vs `Flat_1x_V18`)** | **`-2.00`** (`-2.5 pp`) | `[-6.33, +2.33]` | `0.6875` | Not statistically significant ($p = 0.6875$) |

### 3.3 Repository-Level Breakdown ($N = 80$)

| Repository | Tasks ($n$) | `Flat_1x_V18` (Mean of 3) | `Flat_3x_Select` | `Flat_3x_PassAt3` | `V18_Collab` | `V18_Evolved` | Union Across All Runs |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `astropy/astropy` | `13` | `6.33` (`48.7%`) | `6` (`46.2%`) | `7` (`53.8%`) | `6` (`46.2%`) | `6` (`46.2%`) | `7` (`53.8%`) |
| `matplotlib/matplotlib` | `15` | `8.00` (`53.3%`) | `9` (`60.0%`) | `9` (`60.0%`) | `9` (`60.0%`) | `7` (`46.7%`) | `10` (`66.7%`) |
| `psf/requests` | `1` | `1.00` (`100.0%`) | `1` (`100.0%`) | `1` (`100.0%`) | `1` (`100.0%`) | `1` (`100.0%`) | `1` (`100.0%`) |
| `pydata/xarray` | `6` | `6.00` (`100.0%`) | `6` (`100.0%`) | `6` (`100.0%`) | `5` (`83.3%`) | `6` (`100.0%`) | `6` (`100.0%`) |
| `pylint-dev/pylint` | `4` | `1.33` (`33.3%`) | `2` (`50.0%`) | `2` (`50.0%`) | `1` (`25.0%`) | `2` (`50.0%`) | `2` (`50.0%`) |
| `pytest-dev/pytest` | `11` | `6.67` (`60.6%`) | `8` (`72.7%`) | `8` (`72.7%`) | `7` (`63.6%`) | `6` (`54.5%`) | `9` (`81.8%`) |
| `scikit-learn/scikit-learn` | `15` | `13.00` (`86.7%`) | `13` (`86.7%`) | `13` (`86.7%`) | **`14` (`93.3%`)** | `12` (`80.0%`) | `14` (`93.3%`) |
| `sphinx-doc/sphinx` | `15` | `7.67` (`51.1%`) | `8` (`53.3%`) | `8` (`53.3%`) | **`9` (`60.0%`)** | `8` (`53.3%`) | `9` (`60.0%`) |
| **Total (`eval_80`)** | **`80`** | **`50.00` (`62.5%`)** | **`53` (`66.2%`)** | **`54` (`67.5%`)** | **`52` (`65.0%`)** | **`48` (`60.0%`)** | **`58` (`72.5%`)** |

---

## 4. Deep-Dive Analysis: Where Sampling + Selection Wins vs. Where Collaborative Hierarchy Wins

### 4.1 Why `Flat_3x_Select` Captures `53 / 54` (`98.1%`) of the `Pass@3` Oracle Ceiling
- Across the 3 independent seeds of `Flat_1x_V18` (`51/80`, `49/80`, `50/80`), run-to-run agreement is high: **72 out of 80 tasks (`90.0%`) are unanimous across all 3 seeds**, and mean pairwise agreement is **93.3%**.
- On the **8 discordant tasks** where 1 or 2 seeds succeeded and the other(s) failed (`Pass@3 = 54/80` vs `Pass@1 = 50.0/80`), `Flat_3x_Select` combines self-verification gates (`repro_ok`, `repo_tests_ok`, `audit_ok`) with **exact-SHA majority voting** and **normalized diff-line Jaccard medoid similarity**. This blind selector picks a passing patch on **7 out of 8 discordant tasks (`87.5%`)**, reaching `53 / 80` (`66.2%`) at `442k` tokens/task.

### 4.2 The 4 Tasks Where `V18_Collab` Beats `Flat_3x_PassAt3` (`0 / 3` Flat Rollouts)
Even though `Flat_3x_Select` (`53/80`) and `V18_Collab` (`52/80`) tie statistically overall, their solved sets are **not** subsets of one another. On **4 tasks**, all 3 independent `Flat_1x_V18` rollouts failed (`0 / 3`, so `Flat_3x_PassAt3 = 0`), while `V18_Collab` solved them:
1. **`scikit-learn__scikit-learn-14629`**: Neither `b1` nor `b2` alone passed all checks, so `V18_Collab` convened the **Post-Prototype Synthesis Roundtable (`L2_SynthesisRoundtable_b_synth`)**, which synthesized both prototypes into `b_synth` (`sklearn/multioutput.py`), passed 100% of the Red-Team panel (`rank_score = 4.42`), and resolved the task.
2. **`pytest-dev__pytest-6197`**: Initial prototypes `b1`, `b2`, and `b_synth` passed `.hae/reproduce_issue.py` (`repro_ok=True`) but broke repository regression tests (`repo_tests_ok=False`, `score = 2.13–2.19`). Guided by `18` `#verdicts-and-repros` entries, branch `b3.1` (`cont_codebase_symmetry`) produced a clean fix (`rank_score = 4.42`), which the Candidate-Pool Ranker selected over the 4 earlier broken patches.
3. **`matplotlib__matplotlib-23299` & `sphinx-doc__sphinx-8551`**: In both tasks, the **Pre-Edit Builder Huddle** (`contract_overrides = 1`) refined the L0 Architect's initial contract before any code edits, enabling `b1` to produce a certified global-optimum fix in a single episode (`126k` and `185k` tokens).

Conversely, `Flat_3x_Select` solved **5 tasks** where `V18_Collab` failed (`matplotlib__matplotlib-22865`, `pydata__xarray-4966`, `pylint-dev__pylint-4970`, `pytest-dev__pytest-7324`, `pytest-dev__pytest-7982`). Taking the union of `Flat_3x_Select` (`53/80`) and `V18_Collab` (`52/80`) reaches **`57 / 80` (`71.2%`)** (`58 / 80 = 72.5%` across all 5 runs).

### 4.3 V18 Collaborative Sub-Blackboard & Roundtable Telemetry ($N = 80$)

| Arm | Mean Blackboard Entries / Task | `#recon-map` | `#builders-room` | `#redteam-chamber` | `#verdicts-and-repros` | Builder Huddle Overrides | Synthesis Roundtables | Mean Pool Candidates / Task |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`V18_Collab`** | `15.6` | `155` | `697` | `0` | `394` | `74` (`92.5%` of tasks) | `30` (`37.5%` of tasks) | `2.33` |
| **`V18_Evolved`** | `15.8` | `164` | `699` | `0` | `403` | `72` (`90.0%` of tasks) | `31` (`38.8%` of tasks) | `2.35` |

- Notice that `#redteam-chamber` remained strictly isolated (`0` cross-pollination leaks to builders), while `#verdicts-and-repros` shared `394–403` concrete failing cases and verdicts across the 80 tasks.

---

## 5. Key Scientific Conclusions & Implications for Next Steps

1. **Verification & Selection > Prompt-Persona Evolution**:
   - Converting the Red-Team falsifier from a brittle binary gate (`FRR = 45.5%`) into a continuous **Candidate-Pool Ranker** (`100%` pool recall, `96.0%` top-1 accuracy on resolvable runs) and applying consensus/medoid selection (`Flat_3x_Select`) produced the highest absolute pass rates (`66.2%` and `65.0%`), whereas evolving specialist/falsifier prompt personas (`V18_Evolved`, `60.0%`) underperformed generalist collaborative roles (`V18_Collab`, `65.0%`) by `-4.0` tasks (`-5.0 pp`).
2. **Compute-Matched Test-Time Scaling (`Flat_3x_Select`) Matches Hierarchical Collaboration (`V18_Collab`) at Lower Token Cost**:
   - At `442k` tokens/task, `Flat_3x_Select` (`53/80 = 66.2%`, `1.50 Resolved/MTok`) slightly edges out `V18_Collab` (`52/80 = 65.0%` at `555k` tokens/task, `1.17 Resolved/MTok`), confirming that parallel rollouts + a well-calibrated selector capture most test-time scaling gains on localized single-issue patches.
3. **Complementary Failure Modes Between Parallel Flat Rollouts and Multi-Branch Synthesis**:
   - Because `Flat_1x_V18` has `90.0%` 3-seed unanimity, parallel flat sampling saturates quickly (`Pass@3 = 54/80 = 67.5%`, only `+4.0` tasks above `Pass@1 = 50.0/80`). `V18_Collab` solves **4 tasks where all 3 flat rollouts fail (`0/3`)** via Pre-Edit Builder Huddle contract overrides and Post-Prototype Synthesis Roundtables (`b_synth`), lifting the combined portfolio ceiling to `58 / 80` (`72.5%`).
