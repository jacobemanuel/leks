"""Page retrieval engines behind brain.search (standard library; system Python 3.9).

R1   indexed BM25 with the hand-written rules (page_index; identical to the scan engine R0)
R1p  the plain BM25 ablation (no aliases, phrase bonus, title overlap, priors, intent rules or decay)
R2   dense retrieval with bge-small-en-v1.5 (optional subsystem, tools/dense_helper.py)
R4   reciprocal rank fusion of R1 and R2 (k = 60, top 20 of each), then the archive/current policy of R0;
     R4:once leaves out that second application of the policy (R1's ranking already contains it; on the flat
     RRF scale the multipliers act far more strongly than on BM25 scores), R4:guard never ranks an archived
     page first unless the question asks for the archive; joinable as R4:once+guard
R5   R4's top 20 pages reranked by the cross-encoder ms-marco-MiniLM-L6-v2 on each page's two passages
     closest to the question (dense); page score = sigmoid of the best logit, then the archive/current policy;
     variants R5:m1 (one passage per page) and R5:int8 (quantised model), joinable as R5:m1+int8;
     R5:rrf fuses the reranker ranking with R4's ranking (RRF k = 60) instead of replacing it, and
     R5:guard never ranks an archived page first unless the question asks for the archive
Ablation specs: R1-<rule>[-<rule>] (R1 without rules), R1p+<rule>[+<rule>] (plain BM25 with rules) and
R4:<lexical spec> (the hybrid with another lexical half); rules are page_index.RULES.

The engine comes from "retrieval": {"engine": ...} in system/vault-config.json (default R1). When a dense
engine is configured but the subsystem is unavailable, its lexical half answers and the result says so."""
from pathlib import Path

import dense_client
import page_index

ENGINES = ('R1', 'R1p', 'R2', 'R4', 'R5')
RRF_K, DEPTH = 60, 20
R5_POOL, R5_OPTIONS = 20, {'m1', 'int8', 'rrf', 'guard'}
R4_OPTIONS = {'once', 'guard'}


def lexical_rules(spec):
    """The rules of a lexical engine spec, or None if the spec is not lexical or names an unknown rule."""
    if spec == 'R1':
        return page_index.RULES
    if spec == 'R1p':
        return frozenset()
    if spec.startswith('R1p+'):
        rules = frozenset(spec[4:].split('+'))
    elif spec.startswith('R1-'):
        rules = page_index.RULES - frozenset(spec[3:].split('-'))
        if not frozenset(spec[3:].split('-')) <= page_index.RULES:
            return None
    else:
        return None
    return rules if rules <= page_index.RULES else None


def parse(spec):
    """(lexical rules, dense) for a valid engine spec, else None."""
    if spec == 'R2':
        return page_index.RULES, 'only'
    if spec == 'R4' or (spec.startswith('R4:') and set(spec[3:].split('+')) <= R4_OPTIONS):
        return page_index.RULES, 'fuse'
    if spec == 'R5' or (spec.startswith('R5:') and set(spec[3:].split('+')) <= R5_OPTIONS):
        return page_index.RULES, 'rerank'
    if spec.startswith('R4:') and lexical_rules(spec[3:]) is not None:
        return lexical_rules(spec[3:]), 'fuse'
    rules = lexical_rules(spec)
    return None if rules is None else (rules, None)


def config_problems(setting):
    """Reasons why a "retrieval" configuration value is invalid (empty list when it is valid or absent)."""
    if setting is None:
        return []
    if not isinstance(setting, dict):
        return ['"retrieval" must be an object']
    problems = []
    engine = setting.get('engine', 'R1')
    if not isinstance(engine, str) or not parse(engine):
        problems.append('unknown retrieval engine %r (R1 is used)' % (engine,))
    if setting.get('passages', 'lexical') not in PASSAGE_METHODS:
        problems.append('unknown passage selection %r (lexical is used)' % (setting.get('passages'),))
    if not isinstance(setting.get('abstain', False), (bool, dict)):
        problems.append('"abstain" must be true, false or an object')
    return problems


def configured(brain, root):
    """(engine, problem): the configured engine, or R1 and the reason when the configuration is invalid."""
    setting = brain.config(Path(root)).get('retrieval')
    problems = config_problems(setting)
    engine = (setting or {}).get('engine', 'R1') if isinstance(setting, dict) else 'R1'
    if not isinstance(engine, str) or not parse(engine):
        return 'R1', '; '.join(problems)
    return engine, '; '.join(problems) or None


def _hit(meta, path, score):
    return {'path': path, 'title': meta.get('title'), 'summary': meta.get('summary'), 'as_of': meta.get('as_of'),
            'status': meta.get('status'), 'score': score}


def _intent(brain, query):
    qset = {w for w in brain.tokens(query) if w not in brain.STOP}
    archive = bool(qset & brain.ARCHIVE_INTENT)
    return archive, bool(qset & brain.CURRENT_INTENT) and not archive


def rerank(brain, index, root, query, fused, limit, spec):
    """R5 on the fused R4 ranking; None when the reranker is unavailable."""
    import math
    options = set(spec[3:].split('+')) if spec.startswith('R5:') else set()
    pool = [h['path'] for h in fused[:R5_POOL]]
    scores = dense_client.rerank(root, query, pool, 1 if 'm1' in options else 2,
                                 'model_qint8_arm64.onnx' if 'int8' in options else 'model.onnx')
    if scores is None:
        return None
    archive, current = _intent(brain, query)
    if 'rrf' in options:
        order = sorted(pool, key=lambda p: (-scores.get(p, -1e9), p))
        values = {p: 1 / (RRF_K + order.index(p) + 1) + 1 / (RRF_K + pool.index(p) + 1) for p in pool}
    else:
        values = {p: 1 / (1 + math.exp(-scores[p])) if p in scores else 0.0 for p in pool}
    ranked = []
    for path in pool:
        value = values[path]
        if index.docs[path]['archived'] and not archive:
            value *= 0.35 if current else 0.85
        ranked.append((-value, path))
    ranked.sort()
    pairs = [(p, -v) for v, p in ranked]
    if 'guard' in options:
        pairs = _guard(index, pairs, archive)
    out = [_hit(index.docs[p]['meta'], p, round(s, 6)) for p, s in pairs]
    return (out + [h for h in fused[R5_POOL:]])[:limit]


def _guard(index, ranked, archive):
    """[(path, score)] best first: without archive intent, the best non-archived page takes rank 1."""
    if archive or not ranked or not index.docs[ranked[0][0]]['archived']:
        return ranked
    first = next((i for i, (p, _) in enumerate(ranked) if not index.docs[p]['archived']), None)
    return ranked if first is None else [ranked[first]] + ranked[:first] + ranked[first + 1:]


def fuse(brain, index, query, lexical, dense, limit, options=frozenset()):
    """RRF of two rankings, then archived pages weighted as in R0 (x0.35 for current-intent questions,
    x0.85 otherwise, unless the question asks for the archive); options as in the module docstring."""
    scores = {}
    for hits in (lexical, dense):
        for rank, h in enumerate(hits[:DEPTH], 1):
            scores[h['path']] = scores.get(h['path'], 0.0) + 1 / (RRF_K + rank)
    archive, current = _intent(brain, query)
    if 'once' not in options:
        for path in scores:
            if index.docs[path]['archived'] and not archive:
                scores[path] *= 0.35 if current else 0.85
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    if 'guard' in options:
        ranked = _guard(index, ranked, archive)
    return [_hit(index.docs[p]['meta'], p, round(s, 6)) for p, s in ranked[:limit]]


def search(brain, root, query, limit=5, engine=None):
    """(hits, engine used, fallback reason or None)."""
    problem = None
    if not engine:
        engine, problem = configured(brain, root)
    elif not parse(engine):
        engine, problem = 'R1', 'unknown retrieval engine %r (R1 is used)' % (engine,)
    rules, mode = parse(engine)
    ns = brain if hasattr(brain, 'tokens') else brain._namespace()
    if mode is None:
        return page_index.search(ns, root, query, limit, rules=rules), engine, problem
    index = page_index.get(ns, root)
    resolved = Path(root).resolve()
    dense = dense_client.search(root, query, max(DEPTH, limit))
    if dense is None:
        fallback = engine[3:] if engine.startswith('R4:R1') else 'R1'
        why = dense_client.unavailable(root) or 'dense helper error'
        return index.search(ns, resolved, query, limit, rules=rules), fallback, why
    dense = [h for h in dense if h['path'] in index.docs]
    if mode == 'only':
        return [_hit(index.docs[h['path']]['meta'], h['path'], h['score']) for h in dense[:limit]], engine, None
    lexical = index.search(ns, resolved, query, DEPTH, rules=rules)
    if mode == 'fuse':
        options = frozenset(engine[3:].split('+')) if engine.startswith('R4:') and not engine.startswith('R4:R1') else frozenset()
        return fuse(ns, index, query, lexical, dense, limit, options), engine, None
    fused = fuse(ns, index, query, lexical, dense, max(R5_POOL, limit))
    reranked = rerank(ns, index, root, query, fused, limit, engine)
    if reranked is None:
        return fused[:limit], 'R4', dense_client.unavailable(root) or 'reranker error'
    return reranked, engine, None


PASSAGE_METHODS = ('lexical', 'dense', 'hybrid')


def _lexical_block(brain, block, wanted):
    """The answer pack's original block score: distinct question terms in the block plus 0.05 per occurrence."""
    words = brain.terms(block)
    return len(wanted & set(words)) + 0.05 * sum(1 for w in words if w in wanted)


def passage_candidates(brain, root, question, hits, wanted, method=None):
    """Blocks of the best pages as (score, page rank, block index, text), best first, and the method used.
    lexical: the original stemmed-overlap score; dense: cosine of the dense passage holding the block;
    hybrid: reciprocal rank fusion of both block rankings. Each score is multiplied by the page weight
    (brain.PACK_PAGE_WEIGHTS). dense and hybrid fall back to lexical without the dense helper."""
    setting = brain.config(Path(root)).get('retrieval')
    method = method or (setting.get('passages', 'lexical') if isinstance(setting, dict) else 'lexical')
    method = method if method in PASSAGE_METHODS else 'lexical'
    weights = brain.PACK_PAGE_WEIGHTS
    blocks = [(rank, i, block, weight) for rank, (hit, weight) in enumerate(zip(hits, weights))
              for i, block in enumerate(brain.page_blocks(Path(root), hit['path']))]
    lexical = [_lexical_block(brain, b, wanted) for _, _, b, _ in blocks]
    dense = None
    if method in ('dense', 'hybrid'):
        scores = dense_client.passage_scores(root, question, [h['path'] for h in hits[:len(weights)]])
        if scores is not None:
            best = {}
            for rank, hit in enumerate(hits[:len(weights)]):
                for first, last, score in scores.get(hit['path'], []):
                    for i in range(first, last + 1):
                        best[rank, i] = max(best.get((rank, i), -1.0), score)
            dense = [best.get((rank, i), -1.0) for rank, i, _, _ in blocks]
    if dense is None:
        method = 'lexical'
    if method == 'lexical':
        values = [s * w for s, (_, _, _, w) in zip(lexical, blocks)]
    elif method == 'dense':
        values = [s * w for s, (_, _, _, w) in zip(dense, blocks)]
    else:
        def ranks(scores, positive):
            order = sorted((j for j in range(len(blocks)) if not positive or scores[j] > 0),
                           key=lambda j: (-scores[j], blocks[j][0], blocks[j][1]))
            return {j: r for r, j in enumerate(order, 1)}
        rl, rd = ranks(lexical, True), ranks(dense, False)
        values = [blocks[j][3] * ((1 / (RRF_K + rl[j]) if j in rl else 0.0) + 1 / (RRF_K + rd[j])) for j in range(len(blocks))]
    candidates = [(v, rank, i, block) for v, (rank, i, block, _) in zip(values, blocks)]
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    return candidates, method


# Abstention with the no-answer QA model (optional, off by default): evidence and threshold were chosen on the
# development set by the rule fixed beforehand (highest AUC; threshold with at most 5 % incorrect abstention).
ABSTAIN_DEFAULT = {'evidence': 'passages8', 'threshold': 6.9598}


def abstention(brain, root, question, hits, setting=None):
    """(status or None, details): 'insufficient_evidence' when the best answerability margin over the evidence
    is at or below -threshold. setting None reads "abstain" from the retrieval configuration (absent: off)."""
    if setting is None:
        configured_setting = brain.config(Path(root)).get('retrieval')
        setting = configured_setting.get('abstain') if isinstance(configured_setting, dict) else None
    if setting is None or setting is False:
        return None, None
    setting = dict(ABSTAIN_DEFAULT, **(setting if isinstance(setting, dict) else {}))
    per_page = {'passages4': 1, 'passages8': 2}.get(setting['evidence'], 2)
    texts = dense_client.top_passages(root, question, [h['path'] for h in hits[:4]], per_page)
    margins = dense_client.answerability(root, question, texts) if texts else None
    if margins is None:
        return None, {'abstain': 'unavailable', 'reason': dense_client.unavailable(root) or 'dense helper error'}
    best = max(m for m, _ in margins)
    status = 'insufficient_evidence' if best <= -setting['threshold'] else None
    return status, {'answerability_margin': best, 'abstain_threshold': -setting['threshold'], 'evidence': setting['evidence']}
