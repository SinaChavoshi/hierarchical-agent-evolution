# V9 AB-MCTS live validation, 2026-10-08

First live run of the V9 step policy (`epistemic_policy.search_algorithm = "ab_mcts"`,
[docs/v9/ab_mcts.md](../../../docs/v9/ab_mcts.md)) on a SWE-bench instance, against the
same self-hosted model, container and grader as the V8 smoke run, plus a V8 (`puct`) rerun
on the same code so the two step policies can be compared like for like.

One instance, one seed per policy. Nothing here is a rate. The run found two defects in
code shared by both policies (fixed in the same commit as this README) and one in the V9
prompt; the comparison section says what remains after accounting for them.

## Setup

| item | value |
|---|---|
| instance | `marshmallow-code__marshmallow-1810` (dev split); passes the validity screen: gold resolves, the empty patch does not (controls in `../smoke_2026-10-08/marshmallow-code__marshmallow-1810/grade_gold.json`, `grade_none.json`) |
| code | `25a94ed` for both the V9 run and the V8 rerun: V9 controller, organisation wiring (`8634aeb`) and the 20,000-char module-rewrite cap (`3167904`). The original V8 smoke run had neither of the last two. The fixes described below landed after both runs |
| model | `nvidia/Qwen3.8-Flash-Next-NVFP4` on vLLM, cluster `chavoshi-v6-cohort` (`gemle-gke-dev`, us-central1-b), service `vllm-qwen38-gateway:8000`, 2 ready replicas of 6; thinking off, `VLLM_MAX_TOKENS=8192` |
| executor | `--executor kubectl`, pod `swebench-testbed-marshmallow-1810`, container `testbed`, probes run with `/opt/miniconda3/envs/testbed/bin/python` in `/testbed`; checkout reset between runs |
| genomes | V9: `configs/genomes/v9_ab_mcts_swebench_firm_0.json`; V8: `configs/genomes/v8_smoke_swebench_firm_0.json`. They differ only in `search_algorithm` / `ab_prior_strength` (and ids) |
| budget | `--budget-moves 30 --max-iterations 2` |
| grading | `scripts/grade_in_testbed.py` (reset, apply patch, apply hidden `test_patch`, `pytest -rA` on the touched test files, FAIL_TO_PASS / PASS_TO_PASS by name). In addition each patch was applied alone and the repository's whole suite (`pytest tests`, 1073 tests at the base commit) was run, which the harness does not do |

Run command (V9; the V8 rerun swaps the genome, out-dir and model name):

    LLM_PROVIDER=vllm V5_TYPESAFE_PROTOCOL=1 VLLM_BASE_URL=http://127.0.0.1:8000/v1 \
    VLLM_MODEL=nvidia/Qwen3.8-Flash-Next-NVFP4 VLLM_MAX_TOKENS=8192 HAE_REQUIRE_NETWORK_ISOLATION=0 PYTHONPATH=. \
    python3 -m hae.cli --mode swebench --instance-id marshmallow-code__marshmallow-1810 \
        --dataset data/swebench/swebench_dev.jsonl \
        --executor kubectl --pod swebench-testbed-marshmallow-1810 --container testbed \
        --genome configs/genomes/v9_ab_mcts_swebench_firm_0.json \
        --out-dir results/swebench/v9_ab_mcts_2026-10-08/marshmallow-code__marshmallow-1810 \
        --budget-moves 30 --max-iterations 2 --model-name hae-v9-ab/nvidia-Qwen3.8-Flash-Next-NVFP4

## Results

| run | policy | code | moves | iterations | time | tokens | patch | stop | harness grade | whole suite after patch |
|---|---|---|---|---|---|---|---|---|---|---|
| `../smoke_2026-10-08` (reference) | puct | before `8634aeb`, no rewrite cap, org not wired | 30 | 2 | 53.4 min | 341,572 | 2,164 chars, `fields.py` + `schema.py` | max_iterations | resolved, F2P 1/1, P2P 111/111 | 1073 / 1073 |
| `../v8_puct_rerun_2026-10-08` (like for like) | puct | `25a94ed` | 21 | 2 (19 + 2) | 7.6 min | 89,271 | 4,112 chars, `fields.py` + `schema.py` | max_iterations; both iterations ended `exhausted`, 9 moves unused | **not resolved**: pytest cannot load `tests/conftest.py` (F2P 0/1, 111 P2P missing) | cannot start (exit 4) |
| same, `fields.py` hunk only (counterfactual, see below) | | | | | | | 919 chars | | resolved, F2P 1/1, P2P 111/111 (`grade_fields_hunk_only.json`) | 1073 / 1073 |
| this directory | ab_mcts | `25a94ed` | 24 | 1 | 10.2 min | 146,294 | 3,400 chars, `fields.py` + `schema.py` | all_resolved, 6 moves unused | resolved, F2P 1/1, P2P 111/111 | **1072 / 1073** (see below) |

`PYTHONPATH=. python3 scripts/summarize_swebench_run.py <run dir>` reproduces the counts;
`record.json` holds the full `stats.ab_mcts` block for V9 (per-step draws for every arm,
final posteriors per question).

### What the V9 search did

Turn-0 sizing picked the same four library roles in both current-code runs (`Data-Model &
ORM`, `Docs & CLI Behaviour`, `Issue Reproducer`, `Traceback Localiser`, v_org 0.519). The
ledger had three open questions: q1 (root failure, `schema.py`), q2 and q3.

Step counts: 24 steps = 11 `gen_hypothesis` + 8 `experiment` + 4 `synthesize` + 1
`gen_specialist`. 8 hypotheses accepted (1 duplicate, 2 tabu); 3 supported, 3 falsified,
2 probes refused as invalid Python (never repaired, see defects). 3 of 4 syntheses kept, 1
wrote nothing. No question hit the GEN-exhaustion limit; `gen_empty_draws` 3.

- q3, width 1 (steps 10-12): GEN(`Docs & CLI`) -> h4 SUPPORTED (dU 0.75) -> synthesize h4:
  `Field.root` rewritten and kept. Three moves, the fast path the unit tests pin.
- q1 (steps 1-7): GEN -> h1 refused -> GEN -> h2 SUPPORTED (dU 0.95) -> synthesize h2 wrote
  nothing (the 49 KB `schema.py` is above the rewrite cap and the plan's anchors were not
  found; failure 1/2) -> GEN returned a duplicate -> synthesize h2 again: `Schema._bind_field`
  function rewrite, gate passed, dU 0.03. The synthesize arm kept winning (theta 0.99, 0.81)
  because it starts from the supported hypothesis's posterior and one y=0 only partly lowers it.
- q2, the hard question (steps 8-9, 13-24): h3 refused; h5, h6, h7 (from `Traceback
  Localiser` x2 and `Data-Model`) FALSIFIED in a row with dU 0.21, 0.06, 0.03. The three
  roles' GEN posterior means fell to 0.30-0.37 and at step 19 the CEO arm (mean 0.388, from
  `recruit_prior` 0.332: stall 1, three uncovered modules) won the draw: `API-Contract
  Specialist` recruited from the library. Then GEN(`Docs & CLI`) tabu, GEN(`Issue
  Reproducer`) h8 accepted at prior 0.10, GEN(`API-Contract`) tabu, experiment h8 SUPPORTED
  (dU 0.28), synthesize h8: `Schema._invoke_field_validators` rewrite, gate passed, dU 0.20.
  All three questions resolved; the loop stopped at move 24 and no second iteration ran.

Role credit (`stats.delta_u_by_role`): `Traceback Localiser` 1.19, `Docs & CLI` 0.98,
`Issue Reproducer` 0.28, `Data-Model` 0.09, `API-Contract` (the recruit) 0.00; its only
proposal was tabu.

### What the V8 rerun did

7 proposals (k=3 each; the model returned 1, 3, 3, 3, 3, 3, 3), all routed by UCB to
`Data-Model & ORM Specialist` (the two recruits never proposed), 10 experiments (3
supported, 6 falsified, 1 refused), 2 recruits (`API-Contract Specialist` from the library at
move 3 on `unmatched=3`; a synthesised `Marshmallow Field Internals Specialist` at move 8 on
`stall=2`), 2 syntheses, both kept by the gate:

- move 12, h5 on q3: `Field.root` rewritten to stop at the first `SchemaABC`, walking
  `_parent` or `parent`. This is a correct fix.
- move 19, h10 on q2: `Schema.__init__` rewritten from 40 to 25 lines "to initialise
  `root`". It drops the `only`/`exclude` validation, the `opts` lookups and the error-message
  merge, and reads `self.Meta.mro()[-2].ordered`, which raises `AttributeError` for every
  schema. The gate reported `module check passed`; see defect 2.

Iteration 1 then stopped `exhausted` at move 19: every question had used its
`max_hypothesis_rounds = 2`, the waterfall's cap. The self-oracle was `inconclusive` (no
reproduction probe to rerun). Iteration 2 re-sized the team (the synthesised specialist
replaced `Docs & CLI`), proposed twice on q1, got 3 + 3 tabu rejections and stopped
`exhausted` after 2 moves with 9 of 30 unused.

### The V9 patch

Three kept syntheses, one of substance:

1. `Field.root` (h4, q3): the walk up `.parent` now returns the first `SchemaABC` it meets
   instead of walking to the top and testing there. This is the fix; same mechanism as both
   V8 patches, different from gold (`root = None` on `FieldABC`, set in `_bind_to_schema`).
   The property docstring was deleted.
2. `Schema._bind_field` (h2, q1): no behavioural change relevant to the issue. Docstring and
   a comment deleted, the `TypeError` message reworded, `raise error` -> `raise`.
3. `Schema._invoke_field_validators` (h8, q2): no behavioural change relevant to the issue.
   A subscript reformatted and the `ValueError` text changed from `'"{}" field does not
   exist.'` to `'Field {!r} does not exist.'`.

Change 3 breaks `tests/test_decorators.py::TestValidatesDecorator::test_field_not_present`,
which matches the old message. The harness does not see it: it grades the touched test
files only (`tests/test_fields.py`, 112 tests), so the instance counts as resolved under
SWE-bench rules. On the whole suite the V9 patch scores 1072/1073 against 1073/1073 for the
base commit and for the V8 smoke patch. The module gate did not catch it either: its
`repo_tests` step runs tests near the module by name (`tests/test_schema.py` for
`schema.py`), not `test_decorators.py`.

Two of the three kept syntheses were therefore churn: the hypotheses were "supported" by
probes that confirmed facts about the code, the plan for them was not applicable, and the
fallback function rewrite re-emitted the function with cosmetic edits that the gate cannot
distinguish from a fix. V8 produced the same kind of churn on this instance (docstring
deletions and a `delattr` loop in the smoke run; the `Schema.__init__` rewrite in the rerun).

## Defects found by these runs

1. **Refused probes were dead under AB-MCTS** (V9 only). h1 and h3 (priors 0.85) were
   rejected as invalid Python and never retried. The V8 path repairs a refusal when a later
   proposal restates the mechanism with a working probe (3 of 3 repaired in the smoke run),
   but the k=1 prompt's rule 2 said the mechanism must differ from every listed hypothesis
   "tested or untested", which forbids exactly that. Fixed: the k=1 prompt now names the
   repair exception (`hae/runtime/company.py`);
   `tests/test_ab_mcts.py::FastPathTests::test_a_refused_probe_is_repaired_by_restating_the_mechanism`
   runs refusal -> restatement -> repair -> support -> patch under `ab_mcts`. The k>=2
   prompt is unchanged and the PUCT identity fixture still passes.
2. **A write that broke pytest start-up passed the module gate** (both policies). The
   `repo_tests` step treated pytest exit 3/4 as "inconclusive, step skipped", which is right
   when the environment is at fault but not when the traceback runs through the module that
   was just written (`src/marshmallow/schema.py:372: in __init__`). Fixed:
   `EvidenceGatekeeper._run_repo_tests` now fails the step in that case and keeps the
   inconclusive path otherwise (`tests/test_swebench_gatekeeper.py::ModuleCheckTests::test_startup_failure_caused_by_the_module_fails_the_check`
   and `..._elsewhere_stays_inconclusive`; both need pytest in the test interpreter). With
   this, the V8 rerun's move 19 would have been reverted and its patch would have been the
   `fields.py` hunk alone, which grades resolved and passes the whole suite.
3. **Cosmetic function rewrites pass the gate and can regress tests outside the graded
   files** (both policies). Not fixed. Candidates: run the whole suite once before the patch
   is exported when it is cheap (1.3 s for marshmallow; the preflight could time it), and
   fail or revert a synthesis whose diff changes only strings, docstrings and comments.

## Comparison

On this instance, with the same code and the same four-role team, the two step policies
differed in the way the design predicts:

- PUCT stopped `exhausted` at move 19 because each question had spent its two proposal
  rounds, left 11 moves on the table, and iteration 2 could not propose anything that was not
  tabu. AB-MCTS has no round cap: on q2 it kept drawing GEN arms through three
  falsifications, let the CEO arm win when the role arms had decayed, and stopped on
  `all_resolved` at move 24.
- Neither policy was limited by moves. Both found the `Field.root` fix early (V8 at move
  12, V9 at move 12) and both spent the rest of the budget on syntheses for other questions
  that were cosmetic at best and destructive at worst. The graded outcome of each run was
  decided by what the gate let through afterwards, not by the step policy.
- Proposer diversity differed: PUCT's UCB routing sent all 7 proposal rounds to one role,
  AB-MCTS's per-role GEN arms spread 11 proposals over all 5 roles (4, 2, 2, 2, 1). Whether
  that matters for resolution is not something one instance can say.
- Cost: 25 s and 6.1k tokens per move for V9 against 22 s and 4.3k for V8; the k=1 proposer
  is called more often (11 against 7) and carries the probe-outcome block. The original
  smoke run's 53 minutes were two 8,192-token whole-module rewrites that the current cap
  skips, not the policy.

Nothing here separates the policies on resolution rate, and nothing can on one instance.
The next measurement is the 50-task dev slice with both policies on the same instances and
the fixed gate.

## Caveats

- One instance, one seed per policy. Thompson sampling is stochastic; another seed gives
  another trajectory.
- The harness-style grade is the SWE-bench criterion; the whole-suite number is extra
  information, reported because it changes the reading of this instance.
- The grader approximates the official harness (plain `pytest -rA` on the touched test
  files, no repo-specific install or test command).
- 2 of 6 vLLM replicas were ready; wall time depends on that and on the length of the
  model's responses, not only on the number of moves.
