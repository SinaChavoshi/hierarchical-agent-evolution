# V10 (Empirical AB-MCTS + EPLB) Evaluation Report

This report summarizes **Experiment 10 (V10)** across all **30 validated SWE-bench dev instances**, measuring both the algorithmic impact of **Empirical AB-MCTS** ([`AdaptiveBranchingController`](../../hae/epistemic/ab_mcts.py#L185-L265)) and the cluster infrastructure impact of enabling **Expert Parallelism + EPLB** (`--enable-expert-parallel --enable-eplb`) across the 6 `nvidia/Qwen3.8-Flash-Next-NVFP4` MoE replicas (`12x NVIDIA RTX PRO 6000`).

---

## 1. SWE-Bench Resolution & Quality Comparison (`V8` vs `V9` vs `V10`)

By fixing V9's unpenalized pending-hypothesis backlog (`ab_pending_loss = 1.5`) and boosting physical testbed verification (`ab_deepen_boost = 0.20`), **V10 achieved the highest resolution rate and lowest false-positive rate of any generation tested**:

| Metric | V8 (`puct`) | V9 (`ab_mcts` raw) | **V10 (`Empirical AB-MCTS` + EPLB)** | V10 vs V9 | V10 vs V8 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Graded Resolved (Full Suite Pass)** | 4 / 30 (`13.3%`) | 3 / 30 (`10.0%`) | **5 / 30 (`16.7%`)** | **+66.7% relative (+2)** | **+25.0% relative (+1)** |
| **Self-Oracle False Positives** | 4 | 5 | **3** | **-40.0% (-2)** | **-25.0% (-1)** |
| **Total Tokens Processed** | 2,564,480 | 2,881,238 | **3,018,921** | +4.8% | +17.7% |
| **Total Search Moves Used** | 509 | 539 | **566** | +5.0% | +11.2% |

### Solved Instances Breakdown (`5 / 30`)
1. **`marshmallow-code__marshmallow-1164`** — Solved in **15 moves** (`365.3s`)
2. **`marshmallow-code__marshmallow-1810`** — Solved in **13 moves** (`220.0s`)
3. **`pylint-dev__astroid-1417`** — Solved in **7 moves** (`164.1s`) *(Flipped from a **V9 Self-Oracle False Positive** into a clean full-suite pass)*
4. **`pylint-dev__astroid-1959`** — Solved in **30 moves** (`695.5s`)
5. **`pylint-dev__astroid-1962`** — Solved in **30 moves** (`666.8s`) *(Flipped from a **V9 Self-Oracle False Positive** into a clean full-suite pass)*

---

## 2. Infrastructure & Token Generation Rate Analysis (Pre-EPLB vs Post-EPLB)

### A. End-to-End Cluster Wall-Clock Throughput (All 6 Concurrent Workers)
* **Pre-EPLB Baseline (V8 + V9 Batch)**: Processed `5,445,718` total tokens across `7,080s` (`118.0` wall-clock minutes) $\rightarrow$ **`769.2 total tokens/s`**.
* **Post-EPLB (V10 Batch)**: Processed `3,018,921` total tokens across `3,662s` (`61.0` wall-clock minutes) $\rightarrow$ **`824.4 total tokens/s`** (**+7.2% end-to-end cluster throughput improvement**, despite rolling replica weight reloads occurring during the first 12 minutes of the run).

### B. Per-Worker Effective Throughput (`total_tokens / pod_elapsed_s`, including Docker Testbed Wait)
* **V8 (`puct`, pre-EPLB)**: `153.7 tokens/s` (`2,564,480` tokens over `16,680` aggregate pod-seconds)
* **V9 (`ab_mcts`, pre-EPLB)**: `207.2 tokens/s` (`2,881,238` tokens over `13,908` aggregate pod-seconds)
* **V10 (`Empirical AB-MCTS`, post-EPLB)**: `186.4 tokens/s` (`3,018,921` tokens over `16,200` aggregate pod-seconds — reflecting higher empirical Docker testbed verification compared to V9).

### C. Direct Prometheus Engine Metrics Across All 6 EPLB Replicas (`vllm:` Telemetry)
Direct scraping of `/metrics` across all 6 active `qwen38-flash-next-180b` pods (`512` total experts, `10` active/token, `--tensor-parallel-size 2 --enable-expert-parallel --enable-eplb`) showed crisp MoE balance and prefill/decode responsiveness under multi-thousand-token SWE-bench contexts:
* **Mean Time to First Token (TTFT)**: **`533.4 ms`** (`81.3%` under `500ms`; `95.6%` under `1.0s`)
* **Mean Time Per Output Token (TPOT)**: **`56.46 ms/token`** $\rightarrow$ **`17.7 decoded output tokens/s` per stream** (`106.3 decoded output tokens/s` aggregate across the 6 replicas at concurrency 1, scaling linearly under concurrent requests).
