"""Turning one file into front matter facts, a kind, a status and passages with line numbers."""
import html
import os
import re
import time

from . import config
from .lane_code import CODE_EXT

# config style files count as code for kind and chunking, they get no symbols
CONFIG_EXT = frozenset({'.toml', '.yml', '.yaml', '.json'})

STATUS_WORDS = {
    'current': 'current', 'active': 'current', 'accepted': 'current', 'approved': 'current',
    'live': 'current', 'in progress': 'current', 'in-progress': 'current',
    'done': 'record', 'final': 'record', 'closed': 'record', 'record': 'record',
    'published': 'record', 'released': 'record',
    'superseded': 'superseded', 'replaced': 'superseded', 'deprecated': 'superseded',
    'obsolete': 'superseded', 'withdrawn': 'superseded', 'rejected': 'superseded',
    'retired': 'superseded', 'historical': 'superseded',
    'stale': 'stale', 'outdated': 'stale',
}


def front(lines):
    """Simple key: value front matter between two --- lines at the top. Keys lowercased."""
    fm = {}
    if lines and lines[0].strip() == '---':
        for line in lines[1:60]:
            if line.strip() == '---':
                break
            m = re.match(r'^([A-Za-z_][\w-]*):\s*(.*)$', line)
            if m:
                fm[m.group(1).lower()] = m.group(2).strip()[:300]
    return fm


def body_start(lines):
    """Index of the first line after the front matter (0 when there is none)."""
    if lines and lines[0].strip() == '---':
        for i in range(1, min(len(lines), 60)):
            if lines[i].strip() == '---':
                return i + 1
    return 0


def _unquote(v):
    return (v or '').strip().strip('"\'`').strip()


def title_of(lines, fm, name):
    for line in lines[:80]:
        if line.startswith('# '):
            return line[2:].strip()[:160]
    t = _unquote(fm.get('title'))
    return t[:160] if t else name


def date_of(name, fm, mtime):
    for k in ('updated', 'date', 'created'):
        m = re.search(r'20\d\d-\d\d-\d\d', fm.get(k, ''))
        if m:
            return m.group(0)
    m = re.search(r'20\d\d-\d\d-\d\d', name)
    if m:
        return m.group(0)
    return time.strftime('%Y-%m-%d', time.gmtime(mtime))


def _hit(rel, classify, key):
    return any(config.glob_match(rel, p) for p in (classify.get(key) or ()))


def kind_of(root, rel, ext, classify):
    """state, rule, decision, result by glob, then by extension. history globs do not change the kind."""
    for k in ('state', 'rule', 'decision', 'result'):
        if _hit(rel, classify, k):
            return k
    ext = (ext or '').lower()
    if ext in CODE_EXT or ext in CONFIG_EXT:
        return 'code'
    if ext in ('.pdf', '.docx'):
        return 'document'
    if ext in config.IMAGE_EXT:
        return 'image'
    return 'note'


def _status_word(v):
    v = _unquote(v).lower()
    if not v:
        return None, False
    if v in STATUS_WORDS:
        return STATUS_WORDS[v], True
    # "accepted (2026-01-02)", "Done - shipped": try the leading words
    m = re.match(r'[a-z][a-z -]*', v)
    if m:
        lead = m.group(0).strip()
        if lead in STATUS_WORDS:
            return STATUS_WORDS[lead], True
        first = lead.split(' ')[0].strip('-')
        if first in STATUS_WORDS:
            return STATUS_WORDS[first], True
    return 'unknown', False


def status_of(kind, rel, fm, classify):
    """(status, status_src). path history first, then state notes, frozen, front matter, default."""
    if _hit(rel, classify, 'history'):
        return 'historical-record', 'path'
    if kind == 'state':
        return 'current', 'entry'
    raw = _unquote(fm.get('status')).lower()
    if _unquote(fm.get('frozen')).lower() in ('true', 'yes', '1') or raw in ('frozen', 'locked'):
        return 'record', 'frozen'
    if raw:
        word, known = _status_word(raw)
        if known:
            return word, 'front-matter'
        return 'unknown', 'front-matter-unchecked'
    return 'unknown', 'rule'


def strip_html(t):
    t = re.sub(r'(?is)<(script|style)\b.*?</\1>', lambda m: '\n' * m.group(0).count('\n'), t)
    t = re.sub(r'<[^>\n]+>', ' ', t)
    return html.unescape(t)


def chunks_of(lines, is_code, chunk_chars=1000):
    """Passages as (start_line, end_line, heading, text). Lines are 1-based and the file's own."""
    out = []
    head = ''
    buf = []
    size = 0
    start = 1
    piece = max(50, chunk_chars - 100)
    # the front matter rides along with the first heading, it is no passage of its own
    fm_end = 0 if is_code else body_start(lines)

    def flush(end):
        nonlocal buf, size
        t = '\n'.join(buf).strip()
        if t:
            out.append((start, end, head, t))
        buf, size = [], 0

    for i, line in enumerate(lines, 1):
        if not is_code and re.match(r'^#{1,4} ', line):
            if not (fm_end and i - 1 == fm_end and not out):
                flush(i - 1)
                start = i
            head = line.lstrip('# ').strip()[:120]
        elif is_code and size > 300 and re.match(
                r'^\s*(def |class |func |struct |function |extension |[A-Za-z_]+\(\) *\{)', line):
            flush(i - 1)
            start = i
            head = line.strip()[:120]
        if len(line) > chunk_chars:
            # a very long line becomes several pieces, each citing the same line
            flush(i - 1)
            for p in range(0, len(line), piece):
                out.append((i, i, head, line[p:p + chunk_chars]))
            start = i + 1
            continue
        if size + len(line) > chunk_chars and buf:
            flush(i - 1)
            start = i
        if not buf:
            start = i
        buf.append(line)
        size += len(line) + 1
    flush(len(lines))
    return out


def chunks_of_units(units, merge, chunk_chars=1000):
    """Passages for page, paragraph or image units: (first_unit, last_unit, heading, text).

    merge=False keeps every unit in passages of its own (pages: a citation is path and page).
    merge=True packs neighbours with the same heading up to chunk_chars.
    """
    out = []
    piece = max(50, chunk_chars - 100)
    buf, first, last, head, size = [], 0, 0, '', 0

    def flush():
        nonlocal buf, size
        t = '\n'.join(buf).strip()
        if t:
            out.append((first, last, head, t))
        buf, size = [], 0

    for n, h, t in units:
        t = (t or '').strip()
        if not t:
            continue
        h = h or ''
        if buf and (not merge or h != head or size + len(t) > chunk_chars):
            flush()
        if len(t) > chunk_chars:
            flush()
            for p in range(0, len(t), piece):
                out.append((n, n, h, t[p:p + chunk_chars]))
            continue
        if not buf:
            first, head = n, h
        buf.append(t)
        last = n
        size += len(t) + 1
    flush()
    return out


# versions and replacements between notes

EXPL = re.compile(r'(?i)(superseded|replaced|overtaken|obsoleted|withdrawn in favou?r of|see instead|moved to)'
                  r'\s*(by|with|in|:)?\s*[:\s`*]*\[\[([^\]|#]+)')
FM_SUP = re.compile(r'(?im)^superseded_by:\s*["\']?\[*([^\]\n"\']+)')
VER = re.compile(r'(?i)(?:^|[-_ .])(v|r|rev|revision)[-_ ]?(\d+(?:[._]\d+)*)(?=$|[-_ .])')


def succession(rows):
    """Which note has a newer one. Only two kinds of proof count: the note says so with a link,
    or the file name carries a version number inside a series. Shared dates or words prove nothing.

    rows: (doc_id, display_path, name, date, mtime, head_text).
    Returns {old_id: (new_id, how)}.
    """
    by_stem = {}
    for doc_id, path, name, date, mtime, _head in rows:
        by_stem.setdefault(os.path.splitext(name)[0].lower(), []).append((doc_id, path, date or '', mtime))
    edges = {}
    for doc_id, *_, head in rows:
        for rx, grp in ((EXPL, 3), (FM_SUP, 1)):
            for m in rx.finditer(head or ''):
                st = os.path.basename(m.group(grp).strip()).lower()
                st = st[:-3] if st.endswith('.md') else st
                cand = [c for c in by_stem.get(st, []) if c[0] != doc_id]
                if cand:
                    edges.setdefault(doc_id, (max(cand, key=lambda c: (c[2], c[3]))[0], 'the note says so'))
    series = {}
    for doc_id, path, name, date, mtime, _head in rows:
        b = os.path.splitext(name)[0]
        m = list(VER.finditer(b))
        if not m:
            continue
        v = tuple(int(x) for x in re.split(r'[._]', m[-1].group(2)))
        key = (os.path.dirname(path), VER.sub(' ', b).lower().strip(), os.path.splitext(name)[1])
        if len(re.findall(r'[a-z]{3,}', key[1])) >= 2:
            series.setdefault(key, []).append((v, date or '', mtime, doc_id))
    for members in series.values():
        if len(members) < 2:
            continue
        members.sort()
        top = members[-1][3]
        for x in members[:-1]:
            edges.setdefault(x[3], (top, 'version number in the name'))
    return edges
