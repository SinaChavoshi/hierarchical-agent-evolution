# V9 AB-MCTS confirmation run with synthesis-target resolution (`0220dd7`), 2026-10-09

End-to-end confirmation run of `configs/genomes/v9_ab_mcts_swebench_firm_0.json` on
`marshmallow-code__marshmallow-1810` at commit `0220dd7` (combining the Defect 3 gate fix
from `5437469` with `SweBenchCompanyRunner._synthesis_target` and `focus_lines_for(...,
extra_text=...)` from `0220dd7`).

## Summary

| metric | value |
|---|---|
| commit | `0220dd7` |
| policy | `ab_mcts` (`configs/genomes/v9_ab_mcts_swebench_firm_0.json`) |
| moves | 30 (10 in iteration 1 + 20 in iteration 2) |
| stop | `resolved` (`resolved_by_self_oracle = true`) |
| wall time | 3.9 min (6 ready `qwen38-flash-next-180b` replicas) |
| patch | 692 chars, `src/marshmallow/fields.py` only (`Field.root`) |
| harness grade (`grade.json`) | **resolved**: `FAIL_TO_PASS` 1/1, `PASS_TO_PASS` 111/111 (`FAILED: 0, ERROR: 0, MISSING: 0`) |
| whole suite (`pytest tests/`) | **1073 / 1073 passed** in 1.26 s (zero regressions) |

## Key trajectory events

1. **Preflight whole-suite calibration**:
   `repo_tests=whole_suite (tests/ in 2.26s, 0 baseline failure(s), gate timeout 60.0s; 1073 passed, 1 warning in 1.26s)`.
2. **Cosmetic & regressive rewrites on `q2/h5` (`src/marshmallow/schema.py`) caught and reverted**:
   - Move 13: `Schema._bind_field` function rewrite passed `verify_module` (including the 1073-test suite), then **reverted by `check_synthesis_effect`** as `ineffective (reverted) (dU=0.000, ab_y=0.000)`.
   - Move 14: second `Schema._bind_field` function rewrite **reverted by `verify_module`** (`whole suite: 9 new failure(s) not present on the clean checkout: tests/test_schema.py`, `dU=0.000, ab_y=0.000`).
3. **CEO `gen_specialist` arm triggered twice under Thompson Sampling**:
   - Move 19 (`q1`): `API-Contract Specialist [library]` recruited after active roles' `gen_hypothesis` posteriors decayed.
   - Move 29 (`q3`): `Marshmallow Schema Internals Specialist [synthesized]` invented by the CEO after library candidates had no tag overlap.
4. **Cross-module synthesis on `q1/h8` via `_synthesis_target`**:
   - Move 24 (`q1/h8`): `Issue Reproducer` proposed and confirmed a reproduction probe on `q1` (whose `q1.module` was `src/marshmallow/schema.py`) locating the bug in `marshmallow/fields.py` (`Field.root`, lines 411-420).
   - Move 25 (`synthesize q1/h8`): `_synthesis_target` resolved `q1/h8` to `src/marshmallow/fields.py` (`target resolved from hypothesis to src/marshmallow/fields.py (question module was src/marshmallow/schema.py)`), `focus_lines_for(..., extra_text=...)` included lines 411-420 (`def root`) in `module_view`, and `Data-Model & ORM Specialist` rewrote `Field.root` (lines 411-420, 10 -> 6 lines).
   - Gate verdict: `module check passed: py_compile, import, static_check, repo_tests; effect: effective (dU=0.030, ab_y=0.530)`.
   - Self-oracle at end of iteration 2: `resolved_by_self_oracle = true`.
