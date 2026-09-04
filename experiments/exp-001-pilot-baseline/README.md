# Experiment 001: Pilot Tournament Baseline & Autonomous Headcount Adaptation

## 1. Overview
* **Tournament Scale**: 6 virtual enterprises (Gen 0 seed and Gen 1 evolved).
* **Execution Infrastructure**: Cloud Kubernetes Cluster (`e2-standard-4` worker).
* **Objective**: "5-Year Hyperscale AI Compute Cloud Strategy: Architectural Moats, Silicon Diversification, and Datacenter Topology."
* **Selection Mechanism**: LLM-as-a-Judge 5-Dimensional Strategic Rubric (Strategic Depth, Technical Feasibility, Cross-Functional Coherence, Risk Mitigation, Actionability).
* **Tournament Champion**: **`exp001_elite_2`** (Score: **96.25**, +3.25 pt improvement over seed).

---

## 2. Genomic Artifacts & Telemetry
* [`winning_champion_genome.json`](winning_champion_genome.json): Gen 1 Champion (`exp001_elite_2`, Score: 96.25).
* [`top_5_survivor_genomes.json`](top_5_survivor_genomes.json): Complete genomes for all evaluated pilot firms.
* [`starting_population.json`](starting_population.json): Starting population genomes.
* [`scorecards/`](scorecards/): Full JSON evaluation scorecards.
* [`experiment_report.md`](experiment_report.md): Benchmark report with token and telemetry breakdowns.

---

## 3. Generational Diff from Baseline Seed
* **Baseline Architecture (Gen 0 Seed)**: Fixed 31-agent enterprise consisting of 1 CEO, 5 Department Managers, and 25 Specialists (5 per pod across Strategy, Systems, Software, Product/GTM, Finance/Risk).
* **Genomic Diff Introduced in Gen 1**:
  * **Autonomous Headcount Expansion ($31 \rightarrow 36$ Agents)**: In response to judge critique identifying existential single-source foundry risk, the Meta-Architect injected 5 new operational roles:
    1. *Secondary Silicon Foundry Architect* (Systems Pod)
    2. *Tariff & Export Compliance Specialist* (Finance/Risk Pod)
    3. *Autonomous Fault Recovery Engineer* (Software Pod)
    4. *CoWoS Interposer Packaging Specialist* (Systems Pod)
    5. *Competitive Silicon Intelligence Analyst* (Strategy Pod)
  * **Temperature Differentiation**: Shifted from uniform $\tau = 0.7$ across all agents to hierarchical temperature clustering: Executive ($\tau \approx 0.35$), Systems ($\tau \approx 0.20$), Strategy ($\tau \approx 0.65$).

---

## 4. Organizational Culture & Behavioral Evolution Analysis
* **Consensus $\rightarrow$ Dialectic Scrutiny**: The seed culture was characterized by polite consensus reconciliation, where department managers rarely challenged peer assumptions. In Gen 1, the executive deliberation rules evolved into **adversarial dialectic review**, forcing the Finance/Risk department to stress-test Silicon supply chain dependencies before signing off.
* **Risk Resilience Surge**: Risk Mitigation increased from **80.0 to 98.0 pts** as a direct consequence of the new specialists surfacing geopolitical and supply chain vulnerabilities.
* **Key Finding**: Multi-agent enterprises exhibit organic structural plasticity; under targeted selective pressure, organizational topologies autonomously expand to plug cognitive blind spots.

---

## 5. Reproduction Command
```bash
python3 -m src.main \
  --mode single-firm \
  --config experiments/exp-001-pilot-baseline/winning_champion_genome.json \
  --objective "5-Year Hyperscale AI Compute Cloud Strategy"
```
