# Hierarchical Agent Evolution (HAE)
### A Cloud-Native Platform for Recursively Self-Improving Agentic Workforces via Genetic Architecture Search

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Infrastructure: Kubernetes](https://img.shields.io/badge/Infrastructure-Cloud%20Kubernetes-326ce5.svg)](https://kubernetes.io)
[![Runtime: gVisor / Agent Sandbox](https://img.shields.io/badge/Security-gVisor%20Kernel%20Isolation-green.svg)](https://gvisor.dev)
[![LLM: Gemini 2.5 Flash & Pro](https://img.shields.io/badge/Vertex%20AI-Gemini%202.5-orange.svg)](https://cloud.google.com/vertex-ai)

---

## 1. Vision & Research Premise

Modern LLM-based multi-agent systems (e.g., CrewAI, AutoGen, MetaGPT) typically rely on flat communication graphs or static role topologies. When scaled beyond 10 agents, these flat structures suffer from severe context dilution, quadratic token communication overhead $\mathcal{O}(N^2)$, and reasoning collapse. Crucially, organizational hierarchies, persona backstories, and delegation protocols are conventionally hand-crafted through trial-and-error human prompt engineering.

**Hierarchical Agent Evolution (HAE)** is an open, cloud-native meta-platform designed to facilitate the creation of **recursively self-improving agentic workforces**. HAE models enterprises as federated hierarchies (30–50 specialized agents partitioned into operational departmental pods under an executive steering council) and optimizes their organizational topology, cognitive backstories, and delegation protocols via genetic programming.

Rather than relying on human prompt engineering, HAE investigates **recursive self-hosting**: competing virtual enterprises are tasked with designing, implementing, and verifying the next-generation engine of the platform itself—establishing an unattended evolutionary loop where software agents autonomously evolve their own organizational architecture.

```mermaid
graph TD
    subgraph SelfImprovementLoop ["The Recursive Evolutionary Cycle"]
        Genome["1. Enterprise Genome<br/>(Topology, Roles, Prompts, Delegation Rules)"]
        Execution["2. Distributed Execution Runtime<br/>(31–50 Agent Deliberation on Kubernetes)"]
        Sandbox["3. Real-World Execution in Sandbox<br/>(Agent Sandbox / gVisor: Builds, Pytest, Benchmarks)"]
        Fitness["4. Multi-Objective Fitness Evaluation<br/>(Deterministic Gates + Rubric Judging)"]
        Breeding["5. 3-Way Genetic Selection & Mutation<br/>(Consensus, Pareto Extremes, Directed Mutants)"]

        Genome --> Execution
        Execution --> Sandbox
        Sandbox --> Fitness
        Fitness --> Breeding
        Breeding -->|Offspring Genomes| Genome
    end
```

---

## 2. The Four Architectural Pillars

### Pillar 1: The Organism — The Enterprise Genome
In HAE, an entire virtual organization is treated as a living, evolvable genome (`CompanyGenome`):
* **Topology as Code**: An enterprise's organizational chart, executive council, departmental pods, team sizes, and communication channels are codified declaratively.
* **Structural Plasticity**: The headcount, role specialization (e.g., injecting an SRE Chaos Engineer or Packaging Specialist), and temperature distributions are fully mutable between generations.

### Pillar 2: The Selection Pressure — Hard Deterministic Gates
Autonomous self-improvement cannot exist on subjective LLM evaluations alone; unconstrained models inevitably drift into verbose, non-executable hallucination. HAE couples multi-dimensional LLM-as-a-Judge rubrics with **ground-truth deterministic execution gates**:
* Emitted code must physically install (`pip install -e .`).
* Generated test suites must execute and pass under `pytest`.
* Observability anchors (OpenTelemetry spans and metrics) must be verified.
* Non-executable submissions are heavily penalized, driving hard evolutionary selection toward working software.

### Pillar 3: The Evolutionary Search — 3-Way Breeding Engine
To navigate the high-dimensional space of organizational charts and agent backstories without premature convergence, HAE executes a 3-way balanced reproduction strategy:
1. **Consensus Exploitation (Group A)**: Identifies shared structural invariants across top performers (e.g., common dialectic review rules or strict specialist temperatures) and reinforces them.
2. **Pareto Frontier Amplification (Group B)**: Clones and amplifies dimension-specific champions (e.g., Extreme Technical Rigor, Extreme Adversarial Risk Resilience).
3. **Directed Meta-Architect Mutations (Group C)**: A high-reasoning Meta-Architect analyzes collective post-mortem failure modes and formulates targeted structural hypotheses (e.g., adding a dedicated build verification agent).
4. **Elites**: Preserves top-ranking champions unaltered across generational transitions.

### Pillar 4: Recursive Self-Hosting (The Closed Loop)
The overarching objective of HAE is recursive bootstrapping:
* In Generation 0, human-seeded baseline enterprises generate architectural blueprints.
* In Generation 1, evolved enterprises emit structured code packages, CLI tools, and test suites.
* In subsequent generations, the evolved agent workforces design, patch, and optimize the mutation operators, schedulers, and execution sandboxes governing their own evolution.

---

## 3. Technical Deep Dive

### 3.1 Project Architecture & Directory Layout

```
hierarchical-agent-evolution/
├── pyproject.toml              # Packaging & dependencies
├── Dockerfile                  # Container definition for Kubernetes execution
├── cloudbuild.yaml             # Container image build automation
├── configs/                    # Generational population archives
│   └── generation_1_population.json # Evolved Gen 1 population genomes
├── experiments/                # Empirical experiment ledger & benchmarks
│   ├── README.md               # Benchmark registry and artifact schema
│   ├── pilot_tournament_baseline.md # Baseline pilot tournament report
│   └── parallel_tournament_distributed.md # High-throughput parallel tournament report
├── research/
│   ├── WHITE_PAPER_DRAFT.md    # Formal scientific paper manuscript
│   └── RESEARCH_LOGBOOK.md     # Empirical research ledger
├── src/
│   ├── schema.py               # Genome schemas (Company, Department, Agent, Fitness)
│   ├── company.py              # Federated hierarchical execution runner
│   ├── evaluator.py            # LLM-as-a-Judge multi-dimensional rubric
│   ├── mutator.py              # Genetic mutator & crossover operators
│   ├── breeding.py             # 3-Way breeding engine (Consensus, Pareto, Directed)
│   ├── sandbox_verifier.py     # Deterministic 4-gate sandbox verification engine
│   ├── telemetry.py            # OpenTelemetry instrumentation & token accounting
│   ├── engine.py               # Tournament controller & generational orchestration
│   ├── worker.py               # Distributed indexed worker entrypoint
│   ├── llm_factory.py          # Vertex AI REST client with retry backoff & secret auth
│   └── main.py                 # Platform CLI entrypoint
├── k8s/
│   ├── evolution-job.yaml      # Single-pod tournament job manifest
│   ├── parallel-indexed-job-east4.yaml # Parallel indexed job (5 concurrent pods)
│   ├── parallel-indexed-job-gen1-east4.yaml # Generation 1 parallel indexed job
│   └── rbac.yaml               # Kubernetes ServiceAccount and RBAC bindings
├── templates/
│   └── default_company.json    # Generation 0 seed enterprise genome
└── tests/
    └── test_evolutionary_pipeline.py # Unit and integration test suite
```

---

### 3.2 The Enterprise Genome Specification

The organization is codified in three hierarchical layers implemented via Pydantic in [`src/schema.py`](src/schema.py):

```
CompanyGenome (Enterprise Level)
 ├── CEO: AgentGenome
 ├── Executive Deliberation Rules: str
 └── Departments: List[DepartmentGenome]
      ├── Manager: AgentGenome
      ├── Departmental Mandate & Delegation Rules: str
      └── Agents: List[AgentGenome] (Domain Specialists)
```

#### Annotated Genome Schema (`CompanyGenome`):
```json
{
  "company_id": "gen_1_mutant_1",
  "generation": 1,
  "parent_ids": ["gen_0_firm_3"],
  "mutation_history": [
    "Parallel Generation 0 Variant 3",
    "Hypothesis: Dedicated Packaging Specialist emits executable pyproject.toml & pytest suite"
  ],
  "ceo": {
    "role": "Chief Executive Officer",
    "goal": "Unify cross-departmental capabilities into an unassailable strategic execution roadmap.",
    "backstory": "Veteran technology executive known for first-principles thinking and demanding quantifiable evidence.",
    "temperature": 0.43,
    "model_tier": "executive",
    "system_instructions": "Challenge every assumption. Ensure total alignment between engineering and execution."
  },
  "executive_deliberation_rules": "Dialectical debate: actively pit engineering constraints against product ambition, force finance to stress-test unit economics, and mandate red-team mitigation before sign-off.",
  "departments": [
    {
      "dept_id": "dept_systems_eng",
      "name": "Core Systems & Infrastructure Engineering",
      "mandate": "Architect distributed compute, memory management, and deterministic execution runtime.",
      "delegation_rules": "Rigorous peer review; every specification must include operational constraints.",
      "manager": {
        "role": "VP of Core Systems Engineering",
        "goal": "Deliver robust, production-ready system architecture.",
        "backstory": "Former principal distributed systems architect.",
        "temperature": 0.3,
        "model_tier": "executive"
      },
      "agents": [
        {
          "role": "Distributed Systems Architect",
          "goal": "Design cluster orchestration and consensus mechanisms.",
          "backstory": "Expert in high-throughput distributed systems.",
          "temperature": 0.3,
          "model_tier": "worker"
        },
        {
          "role": "Python Packaging & Test Automation Engineer",
          "goal": "Generate complete, valid pyproject.toml, pytest test suites, and package layout.",
          "backstory": "Staff DevOps & Build Engineer passionate about executable Python packages.",
          "temperature": 0.2,
          "model_tier": "worker"
        }
      ]
    }
  ]
}
```

---

### 3.3 Inter-Generational Genome Lifecycle & Storage Management

The evolutionary lifecycle follows a strictly versioned and distributed management pipeline:

```
[Generation g Population File] ──► [Kubernetes Batch Indexed Job] ──► [Parallel Worker Pods (0..N-1)]
                                                                               │
                                                                               ▼
                                                                     [Local Disk & Cloud Storage]
                                                                               │
[Generation g+1 Population File] ◄── [3-Way Breeding Engine] ◄── [Aggregated Survivor Scorecards]
```

1. **Population Serialization**:
   Each generation's population is stored as a JSON array of `CompanyGenome` definitions in `configs/generation_{g}_population.json`.
2. **Distributed Job Indexing**:
   When launching a tournament on Kubernetes, a Batch Indexed Job is dispatched. The container runtime injects `$JOB_COMPLETION_INDEX` into each worker pod. Worker $k$ loads genome index $k$ directly from the population file:
   ```python
   firm_genome = CompanyGenome(**population[firm_index])
   ```
3. **Scorecard & Artifact Persistence**:
   Upon completing execution, each worker stores its full scorecard and generated code package locally at `/data/outputs/generation_{g}/{company_id}_result.json` and syncs to cloud object storage:
   ```
   gs://<STORAGE_BUCKET>/parallel_runs/generation_{g}/{company_id}_result.json
   ```
4. **Breeding Transition ($g \rightarrow g+1$)**:
   The tournament engine aggregates all generation scorecards, ranks enterprises by overall fitness, selects the top $K=5$ survivors, and executes the 3-way breeding pipeline to serialize `configs/generation_{g+1}_population.json`.

---

### 3.4 Ground-Truth Sandbox Execution & Kubernetes Agent Sandbox

To safely execute untrusted code synthesized by competing agent workforces, HAE integrates with the **Kubernetes Agent Sandbox** (`kubernetes-sigs/agent-sandbox`):

* **Kernel-Level Isolation**: Sandboxes execute inside **gVisor** (`runtimeClassName: gvisor`), enforcing strict system call filtering and network isolation.
* **Sub-Second Warm Pools**: Pre-initialized sandbox pods provide sub-second (`<1s`) warm container provisioning, eliminating multi-minute Kubernetes scheduling overhead during high-volume test evaluation.
* **Deterministic Fitness Formulation**:

$$
\mathcal{F}(\mathcal{C}) = \left[ w_s S(\mathcal{C}) + w_t T(\mathcal{C}) + w_c C(\mathcal{C}) + w_r R(\mathcal{C}) + w_a A(\mathcal{C}) \right] - \mathcal{P}_{\text{sandbox}}
$$

Where:
* $S, T, C, R, A$ are rubric scores (0–100) for Strategic Depth, Technical Feasibility, Cross-Functional Coherence, Risk Mitigation, and Actionability.
* $\mathcal{P}_{\text{sandbox}} \in [0, 25.0]$ is the penalty docked by the 4-Gate Deterministic Sandbox Verifier:

| Gate | Verification Target | Penalty if Failed |
| :--- | :--- | :---: |
| **Build Gate** | Package metadata (`pyproject.toml` or `setup.py`) and runtime entrypoint exist | -6.25 pts |
| **Smoke Gate** | Package contains $\ge 3$ distinct functional code modules | -6.25 pts |
| **Telemetry Gate** | OpenTelemetry spans/metrics or distributed trace hooks are present | -6.25 pts |
| **Test Gate** | Test suites (`test_*.py`) exist and execute cleanly under `pytest` | -6.25 pts |

---

## 4. Empirical Benchmark Highlights

Empirical validation across tournament iterations demonstrated measurable evolutionary ascent and autonomous self-repair:

| Experiment | Infrastructure | Key Scientific Findings | Benchmark Report |
| :--- | :--- | :--- | :---: |
| **`exp-001-baseline`** | Cloud Kubernetes (Single Node Pool) | Baseline progression ($93.00 \rightarrow 96.25$); Autonomous headcount expansion ($31 \rightarrow 36$ agents) to resolve tape-out bottlenecks; Total cost ~$0.22 USD. | [Full Report](experiments/pilot_tournament_baseline.md) |
| **`exp-002-parallel`** | Cloud Kubernetes (5 Parallel Pods) | 10 enterprises (310 agents) evaluated in **22 minutes** (**4.1x speedup**); OpenTelemetry selection pressure rewarded top firms ($77.50$ vs $71.25$). | [Full Report](experiments/parallel_tournament_distributed.md) |
| **`exp-003-sandbox`** | Kubernetes Agent Sandbox + gVisor | Sub-second warm-pool `pytest` dynamic execution of synthesized Python code deliverables. | *In Progress* |

---

## 5. Getting Started

### Local Quickstart
```bash
# Clone repository
git clone https://github.com/SinaChavoshi/hierarchical-agent-evolution.git
cd hierarchical-agent-evolution

# Install dependencies in editable mode
pip install -e .

# Run unit and integration tests
PYTHONPATH=. pytest tests/

# Execute a single firm evaluation locally
python3 -m src.main --mode single-firm --objective "Build next-generation telemetry engine"
```

### Distributed Kubernetes Deployment
To execute high-throughput parallel evolutionary tournaments:

```bash
# 1. Apply Kubernetes ServiceAccount and RBAC roles
kubectl apply -f k8s/rbac.yaml

# 2. Mount Vertex AI authentication secret
kubectl create secret generic vertex-token \
  --from-literal=token="$(gcloud auth application-default print-access-token)" \
  --namespace=agent-evolution

# 3. Dispatch the parallel indexed job (5 concurrent enterprise pods)
kubectl apply -f k8s/parallel-indexed-job-gen1-east4.yaml

# 4. Stream live multi-agent deliberations
kubectl logs -n agent-evolution -l app=parallel-firms-gen1-east4 --tail=50 -f
```

---

## 6. License & Citation

Licensed under the [Apache License, Version 2.0](LICENSE).

```bibtex
@article{chavoshi2026hierarchical,
  title={Bootstrapping Agentic Organizations: Recursive Self-Improvement and Hierarchical Architecture Search on Cloud Kubernetes},
  author={Chavoshi, Sina and Autonomous Agent Systems Research Group},
  journal={arXiv preprint},
  year={2026}
}
```
