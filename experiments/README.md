# Experiments index (V1 → V17)

This directory holds the design notes, evaluation reports, retrospectives, and pre-registered protocols for every HAE iteration across four architectural eras. Raw outputs (patches, `grade.json`, `verdicts_grade.json`, telemetry records) live in [`../results/`](../results/). For the project overview, industry context, and headline findings, see the [top-level README](../README.md).

---

## Era 1 · Execution-grounded firm evolution (V1 → V7)

*Evolved populations of multi-agent firms on synthetic coding benchmarks: a 7-test task (`V1–V4`), then a 50-test, 4-module task (`V5–V7`).*

| Version | Change | Key result | Docs |
| :--- | :--- | :--- | :--- |
| **V1** | LLM-judge fitness | Judge 25 → 97; exec. corr. +0.045 ¹ | [readme](v1/README.md) · [audit](v1/execution_grounded_correction.md) |
| **V2** | Held-out unit tests as fitness | 30 / 30 firms at 7 / 7 | [readme](v2/README.md) |
| **V3** | Firms rewrite the breeder | 10 / 10 at 7 / 7 (Gen 7) ² | [readme](v3_rsi/README.md) |
| **V4** | Self-hosted 180B MoE | 10 / 10 at 7 / 7 via `llm-d` | [readme](v4_llmd/README.md) |
| **V5** | Grammar-constrained JSON | −92 % output tokens; 10 / 10 at 46–50 / 50 | [readme](v5_typesafe_xgrammar/README.md) |
| **V6** | Evidence-gated search | 6 / 9 failed first passes recovered | [readme](v6_epistemic_search/README.md) · [results](../results/hae_gen16_v6_cohort_r2/README.md) |
| **V7** | Learned value / prior heads | Value head beats base rate; prior head doesn't | [results](../results/v7_value_heads/README.md) |

<sub>¹ 0/60 late firms passed all execution gates; LLM judging was abandoned permanently.<br>
² Gen 8–9 inherited workspaces through a copy bug, fixed in `71a7da4`.</sub>

---

## Era 2 · Real GitHub issues, single-layer agents (V8 → V12)

*One hand-designed genome per version on a 30-instance dev cohort from the [50-instance pool](../data/swebench/) (disjoint from `SWE-bench Verified`), graded inside official containers.*

| Version | Change | Resolved | Docs |
| :--- | :--- | :---: | :--- |
| **V8** | SWE-bench harness + dynamic org | 4 / 30 | [roadmap](v8/v8_role_evolution_and_swebench_roadmap.md) · [report](v9/v8_v9_evaluation_report.md) |
| **V9** | AB-MCTS replaces PUCT | 3 / 30 | [spec](v9/ab_mcts.md) · [report](v9/v8_v9_evaluation_report.md) |
| **V10** | Virtual loss + EPLB serving | 5 / 30 | [report](v10/v10_eplb_evaluation_report.md) |
| **V11** | Multi-file repair plans | 6 / 30 | [report](v11/v11_evaluation_report.md) |
| **V12** | Interactive ReAct tool loops | 14 / 30 ³ | [report](v12/v12_evaluation_report.md) · [results](../results/swebench/dev_eval_2026-10-09/README.md) |

<sub>³ Prompts included a small number of instance-targeted hints. `V12` (with hints stripped) serves as the matched-budget flat ReAct control in `V15–V17`.</sub>

---

## Era 3 · Clean-context hierarchy, `AB-MCTS` & milestone ratchets (V13 → V14)

> **Contamination note (Oct 27 audit):** The `V14` SWE-bench dev-slice (`6/6`) and unseen Verified (`21/28`) rows below were later found to contain 4 gold-fix AST detectors and test-task prompt examples; do not cite those SWE-bench numbers as clean generalization—see the [V14 retrospective](v14/v14_retrospective.md). The greenfield `MiniSQL` milestone ratchet benchmark (`15/15` held-out `sqlite3` oracle vs. `8/15` monolithic `AB-MCTS` vs. `0/15` flat ReAct) was unaffected and holds cleanly.

| Version | Change | Tasks | Result | Time | Docs |
| :--- | :--- | :--- | :---: | ---: | :--- |
| **V13** | Clean-context layers + `AB-MCTS` over Git checkpoints | 30 dev tasks | 30 / 30 ⁴ | 1,211 s | [spec](v13/v13_hierarchical_agentic_ab_mcts_architecture.md) · [report](v13/v13_evaluation_report.md) |
| **V13** | Migration to `Beam-1.0-501B-A23B` (NVFP4) | 6 dev tasks | 6 / 6 ⁴ | 306 s | [report](v13/beam_vs_qwen38_evaluation_report.md) |
| **V14** | 5-way component ablation (`--include-hints` OFF) | 6 tasks × 5 arms | 6/6 → 4/6 → 4/6 → 2/6 → 0/6 ⁵ | 279–446 s | [report](v14/v14_verified_and_ablation_report.md) · [retrospective](v14/v14_retrospective.md) |
| **V14** | Unseen `SWE-bench Verified` (`--include-hints` OFF) | 28 tasks (10 repos) | 21 / 28 vs 19 / 28 V12 ⁵ | 513 s | [report](v14/v14_verified_and_ablation_report.md) · [retrospective](v14/v14_retrospective.md) |
| **V14** | **`L0-Macro` planner-driven milestone ratchet** | **`MiniSQL` (`2,345` LoC)** | **`15 / 15` `sqlite3` oracle** (vs **`8 / 15`** monolithic `AB-MCTS`, **`0 / 15`** ReAct) | 739 s | [spec](v14/v14_generalized_macro_ratchet_architecture.md) · [results](../results/ratchet/planner_driven_minisql_eval_2026-10-23/README.md) |
| **V14** | Milestone ratchet (hand-authored DAG) | `MiniLang` (`1,139` LoC) | 4 / 4 ⁶ | 246 s | [spec](v14/v14_generalized_macro_ratchet_architecture.md) · [results](../results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md) |

<sub>⁴ Development runs with repo/instance-specific prompt hints (`e20a400`).<br>
⁵ Contaminated by 4 gold-fix AST detectors and prompt examples; superseded by clean `V15–V17` runs ([retrospective](v14/v14_retrospective.md)).<br>
⁶ Hand-written milestone DAG with self-authored acceptance tests.</sub>

---

## Era 4 · Evolving the harness, "Company DNA" & firewalled Red Teams (V15 → V17)

*All instance hints and AST detectors removed; automated AST/token `LeakageGate` enforced on every code and role mutation; real `llm-d v1.0.0` standalone prefix/queue router on GKE.*

| Version | Focus & Architectural Additions | Key Empirical Findings & Status | Docs |
| :--- | :--- | :--- | :--- |
| **V14 retro** | Full audit of `V13–V14` prompts & detectors; formal separation of train / val / unseen test splits | Stripped 4 gold-fix detectors & test-task examples; motivated `LeakageGate` | [retrospective](v14/v14_retrospective.md) |
| **V15** | **Harness code + 18-knob evolution** (`pop=8`, 6 gens, `LeakageGate`, unit-test gate, matched random-search control) | **Complete:** Evolved **`17 / 28` (`60.7%`)** vs hand-designed seed **`12 / 28` (`42.9%`)** (`5:0` discordant, $p=0.063$) and random search **`16 / 28`** on unseen `SWE-bench Verified`; logs showed `86%` (`508/590`) `consumer_local` anchoring | [design](v15/README.md) · [test results](../results/v15/test_2026-10-28/README.md) |
| **V16** | **"Company DNA" (1-line specialist roles + 3–8 line playbooks + UCB CEO staffing & stall swaps)** + single-use pre-warmed Pod pool + `llm-d` EPP queue tuning | **Train & 2×10 Val complete; 2×28 Test running:** On 2×10 validation, dynamic org `C` reached **`5.0 / 10` mean** (`5 / 10` majority, **`1.00` agreement**) vs `A` `4.0 / 10` mean (`3 / 10` majority), and evolved `D2` (`evo_g1_07`) reached **`5.0 / 10` mean** vs random `E1` `4.0 / 10`. Log audit uncovered **`31.5%` (`211 / 670`) falsifier precision** & 3× duplicate-patch loops | [protocol & interim results](v16/README.md) · [role refresh](v16/role_refresh_report.md) |
| **V17** | **Foundational Red-Team guardrails** (`no_edit`, `lock_repro_test`, `skeptic_default`, `2×` FP verdict fitness) + **Typed Shared Blackboard (`.hae/blackboard.jsonl`) & budgeted 2-turn Q&A** + **`250`-move self-terminating budgets** + `41`-task discriminative pool | **Staged (`v17-redteam`):** Launches immediately after `V16` test & official re-grade finish; compares `A17`, `S`, `B17`, `Q17`, `R`, `Rr`, and matched-budget `V12` | [experiment plan](v17/README.md) · [architecture SVG](v17/assets/hae_architecture_v17.svg) |

---

## Recommended reading order

1. **Why execution grounding is non-negotiable:** [V1](v1/README.md) and its [execution-grounded audit](v1/execution_grounded_correction.md).
2. **Interactive tool loops on real repositories:** [V12 evaluation report](v12/v12_evaluation_report.md).
3. **Clean-context `AB-MCTS` ("Move 37" branching) & Git milestone ratcheting:** [V13 hierarchical AB-MCTS spec](v13/v13_hierarchical_agentic_ab_mcts_architecture.md), [V14 macro-ratchet spec](v14/v14_generalized_macro_ratchet_architecture.md), and [MiniSQL 15/15 `sqlite3` oracle results](../results/ratchet/planner_driven_minisql_eval_2026-10-23/README.md).
4. **Benchmark hygiene & evolving the harness itself:** [V14 retrospective](v14/v14_retrospective.md), [V15 design](v15/README.md), and [V15 unseen Verified results](../results/v15/test_2026-10-28/README.md).
5. **Evolving "Company DNA", firewalled Red Teams & typed blackboards:** [V16 Company DNA protocol & validation results](v16/README.md), [V16 role refresh report](v16/role_refresh_report.md), and the [V17 Red-Team + Blackboard experiment plan](v17/README.md).

Diagrams: [V15–V17 system architecture](v17/assets/hae_architecture_v17.svg) · [V15 architecture](v15/assets/v15_system_architecture.svg) · [V14 milestone ratchet architecture](v14/assets/v14_macro_ratchet_architecture.svg) · [V2–V3 trajectory](v2/assets/v2_v3_evolutionary_trajectory.png)
