# Experiment: 10-Firm Parallel Tournament on GKE (us-east4)

## Executive Summary
This experiment evaluates the scalability, concurrency, and selection dynamics of the Hierarchical Agent Evolution framework deployed on a dedicated regional Google Kubernetes Engine (GKE) cluster in `us-east4` (`hae-regional-cluster-east4`), completely outside congested `us-central1`.

Ten distinct virtual enterprises (each comprising 31 specialized agents across 5 operational departments and an executive tier) were scheduled and evaluated concurrently using a Kubernetes Indexed Job with `parallelism: 5`.

---

## Hardware & Infrastructure Configuration

| Parameter | Specification |
| :--- | :--- |
| **GKE Cluster** | `hae-regional-cluster-east4` (`us-east4-a`) |
| **Compute Nodes** | 3 $\times$ `e2-standard-4` (12 vCPUs, 48 GB RAM) |
| **Workload Orchestration** | Kubernetes Batch Indexed Job (`completions: 10`, `parallelism: 5`) |
| **Isolation & Security** | In-Cluster Secret Token Authentication (`VERTEX_API_TOKEN`) |
| **Regional Endpoint** | `us-east4-aiplatform.googleapis.com` (Gemini 2.5 Flash / Pro) |
| **Total Concurrency** | 5 simultaneous enterprises (155 concurrent active agent threads) |
| **Total Evaluation Duration** | **22 minutes** (vs. ~90 minutes sequential baseline) |
| **Throughput Speedup** | **~4.1x faster** turnaround |

---

## Generation 0 Parallel Leaderboard

All 10 enterprises were evaluated against identical business objectives ("Design and implement the production-ready 'agent-org' platform") and subjected to the 4-Gate Deterministic Sandbox Verifier alongside multi-dimensional rubric judging.

| Rank | Enterprise ID | Overall Score (/100) | Deterministic Sandbox Status | Penalty Applied | Status |
| :---: | :--- | :---: | :---: | :---: | :---: |
| 🥇 **1** | `gen_0_firm_3` | **77.50** | Telemetry Gate: **PASS** | -18.75 pts | **Survivor (Breeding Pool)** |
| 🥈 **2** | `gen_0_firm_6` | **75.50** | Telemetry Gate: **PASS** | -18.75 pts | **Survivor (Breeding Pool)** |
| 🥉 **3** | `gen_0_firm_5` | **72.95** | Baseline File Gate | -25.00 pts | **Survivor (Breeding Pool)** |
| 4 | `gen_0_firm_7` | **72.95** | Baseline File Gate | -25.00 pts | **Survivor (Breeding Pool)** |
| 5 | `gen_0_firm_8` | **72.95** | Baseline File Gate | -25.00 pts | **Survivor (Breeding Pool)** |
| 6 | `gen_0_firm_1` | **71.75** | Baseline File Gate | -25.00 pts | Eliminated |
| 7 | `gen_0_firm_2` | **71.25** | Baseline File Gate | -25.00 pts | Eliminated |
| 8 | `gen_0_firm_4` | **70.50** | Baseline File Gate | -25.00 pts | Eliminated |
| 9 | `gen_0_firm_10` | **70.10** | Baseline File Gate | -25.00 pts | Eliminated |
| 10 | `gen_0_firm_9` | **69.75** | Baseline File Gate | -25.00 pts | Eliminated |

---

## Key Empirical Findings

1. **Selection Pressure Drives Architectural Rigor**:
   Enterprises `gen_0_firm_3` and `gen_0_firm_6` successfully embedded explicit OpenTelemetry observability specifications into their architectural output, earning a pass on the Telemetry Gate (reducing penalty from 25.0 to 18.75). Consequently, both claimed the top two positions on the leaderboard.
2. **Zero Regional Throttling in `us-east4`**:
   Zero `RESOURCE_EXHAUSTED` (HTTP 429) or quota throttling errors occurred across all 310 agent deliberations. Regional isolation from `us-central1` completely resolved API contention.
3. **Secret Token Authentication Resilience**:
   In-cluster secret token authentication completely insulated pods from external IAM policy wipes (Latchkey), achieving 100% pod completion reliability (10/10 completions).
4. **Token & Cost Metrics**:
   * **Total Input/Output Tokens**: ~235,000 tokens across all 10 enterprises.
   * **Inference Cost**: ~$0.44 USD total (~$0.044 per 31-agent firm).

---

## Next Steps
* Enable **GKE Agent Sandbox** (`kubernetes-sigs/agent-sandbox`) for Generation 2 to support sub-second warm-pool `pytest` dynamic execution.
* Execute 3-Way Evolutionary Breeding on Top 5 Survivors (`gen_0_firm_3`, `gen_0_firm_6`, `gen_0_firm_5`, `gen_0_firm_7`, `gen_0_firm_8`) to generate Generation 1 offspring.
