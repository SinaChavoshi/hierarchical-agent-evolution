from dataclasses import dataclass
from typing import List, Any

from .ast_nodes import MiniSQLError

KEYWORDS = {
    'SELECT','FROM','WHERE','INSERT','INTO','VALUES','UPDATE','SET','DELETE',
    'CREATE','TABLE','INDEX','DROP','IF','NOT','EXISTS','AND','OR','NULL',
    'IS','IN','BETWEEN','LIKE','CASE','WHEN','THEN','ELSE','END','AS',
    'JOIN','INNER','LEFT','RIGHT','FULL','OUTER','ON','ORDER','BY','GROUP',
    'HAVING','LIMIT','OFFSET','DISTINCT','PRIMARY','KEY','UNIQUE','DEFAULT',
    'ASC','DESC','BEGIN','COMMIT','ROLLBACK','SAVEPOINT','TRANSACTION',
    'TRUE','FALSE',
}

@dataclass
class Token:
    type: str
    value: Any
    pos: int


def tokenize_sql(sql: str) -> List[Token]:
    tokens = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
            continue
        if c == '-' and i+1 < n and sql[i+1] == '-':
            # line comment
            j = sql.find('\n', i)
            if j == -1:
                j = n
            i = j
            continue
        if c == '/' and i+1 < n and sql[i+1] == '*':
            j = sql.find('*/', i+2)
            if j == -1:
                raise MiniSQLError(f"Unterminated block comment at {i}")
            i = j + 2
            continue
        if c == "'":
            # string
            j = i + 1
            buf = []
            while j < n:
                if sql[j] == "'":
                    if j+1 < n and sql[j+1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(sql[j])
                j += 1
            if j >= n:
                raise MiniSQLError(f"Unterminated string at {i}")
            tokens.append(Token('STRING', ''.join(buf), i))
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n and sql[j] != '"':
                buf.append(sql[j])
                j += 1
            if j >= n:
                raise MiniSQLError(f"Unterminated identifier at {i}")
            tokens.append(Token('IDENT', ''.join(buf), i))
            i = j + 1
            continue
        if c == '`':
            j = i + 1
            buf = []
            while j < n and sql[j] != '`':
                buf.append(sql[j])
                j += 1
            if j >= n:
                raise MiniSQLError(f"Unterminated identifier at {i}")
            tokens.append(Token('IDENT', ''.join(buf), i))
            i = j + 1
            continue
        if c.isdigit() or (c == '.' and i+1 < n and sql[i+1].isdigit()):
            j = i
            has_dot = False
            while j < n and (sql[j].isdigit() or sql[j] == '.'):
                if sql[j] == '.':
                    has_dot = True
                j += 1
            num_str = sql[i:j]
            if has_dot:
                tokens.append(Token('FLOAT', float(num_str), i))
            else:
                tokens.append(Token('INT', int(num_str), i))
            i = j
            continue
        if c == '?':
            tokens.append(Token('PARAM', '?', i))
            i += 1
            continue
        # operators
        two = sql[i:i+2]
        if two in ('==','!=','<>','<=','>=','||'):
            tokens.append(Token('OP', two, i))
            i += 2
            continue
        if c in ('=','<','>','+','-','*','/','%'):
            tokens.append(Token('OP', c, i))
            i += 1
            continue
        if c in ('(',')',',',';','.'):
            tokens.append(Token('PUNCT', c, i))
            i += 1
            continue
        if c.isalpha() or c == '_':
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == '_'):
                j += 1
            word = sql[i:j]
            if word.upper() in KEYWORDS:
                tokens.append(Token('KEYWORD', word.upper(), i))
            else:
                tokens.append(Token('IDENT', word, i))
            i = j
            continue
        raise MiniSQLError(f"Unexpected character {c!r} at {i}")
    return tokens
