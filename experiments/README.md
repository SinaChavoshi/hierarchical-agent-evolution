# Experiments index (V1 → V14)

This directory holds the design notes, evaluation reports and errata for every
HAE version. Each version changed one well-defined part of the system: how
firms are scored, how work is verified, or how the agent hierarchy and search
are structured. Harvested raw outputs (patches, `grade.json`, records) live
under [`../results/`](../results/).

For what the project is and the current headline results, start with the
[top-level README](../README.md).

---

## Era 1: Execution-grounded evolution (V1–V7)

Benchmarks: an internal 7-test `artifacts.py` task (V1–V4) and a 4-module,
50-test `full_stack_hae` benchmark (V5–V6). Fitness is computed over evolved
populations of firms.

| Version | What changed | Result | Write-up |
| :--- | :--- | :--- | :--- |
| **V1** | 31–34-agent firms graded mostly by an LLM judge plus regex "sandbox" gates | Judged fitness rose 25 → 97, but `corr(generation, execution) = +0.045` and 0/60 late firms passed all execution gates. Closed and audited. | [v1](v1/README.md), [correction](v1/execution_grounded_correction.md) |
| **V2** | Held-out unit tests as fitness, zero-seed workspaces, a 10-iteration test-and-repair loop from Gen 4 | Gen 4–6: 30/30 firms at 7/7, and mean repair iterations fell 2.20 → 1.30. Total cost $28. | [v2](v2/README.md) |
| **V3** | Firms rewrite parts of HAE's own breeder (`code_overlays`) | Gen 7: 10/10 at 7/7. The audit found a workspace-inheritance bug in Gen 8–9 (fixed in `71a7da4`). | [v3_rsi](v3_rsi/README.md) |
| **V4** | Self-hosted Qwen3.8-Flash-Next (180B MoE) behind `llm-d` | Gen 11: 10/10 at 7/7, with 455/455 prefix-affinity hits and 37–73% of requests served without GPU work. | [v4_llmd](v4_llmd/README.md) |
| **V5** | Grammar-constrained JSON packets (`xgrammar`) and the 50-test benchmark | Output tokens per call fell 92%. Gen 15: 10/10 firms at 46–50/50. Gen 12 was retracted because of a caching bug (`6818d8a`). | [v5_typesafe_xgrammar](v5_typesafe_xgrammar/README.md) |
| **V6** | Repair as evidence-gated epistemic search: hypothesis ledger, non-LLM gatekeeper, PUCT | It recovers catastrophic first passes (6/9 in run 2). LLM self-stated priors turned out to be uncalibrated. | [v6_epistemic_search](v6_epistemic_search/README.md), [results](../results/hae_gen16_v6_cohort_r2/README.md) |
| **V7** | Logistic value and prior heads trained on 48 V6 search trees | The value head beats the base rate out of fold (Brier 0.186 vs 0.202). The prior head does not (0.0863 vs 0.0862). | [v7_value_heads](../results/v7_value_heads/README.md) |

## Era 2: SWE-bench, single-layer agents (V8–V12)

Benchmark: a 30-instance dev cohort with valid environments, drawn from the
50-instance pool in [`../data/swebench/`](../data/swebench/). The pool is
disjoint from SWE-bench Verified. Grading uses the official containers. Each
version runs a single hand-designed genome, not an evolved population.

| Version | What changed | Resolved (n = 30) | Write-up |
| :--- | :--- | :---: | :--- |
| **V8** | SWE-bench container harness, dynamic org (team sizing, `recruit_specialist`, role mutation) | 4 / 30 | [roadmap](v8/v8_role_evolution_and_swebench_roadmap.md), [report](v9/v8_v9_evaluation_report.md) |
| **V9** | AB-MCTS (Thompson sampling over Beta arms) replaces fixed-width PUCT | 3 / 30 | [ab_mcts](v9/ab_mcts.md), [report](v9/v8_v9_evaluation_report.md) |
| **V10** | Virtual loss on untested backlog, plus expert parallelism and EPLB on the serving side (+7% throughput) | 5 / 30 | [report](v10/v10_eplb_evaluation_report.md) |
| **V11** | Error-conditioned synthesis retries, multi-file repair plans, escape-preserving splicing | 6 / 30 | [report](v11/v11_evaluation_report.md) |
| **V12** | Interactive ReAct tool loops (`view_file`, `bash`, `str_replace`, `submit`), mandatory repro script, Best-of-3 causal framing | 14 / 30 † | [report](v12/v12_evaluation_report.md), [results](../results/swebench/dev_eval_2026-10-09/README.md) |

† The V12 prompts include a few hints aimed at specific instances (for
example, `RemovedInMarshmallow4Warning` and `@cachedproperty → @cached_property`).

## Era 3: Hierarchical verification (V13–V14)

| Version | What changed | Result | Write-up |
| :--- | :--- | :--- | :--- |
| **V13** | Context-bounded layers (L0 controller, L1-A recon, L2 executors, L1-B falsifier) with AB-MCTS over git checkpoints (`GEN` vs `CONT`) | n = 30: 15/30 → 22/30 → 30/30 across successive harness revisions, **all with instance-specific hints** ‡ | [architecture](v13/v13_hierarchical_agentic_ab_mcts_architecture.md), [report](v13/v13_evaluation_report.md) |
| **V13 + Beam-501B** | Swapped Qwen3.8 (180B) for Beam-1.0-501B + DFlash2 speculative decoding, run on the 6-instance slice | Hinted V13: 6/6 on both models, 3.3× faster on Beam (306 s vs 1,011 s) with 55% fewer tokens. V12 on Beam: 2/6. | [report](v13/beam_vs_qwen38_evaluation_report.md), [results](../results/swebench/beam_eval_2026-10-20/README.md) |
| **V14** | All repo- and instance-specific prompt text removed. Generic AST checks, pluggable verifiers, new-file support, and the `L0-Macro` milestone ratchet | Zero-hint, n = 6: **6/6**, 57/57 F2P, 214/214 P2P, 279 s mean. MiniLang ratchet: 4/4 milestones, 1,139 LoC, 25/25 cumulative tests | [architecture](v13/v14_generalized_macro_ratchet_architecture.md), [SWE-bench](../results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md), [ratchet](../results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md) |

‡ The repo-specific strings in `hae/swebench/hierarchical_mcts.py` grew from 3
(15/30 and 22/30) to 25 (30/30, `e20a400`). Several of the 25 describe the gold
fix for specific instances. The V13 30/30 figure should be read as a
development run, not a benchmark score.

V14's limitations:
- The 6-instance slice comes from the dev cohort the harness was tuned on.
- SWE-bench `hints_text` was included.
- Each configuration is a single run.
- The ratchet milestones and their acceptance tests were not produced
  independently: the milestones were hand-written, and the agent wrote the
  tests.

See [Caveats](../README.md#caveats) in the top-level README.

---

## Reading order

1. **Why execution grounding matters.** Read [v1](v1/README.md) and
   [execution_grounded_correction.md](v1/execution_grounded_correction.md).
2. **Search and serving foundations.** Read [v4_llmd](v4_llmd/README.md),
   [v5_typesafe_xgrammar](v5_typesafe_xgrammar/README.md) and
   [v6_epistemic_search](v6_epistemic_search/README.md).
3. **From packets to tool-using agents.** Read the
   [V11 report](v11/v11_evaluation_report.md) and the
   [V12 report](v12/v12_evaluation_report.md).
4. **The hierarchical harness.** Read the
   [V13 architecture](v13/v13_hierarchical_agentic_ab_mcts_architecture.md)
   and the [Beam vs Qwen report](v13/beam_vs_qwen38_evaluation_report.md).
5. **Current system.** Read the
   [V14 architecture](v13/v14_generalized_macro_ratchet_architecture.md) and
   the two V14 result folders linked above.

Diagrams: [V14 architecture](v13/assets/v14_macro_ratchet_architecture.svg),
[V13 architecture](v13/assets/v13_beam_architecture.svg),
[GKE topology](v13/assets/gke_beam_501b_topology.svg),
[V2–V3 trajectory](v2/assets/v2_v3_evolutionary_trajectory.png).

> **Note.** The V14 architecture doc currently lives under `v13/`. Moving it
> and the V14 assets into a `v14/` folder would make this directory easier to
> navigate.
