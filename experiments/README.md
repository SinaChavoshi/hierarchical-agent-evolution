# Experiments

This directory holds the design notes, results and errata of each phase of
the Hierarchical Agent Evolution program. Each phase changed one thing about
how firms are scored, what they are asked to build, or how a firm runs, and
each sub-directory is the record of that phase: what was tried, the numbers,
and what turned out to be wrong with them.

![V2 and V3 held-out test trajectory](v2/assets/v2_v3_evolutionary_trajectory.png)

## Phase index

| Phase | Generations (firm-runs) | What was tested | Outcome | Status | Notes |
|---|---|---|---|---|---|
| V1 | pilot to 10 (13 runs, about 100 firms) | Evolving 31-34-agent hierarchies on open-ended prompts, graded 95% by an LLM judge plus four regex "sandbox" gates | Judged fitness climbed from 25.25 to 96.75. Re-executing the artefacts afterwards gave corr(generation, execution) = +0.045 and 0 of 60 firms in Gen 5-10 passing all five execution gates. | closed, audited | [v1/README.md](v1/README.md), [execution_grounded_correction.md](v1/execution_grounded_correction.md) |
| V2 | 1 to 6 (60 firms, $28.18) | Held-out unit tests as fitness (`artifacts.py`, 7 tests under `unshare -rn`), zero-seed workspaces; single pass in Gen 1-3, a 10-iteration test-and-repair loop with a monotonic verification guard in Gen 4-6 | Gen 1-3: elite fitness up 14.56 points, 0 of 30 firms at 7/7. Gen 4-6: 30 of 30 at 7/7, peak net fitness 98.81, mean iterations 2.20 to 1.30, spend $7.26 to $4.87 per generation. | complete | [v2/README.md](v2/README.md), [ledger.json](v2/ledger.json), [ledger_iter10.json](v2/ledger_iter10.json) |
| V3 | 7 to 9 (30 firms, $18.92) | Firms re-implement HAE's own breeding engine; the breeder compiles and runs the winning overlays to breed the next generation | Gen 7 (zero-seed): 10 of 10 at 7/7, peak 96.83. Gen 8 and 9 were bred with evolved overlays (peaks 97.92 and 94.89). The audit found a `worker.py` filter bug that let Gen 8-9 inherit workspaces (fixed in 71a7da4) and about 56 KB per run of inter-agent memo text. | complete | [v3_rsi/README.md](v3_rsi/README.md), [CAMPAIGN_STATUS.md](v3_rsi/CAMPAIGN_STATUS.md), [overlays/](v3_rsi/overlays/) |
| V4 | 10 to 11 | True zero-seed runs after the fix; three Qwen3.8-Flash-Next NVFP4 (180B) replicas on g4-standard-96 nodes behind one `llm-d` gateway (prefix-affinity routing, request coalescing, KV-cache offload); soft token-efficiency scoring | Gen 10: 6 of 10 at 7/7 (peak 78.92). Gen 11: 10 of 10 at 7/7 on the first iteration (peak 95.29, mean 91.08), with the caveat that identical builder prompts were coalesced across firms. Gateway: 0.74 ms short-circuit, 37-73% of requests served without GPU work, 455/455 prefix-affinity hits. | complete | [v4_llmd/README.md](v4_llmd/README.md) |
| V5 | 12 to 15 | Inter-agent packets as JSON schemas enforced by vLLM's grammar-constrained decoder (xgrammar); the 4-module `full_stack_hae` benchmark with 50 held-out tests and no cross-firm caching of synthesis | Output tokens per call 1,420 to 115 (-91.9%). Gen 13: 3 of 10 at 40-42/50; Gen 14: 9 of 10 at 39-44/50; Gen 15: 10 of 10 at 46-50/50, two at 50/50. Gen 12's 10/10 and 97.42 are retracted (a pre-seeded cache entry, 6818d8a). Gen 15 also showed the plateau that motivated V6: 8 of 8 non-converged firms stuck after iteration 2, 7 of them on the same test. | complete | [v5_typesafe_xgrammar/README.md](v5_typesafe_xgrammar/README.md) |
| V6 | 16 (pilot + 3 cohort runs of 26) | Repair iterations as an explicit search: an epistemic ledger, falsifiable hypotheses with sandbox probes, a non-LLM gatekeeper that alone may update beliefs, PUCT with a low-prior quota, and fitness weighted 60/25/15 over held-out tests, ledger audit and search efficiency | Pilot: one plateaued genome reached 50/50 through a 0.10-prior hypothesis; five engine defects found and fixed. Cohorts: catastrophic first passes recover once a missing module can be authored (6 of 9 in run 2 vs 1 of 7 in run 1); plateau starts barely move; the proposer's priors are uninformative (flat hit rate across prior bins, Brier worse than the base rate). | runs complete, analysis ongoing | [v6_epistemic_search/README.md](v6_epistemic_search/README.md), [results](../results/hae_gen16_v6_cohort/README.md) |
| V7 | - | Logistic value and prior heads trained on the 48 V6 cohort trees, evaluated out of fold by firm | State-value head: Brier 0.186 vs 0.202 for the base rate. Hypothesis-prior head: no better than the base rate (Brier 0.0863 vs 0.0862). Shipped in shadow mode (logged, no decision changed). | measured | [results/v7_value_heads](../results/v7_value_heads/README.md) |
| V8 | - | Role library and CEO-policy genes, turn-0 team sizing, a `recruit_specialist` move with per-role credit, evidence-conditioned LLM role revision (`role_mutation.py`), and SWE-bench adapters with a container execution boundary | Implemented and unit-tested (133 tests) plus initial `kubectl` smoke run; 50-task dev-slice baseline next. | in progress | [docs/v8_role_evolution_and_swebench_roadmap.md](../docs/v8_role_evolution_and_swebench_roadmap.md), [docs/v8/](../docs/v8/) |
| V9 | - | Adaptive Branching MCTS ([AB-MCTS, arXiv:2503.04412](https://arxiv.org/abs/2503.04412)): Thompson sampling over Beta arms (test a hypothesis, synthesise from one, ask a role for one new hypothesis, ask the CEO for a specialist) replaces the fixed-width PUCT waterfall and the coin-flip recruit | Implemented behind `epistemic_policy.search_algorithm` with the default `puct` pinned byte-identical to V8 by a golden fixture; 29 tests cover the width-1 fast path, widening past the V8 round cap, decay-driven recruitment and probe repair. One live instance (`marshmallow-1810`): resolved, 24 moves, 10 min, with a `puct` rerun on the same code as control. The run exposed a gate defect (pytest start-up failures passed as inconclusive) and a V9 prompt defect (refused probes could not be repaired); both fixed. | implemented, one live instance | [docs/v9/ab_mcts.md](../docs/v9/ab_mcts.md), [results](../results/swebench/v9_ab_mcts_2026-10-08/README.md) |
| V10 | - | Full held-out benchmark evaluation across all 500 `SWE-bench Verified` tasks (`Kimi K3` backend) | Planned: evaluate frozen V9 champion genome once via the untouched upstream `swebench` harness against Moonshot AI's published `Kimi K3` baseline. | roadmap | [docs/v8_role_evolution_and_swebench_roadmap.md](../docs/v8_role_evolution_and_swebench_roadmap.md) |

## Reading order

If you are new to the project: V1 explains why fitness is measured by
execution and nothing else; V2 is the benchmark that every later phase uses
in some form; V5 and V6 describe the runtime as it is today. V3 and V4 are
infrastructure phases (self-modification, serving) whose lessons are folded
into the current code but whose numbers are not comparable with later ones.

Across phases the fitness function changed several times (prose judge, 7-test
suite, 50-test suite, audit-weighted composite), so net fitness numbers are
only comparable within a phase. Held-out test counts are the stable measure
from V2 onwards.
