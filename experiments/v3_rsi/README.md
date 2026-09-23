# V3 Experiment Set — Level 3 Closed-Loop Recursive Self-Improvement (`Generations 7–9`)

**Status: COMPLETE (`3 / 3` Generations, `30 / 30` Autonomous Firms, `$18.92` Total Spend).**

| Summary Dimension | Details |
| :--- | :--- |
| **What Was Tested** | **Level-3 Closed-Loop Recursive Self-Improvement (RSI):** Can competing 31–63 agent organizations re-implement HAE's own evolutionary breeding engine ([`hae/genome/morphogenesis.py`](../../hae/genome/morphogenesis.py): `MorphogenesisEngine` & `StructuralCrossoverEngine`) against a held-out test suite ([`tests/test_morphogenesis.py`](../../tests/test_morphogenesis.py)), store their verified implementations in concurrent per-firm `genome.code_overlays`, and have the host [`Breeder`](../../hae/genome/breeder.py) dynamically compile and execute the winning firms' evolved `morphogenesis.py` overlays to breed the next generation? |
| **What Was the Outcome** | **1. Zero-Seed Synthesis (`Generation 7`, `10` firms):** Starting from an empty `morphogenesis.py` (`0/10` inherited), **`10 / 10` (`100.0%`)** firms passed all `7/7` held-out tests, **`60%` (`6/10`)** converged on Iteration 1 (`Mean Iterations = 1.50`), and `gen_7_pareto_1` achieved **`96.83` Net Fitness**.<br/>**2. Closed-Loop Self-Breeding (`Generations 8–9`, `20` firms):** `Breeder` dynamically loaded and executed the winning evolved `MorphogenesisEngine` and `StructuralCrossoverEngine` overlays from `Gen 7` and `Gen 8` to breed `Gen 8` (Peak Net **`97.92`**, `gen_8_pareto_2`) and `Gen 9` (Peak Net **`94.89`**, `gen_9_crossover_1`).<br/>**3. Forensic Transcript Audit (`commit 71a7da4`):** Auditing the internal inter-department conversations (`71 KB` per firm) revealed two critical insights: (a) a `BenchmarkVerifier.target_module` lookup bug in `worker.py` pre-seeded `code_overlays["hae/genome/morphogenesis.py"]` into `seed_files` in `Gen 8–9` (`10/10` inherited, fixed in `commit 71a7da4`), and (b) the 1-pass waterfall runtime caused agents to spend `80%+` of their token budget on polite corporate memos (`"MEMORANDUM FOR THE EXECUTIVE COUNCIL"`), directly motivating the **V4 *Dosadi* Multi-Turn Architecture**. |

![V2 & V3 Ground-Truth Self-Hosting & Closed-Loop RSI Trajectory](../v2/assets/v2_v3_evolutionary_trajectory.png)

---

## 1. Generational Ledger Summary (`Generations 7–9`)

| Generation | Target Module | Breeding Engine Executed | Workspace Target Pre-Seeded? (`inherited_files`) | Firms | Peak Net Fitness (Champion) | Population Median Net | Population Mean Net | `100%` Exec (`7/7`) | First-Shot (`Iter 1`) Rate | Mean Iters | Cohort Spend (`$`) |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Gen 7** | `hae/genome/morphogenesis.py` | Kernel Base (`Gen 6` Champions) | **`0 / 10` (`0%` — Blank Slate)** | `10/10` | **`96.83`** (`gen_7_pareto_1`) | `67.88` | `71.77` | **`10/10` (`100%`)** | **`60%` (`6/10`)** | **`1.50`** | `$6.6945` |
| **Gen 8** | `hae/genome/morphogenesis.py` | **Evolved Overlays from `Gen 7`** | **`10 / 10` (`100%` — Overlay-Seeded*)** ⚠️ | `10/10` | **`97.92`** (`gen_8_pareto_2`) | **`79.00`** | **`74.52`** | `10/10` (`100%`) | `80%*` *(Confounded)* | `1.30*` | `$6.3380` |
| **Gen 9** | `hae/genome/morphogenesis.py` | **Evolved Overlays from `Gen 8`** | **`10 / 10` (`100%` — Overlay-Seeded*)** ⚠️ | `10/10` | **`94.89`** (`gen_9_crossover_1`) | `71.91` | `72.71` | `10/10` (`100%`) | `100%*` *(Confounded)* | `1.00*` | **`$5.8843`** |

---

## 2. Concurrent Per-Firm Code Overlays (`genome.code_overlays`)

To enable Level-3 RSI across 10 concurrent firms per generation without corrupting the shared platform kernel (`hae/`), V3 introduced `CompanyGenome.code_overlays: Dict[str, str]` and dynamic sandboxed module loading ([`hae/runtime/overlay.py:get_overlay_class`](../../hae/runtime/overlay.py)):
1. When a firm achieves `100.0%` ground-truth verification on a target module, [`worker.py`](../../hae/orchestration/worker.py) promotes the verified source code into that firm's `genome.code_overlays`.
2. During inter-generational breeding, [`Breeder`](../../hae/genome/breeder.py) dynamically loads each surviving parent firm's `MorphogenesisEngine` and `StructuralCrossoverEngine` classes from `parent_genome.code_overlays["hae/genome/morphogenesis.py"]` (falling back safely to the kernel implementation if an overlay raises an exception).
3. All 30 evolved `morphogenesis.py` overlays across Generations 7, 8, and 9 are archived side-by-side in [`overlays/`](overlays/) (`overlays/generation_7/`, `overlays/generation_8/`, `overlays/generation_9/`).

---

## 3. Forensic Audit & Internal Agent Deliberation Analysis

### 3.1 How Reading the Agent Transcripts Caught the `Gen 8–9` Overlay Seeding Bug (`commit 71a7da4`)
Every scorecard in [`generation_7_results/`](generation_7_results/), [`generation_8_results/`](generation_8_results/), and [`generation_9_results/`](generation_9_results/) records the full departmental briefs (`run_output.departmental_briefs`) and CEO synthesis (`run_output.final_deliverable`).

Inspecting the **Product & Developer Experience (`dept_product_ux`)** brief inside Generation 9 Champion [`gen_9_crossover_1_result.json`](generation_9_results/gen_9_crossover_1_result.json) revealed the following memo to the CEO:
> *"Our primary finding is one of ruthless efficiency: **the mandated precision fix for temperature crossover is already implemented in the current `hae/genome/morphogenesis.py` module.** ... This allows us to bypass implementation and proceed directly to surgical verification."*

Auditing `run_output.inherited_files` across all 90 scorecards confirmed:
- **Generations 1–7 (`70` firms):** `target in inherited_files = 0 / 10` (`0%`). All 10 firms in **Generation 7** started with **no `morphogenesis.py` on disk** (`code_overlays` only held `artifacts.py` from Gen 6) and genuinely synthesized `hae/genome/morphogenesis.py` from scratch (`60%` Iter-1 convergence).
- **Generations 8 & 9 (`20` firms):** `target in inherited_files = 10 / 10` (`100%`).
- **Root Cause & Fix (`commit 71a7da4`):** In [`hae/orchestration/worker.py`](../../hae/orchestration/worker.py), the filter intended to exclude the active benchmark's `target_module` from `seed_files` when `carry_artifacts=False` checked `getattr(task.verifier, "target_module", None)`. Because `BenchmarkVerifier` stores `target_module` at `verifier.benchmark.task(verifier.task_id).target_module`, `target_mod` evaluated to `None`. Once Gen 7 promoted `hae/genome/morphogenesis.py` into `code_overlays`, `worker.py` copied the parent's `100%`-passing `morphogenesis.py` into `seed_files` at the start of Gen 8 and Gen 9, where the Monotonic Verification Guard ([`hae/runtime/workspace.py`](../../hae/runtime/workspace.py)) then blocked any overwrite scoring `< 100.0%`. This bug was fixed in `commit 71a7da4`.

### 3.2 Internal Conflict Resolution & "Corporate Memo Theatre" (Why V4 Needs *The Dosadi Experiment*)
The internal transcripts from `gen_8_pareto_2` (`97.92` Net Fitness) and `gen_9_crossover_1` (`94.89` Net Fitness) also showed both the strength and the limitation of the V1–V3 4-stage waterfall runtime:
- **Strength (Cross-Departmental Error Catching):** When `dept_systems_eng` proposed adding third-party `from opentelemetry import trace, metrics` instrumentation to `morphogenesis.py` (which crashes in the hermetic `unshare -rn` sandbox), both `dept_qa_redteam` (Adversarial Red Team) and `dept_finance_ops` challenged the proposal in their briefs, and the CEO (`Gemini 2.5 Pro`) explicitly overruled Systems Engineering in `final_deliverable` (*"The Systems Architect's proposal, when stripped of its non-standard dependencies, provides the most robust foundation"*).
- **Limitation ("Corporate Memo Theatre"):** Because agents in V1–V3 only spoke once per waterfall pass (`CEO -> Workers -> VPs -> Red Team -> CEO`) and `70%` of the fitness score came from an LLM Judge grading executive prose rubric dimensions, each company generated **`56,000–71,000` characters (`~500,000+` tokens) of formal corporate memos** (`MEMORANDUM FOR THE EXECUTIVE COUNCIL`, `"This is excellent news"`, `"Respectfully, without compromise"`) rather than engaging in crisp, multi-turn engineering back-and-forth. This finding directly inspired the **V4 *Dosadi* Multi-Turn Architecture**.

---

## 4. Artifact Index

- [`CAMPAIGN_STATUS.md`](CAMPAIGN_STATUS.md) — Generation 7–9 status table and forensic audit note
- [`ledger_level3_rsi.json`](ledger_level3_rsi.json) — Machine-readable summary ledger for Generations 7–9
- [`generation_7_results/`](generation_7_results/) — Full JSON scorecards & transcripts for Generation 7 (`10` firms, Zero-Seed)
- [`generation_8_results/`](generation_8_results/) — Full JSON scorecards & transcripts for Generation 8 (`10` firms, Closed-Loop Bootstrap #1)
- [`generation_9_results/`](generation_9_results/) — Full JSON scorecards & transcripts for Generation 9 (`10` firms, Closed-Loop Bootstrap #2)
- [`overlays/`](overlays/) — Concurrent per-firm evolved Python modules (`generation_7/`, `generation_8/`, `generation_9/`)
