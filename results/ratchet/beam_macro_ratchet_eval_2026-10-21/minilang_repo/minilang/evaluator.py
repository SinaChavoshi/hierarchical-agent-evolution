from typing import Any, Dict, Optional, Union

from .ast_nodes import *
from .parser import parse


class EvalError(Exception):
    pass


class Environment:
    def __init__(self, parent: Optional["Environment"] = None, values: Optional[Dict[str, Any]] = None):
        self.parent = parent
        self.values = dict(values) if values else {}

    def define(self, name: str, value: Any) -> Any:
        self.values[name] = value
        return value

    def get(self, name: str) -> Any:
        if name in self.values:
            return self.values[name]
        if self.parent is not None:
            return self.parent.get(name)
        raise EvalError(f"undefined variable: {name}")


class _Return(Exception):
    def __init__(self, value):
        self.value = value


def _truthy(v):
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return bool(v)


def evaluate(src_or_node: Union[str, Any], env: Optional[Environment] = None) -> Any:
    if isinstance(src_or_node, str):
        node = parse(src_or_node)
    else:
        node = src_or_node
    if env is None:
        env = Environment()

    def eval_node(n, e):
        if isinstance(n, Program):
            last = None
            for s in n.statements:
                last = eval_stmt(s, e)
            return last
        if isinstance(n, Block):
            last = None
            for s in n.statements:
                last = eval_stmt(s, e)
            return last
        if isinstance(n, NumberLiteral):
            return n.value
        if isinstance(n, StringLiteral):
            return n.value
        if isinstance(n, Identifier):
            return e.get(n.name)
        if isinstance(n, UnaryOp):
            v = eval_node(n.operand, e)
            if n.op == "-":
                return -v
            if n.op == "+":
                return +v
            raise EvalError(f"unknown unary op {n.op}")
        if isinstance(n, BinaryOp):
            l = eval_node(n.left, e)
            r = eval_node(n.right, e)
            op = n.op
            if op == "+":
                return l + r
            if op == "-":
                return l - r
            if op == "*":
                return l * r
            if op == "/":
                if r == 0 or r == 0.0:
                    raise EvalError("division by zero")
                return l / r
            if op == "==":
                return l == r
            if op == "!=":
                return l != r
            if op == "<":
                return l < r
            if op == ">":
                return l > r
            if op == "<=":
                return l <= r
            if op == ">=":
                return l >= r
            raise EvalError(f"unknown binary op {op}")
        if isinstance(n, LetStmt):
            v = eval_node(n.expr, e)
            e.define(n.name, v)
            return v
        if isinstance(n, IfExpr):
            c = eval_node(n.cond, e)
            if _truthy(c):
                if isinstance(n.then_branch, (Block,)):
                    return eval_node(n.then_branch, e)
                return eval_node(n.then_branch, e)
            else:
                if n.else_branch is None:
                    return None
                return eval_node(n.else_branch, e)
        if isinstance(n, FnDef):
            closure = e
            def fn(*args):
                if len(args) != len(n.params):
                    raise EvalError("arity mismatch")
                child = Environment(parent=closure)
                for p, a in zip(n.params, args):
                    child.define(p, a)
                try:
                    return eval_node(n.body, child)
                except _Return as r:
                    return r.value
            if n.name:
                e.define(n.name, fn)
            # attach closure env for introspection
            fn.__closure_env__ = closure
            return fn
        if isinstance(n, CallExpr):
            callee = e.get(n.callee) if False else None
            # resolve function
            try:
                f = e.get(n.callee)
            except EvalError:
                raise EvalError(f"undefined function: {n.callee}")
            if not callable(f):
                raise EvalError(f"undefined function: {n.callee}")
            args = [eval_node(a, e) for a in n.args]
            # arity check handled in fn wrapper; but for safety:
            return f(*args)
        if isinstance(n, ReturnStmt):
            v = eval_node(n.expr, e)
            raise _Return(v)
        raise EvalError(f"cannot evaluate {type(n)}")

    def eval_stmt(s, e):
        if isinstance(s, ReturnStmt):
            v = eval_node(s.expr, e)
            raise _Return(v)
        if isinstance(s, FnDef):
            return eval_node(s, e)
        if isinstance(s, LetStmt):
            return eval_node(s, e)
        if isinstance(s, IfExpr):
            return eval_node(s, e)
        if isinstance(s, (Block, Program)):
            return eval_node(s, e)
        # expression statement
        return eval_node(s, e)

    try:
        return eval_node(node, env)
    except _Return as r:
        return r.value
