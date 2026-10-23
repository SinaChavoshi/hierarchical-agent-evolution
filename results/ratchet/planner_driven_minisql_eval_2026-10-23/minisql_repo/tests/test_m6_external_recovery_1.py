from minisql import Database, MiniSQLError
import pytest

def test_ddl_if_not_exists_and_drop():
    db = Database()
    db.execute("CREATE TABLE t1 (id INTEGER PRIMARY KEY, v TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS t1 (id INTEGER, v TEXT)")
    db.execute("DROP TABLE IF EXISTS nope")
    db.execute("DROP TABLE t1")
    with pytest.raises(MiniSQLError):
        db.execute("SELECT * FROM t1")

def test_division_by_zero_atomicity():
    db = Database()
    db.execute("CREATE TABLE t (a INTEGER, b INTEGER)")
    db.execute("INSERT INTO t VALUES (1,0)")
    db.execute("BEGIN")
    with pytest.raises(MiniSQLError):
        db.execute("SELECT a/b FROM t")
    db.execute("ROLLBACK")
    assert db.execute("SELECT COUNT(*) FROM t").scalar() == 1

def test_aggregate_groupby():
    db = Database()
    db.execute("CREATE TABLE sales (region TEXT, rep TEXT, amount INTEGER)")
    db.execute("INSERT INTO sales VALUES ('a','r1',10),('a','r1',20),('b','r2',5)")
    r = db.execute("SELECT region, COUNT(*), COUNT(amount), COUNT(DISTINCT rep), SUM(amount), AVG(amount), MIN(amount), MAX(amount) FROM sales GROUP BY region ORDER BY region")
    assert len(r.rows) == 2

def test_drop_if_exists_noop():
    db = Database()
    db.execute("DROP TABLE IF EXISTS missing")
