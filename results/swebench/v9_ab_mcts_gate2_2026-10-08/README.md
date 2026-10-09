# V9 AB-MCTS confirmation run after the Defect 3 gate fix, 2026-10-08

Confirmation run of `configs/genomes/v9_ab_mcts_swebench_firm_0.json` on
`marshmallow-code__marshmallow-1810` at commit `5437469` (with
`EvidenceGatekeeper.check_synthesis_effect` and `calibrate_whole_suite`), against the same
self-hosted vLLM (`nvidia/Qwen3.8-Flash-Next-NVFP4`, 4 ready replicas of `qwen38-flash-next-180b`)
and testbed pod (`swebench-testbed-marshmallow-1810`) as
[`../v9_ab_mcts_2026-10-08/`](../v9_ab_mcts_2026-10-08/README.md).

## What this run confirmed

1. **Whole-suite calibration engaged automatically at preflight**:
   `repo_tests=whole_suite (tests/ in 2.21s, 0 baseline failure(s), gate timeout 60.0s; 1073 passed in 1.25s)`.
   Every subsequent `verify_module` ran the full 1073-test suite rather than the 3-file
   proximity selection.
2. **Cosmetic function rewrites were caught by `check_synthesis_effect` and reverted**:
   In iteration 2, moves 13 and 14 (`synthesize q2/h5` on `src/marshmallow/schema.py`,
   function rewrites of `Schema._bind_field`) passed `verify_module` (including the 1073-test
   suite) and were then rejected by `check_synthesis_effect` as
   `ineffective (0/1 re-run probe(s) no longer reproduce the bug; 1 still match the buggy prediction, 0 already stopped before this write)`.
   Both writes were reverted (`dU=0.000`, `ab_y=0.000`, `syntheses_ineffective=2`), leaving
   `q2` open and keeping cosmetic churn out of `model_patch`.
3. **CEO `gen_specialist` arm triggered twice under Thompson Sampling**:
   At move 19 (`API-Contract Specialist [library]`) and move 27 (`Marshmallow Internal State
   Specialist [synthesized]`) after active roles' `gen_hypothesis` posteriors decayed on
   repeated falsifications and probe refusals.

## Defect exposed by this run (fixed in the commit accompanying this README)

At move 24 (`q1/h8`), `Traceback Localiser` proposed and confirmed a reproduction probe on
the root question `q1` whose claim and mechanism explicitly located the defect in
`marshmallow/fields.py` (`Field.root`, lines 411-420). Moves 25 and 26 then drew
`("synthesize", "h8")` on `q1`. Because `seed_from_problem_statement` sets `q1.module` to the
top path in the issue (`src/marshmallow/schema.py`, 49 KB, which has two traceback frames),
`_synthesize_patch_adapter` hardcoded `target = question.module` (`src/marshmallow/schema.py`).
The synthesiser emitted a `RepairPlan` for `Field.root` (`fields.py`) that could not match
`schema.py` (`anchor not found; closest is lines 967-971 (ratio 0.59)`), and rung 3 was
skipped by the 20,000-char cap. At move 30 (`q3/h9`, whose module is `src/marshmallow/fields.py`),
`Field.root` was supported again, right as the 30-move budget expired (`stop=budget_moves`,
empty `model_patch`).

### Fix (`hae/swebench/task.py`, `hae/swebench/runner.py`, `hae/runtime/company.py`)

- `SweBenchCompanyRunner._synthesis_target(question, hypothesis)` resolves `.py` paths named in
  `f"{hypothesis.claim}\n{hypothesis.mechanism}"` against tracked non-test `.py` files via
  `hypothesis_target_module` (`extract_paths` + `resolve_paths`, filtering `is_test_path`).
  When a hypothesis on `q1` (`schema.py`) locates the mechanism in `marshmallow/fields.py`,
  synthesis targets `src/marshmallow/fields.py` instead of `src/marshmallow/schema.py`.
- `focus_lines_for(source, statement, path, extra_text=...)` now receives the hypothesis's
  `claim`, `mechanism`, and `probe_code` as `extra_text`, extracting both `line N` / `lines N-M`
  references and named definitions (`def root`) so `module_view` on large modules (such as
  76 KB `src/marshmallow/fields.py`) includes the exact window the hypothesis points at.
