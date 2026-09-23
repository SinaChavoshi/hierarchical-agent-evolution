# Master Experiment Ledger (`V1` $\to$ `V5`)

This directory is the empirical record of the **Hierarchical Agent Evolution (HAE)** program. Each experiment set represents a distinct architectural regime, benchmark environment, and evolutionary fitness function.

![V2 & V3 Ground-Truth Self-Hosting & Closed-Loop RSI Trajectory](v2/assets/v2_v3_evolutionary_trajectory.png)

---

## 1. Completed & Planned Experiment Sets

| Set | Generations & Scale | What Was Tested | Empirical Outcome | Status | Detailed README |
| :---: | :--- | :--- | :--- | :---: | :--- |
| **V1** | `Pilot` → `Gen 10`<br/>*(13 runs, ~100 firms)* | **Open-Ended Prose & Heuristic Sandbox Evolution:** Evolving 31–34 agent hierarchies on strategic/architectural prompts graded by an LLM Judge (`95%`) + 4-gate regex heuristics. | Published prose fitness climbed (`25.25` $\to$ `96.75`), but retroactive physical execution ([`execution_grounded_correction.md`](v1/execution_grounded_correction.md)) proved `corr(gen, exec) = +0.045` (`0/60` passed all 5 gates). Closed & archived. | **Closed & Audited** | [`v1/README.md`](v1/README.md) |
| **V2** | `Gen 1` → `Gen 6`<br/>*(`60 / 60` firms, `$28.18`)* | **Ground-Truth Self-Hosting (`artifacts.py`):** Zero-seed (`carry_artifacts=False`) synthesis against held-out unit tests in `unshare -rn` under Single-Pass (`Gen 1–3`, `iter=1`) vs. Multi-Iteration Self-Repair (`Gen 4–6`, `iter=10`) + Monotonic Verification Guard. | **Single-Pass (`Gen 1–3`):** Elite fitness rose `+14.56` pts (`94.16` peak, `85.7%` tests), `0/30` at `100%`.<br/>**Self-Repair (`Gen 4–6`):** **`30 / 30` (`100%`) firms passed `7/7` tests**, Peak Net Fitness hit **`98.81`** (`gen_5_pareto_2`), mean iterations dropped **`2.20` $\to$ `1.30`**, Iter-1 pass rate reached **`70%`**, and spend fell **`33%`** (`$7.26` $\to$ `$4.87`). | **Complete** | [`v2/README.md`](v2/README.md) |
| **V3** | `Gen 7` → `Gen 9`<br/>*(`30 / 30` firms, `$18.92`)* | **Level-3 Closed-Loop Recursive Self-Improvement (`morphogenesis.py` + `code_overlays`):** Firms re-implement HAE's own breeding engine (`MorphogenesisEngine` & `StructuralCrossoverEngine`); `Breeder` dynamically compiles and executes the winning overlays to breed `Gen 8` and `Gen 9`. | **Gen 7 (Zero-Seed):** `0/10` inherited; **`10/10` (`100%`) passed `7/7` tests** (`60%` Iter-1 pass rate, `1.50` iters, **`96.83` Peak**).<br/>**Gen 8–9 (Closed-Loop Breeding & Audit):** `Breeder` successfully bred `Gen 8` (**`97.92` Peak**) and `Gen 9` (**`94.89` Peak**) using evolved overlays. Transcript audit caught a `worker.py` `target_module` filter bug (`10/10` inherited in `Gen 8–9`, fixed in `71a7da4`) and exposed `56 KB/run` of "Corporate Memo Theatre." | **Complete** | [`v3_rsi/README.md`](v3_rsi/README.md) |
| **V4** | *Next Campaign*<br/>*(`Gen 10–12`, In-Cluster `Qwen3`)* | ***The Dosadi Experiment* & Real-World `google/gvisor` Bugs:** Replace 1-pass corporate memos with **stateful multi-turn peer dialogue** (`@Lead` $\leftrightarrow$ `@Eng` $\leftrightarrow$ `@Verifier`), active **Signal-to-Noise Density (`Dosadi`) scoring** (no hard token cap, heavy fluff penalty + peer course-correction bonus), and real multi-file production issues from [`google/gvisor`](https://github.com/google/gvisor) (`pkg/shim/v1` Issues `#14405` & `#13509`). | *Architecture & benchmark designed; ready for in-cluster `vLLM` (`Qwen3`) execution.* | **Next / Active Design** | [Main Roadmap](../README.md#4-future-roadmap-v4-the-dosadi-experiment--v5-rl-specialization) |
| **V5** | *Future Phase*<br/>*(LoRA `GRPO` / `DPO`)* | **Parametric RL Specialization & Office Delegation:** Distill V4's winning ultra-crisp *Dosadi* multi-turn trajectories vs. losing verbose trajectories into model weights via **GRPO / DPO** so the workforce internalizes zero-noise collaboration for daily office delegation. | *Planned following V4 trajectory harvest.* | **Roadmap** | [Main Roadmap](../README.md#4-future-roadmap-v4-the-dosadi-experiment--v5-rl-specialization) |

---

## 2. Quick Navigation

- **V1 Archive (`Pilot` $\to$ `Gen 10`):** [`v1/README.md`](v1/README.md) | [`v1/execution_grounded_correction.md`](v1/execution_grounded_correction.md)
- **V2 Self-Hosting Ledger (`Gen 1` $\to$ `Gen 6`):** [`v2/README.md`](v2/README.md) | [`v2/ledger_v2.json`](v2/ledger_v2.json)
- **V3 Level-3 Closed-Loop RSI Ledger (`Gen 7` $\to$ `Gen 9`):** [`v3_rsi/README.md`](v3_rsi/README.md) | [`v3_rsi/CAMPAIGN_STATUS.md`](v3_rsi/CAMPAIGN_STATUS.md) | [`v3_rsi/overlays/`](v3_rsi/overlays/)
