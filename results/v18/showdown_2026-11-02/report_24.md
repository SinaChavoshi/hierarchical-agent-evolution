# V18 Evaluation Report (`eval_24_stratified`, $N = 24$ Fresh SWE-bench Verified Tasks)

## 1. Offline Verifier & Candidate-Pool Ranker Calibration ($n = 2,525$ Container-Graded Candidate Patches)

| Metric | V17 Binary Red-Team Gate | V18 Default Pool Ranker | V18 Calibrated Pool Ranker |
| :--- | :---: | :---: | :---: |
| **Container-Graded Patches ($n$)** | 2525 (1385 runs) | 2525 | 2525 |
| **Confusion Matrix (`TP / FP / TN / FN`)** | `397 / 834 / 962 / 332` | Continuous score | Continuous score |
| **Precision (`TP / (TP + FP)`)** | 32.2% (FPR 67.8%) | — | — |
| **Recall (`TP / (TP + FN)`)** | 54.5% (FRR 45.5%) | **100.0%** (0 pool discards) | **100.0%** (0 pool discards) |
| **Specificity (`TN / (TN + FP)`)** | 53.6% | — | — |
| **Patch Ranking AUROC** | `0.540` | `0.549` | **`0.551`** |
| **Resolvable-Run Recall / Top-1 Acc ($n=453$)** | 87.6% | **96.0%** | **96.0%** |
| **Discordant Multi-Patch Top-1 Acc ($n=71$)** | 78.9% | **74.7%** | **74.7%** |

- **Calibrated Ranker Weights**: `ranker_falsifier_weight=0.45`, `ranker_simplicity_weight=0.05`, `ranker_synthesis_bonus=0.08`.

## 2. Compute-Matched Showdown on Fresh `eval_24_stratified` ($N = 24$ Tasks)

| Arm | Tasks Completed | Resolved ($/ N$) | Pass Rate (%) | Replicate Totals | Mean Tokens / Task | Efficiency (`Resolved / MTok`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Flat_1x_V18** | `24/24` | **`15.33`** | `63.9%` | `[16, 15, 15]` | `164,505` | `3.88` |
| **Flat_3x_Select** | `24/24` | **`18.00`** | `75.0%` | `[18]` | `493,515` | `1.52` |
| **Flat_3x_PassAt3 (Oracle Union)** | `24/24` | **`18.00`** | `75.0%` | `[18]` | `493,515` | `1.52` |
| **V18_Collab** | `24/24` | **`16.00`** | `66.7%` | `[16]` | `543,935` | `1.23` |
| **V18_Evolved** | `24/24` | **`14.00`** | `58.3%` | `[14]` | `730,239` | `0.80` |

## 3. Paired Bootstrap 95% Confidence Intervals & Exact McNemar Tests

| Hypothesis Comparison | Net Task Diff ($\Delta$) | 95% Bootstrap CI (Tasks) | Exact McNemar $p$ | Statistical Verdict ($\alpha = 0.05$) |
| :--- | :---: | :---: | :---: | :--- |
| H1 (`Flat_3x_Select` vs `Flat_1x_V18`) | `+2.67` | `[+0.67, +5.00]` | `0.2500` | Not statistically significant (null not rejected) |
| H2 (`V18_Collab` vs `Flat_3x_Select`) | `-2.00` | `[-5.00, +0.00]` | `0.5000` | Not statistically significant (null not rejected) |
| H2b (`V18_Collab` vs `Flat_1x_V18`) | `+0.67` | `[-1.67, +3.00]` | `1.0000` | Not statistically significant (null not rejected) |
| H3 (`V18_Evolved` vs `V18_Collab`) | `-2.00` | `[-6.00, +2.00]` | `0.6250` | Not statistically significant (null not rejected) |
| H3b (`V18_Evolved` vs `Flat_3x_Select`) | `-4.00` | `[-8.00, -1.00]` | `0.1250` | Not statistically significant (null not rejected) |
| H3c (`V18_Evolved` vs `Flat_1x_V18`) | `-1.33` | `[-4.33, +1.33]` | `1.0000` | Not statistically significant (null not rejected) |

## 4. V18 Collaborative Sub-Blackboard & Roundtable Telemetry

| Arm | Mean Blackboard Entries | `#recon-map` | `#builders-room` | `#redteam-chamber` | `#verdicts-and-repros` | Builder Huddle Overrides | Synthesis Roundtables | Mean Pool Candidates |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **V18_Collab** | `15.2` | `43` | `210` | `0` | `112` | `23` | `8` | `2.25` |
| **V18_Evolved** | `18.7` | `57` | `228` | `0` | `163` | `22` | `12` | `2.92` |
