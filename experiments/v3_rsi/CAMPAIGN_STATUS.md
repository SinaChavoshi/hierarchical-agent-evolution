# V3 Campaign Status — Level 3 Closed-Loop Recursive Self-Improvement (`Generations 7–9`)

| Generation | Target Module | Breeding Engine | Workspace Seeding (`target` in `inherited_files`) | Firms | Best Net Fitness | Best Firm | Mean Net Fitness | 100% Exec (`7/7`) | First-Shot (`Iter 1`) | Mean Iters | Spend (`$`) |
| :---: | :--- | :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **Gen 7** | `hae/genome/morphogenesis.py` | Kernel Base (`Gen 6` Champions) | **`0 / 10` (`0%` — Zero-Seed Blank Slate)** | `10/10` | **`96.83`** | `gen_7_pareto_1` | **`71.77`** | `10/10` | **`60%` (`6/10`)** | `1.5` | `$6.6945` |
| **Gen 8** | `hae/genome/morphogenesis.py` | Evolved Overlays from `Gen 7` | **`10 / 10` (`100%` — Overlay-Seeded*)** ⚠️ | `10/10` | **`97.92`** | `gen_8_pareto_2` | **`74.52`** | `10/10` | `80%` *(Confounded)* | `1.3` | `$6.3380` |
| **Gen 9** | `hae/genome/morphogenesis.py` | Evolved Overlays from `Gen 8` | **`10 / 10` (`100%` — Overlay-Seeded*)** ⚠️ | `10/10` | **`94.89`** | `gen_9_crossover_1` | **`72.71`** | `10/10` | `100%` *(Confounded)* | `1.0` | `$5.8843` |

---

## Forensic Audit Note — Zero-Seed Synthesis (`Gen 7`) vs. Overlay-Seeded Execution (`Gen 8–9`)

> [!WARNING]
> **Post-Run Transcript & Workspace Audit (`commit 71a7da4`):**
> Inspecting the inter-agent deliberation transcripts (`run_output.departmental_briefs.dept_product_ux` in `gen_9_crossover_1_result.json`) and `run_output.inherited_files` across Generations 7–9 revealed an important experimental confound separating **Generation 7** (true zero-seed synthesis) from **Generations 8 and 9** (overlay-seeded execution):
>
> 1. **Generation 7 (`0 / 10` Inherited — True Zero-Seed Synthesis):**
>    - In Generation 7, `genome.code_overlays` contained only `hae/evaluation/artifacts.py` (inherited from Generation 6). Zero firms had `hae/genome/morphogenesis.py` in `inherited_files` (`0/10`).
>    - All 10 Generation 7 firms genuinely synthesized `hae/genome/morphogenesis.py` **from a blank slate**, achieving **`10 / 10` (`100.0%`) ground-truth test verification**, **`60%` (`6/10`) first-shot (`Iteration 1/10`) convergence**, `1.50` mean iterations, and **`96.83` peak Net Fitness** (`gen_7_pareto_1`).
>    - Furthermore, the host [`Breeder`](../../hae/genome/breeder.py) genuinely compiled and executed Generation 7's and Generation 8's winning `MorphogenesisEngine` and `StructuralCrossoverEngine` overlays via [`get_overlay_class`](../../hae/runtime/overlay.py) to breed Generation 8 and Generation 9.
> 2. **Generations 8 & 9 (`10 / 10` Inherited — Overlay Pre-Seeding Confound):**
>    - At the end of Generation 7, each firm's `100%`-verified `hae/genome/morphogenesis.py` was promoted into `genome.code_overlays["hae/genome/morphogenesis.py"]` so `Breeder` could execute it during inter-generational breeding.
>    - In [`hae/orchestration/worker.py`](../../hae/orchestration/worker.py), the filter intended to strip the active benchmark target module from `seed_files` when `carry_artifacts=False` looked up `getattr(task.verifier, "target_module", None)`. Because `task.verifier` is a [`BenchmarkVerifier`](../../hae/task/verifier.py) instance where `target_module` resides at `task.verifier.benchmark.task(task.verifier.task_id).target_module`, `getattr(task.verifier, "target_module", None)` returned `None`.
>    - Consequently, `worker.py` failed to strip `hae/genome/morphogenesis.py` from `seed_files` in **Generations 8 and 9**, pre-populating each child pod's workspace with its parent's `100.0%`-passing `hae/genome/morphogenesis.py` at the start of Iteration 1 (`target in inherited_files = 10/10`).
>    - Combined with the **Monotonic Verification Guard** in [`hae/runtime/workspace.py`](../../hae/runtime/workspace.py) (which sees `old_res.score = 100.0%` on disk and blocks any `write_file` call on `morphogenesis.py` that scores `< 100.0%`), Generations 8 and 9 measured **overlay preservation and architectural verification** rather than zero-seed code synthesis (`80%` and `100%` Iter-1 convergence; the only 2 firms in Gen 8 that required `>1` iteration did so because an agent modified `hae/genome/schema.py`, which was not protected by the `morphogenesis.py` guard).
> 3. **Remediation (`commit 71a7da4`):**
>    - [`hae/orchestration/worker.py`](../../hae/orchestration/worker.py) has been patched (`commit 71a7da4`) to resolve `target_mod` from `verifier.benchmark.task(verifier.task_id).target_module` and strip `target_mod` from `seed_files` whenever `carry_artifacts=False`, ensuring future closed-loop RSI generations retain `code_overlays` for `Breeder` without leaking the active target module into the child's workspace.
