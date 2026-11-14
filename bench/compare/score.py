"""Recount every number of docs/comparison.md from the per-question tables in this folder. Standard library only.

    python score.py            # all tables
    python score.py --set q3   # one question set

Rules (the same as bench/public/pep_bench.py): the top 10 counts, a copy of the gold note counts as the gold
note, "newer above replaced" means the newer note is listed and the replaced one is missing or lower. Rates
get a Wilson 95% interval. The paired tests on the held-out set q3 are the 16 comparisons fixed before any
system ran on q3: exact McNemar on hit@5, one Holm correction over all 16.
"""
import argparse
import csv
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
KNOWN_SETS = ['q1', 'q1p', 'q3']
PAIR_SETS = ['q2', 'q2c']
OTHERS = ['bm25', 'qmd-search', 'qmd-vsearch', 'qmd-query', 'mempalace-default', 'mempalace-hybrid',
          'knowledge-rag-hybrid']
FAMILY = ([('leks-3.1-models', o) for o in OTHERS] + [('leks-3.1', o) for o in OTHERS]
          + [('leks-3.1-models', 'leks-3.1'), ('leks-3.1', 'leks-3.0')])


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def pct(k, n):
    p, lo, hi = wilson(k, n)
    return '%.1f (%.1f to %.1f)' % (100 * p, 100 * lo, 100 * hi)


def mcnemar(b, c):
    # exact two-sided test on the discordant pairs
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    out, run = [0.0] * len(ps), 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - r) * ps[i]))
        out[i] = run
    return out


def rank(cell):
    return int(cell) if cell not in ('', None) else None


def load(name):
    with open(os.path.join(a.dir, name), encoding='utf-8', newline='') as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


ap = argparse.ArgumentParser()
ap.add_argument('--dir', default=HERE)
ap.add_argument('--set')
a = ap.parse_args()

kcols, krows = load('ranks_known.csv')
systems = kcols[3:]
by_set = {}
for row in krows:
    by_set.setdefault(row['set'], []).append(row)


def hit5(rows, s):
    return [rank(r[s]) is not None and rank(r[s]) <= 5 for r in rows]


for st in KNOWN_SETS:
    rows = by_set.get(st)
    if not rows or (a.set and a.set != st):
        continue
    n = len(rows)
    print('\n%s, %d questions\n' % (st, n))
    print('| system | hit@1 | hit@5 | MRR@10 |')
    print('|---|---|---|---|')
    for s in systems:
        rk = [rank(r[s]) for r in rows]
        h1 = sum(1 for x in rk if x == 1)
        h5 = sum(1 for x in rk if x is not None and x <= 5)
        mrr = sum(1 / x for x in rk if x is not None) / n
        print('| %s | %s | %s | %.3f |' % (s, pct(h1, n), pct(h5, n), mrr))

pcols, prows = load('ranks_pairs.csv')
psys = [c[:-len(':newer')] for c in pcols if c.endswith(':newer')]
for st in PAIR_SETS:
    rows = [r for r in prows if r['set'] == st]
    if not rows or (a.set and a.set != st):
        continue
    print('\n%s, %d pairs of a replaced note and its newer version\n' % (st, len(rows)))
    print('| system | newer above replaced | replaced first | pointer to the newer (of shown) |')
    print('|---|---|---|---|')
    for s in psys:
        nw = [rank(r[s + ':newer']) for r in rows]
        old = [rank(r[s + ':replaced']) for r in rows]
        above = sum(1 for x, y in zip(nw, old, strict=True) if x is not None and (y is None or x < y))
        first = sum(1 for x, y in zip(nw, old, strict=True) if y is not None and (x is None or y < x))
        ptr = [r[s + ':pointer'] for r in rows if r[s + ':pointer'] != '']
        shown = len(ptr)
        pt = '%d of %d' % (ptr.count('y'), shown) if any(p in ('y', 'w') for p in ptr) else 'none'
        print('| %s | %d of %d | %d | %s |' % (s, above, len(rows), first, pt))

rows3 = by_set.get('q3')
if rows3 and (not a.set or a.set == 'q3'):
    have = [(x, y) for x, y in FAMILY if x in systems and y in systems]
    if len(have) < len(FAMILY):
        print('\nq3 tests: %d of the %d fixed comparisons have both systems; Holm runs over these only'
              % (len(have), len(FAMILY)))
    res = []
    for x, y in have:
        fx, fy = hit5(rows3, x), hit5(rows3, y)
        b = sum(1 for u, v in zip(fx, fy, strict=True) if u and not v)
        c = sum(1 for u, v in zip(fx, fy, strict=True) if v and not u)
        res.append((x, y, sum(fx), sum(fy), b, c, mcnemar(b, c)))
    hp = holm([r[6] for r in res])
    n = len(rows3)
    q1p = by_set.get('q1p') or []
    print('\nq3 paired tests on hit@5, exact McNemar, Holm over the fixed family; q1p difference for comparison\n')
    print('| A | B | A hit@5 | B hit@5 | only A | only B | p | Holm p | q1p A minus B |')
    print('|---|---|---|---|---|---|---|---|---|')
    for (x, y, kx, ky, b, c, p), h in zip(res, hp, strict=True):
        d1 = ('%+.1f' % (100.0 * (sum(hit5(q1p, x)) - sum(hit5(q1p, y))) / len(q1p))) if q1p else '-'
        print('| %s | %s | %.1f | %.1f | %d | %d | %.2g | %.2g | %s |' % (
            x, y, 100.0 * kx / n, 100.0 * ky / n, b, c, p, h, d1))
