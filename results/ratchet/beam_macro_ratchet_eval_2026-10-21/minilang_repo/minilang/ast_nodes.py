from dataclasses import dataclass
from typing import Any, List, Optional, Union


@dataclass
class NumberLiteral:
    value: Union[int, float]


@dataclass
class StringLiteral:
    value: str


Literal = NumberLiteral


@dataclass
class Identifier:
    name: str


@dataclass
class UnaryOp:
    op: str
    operand: Any


@dataclass
class BinaryOp:
    op: str
    left: Any
    right: Any


@dataclass
class LetStmt:
    name: str
    expr: Any


@dataclass
class ReturnStmt:
    expr: Any


@dataclass
class Block:
    statements: List[Any]


@dataclass
class IfExpr:
    cond: Any
    then_branch: Any
    else_branch: Optional[Any] = None


@dataclass
class FnDef:
    name: str
    params: List[str]
    body: Any


@dataclass
class CallExpr:
    callee: str
    args: List[Any]


@dataclass
class Program:
    statements: List[Any]
