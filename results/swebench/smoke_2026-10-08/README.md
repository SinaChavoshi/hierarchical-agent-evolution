# V8 SWE-bench smoke run, 2026-10-08

First end-to-end run of the V8 firm on SWE-bench instances against the self-hosted model.
Three instances from the dev split, one firm each, graded in the same container the firm worked in.

## Setup

| item | value |
|---|---|
| model | `nvidia/Qwen3.8-Flash-Next-NVFP4` on vLLM, cluster `chavoshi-v6-cohort` (project `gemle-gke-dev`, us-central1-b), service `vllm-qwen38-gateway:8000`, 3 of 6 replicas running (the other 3 pending, no spot G4 capacity) |
| client | `LLM_PROVIDER=vllm`, `V5_TYPESAFE_PROTOCOL=1`, `VLLM_MAX_TOKENS=8192`, thinking disabled (`enable_thinking=False`); measured single-stream decode about 20 tok/s |
| executor | `--executor kubectl`: one bare pod per instance from `swebench/sweb.eval.x86_64.<owner>_1776_<repo>-<n>:latest`, container `testbed`, firm runs probes with `/opt/miniconda3/envs/testbed/bin/python` inside `/testbed` |
| genome | `configs/genomes/v8_smoke_swebench_firm_0.json` (firm 0 of `configs/generation_v8_smoke_population.json`, `swebench` role seed library, CEO policy on, recruit mode library+synthesize) |
| budget | `--budget-moves 30 --max-iterations 2` |
| grading | `scripts/grade_in_testbed.py` in the same pod: reset checkout, apply patch, apply hidden `test_patch`, `python -m pytest -rA` on the touched test files, compare FAIL_TO_PASS / PASS_TO_PASS. Each instance was also graded with the gold patch and with no patch as controls. |

Run command (per instance, foreground):

    python3 -m hae.cli --mode swebench --instance-id <id> --dataset data/swebench/swebench_dev.jsonl \
        --executor kubectl --pod swebench-testbed-<short> --container testbed \
        --genome configs/genomes/v8_smoke_swebench_firm_0.json \
        --out-dir results/swebench/smoke_2026-10-08/<id> --budget-moves 30 --max-iterations 2 \
        --model-name hae-v8-smoke/nvidia-Qwen3.8-Flash-Next-NVFP4

## Results

| instance | org wired | moves | time | tokens | patch | self-oracle | grade (firm) | grade (gold) | grade (none) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| `marshmallow-code__marshmallow-1810` | no (run started before the fix in `8634aeb`) | 30 | 53.4 min | 341,572 | 2,164 chars, `fields.py` + `schema.py` | still_reproduces | resolved, F2P 1/1, P2P 111/111 | resolved | not resolved (F2P 0/1) | **resolved** on a discriminating instance |
| `pydicom__pydicom-938` | yes | 17 | 4.5 min | 52,172 | 820 chars, `valuerep.py` | still_reproduces | resolved, F2P 4/4, P2P 33/33 | resolved | **resolved** (F2P 4/4) | non-discriminating in this image: the empty patch also passes, so no credit |
| `pvlib__pvlib-python-1395` | yes | 9 | 1.6 min | 22,928 | empty | n/a | not gradeable | not gradeable | not gradeable | broken image: `import pvlib` fails under NumPy 2.0.2 (`np.Inf` removed), pytest exits 4 at conftest import |

Score on this smoke set: 1 resolved out of 1 gradeable, discriminating instance. That is one data point, not a rate.

Per-instance files: `prediction.json` (SWE-bench prediction row), `record.json` (full run record incl. epistemic searches, org history, token accounting), `grade.json` (firm grade), `grade_gold.json` / `grade_none.json` (controls), `firm.log`, `stdout.log`.
`PYTHONPATH=. python3 scripts/summarize_swebench_run.py results/swebench/smoke_2026-10-08` reproduces the counts below.

### marshmallow-1810

Issue: a schema with a field named `parent` breaks `Field.root`, which walks `.parent` links until it finds a schema; the class attribute `parent` on the schema makes it walk past the schema and return `None`.

Firm's patch (2 hunks of substance plus quote-style churn in `SchemaMeta.__new__`):

- `Field.root`: loop condition `while hasattr(ret, "parent") and not isinstance(ret, SchemaABC)` so the walk stops at the first schema. This alone fixes the reported bug. The property docstring was deleted.
- `SchemaMeta.__new__`: `delattr(klass, field_name)` for every declared field after class creation, "to prevent shadowing". Not needed for the fix and riskier than the gold change; it passed the 111 PASS_TO_PASS tests in `tests/test_fields.py`, which is all the harness checks for this instance.

Gold patch instead adds `root = None` to `FieldABC`, sets `self.root` in `_bind_to_schema`, and removes the property. Different mechanism, same test outcome.

Search statistics: 7 hypothesis proposals, 12 experiments (6 supported, 3 falsified, 3 rejected probes, all 3 repaired by the probe-repair path), 11 synthesis moves (4 kept, 7 reverted). Iteration 1 used 21 moves and ended with every open question marked resolved; the post-patch recheck reran 2 probes, reported `still_reproduces` and reopened q1 to q3; iteration 2 spent the remaining 9 moves. Two rung-3 whole-module rewrites hit the 8,192-token ceiling (38 KB responses, 6.6 min each, both discarded as truncated); this is what made the run 53 minutes at 20 tok/s and motivated `MODULE_REWRITE_MAX_CHARS` (commit `3167904`), which was not yet active for this run.

Self-oracle versus grade: the oracle says `still_reproduces` because the probes it reruns check structural facts ("Field structure intact", exit 0) rather than the failing behaviour, so they match their predictions before and after the patch. The oracle is not a substitute for grading.

### pydicom-938

Turn-0 CEO sizing picked 4 of 12 library roles (`API-Contract Specialist`, `Issue Reproducer`, `Numerics & Symbolic Specialist`, `Test-Suite Analyst`, v_org 0.393). During iteration 1 the stall and uncovered-module triggers fired twice and the CEO synthesised two new roles with `MOVE_RECRUIT_SPECIALIST` (`Python 3.8 Pickle & Serialization Specialist`, reason `stall=0, unmatched=2`; `Python 3.8 Compatibility & Recursion Specialist`, reason `stall=3, unmatched=1`). Iteration 2 re-ran turn-0 over the genome library plus the two recruits and hired both recruits in place of `API-Contract` and `Numerics` (v_org 0.461), then recruited `Numerics & Symbolic Specialist` back from the library mid-run (`stall=1, unmatched=1`). 9 experiments (1 supported, 8 falsified), 1 synthesis kept: an early return in `DS()` for inputs that are already `DS` instances (docstring deleted).

The grade is not informative: the hidden tests pass without any patch in this image (testbed Python 3.6.13; the issue is Python 3.8 `float`/`int` subclass `__str__`/`__repr__` behaviour). Instances like this must be screened out before a batch run.

### pvlib-1395

Turn-0 picked 4 roles, no recruits were triggered, 5 experiments all falsified, no synthesis, empty patch. The package does not import in its own image (NumPy 2.0.2 in the testbed env; `pvlib/singlediode.py` uses `np.Inf`), so neither the firm nor the harness can run anything. `environment_preflight()` (commit `3167904`, after this run) now records this as `env_import=FAILED` on the seed line before any LLM call.

## Defects found by this run and fixed

1. `SweBenchCompanyRunner.run_swebench` did not bind the V8 organisation at all (no turn-0 sizing, no UCB routing, no recruit adapter, no `org` in the record). Fixed in `8634aeb`; pydicom and pvlib ran with the fix, marshmallow did not.
2. Rung-3 whole-module rewrites of large modules were truncated at the token ceiling after minutes of decode. `module_rewrite_max_chars` (20,000 on the SWE-bench runner) skips them. `3167904`.
3. No check that the package under test imports before spending LLM budget. `environment_preflight()`. `3167904`.
4. The grader ran pytest by node id. SWE-bench stores test names cut at the first whitespace, so parametrised ids with spaces (`test_make_error[required-Missing`) are not valid node ids; pytest exited 4 and every PASS_TO_PASS test was reported MISSING, which made the gold control fail. The grader now runs the touched files and matches names the way the harness does. Regression test in `tests/test_swebench_scripts.py::GraderTests`.

## Caveats

- The grader approximates the harness (no repo-specific install or test command, plain `pytest -rA` on the touched files). The official number comes from `swebench eval` on `predictions.jsonl`.
- `:latest` dev-split images are not reliable: 2 of 3 here were unusable for scoring. A validity screen (gold resolves and no-patch does not) has to precede any batch.
- One firm, one seed, 30 moves, 2 iterations. Nothing here supports a rate claim.
- The marshmallow run predates the org wiring; it is a V6-style single-team run on the SWE-bench adapters, not a V8 run.
