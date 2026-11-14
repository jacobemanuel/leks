"""Public benchmark on Python Enhancement Proposals, one command. What it measures: docs/benchmark.md.

    python bench/public/pep_bench.py --leks30 PATH_TO_A_CHECKOUT_OF_TAG_v3.0.0

Fetches the PEPs at the pinned commit into a cache folder, converts them into notes in a work folder,
checks the frozen question file, runs each system in a process of its own and writes JSON and Markdown
under bench/public/results/. No PEP text is written into this repository. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
WORKER = os.path.join(HERE, 'leks_worker.py')
QFILE = os.path.join(HERE, 'questions.json')
FIX = os.path.join(REPO, 'bench', 'fixtures')
K = 20       # results asked per question, the link prior re-ranks this many
CUT = 10     # a gold note below this rank is a miss
BOOT = 10000
SEED = 7
NO_MODELS = {'backend': 'torch', 'device': 'auto', 'weights_dir': None, 'embedder': None, 'reranker': None}
WORD_TIMEOUT = 7200      # seconds for one word lane worker
MODEL_TIMEOUT = 5 * 3600  # the model worker embeds every passage on the machine it runs on first


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


peps = _load('leks_pub_peps', os.path.join(HERE, 'peps.py'))
questions = _load('leks_pub_questions', os.path.join(HERE, 'questions.py'))
link_prior = _load('leks_pub_link_prior', os.path.join(HERE, 'link_prior.py'))
stats = _load('leks_pub_stats', os.path.join(REPO, 'bench', 'stats.py'))


def say(*a):
    print(*a, file=sys.stderr, flush=True)


def default_cache():
    base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(base, 'leks-public-bench')


# ---------------------------------------------------------------- systems

def bm25_run(notes_root, qs, k=K):
    """Plain SQLite FTS5 baseline: one row per note, default tokenizer and bm25(), the query as an OR of its
    words, no priors. Returns {id: {'hits': [{'path': ...}]}}."""
    db = sqlite3.connect(':memory:')
    db.execute('CREATE VIRTUAL TABLE t USING fts5(path UNINDEXED, body)')
    for fn in sorted(os.listdir(notes_root)):
        if fn.endswith('.md'):
            with open(os.path.join(notes_root, fn), encoding='utf-8') as f:
                db.execute('INSERT INTO t VALUES (?, ?)', ('%s/%s' % (questions.ROOT_NAME, fn), f.read()))
    out = {}
    for q in qs:
        words = []
        for w in re.findall(r'[A-Za-z0-9]+', q['question'].lower()):
            if w not in words:
                words.append(w)
        rows = []
        if words:
            rows = db.execute('SELECT path FROM t WHERE t MATCH ? ORDER BY bm25(t), path LIMIT ?',
                              (' OR '.join('"%s"' % w for w in words), k)).fetchall()
        out[q['id']] = {'hits': [{'path': r[0], 'copies': [], 'newer': None} for r in rows]}
    db.close()
    return {'status': 'ok', 'results': out, 'lanes': 'sqlite fts5 bm25, whole notes, no priors'}


def scrubber(pairs):
    pairs = sorted(((os.path.realpath(a), b) for a, b in pairs if a), key=lambda x: -len(x[0]))

    def scrub(s):
        s = str(s)
        for a, b in pairs:
            s = s.replace(a, b)
        return s.replace(os.path.expanduser('~'), '~')
    return scrub


def default_weights():
    """Where `leks models fetch` puts the weights: <LEKS_HOME or ~/.leks>/models."""
    home = os.environ.get('LEKS_HOME') or os.path.join(os.path.expanduser('~'), '.leks')
    return os.path.join(os.path.abspath(os.path.expanduser(home)), 'models')


def run_worker(name, leks_root, cfg, profile, qs, work, py, scrub, k=K, models=None, timeout=WORD_TIMEOUT):
    """One LEKS version in its own process with its own home and config. Returns the worker output.

    models: None for the word lanes, else {'weights_dir': folder, 'fetch': bool} for the model lanes."""
    home = os.path.join(work, 'home-' + name)
    os.makedirs(home, exist_ok=True)
    cfg = dict(cfg, home=home)
    cpath = os.path.join(work, 'config-%s.json' % name)
    jpath = os.path.join(work, 'job-%s.json' % name)
    opath = os.path.join(work, 'out-%s.json' % name)
    with open(cpath, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=1)
    with open(jpath, 'w', encoding='utf-8') as f:
        json.dump({'leks_root': leks_root, 'profile': profile, 'k': k, 'models': models,
                   'questions': [{'id': q['id'], 'question': q['question']} for q in qs]}, f)
    env = {a: b for a, b in os.environ.items() if not a.startswith(('LEKS_', 'PYTHON'))}
    env.update({'LEKS_HOME': home, 'LEKS_CONFIG': cpath, 'LEKS_PROFILE': profile, 'PYTHONPATH': leks_root,
                'PYTHONDONTWRITEBYTECODE': '1'})
    t0 = time.time()
    try:
        r = subprocess.run([py, '-B', WORKER, jpath, opath], env=env, cwd=work, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'status': 'failed', 'reason': 'worker took longer than %d s' % timeout}
    if r.returncode != 0:
        tail = [x for x in (r.stderr or '').strip().splitlines() if x.strip()][-1:] or ['no message']
        return {'status': 'failed', 'reason': scrub('worker exit %d: %s' % (r.returncode, tail[0]))[:300]}
    with open(opath, encoding='utf-8') as f:
        out = json.load(f)
    if out.get('status') == 'skipped':
        # the worker found no way to run this system here and says why
        return {'status': 'skipped', 'reason': scrub(out.get('reason') or 'no reason given')[:300]}
    out['status'] = 'ok'
    out['wall_secs'] = round(time.time() - t0, 1)
    return out


def pep_config():
    with open(os.path.join(FIX, 'config.json'), encoding='utf-8') as f:
        classify = json.load(f)['classify']
    return {'version': 3, 'roots': [], 'profiles': {'peps': {'roots': [questions.ROOT_NAME]}},
            'default_profile': 'peps', 'classify': classify, 'models': dict(NO_MODELS),
            'ocr': {'enabled': 'off', 'images': False, 'helper': None, 'max_pages': 40}}


def fixture_config():
    with open(os.path.join(FIX, 'config.json'), encoding='utf-8') as f:
        d = json.load(f)
    for r in d['roots']:
        r['path'] = os.path.normpath(os.path.join(FIX, r['path']))
    d['models'] = dict(NO_MODELS)
    return d


# ---------------------------------------------------------------- scoring

def ranked_sets(hits, cut=CUT):
    return [{h['path']} | set(h.get('copies') or []) for h in hits[:cut]]


def first_rank(sets, gold):
    g = set(gold)
    for i, s in enumerate(sets):
        if g & s:
            return i + 1
    return None


def rate(k, n):
    p, lo, hi = link_prior.wilson(k, n)
    return {'k': k, 'n': n, 'rate': round(p, 4), 'lo': round(lo, 4), 'hi': round(hi, 4)}


def score_q1(res, qs):
    rows = {}
    for q in qs:
        hits = (res.get(q['id']) or {}).get('hits') or []
        rows[q['id']] = first_rank(ranked_sets(hits), q['gold'])
    return rows


def summary_q1(ranks):
    r = list(ranks)
    rr = [1.0 / x if x is not None and x <= CUT else 0.0 for x in r]
    mean, lo, hi = stats.bootstrap_ci(rr, B=BOOT, seed=SEED)
    return {'n': len(r), 'hit1': rate(sum(1 for x in r if x == 1), len(r)),
            'hit5': rate(sum(1 for x in r if x is not None and x <= 5), len(r)),
            'mrr10': {'mean': round(mean, 4), 'lo': round(lo, 4), 'hi': round(hi, 4)}}


def score_q2(res, qs):
    """Per pair: [rank of new, rank of old, the pointer on the old hit or None]."""
    rows = {}
    for q in qs:
        hits = (res.get(q['id']) or {}).get('hits') or []
        sets = ranked_sets(hits)
        rn, ro = first_rank(sets, q['gold']), first_rank(sets, q['stale'])
        ptr = hits[ro - 1].get('newer') if ro is not None else None
        rows[q['id']] = [rn, ro, ptr]
    return rows


def above(row):
    rn, ro = row[0], row[1]
    return rn is not None and (ro is None or rn < ro)


def summary_q2(rows, qs, pointers=True):
    by = {q['id']: q for q in qs}
    ab = [above(rows[i]) for i in rows]
    shown = [i for i in rows if rows[i][1] is not None]
    ok = [i for i in shown if rows[i][2] in {questions.path_of(n) for n in by[i]['successors']}]
    wrong = [i for i in shown if rows[i][2] is not None and i not in ok]
    out = {'n': len(rows), 'above': rate(sum(ab), len(ab)),
           # the new PEP as a known item: hit@1, hit@5, MRR@10 of the gold note
           'new': summary_q1([rows[i][0] for i in rows]),
           'new_missing': sum(1 for i in rows if rows[i][0] is None),
           'old_first': sum(1 for i in rows if rows[i][1] is not None and (rows[i][0] is None or rows[i][1] < rows[i][0])),
           'old_shown': len(shown)}
    if pointers:
        out['pointer'] = rate(len(ok), len(shown))
        out['pointer_wrong'] = len(wrong)
    return out


def edge_check(edges, qs):
    """How the index reads the relations: pairs whose old PEP has an edge to one of its successors."""
    e = {a: b for a, b, _how in edges if a}
    q2 = [q for q in qs if q['set'] == 'q2']
    succ = {}
    for q in q2:
        succ[questions.path_of(q['old'])] = {questions.path_of(n) for n in q['successors']}
    pairs = sum(1 for q in q2 if e.get(questions.path_of(q['old'])) in succ[questions.path_of(q['old'])])
    olds = sum(1 for o, s in succ.items() if e.get(o) in s)
    extra = sorted([a, b] for a, b in e.items() if b not in succ.get(a, ()))
    missing = [[q['old'], q['new']] for q in q2 if e.get(questions.path_of(q['old'])) not in succ[questions.path_of(q['old'])]]
    how = {}
    for _a, _b, h in edges:
        how[h] = how.get(h, 0) + 1
    return {'edges': len(e), 'pairs_covered': pairs, 'pairs': len(q2), 'old_covered': olds, 'olds': len(succ),
            'not_a_pair': len(extra), 'not_a_pair_list': extra[:40], 'pairs_without_edge': missing, 'how': how}


def paired(a, b):
    a_only = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    b_only = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    return {'n': len(a), 'a_only': a_only, 'b_only': b_only, 'p': stats.mcnemar_exact(a_only, b_only)}


def compare(sa, sb, pa, pb, by_set):
    """sa, sb: system names; pa, pb: their per-question rows."""
    out = []
    q1 = [q['id'] for q in by_set['q1']]
    for label, f in (('q1 hit@1', lambda r: r == 1), ('q1 hit@5', lambda r: r is not None and r <= 5)):
        out.append(dict(paired([f(pa['q1'][i]) for i in q1], [f(pb['q1'][i]) for i in q1]),
                        a=sa, b=sb, measure=label))
    for s in ('q2', 'q2c'):
        ids = [q['id'] for q in by_set[s]]
        out.append(dict(paired([above(pa[s][i]) for i in ids], [above(pb[s][i]) for i in ids]),
                        a=sa, b=sb, measure='%s current above replaced' % s))
    return out


def pointer_compare(sa, sb, pa, pb, by_set):
    out = []
    for s in ('q2', 'q2c'):
        by = {q['id']: q for q in by_set[s]}
        ids = [i for i in by if pa[s][i][1] is not None and pb[s][i][1] is not None]

        def ok(row, q):
            return row[2] in {questions.path_of(n) for n in q['successors']}
        out.append(dict(paired([ok(pa[s][i], by[i]) for i in ids], [ok(pb[s][i], by[i]) for i in ids]),
                        a=sa, b=sb, measure='%s pointer, pairs both list' % s))
    return out


# ---------------------------------------------------------------- link prior

def prior_lists(res, ids):
    """id -> (list of paths in LEKS order, path -> set of paths that count as that hit)."""
    out = {}
    for i in ids:
        hits = (res.get(i) or {}).get('hits') or []
        out[i] = ([h['path'] for h in hits], {h['path']: {h['path']} | set(h.get('copies') or []) for h in hits})
    return out


def prior_ranks(lists, out_g, inn_g, state_linked, w, gold_of, stale_of):
    rows = {}
    for i, (paths, sets) in lists.items():
        order = link_prior.rerank(paths, out_g, inn_g, state_linked, *w)
        ranked = [sets[p] for p in order][:CUT]
        rows[i] = (first_rank(ranked, gold_of[i]) if gold_of[i] else None,
                   first_rank(ranked, stale_of[i]) if stale_of[i] else None)
    return rows


def run_link_prior(pep_out, qs_by_set, notes_root, fix_out, fix_qs, rb):
    """Every variant on both sets, the bar from docs/benchmark.md sections 7 and 7a."""
    report = {'variants': {k: list(v) for k, v in link_prior.VARIANTS.items()},
              'relation_variants': dict(link_prior.REL_VARIANTS), 'sets': {}}
    plain = list(link_prior.VARIANTS.items())
    rel = list(link_prior.REL_VARIANTS.items())

    def weights(name, graphs):
        # a plain variant uses the link graph, a relation variant the relation graph as its out links
        out_g, inn_g, st, newer = graphs
        if name in link_prior.VARIANTS:
            return out_g, inn_g, st, link_prior.VARIANTS[name]
        return newer, {}, set(), (link_prior.REL_VARIANTS[name], 0.0, 0.0)

    # PEP set: q1 for the gain, every kind for the no-loss rule
    texts = link_prior.read_texts([(questions.ROOT_NAME, notes_root)], keep=set(pep_out['docs']))
    out_g, inn_g = link_prior.link_graph(texts)
    newer = link_prior.relation_graph(texts)
    states = [p for p, d in pep_out['docs'].items() if d['kind'] == 'state']
    graphs = (out_g, inn_g, link_prior.state_targets(out_g, states), newer)
    allq = [dict(q, _kind=q['set'] if q['set'] != 'q1' else ('q1 has newer' if q['has_successor'] else 'q1 has none'))
            for s in ('q1', 'q2', 'q2c') for q in qs_by_set[s]]
    lists = prior_lists(pep_out['results'], [q['id'] for q in allq])
    gold = {q['id']: q['gold'] for q in allq}
    stale = {q['id']: q['stale'] for q in allq}
    pep = {'n_links': sum(len(v) for v in out_g.values()), 'notes': len(texts), 'state_notes': len(states),
           'n_relation_edges': sum(len(v) for v in newer.values()), 'variants': {}}
    for name, _w in plain + rel:
        g_out, g_in, st, w = weights(name, graphs)
        rows = prior_ranks(lists, g_out, g_in, st, w, gold, stale)
        q1 = [rows[q['id']][0] for q in qs_by_set['q1']]
        v = {'q1_hit1_list': [x == 1 for x in q1], 'q1': summary_q1(q1)}
        for s in ('q2', 'q2c'):
            ab = [above(rows[q['id']]) for q in qs_by_set[s]]
            v[s + '_above'] = rate(sum(ab), len(ab))
        # the kinds: q1 hit@1 split by a newer PEP, q2 and q2c current above replaced
        kinds = {}
        for q in allq:
            r = rows[q['id']]
            ok = (r[0] == 1) if q['set'] == 'q1' else above(r)
            kinds.setdefault(q['_kind'], []).append(ok)
        v['kinds'] = {k: rate(sum(x), len(x)) for k, x in sorted(kinds.items())}
        pep['variants'][name] = v
    report['sets']['peps'] = pep

    # fixtures, scored like bench/run_bench.py
    texts = link_prior.read_texts([(r['name'], r['path']) for r in fixture_config()['roots']],
                                  keep=set(fix_out['docs']))
    out_g, inn_g = link_prior.link_graph(texts)
    newer = link_prior.relation_graph(texts)
    states = [p for p, d in fix_out['docs'].items() if d['kind'] == 'state']
    graphs = (out_g, inn_g, link_prior.state_targets(out_g, states), newer)
    lists = prior_lists(fix_out['results'], [q['id'] for q in fix_qs])
    gold = {q['id']: q['gold'] for q in fix_qs}
    stale = {q['id']: q.get('stale') or [] for q in fix_qs}
    fx = {'n_links': sum(len(v) for v in out_g.values()), 'notes': len(texts), 'state_notes': len(states),
          'n_relation_edges': sum(len(v) for v in newer.values()), 'variants': {}}
    for name, _w in plain + rel:
        g_out, g_in, st, w = weights(name, graphs)
        rows = prior_ranks(lists, g_out, g_in, st, w, gold, stale)
        rws = [{'id': q['id'], 'cat': q['category'], 'una': not q['gold'], 'rank': rows[q['id']][0],
                'stale_rank': rows[q['id']][1], 'ms': 0.0, 'low': None} for q in fix_qs]
        sm = rb.summarize(rws)
        ans = [r for r in rws if not r['una']]
        cur = [r for r in ans if r['cat'] == 'current']
        kinds = {}
        for r in ans:
            kinds.setdefault('hit@1 ' + r['cat'], []).append(r['rank'] == 1)
        kinds['current above stale'] = [r['rank'] is not None and (r['stale_rank'] is None or r['rank'] < r['stale_rank'])
                                        for r in cur]
        fx['variants'][name] = {
            'hit1_list': [r['rank'] == 1 for r in ans],
            'top1': rate(sum(1 for r in ans if r['rank'] == 1), len(ans)),
            'top5': sm['top5'], 'mrr10': sm['mrr10'],
            'current_above_stale': rate(sum(kinds['current above stale']), len(cur)),
            'kinds': {k: rate(sum(x), len(x)) for k, x in sorted(kinds.items())}}
    report['sets']['fixtures'] = fx

    # tests against control, Holm within each family (the six of section 7, the two of 7a) and each set
    families = [[n for n, _w in plain if n != 'control'], [n for n, _w in rel]]
    for set_name, key in (('peps', 'q1_hit1_list'), ('fixtures', 'hit1_list')):
        vs = report['sets'][set_name]['variants']
        for fam in families:
            tests = [paired(vs['control'][key], vs[n][key]) for n in fam]
            adj = link_prior.holm([t['p'] for t in tests])
            for n, t, a in zip(fam, tests, adj, strict=True):
                vs[n]['vs_control'] = dict(t, holm_p=a)
    report['verdict'] = verdicts(report['sets']['peps']['variants'], report['sets']['fixtures']['variants'],
                                 families[0] + families[1])
    report['ships'] = sorted(n for n, v in report['verdict'].items() if v['ships'])
    for s in report['sets'].values():
        for v in s['variants'].values():
            v.pop('q1_hit1_list', None)
            v.pop('hit1_list', None)
    return report


def verdicts(pep_vs, fix_vs, names):
    """The bar: hit@1 up on both sets with Holm p below 0.05, the replacement metric of section 7 not down on
    either set, and (added in 7a, it can only make shipping harder) no kind down on either set."""
    out = {}
    for n in names:
        gain = all(s[n]['vs_control']['b_only'] > s[n]['vs_control']['a_only'] and s[n]['vs_control']['holm_p'] < 0.05
                   for s in (pep_vs, fix_vs))
        no_loss = (pep_vs[n]['q2_above']['k'] >= pep_vs['control']['q2_above']['k']
                   and fix_vs[n]['current_above_stale']['k'] >= fix_vs['control']['current_above_stale']['k'])
        down = sorted('%s: %s' % (set_name, k) for set_name, vs in (('peps', pep_vs), ('fixtures', fix_vs))
                      for k, r in vs[n]['kinds'].items() if r['k'] < vs['control']['kinds'][k]['k'])
        out[n] = {'gain_on_both': gain, 'no_replacement_loss': no_loss, 'kinds_down': down,
                  'ships': gain and no_loss and not down}
    return out


# ---------------------------------------------------------------- report

def pct(r):
    # from the counts: the stored rate is already rounded to 4 places, and a second rounding of it
    # can land one tenth off (621 of 695 is 89.35%, which shows as 89.4, while 0.8935 shows as 89.3)
    p, lo, hi = link_prior.wilson(r['k'], r['n'])
    return '%.1f%% (%.1f to %.1f)' % (100 * p, 100 * lo, 100 * hi)


def pct_n(r):
    return '%d/%d, %s' % (r['k'], r['n'], pct(r))


def render(rep):
    m = rep['meta']
    L = ['# PEP benchmark results', '',
         'Written by `bench/public/pep_bench.py`. Method, rule and limits: `docs/benchmark.md`.', '',
         '- PEP commit: `%s`, notes: %d, questions file sha256: `%s`' % (
             m['source']['commit'], m['notes'], m['questions_sha256']),
         '- run (UTC): %s, Python %s, SQLite %s, results asked: %d, a gold note below rank %d is a miss' % (
             m['utc'], m['python'], m.get('sqlite', '?'), m.get('k', K), CUT), '']
    L += ['## Systems', '', '| system | status | notes in the index | lanes |', '|---|---|---:|---|']
    for name, s in rep['systems'].items():
        if s.get('status') != 'ok':
            L.append('| %s | skipped: %s | | |' % (name, s.get('reason', '')))
        else:
            L.append('| %s | ok%s | %s | %s |' % (name, (', leks ' + s['version'] + ', code ' + s['package_digest'][:12])
                                                if s.get('version') else '', s.get('indexed', ''), s.get('lanes', '')))
    L += ['', '## Known item (q1)', '', '| system | n | hit@1 | hit@5 | MRR@10 |', '|---|---:|---|---|---|']
    for name, s in rep['systems'].items():
        if s.get('status') == 'ok':
            q = s['q1']
            L.append('| %s | %d | %s | %s | %.3f (%.3f to %.3f) |' % (
                name, q['n'], pct(q['hit1']), pct(q['hit5']), q['mrr10']['mean'], q['mrr10']['lo'], q['mrr10']['hi']))
    L += ['', 'Split by whether the PEP has a newer one:', '',
          '| system | has a newer one: hit@1 | has none: hit@1 |', '|---|---|---|']
    for name, s in rep['systems'].items():
        if s.get('status') == 'ok':
            L.append('| %s | %s | %s |' % (name, pct_n(s['q1_has_newer']['hit1']), pct_n(s['q1_no_newer']['hit1'])))
    L += ['', '## Replacement, the new PEP as the answer', '',
          'Gold is the newer PEP of the pair. Same metrics as q1.', '',
          '| system | set | n | hit@1 | hit@5 | MRR@10 |', '|---|---|---:|---|---|---|']
    for setn in ('q2', 'q2c'):
        for name, s in rep['systems'].items():
            if s.get('status') == 'ok':
                q = s[setn]['new']
                L.append('| %s | %s | %d | %s | %s | %.3f (%.3f to %.3f) |' % (
                    name, setn, q['n'], pct(q['hit1']), pct(q['hit5']), q['mrr10']['mean'], q['mrr10']['lo'],
                    q['mrr10']['hi']))
    for setn in ('q2', 'q2c'):
        L += ['', '## Replacement (%s)' % setn, '',
              '| system | current above replaced | new missing | old first | old listed | pointer when listed | wrong pointer |',
              '|---|---|---:|---:|---:|---|---:|']
        for name, s in rep['systems'].items():
            if s.get('status') == 'ok':
                q = s[setn]
                L.append('| %s | %s | %d | %d | %d | %s | %s |' % (
                    name, pct_n(q['above']), q['new_missing'], q['old_first'], q['old_shown'],
                    pct_n(q['pointer']) if 'pointer' in q else 'n/a', q.get('pointer_wrong', 'n/a')))
    L += ['', '## Paired tests', '', 'Exact two-sided McNemar on the per-question outcomes.', '',
          '| a | b | measure | n | only a | only b | p |', '|---|---|---|---:|---:|---:|---:|']
    for c in rep['comparisons']:
        L.append('| %s | %s | %s | %d | %d | %d | %.3g |' % (c['a'], c['b'], c['measure'], c['n'], c['a_only'],
                                                           c['b_only'], c['p']))
    L += ['', '## How each index reads the relations', '',
          '| system | edges | pairs covered | old PEPs covered | edges that match no pair |', '|---|---:|---|---|---:|']
    for name, s in rep['systems'].items():
        if s.get('status') == 'ok' and 'edges' in s:
            e = s['edges']
            L.append('| %s | %d | %d of %d | %d of %d | %d |' % (name, e['edges'], e['pairs_covered'], e['pairs'],
                                                              e['old_covered'], e['olds'], e['not_a_pair']))
    L += ['', 'Pairs (old, new) with no edge from the old PEP to one of its successors:', '']
    for name, s in rep['systems'].items():
        if s.get('status') == 'ok' and 'edges' in s:
            L.append('- %s: %s' % (name, ', '.join('%d to %d' % tuple(x) for x in s['edges']['pairs_without_edge']) or 'none'))
    return '\n'.join(L) + '\n'


def render_prior(rep):
    L = ['# Link prior results', '', 'Plan and bar: `docs/benchmark.md` sections 7 and 7a. Variants re-rank the '
         'LEKS top %d. The relation variants (rel, rel-low) are the addendum of section 7a.' % link_prior.POOL, '']
    for set_name, title, key, rkey in (('peps', 'PEP set', 'q1', 'q2_above'),
                                       ('fixtures', 'Fixture set', None, 'current_above_stale')):
        s = rep['sets'][set_name]
        L += ['## %s' % title, '', 'notes %d, links %d, relation edges %d, state notes %d' % (
            s['notes'], s['n_links'], s.get('n_relation_edges', 0), s['state_notes']), '']
        if key:
            L += ['| variant | q1 hit@1 | lost | gained | p | Holm p | q2 current above replaced | q2c current above replaced |',
                  '|---|---|---:|---:|---:|---:|---|---|']
        else:
            L += ['| variant | hit@1 | lost | gained | p | Holm p | current above stale |',
                  '|---|---|---:|---:|---:|---:|---|']
        for n, v in s['variants'].items():
            t = v.get('vs_control')
            cells = ['%d' % t['a_only'], '%d' % t['b_only'], '%.3g' % t['p'], '%.3g' % t['holm_p']] if t else ['', '', '', '']
            if key:
                L.append('| %s | %s | %s | %s | %s |' % (n, pct_n(v['q1']['hit1']), ' | '.join(cells),
                                                         pct_n(v['q2_above']), pct_n(v['q2c_above'])))
            else:
                L.append('| %s | %s | %s | %s |' % (n, pct_n(v['top1']), ' | '.join(cells), pct_n(v[rkey])))
        kinds = list(s['variants']['control']['kinds'])
        L += ['', 'Per kind (count of questions right):', '',
              '| variant | ' + ' | '.join(kinds) + ' |', '|---|' + '---:|' * len(kinds)]
        for n, v in s['variants'].items():
            L.append('| %s | %s |' % (n, ' | '.join('%d/%d' % (v['kinds'][k]['k'], v['kinds'][k]['n']) for k in kinds)))
        L.append('')
    L += ['## Verdict', '', '| variant | gain on both sets | no replacement loss | kinds down | ships |',
          '|---|---|---|---|---|']
    for n, v in rep['verdict'].items():
        L.append('| %s | %s | %s | %s | %s |' % (n, 'yes' if v['gain_on_both'] else 'no',
                                                 'yes' if v['no_replacement_loss'] else 'no',
                                                 ', '.join(v['kinds_down']) or 'none', 'yes' if v['ships'] else 'no'))
    L += ['', 'Ships: %s' % (', '.join(rep['ships']) if rep['ships'] else 'none')]
    return '\n'.join(L) + '\n'


# ---------------------------------------------------------------- main

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description='Public benchmark on Python Enhancement Proposals.')
    ap.add_argument('--leks30', default=None, help='folder of a LEKS 3.0.0 checkout, for the 3.0 system')
    ap.add_argument('--cache', default=None, help='where the PEP sources are kept (default %s)' % '~/.cache/leks-public-bench')
    ap.add_argument('--work', default=None, help='work folder for notes and indexes (default: a temp folder)')
    ap.add_argument('--keep', action='store_true', help='keep the work folder')
    ap.add_argument('--offline', action='store_true', help='use the cached sources only')
    ap.add_argument('--systems', default='bm25,leks,leks30,leks+models')
    ap.add_argument('--out', default=os.path.join(HERE, 'results'))
    ap.add_argument('--python', default=sys.executable, help='python for the LEKS workers')
    ap.add_argument('--no-link-prior', action='store_true')
    ap.add_argument('--k', type=int, default=K,
                    help='results asked per question (default %d; the link prior needs %d)' % (K, K))
    ap.add_argument('--weights', default=None,
                    help='model weights folder for leks+models (default: where leks models fetch puts them, '
                         '<LEKS_HOME or ~/.leks>/models; with --fetch-models: <cache>/models)')
    ap.add_argument('--fetch-models', action='store_true',
                    help='download the pinned model weights into --weights before the leks+models run')
    ap.add_argument('--fetch-only', action='store_true', help='fetch and check the PEP sources, then stop')
    ap.add_argument('--write-questions', action='store_true',
                    help='rewrite bench/public/questions.json from the sources and stop (only for a new pin)')
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    systems = [s.strip() for s in args.systems.split(',') if s.strip()]
    cache = os.path.abspath(os.path.expanduser(args.cache or default_cache()))
    work = os.path.abspath(os.path.expanduser(args.work)) if args.work else tempfile.mkdtemp(prefix='leks-pep-')
    os.makedirs(work, exist_ok=True)
    leks30 = os.path.abspath(os.path.expanduser(args.leks30)) if args.leks30 else None
    scrub = scrubber([(work, '<work>'), (cache, '<cache>'), (REPO, '<repo>'), (leks30, '<leks30>')])
    try:
        try:
            src = peps.fetch(cache, offline=args.offline, log=say)
        except (RuntimeError, OSError) as e:
            say('could not get the PEP sources: %s' % scrub(e))
            return 2
        if args.fetch_only:
            say('PEP sources at %s match the pinned digest' % scrub(src))
            return 0
        notes = os.path.join(work, 'notes')
        manifest = peps.convert_tree(src, notes)
        qd = questions.build(manifest)
        blob = questions.dumps(qd)
        if args.write_questions:
            # newline='\n': the file holds the bytes its sha256 in every report is taken of, on windows too
            with open(QFILE, 'w', encoding='ascii', newline='\n') as f:
                f.write(blob)
            try:
                shown = os.path.relpath(QFILE, REPO)
            except ValueError:
                # windows has no relative path from one drive to another
                shown = scrub(QFILE)
            say('wrote %s' % shown)
            return 0
        with open(QFILE, encoding='ascii') as f:
            frozen = f.read()
        if frozen != blob:
            say('the questions made from these sources differ from bench/public/questions.json: stopping')
            return 2
        qsha = hashlib.sha256(frozen.encode('ascii')).hexdigest()
        qs = qd['questions']
        by_set = {s: [q for q in qs if q['set'] == s] for s in ('q1', 'q2', 'q2c')}
        notes_root = os.path.join(notes, 'peps')
        all_notes = sorted('%s/%s' % (questions.ROOT_NAME, x) for x in os.listdir(notes_root) if x.endswith('.md'))
        cfg = pep_config()
        cfg['roots'] = [{'name': questions.ROOT_NAME, 'path': notes_root, 'kind': 'notes'}]

        outs = {}
        for name in systems:
            say('system %s' % name)
            if name == 'bm25':
                outs[name] = bm25_run(notes_root, qs, k=args.k)
            elif name == 'leks':
                outs[name] = run_worker(name, REPO, cfg, 'peps', qs, work, args.python, scrub, k=args.k)
            elif name == 'leks30':
                if not leks30 or not os.path.isfile(os.path.join(leks30, 'leks', '__init__.py')):
                    outs[name] = {'status': 'skipped', 'reason': 'no --leks30 folder with a leks package'}
                else:
                    outs[name] = run_worker(name, leks30, cfg, 'peps', qs, work, args.python, scrub, k=args.k)
            elif name == 'leks+models':
                if args.weights:
                    wd = os.path.abspath(os.path.expanduser(args.weights))
                else:
                    wd = os.path.join(cache, 'models') if args.fetch_models else default_weights()
                mcfg = dict(cfg, models=None)
                outs[name] = run_worker(name, REPO, mcfg, 'peps', qs, work, args.python, scrub, k=args.k,
                                        models={'weights_dir': wd, 'fetch': bool(args.fetch_models)},
                                        timeout=MODEL_TIMEOUT)
            else:
                outs[name] = {'status': 'skipped', 'reason': 'unknown system'}
            if outs[name].get('status') != 'ok':
                say('  %s: %s' % (outs[name].get('status'), outs[name].get('reason')))

        per_q, sysrep = {}, {}
        for name, o in outs.items():
            if o.get('status') != 'ok':
                sysrep[name] = {'status': o.get('status'), 'reason': o.get('reason')}
                continue
            res = o['results']
            rows = {'q1': score_q1(res, by_set['q1']), 'q2': score_q2(res, by_set['q2']),
                    'q2c': score_q2(res, by_set['q2c'])}
            per_q[name] = rows
            has = [rows['q1'][q['id']] for q in by_set['q1'] if q['has_successor']]
            hasnt = [rows['q1'][q['id']] for q in by_set['q1'] if not q['has_successor']]
            leks_sys = 'docs' in o
            s = {'status': 'ok', 'lanes': o.get('lanes'),
                 'q1': summary_q1(rows['q1'].values()), 'q1_has_newer': summary_q1(has),
                 'q1_no_newer': summary_q1(hasnt),
                 'q2': summary_q2(rows['q2'], by_set['q2'], pointers=leks_sys),
                 'q2c': summary_q2(rows['q2c'], by_set['q2c'], pointers=leks_sys)}
            if leks_sys:
                s.update({'version': o['version'], 'package_digest': o['package_digest'], 'indexed': len(o['docs']),
                          'not_indexed': sorted(set(all_notes) - set(o['docs'])),
                          'build_secs': o['build_secs'], 'edges': edge_check(o['edges'], qs),
                          'python': o.get('python'), 'sqlite': o.get('sqlite'), 'embed': o.get('embed'),
                          'current_intent_questions': sum(1 for r in res.values() if r.get('intent'))})
            else:
                s['indexed'] = len(all_notes)
            sysrep[name] = s

        comps = []
        ok = [n for n in ('leks', 'leks30', 'leks+models', 'bm25') if n in per_q]
        for a, b in (('leks30', 'leks'), ('leks', 'leks+models')):
            if a in per_q and b in per_q:
                comps += compare(a, b, per_q[a], per_q[b], by_set)
                comps += pointer_compare(a, b, per_q[a], per_q[b], by_set)
        for n in ok:
            if n != 'bm25' and 'bm25' in per_q:
                comps += compare('bm25', n, per_q['bm25'], per_q[n], by_set)

        now = datetime.datetime.now(datetime.timezone.utc)
        rep = {'meta': {'utc': now.strftime('%Y-%m-%d %H:%M:%S'), 'python': sys.version.split()[0],
                        'sqlite': sqlite3.sqlite_version, 'platform': sys.platform,
                        'source': {'repo': peps.PEPS_REPO, 'commit': peps.PINNED_SHA,
                                   'source_digest': manifest['source_digest']},
                        'notes': len(all_notes), 'left_out_by_license': manifest['left_out'],
                        'questions_sha256': qsha, 'k': args.k, 'cutoff': CUT, 'boot': BOOT, 'seed': SEED},
               'systems': sysrep, 'comparisons': comps,
               'per_question': {n: {s: rows[s] for s in rows} for n, rows in per_q.items()}}
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, 'pep_results.json'), 'w', encoding='ascii', newline='\n') as f:
            f.write(scrub(json.dumps(rep, indent=1, sort_keys=True, ensure_ascii=True)) + '\n')
        md = render(rep)
        with open(os.path.join(args.out, 'pep_results.md'), 'w', encoding='ascii', newline='\n') as f:
            f.write(scrub(md))
        print(md)

        # what was asked for and did not run; the files above are still written, the exit code says so
        not_ok = [n for n in systems if outs[n].get('status') != 'ok']
        if not args.no_link_prior and args.k < link_prior.POOL:
            say('link prior skipped: it re-ranks the top %d, so it needs --k %d or more' % (link_prior.POOL, link_prior.POOL))
            if 'leks' in systems:
                not_ok.append('link prior')
        elif not args.no_link_prior and outs.get('leks', {}).get('status') == 'ok':
            say('link prior: fixtures')
            rb = _load('leks_pub_run_bench', os.path.join(REPO, 'bench', 'run_bench.py'))
            fqs_all, _note = rb.load_questions(os.path.join(FIX, 'questions.json'))
            fcfg = fixture_config()
            fout = run_worker('fixtures', REPO, fcfg, 'orchestrator', fqs_all, work, args.python, scrub)
            if fout.get('status') != 'ok':
                say('fixture run failed: %s' % fout.get('reason'))
                return 1
            roots = {r['name']: r['path'] for r in fcfg['roots']}
            visible = set(fcfg['profiles']['orchestrator']['roots'])

            def exists(g):
                root, _, rel = g.partition('/')
                return root in roots and os.path.isfile(os.path.join(roots[root], rel))
            fqs, fskipped = rb.split_questions(fqs_all, fout.get('caps') or {}, visible, exists)
            lp = run_link_prior(outs['leks'], by_set, notes_root, fout, fqs, rb)
            lp['meta'] = {'utc': now.strftime('%Y-%m-%d %H:%M:%S'), 'fixture_questions': len(fqs_all),
                          'fixture_scored': len(fqs), 'fixture_skipped': fskipped,
                          'fixture_lanes': fout.get('lanes'), 'pool': link_prior.POOL, 'seeds': link_prior.SEEDS}
            with open(os.path.join(args.out, 'link_prior.json'), 'w', encoding='ascii', newline='\n') as f:
                f.write(scrub(json.dumps(lp, indent=1, sort_keys=True, ensure_ascii=True)) + '\n')
            md = render_prior(lp)
            with open(os.path.join(args.out, 'link_prior.md'), 'w', encoding='ascii', newline='\n') as f:
                f.write(scrub(md))
            print(md)
        say('wrote results to %s' % scrub(os.path.abspath(args.out)))
        if not_ok:
            # leave a system out with --systems, the link prior with --no-link-prior
            say('asked for but not run: %s' % ', '.join(not_ok))
            return 1
        return 0
    finally:
        if not args.work and not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
