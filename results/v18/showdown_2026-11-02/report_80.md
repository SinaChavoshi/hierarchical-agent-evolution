# V18 Evaluation Report (`test`, $N = 80$ Fresh SWE-bench Verified Tasks)

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

## 2. Compute-Matched Showdown on Fresh `test` ($N = 80$ Tasks)

| Arm | Tasks Completed | Resolved ($/ N$) | Pass Rate (%) | Replicate Totals | Mean Tokens / Task | Efficiency (`Resolved / MTok`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Flat_1x_V18** | `80/80` | **`50.00`** | `62.5%` | `[51, 49, 50]` | `147,399` | `4.24` |
| **Flat_3x_Select** | `80/80` | **`53.00`** | `66.2%` | `[53]` | `442,196` | `1.50` |
| **Flat_3x_PassAt3 (Oracle Union)** | `80/80` | **`54.00`** | `67.5%` | `[54]` | `442,196` | `1.53` |
| **V18_Collab** | `80/80` | **`52.00`** | `65.0%` | `[52]` | `555,277` | `1.17` |
| **V18_Evolved** | `80/80` | **`48.00`** | `60.0%` | `[48]` | `577,305` | `1.04` |

## 3. Paired Bootstrap 95% Confidence Intervals & Exact McNemar Tests

| Hypothesis Comparison | Net Task Diff ($\Delta$) | 95% Bootstrap CI (Tasks) | Exact McNemar $p$ | Statistical Verdict ($\alpha = 0.05$) |
| :--- | :---: | :---: | :---: | :--- |
| H1 (`Flat_3x_Select` vs `Flat_1x_V18`) | `+3.00` | `[+0.67, +5.67]` | `0.2500` | Not statistically significant (null not rejected) |
| H2 (`V18_Collab` vs `Flat_3x_Select`) | `-1.00` | `[-7.00, +5.00]` | `1.0000` | Not statistically significant (null not rejected) |
| H2b (`V18_Collab` vs `Flat_1x_V18`) | `+2.00` | `[-3.33, +7.67]` | `0.7539` | Not statistically significant (null not rejected) |
| H3 (`V18_Evolved` vs `V18_Collab`) | `-4.00` | `[-10.00, +2.00]` | `0.3438` | Not statistically significant (null not rejected) |
| H3b (`V18_Evolved` vs `Flat_3x_Select`) | `-5.00` | `[-10.00, +0.00]` | `0.1250` | Not statistically significant (null not rejected) |
| H3c (`V18_Evolved` vs `Flat_1x_V18`) | `-2.00` | `[-6.33, +2.33]` | `0.6875` | Not statistically significant (null not rejected) |

## 4. V18 Collaborative Sub-Blackboard & Roundtable Telemetry

| Arm | Mean Blackboard Entries | `#recon-map` | `#builders-room` | `#redteam-chamber` | `#verdicts-and-repros` | Builder Huddle Overrides | Synthesis Roundtables | Mean Pool Candidates |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **V18_Collab** | `15.6` | `155` | `697` | `0` | `394` | `74` | `30` | `2.33` |
| **V18_Evolved** | `15.8` | `164` | `699` | `0` | `403` | `72` | `31` | `2.35` |
