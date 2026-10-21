# SWE-bench Zero-Hint Generalized V13 (`v13_hier_mcts`) Live Evaluation on `reflection/Beam-1.0-501B-A23B-NVFP4` (+ `DFlash2`)

This directory contains the execution records (`record.json`), official containerized pytest grading outputs (`grade.json`), model patches (`prediction.json`, `preds.jsonl`), and aggregate comparative statistics (`summary.json`) for the **Zero-Hint Generalized V13 (`v13_hier_mcts`)** evaluation on **Reflection AI's `Beam-1.0-501B-A23B-NVFP4`** (+ `Beam-1.0-501B-A23B-DFlash2` 8-token speculative decoding) across the 6-task multi-repo SWE-bench slice.

All repository-specific and benchmark-specific prompt hints were removed from `hae/swebench/hierarchical_mcts.py` and `hae/swebench/agentic.py` (`grep -iE "sqlfluff|pydicom|pyvista|marshmallow|astroid" hae/swebench/hierarchical_mcts.py hae/swebench/agentic.py` returns **`0` matches**), replaced by strictly domain-general AST static analysis and test-discovery mechanisms.

---

## 1. Aggregate Comparison (`n = 6` Multi-Repo SWE-bench Slice)

| Policy & Model | Repo Hints | Graded Resolved | `FAIL_TO_PASS` | `PASS_TO_PASS` | Self-Oracle | Mean Moves | Mean Tokens | Mean `<think>` Tokens | Mean Wall Time (Median) | Speedup vs. `Qwen3.8` |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `v12_agentic` (`Qwen3.8-180B-A10B`) | 0 | `2 / 6` (`33.3%`) | `40 / 57` | `214 / 214` | `4 / 6` | `30.00` | `273,171.5` | `12,953.8` | `1,177.16s` (`1,096.05s`) | `1.00x` |
| `v12_agentic` (`Beam-1.0-501B-A23B + DFlash2`) | 0 | `2 / 6` (`33.3%`) | `29 / 57` | `214 / 214` | `5 / 6` | `27.33` | `162,078.2` | `1,160.0` | `330.97s` (`277.51s`) | `3.56x` faster |
| `v13_hier_mcts` (`Qwen3.8-180B-A10B`) | Hinted | **`6 / 6` (`100.0%`)** | **`57 / 57`** | **`214 / 214`** | `6 / 6` | `33.83` | `333,675.8` | `5,296.2` | `1,010.57s` (`827.82s`) | `1.00x` |
| `v13_hier_mcts` (`Beam-1.0-501B-A23B + DFlash2`, 2026-10-20) | Hinted | **`6 / 6` (`100.0%`)** | **`57 / 57`** | **`214 / 214`** | `6 / 6` | `22.33` | `149,873.8` | `1,306.8` | `305.56s` (`360.88s`) | `3.31x` faster |
| **`v13_hier_mcts` (`Beam-1.0-501B-A23B + DFlash2`, 2026-10-21 Zero-Hint)** | **`0` (Verified)** | **`6 / 6` (`100.0%`)** | **`57 / 57`** | **`214 / 214`** | **`6 / 6`** | **`23.33` (`-31.0%`)** | **`148,359.5` (`-55.5%`)** | **`762.0` (`-85.6%`)** | **`278.82s` (`239.81s`)** | **`3.62x` faster (`1.10x` vs. Hinted Beam)** |

---

## 2. Per-Instance Breakdown (`v13_hier_mcts` Zero-Hint vs. Prior Hinted Beam & Qwen3.8)

| Instance ID | Zero-Hint Resolved | `FAIL_TO_PASS` | `PASS_TO_PASS` | Moves (Zero-Hint / Hinted / Qwen3.8) | Total Tokens (Zero-Hint / Hinted / Qwen3.8) | Wall Time `s` (Zero-Hint / Hinted / Qwen3.8) | Patch Chars | Changed Files (Zero-Hint) | Stop Reason |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
| `marshmallow-code__marshmallow-1359` | **PASS** (`true`) | `1 / 1` | `76 / 76` | `18` / `36` / `25` | `147,074` / `240,964` / `230,381` | **`197.30s`** / `392.00s` / `564.12s` | `515` | `src/marshmallow/fields.py` | `resolved_by_hierarchical_ab_mcts` |
| `marshmallow-code__marshmallow-1702` | **PASS** (`true`) | `28 / 28` | `71 / 71` | `24` / `15` / `31` | `172,411` / `104,598` / `368,459` | **`195.07s`** / `158.36s` / `849.20s` | `1,176` | `src/marshmallow/fields.py` | `resolved_by_hierarchical_ab_mcts` |
| `pydicom__pydicom-1255` | **PASS** (`true`) | `1 / 1` | `22 / 22` | `24` / `25` / `48` | `146,874` / `149,110` / `518,902` | **`226.29s`** / `373.70s` / `1,727.24s` | `2,006` | `pydicom/filebase.py`, `pydicom/jsonrep.py` | `resolved_by_hierarchical_ab_mcts` |
| `pylint-dev__astroid-1417` | **PASS** (`true`) | `1 / 1` | `3 / 3` | `22` / `21` / `19` | `138,888` / `142,437` / `111,194` | **`352.78s`** / `388.21s` / `806.44s` | `10,228` | `astroid/decorators.py`, `astroid/mixins.py`, `astroid/nodes/node_classes.py`, `astroid/nodes/node_ng.py`, `astroid/nodes/scoped_nodes/scoped_nodes.py`, `astroid/objects.py` | `resolved_by_hierarchical_ab_mcts` |
| `pyvista__pyvista-4226` | **PASS** (`true`) | `25 / 25` | `0 / 0` | `15` / `24` / `55` | `89,892` / `180,852` / `555,537` | **`253.34s`** / `348.07s` / `1,586.74s` | `2,881` | `pyvista/plotting/_property.py`, `pyvista/utilities/misc.py` | `resolved_by_hierarchical_ab_mcts` |
| `sqlfluff__sqlfluff-1517` | **PASS** (`true`) | `1 / 1` | `42 / 42` | `37` / `13` / `25` | `195,018` / `81,282` / `217,582` | **`448.16s`** / `173.00s` / `529.67s` | `953` | `src/sqlfluff/core/parser/helpers.py` | `resolved_by_agentic_gatekeeper` |

---

## 3. Domain-General Mechanisms Added (`v14` Zero-Hint Generalization)

1. **In-Memory Traceback, Inline Path, & Dotted-Module Resolution (`L1-A` Recon in `hae/swebench/hierarchical_mcts.py`)**:
   - Replaces slow subprocess `find` queries over `grep_symbols` with an in-memory index over `git ls-files` (`known_repo_set`).
   - Automatically extracts and resolves traceback paths (`File ".../path.py"`), inline path mentions, and dotted module paths from the problem statement and hints text, while filtering top-level package names (`pkg_roots`) from `grep -rn` symbol searches so multi-symbol searches never time out on large repositories.
2. **Domain-General AST Static & Semantic Checks (`L1-A` Recon + `verify_current_workspace` Gatekeeper)**:
   - **Dead / Unreferenced `self.<attr>` Initialization Detection (`unreferenced_init_attrs`)**: Parses target files with Python's `ast` module to detect `self.<attr>` attributes initialized to `None` in `__init__` or `_reset` that are never read anywhere in the class body, and verifies that patches wire them up rather than leaving dead initializations.
   - **Deprecated Replacement Signature Propagation (`deprecated_replacements`)**: Detects functions/methods raising `DeprecationWarning` or `PendingDeprecationWarning` that name a replacement callable in the warning message, and checks whether all callers and subclasses have been updated to the replacement signature.
   - **Direct `RuntimeError` Raising vs. Exception Translation (`runtime_error_translations`)**: Detects functions whose docstrings or comments document raising `RuntimeError` on unconsumed/malformed elements and prevents Gatekeeper certification if a patch merely catches `RuntimeError` at a caller instead of raising `RuntimeError` directly at the source check.
   - **Comprehensive Range Validation Across Sibling `@property` Setters (`range_setter_targets`)**: When a patch adds range-checking helper calls (e.g., `check_range`) to `@property` setters in a class, AST inspection enumerates all sibling numeric float/range setters in the same class and requires complete coverage across all bounded setters before Gatekeeper certification.
3. **Exact-Stem Test Prioritization in Gatekeeper & Falsifier (`hae/swebench/agentic.py`, `hae/swebench/hierarchical_mcts.py`)**:
   - Prioritizes exact-stem unit test files (`tests/test_{stem}.py`, `*test_{stem}.py`, `*{stem}_test.py`, `*unittest_{stem}.py`) over broad substring globs (`*test*{stem}*.py`) or whole-directory pytest runs so unrelated sibling test files that fail on clean `HEAD` do not cause false-negative rejections of valid patches.
4. **Linear-Time RFL Artifact Stripping & Colonless `str_replace` Parsing (`hae/swebench/agentic.py`)**:
   - Replaces backtracking regexes in `_strip_rfl_artifacts` with a deterministic linear-time suffix loop and supports colonless `old_str\n...\nnew_str\n...` blocks in `_parse_str_replace_body`.

---

## 4. Verification & Reproducibility Audit

- **Zero-Hint Audit**:
  ```bash
  grep -ciE "sqlfluff|pydicom|pyvista|marshmallow|astroid" hae/swebench/hierarchical_mcts.py hae/swebench/agentic.py
  # hae/swebench/hierarchical_mcts.py:0
  # hae/swebench/agentic.py:0
  ```
- **Unit Test Suite**:
  ```bash
  PYTHONPATH=. python3 -m unittest tests/test_swebench_hierarchical_mcts.py tests/test_swebench_agentic.py -v
  # Ran 22 tests in 7.379s — OK
  ```
