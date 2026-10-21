from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Union

from .ast_nodes import *
from .parser import parse
from .evaluator import EvalError, Environment


class OpCode(Enum):
    LOAD_CONST = "LOAD_CONST"
    LOAD_VAR = "LOAD_VAR"
    STORE_VAR = "STORE_VAR"
    UNARY_NEG = "UNARY_NEG"
    BINARY_ADD = "BINARY_ADD"
    BINARY_SUB = "BINARY_SUB"
    BINARY_MUL = "BINARY_MUL"
    BINARY_DIV = "BINARY_DIV"
    CMP_EQ = "CMP_EQ"
    CMP_NEQ = "CMP_NEQ"
    CMP_LT = "CMP_LT"
    CMP_GT = "CMP_GT"
    CMP_LE = "CMP_LE"
    CMP_GE = "CMP_GE"
    JUMP_IF_FALSE = "JUMP_IF_FALSE"
    JUMP = "JUMP"
    MAKE_FN = "MAKE_FN"
    CALL_FN = "CALL_FN"
    RETURN = "RETURN"
    HALT = "HALT"


@dataclass
class Instruction:
    op: Any
    arg: Any = None


@dataclass
class BytecodeChunk:
    instructions: List[Instruction]
    constants: List[Any] = field(default_factory=list)


def fold_constants(node):
    if isinstance(node, NumberLiteral) or isinstance(node, StringLiteral) or isinstance(node, Identifier):
        return node
    if isinstance(node, UnaryOp):
        operand = fold_constants(node.operand)
        if isinstance(operand, NumberLiteral):
            if node.op == "-":
                return NumberLiteral(-operand.value)
            if node.op == "+":
                return NumberLiteral(+operand.value)
        return UnaryOp(node.op, operand)
    if isinstance(node, BinaryOp):
        left = fold_constants(node.left)
        right = fold_constants(node.right)
        if isinstance(left, NumberLiteral) and isinstance(right, NumberLiteral):
            l, r = left.value, right.value
            if node.op == "+":
                return NumberLiteral(l + r)
            if node.op == "-":
                return NumberLiteral(l - r)
            if node.op == "*":
                return NumberLiteral(l * r)
            if node.op == "/":
                if r == 0 or r == 0.0:
                    return BinaryOp(node.op, left, right)
                return NumberLiteral(l / r)
            if node.op == "==":
                return NumberLiteral(l == r)
            if node.op == "!=":
                return NumberLiteral(l != r)
            if node.op == "<":
                return NumberLiteral(l < r)
            if node.op == ">":
                return NumberLiteral(l > r)
            if node.op == "<=":
                return NumberLiteral(l <= r)
            if node.op == ">=":
                return NumberLiteral(l >= r)
        return BinaryOp(node.op, left, right)
    if isinstance(node, LetStmt):
        return LetStmt(node.name, fold_constants(node.expr))
    if isinstance(node, ReturnStmt):
        return ReturnStmt(fold_constants(node.expr))
    if isinstance(node, Block):
        return Block([fold_constants(s) for s in node.statements])
    if isinstance(node, IfExpr):
        return IfExpr(fold_constants(node.cond), fold_constants(node.then_branch),
                      fold_constants(node.else_branch) if node.else_branch is not None else None)
    if isinstance(node, FnDef):
        return FnDef(node.name, list(node.params), fold_constants(node.body))
    if isinstance(node, CallExpr):
        return CallExpr(node.callee, [fold_constants(a) for a in node.args])
    if isinstance(node, Program):
        return Program([fold_constants(s) for s in node.statements])
    return node


class Compiler:
    def __init__(self):
        self.instructions: List[Instruction] = []
        self.constants: List[Any] = []

    def add_const(self, v):
        self.constants.append(v)
        return len(self.constants) - 1

    def emit(self, op, arg=None):
        self.instructions.append(Instruction(op, arg))

    def compile(self, node):
        if isinstance(node, Program):
            for s in node.statements:
                self.compile(s)
            self.emit(OpCode.HALT)
            return
        if isinstance(node, Block):
            for s in node.statements:
                self.compile(s)
            return
        if isinstance(node, NumberLiteral):
            idx = self.add_const(node.value)
            self.emit(OpCode.LOAD_CONST, idx)
            return
        if isinstance(node, StringLiteral):
            idx = self.add_const(node.value)
            self.emit(OpCode.LOAD_CONST, idx)
            return
        if isinstance(node, Identifier):
            self.emit(OpCode.LOAD_VAR, node.name)
            return
        if isinstance(node, UnaryOp):
            self.compile(node.operand)
            if node.op == "-":
                self.emit(OpCode.UNARY_NEG)
            return
        if isinstance(node, BinaryOp):
            self.compile(node.left)
            self.compile(node.right)
            opmap = {"+": OpCode.BINARY_ADD, "-": OpCode.BINARY_SUB, "*": OpCode.BINARY_MUL,
                     "/": OpCode.BINARY_DIV, "==": OpCode.CMP_EQ, "!=": OpCode.CMP_NEQ,
                     "<": OpCode.CMP_LT, ">": OpCode.CMP_GT, "<=": OpCode.CMP_LE, ">=": OpCode.CMP_GE}
            self.emit(opmap[node.op])
            return
        if isinstance(node, LetStmt):
            self.compile(node.expr)
            self.emit(OpCode.STORE_VAR, node.name)
            return
        if isinstance(node, IfExpr):
            self.compile(node.cond)
            jif = len(self.instructions)
            self.emit(OpCode.JUMP_IF_FALSE, None)
            self.compile(node.then_branch)
            if node.else_branch is not None:
                jmp = len(self.instructions)
                self.emit(OpCode.JUMP, None)
                self.instructions[jif].arg = len(self.instructions)
                self.compile(node.else_branch)
                self.instructions[jmp].arg = len(self.instructions)
            else:
                self.instructions[jif].arg = len(self.instructions)
            return
        if isinstance(node, FnDef):
            # compile body as separate chunk stored as constant? Use MAKE_FN with body instructions index.
            sub = Compiler()
            sub.compile(node.body)
            # store sub-chunk as constant tuple
            chunk = BytecodeChunk(sub.instructions, sub.constants)
            idx = self.add_const(("fn", node.params, chunk))
            self.emit(OpCode.MAKE_FN, (node.name, idx))
            if node.name:
                self.emit(OpCode.STORE_VAR, node.name)
            return
        if isinstance(node, CallExpr):
            for a in node.args:
                self.compile(a)
            self.emit(OpCode.CALL_FN, (node.callee, len(node.args)))
            return
        if isinstance(node, ReturnStmt):
            self.compile(node.expr)
            self.emit(OpCode.RETURN)
            return


def compile_expr(src_or_ast, fold=True):
    if isinstance(src_or_ast, str):
        node = parse(src_or_ast)
    else:
        node = src_or_ast
    if fold:
        node = fold_constants(node)
    c = Compiler()
    c.compile(node)
    if not any(i.op == OpCode.HALT for i in c.instructions):
        c.emit(OpCode.HALT)
    return BytecodeChunk(c.instructions, c.constants)


class VirtualMachine:
    def run(self, chunk_or_src, env=None):
        if isinstance(chunk_or_src, str):
            chunk = compile_expr(chunk_or_src)
        else:
            chunk = chunk_or_src
        if env is None:
            env = Environment()
        # frames: list of (instructions, constants, ip, env)
        # We'll execute with call frames
        # Prepare function objects
        def make_fn(params, body_chunk, closure_env):
            def fn(*args):
                if len(args) != len(params):
                    raise EvalError("arity mismatch")
                child = Environment(parent=closure_env)
                for p, a in zip(params, args):
                    child.define(p, a)
                # run body chunk in new frame
                return self._run_chunk(body_chunk, child)
            fn.__closure_env__ = closure_env
            return fn

        # We need frames stack accessible; use recursive helper
        frames_stack = []

        def run_chunk(chunk, e):
            ip = 0
            stack = []
            # env for this frame
            cur_env = e
            consts = chunk.constants
            instrs = chunk.instructions
            while ip < len(instrs):
                ins = instrs[ip]
                op = ins.op
                if op == OpCode.LOAD_CONST:
                    # resolve if fn tuple
                    v = consts[ins.arg]
                    if isinstance(v, tuple) and v and v[0] == "fn":
                        _, params, body_chunk = v
                        v = make_fn(params, body_chunk, cur_env)
                    stack.append(v)
                elif op == OpCode.LOAD_VAR:
                    stack.append(cur_env.get(ins.arg))
                elif op == OpCode.STORE_VAR:
                    v = stack.pop()
                    cur_env.define(ins.arg, v)
                elif op == OpCode.UNARY_NEG:
                    stack.append(-stack.pop())
                elif op == OpCode.BINARY_ADD:
                    b = stack.pop(); a = stack.pop(); stack.append(a + b)
                elif op == OpCode.BINARY_SUB:
                    b = stack.pop(); a = stack.pop(); stack.append(a - b)
                elif op == OpCode.BINARY_MUL:
                    b = stack.pop(); a = stack.pop(); stack.append(a * b)
                elif op == OpCode.BINARY_DIV:
                    b = stack.pop(); a = stack.pop()
                    if b == 0 or b == 0.0:
                        raise EvalError("division by zero")
                    stack.append(a / b)
                elif op == OpCode.CMP_EQ:
                    b = stack.pop(); a = stack.pop(); stack.append(a == b)
                elif op == OpCode.CMP_NEQ:
                    b = stack.pop(); a = stack.pop(); stack.append(a != b)
                elif op == OpCode.CMP_LT:
                    b = stack.pop(); a = stack.pop(); stack.append(a < b)
                elif op == OpCode.CMP_GT:
                    b = stack.pop(); a = stack.pop(); stack.append(a > b)
                elif op == OpCode.CMP_LE:
                    b = stack.pop(); a = stack.pop(); stack.append(a <= b)
                elif op == OpCode.CMP_GE:
                    b = stack.pop(); a = stack.pop(); stack.append(a >= b)
                elif op == OpCode.JUMP_IF_FALSE:
                    v = stack.pop()
                    truthy = v if not isinstance(v, (int, float, bool, type(None))) else (v != 0 if v is not None else False)
                    if isinstance(v, bool):
                        truthy = v
                    if not truthy:
                        ip = ins.arg
                        continue
                elif op == OpCode.JUMP:
                    ip = ins.arg
                    continue
                elif op == OpCode.MAKE_FN:
                    name, idx = ins.arg
                    v = consts[idx]
                    # v is tuple
                    _, params, body_chunk = v
                    fn = make_fn(params, body_chunk, cur_env)
                    if name:
                        cur_env.define(name, fn)
                    stack.append(fn)
                elif op == OpCode.CALL_FN:
                    callee_name, nargs = ins.arg
                    args = [stack.pop() for _ in range(nargs)][::-1]
                    # resolve function
                    try:
                        f = cur_env.get(callee_name)
                    except EvalError:
                        raise EvalError(f"undefined function: {callee_name}")
                    if not callable(f):
                        raise EvalError(f"undefined function: {callee_name}")
                    # if f is our fn, call runs chunk
                    res = f(*args)
                    stack.append(res)
                elif op == OpCode.RETURN:
                    return stack.pop() if stack else None
                elif op == OpCode.HALT:
                    break
                ip += 1
            return stack[-1] if stack else None

        self._run_chunk = run_chunk
        return run_chunk(chunk, env)
