"""Small text helpers for search: query terms, dates, snippets, citations."""
from __future__ import annotations

import re

# words that carry no meaning for a lookup
STOP = set(
    'a an and are as at be been but by can could did do does for from had has have how i in into is it its me my '
    'of on or our should so than that the their them then there these they this to was we were what when where '
    'which who whom why will with would you your about after again all also am any because before being between '
    'both each few more most other over same some such through under until very'.split()
)

# words that show up in questions about notes but not in the notes themselves
NOISE = set(
    'note notes file files document documents record records recorded holds hold held kept keep say says said '
    'tell tells told mention mentions mentioned listed lists show shows shown give gives given one ones thing '
    'things use used using get got make made need needs want wants'.split()
)

# the question asks what holds now
# a bare "still" or "status" is not enough: "while the tile is still loading" asks about behaviour, not about now
CURRENT = re.compile(
    r'(?i)(\b(current|currently|now|today|latest|newest|state of|status of|state is|status is|next|active|open|opens'
    r'|who owns|still (open|active|valid|true|needed|in use|current|used|pending|unresolved|the case)'
    r'|at the moment|yet|has .* been|is .* done)\b'
    r'|\b(state|status)\s*[?.!]?\s*$)'
)

# the question asks where something is
WHICH = re.compile(
    r'(?i)^\s*(where|which (note|file|document|folder|decision|handoff|record|page|report)'
    r'|what (note|file|document|folder))\b'
)

# exact names: ADR-004, tile_cache.py, v2.1, T16
NAME = re.compile(
    r'[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)+|\b[A-Z]{1,6}\d[A-Za-z0-9.]*\b|\b\d+\.\d+(?:\.\d+)?\b|\b[A-Z]\d[a-z]?\b'
)

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        'january february march april may june july august september october november december'.split()
    )
}

_MONTH_RX = '|'.join(MONTHS)


def terms_of(q: str) -> list[str]:
    """Search words of a query: stop words and noise out, order kept, no repeats."""
    t = [x for x in re.findall(r'[^\W_]+', q) if x.lower() not in STOP and len(x) > 1]
    if not t:
        return re.findall(r'[^\W_]+', q)
    keep = [x for x in t if x.lower() not in NOISE]
    return list(dict.fromkeys(keep if len(keep) >= 2 else t))


def dates_in(q: str) -> list[tuple[int, int | None]]:
    """Dates the question points at as (month, day or None): full date, day and month, or a month alone."""
    out: list[tuple[int, int | None]] = []
    for _y, m, d in re.findall(r'\b(20\d\d)-(\d\d)-(\d\d)\b', q):
        out.append((int(m), int(d)))
    for d, m in re.findall(r'(?i)\b(\d{1,2})(?:st|nd|rd|th)?\s+(' + _MONTH_RX + r')\b', q):
        out.append((MONTHS[m.lower()], int(d)))
    for m, d in re.findall(r'(?i)\b(' + _MONTH_RX + r')\s+(\d{1,2})\b', q):
        out.append((MONTHS[m.lower()], int(d)))
    if not out:
        # a bare "may" is a verb most of the time, so it counts only after "in"
        for m in re.findall(
            r'(?i)\b(january|february|march|april|june|july|august|september|october|november|december)\b|\bin (may)\b', q
        ):
            name = (m[0] or m[1]).lower()
            if name:
                out.append((MONTHS[name], None))
    return out


def snippet(t: str, q: str, n: int = 420) -> str:
    """A flat stretch of text around the first query word."""
    terms = [
        x.lower()
        for x in re.findall(r'[A-Za-z0-9]+', q)
        if x.lower() not in STOP and x.lower() not in NOISE and len(x) > 2
    ]
    low = t.lower()
    pos = [low.find(x) for x in terms if low.find(x) >= 0]
    a = max(0, min(pos) - 80) if pos else 0
    return re.sub(r'\s+', ' ', t[a:a + n])


def best_window(t: str, q: str, n: int) -> str:
    """The stretch of a passage with the most query words, cut at line or sentence ends."""
    t = re.sub(r'[ \t]+', ' ', t).strip()
    if len(t) <= n:
        return re.sub(r'\n+', ' / ', t)
    terms = [
        x.lower()[:6]
        for x in re.findall(r'[A-Za-z0-9]+', q)
        if x.lower() not in STOP and x.lower() not in NOISE and len(x) > 2
    ]
    parts = re.split(r'(?<=[.!?:;])\s+|\n+', t)
    best, best_s = 0, -1
    for i in range(len(parts)):
        size = 0
        sc = 0
        j = i
        while j < len(parts) and size + len(parts[j]) <= n:
            low = parts[j].lower()
            sc += sum(1 for x in terms if x in low)
            size += len(parts[j]) + 1
            j += 1
        if sc > best_s:
            best, best_s = i, sc
    out, size = [], 0
    for p in parts[best:]:
        if size + len(p) > n:
            break
        out.append(p)
        size += len(p) + 1
    return ' / '.join(out) if out else t[:n]


def unit_of(kind: str, unit: str | None) -> str:
    """What the numbers of a citation count: lines, unless the file is a document or an image."""
    if kind in ('document', 'image') and unit in ('page', 'paragraph', 'image'):
        return unit
    return 'line'


_SIGN = {'page': 'p.', 'paragraph': 'par. ', 'image': 'image '}


def cite(hit: dict) -> str:
    """'notes/a.md:12-30', 'docs/x.pdf p.3', 'docs/y.docx par. 4', 'docs/z.png image 1'."""
    a, _, b = str(hit['lines']).partition('-')
    u = hit.get('unit') or 'line'
    if u == 'line':
        return '%s:%s' % (hit['path'], hit['lines'])
    sign = _SIGN.get(u, u + ' ')
    return '%s %s%s' % (hit['path'], sign, a if (not b or a == b) else hit['lines'])


def fts_or(terms: list[str]) -> str:
    """An fts5 match string: every term quoted, joined by OR. Quotes inside a term are dropped."""
    return ' OR '.join('"%s"' % t.replace('"', '') for t in terms)
