# Experiment 006: Parallel Generation 4 Tournament & Model Unit Economics

## 1. Overview
* **Tournament Scale**: 10 evolved virtual enterprises (320 agents total).
* **Execution Infrastructure**: Cloud Kubernetes Cluster (5 concurrent worker pods, Indexed Job) with gVisor / GKE Agent Sandbox.
* **Lineages Evaluated**:
  * **Elites (2 firms)**: `gen_4_elite_1` (from all-time champion `gen_3_consensus_2`), `gen_4_elite_2` (from `gen_3_pareto_bonus_3`).
  * **Consensus Offspring (3 firms)**: `gen_4_consensus_1..3` (Ultra-lean 1-Pro + 31-Flash token economy).
  * **Pareto Extremes (3 firms)**: `gen_4_pareto_bonus_1..3` (Systems & Coherence frontier push).
  * **Directed Mutants (2 firms)**: `gen_4_mutant_1..2` (Selective Pro promotion for Systems Lead + strict token OpEx invariants).
* **Selection Mechanism**: Multi-Objective Strategic Rubric + 4-Gate Sandbox Verifier + **OpEx Target Budget Envelope ($0.45 USD)**.

---

## 2. Genomic Artifacts & Scorecards
* [`starting_population.json`](starting_population.json): Complete 10-firm starting population with model tier pricing annotations.
* [`scorecards/`](scorecards/): Output directory for tournament scorecards.

---

## 3. Generational Diff from Generation 3 (Exp 005)
* **Model Tier Unit Economics**:
  * Differentiated between **Gemini 2.5 Flash** ($0.075 / 1M in, $0.30 / 1M out) and **Gemini 2.5 Pro** ($1.25 / 1M in, $5.00 / 1M out).
  * Introduced diverse organizational allocations: testing whether a lean 1-Pro + 31-Flash architecture can out-compete a 6-Pro + 26-Flash executive-heavy structure on a risk-adjusted Net Fitness basis.
* **The OpEx Budget Envelope & Capital Efficiency Margin**:
  * Set a target token operating budget of **$0.45 USD** per enterprise.
  * Over-budget enterprises incur a progressive operating penalty ($\mathcal{P}_{\text{OpEx}}$ up to -15.0 pts).
  * Lean enterprises passing all 4 sandbox gates under budget receive an EBITDA Capital Efficiency Margin ($\mathcal{B}_{\text{efficiency}}$ up to +3.0 pts).
* **Corporate Asset Schema Foundation**:
  * Added `CorporateAsset` schema to `src/schema.py` in preparation for Generation 5's IP marketplace.

---

## 4. Organizational Culture & Behavioral Evolution Analysis
* **Transition to Capital-Conscious Engineering**:
  * Prior generations treated token compute as infinite and free, causing agents to write repetitive boilerplate.
  * Generation 4 introduces cost awareness into the organizational genome, forcing managers to optimize prompt density and eliminate token-bloated discussions while preserving hermetic code and test deliverables.
* **The "Fire Everyone" Prevention Safeguard**:
  * While firms are incentivized to minimize token OpEx, the Deterministic 4-Gate Sandbox ensures that aggressive downsizing or removing engineering pods causes immediate failure (-25.0 pt dock), enforcing genuine productivity.

---

## 5. Reproduction Command
To launch the Generation 4 tournament on Kubernetes:
```bash
kubectl apply -f k8s/parallel-indexed-job-gen4-east4.yaml
```
