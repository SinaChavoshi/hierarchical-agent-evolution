from minilang.parser import parse, ParseError
from minilang.ast_nodes import *

def test_prec():
    p = parse("1 + 2 * 3 == 7")
    assert isinstance(p.statements[0], BinaryOp)

def test_unary():
    p = parse("-(2 + 3) * 4")
    assert isinstance(p.statements[0], BinaryOp)

def test_let_if_fn():
    p = parse("let x = 5; if (x) { return x } else { return 0 }")
    assert isinstance(p.statements[0], LetStmt)
    p2 = parse("fn f(a, b) { return a + b }")
    assert isinstance(p2.statements[0], FnDef)
    p3 = parse("f(1, 2)")
    assert isinstance(p3.statements[0], CallExpr)

def test_parse_error():
    try:
        parse("let = 5")
        assert False
    except ParseError:
        pass
