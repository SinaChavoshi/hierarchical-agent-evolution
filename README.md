# Hierarchical Agent Evolution (HAE)

**Hierarchical Agent Evolution (HAE)** is a **Context-Bounded Hierarchical Verification & Agentic Tree-Search Engine (`V13`)** paired with self-hosted **Reflection `Beam-1.0-501B-A23B-NVFP4` (`501B` total / `23B` active MoE) + `DFlash2` (`8`-token speculative decoding)** on GKE (`12x NVIDIA RTX PRO 6000 Blackwell 96 GB GPUs`, `1.15 TB` VRAM) for autonomous software diagnosis, verification, and repair on **SWE-bench**.

> [!IMPORTANT]
> **Headline Empirical Results (`V13` Hierarchical Verification Harness + `Beam-1.0-501B-A23B-NVFP4` + `DFlash2`)**
> - **`100.0%` Gold Held-Out SWE-bench Resolution**: Resolves **`30 / 30` (`100.0%`)** instances on the 30-task SWE-bench dev cohort (`275 / 275` individual `FAIL_TO_PASS` tests passed, `0` `PASS_TO_PASS` regressions) with `Qwen3.8-Flash-Next-NVFP4`, and **`6 / 6` (`100.0%`)** on the 6-task multi-repo benchmark cohort (`57 / 57` `FAIL_TO_PASS`, `214 / 214` `PASS_TO_PASS`, `0` regressions) with `Reflection Beam-1.0-501B-A23B-NVFP4 + DFlash2`.
> - **`3.31x` Faster End-to-End Execution**: Pairing `V13` with `Beam-1.0-501B-A23B-NVFP4 + DFlash2` cuts mean wall-clock resolution time from **`1,010.6s` to `305.6s` (`3.31x` faster, up to `5.36x` faster on `marshmallow-1702` at `158.4s`)** and accelerates `v12_agentic` by **`3.56x` (`331.0s` vs. `1,177.2s`)**.
> - **Radical Token & Search Efficiency**: Requires **`-34.0%` fewer agentic moves (`22.3` vs. `33.8`)**, **`-55.1%` fewer total tokens (`149.9k` vs. `333.7k`)**, and **`-75.3%` to `-91.0%` fewer `<think>` tokens** via `DFlash2` speculative decoding and per-turn Harmony `reasoning_effort` budget control.

| Benchmark Cohort & Configuration | Gold Held-Out Resolved (`grade.json`) | Individual `FAIL_TO_PASS` Passed | `PASS_TO_PASS` Regressions | Mean Agentic Moves | Mean Total Tokens | Mean Wall-Clock Time | Speedup vs. `Qwen3.8` | Primary Report |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`v13_hier_mcts` + `Beam-1.0-501B` (`+ DFlash2`)** (`n = 6` multi-repo) | **`6 / 6` (`100.0%`)** | **`57 / 57` (`100.0%`)** | **`0 / 214`** | **`22.3` (`-34.0%`)** | **`149.9k` (`-55.1%`)** | **`305.6s`** | **`3.31x` faster (up to `5.36x`)** | [`beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md) |
| **`v13_hier_mcts` + `Qwen3.8-180B-A10B`** (`n = 6` multi-repo) | **`6 / 6` (`100.0%`)** | **`57 / 57` (`100.0%`)** | **`0 / 214`** | `33.8` | `333.7k` | `1,010.6s` | `1.00x` (baseline) | [`beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md) |
| **`v13_hier_mcts` + `Qwen3.8-180B-A10B`** (`n = 30` full dev cohort) | **`30 / 30` (`100.0%`)** | **`275 / 275` (`100.0%`)** | **`0`** | `37.5` | `348.2k` | `1,210.5s` | **`2.14x` resolution vs. V12** | [`v13_evaluation_report.md`](experiments/v13/v13_evaluation_report.md) |
| **`v12_agentic` + `Beam-1.0-501B` (`+ DFlash2`)** (`n = 6` multi-repo) | `2 / 6` (`33.3%`) | `29 / 57` (`50.9%`) | `0 / 214` | `27.3` (`-9.0%`) | `162.1k` (`-40.7%`) | `331.0s` | **`3.56x` faster (up to `7.92x`)** | [`beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md) |
| **`v12_agentic` + `Qwen3.8-180B-A10B`** (`n = 30` full dev cohort) | `14 / 30` (`46.7%`) | `98 / 269` (`36.4%`) | `2 / 30` | `29.7` | `291.4k` | `1,310.7s` | `1.00x` (single-layer ReAct) | [`v12_evaluation_report.md`](experiments/v12/v12_evaluation_report.md) |

---

## System Architecture

### 1. V13 Hierarchical Verification Harness & Agentic AB-MCTS ("Move 37" Engine)

Rather than running a single long-horizon chat loop that suffers context bloat and self-verification bias, **V13** (`hae/swebench/hierarchical_mcts.py`) structures the agent hierarchy as a **3-Layer Context-Isolation & Popperian Falsification Stack** governed at the root by **Agentic Adaptive Branching MCTS (AB-MCTS)** over Git workspace checkpoints. Full specification: [`experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md`](experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md) (high-res PNG: [`v13_beam_architecture.png`](experiments/v13/assets/v13_beam_architecture.png)).

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
| **`L1-B`: Gatekeeper & Adversarial Falsifier** | `EvidenceGatekeeper` runs the repro script + target `pytest` suite; on pass, a read-only `AdversarialFalsifierSubAgent` audits the diff against $L_0$'s contract and appends adversarial edge-case tests to `/testbed/.hae/falsification_suite.py`. | Ephemeral (`4–6` turns; zero author confirmation bias) | Empirical score $V(s_i) \in [0, 1]$, updated $\text{Beta}(\alpha_i, \beta_i)$, and `< 400`-token `Critique` (or `Certified V = 1.0`) |

---

### 2. GKE Distributed Inference & Execution Topology (`Beam-1.0-501B-A23B-NVFP4` + `DFlash2`)

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

### A. Reflection `Beam-1.0-501B-A23B-NVFP4` (`+ DFlash2`) vs. `Qwen3.8-Flash-Next-NVFP4` (`180B-A10B`)

Full report and telemetry: [`experiments/v13/beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md) and [`results/swebench/beam_eval_2026-10-20/README.md`](results/swebench/beam_eval_2026-10-20/README.md).

```mermaid
xychart-beta
    title "Mean Wall-Clock Time per SWE-bench Instance (Seconds — Lower is Faster)"
    x-axis ["v12_agentic (Qwen3.8)", "v12_agentic (Beam-501B)", "v13_hier_mcts (Qwen3.8)", "v13_hier_mcts (Beam-501B)"]
    y-axis "Mean Wall-Clock Seconds" 0 --> 1300
    bar [1177.2, 331.0, 1010.6, 305.6]
```

#### Per-Instance Head-to-Head on `v13_hier_mcts` (`n = 6` Multi-Repo SWE-bench Slice)

| Instance ID | `Qwen3.8` Status | `Beam-501B` Status | `F2P` / `P2P` | `Qwen3.8` Moves | `Beam-501B` Moves | `Qwen3.8` Tokens | `Beam-501B` Tokens | `Qwen3.8` Time | **`Beam-501B` Time** | **Speedup** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `marshmallow-code__marshmallow-1359` | **PASS** | **PASS** | `1/1` F2P, `76/76` P2P | `25` | `36` | `230,381` | `240,964` | `564.1s` | **`392.0s`** | **`1.44x`** |
| `marshmallow-code__marshmallow-1702` | **PASS** | **PASS** | `28/28` F2P, `71/71` P2P | `31` | **`15`** | `368,459` | **`104,598`** | `849.2s` | **`158.4s`** | **`5.36x`** |
| `pydicom__pydicom-1255` | **PASS** | **PASS** | `1/1` F2P, `22/22` P2P | `48` | **`25`** | `518,902` | **`149,110`** | `1,727.2s` | **`373.7s`** | **`4.62x`** |
| `pylint-dev__astroid-1417` | **PASS** | **PASS** | `1/1` F2P, `3/3` P2P | `19` | `21` | `111,194` | `142,437` | `806.4s` | **`388.2s`** | **`2.08x`** |
| `pyvista__pyvista-4226` | **PASS** | **PASS** | `25/25` F2P, `0/0` P2P | `55` | **`24`** | `555,537` | **`180,852`** | `1,586.7s` | **`348.1s`** | **`4.56x`** |
| `sqlfluff__sqlfluff-1517` | **PASS** | **PASS** | `1/1` F2P, `42/42` P2P | `25` | **`13`** | `217,582` | **`81,282`** | `529.7s` | **`173.0s`** | **`3.06x`** |
| **Mean / Total (`n = 6`)** | **`6 / 6` (`100%`)** | **`6 / 6` (`100%`)** | **`57/57` F2P, `214/214` P2P** | **`33.8`** | **`22.3` (`-34.0%`)** | **`333,676`** | **`149,874` (`-55.1%`)** | **`1,010.6s`** | **`305.6s`** | **`3.31x` faster** |

> [!NOTE]
> **Why `V13` + `Beam-501B` Are Synergistic (`33.3%` $\to$ `100.0%`)**: Even with the `501B` `Beam` model, single-layer ReAct (`v12_agentic`) resolves only `2 / 6` (`33.3%`) tasks (`331.0s` mean time) because single-context agents settle into local optima that pass their own reproduction scripts (`5 / 6` self-oracle certified) while missing cross-module invariants. Wrapping `Beam-501B` in **V13's Hierarchical Verification Harness (`L0` contract + `L1-B` Popperian falsifier + `CONT` delta-refinement)** lifts resolution from **`33.3%` to `100.0%` (`6 / 6`)** while running even **faster** (`305.6s` vs. `331.0s`) and consuming **fewer tokens** (`149.9k` vs. `162.1k`).

---

### B. SWE-bench Multi-Generation Progression (`V8` $\to$ `V13`, `n = 30` Dev Cohort)

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

### C. Three-Era Evolution Summary (`V1` $\to$ `V13 + Beam-501B`)

Across 13 generations, HAE progressed through three distinct architectural eras. See **[`experiments/README.md`](experiments/README.md)** for the complete generation-by-generation (`V1`–`V13`) table, design notes, and errata (high-res PNG: [`v1_to_v13_beam_trajectory.png`](experiments/v13/assets/v1_to_v13_beam_trajectory.png)).

![HAE V1 to V13 + Reflection Beam-501B Evolutionary & SWE-bench Trajectory](experiments/v13/assets/v1_to_v13_beam_trajectory.svg)

| Era | Generations & Benchmark | Core Architectural Paradigm | Key Empirical Outcome | Canonical Write-ups |
| :--- | :--- | :--- | :--- | :--- |
| **Era 1: Execution-Grounded Fitness & Epistemic Search Foundations** | **`V1` – `V7`** (Internal `artifacts.py` 7-test & `full_stack_hae` 50-test benchmarks) | Replaced uncalibrated prose LLM judges (`V1`, `corr = +0.045` with execution) with sandboxed unit-test fitness (`V2`), recursive code overlays (`V3`), self-hosted `vLLM` + `llm-d` prefix-affinity routing (`V4`), `xgrammar` schema packets (`V5`), evidence-gated epistemic search (`V6`), and out-of-fold value heads (`V7`). | Proved execution-grounded verification is mandatory (`0/60` $\to$ `30/30` on 7-test suite; `50/50` on 4-module `full_stack_hae`), cut packet output tokens by `-91.9%`, and achieved `455/455` `llm-d` prefix-affinity hits. | [`experiments/v1`](experiments/v1/README.md), [`v2`](experiments/v2/README.md), [`v3_rsi`](experiments/v3_rsi/README.md), [`v4_llmd`](experiments/v4_llmd/README.md), [`v5`](experiments/v5_typesafe_xgrammar/README.md), [`v6`](experiments/v6_epistemic_search/README.md), [`v7`](results/v7_value_heads/README.md) |
| **Era 2: SWE-bench Container Harness & Single-Layer Agentic ReAct** | **`V8` – `V12`** (Official containerized SWE-bench dev cohort, `n = 30`) | Transitioned to real-world GitHub issues inside `swebench.harness` `/testbed` pods (`V8`), introduced packet-level AB-MCTS (`V9`), MoE Expert Parallelism + EPLB (`V10`), multi-hunk splicing (`V11`), and interactive multi-turn ReAct tool loops (`view_file`, `bash`, `str_replace`, `submit`) with Best-of-3 causal framing (`V12`). | Lifted SWE-bench resolution from **`13.3%` (`4/30`) in `V8` $\to$ `20.0%` (`6/30`) in `V11` $\to$ `46.7%` (`14/30`, `98` `FAIL_TO_PASS` tests) in `V12`**, exposing context-window entropy and single-basin local optima as the remaining bottlenecks. | [`v8_roadmap`](experiments/v8/v8_role_evolution_and_swebench_roadmap.md), [`v8_v9_report`](experiments/v9/v8_v9_evaluation_report.md), [`v10_eplb`](experiments/v10/v10_eplb_evaluation_report.md), [`v11_report`](experiments/v11/v11_evaluation_report.md), [`v12_report`](experiments/v12/v12_evaluation_report.md) |
| **Era 3: Context-Bounded Hierarchical AB-MCTS + Speculative `501B` MoE** | **`V13` & `V13 + Beam-501B`** (SWE-bench `n = 30` cohort & `n = 6` multi-repo slice) | Refactored hierarchy into **Context-Bounded Verification Layers ($L_0 \to L_1 \to L_2$)** with **Agentic AB-MCTS over Git Checkpoints** (`CONT` delta-refinement vs. `GEN` "Move 37" pivots) and a read-only **Popperian `AdversarialFalsifierSubAgent`**, powered by **`Reflection Beam-1.0-501B-A23B-NVFP4` + `DFlash2` (`8`-token speculative decoding)** across 3 Ray `TP=4` replica groups (`12x RTX PRO 6000 96 GB`). | Achieved **`100.0%` (`30/30` on `Qwen3.8`, `6/6` on `Beam-501B`) held-out SWE-bench resolution** (`100%` `FAIL_TO_PASS`, `0` regressions) and **`3.31x` faster wall-clock execution (`305.6s` mean, up to `5.36x` faster)** with **`-55.1%` fewer tokens**. | [`v13_architecture`](experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md), [`v13_report`](experiments/v13/v13_evaluation_report.md), [`beam_vs_qwen38`](experiments/v13/beam_vs_qwen38_evaluation_report.md) |

---

## Repository Layout

```text
hae/
  swebench/       V13 Hierarchical AB-MCTS engine (hierarchical_mcts.py), V12 Agentic ReAct engine (agentic.py), container executor, runner
  infra/          LLM providers (vLLM + Harmony reasoning_effort & <rfl:tool_call> adapter, Vertex), config, preflight, telemetry
  epistemic/      Epistemic ledger, moves, gatekeeper, PUCT search, packet AB-MCTS controller (V9–V11), value/audit, heads (V7)
  genome/         CompanyGenome schema, mutation and crossover, role seeds, morphogenesis
  runtime/        HierarchicalCompanyRunner (first pass + epistemic repair), workspace, overlays
  evaluation/     Benchmark, execution harness, verification loop, fitness judge
  orchestration/  Tournament engine, GKE worker, breeder
  task/           Task specs, budgets, verifiers
  cli.py          Entry point: swebench, tournament, single-firm, breed, benchmark, preflight
k8s/              GKE cluster setup, Beam-1.0-501B + DFlash2 (TP=4 Ray) & Qwen3.8 + EPLB (TP=2) serving manifests (see k8s/README.md)
experiments/      Architecture specs, evaluation reports, and complete V1–V13 phase index (see experiments/README.md)
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

### 3. Single-Instance CLI & Hermetic Unit Tests

Unit tests require Python 3.10+ and only the standard library (zero network dependency):

```bash
# Run the complete unit and regression test suite
PYTHONPATH=. python3 -m unittest discover -s tests -q

# Run the V8-V13 SWE-bench unit tests specifically
PYTHONPATH=. python3 -m unittest tests/test_swebench_hierarchical_mcts.py tests/test_swebench_agentic.py tests/test_swebench_runner.py -v

# Run a single SWE-bench instance via CLI
PYTHONPATH=. python3 -m hae.cli --mode swebench \
  --genome configs/genomes/v13_hierarchical_ab_mcts_firm_0.json \
  --instance-id marshmallow-code__marshmallow-1702
```

---

## Key Documentation & Evaluation Reports

- **V13 & `Beam-1.0-501B` Architecture & Evaluation Reports**:
  - **[`experiments/v13/beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md)**: Head-to-head SWE-bench evaluation of `Reflection Beam-1.0-501B-A23B-NVFP4 + DFlash2` vs. `nvidia/Qwen3.8-Flash-Next-NVFP4` (`100.0%` resolution at `3.31x` faster wall-clock speed and `-55.1%` fewer tokens).
  - **[`experiments/v13/v13_evaluation_report.md`](experiments/v13/v13_evaluation_report.md)**: Full 30-instance SWE-bench cohort report for `V13` (`30 / 30` = `100.0%` resolved, `275 / 275` `FAIL_TO_PASS`, 6-generation longitudinal comparison across `V8`–`V13`).
  - **[`experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md`](experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md)**: Formal specification of the V13 Context-Bounded Hierarchical Verification Harness and Agentic AB-MCTS ("Move 37" Engine).
  - **[`k8s/README.md`](k8s/README.md)**: GKE hardware topology (`12x RTX PRO 6000 Blackwell 96 GB`), multi-node Ray `TP=4` `Beam-501B + DFlash2` serving guide, and `Qwen3.8` EPLB configuration.
- **Complete Multi-Generation Index & Harvested Artifacts**:
  - **[`experiments/README.md`](experiments/README.md)**: Complete generation-by-generation (`V1`–`V13`) phase table, reading order, and links to all historical reports ([`V12`](experiments/v12/v12_evaluation_report.md), [`V11`](experiments/v11/v11_evaluation_report.md), [`V10`](experiments/v10/v10_eplb_evaluation_report.md), [`V8–V9`](experiments/v9/v8_v9_evaluation_report.md)).
  - **[`results/swebench/beam_eval_2026-10-20/README.md`](results/swebench/beam_eval_2026-10-20/README.md)** & **[`results/swebench/dev_eval_2026-10-09/README.md`](results/swebench/dev_eval_2026-10-09/README.md)**: Per-instance patches, `grade.json` pytest reports, and trajectories.

---

## License

Apache-2.0, see [`LICENSE`](LICENSE).
