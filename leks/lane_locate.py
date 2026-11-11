"""Locate lane: find a file by name or path words, or a code definition by name.

A row is a location: name, size, hash when indexed, root. No file content is read here and no model
is used. The names come from the master database (every file the walk saw, indexed or not) and
are copied into each profile database with a small fts index over the name parts.
"""
from __future__ import annotations

import os
import re
import sqlite3

RULE = 'A row is a location: name, size, hash when indexed, root. Locate does not search content.'

PROFILE_DDL = '''
CREATE TABLE IF NOT EXISTS names(id INTEGER PRIMARY KEY, path TEXT, name TEXT, ext TEXT, folder TEXT, size INT,
  sha TEXT, root TEXT, class TEXT, mtime REAL, kind TEXT, status TEXT, date TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS namefts USING fts5(name, folder, content='',
  tokenize="unicode61 remove_diacritics 2", prefix='2 3');
CREATE TABLE IF NOT EXISTS folder_counts(folder TEXT PRIMARY KEY, depth INT, files INT, bytes INT);
'''

NOTE_EXT = frozenset({'.md', '.markdown', '.txt', '.rst', '.tex', '.html', '.htm'})
DOC_EXT = frozenset({'.pdf', '.docx'})
IMAGE_EXT = frozenset({'.png', '.jpg', '.jpeg', '.heic', '.tif', '.tiff', '.gif', '.bmp', '.webp'})
CODE_EXT_LOCAL = frozenset({
    '.py', '.swift', '.metal', '.cs', '.sh', '.bash', '.zsh', '.c', '.h', '.cpp', '.cc', '.hpp', '.m', '.mm',
    '.js', '.ts', '.go', '.rs', '.java', '.kt', '.toml', '.yml', '.yaml',
})
DATA_EXT = frozenset({
    '.json', '.jsonl', '.ndjson', '.csv', '.tsv', '.xml', '.sqlite', '.db', '.parquet', '.npy', '.npz', '.pkl',
    '.bin', '.h5', '.hdf5', '.ply', '.obj', '.glb', '.gltf', '.usd', '.usdz', '.fbx', '.stl', '.blend', '.xlsx',
    '.pptx',
})

_code_ext = None


def _code_extensions():
    """Code extensions: the local list plus whatever the code lane knows, if it is importable."""
    global _code_ext
    if _code_ext is None:
        ext = set(CODE_EXT_LOCAL)
        try:
            from . import lane_code
            ext |= set(getattr(lane_code, 'CODE_EXT', ()))
        except Exception:
            pass
        _code_ext = frozenset(ext)
    return _code_ext


def class_of(ext: str) -> str:
    """note, code, document, image, data or other, from the file extension."""
    ext = (ext or '').lower()
    if ext in NOTE_EXT:
        return 'note'
    if ext in _code_extensions():
        return 'code'
    if ext in DOC_EXT:
        return 'document'
    if ext in IMAGE_EXT:
        return 'image'
    if ext in DATA_EXT:
        return 'data'
    return 'other'


_PART = re.compile(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+')


def _split(token: str) -> list[str]:
    """tileCache -> tile cache, HTTPServer -> http server, v2 -> v 2. Lowercase."""
    return [p.lower() for p in _PART.findall(token)]


def _tok(s: str) -> str:
    """Name tokens as written plus their parts, so tile_cache is found by tile, cache and tilecache."""
    out: dict[str, int] = {}
    for t in re.findall(r'[A-Za-z0-9_]+', s or ''):
        # as written, without underscores (tile_cache -> tilecache), then the parts
        for x in [t.lower(), t.lower().replace('_', '')] + _split(t):
            if x:
                out[x] = 1
    return ' '.join(out)


def has_names(db) -> bool:
    try:
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='names'").fetchone())
    except sqlite3.Error:
        return False


def fill_profile(c, master, roots: list[str]) -> dict:
    """Copy the names of the given roots into a profile database that is being built.

    c: the profile connection (docs table already filled). master: the master connection or None.
    Metadata comes from the profile's docs first, then from the master file rows.
    """
    c.executescript(PROFILE_DDL)
    roots = list(roots)
    meta = {}
    for path, kind, status, date, size, sha, mt in c.execute(
            'SELECT path, kind, status, date, size, sha256, mtime FROM docs'):
        meta[path] = (kind, status, date, size, sha, mt)
    rows = {}
    if master is not None and roots:
        marks = ','.join('?' * len(roots))
        try:
            got = master.execute(
                'SELECT n.root, n.rel, n.size, n.mtime, n.class, f.kind, f.status, f.date, f.sha256 '
                'FROM names n LEFT JOIN files f ON f.root = n.root AND f.rel = n.rel '
                'WHERE n.root IN (%s)' % marks, roots).fetchall()
        except sqlite3.Error:
            got = []
        for root, rel, size, mt, cls, kind, status, date, sha in got:
            rows[root + '/' + rel] = (size, mt, cls, kind, status, date, sha)
    n = 0
    tot: dict[str, list] = {}
    for p in sorted(set(rows) | set(meta)):
        r = rows.get(p)
        m = meta.get(p)
        if p.split('/', 1)[0] not in roots:
            continue
        name = os.path.basename(p)
        folder = os.path.dirname(p)
        ext = os.path.splitext(name)[1].lower()
        size = (r[0] if r else m[3]) or 0
        mt = (r[1] if r else m[5]) or (m[5] if m else 0.0) or 0.0
        cls = (r[2] if r and r[2] else None) or class_of(ext)
        kind = m[0] if m else (r[3] if r else None)
        status = m[1] if m else (r[4] if r else None)
        date = m[2] if m else (r[5] if r else None)
        sha = (m[4] if m and m[4] else '') or (r[6] if r and r[6] else '')
        n += 1
        c.execute('INSERT INTO names VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (n, p, name, ext, folder, size, sha, p.split('/', 1)[0], cls, mt, kind, status, date))
        c.execute('INSERT INTO namefts(rowid, name, folder) VALUES(?,?,?)',
                  (n, _tok(os.path.splitext(name)[0]) + ' ' + ext.lstrip('.'), _tok(folder)))
        parts = folder.split('/')
        for i in range(1, min(len(parts), 5) + 1):
            k = '/'.join(parts[:i])
            if k:
                t = tot.setdefault(k, [i, 0, 0])
                t[1] += 1
                t[2] += size
    c.execute('CREATE INDEX IF NOT EXISTS i_names_ext ON names(ext)')
    c.execute('CREATE INDEX IF NOT EXISTS i_names_name ON names(name)')
    c.execute('CREATE INDEX IF NOT EXISTS i_names_root ON names(root)')
    c.executemany('INSERT OR REPLACE INTO folder_counts VALUES(?,?,?,?)',
                  [(k, v[0], v[1], v[2]) for k, v in tot.items()])
    c.execute("INSERT INTO namefts(namefts) VALUES('optimize')")
    return {'names': n, 'folders': len(tot)}


# query side
_UNIT = {'k': 1024, 'm': 1024 ** 2, 'g': 1024 ** 3, '': 1}
_FILTER = re.compile(r'(?i)^(ext|folder|in|root|class|size|n|symbol):(.+)$')
_SIZE = re.compile(r'(?i)^([<>]?)(\d+(?:\.\d+)?)([kmg]b?)?$')


def parse(q: str) -> dict:
    """Free words plus filters ext: folder: (or in:) root: class: size: n: symbol:.

    An unknown filter word stays a free word, a path can hold a colon.
    """
    f = {'terms': [], 'ext': [], 'folder': [], 'root': None, 'cls': None, 'smin': None, 'smax': None,
         'n': None, 'symbol': None}
    for w in re.findall(r'"[^"]+"|\S+', q or ''):
        w = w.strip('"')
        m = _FILTER.match(w)
        if not m:
            f['terms'].append(w)
            continue
        k, v = m.group(1).lower(), m.group(2)
        if k == 'ext':
            f['ext'] += ['.' + x.lower().lstrip('.') for x in v.split(',') if x.strip('.')]
        elif k in ('folder', 'in'):
            f['folder'].append(v)
        elif k == 'root':
            f['root'] = v.lower()
        elif k == 'class':
            f['cls'] = v.lower()
        elif k == 'n' and v.isdigit():
            f['n'] = max(1, min(int(v), 200))
        elif k == 'symbol':
            f['symbol'] = v
        elif k == 'size':
            sm = _SIZE.match(v)
            if sm:
                unit = (sm.group(3) or '')[:1].lower()
                val = int(float(sm.group(2)) * _UNIT[unit])
                if sm.group(1) == '<':
                    f['smax'] = val
                else:
                    f['smin'] = val
    return f


_COLS = 'n.id,n.path,n.name,n.ext,n.folder,n.size,n.sha,n.root,n.class,n.mtime,n.kind,n.status,n.date'
_HIST = re.compile(r'(?i)(/archive/|/history/|_history|/before/)')


def _out(r) -> dict:
    return {'path': r[1], 'size': r[5], 'root': r[7], 'class': r[8], 'sha': r[6] or '', 'kind': r[10],
            'status': r[11], 'date': r[12]}


def _rank(rows, terms):
    """Exact file name, exact stem, every word in the name, then the fts order. Live above history, short above long."""
    low = [t.lower() for t in terms]
    joined = ' '.join(low)
    scored = []
    for pos, r in enumerate(rows):
        nm = r[2].lower()
        stem = os.path.splitext(nm)[0]
        exact = 0 if nm in low or nm == joined else (1 if stem in low or stem == joined else 2)
        allin = 0 if all(t in nm for t in low) else 1
        hist = 1 if (r[11] in ('superseded', 'stale', 'historical-record') or _HIST.search(r[1])) else 0
        scored.append(((exact, allin, hist, pos, len(r[1])), r))
    scored.sort(key=lambda x: x[0])
    return [r for _, r in scored]


def _symbols(db, f, terms, lim):
    """Definitions for symbol: or for a query that looks like a code name."""
    try:
        from . import lane_code
    except Exception:
        return []
    out = []
    try:
        if f['symbol']:
            out = list(lane_code.locate_symbol(db, f['symbol'], n=lim))
        elif terms and lane_code.looks_like_code(' '.join(terms)):
            for t in lane_code.code_terms(' '.join(terms)):
                out += lane_code.locate_symbol(db, t, n=10)
    except sqlite3.Error:
        return []
    seen, keep = set(), []
    for s in out:
        key = (s.get('path'), s.get('name'), s.get('line'))
        if key not in seen:
            seen.add(key)
            keep.append(s)
    return keep


def locate(db, q: str, n: int = 15) -> dict:
    """What the locate tool calls.

    Returns files (ranked rows), total (matches), matched_by, by_folder (top folders of the matches),
    symbols (definitions when the query asks for one) and rule. Every path is a display path.
    """
    f = parse(q)
    if not has_names(db):
        return {'files': [], 'total': 0, 'matched_by': None, 'by_folder': [], 'symbols': [], 'rule': RULE,
                'note': 'no names table in this database yet'}
    lim = f['n'] or n
    where, arg = [], []
    if f['ext']:
        where.append('n.ext IN (%s)' % ','.join('?' * len(f['ext'])))
        arg += f['ext']
    for x in f['folder']:
        where.append('n.folder LIKE ?')
        arg.append('%' + x + '%')
    if f['root']:
        where.append('n.root = ?')
        arg.append(f['root'])
    if f['cls']:
        where.append('n.class = ?')
        arg.append(f['cls'])
    if f['smin'] is not None:
        where.append('n.size >= ?')
        arg.append(f['smin'])
    if f['smax'] is not None:
        where.append('n.size <= ?')
        arg.append(f['smax'])
    w = (' AND ' + ' AND '.join(where)) if where else ''
    terms = [t for t in f['terms'] if re.search(r'[A-Za-z0-9]', t)]
    syms = _symbols(db, f, terms, lim) if (f['symbol'] or terms) else []
    res, total, by, how = [], 0, [], None
    by_sql = ('SELECT n.folder, COUNT(*), SUM(n.size) FROM %s WHERE %s GROUP BY n.folder '
              'ORDER BY 2 DESC, 1 LIMIT 12')
    if terms:
        toks = []
        for t in terms:
            toks += re.findall(r'[A-Za-z0-9]+', t)
        if toks:
            for label, joint in (('all words', ' AND '), ('any word', ' OR ')):
                m = joint.join('"%s"*' % x for x in toks)
                base = 'namefts f JOIN names n ON n.id = f.rowid'
                try:
                    total = db.execute('SELECT COUNT(*) FROM ' + base + ' WHERE namefts MATCH ?' + w,
                                       [m] + arg).fetchone()[0]
                except sqlite3.OperationalError:
                    total = 0
                if total:
                    how = label
                    rows = db.execute('SELECT ' + _COLS + ' FROM ' + base + ' WHERE namefts MATCH ?' + w
                                      + ' ORDER BY bm25(namefts, 10.0, 2.0) LIMIT 1000', [m] + arg).fetchall()
                    res = _rank(rows, toks + terms)
                    by = db.execute(by_sql % (base, 'namefts MATCH ?' + w), [m] + arg).fetchall()
                    break
    elif where:
        how = 'filters only'
        total = db.execute('SELECT COUNT(*) FROM names n WHERE 1=1' + w, arg).fetchone()[0]
        res = db.execute('SELECT ' + _COLS + ' FROM names n WHERE 1=1' + w + ' ORDER BY n.mtime DESC LIMIT ?',
                         arg + [lim]).fetchall()
        by = db.execute(by_sql % ('names n', '1=1' + w), arg).fetchall()
    return {'files': [_out(r) for r in res[:lim]], 'total': total, 'matched_by': how,
            'by_folder': [{'folder': a, 'files': b, 'bytes': c or 0} for a, b, c in by],
            'symbols': syms[:lim], 'rule': RULE}
