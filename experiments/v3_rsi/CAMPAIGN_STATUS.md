# V3 Campaign Status (Generations 7-9)

Status: complete. 3 of 3 generations, 30 of 30 firms, $18.92 total spend. Target module in every generation: `hae/genome/morphogenesis.py`, verified against 7 held-out tests. Full write-up, overlay mechanism and audit: [`README.md`](README.md).

| Generation | Breeding engine used | Target pre-seeded (`inherited_files`) | Firms | Best net fitness | Best firm | Mean net | Passed 7/7 | Solved on iteration 1 | Mean iterations | Spend |
| :---: | :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| 7 | kernel code (Gen 6 champions) | 0 / 10 | 10/10 | **96.83** | `gen_7_pareto_1` | 71.77 | 10/10 | 60% (6/10) | 1.5 | $6.6945 |
| 8 | evolved overlays from Gen 7 | 10 / 10 (confounded) | 10/10 | **97.92** | `gen_8_pareto_2` | 74.52 | 10/10 | 80% (confounded) | 1.3 | $6.3380 |
| 9 | evolved overlays from Gen 8 | 10 / 10 (confounded) | 10/10 | **94.89** | `gen_9_crossover_1` | 72.71 | 10/10 | 100% (confounded) | 1.0 | $5.8843 |

Audit note. Generation 7 was a genuine zero-seed synthesis (no firm had `morphogenesis.py` on disk). In Generations 8 and 9 a `target_module` lookup bug in `hae/orchestration/worker.py` left each parent's 100%-passing `morphogenesis.py` in the child workspace, so the iteration-1 rates and mean iteration counts for those generations measure overlay preservation, not synthesis. The bug was fixed in commit `71a7da4`. The `Breeder` did execute the Generation 7 and 8 overlays to breed Generations 8 and 9. Details, root cause and the transcript evidence are in the [README audit section](README.md#post-run-audit).

The summary rows are produced from the per-firm scorecards by `scripts/run_level3_rsi_campaign.py`, which also writes [`ledger_level3_rsi.json`](ledger_level3_rsi.json).
