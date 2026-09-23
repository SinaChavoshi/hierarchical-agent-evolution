# V2 Experiment Set — Ground-Truth Self-Hosting Campaigns (`Generations 1–6`)

**Status: COMPLETE (`6 / 6` Generations, `60 / 60` Autonomous Firms Succeeded, `$28.18` Total Spend).**  
*(For `Generations 7–9` Level-3 Closed-Loop RSI results, see [`../v3_rsi/README.md`](../v3_rsi/README.md).)*

| Summary Dimension | Details |
| :--- | :--- |
| **What Was Tested** | **Ground-Truth Self-Hosting under Single-Pass (`Campaign 1: Gen 1–3`) vs. Multi-Iteration Self-Repair (`Campaign 2: Gen 4–6`):** When competing 31–45 agent organizations must re-implement a real platform module ([`hae/evaluation/artifacts.py`](../../hae/evaluation/artifacts.py)) from scratch (`carry_artifacts: false`, `0/10` target files inherited) and are graded against a held-out unit test suite ([`tests/test_artifacts.py`](../../tests/test_artifacts.py)) inside a network-isolated (`unshare -rn`) sandbox (`30%` of total fitness), does Darwinian selection produce heritable software engineering capability? |
| **What Was the Outcome** | **1. Campaign 1 (`Gen 1–3`, Single-Pass `iter=1`):** Purged fragile topologies and raised elite control fitness by **`+14.56` points** (`76.53` $\to$ `91.09`), peaking at `94.16` Net Fitness (`85.7%` execution, `6/7` test classes), but `0 / 30` firms cleared all `7/7` test classes on a single pass due to subtle path-validation edge cases.<br/>**2. Campaign 2 (`Gen 4–6`, Iterative Self-Repair `iter=10` + Monotonic Verification Guard):** Enabled up to 10 ground-truth self-repair iterations and prevented downstream audit agents from overwriting passing code with lower-scoring drafts. **`30 / 30` (`100.0%`) firms achieved `100.0%` (`7/7`) ground-truth execution integrity**, Peak Net Fitness reached **`98.81`** (`gen_5_pareto_2`), **Mean Iterations to Converge dropped monotonically (`2.20` $\to$ `1.60` $\to$ `1.30`)**, First-Shot (`Iteration 1`) `100%` pass rate climbed from `0%` (`Gen 3`) to **`70%` (`Gen 6`)**, and generational spend dropped by **`33%`** (`$7.26` $\to$ `$4.87`). |

V2 transitions Hierarchical Agent Evolution from V1's open-ended prose objectives to **ground-truth self-hosting benchmarks**, where competing multi-agent organizations re-implement modules of this repository (`hae/evaluation/artifacts.py`) from their public specification and are graded against held-out test suites (`tests/test_artifacts.py`) inside a network-isolated (`unshare -rn`) sandbox.

---

## 1. Longitudinal Ledger Summary Across Campaigns 1 & 2 (`Generations 1–6`)

All six generations executed with `carry_artifacts: false` (each firm starts from an empty workspace and must synthesize the module from scratch), `$5.00/firm` budget (`max_calls: 650`), and `execution_integrity` weighted at **30%** of composite fitness.

- **Campaign 1 (`Generations 1–3`):** Single-pass execution (`max_iterations: 1`).
- **Campaign 2 (`Generations 4–6`, Option C):** Multi-iteration ground-truth self-repair (`max_iterations: 10`), where failing test names and `AssertionError` / import exception messages (without test source code) are fed back to the organization across up to 10 repair passes until `score >= 100.0%` or budget exhaustion.

| Generation | Campaign / Mode | Spec | Best Score (Champion) | Cohort Mean | Exec `100.0%` (`7/7`) Rate | Mean Exec Score | Mean Iterations to Converge | Completion Rate | Total Spend | Status |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Gen 1** | Campaign 1 (`iter=1`) | [`gen1.json`](../../configs/generations/gen1.json) | **`94.03`** (`gen_1_mutant_5`) | `81.86` | `0 / 10` (`0%`) | `60.0%` | `1.00` | `10 / 10` (`100%`) | `$3.45` | `COMPLETE` |
| **Gen 2** | Campaign 1 (`iter=1`) | [`gen2.json`](../../configs/generations/gen2.json) | **`94.16`** (`gen_2_crossover_3`) | `71.35` | `0 / 10` (`0%`) | `41.4%` | `1.00` | `10 / 10` (`100%`) | `$3.38` | `COMPLETE` |
| **Gen 3** | Campaign 1 (`iter=1`) | [`gen3.json`](../../configs/generations/gen3.json) | **`93.94`** (`gen_3_elite_1`) | `70.42` | `0 / 10` (`0%`) | `51.4%` | `1.00` | `10 / 10` (`100%`) | `$3.39` | `COMPLETE` |
| **Gen 4** | **Campaign 2 (`iter=10`)** | [`gen4.json`](../../configs/generations/gen4.json) | **`98.14`** (`gen_4_crossover_3`) | `86.76` | **`10 / 10` (`100%`)** | **`100.0%`** | **`2.20`** | `10 / 10` (`100%`) | `$7.26` | `COMPLETE` |
| **Gen 5** | **Campaign 2 (`iter=10`)** | [`gen5.json`](../../configs/generations/gen5.json) | **`98.81`** (`gen_5_pareto_2`) | `79.40` | **`10 / 10` (`100%`)** | **`100.0%`** | **`1.60`** | `10 / 10` (`100%`) | `$5.83` | `COMPLETE` |
| **Gen 6** | **Campaign 2 (`iter=10`)** | [`gen6.json`](../../configs/generations/gen6.json) | **`98.39`** (`gen_6_crossover_3`) | **`87.48`** | **`10 / 10` (`100%`)** | **`100.0%`** | **`1.30`** | `10 / 10` (`100%`) | **`$4.87`** | `COMPLETE` |
| **Gen 7** | **V3 Level-3 RSI Seed (`morphogenesis.py`, Zero-Seed)** | [`gen7.json`](../../configs/generations/gen7.json) | **`96.83`** (`gen_7_pareto_1`) | `71.77` | **`10 / 10` (`100%`)** | **`100.0%`** | **`1.50`** | `10 / 10` (`100%`) | `$6.69` | `COMPLETE (0/10 Inherited)` |
| **Gen 8** | **V3 Closed-Loop Bootstrap #1 (`morphogenesis.py`, Overlay-Seeded*)** | [`gen8.json`](../../configs/generations/gen8.json) | **`97.92`** (`gen_8_pareto_2`) | `74.52` (`79.00` med) | **`10 / 10` (`100%`)** | **`100.0%`** | `1.30*` | `10 / 10` (`100%`) | `$6.34` | `COMPLETE (10/10 Inherited*)` |
| **Gen 9** | **V3 Closed-Loop Bootstrap #2 (`morphogenesis.py`, Overlay-Seeded*)** | [`gen9.json`](../../configs/generations/gen9.json) | **`94.89`** (`gen_9_crossover_1`) | `72.71` (`71.91` med) | **`10 / 10` (`100%`)** | **`100.0%`** | `1.00*` | `10 / 10` (`100%`) | **`$5.88`** | `COMPLETE (10/10 Inherited*)` |

![V2 & V3 Ground-Truth Self-Hosting & Closed-Loop RSI Trajectory](assets/v2_v3_evolutionary_trajectory.png)

---

## 2. Key Scientific & Architectural Findings

### Finding 1 — True Selection Pressure Purges Seed Controls & Fragile Flukes (Campaign 1)
- **Generation 1 (Mutant Sweep):** Seeded with 2 unmodified control clones (`elite_1`, `elite_2`) and 8 `MorphogenesisEngine` topology mutants (`mutant_1`..`mutant_8`). All top 5 survivor slots were won by topology mutants (`mutant_5` at `94.03`, `mutant_3` at `94.01`, `mutant_1` at `93.76`, `mutant_8` at `93.61`, `mutant_2` at `90.87`), eliminating both unmodified seed controls.
- **Generation 2 (Heritability Filter & Structural Crossover Triumph):** Because `carry_artifacts: false` requires every genome to build working software from scratch on a clean workspace, Generation 2 separated heritable competence from single-run luck:
  - Gen 1's #1 winner (`gen_1_mutant_5`) proved to be a fragile topology: all three of its direct offspring (`gen_2_elite_1`, `gen_2_pareto_1`, `gen_2_crossover_1`) suffered cross-department coordination failures and collapsed to `0.0%` execution integrity.
  - Conversely, `gen_2_crossover_3` (a structural crossover recombining `gen_1_mutant_1 × gen_1_mutant_8`) achieved `94.16` (`85.71%` execution, `6/7` test classes passed).

### Finding 2 — Compounding Heritability in Generation 3 (`+14.56` Elite Fitness Gain)
- After Generation 2 purged the fragile `gen_1_mutant_5` lineage and bred forward `gen_2_crossover_3` and `gen_2_pareto_2`, **100% of Generation 3 elite control clones (`gen_3_elite_1` at `93.94` and `gen_3_elite_2` at `88.23`) reproduced `85.71%` execution integrity**.
- Mean elite control fitness rose from **`76.53` in Generation 1** to **`91.09` in Generation 3 (`+14.56` points)**.

### Finding 3 — Ground-Truth Self-Repair (`Option C`, `max_iterations: 10`) Achieves `100.0%` Execution Integrity Across `40 / 40` Zero-Seed Firms (`Gen 4–7`)
- In Campaign 1 (`max_iterations: 1`), `0 / 30` firms achieved `100.0%` (`7/7` test classes) because of edge cases (`notes.` trailing dot and `-flag` leading hyphen in `is_malformed_path`).
- When Campaign 2 (`Generations 4–6` on `artifacts.py`) and V3 Seed (`Generation 7` on `morphogenesis.py`) enabled iterative self-repair (`max_iterations: 10`) with ground-truth failure diagnostics (`FAIL: <test> -> <AssertionError / ImportError>`), **every single zero-seed firm (`40 / 40`, `100.0%`) across Generations 4, 5, 6, and 7 built the target module from a blank slate (`0/10` target files in `inherited_files`) and achieved a perfect `100.0%` (`7 / 7` held-out tests passing) execution integrity score!**
- Multi-iteration self-repair rescued firms from both subtle assertion edge cases and catastrophic module-level import/truncation errors:
  - In **Generation 4**, `gen_4_pareto_2` self-repaired on **Iteration 2/10** (`91.70` Net), `gen_4_mutant_1` on **Iteration 3/10** (`90.62` Net), `gen_4_mutant_2` on **Iteration 4/10** (`75.30` Net), and `gen_4_elite_1` on **Iteration 7/10** (`77.40` Net).
  - In **Generation 5**, `gen_5_pareto_2` achieved the **All-Time Campaign Peak Fitness of `98.81`** (`98.10` Gross Rubric, `100.0%` Execution Integrity, `$0.3574` spend, Iteration `1/10`).

### Finding 4 — Evolutionary Selection Under Iterative Self-Repair Drives Rapid Convergence (`2.20` $\to$ `1.60` $\to$ `1.30` Iterations in `Gen 4–6`)
- Because the Net Fitness function penalizes OpEx overruns (`-$20.0` points per dollar spent over the `$0.50` single-iteration baseline budget), firms that require 4–7 iterations to converge suffer heavy OpEx penalties (e.g. `gen_4_elite_1` spent `$2.28` across 7 iterations, reducing its `92.40` Gross Rubric score to `77.40` Net Fitness and eliminating it from the top 5 breeding pool).
- Consequently, **inter-generation Darwinian selection (`Breeder`) systematically selected for genomes that achieve `100.0%` ground-truth verification on Iteration 1 or Iteration 2**:
  - **Mean Iterations to Converge** dropped monotonically across the zero-seed self-repair campaign: **`2.20` (Gen 4) $\to$ `1.60` (Gen 5) $\to$ `1.30` (Gen 6)**.
  - **First-Pass (`Iteration 1/10`) `100.0%` Convergence Rate** rose from `0%` in Campaign 1 to **`70%` (`7/10` firms) in Generation 6**, with the remaining `30%` (`3/10` firms) converging on **Iteration 2/10** (zero firms needed more than 2 iterations by Generation 6!).
  - **Total Generational Spend** fell by **33%** from **`$7.26` in Generation 4** to **`$5.83` in Generation 5** and **`$4.87` in Generation 6**, while **Cohort Mean Net Fitness reached an all-time high of `87.48` in Generation 6** (with 5 firms scoring above `97.10` Net Fitness).

### Finding 5 — Multi-Department Workspace Protection (`Monotonic Verification Guard`)
- Live telemetry during Campaign 2 revealed a structural failure mode in multi-department agent organizations: upstream engineering departments (`Systems Engineering & Infrastructure Architecture`) frequently authored a `100.0%`-passing `hae/evaluation/artifacts.py`, only for downstream audit/verification departments (`Adversarial Audit & Red Team Verification` or `Formal Verification`) to overwrite `artifacts.py` with a mock stub or inline test script at the end of the pass.
- Implementing the **Monotonic Verification Guard** in `AgentWorkspace.write_file` ([`hae/runtime/workspace.py`](../../hae/runtime/workspace.py)) resolved this permanently: whenever an agent attempts to overwrite a self-hosting target module with code that degrades its ground-truth verification score, `write_file` rejects the destructive overwrite and instructs the agent to write red-team/test suites to separate files (e.g. `tests/test_artifacts.py`).

### Finding 6 — Level 3 Closed-Loop RSI & Forensic Audit of Overlay Pre-Seeding (`Generations 7–9`)
- In **V3 (`Generations 7–9`)**, competing organizations re-implemented HAE's core evolutionary breeding engine (`hae/genome/morphogenesis.py`: `MorphogenesisEngine` and `StructuralCrossoverEngine`). Verified `100.0%`-passing implementations were promoted into each firm's `genome.code_overlays` and dynamically loaded (`hae/runtime/overlay.py:get_overlay_class`) by `Breeder` to breed Generation 8 and Generation 9.
- **Zero-Seed Synthesis in Generation 7 (`0/10` Inherited):** In Generation 7, `code_overlays` only contained `hae/evaluation/artifacts.py`. All 10 firms started with **zero `morphogenesis.py` on disk** and built it from scratch (`10/10` passed `7/7` tests, **`60%` (`6/10`) converged on Iteration 1**, `Mean Iterations = 1.50`, Peak Net Fitness **`96.83`**).
- **Forensic Audit of Overlay Pre-Seeding in Generations 8 & 9 (`*10/10` Inherited, Fixed in `commit 71a7da4`):**
  > [!WARNING]
  > A post-run audit of the internal agent deliberation transcripts (`dept_product_ux` in `gen_9_crossover_1_result.json`) and `run_output.inherited_files` revealed why Iteration-1 convergence reached `80%` in Gen 8 and `100%` in Gen 9:
  > - In [`hae/orchestration/worker.py`](../../hae/orchestration/worker.py), the filter designed to exclude the current benchmark's `target_module` from `seed_files` when `carry_artifacts=False` queried `getattr(task.verifier, "target_module", None)`. Because `BenchmarkVerifier` stores `target_module` at `verifier.benchmark.task(verifier.task_id).target_module`, `getattr(task.verifier, "target_module", None)` returned `None`.
  > - Consequently, once Generation 7 promoted `hae/genome/morphogenesis.py` into `genome.code_overlays`, `worker.py` copied the parent's `100%`-passing `morphogenesis.py` into `seed_files` at the start of Generation 8 and Generation 9 (`target in inherited_files = 10/10`), where the Monotonic Verification Guard then prevented any agent from overwriting it with a lower-scoring draft.
  > - Thus, while **Generations 1–7 (`70` firms)** were true zero-seed synthesis from scratch (`0/10` inherited) and **Gen 7 $\to$ Gen 8 $\to$ Gen 9 inter-generational breeding** genuinely executed the evolved `MorphogenesisEngine` and `StructuralCrossoverEngine` overlays, the **Generation 8 and 9 workspace scores (`80%` and `100%` Iter-1 convergence)** reflect overlay-seeded verification rather than blank-slate synthesis. This bug was fixed in [`hae/orchestration/worker.py`](../../hae/orchestration/worker.py) (`commit 71a7da4`).

---

## 3. Complete Standings by Generation (`Generations 1–6`)

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

### Generation 4 Standings (`experiments/v2/generation_4_results/`)

| Rank | Firm ID | Operator Class | Gross Rubric | Execution Integrity | Held-Out Tests | Iterations | Net Fitness | Cost |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **#1** | `gen_4_crossover_3` | Crossover | `97.50` | **`100.0%`** | `7 / 7` | `1 / 10` | **`98.14`** | `$0.3711` |
| **#2** | `gen_4_mutant_3` | Mutant | `95.80` | **`100.0%`** | `7 / 7` | `1 / 10` | **`96.68`** | `$0.3249` |
| **#3** | `gen_4_crossover_2` | Crossover | `93.00` | **`100.0%`** | `7 / 7` | `1 / 10` | **`94.01`** | `$0.2987` |
| **#4** | `gen_4_pareto_1` | Pareto | `92.70` | **`100.0%`** | `7 / 7` | `1 / 10` | **`92.89`** | `$0.4624` |
| **#5** | `gen_4_pareto_2` | Pareto | `96.00` | **`100.0%`** | `7 / 7` | `2 / 10` | **`91.70`** | `$0.7151` |
| **#6** | `gen_4_mutant_1` | Mutant | `95.80` | **`100.0%`** | `7 / 7` | `3 / 10` | **`90.62`** | `$0.7591` |
| **#7** | `gen_4_elite_2` | Elite | `78.60` | **`100.0%`** | `7 / 7` | `1 / 10` | **`79.38`** | `$0.3431` |
| **#8** | `gen_4_elite_1` | Elite | `92.40` | **`100.0%`** | `7 / 7` | `7 / 10` | **`77.40`** | `$2.2800` |
| **#9** | `gen_4_mutant_2` | Mutant | `90.30` | **`100.0%`** | `7 / 7` | `4 / 10` | **`75.30`** | `$1.3233` |
| **#10** | `gen_4_crossover_1` | Crossover | `70.90` | **`100.0%`** | `7 / 7` | `1 / 10` | **`71.49`** | `$0.3823` |

### Generation 5 Standings (`experiments/v2/generation_5_results/`)

| Rank | Firm ID | Operator Class | Gross Rubric | Execution Integrity | Held-Out Tests | Iterations | Net Fitness | Cost |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **#1** | `gen_5_pareto_2` | Pareto | `98.10` | **`100.0%`** | `7 / 7` | `1 / 10` | **`98.81`** | `$0.3574` |
| **#2** | `gen_5_crossover_1` | Crossover | `97.80` | **`100.0%`** | `7 / 7` | `1 / 10` | **`97.90`** | `$0.4804` |
| **#3** | `gen_5_elite_1` | Elite | `97.10` | **`100.0%`** | `7 / 7` | `1 / 10` | **`97.60`** | `$0.4001` |
| **#4** | `gen_5_mutant_1` | Mutant | `93.60` | **`100.0%`** | `7 / 7` | `1 / 10` | **`93.84`** | `$0.4525` |
| **#5** | `gen_5_crossover_3` | Crossover | `92.90` | **`100.0%`** | `7 / 7` | `2 / 10` | **`90.77`** | `$0.6066` |
| **#6** | `gen_5_pareto_1` | Pareto | `73.40` | **`100.0%`** | `7 / 7` | `1 / 10` | **`74.09`** | `$0.3623` |
| **#7** | `gen_5_mutant_3` | Mutant | `73.00` | **`100.0%`** | `7 / 7` | `1 / 10` | **`74.07`** | `$0.2856` |
| **#8** | `gen_5_elite_2` | Elite | `72.00` | **`100.0%`** | `7 / 7` | `4 / 10` | **`57.00`** | `$1.3321` |
| **#9** | `gen_5_mutant_2` | Mutant | `61.00` | **`100.0%`** | `7 / 7` | `2 / 10` | **`56.49`** | `$0.7257` |
| **#10** | `gen_5_crossover_2` | Crossover | `60.00` | **`100.0%`** | `7 / 7` | `2 / 10` | **`53.46`** | `$0.8269` |

### Generation 6 Standings (`experiments/v2/generation_6_results/`)

| Rank | Firm ID | Operator Class | Gross Rubric | Execution Integrity | Held-Out Tests | Iterations | Net Fitness | Cost |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **#1** | `gen_6_crossover_3` | Crossover | `97.80` | **`100.0%`** | `7 / 7` | `1 / 10` | **`98.39`** | `$0.3821` |
| **#2** | `gen_6_mutant_3` | Mutant | `97.90` | **`100.0%`** | `7 / 7` | `1 / 10` | **`98.33`** | `$0.4134` |
| **#3** | `gen_6_mutant_1` | Mutant | `97.00` | **`100.0%`** | `7 / 7` | `1 / 10` | **`97.50`** | `$0.4000` |
| **#4** | `gen_6_elite_1` | Elite | `96.60` | **`100.0%`** | `7 / 7` | `1 / 10` | **`97.20`** | `$0.3803` |
| **#5** | `gen_6_crossover_2` | Crossover | `96.50` | **`100.0%`** | `7 / 7` | `1 / 10` | **`97.16`** | `$0.3680` |
| **#6** | `gen_6_crossover_1` | Crossover | `96.50` | **`100.0%`** | `7 / 7` | `2 / 10` | **`92.41`** | `$0.7045` |
| **#7** | `gen_6_elite_2` | Elite | `88.00` | **`100.0%`** | `7 / 7` | `1 / 10` | **`88.69`** | `$0.3611` |
| **#8** | `gen_6_mutant_2` | Mutant | `85.60` | **`100.0%`** | `7 / 7` | `1 / 10` | **`86.33`** | `$0.3550` |
| **#9** | `gen_6_pareto_1` | Pareto | `74.50` | **`100.0%`** | `7 / 7` | `2 / 10` | **`69.82`** | `$0.7340` |
| **#10** | `gen_6_pareto_2` | Pareto | `54.50` | **`100.0%`** | `7 / 7` | `2 / 10` | **`49.02`** | `$0.7742` |
