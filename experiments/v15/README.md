# V15: Evolving the Harness (Knobs + Code)

*Opened Oct 27, 2026. Status: complete (Oct 28). **Results: [results/v15/test_2026-10-28/README.md](../../results/v15/test_2026-10-28/README.md).***

> **Outcome.** On the 28 held-out Verified tasks the best evolved harness solved 17/28, random settings search
> 16/28 and the hand-designed seed 12/28. Search beats the seed (evolved vs seed 5 : 0, p = 0.063), but evolution
> did not beat random search at matched budget (generations 0–3: 0 : 4). Random search was stopped after
> generation 3 because of a time constraint.

V1–V7 evolved populations of agent firms, but on tasks that saturated. V8–V14
hand-designed one harness per version. V15 returns to the project's original
thesis and tests it properly: **does evolution find a better agent harness than
hand design, on tasks it never saw?**

Background and motivation: [V14 retrospective](../v14/v14_retrospective.md).

---

## 1. What evolves

A genome ([`HarnessGenome`](../../hae/evolution/genome.py)) has two parts:

| Gene type | What it controls | Where |
| :--- | :--- | :--- |
| **Knobs** (18 typed genes) | Move budget, episode count, turns per layer (recon, GEN, CONT, falsifier), sampling temperatures, CONT trigger, diff-bloat threshold, and the three layer switches (recon, falsifier, CONT) | [`hae/swebench/harness_knobs.py`](../../hae/swebench/harness_knobs.py). Defaults reproduce V14 exactly. |
| **Harness code** | A unified diff against the harness source (`hae/swebench/hierarchical_mcts.py`, `hae/swebench/agentic.py`): prompts, AB-MCTS selection and value functions, verification strategy, context management | [`hae/evolution/mutation.py`](../../hae/evolution/mutation.py) |

Grading, datasets and the evaluator are **not** evolvable, so a candidate can't
change how it is scored. Agents run from a per-genome git worktree; grading
always uses this repository's `scripts/grade_in_testbed.py`.

## 2. Operators

- **Code mutation (DGM / AlphaEvolve-style).** The meta-model (the same
  `Beam-1.0-501B` endpoint) gets a summary of the architecture, a *redacted*
  failure digest from the parent's training runs (stop reasons, layer traces;
  no task names, file paths or hidden-test names), and the full source of 2
  randomly chosen harness hot spots. It returns SEARCH/REPLACE edit blocks.
  Each proposal must (1) apply exactly, (2) pass the **leakage gate**, (3)
  compile, and (4) pass the gate unit tests. Failures are fed back for up to 3
  attempts.
- **Knob mutation**: Gaussian steps on 2 randomly chosen genes (bools flip).
- **Crossover**: uniform knob crossover, and patch union (A's patch, then B's
  on top; falls back to A's if B conflicts or the gates fail).

### Leakage gate

V14's contamination came from task-specific fixes hidden in generic-looking
code. [`hae/evolution/leakage.py`](../../hae/evolution/leakage.py) rejects any
patch that *introduces* (relative to the seed harness) an instance id, a
repository name, or a rare identifier or string literal from the gold or
hidden-test patch of **any** train, validation or test task.

## 3. Protocol

| Split | Tasks | Source | Use |
| :--- | :---: | :--- | :--- |
| **Train** | 21 | Container-validated tasks of the 50-task SWE-bench dev pool | Fitness during evolution (`stage1` = 6 of them) |
| **Validation** | 10 | Remaining validated dev-pool tasks | Final genome selection |
| **Test** | 28 | Unseen SWE-bench Verified tasks from V14 | Reported once, at the end |

Splits are fixed in [`data/swebench/v15_splits.json`](../../data/swebench/v15_splits.json).
Dev pool and Verified are disjoint.

**Per generation (successive halving):** each new genome runs on the 6
`stage1` tasks; the top 3 are promoted to all 21 training tasks; elites are
ranked by **fitness = training resolve rate + 0.25 × partial FAIL_TO_PASS
credit on unresolved tasks − 0.05 × (mean tokens per task / 1M)**. The next
population (size 8) is bred from the top 4 elites (tournament selection): 50%
code mutation, 30% knob mutation, 20% crossover.

> **Protocol revision (Oct 27, during generation 0, before any selection).**
> The first `stage1` subset was uninformative: all 8 generation-0 genomes
> solved the same 1 task and failed the same 5, so ranking came down to token
> cost alone. Both runs were paused and `stage1` was replaced with 4 near-miss
> and 2 solved tasks, chosen from the seed's *training* results only. Partial
> FAIL_TO_PASS credit was added to fitness. The change was applied identically
> to the evolve and random runs; the original subset is kept in the splits
> file as `stage1_v0_uninformative`.

**Arms compared on the test split:**

1. **Evolved harness** (best genome on validation).
2. **Random search** over the same knob space with the same evaluation budget
   and halving schedule (`--mode random`). This is the control that tests
   whether evolution, not just tuning, helps.
3. **Seed harness** (V14 with gold-fix detectors removed; the "hand-designed" baseline).
4. **Flat ReAct** (`v12_agentic`) and **original V14**: results already exist
   on the same 28 test tasks.

## 4. Phase 0 (done)

- Removed the four V14 gold-fix detectors (−310 lines in `hierarchical_mcts.py`).
- Promoted 18 hard-coded settings to knobs; the engine and runner read them
  from the genome (`epistemic_policy.v15_knobs`).
- Clean seed genome [`v15_seed_harness_firm_0.json`](../../configs/genomes/v15_seed_harness_firm_0.json)
  (Era-1 CEO prompt residue removed).
- `scripts/swebench_kubectl_batch.py` can run an agent from a candidate
  worktree (`harness_root`) while grading from the main repository.
- Evolution package: [`hae/evolution/`](../../hae/evolution/), CLI
  [`scripts/v15_evolve.py`](../../scripts/v15_evolve.py), tests
  [`tests/test_v15_evolution.py`](../../tests/test_v15_evolution.py).

## 5. Phase 1 (running)

```bash
# Evolution (knobs + code)
PYTHONPATH=. python3 scripts/v15_evolve.py evolve --mode evolve \
  --out results/v15/evolve_2026-10-27 --workers 4
# Equal-budget random-search control (reuses the seed's training evaluations)
PYTHONPATH=. python3 scripts/v15_evolve.py evolve --mode random \
  --out results/v15/random_2026-10-27 --workers 2 --seed-results-from results/v15/evolve_2026-10-27
# Selection on validation, then a single test run
PYTHONPATH=. python3 scripts/v15_evolve.py validate --out results/v15/validation --genomes <run_dir>:<genome_id> ...
PYTHONPATH=. python3 scripts/v15_evolve.py test --out results/v15/test --genomes <run_dir>:<genome_id> ...
```

> **Serving change (Oct 27, 23:47 UTC).** Both runs were paused at the start of generation 1 (evolve) and the
> end of generation-0 stage 1 (random) and resumed behind the real **llm-d v1.0.0 router** (prefix-cache +
> queue + KV-cache scorers) instead of the repo's custom Python proxy. Completed evaluations were kept. The
> custom proxy's request/response rewrites were audited as no-ops for these runs (see
> [`k8s/llm-d/README.md`](../../k8s/llm-d/README.md)), so the change affects speed, not agent behavior.

## 6. Known limitations

- **Noise.** Every (genome, task) pair is one run. Small fitness differences
  between genomes on 21 tasks are within noise; we keep the elites' cached
  results rather than re-sampling.
- **Residual tuning in the seed.** The seed's recon still contains heuristics
  developed on the dev pool (file-discovery rules). They affect all arms
  equally, but they may inflate absolute training scores.
- **Grading** uses `grade_in_testbed.py` (approximate). Final test results
  should also be re-graded with the official `swebench eval` harness.
