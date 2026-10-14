# HAE V13 Architecture — Hierarchical Verification Harness with Agentic AB-MCTS ("Move 37" Engine)

**Author**: HAE Architecture Team  
**Date**: October 14, 2026  
**Status**: V13 Architecture Specification & Implementation Blueprint  
**Target Benchmark**: SWE-bench Verified / Dev Cohort (`nvidia/Qwen3.8-Flash-Next-NVFP4` on GKE)

---

## 1. Motivation & Core Epiphany

Across Generations V1–V12 of **Hierarchical Agent Evolution (HAE)**, we observed two complementary failure modes that revealed the true purpose of organizational hierarchy in agentic systems:

1. **V8–V11 (Micro-Level AB-MCTS Without Stateful Agentic Execution — `10.0%–20.0%`)**:
   - Treated an organization as a static division of prompt templates (`PROPOSE_HYPOTHESES`, `DESIGN_EXPERIMENT`, `SYNTHESIZE`) governed by PUCT / AB-MCTS at the single-prompt level.
   - Because each tree node was a stateless 1-shot JSON edit over a truncated 300-line snippet and every failed gate check wiped `/testbed` back to `HEAD`, the system could neither iteratively debug a 95%-correct patch nor inspect runtime state inside `/testbed`.

2. **V12 (Stateful Agentic Loop Without Hierarchical Context Isolation or Global Search — `46.7%`, `14 / 30`)**:
   - Giving specialists interactive `bash`, `view_file`, `str_replace`, and `submit` tools inside `/testbed` more than doubled performance (`20.0%` $\to$ `46.7%`, `98` `FAIL_TO_PASS` tests passed vs. `6` in V11).
   - However, V12 exposed **two fundamental ceilings** of flat/linear agent loops:
     - **Ceiling A — Context Window Entropy & Self-Verification Blindness**: When a single specialist spends 20+ turns grepping, reading files, and debugging in one context window (`200K+` tokens), older constraints are truncated/diluted and the agent suffers *author confirmation bias*—writing weak self-tests that confirm its own code while missing secondary requirements (e.g., `marshmallow-1702` dropping `additional_metadata` when `metadata` is also passed, or `astroid-1417` omitting `"functools.cached_property"` from the deprecation warning message).
     - **Ceiling B — Greedy Local Optima & Failure to Discover "Move 37"**: A linear hierarchy commits to its *first plausible hypothesis* on Turn 2 and hill-climbs inside that single basin of attraction for the entire budget. When the true fix ("Move 37") lives in an **upstream producer/invariant** rather than at the downstream symptom site (e.g., `sqlfluff-1625`, `sqlfluff-891`, `pydicom-997`), a linear loop passes its own narrow reproduction test, declares a false global optimum (`resolved_by_self_oracle = True`), and never searches for Move 37.

### The V13 Architectural Principle

> **Why Hierarchies Exist**: In a V13 agent harness, the hierarchy is **not** a bureaucratic org chart of job titles—it is a **Recursive Epistemic Verification & Context-Isolation Stack** governed at the root by **Agentic AB-MCTS**.
> - **Each parent layer $L_k$ exists to specify, monitor, and independently validate/falsify the work of child layer $L_{k+1}$ from an unpolluted context window.**
> - **Each child layer $L_{k+1}$ executes a bounded, scoped contract in a fresh ephemeral context window**, absorbing all noisy trial-and-error (`grep` dumps, file slices, stack traces) and returning upward **only verifiable artifacts + compact empirical evidence**.
> - **AB-MCTS operates at the Seed Layer ($L_0$) over Git Workspace Checkpoints**, using Thompson Sampling over empirical Beta posteriors to dynamically balance **Deepening (`CONT`: spawning a fresh-context Delta-Refinement Sub-Agent on a promising checkpoint)** against **Widening (`GEN`: spawning an orthogonal "Move 37" causal branch when a local optimum is falsified or stagnates)**.

---

## 2. System Architecture Overview

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│ LAYER L0: SEED ARCHITECT & AB-MCTS TREE MASTER (Unpolluted Context < 4K Tokens)   │
│  • Holds Problem Statement + Distilled Recon Brief + Compact Checkpoint Tree.     │
│  • Formulates Master Verification Contract & Abstraction-Stratified Hypotheses:   │
│      - Branch Class 1 (Consumer / Symptom Site)                                   │
│      - Branch Class 2 (Producer / Upstream Invariant — "Move 37")                 │
│      - Branch Class 3 (Codebase Symmetry / Sibling Pattern)                       │
│  • Selects `GEN(s_p)` vs. `CONT(s_i)` via Thompson Sampling over Beta(α_i, β_i).  │
└────────┬──────────────────────────────┬───────────────────────────────┬───────────┘
         │ Phase 0                      │ Phase 2A (`GEN` / `CONT`)     │ Phase 2B (Audit)
         ▼                              ▼                               ▼
┌─────────────────────────┐  ┌─────────────────────────────┐  ┌─────────────────────┐
│ LAYER L1-A:             │  │ LAYER L2:                   │  │ LAYER L1-B:         │
│ Reconnaissance Agent    │  │ Ephemeral Branch Executor   │  │ Independent         │
│ (Fresh Context, 4-6t)   │  │ (Fresh Context, 6-10t)      │  │ Adversarial         │
│  • Maps call chain      │  │  • `GEN`: Starts at HEAD    │  │ Falsifier Agent     │
│    (Producer->Consumer) │  │    with Recon Brief +       │  │ (Fresh Context, 4-6t│
│  • Checks repo warning/ │  │    Branch Contract.         │  │  Zero Author Bias)  │
│    test conventions     │  │  • `CONT`: Starts with      │  │  • Reviews diff vs  │
│  • Authors baseline     │  │    parent `git diff` +      │  │    L0 Contract      │
│    `.hae/reproduce_     │  │    exact Falsifier/Pytest   │  │  • Grows cumulative │
│    issue.py` (verified  │  │    failure critique.        │  │    `.hae/falsify_   │
│    to FAIL on HEAD)     │  │  • Edits via `str_replace`  │  │    suite.py`        │
│  • Returns 400-tok      │  │  • Returns Checkpoint s_i;  │  │  • Certifies V=1.0  │
│    ReconBrief; dies.    │  │    scratchpad GC'd!         │  │    or emits Critique│
└─────────────────────────┘  └─────────────────────────────┘  └─────────────────────┘
```

---

## 3. Formal Mechanics of Hierarchical Agentic AB-MCTS

### 3.1. What Is a Node in the $L_0$ Search Tree?

Unlike V8–V11 (where a node was an unverified text hypothesis) or flat chat loops (where history is a 200K-token transcript), a node $s_i \in \mathcal{T}$ in V13's $L_0$ tree is a **Verified Workspace Checkpoint Tuple**:

$$s_i = \Bigl(\text{id}_i,\; \text{parent}_i,\; \text{locus}_i,\; \text{claim}_i,\; \text{Checkpoint}_i,\; \text{VerificationVector}_i,\; \text{Critique}_i,\; \text{Beta}(\alpha_i, \beta_i)\Bigr)$$

where:
- $\text{Checkpoint}_i$ stores the exact `git diff`, modified file contents, and syntax/static check status.
- $\text{VerificationVector}_i = (\text{compile\_ok}, \text{repro\_ok}, \text{repo\_tests\_ok}, \text{falsifier\_ok}, n_{\text{new\_failures}}, \text{diff\_lines})$.
- $\text{Critique}_i$ is a compact (< 400 tokens) diagnostic produced by `EvidenceGatekeeper` and the independent `AdversarialFalsifierSubAgent` explaining *exact* failing test names, tracebacks, or violated contract invariants.
- **Zero Context Rot Guarantee**: When the ephemeral $L_2$ Executor or $L_1$ Falsifier finishes its 4–10 turn episode, its message history is garbage-collected. Only the ~300-token summary of $s_i$ enters $L_0$'s ledger.

### 3.2. Empirical Node Value $V(s_i)$ & Complexity Regularization

Each checkpoint node $s_i$ is scored strictly by **branch-independent empirical execution** (never by the Executor's self-assessment):

$$V(s_i) = 0.10 \cdot \mathbb{I}[\text{compile\_ok}] + 0.30 \cdot \mathbb{I}[\text{repro\_ok}] + 0.25 \cdot \text{p2p\_score}(s_i) + 0.35 \cdot \mathbb{I}[\text{falsifier\_ok}] - \lambda_{\text{bloat}}(s_i)$$

where:
- $\text{p2p\_score}(s_i) = \max\bigl(0.0,\; 1.0 - 0.25 \cdot n_{\text{new\_failures}}\bigr)$ if `repo_tests` ran,
- $\lambda_{\text{bloat}}(s_i) = \min\bigl(0.15,\; 0.001 \cdot \max(0, \text{diff\_lines} - 80)\bigr)$ penalizes sprawling `if/else` band-aid diffs when a surgical upstream fix exists.
- A node achieves **Certified Global Optimum ($V(s_i) = 1.0$, early termination)** if and only if:
  $$\text{compile\_ok} \;\land\; \text{repro\_ok} \;\land\; \text{repo\_tests\_ok} \;\land\; \text{falsifier\_ok}$$

### 3.3. Adaptive Branching: `GEN` (Move 37 Exploration) vs. `CONT` (Delta Refinement)

At the start of each episode $t \in \{1, \dots, E_{\max}\}$, $L_0$ chooses between expanding a new causal branch (`GEN`) or refining an existing checkpoint (`CONT(s_i)`):

1. **`CONT(s_i)` Posterior ($\text{Beta}(\alpha_i, \beta_i)$)**:
   - Initialized from $V(s_i)$:
     $$\alpha_i = 1.0 + 4.0 \cdot V(s_i), \qquad \beta_i = 1.0 + 4.0 \cdot (1.0 - V(s_i)) + 2.0 \cdot \text{stagnant\_steps}(s_i)$$
   - When $s_i$ passes `repro_ok=True` and fails only a single P2P regression test (e.g. `astroid-934`, `astroid-983`) or a single Falsifier contract check (e.g. `marshmallow-1702`, `astroid-1417`), $V(s_i) \approx 0.60\text{–}0.65$, giving `CONT(s_i)` a high Thompson sample to immediately spawn a **fresh-context 6-turn Delta-Refinement Sub-Agent** starting with $s_i$'s `git diff` already applied in `/testbed`.
   - **Stagnation Cutoff**: If a `CONT` child $s_{i,1}$ fails to strictly improve $V(s_{i,1}) > V(s_i)$, `stagnant_steps(s_i)` increments and $\beta_i \mathrel{+}= 2.5$. After 2 stagnant refinements, `CONT` on that branch is suspended, forcing the search out of the local optimum!

2. **`GEN` Posterior ($\text{Beta}(\alpha_{\text{gen}}, \beta_{\text{gen}})$ — The "Move 37" Forcing Function)**:
   - Whenever the tree has no `repro_ok=True` branch yet, or whenever existing branches have stagnated / been falsified on structural grounds, `GEN` is selected to launch an **orthogonal causal branch** from clean `HEAD`.
   - **Abstraction-Stratified Causal Loci**: Sibling `GEN` branches are prohibited from repeating the same mechanism. $L_0$ stratifies `GEN` branches across three orthogonal loci:
     1. **`consumer_local` (Symptom / Call-Site Locus)**: Minimal guard or transformation at the immediate function/method where the bug manifests.
     2. **`producer_upstream` ("Move 37" / Upstream Invariant Locus)**: Traces backward along the call/data-flow chain in the `ReconBrief` to fix *where the invalid state, context, AST node, or segment was constructed or mutated upstream*.
     3. **`codebase_symmetry` (Sibling / Convention Locus)**: Replicates the exact structural pattern used by sibling classes, rules, or decorators elsewhere in the repository.

---

## 4. The Three Preventing Traps: How V13 Eliminates Local Optima

| Failure Mode in Flat/Linear Agents | V13 Architectural Countermeasure |
| :--- | :--- |
| **1. Context Window Exhaustion & Lossy Summarization** | Every sub-agent ($L_1$-Recon, $L_2$-Executor, $L_1$-Falsifier) runs for only **4–10 turns in a brand-new context window** and is garbage-collected upon return. Only structured artifacts (`ReconBrief`, `git diff`, `VerificationContract`, `Critique`) cross layer boundaries. |
| **2. Fake Global Optima ($V=1.0$ from Weak Self-Tests)** | **Cumulative Adversarial Falsification**: An Executor never grades its own patch. Every candidate patch with `repro_ok=True` must survive an independent **$L_1$ Adversarial Falsifier Sub-Agent** in a fresh context. Any counterexample found by the Falsifier is added to `/testbed/.hae/falsification_suite.py` and enforced across **all** branches in the AB-MCTS tree. |
| **3. Tunnel Vision in the Wrong Architectural Layer (Missing "Move 37")** | **Negative-Ledger `GEN` + Stagnation Cutoffs**: When a local symptom patch fails falsification or stagnates ($\Delta V \le 0$), AB-MCTS penalizes its $\beta$ posterior and triggers `GEN` with a **Negative Evidence Ledger** of all prior branches, forcing $L_2$ to execute the `producer_upstream` ("Move 37") or `codebase_symmetry` branch from a clean checkout. |

---

## 5. End-to-End Execution Lifecycle in Code (`hae/swebench/hierarchical_mcts.py`)

1. **Stage 0 — Fast Gatekeeper Calibration (`SweBenchFirmRunner`)**:
   - Seeds initial candidate modules and calibrates `EvidenceGatekeeper` (`whole_suite` if $\le 15\text{s}$, else fast `proximity` test suite).
2. **Stage 1 — Layer $L_1$ Reconnaissance Episode (`ReconnaissanceSubAgent`, 4–6 turns)**:
   - Uses `bash` (`git grep -n`, `git log -n 3`) and `view_file` to map:
     - Exact file paths & line ranges for both the **consumer (symptom site)** and **producer (upstream caller/builder)**,
     - Existing test files and repo-specific conventions (e.g. custom warning classes like `RemovedInMarshmallow4Warning`, deprecation message phrasing),
   - Creates `/testbed/.hae/reproduce_issue.py` and verifies that running it on clean `HEAD` **fails (non-zero exit code)** as expected.
   - Emits `submit` with a structured `ReconBrief`.
3. **Stage 2 — Layer $L_0$ Seed Contract & Stratified Hypothesis Generation (`SeedArchitectNode`, 1 LLM call)**:
   - Reads Problem Statement + `ReconBrief` in a clean context.
   - Outputs JSON `VerificationContract` (explicit checklist of requirements, edge cases, warning classes, and sibling methods) + 3 stratified `CausalBranchSpec` plans (`consumer_local`, `producer_upstream`, `codebase_symmetry`).
4. **Stage 3 — Agentic AB-MCTS Episode Loop (`max_episodes = 3..4`)**:
   - **Step 3a (`select_next_operation`)**: Thompson Sampling over `AgenticMCTSNode` Beta posteriors selects either `GEN(next_orthogonal_branch)` or `CONT(best_expandable_node)`.
   - **Step 3b (`run_branch_executor`)**:
     - If `GEN`: resets `/testbed` to clean `HEAD` (keeping `/testbed/.hae/reproduce_issue.py` and `/testbed/.hae/falsification_suite.py`) and runs a fresh-context **$L_2$ Branch Executor** (up to 9 turns) to implement the branch's `CausalBranchSpec`.
     - If `CONT(s_i)`: restores $s_i$'s `git diff` into `/testbed` and runs a fresh-context **$L_2$ Delta-Refinement Executor** (up to 7 turns) with $s_i$'s diff and exact `Critique` to fix the failing regression or edge case in place.
   - **Step 3c (`verify_and_falsify_node`)**:
     - Runs `EvidenceGatekeeper` (`py_compile`, `static_check`, `repo_tests`) + `/testbed/.hae/reproduce_issue.py` + `/testbed/.hae/falsification_suite.py`.
     - If `compile_ok`, `repro_ok`, and `repo_tests_ok` all hold, spawns a fresh-context **$L_1$ Adversarial Falsifier Sub-Agent** (up to 5 turns) to audit the diff against $L_0$'s `VerificationContract` and run `/testbed/.hae/falsification_suite.py`.
     - Updates $s_{\text{new}}$'s empirical value $V(s_{\text{new}})$ and Beta posterior $(\alpha, \beta)$ in $L_0$'s tree.
     - If $V(s_{\text{new}}) == 1.0$ (certified by both Gatekeeper and Independent Falsifier), terminates immediately!
5. **Stage 4 — Best-Node Restoration & Epistemic Ledger Certification**:
   - Restores the highest-ranked `AgenticMCTSNode` checkpoint across the entire AB-MCTS tree, cleans any untracked files outside `.hae/`, and records the full tree and certification trail into `EpistemicState`.
