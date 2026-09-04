# Experiment 001: Pilot Baseline Tournament

## 1. Overview
* **Tournament Scale**: 6 virtual enterprises (3 in Generation 0, 3 in Generation 1).
* **Headcount**: 31 agents baseline, expanding to 36 agents in mutated lineages.
* **Objective**: "5-Year Hyperscale AI Compute Cloud Strategy (100k+ custom accelerators, power/cooling, interconnect, financing, and competitive defense)".
* **Key Finding**: Peak fitness progressed from $94.60$ (Gen 0) to **$96.25$** (Gen 1). Mutator autonomously expanded headcount to address critical single-source dependencies.

## 2. Genomic Artifacts
* [`seed_genome.json`](seed_genome.json): The unmutated 31-agent enterprise genome template.
* [`winning_champion_genome.json`](winning_champion_genome.json): Tournament Champion (`gen_1_elite_2`, Score: 96.25).
* [`mutant_genome.json`](mutant_genome.json): Headcount-adapted architecture (`gen_1_mutant_1`, 36 agents, Score: 94.00).
* [`experiment_report.md`](experiment_report.md): Full empirical benchmark report with telemetry and token breakdowns.

## 3. Reproduction Command
To re-evaluate the winning champion locally:
```bash
python3 -m src.main \
  --mode single-firm \
  --config experiments/exp-001-pilot-baseline/winning_champion_genome.json \
  --objective "5-Year Hyperscale AI Compute Cloud Strategy"
```
