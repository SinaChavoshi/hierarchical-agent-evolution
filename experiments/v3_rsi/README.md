# V3: Closed-Loop Recursive Self-Improvement (Generations 7-9)

V3 tested whether the agent firms bred by HAE (hierarchical-agent-evolution) can re-implement part of HAE's own breeding engine, and whether the host can then execute those re-implementations to breed the next generation. The campaign is complete: 3 generations, 30 of 30 firms finished, $18.92 total spend. A post-run audit found that Generations 8 and 9 started with the target module already on disk, so their iteration figures do not measure from-scratch synthesis; see the audit section for the evidence and the fix.

## What was tested

Each firm (an organisation of 31-63 LLM agents) had to write [`hae/genome/morphogenesis.py`](../../hae/genome/morphogenesis.py), the module whose `MorphogenesisEngine` and `StructuralCrossoverEngine` classes mutate and cross genomes. Work was scored against a held-out suite the firms cannot see, [`tests/test_morphogenesis.py`](../../tests/test_morphogenesis.py) (7 tests). Passing implementations were stored per firm as code overlays, and the host [`Breeder`](../../hae/orchestration/breeder.py) executed the winning overlays to breed the following generation (mechanism below).

## Results

All three generations targeted `hae/genome/morphogenesis.py` with 10 firms each. Net fitness is the scorecard `fitness_score`. The machine-readable version is [`ledger_level3_rsi.json`](ledger_level3_rsi.json).

| Generation | Breeding engine used | Target pre-seeded (`inherited_files`) | Peak net fitness (firm) | Median net | Mean net | Passed 7/7 | Solved on iteration 1 | Mean iterations | Spend |
| :---: | :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| 7 | kernel code (Gen 6 champions) | 0 / 10 | **96.83** (`gen_7_pareto_1`) | 67.88 | 71.77 | 10/10 | 60% (6/10) | 1.50 | $6.6945 |
| 8 | evolved overlays from Gen 7 | 10 / 10 (confounded) | **97.92** (`gen_8_pareto_2`) | 79.00 | 74.52 | 10/10 | 80% (confounded) | 1.30 (confounded) | $6.3380 |
| 9 | evolved overlays from Gen 8 | 10 / 10 (confounded) | **94.89** (`gen_9_crossover_1`) | 71.91 | 72.71 | 10/10 | 100% (confounded) | 1.00 (confounded) | $5.8843 |

Generation 7 started from an empty `morphogenesis.py`; all 10 firms passed the 7 tests, 6 of them on the first iteration. The Generation 8 and 9 iteration columns are marked confounded because every firm in those generations began with its parent's passing `morphogenesis.py` in the workspace.

![V2 and V3 fitness trajectory, Generations 1-9](../v2/assets/v2_v3_evolutionary_trajectory.png)

## Per-firm code overlays

To let 10 firms per generation modify HAE code concurrently without touching the shared kernel under `hae/`, V3 added `CompanyGenome.code_overlays: Dict[str, str]` and a sandboxed module loader, `get_overlay_class` in [`hae/runtime/overlay.py`](../../hae/runtime/overlay.py).

1. When a firm reaches 100% on the held-out tests for a target module, [`worker.py`](../../hae/orchestration/worker.py) copies the verified source into that firm's `genome.code_overlays`.
2. During breeding, the `Breeder` loads each surviving parent's `MorphogenesisEngine` and `StructuralCrossoverEngine` from `parent_genome.code_overlays["hae/genome/morphogenesis.py"]`. If an overlay raises an exception, it falls back to the kernel implementation.
3. All 30 evolved `morphogenesis.py` overlays are archived in [`overlays/`](overlays/) (`generation_7/`, `generation_8/`, `generation_9/`).

## Post-run audit

Every scorecard in [`generation_7_results/`](generation_7_results/), [`generation_8_results/`](generation_8_results/) and [`generation_9_results/`](generation_9_results/) stores the full inter-department transcript (`run_output.departmental_briefs`) and the CEO's synthesis (`run_output.final_deliverable`), about 71 KB per firm. Reading these transcripts after the run produced the two findings below.

### Target module pre-seeded in Generations 8 and 9 (fixed in commit `71a7da4`)

The Product and Developer Experience brief (`dept_product_ux`) of the Generation 9 champion, [`gen_9_crossover_1_result.json`](generation_9_results/gen_9_crossover_1_result.json), told the CEO that "the mandated precision fix for temperature crossover is already implemented in the current `hae/genome/morphogenesis.py` module" and that the department would skip implementation and go straight to verification. For a zero-seed task that should have been impossible. Checking `run_output.inherited_files` across all 90 scorecards (Generations 1-9) showed:

- Generations 1-7 (70 firms): the target module never appeared in `inherited_files`. In Generation 7, `code_overlays` held only `hae/evaluation/artifacts.py` from Generation 6, so all 10 firms wrote `morphogenesis.py` from scratch.
- Generations 8 and 9 (20 firms): the target module was in `inherited_files` for 10/10 firms in each generation.

Root cause. In [`hae/orchestration/worker.py`](../../hae/orchestration/worker.py), the filter meant to remove the active benchmark's target module from `seed_files` when `carry_artifacts=False` read `getattr(task.verifier, "target_module", None)`. The verifier is a [`BenchmarkVerifier`](../../hae/task/verifier.py), which keeps that value at `verifier.benchmark.task(verifier.task_id).target_module`, so the lookup returned `None` and nothing was filtered. Once Generation 7 had promoted `morphogenesis.py` into `code_overlays`, each Generation 8 and 9 firm started iteration 1 (of up to 10) with its parent's 100%-passing copy on disk, and the monotonic verification guard in [`hae/runtime/workspace.py`](../../hae/runtime/workspace.py), which refuses any `write_file` that would lower a file's current test score, blocked every overwrite scoring below 100%.

Consequence. Generations 8 and 9 measured overlay preservation and verification, not zero-seed synthesis; their iteration-1 rates (80%, 100%) and mean iterations (1.30, 1.00) should not be compared with Generation 7. The only two Generation 8 firms that needed more than one iteration did so because an agent modified `hae/genome/schema.py`, which the guard on `morphogenesis.py` did not protect. Two parts of the result stand: Generation 7 was a genuine zero-seed synthesis, and the `Breeder` did compile and execute the Generation 7 and 8 overlays, through `get_overlay_class`, to breed Generations 8 and 9.

Fix. Commit `71a7da4` changes `worker.py` to resolve the target module from `verifier.benchmark.task(verifier.task_id).target_module` and strip it from `seed_files` whenever `carry_artifacts=False`. The genome keeps its `code_overlays` for the `Breeder`; only the child's workspace no longer receives the active target.

### What the transcripts show about the one-pass waterfall runtime

The transcripts of `gen_8_pareto_2` (97.92) and `gen_9_crossover_1` (94.89) show one strength and one limitation of the V1-V3 runtime, in which each agent speaks once per pass (CEO -> workers -> VPs -> red team -> CEO).

- Cross-department error catching worked. When `dept_systems_eng` proposed adding `from opentelemetry import trace, metrics` to `morphogenesis.py`, which would crash in the hermetic `unshare -rn` sandbox, both `dept_qa_redteam` and `dept_finance_ops` objected in their briefs, and the CEO (Gemini 2.5 Pro) accepted the Systems Engineering design only with the non-standard dependencies removed.
- Most of the budget went to memos. Because agents spoke once per pass and 70% of the fitness score came from an LLM judge grading the prose deliverable, each firm produced 56,000-71,000 characters (over 500,000 tokens) of formal memos ("MEMORANDUM FOR THE EXECUTIVE COUNCIL", "Respectfully, without compromise") instead of short multi-turn engineering exchanges; more than 80% of the token budget went to this prose. This motivated the multi-turn runtime of V4 (code-named Dosadi; see [`../v4_llmd/README.md`](../v4_llmd/README.md)).

## Files

- [`CAMPAIGN_STATUS.md`](CAMPAIGN_STATUS.md): short status table for Generations 7-9.
- [`ledger_level3_rsi.json`](ledger_level3_rsi.json): machine-readable summary ledger.
- [`generation_7_results/`](generation_7_results/), [`generation_8_results/`](generation_8_results/), [`generation_9_results/`](generation_9_results/): JSON scorecards and transcripts, 10 firms each (Generation 7 zero-seed; 8 and 9 the first and second closed-loop generations).
- [`overlays/`](overlays/): the evolved `morphogenesis.py` of every firm (`generation_7/`, `generation_8/`, `generation_9/`).
