from minisql import Database, MiniSQLError
import pytest

def test_drop_then_select():
    db = Database()
    db.execute("CREATE TABLE t1 (id INT)")
    db.execute("DROP TABLE t1")
    with pytest.raises(MiniSQLError):
        db.execute("SELECT * FROM t1")

def test_if_not_exists_no_overwrite():
    db = Database()
    db.execute("CREATE TABLE t1 (id INT)")
    db.execute("INSERT INTO t1 VALUES (1)")
    db.execute("CREATE TABLE IF NOT EXISTS t1 (id INT, v TEXT)")
    assert db.execute("SELECT COUNT(*) FROM t1").scalar() == 1

def test_div_zero():
    db = Database()
    db.execute("CREATE TABLE t (a INT, b INT)")
    db.execute("INSERT INTO t VALUES (1,0)")
    with pytest.raises(MiniSQLError):
        db.execute("SELECT a/b FROM t")

def test_agg_groupby():
    db = Database()
    db.execute("CREATE TABLE sales (region TEXT, rep TEXT, amount INT)")
    db.execute("INSERT INTO sales VALUES ('a','r1',10),('a','r1',20),('b','r2',5)")
    r = db.execute("SELECT region, COUNT(*), COUNT(amount), COUNT(DISTINCT rep) FROM sales GROUP BY region ORDER BY region")
    assert len(r.rows) == 2

def test_drop_if_exists_missing():
    db = Database()
    db.execute("DROP TABLE IF EXISTS missing")
