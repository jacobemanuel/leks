"""Revisit lines: the decisions and notes that say when to look at them again.

A revisit line is `Revisit: <date or condition>` on a line of its own, maybe a list
item or in bold, outside code blocks; a `revisit:` key in the front matter reads
the same. A date (YYYY-MM-DD) at its start makes the line due from that day on.
Any other text is a condition: it is listed and never due, as no clock can tell
when it holds. A note may hold many revisit lines, one per entry of a decision
log, and each is listed with the heading above it. Notes marked superseded and
notes in a history folder are left out.

Read-only: the profile index finds the notes that mention revisit, the text on
disk gives the lines. `leks revisit` lists them, the session brief names the due
ones.
"""
from __future__ import annotations

import os
import re
import sqlite3

from . import text as text_mod

NOTE_KINDS = ('state', 'rule', 'decision', 'result', 'note')
SETTLED = ('superseded', 'historical-record')
MAX_READ = 1024 * 1024      # bytes read per note
MAX_ROWS = 2000             # notes read per call
MAX_PER_NOTE = 200          # revisit lines kept per note
MAX_TEXT = 200              # characters kept of a condition or a heading
_FENCE = re.compile(r' {0,3}(`{3,}|~{3,})')
_HEADING = re.compile(r' {0,3}#{1,6}[ \t]+(.*?)[ \t#]*$')


def _clean(s):
    s = ''.join(c if c.isprintable() else ' ' for c in s).strip()
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT - 3] + '...'


def lines_of(path):
    """[(line number, date or None, text, heading above it)] for each revisit line outside code blocks,
    [] when there is none or the file cannot be read."""
    try:
        with open(path, 'rb') as f:
            raw = f.read(MAX_READ)
    except OSError:
        return []
    out, fence, heading = [], None, ''
    # split at \n alone, as the build and read number lines: splitlines() also cuts at \u2028 or a form feed
    for i, line in enumerate(raw.decode('utf-8', errors='replace').replace('\r\n', '\n').split('\n'), 1):
        m = _FENCE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip(' `~'):
                fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        h = _HEADING.match(line)
        if h:
            heading = h.group(1)
            continue
        got = text_mod.revisit(line)
        if got:
            out.append((i, got[0], _clean(got[1]), _clean(heading)))
            if len(out) >= MAX_PER_NOTE:
                break
    return out


def _inside(cfg, path, ap):
    """The real file lies under its root, unless the root follows links on purpose: the rule read keeps."""
    try:
        root = cfg.root(path.split('/', 1)[0])
    except (KeyError, AttributeError):
        return False
    if root.follow_links:
        return True
    rp, base = os.path.realpath(ap), os.path.realpath(root.path)
    return rp == base or rp.startswith(base + os.sep)


def entries(conn, cfg, today, dated_only=False, ok=None, limit=MAX_ROWS):
    """Every revisit line of the notes the index holds, as dicts with path, line, state (due, ahead or
    when), date, text, title, kind and status, sorted: due oldest first, then ahead soonest first, then
    the conditions by path. cfg gives the file of a display path and its root; ok(path) may leave one out.
    A note that is now a link to a file outside its root is left out, as read leaves it."""
    q = '"revisit 20" *' if dated_only else 'revisit'
    try:
        rows = conn.execute(
            'SELECT DISTINCT d.path, d.kind, d.status FROM fts JOIN chunks ch ON ch.id = fts.rowid '
            'JOIN docs d ON d.id = ch.doc WHERE fts MATCH ? AND d.kind IN (%s) AND d.status NOT IN (%s) '
            'ORDER BY d.path LIMIT %d' % (','.join('?' * len(NOTE_KINDS)), ','.join('?' * len(SETTLED)), limit),
            (q,) + NOTE_KINDS + SETTLED).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for path, kind, status in rows:
        if ok is not None and not ok(path):
            continue
        try:
            ap = cfg.abs_path(path)
        except (KeyError, ValueError, TypeError):
            continue
        if not ap or not os.path.isfile(ap) or not _inside(cfg, path, ap):
            continue
        for line, date, said, title in lines_of(ap):
            if date is None and dated_only:
                continue
            state = 'when' if date is None else ('due' if date <= today else 'ahead')
            out.append({'path': path, 'line': line, 'state': state, 'date': date, 'text': said,
                        'title': title, 'kind': kind, 'status': status})
    order = {'due': 0, 'ahead': 1, 'when': 2}
    out.sort(key=lambda e: (order[e['state']], e['date'] or '', e['path'], e['line']))
    return out


def render(found, today, profile=None):
    """The text `leks revisit` prints: one line per revisit line, the state first."""
    n = {k: sum(1 for e in found if e['state'] == k) for k in ('due', 'ahead', 'when')}
    out = ['revisit lines%s, today %s: %d due, %d ahead, %d on a condition'
           % (' in profile %s' % profile if profile else '', today, n['due'], n['ahead'], n['when'])]
    for e in found:
        cols = ['%s:%d' % (e['path'], e['line']), e['date'] or e['text']]
        if e['date'] and e['text'] != e['date']:
            cols[-1] = e['text']
        if e['title']:
            cols.append(e['title'])
        cols.append('%s, %s' % (e['kind'], e['status']))
        out.append('%s: %s' % (e['state'], ' | '.join(cols)))
    if not found:
        out.append('no revisit lines found: a note gets one with a line such as Revisit: 2027-01-01 or '
                   'Revisit: when the cache lands, then leks build')
    return '\n'.join(out)
