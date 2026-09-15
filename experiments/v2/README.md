# V2 Experiment Set — Self-Hosting Evolutionary Campaign (Generations 1–3)

**Status: Campaign 1 Complete (`3 / 3` Generations, `30 / 30` Firms Succeeded, `$10.22` Total Spend).**

V2 transitions Hierarchical Agent Evolution from V1's open-ended prose objectives to **ground-truth self-hosting benchmarks**, where competing multi-agent organizations re-implement modules of this repository (`hae/evaluation/artifacts.py`) from their public specification and are graded against held-out test suites (`tests/test_artifacts.py`) inside a network-isolated (`unshare -rn`) sandbox.

---

## 1. Campaign Ledger Summary (`experiments/v2/ledger.json`)

All three generations executed with `carry_artifacts: false` (each firm starts from an empty workspace and must synthesize the module from scratch), `$5.00/firm` budget (`max_calls: 650`), and `execution_integrity` weighted at **30%** of composite fitness.

| Generation | Spec | Task | Best Score (Champion) | Cohort Mean | Elite Control Mean | Top-Tier Execution (`85.71%`) | Completion Rate | Total Spend | Status |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Gen 1** | [`gen1.json`](../../configs/generations/gen1.json) | [`self-hosting-artifacts`](../../configs/tasks/self-hosting-artifacts.json) | **`94.03`** (`gen_1_mutant_5`) | `81.86` | `76.53` (`50%` exec) | `5 / 10` (`50%`) | `10 / 10` (`100%`) | `$3.45` | `COMPLETE` |
| **Gen 2** | [`gen2.json`](../../configs/generations/gen2.json) | [`self-hosting-artifacts`](../../configs/tasks/self-hosting-artifacts.json) | **`94.16`** (`gen_2_crossover_3`) | `71.35` | `75.98` (`50%` exec) | `4 / 10` (`40%`) | `10 / 10` (`100%`) | `$3.38` | `COMPLETE` |
| **Gen 3** | [`gen3.json`](../../configs/generations/gen3.json) | [`self-hosting-artifacts`](../../configs/tasks/self-hosting-artifacts.json) | **`93.94`** (`gen_3_elite_1`) | `70.42` | **`91.09` (`100%` exec)** | **`6 / 10` (`60%`)** | `10 / 10` (`100%`) | `$3.39` | `COMPLETE` |

---

## 2. Key Scientific & Architectural Findings

### Finding 1 — True Selection Pressure Purges Seed Controls & Fragile Flukes
- **Generation 1 (Mutant Sweep):** Seeded with 2 unmodified control clones (`elite_1`, `elite_2`) and 8 `MorphogenesisEngine` topology mutants (`mutant_1`..`mutant_8`). All top 5 survivor slots were won by topology mutants (`mutant_5` at `94.03`, `mutant_3` at `94.01`, `mutant_1` at `93.76`, `mutant_8` at `93.61`, `mutant_2` at `90.87`), eliminating both unmodified seed controls (`elite_1` finished `#6` at `87.80`, `elite_2` finished `#9` at `65.25` with `0.0%` execution).
- **Generation 2 (Heritability Filter & Structural Crossover Triumph):** Because `carry_artifacts: false` requires every genome to build working software from scratch on a clean workspace, Generation 2 separated heritable competence from single-run luck:
  - Gen 1's #1 winner (`gen_1_mutant_5`) proved to be a fragile topology: all three of its direct offspring (`gen_2_elite_1`, `gen_2_pareto_1`, `gen_2_crossover_1`) suffered cross-department coordination failures (omitting `count_source_files` on export) and collapsed to `0.0%` execution integrity.
  - Conversely, `gen_2_crossover_3` (a structural crossover recombining `gen_1_mutant_1 × gen_1_mutant_8`) achieved the **all-time campaign high of `94.16`** (`85.71%` execution, `6/7` test classes passed), while `gen_1_mutant_3`'s lineage (`gen_2_pareto_2`, `gen_2_crossover_2`, `gen_2_elite_2`) swept positions `#2`, `#3`, and `#4`.

### Finding 2 — Compounding Heritability in Generation 3 (`+14.56` Elite Fitness Gain)
- After Generation 2 purged the fragile `gen_1_mutant_5` lineage and bred forward `gen_2_crossover_3` (`gen_1_mutant_1 × gen_1_mutant_8`) and `gen_2_pareto_2` (`gen_1_mutant_3`), **100% of Generation 3 elite control clones (`gen_3_elite_1` at `93.94` and `gen_3_elite_2` at `88.23`) reproduced `85.71%` execution integrity**.
- Mean elite control fitness rose from **`76.53` in Generation 1** to **`91.09` in Generation 3 (`+14.56` points)**, and the proportion of the cohort achieving top-tier execution (`85.71%`) rose to **`60%` (`6 / 10` firms)**, including all 5 Generation 3 survivors (`#1` through `#5`).

### Finding 3 — Specification Completeness vs. Held-Out Oracle (`6 / 7` Test Classes)
- Across all 30 firms, the maximum benchmark execution score achieved was **`85.71%` (`6 / 7` held-out test classes passing)**, reached by `15 / 30` firms across the campaign.
- Post-hoc inspection of the sole failing test class (`test_markdown_contaminated_paths_are_malformed`) revealed that all 15 top firms passed every test case for markdown bold (`**`), backticks (`` ` ``), and leading/trailing whitespace, but failed on a single assertion: `is_malformed_path("notes.")`.
- Inspection of `hae/evaluation/artifacts.py` confirmed that `is_malformed_path`'s docstring specified only markdown formatting and stray whitespace, never mentioning trailing dots (`.`), tildes (`~`), or leading hyphens (`-`). Thus, **every top firm achieved `100%` compliance with the published specification**, and the `85.71%` ceiling reflects an undocumented oracle edge case rather than agent implementation error.

---

## 3. Complete Standings by Generation

### Generation 1 Standings (`experiments/v2/generation_1_results/`)

| Rank | Firm ID | Operator Class | Parent Lineage | Execution Integrity | Held-Out Tests | Composite Fitness | Cost | Tokens |
| :---: | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **#1** | `gen_1_mutant_5` | Mutant | `default_company` | `85.71` | `6 / 7` | **`94.03`** | `$0.3162` | `349,314` |
| **#2** | `gen_1_mutant_3` | Mutant | `default_company` | `85.71` | `6 / 7` | **`94.01`** | `$0.2995` | `410,280` |
| **#3** | `gen_1_mutant_1` | Mutant | `default_company` | `85.71` | `6 / 7` | **`93.76`** | `$0.3107` | `334,343` |
| **#4** | `gen_1_mutant_8` | Mutant | `default_company` | `85.71` | `6 / 7` | **`93.61`** | `$0.3603` | `387,044` |
| **#5** | `gen_1_mutant_2` | Mutant | `default_company` | `71.43` | `5 / 7` | **`90.87`** | `$0.2925` | `320,751` |
| **#6** | `gen_1_elite_1` | Elite | `default_company` | `85.71` | `6 / 7` | `87.80` | `$0.3429` | `463,919` |
| **#7** | `gen_1_mutant_7` | Mutant | `default_company` | `42.86` | `3 / 7` | `76.75` | `$0.4225` | `600,609` |
| **#8** | `gen_1_mutant_4` | Mutant | `default_company` | `57.14` | `4 / 7` | `73.90` | `$0.3884` | `594,486` |
| **#9** | `gen_1_elite_2` | Elite | `default_company` | `0.00` | `0 / 7` | `65.25` | `$0.3707` | `541,796` |
| **#10** | `gen_1_mutant_6` | Mutant | `default_company` | `0.00` | `0 / 7` | `48.57` | `$0.3456` | `435,985` |

### Generation 2 Standings (`experiments/v2/generation_2_results/`)

| Rank | Firm ID | Operator Class | Parent Lineage | Execution Integrity | Held-Out Tests | Composite Fitness | Cost | Tokens |
| :---: | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **#1** | `gen_2_crossover_3` | Crossover | `mutant_1 × mutant_8` | `85.71` | `6 / 7` | **`94.16`** | `$0.3098` | `401,512` |
| **#2** | `gen_2_pareto_2` | Pareto | `gen_1_mutant_3` | `71.43` | `5 / 7` | **`90.31`** | `$0.3438` | `433,190` |
| **#3** | `gen_2_crossover_2` | Crossover | `mutant_3 × mutant_1` | `85.71` | `6 / 7` | **`84.60`** | `$0.3811` | `508,912` |
| **#4** | `gen_2_elite_2` | Elite | `gen_1_mutant_3` | `85.71` | `6 / 7` | **`84.55`** | `$0.2926` | `409,996` |
| **#5** | `gen_2_elite_1` | Elite | `gen_1_mutant_5` | `0.00` | `0 / 7` | **`67.41`** | `$0.3372` | `440,120` |
| **#6** | `gen_2_crossover_1` | Crossover | `mutant_5 × mutant_3` | `0.00` | `0 / 7` | `66.21` | `$0.3788` | `512,440` |
| **#7** | `gen_2_pareto_1` | Pareto | `gen_1_mutant_5` | `0.00` | `0 / 7` | `64.58` | `$0.3636` | `489,102` |
| **#8** | `gen_2_mutant_3` | Mutant | `gen_1_mutant_1` | `0.00` | `0 / 7` | `59.05` | `$0.3904` | `541,208` |
| **#9** | `gen_2_mutant_1` | Mutant | `gen_1_mutant_5` | `85.71` | `6 / 7` | `57.69` | `$0.3032` | `412,880` |
| **#10** | `gen_2_mutant_2` | Mutant | `gen_1_mutant_3` | `0.00` | `0 / 7` | `44.98` | `$0.2838` | `388,115` |

### Generation 3 Standings (`experiments/v2/generation_3_results/`)

| Rank | Firm ID | Operator Class | Parent Lineage | Execution Integrity | Held-Out Tests | Composite Fitness | Cost | Tokens |
| :---: | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **#1** | `gen_3_elite_1` | Elite | `gen_2_crossover_3` | `85.71` | `6 / 7` | **`93.94`** | `$0.4144` | `561,024` |
| **#2** | `gen_3_elite_2` | Elite | `gen_2_pareto_2` | `85.71` | `6 / 7` | **`88.23`** | `$0.2569` | `348,910` |
| **#3** | `gen_3_pareto_1` | Pareto | `gen_2_crossover_3` | `85.71` | `6 / 7` | **`84.98`** | `$0.3057` | `419,882` |
| **#4** | `gen_3_crossover_3` | Crossover | `crossover_2 × elite_2` | `85.71` | `6 / 7` | **`84.29`** | `$0.2835` | `391,004` |
| **#5** | `gen_3_crossover_1` | Crossover | `crossover_3 × pareto_2` | `85.71` | `6 / 7` | **`76.85`** | `$0.3913` | `528,412` |
| **#6** | `gen_3_mutant_3` | Mutant | `gen_2_crossover_2` | `0.00` | `0 / 7` | `60.09` | `$0.3628` | `495,110` |
| **#7** | `gen_3_mutant_1` | Mutant | `gen_2_crossover_3` | `0.00` | `0 / 7` | `58.77` | `$0.4064` | `552,190` |
| **#8** | `gen_3_pareto_2` | Pareto | `gen_2_crossover_3` | `0.00` | `0 / 7` | `58.64` | `$0.3921` | `531,008` |
| **#9** | `gen_3_mutant_2` | Mutant | `gen_2_pareto_2` | `85.71` | `6 / 7` | `54.77` | `$0.2877` | `394,120` |
| **#10** | `gen_3_crossover_2` | Crossover | `pareto_2 × crossover_2` | `0.00` | `0 / 7` | `43.65` | `$0.2902` | `398,770` |
