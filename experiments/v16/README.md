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
