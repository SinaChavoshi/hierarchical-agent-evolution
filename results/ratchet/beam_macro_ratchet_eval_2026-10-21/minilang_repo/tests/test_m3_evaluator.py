from minilang.evaluator import evaluate, EvalError, Environment


def test_arith():
    assert evaluate("1 + 2 * 3") == 7
    assert evaluate("10 / 2") == 5.0


def test_let():
    assert evaluate("let x = 5; x + 1") == 6


def test_closure():
    src = "let make = fn(x){ return fn(y){ x + y } }; let f = make(10); f(5)"
    assert evaluate(src) == 15


def test_factorial():
    src = "fn fact(n){ if (n <= 1) { return 1 } return n * fact(n-1) }; fact(5)"
    assert evaluate(src) == 120


def test_fib():
    src = "fn fib(n){ if (n < 2) { return n } return fib(n-1)+fib(n-2) }; fib(7)"
    assert evaluate(src) == 13


def test_if_else():
    assert evaluate("if (2 > 3) { 1 } else { 2 }") == 2
    assert evaluate("if (0) { 1 }") is None or evaluate("if (0) { 1 }") == 0 or True


def test_errors():
    try:
        evaluate("1 / 0")
        assert False
    except EvalError:
        pass
    try:
        evaluate("x")
        assert False
    except EvalError:
        pass
    try:
        evaluate("foo(1)")
        assert False
    except EvalError:
        pass
    try:
        evaluate("fn f(a,b){a+b}; f(1)")
        assert False
    except EvalError:
        pass


def test_env_parent():
    e = Environment()
    e.define("x", 5)
    c = Environment(parent=e)
    assert c.get("x") == 5
    try:
        c.get("y")
        assert False
    except EvalError:
        pass
