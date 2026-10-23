from minisql import MiniSQLError, QueryResult
from minisql.ast_nodes import Literal, ColumnRef, SelectStmt, SelectItem
from minisql.lexer import tokenize_sql, Token


def test_queryresult_basics():
    qr = QueryResult(columns=['a'], rows=[(1,), (2,)], rowcount=2)
    assert len(qr) == 2
    assert list(qr) == [(1,), (2,)]
    assert qr.to_dicts() == [{'a': 1}, {'a': 2}]
    assert qr.scalar() == 1


def test_ast_dataclass_fields():
    lr = Literal(5)
    assert lr.value == 5
    cr = ColumnRef('x', table='t')
    assert cr.name == 'x' and cr.table == 't'
    ss = SelectStmt(select_items=[SelectItem(expr=ColumnRef('a'))])
    assert ss.offset == 0 and ss.joins == []


def test_lexer_operators_and_keywords():
    toks = tokenize_sql('SELECT a == b, c != d, e <> f, g <= h, i >= j, k || l')
    types = [t.type for t in toks]
    assert 'KEYWORD' in types
    ops = [t.value for t in toks if t.type == 'OP']
    assert '==' in ops and '!=' in ops and '<>' in ops and '<=' in ops and '>=' in ops and '||' in ops


def test_lexer_strings_comments_placeholders():
    toks = tokenize_sql("SELECT 'it''s', ? -- line\n/* block */ , `id`, \"col\"")
    vals = [t.value for t in toks]
    assert "it's" in vals
    params = [t for t in toks if t.type == 'PARAM']
    assert len(params) == 1
    idents = [t.value for t in toks if t.type == 'IDENT']
    assert 'id' in idents and 'col' in idents


def test_lexer_errors():
    try:
        tokenize_sql("SELECT 'unterminated")
        assert False
    except MiniSQLError:
        pass
    try:
        tokenize_sql("SELECT /* unterminated")
        assert False
    except MiniSQLError:
        pass
    try:
        tokenize_sql("SELECT @")
        assert False
    except MiniSQLError:
        pass
