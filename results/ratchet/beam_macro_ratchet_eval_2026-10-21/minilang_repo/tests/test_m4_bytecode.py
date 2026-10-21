from minilang import compile_expr, VirtualMachine, evaluate, EvalError

def test_fold():
    a = compile_expr('2 + 3 * 4', fold=True)
    b = compile_expr('2 + 3 * 4', fold=False)
    assert len(a.instructions) < len(b.instructions)

def test_parity():
    srcs = ['2 + 3 * 4', 'let x = 5\nx * 2', 'if 1 < 2 { 10 } else { 20 }', 'fn f(n) { if n <= 1 { 1 } else { n * f(n-1) } } f(5)']
    for s in srcs:
        assert VirtualMachine().run(compile_expr(s)) == evaluate(s)

def test_errors():
    try:
        VirtualMachine().run(compile_expr('1 / 0'))
        assert False
    except EvalError:
        pass
    try:
        VirtualMachine().run(compile_expr('y + 1'))
        assert False
    except EvalError:
        pass
