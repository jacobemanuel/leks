"""Link prior experiment: re-rank the LEKS top 20 with the links between notes. Outside the engine on purpose.

The plan (variants, weights, seeds, the bar to ship) was fixed in docs/benchmark.md section 7 before the
first run; the relation variants of section 7a were fixed after it and before their own run.
Standard library only.
"""
from __future__ import annotations

import math
import os
import re

SEEDS = 3
POOL = 20
BASE = 10.0

# name -> (w_out, w_in, w_state), the table in docs/benchmark.md section 7
VARIANTS = {
    'control': (0.0, 0.0, 0.0),
    'out': (0.25, 0.0, 0.0),
    'in': (0.0, 0.25, 0.0),
    'both': (0.25, 0.25, 0.0),
    'both-low': (0.10, 0.10, 0.0),
    'state': (0.0, 0.0, 0.25),
    'all': (0.25, 0.25, 0.25),
}

# addendum in docs/benchmark.md section 7a, fixed after the first run and before its own run:
# the relation fields alone, name -> weight on a note that a seed names as its newer version
REL_VARIANTS = {
    'rel': 0.25,
    'rel-low': 0.10,
}
# front matter fields where a note names a newer one, and where it names an older one
REL_NEWER = ('superseded_by', 'superseded-by', 'replaced_by', 'replaced-by')
REL_OLDER = ('supersedes', 'replaces')

WIKI = re.compile(r'\[\[([^\]|#\n]+)(?:[|#][^\]\n]*)?\]\]')
TEXT_EXT = ('.md', '.markdown', '.txt', '.rst')


def stem(path):
    return os.path.splitext(os.path.basename(path))[0].lower()


def wikilinks(text):
    """Link targets in a text as lowercased file names without extension."""
    return [stem(m.group(1).strip()) for m in WIKI.finditer(text or '')]


def link_graph(texts):
    """texts: display path -> text. Returns (out, inn), each path -> set of paths.

    A target is matched by file name without extension, case ignored. A name two notes share links to both;
    the corpora here have no such names."""
    by_stem = {}
    for p in texts:
        by_stem.setdefault(stem(p), []).append(p)
    out = {p: set() for p in texts}
    inn = {p: set() for p in texts}
    for p, t in texts.items():
        for s in wikilinks(t):
            for q in by_stem.get(s, ()):
                if q != p:
                    out[p].add(q)
                    inn[q].add(p)
    return out, inn


def front_fields(text):
    """Flat key: value lines of a leading front matter block, keys lowercased. {} when there is no block."""
    lines = (text or '').split('\n')
    if not lines or lines[0].strip() != '---':
        return {}
    out = {}
    for ln in lines[1:]:
        if ln.strip() == '---':
            return out
        m = re.match(r'^([A-Za-z][\w-]*):\s*(.*)$', ln)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
    # no closing line, so no front matter
    return {}


def names_in(value):
    """Note names in a relation value: '"[[pep-0649]]"', '["[[a]]", "[[b|text]]"]' or a bare 'adr-006'."""
    v = (value or '').strip()
    if v.startswith('[') and not v.startswith('[['):
        v = v[1:-1] if v.endswith(']') else v[1:]
    out = []
    for part in v.split(','):
        p = part.strip().strip('"\'').strip()
        m = re.match(r'^\[\[([^\]|#]+)', p)
        if m:
            p = m.group(1).strip()
        if p:
            out.append(stem(p))
    return out


def relation_graph(texts):
    """path -> set of paths a relation field names as its newer version, from both sides of the relation.

    An old note names the new one in superseded_by or replaced_by, a new note names the old one in supersedes
    or replaces. Names resolve like wikilinks in link_graph."""
    by_stem = {}
    for p in texts:
        by_stem.setdefault(stem(p), []).append(p)
    newer = {p: set() for p in texts}
    for p, t in texts.items():
        f = front_fields(t)
        for key in REL_NEWER:
            for s in names_in(f.get(key)):
                for q in by_stem.get(s, ()):
                    if q != p:
                        newer[p].add(q)
        for key in REL_OLDER:
            for s in names_in(f.get(key)):
                for q in by_stem.get(s, ()):
                    if q != p:
                        newer[q].add(p)
    return newer


def read_texts(roots, keep=None):
    """roots: [(name, folder)]. Display path -> text of the text files under them, limited to keep if given."""
    texts = {}
    for name, folder in roots:
        for dp, dns, fns in os.walk(folder):
            dns[:] = sorted(d for d in dns if not d.startswith('.'))
            for fn in sorted(fns):
                if not fn.lower().endswith(TEXT_EXT):
                    continue
                full = os.path.join(dp, fn)
                disp = name + '/' + os.path.relpath(full, folder).replace(os.sep, '/')
                if keep is not None and disp not in keep:
                    continue
                with open(full, encoding='utf-8', errors='replace') as f:
                    texts[disp] = f.read()
    return texts


def rerank(paths, out, inn, state_linked, w_out=0.0, w_in=0.0, w_state=0.0, seeds=SEEDS):
    """New order of the LEKS list paths (best first, at most POOL long).

    score = 1 / (10 + rank) plus w / (10 + seed rank) per link with a seed, plus w_state / 11 once for a note
    a state note links to."""
    paths = list(paths)[:POOL]
    rank = {p: i + 1 for i, p in enumerate(paths)}
    top = paths[:seeds]
    scored = []
    for p in paths:
        s = 1.0 / (BASE + rank[p])
        for q in top:
            if q == p:
                continue
            if w_out and p in out.get(q, ()):
                s += w_out / (BASE + rank[q])
            if w_in and q in out.get(p, ()):
                s += w_in / (BASE + rank[q])
        if w_state and p in state_linked:
            s += w_state / (BASE + 1)
        scored.append((-s, rank[p], p))
    scored.sort()
    return [p for _s, _r, p in scored]


def state_targets(out, state_notes):
    """Notes a state note links to, state notes themselves left out."""
    hit = set()
    for s in state_notes:
        hit.update(out.get(s, ()))
    return hit - set(state_notes)


def holm(pvals):
    """Holm adjusted p values, same order as given."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    run = 0.0
    for j, i in enumerate(order):
        run = max(run, min(1.0, (m - j) * pvals[i]))
        adj[i] = run
    return adj


def wilson(k, n, z=1.959963984540054):
    """(rate, low, high) of k successes in n, Wilson score interval."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))
