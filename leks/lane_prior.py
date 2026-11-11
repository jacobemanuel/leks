"""Prior-work check (tool tried): a short proposal in, at most 5 rows of what was already tried, done or rejected out.

It runs the same lanes as search, so it improves when the meaning lane is there. It decides nothing, it
only hands evidence over. The weak-match gate is word coverage: bm25 with OR always finds something,
so a row must hold most of the proposal's rare words.
"""
from __future__ import annotations

import math
import re
import sqlite3

from . import source_class, text

MAX_PROPOSAL = 600
MIN_COVER = 0.65
MIN_RARE = 2
RARE_IDF = 2.5
MAX_ROWS = 5
# nothing passed the gate: show this many rows search put first anyway, labelled
CLOSEST = 3
CLOSEST_CLASSES = ('decision', 'result')
# plain notes only count when the passage itself mentions an outcome
OUTCOME = re.compile(
    r'(?i)\b(reject\w*|withdrawn|withdrew|refuted|falsified|ruled out|not adopted|abandon\w*|shelved|tried|failed'
    r'|fails|retired|decided|decision|ruling|outcome|result\w*|measured|closed|dropped|null)\b'
)
CLASSES = ('frozen', 'decision', 'result', 'state', 'history', 'note')
KINDS = ('decision', 'result', 'note', 'state')


def _stem(t: str) -> str:
    return t[:5]


def _idf(count, n: int, terms: list[str]) -> dict:
    out = {}
    for t in terms:
        try:
            df = count(t)
        except sqlite3.Error:
            df = 0
        out[t] = math.log((n + 1.0) / (df + 1.0))
    return out


def cover(idf: dict, text_: str) -> tuple[float, int]:
    """Share of the proposal's weight found in this text, and how many rare words are among it."""
    have = {_stem(w.lower()) for w in re.findall(r'[^\W_]+', text_)}
    tot = sum(idf.values()) or 1.0
    got = rare = 0
    for t, w in idf.items():
        if _stem(t) in have:
            got += w
            rare += w >= RARE_IDF
    return got / tot, rare


def doc_rows(engine, proposal: str) -> tuple[list[dict], dict]:
    """Rows that pass the gate, best first, and the raw search result they came from."""
    r = engine.search(proposal, k=40, per_doc=1, priors=False, rerank=True)
    terms = [t.lower() for t in text.terms_of(proposal)]
    n = engine.db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]

    def count(t):
        return engine.db.execute('SELECT COUNT(*) FROM fts WHERE fts MATCH ?', ('"%s"' % t.replace('"', ''),)).fetchone()[0]

    idf = _idf(count, n, terms)
    rows = []
    for pos, x in enumerate(r.get('results', [])):
        if x['kind'] not in KINDS or x['source'] not in CLASSES:
            continue
        body = ' '.join((x.get('title') or '', x.get('heading') or '', x.get('_text') or ''))
        if x['source'] == 'note' and not OUTCOME.search(body):
            continue
        c, rare = cover(idf, body)
        if c >= MIN_COVER and rare >= MIN_RARE:
            rows.append((round(c, 3), pos, x))
    # near ties in coverage go by the order of trust, then by the search rank. this list only
    rank = {c: i for i, c in enumerate(source_class.TRUST)}
    rows.sort(key=lambda a: (-round(a[0], 1), rank.get(a[2]['source'], len(rank)), a[1]))
    return [x for _, _, x in rows], r


def closest(r: dict) -> list[dict]:
    """The first decision or result rows of a search, gate not asked. An old rejection is often filed as history."""
    out = []
    for x in r.get('results', []):
        if x['source'] in CLOSEST_CLASSES or x.get('kind') == 'decision':
            out.append(x)
            if len(out) >= CLOSEST:
                break
    return out


def _what(x: dict, proposal: str) -> str:
    return re.sub(r'\s+', ' ', text.best_window(x.get('_text') or x['snippet'], proposal, 180))


def tried(engine, proposal: str) -> str:
    """The text the tried tool returns."""
    from . import query
    proposal = (proposal or '')[:MAX_PROPOSAL]
    docs, r = doc_rows(engine, proposal)
    docs = docs[:MAX_ROWS]

    def mark(x):
        return ' | ' + query.CHANGED if engine.changed(x.get('path')) else ''

    line = query.lane_line(r) if r.get('lanes') else ''
    lines = []
    for x in docs:
        lines.append('%d. %s: %s | %s | %s | %s | %s' % (
            len(lines) + 1, (x.get('title') or '')[:70], _what(x, proposal), x.get('date') or 'no date',
            x['status'], x['source'], text.cite(x)) + mark(x))
    if not lines:
        near = closest(r)
        if not near:
            return 'nothing certain. No row holds enough of this proposal. ' + line
        lines.append('nothing certain. No row holds enough of this proposal. The nearest decision or result rows '
                     'follow, they share words and may be about something else. Read the cited lines before you '
                     'say tried or not tried.')
        for x in near:
            lines.append('closest, not a match: %s: %s | %s | %s | %s | %s' % (
                (x.get('title') or '')[:70], _what(x, proposal), x.get('date') or 'no date', x['status'],
                x['source'], text.cite(x)) + mark(x))
        lines.append(line)
        return '\n'.join(lines)
    lines.append('Evidence only, it decides nothing: read the cited lines. ' + line)
    return '\n'.join(lines)
