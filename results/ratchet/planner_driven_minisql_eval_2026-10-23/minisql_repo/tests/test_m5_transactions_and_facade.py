from minisql.engine import Database
from minisql import MiniSQLError

def test_execute():
    db = Database()
    db.execute("CREATE TABLE t (a INT);")
    r = db.execute("INSERT INTO t VALUES (1);")
    assert r.rowcount == 1
    r = db.execute("SELECT * FROM t;")
    assert len(r.rows) == 1

def test_execute_script():
    db = Database()
    res = db.execute_script("CREATE TABLE t (a INT); INSERT INTO t VALUES (1),(2); SELECT * FROM t;")
    assert len(res) == 3
    assert len(res[2].rows) == 2

def test_transactions():
    db = Database()
    db.execute("CREATE TABLE t (a INT);")
    db.execute("BEGIN;")
    db.execute("INSERT INTO t VALUES (1);")
    db.execute("ROLLBACK;")
    r = db.execute("SELECT * FROM t;")
    assert len(r.rows) == 0
    # errors
    try:
        db.execute("COMMIT;")
        assert False
    except MiniSQLError:
        pass
    try:
        db.execute("BEGIN;")
        db.execute("BEGIN;")
        assert False
    except MiniSQLError:
        pass

def test_savepoints():
    db = Database()
    db.execute("CREATE TABLE t (a INT);")
    db.execute("BEGIN;")
    db.execute("INSERT INTO t VALUES (1);")
    db.execute("SAVEPOINT sp1;")
    db.execute("INSERT INTO t VALUES (2);")
    db.execute("ROLLBACK TO SAVEPOINT sp1;")
    r = db.execute("SELECT * FROM t;")
    assert len(r.rows) == 1
    # unknown
    try:
        db.execute("ROLLBACK TO SAVEPOINT nope;")
        assert False
    except MiniSQLError:
        pass
    db.execute("RELEASE SAVEPOINT sp1;")
    try:
        db.execute("ROLLBACK TO SAVEPOINT sp1;")
        assert False
    except MiniSQLError:
        pass

def test_statement_atomicity():
    db = Database()
    db.execute("CREATE TABLE t (a INT UNIQUE);")
    db.execute("INSERT INTO t VALUES (1);")
    try:
        db.execute("INSERT INTO t VALUES (2),(1);")
        assert False
    except MiniSQLError:
        pass
    r = db.execute("SELECT * FROM t;")
    assert len(r.rows) == 1
