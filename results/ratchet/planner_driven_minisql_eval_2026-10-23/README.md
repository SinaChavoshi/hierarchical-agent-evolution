# Live Planner-Driven `L0-Macro` Ratchet Build & Held-Out External SQLite Oracle Evaluation

- **Date**: `2026-10-23`
- **Live Endpoint**: `http://127.0.0.1:8000/v1`
- **Model**: `reflection/Beam-1.0-501B-A23B-NVFP4` (`+ DFlash2` speculative decoding on GKE)
- **Planner Mode**: **Automatic `L0MacroPlanner.plan()` from high-level system specification (`dag=None`)**
- **Planner LLM Calls / Tokens**: `1` call(s) / `5,317` tokens
- **Planned & Certified Milestones**: `True` (`7/7`: `['m1_lexer_and_ast', 'm2_sql_parser', 'm3_storage_and_expr', 'm4_query_execution', 'm5_transactions_and_facade', 'm6_external_recovery_1', 'm7_external_recovery_2']`)
- **Held-Out External Test Suite (`test_minisql_external_oracle.py`, SQLite3 differential oracle)**: initial=`9 passed` (`ok=False`), final=`15 passed` (`ok=True`), recovery_milestones=`['m6_external_recovery_1', 'm7_external_recovery_2']`
- **Total Wall-Clock Time**: `738.54s`
- **Total LLM Calls / Tokens (Planner + Micro)**: `171` calls / `1,801,303` tokens (`prompt=1,749,973`, `output=51,330`)
- **Final Ratcheted `HEAD` SHA**: `07740fb54a923db08c775369ac40de3bfe23ddd3`

## Per-Milestone Ratchet & Telemetry Summary

| Milestone (Auto-Planned by `L0MacroPlanner`) | Status | Moves | LLM Calls | Total Tokens | Wall Time (s) | Active Delta (`+LoC` vs `HEAD_{k-1}`) | Total Repo LoC | Cumulative Regression Gate (`M_1..M_k`) | Certified Commit SHA | Git Tag |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `m1_lexer_and_ast` | **CERTIFIED** | 19 | 19 | 124,666 | 61.01s | `+388` lines (`< 850`) | `395` lines | `m1_lexer_and_ast` (**5 passed**) | `e02bb6b51018` | `hae-milestone-m1_lexer_and_ast` |
| `m2_sql_parser` | **CERTIFIED** | 29 | 29 | 285,169 | 129.71s | `+618` lines (`< 850`) | `1013` lines | `m1_lexer_and_ast+m2_sql_parser` (**11 passed**) | `3976fdcae0da` | `hae-milestone-m2_sql_parser` |
| `m3_storage_and_expr` | **CERTIFIED** | 15 | 15 | 144,371 | 88.68s | `+530` lines (`< 850`) | `1543` lines | `m1_lexer_and_ast+m2_sql_parser+m3_storage_and_expr` (**16 passed**) | `309e63fa6d13` | `hae-milestone-m3_storage_and_expr` |
| `m4_query_execution` | **CERTIFIED** | 36 | 36 | 458,475 | 169.19s | `+388` lines (`< 850`) | `1930` lines | `m1_lexer_and_ast+m2_sql_parser+m3_storage_and_expr+m4_query_execution` (**21 passed**) | `f996bfd24287` | `hae-milestone-m4_query_execution` |
| `m5_transactions_and_facade` | **CERTIFIED** | 14 | 14 | 130,598 | 72.05s | `+267` lines (`< 850`) | `2162` lines | `m1_lexer_and_ast+m2_sql_parser+m3_storage_and_expr+m4_query_execution+m5_transactions_and_facade` (**26 passed**) | `52bc3bc2573f` | `hae-milestone-m5_transactions_and_facade` |
| `m6_external_recovery_1` | **CERTIFIED** | 20 | 20 | 207,970 | 69.98s | `+44` lines (`< 850`) | `2203` lines | `m1_lexer_and_ast+m2_sql_parser+m3_storage_and_expr+m4_query_execution+m5_transactions_and_facade+m6_external_recovery_1` (**30 passed**) | `dcaffdd1a88f` | `hae-milestone-m6_external_recovery_1` |
| `m7_external_recovery_2` | **CERTIFIED** | 37 | 37 | 444,737 | 147.92s | `+143` lines (`< 850`) | `2345` lines | `m1_lexer_and_ast+m2_sql_parser+m3_storage_and_expr+m4_query_execution+m5_transactions_and_facade+m6_external_recovery_1+m7_external_recovery_2` (**34 passed**) | `07740fb54a92` | `hae-milestone-m7_external_recovery_2` |

## Held-Out External Test Suite & Differential `sqlite3` Oracle Verification

- **Isolation Guarantee**: `heldout_external_tests/test_minisql_external_oracle.py` was stored outside `minisql_repo/` during the entire build so neither `L0MacroPlanner` nor `L0-Micro` could read or modify it.
- **Initial Post-DAG Result**: `build OK (no build_cmd configured); test `pytest -q /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/heldout_external_tests/test_minisql_external_oracle.py` FAILED (rc=1, failures=2): === FAILURES: /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/heldout_external_tests/test_minisql_external_oracle.py::test_02_ddl_if_not_exists_and_drop_table ===
KeyError: 't1'
Traceback (most recent call last):
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/minisql_repo/.hae/pybin/pytest.py", line 319, in _invoke_test_callable
    fn(**call_kwargs)
    ~~^^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/heldout_external_tests/test_minisql_external_oracle.py", line 78, in test_02_ddl_if_not_exists_and_drop_table
    db.execute("SELECT * FROM t1")
    ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/minisql_repo/minisql/engine.py", line 122, in execute
    return QueryExecutor(self).execute(stmt, params)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/minisql_repo/minisql/engine.py", line 215, in execute
    base = self.db.catalog.tables[stmt.from_table]
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
KeyError: 't1'
FAILED /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/heldout_external_tests/test_minisql_external_oracle.py::test_02_ddl_if_not_exists_and_drop_table - KeyError: 't1'
=== FAILURES: /usr/local/google/home/chavoshi/hierarchical-agent-evolution/resul
... [truncated 6601 chars] ...
LECT region, COUNT(*), COUNT(amount), COUNT(DISTINCT rep), SUM(amount), AVG(amount), MIN(amount), MAX(amount) FROM sales GROUP BY region ORDER BY region': minisql=3 vs sqlite=2
=== FAILURES: /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/heldout_external_tests/test_minisql_external_oracle.py::test_15_division_by_zero_and_statement_atomicity ===
AssertionError: DID NOT RAISE <class 'minisql.ast_nodes.MiniSQLError'>
Traceback (most recent call last):
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/minisql_repo/.hae/pybin/pytest.py", line 319, in _invoke_test_callable
    fn(**call_kwargs)
    ~~^^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/heldout_external_tests/test_minisql_external_oracle.py", line 358, in test_15_division_by_zero_and_statement_atomicity
    with pytest.raises(MiniSQLError):
         ~~~~~~~~~~~~~^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/minisql_repo/.hae/pybin/pytest.py", line 62, in __exit__
    raise AssertionError(f"DID NOT RAISE {self.expected!r}")
AssertionError: DID NOT RAISE <class 'minisql.ast_nodes.MiniSQLError'>
FAILED /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/heldout_external_tests/test_minisql_external_oracle.py::test_15_division_by_zero_and_statement_atomicity - AssertionError: DID NOT RAISE <class 'minisql.ast_nodes.MiniSQLError'>
6 failed, 9 passed in 0.07s`
- **Final Result**: `build OK (no build_cmd configured); test `pytest -q /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/planner_driven_minisql_eval_2026-10-23/heldout_external_tests/test_minisql_external_oracle.py` PASSED (15 passed)`

## Persistent Git Commits & Tags

- `e9d4e8ae113d`: `chore: initial repository scaffold`
- `e02bb6b51018`: `hae(ratchet): certify milestone m1_lexer_and_ast - Lexer and AST foundations`
- `3976fdcae0da`: `hae(ratchet): certify milestone m2_sql_parser - Recursive-descent SQL parser`
- `309e63fa6d13`: `hae(ratchet): certify milestone m3_storage_and_expr - Storage catalog and expression evaluator`
- `f996bfd24287`: `hae(ratchet): certify milestone m4_query_execution - Relational query and DML execution`
- `52bc3bc2573f`: `hae(ratchet): certify milestone m5_transactions_and_facade - Transactions, savepoints, and public facade`
- `dcaffdd1a88f`: `hae(ratchet): certify milestone m6_external_recovery_1 - External Acceptance Recovery & Edge-Case Remediation (#1)`
- `07740fb54a92`: `hae(ratchet): certify milestone m7_external_recovery_2 - External Acceptance Recovery & Edge-Case Remediation (#2)`
