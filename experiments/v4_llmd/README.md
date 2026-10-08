# V4: Generations 10-11 on three Qwen3.8-Flash-Next-NVFP4 (180B) replicas behind a shared llm-d gateway

Phase 4 (V4) of hierarchical-agent-evolution (HAE) ran Generations 10 and 11 on three self-hosted nodes serving `nvidia/Qwen3.8-Flash-Next-NVFP4` (a 180B-parameter mixture-of-experts model in NVIDIA's 4-bit NVFP4 format) behind one shared llm-d inference gateway, the request router between the agent worker pods and the vLLM replicas. Both generations ran after commit 71a7da4 fixed the seed-file leak that had copied a parent's passing `morphogenesis.py` into child workspaces (see the [V3 report](../v3_rsi/README.md)), so they are true zero-seed runs: no firm inherited the target module.

Results: Gen 10, 10/10 firms finished, 6/10 passed 7/7 held-out tests, peak net fitness 78.92, mean 42.78. Gen 11, **10/10 firms passed 7/7 held-out tests on iteration 1 of 10** (mean iterations 1.00), peak 95.29, mean 91.08, subject to the independence caveat below. That is +40.0 points of pass rate (60% to 100%), +16.37 points of peak and +48.30 points of mean net fitness over Gen 10; under the pre-V4 setup, 90-agent firms had scored 0/7.

## What changed

Earlier phases used cloud APIs (Gemini 2.5) and a single-node open-source engine with a 32k context (Qwen3-Coder-32B). V4 serves one vLLM replica of the 180B model per `g4-standard-96` node of `chavoshi-g4-cluster` (6 GPUs, 576 GiB of GDDR7 VRAM in total) behind `Deployment/llmd-inference-gateway`, defined in [`k8s/qwen38-flash-next-180b-llmd.yaml`](../../k8s/qwen38-flash-next-180b-llmd.yaml). Three full runs were made (call counts in the gateway table below): Gen 10, a Gen 11 attempt with tools enabled for every agent that became an unplanned stress test, and the Gen 11 run reported here. The original summary counted 8,500+ calls; the per-run counts sum to 7,894.

1. One gateway session for all companies. Instead of keying sessions by `X-Company-ID`, `canonicalize_prompt_for_global_session` rewrites company identifiers (`gen_9_elite_1`, `gen_10_crossover_2`, `firm_3`, ...) to `<SHARED_COMPANY>`, so identical prompts from different companies hash to one key. A prompt already answered for one company is returned to the next from the gateway cache (`X-LLMD-Cache: HIT-SHORT-CIRCUIT`) in 0.74 ms instead of the 880.5 ms of a 180B forward pass (1,190x, no GPU work); concurrent identical prompts from parallel worker pods are coalesced into one forward pass ("singleflight", `X-LLMD-Cache: HIT-SINGLEFLIGHT-COALESCED`). Before, every company ran its own forward passes (0.0% offload).
2. Prefix-affinity routing. Bounded-load consistent hashing over the shared task/codebase prefix sends each request to the replica holding the warm fp8 key/value (KV) cache for that prefix, replacing round-robin (about 33.3% prefix hit rate over 3 nodes). The cache is tiered: 282.3 GiB of fp8 GDDR7 VRAM plus 144.0 GiB of CPU DRAM offload in `/dev/shm` across the nodes, 426.3 GiB in total against 46 GiB of VRAM on the previous single node (9.2x).
3. No hard call ceiling; logarithmic token-efficiency scoring (commit 86c964c). Self-hosted inference has no marginal per-token API cost, so the legacy Vertex AI cutoff (`working_max_calls = 585`, `calls = 586`), which had truncated 60- to 90-agent companies mid-run, was removed (`max_calls: null`). Scoring now uses a smooth, unbounded curve over the shadow token cost (tokens priced at reference API rates against a $0.50 budget): $\text{Penalty} = 4.0 \ln(1 + \frac{\text{cost} - \text{budget}}{\text{budget}})$, $\text{Bonus} = \min(5.0,\ 5.0 \frac{\text{budget} - \text{cost}}{\text{budget}})$.
4. Longer context, no thinking blocks, new parsing. Context went from 32,768 to 131,072 tokens (128k) and max output from 3,072-4,096 to 16,384 (4.0x each). Thinking is off (`enable_thinking: False`) and a `</think>` stripper in the gateway removes residual reasoning blocks, cutting per-turn response time from 6,980 ms to 880.5 ms (7.9x, about 5x fewer wasted output tokens). Native Qwen3.8 XML plus fenced-module parsing (commit f8beb16) and direct specification propagation (commit 86c964c) landed at the same time.

## Gateway measurements

| Run | LLM calls | Served without GPU work (short-circuit / coalesced) | Routed to the replica with the warm prefix |
| :-- | --: | --: | --: |
| Gen 10 | 2,289 | 327 (14.3%; 276 / 51) | 1,903 / 1,962 (96.9%) |
| Gen 11 stress run (tools on all 555 agents) | 4,881 | 2,113 (43.29%; 1,725 / 388) | 2,760 / 2,768 (99.71%) |
| Gen 11 converged run (10 companies, 555 agents) | 724 | 271 (37.43%; 106 / 165) | 455 / 455 (100.00%, 0 load spillovers) |

The offload rate peaked at 73.2% (71 of 97 requests) while the first wave of Gen 11 companies started in parallel. Serving 37.4%-43.3% of requests from the gateway corresponds to 1.60x-1.93x the effective throughput of the same six GPUs, and the prefix affinity is about 3.0x the round-robin hit rate. The 4,881-call stress run finished without a pod restart or out-of-memory event.

## Generation 11 ledger

Job `job.batch/hae-gen11-qwen38-180b`. Task: zero-seed synthesis (`carry_artifacts=False`, 0/10 firms inherited the target module) of [`hae/genome/morphogenesis.py`](../../hae/genome/morphogenesis.py) (`MorphogenesisEngine` and `StructuralCrossoverEngine`), graded on the 7 held-out unit tests in [`tests/test_morphogenesis.py`](../../tests/test_morphogenesis.py), which the agents never see; no call limit; scoring as above. All ten firms passed 7/7 on iteration 1 of 10. Net fitness is the gross score plus the efficiency bonus; the overlay is the authored `morphogenesis.py`.

| Rank | Company | Agents | Overlay (bytes) | Shadow cost | Bonus | Gross | Net |
| :-- | :-- | --: | --: | --: | --: | --: | --: |
| 1 | `gen_10_pareto_2` | 60 | 15,460 | $0.2411 | +2.59 | 92.70 | **95.29** |
| 2 | `gen_10_pareto_1` | 33 | 16,352 | $0.1441 | +3.56 | 91.20 | 94.76 |
| 3 | `gen_10_mutant_3` | 90 | 19,727 | $0.3331 | +1.67 | 92.70 | 94.37 |
| 4 (tie) | `gen_10_elite_1` | 33 | 14,786 | $0.1356 | +3.64 | 90.70 | 94.34 |
| 4 (tie) | `gen_10_crossover_3` | 33 | 14,786 | $0.1356 | +3.64 | 90.70 | 94.34 |
| 6 | `gen_10_crossover_1` | 33 | 16,959 | $0.2041 | +2.96 | 90.70 | 93.66 |
| 7 (tie) | `gen_10_elite_2` | 90 | 14,051 | $0.2851 | +2.15 | 90.70 | 92.85 |
| 7 (tie) | `gen_10_crossover_2` | 90 | 14,051 | $0.2851 | +2.15 | 90.70 | 92.85 |
| 9 | `gen_10_mutant_1` | 33 | 13,314 | $0.1801 | +3.20 | 78.60 | 81.80 |
| 10 | `gen_10_mutant_2` | 60 | 20,868 | $0.4923 | +0.08 | 76.50 | 76.58 |
| Mean (10 firms, 555 agents) | | 55.5 | 16,035 | $0.2436 | +2.56 | 88.52 | **91.08** |

Caveat on independence. Because all companies shared one gateway session, identical builder prompts from different companies were served the same model-generated answer instead of each company producing its own. The ledger shows it: `gen_10_elite_1` and `gen_10_crossover_3` have identical overlay size, cost and scores, as do `gen_10_elite_2` and `gen_10_crossover_2`. The result is zero-seed, but the ten firms are not ten independent samples. The current serving manifest ([`k8s/qwen38-flash-next-180b-llmd.yaml`](../../k8s/qwen38-flash-next-180b-llmd.yaml)) excludes code-authoring requests (those carrying `ACTIVE WORKSPACE SANDBOX:`) from both short-circuit and singleflight, so later generations did not share synthesis across companies.

Token efficiency. With the 586-call ceiling gone, all three 90-agent firms and both 60-agent firms finished on iteration 1 and passed 7/7, like the 33-agent firms. The curve still favoured leaner firms, from +3.64 for the 33-agent `gen_10_elite_1` ($0.1356) to +1.67 for the 90-agent `gen_10_mutant_3` ($0.3331); the +2.59 bonus is what put the 60-agent `gen_10_pareto_2` (92.70 gross, $0.2411) first.

Tool-proliferation ablation. The earlier Gen 11 attempt set `tools_enabled=True` for every agent instead of only the two `Autonomous Self-Repair Core Architect` engineers per firm. With workspace tools in the hands of all 27-84 business, strategy and finance agents, the run made 4,881 calls instead of 724 (6.7x) and scored 0/7 because non-coding agents overwrote the shared workspace. This is consistent with the design rule that tool-write permission belongs to one or two engineers while deliberating agents stay stateless.

## Generation 10 ledger

Job `job.batch/hae-gen10-qwen3-coder`, same task and tests. The parent-lineage column is reproduced as recorded.

| Rank | Company | Parent lineage | Agents | Tests | Iterations | Gross | Net |
| :-- | :-- | :-- | --: | :-- | :-- | --: | --: |
| 1 | `gen_9_pareto_1` | `gen_8_pareto_2` | 33 | 7/7 | 1/10 | 78.9 | **78.92** |
| 2 | `gen_9_crossover_2` | `gen_9_crossover_2` | 90 | 7/7 | 1/10 | 75.1 | 75.32 |
| 3 | `gen_9_crossover_1` | `gen_9_crossover_1` | 60 | 7/7 | 1/10 | 74.2 | 75.15 |
| 4 | `gen_9_elite_1` | `gen_8_pareto_2` | 33 | 7/7 | 1/10 | 67.0 | 68.34 |
| 5 | `gen_9_elite_2` | `gen_8_elite_2` | 33 | 7/7 | 1/10 | 68.3 | 68.32 |
| 6 | `gen_9_crossover_3` | `gen_9_crossover_3` | 90 | 6/7 (85.71%) | 2/10 | 76.71 | 61.71 |
| 7 | `gen_9_mutant_3` | `gen_9_mutant_3` | 63 | partial | 9/10 | 59.7 | 44.70 |
| 8 | `gen_9_pareto_2` | `gen_8_pareto_2` | 33 | 0/7 | 10/10 | 15.5 | 0.50 |
| 9 | `gen_9_mutant_1` | `gen_9_mutant_1` | 27 | 0/7 | 10/10 | 7.0 | 0.00 |
| 10 | `gen_9_mutant_2` | `gen_9_mutant_2` | 33 | 0/7 | 10/10 | 6.0 | 0.00 |

## Hardware and storage

Each `g4-standard-96` node in `chavoshi-g4-cluster` (zone `us-east5-a`) has 2x NVIDIA RTX PRO 6000 Blackwell Server Edition 96 GB GPUs (192 GiB GDDR7), 96 vCPUs, 354 GiB of DDR5 RAM and a 242 GiB NVMe boot disk (`/dev/nvme0n1p1`).

Model loading: a 6-thread loader streams the weights over HTTP with `urllib.request` (16 MiB buffer), bypassing the 75 GiB root-filesystem chunk cache `hf_xet` would otherwise fill. Shards `model-00001-of-00010.safetensors` to `model-00010-of-00010.safetensors` (73.5 GiB) go to a 74 GiB DDR5 tmpfs at `/model-ram` (`emptyDir: { medium: Memory, sizeLimit: 80Gi }`, symlinked into `/model`); `model-fp8-mtp-ple.safetensors` (50.0 GiB) goes to `/model` on the NVMe SSD (50 GiB). This leaves 100-160 GiB (40%-65%) of the disk free, 4x the kubelet eviction threshold, and more than 140 GiB of free RAM per node.

KV cache: vLLM V1 runs with `--tensor-parallel-size 2 --max-model-len 131072 --gpu-memory-utilization 0.92 --kv-cache-dtype fp8 --enable-prefix-caching --kv-offloading-size 48 --kv-offloading-backend native`, giving 47.05 GiB x 2 = 94.10 GiB of fp8 KV cache in VRAM per node plus a 49,116 MiB (48 GiB) native CPU KV-offload region in `/dev/shm` (`emptyDir: { medium: Memory, sizeLimit: 64Gi }`); over three nodes, the 282.3 GiB plus 144 GiB (426.3 GiB) cited above.
