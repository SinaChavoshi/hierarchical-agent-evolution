from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, Dict

class MiniSQLError(Exception):
    pass

@dataclass
class QueryResult:
    columns: List[str] = field(default_factory=list)
    rows: List[Tuple[Any, ...]] = field(default_factory=list)
    rowcount: int = 0
    def to_dicts(self):
        return [dict(zip(self.columns, row)) for row in self.rows]
    def scalar(self):
        if self.rows and len(self.rows[0]) > 0:
            return self.rows[0][0]
        return None
    def __iter__(self):
        return iter(self.rows)
    def __len__(self):
        return len(self.rows)

@dataclass
class Literal:
    value: Any = None

@dataclass
class ParamPlaceholder:
    index: int = 0

@dataclass
class ColumnRef:
    name: str
    table: Optional[str] = None

@dataclass
class StarRef:
    table: Optional[str] = None

@dataclass
class UnaryExpr:
    op: str
    operand: Any

@dataclass
class BinaryExpr:
    op: str
    left: Any
    right: Any

@dataclass
class IsNullExpr:
    expr: Any
    negated: bool = False

@dataclass
class InExpr:
    expr: Any
    items: List[Any]
    negated: bool = False

@dataclass
class BetweenExpr:
    expr: Any
    low: Any
    high: Any
    negated: bool = False

@dataclass
class LikeExpr:
    expr: Any
    pattern: Any
    negated: bool = False

@dataclass
class CaseExpr:
    when_thens: List[Tuple[Any, Any]]
    else_expr: Any = None

@dataclass
class FuncCall:
    name: str
    args: List[Any]
    distinct: bool = False

@dataclass
class ColumnSpec:
    name: str
    data_type: str = 'TEXT'
    primary_key: bool = False
    not_null: bool = False
    unique: bool = False
    default: Any = None
    has_default: bool = False

@dataclass
class SelectItem:
    expr: Any
    alias: Optional[str] = None

@dataclass
class JoinClause:
    join_type: str
    table_name: str
    alias: Optional[str] = None
    on_expr: Any = None

@dataclass
class OrderByItem:
    expr: Any
    asc: bool = True

@dataclass
class CreateTableStmt:
    table_name: str
    columns: List[ColumnSpec]
    if_not_exists: bool = False

@dataclass
class DropTableStmt:
    table_name: str
    if_exists: bool = False

@dataclass
class CreateIndexStmt:
    index_name: str
    table_name: str
    column_name: str
    if_not_exists: bool = False

@dataclass
class InsertStmt:
    table_name: str
    columns: Optional[List[str]]
    values_rows: List[List[Any]]

@dataclass
class UpdateStmt:
    table_name: str
    assignments: List[Tuple[str, Any]]
    where: Any = None

@dataclass
class DeleteStmt:
    table_name: str
    where: Any = None

@dataclass
class SelectStmt:
    select_items: List[SelectItem]
    from_table: Optional[str] = None
    from_alias: Optional[str] = None
    distinct: bool = False
    joins: List[JoinClause] = field(default_factory=list)
    where: Any = None
    group_by: List[Any] = field(default_factory=list)
    having: Any = None
    order_by: List[OrderByItem] = field(default_factory=list)
    limit: Optional[int] = None
    offset: int = 0

@dataclass
class TransactionStmt:
    action: str
    savepoint_name: Optional[str] = None
