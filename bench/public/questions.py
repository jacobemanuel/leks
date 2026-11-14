"""Benchmark questions made by a fixed rule from PEP metadata only: number, title, Superseded-By, Replaces.

No PEP body text and no search result goes into a question. The rule is written out in docs/benchmark.md.

q1 known item: one question per PEP, the query is its title without its own number.
q2 replacement: one question per (old, new) pair linked by Superseded-By or Replaces. The query is the old
   PEP's title without either number, gold is the new PEP, the old one is listed as stale.
q2c: the q2 pairs again with the word "current" in front, so LEKS reads them as asking what holds now.
"""
from __future__ import annotations

import json
import re
import unicodedata

ROOT_NAME = 'peps'
MIN_WORDS = 2  # words of three or more letters a query needs, after the numbers are gone


def path_of(n):
    return '%s/pep-%04d.md' % (ROOT_NAME, int(n))


def ascii_fold(s):
    """Accents dropped, any other non-ASCII character (curly quotes, long dashes) becomes a space."""
    out = []
    for c in unicodedata.normalize('NFKD', s or ''):
        if ord(c) < 128:
            out.append(c)
        elif not unicodedata.combining(c):
            out.append(' ')
    return ''.join(out)


def strip_numbers(title, nums):
    """The title without "PEP n" and without n standing alone, for each n in nums. Version numbers like 3.4
    keep their digits."""
    t = ascii_fold(title)
    for n in nums:
        t = re.sub(r'(?i)\bPEPs?\s*0*%d\b' % int(n), ' ', t)
        t = re.sub(r'(?<!\w)(?<!\d\.)0*%d(?!\w)(?!\.\d)' % int(n), ' ', t)
    t = re.sub(r'\(\s*\)', ' ', t)
    return re.sub(r'\s+', ' ', t).strip(' -:,;.')


def word_count(q):
    return len(re.findall(r'[A-Za-z]{3,}', q))


def norm(q):
    return re.sub(r'\s+', ' ', q.lower()).strip()


def relation_pairs(peps):
    """Sorted (old, new) pairs among the PEPs given, from both headers."""
    have = {p['pep'] for p in peps}
    pairs = set()
    for p in peps:
        for new in p.get('superseded_by') or []:
            if new in have and new != p['pep']:
                pairs.add((p['pep'], new))
        for old in p.get('replaces') or []:
            if old in have and old != p['pep']:
                pairs.add((old, p['pep']))
    return sorted(pairs)


def chains(pairs):
    """Paths old -> ... -> newest of length three or more PEPs, used only to count q3 candidates."""
    nxt = {}
    for o, n in pairs:
        nxt.setdefault(o, set()).add(n)
    heads = sorted({o for o, _ in pairs} - {n for _, n in pairs})
    out = []

    def walk(path):
        tail = path[-1]
        if tail not in nxt:
            if len(path) >= 3:
                out.append(path)
            return
        for n in sorted(nxt[tail]):
            if n not in path:
                walk(path + [n])

    for h in heads:
        walk([h])
    return out


def build(manifest):
    """The question file as a dict. manifest is what peps.convert_tree returns."""
    peps = sorted(manifest['peps'], key=lambda p: p['pep'])
    by_num = {p['pep']: p for p in peps}
    pairs = relation_pairs(peps)
    succ = {}
    for o, n in pairs:
        succ.setdefault(o, []).append(n)
    dropped = {'q1_short': [], 'q1_duplicate': [], 'q2_short': []}

    q1 = []
    for p in peps:
        q = strip_numbers(p['title'], [p['pep']])
        if word_count(q) < MIN_WORDS:
            dropped['q1_short'].append(p['pep'])
            continue
        q1.append((p['pep'], q))
    seen = {}
    for n, q in q1:
        seen.setdefault(norm(q), []).append(n)
    questions = []
    for n, q in q1:
        if len(seen[norm(q)]) > 1:
            dropped['q1_duplicate'].append(n)
            continue
        questions.append({'id': 'q1-%04d' % n, 'set': 'q1', 'category': 'known', 'question': q,
                          'gold': [path_of(n)], 'stale': [], 'pep': n, 'has_successor': n in succ})

    q2 = []
    for o, n in pairs:
        q = strip_numbers(by_num[o]['title'], [o, n])
        if word_count(q) < MIN_WORDS:
            dropped['q2_short'].append([o, n])
            continue
        q2.append((o, n, q))
    for prefix, label in (('', 'q2'), ('current ', 'q2c')):
        for o, n, q in q2:
            questions.append({'id': '%s-%04d-%04d' % (label, o, n), 'set': label, 'category': 'current',
                              'question': prefix + q, 'gold': [path_of(n)], 'stale': [path_of(o)],
                              'old': o, 'new': n, 'successors': sorted(succ[o]),
                              'new_is_latest': n not in succ})

    ch = chains(pairs)
    counts = {
        'peps': len(peps),
        'left_out_by_license': len(manifest.get('left_out') or []),
        'pairs': len(pairs),
        'pairs_from_superseded_by': sum(1 for o, n in pairs if n in (by_num[o].get('superseded_by') or [])),
        'pairs_from_replaces': sum(1 for o, n in pairs if o in (by_num[n].get('replaces') or [])),
        'pairs_new_is_latest': sum(1 for o, n in pairs if n not in succ),
        'old_peps': len(succ),
        'chains_of_three_or_more': len(ch),
    }
    for s in ('q1', 'q2', 'q2c'):
        counts[s] = sum(1 for q in questions if q['set'] == s)
    counts['q1_with_successor'] = sum(1 for q in questions if q['set'] == 'q1' and q['has_successor'])
    return {
        '_note': 'Generated by bench/public/questions.py from PEP metadata only, frozen before any result. '
                 'Do not edit by hand: pep_bench.py rebuilds it from the pinned sources and stops on a mismatch.',
        'source': {'repo': manifest.get('repo'), 'commit': manifest.get('commit'),
                   'source_digest': manifest.get('source_digest')},
        'rule': {'min_words': MIN_WORDS, 'root': ROOT_NAME, 'q3': 'not made, see chains'},
        'counts': counts,
        'chains': ch,
        'dropped': dropped,
        'questions': questions,
    }


def dumps(d):
    """The exact bytes of the question file: sorted keys, ASCII, one space indent, newline at the end."""
    return json.dumps(d, indent=1, sort_keys=True, ensure_ascii=True) + '\n'
