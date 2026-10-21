from minilang import Token, TokenType, LexError, tokenize
import pytest

def test_multiline_string():
    toks = tokenize('"a\nb"')
    assert toks[0].value == "a\nb"
    assert toks[1].type == TokenType.EOF

def test_float_adjacent():
    toks = tokenize("1.5x")
    # float then ident
    assert toks[0].type == TokenType.FLOAT and toks[0].value == 1.5
    assert toks[1].type == TokenType.IDENT

def test_unknown():
    with pytest.raises(LexError):
        tokenize("@")

def test_empty():
    toks = tokenize("")
    assert toks[0].type == TokenType.EOF

def test_export():
    import minilang
    assert set(["Token","TokenType","LexError","tokenize"]).issubset(set(minilang.__all__))

if __name__ == "__main__":
    test_multiline_string()
    test_float_adjacent()
    test_unknown()
    test_empty()
    test_export()
    print("OK")
