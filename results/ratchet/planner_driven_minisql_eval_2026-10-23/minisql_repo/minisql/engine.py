import copy
from .ast_nodes import *
from .storage import Catalog, Table, eval_expr, is_truthy, _coerce
from .parser import parse_sql

class Database:
    def __init__(self):
        self.catalog = Catalog()
        self.tables = self.catalog.tables
        self._in_txn = False
        self._savepoints = []  # list of (name, snapshot)
        self._txn_snapshot = None

    def _snapshot(self):
        return copy.deepcopy(self.catalog)

    def _restore(self, snap):
        self.catalog = snap
        self.tables = self.catalog.tables

    def _split_statements(self, sql_script):
        # reuse execute_script splitter logic
        stmts = []
        buf = []
        i = 0
        n = len(sql_script)
        in_single = False
        in_double = False
        in_line_comment = False
        in_block_comment = False
        while i < n:
            c = sql_script[i]
            if in_line_comment:
                buf.append(c)
                if c == '\n':
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                buf.append(c)
                if c == '*' and i+1 < n and sql_script[i+1] == '/':
                    buf.append(sql_script[i+1])
                    i += 2
                    in_block_comment = False
                    continue
                i += 1
                continue
            if in_single:
                buf.append(c)
                if c == "'":
                    if i+1 < n and sql_script[i+1] == "'":
                        buf.append(sql_script[i+1])
                        i += 2
                        continue
                    in_single = False
                i += 1
                continue
            if in_double:
                buf.append(c)
                if c == '"':
                    if i+1 < n and sql_script[i+1] == '"':
                        buf.append(sql_script[i+1])
                        i += 2
                        continue
                    in_double = False
                i += 1
                continue
            if c == "'":
                in_single = True
                buf.append(c)
                i += 1
                continue
            if c == '"':
                in_double = True
                buf.append(c)
                i += 1
                continue
            if c == '-' and i+1 < n and sql_script[i+1] == '-':
                in_line_comment = True
                buf.append(c)
                i += 1
                continue
            if c == '/' and i+1 < n and sql_script[i+1] == '*':
                in_block_comment = True
                buf.append(c)
                i += 1
                continue
            if c == ';':
                stmt = ''.join(buf).strip()
                if stmt:
                    stmts.append(stmt)
                buf = []
                i += 1
                continue
            buf.append(c)
            i += 1
        tail = ''.join(buf).strip()
        if tail:
            stmts.append(tail)
        return stmts if len(stmts)>1 else [sql_script.strip()]

    def execute(self, sql, params=None):
        params = params or []
        if isinstance(sql, str):
            # support multi-statement strings
            stmts = self._split_statements(sql)
            if len(stmts) > 1:
                res = None
                for s in stmts:
                    res = self.execute(s, params)
                return res if res is not None else QueryResult()
            stmt = parse_sql(sql)
        else:
            stmt = sql
        from .ast_nodes import (CreateTableStmt, DropTableStmt, CreateIndexStmt,
            InsertStmt, UpdateStmt, DeleteStmt, SelectStmt, TransactionStmt)
        # transaction control
        if isinstance(stmt, TransactionStmt):
            action = stmt.action
            if action == 'BEGIN':
                if self._in_txn:
                    raise MiniSQLError("already in transaction")
                self._in_txn = True
                self._txn_snapshot = self._snapshot()
                self._savepoints = []
                return QueryResult()
            if action == 'COMMIT':
                if not self._in_txn:
                    raise MiniSQLError("no active transaction")
                self._in_txn = False
                self._txn_snapshot = None
                self._savepoints = []
                return QueryResult()
            if action == 'ROLLBACK':
                if not self._in_txn:
                    raise MiniSQLError("no active transaction")
                self._restore(self._txn_snapshot)
                self._in_txn = False
                self._txn_snapshot = None
                self._savepoints = []
                return QueryResult()
            if action == 'SAVEPOINT':
                if not self._in_txn:
                    # allow? spec implies within txn; but save snapshot anyway
                    pass
                self._savepoints.append((stmt.savepoint_name, self._snapshot()))
                return QueryResult()
            if action == 'RELEASE':
                # remove savepoint
                names = [s[0] for s in self._savepoints]
                if stmt.savepoint_name not in names:
                    raise MiniSQLError("unknown savepoint")
                # remove all with that name? remove last occurrence
                idx = None
                for i in range(len(self._savepoints)-1, -1, -1):
                    if self._savepoints[i][0] == stmt.savepoint_name:
                        idx = i
                        break
                del self._savepoints[idx]
                return QueryResult()
            if action == 'ROLLBACK_TO':
                names = [s[0] for s in self._savepoints]
                if stmt.savepoint_name not in names:
                    raise MiniSQLError("unknown savepoint")
                idx = None
                for i in range(len(self._savepoints)-1, -1, -1):
                    if self._savepoints[i][0] == stmt.savepoint_name:
                        idx = i
                        break
                snap = self._savepoints[idx][1]
                self._restore(snap)
                # keep savepoints up to idx? keep transaction active; trim savepoints after
                self._savepoints = self._savepoints[:idx+1]
                return QueryResult()
            raise MiniSQLError("bad transaction")
        # mutating statement atomicity: snapshot before
        mutating = isinstance(stmt, (CreateTableStmt, DropTableStmt, CreateIndexStmt, InsertStmt, UpdateStmt, DeleteStmt))
        snap_before = self._snapshot() if mutating else None
        try:
            if isinstance(stmt, CreateTableStmt):
                self.catalog.create_table(stmt)
                return QueryResult(rowcount=0)
            if isinstance(stmt, DropTableStmt):
                self.catalog.drop_table(stmt.table_name, stmt.if_exists)
                return QueryResult(rowcount=0)
            if isinstance(stmt, CreateIndexStmt):
                self.catalog.create_index(stmt)
                return QueryResult(rowcount=0)
            if isinstance(stmt, InsertStmt):
                tbl = self.catalog.tables[stmt.table_name]
                n = 0
                for row in stmt.values_rows:
                    vals = [eval_expr(e, {}, params) for e in row]
                    if stmt.columns is not None and len(vals) != len(stmt.columns):
                        raise MiniSQLError("column/value count mismatch")
                    if stmt.columns is None and len(vals) != len(tbl.columns):
                        raise MiniSQLError("column/value count mismatch")
                    tbl.insert(vals, stmt.columns)
                    n += 1
                return QueryResult(rowcount=n)
            if isinstance(stmt, UpdateStmt):
                tbl = self.catalog.tables[stmt.table_name]
                affected = tbl.update(stmt.assignments, stmt.where, params)
                return QueryResult(rowcount=affected)
            if isinstance(stmt, DeleteStmt):
                tbl = self.catalog.tables[stmt.table_name]
                affected = tbl.delete(stmt.where, params)
                return QueryResult(rowcount=affected)
            if isinstance(stmt, SelectStmt):
                return QueryExecutor(self).execute(stmt, params)
            raise MiniSQLError("unsupported statement")
        except MiniSQLError:
            if mutating and snap_before is not None:
                self._restore(snap_before)
            raise

    def execute_script(self, sql_script):
        # split semicolon respecting quotes and comments
        stmts = []
        buf = []
        i = 0
        n = len(sql_script)
        in_single = False
        in_double = False
        in_line_comment = False
        in_block_comment = False
        while i < n:
            c = sql_script[i]
            if in_line_comment:
                buf.append(c)
                if c == '\n':
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                buf.append(c)
                if c == '*' and i+1 < n and sql_script[i+1] == '/':
                    buf.append(sql_script[i+1])
                    i += 2
                    in_block_comment = False
                    continue
                i += 1
                continue
            if in_single:
                buf.append(c)
                if c == "'":
                    if i+1 < n and sql_script[i+1] == "'":
                        buf.append(sql_script[i+1])
                        i += 2
                        continue
                    in_single = False
                i += 1
                continue
            if in_double:
                buf.append(c)
                if c == '"':
                    if i+1 < n and sql_script[i+1] == '"':
                        buf.append(sql_script[i+1])
                        i += 2
                        continue
                    in_double = False
                i += 1
                continue
            if c == "'":
                in_single = True
                buf.append(c)
                i += 1
                continue
            if c == '"':
                in_double = True
                buf.append(c)
                i += 1
                continue
            if c == '-' and i+1 < n and sql_script[i+1] == '-':
                in_line_comment = True
                buf.append(c)
                i += 1
                continue
            if c == '/' and i+1 < n and sql_script[i+1] == '*':
                in_block_comment = True
                buf.append(c)
                i += 1
                continue
            if c == ';':
                stmt = ''.join(buf).strip()
                if stmt:
                    stmts.append(stmt)
                buf = []
                i += 1
                continue
            buf.append(c)
            i += 1
        tail = ''.join(buf).strip()
        if tail:
            stmts.append(tail)
        results = []
        for s in stmts:
            results.append(self.execute(s))
        return results

class QueryExecutor:
    def __init__(self, db):
        self.db = db

    def execute(self, stmt, params):
        # Build base rows as list of ctx dicts
        cur_rows = []  # list of ctx dict
        if stmt.from_table is None:
            cur_rows = [{}]
        else:
            if stmt.from_table not in self.db.catalog.tables:
                raise MiniSQLError(f"no such table: {stmt.from_table}")
            base = self.db.catalog.tables[stmt.from_table]
            alias = stmt.from_alias or stmt.from_table
            for r in base.rows:
                ctx = {}
                for i, c in enumerate(base.columns):
                    ctx[f"{alias}.{c.name}"] = r[i]
                    ctx[c.name] = r[i]
                cur_rows.append(ctx)
            for jc in stmt.joins:
                jtbl = self.db.catalog.tables[jc.table_name]
                jalias = jc.alias or jc.table_name
                jtype = jc.join_type.upper()
                new_rows = []
                for ctx in cur_rows:
                    matched = False
                    for r in jtbl.rows:
                        jctx = dict(ctx)
                        for i, c in enumerate(jtbl.columns):
                            jctx[f"{jalias}.{c.name}"] = r[i]
                            jctx[c.name] = r[i]
                        if jc.on_expr is not None:
                            if is_truthy(eval_expr(jc.on_expr, jctx, params)) is True:
                                new_rows.append(jctx)
                                matched = True
                        else:
                            # cross join
                            new_rows.append(jctx)
                            matched = True
                    if jtype in ('LEFT', 'LEFT OUTER', 'LEFT OUTER JOIN') or 'LEFT' in jtype:
                        if not matched and jc.on_expr is not None:
                            jctx = dict(ctx)
                            for c in jtbl.columns:
                                jctx[f"{jalias}.{c.name}"] = None
                                jctx[c.name] = None
                            new_rows.append(jctx)
                    if jtype in ('CROSS', 'CROSS JOIN') and jc.on_expr is None:
                        pass
                if jtype in ('CROSS',) or (jc.on_expr is None and 'CROSS' in jtype):
                    # redo as cross product
                    new_rows = []
                    for ctx in cur_rows:
                        for r in jtbl.rows:
                            jctx = dict(ctx)
                            for i, c in enumerate(jtbl.columns):
                                jctx[f"{jalias}.{c.name}"] = r[i]
                                jctx[c.name] = r[i]
                            new_rows.append(jctx)
                cur_rows = new_rows if new_rows or stmt.joins else cur_rows

        # WHERE
        if stmt.where is not None:
            filtered = []
            for ctx in cur_rows:
                if is_truthy(eval_expr(stmt.where, ctx, params)) is True:
                    filtered.append(ctx)
            cur_rows = filtered

        # Detect aggregates
        def has_agg(expr):
            if isinstance(expr, FuncCall) and expr.name.upper() in ('COUNT','SUM','AVG','MIN','MAX'):
                return True
            if isinstance(expr, (BinaryExpr, UnaryExpr)):
                for a in (getattr(expr,'left',None), getattr(expr,'right',None), getattr(expr,'operand',None)):
                    if a is not None and has_agg(a):
                        return True
            if isinstance(expr, SelectItem):
                return has_agg(expr.expr)
            return False

        any_agg = any(has_agg(si) for si in stmt.select_items) or (stmt.having is not None and has_agg(stmt.having))

        # GROUP BY
        groups = []  # list of (key, list ctx)
        if stmt.group_by:
            from collections import OrderedDict
            buckets = OrderedDict()
            for ctx in cur_rows:
                key = tuple(eval_expr(g, ctx, params) for g in stmt.group_by)
                buckets.setdefault(key, []).append(ctx)
            groups = list(buckets.values())
        else:
            if any_agg:
                groups = [list(cur_rows)]  # one group even if empty
            else:
                groups = [[ctx] for ctx in cur_rows]

        # aggregate eval helper
        def agg_value(func, arg_expr, rows, ctx):
            name = func.name.upper()
            if name == 'COUNT':
                if func.args and isinstance(func.args[0], StarRef):
                    return len(rows)
                if func.args:
                    vals = [eval_expr(func.args[0], r, params) for r in rows]
                    if getattr(func, 'distinct', False):
                        return len(set(v for v in vals if v is not None))
                    return sum(1 for v in vals if v is not None)
                return len(rows)
            vals = []
            if func.args:
                vals = [eval_expr(func.args[0], r, params) for r in rows]
                vals = [v for v in vals if v is not None]
            if name == 'SUM':
                return sum(vals) if vals else None
            if name == 'AVG':
                return (sum(vals)/len(vals)) if vals else None
            if name == 'MIN':
                return min(vals) if vals else None
            if name == 'MAX':
                return max(vals) if vals else None
            return None

        def eval_with_agg(expr, ctx, rows, params):
            if isinstance(expr, FuncCall) and expr.name.upper() in ('COUNT','SUM','AVG','MIN','MAX'):
                return agg_value(expr, expr.args[0] if expr.args else None, rows, ctx)
            if isinstance(expr, BinaryExpr):
                l = eval_with_agg(expr.left, ctx, rows, params)
                r = eval_with_agg(expr.right, ctx, rows, params)
                op = expr.op
                if op == '+': return l + r if l is not None and r is not None else None
                if op == '-': return l - r if l is not None and r is not None else None
                if op == '*': return l * r if l is not None and r is not None else None
                if op == '/':
                    if r == 0:
                        raise MiniSQLError('division by zero')
                    return l / r if r is not None else None
                if op == '=': return l == r
                if op == '!=': return l != r
                if op == '<': return l < r
                if op == '>': return l > r
                if op == '<=': return l <= r
                if op == '>=': return l >= r
                if op == 'AND': return is_truthy(l) and is_truthy(r)
                if op == 'OR': return is_truthy(l) or is_truthy(r)
                return eval_expr(expr, ctx, params)
            if isinstance(expr, UnaryExpr):
                v = eval_with_agg(expr.operand, ctx, rows, params)
                if expr.op == '-': return -v if v is not None else None
                if expr.op == 'NOT': return not is_truthy(v)
                return v
            # alias resolution: if expr is ColumnRef whose name matches a select alias, resolve later? handle in projection
            # try eval_expr with ctx
            if isinstance(expr, ColumnRef):
                # resolve SELECT output alias
                for si in stmt.select_items:
                    if si.alias == expr.name:
                        return eval_with_agg(si.expr, ctx, g, params)
            try:
                return eval_expr(expr, ctx, params)
            except Exception:
                return None

        # HAVING alias resolution: build alias map per group after projection? We'll evaluate having with ctx and allow ColumnRef to select aliases by checking.
        # For simplicity, evaluate having using eval_with_agg; alias refs handled by trying ctx value.

        # Project
        out_rows = []
        out_cols = None
        group_reps = []
        for g in groups:
            ctx = g[0] if g else {}
            # HAVING
            if stmt.having is not None:
                hval = eval_with_agg(stmt.having, ctx, g, params)
                # alias resolution: if ColumnRef name in select aliases, compute
                if is_truthy(hval) is not True:
                    continue
            proj = []
            colnames = []
            for si in stmt.select_items:
                expr = si.expr
                alias = si.alias
                if isinstance(expr, StarRef):
                    if expr.table:
                        prefix = expr.table + '.'
                        keys = [k for k in ctx.keys() if k.startswith(prefix)]
                        for k in keys:
                            proj.append(ctx.get(k))
                            colnames.append(k.split('.')[-1])
                    else:
                        seen = set()
                        for k in ctx.keys():
                            if '.' not in k and k not in seen:
                                seen.add(k)
                                proj.append(ctx.get(k))
                                colnames.append(k)
                else:
                    val = eval_with_agg(expr, ctx, g, params)
                    proj.append(val)
                    if alias:
                        colnames.append(alias)
                    elif isinstance(expr, ColumnRef):
                        colnames.append(expr.name)
                    else:
                        colnames.append(str(expr))
            if out_cols is None:
                out_cols = colnames
            out_rows.append((tuple(proj), ctx, g))
            group_reps.append(ctx)

        # DISTINCT
        if stmt.distinct:
            seen = set()
            uniq = []
            for r, ctx, g in out_rows:
                if r not in seen:
                    seen.add(r)
                    uniq.append((r, ctx, g))
            out_rows = uniq

        # ORDER BY
        if stmt.order_by:
            def sort_val(item, ob):
                r, ctx, g = item
                # find column index or eval
                # try eval expression with agg support using ctx/group
                try:
                    v = eval_with_agg(ob.expr, ctx, g, params)
                except Exception:
                    v = None
                # alias resolution: if ColumnRef and name in out_cols, use that column value
                if isinstance(ob.expr, ColumnRef):
                    if ob.expr.name in (out_cols or []):
                        idx = (out_cols or []).index(ob.expr.name)
                        v = r[idx]
                    elif f"{ob.expr.table}.{ob.expr.name}" if ob.expr.table else None in (out_cols or []):
                        pass
                return v
            def keyfn(item):
                vals = []
                for ob in stmt.order_by:
                    v = sort_val(item, ob)
                    # None handling: ASC None first, DESC None last
                    # encode as tuple (is_none, v) adjusted
                    if ob.asc:
                        # None first: None < anything
                        vals.append((0 if v is None else 1, v if v is not None else 0))
                    else:
                        vals.append((0 if v is not None else 1, v if v is not None else 0))
                # need reverse per item not global; handle by negating? Use stable sort per key descending separately.
                return vals
            # Multi-key with mixed asc/desc: sort sequentially from last to first
            for ob in reversed(stmt.order_by):
                def kfn(item, ob=ob):
                    v = sort_val(item, ob)
                    if ob.asc:
                        return (0 if v is None else 1, v if v is not None else 0)
                    else:
                        # None last: None treated as largest
                        return (1 if v is None else 0, v if v is not None else 0)
                reverse = not ob.asc
                # For desc, we want descending values but None last. The tuple (0/1, v) with reverse=True puts None? 
                # Simpler: sort ascending by tuple then reverse whole? Mixed. Do per-key stable sort with reverse flag on value only is tricky.
                # Use key with negation impossible for strings. Use functools.cmp_to_key.
                out_rows.sort(key=kfn, reverse=(not ob.asc) if False else False)
            # The above may not handle desc value ordering. Redo with cmp.
            import functools
            def cmp(a, b):
                for ob in stmt.order_by:
                    va = sort_val(a, ob)
                    vb = sort_val(b, ob)
                    # None handling
                    if va is None and vb is None:
                        pass
                    elif va is None:
                        # asc: a first; desc: a last
                        if ob.asc:
                            return -1
                        else:
                            return 1
                    elif vb is None:
                        if ob.asc:
                            return 1
                        else:
                            return -1
                    else:
                        if va < vb:
                            r = -1
                        elif va > vb:
                            r = 1
                        else:
                            r = 0
                        if not ob.asc:
                            r = -r
                        if r != 0:
                            return r
                return 0
            out_rows.sort(key=functools.cmp_to_key(cmp))

        # OFFSET/LIMIT
        start = stmt.offset or 0
        end = None
        if stmt.limit is not None:
            end = start + stmt.limit
        sliced = out_rows[start:end]
        final_rows = [r[0] for r in sliced]
        if out_cols is None:
            out_cols = []
            for si in stmt.select_items:
                expr = si.expr
                alias = si.alias
                if isinstance(expr, StarRef):
                    out_cols.append('*')
                elif isinstance(expr, ColumnRef):
                    out_cols.append(alias or expr.name)
                else:
                    out_cols.append(alias or str(expr))
        return QueryResult(columns=out_cols or [], rows=final_rows, rowcount=len(final_rows))
