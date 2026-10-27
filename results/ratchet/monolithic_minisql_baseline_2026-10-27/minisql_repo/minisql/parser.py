from .lexer import tokenize_sql
from .ast_nodes import *

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.param_counter = 0
    def peek(self):
        return self.tokens[self.pos]
    def advance(self):
        t = self.tokens[self.pos]
        self.pos += 1
        return t
    def expect(self, type=None, value=None):
        t = self.peek()
        if type and t.type != type:
            raise MiniSQLError(f"Expected {type}, got {t.type}")
        if value is not None and t.value != value:
            raise MiniSQLError(f"Expected {value}, got {t.value}")
        return self.advance()
    def match(self, type=None, value=None):
        t = self.peek()
        if type and t.type != type: return False
        if value is not None and t.value != value: return False
        self.advance()
        return True
    def parse(self):
        stmt = self.statement()
        if self.peek().type != 'EOF':
            raise MiniSQLError("Unexpected trailing tokens")
        return stmt
    def statement(self):
        t = self.peek()
        if t.type == 'KEYWORD':
            kw = t.value
            if kw == 'CREATE':
                self.advance()
                if self.peek().value == 'TABLE':
                    return self.create_table()
                if self.peek().value == 'INDEX':
                    return self.create_index()
                raise MiniSQLError("Expected TABLE or INDEX")
            if kw == 'DROP':
                self.advance()
                if self.peek().value == 'TABLE':
                    return self.drop_table()
                raise MiniSQLError("Expected TABLE")
            if kw == 'INSERT':
                return self.insert()
            if kw == 'UPDATE':
                return self.update()
            if kw == 'DELETE':
                return self.delete()
            if kw == 'SELECT':
                return self.select()
            if kw in ('BEGIN','COMMIT','ROLLBACK','SAVEPOINT','RELEASE'):
                return self.transaction()
        raise MiniSQLError("Unexpected statement")
    def create_table(self):
        self.expect(value='TABLE')
        ifn = False
        if self.peek().value == 'IF':
            self.advance(); self.expect(value='NOT'); self.expect(value='EXISTS'); ifn=True
        name = self.expect(type='IDENT').value
        self.expect(value='(')
        cols = [self.column_spec()]
        while self.match(value=','):
            cols.append(self.column_spec())
        self.expect(value=')')
        return CreateTableStmt(name, cols, ifn)
    def column_spec(self):
        name = self.expect(type='IDENT').value
        dtype = 'TEXT'
        if self.peek().type in ('KEYWORD','IDENT') and str(self.peek().value).upper() in ('INT','INTEGER','REAL','FLOAT','TEXT','VARCHAR','BOOL','BOOLEAN'):
            dtype = self.advance().value
        pk=nn=uniq=False
        default=None; has=False
        while True:
            t=self.peek()
            if t.type=='KEYWORD' and t.value=='PRIMARY':
                self.advance(); self.expect(value='KEY'); pk=True; nn=True
            elif t.type=='KEYWORD' and t.value=='NOT':
                self.advance(); self.expect(value='NULL'); nn=True
            elif t.type=='KEYWORD' and t.value=='UNIQUE':
                self.advance(); uniq=True
            elif t.type=='KEYWORD' and t.value=='DEFAULT':
                self.advance()
                expr = self.expr()
                if isinstance(expr, Literal):
                    default = expr.value
                elif isinstance(expr, UnaryExpr) and expr.op=='-' and isinstance(expr.operand, Literal):
                    default = -expr.operand.value
                else:
                    default = expr
                has=True
            else:
                break
        return ColumnSpec(name, dtype, pk, nn, uniq, default, has)
    def create_index(self):
        self.expect(value='INDEX')
        ifn=False
        if self.peek().value=='IF':
            self.advance(); self.expect(value='NOT'); self.expect(value='EXISTS'); ifn=True
        idx=self.expect(type='IDENT').value
        self.expect(value='ON')
        tbl=self.expect(type='IDENT').value
        self.expect(value='(')
        col=self.expect(type='IDENT').value
        self.expect(value=')')
        return CreateIndexStmt(idx, tbl, col, ifn)
    def drop_table(self):
        self.expect(value='TABLE')
        ie=False
        if self.peek().value=='IF':
            self.advance(); self.expect(value='EXISTS'); ie=True
        name=self.expect(type='IDENT').value
        return DropTableStmt(name, ie)
    def insert(self):
        self.expect(value='INSERT'); self.expect(value='INTO')
        tbl=self.expect(type='IDENT').value
        cols=None
        if self.peek().value=='(':
            self.advance()
            cols=[self.expect(type='IDENT').value]
            while self.match(value=','):
                cols.append(self.expect(type='IDENT').value)
            self.expect(value=')')
        self.expect(value='VALUES')
        rows=[self.value_row()]
        while self.match(value=','):
            rows.append(self.value_row())
        return InsertStmt(tbl, cols, rows)
    def value_row(self):
        self.expect(value='(')
        vals=[self.expr()]
        while self.match(value=','):
            vals.append(self.expr())
        self.expect(value=')')
        return vals
    def update(self):
        self.expect(value='UPDATE')
        tbl=self.expect(type='IDENT').value
        self.expect(value='SET')
        assigns=[]
        col=self.expect(type='IDENT').value
        self.expect(value='=')
        expr=self.expr()
        assigns.append((col, expr))
        while self.match(value=','):
            col=self.expect(type='IDENT').value
            self.expect(value='=')
            expr=self.expr()
            assigns.append((col, expr))
        where=None
        if self.peek().value=='WHERE':
            self.advance(); where=self.expr()
        return UpdateStmt(tbl, assigns, where)
    def delete(self):
        self.expect(value='DELETE'); self.expect(value='FROM')
        tbl=self.expect(type='IDENT').value
        where=None
        if self.peek().value=='WHERE':
            self.advance(); where=self.expr()
        return DeleteStmt(tbl, where)
    def select(self):
        self.expect(value='SELECT')
        distinct=False
        if self.peek().value=='DISTINCT':
            self.advance(); distinct=True
        items=[self.select_item()]
        while self.match(value=','):
            items.append(self.select_item())
        from_table=None; from_alias=None; joins=[]
        if self.peek().value=='FROM':
            self.advance()
            from_table=self.expect(type='IDENT').value
            if self.peek().value=='AS':
                self.advance()
                from_alias=self.expect(type='IDENT').value
            elif self.peek().type=='IDENT':
                from_alias=self.advance().value
            while self.peek().value in ('INNER','LEFT','RIGHT','FULL','CROSS','JOIN'):
                joins.append(self.join())
        where=None
        if self.peek().value=='WHERE':
            self.advance(); where=self.expr()
        group_by=[]
        if self.peek().value=='GROUP':
            self.advance(); self.expect(value='BY')
            group_by=[self.expr()]
            while self.match(value=','):
                group_by.append(self.expr())
        having=None
        if self.peek().value=='HAVING':
            self.advance(); having=self.expr()
        order_by=[]
        if self.peek().value=='ORDER':
            self.advance(); self.expect(value='BY')
            order_by=[self.order_item()]
            while self.match(value=','):
                order_by.append(self.order_item())
        limit=None; offset=0
        if self.peek().value=='LIMIT':
            self.advance()
            le=self.expr()
            limit = le.value if isinstance(le, Literal) else None
            if self.peek().value=='OFFSET':
                self.advance()
                oe=self.expr()
                offset = oe.value if isinstance(oe, Literal) else 0
        return SelectStmt(items, from_table, from_alias, distinct, joins, where, group_by, having, order_by, limit, offset)
    def select_item(self):
        if self.peek().value=='*':
            self.advance(); return SelectItem(StarRef())
        expr=self.expr()
        alias=None
        if self.peek().value=='AS':
            self.advance(); alias=self.expect(type='IDENT').value
        elif self.peek().type=='IDENT' and self.peek().value not in ('FROM','WHERE','GROUP','HAVING','ORDER','LIMIT','OFFSET',):
            # alias without AS only if next is keyword boundary? allow
            alias=self.advance().value
        # handle table.* 
        return SelectItem(expr, alias)
    def join(self):
        jtype='INNER'
        if self.peek().value in ('INNER','LEFT','RIGHT','FULL','CROSS'):
            jtype=self.advance().value
            if self.peek().value=='OUTER':
                self.advance()
                jtype = jtype + ' OUTER'
            if self.peek().value=='JOIN':
                self.advance()
        elif self.peek().value=='JOIN':
            self.advance()
        else:
            self.expect(value='JOIN')
        tbl=self.expect(type='IDENT').value
        alias=None
        if self.peek().value=='AS':
            self.advance(); alias=self.expect(type='IDENT').value
        elif self.peek().type=='IDENT' and self.peek().value not in ('ON','WHERE','GROUP','HAVING','ORDER','LIMIT','INNER','LEFT','RIGHT','FULL','CROSS','JOIN'):
            alias=self.advance().value
        on=None
        if self.peek().value=='ON':
            self.advance(); on=self.expr()
        if jtype=='CROSS':
            jtype='CROSS'
        return JoinClause(jtype, tbl, alias, on)
    def order_item(self):
        expr=self.expr()
        asc=True
        if self.peek().value=='ASC':
            self.advance(); asc=True
        elif self.peek().value=='DESC':
            self.advance(); asc=False
        return OrderByItem(expr, asc)
    def transaction(self):
        kw=self.advance().value
        if kw=='BEGIN':
            if self.peek().value=='TRANSACTION':
                self.advance()
            return TransactionStmt('BEGIN')
        if kw=='COMMIT':
            if self.peek().value=='TRANSACTION':
                self.advance()
            return TransactionStmt('COMMIT')
        if kw=='ROLLBACK':
            if self.peek().value=='TRANSACTION':
                self.advance()
            if self.peek().value=='TO':
                self.advance()
                if self.peek().value=='SAVEPOINT':
                    self.advance()
                name=self.expect(type='IDENT').value
                return TransactionStmt('ROLLBACK_TO', name)
            return TransactionStmt('ROLLBACK')
        if kw=='SAVEPOINT':
            name=self.expect(type='IDENT').value
            return TransactionStmt('SAVEPOINT', name)
        if kw=='RELEASE':
            if self.peek().value=='SAVEPOINT':
                self.advance()
            name=self.expect(type='IDENT').value
            return TransactionStmt('RELEASE', name)
        raise MiniSQLError("Bad transaction")
    # expressions
    def expr(self):
        return self.or_expr()
    def or_expr(self):
        left=self.and_expr()
        while self.peek().value=='OR':
            self.advance(); right=self.and_expr(); left=BinaryExpr('OR', left, right)
        return left
    def and_expr(self):
        left=self.not_expr()
        while self.peek().value=='AND':
            self.advance(); right=self.not_expr(); left=BinaryExpr('AND', left, right)
        return left
    def not_expr(self):
        if self.peek().value=='NOT':
            self.advance(); return UnaryExpr('NOT', self.not_expr())
        return self.is_expr()
    def is_expr(self):
        left=self.comp_expr()
        # IS [NOT] NULL
        if self.peek().value=='IS':
            self.advance()
            neg=False
            if self.peek().value=='NOT':
                self.advance(); neg=True
            if self.peek().value=='NULL':
                self.advance(); return IsNullExpr(left, neg)
            raise MiniSQLError("Expected NULL")
        # [NOT] IN / BETWEEN / LIKE handled as prefix? SQL allows NOT IN etc. We handle NOT already unary? Actually NOT IN should be InExpr negated. Need detect when left followed by NOT? We'll handle in comp? Simpler: handle here if peek NOT then IN/BETWEEN/LIKE
        if self.peek().value=='NOT':
            # lookahead
            if self.pos+1 < len(self.tokens) and self.tokens[self.pos+1].value in ('IN','BETWEEN','LIKE'):
                self.advance()
                if self.peek().value=='IN':
                    self.advance(); return self.in_expr(left, True)
                if self.peek().value=='BETWEEN':
                    self.advance(); return self.between_expr(left, True)
                if self.peek().value=='LIKE':
                    self.advance(); return self.like_expr(left, True)
        if self.peek().value=='IN':
            self.advance(); return self.in_expr(left, False)
        if self.peek().value=='BETWEEN':
            self.advance(); return self.between_expr(left, False)
        if self.peek().value=='LIKE':
            self.advance(); return self.like_expr(left, False)
        return left
    def in_expr(self, left, neg):
        self.expect(value='(')
        if self.peek().value=='SELECT':
            # subquery not supported; parse expr list only
            pass
        items=[]
        if self.peek().value != ')':
            items.append(self.expr())
            while self.match(value=','):
                items.append(self.expr())
        self.expect(value=')')
        return InExpr(left, items, neg)
    def between_expr(self, left, neg):
        low=self.expr()
        self.expect(value='AND')
        high=self.expr()
        return BetweenExpr(left, low, high, neg)
    def like_expr(self, left, neg):
        pat=self.expr()
        return LikeExpr(left, pat, neg)
    def comp_expr(self):
        left=self.concat_expr()
        while self.peek().type=='OP' and self.peek().value in ('=','==','!=','<>','<','<=','>','>='):
            op=self.advance().value
            right=self.concat_expr()
            left=BinaryExpr(op, left, right)
        return left
    def concat_expr(self):
        left=self.add_expr()
        while self.peek().value=='||' or (self.peek().type=='OP' and self.peek().value=='||'):
            self.advance(); right=self.add_expr(); left=BinaryExpr('||', left, right)
        # token OP? lexer OP includes ||
        return left
    def add_expr(self):
        left=self.mul_expr()
        while self.peek().type=='OP' and self.peek().value in ('+','-'):
            op=self.advance().value
            right=self.mul_expr()
            left=BinaryExpr(op, left, right)
        return left
    def mul_expr(self):
        left=self.unary_expr()
        while self.peek().type=='OP' and self.peek().value in ('*','/','%'):
            op=self.advance().value
            right=self.unary_expr()
            left=BinaryExpr(op, left, right)
        return left
    def unary_expr(self):
        if self.peek().type=='OP' and self.peek().value in ('+','-'):
            op=self.advance().value
            return UnaryExpr(op, self.unary_expr())
        return self.primary()
    def primary(self):
        t=self.peek()
        if t.type=='NUMBER':
            self.advance(); return Literal(t.value)
        if t.type=='STRING':
            self.advance(); return Literal(t.value)
        if t.type=='PARAM':
            self.advance(); idx=self.param_counter; self.param_counter+=1; return ParamPlaceholder(index=idx)
        if t.type=='KEYWORD' and t.value in ('TRUE','FALSE','NULL'):
            self.advance()
            if t.value=='TRUE': return Literal(True)
            if t.value=='FALSE': return Literal(False)
            return Literal(None)
        if t.type=='KEYWORD' and t.value=='CASE':
            self.advance()
            whens=[]
            while self.peek().value=='WHEN':
                self.advance()
                cond=self.expr()
                self.expect(value='THEN')
                res=self.expr()
                whens.append((cond,res))
            else_expr=None
            if self.peek().value=='ELSE':
                self.advance(); else_expr=self.expr()
            self.expect(value='END')
            return CaseExpr(whens, else_expr)
        if t.type=='IDENT' or (t.type=='KEYWORD' and t.value in ('COUNT','SUM','AVG','MIN','MAX','COALESCE','IFNULL','NULLIF','UPPER','LOWER','LENGTH','ABS','ROUND')):
            # function call or column ref
            if self.pos+1 < len(self.tokens) and self.tokens[self.pos+1].value=='(':
                name=self.advance().value
                self.expect(value='(')
                distinct=False
                if self.peek().value=='DISTINCT':
                    self.advance(); distinct=True
                args=[]
                if self.peek().value != ')':
                    if self.peek().value=='*':
                        self.advance(); args.append(StarRef())
                    else:
                        args.append(self.expr())
                    while self.match(value=','):
                        if self.peek().value=='*':
                            self.advance(); args.append(StarRef())
                        else:
                            args.append(self.expr())
                self.expect(value=')')
                return FuncCall(name.upper(), args, distinct)
            # column ref possibly table.col or table.*
            if self.pos+1 < len(self.tokens) and self.tokens[self.pos+1].value=='.':
                table=self.advance().value
                self.advance() # .
                if self.peek().value=='*':
                    self.advance(); return StarRef(table)
                col=self.expect(type='IDENT').value
                # allow keyword col
                return ColumnRef(col, table)
            # star
            if t.value=='*':
                self.advance(); return StarRef()
            self.advance()
            return ColumnRef(t.value)
        if t.value=='*':
            self.advance(); return StarRef()
        if t.value=='(':
            self.advance()
            # star ref? 
            e=self.expr()
            self.expect(value=')')
            return e
        raise MiniSQLError(f"Unexpected token {t}")

def parse_sql(sql):
    toks=tokenize_sql(sql)
    p=Parser(toks)
    # reset param counter per statement naturally
    return p.parse()

def parse_statements(sql):
    # split by semicolons respecting quotes/comments via tokenizer? We'll tokenize whole and split on semicolon tokens.
    toks=tokenize_sql(sql)
    stmts=[]
    buf=[]
    for t in toks:
        if t.type=='PUNCT' and t.value==';':
            if buf:
                # append EOF
                buf.append(Token('EOF', None, t.pos))
                p=Parser(buf)
                p.param_counter=0
                stmts.append(p.parse())
                buf=[]
        else:
            buf.append(t)
    if buf:
        # remove trailing EOF? tokenize appended EOF
        if not (buf and buf[-1].type=='EOF'):
            buf.append(Token('EOF', None, buf[-1].pos if buf else 0))
        # filter duplicate? 
        p=Parser(buf)
        p.param_counter=0
        # if only EOF, skip
        if not (len(buf)==1 and buf[0].type=='EOF'):
            stmts.append(p.parse())
    return stmts
