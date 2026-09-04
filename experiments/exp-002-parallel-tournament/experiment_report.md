# Experiment: High-Throughput 10-Firm Parallel Tournament on Distributed Kubernetes

## 1. Executive Summary
This experiment evaluates the scalability, concurrent execution throughput, and selection pressure of the Hierarchical Agent Evolution framework running on a dedicated Kubernetes cluster.

Ten distinct virtual enterprises (each comprising 31 specialized agents across 5 operational departments and an executive tier) were scheduled and evaluated concurrently using a Kubernetes Batch Indexed Job (`parallelism: 5`).

---

## 2. Infrastructure & Compute Configuration

| Parameter | Specification |
| :--- | :--- |
| **Compute Infrastructure** | Cloud Kubernetes Cluster (Dedicated 3-Node Worker Pool, 12 vCPUs, 48 GB RAM) |
| **Workload Orchestration** | Kubernetes Batch Indexed Job (`completions: 10`, `parallelism: 5`) |
| **Security & Authentication** | In-Cluster Secret Token Mounting (`VERTEX_API_TOKEN`) |
| **Model Tiers** | `gemini-2.5-flash` (Worker Tier) / `gemini-2.5-pro` (Executive, Judge & Meta-Architect) |
| **Active Concurrency** | 5 simultaneous enterprises (155 concurrent active agent deliberation threads) |
| **Total Evaluation Duration** | **22 minutes** (vs. ~90 minutes sequential baseline) |
| **Throughput Multiplier** | **~4.1x faster** generational turnaround |

---

## 3. Generation 0 Parallel Leaderboard

All 10 enterprises were evaluated against an identical engineering objective:
> *"Design and implement the production-ready 'agent-org' platform: a Python package providing hierarchical multi-agent orchestration, OpenTelemetry observability anchors, deterministic sandbox execution, and evolutionary search."*

Deliverables were subjected to a 4-Gate Deterministic Sandbox Verifier alongside multi-dimensional rubric judging.

| Rank | Enterprise ID | Overall Score (/100) | Deterministic Sandbox Status | Penalty Applied | Selection Outcome |
| :---: | :--- | :---: | :---: | :---: | :---: |
| 🥇 **1** | `gen_0_firm_3` | **77.50** | Telemetry Gate: **PASS** | -18.75 pts | **Selected for Breeding Pool** |
| 🥈 **2** | `gen_0_firm_6` | **75.50** | Telemetry Gate: **PASS** | -18.75 pts | **Selected for Breeding Pool** |
| 🥉 **3** | `gen_0_firm_5` | **72.95** | Baseline File Gate | -25.00 pts | **Selected for Breeding Pool** |
| 4 | `gen_0_firm_7` | **72.95** | Baseline File Gate | -25.00 pts | **Selected for Breeding Pool** |
| 5 | `gen_0_firm_8` | **72.95** | Baseline File Gate | -25.00 pts | **Selected for Breeding Pool** |
| 6 | `gen_0_firm_1` | **71.75** | Baseline File Gate | -25.00 pts | Eliminated |
| 7 | `gen_0_firm_2` | **71.25** | Baseline File Gate | -25.00 pts | Eliminated |
| 8 | `gen_0_firm_4` | **70.50** | Baseline File Gate | -25.00 pts | Eliminated |
| 9 | `gen_0_firm_10` | **70.10** | Baseline File Gate | -25.00 pts | Eliminated |
| 10 | `gen_0_firm_9` | **69.75** | Baseline File Gate | -25.00 pts | Eliminated |

---

## 4. Key Empirical Findings

1. **Deterministic Selection Pressure Drives Architectural Rigor**:
   Enterprises `gen_0_firm_3` and `gen_0_firm_6` successfully synthesized explicit OpenTelemetry observability specifications into their architectural deliverables, passing the Telemetry Gate (reducing penalty from 25.0 to 18.75 pts). Consequently, both claimed the top two positions on the leaderboard.
2. **Zero API Throttling Under High Concurrency**:
   Zero `RESOURCE_EXHAUSTED` (HTTP 429) errors occurred across all 310 agent deliberations. The decoupled parallel batch scheduling effectively distributed request bursts across backend model endpoints.
3. **In-Cluster Secret Token Resilience**:
   In-cluster secret token authentication achieved 100% pod execution reliability (10/10 successful firm completions) without external authentication timeouts.
4. **Token & Cost Efficiency**:
   * **Total Input/Output Tokens**: ~235,000 tokens across all 10 enterprises.
   * **Inference Cost**: ~$0.44 USD total (~$0.044 per 31-agent enterprise).

---

## 5. Next Steps
* Deploy **Kubernetes Agent Sandbox** (`kubernetes-sigs/agent-sandbox`) with gVisor isolation to enable sub-second warm-pool `pytest` dynamic execution.
* Execute 3-Way Evolutionary Breeding on Top 5 Survivors (`gen_0_firm_3`, `gen_0_firm_6`, `gen_0_firm_5`, `gen_0_firm_7`, `gen_0_firm_8`) to generate Generation 1 offspring.
