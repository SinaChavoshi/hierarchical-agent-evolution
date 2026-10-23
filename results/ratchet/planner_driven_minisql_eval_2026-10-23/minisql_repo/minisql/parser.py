from typing import Any, List, Optional, Tuple
from .ast_nodes import *
from .lexer import tokenize_sql, Token


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.param_counter = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self):
        t = self.peek()
        self.pos += 1
        return t

    def expect(self, type_=None, value=None):
        t = self.peek()
        if t is None:
            raise MiniSQLError("Unexpected end of input")
        if type_ is not None and t.type != type_:
            raise MiniSQLError(f"Expected {type_} got {t.type}")
        if value is not None and t.value != value:
            raise MiniSQLError(f"Expected {value} got {t.value}")
        return self.advance()

    def match(self, type_=None, value=None):
        t = self.peek()
        if t is None:
            return False
        if type_ is not None and t.type != type_:
            return False
        if value is not None and t.value != value:
            return False
        return True

    def parse(self):
        stmts = []
        while self.pos < len(self.tokens):
            if self.match('PUNCT', ';'):
                self.advance()
                continue
            stmt = self.parse_statement()
            stmts.append(stmt)
            if self.match('PUNCT', ';'):
                self.advance()
        return stmts

    def parse_statement(self):
        self.param_counter = 0
        t = self.peek()
        if t is None:
            raise MiniSQLError("Empty statement")
        kw = t.value
        kwu = kw.upper() if isinstance(kw, str) else kw
        if t.type == 'KEYWORD' or (t.type == 'IDENT' and kwu in ('RELEASE',)):
            if kwu == 'SELECT':
                return self.parse_select()
            if kwu == 'INSERT':
                return self.parse_insert()
            if kwu == 'UPDATE':
                return self.parse_update()
            if kwu == 'DELETE':
                return self.parse_delete()
            if kwu == 'CREATE':
                return self.parse_create()
            if kwu == 'DROP':
                return self.parse_drop()
            if kwu in ('BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE'):
                return self.parse_transaction()
        raise MiniSQLError(f"Unexpected token {t}")

    def parse_create(self):
        self.expect('KEYWORD', 'CREATE')
        if self.match('KEYWORD', 'TABLE'):
            self.advance()
            ifn = False
            if self.match('KEYWORD', 'IF'):
                self.advance()
                self.expect('KEYWORD', 'NOT')
                self.expect('KEYWORD', 'EXISTS')
                ifn = True
            name = self.expect('IDENT').value
            self.expect('PUNCT', '(')
            cols = []
            while True:
                col = self.parse_column_spec()
                cols.append(col)
                if self.match('PUNCT', ','):
                    self.advance()
                    continue
                break
            self.expect('PUNCT', ')')
            return CreateTableStmt(table_name=name, columns=cols, if_not_exists=ifn)
        if self.match('KEYWORD', 'INDEX'):
            self.advance()
            ifn = False
            if self.match('KEYWORD', 'IF'):
                self.advance()
                self.expect('KEYWORD', 'NOT')
                self.expect('KEYWORD', 'EXISTS')
                ifn = True
            idx = self.expect('IDENT').value
            self.expect('KEYWORD', 'ON')
            tbl = self.expect('IDENT').value
            self.expect('PUNCT', '(')
            col = self.expect('IDENT').value
            self.expect('PUNCT', ')')
            return CreateIndexStmt(index_name=idx, table_name=tbl, column_name=col, if_not_exists=ifn)
        raise MiniSQLError("Expected TABLE or INDEX")

    def parse_column_spec(self):
        name = self.expect('IDENT').value
        dtype = 'TEXT'
        if self.peek() and self.peek().type == 'IDENT':
            dtype = self.advance().value
        elif self.peek() and self.peek().type == 'KEYWORD' and self.peek().value in ('INT','INTEGER','TEXT','REAL','BLOB','BOOLEAN'):
            dtype = self.advance().value
        pk = False; nn = False; uniq = False; default = None; has_default = False
        while True:
            t = self.peek()
            if t is None or t.type == 'PUNCT':
                break
            if t.type == 'KEYWORD':
                if t.value == 'PRIMARY':
                    self.advance(); self.expect('KEYWORD', 'KEY'); pk = True; continue
                if t.value == 'NOT':
                    self.advance(); self.expect('KEYWORD', 'NULL'); nn = True; continue
                if t.value == 'UNIQUE':
                    self.advance(); uniq = True; continue
                if t.value == 'DEFAULT':
                    self.advance()
                    expr = self.parse_expr()
                    if isinstance(expr, Literal):
                        default = expr.value
                    elif isinstance(expr, UnaryExpr) and expr.op == '-' and isinstance(expr.operand, Literal):
                        default = -expr.operand.value
                    else:
                        default = expr
                    has_default = True
                    continue
            break
        return ColumnSpec(name=name, data_type=dtype, primary_key=pk, not_null=nn, unique=uniq, default=default, has_default=has_default)

    def parse_drop(self):
        self.expect('KEYWORD', 'DROP')
        self.expect('KEYWORD', 'TABLE')
        ife = False
        if self.match('KEYWORD', 'IF'):
            self.advance(); self.expect('KEYWORD', 'EXISTS'); ife = True
        name = self.expect('IDENT').value
        return DropTableStmt(table_name=name, if_exists=ife)

    def parse_insert(self):
        self.expect('KEYWORD', 'INSERT')
        self.expect('KEYWORD', 'INTO')
        tbl = self.expect('IDENT').value
        cols = None
        if self.match('PUNCT', '('):
            self.advance()
            cols = []
            while True:
                cols.append(self.expect('IDENT').value)
                if self.match('PUNCT', ','):
                    self.advance(); continue
                break
            self.expect('PUNCT', ')')
        self.expect('KEYWORD', 'VALUES')
        rows = []
        while True:
            self.expect('PUNCT', '(')
            vals = []
            while True:
                vals.append(self.parse_expr())
                if self.match('PUNCT', ','):
                    self.advance(); continue
                break
            self.expect('PUNCT', ')')
            rows.append(vals)
            if self.match('PUNCT', ','):
                self.advance(); continue
            break
        return InsertStmt(table_name=tbl, columns=cols, values_rows=rows)

    def parse_update(self):
        self.expect('KEYWORD', 'UPDATE')
        tbl = self.expect('IDENT').value
        self.expect('KEYWORD', 'SET')
        assigns = []
        while True:
            col = self.expect('IDENT').value
            self.expect('OP', '=')
            expr = self.parse_expr()
            assigns.append((col, expr))
            if self.match('PUNCT', ','):
                self.advance(); continue
            break
        where = None
        if self.match('KEYWORD', 'WHERE'):
            self.advance(); where = self.parse_expr()
        return UpdateStmt(table_name=tbl, assignments=assigns, where=where)

    def parse_delete(self):
        self.expect('KEYWORD', 'DELETE')
        self.expect('KEYWORD', 'FROM')
        tbl = self.expect('IDENT').value
        where = None
        if self.match('KEYWORD', 'WHERE'):
            self.advance(); where = self.parse_expr()
        return DeleteStmt(table_name=tbl, where=where)

    def parse_select(self):
        self.expect('KEYWORD', 'SELECT')
        distinct = False
        if self.match('KEYWORD', 'DISTINCT'):
            self.advance(); distinct = True
        items = []
        while True:
            expr = self.parse_expr()
            alias = None
            if self.match('KEYWORD', 'AS'):
                self.advance(); alias = self.expect('IDENT').value
            elif self.peek() and self.peek().type == 'IDENT' and not self.match('KEYWORD'):
                # alias without AS (only if next is not comma/from/etc)
                if self.peek() and self.peek().value not in (',',):
                    alias = self.advance().value
            items.append(SelectItem(expr=expr, alias=alias))
            if self.match('PUNCT', ','):
                self.advance(); continue
            break
        from_table = None; from_alias = None; joins = []
        if self.match('KEYWORD', 'FROM'):
            self.advance()
            from_table = self.expect('IDENT').value
            if self.match('KEYWORD', 'AS'):
                self.advance(); from_alias = self.expect('IDENT').value
            elif self.peek() and self.peek().type == 'IDENT' and self.peek().value.upper() not in ('WHERE','GROUP','HAVING','ORDER','LIMIT','INNER','LEFT','RIGHT','FULL','CROSS','JOIN','ON'):
                from_alias = self.advance().value
            # joins
            while True:
                jt = None
                if self.match('KEYWORD', 'INNER'):
                    self.advance(); jt = 'INNER'
                elif self.match('KEYWORD', 'LEFT'):
                    self.advance(); jt = 'LEFT'
                    if self.match('KEYWORD', 'OUTER'):
                        self.advance()
                elif self.match('KEYWORD', 'RIGHT'):
                    self.advance(); jt = 'RIGHT'
                    if self.match('KEYWORD', 'OUTER'):
                        self.advance()
                elif self.match('KEYWORD', 'FULL'):
                    self.advance(); jt = 'FULL'
                    if self.match('KEYWORD', 'OUTER'):
                        self.advance()
                elif self.match('KEYWORD', 'CROSS'):
                    self.advance(); jt = 'CROSS'
                if jt is not None or self.match('KEYWORD', 'JOIN'):
                    if jt is None:
                        jt = 'INNER'
                    self.expect('KEYWORD', 'JOIN')
                    tbl = self.expect('IDENT').value
                    alias = None
                    if self.match('KEYWORD', 'AS'):
                        self.advance(); alias = self.expect('IDENT').value
                    elif self.peek() and self.peek().type == 'IDENT' and self.peek().value.upper() not in ('ON','WHERE','GROUP','HAVING','ORDER','LIMIT'):
                        alias = self.advance().value
                    on_expr = None
                    if self.match('KEYWORD', 'ON'):
                        self.advance(); on_expr = self.parse_expr()
                    joins.append(JoinClause(join_type=jt, table_name=tbl, alias=alias, on_expr=on_expr))
                    continue
                break
        where = None
        if self.match('KEYWORD', 'WHERE'):
            self.advance(); where = self.parse_expr()
        group_by = []
        if self.match('KEYWORD', 'GROUP'):
            self.advance(); self.expect('KEYWORD', 'BY')
            while True:
                group_by.append(self.parse_expr())
                if self.match('PUNCT', ','):
                    self.advance(); continue
                break
        having = None
        if self.match('KEYWORD', 'HAVING'):
            self.advance(); having = self.parse_expr()
        order_by = []
        if self.match('KEYWORD', 'ORDER'):
            self.advance(); self.expect('KEYWORD', 'BY')
            while True:
                expr = self.parse_expr()
                asc = True
                if self.match('KEYWORD', 'ASC'):
                    self.advance(); asc = True
                elif self.match('KEYWORD', 'DESC'):
                    self.advance(); asc = False
                order_by.append(OrderByItem(expr=expr, asc=asc))
                if self.match('PUNCT', ','):
                    self.advance(); continue
                break
        limit = None; offset = 0
        if self.match('KEYWORD', 'LIMIT'):
            self.advance()
            t = self.peek()
            if t.type == 'INT':
                limit = self.advance().value
            else:
                limit = self.parse_expr()
        if self.match('KEYWORD', 'OFFSET'):
            self.advance()
            t = self.peek()
            if t.type == 'INT':
                offset = self.advance().value
            else:
                offset = self.parse_expr()
        return SelectStmt(select_items=items, from_table=from_table, from_alias=from_alias, distinct=distinct, joins=joins, where=where, group_by=group_by, having=having, order_by=order_by, limit=limit, offset=offset)

    def parse_transaction(self):
        t = self.peek().value
        if t == 'BEGIN':
            self.advance()
            if self.match('KEYWORD', 'TRANSACTION'):
                self.advance()
            return TransactionStmt(action='BEGIN')
        if t == 'COMMIT':
            self.advance()
            if self.match('KEYWORD', 'TRANSACTION'):
                self.advance()
            return TransactionStmt(action='COMMIT')
        if t == 'ROLLBACK':
            self.advance()
            if self.match('KEYWORD', 'TRANSACTION'):
                self.advance()
            if self.match('KEYWORD', 'TO') or (self.peek() and self.peek().type=='IDENT' and self.peek().value.upper()=='TO'):
                self.advance()
                if self.match('KEYWORD', 'SAVEPOINT'):
                    self.advance()
                name = self.expect('IDENT').value
                return TransactionStmt(action='ROLLBACK_TO', savepoint_name=name)
            return TransactionStmt(action='ROLLBACK')
        if t == 'SAVEPOINT':
            self.advance()
            name = self.expect('IDENT').value
            return TransactionStmt(action='SAVEPOINT', savepoint_name=name)
        if t == 'RELEASE':
            self.advance()
            if self.match('KEYWORD', 'SAVEPOINT'):
                self.advance()
            name = self.expect('IDENT').value
            return TransactionStmt(action='RELEASE', savepoint_name=name)
        raise MiniSQLError("Bad transaction")

    # Expressions
    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.match('KEYWORD', 'OR'):
            self.advance()
            right = self.parse_and()
            left = BinaryExpr(op='OR', left=left, right=right)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.match('KEYWORD', 'AND'):
            self.advance()
            right = self.parse_not()
            left = BinaryExpr(op='AND', left=left, right=right)
        return left

    def parse_not(self):
        if self.match('KEYWORD', 'NOT'):
            self.advance()
            operand = self.parse_not()
            return UnaryExpr(op='NOT', operand=operand)
        return self.parse_is()

    def parse_is(self):
        left = self.parse_comparison()
        # IS [NOT] NULL
        if self.match('KEYWORD', 'IS'):
            self.advance()
            neg = False
            if self.match('KEYWORD', 'NOT'):
                self.advance(); neg = True
            if self.match('KEYWORD', 'NULL'):
                self.advance()
                return IsNullExpr(expr=left, negated=neg)
        # [NOT] IN / BETWEEN / LIKE handled below? spec groups them here
        # NOT IN/BETWEEN/LIKE
        # handle prefix NOT
        return left

    def parse_comparison(self):
        left = self.parse_concat()
        # handle IN/BETWEEN/LIKE/IS NULL with optional NOT before
        # We'll check for NOT keyword then IN/BETWEEN/LIKE
        while True:
            neg = False
            if self.match('KEYWORD', 'NOT'):
                # peek next
                nxt = self.tokens[self.pos+1] if self.pos+1 < len(self.tokens) else None
                if nxt and nxt.type == 'KEYWORD' and nxt.value in ('IN','BETWEEN','LIKE'):
                    self.advance(); neg = True
                else:
                    break
            if self.match('KEYWORD', 'IN'):
                self.advance()
                self.expect('PUNCT', '(')
                items = []
                while True:
                    items.append(self.parse_expr())
                    if self.match('PUNCT', ','):
                        self.advance(); continue
                    break
                self.expect('PUNCT', ')')
                left = InExpr(expr=left, items=items, negated=neg)
                continue
            if self.match('KEYWORD', 'BETWEEN'):
                self.advance()
                low = self.parse_concat()
                self.expect('KEYWORD', 'AND')
                high = self.parse_concat()
                left = BetweenExpr(expr=left, low=low, high=high, negated=neg)
                continue
            if self.match('KEYWORD', 'LIKE'):
                self.advance()
                pat = self.parse_concat()
                left = LikeExpr(expr=left, pattern=pat, negated=neg)
                continue
            # comparison ops
            if self.peek() and self.peek().type == 'OP' and self.peek().value in ('=','==','!=','<>','<','<=','>','>='):
                op = self.advance().value
                right = self.parse_concat()
                left = BinaryExpr(op=op, left=left, right=right)
                continue
            break
        # IS NULL handled? already in parse_is; but comparison called from parse_is? reorder: parse_not -> parse_is -> parse_comparison. Need IS NULL check before comparison. We'll handle IS NULL here too.
        return left

    def parse_concat(self):
        left = self.parse_additive()
        while self.match('OP', '||'):
            self.advance()
            right = self.parse_additive()
            left = BinaryExpr(op='||', left=left, right=right)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.peek() and self.peek().type == 'OP' and self.peek().value in ('+','-'):
            op = self.advance().value
            right = self.parse_multiplicative()
            left = BinaryExpr(op=op, left=left, right=right)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.peek() and self.peek().type == 'OP' and self.peek().value in ('*','/','%'):
            op = self.advance().value
            right = self.parse_unary()
            left = BinaryExpr(op=op, left=left, right=right)
        return left

    def parse_unary(self):
        if self.peek() and self.peek().type == 'OP' and self.peek().value in ('+','-'):
            op = self.advance().value
            operand = self.parse_unary()
            return UnaryExpr(op=op, operand=operand)
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t is None:
            raise MiniSQLError("Unexpected end")
        if t.type == 'INT':
            self.advance(); return Literal(t.value)
        if t.type == 'FLOAT':
            self.advance(); return Literal(t.value)
        if t.type == 'STRING':
            self.advance(); return Literal(t.value)
        if t.type == 'KEYWORD' and t.value in ('TRUE','FALSE','NULL'):
            self.advance()
            if t.value == 'TRUE': return Literal(True)
            if t.value == 'FALSE': return Literal(False)
            return Literal(None)
        if t.type == 'PARAM':
            self.advance()
            idx = self.param_counter
            self.param_counter += 1
            return ParamPlaceholder(index=idx)
        if t.type == 'PUNCT' and t.value == '(':
            self.advance()
            # CASE? no
            expr = self.parse_expr()
            self.expect('PUNCT', ')')
            return expr
        if t.type == 'KEYWORD' and t.value == 'CASE':
            self.advance()
            when_thens = []
            while self.match('KEYWORD', 'WHEN'):
                self.advance()
                cond = self.parse_expr()
                self.expect('KEYWORD', 'THEN')
                res = self.parse_expr()
                when_thens.append((cond, res))
            else_expr = None
            if self.match('KEYWORD', 'ELSE'):
                self.advance()
                else_expr = self.parse_expr()
            self.expect('KEYWORD', 'END')
            return CaseExpr(when_thens=when_thens, else_expr=else_expr)
        if t.type == 'IDENT' or (t.type == 'KEYWORD'):
            # function call or column ref or star
            if t.type == 'KEYWORD' and t.value not in ('COUNT',):
                # could be function? treat as ident if followed by (
                pass
            # check function call: ident followed by (
            if self.pos+1 < len(self.tokens) and self.tokens[self.pos+1].type == 'PUNCT' and self.tokens[self.pos+1].value == '(':
                name = self.advance().value
                self.expect('PUNCT', '(')
                distinct = False
                if self.match('KEYWORD', 'DISTINCT'):
                    self.advance(); distinct = True
                args = []
                if not (self.peek() and self.peek().type == 'PUNCT' and self.peek().value == ')'):
                    if self.peek() and self.peek().type == 'OP' and self.peek().value == '*':
                        self.advance(); args.append(StarRef())
                    else:
                        args.append(self.parse_expr())
                    while self.match('PUNCT', ','):
                        self.advance()
                        if self.peek() and self.peek().type == 'OP' and self.peek().value == '*':
                            self.advance(); args.append(StarRef())
                        else:
                            args.append(self.parse_expr())
                self.expect('PUNCT', ')')
                return FuncCall(name=name, args=args, distinct=distinct)
            # star ref table.*
            if self.peek() and self.peek().type == 'OP' and self.peek().value == '*':
                # table.* 
                pass
            # column ref possibly table.col or table.*
            name_tok = self.advance()
            name = name_tok.value
            if self.match('PUNCT', '.'):
                self.advance()
                if self.peek() and self.peek().type == 'OP' and self.peek().value == '*':
                    self.advance()
                    return StarRef(table=name)
                col = self.expect('IDENT').value if self.peek().type=='IDENT' else self.expect('KEYWORD').value
                return ColumnRef(name=col, table=name)
            if name == '*' :
                return StarRef()
            return ColumnRef(name=name)
        if (t.type in ('PUNCT','OP')) and t.value == '*':
            self.advance(); return StarRef()
        raise MiniSQLError(f"Unexpected token {t}")

    # fix IS NULL handling: wrap parse_comparison? We'll post-process in parse_is by calling comparison then check IS.
    # Already parse_is calls parse_comparison first; but IS NULL check done before returning left. Need to move IS NULL check after parse_comparison. Already there? We put IS check in parse_is before comparison. Fix: reorder.

def parse_sql(sql):
    toks = tokenize_sql(sql)
    p = Parser(toks)
    stmts = p.parse()
    if len(stmts) == 1:
        return stmts[0]
    # parse_sql returns one AST node? spec says parse_sql -> Any. For multiple, return first? Usually parse_statements. We'll return list? Spec: parse_sql returns one. If multiple, return first.
    return stmts[0] if stmts else None

def parse_statements(sql):
    toks = tokenize_sql(sql)
    p = Parser(toks)
    return p.parse()
