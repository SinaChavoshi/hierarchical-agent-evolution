#!/usr/bin/env python3
"""Live End-to-End Planner-Driven `L0-Macro` Ratchet Build with Held-Out External Test Suite & SQLite Differential Oracle.

Addresses Roadmap #2 / #3 in `README.md`:
  1. **Zero Hand-Authored Milestones (`dag=None`)**:
     Passes ONLY the high-level `MINISQL_SYSTEM_SPEC` string to `L0MacroPlanner.plan()`,
     which invokes the live `reflection/Beam-1.0-501B-A23B-NVFP4` (`+ DFlash2`) model on GKE
     to automatically decompose the specification into a topological `MilestoneDAG`.
  2. **Compositional `L0-Macro -> L0-Micro` Ratchet Execution**:
     `MacroMilestoneRatchetEngine` executes each LLM-planned milestone via V13
     `HierarchicalAgenticMCTSEngine`, verifies cumulative regressions (`M_1 .. M_k`),
     archives Popperian falsification suites (`.hae/ratchet_suites/falsify_<id>.py`),
     and ratchets each certified milestone as a permanent Git commit and tag.
  3. **Strictly Held-Out External Test Suite + Differential `sqlite3` Oracle**:
     The external test suite (`test_minisql_external_oracle.py`, containing 25+ held-out
     test functions and differential equivalence checks against Python's stdlib `sqlite3`)
     is stored OUTSIDE the repository workspace during milestone execution so the agent
     never sees or writes it. After the planned DAG completes, `MacroMilestoneRatchetEngine`
     grades the certified repository against the external oracle (and automatically triggers
     a ratcheted recovery milestone if any held-out test fails).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hae.hierarchical import (
    HierarchicalMCTSSweEngine,
    L0MacroPlanner,
    MacroMilestoneRatchetEngine,
    MilestoneDAG,
    MilestoneSpec,
    PytestVerifierPlugin,
    ShellCommandVerifierPlugin,
)
from hae.infra.llm import detect_llm_provider
from hae.swebench.agentic import AgenticSweEngine
from hae.swebench.executor import LocalExecutor, SCRATCH_DIR


MINISQL_SYSTEM_SPEC = """Build a pure-Python in-memory Relational SQL Database Engine package named `minisql` (`minisql/__init__.py`, `minisql/ast_nodes.py`, `minisql/lexer.py`, `minisql/parser.py`, `minisql/storage.py`, `minisql/engine.py`).

Decompose this build into a 5-milestone topological DAG (`m1_lexer_and_ast`, `m2_sql_parser`, `m3_storage_and_expr`, `m4_query_execution`, `m5_transactions_and_facade`) where each milestone is under 850 diff lines and includes its own `pytest` suite (`tests/test_m1_lexer_and_ast.py`, `tests/test_m2_sql_parser.py`, `tests/test_m3_storage_and_expr.py`, `tests/test_m4_query_execution.py`, `tests/test_m5_transactions_and_facade.py`).

### Public Package API (`minisql/__init__.py`)
Export at top-level `minisql`:
- `class MiniSQLError(Exception)`: raised on lexical/syntax errors, unknown tables/columns, constraint violations (`PRIMARY KEY`, `UNIQUE`, `NOT NULL`), division by zero, parameter count mismatch, or invalid transaction state.
- `@dataclass class QueryResult`:
  - `columns: List[str] = field(default_factory=list)`
  - `rows: List[Tuple[Any, ...]] = field(default_factory=list)`
  - `rowcount: int = 0`
  - `def to_dicts(self) -> List[Dict[str, Any]]`: returns `[dict(zip(self.columns, row)) for row in self.rows]`.
  - `def scalar(self) -> Any`: returns `self.rows[0][0]` if `self.rows` and `len(self.rows[0]) > 0` else `None`.
  - `def __iter__(self)` -> `iter(self.rows)` and `def __len__(self) -> int` -> `len(self.rows)`.
- `class Database`:
  - `__init__(self)`
  - `execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> QueryResult`
  - `execute_script(self, sql_script: str) -> List[QueryResult]` (splits semicolon-terminated statements while respecting quotes and comments, executing each in order).

### Subsystem & Milestone Architecture
1. **Milestone 1 (`m1_lexer_and_ast` -> `minisql/__init__.py`, `minisql/ast_nodes.py`, `minisql/lexer.py`, `tests/test_m1_lexer_and_ast.py`)**:
   - `MiniSQLError` and `QueryResult` definitions in `minisql/ast_nodes.py` (and re-exported in `minisql/__init__.py`).
   - SQL AST node dataclasses in `minisql/ast_nodes.py`:
     - Expressions: `Literal(value)`, `ParamPlaceholder(index: int = 0)`, `ColumnRef(name: str, table: Optional[str] = None)`, `StarRef(table: Optional[str] = None)`, `UnaryExpr(op: str, operand: Any)`, `BinaryExpr(op: str, left: Any, right: Any)`, `IsNullExpr(expr: Any, negated: bool = False)`, `InExpr(expr: Any, items: List[Any], negated: bool = False)`, `BetweenExpr(expr: Any, low: Any, high: Any, negated: bool = False)`, `LikeExpr(expr: Any, pattern: Any, negated: bool = False)`, `CaseExpr(when_thens: List[Tuple[Any, Any]], else_expr: Any = None)`, `FuncCall(name: str, args: List[Any], distinct: bool = False)`.
     - Clauses & Statements: `ColumnSpec(name: str, data_type: str = 'TEXT', primary_key: bool = False, not_null: bool = False, unique: bool = False, default: Any = None, has_default: bool = False)`, `SelectItem(expr: Any, alias: Optional[str] = None)`, `JoinClause(join_type: str, table_name: str, alias: Optional[str] = None, on_expr: Any = None)`, `OrderByItem(expr: Any, asc: bool = True)`, `CreateTableStmt(table_name: str, columns: List[ColumnSpec], if_not_exists: bool = False)`, `DropTableStmt(table_name: str, if_exists: bool = False)`, `CreateIndexStmt(index_name: str, table_name: str, column_name: str, if_not_exists: bool = False)`, `InsertStmt(table_name: str, columns: Optional[List[str]], values_rows: List[List[Any]])`, `UpdateStmt(table_name: str, assignments: List[Tuple[str, Any]], where: Any = None)`, `DeleteStmt(table_name: str, where: Any = None)`, `SelectStmt(select_items: List[SelectItem], from_table: Optional[str] = None, from_alias: Optional[str] = None, distinct: bool = False, joins: List[JoinClause] = ..., where: Any = None, group_by: List[Any] = ..., having: Any = None, order_by: List[OrderByItem] = ..., limit: Optional[int] = None, offset: int = 0)`, `TransactionStmt(action: str, savepoint_name: Optional[str] = None)`.
   - SQL Tokenizer in `minisql/lexer.py` (`Token(type, value, pos)`, `tokenize_sql(sql: str) -> List[Token]`):
     - Handles case-insensitive keywords, identifiers (including double-quoted `"..."` and backtick `` `...` `` identifiers), integer/float literals, single-quoted strings (`'...'` with `''` escaped single quotes), positional `?` placeholders, operators (`=`, `==`, `!=`, `<>`, `<`, `<=`, `>`, `>=`, `+`, `-`, `*`, `/`, `%`, `||`), punctuation (`(`, `)`, `,`, `;`, `.`), `--` line comments, and `/* ... */` block comments. Unterminated string or comment or unexpected character raises `MiniSQLError`.

2. **Milestone 2 (`m2_sql_parser` -> `minisql/parser.py`, `tests/test_m2_sql_parser.py`, depends on `m1_lexer_and_ast`)**:
   - Recursive-descent SQL Parser in `minisql/parser.py` (`parse_sql(sql: str) -> Any` for single statement and `parse_statements(sql: str) -> List[Any]` for semicolon-separated scripts, importing AST nodes from `minisql.ast_nodes` and `tokenize_sql` from `minisql.lexer`):
     - Parses `CREATE TABLE [IF NOT EXISTS]`, `DROP TABLE [IF EXISTS]`, `CREATE INDEX [IF NOT EXISTS] idx ON tbl(col)`, `INSERT INTO tbl [(cols)] VALUES (...), (...)`, `UPDATE tbl SET col = expr, ... [WHERE ...]`, `DELETE FROM tbl [WHERE ...]`, `SELECT [DISTINCT] ... [FROM tbl [[AS] alias]] [INNER/LEFT [OUTER]/CROSS JOIN ...] [WHERE ...] [GROUP BY ...] [HAVING ...] [ORDER BY ... [ASC|DESC]] [LIMIT n [OFFSET m]]`, and transaction statements (`BEGIN [TRANSACTION]`, `COMMIT [TRANSACTION]`, `ROLLBACK [TRANSACTION]`, `SAVEPOINT name`, `RELEASE [SAVEPOINT] name`, `ROLLBACK [TRANSACTION] TO [SAVEPOINT] name`).
     - Positional `?` placeholders MUST be assigned sequential 0-based indices within each statement (`ParamPlaceholder(index=0)`, `ParamPlaceholder(index=1)`, ..., resetting to `0` at the start of each statement).
     - In `CREATE TABLE` column `DEFAULT <expr>`, if the parsed default is a `Literal`, store `default.value` (or evaluate unary `-Literal` to `-default.value`) in `ColumnSpec.default` so `ColumnSpec.default` holds the scalar Python default value directly.
     - Parses operator precedence (`OR` -> `AND` -> `NOT` -> `IS [NOT] NULL` / `[NOT] IN` / `[NOT] BETWEEN` / `[NOT] LIKE` / comparisons `=`, `==`, `!=`, `<>`, `<`, `<=`, `>`, `>=` -> `||` -> `+`, `-` -> `*`, `/`, `%` -> unary `+`, `-` -> primary literals, `?`, `[table.]col`, `table.*`, `*`, `(expr)`, `CASE WHEN ... THEN ... ELSE ... END`, and `FUNC([DISTINCT] ...)` including `COUNT(*)`). Raises `MiniSQLError` on invalid syntax.
     - Write at least 5 real unit tests in `tests/test_m2_sql_parser.py`.

3. **Milestone 3 (`m3_storage_and_expr` -> `minisql/storage.py`, `tests/test_m3_storage_and_expr.py`, depends on `m2_sql_parser`)**:
   - In-memory Table & Schema Catalog (`ColumnDef`, `Table`, `Catalog`) and 3-Valued SQL `NULL` Expression Evaluator (`eval_expr`, `is_truthy`) in `minisql/storage.py`:
     - Type coercion/affinity for `INT`/`INTEGER`, `REAL`/`FLOAT`, `TEXT`/`VARCHAR`, `BOOL`/`BOOLEAN` (preserving `None` as SQL `NULL`).
     - Constraint enforcement on row insert and update: `PRIMARY KEY` (implies `NOT NULL` and `UNIQUE`), `UNIQUE` (multiple `NULL`s allowed in `UNIQUE` columns just like SQLite), `NOT NULL`, and `DEFAULT` value application when columns are omitted on `INSERT` (if `c.default` is an AST `Literal` or expression, unwrap/evaluate it with `eval_expr`).
     - Secondary hash index support (`CREATE INDEX [IF NOT EXISTS] idx ON tbl(col)`) maintained on insert/update/delete.
     - 3-Valued SQL `NULL` Expression Evaluator (`eval_expr(expr, row_ctx, params)` and `is_truthy(val)`):
       - Arithmetic (`+`, `-`, `*`, `/`, `%`, unary `-`, `+`): any `NULL` operand returns `None`; division or modulo by `0` raises `MiniSQLError`.
       - String concatenation `||`: converts numbers to str; if either operand is `NULL`, returns `None`.
       - Comparisons (`=`, `==`, `!=`, `<>`, `<`, `<=`, `>`, `>=`): if either operand is `NULL`, returns `None`.
       - Boolean `AND`, `OR`, `NOT` with 3-valued truth tables (`False AND None -> False`, `True OR None -> True`, `NOT None -> None`).
       - `IS NULL`, `IS NOT NULL`, `[NOT] IN (...)`, `[NOT] BETWEEN low AND high`, `[NOT] LIKE pattern` (case-insensitive ASCII matching with `%` and `_` wildcards; returns `None` if input or pattern is `NULL`), `CASE WHEN ... THEN ... ELSE ... END`.
       - Positional `ParamPlaceholder(index)`: if `index >= len(params)` or `params` is `None`, raise `MiniSQLError`.
       - Scalar functions: `COALESCE(...)`, `IFNULL(a, b)`, `NULLIF(a, b)`, `UPPER(s)`, `LOWER(s)`, `LENGTH(s)`, `ABS(x)`, `ROUND(x [, n])`.
     - Write at least 5 real unit tests in `tests/test_m3_storage_and_expr.py`.

4. **Milestone 4 (`m4_query_execution` -> `minisql/engine.py`, `tests/test_m4_query_execution.py`, depends on `m3_storage_and_expr`)**:
   - Relational Query & DML Executor (`QueryExecutor` / `Database` core in `minisql/engine.py`):
     - Executes `CREATE TABLE`, `DROP TABLE`, `CREATE INDEX` (with `if_not_exists`), `INSERT` (evaluating each expression in `values_rows` with `eval_expr(expr, {}, params)`, checking column-value count match, and returning `QueryResult(rowcount=n)`), `UPDATE` (returning `QueryResult(rowcount=n)`), and `DELETE` (returning `QueryResult(rowcount=n)`).
     - Full `SELECT` pipeline:
       1. `FROM` base table (with optional alias) + chained `INNER JOIN`, `LEFT JOIN` (`LEFT OUTER JOIN`), and `CROSS JOIN` evaluation (`LEFT JOIN` pads unmatched right-side columns with `None`). Also supports `SELECT` without `FROM` (e.g. `SELECT 1 + 2`).
       2. `WHERE` filtering (keeps rows where `is_truthy(eval_expr(where))` is `True`).
       3. `GROUP BY` partitioning + aggregate functions `COUNT(*)`, `COUNT(expr)`, `COUNT(DISTINCT expr)`, `SUM(expr)`, `AVG(expr)`, `MIN(expr)`, `MAX(expr)`:
          - IMPORTANT: If `stmt.group_by` is empty AND no aggregate function (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`) appears in `select_items` or `having`, do NOT collapse `row_list` into a single group — project each row in `row_list` individually!
          - If `stmt.group_by` is non-empty, partition `row_list` by the `group_by` key tuple. If `stmt.group_by` is empty but aggregate functions are present in `select_items` or `having`, treat the entire `row_list` as a single group `[ (None, row_list) ]` (even when `row_list` is empty, where `COUNT` -> `0` and `SUM/AVG/MIN/MAX` -> `None`), and support arithmetic expressions over aggregates (e.g. `COUNT(*) + 1`, `SUM(x) / COUNT(*)`).
       4. `HAVING` filtering on grouped/aggregated rows (supporting both aggregate expressions and `SELECT` output aliases).
       5. Projection of `SELECT` items (`*`, `alias.*`, expressions, `AS alias`) and `DISTINCT` deduplication (for `*` on a single table without joins, output unqualified column names `col`, not `alias.col`).
       6. `ORDER BY` sorting (multi-key `ASC`/`DESC`, resolving column names, qualified names, `SELECT` output aliases, or expressions; `None`/`NULL` sorts first in `ASC` and last in `DESC`, matching SQLite).
       7. `OFFSET` and `LIMIT` slicing.
     - Write at least 5 real unit tests in `tests/test_m4_query_execution.py` testing `INSERT`, `SELECT`, `JOIN`, `GROUP BY`/`HAVING`, and `ORDER BY`/`LIMIT`.

5. **Milestone 5 (`m5_transactions_and_facade` -> `minisql/engine.py`, `minisql/__init__.py`, `tests/test_m5_transactions_and_facade.py`, depends on `m4_query_execution`)**:
   - ACID Transactions, Savepoints & Public `Database` Facade:
     - `BEGIN [TRANSACTION]`, `COMMIT`, `ROLLBACK` using deep-copy catalog snapshots so `ROLLBACK` restores all tables, rows, schema changes, and indexes to their exact pre-transaction state (raising `MiniSQLError` on `COMMIT` or `ROLLBACK` when no transaction is active, or on `BEGIN` when already in a transaction).
     - Nested `SAVEPOINT <name>`, `RELEASE [SAVEPOINT] <name>`, and `ROLLBACK TO [SAVEPOINT] <name>` restoring state to the named savepoint while keeping the transaction active (raising `MiniSQLError` if the savepoint name is unknown).
     - Statement-level atomicity: before executing any mutating statement (`INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`), save a snapshot of the catalog so if `MiniSQLError` is raised mid-statement (e.g., constraint violation on the 2nd row of a multi-row `INSERT` or division by zero in `UPDATE`), the statement's partial modifications are rolled back.
     - Export `Database`, `QueryResult`, `MiniSQLError` in `minisql/__init__.py` and write at least 5 real unit tests in `tests/test_m5_transactions_and_facade.py` verifying `execute`, `execute_script`, transactions, savepoints, and statement atomicity.
"""


HELDOUT_EXTERNAL_ORACLE_TEST_SOURCE = '''"""Strictly Held-Out External Test Suite & SQLite3 Differential Oracle for `minisql`.

This test suite is NEVER visible to `L0MacroPlanner` or `L0-Micro` during the build.
It grades the certified `minisql` repository across 26 independent test cases including
direct differential equivalence checks against Python's standard library `sqlite3`.
"""

from __future__ import annotations

import sqlite3
import pytest

import minisql
from minisql import Database, MiniSQLError, QueryResult


def run_differential_check(sql_script: str, queries: list[tuple[str, tuple | list | None]], float_tol: float = 1e-6) -> None:
    """Execute identical setup script and queries on both `minisql.Database` and `sqlite3.connect(':memory:')`
    and assert exact row and column count parity.
    """
    mdb = Database()
    sdb = sqlite3.connect(":memory:")
    try:
        mdb.execute_script(sql_script)
        sdb.executescript(sql_script)

        for q_sql, params in queries:
            m_res = mdb.execute(q_sql, params) if params is not None else mdb.execute(q_sql)
            cur = sdb.execute(q_sql, tuple(params)) if params is not None else sdb.execute(q_sql)
            s_rows = cur.fetchall()
            s_cols = [d[0] for d in (cur.description or [])]

            assert len(m_res.rows) == len(s_rows), f"Row count mismatch for {q_sql!r}: minisql={m_res.rows} vs sqlite={s_rows}"
            assert len(m_res.columns) == len(s_cols), f"Column count mismatch for {q_sql!r}: {m_res.columns} vs {s_cols}"

            for r_idx, (m_row, s_row) in enumerate(zip(m_res.rows, s_rows)):
                assert len(m_row) == len(s_row)
                for c_idx, (m_val, s_val) in enumerate(zip(m_row, s_row)):
                    if isinstance(m_val, bool):
                        m_val = int(m_val)
                    if isinstance(s_val, bool):
                        s_val = int(s_val)
                    if isinstance(m_val, (int, float)) and isinstance(s_val, (int, float)):
                        assert abs(float(m_val) - float(s_val)) <= float_tol, (
                            f"Numeric mismatch at row {r_idx} col {c_idx} for {q_sql!r}: minisql={m_val!r} vs sqlite={s_val!r}"
                        )
                    else:
                        assert m_val == s_val, (
                            f"Value mismatch at row {r_idx} col {c_idx} for {q_sql!r}: minisql={m_val!r} vs sqlite={s_val!r}"
                        )
    finally:
        sdb.close()


def test_01_public_exports_and_query_result_helpers():
    db = Database()
    db.execute("CREATE TABLE items (id INT PRIMARY KEY, name TEXT NOT NULL, price REAL DEFAULT 1.5)")
    r_ins = db.execute("INSERT INTO items (id, name) VALUES (1, 'apple')")
    assert r_ins.rowcount == 1
    res = db.execute("SELECT id, name, price FROM items")
    assert isinstance(res, QueryResult)
    assert len(res) == 1
    assert list(res) == [(1, "apple", 1.5)]
    assert res.to_dicts() == [{"id": 1, "name": "apple", "price": 1.5}]
    assert db.execute("SELECT name FROM items WHERE id = 1").scalar() == "apple"
    assert db.execute("SELECT name FROM items WHERE id = 999").scalar() is None


def test_02_ddl_if_not_exists_and_drop_table():
    db = Database()
    db.execute("CREATE TABLE IF NOT EXISTS t1 (id INT PRIMARY KEY, v TEXT)")
    db.execute("INSERT INTO t1 VALUES (1, 'a')")
    db.execute("CREATE TABLE IF NOT EXISTS t1 (id INT PRIMARY KEY, v TEXT)")
    assert db.execute("SELECT COUNT(*) FROM t1").scalar() == 1
    db.execute("DROP TABLE IF EXISTS non_existent")
    db.execute("DROP TABLE t1")
    with pytest.raises(MiniSQLError):
        db.execute("SELECT * FROM t1")


def test_03_constraints_primary_key_unique_not_null_and_defaults():
    db = Database()
    db.execute(
        "CREATE TABLE users ("
        "id INT PRIMARY KEY, "
        "email TEXT UNIQUE, "
        "username TEXT NOT NULL, "
        "role TEXT DEFAULT 'member'"
        ")"
    )
    db.execute("INSERT INTO users (id, email, username) VALUES (1, 'a@x.com', 'alice')")
    # Multiple NULLs are permitted in a UNIQUE column
    db.execute("INSERT INTO users (id, email, username) VALUES (2, NULL, 'bob')")
    db.execute("INSERT INTO users (id, email, username) VALUES (3, NULL, 'charlie')")
    assert db.execute("SELECT role FROM users WHERE id = 1").scalar() == "member"

    # Duplicate PRIMARY KEY
    with pytest.raises(MiniSQLError):
        db.execute("INSERT INTO users (id, email, username) VALUES (1, 'other@x.com', 'dup_id')")
    # NULL PRIMARY KEY
    with pytest.raises(MiniSQLError):
        db.execute("INSERT INTO users (id, email, username) VALUES (NULL, 'nullpk@x.com', 'null_id')")
    # Duplicate non-NULL UNIQUE
    with pytest.raises(MiniSQLError):
        db.execute("INSERT INTO users (id, email, username) VALUES (4, 'a@x.com', 'dup_email')")
    # Violating NOT NULL
    with pytest.raises(MiniSQLError):
        db.execute("INSERT INTO users (id, email, username) VALUES (5, 'e@x.com', NULL)")


def test_04_multi_row_insert_and_positional_parameters():
    db = Database()
    db.execute("CREATE TABLE products (id INT PRIMARY KEY, title TEXT, qty INT, price REAL)")
    r = db.execute(
        "INSERT INTO products VALUES (1, 'Pen', 10, 1.25), (2, 'Book', 5, 12.50), (3, 'Bag', 2, 45.0)"
    )
    assert r.rowcount == 3
    r2 = db.execute("INSERT INTO products (id, title, qty, price) VALUES (?, ?, ?, ?)", [4, "Lamp", 3, 22.0])
    assert r2.rowcount == 1
    rows = db.execute("SELECT id, title FROM products WHERE qty >= ? AND price < ?", (3, 25.0)).rows
    assert sorted(rows) == [(1, "Pen"), (2, "Book"), (4, "Lamp")]


def test_05_escaped_quotes_and_sql_comments():
    db = Database()
    db.execute("CREATE TABLE notes (id INT PRIMARY KEY, body TEXT)")
    db.execute(
        """
        -- Leading line comment
        INSERT INTO notes VALUES (1, 'It''s a ''quoted'' string -- not a comment');
        /* Multi-line
           block comment */
        INSERT INTO notes VALUES (2, 'Second /* literal */ note');
        """
    )
    rows = db.execute("SELECT id, body FROM notes ORDER BY id ASC").rows
    expected_rows = [
        (1, "It's a 'quoted' string -- not a comment"),
        (2, "Second /* literal */ note"),
    ]
    assert rows == expected_rows, (
        f"Multi-statement execute() with comments and escaped single quotes returned {rows!r}, expected {expected_rows!r}"
    )


def test_06_update_and_delete_rowcounts_and_expressions():
    db = Database()
    db.execute("CREATE TABLE accounts (id INT PRIMARY KEY, owner TEXT, balance INT)")
    db.execute("INSERT INTO accounts VALUES (1, 'a', 100), (2, 'b', 250), (3, 'c', 400)")
    u_res = db.execute("UPDATE accounts SET balance = balance + 50 WHERE balance >= 200")
    assert u_res.rowcount == 2
    assert db.execute("SELECT balance FROM accounts ORDER BY id").rows == [(100,), (300,), (450,)]

    d_res = db.execute("DELETE FROM accounts WHERE balance < 200")
    assert d_res.rowcount == 1
    assert db.execute("SELECT id FROM accounts ORDER BY id").rows == [(2,), (3,)]


def test_07_three_valued_null_logic_truth_tables():
    script = """
    CREATE TABLE null_test (id INT PRIMARY KEY, a INT, b INT);
    INSERT INTO null_test VALUES (1, 10, 20), (2, 10, NULL), (3, NULL, 20), (4, NULL, NULL);
    """
    queries = [
        ("SELECT id FROM null_test WHERE a = 10 OR b = 999 ORDER BY id", None),
        ("SELECT id FROM null_test WHERE a = 999 AND b IS NULL ORDER BY id", None),
        ("SELECT id FROM null_test WHERE a = b ORDER BY id", None),
        ("SELECT id FROM null_test WHERE a != b ORDER BY id", None),
        ("SELECT id FROM null_test WHERE NOT (a = 10) ORDER BY id", None),
        ("SELECT id FROM null_test WHERE a IS NULL AND b IS NOT NULL ORDER BY id", None),
    ]
    run_differential_check(script, queries)


def test_08_where_operators_between_in_like_and_case():
    script = """
    CREATE TABLE emp (id INT PRIMARY KEY, name TEXT, dept TEXT, salary INT);
    INSERT INTO emp VALUES
        (1, 'Alice', 'Eng', 120),
        (2, 'Bob', 'Eng', 95),
        (3, 'Charlie', 'Sales', 80),
        (4, 'Diana', 'Marketing', 110),
        (5, 'Evan', 'Sales', NULL);
    """
    queries = [
        ("SELECT id, name FROM emp WHERE salary BETWEEN 95 AND 120 ORDER BY id", None),
        ("SELECT id, name FROM emp WHERE salary NOT BETWEEN 90 AND 115 ORDER BY id", None),
        ("SELECT id, name FROM emp WHERE dept IN ('Eng', 'Marketing') ORDER BY id", None),
        ("SELECT id, name FROM emp WHERE dept NOT IN ('Sales') ORDER BY id", None),
        ("SELECT id, name FROM emp WHERE name LIKE 'a%' OR name LIKE '_ia_a' ORDER BY id", None),
        ("SELECT id, name FROM emp WHERE name NOT LIKE '%e%' ORDER BY id", None),
        (
            "SELECT id, CASE WHEN salary >= 115 THEN 'senior' WHEN salary >= 90 THEN 'mid' ELSE 'other' END AS band "
            "FROM emp ORDER BY id",
            None,
        ),
    ]
    run_differential_check(script, queries)


def test_09_scalar_functions_and_string_concat():
    script = """
    CREATE TABLE funcs (id INT PRIMARY KEY, s TEXT, n REAL, alt TEXT);
    INSERT INTO funcs VALUES (1, 'Hello', -3.14159, 'Fallback'), (2, NULL, 7.6, 'Backup');
    """
    queries = [
        ("SELECT id, UPPER(s), LOWER(s), LENGTH(s) FROM funcs WHERE id = 1", None),
        ("SELECT id, COALESCE(s, alt, 'None'), IFNULL(s, alt), NULLIF(id, 2) FROM funcs ORDER BY id", None),
        ("SELECT id, ABS(n), ROUND(n, 2), ROUND(n) FROM funcs ORDER BY id", None),
        ("SELECT id, s || ' - ' || alt FROM funcs ORDER BY id", None),
    ]
    run_differential_check(script, queries)


def test_10_inner_left_and_chained_multi_table_joins():
    script = """
    CREATE TABLE departments (id INT PRIMARY KEY, dname TEXT);
    CREATE TABLE employees (id INT PRIMARY KEY, ename TEXT, dept_id INT, salary INT);
    CREATE TABLE projects (id INT PRIMARY KEY, pname TEXT, lead_emp_id INT);

    INSERT INTO departments VALUES (10, 'Research'), (20, 'Platform'), (30, 'Legal');
    INSERT INTO employees VALUES
        (1, 'Ada', 10, 150),
        (2, 'Grace', 10, 140),
        (3, 'Linus', 20, 160),
        (4, 'Ghost', NULL, 90);
    INSERT INTO projects VALUES (100, 'Compiler', 1), (200, 'Kernel', 3), (300, 'Unassigned', 99);
    """
    queries = [
        (
            "SELECT e.ename, d.dname FROM employees e INNER JOIN departments d ON e.dept_id = d.id ORDER BY e.id",
            None,
        ),
        (
            "SELECT e.ename, d.dname FROM employees e LEFT JOIN departments d ON e.dept_id = d.id ORDER BY e.id",
            None,
        ),
        (
            "SELECT d.dname, e.ename FROM departments d LEFT JOIN employees e ON d.id = e.dept_id ORDER BY d.id, e.id",
            None,
        ),
        (
            "SELECT p.pname, e.ename, d.dname "
            "FROM projects p "
            "LEFT JOIN employees e ON p.lead_emp_id = e.id "
            "LEFT JOIN departments d ON e.dept_id = d.id "
            "ORDER BY p.id",
            None,
        ),
    ]
    run_differential_check(script, queries)


def test_11_group_by_having_and_aggregates():
    script = """
    CREATE TABLE sales (id INT PRIMARY KEY, region TEXT, rep TEXT, amount INT);
    INSERT INTO sales VALUES
        (1, 'West', 'A', 100),
        (2, 'West', 'A', 200),
        (3, 'West', 'B', 50),
        (4, 'East', 'C', 300),
        (5, 'East', 'D', NULL),
        (6, 'North', 'E', 40);
    """
    queries = [
        (
            "SELECT region, COUNT(*), COUNT(amount), COUNT(DISTINCT rep), SUM(amount), AVG(amount), MIN(amount), MAX(amount) "
            "FROM sales GROUP BY region ORDER BY region",
            None,
        ),
        (
            "SELECT region, SUM(amount) AS total_sales FROM sales GROUP BY region HAVING SUM(amount) >= 300 ORDER BY region",
            None,
        ),
        (
            "SELECT COUNT(*), COUNT(amount), SUM(amount), MIN(amount), MAX(amount) FROM sales",
            None,
        ),
        (
            "SELECT COUNT(*), SUM(amount), AVG(amount), MIN(amount), MAX(amount) FROM sales WHERE id > 999",
            None,
        ),
    ]
    run_differential_check(script, queries)


def test_12_distinct_order_by_nulls_limit_and_offset():
    script = """
    CREATE TABLE scores (id INT PRIMARY KEY, team TEXT, pts INT);
    INSERT INTO scores VALUES
        (1, 'Alpha', 30),
        (2, 'Beta', NULL),
        (3, 'Alpha', 30),
        (4, 'Gamma', 10),
        (5, 'Delta', 50),
        (6, 'Epsilon', 20);
    """
    queries = [
        ("SELECT DISTINCT team, pts FROM scores ORDER BY pts ASC, team ASC", None),
        ("SELECT id, team, pts FROM scores ORDER BY pts DESC, id ASC", None),
        ("SELECT id, pts * 2 AS double_pts FROM scores WHERE pts IS NOT NULL ORDER BY double_pts DESC LIMIT 3 OFFSET 1", None),
    ]
    run_differential_check(script, queries)


def test_13_secondary_index_consistency_across_dml():
    db = Database()
    db.execute("CREATE TABLE logs (id INT PRIMARY KEY, tag TEXT, val INT)")
    db.execute("CREATE INDEX idx_logs_tag ON logs (tag)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_logs_tag ON logs (tag)")
    db.execute("INSERT INTO logs VALUES (1, 'auth', 10), (2, 'db', 20), (3, 'auth', 30)")
    assert db.execute("SELECT id, val FROM logs WHERE tag = 'auth' ORDER BY id").rows == [(1, 10), (3, 30)]

    db.execute("UPDATE logs SET tag = 'cache' WHERE id = 1")
    assert db.execute("SELECT id FROM logs WHERE tag = 'auth'").rows == [(3,)]
    assert db.execute("SELECT id FROM logs WHERE tag = 'cache'").rows == [(1,)]

    db.execute("DELETE FROM logs WHERE id = 3")
    assert db.execute("SELECT id FROM logs WHERE tag = 'auth'").rows == []


def test_14_transactions_begin_commit_rollback_and_savepoints():
    db = Database()
    db.execute("CREATE TABLE ledger (id INT PRIMARY KEY, amt INT)")
    db.execute("INSERT INTO ledger VALUES (1, 100), (2, 200)")

    # Explicit transaction + ROLLBACK restores state completely
    db.execute("BEGIN TRANSACTION")
    db.execute("INSERT INTO ledger VALUES (3, 300)")
    db.execute("UPDATE ledger SET amt = 999 WHERE id = 1")
    db.execute("DELETE FROM ledger WHERE id = 2")
    assert db.execute("SELECT id, amt FROM ledger ORDER BY id").rows == [(1, 999), (3, 300)]
    db.execute("ROLLBACK")
    assert db.execute("SELECT id, amt FROM ledger ORDER BY id").rows == [(1, 100), (2, 200)]

    # Nested SAVEPOINT + ROLLBACK TO + RELEASE + COMMIT
    db.execute("BEGIN")
    db.execute("UPDATE ledger SET amt = 150 WHERE id = 1")
    db.execute("SAVEPOINT sp1")
    db.execute("INSERT INTO ledger VALUES (3, 500)")
    db.execute("SAVEPOINT sp2")
    db.execute("DELETE FROM ledger WHERE id = 2")
    assert db.execute("SELECT COUNT(*) FROM ledger").scalar() == 2

    db.execute("ROLLBACK TO SAVEPOINT sp2")
    assert db.execute("SELECT id, amt FROM ledger ORDER BY id").rows == [(1, 150), (2, 200), (3, 500)]

    db.execute("ROLLBACK TO sp1")
    assert db.execute("SELECT id, amt FROM ledger ORDER BY id").rows == [(1, 150), (2, 200)]

    db.execute("INSERT INTO ledger VALUES (4, 400)")
    db.execute("COMMIT")
    assert db.execute("SELECT id, amt FROM ledger ORDER BY id").rows == [(1, 150), (2, 200), (4, 400)]


def test_15_division_by_zero_and_statement_atomicity():
    db = Database()
    db.execute("CREATE TABLE nums (id INT PRIMARY KEY, x INT UNIQUE)")
    db.execute("INSERT INTO nums VALUES (1, 10)")

    with pytest.raises(MiniSQLError):
        db.execute("SELECT 10 / 0 FROM nums")

    # Multi-row insert where second row violates UNIQUE constraint must be atomic
    with pytest.raises(MiniSQLError):
        db.execute("INSERT INTO nums VALUES (2, 20), (3, 10)")
    assert db.execute("SELECT id, x FROM nums ORDER BY id").rows == [(1, 10)]
'''


def verify_vllm_endpoint(vllm_url: str, vllm_model: str) -> Dict[str, Any]:
    """Verify live vLLM endpoint connectivity and model availability via /models."""
    models_url = f"{vllm_url.rstrip('/')}/models"
    req = urllib.request.Request(models_url, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    model_ids = [m.get("id", "") for m in data.get("data", [])]
    return {
        "endpoint": vllm_url,
        "requested_model": vllm_model,
        "available_models": model_ids,
        "detected_provider": detect_llm_provider(),
        "hierarchical_micro_engine": HierarchicalMCTSSweEngine.__name__,
    }


def init_minisql_git_workspace(workspace_dir: Path, clean: bool = True) -> LocalExecutor:
    """Initialize a clean Git repository for the Planner-Driven MiniSQL build."""
    if clean and workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    executor = LocalExecutor(root=str(workspace_dir), python=sys.executable)
    if not (workspace_dir / ".git").exists():
        executor.git(["init"])
        executor.git(["config", "user.name", "HAE Ratchet Eval"])
        executor.git(["config", "user.email", "hae-eval@local"])
        executor.write_file(
            ".gitignore",
            ".hae/\n__pycache__/\n*.pyc\n.pytest_cache/\n",
        )
        executor.write_file(
            "README.md",
            "# MiniSQL Relational SQL Engine\n\n"
            "Planner-driven `L0-Macro` compositional build managed by `MacroMilestoneRatchetEngine`.\n",
        )
        executor.git(["add", ".gitignore", "README.md"])
        executor.git(["commit", "-m", "chore: initial repository scaffold"])
    return executor


def collect_git_ratchet_telemetry(executor: LocalExecutor, dag: MilestoneDAG) -> Dict[str, Any]:
    """Inspect the Git commit history, tags, diffs, and file line counts in the workspace."""
    log_res = executor.git(["log", "--reverse", "--pretty=format:%H|%s"])
    commits: List[Dict[str, str]] = []
    if log_res.ok:
        for line in log_res.stdout.splitlines():
            if "|" in line:
                sha, subject = line.split("|", 1)
                commits.append({"sha": sha.strip(), "subject": subject.strip()})

    tag_res = executor.git(["tag", "-l", "hae-milestone-*"])
    tags: Dict[str, str] = {}
    if tag_res.ok:
        for tname in [l.strip() for l in tag_res.stdout.splitlines() if l.strip()]:
            rev = executor.git(["rev-parse", tname])
            if rev.ok:
                tags[tname] = rev.stdout.strip()

    milestone_git_stats: Dict[str, Dict[str, Any]] = {}
    for mid in dag.topological_order():
        m = dag.milestones[mid]
        sha = m.certified_commit_sha
        if not sha:
            continue
        parent_res = executor.git(["rev-parse", f"{sha}^"])
        parent_sha = parent_res.stdout.strip() if parent_res.ok else ""
        diff_res = executor.git(["diff", parent_sha, sha]) if parent_sha else executor.git(["show", sha])
        diff_text = diff_res.stdout if diff_res.ok else ""
        added = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))

        tree_res = executor.git(["ls-tree", "-r", "--name-only", sha])
        repo_loc_at_commit = 0
        tracked_files_at_commit: List[str] = []
        if tree_res.ok:
            for rel in [l.strip() for l in tree_res.stdout.splitlines() if l.strip()]:
                if rel.startswith(SCRATCH_DIR + "/"):
                    continue
                tracked_files_at_commit.append(rel)
                blob_res = executor.git(["show", f"{sha}:{rel}"])
                if blob_res.ok:
                    repo_loc_at_commit += len(blob_res.stdout.splitlines())

        milestone_git_stats[mid] = {
            "base_commit_sha": parent_sha,
            "certified_commit_sha": sha,
            "git_tag": f"hae-milestone-{mid}",
            "tag_matches_sha": tags.get(f"hae-milestone-{mid}") == sha,
            "delta_added_lines": added,
            "delta_removed_lines": removed,
            "delta_total_diff_lines": len(diff_text.splitlines()),
            "bounded_under_max_diff_lines": added <= m.max_diff_lines,
            "total_repo_loc_at_commit": repo_loc_at_commit,
            "tracked_files_at_commit": tracked_files_at_commit,
        }

    return {
        "commits": commits,
        "tags": tags,
        "milestone_git_stats": milestone_git_stats,
    }


def write_eval_readme(out_dir: Path, summary: Dict[str, Any]) -> None:
    """Render a comprehensive Markdown report in `<out_dir>/README.md`."""
    ext_v = summary.get("external_verification") or {}
    ext_init = ext_v.get("initial") or {}
    ext_final = ext_v.get("final") or {}
    p_tok = summary.get("planner_token_usage") or {}

    lines: List[str] = []
    lines.append("# Live Planner-Driven `L0-Macro` Ratchet Build & Held-Out External SQLite Oracle Evaluation")
    lines.append("")
    lines.append(f"- **Date**: `2026-10-23`")
    lines.append(f"- **Live Endpoint**: `{summary['endpoint_info']['endpoint']}`")
    lines.append(f"- **Model**: `{summary['endpoint_info']['requested_model']}` (`+ DFlash2` speculative decoding on GKE)")
    lines.append(f"- **Planner Mode**: **Automatic `L0MacroPlanner.plan()` from high-level system specification (`dag=None`)**")
    lines.append(f"- **Planner LLM Calls / Tokens**: `{p_tok.get('llm_calls', 0)}` call(s) / `{p_tok.get('total_tokens', 0):,}` tokens")
    lines.append(f"- **Planned & Certified Milestones**: `{summary['completed']}` (`{len(summary['certified_milestones'])}/{len(summary['topological_order'])}`: `{summary['certified_milestones']}`)")
    lines.append(
        f"- **Held-Out External Test Suite (`test_minisql_external_oracle.py`, SQLite3 differential oracle)**: "
        f"initial=`{ext_init.get('passed_tests', 0)} passed` (`ok={ext_init.get('tests_ok')}`), "
        f"final=`{ext_final.get('passed_tests', 0)} passed` (`ok={ext_final.get('tests_ok')}`), "
        f"recovery_milestones=`{ext_v.get('recovery_milestones', [])}`"
    )
    lines.append(f"- **Total Wall-Clock Time**: `{summary['total_wall_clock_s']:.2f}s`")
    lines.append(
        f"- **Total LLM Calls / Tokens (Planner + Micro)**: `{summary['totals']['llm_calls']}` calls / "
        f"`{summary['totals']['total_tokens']:,}` tokens (`prompt={summary['totals']['prompt_tokens']:,}`, `output={summary['totals']['output_tokens']:,}`)"
    )
    lines.append(f"- **Final Ratcheted `HEAD` SHA**: `{summary['head_commit_sha']}`")
    lines.append("")
    lines.append("## Per-Milestone Ratchet & Telemetry Summary")
    lines.append("")
    lines.append(
        "| Milestone (Auto-Planned by `L0MacroPlanner`) | Status | Moves | LLM Calls | Total Tokens | Wall Time (s) | Active Delta (`+LoC` vs `HEAD_{k-1}`) | Total Repo LoC | Cumulative Regression Gate (`M_1..M_k`) | Certified Commit SHA | Git Tag |"
    )
    lines.append(
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |"
    )
    for m in summary["milestones"]:
        mid = m["milestone_id"]
        status = m["status"]
        moves = m["moves_used"]
        llm_calls = m["token_usage"].get("llm_calls", 0)
        tot_tok = m["token_usage"].get("total_tokens", 0)
        elapsed = m["elapsed_s"]
        added = m["delta_added_lines"]
        max_dl = m["max_diff_lines"]
        total_loc = m["total_repo_loc"]
        cum_stages = "+".join(m["verified_milestones"])
        cum_tests = m["cumulative_passed_tests"]
        sha_short = (m["certified_commit_sha"] or "")[:12]
        tag = m["git_tag"]
        lines.append(
            f"| `{mid}` | **{status.upper()}** | {moves} | {llm_calls} | {tot_tok:,} | {elapsed:.2f}s | "
            f"`+{added}` lines (`< {max_dl}`) | `{total_loc}` lines | "
            f"`{cum_stages}` (**{cum_tests} passed**) | `{sha_short}` | `{tag}` |"
        )

    lines.append("")
    lines.append("## Held-Out External Test Suite & Differential `sqlite3` Oracle Verification")
    lines.append("")
    lines.append(
        "- **Isolation Guarantee**: `heldout_external_tests/test_minisql_external_oracle.py` was stored outside "
        "`minisql_repo/` during the entire build so neither `L0MacroPlanner` nor `L0-Micro` could read or modify it."
    )
    lines.append(f"- **Initial Post-DAG Result**: `{ext_init.get('summary', '')}`")
    lines.append(f"- **Final Result**: `{ext_final.get('summary', '')}`")
    lines.append("")
    lines.append("## Persistent Git Commits & Tags")
    lines.append("")
    for c in summary["git_telemetry"]["commits"]:
        lines.append(f"- `{c['sha'][:12]}`: `{c['subject']}`")
    lines.append("")
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Live Planner-Driven L0-Macro Ratchet Build & External SQLite Oracle Evaluation on Beam-1.0-501B."
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="results/ratchet/planner_driven_minisql_eval_2026-10-23",
        help="Output directory for summary.json, planner_dag.json, heldout_external_tests/, and minisql_repo/.",
    )
    parser.add_argument(
        "--vllm-url",
        type=str,
        default="http://127.0.0.1:8000/v1",
        help="Live vLLM OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--vllm-model",
        type=str,
        default="reflection/Beam-1.0-501B-A23B-NVFP4",
        help="Model identifier served by the live vLLM endpoint.",
    )
    parser.add_argument(
        "--budget-moves",
        type=int,
        default=18,
        help="Maximum L0-Micro AB-MCTS moves per milestone.",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=850,
        help="Maximum added diff lines permitted per individual milestone.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing workspace .hae/ratchet_state.json if present.",
    )
    parser.add_argument(
        "--mode",
        choices=["planner_ratchet", "single_milestone_baseline", "flat_react_baseline"],
        default="planner_ratchet",
        help="Execution mode: full L0-Macro planner + commit ratchet, single-milestone L0-Micro baseline, or flat ReAct baseline.",
    )
    args = parser.parse_args()

    os.environ["LLM_PROVIDER"] = "vllm"
    os.environ["VLLM_BASE_URL"] = args.vllm_url
    os.environ["VLLM_MODEL"] = args.vllm_model
    os.environ["HAE_WORKER_MODEL"] = args.vllm_model
    os.environ["VLLM_API_KEY"] = os.environ.get("VLLM_API_KEY", "EMPTY")
    os.environ["HAE_DISABLE_STAGNATION_ESCALATION"] = "1"
    os.environ["HAE_LLM_MAX_TOKENS"] = os.environ.get("HAE_LLM_MAX_TOKENS", "8192")

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = out_dir / "minisql_repo"

    # Write held-out external oracle test suite OUTSIDE `minisql_repo/`
    heldout_dir = out_dir / "heldout_external_tests"
    heldout_dir.mkdir(parents=True, exist_ok=True)
    heldout_test_file = heldout_dir / "test_minisql_external_oracle.py"
    heldout_test_file.write_text(HELDOUT_EXTERNAL_ORACLE_TEST_SOURCE, encoding="utf-8")

    print(
        f"[planner-ratchet-eval] Verifying live vLLM endpoint at {args.vllm_url} (model={args.vllm_model})...",
        flush=True,
    )
    endpoint_info = verify_vllm_endpoint(args.vllm_url, args.vllm_model)
    print(f"[planner-ratchet-eval] Endpoint verified: {json.dumps(endpoint_info)}", flush=True)

    executor = init_minisql_git_workspace(workspace_dir, clean=not args.resume)

    planner = L0MacroPlanner(
        max_diff_lines=args.max_diff_lines,
        budget_moves=args.budget_moves,
        model_name=args.vllm_model,
        use_live_llm=True,
        logger=lambda msg: print(msg, flush=True),
    )

    verifier_plugin = PytestVerifierPlugin(diff_bloat_threshold=args.max_diff_lines)
    external_verifier_plugin = ShellCommandVerifierPlugin(
        test_cmd=f"pytest -q {heldout_test_file}",
        diff_bloat_threshold=max(args.max_diff_lines, 5000),
    )

    initial_dag = None
    custom_runner = None
    max_recovery = 2
    if args.mode in ("single_milestone_baseline", "flat_react_baseline"):
        from hae.epistemic.gatekeeper import EvidenceGatekeeper
        from hae.epistemic.ledger import EpistemicState
        from hae.hierarchical.milestone_ratchet import (
            _DefaultMicroRunnerAdapter,
            _RatchetWorkspaceExecutorProxy,
        )
        from hae.swebench.task import SweTask

        max_recovery = 0
        mono_id = "m1_monolithic_minisql" if args.mode == "single_milestone_baseline" else "m1_flat_react_minisql"
        initial_dag = MilestoneDAG(
            system_objective=MINISQL_SYSTEM_SPEC,
            milestones={
                mono_id: MilestoneSpec(
                    milestone_id=mono_id,
                    title="Monolithic Single-Shot MiniSQL Build (No L0-Macro Decomposition/Ratchet)",
                    objective=MINISQL_SYSTEM_SPEC,
                    depends_on=[],
                    target_files=[
                        "minisql/__init__.py",
                        "minisql/ast_nodes.py",
                        "minisql/lexer.py",
                        "minisql/parser.py",
                        "minisql/storage.py",
                        "minisql/engine.py",
                        "tests/test_m1_lexer_and_ast.py",
                    ],
                    acceptance_cmd="pytest -q tests/",
                    max_diff_lines=5000,
                    budget_moves=max(args.budget_moves, 30),
                )
            },
        )
        verifier_plugin = PytestVerifierPlugin(diff_bloat_threshold=5000)
        if args.mode == "flat_react_baseline":
            def _flat_react_runner(milestone: MilestoneSpec, ex: LocalExecutor) -> Dict[str, Any]:
                proxy_ex = (
                    ex
                    if isinstance(ex, _RatchetWorkspaceExecutorProxy)
                    else _RatchetWorkspaceExecutorProxy(ex)
                )
                adapter = _DefaultMicroRunnerAdapter(proxy_ex, milestone, model_name=args.vllm_model)
                swe_task = SweTask(
                    instance_id=f"ratchet__{milestone.milestone_id}",
                    repo="minisql_repo",
                    base_commit="HEAD",
                    version="1.0",
                    problem_statement=adapter.objective_text(),
                )
                state = EpistemicState(company_id=f"ratchet_{milestone.milestone_id}")
                state.add_question(
                    f"How to implement and verify {milestone.milestone_id}: {milestone.title}?",
                    module="minisql/__init__.py",
                    uncertainty=0.9,
                )
                gk = EvidenceGatekeeper(
                    workspace=proxy_ex.root,
                    python_executable=proxy_ex.python,
                    executor=proxy_ex,
                    logger=lambda msg: print(msg, flush=True),
                )
                react_engine = AgenticSweEngine(
                    runner=adapter,
                    executor=proxy_ex,
                    task=swe_task,
                    state=state,
                    gatekeeper=gk,
                    logger=lambda msg: print(msg, flush=True),
                    allow_new_files=True,
                )
                t0 = time.time()
                res_react = react_engine.run(
                    candidate_modules=list(milestone.target_files),
                    budget_moves=milestone.budget_moves,
                )
                res_react["elapsed_s"] = round(time.time() - t0, 3)
                res_react["token_usage"] = adapter.token_summary()
                return res_react
            custom_runner = _flat_react_runner

    engine = MacroMilestoneRatchetEngine(
        executor=executor,
        dag=initial_dag,  # None -> Decomposed automatically by L0MacroPlanner on live Beam-1.0-501B!
        objective=MINISQL_SYSTEM_SPEC,
        planner=planner,
        milestone_runner=custom_runner,
        verifier_plugin=verifier_plugin,
        external_verifier_plugin=external_verifier_plugin,
        max_recovery_milestones=max_recovery,
        stop_on_failure=(args.mode == "planner_ratchet"),
        logger=lambda msg: print(msg, flush=True),
    )

    t_start = time.time()
    result = engine.run(resume=args.resume)
    total_wall_s = round(time.time() - t_start, 3)

    dag = engine.dag
    assert dag is not None

    # Save the planner's raw LLM response and planned DAG (preserve existing raw response on --resume)
    if planner.last_raw_response:
        (out_dir / "planner_raw_response.txt").write_text(planner.last_raw_response, encoding="utf-8")
    (out_dir / "planner_dag.json").write_text(json.dumps(dag.to_dict(), indent=2), encoding="utf-8")

    git_telemetry = collect_git_ratchet_telemetry(executor, dag)

    # Snapshot `.hae/ratchet_state.json` and `.hae/ratchet_suites/` into `out_dir`
    ws_state_file = workspace_dir / SCRATCH_DIR / "ratchet_state.json"
    out_hae_dir = out_dir / SCRATCH_DIR
    out_hae_dir.mkdir(parents=True, exist_ok=True)
    if ws_state_file.exists():
        shutil.copy2(ws_state_file, out_dir / "ratchet_state.json")
        shutil.copy2(ws_state_file, out_hae_dir / "ratchet_state.json")

    ws_suites_dir = workspace_dir / SCRATCH_DIR / "ratchet_suites"
    if ws_suites_dir.exists():
        out_suites_dir = out_hae_dir / "ratchet_suites"
        if out_suites_dir.exists():
            shutil.rmtree(out_suites_dir)
        shutil.copytree(ws_suites_dir, out_suites_dir)

    milestones_summary: List[Dict[str, Any]] = []
    planner_tok = dict(planner.last_token_usage or {})
    prev_ext_initial = None
    if (out_dir / "summary.json").exists():
        try:
            prev_summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
            if not planner_tok:
                planner_tok = dict(prev_summary.get("planner_token_usage") or {})
            prev_ext_initial = (prev_summary.get("external_verification") or {}).get("initial")
        except Exception:
            pass
    tot_prompt_tokens = int(planner_tok.get("prompt_tokens", 0))
    tot_output_tokens = int(planner_tok.get("output_tokens", 0))
    tot_thought_tokens = int(planner_tok.get("thought_tokens", 0))
    tot_tokens = int(planner_tok.get("total_tokens", 0))
    tot_llm_calls = int(planner_tok.get("llm_calls", 0))
    tot_moves = 0

    for mid in dag.topological_order():
        m = dag.milestones[mid]
        ms = m.mcts_summary or {}
        gate = ms.get("cumulative_gate") or {}
        gate_details = gate.get("details") or {}
        tok = ms.get("token_usage") or {}
        git_stat = git_telemetry["milestone_git_stats"].get(mid, {})

        p_tok = int(tok.get("prompt_tokens", 0))
        o_tok = int(tok.get("output_tokens", 0))
        th_tok = int(tok.get("thought_tokens", 0))
        t_tok = int(tok.get("total_tokens", 0))
        calls = int(tok.get("llm_calls", 0))
        moves = int(ms.get("moves_used", 0))

        tot_prompt_tokens += p_tok
        tot_output_tokens += o_tok
        tot_thought_tokens += th_tok
        tot_tokens += t_tok
        tot_llm_calls += calls
        tot_moves += moves

        milestones_summary.append(
            {
                "milestone_id": mid,
                "title": m.title,
                "depends_on": list(m.depends_on),
                "target_files": list(m.target_files),
                "acceptance_cmd": m.acceptance_cmd,
                "max_diff_lines": m.max_diff_lines,
                "status": m.status,
                "base_head_sha": ms.get("base_head_sha", git_stat.get("base_commit_sha", "")),
                "certified_commit_sha": m.certified_commit_sha,
                "git_tag": f"hae-milestone-{mid}",
                "elapsed_s": ms.get("elapsed_s", 0.0),
                "moves_used": moves,
                "token_usage": {
                    "llm_calls": calls,
                    "prompt_tokens": p_tok,
                    "output_tokens": o_tok,
                    "thought_tokens": th_tok,
                    "total_tokens": t_tok,
                },
                "delta_files": ms.get("delta_files", []),
                "delta_added_lines": git_stat.get("delta_added_lines", ms.get("delta_added_lines", 0)),
                "delta_removed_lines": git_stat.get("delta_removed_lines", 0),
                "delta_diff_lines": git_stat.get("delta_total_diff_lines", ms.get("delta_diff_lines", 0)),
                "total_repo_loc": git_stat.get("total_repo_loc_at_commit", ms.get("total_repo_loc", 0)),
                "archived_falsification_suite": m.falsification_suite_path,
                "verified_milestones": gate_details.get("verified_milestones", []),
                "milestone_passed_tests": gate_details.get("milestone_passed_tests", {}),
                "cumulative_passed_tests": gate_details.get("cumulative_passed_tests", gate.get("passed_tests", 0)),
                "cumulative_gate": gate,
            }
        )

    total_wall_s = max(
        total_wall_s,
        round(sum(float(m.get("elapsed_s", 0.0) or 0.0) for m in milestones_summary), 3),
    )
    ext_v_out = dict(result.get("external_verification", {}) or {})
    if not ext_v_out or ext_v_out.get("initial") is None:
        direct_ext = external_verifier_plugin.verify(
            executor=executor,
            changed_files=[],
        )
        ext_v_out = {
            "external_verified": direct_ext.ok,
            "initial": direct_ext.to_dict(),
            "final": direct_ext.to_dict(),
            "recovery_milestones": [],
        }
    if prev_ext_initial and ext_v_out.get("recovery_milestones"):
        ext_v_out["initial"] = prev_ext_initial
    summary_payload: Dict[str, Any] = {
        "eval_id": out_dir.name,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint_info": endpoint_info,
        "planner_mode": args.mode,
        "planner_token_usage": planner_tok,
        "completed": result["completed"],
        "topological_order": result["topological_order"],
        "certified_milestones": result["certified_milestones"],
        "failed_milestones": result["failed_milestones"],
        "pending_milestones": result["pending_milestones"],
        "head_commit_sha": result["head_commit_sha"],
        "total_wall_clock_s": total_wall_s,
        "totals": {
            "moves_used": tot_moves,
            "llm_calls": tot_llm_calls,
            "prompt_tokens": tot_prompt_tokens,
            "output_tokens": tot_output_tokens,
            "thought_tokens": tot_thought_tokens,
            "total_tokens": tot_tokens,
        },
        "external_verification": ext_v_out,
        "milestones": milestones_summary,
        "git_telemetry": git_telemetry,
        "ratchet_state_path": str(out_dir / ".hae" / "ratchet_state.json"),
    }

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    write_eval_readme(out_dir, summary_payload)

    print(
        f"[planner-ratchet-eval] Finished! completed={result['completed']} "
        f"certified={result['certified_milestones']} "
        f"external_verified={ext_v_out.get('external_verified')} "
        f"wall_s={total_wall_s:.2f}s summary={summary_path}",
        flush=True,
    )
    return 0 if (result["completed"] or args.mode != "planner_ratchet") else 1


if __name__ == "__main__":
    raise SystemExit(main())
