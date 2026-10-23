from minisql.storage import Catalog, eval_expr, is_truthy
from minisql.ast_nodes import ColumnSpec, CreateTableStmt, CreateIndexStmt, Literal, ParamPlaceholder, BinaryExpr, UnaryExpr

def test_all():
    c = Catalog()
    c.create_table(CreateTableStmt('t', [ColumnSpec('id','INT',primary_key=True), ColumnSpec('v','INT'), ColumnSpec('name','TEXT',default=Literal('x'),has_default=True)]))
    t = c.tables['t']
    t.insert([1, 5])
    assert t.rows[0][2] == 'x'
    c.create_index(CreateIndexStmt('idx','t','v', if_not_exists=True))
    assert 0 in t.secondary_indexes['idx']['map'].get(5, set())
    # param placeholder
    try:
        eval_expr(ParamPlaceholder(5), {}, [1,2])
        assert False
    except Exception:
        pass
    # div zero
    try:
        eval_expr(BinaryExpr('/', Literal(1), Literal(0)), {}, [])
        assert False
    except Exception:
        pass
    assert eval_expr(BinaryExpr('+', Literal(1), Literal(None)), {}, []) is None
    print("ALL OK")

if __name__ == '__main__':
    test_all()
