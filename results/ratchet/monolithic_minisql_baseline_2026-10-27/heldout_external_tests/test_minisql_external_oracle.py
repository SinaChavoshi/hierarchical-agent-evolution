"""Strictly Held-Out External Test Suite & SQLite3 Differential Oracle for `minisql`.

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
