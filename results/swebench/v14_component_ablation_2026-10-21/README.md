# V14 5-Way Component Ablation Study (`Reflection Beam-1.0-501B-A23B-NVFP4 + DFlash2`, Zero Hints)

> **Date**: October 21, 2026  
> **Serving Stack**: `Reflection Beam-1.0-501B-A23B-NVFP4` (`501B` total / `23B` active MoE) + `DFlash2` (`8` speculative draft tokens) across `3x` multi-node `TP=4` replicas (`6x g2-standard-96`, `48x NVIDIA L4 GPUs`)  
> **Protocol**: Strict Zero-Hint Evaluation (`--include-hints` OFF — `hints_text` never read by any layer)  
> **Grading**: Official in-pod `FAIL_TO_PASS` (`57` tests) + `PASS_TO_PASS` (`214` tests) grading via `scripts/grade_in_testbed.py`  
> **Raw Data**: [`summary.json`](./summary.json)

---

## 1. Executive Summary: Every V14 Component Earns Its Cost

To rigorously test whether each architectural layer of **V14 Hierarchical Agentic AB-MCTS** earns its complexity, we executed a **30-run (`6 instances × 5 policies`) controlled ablation study** with `hints_text` strictly disabled (`--include-hints` OFF) on the `Reflection Beam-1.0-501B-A23B-NVFP4 (+ DFlash2)` GKE cluster:

1. **`v13_hier_mcts` (Full V14 Hierarchy, Zero Hints)**: `L1-A Reconnaissance` + `L0 Seed Architect & AB-MCTS (GEN + CONT)` + `L2 Branch/Delta Executor` + `L1-B Adversarial Falsifier`.
2. **`v14_no_falsifier` (Ablate `L1-B` Adversarial Falsifier)**: Sets `enable_falsifier=False`. Any patch that passes Stage A (`EvidenceGatekeeper`: syntax + `reproduce_issue.py` + local `pytest`) is immediately certified as $V(s_i) = 1.0$ without independent Popperian falsification.
3. **`v14_no_cont` (Ablate `CONT` Delta-Refinement)**: Sets `enable_cont_refinement=False`. Forces `GEN`-only branching (every episode resets to `git checkout HEAD` instead of spawning a clean-context `L2_DeltaRefinementSubAgent` on top of near-miss checkpoints).
4. **`v14_no_recon` (Ablate `L1-A` Reconnaissance)**: Sets `enable_recon=False`. Skips the fast deterministic repository/call-site reconnaissance probe, forcing `L0` and `L2` to localize files from scratch.
5. **`v12_agentic` (Single-Layer ReAct Baseline, Zero Hints)**: Removes the entire `L0 -> L1 -> L2` hierarchy and runs the flat 2-stage `V12` ReAct loop on the exact same `Beam-501B + DFlash2` serving stack without `hints_text`.

| Policy / Ablation Arm | Architecture Enabled | Resolved (`n=6`) | Resolve Rate | `FAIL_TO_PASS` Tests | `PASS_TO_PASS` Tests | Avg Moves | Avg Tokens | Avg Wall-Clock (`s`) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`v13_hier_mcts` (Full V14)** | **`L1-A` + `L0 (GEN+CONT)` + `L2` + `L1-B`** | **`6 / 6`** | **`100.0%`** | **`57 / 57` (`100.0%`)** | **`214 / 214` (`100.0%`)** | **`23.3`** | **`148,360`** | **`278.8s`** |
| **`v14_no_falsifier`** | Ablate `L1-B` Adversarial Falsifier | `4 / 6` | `66.7%` (`-33.3 pp`) | `55 / 57` (`96.5%`) | `214 / 214` (`100.0%`) | `22.0` | `156,570` | `363.7s` |
| **`v14_no_cont`** | Ablate `CONT` Delta-Refinement (`GEN`-only) | `4 / 6` | `66.7%` (`-33.3 pp`) | `55 / 57` (`96.5%`) | `214 / 214` (`100.0%`) | `26.0` | `158,287` | `367.8s` |
| **`v14_no_recon`** | Ablate `L1-A` Reconnaissance Probe | `2 / 6` | `33.3%` (`-66.7 pp`) | `29 / 57` (`50.9%`) | `214 / 214` (`100.0%`) | `31.7` | `170,172` | `446.1s` |
| **`v12_agentic` (Flat ReAct)** | Single-Layer ReAct (No Hierarchy) | `0 / 6` | `0.0%` (`-100.0 pp`) | `14 / 57` (`24.6%`) | `213 / 214` (`99.5%`) | `25.5` | `134,188` | `310.8s` |

---

## 2. Per-Instance Matrix (`6 Instances × 5 Policies = 30 Live Pod Runs`)

| Instance ID | **`v13_hier_mcts` (Full V14)** | **`v14_no_falsifier` (No `L1-B`)** | **`v14_no_cont` (No `CONT`)** | **`v14_no_recon` (No `L1-A`)** | **`v12_agentic` (Flat ReAct)** |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `marshmallow-code__marshmallow-1359` | **PASS** (`1/1` F2P, `18m`, `197s`) | **FAIL** (`0/1` F2P, `16m`, `141s`) | **FAIL** (`0/1` F2P, `17m`, `168s`) | **FAIL** (`0/1` F2P, `16m`, `166s`) | **FAIL** (`0/1` F2P, `16m`, `144s`) |
| `marshmallow-code__marshmallow-1702` | **PASS** (`28/28` F2P, `24m`, `195s`) | **PASS** (`28/28` F2P, `9m`, `124s`) | **PASS** (`28/28` F2P, `15m`, `167s`) | **PASS** (`28/28` F2P, `15m`, `156s`) | **FAIL** (`14/28` F2P, `24m`, `207s`) |
| `pydicom__pydicom-1255` | **PASS** (`1/1` F2P, `24m`, `226s`) | **FAIL** (`0/1` F2P, `69m`, `1233s`) | **FAIL** (`0/1` F2P, `71m`, `1052s`) | **FAIL** (`0/1` F2P, `69m`, `1109s`) | **FAIL** (`0/1` F2P, `41m`, `732s`) |
| `pylint-dev__astroid-1417` | **PASS** (`1/1` F2P, `22m`, `353s`) | **PASS** (`1/1` F2P, `15m`, `303s`) | **PASS** (`1/1` F2P, `21m`, `373s`) | **PASS** (`1/1` F2P, `19m`, `355s`) | **FAIL** (`0/1` F2P, `27m`, `259s`) |
| `pyvista__pyvista-4226` | **PASS** (`25/25` F2P, `15m`, `253s`) | **PASS** (`25/25` F2P, `11m`, `211s`) | **PASS** (`25/25` F2P, `16m`, `254s`) | **FAIL** (`0/25` F2P, `46m`, `585s`) | **FAIL** (`0/25` F2P, `19m`, `202s`) |
| `sqlfluff__sqlfluff-1517` | **PASS** (`1/1` F2P, `37m`, `448s`) | **PASS** (`1/1` F2P, `12m`, `170s`) | **PASS** (`1/1` F2P, `16m`, `194s`) | **FAIL** (`0/1` F2P, `25m`, `306s`) | **FAIL** (`0/1` F2P, `26m`, `320s`) |

---

## 3. Causal Analysis of Each Component

1. **Why `L1-A` Reconnaissance (`v14_no_recon`: `100% -> 33.3%`, `+60%` latency) Is Essential**:
   - Without `L1-A`'s deterministic repository map (`ReconBrief`), `L2` wastes its 18-turn episode budget grepping blind across large codebases.
   - On `pyvista__pyvista-4226`, `v14_no_recon` patched only `pyvista/plotting/_property.py` while completely missing the upstream `pyvista/utilities/misc.py` helper (`0/25` F2P, `46` moves vs. `15` moves in Full V14).
   - On `sqlfluff__sqlfluff-1517`, `v14_no_recon` edited both `sequence.py` and `helpers.py` inconsistently (`0/1` F2P), whereas Full V14 pinpointed `src/sqlfluff/core/parser/helpers.py`.
2. **Why `L1-B` Adversarial Falsifier (`v14_no_falsifier`: `100% -> 66.7%`) Is Essential**:
   - Without `L1-B`, any patch that passes the author's own `reproduce_issue.py` and existing `PASS_TO_PASS` unit tests is immediately accepted (`self_oracle=True`).
   - On `marshmallow-code__marshmallow-1359`, `v14_no_falsifier` accepted a narrow `List` fix on `b1` that missed `Tuple` and `root` schema fallback when `parent.opts` is absent (`0/1` F2P). In Full V14, `L1-B` immediately exposed the `Tuple` / `root` invariant gap.
3. **Why `CONT` Delta-Refinement (`v14_no_cont`: `100% -> 66.7%`) Is Essential**:
   - When `enable_cont_refinement=False`, every episode after a near-miss (`V = 0.55–0.78`) must reset to `HEAD` (`GEN`-only) and start from scratch in a different locus.
   - On `pydicom__pydicom-1255`, `v14_no_cont` burned `71` moves and `1,052s` repeatedly re-discovering `filebase.py` without being able to incrementally repair `jsonrep.py` on top of the working `b1` checkpoint.
4. **Why Removing All Hierarchy Collapses When `hints_text` Is Disabled (`v12_agentic`: `0 / 6 = 0.0%`)**:
   - In earlier evaluations where `hints_text` was passed (`--include-hints`), `v12_agentic` solved `2/6` (`33.3%`) because maintainer hints named the exact target function on `marshmallow-1359` and `sqlfluff-1517`.
   - Under strict zero-hint evaluation (`--include-hints` OFF), `v12_agentic` drops to **`0 / 6` (`0.0%`)** (`14/57` F2P tests), whereas **Full V14 (`v13_hier_mcts`) maintains `6 / 6` (`100.0%`)** (`57/57` F2P tests) in `278.8s` average wall-clock time.
