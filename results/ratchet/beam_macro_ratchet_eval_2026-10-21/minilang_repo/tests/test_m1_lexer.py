from minilang.lexer import tokenize, TokenType, LexError


def types(src):
    return [t.type for t in tokenize(src)]


def test_ints():
    toks = tokenize("1 42 0")
    assert [t.type for t in toks[:-1]] == [TokenType.INT, TokenType.INT, TokenType.INT]
    assert [t.value for t in toks[:-1]] == [1, 42, 0]


def test_floats():
    toks = tokenize("1.5 3.0")
    assert [t.type for t in toks[:-1]] == [TokenType.FLOAT, TokenType.FLOAT]
    assert [t.value for t in toks[:-1]] == [1.5, 3.0]


def test_strings():
    toks = tokenize('"abc" \'def\'')
    assert [t.type for t in toks[:-1]] == [TokenType.STRING, TokenType.STRING]
    assert [t.value for t in toks[:-1]] == ["abc", "def"]


def test_keywords_identifiers():
    toks = tokenize("let fn if else return foo bar")
    assert [t.type for t in toks[:-1]] == [TokenType.LET, TokenType.FN, TokenType.IF, TokenType.ELSE, TokenType.RETURN, TokenType.IDENT, TokenType.IDENT]


def test_operators():
    toks = tokenize("+ - * / == != < > <= >= = ( ) { } , ;")
    expected = [TokenType.PLUS, TokenType.MINUS, TokenType.STAR, TokenType.SLASH, TokenType.EQ, TokenType.NEQ, TokenType.LT, TokenType.GT, TokenType.LE, TokenType.GE, TokenType.ASSIGN, TokenType.LPAREN, TokenType.RPAREN, TokenType.LBRACE, TokenType.RBRACE, TokenType.COMMA, TokenType.SEMI]
    assert [t.type for t in toks[:-1]] == expected


def test_comments():
    toks = tokenize("1 # comment\n2")
    assert [t.type for t in toks[:-1]] == [TokenType.INT, TokenType.INT]
    assert toks[1].line == 2


def test_positions():
    toks = tokenize("  42")
    assert toks[0].line == 1 and toks[0].col == 3


def test_lexerror_unterminated():
    for src in ['"abc', "'abc", '"abc\n']:
        try:
            tokenize(src)
            assert False
        except LexError as e:
            assert e.line >= 1 and e.col >= 1
            assert "line" in str(e) and "col" in str(e)


def test_unknown_char():
    try:
        tokenize("@")
        assert False
    except LexError:
        pass


def test_empty():
    toks = tokenize("")
    assert len(toks) == 1 and toks[0].type == TokenType.EOF

