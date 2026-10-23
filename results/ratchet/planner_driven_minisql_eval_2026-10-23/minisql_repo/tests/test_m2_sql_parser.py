from minisql.parser import parse_sql, parse_statements
from minisql.ast_nodes import *

def test_basic_select():
    s = parse_sql("SELECT DISTINCT a, b AS c FROM t AS x LEFT OUTER JOIN u ON x.id=u.id WHERE x.a=1 GROUP BY x.a HAVING COUNT(*)>1 ORDER BY x.a DESC LIMIT 5 OFFSET 2")
    assert s.distinct is True
    assert len(s.select_items)==2
    assert s.from_table=='t'
    assert s.joins[0].join_type=='LEFT'
    assert s.limit==5 and s.offset==2

def test_defaults():
    s = parse_sql("CREATE TABLE t (id INT PRIMARY KEY, n INT DEFAULT -3, s TEXT DEFAULT 'x')")
    assert s.columns[1].default==-3
    assert s.columns[2].default=='x'

def test_placeholders_reset():
    stmts = parse_statements("SELECT ?; SELECT ?")
    assert stmts[0].select_items[0].expr.index==0
    assert stmts[1].select_items[0].expr.index==0

def test_transactions():
    assert parse_sql("BEGIN TRANSACTION").action=='BEGIN'
    assert parse_sql("SAVEPOINT sp1").savepoint_name=='sp1'
    assert parse_sql("ROLLBACK TO SAVEPOINT sp1").action=='ROLLBACK_TO'

def test_precedence_case_func():
    s = parse_sql("SELECT CASE WHEN a=1 THEN 2 ELSE 3 END, COUNT(*) FROM t")
    assert isinstance(s.select_items[0].expr, CaseExpr)
    assert isinstance(s.select_items[1].expr, FuncCall)

def test_invalid():
    try:
        parse_sql("SELECT 1 FROM")
        assert False
    except MiniSQLError:
        pass
