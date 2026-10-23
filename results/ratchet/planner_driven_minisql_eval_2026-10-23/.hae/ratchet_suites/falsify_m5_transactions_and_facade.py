import warnings
from minisql import Database, MiniSQLError

def test_all():
    db = Database()
    db.execute("CREATE TABLE t (a INT UNIQUE);")
    db.execute("INSERT INTO t VALUES (1);")
    # quoted semicolon in execute_script
    res = db.execute_script("SELECT 'a;b' AS x; -- c; \n SELECT 2;")
    assert len(res) == 2
    # transaction errors
    for fn in [lambda: db.execute("COMMIT;"), lambda: db.execute("ROLLBACK;")]:
        try:
            fn(); assert False
        except MiniSQLError: pass
    try:
        db.execute("BEGIN;"); db.execute("BEGIN;"); assert False
    except MiniSQLError: pass
    db.execute("ROLLBACK;")
    # savepoint unknown
    db.execute("BEGIN;")
    try:
        db.execute("ROLLBACK TO SAVEPOINT nope;"); assert False
    except MiniSQLError: pass
    db.execute("ROLLBACK;")
    # statement atomicity
    try:
        db.execute("INSERT INTO t VALUES (2),(1);"); assert False
    except MiniSQLError: pass
    assert db.execute("SELECT * FROM t;").rowcount if False else len(db.execute("SELECT * FROM t;").rows) == 1
    # release then rollback to
    db.execute("BEGIN;")
    db.execute("SAVEPOINT sp;")
    db.execute("RELEASE SAVEPOINT sp;")
    try:
        db.execute("ROLLBACK TO SAVEPOINT sp;"); assert False
    except MiniSQLError: pass
    db.execute("ROLLBACK;")
    print("ALL OK")

test_all()
