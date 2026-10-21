from dataclasses import dataclass
from enum import Enum
from typing import Any, List


class TokenType(Enum):
    INT = "INT"
    FLOAT = "FLOAT"
    STRING = "STRING"
    IDENT = "IDENT"
    LET = "LET"
    FN = "FN"
    IF = "IF"
    ELSE = "ELSE"
    RETURN = "RETURN"
    PLUS = "PLUS"
    MINUS = "MINUS"
    STAR = "STAR"
    SLASH = "SLASH"
    EQ = "EQ"
    NEQ = "NEQ"
    LT = "LT"
    GT = "GT"
    LE = "LE"
    GE = "GE"
    ASSIGN = "ASSIGN"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    LBRACE = "LBRACE"
    RBRACE = "RBRACE"
    COMMA = "COMMA"
    SEMI = "SEMI"
    EOF = "EOF"


@dataclass
class Token:
    type: TokenType
    value: Any
    line: int = 1
    col: int = 1


class LexError(Exception):
    def __init__(self, message: str, line: int = 1, col: int = 1):
        super().__init__(f"{message} at line {line}, col {col}")
        self.message = message
        self.line = line
        self.col = col

    def __str__(self):
        return f"{self.message} at line {self.line}, col {self.col}"


KEYWORDS = {
    "let": TokenType.LET,
    "fn": TokenType.FN,
    "if": TokenType.IF,
    "else": TokenType.ELSE,
    "return": TokenType.RETURN,
}


def tokenize(src: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0
    n = len(src)
    line = 1
    col = 1

    while i < n:
        ch = src[i]
        if ch == "\n":
            i += 1
            line += 1
            col = 1
            continue
        if ch.isspace():
            i += 1
            col += 1
            continue
        if ch == "#":
            while i < n and src[i] != "\n":
                i += 1
                col += 1
            continue
        start_line = line
        start_col = col
        if ch.isdigit():
            j = i
            is_float = False
            while j < n and src[j].isdigit():
                j += 1
            if j < n and src[j] == "." and j + 1 < n and src[j + 1].isdigit():
                is_float = True
                j += 1
                while j < n and src[j].isdigit():
                    j += 1
            num_str = src[i:j]
            if is_float:
                tokens.append(Token(TokenType.FLOAT, float(num_str), start_line, start_col))
            else:
                tokens.append(Token(TokenType.INT, int(num_str), start_line, start_col))
            consumed = j - i
            i = j
            col += consumed
            continue
        if ch == '"' or ch == "'":
            quote = ch
            j = i + 1
            chars = []
            cur_line = line
            cur_col = col
            while j < n and src[j] != quote:
                if src[j] == "\n":
                    cur_line += 1
                    cur_col = 1
                else:
                    cur_col += 1
                chars.append(src[j])
                j += 1
            if j >= n:
                raise LexError("Unterminated string", start_line, start_col)
            tokens.append(Token(TokenType.STRING, "".join(chars), start_line, start_col))
            consumed = j - i + 1
            i = j + 1
            col += consumed
            # adjust line/col if multiline string
            # recompute from start
            # simpler: track via loop already; set line/col based on content
            # Recompute line/col accurately:
            segment = src[i - consumed:i]
            # count newlines
            nl = segment.count("\n")
            if nl:
                line = start_line + nl
                last_nl = segment.rfind("\n")
                col = (len(segment) - last_nl)
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            ttype = KEYWORDS.get(word, TokenType.IDENT)
            tokens.append(Token(ttype, word, start_line, start_col))
            consumed = j - i
            i = j
            col += consumed
            continue
        # operators
        two = src[i:i + 2]
        if two == "==":
            tokens.append(Token(TokenType.EQ, "==", start_line, start_col)); i += 2; col += 2; continue
        if two == "!=":
            tokens.append(Token(TokenType.NEQ, "!=", start_line, start_col)); i += 2; col += 2; continue
        if two == "<=":
            tokens.append(Token(TokenType.LE, "<=", start_line, start_col)); i += 2; col += 2; continue
        if two == ">=":
            tokens.append(Token(TokenType.GE, ">=", start_line, start_col)); i += 2; col += 2; continue
        single_map = {
            "+": TokenType.PLUS,
            "-": TokenType.MINUS,
            "*": TokenType.STAR,
            "/": TokenType.SLASH,
            "<": TokenType.LT,
            ">": TokenType.GT,
            "=": TokenType.ASSIGN,
            "(": TokenType.LPAREN,
            ")": TokenType.RPAREN,
            "{": TokenType.LBRACE,
            "}": TokenType.RBRACE,
            ",": TokenType.COMMA,
            ";": TokenType.SEMI,
        }
        if ch in single_map:
            tokens.append(Token(single_map[ch], ch, start_line, start_col))
            i += 1
            col += 1
            continue
        raise LexError(f"Unknown character {ch!r}", start_line, start_col)

    tokens.append(Token(TokenType.EOF, None, line, col))
    return tokens
