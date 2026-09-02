# `agent-org`: Open Source Platform Architecture Specification

An open-source, cloud-native platform for designing, executing, and evolving hierarchical virtual agent enterprises.

---

## 1. System Topology & Directory Layout

```
agent-org/
├── pyproject.toml              # Packaging, build dependencies & CLI entrypoints
├── README.md                   # Quickstart, architecture overview, docs
├── agent_org/
│   ├── __init__.py
│   ├── core/
│   │   ├── genome.py           # CompanyGenome, DepartmentGenome, AgentGenome
│   │   ├── schema.py           # EvaluationResult, FitnessScore, LineageTree
│   │   └── exceptions.py
│   ├── runtime/
│   │   ├── company.py          # Federated hierarchical execution engine
│   │   ├── delegation.py       # Inter-departmental protocols (dialectic, sequential)
│   │   └── providers.py        # Vertex AI, OpenAI, Anthropic, local vLLM adapters
│   ├── evolution/
│   │   ├── tournament.py       # Population manager (50 firms per generation)
│   │   ├── selector.py         # Top-K result-driven selection (Top 5 survivors)
│   │   ├── consensus.py        # Group A: Common motif & paradigm extractor
│   │   ├── pareto.py           # Group B: Dimension-extreme amplification
│   │   └── mutator.py          # Group C: Directed LLM meta-mutation
│   ├── sandbox/
│   │   ├── verifier.py         # Build, test, and smoke test runner
│   │   └── container.py        # Isolated ephemeral container runner
│   └── telemetry/
│       ├── tracker.py          # Token & latency accounting
│       ├── otel.py             # OpenTelemetry tracing hooks
│       └── sink.py             # GCS, BigQuery, and local JSONL sinks
├── tests/
│   ├── test_genome.py
│   ├── test_runtime.py
│   ├── test_evolution.py
│   └── test_telemetry.py
└── manifests/
    ├── k8s_job.yaml
    └── k8s_rbac.yaml
```

---

## 2. Core Python Abstractions & API Contracts

### 2.1 The Evolutionary Engine Contract
```python
from agent_org.core import CompanyGenome
from agent_org.evolution import EvolutionaryTournament
from agent_org.sandbox import DeterministicVerifier

# Define tournament parameters
tournament = EvolutionaryTournament(
    objective="Build the agent-org platform with telemetry and verification.",
    population_size=50,
    top_k_survivors=5,
    generations=10,
    verifier=DeterministicVerifier(
        require_clean_build=True,
        min_test_coverage=0.80,
        run_smoke_task=True
    ),
    breeding_split={
        "consensus": 15,       # Group A: Common structural paradigms
        "pareto_extremes": 15, # Group B: Dimensional champions (tech, risk, speed)
        "directed_mutants": 15,# Group C: Hypothesis-driven novel mutations
        "elites": 5            # Preserved top 5 winners
    }
)

champion = tournament.run()
```

### 2.2 Telemetry Anchor Interface
Every agent invocation emits an OpenTelemetry span:
```python
with tracer.start_as_current_span("agent_execution") as span:
    span.set_attribute("agent.role", agent.role)
    span.set_attribute("agent.dept", dept.dept_id)
    span.set_attribute("company.id", company.company_id)
    span.set_attribute("llm.model", model_name)
    span.set_attribute("llm.prompt_tokens", prompt_tokens)
    span.set_attribute("llm.completion_tokens", completion_tokens)
    span.set_attribute("llm.cost_usd", calculated_cost)
```

---

## 3. The Recursive Bootstrapping Fixture

To verify that candidate platforms can build themselves, the platform comes with a standard **Self-Hosting Test Fixture**:

1. **Target Task**: The 50 firms are given the `agent-org` Target Spec and asked to write the source code into a directory `generated_platform/`.
2. **Deterministic Sandbox Test**:
   * Runs `pip install -e generated_platform/` inside a fresh ephemeral sandbox container.
   * Runs `pytest generated_platform/tests/`.
   * Runs `python -m generated_platform.cli run --objective "Test task"` with a miniature 3-agent hierarchy.
   * Verifies that `generated_platform` emitted valid OpenTelemetry spans to a local collector.
3. **Bootstrapping Transition**:
   * The candidate with the highest test coverage, lowest latency, and best architectural score is tagged as `agent-org:v{generation}`.
   * The orchestrator replaces its own runtime binary with `agent-org:v{generation}` and launches the next generation of evolution!
