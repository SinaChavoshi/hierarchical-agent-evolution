import copy
from .ast_nodes import *
from .storage import *
from .parser import parse_sql, parse_statements

AGG_FUNCS = {'COUNT','SUM','AVG','MIN','MAX'}

class QueryExecutor:
    def __init__(self, catalog):
        self.catalog = catalog
        self.in_txn = False
        self.snapshots = []  # list of (name, catalog snapshot) for savepoints? We'll manage
        self.txn_snapshot = None
        self.savepoints = []  # list of (name, snapshot)
    def execute(self, stmt, params=None):
        if params is None: params=[]
        # transaction statements
        if isinstance(stmt, TransactionStmt):
            return self.handle_txn(stmt)
        # statement atomicity snapshot for mutating
        mutating = isinstance(stmt, (CreateTableStmt, DropTableStmt, CreateIndexStmt, InsertStmt, UpdateStmt, DeleteStmt))
        pre = None
        if mutating:
            pre = copy.deepcopy(self.catalog)
        try:
            if isinstance(stmt, CreateTableStmt):
                self.catalog.create_table(stmt)
                # apply if not exists already
                return QueryResult()
            if isinstance(stmt, DropTableStmt):
                self.catalog.drop_table(stmt.table_name, stmt.if_exists)
                return QueryResult()
            if isinstance(stmt, CreateIndexStmt):
                t = self.catalog.get_table(stmt.table_name)
                if t is None: raise MiniSQLError("No such table")
                if stmt.index_name in t.indexes:
                    if stmt.if_not_exists: return QueryResult()
                    raise MiniSQLError("Index exists")
                t.indexes[stmt.index_name] = stmt.column_name
                # build index
                return QueryResult()
            if isinstance(stmt, InsertStmt):
                return self.do_insert(stmt, params)
            if isinstance(stmt, UpdateStmt):
                return self.do_update(stmt, params)
            if isinstance(stmt, DeleteStmt):
                return self.do_delete(stmt, params)
            if isinstance(stmt, SelectStmt):
                return self.do_select(stmt, params)
            raise MiniSQLError("Unknown statement")
        except Exception as e:
            if mutating and pre is not None:
                self.catalog = pre
            # also restore? catalog reference in Database? We'll handle Database wrapper
            raise
    def handle_txn(self, stmt):
        if stmt.action == 'BEGIN':
            if self.in_txn: raise MiniSQLError("Already in transaction")
            self.in_txn = True
            self.txn_snapshot = copy.deepcopy(self.catalog)
            self.savepoints = []
            return QueryResult()
        if stmt.action == 'COMMIT':
            if not self.in_txn: raise MiniSQLError("No transaction")
            self.in_txn = False
            self.txn_snapshot = None
            self.savepoints = []
            return QueryResult()
        if stmt.action == 'ROLLBACK':
            if not self.in_txn: raise MiniSQLError("No transaction")
            self.catalog = copy.deepcopy(self.txn_snapshot)
            self.in_txn = False
            self.txn_snapshot = None
            self.savepoints = []
            return QueryResult()
        if stmt.action == 'SAVEPOINT':
            if not self.in_txn: raise MiniSQLError("No transaction")
            self.savepoints.append((stmt.savepoint_name, copy.deepcopy(self.catalog)))
            return QueryResult()
        if stmt.action == 'RELEASE':
            if not self.in_txn: raise MiniSQLError("No transaction")
            # remove savepoint
            names = [s[0] for s in self.savepoints]
            if stmt.savepoint_name not in names:
                raise MiniSQLError("Unknown savepoint")
            # remove all? release named and later? SQLite releases named; remove it and any after? We'll remove matching last
            idx = None
            for i in range(len(self.savepoints)-1,-1,-1):
                if self.savepoints[i][0]==stmt.savepoint_name:
                    idx=i; break
            if idx is None: raise MiniSQLError("Unknown savepoint")
            self.savepoints = self.savepoints[:idx]
            return QueryResult()
        if stmt.action == 'ROLLBACK_TO':
            if not self.in_txn: raise MiniSQLError("No transaction")
            # find savepoint
            idx=None
            for i in range(len(self.savepoints)-1,-1,-1):
                if self.savepoints[i][0]==stmt.savepoint_name:
                    idx=i; break
            if idx is None: raise MiniSQLError("Unknown savepoint")
            self.catalog = copy.deepcopy(self.savepoints[idx][1])
            # keep savepoints up to idx
            self.savepoints = self.savepoints[:idx+1]
            return QueryResult()
        raise MiniSQLError("Bad txn")
    def do_insert(self, stmt, params):
        t = self.catalog.get_table(stmt.table_name)
        if t is None: raise MiniSQLError("No such table")
        n=0
        for row in stmt.values_rows:
            # build dict
            data = {}
            cols = stmt.columns if stmt.columns else [c.name for c in t.columns]
            if len(cols) != len(row):
                raise MiniSQLError("Column count mismatch")
            for c, expr in zip(cols, row):
                val = eval_expr(expr, {}, params)
                data[c] = val
            # defaults
            for c in t.columns:
                if c.name not in data:
                    if c.has_default:
                        d = c.default
                        if isinstance(d, (Literal,)):
                            data[c.name] = d.value
                        else:
                            try:
                                data[c.name] = eval_expr(d, {}, params) if not isinstance(d, (int,float,str,bool,type(None))) else d
                            except:
                                data[c.name] = d
                    else:
                        data[c.name] = None
            # coerce + constraints
            # check columns exist
            for c in cols:
                if t.get_col(c) is None: raise MiniSQLError("Unknown column")
            # coerce
            for c in t.columns:
                data[c.name] = coerce(data.get(c.name), c.data_type)
            # constraints
            # PK implies not null unique
            for c in t.columns:
                if c.primary_key or c.not_null:
                    if data[c.name] is None:
                        raise MiniSQLError("NOT NULL violation")
            # unique/pk
            for c in t.columns:
                if c.primary_key or c.unique:
                    for r in t.rows:
                        if r.get(c.name) == data[c.name] and data[c.name] is not None:
                            raise MiniSQLError("UNIQUE violation")
            # insert
            t.rows.append(data)
            n += 1
        return QueryResult(rowcount=n)
    def do_update(self, stmt, params):
        t = self.catalog.get_table(stmt.table_name)
        if t is None: raise MiniSQLError("No such table")
        # find rows
        targets = []
        for r in t.rows:
            ctx = {}
            for k,v in r.items():
                ctx[k]=v
                ctx[f"{t.name}.{k}"]=v
            if stmt.where is None or is_truthy(eval_expr(stmt.where, ctx, params)):
                targets.append(r)
        n=0
        for r in targets:
            ctx = {}
            for k,v in r.items():
                ctx[k]=v
                ctx[f"{t.name}.{k}"]=v
            # apply assignments
            newvals = {}
            for col, expr in stmt.assignments:
                if t.get_col(col) is None: raise MiniSQLError("Unknown column")
                newvals[col] = eval_expr(expr, ctx, params)
            # coerce
            for col,val in newvals.items():
                c = t.get_col(col)
                newvals[col] = coerce(val, c.data_type)
            # constraints check on updated row
            # build candidate
            cand = dict(r)
            cand.update(newvals)
            for c in t.columns:
                if c.primary_key or c.not_null:
                    if cand[c.name] is None:
                        raise MiniSQLError("NOT NULL violation")
            for c in t.columns:
                if c.primary_key or c.unique:
                    for r2 in t.rows:
                        if r2 is r: continue
                        if r2.get(c.name) == cand[c.name] and cand[c.name] is not None:
                            raise MiniSQLError("UNIQUE violation")
            # apply
            r.update(newvals)
            n += 1
        return QueryResult(rowcount=n)
    def do_delete(self, stmt, params):
        t = self.catalog.get_table(stmt.table_name)
        if t is None: raise MiniSQLError("No such table")
        keep=[]
        n=0
        for r in t.rows:
            ctx={}
            for k,v in r.items():
                ctx[k]=v; ctx[f"{t.name}.{k}"]=v
            if stmt.where is not None and is_truthy(eval_expr(stmt.where, ctx, params)):
                n+=1
            else:
                keep.append(r)
        t.rows = keep
        return QueryResult(rowcount=n)
    def do_select(self, stmt, params):
        # FROM base
        row_list = []  # list of dict ctx
        if stmt.from_table is None:
            row_list = [{}]
        else:
            t = self.catalog.get_table(stmt.from_table)
            if t is None: raise MiniSQLError("No such table")
            alias = stmt.from_alias or t.name
            for r in t.rows:
                ctx = {}
                for k,v in r.items():
                    ctx[f"{alias}.{k}"] = v
                    ctx[k] = v
                ctx['__table__'] = alias
                row_list.append(ctx)
            # joins
            for j in stmt.joins:
                jt = self.catalog.get_table(j.table_name)
                if jt is None: raise MiniSQLError("No such table")
                jalias = j.alias or jt.name
                new_rows = []
                for row in row_list:
                    matched = False
                    for r2 in jt.rows:
                        ctx2 = dict(row)
                        for k,v in r2.items():
                            ctx2[f"{jalias}.{k}"] = v
                            # unqualified if not exists
                            if k not in ctx2:
                                ctx2[k]=v
                        # on expr
                        if j.join_type.upper().startswith('CROSS'):
                            new_rows.append(ctx2); matched=True; continue
                        if j.on_expr is not None:
                            # evaluate in combined ctx
                            combined = dict(row)
                            for k,v in r2.items():
                                combined[f"{jalias}.{k}"]=v
                                if f"{jalias}.{k}" not in combined: pass
                            # also unqualified
                            for k,v in r2.items():
                                if k not in combined:
                                    combined[k]=v
                            if not is_truthy(eval_expr(j.on_expr, combined, params)):
                                continue
                        # match
                        matched=True
                        combined = dict(row)
                        for k,v in r2.items():
                            combined[f"{jalias}.{k}"]=v
                            if k not in row:  # avoid overwriting base? keep base unqualified
                                pass
                        # ensure unqualified right columns available as jalias.col only; but for * projection need all. We'll store both qualified.
                        for k,v in r2.items():
                            if f"{jalias}.{k}" not in combined:
                                combined[f"{jalias}.{k}"]=v
                        new_rows.append(combined)
                    if j.join_type.upper().startswith('LEFT') and not matched:
                        # pad
                        combined = dict(row)
                        for c in jt.columns:
                            combined[f"{jalias}.{c.name}"] = None
                        new_rows.append(combined)
                if not j.join_type.upper().startswith('LEFT'):
                    # if no matches for inner, row dropped naturally
                    pass
                # For inner join, if a base row had no match, it should be dropped. Our loop only appends matches, so yes.
                row_list = new_rows if new_rows or stmt.joins else row_list
                # handle empty
                if not row_list and not j.join_type.upper().startswith('LEFT'):
                    row_list = []
        # WHERE
        if stmt.where is not None:
            filtered=[]
            for row in row_list:
                if is_truthy(eval_expr(stmt.where, row, params)) is True:
                    filtered.append(row)
            row_list = filtered
        # detect aggregates
        def has_agg(expr):
            if isinstance(expr, FuncCall) and expr.name.upper() in AGG_FUNCS:
                return True
            for attr in ('left','right','operand','expr','low','high','pattern','else_expr'):
                if hasattr(expr, attr):
                    v=getattr(expr, attr)
                    if v is not None and has_agg(v): return True
            if hasattr(expr,'args'):
                for a in expr.args:
                    if has_agg(a): return True
            if hasattr(expr,'when_thens'):
                for c,r in expr.when_thens:
                    if has_agg(c) or has_agg(r): return True
            if hasattr(expr,'select_items'):
                pass
            return False
        agg_present = False
        for si in stmt.select_items:
            if has_agg(si.expr): agg_present=True
        if stmt.having is not None and has_agg(stmt.having):
            agg_present=True
        # GROUP BY
        groups = []  # list of (key, rows)
        if stmt.group_by:
            from collections import defaultdict
            d = defaultdict(list)
            for row in row_list:
                key = tuple(eval_expr(g, row, params) for g in stmt.group_by)
                d[key].append(row)
            groups = [(k, v) for k,v in d.items()]
        elif agg_present:
            groups = [(None, row_list)]
        else:
            groups = [(None, [r]) for r in row_list]
        # aggregate eval helper
        def agg_value(func, expr, rows, distinct):
            name = func.name.upper()
            if name == 'COUNT':
                if len(func.args)==1 and isinstance(func.args[0], StarRef):
                    return len(rows)
                vals = [eval_expr(func.args[0], r, params) for r in rows]
                if distinct:
                    vals = list(set(v for v in vals if v is not None))  # distinct counts non-null? SQL count distinct excludes null
                    # Actually count distinct excludes nulls
                    vals = [v for v in vals if v is not None]
                    return len(set(vals))
                # count(expr) excludes null
                return sum(1 for v in vals if v is not None)
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
        # evaluate select expr with aggregate substitution: need replace FuncCall aggregates in expr with computed value.
        def eval_select_expr(expr, row, group_rows, group_key):
            # recursively evaluate, handling FuncCall aggregates
            if isinstance(expr, FuncCall) and expr.name.upper() in AGG_FUNCS:
                return agg_value(expr, expr, group_rows, expr.distinct)
            if isinstance(expr, Literal):
                return expr.value
            if isinstance(expr, ParamPlaceholder):
                return params[expr.index] if expr.index < len(params) else None
            if isinstance(expr, ColumnRef):
                # if group row? For non-aggregated column, use first row? In SQL, bare column in aggregate query is ambiguous; use row (group representative). We'll evaluate from first row of group if row is None.
                r = row if row is not None else (group_rows[0] if group_rows else {})
                return eval_expr(expr, r, params)
            if isinstance(expr, UnaryExpr):
                v = eval_select_expr(expr.operand, row, group_rows, group_key)
                if expr.op=='-': return None if v is None else -v
                if expr.op=='+': return None if v is None else +v
                if expr.op=='NOT': return None if v is None else (not is_truthy(v))
            if isinstance(expr, BinaryExpr):
                l = eval_select_expr(expr.left, row, group_rows, group_key)
                r = eval_select_expr(expr.right, row, group_rows, group_key)
                # reuse eval_expr by wrapping? Simpler: construct a fake row? We'll handle arithmetic/comparisons manually via eval_expr with a context containing a special? Instead, use eval_expr on original expr but aggregates not computed. Need transform: replace aggregate FuncCall with Literal of computed value.
                pass
            # Fallback: transform aggregates to literals then eval_expr
            return None
        # Better: transform expression aggregates to Literal before eval
        def transform(expr, group_rows):
            if isinstance(expr, FuncCall) and expr.name.upper() in AGG_FUNCS:
                return Literal(agg_value(expr, expr, group_rows, expr.distinct))
            if isinstance(expr, Literal):
                return expr
            if isinstance(expr, ParamPlaceholder):
                return expr
            if isinstance(expr, ColumnRef):
                return expr
            if isinstance(expr, StarRef):
                return expr
            if isinstance(expr, UnaryExpr):
                return UnaryExpr(expr.op, transform(expr.operand, group_rows))
            if isinstance(expr, BinaryExpr):
                return BinaryExpr(expr.op, transform(expr.left, group_rows), transform(expr.right, group_rows))
            if isinstance(expr, IsNullExpr):
                return IsNullExpr(transform(expr.expr, group_rows), expr.negated)
            if isinstance(expr, InExpr):
                return InExpr(transform(expr.expr, group_rows), [transform(x, group_rows) for x in expr.items], expr.negated)
            if isinstance(expr, BetweenExpr):
                return BetweenExpr(transform(expr.expr, group_rows), transform(expr.low, group_rows), transform(expr.high, group_rows), expr.negated)
            if isinstance(expr, LikeExpr):
                return LikeExpr(transform(expr.expr, group_rows), transform(expr.pattern, group_rows), expr.negated)
            if isinstance(expr, CaseExpr):
                return CaseExpr([(transform(c, group_rows), transform(r, group_rows)) for c,r in expr.when_thens], transform(expr.else_expr, group_rows) if expr.else_expr is not None else None)
            if isinstance(expr, FuncCall):
                return FuncCall(expr.name, [transform(a, group_rows) for a in expr.args], expr.distinct)
            return expr
        # HAVING filtering
        filtered_groups = []
        for key, rows in groups:
            if stmt.having is not None:
                texpr = transform(stmt.having, rows)
                # evaluate on representative row (first or
                rep = rows[0] if rows else {}
                if not is_truthy(eval_expr(texpr, rep, params)):
                    continue
            filtered_groups.append((key, rows))
        groups = filtered_groups
        # Projection
        out_rows = []
        for key, rows in groups:
            rep = rows[0] if rows else {}
            # build output columns
            # We'll collect per select item
            projected = []
            for si in stmt.select_items:
                if isinstance(si.expr, StarRef):
                    # determine table
                    if si.expr.table:
                        # columns of that table
                        t = self.catalog.get_table(si.expr.table)
                        cols = [c.name for c in t.columns] if t else []
                        for col in cols:
                            # find value in rep
                            val = rep.get(f"{si.expr.table}.{col}")
                            if val is None and f"{si.expr.table}.{col}" not in rep:
                                val = rep.get(col)
                            projected.append((f"{si.expr.table}.{col}", val))
                    else:
                        # all columns from base/join? Use keys in rep excluding __
                        # For single table without joins, unqualified col names
                        # Collect unique column names from rep keys
                        seen=set()
                        cols=[]
                        for k in rep.keys():
                            if k.startswith('__'): continue
                            base=k.split('.')[-1]
                            if base not in seen:
                                seen.add(base); cols.append(base)
                        # if qualified keys present, use unqualified names
                        for col in cols:
                            # find value
                            val = rep.get(col)
                            if val is None and col not in rep:
                                # try qualified
                                for k,v in rep.items():
                                    if k.split('.')[-1]==col:
                                        val=v; break
                            projected.append((col, val))
                else:
                    texpr = transform(si.expr, rows)
                    val = eval_expr(texpr, rep, params)
                    name = si.alias
                    if name is None:
                        # derive
                        if isinstance(si.expr, ColumnRef):
                            name = si.expr.name
                            if si.expr.table:
                                name = f"{si.expr.table}.{si.expr.name}"
                        else:
                            name = str(si.expr)
                    projected.append((name, val))
            # build row tuple
            # column names determined later; store list of (name,val)
            out_rows.append(projected)
        # Determine final columns
        if out_rows:
            columns = [p[0] for p in out_rows[0]]
        else:
            # empty result: derive columns from select items
            columns = []
            for si in stmt.select_items:
                if isinstance(si.expr, StarRef):
                    if si.expr.table:
                        t=self.catalog.get_table(si.expr.table)
                        if t:
                            for c in t.columns:
                                columns.append(f"{si.expr.table}.{c.name}")
                    else:
                        # unknown; leave
                        pass
                else:
                    name = si.alias
                    if name is None:
                        if isinstance(si.expr, ColumnRef):
                            name = si.expr.name
                            if si.expr.table: name=f"{si.expr.table}.{si.expr.name}"
                        else: name=str(si.expr)
                    columns.append(name)
        # DISTINCT
        if stmt.distinct:
            seen=set()
            uniq=[]
            for proj in out_rows:
                tup=tuple(p[1] for p in proj)
                if tup in seen: continue
                seen.add(tup); uniq.append(proj)
            out_rows=uniq
        # ORDER BY
        def sort_key(proj):
            # map column name to index
            idxmap = {name:i for i,name in enumerate(columns)}
            keys=[]
            for ob in stmt.order_by:
                # evaluate expr
                # transform aggregates? already none in order by maybe; evaluate using rep? We'll evaluate on a dict row built from proj
                rowdict = {name:val for name,val in proj}
                # also qualified? 
                # expr may be ColumnRef/alias
                try:
                    # if expr is column name string? 
                    val = eval_expr(transform(ob.expr, []), rowdict, params) if False else None
                except:
                    val=None
                # simpler: if ColumnRef, lookup
                if isinstance(ob.expr, ColumnRef):
                    nm = ob.expr.name
                    if ob.expr.table:
                        nm = f"{ob.expr.table}.{ob.expr.name}"
                    if nm in idxmap:
                        val = proj[idxmap[nm]][1]
                    elif nm in rowdict:
                        val = rowdict[nm]
                    else:
                        # search
                        for name,v in proj:
                            if name.split('.')[-1]==nm:
                                val=v; break
                elif isinstance(ob.expr, str):
                    val = rowdict.get(ob.expr)
                else:
                    # evaluate transformed with group rows? no group; use rowdict
                    try:
                        val = eval_expr(ob.expr, rowdict, params)
                    except:
                        val=None
                # None sorting
                # return tuple with None handling via custom? We'll sort with key wrapper later
                keys.append((val, ob.asc))
            return keys
        # Python sort with None handling
        def cmp_rows(a,b):
            ka=sort_key(a); kb=sort_key(b)
            for (va,asca),(vb,ascb) in zip(ka,kb):
                # None first in asc, last in desc
                if va is None and vb is None:
                    continue
                if va is None:
                    # asc -> first (less), desc -> last (greater)
                    res = -1 if asca else 1
                elif vb is None:
                    res = 1 if asca else -1
                else:
                    if va < vb: res=-1
                    elif va > vb: res=1
                    else: res=0
                if res != 0:
                    if not asca:
                        res = -res
                    return res
            return 0
        import functools
        out_rows_sorted = sorted(out_rows, key=functools.cmp_to_key(cmp_rows))
        if stmt.order_by:
            out_rows = out_rows_sorted
        # OFFSET/LIMIT
        start = stmt.offset or 0
        if stmt.limit is None:
            sliced = out_rows[start:]
        else:
            sliced = out_rows[start:start+stmt.limit]
        # build final rows
        final_rows = [tuple(p[1] for p in proj) for proj in sliced]
        return QueryResult(columns=columns, rows=final_rows, rowcount=len(final_rows))

class Database:
    def __init__(self):
        self.catalog = Catalog()
        self.executor = QueryExecutor(self.catalog)
    def execute(self, sql, params=None):
        stmt = parse_sql(sql)
        # ensure catalog sync
        self.executor.catalog = self.catalog
        try:
            res = self.executor.execute(stmt, params)
        except Exception as e:
            # if executor replaced catalog during txn/atomicity, sync back
            self.catalog = self.executor.catalog
            raise
        self.catalog = self.executor.catalog
        return res
    def execute_script(self, sql_script):
        stmts = parse_statements(sql_script)
        ress=[]
        for s in stmts:
            ress.append(self.execute(s if False else sql_script and None) if False else None)
        # Actually execute parsed stmt directly:
        ress=[]
        for s in stmts:
            self.executor.catalog = self.catalog
            r = self.executor.execute(s, None)
            self.catalog = self.executor.catalog
            ress.append(r)
        return ress
