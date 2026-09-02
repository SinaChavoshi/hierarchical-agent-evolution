# Hierarchical Agent Evolution (HAE)
### Recursive Self-Hosting and Architecture Search for Autonomous Organizations on Cloud Kubernetes

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Infrastructure: GKE](https://img.shields.io/badge/Infrastructure-Google%20Kubernetes%20Engine-326ce5.svg)](https://cloud.google.com/kubernetes-engine)
[![LLM: Gemini 2.5 Flash & Pro](https://img.shields.io/badge/Vertex%20AI-Gemini%202.5-orange.svg)](https://cloud.google.com/vertex-ai)

---

## 1. Abstract & Research Premise

Current multi-agent frameworks (e.g., CrewAI, AutoGen, MetaGPT) typically rely on flat communication graphs or static role topologies. When scaled beyond 10 agents, these flat structures exhibit severe context dilution, quadratic token communication overhead $\mathcal{O}(N^2)$, and reasoning collapse. Furthermore, organizational charts, persona backstories, and delegation protocols are conventionally hand-crafted through trial-and-error human prompt engineering.

**Hierarchical Agent Evolution (HAE)** is a distributed framework that models organizations as federated hierarchies (30–50 agents per firm partitioned into specialized departmental pods under an executive steering council) and optimizes their organizational genome via genetic programming. 

Crucially, HAE investigates **recursive self-hosting**: competing virtual enterprises are tasked with designing and implementing the next-generation engine of the platform itself, enabling continuous bootstrapping without human engineering intervention.

```mermaid
graph TD
    subgraph Federated_Enterprise_Topology ["Federated Enterprise Topology (31–50 Agents)"]
        CEO["Chief Executive Officer (gemini-2.5-pro)<br/>Executive Directive & Master Strategic Synthesis"]
        CEO --> M1["VP Systems Architecture"]
        CEO --> M2["VP Product & Developer UX"]
        CEO --> M3["VP Market Strategy"]
        CEO --> M4["VP Finance & Operations"]
        CEO --> M5["Director QA & Adversarial Red Team"]
        
        M1 --> S1["Pod 1: Hardware, Network & Cooling Specialists (gemini-2.5-flash)"]
        M2 --> S2["Pod 2: API, SDK & Documentation Specialists (gemini-2.5-flash)"]
        M3 --> S3["Pod 3: Go-To-Market, Positioning & Sales Specialists (gemini-2.5-flash)"]
        M4 --> S4["Pod 4: CapEx, Unit Economics & Substation Specialists (gemini-2.5-flash)"]
        M5 --> S5["Pod 5: Chaos, Attack-Vector & Audit Specialists (gemini-2.5-flash)"]
    end
```

---

## 2. The 3-Way Genetic Breeding Engine

To navigate the high-dimensional search space of prompt backstories, organizational charts, and delegation protocols without premature convergence, HAE employs a **3-Way Breeding Pool**:

```
                       [ 50 Competing Firms in Generation g ]
                                         │
                         [ Hard Ground-Truth Sandbox Run ]
                                         │
                             [ Select Top 5 Winners ]
                                         │
    ┌────────────────────────────────────┼────────────────────────────────────┐
    │ (Exploitation)                     │ (Pareto Exploitation)              │ (Directed Exploration)
    ▼                                    ▼                                    ▼
Group A: Consensus (~15 Firms)      Group B: Dimension Extremes (~15)    Group C: Directed Mutants (~15)
- Extract common structural motifs   - 3x Extreme Execution Velocity      - Hypothesis-driven novel roles
- Universal role definitions         - 3x Extreme Technical Rigor         - Contrarian organizational rules
- Intersected delegation rules       - 3x Extreme Telemetry/Observability - Untested prompt topologies
- Shared persona guardrails          - 3x Extreme Modularity / Simplicity - Dynamic role scaling
    │                                    │                                    │
    └────────────────────────────────────┼────────────────────────────────────┘
                                         ▼
                       [ 45 New Offspring + 5 Elites = 50 Firms in Gen g+1 ]
```

1. **Group A: Consensus Exploitation (15 Firms)**:
   An AST and schema diffing engine identifies common structural traits present across $\ge 80\%$ of winning firms (e.g. mandatory OpenAPI contract agents, strict QA temperatures $\le 0.3$) and preserves them as invariants.
2. **Group B: Pareto Frontier Amplification (15 Firms)**:
   Clones and amplifies specialized dimension champions (e.g. Extreme Technical Rigor, Extreme Adversarial Risk Mitigation, Extreme Telemetry Rigor).
3. **Group C: Directed Hypothesis Mutations (15 Firms)**:
   An LLM Meta-Architect (`gemini-2.5-pro`) reviews collective post-mortem bottlenecks and formulates testable structural hypotheses (e.g., injecting an SRE Chaos Engineer or an ITAR Compliance Officer).
4. **Elites (5 Firms)**:
   The top 5 original surviving firms are preserved unaltered into the next round.

---

## 3. Ground-Truth Deterministic Sandbox Verification

To prevent LLM hallucination and ensure competing firms write real, working software rather than plausible text memos, HAE enforces a strict **Deterministic Sandbox Gate**:

$$\text{Fitness Floor} = \begin{cases} 
0 & \text{Build failure (\texttt{pip install -e .} fails)} \\
20 \times \text{PassRate} & \text{Unit/Integration test failures (\texttt{pytest})} \\
50 + 0.5 \times F_{\text{LLM}} & \text{Clean build + passing tests + successful smoke execution}
\end{cases}$$

* **Build Gate**: Verifies valid `pyproject.toml` and directory structure.
* **Test Gate**: Executes test suites under `pytest` with coverage tracking.
* **Smoke Execution Gate**: Instantiates a mini 3-agent hierarchy using the firm's generated package and executes a sample task.
* **Telemetry Gate**: Verifies that OpenTelemetry spans and metrics are actively emitted.

---

## 4. Empirical Pilot Results (Google Kubernetes Engine)

During initial validation on Google Kubernetes Engine (GKE) leveraging Vertex AI in Google Cloud Platform (`YOUR_GCP_PROJECT_ID`):

### Generation 0 Leaderboard (Baseline 31-Agent Enterprises)
| Rank | Firm ID | Strategy Name | Overall Score | Strategic (25%) | Technical (25%) | Coherence (20%) | Risk (15%) | Action (15%) |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| 🥇 **#1** | **`gen_0_firm_3`** | *The Forged Path* | **`94.60`** | `95.0` | `88.0` | `98.0` | **`97.0`** | `98.0` |
| 🥈 **#2** | **`gen_0_firm_1`** | *Project Chimera* | **`93.75`** | `95.0` | **`92.0`** | **`100.0`** | `80.0` | **`100.0`** |
| 🥉 **#3** | **`gen_0_firm_2`** | *Project Cathedral* | **`93.00`** | `95.0` | `80.0` | **`100.0`** | `95.0` | **`100.0`** |

### Autonomous Genetic Adaptation
* The evaluation judge flagged a critical-path single point of failure in Firm 3's 6-month custom silicon tape-out schedule and key-person executive dependencies.
* The evolutionary mutator autonomously adapted the genome, expanding headcount from **31 to 36 agents** in `gen_1_mutant_1` to add redundant silicon supply-chain and regulatory compliance specialists.

### Generation 1 Tournament Progression & Final Champion
* `gen_1_elite_1`: Score `89.80`
* `gen_1_mutant_1` (36 agents, resolved tape-out bottleneck): Score `94.00`
* `gen_1_elite_2`: **`96.25 / 100`** (**Tournament Champion**)

### Efficiency & Token Economics
* **Total Tokens Consumed**: ~115,000 tokens across 6 complete virtual enterprises.
* **Effective Inference Cost**: **~$0.22 USD** total.
* **Model Tiering**: Gemini 2.5 Flash for operational specialists accounted for 70% of tokens at 1/15th the cost of frontier reasoning models, while Gemini 2.5 Pro reserved for executives guaranteed high-level synthesis quality.

---

## 5. Repository Structure

```
hierarchical-agent-evolution/
├── pyproject.toml              # Build config & dependencies
├── Dockerfile                  # Container definition for GKE execution
├── cloudbuild.yaml             # Google Cloud Build automation
├── src/
│   ├── schema.py               # Genome schemas (Company, Department, Agent)
│   ├── company.py              # Federated hierarchical execution runner
│   ├── evaluator.py            # LLM-as-a-Judge multi-dimensional rubric
│   ├── mutator.py              # Genetic mutators & crossover operators
│   ├── breeding.py             # 3-Way breeding engine (Consensus, Pareto, Directed)
│   ├── sandbox_verifier.py     # Deterministic code extraction & execution verifier
│   ├── telemetry.py            # Research ledger, OpenTelemetry & cost tracking
│   ├── engine.py               # Tournament controller & generation manager
│   ├── llm_factory.py          # Vertex AI REST client with retry logic
│   └── main.py                 # CLI entrypoint
├── research/
│   ├── WHITE_PAPER_DRAFT.md    # Academic paper manuscript
│   └── RESEARCH_LOGBOOK.md     # Empirical ledger and lineage logs
├── docs/
│   └── PLATFORM_SPECIFICATION.md# Production platform architecture blueprint
├── k8s/
│   ├── evolution-job.yaml      # Kubernetes Job manifest
│   └── rbac.yaml               # ServiceAccount & RBAC bindings
├── templates/
│   └── default_company.json    # Generation 0 seed enterprise genome
└── tests/
    └── test_evolutionary_pipeline.py # Integration test suite
```

---

## 6. Getting Started

### Local Quickstart
```bash
# Clone repository
git clone https://github.com/SinaChavoshi/hierarchical-agent-evolution.git
cd hierarchical-agent-evolution

# Install package
pip install -e .

# Run unit & integration tests
PYTHONPATH=. python3 tests/test_evolutionary_pipeline.py

# Run a single firm execution
python3 -m src.main --mode single-firm --objective "Build next-generation telemetry engine"
```

### Multi-Cluster GKE Deployment
To run high-throughput parallel tournaments across multiple non-congested cloud regions (`us-east4`, `us-west1`):

```bash
# Authenticate with GKE
gcloud container clusters get-credentials hae-cluster-east4 --zone us-east4-a

# Apply Kubernetes manifests
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/evolution-job.yaml

# Stream live generation logs
kubectl logs -n agent-evolution -l app=agent-evolution -f
```

---

## 7. License & Citation

Licensed under the [Apache License, Version 2.0](LICENSE).

```bibtex
@article{chavoshi2026hierarchical,
  title={Bootstrapping Agentic Organizations: Recursive Self-Improvement and Hierarchical Architecture Search on Cloud Kubernetes},
  author={Chavoshi, Sina and Autonomous Agent Systems Research Group},
  journal={arXiv preprint},
  year={2026}
}
```
