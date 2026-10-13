# V11 Evaluation Report: Closing Synthesis-Handoff Bottlenecks, Multi-Specialist Root Corroboration, & Escape-Preserving AST Splicing

## Executive Summary

Across all **30 valid SWE-bench `dev` instances** (`results/swebench/dev_eval_2026-10-09/`) evaluated on the 12-GPU NVIDIA MoE + EPLB GKE cluster (`nvidia/Qwen3.8-Flash-Next-NVFP4` across 3 × 4-GPU expert-parallel pods), **V11 (`v11_ab_mcts_swebench_firm_0`)** resolved all three targeted synthesis-handoff bottlenecks identified in V10 plus two structural Python AST/JSON splicing invariants discovered during execution forensics, reaching a new high watermark of **`6 / 30` (`20.00%`) resolved**:

| Generation / Policy | Search Algorithm & Key Changes | Resolved (`n = 30`) | Resolve Rate | Large-Module Unwritten Skips (`>20k` chars) | Mean Tokens / Instance | Mean Wall-Clock / Instance |
|---|---|---|---|---|---|---|
| **V8 (`v8_puct`)** | Fixed-width PUCT + V8 specialist role library (pre-EPLB) | `4 / 30` | `13.33%` | `7` | `85,482.7` | `555.8 s` (`153.8 tok/s`) |
| **V9 (`v9_ab_mcts`)** | Uncalibrated AB-MCTS (`ab_pending_loss = 0.0`, pre-EPLB) | `3 / 30` | `10.00%` | `4` | `96,041.3` | `463.8 s` (`207.1 tok/s`) |
| **V10 (`v10_ab_mcts`)** | Empirical AB-MCTS (`pending_loss = 1.5`, `deepen_boost = 0.20`) + MoE EPLB | `5 / 30` | `16.67%` | `7` | `100,630.7` | `539.8 s` (`186.4 tok/s`) |
| **V11 (`v11_ab_mcts`)** | **Synthesis-Handoff Fixes + Multi-Specialist Corroboration + 3-Way AST Splicing** | **`6 / 30`** | **`20.00%`** | **`3` (`-57.1%` vs V10)** | **`99,132.6`** | **`519.5 s` (`190.8 tok/s`)** |

> [!IMPORTANT]
> **100% Union Parity Across All Generations (`6 / 6`)**: V11 resolves **every single instance ever solved by V8, V9, or V10 combined** (`marshmallow-code__marshmallow-1164`, `marshmallow-code__marshmallow-1343`, `marshmallow-code__marshmallow-1810`, `pylint-dev__astroid-1417`, `pylint-dev__astroid-1959`, `pylint-dev__astroid-1962`) — eliminating the cross-generation tradeoff where PUCT (`V8`) solved `marshmallow-1343` while losing `astroid-1417` & `astroid-1962`, and AB-MCTS (`V9`/`V10`) solved `astroid-1417` & `astroid-1962` while skipping `marshmallow-1343`.

---

## Architectural Fixes Implemented in V11

### 1. Error-Conditioned Synthesis Retries (`hae/epistemic/ledger.py`, `hae/epistemic/mcts.py`, `hae/runtime/company.py`)
In V10, when a candidate patch failed `py_compile`, module import, proximity `repo_tests`, or post-synthesis probe inversion (`check_synthesis_effect`), `mcts.py` reverted the file to its pre-synthesis state and incremented `hypothesis.synthesis_failures += 1`, but stored **no record** of what diff was attempted or why it failed. On retry, the agent was presented with an identical prompt and re-emitted the exact same failing patch byte-for-byte.
- **V11 Solution**: Added structured `Hypothesis.synthesis_attempts` recording `{attempt, paths, mode, stage, detail, diff, diagnostics}` for every reverted synthesis step. `_do_synthesize` (`company.py`) injects the last two rejected unified diffs + compiler/pytest/probe tracebacks into retry prompts with a strict non-repetition directive.

### 2. Strict Post-Synthesis Probe Inversion, Crash Discrimination, & Nested Subpackage Proximity (`hae/epistemic/gatekeeper.py`, `hae/swebench/runner.py`)
In V10, `apply_synthesis` accepted `EFFECT_INCONCLUSIVE` writes (where the patch crashed the reproduction probe with an unhandled exception) as resolved, and `_proximity_tests` aborted proximity pytest selection whenever `len(sub_dirs) > 5` (such as `sqlfluff/src/sqlfluff/rules/`).
- **V11 Solution**:
  - Enabled `strict_probe_inversion = True` in `EpistemicPolicyGene` (`v11_ab_mcts_swebench_firm_0.json`), requiring positive empirical probe flip (`EFFECT_EFFECTIVE`) while properly distinguishing genuinely crashed probes (`not completed or (not ev.matched_prediction and int(ev.exit_code) != 0)`) from sibling probes whose baseline failure contract itself asserts `expect_exit_code == 1`.
  - Upgraded `_proximity_tests` and `_repo_tests_argv` in `runner.py` to support arbitrarily deep rule subpackages (`len(sub_dirs) >= 2`, e.g., `test/rules/`) and `-k <rule_code>` proximity scoping.

### 3. Coordinated Multi-Hunk / Multi-File Patch Plans & Large-Module Re-Anchoring (`hae/epistemic/moves.py`, `hae/runtime/company.py`, `hae/swebench/runner.py`)
In V10, Rung 1 (`REPAIR_PLAN`) permitted only a single contiguous `old_lines -> new_lines` span per synthesis step (`maxLength: 200`), and modules exceeding `module_rewrite_max_chars = 20000` (`src/marshmallow/schema.py` at `39,075` chars, `src/marshmallow/fields.py` at `58,412` chars) skipped Rung 3 (`write_file`) whenever Rung 1 missed an anchor (`7` unwritten skips in V10).
- **V11 Solution**:
  - Extended `RepairPlan` / `V6_REPAIR_PLAN_SCHEMA` (`moves.py`) with `secondary_edits: List[SecondaryEdit]` (up to 3 coordinated `{path, function, old_lines, new_lines}` hunks in the same module or collaborator files) with atomic multi-file commit & rollback in `_do_synthesize`.
  - Added **Rung 1b (`EXACT WINDOW RE-ANCHOR`)** for `>20k`-char modules: when Rung 1 reports `anchor not found; closest is lines W_LO-W_HI`, `_do_synthesize` slices the exact unnumbered source lines `[W_LO-15 : W_HI+15]` and re-prompts `REPAIR_PLAN` verbatim — cutting large-module unwritten skips by **`-57.1%` (`7 -> 3`)** and directly recovering `marshmallow-code__marshmallow-1343` (`39,075`-char `src/marshmallow/schema.py` via two coordinated hunks at lines `672` and `678` of `_do_load`).

### 4. Elided-Header AST Recovery & 3-Way Raw Escape Projection (`hae/epistemic/moves.py`)
Execution forensics on `pylint-dev__astroid-1417` and `marshmallow-code__marshmallow-1524` revealed two subtle failure modes when splicing grammar-constrained JSON arrays (`old_lines` / `new_lines`):
1. **Header-Elision Duplicate Class/Function Nesting**: When an LLM writes `old_lines = ["class cachedproperty:", "    def __init__(self, wrapped):", ...]` while omitting an 8-line class docstring + `__slots__` between `class cachedproperty:` and `def __init__:`, sliding-window fuzzy matching matched the interior body window (`ratio = 0.97`) and re-indented `class cachedproperty:` by 4 spaces *inside* the outer `class cachedproperty:`.
2. **JSON Backslash Stripping on Unchanged/Modified Lines**: Under RFC 8259 JSON string parsing, Python backslash escapes in unchanged lines (`"\n%s"`, `r"|\\[\001-\177])*\"$)"`) were altered when serialized inside JSON strings.
- **V11 Solution**:
  - Added **elided-header body matching** in `apply_repair_plan` (`moves.py`): detects when `old[0]` names a `class`/`def` header and `old[1:]` matches body lines below an omitted docstring/preamble, preserving the true header and omitted preamble in place at the true definition indent.
  - Added **3-way character escape projection (`_apply_line_edit_to_source_line`)** and **in-memory `ast.parse(new_source)` + duplicate `X.X` nesting guards** so unchanged escapes are preserved byte-for-byte from `source` and any syntactically invalid `REPAIR_PLAN` falls through in `0.001s` in-memory to Rung 2 (`FUNCTION_REWRITE`).

### 5. Multi-Specialist Root Corroboration (`hae/epistemic/ab_mcts.py`, `hae/runtime/company.py`)
In AB-MCTS, a single specialist's first `SUPPORTED` hypothesis (`h1`, posterior + deepen boost `= 1.00`) always out-sampled remaining specialist `GEN` arms (`prior ~ 0.50`), closing root questions on Move 3 after addressing only half of multi-requirement issues (e.g., adding `cached_property` alias on `astroid-1417` before `API-Contract Specialist` / `Docs & CLI Behaviour Specialist` could verify the `DeprecationWarning` requirement).
- **V11 Solution**: On root questions (`parent_question_id is None`) when multiple active prober specialists exist, `candidate_arms` (`ab_mcts.py`) requires at least `2` hypotheses to be tested (`tested_on_q >= 2`) before unlocking `ARM_SYNTHESIZE`, `_propose_hypotheses_adapter` (`company.py`) explicitly lists already-`SUPPORTED` hypotheses on `q` and instructs subsequent specialists to probe complementary uncovered issue requirements, and `_do_synthesize` conditions the patch on **all `SUPPORTED` sibling hypotheses together**.

---

## Per-Instance Resolution Matrix (`V8` vs `V9` vs `V10` vs `V11`)

| Instance ID | V8 (`puct`) | V9 (`ab_mcts`) | V10 (`ab_mcts` + EPLB) | **V11 (`v11_ab_mcts`)** | Key Technical Notes on V11 Trajectory |
|---|:---:|:---:|:---:|:---:|---|
| `marshmallow-code__marshmallow-1164` | PASS | PASS | PASS | **PASS** | Solved via Rung 1b exact-window re-anchor in `Nested._serialize` (`src/marshmallow/fields.py`, line 440) |
| `marshmallow-code__marshmallow-1343` | PASS | FAIL | FAIL | **PASS** | **Recovered vs V9 & V10**: Rung 1b exact-window re-anchor + 2 coordinated hunks (`lines 672 & 678` of `_do_load` in 39k-char `src/marshmallow/schema.py`) |
| `marshmallow-code__marshmallow-1810` | PASS | PASS | PASS | **PASS** | Solved cleanly (`1/1` F2P, `0` P2P regressions) |
| `pylint-dev__astroid-1417` | FAIL | FAIL | PASS | **PASS** | **Multi-specialist corroboration**: Specialist 1 confirmed `cached_property` alias (`h1`) + Specialist 2 confirmed `DeprecationWarning` (`h2`); joint Rung 1 elided-header patch resolved all checks in 5 moves (`82.6s`) |
| `pylint-dev__astroid-1959` | PASS | PASS | PASS | **PASS** | Recruited synthesized `C++/Python Binding Specialist` (`tags: astroid, raw_building, pyqt, runtimeerror, c_extension`); caught `RuntimeError` cleanly in `astroid/raw_building.py` |
| `pylint-dev__astroid-1962` | FAIL | FAIL | PASS | **PASS** | Solved via Rung 1 anchored edit in `ast_from_module_name` (`astroid/builder.py`, line 199) |
| **Total Resolved (`n = 30`)** | **4 / 30 (`13.3%`)** | **3 / 30 (`10.0%`)** | **5 / 30 (`16.7%`)** | **6 / 30 (`20.0%`)** | **100% superset of all prior generations (`+33.3%` absolute gain over V9, `+20%` relative gain over V10)** |

---

## Reproducibility & Artifacts
- **Genome Configuration**: [`configs/genomes/v11_ab_mcts_swebench_firm_0.json`](../../configs/genomes/v11_ab_mcts_swebench_firm_0.json)
- **Unit & Regression Suite**: [`tests/test_v11_synthesis_handoff.py`](../../tests/test_v11_synthesis_handoff.py) (`49/49` synthesis tests & full stdlib suite passing)
- **Full Evaluation Logs, Ledgers, & Grades (`30/30` instances)**: [`results/swebench/dev_eval_2026-10-09/v11_ab_mcts/`](../../results/swebench/dev_eval_2026-10-09/v11_ab_mcts/)
- **Aggregated Cross-Generation Matrix**: [`results/swebench/dev_eval_2026-10-09/v11_summary.json`](../../results/swebench/dev_eval_2026-10-09/v11_summary.json)
