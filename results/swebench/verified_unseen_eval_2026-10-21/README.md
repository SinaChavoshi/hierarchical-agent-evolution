# Unseen `SWE-bench Verified` Zero-Hint Evaluation (`Reflection Beam-1.0-501B-A23B-NVFP4 + DFlash2`)

> **Date**: October 21, 2026  
> **Serving Stack**: `Reflection Beam-1.0-501B-A23B-NVFP4` (`501B` total / `23B` active MoE) + `DFlash2` (`8` speculative draft tokens) across `3x` multi-node `TP=4` replicas (`6x g2-standard-96`, `48x NVIDIA L4 GPUs`)  
> **Protocol**: Strict Zero-Hint Evaluation (`--include-hints` OFF — `hints_text` never read by any layer; frozen V14 prompts with zero task-specific tuning)  
> **Grading**: Official in-pod `FAIL_TO_PASS` (`22` tests) + `PASS_TO_PASS` (`3,533` tests) grading via `scripts/grade_in_testbed.py`  
> **Raw Data**: [`summary.json`](./summary.json)

---

## 1. Executive Summary

To evaluate out-of-distribution generalization beyond the 6-instance `SWE-bench Dev` cohort, we ran frozen **`v13_hier_mcts` (V14 3-Layer Hierarchical AB-MCTS)** head-to-head against **`v12_agentic` (Single-Layer ReAct)** on **12 pre-screened, unseen `SWE-bench Verified` instances** spanning **8 distinct open-source Python repositories** (`astropy`, `flask`, `matplotlib`, `pylint`, `requests`, `seaborn`, `sphinx`, `xarray`) with **`--include-hints` strictly disabled**.

### A. Unseen `SWE-bench Verified` Cohort (`12 Instances × 2 Policies = 24 Runs`, Zero Hints)

| Policy | Architecture | Resolved (`n=12`) | Resolve Rate | `FAIL_TO_PASS` Tests | `PASS_TO_PASS` Tests | Regressions | Avg Moves | Avg Tokens | Avg Elapsed (`s`) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`v13_hier_mcts` (V14 Hierarchy)** | `L0 -> L1 -> L2` + AB-MCTS (`GEN`/`CONT`) | **`8 / 12`** | **`66.7%`** | **`15 / 22` (`68.2%`)** | **`3,533 / 3,533` (`100.0%`)** | **`0`** | `38.3` | `255,132` | `595.0s` |
| **`v12_agentic` (Single-Layer ReAct)** | 2-Stage Flat ReAct + Gatekeeper | `7 / 12` | `58.3%` | `14 / 22` (`63.6%`) | `3,533 / 3,533` (`100.0%`) | `0` | `24.2` | `129,178` | `292.9s` |

### B. Combined Zero-Hint Benchmark Across All 18 Valid Instances (`6 Dev + 12 Unseen Verified`, Zero Hints)

When combined with the zero-hint evaluation on the 6-instance `SWE-bench Dev` cohort ([`../v14_component_ablation_2026-10-21/README.md`](../v14_component_ablation_2026-10-21/README.md)), across all **18 valid instances** (`12` distinct repositories, `79` `FAIL_TO_PASS` tests, `3,747` `PASS_TO_PASS` tests):

| Policy | Dev Cohort (`n=6`, Zero Hints) | Unseen `Verified` (`n=12`, Zero Hints) | **Combined Total (`n=18`, Zero Hints)** | Total `FAIL_TO_PASS` | Total `PASS_TO_PASS` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **`v13_hier_mcts` (Full V14)** | **`6 / 6` (`100.0%`)** | **`8 / 12` (`66.7%`)** | **`14 / 18` (`77.8%`)** | **`72 / 79` (`91.1%`)** | **`3,747 / 3,747` (`100.0%`)** |
| **`v12_agentic` (Flat ReAct)** | `0 / 6` (`0.0%`) | `7 / 12` (`58.3%`) | **`7 / 18` (`38.9%`)** | `28 / 79` (`35.4%`) | `3,746 / 3,747` (`99.97%`) |
| **Net Lift (`V14 - V12`)** | **`+100.0 pp` (`+6` tasks)** | **`+8.4 pp` (`+1` task)** | **`+38.9 pp` (`2.0x` resolve rate)** | **`+55.7 pp` (`+44` F2P)** | **`0` regressions** |

> **Strict Monotonicity (`V14 >= V12` on `18 / 18` instances)**: Across all 18 zero-hint instances, `v13_hier_mcts` solved every single instance that `v12_agentic` solved (`7/7`), plus **7 additional instances** (`marshmallow-1359`, `marshmallow-1702`, `pydicom-1255`, `astroid-1417`, `pyvista-4226`, `sqlfluff-1517`, and unseen `pydata__xarray-2905`) where `v12_agentic` failed (`0` instances where `V14` regressed relative to `V12`).

---

## 2. Per-Instance Results (`12 Unseen SWE-bench Verified Instances`, `--include-hints` OFF)

| Instance ID | Repository | **`v13_hier_mcts` (V14 Hierarchy)** | **`v12_agentic` (Single-Layer ReAct)** | `FAIL_TO_PASS` (`V14` vs `V12`) | `PASS_TO_PASS` (`V14` vs `V12`) | Moves (`V14` / `V12`) | Wall-Clock (`V14` / `V12`) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `astropy__astropy-8872` | `astropy/astropy` | **PASS** | **PASS** | `1/1` vs `1/1` | `80/80` vs `80/80` | `67` / `41` | `1113.9s` / `641.8s` |
| `astropy__astropy-13579` | `astropy/astropy` | **PASS** | **PASS** | `1/1` vs `1/1` | `40/40` vs `40/40` | `64` / `26` | `713.2s` / `296.4s` |
| `matplotlib__matplotlib-25287` | `matplotlib/matplotlib` | **PASS** | **PASS** | `2/2` vs `2/2` | `786/786` vs `786/786` | `20` / `23` | `251.9s` / `253.2s` |
| `matplotlib__matplotlib-26208` | `matplotlib/matplotlib` | **FAIL** | **FAIL** | `0/2` vs `0/2` | `813/813` vs `813/813` | `64` / `35` | `653.3s` / `385.9s` |
| `mwaskom__seaborn-3069` | `mwaskom/seaborn` | **FAIL** | **FAIL** | `0/2` vs `0/2` | `94/94` vs `94/94` | `34` / `25` | `1857.0s` / `305.0s` |
| `mwaskom__seaborn-3187` | `mwaskom/seaborn` | **FAIL** | **FAIL** | `0/2` vs `0/2` | `248/248` vs `248/248` | `20` / `27` | `252.0s` / `252.8s` |
| `pallets__flask-5014` | `pallets/flask` | **PASS** | **PASS** | `1/1` vs `1/1` | `59/59` vs `59/59` | `17` / `16` | `168.3s` / `159.5s` |
| `psf__requests-1766` | `psf/requests` | **PASS** | **PASS** | `6/6` vs `6/6` | `79/79` vs `79/79` | `60` / `22` | `563.4s` / `185.8s` |
| **`pydata__xarray-2905`** | `pydata/xarray` | **PASS** | **FAIL** | **`1/1` vs `0/1`** | `364/364` vs `364/364` | `18` / `17` | `380.5s` / `316.0s` |
| `pydata__xarray-4075` | `pydata/xarray` | **PASS** | **PASS** | `2/2` vs `2/2` | `958/958` vs `958/958` | `11` / `16` | `199.5s` / `274.4s` |
| `pylint-dev__pylint-6386` | `pylint-dev/pylint` | **FAIL** | **FAIL** | `0/1` vs `0/1` | `7/7` vs `7/7` | `66` / `29` | `755.7s` / `267.7s` |
| `sphinx-doc__sphinx-8269` | `sphinx-doc/sphinx` | **PASS** | **PASS** | `1/1` vs `1/1` | `5/5` vs `5/5` | `19` / `14` | `232.2s` / `176.8s` |

---

## 3. Case Study & Failure Mode Diagnostics

### A. Head-to-Head Win on Unseen `pydata__xarray-2905` (`V14 PASS` vs. `V12 FAIL`)
- **Problem**: `xr.DataArray` / `xr.Variable` coerces any custom object with a `.values` property via `getattr(data, "values", data)` inside `as_compatible_data`.
- **Why `v12_agentic` Failed (`0/1` F2P)**: `v12_agentic` wrote a heuristic module-prefix check: `getattr(data, "values", data) if type(data).__module__.startswith(("pandas", "xarray")) else data`. In the official test suite (`xarray/tests/test_variable.py`), the test defines `class HasValues` inside `xarray.tests.test_variable` (whose `__module__` starts with `"xarray"`!), causing `v12_agentic`'s patch to fail the official `FAIL_TO_PASS` test.
- **Why `v13_hier_mcts` Succeeded (`1/1` F2P, `364/364` P2P)**: `L0`'s `VerificationContract` and `L1-B`'s adversarial audit enforced explicit type checking (`isinstance(data, (Variable, DataArray, pd.Series, pd.DataFrame, pd.Index))`), cleanly resolving the issue on `b1` in `18` moves.

### B. Root Causes of the 4 Unresolved Unseen Instances (`pylint-6386`, `seaborn-3069`, `seaborn-3187`, `matplotlib-26208`)
1. **Hidden Dual-Surface Requirement when `hints_text` Is Disabled (`mwaskom__seaborn-3187`)**:
   - The `problem_statement` only reports a bug in `seaborn.objects.Plot` (`Continuous` scale legend offset), which both `v12_agentic` and `v13_hier_mcts` fixed in `seaborn/_core/scales.py`. However, the hidden `FAIL_TO_PASS` test suite *also* asserts on the legacy `relplot` API (`seaborn/utils.py::locator_to_legend_entries`), which was only mentioned in `hints_text`.
2. **Post-Render Lifecycle Hook & Slow Proximity Test Suites (`mwaskom__seaborn-3069`)**:
   - `v13_hier_mcts` localized the exact target file (`seaborn/_core/plot.py`, whereas `v12_agentic` edited the wrong file `seaborn/_core/scales.py`), but applied axis margin/inversion tweaks during `_setup_scales` rather than `_finalize_figure` after artists are drawn, and `tests/_core/test_plot.py` exceeded the 30-second per-submit proximity pytest timeout.
3. **Shared-Axis State Mutation in `twinx` (`matplotlib__matplotlib-26208`) & `argparse` Action Subclassing (`pylint-dev__pylint-6386`)**:
   - Both agents localized the exact files (`lib/matplotlib/axes/_base.py` and `pylint/lint/base_options.py`), but in `matplotlib-26208` the reproduction script did not capture the exact `stackplot` + `twinx` call order, and in `pylint-6386` the agent's reproduction script did not fail on `HEAD` (`repro_fails_on_head=False`) when invoking `pylint -v` via CLI.
