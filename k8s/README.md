# GKE Serving & Cluster Setup: Reflection `Beam-1.0-501B-A23B-NVFP4` (`+ DFlash2`) and `Qwen3.8-Flash-Next-NVFP4` (`+ EPLB`)

This directory contains the GKE cluster provisioning scripts, self-hosted `vLLM` + `llm-d` serving manifests, and Kubernetes Job definitions used across all generations (`V1`–`V13`) of **Hierarchical Agent Evolution (HAE)**.

---

## 1. GKE Hardware Topology

Both model stacks run on the same GKE cluster (`hae-cluster` in `us-central1-a`), provisioned via [`cluster-setup.sh`](cluster-setup.sh):
- **GPU Node Pool (`gpu-pool-rtx6000-spot`)**: `6x g4-standard-96` Spot VMs.
  - **Per Node**: `2x NVIDIA RTX PRO 6000 Blackwell Server Edition (96 GB VRAM each = 192 GB VRAM/node)`, `96 vCPUs`, `360 GiB DDR5 RAM`, local NVMe SSD.
  - **Cluster Total**: **`12x NVIDIA RTX PRO 6000 Blackwell GPUs (`1,152 GB` / `1.15 TB` VRAM)**, `576 vCPUs`, `2.16 TiB DDR5 RAM`.
- **System / Coordinator Node Pool (`default-pool`)**: `e2-standard-8` nodes hosting the `llm-d` prefix-affinity inference gateway (`deploy/llmd-inference-gateway`) and SWE-bench `/testbed` evaluation pods (`swebench-tb-*`).

```bash
# Provision the GKE cluster and GPU driver DaemonSet:
bash k8s/cluster-setup.sh
```

---

## 2. Option A: Deploying Reflection `Beam-1.0-501B-A23B-NVFP4` + `DFlash2` (`k8s/beam-501b-nvfp4-serving.yaml`)

Reflection AI's **`reflection/Beam-1.0-501B-A23B-NVFP4`** (`501B` total parameters, `23B` active MoE parameters) has `~277 GiB` of compressed-tensors `NVFP4` weights (`57` safetensor shards) plus the **`reflection/Beam-1.0-501B-A23B-DFlash2`** speculative decoding drafter (`~3.7 GiB`).

Because each `g4-standard-96` node has `2x 96 GB = 192 GB` VRAM and `Beam-501B-NVFP4` requires `~283 GiB` VRAM for weights alone, it cannot fit on a single 2-GPU node. [`beam-501b-nvfp4-serving.yaml`](beam-501b-nvfp4-serving.yaml) pairs the 6 GPU nodes into **3 independent 2-node (`TP=4`) Ray replica groups** (`beam-0`, `beam-1`, `beam-2`):

### Architecture & Memory Layout per `TP=4` Replica (`4x 96 GB = 384 GB` VRAM)
- **Weights per GPU**: `70.71 GiB` (`NVFP4` compressed-tensors MoE) + `0.94 GiB` (`DFlash2` speculative drafter).
- **`fp8_e4m3` KV-Cache per GPU**: `12.78 GiB` $\rightarrow$ **`837,056` KV-cache tokens per replica** (**`2,511,168` (`2.51M`) total KV-cache tokens** across the 3 replicas).
- **Hybrid DDR5 `tmpfs` + NVMe Weight Loader (`ConfigMap/beam-501b-loader-script`)**:
  - Streams shards 1–28 (`~140 GiB`) into `/weights-ram` (DDR5 `emptyDir: Memory`) and symlinked into `/weights/nvfp4`, while shards 29–57 (`~137 GiB`) and `/weights/dflash` stream into `/weights` (NVMe `emptyDir`), preventing root-disk eviction while maximizing load bandwidth (`~3.5 min` cold download).
- **Container Image & vLLM Flags**:
  - Image: `vllm/vllm-openai:reflection-beam` (official Reflection `BeamForCausalLM` + `DFlash` support).
  - Speculative decoding: `--speculative-config '{"method":"dflash","model":"/weights/dflash","num_speculative_tokens":8}'`
  - Context & batching: `--tensor-parallel-size 4 --distributed-executor-backend ray --kv-cache-dtype fp8_e4m3 --max-model-len 65536 --max-num-seqs 64 --gpu-memory-utilization 0.90 --enable-prefix-caching --reasoning-parser harmony --tool-call-parser harmony`

### Deploy `Beam-1.0-501B-A23B-NVFP4`
```bash
# 1. Scale down Qwen3.8 if running so all 12 GPUs are free
kubectl scale deploy/qwen38-flash-next-180b --replicas=0

# 2. Deploy the 3 multi-node TP=4 Beam replica groups + llm-d gateway
kubectl apply -f k8s/beam-501b-nvfp4-serving.yaml

# 3. Monitor startup (~4-5 min for weight download + Ray TP=4 init + CUDA graph capture)
kubectl get pods -l app=beam-501b-nvfp4 -w

# 4. Port-forward the llm-d inference gateway locally
kubectl port-forward svc/vllm-qwen38-gateway 8000:8000
```

### Client & Prompt Configuration Notes for `Beam-1.0-501B` (`hae/infra/llm.py`, `hae/swebench/agentic.py`)
1. **`reasoning_effort` Control**: `Beam-1.0-501B`'s `chat_template.jinja` maps `reasoning_effort` (`"minimal"`/`8`, `"low"`/`16`, `"medium"`/`64`, `"high"`/`100`) into `<|reasoning_budget|>N<|end|>`. `hae/infra/llm.py` passes `reasoning_effort="medium"` on Turn 1 and `reasoning_effort="minimal"` on follow-up turns, reducing `<think>` token usage by **75%–91%** compared to `Qwen3.8`.
2. **Harmony Wire Token Sanitization**: `Beam`'s chat template rejects raw Harmony wire tokens (`<|channel|>`, `<|message|>`, `<|start|>`, `<|end|>`, `<|return|>`, `<|call|>`) in input messages (`HTTP 400`). `hae/infra/llm.py` automatically strips these tokens from outbound history and preserves `reasoning_content` across turns.
3. **`<rfl:tool_call>` Tag Stripping**: `hae/swebench/agentic.py` strips trailing `</rfl:parameter>` / `</rfl:tool_call>` tags and supports `<<<< ... >>>> ... >>>>` dividers inside `str_replace`.

---

## 3. Option B: Deploying NVIDIA `Qwen3.8-Flash-Next-NVFP4` + EPLB (`k8s/qwen38-flash-next-180b-eplb-serving.yaml`)

`nvidia/Qwen3.8-Flash-Next-NVFP4` (`180B` total parameters, `10B` active MoE parameters, `~98 GiB` weights) fits on a single `g4-standard-96` node (`2x 96 GB` GPUs) at `TP=2`. [`qwen38-flash-next-180b-eplb-serving.yaml`](qwen38-flash-next-180b-eplb-serving.yaml) runs **6 single-node `TP=2` replicas** across all 12 GPUs:

- **MoE Expert Parallelism & EPLB**: `--tensor-parallel-size 2 --enable-expert-parallel --enable-eplb`
- **KV-Cache & Offloading**: `--kv-cache-dtype fp8 --enable-prefix-caching --kv-offloading-size 48 --kv-offloading-backend native --max-model-len 131072`

### Deploy `Qwen3.8-Flash-Next-NVFP4`
```bash
# 1. Scale down Beam-501B if running
kubectl scale deploy beam-501b-0-head beam-501b-0-worker beam-501b-1-head beam-501b-1-worker beam-501b-2-head beam-501b-2-worker --replicas=0

# 2. Deploy the 6 TP=2 Qwen3.8 EPLB replicas + llm-d gateway
kubectl apply -f k8s/qwen38-flash-next-180b-eplb-serving.yaml

# 3. Monitor readiness
kubectl get pods -l app=qwen38-flash-next-180b -w
```

---

## 4. Running SWE-bench Evaluations Against Either Model

With `kubectl port-forward svc/vllm-qwen38-gateway 8000:8000` active:

```bash
# Run V13 Hierarchical AB-MCTS with Reflection Beam-1.0-501B-A23B-NVFP4:
PYTHONPATH=. python3 -u scripts/swebench_kubectl_batch.py \
  --out-root results/swebench/beam_eval_2026-10-20 \
  --policies v12_agentic v13_hier_mcts \
  --vllm-url http://127.0.0.1:8000/v1 \
  --vllm-model reflection/Beam-1.0-501B-A23B-NVFP4 \
  --workers 3

# Run V13 Hierarchical AB-MCTS with nvidia/Qwen3.8-Flash-Next-NVFP4:
PYTHONPATH=. python3 -u scripts/swebench_kubectl_batch.py \
  --out-root results/swebench/dev_eval_2026-10-09 \
  --policies v12_agentic v13_hier_mcts \
  --vllm-url http://127.0.0.1:8000/v1 \
  --vllm-model nvidia/Qwen3.8-Flash-Next-NVFP4 \
  --workers 4
```

---

## 5. Manifest Inventory (`k8s/`)

| File | Purpose |
| :--- | :--- |
| [`beam-501b-nvfp4-serving.yaml`](beam-501b-nvfp4-serving.yaml) | **Reflection `Beam-1.0-501B-A23B-NVFP4` + `DFlash2`**: 3 multi-node Ray `TP=4` replica pairs (`beam-501b-{0,1,2}-{head,worker}`), hybrid DDR5+NVMe weight loader, headless Ray/HTTP services, and `llm-d` gateway |
| [`qwen38-flash-next-180b-eplb-serving.yaml`](qwen38-flash-next-180b-eplb-serving.yaml) | **NVIDIA `Qwen3.8-Flash-Next-NVFP4` (`180B-A10B`)**: 6 single-node `TP=2` replicas with `--enable-expert-parallel --enable-eplb`, 48 GiB CPU KV-cache offload, and `llm-d` gateway |
| [`qwen38-flash-next-180b-llmd.yaml`](qwen38-flash-next-180b-llmd.yaml) | Initial V4–V9 3-replica `Qwen3.8-Flash-Next-NVFP4` serving manifest |
| [`gen16-cohort-serving.yaml`](gen16-cohort-serving.yaml) | V6 Gen-16 4-replica cohort serving overlay |
| [`cluster-setup.sh`](cluster-setup.sh) / [`teardown.sh`](teardown.sh) | GKE cluster creation (`g4-standard-96` `2x RTX PRO 6000` node pool) and teardown scripts |
