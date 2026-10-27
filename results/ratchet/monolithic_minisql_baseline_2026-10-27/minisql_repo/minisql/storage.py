import copy
from .ast_nodes import *
from .parser import parse_sql

def coerce(value, dtype):
    if value is None: return None
    dt = (dtype or 'TEXT').upper()
    if dt in ('INT','INTEGER'):
        if isinstance(value, bool): return int(value)
        try: return int(value)
        except: 
            try: return int(float(value))
            except: return value
    if dt in ('REAL','FLOAT'):
        try: return float(value)
        except: return value
    if dt in ('BOOL','BOOLEAN'):
        if isinstance(value, str):
            if value.lower() in ('true','1'): return True
            if value.lower() in ('false','0'): return False
        return bool(value) if value is not None else None
    return value

class ColumnDef:
    def __init__(self, spec):
        self.spec = spec
        self.name = spec.name
        self.data_type = spec.data_type

class Table:
    def __init__(self, name, columns):
        self.name = name
        self.columns = columns  # list ColumnSpec
        self.rows = []  # list dict col->value
        self.indexes = {}  # name -> (col, dict value->row indices)
    def colnames(self):
        return [c.name for c in self.columns]
    def get_col(self, name):
        for c in self.columns:
            if c.name == name:
                return c
        return None

class Catalog:
    def __init__(self):
        self.tables = {}
    def get_table(self, name):
        return self.tables.get(name)
    def create_table(self, stmt):
        if stmt.table_name in self.tables:
            if stmt.if_not_exists:
                return
            raise MiniSQLError("Table exists")
        self.tables[stmt.table_name] = Table(stmt.table_name, list(stmt.columns))
    def drop_table(self, name, if_exists):
        if name not in self.tables:
            if if_exists: return
            raise MiniSQLError("No such table")
        del self.tables[name]

def eval_expr(expr, row_ctx, params=None):
    if params is None: params=[]
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, ParamPlaceholder):
        if expr.index >= len(params):
            raise MiniSQLError("Parameter count mismatch")
        return params[expr.index]
    if isinstance(expr, ColumnRef):
        # row_ctx may be dict with qualified/unqualified
        if expr.table:
            key = f"{expr.table}.{expr.name}"
            if key in row_ctx: return row_ctx[key]
        if expr.name in row_ctx: return row_ctx[expr.name]
        # search
        for k,v in row_ctx.items():
            if k.split('.')[-1]==expr.name:
                return v
        raise MiniSQLError(f"Unknown column {expr.name}")
    if isinstance(expr, StarRef):
        return None
    if isinstance(expr, UnaryExpr):
        v = eval_expr(expr.operand, row_ctx, params)
        if expr.op == '-':
            if v is None: return None
            return -v
        if expr.op == '+':
            if v is None: return None
            return +v
        if expr.op == 'NOT':
            if v is None: return None
            return not is_truthy(v)
        return v
    if isinstance(expr, BinaryExpr):
        op=expr.op
        if op == '||':
            l=eval_expr(expr.left,row_ctx,params); r=eval_expr(expr.right,row_ctx,params)
            if l is None or r is None: return None
            return str(l)+str(r)
        if op in ('+','-','*','/','%'):
            l=eval_expr(expr.left,row_ctx,params); r=eval_expr(expr.right,row_ctx,params)
            if l is None or r is None: return None
            if op=='/' and r==0: raise MiniSQLError("Division by zero")
            if op=='%' and r==0: raise MiniSQLError("Division by zero")
            if op=='+': return l+r
            if op=='-': return l-r
            if op=='*': return l*r
            if op=='/': 
                # preserve int? 
                return l/r
            if op=='%': return l%r
        if op in ('AND',):
            l=eval_expr(expr.left,row_ctx,params); r=eval_expr(expr.right,row_ctx,params)
            lt=is_truthy(l); rt=is_truthy(r)
            if lt is False or rt is False: return False
            if lt is None or rt is None: return None
            return True
        if op in ('OR',):
            l=eval_expr(expr.left,row_ctx,params); r=eval_expr(expr.right,row_ctx,params)
            lt=is_truthy(l); rt=is_truthy(r)
            if lt is True or rt is True: return True
            if lt is None or rt is None: return None
            return False
        if op in ('=','==','!=','<>','<','<=','>','>='):
            l=eval_expr(expr.left,row_ctx,params); r=eval_expr(expr.right,row_ctx,params)
            if l is None or r is None: return None
            if op in ('=','=='): return l==r
            if op in ('!=','<>'): return l!=r
            if op=='<': return l<r
            if op=='<=': return l<=r
            if op=='>': return l>r
            if op=='>=': return l>=r
    if isinstance(expr, IsNullExpr):
        v=eval_expr(expr.expr,row_ctx,params)
        res = v is None
        if expr.negated: res = not res
        return res
    if isinstance(expr, InExpr):
        v=eval_expr(expr.expr,row_ctx,params)
        vals=[eval_expr(x,row_ctx,params) for x in expr.items]
        if v is None: return None
        res = v in vals
        # if any null in vals and not found -> None? SQL: x IN (..., NULL) unknown if no match. Implement simple: if not res and any(x is None for x in vals): return None
        if not res and any(x is None for x in vals):
            res = None
        if expr.negated:
            if res is None: return None
            return not res
        return res
    if isinstance(expr, BetweenExpr):
        v=eval_expr(expr.expr,row_ctx,params)
        lo=eval_expr(expr.low,row_ctx,params)
        hi=eval_expr(expr.high,row_ctx,params)
        if v is None or lo is None or hi is None: return None
        res = lo <= v <= hi
        if expr.negated: return not res
        return res
    if isinstance(expr, LikeExpr):
        v=eval_expr(expr.expr,row_ctx,params)
        pat=eval_expr(expr.pattern,row_ctx,params)
        if v is None or pat is None: return None
        # case-insensitive ASCII like with % _
        import fnmatch
        # convert SQL like to fnmatch? % -> *, _ -> ?
        p = str(pat).replace('%','*').replace('_','?')
        # case-insensitive
        res = fnmatch.fnmatchcase(str(v).lower(), p.lower())
        if expr.negated: return not res
        return res
    if isinstance(expr, CaseExpr):
        for cond,res in expr.when_thens:
            if is_truthy(eval_expr(cond,row_ctx,params)):
                return eval_expr(res,row_ctx,params)
        if expr.else_expr is not None:
            return eval_expr(expr.else_expr,row_ctx,params)
        return None
    if isinstance(expr, FuncCall):
        name=expr.name.upper()
        if name=='COALESCE':
            for a in expr.args:
                v=eval_expr(a,row_ctx,params)
                if v is not None: return v
            return None
        if name=='IFNULL':
            a=eval_expr(expr.args[0],row_ctx,params)
            if a is not None: return a
            return eval_expr(expr.args[1],row_ctx,params)
        if name=='NULLIF':
            a=eval_expr(expr.args[0],row_ctx,params)
            b=eval_expr(expr.args[1],row_ctx,params)
            if a==b: return None
            return a
        if name=='UPPER':
            v=eval_expr(expr.args[0],row_ctx,params)
            return None if v is None else str(v).upper()
        if name=='LOWER':
            v=eval_expr(expr.args[0],row_ctx,params)
            return None if v is None else str(v).lower()
        if name=='LENGTH':
            v=eval_expr(expr.args[0],row_ctx,params)
            return None if v is None else len(str(v))
        if name=='ABS':
            v=eval_expr(expr.args[0],row_ctx,params)
            return None if v is None else abs(v)
        if name=='ROUND':
            v=eval_expr(expr.args[0],row_ctx,params)
            if v is None: return None
            n=0
            if len(expr.args)>1:
                n=eval_expr(expr.args[1],row_ctx,params)
            return round(v, n)
        # aggregates handled in engine; if called here return None
        return None
    raise MiniSQLError("Cannot evaluate expression")

def is_truthy(val):
    if val is None: return None
    if isinstance(val, bool): return val
    if isinstance(val, (int,float)): return val != 0
    if isinstance(val, str): return len(val)>0
    return bool(val)
