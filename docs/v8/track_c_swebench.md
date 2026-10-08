# Track C — SWE-bench adapters (v8)

Status: merged on `main`. First live run on 2026-10-08 (`KubectlExecutor`
against a SWE-bench instance image on GKE, self-hosted Qwen3.8 via vLLM); see
`results/swebench/smoke_2026-10-08/`. The Docker path is verified only through
argv construction and the upstream docs cited at the end.

## 1. Executor boundary

The firm never touches a task repository directly. `hae/swebench/executor.py`
defines `CommandExecutor` (`run`, `read_file`, `write_file`, `list_files`,
`diff`, `changed_files`) with `LocalExecutor` (a directory; used by tests),
`DockerExecutor` (`docker exec -w /testbed <container>`) and `KubectlExecutor`
(`kubectl exec`). Why: the prebuilt instance images carry the exact Python env
(`/opt/miniconda3/envs/testbed/bin/python`), the firm's image has no project
deps, and the prediction must be a diff of *that* checkout — `export_prediction`
is `git diff` after `git add -N`, excluding `.hae/` scratch and bytecode.
Timeouts run inside the container (`timeout -k 5 <n>` when present) plus a
host-side grace.

## 2. Gatekeeper in executor mode (`hae/epistemic/gatekeeper.py`)

`EvidenceGatekeeper(executor=..., test_command=None)`; with `executor=None` the
class is byte-for-byte v7. In executor mode a probe is written to
`.hae/probe_<id>.py` and run in the repo; `verify_module` runs in-memory
compile → import → static check (new findings vs `git show HEAD:`) →
*proximity tests*: at most 3 tracked `test_*.py`/`*_test.py`/`tests.py` files
under a `tests/`, `test/` or `testing/` directory whose name contains the edited
module's stem, shortest first. It never reads `FAIL_TO_PASS`/`PASS_TO_PASS`;
`tests/test_swebench_dataset.py::LeakageTests` AST-scans
runner/task/export/gatekeeper/executor for those names. Repos without pytest
(django) get "skipped-pass" — pass `--test-command` for those.

## 3. Grading information

`SweTask` (instance_id, repo, base_commit, version, problem_statement,
created_at, environment_setup_commit; hints_text="" unless `--include-hints`)
is all the firm sees. `GradingInfo` (patch, test_patch, FAIL_TO_PASS,
PASS_TO_PASS) is loaded only by `load_grading_info`, which no runtime module
imports. Hidden tests run only in the upstream harness on `preds.jsonl`.

## 4. Issue-seeded search (`hae/swebench/task.py`)

One root question "Reproduce the reported behaviour: …" (uncertainty 1.0, key
`swebench:<instance_id>`, module = best path in the statement, else "") plus
≤3 sub-questions for paths the issue names (uncertainty 0.8, empty
`source_failure` so each is its own frontier cluster). `reconcile_with_oracle`
is never called. 134/225 dev statements name no path, so the runner has a
LOCATE step (one JSON call) before proposing.

## 5. Self-oracle — definition and weakness

After each iteration that changed files, `reconcile_with_reproduction` re-runs
the root question's SUPPORTED/CERTIFIED probes unchanged (each matched its
prediction *of the buggy behaviour* before the patch).
`bug_no_longer_reproduces(h)` := re-run completed (no timeout/transport error)
**and** prediction no longer matches **and** exit 0. Any such probe ⇒ root
RESOLVED (floor uncertainty), run stops; a probe that now crashes proves
nothing. Otherwise questions resolved this iteration are reopened and their
`patch_applied` hypotheses falsified; no supported root probe ⇒ `inconclusive`.
Weakness: a probe can only disprove the behaviour it encoded — no regressions
seen, satisfiable by an unrelated change. A stopping rule, not a grade.
Non-fixing patches are still exported (an empty prediction scores 0 anyway).

## 6. V8 organisation on the SWE-bench path (`hae/swebench/runner.py`)

Until 2026-10-08 `run_swebench` built its `EpistemicSearchLoop` from the static
department bindings only; the first live run showed no `[org]` line and
`record.json` had no `org` key. The runner now calls
`_bind_dynamic_organization(..., features=task_features(task, paths))` once per
iteration when `ceo_policy.enabled`: the turn-0 team is sized against the
problem-statement triage (`TaskFeatures.from_problem_statement`, not oracle
failures, which SWE-bench does not have), `hypothesis`/`experiment`/`synthesis`
are re-bound through `OrgState.pick_role`, and the loop receives `org_state` and
the `MOVE_RECRUIT_SPECIALIST` adapter. Roles hired in iteration *n* are
candidates at iteration *n+1*'s turn 0. Output and `record.json` carry `org`,
`org_audit`, `org_history` (one entry per iteration, the Gen-16 shape) and each
`swebench.iterations[i].org` has `team`, `recruits`, `stall`, `unmatched`. With
the gene off nothing changes (`tests/test_swebench_runner.py::DynamicOrganizationTests`).
For SWE-bench genomes use `seed_role_library("swebench")`; the legacy seeds are
tagged for the Gen-16 corpus.

## 7. Running

Docker (VM) or kubectl (the testbed image as a pod on a CPU node pool; the
firm runs on the host and `kubectl exec`s into it — used for the first live run):

```sh
python3 scripts/fetch_swebench.py --check-images && python3 scripts/check_swebench_splits.py
IID=marshmallow-code__marshmallow-1810; IMG=swebench/sweb.eval.x86_64.marshmallow-code_1776_marshmallow-1810:latest
docker pull $IMG && docker run -d --name hae_$IID $IMG sleep infinity
python3 scripts/run_swebench_instance.py --instance-id $IID --dataset data/swebench/swebench_dev.jsonl \
    --executor docker --container hae_$IID --genome <firm.json> --out-dir results/swebench/<run>/$IID/
docker rm -f hae_$IID
python3 scripts/swebench_docker_batch.py --run-id <run> --genome <firm.json> --n 10 --workers 2  # resumable; writes preds.jsonl
swebench eval verified -p results/swebench/<run>/preds.jsonl --run-id <run>   # dev ids: point at the dev dataset
# kubectl variant: a Pod running `sleep infinity` on IMG, then
#   --executor kubectl --pod swebench-testbed-marshmallow-1810 --container testbed
```

## 8. Instance validity screen (do this before spending moves)

Two of the first three dev-slice instances were not gradeable in their
published `:latest` images (2026-10-08):

* `pvlib__pvlib-python-1395`: the testbed env resolved NumPy 2.0.2 and
  `import pvlib` fails in `pvlib/ivtools/sdm.py` (`np.Inf` was removed). Every
  probe is falsified by the same ImportError; the gold patch cannot pass either.
  `environment_preflight` (section 6 of the runner) now reports this on the
  seed line as `env_import=FAILED`.
* `pydicom__pydicom-938`: the testbed is Python 3.6.13 and the issue is a
  Python 3.8 `float`/`int` subclass `__str__`/`__repr__` change, so the
  FAIL_TO_PASS tests already pass at the base commit. A firm "resolves" it by
  doing nothing.

`scripts/grade_in_testbed.py` approximates the harness inside the live
testbed container: reset, apply patch, apply `test_patch`, `python -m pytest
-rA` on the test files named by FAIL_TO_PASS and PASS_TO_PASS, then match the
reported names. It runs files rather than node ids because SWE-bench stores
test names cut at the first whitespace (`test_make_error[required-Missing` for
a parametrised id containing spaces); such names are not valid node ids and
pytest exits 4 without running anything, which first showed up as 111 MISSING
PASS_TO_PASS tests on marshmallow-1810. Its two controls are the screen:
`--gold` must resolve and `--none` must *not*. Run both before an instance
counts, and keep the firm's prediction graded only on instances that pass the
screen. The script imports `load_grading_info` and therefore lives outside
`hae/`.

The third smoke instance, `marshmallow-code__marshmallow-1810`, passes the
screen (gold resolves, no-patch fails FAIL_TO_PASS 0/1) and the firm's patch
was graded resolved (FAIL_TO_PASS 1/1, PASS_TO_PASS 111/111) with a different
mechanism from the gold patch. Details and caveats, including that this run
predates the organisation wiring, are in
`results/swebench/smoke_2026-10-08/README.md`.

`python -m hae.cli --mode swebench …` dispatches to the same runner. Sources:
image rule `https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/swebench/image_builder/image_spec.py`;
CLI `https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/README.md`;
rows `https://datasets-server.huggingface.co/rows` (dev 225, Lite dev 23,
Verified 500; Docker Hub: 175 dev images published, 41 not, 9 unknown).
