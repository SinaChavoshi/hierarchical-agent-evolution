# V16: "Company DNA" — Evolvable Roles & CEO Policy on the Hierarchical Harness

> **Status (Oct 29, 2026):** Training (`21` tasks, 6 generations of `evolve` + matched `random`) and 2-replicate Validation (`10` tasks × `2` runs) are **complete**. The interleaved 2-replicate Held-Out Test (`28` unseen `SWE-bench Verified` tasks × `2` runs across `A`, `B`, `C`, `D`, `E` + `V12`, followed by official `swebench` container re-grading) is **actively running** on GKE.

V15 evolved the harness code and knobs (`12 / 28 → 17 / 28` on unseen `SWE-bench Verified`). The seed genome also carried a role library and a CEO policy, but the hierarchical engine never bound them. **V16** introduces **Company DNA**—representing an engineering organization as compact one-line specialist goals, 3–8 line imperative playbooks, and a UCB + tag-overlap `CEOPolicyGene`—and tests whether staffing and evolving that organization improves on the V15 champion.

---

## 1. Research Questions

1. **Static vs. Dynamic Staffing:** Does staffing the harness layers from a failure-mode specialist library (`B`), and dynamically routing branch-matched executors + swapping stalled roles mid-run (`C`), resolve more held-out tasks than the unstaffed V15 champion (`A`)?
2. **Evolving Company DNA vs. Random Search:** Does jointly evolving specialist playbooks, role composition, CEO policy, knobs, and harness code (`D`) outperform matched-budget random search (`E`)?

---

## 2. Architectural Changes

| Component | Change |
| :--- | :--- |
| **Infra (Phase 0)** | Pre-warmed **single-use** sandbox pods (`2 CPU / 6 GiB`, never reused across jobs); interleaved `(genome, task)` round-robin scheduling; `INFRA_SYNC_FILES` synced into every candidate worktree; `kubectl` and LLM transport retries; `llm-d` Endpoint Picker re-weighted (`queue-scorer: 3, kv-cache-utilization-scorer: 2, prefix-cache-scorer: 1`). |
| **"Company DNA" Roles (Phase 1)** | `RoleAllele` gains `layer`, `playbook` (3–8 one-line imperative steps), `failure_modes`, and `is_generalist`. Refreshed from a redacted V15 train/val failure digest (`scripts/v16_refresh_roles.py`, full audit in [role_refresh_report.md](role_refresh_report.md)): **15 roles** (**5 generalists** + **10 failure-mode specialists**). |
| **External Binding (Phase 2)** | `hae/swebench/org_binding.py` (non-evolvable infrastructure) wraps `_call_specialist_turn` from outside the engine so V15 patches apply cleanly. `org_mode` is `off`, `static`, or `dynamic`. |
| **Org Evolution (Phase 3)** | `HarnessGenome` gains `org_mode`, `roles`, and `ceo_policy`. New operators in `hae/evolution/org_evolution.py`: `role_rewrite`, `role_add`, `role_remove`, `mutate_ceo`, and `org_crossover`. Role $\Delta V$ credits from training runs feed back into UCB staffing. |

### How "Company DNA" Binding Works

- **Byte-Identical Generalist Controls:** Every layer retains an immutable `is_generalist=True` baseline allele that appends zero tokens—so when generalists are staffed, prompts are byte-identical to V15 (`tests/test_v16_org.py`).
- **Compact Specialist Injection:** When the CEO hires a specialist, `org_binding` appends one concise `## Organization assignment` block containing the role's 1-line `goal`, `playbook` steps, and guarded `failure_modes`.
- **`static` Mode:** At task start, the CEO scores each layer's alleles via empirical $\Delta V$ utility $\times$ issue-tag overlap + UCB exploration, hiring a specialist only when its score beats the generalist.
- **`dynamic` Mode:** Adds two runtime decisions:
  1. **Locus-Aware Branch Routing:** Each `AB-MCTS` branch (`consumer_local`, `producer_upstream` *"Move 37"*, `codebase_symmetry`, `counterfactual_move37`) adds its causal locus to the staffing tags so upstream/symmetry branches recruit matching executors (e.g., *Multi-File Parity Executor*).
  2. **Mid-Run Stall Swaps:** When $\Delta V < \text{stall\_delta\_u}$ for `stall_moves` episodes, the stalled layer swaps to the highest-scoring untried specialist.
- **Anti-Leakage Guardrails:** Every role mutation passes `RoleGates` and `LeakageGate` built over all train, validation, and test patches, plus a strict repository/instance-ID ban list and file-path filter.

---

## 3. Experimental Arms & Protocol

All arms inherit the V15 champion `evo_g5_03` source patch and knobs, the V15 splits (`data/swebench/v15_splits.json`), and the identical GKE cluster commit.

| Arm | Organization Configuration | Selection |
| :--- | :--- | :--- |
| **`A`** | `org_mode=off` (exact V15 champion `evo_g5_03`) | Fixed |
| **`B`** | Refreshed 15-role library, `org_mode=static` | Fixed |
| **`C`** | Refreshed 15-role library, `org_mode=dynamic` (locus-matched executors + stall swaps) | Fixed |
| **`D`** | Full evolution from `C` (code, knobs, 1-line roles, CEO policy; 6 gens, pop 8, stage-1 = 6 → top 3 on 21 train, `p_org=0.4`) | Top 3 train → best on 2×10 val |
| **`E`** | Matched-budget random search from `C` (random knobs + random specialist subsets + uniform CEO genes + random mode; 6 gens, pop 8) | Top 3 train → best on 2×10 val |
| **`V12`** | Flat ReAct baseline (`v12_agentic_swebench_firm_0.json`, matched `budget_moves=68`) | Fixed |

---

## 4. Interim Results (Completed Train & Validation; Test In Progress)

### 4.1 Training Split (`21` Dev Tasks — Complete)

- **Fixed baselines (`train_arms_2026-10-29`):** `A = 4 / 21`, `B = 4 / 21`, `C = 4 / 21`.
- **Evolution (`D`, 6 generations, `evolve_2026-10-29`):**
  - Top promoted elites: **`D1 = evo_g5_04`** (`role_remove`, **`6 / 21`** = `28.6%`, fitness `0.2943`, `dynamic`), **`D2 = evo_g1_07`** (`mutate_ceo`, **`6 / 21`** = `28.6%`, fitness `0.2925`, `dynamic`), and **`D3 = evo_g1_02`** (`mutate_ceo`, **`5 / 21`** = `23.8%`, fitness `0.2668`, `dynamic`).
- **Random Search (`E`, 6 generations, `random_2026-10-29`):**
  - Top promoted elites: **`E1 = ran_g0_02`** (**`6 / 21`** = `28.6%`, fitness `0.2996`, `static`), **`E2 = ran_g4_04`** (**`6 / 21`**, fitness `0.2984`), and **`E3 = ran_g4_05`** (**`6 / 21`**, fitness `0.2829`).

### 4.2 Held-Out Validation Split (`10` Tasks × `2` Replicates — Complete)

| Arm / Candidate | Mode & Origin | Rep 1 (`/10`) | Rep 2 (`/10`) | Mean Resolves | Majority (`2/2` Resolves) | Run-to-Run Agreement | Validation Outcome |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **`A`** (`evo_g5_03`) | `off` (V15 champion) | `4` | `4` | **`4.0 / 10`** (`40%`) | `3 / 10` | `0.80` | Fixed control |
| **`B`** | `static` (15-role library) | `4` | `5` | **`4.5 / 10`** (`45%`) | `4 / 10` | `0.90` | Fixed static org |
| **`C`** | `dynamic` (locus + stall swaps) | **`5`** | **`5`** | **`5.0 / 10`** (**`50%`**) | **`5 / 10`** | **`1.00`** | Fixed dynamic org (`C` vs `A`: `2:0` discordant wins, `Δ = +0.10`) |
| `D1` (`evo_g5_04`) | `dynamic` (`role_remove`) | `4` | `4` | `4.0 / 10` (`40%`) | `3 / 10` | `0.80` | Not promoted |
| **`D2` (`evo_g1_07`)** | `dynamic` (`mutate_ceo`) | **`6`** | **`4`** | **`5.0 / 10`** (**`50%`**) | **`4 / 10`** | `0.60` | **Promoted to Test as `D`** (`D2` vs `E1`: `Δ = +0.10`, CI `[0.00, +0.25]`) |
| `D3` (`evo_g1_02`) | `dynamic` (`mutate_ceo`) | `4` | `6` | **`5.0 / 10`** (`50%`) | `4 / 10` | `0.60` | Tied `D2` on mean; `D2` won train tie-break |
| **`E1` (`ran_g0_02`)** | `static` (random search) | `5` | `3` | **`4.0 / 10`** (`40%`) | `3 / 10` | `0.60` | **Promoted to Test as `E`** |
| `E2` (`ran_g4_04`) | random search | `4` | `3` | `3.5 / 10` (`35%`) | `2 / 10` | `0.70` | Not promoted |
| `E3` (`ran_g4_05`) | random search | `4` | `4` | `4.0 / 10` (`40%`) | `3 / 10` | `0.80` | Tied `E1` on mean; `E1` won train tie-break |

### 4.3 Diagnostic Discovery from V16 Logs (Direct Motivation for V17)

Auditing all completed V16 training and validation traces surfaced two structural bottlenecks that directly shaped the [V17 experiment plan](../v17/README.md):

1. **`31.5%` Falsifier Certification Precision (`211 / 670` certified training runs):**
   Across **`670` training runs** where the `L1-B` falsifier certified a candidate patch as `PASS` (`27` in `train_arms`, `309` in `evolve`, `334` in `random`; `699` including smoke tests), **only `211` (`31.5%`) actually passed the hidden container tests** (`67 / 108 = 62.0%` on validation). Evolution did not make the falsifier stricter because: (a) falsifier role credit was only `±0.1` per run; (b) `run_falsifier_episode` defaulted to `CERTIFIED` if turns ran out without `"VERDICT: FAIL"` and still had write access to repository files; (c) the 21-task dev split had too few intermediate-difficulty discriminators; and (d) the `68`-move ceiling penalized strict rejections by exhausting steps before repair finished.
2. **Blind Duplicate-Patch Loops & Re-Grepping Under Pure Isolation:**
   Because episodes shared zero memory beyond a short prose critique, refiners occasionally resubmitted the **exact same patch 3× in a row** after falsifier rejection (unable to see or ask for the concrete failing input), and parallel `AB-MCTS` branches repeatedly re-grepped the same repository files.

### 4.4 Held-Out Test (`28` Unseen `SWE-bench Verified` Tasks × `2` Runs — In Progress)

> **Live Run Status:** The interleaved `280`-run hierarchical batch (`A`, `B`, `C`, `D = evo_g1_07`, `E = ran_g0_02` × `28` tasks × `2` replicates) and the concurrent `56`-run `V12` flat ReAct batch (`28` tasks × `2` replicates at `budget_moves=68`) are currently executing on GKE (`results/v16/test_2026-10-29` and `results/v16/v12_test_2026-10-29`). Final test resolve tables, paired sign tests, bootstrap CIs, and official `swebench` container re-grades will be appended here immediately upon batch completion.

| Arm | Description | Rep 1 (`/28`) | Rep 2 (`/28`) | Mean (`/28`) | Official `swebench` Re-Grade | Run Agreement | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **`A`** | `off` (V15 champion `evo_g5_03`) | *running* | *running* | *pending* | *pending* | *pending* | 🔄 Running (`test_2026-10-29`) |
| **`B`** | `static` Company DNA (15 roles) | *running* | *running* | *pending* | *pending* | *pending* | 🔄 Running (`test_2026-10-29`) |
| **`C`** | `dynamic` Company DNA (locus + stall swaps) | *running* | *running* | *pending* | *pending* | *pending* | 🔄 Running (`test_2026-10-29`) |
| **`D`** | Evolved Company DNA (`evo_g1_07`) | *running* | *running* | *pending* | *pending* | *pending* | 🔄 Running (`test_2026-10-29`) |
| **`E`** | Matched-budget Random Search (`ran_g0_02`) | *running* | *running* | *pending* | *pending* | *pending* | 🔄 Running (`test_2026-10-29`) |
| **`V12`** | Flat ReAct (`budget_moves=68`) | *running* | *running* | *pending* | *pending* | *pending* | 🔄 Running (`v12_test_2026-10-29`) |
