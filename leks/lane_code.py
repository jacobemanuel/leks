"""Code lane: identifier parts for an extra word column, a symbols table (where is X defined),
a symbol lane for the fusion and a cheap near duplicate hash.

Stdlib only, no model. Python goes through ast (parse only, nothing from a scanned file runs).
The other languages use regex scanners, so they are good guesses: every symbol has to stand
on its own line or it is dropped.
"""
import ast
import hashlib
import re
import sqlite3
import warnings

LANG = {'.py': 'python', '.swift': 'swift', '.metal': 'metal', '.cs': 'csharp', '.sh': 'shell',
        '.bash': 'shell', '.zsh': 'shell', '.h': 'c', '.hpp': 'cpp', '.c': 'c', '.cc': 'cpp',
        '.cpp': 'cpp', '.m': 'objc', '.mm': 'objc'}
# these get text passages but no symbols
TEXT_ONLY_EXT = frozenset({'.js', '.ts', '.go', '.rs', '.java', '.kt'})
CODE_EXT = frozenset(LANG) | TEXT_ONLY_EXT

MAX_IDENT_TOKENS = 400
MAX_SIG = 160
# bump when extract() output changes, so code files get read once more
EXTRACT_VERSION = '1'
HEXISH = re.compile(r'^[0-9a-fA-F]{7,}$')
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_B1 = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')
_B2 = re.compile(r'(?<=[A-Z])(?=[A-Z][a-z])')
_B3 = re.compile(r'(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])')


def split_ident(name):
    """computeTileCost -> compute tile cost, HTTPServer -> http server. Lowercase, order kept."""
    out = []
    for part in re.split(r'[_\W]+', name):
        if not part:
            continue
        for a in _B2.split(_B1.sub(' ', part)):
            for b in a.split(' '):
                for c in _B3.split(b):
                    if c:
                        out.append(c.lower())
    return out


def _split_worthy(tok):
    # plain words already work in the text column, hashes would only add noise
    if len(tok) < 3 or len(tok) > 64 or HEXISH.match(tok):
        return False
    if '_' in tok:
        return True
    if tok.islower() or tok.isupper() and not re.search(r'\d', tok):
        return bool(re.search(r'[a-z]\d|\d[a-z]', tok))
    if tok.istitle():
        return False
    return True


def ident_column(text):
    """Extra fts column for one passage: parts of names the tokenizer keeps whole.
    Each part appears once, so a long file does not win on bm25 by repeating itself."""
    seen = {}
    for tok in IDENT.findall(text or ''):
        if not _split_worthy(tok):
            continue
        for p in split_ident(tok):
            if len(p) > 1 or p.isdigit():
                seen[p] = 1
        if len(seen) >= MAX_IDENT_TOKENS:
            break
    return ' '.join(seen)


def _sig(lines, i):
    return lines[i - 1].strip()[:MAX_SIG] if 0 < i <= len(lines) else ''


def _py(text, lines):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        tree = ast.parse(text)
    out = []

    def walk(node, parent):
        for n in ast.iter_child_nodes(node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = 'class' if isinstance(n, ast.ClassDef) else (
                    'method' if parent and parent[1] == 'class' else 'function')
                # with decorators lineno can point above the def line, walk down to the real one
                ln = n.lineno
                while (ln <= len(lines) and not re.match(r'\s*(async\s+def|def|class)\b', lines[ln - 1])
                       and ln < n.lineno + 40):
                    ln += 1
                out.append((n.name, kind, ln, getattr(n, 'end_lineno', ln) or ln, parent[0] if parent else ''))
                walk(n, (((parent[0] + '.') if parent else '') + n.name, 'class' if kind == 'class' else 'function'))
            elif parent is None and isinstance(n, (ast.Assign, ast.AnnAssign)):
                ts = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in ts:
                    if isinstance(t, ast.Name):
                        out.append((t.id, 'variable', n.lineno, getattr(n, 'end_lineno', n.lineno) or n.lineno, ''))
            elif isinstance(n, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                walk(n, parent)
    walk(tree, None)
    return out


_PY_RX = re.compile(r'^(\s*)(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)')


def _py_rx(lines):
    # fallback for a file ast refuses: the def and class lines are still worth having
    out = []
    for i, line in enumerate(lines, 1):
        m = _PY_RX.match(line)
        if m:
            out.append((m.group(3), 'class' if m.group(2) == 'class' else 'function', i, i, ''))
    return out


def _clean(line, in_block):
    # one line without strings and comments so braces count right. multi-line strings are not tracked,
    # the own-line check in extract() catches what that breaks
    out = []
    i = 0
    n = len(line)
    while i < n:
        if in_block:
            j = line.find('*/', i)
            if j < 0:
                return ''.join(out), True
            i = j + 2
            in_block = False
            continue
        c = line[i]
        if c == '/' and i + 1 < n and line[i + 1] == '/':
            break
        if c == '/' and i + 1 < n and line[i + 1] == '*':
            in_block = True
            i += 2
            continue
        if c == '"':
            j = i + 1
            while j < n and line[j] != '"':
                j += 2 if line[j] == '\\' else 1
            out.append('""')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return ''.join(out), in_block


_SW_MOD = (r'(?:(?:public|private|internal|fileprivate|open|final|static|class|override|mutating|nonmutating|'
           r'indirect|convenience|required|lazy|weak|unowned|nonisolated|isolated|dynamic|package|consuming|'
           r'borrowing)(?:\(set\))?\s+|@\w+(?:\([^)]*\))?\s+)*')
_SW_TYPE = re.compile(r'^\s*' + _SW_MOD + r'(class|struct|enum|protocol|actor|extension|typealias)\s+([A-Za-z_][\w.]*)')
_SW_FUNC = re.compile(r'^\s*' + _SW_MOD + r'func\s+([A-Za-z_]\w*|[^\s(]+)')
_SW_INIT = re.compile(r'^\s*' + _SW_MOD + r'(init|deinit)\b')
_SW_VAR = re.compile(r'^\s*' + _SW_MOD + r'(?:let|var)\s+([A-Za-z_]\w*)')
_SW_CASE = re.compile(r'^\s*(?:indirect\s+)?case\s+([A-Za-z_]\w*)')
_C_TYPE = re.compile(r'^\s*(?:typedef\s+)?(?:(?:public|private|protected|internal|static|sealed|abstract|partial|'
                     r'readonly|unsafe|new)\s+)*(class|struct|interface|enum(?:\s+class)?|record|namespace|union)'
                     r'\s+(?:\[\[[^\]]*\]\]\s*)?([A-Za-z_][\w.:]*)')
_CS_DELEG = re.compile(r'^\s*(?:(?:public|private|protected|internal|static)\s+)*delegate\s+[\w<>,\[\]\s.?]+?'
                       r'\s+([A-Za-z_]\w*)\s*[(<]')
_CS_PROP = re.compile(r'^\s*(?:\[[^\]]*\]\s*)*(?:(?:public|private|protected|internal|static|virtual|override|'
                      r'abstract|sealed|readonly|new)\s+)+(?:[\w<>,\[\]?.]+\s+)+([A-Za-z_]\w*)\s*(?:\{|=>|=|;)')
_CS_ARROW = re.compile(r'^\s*(?:\[[^\]]*\]\s*)*(?:(?:public|private|protected|internal|static|virtual|override|'
                       r'abstract|sealed|readonly|async|new)\s+)+[\w<>,\[\]?.\s]+?\s([A-Za-z_]\w*)\s*\([^;{]*\)'
                       r'\s*(?:=>|;)')
_METAL_FN = re.compile(r'^\s*(?:\[\[[^\]]*\]\]\s*)?(kernel|vertex|fragment)\s+[\w:<>,\s*&]+?\s+([A-Za-z_]\w*)\s*\(')
_C_FN = re.compile(r'^\s*(?:\[[^\]]*\]\s*)*(?:template\s*<[^>]*>\s*)?(?:(?:static|inline|constexpr|extern|virtual|'
                   r'override|public|private|protected|internal|async|unsafe|sealed|abstract|new|const|readonly)'
                   r'\s+)*[A-Za-z_][\w:<>,\[\]?.*&\s]*?[\s*&]([A-Za-z_~][\w:~]*)\s*\([^;]*$')
_C_MACRO = re.compile(r'^\s*#\s*define\s+([A-Za-z_]\w*)')
_C_TDEF_END = re.compile(r'^\s*\}\s*([A-Za-z_]\w*)\s*;')
_C_USING = re.compile(r'^\s*(?:typedef\s+.*\s|using\s+)([A-Za-z_]\w*)\s*(?:=.*)?;')
_C_CONST = re.compile(r'^\s*(?:constant|constexpr|static\s+const|const)\s+[\w:<>,\s*&]+?\s+([A-Za-z_]\w*)\s*(?:=|\[|\{)')
_NOT_FN = {'if', 'for', 'while', 'switch', 'catch', 'return', 'sizeof', 'else', 'using', 'lock', 'foreach', 'new',
           'throw', 'do', 'nameof', 'typeof', 'await', 'assert', 'defined', 'static_assert', 'alignas', 'decltype',
           'case', 'default'}


def _brace_scan(lines, match):
    """One scanner for the brace languages. match(line, depth, scope_kind) gives
    (name, kind, opens_scope) or None, or a list of those. A scope ends when its brace closes,
    which sets end_line and tells nesting."""
    out = []
    stack = []
    depth = 0
    in_block = False
    pending = None
    for i, raw in enumerate(lines, 1):
        line, in_block = _clean(raw, in_block)
        if not line.strip():
            continue
        top = stack[-1] if stack else None
        hit = match(line, depth, top[1] if top else None)
        if hit:
            for name, kind, opens in (hit if isinstance(hit, list) else [hit]):
                parent = '.'.join(s[0] for s in stack if s[1] != 'block')
                rec = [name, kind, i, i, parent]
                out.append(rec)
                if opens:
                    pending = (name, kind, rec)
        opened = line.count('{')
        closed = line.count('}')
        if opened:
            if pending:
                stack.append((pending[0], pending[1], depth, pending[2]))
                pending = None
                opened -= 1
                depth += 1
            for _ in range(opened):
                stack.append(('', 'block', depth, None))
                depth += 1
        elif pending and line.rstrip().endswith(';'):
            pending = None
        for _ in range(closed):
            if stack:
                s = stack.pop()
                depth = s[2]
                if s[3] is not None:
                    s[3][3] = i
            else:
                depth = max(0, depth - 1)
    for s in stack:
        if s[3] is not None:
            s[3][3] = len(lines)
    return [tuple(r) for r in out]


def _swift_match(line, depth, scope):
    m = _SW_TYPE.match(line)
    if m:
        return (m.group(2), m.group(1), m.group(1) != 'typealias')
    m = _SW_FUNC.match(line)
    if m:
        return (m.group(1), 'method' if scope in ('class', 'struct', 'enum', 'protocol', 'actor', 'extension')
                else 'function', True)
    m = _SW_INIT.match(line)
    if m and scope in ('class', 'struct', 'enum', 'actor', 'extension', 'protocol'):
        return (m.group(1), 'init', True)
    if scope in (None, 'class', 'struct', 'enum', 'protocol', 'actor', 'extension'):
        m = _SW_VAR.match(line)
        if m:
            return (m.group(1), 'property' if scope else 'variable', False)
    if scope == 'enum':
        m = _SW_CASE.match(line)
        if m:
            return (m.group(1), 'case', False)
    return None


def _c_family_match(lang):
    types = ('class', 'struct', 'interface', 'enum', 'record', 'namespace', 'union')

    def match(line, depth, scope):
        if lang != 'csharp':
            m = _C_MACRO.match(line)
            if m:
                return (m.group(1), 'macro', False)
        if lang == 'metal':
            m = _METAL_FN.match(line)
            if m:
                return (m.group(2), m.group(1), True)
        if lang == 'csharp':
            m = _CS_DELEG.match(line)
            if m:
                return (m.group(1), 'delegate', False)
        m = _C_TYPE.match(line)
        if m and not line.rstrip().endswith(';') or m and '{' in line:
            k = m.group(1).split()[0]
            return (m.group(2), k, True)
        if scope in (None, 'namespace', 'class', 'struct', 'interface', 'record', 'union'):
            m = _C_TDEF_END.match(line)
            if m:
                return (m.group(1), 'typedef', False)
            m = _C_USING.match(line)
            if m and lang != 'csharp':
                return (m.group(1), 'typedef', False)
            m = _C_CONST.match(line)
            if m:
                return (m.group(1), 'constant', False)
            m = _C_FN.match(line)
            if m and m.group(1) not in _NOT_FN:
                return (m.group(1), 'method' if scope in types[:5] and scope != 'namespace' else 'function', True)
            if lang == 'csharp' and scope in ('class', 'struct', 'interface', 'record'):
                # expression bodied or abstract members have no body brace, the function regex wants none
                m = _CS_ARROW.match(line)
                if m and m.group(1) not in _NOT_FN:
                    return (m.group(1), 'method', False)
                m = _CS_PROP.match(line)
                if m and m.group(1) not in _NOT_FN:
                    return (m.group(1), 'property', '{' in line)
        return None
    return match


_SH_FN = re.compile(r'^\s*(?:function\s+)?([A-Za-z_][\w:-]*)\s*\(\s*\)\s*(?:\{|\(|$)|^\s*function\s+([A-Za-z_][\w:-]*)')
_SH_VAR = re.compile(r'^(?:export\s+|readonly\s+|declare\s+(?:-\w+\s+)?)?([A-Za-z_]\w*)=')
_SH_ALIAS = re.compile(r'^\s*alias\s+([A-Za-z_][\w-]*)=')


def _shell(lines):
    out = []
    here = None
    for i, line in enumerate(lines, 1):
        if here:
            if line.strip() == here:
                here = None
            continue
        m = re.search(r'<<-?\s*[\'"]?([A-Za-z_]\w*)[\'"]?', line)
        if line.lstrip().startswith('#'):
            continue
        fm = _SH_FN.match(line)
        if fm:
            out.append((fm.group(1) or fm.group(2), 'function', i, i, ''))
        else:
            vm = _SH_VAR.match(line)
            if vm:
                out.append((vm.group(1), 'variable', i, i, ''))
            else:
                am = _SH_ALIAS.match(line)
                if am:
                    out.append((am.group(1), 'alias', i, i, ''))
        if m:
            here = m.group(1)
    return out


def extract(text, ext):
    """(symbols, how). A symbol is (name, kind, line, end_line, parent, signature).
    `how` says what parsed the file and how many hits failed the own-line check."""
    lang = LANG.get(ext)
    if not lang:
        return [], 'no symbol scanner'
    lines = text.split('\n')
    how = lang
    try:
        if lang == 'python':
            try:
                raw = _py(text, lines)
                how = 'python ast'
            except (SyntaxError, ValueError, RecursionError, MemoryError):
                raw = _py_rx(lines)
                how = 'python regex, ast refused'
        elif lang == 'shell':
            raw = _shell(lines)
        elif lang == 'swift':
            raw = _brace_scan(lines, _swift_match)
        else:
            raw = _brace_scan(lines, _c_family_match(lang))
    except RecursionError:
        return [], lang + ' too deep to scan'
    out = []
    bad = 0
    seen = set()
    for name, kind, ln, end, parent in raw:
        # the name has to stand on its own line, else the scanner got lost
        if not (0 < ln <= len(lines)) or name.split('.')[-1] not in lines[ln - 1]:
            bad += 1
            continue
        # a one letter variable answers nothing
        if len(name) < 2 and kind in ('variable', 'property', 'constant', 'case'):
            continue
        key = (name, kind, ln)
        if key in seen:
            continue
        seen.add(key)
        out.append((name, kind, ln, max(end, ln), parent, _sig(lines, ln)))
    if bad:
        how += ', %d failed the line check' % bad
    return out, how


# profile database side

PROFILE_DDL = '''
CREATE TABLE IF NOT EXISTS symbols(id INTEGER PRIMARY KEY, doc INT, name TEXT, lname TEXT, kind TEXT, lang TEXT,
  line INT, end_line INT, parent TEXT, signature TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS symfts USING fts5(name, parts, parent, tokenize="unicode61 remove_diacritics 2",
  prefix='2 3');
CREATE TABLE IF NOT EXISTS chunk_nhash(chunk INTEGER PRIMARY KEY, nhash TEXT);
'''


def add_schema(c):
    c.executescript(PROFILE_DDL)


def normal_hash(text):
    """Case, digits, punctuation and spacing gone, then a short hash. Two passages that differ by a
    date or a heading mark count as one. Real paraphrase is not caught and is not meant to be."""
    t = re.sub(r'[^a-z]+', ' ', (text or '').lower()).strip()
    return hashlib.sha1(t.encode()).hexdigest()[:16] if len(t) >= 40 else None


def fill_profile(c, master, ids):
    """Copy the symbols of the docs this profile holds, then fill chunk_nhash.
    ids maps (root, rel) to docs.id. A doc that lost the dedupe has no id and no symbols here."""
    try:
        rows = master.execute('SELECT root,rel,name,kind,lang,line,end_line,parent,signature FROM symbols '
                              'ORDER BY root,rel,line,name').fetchall()
    except sqlite3.OperationalError:
        rows = []
    n = 0
    for root, rel, name, kind, lang, line, end, parent, sig in rows:
        d = ids.get((root, rel))
        if d is None:
            continue
        n += 1
        c.execute('INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (n, d, name, name.lower(), kind, lang, line, end, parent, sig))
        c.execute('INSERT INTO symfts(rowid,name,parts,parent) VALUES(?,?,?,?)',
                  (n, name, ' '.join(split_ident(name)), ' '.join(split_ident(parent or ''))))
    c.execute('CREATE INDEX IF NOT EXISTS i_symbols_lname ON symbols(lname)')
    c.execute('CREATE INDEX IF NOT EXISTS i_symbols_doc ON symbols(doc)')
    c.execute("INSERT INTO symfts(symfts) VALUES('optimize')")
    m = 0
    for cid, t in c.execute('SELECT id,text FROM chunks').fetchall():
        h = normal_hash(t)
        if h:
            c.execute('INSERT INTO chunk_nhash VALUES(?,?)', (cid, h))
            m += 1
    return dict(symbols=n, nhash=m)


# query side

# plain English words like method or class must not open the code lane. a name has to look like a
# name, or the sentence has to ask where something is defined
CODE_Q = re.compile(r'\b[a-z]+[a-z0-9]*[A-Z]\w*|\b[A-Z][a-z0-9]+[A-Z]\w*|\b[A-Za-z_][A-Za-z0-9]*_\w+\b|\b\w+\(\)|'
                    r'\b\w+(?:::|\.)\w+\(|\b(?:def|func|struct|enum)\s+[A-Za-z_]\w*|'
                    r'\bwhere\s+is\s+[A-Za-z_]\w*\s+defined\b')
_CODE_WORDS = ('def', 'func', 'struct', 'enum', 'where', 'is', 'defined')


def looks_like_code(q):
    return bool(CODE_Q.search(q or ''))


def code_terms(q):
    """The words of a query that look like names, whole."""
    out = []
    for m in CODE_Q.finditer(q or ''):
        for t in re.findall(r'[A-Za-z_]\w*', m.group(0)):
            if t not in _CODE_WORDS:
                out.append(t)
    return list(dict.fromkeys(out))


def has_symbols(db):
    try:
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='symbols'").fetchone())
    except sqlite3.Error:
        return False


def _fts_phrase(parts, prefix_last=True):
    if not parts:
        return None
    return ' AND '.join('"%s"%s' % (p, '*' if prefix_last and i == len(parts) - 1 else '')
                        for i, p in enumerate(parts))


def locate_symbol(db, name, kind=None, lang=None, n=20, exact_only=False):
    """Definitions of a name, best first: exact spelling, same name in any case, Parent.name,
    every part of the name, then a prefix of the last part. Live definitions come before history."""
    if not has_symbols(db):
        return []
    name = (name or '').strip()
    parent = None
    if '.' in name or '::' in name:
        bits = re.split(r'\.|::', name)
        parent, name = '.'.join(bits[:-1]), bits[-1]
    name = name.rstrip('()')
    if not name:
        return []
    cols = 's.id,s.name,s.kind,s.lang,s.line,s.end_line,s.parent,s.signature,d.path,d.status,d.date,d.rel'
    where = ''
    arg_tail = []
    if kind:
        where += ' AND s.kind=?'
        arg_tail.append(kind)
    if lang:
        where += ' AND s.lang=?'
        arg_tail.append(lang)
    found = {}

    def add(rows, tier):
        for r in rows:
            cur = found.get(r[0])
            if cur is None or tier < cur[0]:
                found[r[0]] = (tier, r)
    rows = db.execute('SELECT %s FROM symbols s JOIN docs d ON d.id=s.doc WHERE s.lname=?%s LIMIT 500'
                      % (cols, where), [name.lower()] + arg_tail).fetchall()
    add([r for r in rows if r[1] == name], 0)
    add([r for r in rows if r[1] != name], 1)
    parts = split_ident(name)
    m = _fts_phrase(parts)
    if m and len(found) < n and not exact_only:
        try:
            add(db.execute('SELECT %s FROM symfts f JOIN symbols s ON s.id=f.rowid JOIN docs d ON d.id=s.doc '
                           'WHERE symfts MATCH ?%s ORDER BY bm25(symfts, 4.0, 2.0, 1.0) LIMIT 200'
                           % (cols, where), ['{name parts} : (' + m + ')'] + arg_tail).fetchall(), 2)
        except sqlite3.OperationalError:
            pass
    out = []
    for tier, r in found.values():
        hist = 1 if r[9] in ('superseded', 'stale', 'historical-record') else 0
        pmiss = 0 if not parent or parent.lower() in (r[6] or '').lower() else 1
        # a definition beats a variable of the same name
        k = {'variable': 2, 'property': 1, 'case': 1, 'constant': 1, 'macro': 1}.get(r[2], 0)
        out.append(((pmiss, tier, hist, k, len(r[11] or '')), r))
    out.sort(key=lambda x: x[0])
    keys = ('name', 'kind', 'lang', 'line', 'end_line', 'parent', 'signature', 'path', 'status', 'date')
    return [dict(zip(keys, (r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]), strict=True)) for _, r in out[:n]]


def symbol_lane(db, q, n=40):
    """Chunk ids, best first, for the fusion: the passage that holds each definition. Empty unless
    the query looks like code. No vectors, no model."""
    if not looks_like_code(q) or not has_symbols(db):
        return []
    ids = []
    for t in code_terms(q):
        # exact names only here, parts of names are what the idents column is for
        for s in locate_symbol(db, t, n=15, exact_only=True):
            row = db.execute('SELECT c.id FROM chunks c JOIN docs d ON d.id=c.doc WHERE d.path=? AND '
                             'c.start_line<=? AND c.end_line>=? ORDER BY c.ord LIMIT 1',
                             (s['path'], s['line'], s['line'])).fetchone()
            if row:
                ids.append(row[0])
    return list(dict.fromkeys(ids))[:n]


def drop_near_dups(chunk_ids, db):
    """Among ranked chunk ids keep the first of each normalized text.
    Returns (kept, {dropped_id: kept_id})."""
    if not chunk_ids:
        return [], {}
    try:
        marks = ','.join('?' * len(chunk_ids))
        h = dict(db.execute('SELECT chunk,nhash FROM chunk_nhash WHERE chunk IN (%s)' % marks, chunk_ids).fetchall())
    except sqlite3.OperationalError:
        return list(chunk_ids), {}
    seen = {}
    kept = []
    gone = {}
    for c in chunk_ids:
        k = h.get(c)
        if k and k in seen:
            gone[c] = seen[k]
            continue
        if k:
            seen[k] = c
        kept.append(c)
    return kept, gone
