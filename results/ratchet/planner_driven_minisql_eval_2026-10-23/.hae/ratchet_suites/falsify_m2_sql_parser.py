import pytest
from minisql.parser import parse_sql, parse_statements
from minisql.ast_nodes import *

def test_all():
    # placeholders reset
    stmts = parse_statements("SELECT ?; SELECT ?")
    assert stmts[0].select_items[0].expr.index == 0
    assert stmts[1].select_items[0].expr.index == 0
    # defaults
    s = parse_sql("CREATE TABLE t (id INT PRIMARY KEY, n INT DEFAULT -3, s TEXT DEFAULT 'x')")
    assert s.columns[1].default == -3
    assert s.columns[2].default == 'x'
    # invalid
    try:
        parse_sql("SELECT 1 FROM")
        assert False
    except MiniSQLError:
        pass
    print("OK")

if __name__ == "__main__":
    test_all()
