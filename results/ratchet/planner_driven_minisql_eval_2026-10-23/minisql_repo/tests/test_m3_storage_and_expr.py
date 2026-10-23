from minisql.storage import Catalog, eval_expr, is_truthy
from minisql.ast_nodes import ColumnSpec, CreateTableStmt, CreateIndexStmt, Literal

def test_coercion_null():
    assert is_truthy(None) is None

def test_pk_notnull():
    c=Catalog()
    c.create_table(CreateTableStmt('t',[ColumnSpec('id','INT',primary_key=True)]))
    t=c.tables['t']
    try:
        t.insert([None])
        assert False
    except Exception:
        pass

def test_default_literal():
    c=Catalog()
    c.create_table(CreateTableStmt('t',[ColumnSpec('id','INT',primary_key=True),ColumnSpec('name','TEXT',default=Literal('x'),has_default=True)]))
    t=c.tables['t']
    t.insert([1])
    assert t.rows[0][1]=='x'

def test_index():
    c=Catalog()
    c.create_table(CreateTableStmt('t',[ColumnSpec('id','INT',primary_key=True),ColumnSpec('v','INT')]))
    t=c.tables['t']
    t.insert([1,5])
    c.create_index(CreateIndexStmt('idx','t','v'))
    assert 0 in t.secondary_indexes['idx']['map'].get(5,set())

def test_expr_null():
    assert eval_expr(__import__('minisql.ast_nodes',fromlist=['BinaryExpr']).BinaryExpr('+',Literal(1),Literal(None)),{},[]) is None
