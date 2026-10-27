def test_smoke():
    from minisql import Database
    db = Database()
    db.execute('CREATE TABLE t (id INT)')
    db.execute('INSERT INTO t VALUES (1)')
    assert db.execute('SELECT * FROM t').scalar() == 1
