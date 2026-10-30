# V16: roles and CEO policy on the hierarchical harness

V15 evolved the harness code and knobs. The seed genome also carried a role library and a CEO
policy, but the hierarchical harness never used them. V16 connects them and tests whether a
staffed and evolving organization improves on the V15 champion.

This README fixes the protocol before any V16 run. Results will be added below it.

## Question

Does staffing the harness layers from a role library, with a CEO policy that hires and replaces
roles, resolve more held-out SWE-bench Verified tasks than the V15 champion? Does evolving the
roles and the policy help more than random search over them?

## What changed (code)

| Part | Change |
| :--- | :--- |
| Infra (Phase 0) | Pre-warmed single-use sandbox pods; interleaved (genome, task) job order; infra files synced into every candidate worktree; kubectl and LLM transport retries; llm-d router re-weighted (queue 3, KV 2, prefix 1). |
| Roles (Phase 1) | `RoleAllele` gains `layer`, `playbook`, `failure_modes`, `is_generalist`. The library was rewritten by the LLM from a redacted V15 train/val failure digest (`scripts/v16_refresh_roles.py`, report in [role_refresh_report.md](role_refresh_report.md)). It has 15 roles: 5 generalists and 10 specialists. |
| Binding (Phase 2) | `hae/swebench/org_binding.py` (not evolvable) attaches to the engine from outside. `org_mode` is `off`, `static` or `dynamic`. |
| Evolution (Phase 3) | `HarnessGenome` gains `org_mode`, `roles` and `ceo_policy`. New operators: role rewrite, role add, role remove, CEO-policy mutation and org crossover (`hae/evolution/org_evolution.py`). Role credits from training runs feed back into the library. |

### How the binding works

- All LLM calls of the hierarchical engine go through `_call_specialist_turn`. The binding wraps
  it and appends one "Organization assignment" block to the system message of a specialist's
  calls: title, focus, playbook and failure modes.
- A generalist adds no text, so with generalists staffed the prompts are byte-identical to V15
  (`tests/test_v16_org.py`).
- `static`: at task start the CEO staffs each layer once. A specialist is hired only if its tags
  overlap the issue text and its score beats the generalist's.
- `dynamic`: as `static`, plus two extra moves.
  - Per-branch executor choice: the branch locus is added to the tags.
  - Recruitment on stall: when ΔV < `stall_delta_u` for `stall_moves` episodes and the recruit
    prior fires, the stalled layer's role is swapped for the best untried role.
- Credit: each GEN or CONT episode credits its role with ΔV = V(node) − V(parent), where a
  GEN parent counts as 0.
- The falsifier is credited ±0.1 per run, by whether its final PASS agreed with the hidden tests.
- Each run's `record.json["swebench"]["org"]` holds the team, per-episode roles, swaps, credits
  and call counts.

### Deviations from the brief

- **Hooks:** the brief said to edit the prompt functions. Instead, the binding wraps the engine
  from outside, so the evolvable files are unchanged. This means the V15 champion patch applies
  as is, and evolved code cannot remove the org code.
- **Recon:** the V15 recon layer is deterministic and makes no LLM call, so recon roles are
  inert. The library keeps only the recon generalist.
- **Temperatures:** role temperatures are not applied. Sampling temperatures stay at the knob
  values, so the arms differ only in prompt text and staffing.
- **Pod pool:** pods are pre-warmed but used once each, not reused across jobs. An agent can
  change state outside `/testbed` (for example pip installs) that a git reset does not undo.

## Arms

All arms use the V15 champion `evo_g5_03` code patch and knobs, the same evaluator, budget and
grading, the V15 splits, and the same infra commit (recorded in each `run_meta.json`).

| Arm | Organization | Selection |
| :--- | :--- | :--- |
| A | `org_mode=off` (exactly V15) | fixed |
| B | refreshed library, `static` | fixed |
| C | refreshed library, `dynamic` | fixed |
| D | full evolution from C: code, knobs, roles, CEO policy. 6 generations, population 8, stage-1 halving as V15, `p_org=0.4` | best training fitness |
| E | random search from C: random knobs + random organization (each specialist kept with p=0.5, uniform CEO genes, random mode). Same schedule and budget as D. | best training fitness |
| V12 | flat ReAct agent (`configs/genomes/v12_agentic_swebench_firm_0.json`), budget 68 moves (the champion's effective floor) | fixed |

## Protocol

1. **Training (D, E):** both run concurrently, so they share the same cluster load. The top 3 of
   each by training fitness are promoted to validation.
2. **Validation (10 tasks):** A, B, C and the promoted D and E genomes, 2 runs each.
   - The best D genome and the best E genome by mean validation resolves (ties broken by
     training fitness) go to test.
3. **Test (28 unseen Verified tasks):** A, B, C, best D, best E and V12, 2 runs each, all
   interleaved in one pool.
4. **Official re-grade:** every test prediction is re-graded with the official SWE-bench
   evaluation script built inside the testbed pod. Both grades are reported.
5. **Infrastructure failures** are discarded and re-run, never scored.

## Metrics and tests

- **Primary:** test resolves (mean of 2 runs) per arm.
- **Paired comparison:** C vs A and D vs E, using per-task majority outcomes. Report a two-sided
  sign test on discordant tasks and a 95% bootstrap CI (10,000 resamples over tasks) of the
  resolve-rate difference.
- **Run-to-run agreement:** the fraction of tasks with the same outcome in both runs.
- **Organization metrics** (B, C, D, E):
  - specialist hire rate per layer;
  - swaps per run;
  - mean ΔV per role;
  - resolve rate when a specialist versus a generalist staffed the executor;
  - falsifier agreement with the hidden tests.

## Guardrails

- Grading (`scripts/grade_in_testbed.py`), the splits (`data/swebench/v15_splits.json`) and
  `results/v15/` are not modified.
- Role texts pass the leakage gate over the train, val and test patches, a ban list of instance
  ids and repository names, and a file-path check.
- Generalists are never removed (asserted in the evolve loop).
- The test split is read only in step 3.

---

## Results (Oct 29–30, 2026)

*Model: `reflection/Beam-1.0-501B-A23B-NVFP4` behind the llm-d v1.0.0 router.*

### 1. Summary across splits

| Arm | Organization | Train (21 dev, 1 run) | Validation (10 dev, 2 runs; mean / maj) | **Test (28 unseen Verified, 2 runs; mean / maj)** | Test runs `[r1, r2]` | Test agreement | Test mean tokens |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | ---: |
| **A** | `org_mode=off` (V15 champion `evo_g5_03`) | 4/21 | 4.0/10 (`[4, 4]`) / 3 | **16.5/28 (58.9%)** / 15 | `[17, 16]` | 0.893 | 195.8k |
| **B** | Refreshed library, `static` | 4/21 | 4.5/10 (`[4, 5]`) / 4 | **15.5/28 (55.4%)** / 14 | `[16, 15]` | 0.893 | 211.6k |
| **C** | Refreshed library, `dynamic` | 4/21 | **5.0/10 (`[5, 5]`)** / 5 | **16.5/28 (58.9%)** / 15 | `[15, 18]` | 0.893 | 208.8k |
| **D** (`evo_g1_07`) | Evolved org + code from C (best val of top-3 train) | **6/21** | **5.0/10 (`[6, 4]`)** / 4 | **15.5/28 (55.4%)** / 14 | `[15, 16]` | 0.893 | 184.4k |
| **E** (`ran_g0_02`) | Random search org + knobs from C (best val of top-3 train) | **6/21** | 4.0/10 (`[5, 3]`) / 3 | **16.0/28 (57.1%)** / 15 | `[15, 17]` | 0.929 | 159.9k |
| **V12** | Flat single-agent ReAct (68 moves) | — | — | **16.5/28 (58.9%)** / 16 | `[17, 16]` | 0.964 | 149.6k |

All 3 validation candidates per search arm (`D1=evo_g5_04`: `[4, 4]` mean 4.0; `D2=evo_g1_07`: `[6, 4]` mean 5.0; `D3=evo_g1_02`: `[4, 6]` mean 5.0; `E1=ran_g0_02`: `[5, 3]` mean 4.0; `E2=ran_g4_04`: `[4, 3]` mean 3.5; `E3=ran_g4_05`: `[4, 4]` mean 4.0) were evaluated twice on the 10-task validation split. `D2` (`evo_g1_07`) beat `D3` (`evo_g1_02`) on the pre-registered training-fitness tie-break (`0.2925` vs `0.2668`), and `E1` (`ran_g0_02`) beat `E3` (`ran_g4_05`) (`0.2996` vs `0.2829`).

### 2. Paired comparisons on the 28 unseen Test tasks

Per-task majority outcomes (exact two-sided sign test on discordant tasks) and 95% bootstrap CIs (10,000 resamples over tasks) on the mean resolve-rate difference:

| Comparison | Majority wins : losses | Sign test *p* | Mean Δ resolve rate | 95% bootstrap CI |
| :--- | :---: | :---: | :---: | :---: |
| `C` vs `A` (dynamic org vs no org) | 1 : 1 | 1.000 | +0.000 | `[-0.071, +0.071]` |
| `B` vs `A` (static org vs no org) | 0 : 1 | 1.000 | −0.036 | `[-0.107, +0.036]` |
| `D` vs `E` (evolved org vs random org) | 0 : 1 | 1.000 | −0.018 | `[-0.107, +0.054]` |
| `D` vs `A` (evolved org vs V15 champion) | 0 : 1 | 1.000 | −0.036 | `[-0.107, +0.036]` |
| `C` vs `V12` (dynamic org vs flat ReAct) | 1 : 2 | 1.000 | +0.000 | `[-0.107, +0.107]` |
| `A` vs `V12` (V15 champion vs flat ReAct) | 1 : 2 | 1.000 | +0.000 | `[-0.107, +0.107]` |

### 3. Organization metrics on Test (`n = 56` runs per arm)

| Metric | Arm B (`static`) | Arm C (`dynamic`) | Arm D (`evo_g1_07`) | Arm E (`ran_g0_02`) |
| :--- | :---: | :---: | :---: | :---: |
| Specialist hire rate — `architect` | 0.321 | 0.321 | 0.321 | 0.036 |
| Specialist hire rate — `executor` | 0.536 | 0.536 | 0.536 | 0.500 |
| Specialist hire rate — `refiner` | 0.393 | 0.411 | 0.375 | 0.286 |
| Specialist hire rate — `falsifier` | 0.179 | 0.179 | 0.179 | 0.179 |
| Mid-run CEO role swaps per run | 0.000 | 0.089 | 0.054 | 0.000 |
| Resolve rate when first executor (`b1`) is a specialist | 18/30 (60.0%) | 18/30 (60.0%) | 17/30 (56.7%) | 18/28 (64.3%) |
| Resolve rate when first executor (`b1`) is a generalist | 13/26 (50.0%) | 15/26 (57.7%) | 14/26 (53.8%) | 14/28 (50.0%) |
| **Falsifier agreement with hidden tests** | **33/56 (58.9%)** | **33/56 (58.9%)** | **40/56 (71.4%)** | **36/56 (64.3%)** |

### 4. Conclusions and diagnosis

1. **Validation gains did not transfer to the unseen test set.** On validation (`n = 10` × 2 runs), adding the dynamic organization (`C`: `5.0/10`) and evolving the organization (`D2`: `5.0/10`, `D3`: `5.0/10`, with `D3` uniquely solving the 39-test `sqlfluff__sqlfluff-880` task on both replicates) improved over the V15 champion `A` (`4.0/10`) and random search `E` (`4.0/10`). On the 28 unseen Verified test tasks, however, all arms clustered tightly between `15.5/28` (`55.4%`) and `16.5/28` (`58.9%`), with no statistically significant difference (`p = 1.0`).
2. **Flat ReAct (`V12`) matched the hierarchical harness at matched move floor (`16.5/28`).** Under identical cluster load and budget floor (`68` moves), flat single-agent ReAct achieved `[17, 16]` (`16.5/28`, `149.6k` tokens), tying `A` and `C` (`16.5/28`) with lower token cost and higher replicate agreement (`0.964` vs `0.893`).
3. **Why organization evolution plateaued:**
   - **Permissive falsifier / premature `VERDICT: PASS`:** The single-role falsifier agreed with the hidden test suite only `58.9%` of the time in `B`/`C` (`33/56`), rubber-stamping incomplete patches (`resolved_by_agentic_gatekeeper` or `resolved_by_hierarchical_ab_mcts` on tasks where hidden `FAIL_TO_PASS` or `PASS_TO_PASS` tests failed). Evolution (`D`) improved falsifier agreement to `40/56` (`71.4%`), but the falsifier was only hired as a specialist on `17.9%` of runs and received a tiny `±0.1` terminal credit only on the final episode.
   - **Tight turn caps (`68` moves, `6` falsifier turns, `10–18` executor turns):** Complex multi-file tasks exhausted episode turn caps before the executor or falsifier could run thorough regression checks.
   - **No structured cross-role memory or communication:** Episodes could not share discovered facts, ruled-out hypotheses, or failing inputs except through one-way summary strings, leading to repeated search and duplicate candidate patches.

Raw data: `results/v16/train_arms_2026-10-29/`, `results/v16/evolve_2026-10-29/`, `results/v16/random_2026-10-29/`, `results/v16/val_2026-10-29/`, `results/v16/test_2026-10-29/report.json`, `results/v16/v12_test_2026-10-29/`.

