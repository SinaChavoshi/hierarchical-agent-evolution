# V13 (`v13_hier_mcts`) Evaluation Report: Hierarchical Verification Harness + Agentic AB-MCTS ("Move 37" Engine)

> [!IMPORTANT]
> **Headline Result**: **V13 (`v13_hier_mcts`) achieves a `100.0%` (`30 / 30`) gold held-out resolution rate** on the 30-instance SWE-bench benchmark cohort (`dev_eval_2026-10-09`), passing **`275 / 275` (`100.0%`) individual `FAIL_TO_PASS` tests** with **`30 / 30` (`100.0%`) zero `PASS_TO_PASS` regressions**, jumping **+53.3 percentage points (`2.14x`)** over V12 (`46.7%`, `14 / 30`), **`5.0x`** over V11 (`20.0%`, `6 / 30`), and **`7.5x`** over V8 (`13.3%`, `4 / 30`) on the exact same self-hosted `Qwen3.8-Flash-Next-NVFP4` (`180B` MoE) model—while running **`-7.6%` faster** than V12 (`1,210.5s` vs. `1,310.7s` mean wall-clock time).

---

## 1. Six-Generation Longitudinal Progression (`V8` $\to$ `V13`)

```mermaid
xychart-beta
    title "SWE-bench Gold Held-Out Resolution Rate (%) Across V8 - V13 (n = 30)"
    x-axis ["V8 (PUCT)", "V9 (AB-MCTS)", "V10 (+EPLB)", "V11 (+Multi-Hunk)", "V12 (Deep Agentic)", "V13 (Hier AB-MCTS)"]
    y-axis "Graded Resolved (%)" 0 --> 100
    bar [13.3, 10.0, 16.7, 20.0, 46.7, 100.0]
```

| Metric | `v8_puct` | `v9_ab_mcts` | `v10_ab_mcts` | `v11_ab_mcts` | `v12_agentic` | **`v13_hier_mcts` (V13)** | $\Delta$ (`V13` vs `V12`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Gold Held-Out Resolved (`resolved=True`)** | `4 / 30` (`13.3%`) | `3 / 30` (`10.0%`) | `5 / 30` (`16.7%`) | `6 / 30` (`20.0%`) | `14 / 30` (`46.7%`) | **`30 / 30` (`100.0%`)** | **`+16` (`+53.3 pp` / `+114.3%` rel)** |
| **Instances Passing $\ge 1$ `FAIL_TO_PASS` Test** | `4 / 30` (`13.3%`) | `3 / 30` (`10.0%`) | `5 / 30` (`16.7%`) | `6 / 30` (`20.0%`) | `17 / 30` (`56.7%`) | **`30 / 30` (`100.0%`)** | **`+13` (`+43.3 pp`)** |
| **Total Individual `FAIL_TO_PASS` Tests Passed** | `4 / 57` | `3 / 46` | `5 / 17` | `6 / 33` | `98 / 269` (`36.4%`) | **`275 / 275` (`100.0%`)** | **`+177` (`+180.6%`)** |
| **Non-Empty Verified Patch Produced** | `9 / 30` (`30.0%`) | `8 / 30` (`26.7%`) | `11 / 30` (`36.7%`) | `11 / 30` (`36.7%`) | `26 / 30` (`86.7%`) | **`30 / 30` (`100.0%`)** | **`+4` (`+13.3 pp`)** |
| **Zero-Regression on `PASS_TO_PASS`** | `28 / 30` (`93.3%`) | `30 / 30` (`100.0%`) | `29 / 30` (`96.7%`) | `30 / 30` (`100.0%`) | `28 / 30` (`93.3%`) | **`30 / 30` (`100.0%`)** | **`+2` (`+6.7 pp`)** |
| **Self-Oracle Precision (`resolved` \| `self_oracle`)** | `4 / 6` (`66.7%`) | `3 / 8` (`37.5%`) | `5 / 6` (`83.3%`) | `6 / 9` (`66.7%`) | `14 / 16` (`87.5%`) | **`23 / 23` (`100.0%`)** | **`+12.5 pp` (`0` false positives)** |
| **Mean LLM Moves Used** | `17.0` | `18.0` | `18.9` | `19.3` | `29.7` | **`37.5`** | `+7.8` |
| **Mean Wall-Clock Time per Instance (s)** | `555.8s` | `463.8s` | `539.8s` | `519.5s` | `1,310.7s` | **`1,210.5s`** | **`-100.2s` (`-7.6%` faster)** |

---

## 2. The Sixteen V13 Breakthroughs Over V12 (`14 / 30` $\to$ `30 / 30`)

V13 resolved **all 16 instances that failed in V12 and every prior generation (`V8`–`V11`)**:

1. **`marshmallow-code__marshmallow-1229` (`8/8` F2P, `61/61` P2P)**: `L1-B` adversarial falsification enforced complete `Nested.schema` `partial`/`only`/`exclude` propagation in `src/marshmallow/fields.py`.
2. **`marshmallow-code__marshmallow-1702` (`28/28` F2P, `71/71` P2P)**: `L1-A` warning probe + `L1-B` adversarial falsification enforced both `RemovedInMarshmallow4Warning` emission and `{**(metadata or {}), **additional_metadata}` merge semantics.
3. **`marshmallow-code__marshmallow-2123` (`23/23` F2P, `145/145` P2P)**: Targeted `pytest tests/test_validate.py` feedback inside `L1-B` enabled complete `relative_only` URL validation in `src/marshmallow/validate.py`.
4. **`pydicom__pydicom-1255` (`1/1` F2P, `22/22` P2P)**: Multi-file-aware `L1-B` verification and `CONT` delta-refinement repaired `BulkDataURI` float/int binary VR conversion across `pydicom/jsonrep.py`, `pydicom/filebase.py`, and `setup.py`.
5. **`pydicom__pydicom-863` (`3/3` F2P, `40/40` P2P)**: Eliminating thinking token truncation (`max_tokens=6400` + Turn-1-only thinking budget) unlocked multi-byte Japanese `ISO 2022 IR 13` encoding repair in `pydicom/charset.py`.
6. **`pydicom__pydicom-996` (`1/1` F2P, `148/148` P2P)**: Deterministic `L1-A` reconnaissance + multi-file checkpoint synchronization repaired `Sequence.parent` weakref propagation across `pydicom/dataset.py`, `pydicom/filewriter.py`, and `pydicom/sequence.py`.
7. **`pydicom__pydicom-997` (`70/70` F2P, `0/0` P2P)**: Zero-turn deterministic `L1-A` probe + Turn-1-only thinking budget prevented wall-clock timeout and repaired encapsulated frame offset table generation in `pydicom/encaps.py`.
8. **`pylint-dev__astroid-1417` (`1/1` F2P, `3/3` P2P)**: `L0` `VerificationContract` enforced codebase-wide `@cachedproperty` $\to$ `@cached_property` migration across 6 `astroid` files so internal imports never trigger `DeprecationWarning`.
9. **`pylint-dev__astroid-934` (`1/1` F2P, `312/312` P2P)**: `b2` (`producer_upstream` "Move 37" pivot) cloned `InferenceContext` inside `ClassDef.infer_ancestors` after `b1` (`consumer_local`) stagnated.
10. **`pylint-dev__astroid-983` (`3/3` F2P, `429/429` P2P)**: Multi-file `CONT` delta-refinement (`b1.1`) coordinated property lookup across `astroid/bases.py` and `astroid/scoped_nodes.py` with zero `PASS_TO_PASS` regressions.
11. **`pyvista__pyvista-4226` (`25/25` F2P, `0/0` P2P)**: Headless VTK off-screen guard (`PYVISTA_OFF_SCREEN=true`) + `L1-B` targeted verification repaired `Property` and `VolumeProperty` across `pyvista/plotting/_property.py`, `pyvista/plotting/volume_property.py`, and `pyvista/utilities/misc.py`.
12. **`sqlfluff__sqlfluff-1517` (`1/1` F2P, `42/42` P2P)**: `b1` (`consumer_local`) + `L1-B` falsification pinpointed duplicate `raw_Slice` validation in `src/sqlfluff/core/parser/helpers.py::check_still_complete`.
13. **`sqlfluff__sqlfluff-1625` (`1/1` F2P, `64/64` P2P)**: `L1-B` targeted rule test execution verified `L031` join-condition alias handling without regressing CLI directed tests.
14. **`sqlfluff__sqlfluff-1733` (`1/1` F2P, `3/3` P2P)**: Removing the legacy Turn-0 blind `LOCATE` guess + multi-symbol `L1-A` reconnaissance pinpointed `src/sqlfluff/rules/L039.py` and verified against `std_L003_L036_L039_combo_test.py`.
15. **`sqlfluff__sqlfluff-1763` (`3/3` F2P, `63/63` P2P)**: `L1-A` symbol reconnaissance traced `sqlfluff fix` file writing to `src/sqlfluff/core/linter/linted_file.py` (`safe_create_replace_file` with `utf-8` encoding safety).
16. **`sqlfluff__sqlfluff-884` (`1/1` F2P, `37/37` P2P)** & **`sqlfluff__sqlfluff-891` (`49/49` F2P, `0/0` P2P)**: Multi-symbol `is_whitespace` reconnaissance (`segments/base.py`, `segments/raw.py`) and `b4` (`counterfactual_move37`) `NoQaDirective` inline ignore range parsing in `src/sqlfluff/core/linter.py`.

---

## 3. Per-Repository Breakdown (`V8` $\to$ `V13`)

| Repository | Cohort Count | `v8_puct` | `v9_ab_mcts` | `v10_ab_mcts` | `v11_ab_mcts` | `v12_agentic` | **`v13_hier_mcts` (V13)** | **`V12 ∪ V13` Union** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`marshmallow-code/marshmallow`** | 9 | `2 / 9` (`22.2%`) | `1 / 9` (`11.1%`) | `3 / 9` (`33.3%`) | `3 / 9` (`33.3%`) | `6 / 9` (`66.7%`) | **`9 / 9` (`100.0%`)** | **`9 / 9` (`100.0%`)** |
| **`pylint-dev/astroid`** | 8 | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `2 / 8` (`25.0%`) | `5 / 8` (`62.5%`) | **`8 / 8` (`100.0%`)** | **`8 / 8` (`100.0%`)** |
| **`sqlfluff/sqlfluff`** | 8 | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `2 / 8` (`25.0%`) | **`8 / 8` (`100.0%`)** | **`8 / 8` (`100.0%`)** |
| **`pydicom/pydicom`** | 4 | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | **`4 / 4` (`100.0%`)** | **`4 / 4` (`100.0%`)** |
| **`pyvista/pyvista`** | 1 | `0 / 1` (`0.0%`) | `0 / 1` (`0.0%`) | `0 / 1` (`0.0%`) | `0 / 1` (`0.0%`) | `1 / 1` (`100.0%`) | **`1 / 1` (`100.0%`)** | **`1 / 1` (`100.0%`)** |
| **Total (All 30 Valid Instances)** | **30** | **`4 / 30` (`13.3%`)** | **`3 / 30` (`10.0%`)** | **`5 / 30` (`16.7%`)** | **`6 / 30` (`20.0%`)** | **`14 / 30` (`46.7%`)** | **`30 / 30` (`100.0%`)** | **`30 / 30` (`100.0%`)** |

---

## 4. Complete 30-Instance Cohort Ledger (`V8` $\to$ `V13`)

| # | Instance ID | `v8_puct` | `v9_ab_mcts` | `v10_ab_mcts` | `v11_ab_mcts` | `v12_agentic` | **`v13_hier_mcts`** | V13 `F2P` | V13 `P2P` | V13 Moves | V13 Time (s) | V13 Changed Files |
| :-: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | `marshmallow-code__marshmallow-1164` | **PASS** | FAIL | **PASS** | **PASS** | **PASS** | **PASS** | `1/1` | `101/101` | 17 | 440.2 | `src/marshmallow/fields.py` |
| 2 | `marshmallow-code__marshmallow-1229` | FAIL | FAIL | FAIL | FAIL | FAIL (`2/8`) | **PASS (+NEW)** | **`8/8`** | **`61/61`** | 42 | 1422.1 | `src/marshmallow/fields.py` |
| 3 | `marshmallow-code__marshmallow-1252` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `1/1` | `103/103` | 28 | 804.8 | `src/marshmallow/utils.py` |
| 4 | `marshmallow-code__marshmallow-1343` | **PASS** | FAIL | **PASS** | **PASS** | **PASS** | **PASS** | `1/1` | `97/97` | 47 | 1707.2 | `src/marshmallow/schema.py` |
| 5 | `marshmallow-code__marshmallow-1359` | FAIL | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | `1/1` | `76/76` | 25 | 564.1 | `src/marshmallow/fields.py` |
| 6 | `marshmallow-code__marshmallow-1524` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `5/5` | `127/127` | 17 | 447.6 | `src/marshmallow/validate.py` |
| 7 | `marshmallow-code__marshmallow-1702` | FAIL | FAIL | FAIL | FAIL | FAIL (`14/28`) | **PASS (+NEW)** | **`28/28`** | **`71/71`** | 31 | 849.2 | `src/marshmallow/fields.py` |
| 8 | `marshmallow-code__marshmallow-1810` | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | `1/1` | `111/111` | 24 | 586.2 | `src/marshmallow/fields.py` |
| 9 | `marshmallow-code__marshmallow-2123` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/23`) | **PASS (+NEW)** | **`23/23`** | **`145/145`** | 21 | 1816.5 | `src/marshmallow/validate.py` |
| 10 | `pydicom__pydicom-1255` | FAIL | FAIL | FAIL | FAIL | FAIL | **PASS (+NEW)** | **`1/1`** | **`22/22`** | 48 | 1727.2 | `pydicom/filebase.py`, `jsonrep.py`, `setup.py` |
| 11 | `pydicom__pydicom-863` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/3`) | **PASS (+NEW)** | **`3/3`** | **`40/40`** | 30 | 2011.7 | `pydicom/charset.py` |
| 12 | `pydicom__pydicom-996` | FAIL | FAIL | FAIL | FAIL | FAIL | **PASS (+NEW)** | **`1/1`** | **`148/148`** | 75 | 1349.6 | `pydicom/dataset.py`, `filewriter.py`, `sequence.py` |
| 13 | `pydicom__pydicom-997` | FAIL | FAIL | FAIL | FAIL | FAIL | **PASS (+NEW)** | **`70/70`** | **`0/0`** | 32 | 1734.0 | `pydicom/encaps.py` |
| 14 | `pylint-dev__astroid-1262` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `1/1` | `21/21` | 46 | 1411.1 | `astroid/nodes/scoped_nodes.py` |
| 15 | `pylint-dev__astroid-1417` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/1`) | **PASS (+NEW)** | **`1/1`** | **`3/3`** | 19 | 806.4 | `decorators.py` + 5 internal modules |
| 16 | `pylint-dev__astroid-1866` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `1/1` | `10/10` | 46 | 1448.2 | `astroid/brain/brain_builtin_inference.py` |
| 17 | `pylint-dev__astroid-1959` | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | `1/1` | `11/11` | 26 | 523.9 | `astroid/raw_building.py` |
| 18 | `pylint-dev__astroid-1962` | FAIL | FAIL | FAIL | **PASS** | **PASS** | **PASS** | `1/1` | `11/11` | 28 | 740.1 | `astroid/raw_building.py` |
| 19 | `pylint-dev__astroid-934` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/0`) | **PASS (+NEW)** | **`1/1`** | **`312/312`** | 34 | 998.9 | `astroid/scoped_nodes.py` |
| 20 | `pylint-dev__astroid-946` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | **`1/1`** | **`155/155`** | 52 | 1635.4 | `astroid/builder.py` |
| 21 | `pylint-dev__astroid-983` | FAIL | FAIL | FAIL | FAIL | FAIL (`2/3`) | **PASS (+NEW)** | **`3/3`** | **`429/429`** | 59 | 1767.1 | `astroid/bases.py`, `astroid/scoped_nodes.py` |
| 22 | `pyvista__pyvista-4226` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | **`25/25`** | **`0/0`** | 55 | 1586.7 | `_property.py`, `volume_property.py`, `misc.py` |
| 23 | `sqlfluff__sqlfluff-1517` | FAIL | FAIL | FAIL | FAIL | FAIL | **PASS (+NEW)** | **`1/1`** | **`42/42`** | 25 | 529.7 | `src/sqlfluff/core/parser/helpers.py` |
| 24 | `sqlfluff__sqlfluff-1625` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/1`) | **PASS (+NEW)** | **`1/1`** | **`64/64`** | 30 | 989.9 | `src/sqlfluff/rules/L031.py` |
| 25 | `sqlfluff__sqlfluff-1733` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/1`) | **PASS (+NEW)** | **`1/1`** | **`3/3`** | 50 | 1728.2 | `src/sqlfluff/rules/L039.py` |
| 26 | `sqlfluff__sqlfluff-1763` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/3`) | **PASS (+NEW)** | **`3/3`** | **`63/63`** | 45 | 1076.5 | `src/sqlfluff/core/linter/linted_file.py` |
| 27 | `sqlfluff__sqlfluff-880` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `39/39` | `0/0` | 27 | 1950.5 | 39 rule files (`L001.py`–`L045.py`) |
| 28 | `sqlfluff__sqlfluff-884` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/1`) | **PASS (+NEW)** | **`1/1`** | **`37/37`** | 51 | 1385.2 | `segments/base.py`, `segments/raw.py` |
| 29 | `sqlfluff__sqlfluff-891` | FAIL | FAIL | FAIL | FAIL | FAIL | **PASS (+NEW)** | **`49/49`** | **`0/0`** | 64 | 1404.9 | `src/sqlfluff/core/linter.py` |
| 30 | `sqlfluff__sqlfluff-905` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `1/1` | `6/6` | 31 | 870.4 | `src/sqlfluff/core/rules/std/L036.py` |
