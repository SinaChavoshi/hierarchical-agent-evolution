# V8 (puct) rerun on current code, 2026-10-08

Like-for-like control for the V9 AB-MCTS run: the V8 smoke genome
(`configs/genomes/v8_smoke_swebench_firm_0.json`, `search_algorithm = "puct"`) on
`marshmallow-code__marshmallow-1810`, same code (`25a94ed`), model, pod, budget and grader
as `../v9_ab_mcts_2026-10-08`. The original smoke run (`../smoke_2026-10-08`) predates the
organisation wiring and the module-rewrite cap and is not comparable on time or tokens.

Outcome: 21 moves (iteration 1 `exhausted` at 19, iteration 2 `exhausted` at 2), 7.6 min,
89,271 tokens, 4,112-char patch. Graded **not resolved**: the second kept synthesis
(`Schema.__init__`, move 19) makes every schema raise, `tests/conftest.py` fails to import
and pytest exits 4 before collecting anything. The module gate passed that write because it
treated pytest exit 4 as inconclusive; that is fixed in the commit that adds this README.
The first kept synthesis (`Field.root`, move 12) is a correct fix: applied alone it grades
resolved (F2P 1/1, P2P 111/111, `grade_fields_hunk_only.json`) and passes the repository's
whole suite (1073/1073).

Files: `prediction.json`, `record.json`, `grade.json`, `grade_fields_hunk_only.json`
(counterfactual, `fields.py` hunk only), `firm.log`, `stdout.log`, `started_at.txt`,
`finished_at.txt`. Analysis and the comparison with V9 are in
`../v9_ab_mcts_2026-10-08/README.md`.
