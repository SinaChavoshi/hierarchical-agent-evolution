import re
from .ast_nodes import MiniSQLError

KEYWORDS = {
 'SELECT','FROM','WHERE','INSERT','INTO','VALUES','UPDATE','SET','DELETE','CREATE','TABLE','INDEX','DROP','IF','NOT','EXISTS','JOIN','INNER','LEFT','RIGHT','FULL','OUTER','CROSS','ON','GROUP','BY','HAVING','ORDER','ASC','DESC','LIMIT','OFFSET','AND','OR','IS','NULL','IN','BETWEEN','LIKE','CASE','WHEN','THEN','ELSE','END','DISTINCT','AS','BEGIN','TRANSACTION','COMMIT','ROLLBACK','SAVEPOINT','RELEASE','TO','DEFAULT','PRIMARY','KEY','UNIQUE','INT','INTEGER','REAL','FLOAT','TEXT','VARCHAR','BOOL','BOOLEAN','COUNT','SUM','AVG','MIN','MAX','COALESCE','IFNULL','NULLIF','UPPER','LOWER','LENGTH','ABS','ROUND','TRUE','FALSE',
}

class Token:
    def __init__(self, type, value, pos):
        self.type = type
        self.value = value
        self.pos = pos
    def __repr__(self):
        return f"Token({self.type!r}, {self.value!r}, {self.pos!r})"

def tokenize_sql(sql):
    tokens = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
            continue
        if c == '-' and i+1 < n and sql[i+1] == '-':
            j = sql.find('\n', i)
            if j == -1: j = n
            i = j
            continue
        if c == '/' and i+1 < n and sql[i+1] == '*':
            j = sql.find('*/', i+2)
            if j == -1:
                raise MiniSQLError("Unterminated block comment")
            i = j + 2
            continue
        if c == "'":
            j = i+1
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
                raise MiniSQLError("Unterminated string")
            tokens.append(Token('STRING', ''.join(buf), i))
            i = j+1
            continue
        if c == '"':
            j = i+1
            buf = []
            while j < n and sql[j] != '"':
                buf.append(sql[j]); j += 1
            if j >= n:
                raise MiniSQLError("Unterminated identifier")
            tokens.append(Token('IDENT', ''.join(buf), i))
            i = j+1
            continue
        if c == '`':
            j = i+1
            buf = []
            while j < n and sql[j] != '`':
                buf.append(sql[j]); j += 1
            if j >= n:
                raise MiniSQLError("Unterminated identifier")
            tokens.append(Token('IDENT', ''.join(buf), i))
            i = j+1
            continue
        if c.isdigit() or (c == '.' and i+1 < n and sql[i+1].isdigit()):
            j = i
            has_dot = False
            while j < n and (sql[j].isdigit() or sql[j] == '.'):
                if sql[j] == '.':
                    has_dot = True
                j += 1
            num = sql[i:j]
            if has_dot:
                tokens.append(Token('NUMBER', float(num), i))
            else:
                tokens.append(Token('NUMBER', int(num), i))
            i = j
            continue
        if c == '?':
            tokens.append(Token('PARAM', '?', i)); i += 1; continue
        two = sql[i:i+2]
        if two in ('==','!=','<>','<=','>=','||'):
            tokens.append(Token('OP', two, i)); i += 2; continue
        if c in ('=','<','>','+','-','*','/','%'):
            tokens.append(Token('OP', c, i)); i += 1; continue
        if c in ('(',')',',',';','.'):
            tokens.append(Token('PUNCT', c, i)); i += 1; continue
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
    tokens.append(Token('EOF', None, n))
    return tokens
