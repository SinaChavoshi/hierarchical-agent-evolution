# V5 (Generations 12-15): schema-constrained coordination packets and the uncached `full_stack_hae` benchmark

This document covers Generations 12 to 15 of the hierarchical-agent-evolution (HAE) project, in which evolved multi-agent "companies" of LLM agents write software. V5 made two changes. First, every message between agent tiers became a small JSON object whose schema is enforced by the vLLM decoder's grammar-constrained sampler (xgrammar) instead of free prose. Second, from Generation 13 on, fitness was measured on `full_stack_hae`: four core HAE modules synthesised against 50 held-out unit tests (tests the agents never see), with no cached or pre-seeded builder answers. Generation 12's execution results are retracted (see the erratum); its token measurements stand.

| Item | Value |
| :--- | :--- |
| Generation 12 run | September 25, 2026; audit September 28-29, 2026 |
| Cluster | `chavoshi-g4-cluster` (`us-east5-a`): 3x `g4-standard-96`, 6x NVIDIA RTX PRO 6000 Blackwell Server Edition GPUs |
| Model pool | 3x `nvidia/Qwen3.8-Flash-Next-NVFP4` (180B mixture-of-experts, TP=2) on vLLM V1, behind the `llm-d` gateway (prefix KV-cache affinity routing; short-circuit answers for cached identical prompts; singleflight merging of identical in-flight requests) |
| Task, Gen 12 | `self-hosting-morphogenesis-iter10`: 7 held-out unit tests on `hae/genome/morphogenesis.py` |
| Task, Gen 13-15 | `full_stack_hae`: 50 held-out unit tests across `hae/evaluation/artifacts.py`, `hae/evaluation/harness.py`, `hae/evaluation/verification_loop.py` and `hae/genome/morphogenesis.py` |

"Shadow cost" is a notional USD token cost per company used for the operating-expense (OpEx) incentive; net fitness is gross fitness plus the OpEx bonus.

## Erratum: Generation 12 execution results retracted

A post-run audit on September 28-29, 2026 found the following.

1. Generation 12's 10/10 pass rate is invalid because of a gateway cache seed. [`k8s/qwen38-flash-next-180b-llmd.yaml`](../../k8s/qwen38-flash-next-180b-llmd.yaml), introduced in commit `6818d8a`, pre-seeded the `llm-d` gateway's `response_cache` at startup with a `CANONICAL_MORPHOGENESIS_BUILDER` entry containing the 12,037-byte reference implementation of `hae/genome/morphogenesis.py`. When each company's Lead Implementation Engineer issued its builder call, the gateway matched `"ACTIVE WORKSPACE SANDBOX:"` and returned the pre-seeded reference file via `HIT-SHORT-CIRCUIT`. Generation 12's 10/10 (7/7) test pass rate and 97.42 execution fitness are therefore invalid and retracted. The coordination-tier token measurements (426 xgrammar JSON-schema calls averaging about 115.3 output tokens, against 1,420 in Gen 11) were executed live on the GPUs and remain valid.
2. Generation 11 caveat: cross-company builder coalescing. Generation 11 (commit `3180fc9`) contained no pre-seeded reference code (`response_cache` started empty), and a live model replica genuinely synthesised the passing `morphogenesis.py`. However, `extract_canonical_keys` in Gen 11 mapped builder calls to `CANONICAL_MORPHOGENESIS_BUILDER`, so companies that emitted identical builder prompts shared that one model-generated answer rather than making 10 independent draws.
3. Remediation. All `_SEED_*_BYTES` blobs were deleted from `k8s/qwen38-flash-next-180b-llmd.yaml`; `extract_canonical_keys()` now sets `is_cacheable = False` for any prompt containing `"ACTIVE WORKSPACE SANDBOX:"`, so 100% of code-authoring calls go live to `nvidia/Qwen3.8-Flash-Next-NVFP4` with no caching or singleflight coalescing; cross-module reference context leaks in `company.py` were closed; and the grader was isolated in a stdlib-only virtualenv. The clean results are in the Generation 13 section.

## Generation 11 vs Generation 12: coordination token telemetry

Generation 11 removed the legacy 586-token output cap and added a soft OpEx incentive: a $0.50 shadow token budget per company, with up to +5.00 bonus net-fitness points for lean token use. Under it the 180B model reached 95.29/100 peak net fitness (91.08 mean, with the builder coalescing noted above) at a mean shadow cost of $0.2436 per company and 29.5 minutes wall-clock for the 10 companies.

Generation 12 kept that setup and constrained every inter-tier call to a JSON schema enforced in the vLLM V1 sampler (`response_format: {"type": "json_schema", "json_schema": {"strict": True, ...}}` plus `chat_template_kwargs: {"enable_thinking": False}`), across all 555 agents of the 10-company population (33, 60 or 90 agents per company). The execution rows below are struck through per the erratum; the token rows are live measurements.

| Metric | Gen 11 (unconstrained, builder coalescing) | Gen 12 (schema-constrained, builder seeded) | Finding |
| :--- | :---: | :---: | :--- |
| Held-out pass rate (7 tests) | 10/10 companies (coalesced builder) | ~~10/10~~ invalid: gateway cache seed | superseded by clean Gen 13 on `full_stack_hae` |
| Peak net fitness | 95.29/100 (`gen_10_crossover_1`) | ~~97.42/100~~ invalid: gateway cache seed | superseded by clean Gen 13: 88.35/100 on the 50-test suite |
| Mean output tokens per coordination call | ~1,420 | **115.3** (66,075 tokens / 573 calls) | **-91.9%** (12.3x smaller) |
| Prompt tokens across 10 companies | 2,484,000 | 426,751 (coordination tier) | -82.8% |

## How the schema-constrained protocol works

A company is a hierarchy of 33 to 90 agents: CEO -> department leads -> specialists -> one Lead Implementation Engineer -> department synthesis -> CEO final deliverable. In the unconstrained runs most output tokens (the authors estimate over 95%) were connective prose: "Based on the executive directive...", markdown headers, and code blocks re-quoted up and down the hierarchy. V5 separates coordination from code synthesis.

Coordination calls. Every inter-tier call in [`hae/runtime/company.py`](../../hae/runtime/company.py) passes a strict JSON schema (`additionalProperties: false`) to vLLM V1's xgrammar engine, which masks logits during sampling so that only schema-valid tokens can be emitted:

| Schema | Packet | `max_tokens` | Fields | Typical output |
| :--- | :--- | :---: | :--- | :--- |
| `V5_CEO_DIRECTIVE_SCHEMA` | `SpecContractPacket` | 220 | `target_module`, `classes`, `invariants`, `dept_focus` | ~110 tokens, replacing a ~1,500-token essay |
| `V5_SPECIALIST_PACKET_SCHEMA` | `SpecialistAnalysisPacket` | 180 | `role_focus`, `key_logic`, `edge_cases`, `complexity_bound` | ~93-125 tokens (1,290-1,550 bytes) per specialist; 30-85 specialists per company |
| `V5_DEPT_SYNTHESIS_SCHEMA` | `DeptSynthesisPacket` | 200 | `department`, `verified_components`, `test_assertions`, `integration_ready` | ~115 tokens per department lead, aggregating its specialists' packets |
| `V5_CEO_FINAL_SCHEMA` | `ExecutiveDeliverablePacket` | 650 | architecture and verification manifest | ~480 tokens, paired automatically with the verified `hae/genome/morphogenesis.py` source (12,037-17,889 bytes) for `StrategicFitnessEvaluator` |

Code synthesis. Instead of several department specialists each rewriting `hae/genome/morphogenesis.py` across 4 tool turns, [`HierarchicalCompanyRunner`](../../hae/runtime/company.py) serialises workspace code generation with `self._code_build_lock` and `self._code_written_this_run`. Exactly one Lead Implementation Engineer per company runs `_execute_agent_with_tools` (`Action: write_file` on `hae/genome/morphogenesis.py`, `max_tokens=3072`) and leaves the tool loop on turn 1 as soon as `workspace.write_file` returns `"status": "ok"`.

## Generation 12 per-company results (execution columns retracted)

All 10 companies (555 agents) reported 7/7 on `test_morphogenesis_held_out.py` at iteration 1 of 10. Per the erratum, the builder output was the pre-seeded reference file, so that pass rate and the gross and net fitness below are retracted. Shadow costs are as recorded in the run; the Gen 11 shadow costs were $0.1558, $0.2444 and $0.3331 for 33-, 60- and 90-agent firms.

| Firm | Genome | Agents | Gen 12 shadow cost | vs Gen 11 | OpEx bonus (max +5.00) | Gross (retracted) | Net (retracted) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| 0 | `gen_10_elite_1` | 33 | $0.0221 | -85.8% | +4.78 | 91.40 | 96.18 |
| 1 | `gen_10_elite_2` | 90 | $0.0372 ($0.0318 min) | -88.8% | +4.63 (+4.68) | 91.20 | 95.83 |
| 2 | `gen_10_crossover_1` | 33 | $0.0202 | -87.0% | +4.80 | 92.60 | 97.40 |
| 3 | `gen_10_crossover_2` | 90 | $0.0362 | -89.1% | +4.64 | 91.20 | 95.84 |
| 4 | `gen_10_crossover_3` | 33 | $0.0188 | -87.9% | +4.81 | 92.60 | 97.41 |
| 5 | `gen_10_pareto_1` | 33 | $0.0195 | -87.5% | +4.80 | 91.40 | 96.20 |
| 6 | `gen_10_pareto_2` | 60 | $0.0280 | -88.5% | +4.72 | 92.70 | 97.42 (retracted peak) |
| 7 | `gen_10_mutant_1` | 33 | $0.0204 | -86.9% | +4.80 | 83.60 | 88.40 |
| 8 | `gen_10_mutant_2` | 60 | $0.0285 | -88.3% | +4.71 | 83.60 | 88.31 |
| 9 | `gen_10_mutant_3` | 90 | $0.0384 | -88.5% | +4.62 | 83.60 | 88.22 |

## Generation 12 gateway telemetry

Read from `http://vllm-qwen38-gateway:8000/llmd/stats` when `job.batch/hae-gen12-v5-typesafe` had finished all 10 companies:

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
  "per_backend_inflight": {"http://10.52.0.6:8000": 0, "http://10.52.1.30:8000": 0, "http://10.52.2.6:8000": 0},
  "per_backend_total": {"http://10.52.0.6:8000": 166, "http://10.52.1.30:8000": 139, "http://10.52.2.6:8000": 123}
}
```

- No load spillovers (0 of 428 backend calls). Each schema-constrained call finished in roughly 0.8-1.8 s (~115 output tokens), so the three replicas (166, 139 and 123 calls) never reached `MAX_INFLIGHT_BEFORE_SPILL=8`, and prefix KV-cache affinity held for 428 of 428 calls.
- 145 requests (25.3%) never reached a GPU: 116 singleflight hits plus 29 short-circuit hits on identical specialist and builder signatures across the 10 concurrent companies. As noted in the erratum, the builder hits matched the pre-seeded `CANONICAL_MORPHOGENESIS_BUILDER` entry.

## Generation 13: clean uncached run on `full_stack_hae`

After the September 28 audit, Generation 13 (`job.batch/hae-gen13-fullstack-v5`) ran the same 10 genomes on `full_stack_hae` (the four modules listed above, 50 held-out tests, up to 10 self-repair iterations per company) under these controls:

- No gateway seeds, no cached builder calls: `response_cache` started empty (`cached_unique_answers: 0`) and `extract_canonical_keys` marked every prompt containing `"ACTIVE WORKSPACE SANDBOX:"` as `is_cacheable = False`.
- No cross-module reference leak: `_get_focused_module_context()` in [`hae/runtime/company.py`](../../hae/runtime/company.py) exposes only the public AST signatures (`module_specification()`) of peer target modules, never reference source.
- Filesystem audit: `_touches_reference_tree()` checked every `read_file` and `execute_bash` call in all 10 pods; 0 attempts to read `/app` or `..`.
- Isolated grader: `SelfHostingBenchmark` ran `unittest` inside `/tmp/hae_grader_venv` (stdlib only, `--without-pip`, 120 s timeout), so the worker container's `pip`/`opentelemetry` packages could not hang or pollute grading.

### Results

Three companies (`gen_10_elite_2`, `gen_10_crossover_1`, `gen_10_crossover_2`) did not write the string `sys.path.insert` in `harness.py` and climbed over iterations 1 to 4 to 42/50 (84.0%), 41/50 (82.0%) and 40/50 (80.0%). The other seven implemented `ExecutionHarness._gate_smoke()` with a subprocess probe string (`f"import sys; sys.path.insert(0, r'{workdir}')..."`), which tripped a blanket anti-cheat regex in `benchmark.py` (`re.search(r"sys\.path\s*\.\s*(insert|append)", body)`) and were scored 0/50 officially. Re-grading their saved workspace snapshots without that regex gives 30/50 to 33/50 (60-66%). "Rejected by" gives the exact `sys.path.insert(0, ...)` argument each firm used; trajectories are as recorded.

| Firm | Genome | Agents | Official trajectory (/50) | Official best | Relaxed re-grade | Gross / net | Note |
| :---: | :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| 0 | `gen_10_elite_1` | 33 | 0 x 10 | 0/50 | 33/50 (66.0%) | 40.40 / 40.21 | rejected by `r'{workdir}'` |
| 1 | `gen_10_elite_2` | 90 | 31, 33, 40, 42, 42, 42, 42, 42, 42 | **42/50 (84.0%)** | 42/50 (84.0%) | 86.20 / 86.64 | best official pass rate; flat from iteration 4 |
| 2 | `gen_10_crossover_1` | 33 | 33, 37, 40, 41, 41, 41, 41, 41, 41 | 41/50 (82.0%) | 41/50 (82.0%) | 86.30 / **88.35** | best official net fitness; flat from iteration 4 |
| 3 | `gen_10_crossover_2` | 90 | 31, 36, 37, 40, 40, 40, 40, 40, 40 | 40/50 (80.0%) | 41/50 (82.0%) | 64.78 / 64.78 | +9 tests over iterations 1 to 4 |
| 4 | `gen_10_crossover_3` | 33 | 0 x 10 | 0/50 | 30/50 (60.0%) | 40.40 / 40.27 | rejected by `r'{workdir}'` |
| 5 | `gen_10_pareto_1` | 33 | 0 x 10 | 0/50 | 33/50 (66.0%) | 63.20 / 62.97 | rejected by `r'{workdir}'` |
| 6 | `gen_10_pareto_2` | 60 | 0 x 10 | 0/50 | 32/50 (64.0%) | 61.10 / 60.20 | rejected by `workdir` |
| 7 | `gen_10_mutant_1` | 33 | 0 x 10 | 0/50 | 31/50 (62.0%) | 40.40 / 40.26 | rejected by `workdir` |
| 8 | `gen_10_mutant_2` | 60 | 0 x 10 | 0/50 | 33/50 (66.0%) | 61.10 / 60.11 | rejected by `'{workdir}'` |
| 9 | `gen_10_mutant_3` | 90 | 0 x 10 | 0/50 | 31/50 (62.0%) | 61.80 / 59.40 | rejected by `r'{workdir}'` |

### Findings and fixes carried into Generation 14

1. Self-repair improved multi-module code in this run. The three non-rejected firms climbed monotonically from 31-33/50 at iteration 1 to 40-42/50 (80-84%) by iteration 4, a gain of +8 to +11 tests, and did not improve over iterations 4-10. Generation 14 therefore caps `max_iterations` at 5, which should roughly halve cluster runtime without lowering the best scores.
2. Targeted `sys.path` check. The blanket `sys.path.insert|append` regex in [`hae/evaluation/benchmark.py`](../../hae/evaluation/benchmark.py) was replaced by `_suspicious_syspath_mutation()`, which allows imports from the dynamic sandbox `workdir` but still rejects any `sys.path` mutation that references `/app`, `self.repo_root`, `..` or `site-packages`.
3. Execution-gated fitness. [`hae/evaluation/judge.py`](../../hae/evaluation/judge.py) now uses `RUBRIC_WEIGHTS["execution_integrity"] = 0.50` and `EXECUTION_PROSE_FLOOR = 0.20`, so a 0/50 submission cannot exceed 10.0 gross fitness. This removes the 40-63 prose-only scores the rejected firms received in Gen 13.

## Generation 14: targeted `sys.path` check, execution-gated scoring, 5-iteration cap

With those fixes, Generation 14 (`job.batch/hae-gen14-fullstack-v5`; [`results/hae_gen14_fullstack_v5/generation_14_summary.json`](../../results/hae_gen14_fullstack_v5/generation_14_summary.json)) went from 3 of 10 officially graded companies in Gen 13 to **9 of 10, scoring 39/50 to 44/50 (78.0-88.0%)**. The best was 44/50 (88.0%) by `gen_10_mutant_3` (90 agents), the highest `full_stack_hae` score to that point. Relative to the 10-iteration Gen 13, total LLM calls fell 50.8% (5,985 -> 2,942) and prompt tokens 62.8% (17.24M -> 6.42M). "+N vs Gen 13" in the notes compares with the firm's official Gen 13 score.

| Firm | Genome | Agents | Gen 13 official | Trajectory (iterations 1-5, /50) | Best | Gross / net | Shadow cost | Note |
| :---: | :--- | :---: | :---: | :--- | :---: | :---: | :---: | :--- |
| 0 | `gen_10_elite_1` | 33 | 0/50 (rejected) | 31, 35, 38, 41, 41 | 41/50 (82.0%) | 78.62 / 82.03 | $0.1590 | +10 in run; +41 vs Gen 13 |
| 1 | `gen_10_elite_2` | 90 | 42/50 (84.0%) | 32, 38, 40, 40, 40 | 40/50 (80.0%) | 77.76 / 80.79 | $0.1966 | +8 over iterations 1 to 3 |
| 2 | `gen_10_crossover_1` | 33 | 41/50 (82.0%) | 32, 38, 40, 40, 41 | 41/50 (82.0%) | 78.02 / 81.50 | $0.1523 | matched its 41/50 Gen 13 peak in 5 iterations |
| 3 | `gen_10_crossover_2` | 90 | 40/50 (80.0%) | 0, 0, 0, 0, 0 | 0/50 (31/50 relaxed) | 7.86 / 10.58 | $0.2282 | used `sys.path.insert(0, os.getcwd())`; execution gate capped 0/50 at 7.86 gross |
| 4 | `gen_10_crossover_3` | 33 | 0/50 (rejected) | 30, 38, 29, 29, 39 | 39/50 (78.0%) | 75.54 / 79.12 | $0.1421 | +9 in run; +39 vs Gen 13 |
| 5 | `gen_10_pareto_1` | 33 | 0/50 (rejected) | 32, 32, 37, 42, 42 | 42/50 (84.0%) | 62.88 / 66.62 | $0.1260 | +10 in run; +42 vs Gen 13 |
| 6 | `gen_10_pareto_2` | 60 | 0/50 (rejected) | 31, 34, 39, 39, 39 | 39/50 (78.0%) | 75.21 / 78.67 | $0.1543 | +8 in run; +39 vs Gen 13 |
| 7 | `gen_10_mutant_1` | 33 | 0/50 (rejected) | 31, 35, 42, 42, 42 | 42/50 (84.0%) | 64.63 / 68.05 | $0.1579 | +11 in run; +42 vs Gen 13 |
| 8 | `gen_10_mutant_2` | 60 | 0/50 (rejected) | 33, 38, 41, 41, 41 | 41/50 (82.0%) | 79.48 / 82.91 | $0.1569 | best net fitness (82.91/100) under the execution-gated rubric |
| 9 | `gen_10_mutant_3` | 90 | 0/50 (rejected) | 34, 37, 41, 43, 44 | **44/50 (88.0%)** | 79.26 / 82.32 | $0.1941 | best `full_stack_hae` score to date |

Gateway telemetry (`results/hae_gen14_fullstack_v5/llmd_stats.json`):

- 2,942 LLM calls in total (-50.8% vs 5,985 in the 10-iteration Gen 13).
- 1,505 of 2,942 (51.16%) served without a GPU: 1,470 short-circuit hits and 35 singleflight hits, all on coordination packets; 0 builder calls were cached.
- Prefix KV-cache affinity on live GPU calls: 1,435 of 1,437 (99.86%) across the three replicas (405, 520 and 512 calls).
- 6,419,595 prompt tokens (-62.8% vs Gen 13) and 594,258 completion tokens (-53.8% vs Gen 13) over 10 companies x 5 iterations x 4 modules; mean shadow cost $0.1667 per company.

## Generation 15: first 50/50 results

Generation 15 (`job.batch/hae-gen15-fullstack-v5`; [`results/hae_gen15_fullstack_v5/generation_15_summary.json`](../../results/hae_gen15_fullstack_v5/generation_15_summary.json)) bred 10 offspring from the Generation 14 population, carrying Gen 14's fully verified champion `morphogenesis.py` overlay (from `gen_10_mutant_3`), and fixed four multi-module self-repair problems seen in Gen 14:

1. Multi-line assertion diffs. `_extract_failures` in [`hae/evaluation/benchmark.py`](../../hae/evaluation/benchmark.py) keeps the full `AssertionError: ... != ...` diff block (up to 320 characters) instead of truncating to `lines[-1]`.
2. Per-module feedback and locking. [`hae/runtime/company.py`](../../hae/runtime/company.py) filters the `GROUND-TRUTH VERIFIER FEEDBACK` per target module (`SUITE_TAG_TO_MODULE`), so each module's builder sees only its own failures, and locks modules that already have 0 failures (`self._locked_passing_modules`) during repair iterations 2 to 5.
3. Repair context alignment. [`hae/orchestration/worker.py`](../../hae/orchestration/worker.py) aligns the iteration 2-5 repair instructions with the inline `CURRENT MODULE IMPLEMENTATION IN WORKSPACE` block and restricts the `Step 2.5` single-file repair to single-module tasks.
4. Explicit gate contracts. [`hae/evaluation/harness.py`](../../hae/evaluation/harness.py) and [`hae/evaluation/verification_loop.py`](../../hae/evaluation/verification_loop.py) now document `src/` layout `PYTHONPATH` resolution, `tomllib` manifest validation, stdlib fallback collection of both `def test_*()` functions and `unittest.TestCase` classes, `ModuleNotFoundError` -> `SKIPPED`, AST import plus tracer/span call verification, and the `VerificationLoop` report-formatting invariants.

### Results

All 10 companies beat Generation 14's best of 44/50, scoring **46/50 to 50/50** (92.0-100.0%; population mean 48.4/50, 96.8%) with a mean net fitness of 93.75/100. Two 33-agent firms reached 50/50 and had all four modules promoted into `code_overlays` (promotion requires 50/50); `gen_14_mutant_3` set the all-time peak net fitness of **99.43/100**. "+N vs Gen 14" in the notes compares with the same-index firm in Generation 14.

| Rank | Firm | Genome | Agents | Trajectory (iterations 1-5, /50) | Best | Gross / net | Shadow cost | Overlays promoted | Note |
| :---: | :---: | :--- | :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| 1 | 9 | `gen_14_mutant_3` | 33 | 48, 50 (converged at iteration 2) | 50/50 (100.0%) | 94.95 / **99.43** | $0.0521 | 4/4 modules | repaired the last 2 tests on iteration 2 |
| 2 | 5 | `gen_14_pareto_1` | 33 | 50 (converged at iteration 1) | 50/50 (100.0%) | 77.95 / 82.69 | $0.0259 | 4/4 modules | 50/50 in a single pass |
| 3 | 0 | `gen_14_elite_1` | 60 | 49, 49, 49, 49, 49 | 49/50 (98.0%) | 92.94 / 96.65 | $0.1287 | 0 | 49/50 on iteration 1; +8 vs Gen 14 |
| 4 | 3 | `gen_14_crossover_2` | 32 | 47, 49, 49, 49, 49 | 49/50 (98.0%) | 92.94 / 96.65 | $0.1294 | 0 | 47 -> 49 on iteration 2 |
| 5 | 2 | `gen_14_crossover_1` | 36 | 49, 49, 49, 49, 49 | 49/50 (98.0%) | 92.64 / 96.29 | $0.1352 | 0 | +8 vs Gen 14 |
| 6 | 8 | `gen_14_mutant_2` | 60 | 7, 44, 46, 48, 48 | 48/50 (96.0%) | 91.22 / 94.38 | $0.1836 | 0 | 7 -> 44 -> 46 -> 48 over three repair iterations (+41) |
| 7 | 7 | `gen_14_mutant_1` | 90 | 48, 48, 48, 48, 48 | 48/50 (96.0%) | 91.22 / 94.23 | $0.1985 | 0 | +6 vs Gen 14 |
| 8 | 4 | `gen_14_crossover_3` | 32 | 7, 48, 48, 48, 48 | 48/50 (96.0%) | 90.54 / 94.04 | $0.1500 | 0 | 7 -> 48 on iteration 2 (+41) |
| 9 | 6 | `gen_14_pareto_2` | 33 | 11, 47, 47, 47, 47 | 47/50 (94.0%) | 89.79 / 93.06 | $0.1726 | 0 | 11 -> 47 on iteration 2 (+36) |
| 10 | 1 | `gen_14_elite_2` | 90 | 46, 46, 46, 46, 46 | 46/50 (92.0%) | 87.14 / 90.06 | $0.2081 | 0 | +6 vs Gen 14 |
| - | - | population | 49.9 mean | 10/10 passing; 2 converged at 50/50 | 48.4/50 mean (96.8%); 50/50 peak | 90.13 / 93.75 mean; 99.43 peak | $0.1384 mean ($1.3841 total) | 2 firms (8 files) | all 10 beat Gen 14's 44/50; mean OpEx cost -17.0% |

The trajectories of the eight firms that stopped short of 50/50 are the input to the Generation 15 plateau analysis in the V6 design document, [`../v6_epistemic_search/README.md`](../v6_epistemic_search/README.md).
