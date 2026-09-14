# Experiment Ledger

This directory is the empirical record of the Hierarchical Agent Evolution
program. It is organised by **experiment set** — a contiguous run of
generations sharing one platform architecture and one fitness function.

| Set | Generations | Status | Index |
|---|---|---|---|
| **V1** | Pilot → Gen 10 (Gen 11 aborted) | **Closed** | [`v1/README.md`](v1/README.md) |
| **V2** | Gen 1 → … | Not started | [`v2/README.md`](v2/README.md) |

---

## Why V1 was closed

V1 ran thirteen experiments and produced a published net-fitness curve that
climbed from 25.25 to the low 90s. Re-verification under a real execution
harness showed that curve measured **prose quality, not working software**:

* `corr(generation, execution_score) = +0.045` — **six generations of selection
  produced no measurable improvement in whether the generated code runs**,
  while published net fitness climbed at +0.50/generation.
* **No firm in 60 ever passed all five execution gates.** The best
  (`gen_9_pareto_bonus_2`, 4/5) finished 6th in its cohort and was never bred
  forward.
* Under a rebuilt, execution-weighted rubric, **five of six champions change**;
  the Gen 9 champion drops to last of ten.
* Two headline capabilities — Gen 6's consortiums/harnesses and Gen 10's
  federated mesh / self-evolving rubrics — were **never wired into any runtime
  path**. The modules existed and had tests; no tournament ever called them.
* Every generation from Gen 5 on ran on a **human's OAuth token**, which is why
  firms died roughly an hour into each tournament.

The full account, with per-claim retractions, is in
[`v1/execution_grounded_correction.md`](v1/execution_grounded_correction.md).

V1 is **frozen**. Its scorecards, genomes, breeding scripts, populations and
Kubernetes manifests are archived in place under `v1/` so every published
number remains reproducible. Nothing in `v1/` is maintained against the
current codebase.

## What V2 changes

| V1 failure | V2 remedy |
|---|---|
| Fitness dominated by an LLM judge that saturated at 99/100 | `execution_integrity` is the heaviest single term (0.30) and the judge cannot observe it |
| Objective was open-ended prose; any plausible essay scored well | **Self-hosting benchmark** — firms implement a real `hae/` module against held-out tests they have never seen |
| Capabilities named a generation without being reachable | Architectural guard test: every module must be reachable from an entry point |
| Ten one-off `breed_genN.py` scripts | One runner + declarative per-generation config |
| 22 near-identical Kubernetes manifests | One templated Job |
| Backwards-compatibility shims masking real failures | Removed; missing data fails loud |

---

## Reproducing a V1 result

All V1 tooling lives beside the data it produced. Run from the repository root:

```bash
PYTHONPATH=. python3 experiments/v1/scripts/backfill_execution_fitness.py
PYTHONPATH=. python3 experiments/v1/scripts/rescore_under_new_rubric.py
PYTHONPATH=. python3 experiments/v1/scripts/audit_artifact_integrity.py
```

> [!WARNING]
> V1 scripts import from the pre-refactor `src/` package layout. They are
> preserved as a historical record and are **not** expected to run against the
> current `hae/` package without modification.
