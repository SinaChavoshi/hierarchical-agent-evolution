# Live Planner-Driven `L0-Macro` Ratchet Build & Held-Out External SQLite Oracle Evaluation

- **Date**: `2026-10-23`
- **Live Endpoint**: `http://127.0.0.1:8000/v1`
- **Model**: `reflection/Beam-1.0-501B-A23B-NVFP4` (`+ DFlash2` speculative decoding on GKE)
- **Planner Mode**: **Automatic `L0MacroPlanner.plan()` from high-level system specification (`dag=None`)**
- **Planner LLM Calls / Tokens**: `0` call(s) / `0` tokens
- **Planned & Certified Milestones**: `False` (`1/1`: `['m1_monolithic_minisql']`)
- **Held-Out External Test Suite (`test_minisql_external_oracle.py`, SQLite3 differential oracle)**: initial=`8 passed` (`ok=False`), final=`8 passed` (`ok=False`), recovery_milestones=`[]`
- **Total Wall-Clock Time**: `198.46s`
- **Total LLM Calls / Tokens (Planner + Micro)**: `23` calls / `365,113` tokens (`prompt=345,930`, `output=19,183`)
- **Final Ratcheted `HEAD` SHA**: `0293fafbe9c42d23c8818cf2238fd9b94d5c8e5d`

## Per-Milestone Ratchet & Telemetry Summary

| Milestone (Auto-Planned by `L0MacroPlanner`) | Status | Moves | LLM Calls | Total Tokens | Wall Time (s) | Active Delta (`+LoC` vs `HEAD_{k-1}`) | Total Repo LoC | Cumulative Regression Gate (`M_1..M_k`) | Certified Commit SHA | Git Tag |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `m1_monolithic_minisql` | **CERTIFIED** | 23 | 23 | 365,113 | 197.83s | `+1609` lines (`< 5000`) | `1616` lines | `m1_monolithic_minisql` (**1 passed**) | `0293fafbe9c4` | `hae-milestone-m1_monolithic_minisql` |

## Held-Out External Test Suite & Differential `sqlite3` Oracle Verification

- **Isolation Guarantee**: `heldout_external_tests/test_minisql_external_oracle.py` was stored outside `minisql_repo/` during the entire build so neither `L0MacroPlanner` nor `L0-Micro` could read or modify it.
- **Initial Post-DAG Result**: `build OK (no build_cmd configured); test `pytest -q /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py` FAILED (rc=1, failures=7): === FAILURES: /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py::test_05_escaped_quotes_and_sql_comments ===
MiniSQLError: Unexpected trailing tokens
Traceback (most recent call last):
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/.hae/pybin/pytest.py", line 319, in _invoke_test_callable
    fn(**call_kwargs)
    ~~^^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py", line 127, in test_05_escaped_quotes_and_sql_comments
    db.execute(
    ~~~~~~~~~~^
        """
        ^^^
    ...<5 lines>...
        """
        ^^^
    )
    ^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/engine.py", line 593, in execute
    stmt = parse_sql(sql)
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/parser.py", line 461, in parse_sql
    return p.parse()
           ~~~~~~~^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/parser.py", line 31, in parse
    raise MiniSQLError("Unexpected trailing tokens")
minisql.ast_nodes.MiniSQLError: Unexpected trailing tokens
FAILED /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026
... [truncated 10249 chars] ...
back (most recent call last):
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/.hae/pybin/pytest.py", line 319, in _invoke_test_callable
    fn(**call_kwargs)
    ~~^^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py", line 303, in test_12_distinct_order_by_nulls_limit_and_offset
    run_differential_check(script, queries)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py", line 24, in run_differential_check
    mdb.execute_script(sql_script)
    ~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/engine.py", line 605, in execute_script
    stmts = parse_statements(sql_script)
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/parser.py", line 472, in parse_statements
    buf.append(Token('EOF', None, t.pos))
               ^^^^^
NameError: name 'Token' is not defined. Did you mean: 'open'?
FAILED /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py::test_12_distinct_order_by_nulls_limit_and_offset - NameError: name 'Token' is not defined
7 failed, 8 passed in 0.06s`
- **Final Result**: `build OK (no build_cmd configured); test `pytest -q /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py` FAILED (rc=1, failures=7): === FAILURES: /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py::test_05_escaped_quotes_and_sql_comments ===
MiniSQLError: Unexpected trailing tokens
Traceback (most recent call last):
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/.hae/pybin/pytest.py", line 319, in _invoke_test_callable
    fn(**call_kwargs)
    ~~^^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py", line 127, in test_05_escaped_quotes_and_sql_comments
    db.execute(
    ~~~~~~~~~~^
        """
        ^^^
    ...<5 lines>...
        """
        ^^^
    )
    ^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/engine.py", line 593, in execute
    stmt = parse_sql(sql)
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/parser.py", line 461, in parse_sql
    return p.parse()
           ~~~~~~~^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/parser.py", line 31, in parse
    raise MiniSQLError("Unexpected trailing tokens")
minisql.ast_nodes.MiniSQLError: Unexpected trailing tokens
FAILED /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026
... [truncated 10249 chars] ...
back (most recent call last):
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/.hae/pybin/pytest.py", line 319, in _invoke_test_callable
    fn(**call_kwargs)
    ~~^^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py", line 303, in test_12_distinct_order_by_nulls_limit_and_offset
    run_differential_check(script, queries)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py", line 24, in run_differential_check
    mdb.execute_script(sql_script)
    ~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/engine.py", line 605, in execute_script
    stmts = parse_statements(sql_script)
  File "/usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/minisql_repo/minisql/parser.py", line 472, in parse_statements
    buf.append(Token('EOF', None, t.pos))
               ^^^^^
NameError: name 'Token' is not defined. Did you mean: 'open'?
FAILED /usr/local/google/home/chavoshi/hierarchical-agent-evolution/results/ratchet/monolithic_minisql_baseline_2026-10-27/heldout_external_tests/test_minisql_external_oracle.py::test_12_distinct_order_by_nulls_limit_and_offset - NameError: name 'Token' is not defined
7 failed, 8 passed in 0.06s`

## Persistent Git Commits & Tags

- `9114c8f30c3c`: `chore: initial repository scaffold`
- `0293fafbe9c4`: `hae(ratchet): certify milestone m1_monolithic_minisql - Monolithic Single-Shot MiniSQL Build (No L0-Macro Decomposition/Ratchet)`
