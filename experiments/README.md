# Empirical Experimentation Ledger & Architecture Search Archive

This directory serves as the centralized empirical repository for the **Hierarchical Agent Evolution (HAE)** platform. It catalogs tournament runs, evolutionary lineages, multi-generational performance trajectories, and complete genomic snapshots required for exact reproducibility.

---

## 1. Experiment Registry & Snapshot Archive

Each experiment subfolder contains self-contained genomic definitions, tournament scorecards, and reproduction manifests:

| Experiment ID | Focus & Selection Pressure | Infrastructure | Generational Scale | Champion Score | Artifact Directory |
| :--- | :--- | :--- | :---: | :---: | :---: |
| [**`exp-001-pilot-baseline`**](exp-001-pilot-baseline/) | Strategic Architecture & Autonomous Headcount Adaptation | Cloud Kubernetes (`e2-standard-4`) | 6 virtual enterprises (Gen 0 & 1) | **96.25** | [`exp-001-pilot-baseline/`](exp-001-pilot-baseline/) |
| [**`exp-002-parallel-tournament`**](exp-002-parallel-tournament/) | High-Throughput 10-Firm Parallel Tournament with 4-Gate Sandbox | Cloud Kubernetes (5 Parallel Pods) | 10 virtual enterprises (310 agents) | **77.50** | [`exp-002-parallel-tournament/`](exp-002-parallel-tournament/) |
| [**`exp-003-parallel-gen1`**](exp-003-parallel-gen1/) | 3-Way Recombination & Directed Packaging Mutation | Cloud Kubernetes + gVisor Agent Sandbox | 10 virtual enterprises (312 agents) | **76.55** | [`exp-003-parallel-gen1/`](exp-003-parallel-gen1/) |
| [**`exp-004-parallel-gen2`**](exp-004-parallel-gen2/) | Persona Discretization (`backstory_traits`) & Sandbox Convergence | Cloud Kubernetes + gVisor Agent Sandbox | 10 virtual enterprises (314 agents) | **94.50** | [`exp-004-parallel-gen2/`](exp-004-parallel-gen2/) |
| [**`exp-005-parallel-gen3`**](exp-005-parallel-gen3/) | Allelic Consensus Mining & Hermetic Pytest Assertion Rigor | Cloud Kubernetes + gVisor Agent Sandbox | 10 virtual enterprises (318 agents) | **96.75** | [`exp-005-parallel-gen3/`](exp-005-parallel-gen3/) |

---

## 2. Multi-Generational Fitness Progression

### 2.1 Visual Performance Trajectory

The chart below contrasts the unconstrained semantic search trajectory with the grounded deterministic sandbox verification trajectory across 5 evolutionary generations:

![Generational Fitness Trajectory & Sandbox Convergence](assets/fitness_trajectory.png)

```mermaid
graph LR
    subgraph TrackA ["Track A: Unconstrained Semantic Search (Exp 001)"]
        Seed["Gen 0 Baseline Seed<br/>Score: 93.00 (31 Agents)"] --> Firm3["Gen 0 Firm 3 Winner<br/>Score: 94.60"]
        Firm3 --> Elite2["Gen 1 Champion<br/>Score: 96.25 (+3.25 pts)"]
    end

    subgraph TrackB ["Track B: Ground-Truth Deterministic Sandbox (Exp 002 through 005)"]
        ParSeed["Gen 0 Parallel Cohort (Exp 002)<br/>Raw Semantic: ~95-98 pts"]
        Gates{"4 Deterministic Gates<br/>Build | Smoke | OTel | Test"}
        Penalized["Gen 0 Grounded Survivors<br/>Docked -18.75 to -25.0 pts<br/>Top: gen_0_firm_3 (77.50)"]
        Gen1Cohort["Gen 1 Cohort (Exp 003)<br/>Champion: gen_1_elite_2 (76.55)<br/>Docked -18.75 to -25.0"]
        Gen2Cohort["Gen 2 Cohort (Exp 004)<br/>Champion: gen_2_mutant_2 (94.50)<br/>90% Code | 3 Zero-Penalty"]
        Gen3Cohort["Gen 3 Cohort (Exp 005)<br/>Champion: gen_3_consensus_2 (96.75)<br/>100% Code | 4 Zero-Penalty"]

        ParSeed --> Gates
        Gates --> Penalized
        Penalized -->|3-Way Breeding| Gen1Cohort
        Gen1Cohort -->|Persona Discretization| Gen2Cohort
        Gen2Cohort -->|Allelic Consensus Mining| Gen3Cohort
    end
```

---

### 2.2 Detailed Multi-Objective Score Breakdown

$$
\mathcal{F}(\mathcal{C}) = \left[ w_s S + w_t T + w_c C + w_r R + w_a A \right] - \mathcal{P}_{\text{sandbox}}
$$

Where:
* $S, T, C, R, A \in [0, 100]$ represent Strategic Depth ($25\%$), Technical Feasibility ($25\%$), Cross-Functional Coherence ($20\%$), Risk Mitigation ($15\%$), and Actionability ($15\%$).
* $\mathcal{P}_{\text{sandbox}} \in [0, 25.0]$ is the penalty docked by the 4-Gate Deterministic Sandbox Verifier:
  * **Build Gate** ($-6.25$ pts): `pyproject.toml` or `setup.py` packaging manifest exists.
  * **Smoke Gate** ($-6.25$ pts): Module contains $\ge 3$ distinct functional code files.
  * **Telemetry Gate** ($-6.25$ pts): OpenTelemetry spans or metric hooks verified.
  * **Test Gate** ($-6.25$ pts): Test suites execute cleanly under `pytest`.

| Milestone / Architecture | Generation | Strategic (25%) | Technical (25%) | Coherence (20%) | Risk (15%) | Action (15%) | Sandbox Penalty | Overall Fitness | Gates Cleared |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`exp001_seed`** (Baseline) | Gen 0 | 95.0 | 80.0 | 100.0 | 95.0 | 100.0 | $0.00$ | **93.00** | Unanchored Baseline |
| **`exp001_elite_2`** (Exp 001 Top) | Gen 1 | 95.0 | 90.0 | 100.0 | 98.0 | 100.0 | $0.00$ | **96.25** | Unanchored Baseline |
| **`gen_0_firm_3`** (Gen 0 Champ) | Gen 0 | 95.0 | 98.0 | 95.0 | 95.0 | 95.0 | $-18.75$ | **77.50** | OTel Only |
| **`gen_1_elite_2`** (Gen 1 Champ) | Gen 1 | 95.0 | 92.0 | 98.0 | 96.0 | 97.0 | $-18.75$ | **76.55** | OTel Only |
| **`gen_2_mutant_2`** (Gen 2 Champ) | Gen 2 | 95.0 | 93.0 | 96.0 | 94.0 | 95.0 | **$0.00$** | **94.50** | **Build, Smoke, OTel, Tests (10 Files)** |
| **`gen_2_pareto_bonus_3`** (Gen 2 #2) | Gen 2 | 94.0 | 95.0 | 93.0 | 91.0 | 92.0 | **$0.00$** | **92.85** | **Build, Smoke, OTel, Tests (5 Files)** |
| **`gen_3_consensus_2`** (Gen 3 Champ) | Gen 3 | 95.0 | 98.0 | 100.0 | 90.0 | 100.0 | **$0.00$** | **96.75** | **Build, Smoke, OTel, Tests (5 Files - Record)** |
| **`gen_3_pareto_bonus_3`** (Gen 3 #2) | Gen 3 | 95.0 | 95.0 | 100.0 | 90.0 | 100.0 | **$0.00$** | **96.00** | **Build, Smoke, OTel, Tests (5 Files)** |
| **`gen_3_elite_1`** (Gen 3 #3) | Gen 3 | 92.0 | 95.0 | 100.0 | 94.0 | 98.0 | **$0.00$** | **95.55** | **Build, Smoke, OTel, Tests (8 Files)** |
| **`gen_3_consensus_3`** (Gen 3 #4) | Gen 3 | 95.0 | 90.0 | 100.0 | 95.0 | 100.0 | **$0.00$** | **95.50** | **Build, Smoke, OTel, Tests (5 Files)** |
| **`gen_3_elite_2`** (Gen 3 #5) | Gen 3 | 98.0 | 95.0 | 100.0 | 100.0 | 100.0 | $-6.25$ | **92.00** | Build, Smoke, OTel (5 Files) |

---

## 3. Genomic & Structural Mutation Highlights

### 3.1 The Power of Allelic Consensus Mining (Generation 3)
* **Champion Origin (`gen_3_consensus_2`)**: Offspring generated by mining shared traits between `gen_2_mutant_2` (the Gen 2 packaging mutant) and `gen_2_pareto_bonus_2` (the coherence leader).
* By isolating high-frequency operational alleles and crossing them over, the breeding engine reinforced:
  1. Concrete packaging format axioms (`### File: <path>`)
  2. Strict unit test assertions (`tests/test_<module>.py`)
  3. Executive cross-functional coherence directives (100.0/100 score)
* **Result**: An all-time record composite fitness of **96.75 pts** with a 0.00 sandbox penalty.

### 3.2 100% Code Extraction Across the Cohort
* In Generations 0 and 1, 0% of firms emitted extractable code files due to thin monolithic backstories.
* In Generation 2, 90% emitted code.
* In Generation 3, **100% of enterprises (10/10)** successfully authored full multi-file codebases, packages, manifests, and test suites.

### 3.3 Four Flawless Zero-Penalty Passes
* Four separate lineages (`gen_3_consensus_2`, `gen_3_pareto_bonus_3`, `gen_3_elite_1`, `gen_3_consensus_3`) achieved **100% pass rates across all 4 gates** (Build, Smoke, Telemetry, Tests).
* Cohort mean climbed from $71.37$ (Gen 0) $\rightarrow$ $69.60$ (Gen 1) $\rightarrow$ $81.52$ (Gen 2) $\rightarrow$ **$86.47$ pts** (Gen 3).

---

## 4. Total Reproduction Protocol

All experiments are engineered for full deterministic replay:

### Local Replay (Single Enterprise)
```bash
# Replay Experiment 004 Champion
python3 -m src.main \
  --mode single-firm \
  --config experiments/exp-004-parallel-gen2/winning_champion_genome.json \
  --objective "Design and implement the production-ready 'agent-org' platform"

# Replay Experiment 005 Champion (All-Time Record: 96.75)
python3 -m src.main \
  --mode single-firm \
  --config experiments/exp-005-parallel-gen3/winning_champion_genome.json \
  --objective "Design and implement the production-ready 'agent-org' platform"
```

### Distributed Cluster Replay (Full Tournament)
```bash
# Re-run Experiment 004 (Generation 2 tournament)
kubectl apply -f k8s/parallel-indexed-job-gen2-east4.yaml

# Re-run Experiment 005 (Generation 3 tournament)
kubectl apply -f k8s/parallel-indexed-job-gen3-east4.yaml
```
