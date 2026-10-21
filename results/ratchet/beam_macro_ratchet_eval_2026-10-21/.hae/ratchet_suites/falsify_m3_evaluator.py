from minilang.evaluator import evaluate, EvalError, Environment
from minilang.parser import parse

def test_parse_equiv():
    assert evaluate("1+2") == evaluate(parse("1+2"))

def test_div_zero():
    for src in ["1/0","1/0.0","1.0/0"]:
        try:
            evaluate(src); raise AssertionError("no error")
        except EvalError as e:
            assert "division" in str(e).lower()

def test_undef_var():
    try:
        evaluate("x"); raise AssertionError("no error")
    except EvalError as e:
        assert "undefined" in str(e).lower() and "variable" in str(e).lower()

def test_undef_fn():
    try:
        evaluate("foo(1)"); raise AssertionError("no error")
    except EvalError as e:
        assert "undefined" in str(e).lower() and "function" in str(e).lower()

def test_arity():
    try:
        evaluate("fn f(a,b){a+b}; f(1)"); raise AssertionError("no error")
    except EvalError as e:
        assert "arity" in str(e).lower()

def test_closure_capture():
    src = "let make = fn(x){ return fn(y){ x + y } }; let f = make(10); f(5)"
    assert evaluate(src) == 15

def test_if_missing_else():
    assert evaluate("if (0) { 1 }") is None or evaluate("if (0) { 1 }") == 0

def test_fact_fib():
    assert evaluate("fn fact(n){ if (n <= 1) { return 1 } return n * fact(n-1) }; fact(5)") == 120
    assert evaluate("fn fib(n){ if (n < 2) { return n } return fib(n-1)+fib(n-2) }; fib(7)") == 13

def test_env_parent():
    e = Environment(); e.define("x",5); c=Environment(parent=e); assert c.get("x")==5
    try:
        c.get("y"); raise AssertionError("no error")
    except EvalError: pass

if __name__ == "__main__":
    for name in [n for n in globals() if n.startswith("test_")]:
        globals()[name]()
    print("ALL PASS")
