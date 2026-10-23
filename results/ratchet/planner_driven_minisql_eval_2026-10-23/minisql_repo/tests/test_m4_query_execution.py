from minisql.parser import parse_sql
from minisql.engine import Database

def run(sqls):
    db = Database()
    res = None
    for s in sqls:
        stmts = [parse_sql(s)]
        for st in stmts:
            res = db.execute(st)
    return db, res

def test_insert_select():
    db, _ = run(["CREATE TABLE t (a INT, b TEXT);", "INSERT INTO t VALUES (1,'x'),(2,'y');"])
    stmt = parse_sql("SELECT * FROM t;")
    r = db.execute(stmt)
    assert len(r.rows) == 2

def test_join():
    db, _ = run(["CREATE TABLE a (id INT);", "CREATE TABLE b (aid INT, v TEXT);",
                  "INSERT INTO a VALUES (1),(2);", "INSERT INTO b VALUES (1,'x');"])
    stmt = parse_sql("SELECT * FROM a LEFT JOIN b ON a.id=b.aid;")
    r = db.execute(stmt)
    assert len(r.rows) == 2

def test_group():
    db, _ = run(["CREATE TABLE t (g TEXT, v INT);", "INSERT INTO t VALUES ('a',1),('a',2),('b',3);"])
    stmt = parse_sql("SELECT g, COUNT(*) FROM t GROUP BY g;")
    r = db.execute(stmt)
    assert len(r.rows) == 2

def test_having():
    db, _ = run(["CREATE TABLE t (g TEXT, v INT);", "INSERT INTO t VALUES ('a',1),('a',2),('b',3);"])
    stmt = parse_sql("SELECT g, SUM(v) AS s FROM t GROUP BY g HAVING s > 2;")
    r = db.execute(stmt)
    assert len(r.rows) == 2

def test_order_limit():
    db, _ = run(["CREATE TABLE t (v INT);", "INSERT INTO t VALUES (3),(1),(2);"])
    stmt = parse_sql("SELECT v FROM t ORDER BY v ASC LIMIT 2;")
    r = db.execute(stmt)
    assert [x[0] for x in r.rows] == [1,2]
