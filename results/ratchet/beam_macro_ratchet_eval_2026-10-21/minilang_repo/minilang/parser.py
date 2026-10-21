from typing import Any, List, Optional

from .lexer import Token, TokenType, tokenize


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, ttype):
        t = self.peek()
        if t.type != ttype:
            raise ParseError(f"expected {ttype} got {t.type}")
        return self.advance()

    def parse(self):
        stmts = self.parse_statements()
        if self.peek().type != TokenType.EOF:
            raise ParseError("unexpected token")
        return Program(stmts)

    def parse_statements(self):
        stmts = []
        while self.peek().type not in (TokenType.RBRACE, TokenType.EOF):
            stmts.append(self.parse_statement())
            if self.peek().type == TokenType.SEMI:
                self.advance()
        return stmts

    def parse_statement(self):
        t = self.peek()
        if t.type == TokenType.LET:
            self.advance()
            name = self.expect(TokenType.IDENT).value
            # let x = fn(...) { ... }
            if self.peek().type == TokenType.ASSIGN:
                self.advance()
                expr = self.parse_expr()
                return LetStmt(name, expr)
            raise ParseError("expected =")
        if t.type == TokenType.FN:
            return self.parse_fn_stmt()
        if t.type == TokenType.RETURN:
            self.advance()
            return ReturnStmt(self.parse_expr())
        if t.type == TokenType.IF:
            return self.parse_if()
        return self.parse_expr_stmt()

    def parse_fn_stmt(self):
        self.expect(TokenType.FN)
        name = self.expect(TokenType.IDENT).value
        self.expect(TokenType.LPAREN)
        params = self.parse_params()
        self.expect(TokenType.RPAREN)
        body = self.parse_block()
        return FnDef(name, params, body)

    def parse_params(self):
        params = []
        if self.peek().type == TokenType.RPAREN:
            return params
        params.append(self.expect(TokenType.IDENT).value)
        while self.peek().type == TokenType.COMMA:
            self.advance()
            params.append(self.expect(TokenType.IDENT).value)
        return params

    def parse_block(self):
        self.expect(TokenType.LBRACE)
        stmts = self.parse_statements()
        self.expect(TokenType.RBRACE)
        return Block(stmts)

    def parse_if(self):
        self.expect(TokenType.IF)
        # optional parens
        if self.peek().type == TokenType.LPAREN:
            self.advance()
            cond = self.parse_expr()
            self.expect(TokenType.RPAREN)
        else:
            cond = self.parse_expr()
        # branch
        if self.peek().type == TokenType.LBRACE:
            then_branch = self.parse_block()
        else:
            then_branch = self.parse_expr()
        else_branch = None
        if self.peek().type == TokenType.ELSE:
            self.advance()
            if self.peek().type == TokenType.IF:
                else_branch = self.parse_if()
            elif self.peek().type == TokenType.LBRACE:
                else_branch = self.parse_block()
            else:
                else_branch = self.parse_expr()
        return IfExpr(cond, then_branch, else_branch)

    def parse_expr_stmt(self):
        return self.parse_expr()

    # expressions
    def parse_expr(self):
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_additive()
        while self.peek().type in (TokenType.EQ, TokenType.NEQ, TokenType.LT, TokenType.GT, TokenType.LE, TokenType.GE):
            op = self.advance().value
            right = self.parse_additive()
            left = BinaryOp(op, left, right)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.peek().type in (TokenType.PLUS, TokenType.MINUS):
            op = self.advance().value
            right = self.parse_multiplicative()
            left = BinaryOp(op, left, right)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.peek().type in (TokenType.STAR, TokenType.SLASH):
            op = self.advance().value
            right = self.parse_unary()
            left = BinaryOp(op, left, right)
        return left

    def parse_unary(self):
        if self.peek().type in (TokenType.MINUS, TokenType.PLUS):
            op = self.advance().value
            return UnaryOp(op, self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t.type in (TokenType.INT, TokenType.FLOAT):
            self.advance()
            return NumberLiteral(t.value)
        if t.type == TokenType.STRING:
            self.advance()
            return StringLiteral(t.value)
        if t.type == TokenType.IDENT:
            self.advance()
            if self.peek().type == TokenType.LPAREN:
                self.advance()
                args = self.parse_args()
                self.expect(TokenType.RPAREN)
                return CallExpr(t.value, args)
            return Identifier(t.value)
        if t.type == TokenType.LPAREN:
            self.advance()
            e = self.parse_expr()
            self.expect(TokenType.RPAREN)
            return e
        if t.type == TokenType.IF:
            return self.parse_if()
        if t.type == TokenType.FN:
            # fn as expression: fn(params){...}
            self.advance()
            self.expect(TokenType.LPAREN)
            params = self.parse_params()
            self.expect(TokenType.RPAREN)
            body = self.parse_block()
            return FnDef("", params, body)
        raise ParseError(f"unexpected {t.type}")

    def parse_args(self):
        args = []
        if self.peek().type == TokenType.RPAREN:
            return args
        args.append(self.parse_expr())
        while self.peek().type == TokenType.COMMA:
            self.advance()
            args.append(self.parse_expr())
        return args


def parse(src):
    tokens = tokenize(src)
    return Parser(tokens).parse()


# export Program etc via ast_nodes? keep here
from .ast_nodes import *  # noqa
