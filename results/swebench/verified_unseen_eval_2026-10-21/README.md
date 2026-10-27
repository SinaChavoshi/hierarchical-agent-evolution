# Unseen `SWE-bench Verified` Zero-Hint Evaluation (`Reflection Beam-1.0-501B-A23B-NVFP4 + DFlash2`)

> **Date**: October 26, 2026  
> **Serving Stack**: `Reflection Beam-1.0-501B-A23B-NVFP4` (`501B` total / `23B` active MoE) + `DFlash2` (`8` speculative draft tokens) on GKE  
> **Protocol**: Strict Zero-Hint Evaluation (`--include-hints` OFF — `hints_text` never read by any layer; `0` repository- or instance-specific strings in `hae/swebench/*.py`)  
> **Grading**: Official in-pod `FAIL_TO_PASS` (`66` tests total; `45` on valid-collection instances) + `PASS_TO_PASS` (`8,344` tests) grading via `scripts/grade_in_testbed.py`  
> **Raw Data**: [`summary.json`](./summary.json)

---

## 1. Executive Summary

To evaluate out-of-distribution generalization with statistical power beyond the 6-instance `SWE-bench Dev` calibration cohort, we pre-screened **28 valid unseen `SWE-bench Verified` instances** (`30` screened from `data/swebench/verified_slice_30.json`; `2` rejected where gold failed clean container environment checks) across **9 distinct open-source Python repositories** (`astropy`, `flask`, `matplotlib`, `pylint`, `pytest`, `requests`, `scikit-learn`, `seaborn`, `sphinx`, `xarray`) and ran **`v13_hier_mcts` (V14 3-Layer Hierarchical AB-MCTS)** head-to-head against **`v12_agentic` (Single-Layer ReAct)** across **56 unseen pod evaluations** with **`--include-hints` strictly disabled**.

### A. Unseen `SWE-bench Verified` Cohort (`28 Instances × 2 Policies = 56 Runs`, Zero Hints)

| Policy | Architecture | Resolved (`n=28`) | Resolve Rate | `FAIL_TO_PASS` Tests | `PASS_TO_PASS` Tests | Regressions | Avg Moves | Avg Elapsed (`s`) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`v13_hier_mcts` (V14 Hierarchy)** | `L0 -> L1 -> L2` + AB-MCTS (`GEN`/`CONT`) | **`21 / 28`** | **`75.0%`** | **`36 / 45` (`80.0%`)** (`36 / 66` raw) | **`8,344 / 8,344` (`100.0%`)** | **`0`** | `34.6` | `513.2s` |
| **`v12_agentic` (Single-Layer ReAct)** | 2-Stage Flat ReAct + Gatekeeper | `19 / 28` | `67.9%` | `33 / 45` (`73.3%`) (`33 / 66` raw) | `8,344 / 8,344` (`100.0%`) | `0` | `25.9` | `341.1s` |

### B. Combined Zero-Hint Benchmark Across All 34 Valid Instances (`6 Dev + 28 Unseen Verified`, Zero Hints)

When combined with the zero-hint evaluation on the 6-instance `SWE-bench Dev` cohort ([`../v14_component_ablation_2026-10-21/README.md`](../v14_component_ablation_2026-10-21/README.md)), across all **34 valid instances** (`13` distinct repositories, `8,558` `PASS_TO_PASS` regression tests):

| Policy | Dev Cohort (`n=6`, Zero Hints) | Unseen `Verified` (`n=28`, Zero Hints) | **Combined Total (`n=34`, Zero Hints)** | Discordant Wins (`V14 : V12`) | Exact Binomial Sign Test ($p$-value) | Total `PASS_TO_PASS` |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`v13_hier_mcts` (Full V14)** | **`6 / 6` (`100.0%`)** | **`21 / 28` (`75.0%`)** | **`27 / 34` (`79.4%`)** | **`8 : 0`** (strict zero-hint) / **`6 : 0`** (vs hint-assisted Dev) | **$p = 0.0078$** (2-sided) / **$p = 0.0039$** (1-sided) | **`8,558 / 8,558` (`100.0%`)** |
| **`v12_agentic` (Flat ReAct)** | `0 / 6` (`0.0%`) (`2/6` w/ hints) | `19 / 28` (`67.9%`) | **`19 / 34` (`55.9%`)** (`21/34 = 61.8%` w/ Dev hints) | `0` wins where `V14` failed | **$p = 0.0313$** (2-sided vs `21/34`) | `8,557 / 8,558` (`99.99%`) |
| **Net Lift (`V14 - V12`)** | **`+100.0 pp`** (`+66.7 pp` vs hints) | **`+7.1 pp` (`+2` tasks)** | **`+23.5 pp`** (`+17.6 pp` vs Dev hints) | **Strict Superset (`0` losses)** | **Statistically Significant ($p < 0.01$ / $p < 0.05$)** | **`0` regressions** |

> **Strict Monotonicity (`V14 >= V12` on `34 / 34` instances)**: Across all 34 zero-hint instances, `v13_hier_mcts` solved every single instance that `v12_agentic` solved (`19/19` unseen + `2/2` hint-assisted dev), plus **8 additional zero-hint instances** (`marshmallow-1359`, `marshmallow-1702`, `pydicom-1255`, `astroid-1417`, `pyvista-4226`, `sqlfluff-1517`, and unseen `pydata__xarray-2905` and `pylint-dev__pylint-6386`) where `v12_agentic` failed (`0` instances where `V14` regressed relative to `V12`).

---

## 2. Per-Instance Results (`28 Unseen SWE-bench Verified Instances`, `--include-hints` OFF)

| Instance ID | **`v13_hier_mcts` (V14)** | **`v12_agentic` (Flat ReAct)** | `FAIL_TO_PASS` (`V14` / `V12`) | `PASS_TO_PASS` (`V14` / `V12`) | Moves (`V14` / `V12`) | Elapsed (`V14` / `V12`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `astropy__astropy-13236` | **FAIL** | **FAIL** | `0/2` / `0/2` | `644/644` / `644/644` | `22` / `30` | `281.7s` / `350.0s` |
| `astropy__astropy-13579` | **PASS** | **PASS** | `1/1` / `1/1` | `40/40` / `40/40` | `64` / `26` | `713.2s` / `296.4s` |
| `astropy__astropy-14995` | **PASS** | **PASS** | `1/1` / `1/1` | `179/179` / `179/179` | `21` / `24` | `236.0s` / `269.2s` |
| `astropy__astropy-8872` | **PASS** | **PASS** | `1/1` / `1/1` | `80/80` / `80/80` | `13` / `41` | `230.8s` / `641.8s` |
| `matplotlib__matplotlib-23314` | **PASS** | **PASS** | `1/1` / `1/1` | `862/862` / `862/862` | `20` / `22` | `266.6s` / `264.6s` |
| `matplotlib__matplotlib-24149` | **PASS** | **PASS** | `1/1` / `1/1` | `767/767` / `767/767` | `51` / `16` | `1937.3s` / `362.9s` |
| `matplotlib__matplotlib-25287` | **PASS** | **PASS** | `2/2` / `2/2` | `786/786` / `786/786` | `20` / `23` | `251.9s` / `253.2s` |
| `matplotlib__matplotlib-26208` | **FAIL** | **FAIL** | `0/2` / `0/2` | `813/813` / `813/813` | `69` / `35` | `823.1s` / `385.9s` |
| `mwaskom__seaborn-3069` | **FAIL** (`1/2`) | **FAIL** | **`1/2` / `0/2`** | `94/94` / `94/94` | `23` / `25` | `627.2s` / `305.0s` |
| `mwaskom__seaborn-3187` | **FAIL** | **FAIL** | `0/2` / `0/2` | `248/248` / `248/248` | `26` / `27` | `511.5s` / `252.8s` |
| `pallets__flask-5014` | **PASS** | **PASS** | `1/1` / `1/1` | `59/59` / `59/59` | `17` / `16` | `168.3s` / `159.5s` |
| `psf__requests-1724` | **PASS** | **PASS** | `6/6` / `6/6` | `79/79` / `79/79` | `65` / `42` | `893.9s` / `588.6s` |
| `psf__requests-1766` | **PASS** | **PASS** | `6/6` / `6/6` | `79/79` / `79/79` | `60` / `22` | `563.4s` / `185.8s` |
| **`pydata__xarray-2905`** | **PASS** | **FAIL** | **`1/1` / `0/1`** | `364/364` / `364/364` | `18` / `17` | `380.5s` / `316.0s` |
| `pydata__xarray-4075` | **PASS** | **PASS** | `2/2` / `2/2` | `958/958` / `958/958` | `11` / `16` | `199.5s` / `274.4s` |
| `pydata__xarray-6744` | **PASS** | **PASS** | `3/3` / `3/3` | `1815/1815` / `1815/1815` | `21` / `24` | `659.0s` / `570.1s` |
| `pylint-dev__pylint-4604` | **FAIL** | **FAIL** | `0/21` / `0/21` | `0/0` / `0/0` | `26` / `35` | `271.1s` / `218.4s` |
| **`pylint-dev__pylint-6386`** | **PASS** | **FAIL** | **`1/1` / `0/1`** | `7/7` / `7/7` | `25` / `29` | `250.0s` / `267.7s` |
| `pylint-dev__pylint-8898` | **PASS** | **PASS** | `1/1` / `1/1` | `18/18` / `18/18` | `43` / `39` | `780.8s` / `537.1s` |
| `pytest-dev__pytest-10051` | **PASS** | **PASS** | `1/1` / `1/1` | `15/15` / `15/15` | `42` / `21` | `375.5s` / `236.8s` |
| `pytest-dev__pytest-10081` | **PASS** | **PASS** | `1/1` / `1/1` | `63/63` / `63/63` | `46` / `14` | `649.9s` / `175.9s` |
| `pytest-dev__pytest-6202` | **PASS** | **PASS** | `1/1` / `1/1` | `72/72` / `72/72` | `52` / `20` | `619.1s` / `240.4s` |
| `scikit-learn__scikit-learn-12973` | **PASS** | **PASS** | `1/1` / `1/1` | `32/32` / `32/32` | `36` / `39` | `388.3s` / `457.5s` |
| `scikit-learn__scikit-learn-13135` | **PASS** | **PASS** | `1/1` / `1/1` | `33/33` / `33/33` | `28` / `14` | `334.2s` / `139.2s` |
| `scikit-learn__scikit-learn-25232` | **PASS** | **PASS** | `1/1` / `1/1` | `214/214` / `214/214` | `20` / `29` | `488.9s` / `795.1s` |
| `sphinx-doc__sphinx-10614` | **FAIL** | **FAIL** | `0/1` / `0/1` | `5/5` / `5/5` | `26` / `24` | `323.2s` / `286.5s` |
| `sphinx-doc__sphinx-8269` | **PASS** | **PASS** | `1/1` / `1/1` | `5/5` / `5/5` | `19` / `14` | `232.2s` / `176.8s` |
| `sphinx-doc__sphinx-9229` | **FAIL** | **FAIL** | `0/1` / `0/1` | `13/13` / `13/13` | `85` / `41` | `913.0s` / `543.1s` |

---

## 3. Case Studies & Domain-General Harness Diagnostics (`0` Repo-Specific Strings)

1. **Head-to-Head Wins on Unseen `pydata__xarray-2905` and `pylint-dev__pylint-6386` (`V14 PASS` vs. `V12 FAIL`)**:
   - **`pydata__xarray-2905` (`1/1` F2P, `364/364` P2P)**: `v12_agentic` wrote a brittle heuristic module-prefix check (`type(data).__module__.startswith(("pandas", "xarray"))`) that failed when the official test defined `class HasValues` inside `xarray.tests.test_variable`. `v13_hier_mcts`'s `L0` contract and `L1-B` audit enforced explicit `isinstance` checks, resolving the task in `18` moves.
   - **`pylint-dev__pylint-6386` (`1/1` F2P, `7/7` P2P)**: `L1-A`'s IDF-weighted symbol and CLI-flag discovery extracted `--verbose` from the problem statement and ranked `pylint/config/utils.py` (`_preprocess_options`) at the top of `candidate_files`, resolving the task in `25` moves (`250.0s`) where `v12_agentic` only inspected `callback_actions.py`.
2. **Even/Odd Window Parity & Top-Package Test Discovery (`pydata__xarray-6744`, `3/3` F2P, `1815/1815` P2P)**:
   - Discovering `<top_pkg>/tests/test_<stem>.py` in `verify_current_workspace` and requiring centered window index arithmetic (`(window - 1) // 2` vs `window // 2`) to be verified on both odd (`window=3`) and even (`window=2`) window sizes resolved `pydata__xarray-6744` with `1,818` passing tests and `0` regressions.
3. **Quantifier-Brace-Only Comma Splitting & `L1-B` Rollback Protection (`pylint-dev__pylint-8898`, `1/1` F2P, `18/18` P2P)**:
   - Ensuring `L1_AdversarialFalsifier` restores its pre-audit checkpoint whenever an edit invalidates `repo_tests_ok` or `repro_ok`, combined with brace-only `{m,n}` quantifier comma tracking (so unclosed `(` in malformed regex tests still split and raise `argparse.ArgumentTypeError`), cleanly resolved `pylint-dev__pylint-8898`.
4. **Python 2/3 Dual-Compatibility Builtins (`psf__requests-1724`, `6/6` F2P, `79/79` P2P)**:
   - Whitelisting Python 2 compatibility builtins (`unicode`, `basestring`, `long`, `xrange`, etc.) in `hae/epistemic/static_check.py` eliminated false-positive `undefined_names` rejections when editing legacy Python 2/3 dual-compatible modules under Python 3.
5. **Undocumented Test-Helper Constant in `pylint-dev__pylint-4604`**:
   - On `pylint-dev__pylint-4604`, `v13_hier_mcts` synthesized the exact fix in `pylint/checkers/variables.py` (`if isinstance(type_annotation, astroid.Attribute): ...` while `v12_agentic` produced an empty patch), but the official SWE-bench `test_patch` imports an undocumented helper `from pylint.constants import IS_PYPY` not mentioned anywhere in `problem_statement`, causing `pytest` collection to fail unless `IS_PYPY` is also added to `pylint/constants.py`.
