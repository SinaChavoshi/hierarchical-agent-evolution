import pytest
from minisql import Database, MiniSQLError, QueryResult

def test_basic():
    db = Database()
    db.execute('CREATE TABLE t (id INT, name TEXT)')
    db.execute('INSERT INTO t VALUES (1, \'a\')')
    r = db.execute('SELECT * FROM t')
    assert r.to_dicts() == [{'id':1,'name':'a'}]
    assert r.scalar() == 1
    assert len(r) == 1

def test_placeholders_reset():
    from minisql.parser import parse_statements
    stmts = parse_statements('SELECT ?; SELECT ?')
    from minisql.ast_nodes import ParamPlaceholder
    assert stmts[0].select_items[0].expr.index == 0
    assert stmts[1].select_items[0].expr.index == 0

def test_default_negative():
    db = Database()
    db.execute('CREATE TABLE t (id INT, x INT DEFAULT -5)')
    db.execute('INSERT INTO t (id) VALUES (1)')
    assert db.execute('SELECT x FROM t').scalar() == -5

def test_lexer_unterminated():
    from minisql.lexer import tokenize_sql
    with pytest.raises(MiniSQLError):
        tokenize_sql("'unterminated")
    with pytest.raises(MiniSQLError):
        tokenize_sql("/* unterminated")

def test_case_insensitive_identifiers():
    db = Database()
    db.execute('CREATE TABLE "My Table" (`Col Name` INT)')
    db.execute('INSERT INTO "My Table" (`Col Name`) VALUES (7)')
    assert db.execute('SELECT `Col Name` FROM "My Table"').scalar() == 7

if __name__ == '__main__':
    pytest.main(['-q', __file__])
