# HAE V12 Evaluation Report — Agentic Epistemic Re-Architecture (`46.7%` on SWE-bench)

**Date**: October 14, 2026  
**Cluster**: `gke_gemle-gke-dev_us-central1-b_chavoshi-v6-cohort` (`12 × NVIDIA RTX PRO 6000 Blackwell Server Edition GPUs`, `6 × qwen38-flash-next-180b` pods)  
**Model**: Self-hosted `nvidia/Qwen3.8-Flash-Next-NVFP4` (`125B` total / `6B` active MoE, TP=2)  
**Benchmark Cohort**: 30 Two-Control-Validated `SWE-bench` `dev` Instances (`results/swebench/dev_eval_2026-10-09/v12_agentic/`)

---

## 1. Executive Summary

Following the V11 post-mortem (`6 / 30`, `20.0%`), we stepped back from incremental tuning of V6-era machinery and conducted a first-principles audit of every layer between `nvidia/Qwen3.8-Flash-Next-NVFP4` and the `/testbed` container. Our guiding design principle was explicit:

> **Guiding Principle**: *The goal is to match or beat the official `Qwen3.8-Flash-Next` SWE-bench benchmark (`~42%–48%`), not lag behind it. Every V6 leftover that restricts the model's context, strips its reasoning, caches stale responses, or forces blind one-shot JSON code generation is a detriment to the architecture and must be replaced.*

By eliminating **six foundational V6 architectural bottlenecks** and introducing the **V12 Agentic Epistemic Engine** (`hae/swebench/agentic.py`), HAE V12 achieved **`14 / 30` (`46.7%`) verified resolved instances** on the exact same 30-instance SWE-bench cohort and self-hosted `Qwen3.8-Flash-Next-NVFP4` weights — a **`2.33x` increase over V11 (`20.0%`)**, **`2.80x` over V10 (`16.7%`)**, **`4.67x` over V9 (`10.0%`)**, and **`3.50x` over V8 (`13.3%`)**, squarely matching the model's official agentic SWE-bench capability (`42%–48%`).

| Policy Version | Architecture Paradigm | Resolved (`N=30`) | Resolution Rate | Total `FAIL_TO_PASS` Tests Passed | Relative Gain vs. V8 | Relative Gain vs. V11 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **V8 (`v8_puct`)** | V6 PUCT + Stateless 1-Shot JSON Synthesis | `4 / 30` | `13.3%` | `4` | `1.00x` | `0.67x` |
| **V9 (`v9_ab_mcts`)** | V6 AB-MCTS + Uncalibrated Value Head | `3 / 30` | `10.0%` | `3` | `0.75x` | `0.50x` |
| **V10 (`v10_ab_mcts`)** | V6 AB-MCTS + EPLB Prior + Unbounded Retries | `5 / 30` | `16.7%` | `5` | `1.25x` | `0.83x` |
| **V11 (`v11_ab_mcts`)** | V6 AB-MCTS + 2-Failure Cap + Top-3 Sibling Seed | `6 / 30` | `20.0%` | `6` | `1.50x` | `1.00x` |
| **V12 (`v12_agentic`)** | **Multi-Turn Stateful Agentic Epistemic Engine** | **`14 / 30`** | **`46.7%`** | **`98`** | **`3.50x`** | **`2.33x`** |

---

## 2. Deep Architectural Audit: The Six V6 Leftovers Eliminated in V12

Our codebase audit identified six specific V6 design artifacts that artificially capped `nvidia/Qwen3.8-Flash-Next-NVFP4` at `10%–20%`:

### Bottleneck 1: Thinking Stripped at the Gateway & LLM Client (`enable_thinking: False`)
- **V6 Leftover**: `hae/infra/llm.py` defaulted `VLLM_ENABLE_THINKING="0"`, and `deploy/gke/llmd_gateway.py` injected `chat_template_kwargs={"enable_thinking": False}` on every request, stripping Qwen3.8's chain-of-thought `<think>...</think>` reasoning before every localization and code-editing decision.
- **V12 Fix**: Enabled native per-turn `<think>...</think>` reasoning (`enable_thinking=True`) across the V12 agentic loop, updated `hae/infra/llm.py` to detect unclosed `<think>` monologues (`"</think>" not in raw_text`) so internal thoughts are never misparsed as tool calls, and added automatic single-turn `enable_thinking=False` recovery whenever a turn hits the token cap mid-thought.

### Bottleneck 2: Gateway Response Cache Short-Circuiting Multi-Turn Retries (`HIT-SHORT-CIRCUIT`)
- **V6 Leftover**: `deploy/gke/llmd_gateway.py` cached responses in `response_cache` keyed by normalized prompt text whenever `caller_company == "anon"` (which is true for all SWE-bench runs!), returning identical stale outputs even when `temperature > 0` was raised on retry turns. Furthermore, `llmd_gateway.py` overwrote `data["max_tokens"] = 16384` whenever `response_format` was unset.
- **V12 Fix**: Updated `deploy/gke/llmd_gateway.py` (and rolled out `configmap/llmd-gateway-script` on GKE) so `allow_cache = False` whenever `caller_company == "anon"` or `X-No-Cache: 1` is set, while preserving prefix KV-cache affinity routing (`select_backend(prefix_key)`) and honoring explicit caller `max_tokens`.

### Bottleneck 3: Stateless Single-Turn `call_llm(prompt, system_instruction)` Without Conversation History
- **V6 Leftover**: Every LLM call in V6–V11 constructed a single `[system, user]` message pair from scratch and discarded the conversation immediately after receiving one JSON response. The model had no memory of what commands it had already run, what lines it had inspected, or why its previous edit failed.
- **V12 Fix**: Extended `call_llm` and `call_openai_compatible_rest` in `hae/infra/llm.py` to accept full multi-turn `messages: List[Dict[str, str]]`, and added sliding-window observation condensation (`_condense_messages`) in `hae/swebench/agentic.py` to preserve full recent turns while keeping total context compact.

### Bottleneck 4: Blind One-Shot JSON Synthesis (`_synthesize_module`) vs. Iterative `str_replace`
- **V6 Leftover**: In V6–V11, `PROPOSE_HYPOTHESES`, `DESIGN_EXPERIMENT`, and `_synthesize_module` were disconnected single-shot JSON calls. When `_synthesize_module` was called, the model saw only a `14,000`-character snippet (`~300` lines) of a `2,000`-line file, had to emit a JSON `edits` array in one shot without running `pytest`, and if `verify_module` failed, the entire working tree was wiped (`git checkout -- .`) and all diagnostic tracebacks were discarded.
- **V12 Fix**: Replaced blind one-shot JSON synthesis with an interactive multi-turn tool environment (`bash`, `view_file`, `str_replace`, `write_file`, `submit`) inside `/testbed`:
  - `str_replace` supports fenced blocks (` ```str_replace `), Qwen3-Coder XML `<tool_call><function=str_replace>` blocks (`old_str`/`new_str` and `old_string`/`new_string`), and hybrid XML + conflict-marker (`<<<< ... ==== ... >>>>`) blocks.
  - Every `str_replace` is syntax-checked immediately via `py_compile` in `/testbed` and automatically rolled back with exact line-numbered context if it introduces a `SyntaxError`.
  - When a `str_replace` passes `py_compile`, the working tree is kept **in place** so the specialist can immediately run `/testbed/.hae/reproduce_issue.py` and `pytest` and refine its patch iteratively across turns.

### Bottleneck 5: No Issue-Specific Reproduction Test (`reproduce_issue.py`) Before `submit`
- **V6 Leftover**: `EvidenceGatekeeper.verify_module` only ran *existing* repository tests (`PASS_TO_PASS`). Any no-op or partial edit that didn't break existing tests was immediately certified by the self-oracle (`resolved_by_self_oracle = True`), yielding a `78%` false-positive self-oracle rate in V8–V11.
- **V12 Fix**: Required Specialist 1 (`PrimaryRepairSpecialist`) to author and execute a standalone failing reproduction script (`/testbed/.hae/reproduce_issue.py`, isolated inside `.hae/` so untracked files never pollute `/testbed`) and required `EvidenceGatekeeper` pre-submission verification (`verify_current_workspace`) to run both `/testbed/.hae/reproduce_issue.py` (`repro_ok`) and `verify_module` (`repo_tests_ok`) before accepting `submit`.

### Bottleneck 6: Whole-Suite Verification Timeouts on Large Repositories (`sqlfluff`, `astroid`)
- **V6 Leftover**: `WHOLE_SUITE_MAX_S = 30.0` in `hae/epistemic/gatekeeper.py` selected whole-suite `pytest` (`1,100+` tests) whenever the clean checkout finished in `< 30s`, causing `sqlfluff` and `astroid` verification runs under parallel load to time out at `60s`.
- **V12 Fix**: Lowered `WHOLE_SUITE_MAX_S = 15.0` so large suites automatically use fast `proximity` test discovery (`0.4s–3.5s` per gate check) while fast suites remain whole-suite verified.

---

## 3. V12 Multi-Specialist Organizational Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                     HAE V12 Agentic Epistemic Engine                         │
│                        (hae/swebench/agentic.py)                             │
├──────────────────────────────────────────────────────────────────────────────┤
│  Stage 0: Static Seed & Fast Test Discovery (SweBenchFirmRunner)             │
│    • Extract candidate modules & symbols from problem statement              │
│    • Calibrate EvidenceGatekeeper (whole-suite <= 15s vs proximity pytest)   │
├──────────────────────────────────────────────────────────────────────────────┤
│  Stage 1: Specialist 1 — PrimaryReproductionAndRepairSpecialist (21-24 turns)│
│    • Locate root cause via `bash` (`git grep -n`) and `view_file`            │
│    • Author & run `/testbed/.hae/reproduce_issue.py` (must fail on HEAD)     │
│    • Apply surgical edits via `str_replace` (instant `py_compile` guard)     │
│    • Verify `/testbed/.hae/reproduce_issue.py` + `pytest` & checkpoint       │
├──────────────────────────────────────────────────────────────────────────────┤
│  Stage 2A (if Stage 1 certified):                                            │
│    Specialist 2 — AdversarialEdgeCaseAuditor (up to 11 turns)                │
│    • Inspect sibling classes/methods, deprecations, and boundary conditions  │
│    • Author & run `/testbed/.hae/audit_edge_cases.py` + `pytest`             │
│    • Refine patch in place via `str_replace` if any edge case fails          │
│                                                                              │
│  Stage 2B (if Stage 1 did not certify):                                      │
│    Specialist 2 — EscalationRepairSpecialist (up to 14 turns)                │
│    • If Specialist 1 achieved `repro_ok=True`, keep patch applied & fix      │
│      the exact failing `pytest` regression test in place                     │
│    • Otherwise reset tracked files to HEAD and repair from post-mortem       │
├──────────────────────────────────────────────────────────────────────────────┤
│  Stage 3: Epistemic Checkpoint Restoration & Ledger Certification            │
│    • Rank all syntax-valid checkpoints by:                                   │
│      (gatekeeper_ok, repro_ok & repo_tests_ok, audit_ok, repro_ok,           │
│       repo_tests_ok, static_ok, -new_test_failures, checkpoint_index)        │
│    • Restore highest-ranked checkpoint, clean untracked files, record ledger │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Per-Repository Breakdown (V8 vs. V9 vs. V10 vs. V11 vs. V12)

| Repository | Valid Cohort (`N=30`) | V8 (`v8_puct`) | V9 (`v9_ab_mcts`) | V10 (`v10_ab_mcts`) | V11 (`v11_ab_mcts`) | **V12 (`v12_agentic`)** | V12 vs. V11 Gain |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `marshmallow-code/marshmallow` | `9` | `3 / 9` (`33.3%`) | `2 / 9` (`22.2%`) | `2 / 9` (`22.2%`) | `3 / 9` (`33.3%`) | **`6 / 9` (`66.7%`)** | **`+33.4 pp` (`2.00x`)** |
| `pylint-dev/astroid` | `8` | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `3 / 8` (`37.5%`) | `3 / 8` (`37.5%`) | **`5 / 8` (`62.5%`)** | **`+25.0 pp` (`1.67x`)** |
| `pyvista/pyvista` | `1` | `0 / 1` (`0.0%`) | `0 / 1` (`0.0%`) | `0 / 1` (`0.0%`) | `0 / 1` (`0.0%`) | **`1 / 1` (`100.0%`)** | **`+100.0 pp` (First solve!)** |
| `sqlfluff/sqlfluff` | `8` | `0 / 8` (`0.0%`) | `0 / 8` (`0.0%`) | `0 / 8` (`0.0%`) | `0 / 8` (`0.0%`) | **`2 / 8` (`25.0%`)** | **`+25.0 pp` (First solves!)** |
| `pydicom/pydicom` | `4` | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | **`0 / 4` (`0.0%`)** | `0.0 pp` |
| **Total (`N=30`)** | **`30`** | **`4 / 30` (`13.3%`)** | **`3 / 30` (`10.0%`)** | **`5 / 30` (`16.7%`)** | **`6 / 30` (`20.0%`)** | **`14 / 30` (`46.7%`)** | **`+26.7 pp` (`2.33x`)** |

---

## 5. Full 30-Instance Results Table (`v12_agentic` vs. V8–V11)

| # | Instance ID | V8 | V9 | V10 | V11 | **V12 (`v12_agentic`)** | V12 `FAIL_TO_PASS` | V12 `PASS_TO_PASS` | Moves | Patch Chars | Stop Reason |
| :-: | :--- | :-: | :-: | :-: | :-: | :-: | :---: | :---: | :---: | :---: | :--- |
| 1 | `marshmallow-code__marshmallow-1164` | PASS | PASS | PASS | PASS | **PASS** | `1 / 1` | `101 / 101` | `23` | `554` | `resolved_by_agentic_gatekeeper` |
| 2 | `marshmallow-code__marshmallow-1229` | FAIL | FAIL | FAIL | FAIL | Partial (`25%` F2P) | `2 / 8` | `908 / 908` | `33` | `1070` | `resolved_by_agentic_gatekeeper` |
| 3 | `marshmallow-code__marshmallow-1252` | FAIL | FAIL | FAIL | PASS | **PASS** | `1 / 1` | `37 / 37` | `29` | `1368` | `resolved_by_agentic_gatekeeper` |
| 4 | `marshmallow-code__marshmallow-1343` | PASS | FAIL | FAIL | FAIL | **PASS** | `1 / 1` | `24 / 24` | `28` | `1027` | `resolved_by_agentic_gatekeeper` |
| 5 | `marshmallow-code__marshmallow-1359` | PASS | PASS | PASS | PASS | **PASS** | `1 / 1` | `76 / 76` | `17` | `491` | `resolved_by_agentic_gatekeeper` |
| 6 | `marshmallow-code__marshmallow-1524` | FAIL | FAIL | FAIL | FAIL | **PASS** *(New!)* | `5 / 5` | `127 / 127` | `23` | `1557` | `resolved_by_agentic_gatekeeper` |
| 7 | `marshmallow-code__marshmallow-1702` | FAIL | FAIL | FAIL | FAIL | Partial (`50%` F2P) | `14 / 28` | `71 / 71` | `35` | `2265` | `resolved_by_agentic_gatekeeper` |
| 8 | `marshmallow-code__marshmallow-1810` | FAIL | FAIL | FAIL | FAIL | **PASS** *(New!)* | `1 / 1` | `111 / 111` | `19` | `471` | `resolved_by_agentic_gatekeeper` |
| 9 | `marshmallow-code__marshmallow-2123` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 23` | `76 / 145` | `25` | `4544` | `best_checkpoint_restored` |
| 10 | `pydicom__pydicom-863` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 3` | — | `30` | `0` | `budget_exhausted` |
| 11 | `pydicom__pydicom-996` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 1` | — | `35` | `0` | `budget_exhausted` |
| 12 | `pydicom__pydicom-997` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 70` | `39 / 39` | `28` | `1373` | `best_checkpoint_restored` |
| 13 | `pydicom__pydicom-1255` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 1` | `43 / 43` | `35` | `1614` | `best_checkpoint_restored` |
| 14 | `pylint-dev__astroid-934` | FAIL | FAIL | FAIL | FAIL | Near-Miss (`100%` F2P in R1) | `1 / 1` (R1) | `311 / 312` | `35` | `1897` | `best_checkpoint_restored` |
| 15 | `pylint-dev__astroid-946` | FAIL | FAIL | FAIL | FAIL | **PASS** *(New!)* | `1 / 1` | `155 / 155` | `35` | `957` | `best_checkpoint_restored` |
| 16 | `pylint-dev__astroid-983` | FAIL | FAIL | FAIL | FAIL | Partial (`67%–100%` F2P) | `2 / 3` (`3/3` R1) | `428 / 429` | `32` | `739` | `best_checkpoint_restored` |
| 17 | `pylint-dev__astroid-1262` | FAIL | FAIL | FAIL | FAIL | **PASS** *(New!)* | `1 / 1` | `21 / 21` | `32` | `500` | `resolved_by_agentic_gatekeeper` |
| 18 | `pylint-dev__astroid-1417` | FAIL | FAIL | FAIL | FAIL | FAIL (1-word msg diff) | `0 / 1` | `3 / 3` | `29` | `8626` | `resolved_by_agentic_gatekeeper` |
| 19 | `pylint-dev__astroid-1866` | FAIL | FAIL | FAIL | FAIL | **PASS** *(New!)* | `1 / 1` | `10 / 10` | `19` | `599` | `resolved_by_agentic_gatekeeper` |
| 20 | `pylint-dev__astroid-1959` | FAIL | FAIL | PASS | PASS | **PASS** | `1 / 1` | `11 / 11` | `32` | `1210` | `resolved_by_agentic_gatekeeper` |
| 21 | `pylint-dev__astroid-1962` | PASS | PASS | PASS | PASS | **PASS** | `1 / 1` | `11 / 11` | `35` | `1370` | `resolved_by_agentic_gatekeeper` |
| 22 | `pyvista__pyvista-4226` | FAIL | FAIL | FAIL | FAIL | **PASS** *(New!)* | `25 / 25` | `0` fail | `32` | `7156` | `best_checkpoint_restored` |
| 23 | `sqlfluff__sqlfluff-880` | FAIL | FAIL | FAIL | FAIL | **PASS** *(New!)* | `39 / 39` | `0` fail | `29` | `26803` | `best_checkpoint_restored` |
| 24 | `sqlfluff__sqlfluff-884` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 1` | — | `35` | `0` | `budget_exhausted` |
| 25 | `sqlfluff__sqlfluff-891` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 49` | `0` fail | `32` | `4147` | `resolved_by_agentic_gatekeeper` |
| 26 | `sqlfluff__sqlfluff-905` | FAIL | FAIL | FAIL | FAIL | **PASS** *(New!)* | `1 / 1` | `6 / 6` | `21` | `1823` | `best_checkpoint_restored` |
| 27 | `sqlfluff__sqlfluff-1517` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 1` | `36 / 36` | `32` | `1156` | `resolved_by_agentic_gatekeeper` |
| 28 | `sqlfluff__sqlfluff-1625` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 1` | `64 / 64` | `29` | `1082` | `resolved_by_agentic_gatekeeper` |
| 29 | `sqlfluff__sqlfluff-1733` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 1` | `12 / 12` | `35` | `1078` | `best_checkpoint_restored` |
| 30 | `sqlfluff__sqlfluff-1763` | FAIL | FAIL | FAIL | FAIL | FAIL | `0 / 3` | `34 / 34` | `35` | `2495` | `best_checkpoint_restored` |

---

## 6. Key Breakthrough Case Studies in V12

1. **`sqlfluff__sqlfluff-880` (`39 / 39` `FAIL_TO_PASS` tests passed — First-ever `sqlfluff` resolution)**:
   - In V8–V11, `sqlfluff` was `0 / 8` (`0.0%`) because whole-suite `pytest` (`1,164` tests) timed out at `60s` and single-window JSON synthesis could not coordinate multi-dialect rule changes.
   - In V12, `AgenticSweEngine` used `git grep -n` to locate all dialect/lexer references, iteratively tested with targeted `pytest`, and produced a clean patch passing all `39 / 39` `FAIL_TO_PASS` tests with zero regressions.
2. **`pyvista__pyvista-4226` (`25 / 25` `FAIL_TO_PASS` tests passed across 4 files — First-ever `pyvista` resolution)**:
   - Required coordinated changes across 4 files (`7,156` chars of diff). V6–V11's single-module `_synthesize_module` was structurally incapable of atomic multi-file synthesis and verification. V12 edited all 4 files via `str_replace` and verified them together in `/testbed`.
3. **`marshmallow-code__marshmallow-1524` (`5 / 5` `FAIL_TO_PASS`) & `marshmallow-code__marshmallow-1810` (`1 / 1` `FAIL_TO_PASS`)**:
   - In V11, `marshmallow-1524` fixed `validate.Equal` (`3/5` F2P) but missed sibling `validate.OneOf` (`2/5` F2P). In V12, `PrimaryRepairSpecialist` + `AdversarialEdgeCaseAuditor` inspected sibling validators in `src/marshmallow/validate.py` and passed `5 / 5` F2P and `127 / 127` P2P.
4. **Five `pylint-dev/astroid` Resolutions (`62.5%`: `astroid-946`, `astroid-1262`, `astroid-1866`, `astroid-1959`, `astroid-1962`)**:
   - `astroid-946`, `astroid-1262`, and `astroid-1866` were never resolved by any prior generation (V8–V11). Multi-turn reproduction in `/testbed/.hae/reproduce_issue.py` allowed the specialist to inspect AST inference behavior directly before submitting.
