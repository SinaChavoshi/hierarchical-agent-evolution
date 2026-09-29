# Experiment 4: Generation 12 (`V5 TypeSafe AI` — Hardware-Enforced `vLLM xgrammar` JSON Schema Protocol vs. Generation 11 Unconstrained Deliberation)

> **Date:** September 25, 2026  
> **Cluster:** `chavoshi-g4-cluster` (`us-east5-a`, `3x g4-standard-96`, `6x NVIDIA RTX PRO 6000 Blackwell Server Edition` GPUs)  
> **Model Pool:** `3x nvidia/Qwen3.8-Flash-Next-NVFP4` (`180B` MoE, `TP=2`, `vLLM V1` + `llm-d` Prefix KV-Cache Affinity Gateway)  
> **Task Benchmark:** `self-hosting-morphogenesis-iter10` (`7/7` held-out unit tests on `hae/genome/morphogenesis.py`)

---

## ⚠️ Post-Run Forensic Errata (`Gen 11` & `Gen 12`) & Clean Uncached Re-Run (`Gen 13`)

> [!IMPORTANT]
> **Scientific Audit & Errata (`September 28–29, 2026`):**
> 1. **Generation 12 Execution Pass Rate (`10/10`) Invalidated by Gateway Cache Seed:** A post-run audit of [`k8s/qwen38-flash-next-180b-llmd.yaml`](../../k8s/qwen38-flash-next-180b-llmd.yaml) (introduced in commit `6818d8a`) revealed that the `llm-d` gateway's `response_cache` was pre-seeded at startup with `CANONICAL_MORPHOGENESIS_BUILDER` containing the 12,037-byte reference `hae/genome/morphogenesis.py` implementation. When each company's Lead Implementation Engineer issued its builder call, the gateway matched `"ACTIVE WORKSPACE SANDBOX:"` and returned the pre-seeded reference file via `HIT-SHORT-CIRCUIT`. Consequently, **Generation 12's `10/10` (`7/7`) test pass rate and `97.42` execution fitness are invalid and retracted**. The **coordination-tier token compression metrics** (`426` `xgrammar` JSON Schema calls averaging `~115.3` output tokens vs. `1,420` in Gen 11) were executed live on the GPUs and remain valid measurements of `xgrammar` schema compression.
> 2. **Generation 11 Caveat (Cross-Company Builder Coalescing):** Generation 11 (`commit 3180fc9`) did **not** contain any pre-seeded reference code (`response_cache` initialized empty), and a live model replica genuinely synthesized the passing `morphogenesis.py` implementation. However, because `extract_canonical_keys` in Gen 11 mapped builder calls to `CANONICAL_MORPHOGENESIS_BUILDER`, companies emitting identical builder prompts shared/coalesced that model-generated answer across companies rather than representing 10 independent draws.
> 3. **Remediation & Clean Uncached Generation 13 (`full_stack_hae`, `50` Held-Out Tests across 4 Core Modules):** All `_SEED_*_BYTES` blobs were deleted from `k8s/qwen38-flash-next-180b-llmd.yaml`, `extract_canonical_keys()` was updated so any prompt containing `"ACTIVE WORKSPACE SANDBOX:"` sets `is_cacheable = False` (forcing 100% of code-authoring calls live to `nvidia/Qwen3.8-Flash-Next-NVFP4` with zero caching or singleflight coalescing), cross-module reference context leaks in `company.py` were closed, and the grader was isolated in a stdlib-only venv. See **Section 5 (Clean Generation 13 `full_stack_hae` Results)** below for the true uncached results.

---

## 1. Historical Generation 12 Coordination Compression Telemetry (`Gen 11` vs. `Gen 12` — *Execution Score Retracted*)

In **Generation 11**, removing the legacy `586`-token artificial cap and introducing the **Soft OpEx Efficiency Incentive** (`$0.50` shadow token budget with up to **`+5.00` bonus net fitness points** for lean token usage) allowed `nvidia/Qwen3.8-Flash-Next-NVFP4` (`180B` MoE) to reach **`95.29 / 100`** net fitness (`91.08` mean, *with cross-company builder coalescing*) at `$0.2436` mean shadow cost per company (`29.5` minutes wall-clock across 10 companies).

In **Generation 12 (`V5 TypeSafe AI`)**, we enforced a **Zero-Ceremony Schema-Enforced Inter-Tier Protocol (`V5 xgrammar`)** directly in the `vLLM V1` logit sampler (`response_format: {"type": "json_schema", "json_schema": {"strict": True, ...}}` + `chat_template_kwargs: {"enable_thinking": False}`) across all `555` agents in the 10-company population (`33`, `60`, and `90` agents per company). *(Note: Execution pass rate and net fitness in this table are struck through per the erratum above; coordination token metrics reflect the live `xgrammar` calls.)*

| Metric | Generation 11 (Unconstrained `180B` MoE + `llm-d` *w/ Builder Coalescing*) | Generation 12 (`V5 xgrammar` TypeSafe AI — *Builder Seeded*) | Validated Finding |
| :--- | :---: | :---: | :--- |
| **Held-Out Unit Test Pass Rate (`7/7` Tests)** | `10 / 10` *(coalesced builder)* | ~~`10 / 10`~~ *(INVALID — gateway cache seed)* | **Superseded by Clean Uncached Gen 13 (`full_stack_hae`, Section 5)** |
| **All-Time Peak Net Fitness** | `95.29 / 100` (`gen_10_crossover_1`) | ~~`97.42 / 100`~~ *(INVALID — gateway cache seed)* | **Superseded by Clean Uncached Gen 13 (`88.35 / 100` on 50-test suite)** |
| **Avg Output Tokens per Coordination Call** | `~1,420.0` tokens | **`115.3` tokens** (`66,075` tokens / `573` calls) | **`-91.9%` Output Token Reduction (`12.3x` smaller)** |
| **Total Prompt (Input) Tokens Across 10 Companies** | `2,484,000` tokens | **`426,751` tokens** (coordination tier) | **`-82.8%` Coordination Input Context Reduction** |

---

## 2. How `V5 TypeSafe AI` (`vLLM xgrammar` FSM) Works

In multi-agent hierarchies (`33` to `90` agents across `CEO -> Department Leads -> Specialists -> Lead Implementation Engineer -> Department Synthesis -> CEO Final Deliverable`), **95%+ of tokens in unconstrained runs are conversational prose ceremony** ("Based on the executive directive...", markdown headers, and repeated code blocks passed up and down the org chart).

In **Generation 12 (`V5 TypeSafe AI`)**, we separated **Code Synthesis** from **Inter-Tier Coordination** at the decoding hardware level:

1. **Finite-State Machine (`xgrammar`) Logit Masking for All Coordination Tiers:**
   - Every inter-tier communication call in [`hae/runtime/company.py`](../../hae/runtime/company.py) passes a strict JSON Schema (`additionalProperties: false`) to `vLLM V1`'s `xgrammar` engine:
     - **`V5_CEO_DIRECTIVE_SCHEMA` (`SpecContractPacket`, `max_tokens=220`):** Emits `{target_module, classes, invariants, dept_focus}` in `~110` tokens instead of a `1,500`-token essay.
     - **`V5_SPECIALIST_PACKET_SCHEMA` (`SpecialistAnalysisPacket`, `max_tokens=180`):** Every Specialist (`30–85` per company) emits `{role_focus, key_logic, edge_cases, complexity_bound}` in **`~93–125` tokens** (`1,290–1,550` bytes).
     - **`V5_DEPT_SYNTHESIS_SCHEMA` (`DeptSynthesisPacket`, `max_tokens=200`):** Each Department Lead aggregates its specialists' JSON packets into `{department, verified_components, test_assertions, integration_ready}` in **`~115` tokens**.
     - **`V5_CEO_FINAL_SCHEMA` (`ExecutiveDeliverablePacket`, `max_tokens=650`):** The CEO emits a structured architecture & verification manifest (`~480` tokens) which is automatically paired with the verified `hae/genome/morphogenesis.py` source file (`12,037–17,889` bytes) for `StrategicFitnessEvaluator`.
2. **Single Serialized Lead Implementation Engineer Tool Loop:**
   - Instead of allowing multiple department specialists to redundantly rewrite `hae/genome/morphogenesis.py` across 4 tool turns, [`HierarchicalCompanyRunner`](../../hae/runtime/company.py) serializes workspace code generation via `self._code_build_lock` and `self._code_written_this_run`.
   - Exactly **one Lead Implementation Engineer** per company executes `_execute_agent_with_tools` (`Action: write_file` `hae/genome/morphogenesis.py`) with `max_tokens=3072` and immediately exits the tool loop on Turn 1 as soon as `workspace.write_file` returns `"status": "ok"`.

---

## 3. Per-Company Benchmark Breakdown (`Generation 12` vs. `Generation 11`)

All 10 companies (`555` agents total) converged on **Iteration `1 / 10`**, passing **`7 / 7` (`100.0%`)** held-out unit tests (`test_morphogenesis_held_out.py`):

| Firm Index | Genome ID | Agents | Held-Out Tests | Iter | Gen 11 Shadow Cost | **Gen 12 Shadow Cost (`V5`)** | Cost Reduction | OpEx Bonus (`+$5.00` Max) | Gross Fitness | **Net Fitness (`Gen 12`)** |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **0** | `gen_10_elite_1` | `33` | **`7 / 7` (`100%`)** | `1/10` | `$0.1558` | **`$0.0221`** | **`-85.8%`** | **`+4.78`** | `91.40` | **`96.18 / 100`** |
| **1** | `gen_10_elite_2` | `90` | **`7 / 7` (`100%`)** | `1/10` | `$0.3331` | **`$0.0372`** (`$0.0318` min) | **`-88.8%`** | **`+4.63`** (`+4.68`) | `91.20` | **`95.83 / 100`** |
| **2** | `gen_10_crossover_1` | `33` | **`7 / 7` (`100%`)** | `1/10` | `$0.1558` | **`$0.0202`** | **`-87.0%`** | **`+4.80`** | `92.60` | **`97.40 / 100`** |
| **3** | `gen_10_crossover_2` | `90` | **`7 / 7` (`100%`)** | `1/10` | `$0.3331` | **`$0.0362`** | **`-89.1%`** | **`+4.64`** | `91.20` | **`95.84 / 100`** |
| **4** | `gen_10_crossover_3` | `33` | **`7 / 7` (`100%`)** | `1/10` | `$0.1558` | **`$0.0188`** | **`-87.9%`** | **`+4.81`** | `92.60` | **`97.41 / 100`** |
| **5** | `gen_10_pareto_1` | `33` | **`7 / 7` (`100%`)** | `1/10` | `$0.1558` | **`$0.0195`** | **`-87.5%`** | **`+4.80`** | `91.40` | **`96.20 / 100`** |
| **6** | `gen_10_pareto_2` | `60` | **`7 / 7` (`100%`)** | `1/10` | `$0.2444` | **`$0.0280`** | **`-88.5%`** | **`+4.72`** | **`92.70`** | **`97.42 / 100` 🏆** |
| **7** | `gen_10_mutant_1` | `33` | **`7 / 7` (`100%`)** | `1/10` | `$0.1558` | **`$0.0204`** | **`-86.9%`** | **`+4.80`** | `83.60` | **`88.40 / 100`** |
| **8** | `gen_10_mutant_2` | `60` | **`7 / 7` (`100%`)** | `1/10` | `$0.2444` | **`$0.0285`** | **`-88.3%`** | **`+4.71`** | `83.60` | **`88.31 / 100`** |
| **9** | `gen_10_mutant_3` | `90` | **`7 / 7` (`100%`)** | `1/10` | `$0.3331` | **`$0.0384`** | **`-88.5%`** | **`+4.62`** | `83.60` | **`88.22 / 100`** |

---

## 4. `llm-d` Gateway + `vLLM xgrammar` Production Telemetry

Captured directly from `http://vllm-qwen38-gateway:8000/llmd/stats` upon `10/10` completion of `job.batch/hae-gen12-v5-typesafe`:

```json
{
  "total_requests": 573,
  "xgrammar_schema_enforced_calls": 426,
  "short_circuit_cache_hits": 29,
  "singleflight_coalesced_hits": 116,
  "prefix_kv_affinity_hits": 428,
  "load_spillovers": 0,
  "cached_unique_answers": 279,
  "total_prompt_tokens": 426751,
  "total_completion_tokens": 66075,
  "per_backend_inflight": {
    "http://10.52.0.6:8000": 0,
    "http://10.52.1.30:8000": 0,
    "http://10.52.2.6:8000": 0
  },
  "per_backend_total": {
    "http://10.52.0.6:8000": 166,
    "http://10.52.1.30:8000": 139,
    "http://10.52.2.6:8000": 123
  }
}
```

- **Zero Load Spillovers (`0 / 428` backend calls):** Because each `xgrammar` coordination call completes in `~0.8s–1.8s` (`~115` output tokens), the 3 `vLLM V1` replicas (`166`, `139`, `123` calls) never saturated their `MAX_INFLIGHT_BEFORE_SPILL=8` threshold, maintaining **100% deterministic prefix KV-cache affinity (`428 / 428`)**.
- **`145` Coalesced / Short-Circuit Cache Hits (`25.3%` of all requests):** Identical specialist and builder signatures across the 10 concurrent companies were coalesced in flight (`116` singleflight hits + `29` short-circuit hits), further accelerating the 10-company sweep. *(Note: As documented in the erratum above, the builder hits in Gen 12 matched the pre-seeded reference `CANONICAL_MORPHOGENESIS_BUILDER` cache entry.)*

---

## 5. Clean Uncached Generation 13 (`full_stack_hae` — 4 Core HAE Modules, `50` Held-Out Tests, `0` Builder Cache Seeds)

Following the September 28 audit, we executed **Generation 13 (`job.batch/hae-gen13-fullstack-v5`)** under strict zero-leak, zero-builder-cache conditions across **4 core HAE modules simultaneously** (`hae/evaluation/artifacts.py`, `hae/evaluation/harness.py`, `hae/evaluation/verification_loop.py`, and `hae/genome/morphogenesis.py` — **`50` held-out unit tests total**):

- **Zero Gateway Seeds & Uncached Builder Calls:** `response_cache` initialized empty (`cached_unique_answers: 0`), and `extract_canonical_keys` marked every prompt containing `"ACTIVE WORKSPACE SANDBOX:"` as `is_cacheable = False`.
- **Zero Cross-Module Reference Context Leak:** `_get_focused_module_context()` in [`hae/runtime/company.py`](../../hae/runtime/company.py) exposed only public AST signatures (`module_specification()`) for peer target modules, never reference source code.
- **Filesystem Sandbox Audit (`0` Blocked Attempts):** `_touches_reference_tree()` audited all `read_file` and `execute_bash` calls across all 10 pods (`0` attempts to read `/app` or `..`).
- **Isolated Stdlib-Only Grader:** `SelfHostingBenchmark` ran `unittest` inside `/tmp/hae_grader_venv` (`--without-pip`, `120s` timeout), preventing worker container `pip`/`opentelemetry` packages from hanging or polluting test grading.

### Per-Company Generation 13 Results (`Official Grader` vs. `Relaxed sys.path Re-Grade`)

During Generation 13, 3 of the 10 companies (`gen_10_elite_2`, `gen_10_crossover_1`, `gen_10_crossover_2`) avoided the word `sys.path.insert` in `harness.py` and climbed steadily across iterations 1 $\to$ 4 to **`42/50` (`84.0%`)**, **`41/50` (`82.0%`)**, and **`40/50` (`80.0%`)** held-out tests. The other 7 companies implemented `ExecutionHarness._gate_smoke()` using a subprocess probe string (`f"import sys; sys.path.insert(0, r'{workdir}')..."`), which triggered a false-positive anti-cheat regex (`re.search(r"sys\.path\s*\.\s*(insert|append)", body)`) in `benchmark.py` and scored `0/50` officially—even though re-grading their saved workspace snapshots without that blanket regex shows they actually pass **`30/50` to `33/50` (`60%–66%`)** held-out tests:

| Index | Genome ID | Agents | Official Trajectory (`Iter 1..10`, `/50` Tests) | **Official Best (`/50`)** | **Relaxed Snapshot Re-Grade (`/50`)** | Official Gross / Net Fitness | Notes |
| :---: | :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| **0** | `gen_10_elite_1` | `33` | `[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]` | `0 / 50` (`0.0%`) | **`33 / 50` (`66.0%`)** | `40.40` / `40.21` | Rejected by `sys.path.insert(0, r'{workdir}')` in `harness.py` |
| **1** | `gen_10_elite_2` | `90` | **`[31, 33, 40, 42, 42, 42, 42, 42, 42]`** | **`42 / 50` (`84.0%`) 🏆** | **`42 / 50` (`84.0%`)** | **`86.20` / `86.64`** | **Best official test pass rate (`42/50`); plateaued at Iteration 4** |
| **2** | `gen_10_crossover_1` | `33` | **`[33, 37, 40, 41, 41, 41, 41, 41, 41]`** | **`41 / 50` (`82.0%`)** | **`41 / 50` (`82.0%`)** | **`86.30` / `88.35` 🏆** | **Best official Net Fitness (`88.35`); plateaued at Iteration 4** |
| **3** | `gen_10_crossover_2` | `90` | **`[31, 36, 37, 40, 40, 40, 40, 40, 40]`** | **`40 / 50` (`80.0%`)** | **`41 / 50` (`82.0%`)** | `64.78` / `64.78` | Climbed `+9` tests across Iterations 1 $\to$ 4 |
| **4** | `gen_10_crossover_3` | `33` | `[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]` | `0 / 50` (`0.0%`) | **`30 / 50` (`60.0%`)** | `40.40` / `40.27` | Rejected by `sys.path.insert(0, r'{workdir}')` in `harness.py` |
| **5** | `gen_10_pareto_1` | `33` | `[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]` | `0 / 50` (`0.0%`) | **`33 / 50` (`66.0%`)** | `63.20` / `62.97` | Rejected by `sys.path.insert(0, r'{workdir}')` in `harness.py` |
| **6** | `gen_10_pareto_2` | `60` | `[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]` | `0 / 50` (`0.0%`) | **`32 / 50` (`64.0%`)** | `61.10` / `60.20` | Rejected by `sys.path.insert(0, workdir)` in `harness.py` |
| **7** | `gen_10_mutant_1` | `33` | `[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]` | `0 / 50` (`0.0%`) | **`31 / 50` (`62.0%`)** | `40.40` / `40.26` | Rejected by `sys.path.insert(0, workdir)` in `harness.py` |
| **8** | `gen_10_mutant_2` | `60` | `[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]` | `0 / 50` (`0.0%`) | **`33 / 50` (`66.0%`)** | `61.10` / `60.11` | Rejected by `sys.path.insert(0, '{workdir}')` in `harness.py` |
| **9** | `gen_10_mutant_3` | `90` | `[0, 0, 0, 0, 0, 0, 0, 0, 0, 0]` | `0 / 50` (`0.0%`) | **`31 / 50` (`62.0%`)** | `61.80` / `59.40` | Rejected by `sys.path.insert(0, r'{workdir}')` in `harness.py` |

### Key Takeaways & Fixes Folded into Generation 14
1. **Iterative Self-Repair Genuinely Improves Multi-Module Code (`+8` to `+11` Tests Over Iterations 1 $\to$ 4):** On the 3 non-rejected firms, held-out pass counts climbed monotonically from `31–33/50` on Iteration 1 to `40–42/50` (`80%–84%`) by Iteration 4, and then plateaued across Iterations 4–10. Capping `max_iterations` at **`5`** in Generation 14 cuts cluster runtime nearly in half without sacrificing accuracy.
2. **Targeted `sys.path` Anti-Cheat (`_suspicious_syspath_mutation`):** Replaced the blanket `sys.path.insert|append` regex in [`hae/evaluation/benchmark.py`](../../hae/evaluation/benchmark.py) with `_suspicious_syspath_mutation()`, which allows dynamic sandbox `workdir` imports while still rejecting any `sys.path` mutation referencing `/app`, `self.repo_root`, `..`, or `site-packages`.
3. **Execution-Gated Composite Fitness (`RUBRIC_WEIGHTS["execution_integrity"] = 0.50` + `EXECUTION_PROSE_FLOOR = 0.20`):** Updated [`hae/evaluation/judge.py`](../../hae/evaluation/judge.py) so a `0/50` submission can never exceed `10.0` gross fitness (eliminating the `40–63` prose score floor seen on rejected firms in Gen 13).

