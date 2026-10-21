from minilang.parser import parse, ParseError
from minilang.ast_nodes import *
from minilang.lexer import LexError

def check(cond, msg):
    assert cond, msg

# precedence
p = parse("1 + 2 * 3 == 7")
assert isinstance(p.statements[0], BinaryOp) and p.statements[0].op == "=="
assert p.statements[0].left.op == "+"
assert p.statements[0].left.right.op == "*"

# unary
p = parse("-(2 + 3) * 4")
assert p.statements[0].op == "*"
assert isinstance(p.statements[0].left, UnaryOp)

# chained comparisons left-assoc
p = parse("1 < 2 < 3")
assert p.statements[0].left.op == "<"

# if optional parens/else/else if/single expr
p = parse("if x { 1 } else if y { 2 } else 3")
assert isinstance(p.statements[0], IfExpr)
p = parse("if (x) 1 else 2")
assert isinstance(p.statements[0], IfExpr)

# fn stmt and let binding
p = parse("fn f(a, b) { return a + b }")
assert isinstance(p.statements[0], FnDef) and p.statements[0].name == "f"
p = parse("let g = fn() { return 1 }")
assert isinstance(p.statements[0], LetStmt) and isinstance(p.statements[0].expr, FnDef)

# calls zero/multi
p = parse("f() ; g(1, 2)")
assert isinstance(p.statements[0], CallExpr) and p.statements[0].args == []
assert p.statements[1].args == [NumberLiteral(1), NumberLiteral(2)]

# optional/trailing semis
p = parse("1; 2;")
assert len(p.statements) == 2

# parse error
try:
    parse("let = 5")
    raise AssertionError("no error")
except ParseError:
    pass

# lexer error propagates
try:
    parse('let x = "unterminated')
    raise AssertionError("no lex error")
except LexError:
    pass

print("ALL EDGE CASES PASS")
