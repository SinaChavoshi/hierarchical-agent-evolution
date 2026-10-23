from .ast_nodes import MiniSQLError, Literal, ParamPlaceholder, ColumnRef, UnaryExpr, BinaryExpr, IsNullExpr, InExpr, BetweenExpr, LikeExpr, CaseExpr, FuncCall, ColumnSpec

def is_truthy(val):
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val != ''
    return bool(val)

def _coerce(col, val):
    if val is None:
        return None
    dt = (col.data_type or 'TEXT').upper()
    if dt in ('INT','INTEGER'):
        if isinstance(val, bool):
            return int(val)
        if isinstance(val, (int,)):
            return val
        if isinstance(val, float):
            return int(val)
        try:
            return int(val)
        except (ValueError, TypeError):
            try:
                return int(float(val))
            except (ValueError, TypeError):
                return val
    if dt in ('REAL','FLOAT'):
        if isinstance(val, bool):
            return float(val)
        try:
            return float(val)
        except (ValueError, TypeError):
            return val
    if dt in ('TEXT','VARCHAR'):
        if isinstance(val, bool):
            return '1' if val else '0'
        if val is None:
            return None
        return str(val) if not isinstance(val, str) else val
    if dt in ('BOOL','BOOLEAN'):
        if isinstance(val, bool):
            return val
        if isinstance(val, (int,float)):
            return val != 0
        if isinstance(val, str):
            low = val.lower()
            if low in ('true','1','yes'):
                return True
            if low in ('false','0','no',''):
                return False
        return bool(val)
    return val

class ColumnDef:
    def __init__(self, spec):
        self.name = spec.name
        self.data_type = spec.data_type
        self.primary_key = spec.primary_key
        self.not_null = spec.not_null or spec.primary_key
        self.unique = spec.unique or spec.primary_key
        self.default = spec.default
        self.has_default = spec.has_default
        if self.primary_key:
            self.not_null = True
            self.unique = True

class Table:
    def __init__(self, name, columns):
        self.name = name
        self.columns = [ColumnDef(c) if not isinstance(c, ColumnDef) else c for c in columns]
        self.col_index = {c.name: i for i, c in enumerate(self.columns)}
        self.rows = []
        self.indexes = {}  # name -> {col: {value: set of row indices?}} we'll store dict value->list of row positions
        # secondary hash indexes: idx_name -> (col_name, dict value -> set of row indices)
        self.secondary_indexes = {}

    def _apply_defaults(self, values):
        # values: list aligned to columns or None entries for omitted
        out = list(values)
        for i, c in enumerate(self.columns):
            if i >= len(out) or out[i] is _OMITTED:
                if c.has_default:
                    d = c.default
                    if isinstance(d, Literal):
                        d = d.value
                    if hasattr(d, '__dataclass_fields__') or isinstance(d, (UnaryExpr, BinaryExpr, FuncCall, CaseExpr, ColumnRef, IsNullExpr, InExpr, BetweenExpr, LikeExpr)):
                        # expression
                        try:
                            d = eval_expr(d, {}, [])
                        except Exception:
                            pass
                    out_val = d
                else:
                    out_val = None
                if i >= len(out):
                    out.append(out_val)
                else:
                    out[i] = out_val
        # pad
        while len(out) < len(self.columns):
            out.append(None)
        return out

    def insert(self, values, columns=None):
        # build full row
        if columns is None:
            row = list(values)
            # pad with omitted
            row = row + [_OMITTED] * (len(self.columns) - len(row))
        else:
            row = [_OMITTED] * len(self.columns)
            for ci, colname in enumerate(columns):
                idx = self.col_index[colname]
                row[idx] = values[ci]
        row = self._apply_defaults(row)
        # coerce
        for i, c in enumerate(self.columns):
            row[i] = _coerce(c, row[i])
        # constraints
        self._check_constraints(row, None)
        # unique indexes check
        self._check_unique(row, None)
        self.rows.append(tuple(row))
        new_idx = len(self.rows) - 1
        # maintain secondary indexes
        for idx_name, (col_name,) in []:  # placeholder
            pass
        for idx_name, info in self.secondary_indexes.items():
            col = info['col']
            val = row[self.col_index[col]]
            info['map'].setdefault(val, set()).add(new_idx)
        return new_idx

    def _check_constraints(self, row, skip_idx):
        for i, c in enumerate(self.columns):
            val = row[i]
            if c.not_null and val is None:
                raise MiniSQLError(f"NOT NULL constraint failed: {c.name}")
            if c.unique and val is not None:
                for j, r in enumerate(self.rows):
                    if skip_idx is not None and j == skip_idx:
                        continue
                    if r[i] == val:
                        raise MiniSQLError(f"UNIQUE constraint failed: {c.name}")

    def _check_unique(self, row, skip_idx):
        self._check_constraints(row, skip_idx)

    def update(self, assignments, where=None, params=None):
        # assignments: list of (colname, expr)
        affected = 0
        new_rows = list(self.rows)
        for ri, r in enumerate(list(self.rows)):
            row_ctx = {self.columns[i].name: r[i] for i in range(len(self.columns))}
            row_ctx.update({'row': r})
            if where is not None:
                w = eval_expr(where, row_ctx, params or [])
                if not is_truthy(w):
                    continue
            # build new row
            new_row = list(r)
            for colname, expr in assignments:
                idx = self.col_index[colname]
                val = eval_expr(expr, row_ctx, params or [])
                new_row[idx] = _coerce(self.columns[idx], val)
            # constraints
            self._check_constraints(new_row, ri)
            # update secondary indexes: remove old
            for idx_name, info in self.secondary_indexes.items():
                col = info['col']
                old_val = r[self.col_index[col]]
                if old_val in info['map']:
                    info['map'][old_val].discard(ri)
            new_rows[ri] = tuple(new_row)
            # add new
            for idx_name, info in self.secondary_indexes.items():
                col = info['col']
                val = new_row[self.col_index[col]]
                info['map'].setdefault(val, set()).add(ri)
            affected += 1
        self.rows = new_rows
        return affected

    def delete(self, where=None, params=None):
        affected = 0
        keep = []
        for ri, r in enumerate(self.rows):
            row_ctx = {self.columns[i].name: r[i] for i in range(len(self.columns))}
            if where is not None:
                w = eval_expr(where, row_ctx, params or [])
                if not is_truthy(w):
                    keep.append(r)
                    continue
            # remove from indexes
            for idx_name, info in self.secondary_indexes.items():
                col = info['col']
                val = r[self.col_index[col]]
                if val in info['map']:
                    info['map'][val].discard(ri)
            affected += 1
        # rebuild row indices in indexes? We keep sets of old indices; simpler: rebuild indexes
        self.rows = [r for r in self.rows if r in keep] if False else None
        # redo cleanly
        self.rows = []
        kept_indices = []
        for ri, r in enumerate(list(self.rows) if self.rows is not None else []):
            pass
        # Actually redo properly:
        return affected

class Catalog:
    def __init__(self):
        self.tables = {}
        self.indexes = {}  # idx_name -> (table, col)

    def create_table(self, stmt):
        from .ast_nodes import CreateTableStmt
        if stmt.table_name in self.tables and not stmt.if_not_exists:
            raise MiniSQLError("table exists")
        if stmt.table_name in self.tables and stmt.if_not_exists:
            return
        cols = stmt.columns
        self.tables[stmt.table_name] = Table(stmt.table_name, cols)

    def drop_table(self, name, if_exists=False):
        if name in self.tables:
            del self.tables[name]
        elif not if_exists:
            raise MiniSQLError("no such table")

    def create_index(self, stmt):
        from .ast_nodes import CreateIndexStmt
        tbl = self.tables[stmt.table_name]
        if stmt.index_name in self.indexes or stmt.index_name in tbl.secondary_indexes:
            if stmt.if_not_exists:
                return
            raise MiniSQLError("index exists")
        tbl.secondary_indexes[stmt.index_name] = {'col': stmt.column_name, 'map': {}}
        self.indexes[stmt.index_name] = (stmt.table_name, stmt.column_name)
        # populate
        for ri, r in enumerate(tbl.rows):
            val = r[tbl.col_index[stmt.column_name]]
            tbl.secondary_indexes[stmt.index_name]['map'].setdefault(val, set()).add(ri)

_OMITTED = object()

# Fix delete method properly
def _delete_impl(self, where=None, params=None):
    affected = 0
    new_rows = []
    removed_positions = set()
    for ri, r in enumerate(self.rows):
        row_ctx = {self.columns[i].name: r[i] for i in range(len(self.columns))}
        if where is not None:
            w = eval_expr(where, row_ctx, params or [])
            if not is_truthy(w):
                new_rows.append(r)
                continue
        removed_positions.add(ri)
        affected += 1
    # rebuild indexes from scratch for remaining rows
    self.rows = new_rows
    # rebuild secondary indexes
    for idx_name, info in self.secondary_indexes.items():
        info['map'] = {}
        for ri, r in enumerate(self.rows):
            val = r[self.col_index[info['col']]]
            info['map'].setdefault(val, set()).add(ri)
    return affected

Table.delete = _delete_impl

# Fix insert omitted handling: redefine insert with proper omitted sentinel already used.

def eval_expr(expr, row_ctx, params):
    if expr is None:
        return None
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, ParamPlaceholder):
        if params is None or expr.index >= len(params):
            raise MiniSQLError("invalid param index")
        return params[expr.index]
    if isinstance(expr, ColumnRef):
        if expr.table:
            key = f"{expr.table}.{expr.name}"
            if key in row_ctx:
                return row_ctx[key]
            for k, v in row_ctx.items():
                if isinstance(k, str) and k.lower() == key.lower():
                    return v
        name = expr.name
        if name in row_ctx:
            return row_ctx[name]
        for k, v in row_ctx.items():
            if isinstance(k, str) and k.lower() == name.lower():
                return v
        return None
    if isinstance(expr, UnaryExpr):
        v = eval_expr(expr.operand, row_ctx, params)
        if expr.op == '-':
            if v is None:
                return None
            return -v
        if expr.op == '+':
            if v is None:
                return None
            return +v
        if expr.op.upper() in ('NOT',):
            t = is_truthy(v)
            if t is None:
                return None
            return not t
        return v
    if isinstance(expr, BinaryExpr):
        op = expr.op
        if op in ('AND','and'):
            l = is_truthy(eval_expr(expr.left, row_ctx, params))
            r = is_truthy(eval_expr(expr.right, row_ctx, params))
            if l is False or r is False:
                return False
            if l is None or r is None:
                return None
            return True
        if op in ('OR','or'):
            l = is_truthy(eval_expr(expr.left, row_ctx, params))
            r = is_truthy(eval_expr(expr.right, row_ctx, params))
            if l is True or r is True:
                return True
            if l is None or r is None:
                return None
            return False
        if op == '||':
            l = eval_expr(expr.left, row_ctx, params)
            r = eval_expr(expr.right, row_ctx, params)
            if l is None or r is None:
                return None
            return str(l) + str(r)
        l = eval_expr(expr.left, row_ctx, params)
        r = eval_expr(expr.right, row_ctx, params)
        if op in ('+','-','*','/','%'):
            if l is None or r is None:
                return None
            if op == '+':
                # if both str? concat not here; + numeric
                if isinstance(l, str) or isinstance(r, str):
                    # try numeric
                    pass
                return l + r
            if op == '-':
                return l - r
            if op == '*':
                return l * r
            if op == '/':
                if r == 0:
                    raise MiniSQLError("division by zero")
                return l / r if isinstance(l,float) or isinstance(r,float) else l // r if False else l / r
            if op == '%':
                if r == 0:
                    raise MiniSQLError("modulo by zero")
                return l % r
        if op in ('=','=='):
            if l is None or r is None:
                return None
            return l == r
        if op in ('!=','<>'):
            if l is None or r is None:
                return None
            return l != r
        if op == '<':
            if l is None or r is None:
                return None
            return l < r
        if op == '>':
            if l is None or r is None:
                return None
            return l > r
        if op == '<=':
            if l is None or r is None:
                return None
            return l <= r
        if op == '>=':
            if l is None or r is None:
                return None
            return l >= r
        # default
        return None
    if isinstance(expr, IsNullExpr):
        v = eval_expr(expr.expr, row_ctx, params)
        if expr.negated:
            return v is not None
        return v is None
    # fix IS NOT NULL
    if isinstance(expr, InExpr):
        v = eval_expr(expr.expr, row_ctx, params)
        items = [eval_expr(x, row_ctx, params) for x in expr.items]
        if v is None:
            return None
        if any(x is None for x in items):
            # if v in non-null items -> true; else if any null -> None; else false
            if v in [x for x in items if x is not None]:
                res = True
            else:
                res = None if any(x is None for x in items) else False
        else:
            res = v in items
        if expr.negated:
            if res is None:
                return None
            return not res
        return res
    if isinstance(expr, BetweenExpr):
        v = eval_expr(expr.expr, row_ctx, params)
        lo = eval_expr(expr.low, row_ctx, params)
        hi = eval_expr(expr.high, row_ctx, params)
        if v is None or lo is None or hi is None:
            res = None
        else:
            res = (lo <= v <= hi)
        if expr.negated:
            if res is None:
                return None
            return not res
        return res
    if isinstance(expr, LikeExpr):
        v = eval_expr(expr.expr, row_ctx, params)
        pat = eval_expr(expr.pattern, row_ctx, params)
        if v is None or pat is None:
            res = None
        else:
            res = _like(str(v), str(pat))
        if expr.negated:
            if res is None:
                return None
            return not res
        return res
    if isinstance(expr, CaseExpr):
        for w, t in expr.when_thens:
            wv = eval_expr(w, row_ctx, params)
            if is_truthy(wv) is True:
                return eval_expr(t, row_ctx, params)
        if expr.else_expr is not None:
            return eval_expr(expr.else_expr, row_ctx, params)
        return None
    if isinstance(expr, FuncCall):
        name = expr.name.upper()
        args = [eval_expr(a, row_ctx, params) for a in expr.args]
        if name == 'COALESCE':
            for a in args:
                if a is not None:
                    return a
            return None
        if name == 'IFNULL':
            return args[0] if args[0] is not None else (args[1] if len(args)>1 else None)
        if name == 'NULLIF':
            if len(args) >= 2 and args[0] == args[1]:
                return None
            return args[0] if args else None
        if name == 'UPPER':
            return None if args[0] is None else str(args[0]).upper()
        if name == 'LOWER':
            return None if args[0] is None else str(args[0]).lower()
        if name == 'LENGTH':
            return None if args[0] is None else len(str(args[0]))
        if name == 'ABS':
            return None if args[0] is None else abs(args[0])
        if name == 'ROUND':
            if args[0] is None:
                return None
            if len(args) > 1 and args[1] is not None:
                return round(args[0], int(args[1]))
            return round(args[0])
        return None

def _like(s, pat):
    # case-insensitive ASCII % and _
    s = s.lower()
    pat = pat.lower()
    # dynamic programming
    # convert
    # simple recursive with memo
    from functools import lru_cache
    @lru_cache(None)
    def match(i, j):
        if j == len(pat):
            return i == len(s)
        if pat[j] == '%':
            if match(i, j+1):
                return True
            if i < len(s) and match(i+1, j):
                return True
            return False
        if i < len(s) and (pat[j] == '_' or pat[j] == s[i]):
            return match(i+1, j+1)
        return False
    return match(0, 0)

# finish ROUND
