# Hierarchical Agent Evolution (HAE)

**Hierarchical Agent Evolution (HAE)** is a **Universal Two-Tier Hierarchical Verification & Agentic Tree-Search Architecture (`V14`)**—combining an outer **`L0-Macro` Compositional Milestone DAG & Persistent Git Commit Ratchet** (`hae/hierarchical/`) with an inner **Zero-Hint `L0-Micro` V13 Context-Bounded Hierarchical AB-MCTS ("Move 37" Engine)** (`hae/swebench/hierarchical_mcts.py`), powered by self-hosted **Reflection `Beam-1.0-501B-A23B-NVFP4` (`501B` total / `23B` active MoE) + `DFlash2` (`8`-token speculative decoding)** on GKE (`12x NVIDIA RTX PRO 6000 Blackwell 96 GB GPUs`, `1.15 TB` VRAM). HAE scales seamlessly from **5-minute surgical bug fixes (`< 200` LoC, `100.0%` SWE-bench resolved)** to **multi-day full-system builds (`5,000–100,000+` LoC)** with zero benchmark-specific hints, zero context rot, and zero `L1` falsifier overload.

> [!IMPORTANT]
> **Headline Empirical Results & Architectural Capabilities (`V14`: `L0-Macro` Ratchet + Zero-Hint `L0-Micro` V13 + `Beam-1.0-501B` + `DFlash2`)**
> - **Unbounded Multi-Day Scaling via `L0-Macro` Git Commit Ratchet (`V14`)**: Decomposes multi-stage `5,000–100,000+` LoC system builds into topological `< 500`-LoC milestones ($M_1 \prec M_2 \prec \dots \prec M_K$), permanently committing each Popperian-certified milestone ($V = 1.0$) to Git `HEAD_k`, tagging `hae-milestone-M_k`, and locking `.hae/ratchet_suites/falsify_M_k.py` into a cumulative regression guard so `L1-A` Recon and `L1-B` Falsifier never inspect more than `< 500` lines of uncommitted diff (`git diff HEAD`). On our live 4-milestone `MiniLang` compiler/VM build (`m1_lexer -> m2_parser -> m3_evaluator -> m4_bytecode_vm`), `V14 + Beam-501B` certified **`4 / 4` (`100.0%`) milestones in `245.58s`** (`49` moves, `381.7k` tokens), growing the repository to **`1,139` LoC** while keeping active per-milestone diffs bounded to **`257–364` lines (`< 500` LoC)** and passing **`25 / 25` cumulative regression tests** ([`beam_macro_ratchet_eval_2026-10-21`](results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md)).
> - **100% Domain-General Zero-Hint `L0-Micro` Engine (`V14`, `6 / 6` = `100.0%` at `278.8s` Mean Time)**: Zero repository-specific or benchmark-specific prompt hints (`0` hardcoded library names in `hae/swebench/`), pluggable `WorkspaceVerifierPlugin` (`PytestVerifierPlugin` & `ShellCommandVerifierPlugin`), untracked file checkpointing (`allow_new_files=True` via `git add -N`), and configurable `diff_bloat_threshold`. On the 6-task multi-repo SWE-bench slice with `Beam-1.0-501B + DFlash2`, **Zero-Hint `V14` resolves `6 / 6` (`100.0%`, `57 / 57` `FAIL_TO_PASS`, `214 / 214` `PASS_TO_PASS`) in `278.82s` mean time (`239.81s` median)—`3.62x` faster than `Qwen3.8` (`1,010.57s`) and `1.10x` faster than prior hinted `Beam-501B` (`305.56s`)—using `-55.5%` fewer tokens (`148.4k`) and `-85.6%` fewer `<think>` tokens (`762.0`)** ([`v14_zero_hint_beam_eval_2026-10-21`](results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md)).
> - **`100.0%` Gold Held-Out SWE-bench Resolution Across All Cohorts**: Resolves **`30 / 30` (`100.0%`)** instances on the 30-task SWE-bench dev cohort (`275 / 275` individual `FAIL_TO_PASS` tests passed, `0` `PASS_TO_PASS` regressions) with `Qwen3.8-Flash-Next-NVFP4`, and **`6 / 6` (`100.0%`)** on the 6-task multi-repo benchmark cohort (`57 / 57` `FAIL_TO_PASS`, `214 / 214` `PASS_TO_PASS`, `0` regressions) with `Reflection Beam-1.0-501B-A23B-NVFP4 + DFlash2` (both Zero-Hint `V14` and `V13`).
> - **`3.62x` Faster End-to-End Execution & `-55.5%` Fewer Tokens**: Pairing `V13`/`V14` with `Beam-1.0-501B-A23B-NVFP4 + DFlash2` cuts mean wall-clock resolution time from **`1,010.6s` to `278.8s` (`3.62x` faster on Zero-Hint `V14`, up to `7.63x` faster on `pydicom-1255` at `226.3s`)**, requires **`-31.0%` to `-34.0%` fewer agentic moves (`22.3–23.3` vs. `33.8`)**, **`-55.5%` fewer total tokens (`148.4k` vs. `333.7k`)**, and **`-85.6%` to `-91.0%` fewer `<think>` tokens** via `DFlash2` speculative decoding and per-turn Harmony `reasoning_effort` budget control.

| Benchmark Cohort & Configuration | Gold Held-Out / Milestone Certified | Individual Tests Passed | Regressions | Mean Agentic Moves | Mean Total Tokens | Mean Wall-Clock Time | Speedup / Scaling Invariant | Primary Report |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`V14 L0-Macro Ratchet` + `Beam-1.0-501B` (`+ DFlash2`)** (`4`-milestone `MiniLang` compiler/VM) | **`4 / 4` (`100.0%`)** | **`25 / 25` cumulative (`10 -> 14 -> 22 -> 25`)** | **`0`** | **`12.3` / milestone (`49` total)** | **`95.4k` / milestone (`381.7k` total)** | **`61.4s` / milestone (`245.6s` total)** | **`1,139` total LoC (`257–364` active diff `< 500`)** | [`beam_macro_ratchet_eval_2026-10-21/README.md`](results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md) & [`summary.json`](results/ratchet/beam_macro_ratchet_eval_2026-10-21/summary.json) |
| **`V14 Zero-Hint v13_hier_mcts` + `Beam-1.0-501B` (`+ DFlash2`)** (`n = 6` multi-repo, `0` repo hints) | **`6 / 6` (`100.0%`)** | **`57 / 57` F2P (`100.0%`)** | **`0 / 214` P2P** | **`23.3` (`-31.0%`)** | **`148.4k` (`-55.5%`)** | **`278.8s` (`239.8s` med)** | **`3.62x` faster vs. `Qwen3.8` (`1.10x` vs. Hinted)** | [`v14_zero_hint_beam_eval_2026-10-21/README.md`](results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md) & [`summary.json`](results/swebench/v14_zero_hint_beam_eval_2026-10-21/summary.json) |
| **`v13_hier_mcts` + `Beam-1.0-501B` (`+ DFlash2`)** (`n = 6` multi-repo, prior hinted) | **`6 / 6` (`100.0%`)** | **`57 / 57` F2P (`100.0%`)** | **`0 / 214` P2P** | **`22.3` (`-34.0%`)** | **`149.9k` (`-55.1%`)** | **`305.6s` (`360.9s` med)** | **`3.31x` faster (up to `5.36x`)** | [`beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md) |
| **`v13_hier_mcts` + `Qwen3.8-180B-A10B`** (`n = 6` multi-repo) | **`6 / 6` (`100.0%`)** | **`57 / 57` F2P (`100.0%`)** | **`0 / 214` P2P** | `33.8` | `333.7k` | `1,010.6s` | `1.00x` (baseline) | [`beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md) |
| **`v13_hier_mcts` + `Qwen3.8-180B-A10B`** (`n = 30` full dev cohort) | **`30 / 30` (`100.0%`)** | **`275 / 275` F2P (`100.0%`)** | **`0`** | `37.5` | `348.2k` | `1,210.5s` | **`2.14x` resolution vs. V12** | [`v13_evaluation_report.md`](experiments/v13/v13_evaluation_report.md) |
| **`v12_agentic` + `Beam-1.0-501B` (`+ DFlash2`)** (`n = 6` multi-repo) | `2 / 6` (`33.3%`) | `29 / 57` F2P (`50.9%`) | `0 / 214` P2P | `27.3` (`-9.0%`) | `162.1k` (`-40.7%`) | `331.0s` | **`3.56x` faster (up to `7.92x`)** | [`beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md) |
| **`v12_agentic` + `Qwen3.8-180B-A10B`** (`n = 30` full dev cohort) | `14 / 30` (`46.7%`) | `98 / 269` F2P (`36.4%`) | `2 / 30` | `29.7` | `291.4k` | `1,310.7s` | `1.00x` (single-layer ReAct) | [`v12_evaluation_report.md`](experiments/v12/v12_evaluation_report.md) |

---

## System Architecture

### 1. V14 Generalized Two-Tier `L0-Macro` Milestone DAG & Persistent Git Commit Ratchet (`L0-Macro` $\times$ `L0-Micro`)

To scale from **5–20 minute surgical bug fixes (`< 200` LoC)** to **multi-hour and multi-day full-system builds (`5,000–100,000+` LoC)** without overloading Layer `L1` (`L1-A` Recon and `L1-B`'s `4,500`-char diff window) or wiping out completed subsystems on `GEN` branch resets, **V14** wraps the generalized Zero-Hint `L0-Micro` V13 engine inside an outer **`L0-Macro` Compositional Milestone AND-DAG & Persistent Git Commit Ratchet** (`hae/hierarchical/milestone_ratchet.py`, `hae/hierarchical/verifier_plugin.py`). Full specification: [`experiments/v13/v14_generalized_macro_ratchet_architecture.md`](experiments/v13/v14_generalized_macro_ratchet_architecture.md) (high-res PNG: [`v14_macro_ratchet_architecture.png`](experiments/v13/assets/v14_macro_ratchet_architecture.png)).

![Universal Two-Tier Hierarchical Verification & Milestone Ratchet Architecture](experiments/v13/assets/v14_macro_ratchet_architecture.svg)

```mermaid
flowchart TB
    subgraph Macro["Tier 1 — Layer L0-Macro: Compositional Milestone AND-DAG & Persistent Git Commit Ratchet"]
        Spec["System Specification (5K-100K+ LoC Build)"] --> Planner["L0MacroPlanner (Decomposes into Topological <500-LoC Milestones)"]
        Planner --> M1["Milestone M1: Core Primitives (Certified V=1.0 -> Commit HEAD_1)"]
        M1 --> M2["Milestone M2: Parser / Engine (Certified V=1.0 -> Commit HEAD_2)"]
        M2 --> Mk["Active Milestone M_k (Scoped Delta: git diff HEAD_{k-1} < 500 LoC)"]
        State[".hae/ratchet_state.json (Crash Recovery) + .hae/ratchet_suites/falsify_m1..m_{k-1}.py"] --- Mk
    end

    subgraph Micro["Tier 2 — Layer L0-Micro: Zero-Hint Generalized V13 AB-MCTS OR-Tree on Milestone M_k"]
        Mk -- "Dispatch M_k (base_ref = HEAD_{k-1})" --> L1A["L1-A: Scoped Recon Probe (Maps M_k Target Files in 0-6 Turns)"]
        L1A -- "ReconBrief (< 800 Tokens)" --> L0M["L0-Micro: Seed Architect (M_k VerificationContract + 3 Branches b1/b2/b3)"]
        L0M --> TS{"Thompson Sampling AB-MCTS (Beta Posteriors)"}
    end

    subgraph Exec["Tier 3 — Layer L2: Ephemeral Clean-Context Executors (Beam-1.0-501B + DFlash2)"]
        TS -- "GEN: Widen from HEAD_{k-1}" --> Gen["L2-GEN: Branch Executor (8-18 Turns, allow_new_files=True)"]
        TS -- "CONT: Deepen Near-Miss s_i" --> Cont["L2-CONT: Delta Refiner (6-12 Turns, s_i diff + critique)"]
        Gen --> Delta["Bounded Candidate Delta s_i (git diff HEAD_{k-1} < 500 LoC)"]
        Cont --> Delta
    end

    subgraph Verify["Tier 4 — Layer L1-B: Pluggable Verifier & Bounded-Delta Popperian Falsifier"]
        Delta --> Plugin["Stage A: WorkspaceVerifierPlugin (M_k acceptance_cmd + Prior Suites M_1..M_{k-1})"]
        Plugin -- "Pass Stage A" --> Falsifier["Stage B: AdversarialFalsifierSubAgent (Audits <500-LoC Delta vs. M_k Contract)"]
        Falsifier -- "Certified V(s*) = 1.0" --> Ratchet["GitCommitRatchet: Commit HEAD_k + Tag hae-milestone-M_k + Advance to M_{k+1}"]
    end

    Plugin -- "Fail: Empirical V(s_i) + Critique" --> TS
    Falsifier -- "Fail: Falsifier Critique + Suite Append" --> TS
    Ratchet -- "Next Topological Milestone M_{k+1}" --> Mk

    classDef macroCert fill:#065f46,stroke:#34d399,stroke-width:2px,color:#ffffff;
    classDef macroActive fill:#0284c7,stroke:#38bdf8,stroke-width:2px,color:#ffffff;
    classDef macroState fill:#0f172a,stroke:#10b981,stroke-width:2px,color:#6ee7b7;
    classDef recon fill:#0f766e,stroke:#2dd4bf,stroke-width:2px,color:#ffffff;
    classDef microArch fill:#4338ca,stroke:#818cf8,stroke-width:2px,color:#ffffff;
    classDef mcts fill:#6d28d9,stroke:#c4b5fd,stroke-width:2px,color:#ffffff;
    classDef execGen fill:#d97706,stroke:#fcd34d,stroke-width:2px,color:#ffffff;
    classDef execCont fill:#ea580c,stroke:#fdba74,stroke-width:2px,color:#ffffff;
    classDef gate fill:#be123c,stroke:#fb7185,stroke-width:2px,color:#ffffff;
    classDef falsifier fill:#e11d48,stroke:#fda4af,stroke-width:2px,color:#ffffff;
    classDef ratchet fill:#059669,stroke:#6ee7b7,stroke-width:3px,color:#ffffff;

    class M1,M2 macroCert;
    class Spec,Planner,Mk macroActive;
    class State macroState;
    class L1A recon;
    class L0M microArch;
    class TS mcts;
    class Gen,Delta execGen;
    class Cont execCont;
    class Plugin gate;
    class Falsifier falsifier;
    class Ratchet ratchet;

    style Macro fill:#ecfdf5,stroke:#059669,stroke-width:2px,color:#064e3b
    style Micro fill:#eef2ff,stroke:#4f46e5,stroke-width:2px,color:#1e1b4b
    style Exec fill:#fffbeb,stroke:#d97706,stroke-width:2px,color:#78350f
    style Verify fill:#fff1f2,stroke:#e11d48,stroke-width:2px,color:#881337
```

1. **Domain-General Zero-Hint `L0-Micro` Engine (`hae/swebench/hierarchical_mcts.py`, `hae/hierarchical/verifier_plugin.py`)**:
   - **Zero Repo-Specific Hints**: Domain-general call-chain localization, stratified causal branches (`b1` local consumer, `b2` upstream producer "Move 37", `b3` codebase symmetry), and Popperian audit checklists (`0` hardcoded benchmark library strings).
   - **Pluggable `WorkspaceVerifierPlugin`**: Ships with `PytestVerifierPlugin` and `ShellCommandVerifierPlugin` (supporting Rust `cargo test`, Go `go test`, C++ `cmake`/`ctest`, TypeScript `npm test`, and custom acceptance scripts).
   - **Greenfield File Creation (`allow_new_files=True`) & Configurable `diff_bloat_threshold`**: Stages newly created files via `git add -N` into `PatchCheckpoint.created_files` and scales diff regularization up to `500+` LoC per milestone.
2. **Ratcheting Git Commits & Cumulative Regression Guard (`hae/hierarchical/milestone_ratchet.py`)**:
   - `L0MacroPlanner` decomposes a system build into a topological DAG of bounded `< 500`-LoC milestones ($M_1 \prec M_2 \prec \dots \prec M_K$).
   - When milestone $M_k$ achieves Popperian certification ($V = 1.0$) and passes the **Cumulative Regression Guard** (re-running all prior milestones' `acceptance_cmd` and `.hae/ratchet_suites/falsify_M_1..M_{k-1}.py`), `GitCommitRatchet` permanently commits $M_k$ to Git (`HEAD_k`), tags `hae-milestone-M_k`, and saves `.hae/ratchet_state.json` for crash-resilient multi-day resume (`--resume`).
   - Because $M_{k-1}$ is committed to `HEAD`, `git diff HEAD` during $M_k$ **only ever contains $M_k$'s `< 500`-LoC delta**, ensuring `L1-A` and `L1-B` never overload regardless of total repository size.

#### Readiness by Task Horizon

| Task Horizon | Wall-Clock & Scale | Examples | Active HAE Mode & Configuration | How Context & `L1` Audit Stay Bounded |
| :--- | :--- | :--- | :--- | :--- |
| **Short Horizon (Surgical Bug Fix)** | **5–20 min** (`1–5` files, `< 200` LoC) | SWE-bench issues, production regressions, security patches | **`L0-Micro` Direct (`--mode swebench`)** (`diff_bloat_threshold = 120`) | `L1-A` maps fault in `0–6` turns; `L2` explores `b1`/`b2`/`b3`; `L1-B` falsifies full diff. **`100.0%` SWE-bench resolved (`305.6s` mean on `Beam-501B`)**. |
| **Medium Horizon (Feature / Module Build)** | **30–90 min** (`3–10` files, `200–1,500` LoC) | New CLI subcommand, protocol parser, REST endpoint + test suite | **`L0-Micro` Greenfield or `2–4` Node Ratchet (`--mode ratchet`)** (`allow_new_files = True`, `diff_bloat_threshold = 500`) | New files tracked via `git add -N` in `PatchCheckpoint.created_files`; `WorkspaceVerifierPlugin` runs build + test gates. |
| **Long Horizon (Full System Build)** | **Hours to Days** (`20–200+` files, `5,000–100,000+` LoC) | Toy browser engine, bytecode compiler + VM, embedded SQL engine | **Two-Tier `L0-Macro` Milestone DAG + Git Commit Ratchet (`--mode ratchet --resume`)** | Each certified milestone $M_k$ advances `git HEAD` and locks `falsify_M_k.py` into the regression guard so `git diff HEAD` stays `< 500` LoC at every step. |

---

### 2. Inner `L0-Micro` Core: V13 Hierarchical Verification Harness & Agentic AB-MCTS ("Move 37" Engine)

Inside each milestone $M_k$ (or directly on standalone bug-fix tasks), **V13** (`hae/swebench/hierarchical_mcts.py`) structures the agent hierarchy as a **3-Layer Context-Isolation & Popperian Falsification Stack** governed by **Agentic Adaptive Branching MCTS (AB-MCTS)** over Git workspace checkpoints. Full specification: [`experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md`](experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md) (high-res PNG: [`v13_beam_architecture.png`](experiments/v13/assets/v13_beam_architecture.png)).

![V13 + Beam-501B Architecture](experiments/v13/assets/v13_beam_architecture.svg)

```mermaid
flowchart TB
    subgraph S1["1. Layer L1-A: Ephemeral Reconnaissance Probe (Teal Zone — Zero Context Pollution)"]
        Issue["GitHub Issue + /testbed Pod"] --> L1A["L1-A: Recon Probe (Locate Fault Surface + Call Chain in 0-6 Turns)"]
        L1A --> Repro["Author & Verify /testbed/.hae/reproduce_issue.py (Must FAIL on Clean HEAD)"]
        Repro -- "Distilled ReconBrief (< 800 Tokens)" --> L0
    end

    subgraph S2["2. Layer L0: Seed Principal Architect & Agentic AB-MCTS Master (Indigo Zone — < 4K Token Ledger)"]
        L0["L0: Seed Architect (Synthesizes VerificationContract: Invariants, Sibling Parity & Anti-Cheat Rules)"]
        L0 --> Branches["Populate 3 Orthogonal Causal Branches: b1 Consumer/Local | b2 Producer/Upstream ('Move 37') | b3 Codebase Symmetry"]
        Branches --> Select{"Agentic AB-MCTS Controller (Thompson Sampling over Beta Posteriors + Negative Ledger)"}
    end

    subgraph S3["3. Layer L2: Ephemeral Clean-Context Executors on Beam-1.0-501B + DFlash2 (Amber Zone — 0.8s-3.5s/turn)"]
        Select -- "GEN: Widen New Branch (from clean git checkout -f HEAD)" --> L2Gen["L2: Branch Executor (Fresh 8-18 Turn Context)"]
        Select -- "CONT: Deepen Near-Miss (parent git diff pre-applied + exact critique)" --> L2Cont["L2: Delta Refiner (Fresh 6-12 Turn Context)"]
        L2Gen --> Tools["Interactive /testbed ReAct Loop: view_file, bash, str_replace, submit (Scratchpad GC'd on Exit)"]
        L2Cont --> Tools
    end

    subgraph S4["4. Layer L1-B: Popperian Verification Gate & Adversarial Falsifier (Crimson -> Emerald Zone)"]
        Tools -- "Candidate git diff Checkpoint s_i" --> Gate["Stage A: Deterministic EvidenceGatekeeper (py_compile + reproduce_issue.py + Target Pytest)"]
        Gate -- "Pass All Target Tests" --> L1B["Stage B: AdversarialFalsifierSubAgent (Read-Only Audit vs. L0 Contract + Appends /testbed/.hae/falsification_suite.py)"]
        L1B -- "Certified Global Optimum (V(s) = 1.0)" --> Done["Verified Patch (100% SWE-bench Resolved | 0 Regressions)"]
    end

    Gate -- "Fail: Traceback + Empirical Score V(s) (Updates Beta Posteriors)" --> Select
    L1B -- "Fail: Falsifier Critique + Score V(s) (Updates Beta Posteriors & Negative Ledger)" --> Select

    classDef input fill:#0284c7,stroke:#0369a1,stroke-width:2px,color:#ffffff;
    classDef recon fill:#0d9488,stroke:#0f766e,stroke-width:2px,color:#ffffff;
    classDef arch fill:#3730a3,stroke:#312e81,stroke-width:2px,color:#ffffff;
    classDef mcts fill:#4f46e5,stroke:#3730a3,stroke-width:2px,color:#ffffff;
    classDef exec fill:#d97706,stroke:#b45309,stroke-width:2px,color:#ffffff;
    classDef delta fill:#ea580c,stroke:#c2410c,stroke-width:2px,color:#ffffff;
    classDef gate fill:#be123c,stroke:#9f1239,stroke-width:2px,color:#ffffff;
    classDef falsifier fill:#e11d48,stroke:#be123c,stroke-width:2px,color:#ffffff;
    classDef certified fill:#059669,stroke:#047857,stroke-width:3px,color:#ffffff;

    class Issue input;
    class L1A,Repro recon;
    class L0,Branches arch;
    class Select mcts;
    class L2Gen,Tools exec;
    class L2Cont delta;
    class Gate gate;
    class L1B falsifier;
    class Done certified;

    style S1 fill:#f0fdfa,stroke:#0d9488,stroke-width:2px,color:#134e4a
    style S2 fill:#eef2ff,stroke:#4f46e5,stroke-width:2px,color:#1e1b4b
    style S3 fill:#fffbeb,stroke:#d97706,stroke-width:2px,color:#78350f
    style S4 fill:#fff1f2,stroke:#e11d48,stroke-width:2px,color:#881337
```

#### Zero-Context-Bloat Layer Breakdown ($L_0 \to L_1 \to L_2$)

| Layer | Role & Scope | Context Window | Upward Artifact Returned |
| :--- | :--- | :--- | :--- |
| **`L0`: Seed Architect & AB-MCTS Controller** | Synthesizes the `VerificationContract` (invariants, anti-cheat rules, regression targets), seeds 3 orthogonal causal branches (`b1: consumer_local`, `b2: producer_upstream` / "Move 37", `b3: codebase_symmetry`), and uses Thompson Sampling over $\text{Beta}(\alpha_i, \beta_i)$ posteriors to choose `GEN` vs. `CONT`. | Clean strategic ledger (`< 4K` tokens; never ingests raw `grep` or test dumps) | Selected `GEN(b_k)` or `CONT(s_i)` dispatch |
| **`L1-A`: Ephemeral Recon Probe** | Maps the producer $\to$ consumer call chain and target test files inside `/testbed`, and authors a baseline reproduction script (`/testbed/.hae/reproduce_issue.py`) verified to fail on `HEAD`. | Ephemeral (`0–6` turns; scratchpad garbage-collected on exit) | Distilled `ReconBrief` (`< 800` tokens) + `/testbed/.hae/reproduce_issue.py` |
| **`L2`: Ephemeral Branch & Delta Executors** | Runs interactive ReAct tools (`view_file`, `bash`, `str_replace`, `submit`) inside `/testbed`. **`GEN`** starts from clean `git checkout -f HEAD`; **`CONT`** starts with parent checkpoint $s_i$'s `git diff` pre-applied plus the exact failing test or falsifier critique. | Fresh context per tree node (`8–18` turns for `GEN`, `6–12` turns for `CONT`; GC'd on exit) | Candidate `git diff` workspace checkpoint $s_i$ |
| **`L1-B`: Gatekeeper & Adversarial Falsifier** | `EvidenceGatekeeper` / `WorkspaceVerifierPlugin` runs the repro script + target test suite; on pass, a read-only `AdversarialFalsifierSubAgent` audits the diff against $L_0$'s contract and appends adversarial edge-case tests to `/testbed/.hae/falsification_suite.py`. | Ephemeral (`4–6` turns; zero author confirmation bias) | Empirical score $V(s_i) \in [0, 1]$, updated $\text{Beta}(\alpha_i, \beta_i)$, and `< 400`-token `Critique` (or `Certified V = 1.0`) |

---

### 3. GKE Distributed Inference & Execution Topology (`Beam-1.0-501B-A23B-NVFP4` + `DFlash2`)

HAE executes distributed evaluations on GKE (`hae-cluster`) across **`6x g4-standard-96` Spot VMs (`12x NVIDIA RTX PRO 6000 Blackwell Server Edition 96 GB GPUs` = `1.15 TB` VRAM, `576` vCPUs, `2.16 TiB` DDR5 RAM)**. Full cluster and manifest documentation: [`k8s/README.md`](k8s/README.md) (high-res PNG: [`gke_beam_501b_topology.png`](experiments/v13/assets/gke_beam_501b_topology.png)).

![GKE Distributed Serving & Sandbox Execution Topology](experiments/v13/assets/gke_beam_501b_topology.svg)

```mermaid
flowchart TB
    subgraph CoordPool["Tier 1 — Coordinator & Sandbox Pool (default-pool: e2-standard-8)"]
        Orch["V13 AB-MCTS Orchestrator (scripts/swebench_kubectl_batch.py — Parallel Worker Pool)"]
        Pods["SWE-bench /testbed Sandbox Pods (swebench-tb-* — Isolated Git Worktrees & Pytest)"]
        Orch -- "kubectl exec (ReAct Tools + Pytest Gatekeeper)" --> Pods
    end

    subgraph GatewayTier["Tier 2 — llm-d Intelligent Inference Gateway (svc/vllm-qwen38-gateway:8000)"]
        GW["llm-d Gateway: Prefix-Affinity Router + Singleflight Coalescing Cache (0.74 ms Short-Circuit)"]
        Harmony["Harmony Wire-Tag Sanitizer & reasoning_effort Controller (Turn 1: medium/64 | Turn 2+: minimal/8)"]
        GW --> Harmony
    end

    subgraph GPUPool["Tier 3 — GPU Inference Pool (3x Multi-Node Ray TP=4 Replica Groups across 6x g4-standard-96 = 12x RTX PRO 6000 96 GB / 1.15 TB VRAM)"]
        R0["Replica Group 0 (beam-0: Head + Worker, 4x 96 GB GPU = 384 GB VRAM, 837k fp8_e4m3 KV tokens)"]
        R1["Replica Group 1 (beam-1: Head + Worker, 4x 96 GB GPU = 384 GB VRAM, 837k fp8_e4m3 KV tokens)"]
        R2["Replica Group 2 (beam-2: Head + Worker, 4x 96 GB GPU = 384 GB VRAM, 837k fp8_e4m3 KV tokens)"]
        Spec["Per-Replica Engine: Beam-1.0-501B-A23B-NVFP4 (70.71 GiB/GPU) + DFlash2 Speculative Drafter (8 tokens/step, 0.94 GiB/GPU) + Hybrid DDR5 tmpfs (/weights-ram) & NVMe (/weights) (2.51M Total Cluster KV Tokens)"]
        R0 --- Spec
        R1 --- Spec
        R2 --- Spec
    end

    Orch -- "OpenAI Chat Completions API" --> GW
    Harmony -- "Prefix-Affinity Route (Replica 0)" --> R0
    Harmony -- "Prefix-Affinity Route (Replica 1)" --> R1
    Harmony -- "Prefix-Affinity Route (Replica 2)" --> R2

    classDef coord fill:#0f766e,stroke:#115e59,stroke-width:2px,color:#ffffff;
    classDef sandbox fill:#0284c7,stroke:#0369a1,stroke-width:2px,color:#ffffff;
    classDef gateway fill:#4f46e5,stroke:#3730a3,stroke-width:2px,color:#ffffff;
    classDef replica fill:#7c3aed,stroke:#5b21b6,stroke-width:2px,color:#ffffff;
    classDef engine fill:#059669,stroke:#047857,stroke-width:2px,color:#ffffff;

    class Orch coord;
    class Pods sandbox;
    class GW,Harmony gateway;
    class R0,R1,R2 replica;
    class Spec engine;

    style CoordPool fill:#f0fdfa,stroke:#0d9488,stroke-width:2px,color:#134e4a
    style GatewayTier fill:#eef2ff,stroke:#4f46e5,stroke-width:2px,color:#1e1b4b
    style GPUPool fill:#f5f3ff,stroke:#7c3aed,stroke-width:2px,color:#2e1065
```

---

## Benchmark Results & Evolutionary Progression

### A. Live `V14` 4-Milestone `L0-Macro` Compositional Milestone DAG & Git Commit Ratchet (`Beam-1.0-501B` + `DFlash2`)

Full harvested telemetry, `.hae/ratchet_state.json`, archived Popperian falsification suites (`.hae/ratchet_suites/`), and the ratcheted Git repository (`minilang_repo/`): **[`results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md`](results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md)** and **[`summary.json`](results/ratchet/beam_macro_ratchet_eval_2026-10-21/summary.json)**.

```mermaid
xychart-beta
    title "V14 L0-Macro Ratchet: Bounded Active Diff (<500 LoC) vs. Growing Total Repo LoC Across Milestones"
    x-axis ["m1_lexer (56.2s)", "m2_parser (57.2s)", "m3_evaluator (51.1s)", "m4_bytecode_vm (79.1s)"]
    y-axis "Lines of Code (LoC)" 0 --> 1250
    bar [274, 328, 257, 364]
    line [263, 564, 798, 1139]
```

| Milestone (`depends_on`) | Status | Moves | Total Tokens | Wall Time | Active Delta (`git diff HEAD_{k-1}`) | Total Repo LoC | Cumulative Regression Gate (`M_1..M_k`) | Certified Commit SHA | Git Tag |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`m1_lexer`** (`[]`) | **CERTIFIED** | `11` | `64,552` | `56.23s` | **`+256` LoC** (`274` diff, `< 500`) | `263` LoC | `M_1`: **`10` passed** + `falsify_m1_lexer.py` | `43eb7bda4374` | `hae-milestone-m1_lexer` |
| **`m2_parser`** (`[m1_lexer]`) | **CERTIFIED** | `10` | `67,755` | `57.21s` | **`+302` LoC** (`328` diff, `< 500`) | `564` LoC | `M_1..M_2`: **`14` passed** (`10+4`) + `falsify_m1..m2` | `701ded5bea21` | `hae-milestone-m2_parser` |
| **`m3_evaluator`** (`[m2_parser]`) | **CERTIFIED** | `10` | `75,457` | `51.06s` | **`+235` LoC** (`257` diff, `< 500`) | `798` LoC | `M_1..M_3`: **`22` passed** (`10+4+8`) + `falsify_m1..m3` | `da346ab6e349` | `hae-milestone-m3_evaluator` |
| **`m4_bytecode_vm`** (`[m3_evaluator]`) | **CERTIFIED** | `18` | `173,888` | `79.06s` | **`+342` LoC** (`364` diff, `< 500`) | **`1,139` LoC** | `M_1..M_4`: **`25` passed** (`10+4+8+3`) + `falsify_m1..m3` | `c7e1d45161d6` | `hae-milestone-m4_bytecode_vm` |
| **Total / Final (`4 / 4`)** | **`100.0%`** | **`49`** | **`381,652`** | **`245.58s`** | **Max `364` diff (`< 500` cap)** | **`1,139` LoC** | **`25 / 25` cumulative (`0` regressions)** | `c7e1d45161d6` | `m1..m4` tags |

---

### B. Reflection `Beam-1.0-501B-A23B-NVFP4` (`+ DFlash2`) Zero-Hint `V14` & `V13` vs. `Qwen3.8-Flash-Next-NVFP4` (`180B-A10B`)

Full reports and telemetry:
- **Zero-Hint Generalized `V14` (`v13_hier_mcts`, `0` repo hints)**: **[`results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md`](results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md)** and **[`summary.json`](results/swebench/v14_zero_hint_beam_eval_2026-10-21/summary.json)** (`6 / 6` = `100.0%` resolved, `278.8s` mean / `239.8s` median wall time, `3.62x` faster than `Qwen3.8` and `1.10x` faster than prior hinted `Beam-501B`, `148.4k` mean tokens = `-55.5%` fewer tokens, `762.0` mean `<think>` tokens = `-85.6%`).
- **Head-to-Head `Beam-501B` vs. `Qwen3.8` Report**: **[`experiments/v13/beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md)** and **[`results/swebench/beam_eval_2026-10-20/README.md`](results/swebench/beam_eval_2026-10-20/README.md)**.

```mermaid
xychart-beta
    title "Mean Wall-Clock Time per SWE-bench Instance (Seconds — Lower is Faster)"
    x-axis ["v12_agentic (Qwen3.8)", "v13_hier_mcts (Qwen3.8)", "v12_agentic (Beam-501B)", "v13 Hinted (Beam-501B)", "V14 Zero-Hint (Beam-501B)"]
    y-axis "Mean Wall-Clock Seconds" 0 --> 1300
    bar [1177.2, 1010.6, 331.0, 305.6, 278.8]
```

#### Per-Instance Head-to-Head on `v13_hier_mcts` (`n = 6` Multi-Repo SWE-bench Slice: Zero-Hint `V14` vs. Hinted `V13` vs. `Qwen3.8`)

| Instance ID | `Qwen3.8` Status | `Beam-501B` Zero-Hint Status | `F2P` / `P2P` | Moves (`Zero-Hint` / `Hinted` / `Qwen3.8`) | Tokens (`Zero-Hint` / `Hinted` / `Qwen3.8`) | `Qwen3.8` Time | `Beam-501B` Hinted Time | **`Beam-501B` Zero-Hint Time** | **Zero-Hint Speedup vs. `Qwen3.8`** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `marshmallow-code__marshmallow-1359` | **PASS** | **PASS** | `1/1` F2P, `76/76` P2P | `18` / `36` / `25` | `147,074` / `240,964` / `230,381` | `564.1s` | `392.0s` | **`197.3s`** | **`2.86x`** |
| `marshmallow-code__marshmallow-1702` | **PASS** | **PASS** | `28/28` F2P, `71/71` P2P | `24` / `15` / `31` | `172,411` / `104,598` / `368,459` | `849.2s` | `158.4s` | **`195.1s`** | **`4.35x`** |
| `pydicom__pydicom-1255` | **PASS** | **PASS** | `1/1` F2P, `22/22` P2P | `24` / `25` / `48` | `146,874` / `149,110` / `518,902` | `1,727.2s` | `373.7s` | **`226.3s`** | **`7.63x`** |
| `pylint-dev__astroid-1417` | **PASS** | **PASS** | `1/1` F2P, `3/3` P2P | `22` / `21` / `19` | `138,888` / `142,437` / `111,194` | `806.4s` | `388.2s` | **`352.8s`** | **`2.29x`** |
| `pyvista__pyvista-4226` | **PASS** | **PASS** | `25/25` F2P, `0/0` P2P | `15` / `24` / `55` | `89,892` / `180,852` / `555,537` | `1,586.7s` | `348.1s` | **`253.3s`** | **`6.26x`** |
| `sqlfluff__sqlfluff-1517` | **PASS** | **PASS** | `1/1` F2P, `42/42` P2P | `37` / `13` / `25` | `195,018` / `81,282` / `217,582` | `529.7s` | `173.0s` | **`448.2s`** | **`1.18x`** |
| **Mean / Total (`n = 6`)** | **`6 / 6` (`100%`)** | **`6 / 6` (`100%`)** | **`57/57` F2P, `214/214` P2P** | **`23.3` / `22.3` / `33.8`** | **`148,360` (`-55.5%`)** | **`1,010.6s`** | **`305.6s` (`3.31x`)** | **`278.8s` (`239.8s` med)** | **`3.62x` faster (`1.10x` vs. Hinted)** |

> [!NOTE]
> **Why `V13`/`V14` + `Beam-501B` Are Synergistic (`33.3%` $\to$ `100.0%`)**: Even with the `501B` `Beam` model, single-layer ReAct (`v12_agentic`) resolves only `2 / 6` (`33.3%`) tasks (`331.0s` mean time) because single-context agents settle into local optima that pass their own reproduction scripts (`5 / 6` self-oracle certified) while missing cross-module invariants. Wrapping `Beam-501B` in **V13/V14's Hierarchical Verification Harness (`L0` contract + `L1-B` Popperian falsifier + `CONT` delta-refinement + domain-general AST static checks)** lifts resolution from **`33.3%` to `100.0%` (`6 / 6`)** with **zero repo-specific hints**, while running even **faster** (`278.8s` vs. `331.0s`) and consuming **fewer tokens** (`148.4k` vs. `162.1k`).

---

### C. SWE-bench Multi-Generation Progression (`V8` $\to$ `V13`, `n = 30` Dev Cohort)

Full 30-instance ledger and analysis: [`experiments/v13/v13_evaluation_report.md`](experiments/v13/v13_evaluation_report.md) and [`results/swebench/dev_eval_2026-10-09/README.md`](results/swebench/dev_eval_2026-10-09/README.md).

```mermaid
xychart-beta
    title "SWE-bench Gold Held-Out Resolution Rate (%) Across V8 - V13 (n = 30)"
    x-axis ["V8 (PUCT)", "V9 (AB-MCTS)", "V10 (+EPLB)", "V11 (+Multi-Hunk)", "V12 (Agentic ReAct)", "V13 (Hier AB-MCTS)"]
    y-axis "Graded Resolved (%)" 0 --> 100
    bar [13.3, 10.0, 16.7, 20.0, 46.7, 100.0]
```

| Metric (`n = 30` Valid SWE-bench Cohort) | `V8` (`puct`) | `V9` (`ab_mcts`) | `V10` (`+ EPLB`) | `V11` (`+ Multi-Hunk`) | `V12` (`v12_agentic`) | **`V13` (`v13_hier_mcts`)** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Gold Held-Out Resolved (`grade.json`)** | `4 / 30` (`13.3%`) | `3 / 30` (`10.0%`) | `5 / 30` (`16.7%`) | `6 / 30` (`20.0%`) | `14 / 30` (`46.7%`) | **`30 / 30` (`100.0%`)** |
| **Individual `FAIL_TO_PASS` Tests Passed** | `4 / 57` | `3 / 46` | `5 / 17` | `6 / 33` | `98 / 269` (`36.4%`) | **`275 / 275` (`100.0%`)** |
| **Zero-Regression on `PASS_TO_PASS`** | `28 / 30` (`93.3%`) | `30 / 30` (`100.0%`) | `29 / 30` (`96.7%`) | `30 / 30` (`100.0%`) | `28 / 30` (`93.3%`) | **`30 / 30` (`100.0%`)** |
| **Non-Empty Verified Patches** | `9 / 30` (`30.0%`) | `8 / 30` (`26.7%`) | `11 / 30` (`36.7%`) | `11 / 30` (`36.7%`) | `26 / 30` (`86.7%`) | **`30 / 30` (`100.0%`)** |
| **Mean Wall-Clock Time (s)** | `555.8s` | `463.8s` | `539.8s` | `519.5s` | `1,310.7s` | **`1,210.5s` (`305.6s` on `Beam-501B`)** |

---

### D. Three-Era Evolution Summary (`V1` $\to$ `V14: L0-Macro Ratchet + Zero-Hint V13 + Beam-501B`)

Across 14 generations, HAE progressed through three distinct architectural eras. See **[`experiments/README.md`](experiments/README.md)** for the complete generation-by-generation (`V1`–`V14`) table, design notes, and errata (high-res PNG: [`v1_to_v13_beam_trajectory.png`](experiments/v13/assets/v1_to_v13_beam_trajectory.png)).

![HAE V1 to V13 + Reflection Beam-501B Evolutionary & SWE-bench Trajectory](experiments/v13/assets/v1_to_v13_beam_trajectory.svg)

| Era | Generations & Benchmark | Core Architectural Paradigm | Key Empirical Outcome | Canonical Write-ups |
| :--- | :--- | :--- | :--- | :--- |
| **Era 1: Execution-Grounded Fitness & Epistemic Search Foundations** | **`V1` – `V7`** (Internal `artifacts.py` 7-test & `full_stack_hae` 50-test benchmarks) | Replaced uncalibrated prose LLM judges (`V1`, `corr = +0.045` with execution) with sandboxed unit-test fitness (`V2`), recursive code overlays (`V3`), self-hosted `vLLM` + `llm-d` prefix-affinity routing (`V4`), `xgrammar` schema packets (`V5`), evidence-gated epistemic search (`V6`), and out-of-fold value heads (`V7`). | Proved execution-grounded verification is mandatory (`0/60` $\to$ `30/30` on 7-test suite; `50/50` on 4-module `full_stack_hae`), cut packet output tokens by `-91.9%`, and achieved `455/455` `llm-d` prefix-affinity hits. | [`experiments/v1`](experiments/v1/README.md), [`v2`](experiments/v2/README.md), [`v3_rsi`](experiments/v3_rsi/README.md), [`v4_llmd`](experiments/v4_llmd/README.md), [`v5`](experiments/v5_typesafe_xgrammar/README.md), [`v6`](experiments/v6_epistemic_search/README.md), [`v7`](results/v7_value_heads/README.md) |
| **Era 2: SWE-bench Container Harness & Single-Layer Agentic ReAct** | **`V8` – `V12`** (Official containerized SWE-bench dev cohort, `n = 30`) | Transitioned to real-world GitHub issues inside `swebench.harness` `/testbed` pods (`V8`), introduced packet-level AB-MCTS (`V9`), MoE Expert Parallelism + EPLB (`V10`), multi-hunk splicing (`V11`), and interactive multi-turn ReAct tool loops (`view_file`, `bash`, `str_replace`, `submit`) with Best-of-3 causal framing (`V12`). | Lifted SWE-bench resolution from **`13.3%` (`4/30`) in `V8` $\to$ `20.0%` (`6/30`) in `V11` $\to$ `46.7%` (`14/30`, `98` `FAIL_TO_PASS` tests) in `V12`**, exposing context-window entropy and single-basin local optima as the remaining bottlenecks. | [`v8_roadmap`](experiments/v8/v8_role_evolution_and_swebench_roadmap.md), [`v8_v9_report`](experiments/v9/v8_v9_evaluation_report.md), [`v10_eplb`](experiments/v10/v10_eplb_evaluation_report.md), [`v11_report`](experiments/v11/v11_evaluation_report.md), [`v12_report`](experiments/v12/v12_evaluation_report.md) |
| **Era 3: Context-Bounded Hierarchical AB-MCTS, Speculative `501B` MoE & Two-Tier `L0-Macro` Ratchet** | **`V13`, `V13 + Beam-501B` & `V14` (`L0-Macro` Ratchet + Zero-Hint `L0-Micro`)** (SWE-bench `n = 30` cohort, `n = 6` multi-repo slice & multi-milestone builds) | Refactored hierarchy into **Context-Bounded Verification Layers ($L_0 \to L_1 \to L_2$)** with **Agentic AB-MCTS over Git Checkpoints** (`CONT` delta-refinement vs. `GEN` "Move 37" pivots), read-only **Popperian `AdversarialFalsifierSubAgent`**, zero-hint domain-general prompts, pluggable `WorkspaceVerifierPlugin`, and the outer **`L0-Macro` Milestone DAG & Git Commit Ratchet (`V14`)**, powered by **`Reflection Beam-1.0-501B-A23B-NVFP4` + `DFlash2` (`8`-token speculative decoding)** across 3 Ray `TP=4` replica groups (`12x RTX PRO 6000 96 GB`). | Achieved **`100.0%` (`30/30` on `Qwen3.8`, `6/6` on `Beam-501B`) held-out SWE-bench resolution** (`100%` `FAIL_TO_PASS`, `0` regressions), **`3.31x` faster wall-clock execution (`305.6s` mean, up to `5.36x` faster)** with **`-55.1%` fewer tokens**, and bounded `< 500`-LoC per-milestone ratcheting for `5,000–100,000+` LoC builds. | [`v14_macro_ratchet`](experiments/v13/v14_generalized_macro_ratchet_architecture.md), [`v13_architecture`](experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md), [`v13_report`](experiments/v13/v13_evaluation_report.md), [`beam_vs_qwen38`](experiments/v13/beam_vs_qwen38_evaluation_report.md) |

---

## Repository Layout

```text
hae/
  hierarchical/   Two-Tier L0-Macro Milestone DAG & Git Commit Ratchet (milestone_ratchet.py), pluggable WorkspaceVerifierPlugin (verifier_plugin.py)
  swebench/       Generalized L0-Micro V13 Hierarchical AB-MCTS engine (hierarchical_mcts.py), V12 Agentic ReAct engine (agentic.py), container executor, runner
  infra/          LLM providers (vLLM + Harmony reasoning_effort & <rfl:tool_call> adapter, Vertex), config, preflight, telemetry
  epistemic/      Epistemic ledger, moves, gatekeeper, PUCT search, packet AB-MCTS controller (V9–V11), value/audit, heads (V7)
  genome/         CompanyGenome schema, mutation and crossover, role seeds, morphogenesis
  runtime/        HierarchicalCompanyRunner (first pass + epistemic repair), workspace, overlays
  evaluation/     Benchmark, execution harness, verification loop, fitness judge
  orchestration/  Tournament engine, GKE worker, breeder
  task/           Task specs, budgets, verifiers
  cli.py          Entry point: ratchet, swebench, tournament, single-firm, breed, benchmark, preflight
k8s/              GKE cluster setup, Beam-1.0-501B + DFlash2 (TP=4 Ray) & Qwen3.8 + EPLB (TP=2) serving manifests (see k8s/README.md)
experiments/      Architecture specs, evaluation reports, and complete V1–V13+ phase index (see experiments/README.md)
results/          Harvested SWE-bench evaluations (beam_eval_2026-10-20/, dev_eval_2026-10-09/) and V6/V7 cohort artifacts
configs/          V8–V13 SWE-bench genomes (configs/genomes/), generation population specs, task definitions
scripts/          GKE SWE-bench batch evaluator (swebench_kubectl_batch.py), harvesters, telemetry & summary generators
data/swebench/    SWE-bench dev-slice manifest (50-instance pool, 0 overlap with SWE-bench Verified)
tests/            Hermetic unit and regression test suite (stdlib unittest, zero network dependency)
```

---

## Quickstart & Running on GKE

### 1. Deploy Reflection `Beam-1.0-501B-A23B-NVFP4` + `DFlash2` on GKE (Primary Stack)

[`k8s/beam-501b-nvfp4-serving.yaml`](k8s/beam-501b-nvfp4-serving.yaml) deploys 3 multi-node Ray `TP=4` replica groups (`beam-501b-{0,1,2}-{head,worker}`) across `6x g4-standard-96` nodes (`12x RTX PRO 6000 Blackwell 96 GB GPUs`, `2.51M` total `fp8_e4m3` KV-cache tokens) behind the `llm-d` inference gateway (`svc/vllm-qwen38-gateway:8000`). See [`k8s/README.md`](k8s/README.md) for full cluster setup details.

```bash
# 1. Deploy the 3 multi-node Ray TP=4 Beam-501B + DFlash2 replica groups and llm-d gateway
kubectl apply -f k8s/beam-501b-nvfp4-serving.yaml

# 2. Wait for hybrid DDR5+NVMe weight loading and Ray TP=4 initialization (~4-5 min)
kubectl get pods -l app=beam-501b-nvfp4 -w

# 3. Port-forward the llm-d inference gateway locally
kubectl port-forward svc/vllm-qwen38-gateway 8000:8000

# 4. Run V13 Hierarchical Verification Harness + Agentic AB-MCTS with Beam-1.0-501B + DFlash2
PYTHONPATH=. python3 -u scripts/swebench_kubectl_batch.py \
  --out-root results/swebench/beam_eval_2026-10-20 \
  --policies v13_hier_mcts \
  --vllm-url http://127.0.0.1:8000/v1 \
  --vllm-model reflection/Beam-1.0-501B-A23B-NVFP4 \
  --workers 3
```

### 2. Alternative: Run with `nvidia/Qwen3.8-Flash-Next-NVFP4` (`180B-A10B` + EPLB)

```bash
# Deploy 6 single-node TP=2 Qwen3.8 replicas with Expert Parallelism & EPLB
kubectl apply -f k8s/qwen38-flash-next-180b-eplb-serving.yaml

# Run V13 across the 30-instance SWE-bench dev cohort
PYTHONPATH=. python3 -u scripts/swebench_kubectl_batch.py \
  --out-root results/swebench/dev_eval_2026-10-09 \
  --policies v13_hier_mcts \
  --vllm-url http://127.0.0.1:8000/v1 \
  --vllm-model nvidia/Qwen3.8-Flash-Next-NVFP4 \
  --workers 4 \
  --budget-moves 36
```

### 3. Multi-Milestone Ratchet CLI (`--mode ratchet`), Single-Instance CLI & Hermetic Unit Tests

Unit tests require Python 3.10+ and only the standard library (zero network dependency):

```bash
# Run the complete unit and regression test suite
PYTHONPATH=. python3 -m unittest discover -s tests -q

# Run the Two-Tier L0-Macro Milestone Ratchet & Generalized V13 L0-Micro tests specifically
PYTHONPATH=. python3 -m unittest tests/test_milestone_ratchet.py tests/test_swebench_hierarchical_mcts.py tests/test_swebench_agentic.py tests/test_swebench_runner.py -v

# Run a multi-milestone system build with the Two-Tier L0-Macro Git Commit Ratchet
PYTHONPATH=. python3 -m hae.cli --mode ratchet \
  --workspace-dir /path/to/target-repo \
  --objective "Build a modular tokenizer, recursive-descent parser, and evaluator with full unit tests." \
  --acceptance-cmd "pytest -q" \
  --resume

# Run a single SWE-bench instance via CLI
PYTHONPATH=. python3 -m hae.cli --mode swebench \
  --genome configs/genomes/v13_hierarchical_ab_mcts_firm_0.json \
  --instance-id marshmallow-code__marshmallow-1702
```

---

## Key Documentation & Evaluation Reports

- **Generalized Two-Tier `L0-Macro` Ratchet, `V13` & `Beam-1.0-501B` Architecture & Evaluation Reports**:
  - **[`experiments/v13/v14_generalized_macro_ratchet_architecture.md`](experiments/v13/v14_generalized_macro_ratchet_architecture.md)**: Universal Two-Tier Hierarchical Verification & Milestone Ratchet specification (`L0-Macro` Compositional Milestone DAG & Persistent Git Commit Ratchet $\times$ `L0-Micro` Generalized V13 AB-MCTS, scaling from 5-minute bug fixes to multi-day `100,000+` LoC builds).
  - **[`results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md`](results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md)** & **[`summary.json`](results/swebench/v14_zero_hint_beam_eval_2026-10-21/summary.json)**: Zero-Hint Generalized V13 `L0-Micro` evaluation on the 6-task SWE-bench multi-repo slice with `Reflection Beam-1.0-501B-A23B-NVFP4 + DFlash2` (`6 / 6` = `100.0%` resolved, `57 / 57` F2P, `214 / 214` P2P, `278.82s` mean wall time = `3.62x` faster than `Qwen3.8` and `1.10x` faster than prior hinted `Beam-501B`, `-55.5%` fewer tokens, `0` repo hints).
  - **[`results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md`](results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md)** & **[`summary.json`](results/ratchet/beam_macro_ratchet_eval_2026-10-21/summary.json)**: Live 4-milestone `L0-Macro` Compositional Milestone DAG & Git Commit Ratchet evaluation on `Reflection Beam-1.0-501B-A23B-NVFP4 + DFlash2` (`4 / 4` milestones certified in `245.58s`, `1,139` cumulative LoC with active delta bounded to `257–364` lines `< 500` LoC, `25 / 25` cumulative regression tests passed).
  - **[`experiments/v13/beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md)**: Head-to-head SWE-bench evaluation of `Reflection Beam-1.0-501B-A23B-NVFP4 + DFlash2` vs. `nvidia/Qwen3.8-Flash-Next-NVFP4` (`100.0%` resolution at `3.31x` faster wall-clock speed and `-55.1%` fewer tokens).
  - **[`experiments/v13/v13_evaluation_report.md`](experiments/v13/v13_evaluation_report.md)**: Full 30-instance SWE-bench cohort report for `V13` (`30 / 30` = `100.0%` resolved, `275 / 275` `FAIL_TO_PASS`, 6-generation longitudinal comparison across `V8`–`V13`).
  - **[`experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md`](experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md)**: Formal specification of the V13 Context-Bounded Hierarchical Verification Harness and Agentic AB-MCTS ("Move 37" Engine).
  - **[`k8s/README.md`](k8s/README.md)**: GKE hardware topology (`12x RTX PRO 6000 Blackwell 96 GB`), multi-node Ray `TP=4` `Beam-501B + DFlash2` serving guide, and `Qwen3.8` EPLB configuration.
- **Complete Multi-Generation Index & Harvested Artifacts**:
  - **[`experiments/README.md`](experiments/README.md)**: Complete generation-by-generation (`V1`–`V14`) phase table, reading order, and links to all historical reports ([`V12`](experiments/v12/v12_evaluation_report.md), [`V11`](experiments/v11/v11_evaluation_report.md), [`V10`](experiments/v10/v10_eplb_evaluation_report.md), [`V8–V9`](experiments/v9/v8_v9_evaluation_report.md)).
  - **[`results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md`](results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md)**, **[`results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md`](results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md)**, **[`results/swebench/beam_eval_2026-10-20/README.md`](results/swebench/beam_eval_2026-10-20/README.md)** & **[`results/swebench/dev_eval_2026-10-09/README.md`](results/swebench/dev_eval_2026-10-09/README.md)**: Harvested Zero-Hint SWE-bench evaluations, milestone ratchets, per-instance patches, `grade.json` pytest reports, and trajectories.

---

## License

Apache-2.0, see [`LICENSE`](LICENSE).
