from minisql.engine import Database

def test_escaped_quotes_comments():
    db = Database()
    db.execute('CREATE TABLE t (id INT, note TEXT)')
    db.execute_script("INSERT INTO t VALUES (1, 'It''s a ''quoted'' string -- not a comment'); INSERT INTO t VALUES (2, 'Second /* literal */ note');")
    rows = db.execute('SELECT * FROM t').rows
    assert rows == [(1, "It's a 'quoted' string -- not a comment"), (2, 'Second /* literal */ note')]

def test_block_comment_semicolon():
    db = Database()
    db.execute('CREATE TABLE t (id INT)')
    db.execute_script("/* a; b */ INSERT INTO t VALUES (1);")
    assert db.execute('SELECT * FROM t').rows == [(1,)]

def test_left_join_order():
    db = Database()
    db.execute('CREATE TABLE employees (id INT, ename TEXT, dept_id INT)')
    db.execute("INSERT INTO employees VALUES (1,'Ada',1),(2,'Ghost',NULL)")
    db.execute('CREATE TABLE departments (id INT, dname TEXT)')
    db.execute("INSERT INTO departments VALUES (1,'Eng')")
    rows = db.execute('SELECT e.ename, d.dname FROM employees e LEFT JOIN departments d ON e.dept_id = d.id ORDER BY e.id').rows
    assert rows == [('Ada','Eng'),('Ghost',None)]

def test_empty_statements():
    db = Database()
    db.execute('CREATE TABLE t (id INT)')
    db.execute_script(";; INSERT INTO t VALUES (1); ;")
    assert db.execute('SELECT * FROM t').rows == [(1,)]
