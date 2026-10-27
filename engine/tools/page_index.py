"""Persistent, incrementally maintained index for page search (engine R1).

Rankings are identical to the scan engine (brain.search_scan): the same documents, tokens, BM25 formula,
heuristics and tie-breaking, including the order in which floating-point contributions are added. What is
gone is reading and tokenising every page on every query.

Freshness: each query compares every indexed file's size, modification and status-change time (one stat
call per file) and re-reads only files that changed, appeared or disappeared, so a result never comes from
a stale index. The index lives in memory per process and is saved to .brain/page-index.pickle for cold
starts; a snapshot written by different engine code is ignored and rebuilt.
"""
import collections
import hashlib
import math
import os
from pathlib import Path
import pickle
import threading

SNAPSHOT = '.brain/page-index.pickle'
# The hand-written ranking rules of R0, each switchable for ablations; all of them together are R1.
RULES = frozenset(('aliases', 'phrases', 'title', 'priors', 'archive', 'reviews', 'procedure', 'skills', 'decay'))
_cache = {}
_lock = threading.Lock()


def _fingerprint():
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in ('page_index.py', 'brain.py'):
        h.update((here / name).read_bytes())
    return h.hexdigest()


def _scan(root):
    """Signatures of every searchable document: {relative path: (size, mtime_ns, ctime_ns)}.

    Every path is built by joining names onto root, so the relative path is the text after root's prefix;
    os.path.relpath would re-normalise both paths per file and cost more than the stat call itself."""
    out = {}
    cut = len(os.path.join(root, ''))

    def add(path):
        try:
            st = os.stat(path)
        except OSError:
            return
        out[path[cut:]] = (st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    stack = [os.path.join(root, 'wiki')]
    hub = os.path.join(root, 'wiki', 'index.md')
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                stack.append(e.path)
            elif e.name.endswith('.md') and e.path != hub:
                add(e.path)
    for e in (os.scandir(os.path.join(root, 'system')) if os.path.isdir(os.path.join(root, 'system')) else []):
        if e.name.endswith('.md') and e.is_file():
            add(e.path)
    skills = os.path.join(root, '.agents', 'skills')
    for e in (os.scandir(skills) if os.path.isdir(skills) else []):
        if e.is_dir() and os.path.isfile(os.path.join(e.path, 'SKILL.md')):
            add(os.path.join(e.path, 'SKILL.md'))
    for name in ('01_CONTINUE.md', '00_HOME.md'):
        if os.path.isfile(os.path.join(root, name)):
            add(os.path.join(root, name))
    return out


def _document(brain, root, rel):
    """One document exactly as brain.search_scan builds it."""
    p = Path(root) / rel
    full = p.read_text(encoding='utf-8')
    if rel.startswith('wiki/'):
        m, body = brain.metadata(full), full
    elif rel.startswith('system/'):
        m, body = brain.metadata(full), full
        if not m.get('title'):
            m = {'title': full.splitlines()[0].lstrip('# '), 'summary': 'Knowledge workflows and agent configuration.'}
    elif rel.startswith('.agents/'):
        body = full.split('\n---\nRules:')[0]
        sm = brain.metadata(body)
        m = {'title': sm.get('name', ''), 'summary': sm.get('description', ''), 'category': 'Skill'}
    else:
        m, body = brain.metadata(full), full
    counter = collections.Counter(brain.tokens(brain.head_text(m)) * 5 + brain.tokens(body))
    seq = brain.tokens(full)
    stop = brain.PHRASE_STOP
    bigrams = collections.Counter(a + ' ' + b for a, b in zip(seq, seq[1:]) if a in stop or b in stop)
    keep = {k: m.get(k) for k in ('title', 'label', 'summary', 'as_of', 'status', 'category')}
    title_words = frozenset(brain.tokens(str(m.get('title', '')) + ' ' + str(m.get('label', ''))))
    return {'meta': keep, 'counter': counter, 'length': sum(counter.values()), 'bigrams': bigrams,
            'title_words': title_words, 'archived': brain.is_archived(rel, m)}


class PageIndex:
    """Postings hold term frequencies (term -> {document: tf}); documents keep only what the heuristics need.
    Scoring is term-at-a-time but adds each document's contributions in the same order and with the same
    expression as search_scan, so scores are bit-identical."""

    def __init__(self, fingerprint):
        self.fingerprint = fingerprint
        self.signatures, self.docs = {}, {}
        self.postings = {}
        self.total_length = 0
        self._decay = self._norm = None

    def _remove(self, rel):
        doc = self.docs.pop(rel)
        for w in doc['terms']:
            post = self.postings[w]
            del post[rel]
            if not post:
                del self.postings[w]
        self.total_length -= doc['length']
        del self.signatures[rel]

    def _add(self, brain, root, rel, signature):
        doc = _document(brain, root, rel)
        counter = doc.pop('counter')
        doc['terms'] = tuple(counter)
        self.docs[rel], self.signatures[rel] = doc, signature
        for w, tf in counter.items():
            self.postings.setdefault(w, {})[rel] = tf
        self.total_length += doc['length']

    def refresh(self, brain, root):
        """Bring the index up to date with the files; returns the number of documents re-read or dropped."""
        current = _scan(root)
        changed = 0
        for rel in [r for r in self.signatures if current.get(r) != self.signatures[r]]:
            self._remove(rel)
            changed += 1
        for rel, signature in current.items():
            if rel not in self.signatures:
                self._add(brain, root, rel, signature)
                changed += 1
        if changed or self._decay is None:
            self._decay = self._dated_decay(brain)
            # The length part of the BM25 denominator depends only on the document and the average length;
            # search_scan evaluates it to this same float before adding tf, so caching it changes no score.
            avg = self.total_length / max(1, len(self.docs))
            self._norm = {rel: 1.2 * (0.25 + 0.75 * doc['length'] / max(1, avg)) for rel, doc in self.docs.items()}
        return changed

    def _dated_decay(self, brain):
        dated = {r: m.group(2) for r, m in ((r, brain.DATED_RECORD.match(r)) for r in self.docs) if m}
        dates = sorted(dated.values())
        return {r: 0.9 ** sum(1 for d in dates if d > own) for r, own in dated.items()}

    def search(self, brain, root, query, limit=5, plain=False, rules=None):
        """Engine R1 with every rule of RULES; plain=True (no rule) is the ablation R1p: BM25 over the same index
        (title words still count five times). Any subset of RULES gives a component ablation."""
        rules = frozenset() if plain else (RULES if rules is None else frozenset(rules))
        tokens = brain.tokens
        query_words = [w for w in tokens(query) if w not in brain.STOP]
        qset, qall = set(query_words), set(tokens(query))
        expanded = list(query_words)
        for word in (query_words if 'aliases' in rules else []):
            expanded += tokens(' '.join(brain.ALIASES.get(word, [])))
        expanded = list(dict.fromkeys(expanded))
        all_words = tokens(query)
        phrases = {a + ' ' + b for a, b in zip(all_words, all_words[1:])
                   if (a in brain.PHRASE_STOP and b not in brain.STOP) or (b in brain.PHRASE_STOP and a not in brain.STOP)}
        n = len(self.docs)
        norm = self._norm
        scores = {}
        for w in expanded:
            post = self.postings.get(w)
            if not post:
                continue
            df = len(post)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            weight = 1 if w in query_words else 0.6
            get = scores.get
            for rel, tf in post.items():
                scores[rel] = get(rel, 0.0) + idf * tf * 2.2 / (tf + norm[rel]) * weight
        if not rules:
            ranked = sorted((-round(score, 4), rel) for rel, score in scores.items() if score)
        else:
            context = {'qset': qset, 'qall': qall, 'phrases': phrases, 'archive': bool(qset & brain.ARCHIVE_INTENT),
                       'priors': brain.hub_priors(root), 'procedure': bool(brain.PROCEDURE_QUESTION.search(query))}
            context['current'] = bool(qset & brain.CURRENT_INTENT) and not context['archive']
            ranked = sorted((-round(self._finish(brain, rel, score, context, rules), 4), rel) for rel, score in scores.items() if score)
        hits = []
        for neg, path in ranked[:limit]:
            m = self.docs[path]['meta']
            hits.append({'path': path, 'title': m.get('title'), 'summary': m.get('summary'), 'as_of': m.get('as_of'),
                         'status': m.get('status'), 'score': -neg})
        return hits

    def _finish(self, brain, path, score, ctx, rules=None):
        """The final score after the phrase bonus and the page heuristics, in search_scan's order; rules
        (default: all) selects which of them apply."""
        rules = RULES if rules is None else rules
        doc = self.docs[path]
        m = doc['meta']
        if 'phrases' in rules and ctx['phrases']:
            score += sum(tf / (tf + 1) for pair, tf in doc['bigrams'].items() if pair in ctx['phrases'])
        if 'title' in rules:
            overlap = len(ctx['qset'] & doc['title_words']) / max(1, len(ctx['qset']))
            score *= 1 + 0.6 * overlap
        prior, intent = ctx['priors'].get(path, (1.0, set()))
        if 'priors' in rules and prior < 1 and not (ctx['qall'] & intent):
            score *= prior
        if 'archive' in rules and doc['archived'] and not ctx['archive']:
            score *= 0.35 if ctx['current'] else 0.85
        if 'reviews' in rules and path.startswith('wiki/reviews/') and not (ctx['qset'] & brain.REVIEW_INTENT):
            score *= 0.8
        if 'procedure' in rules and ctx['procedure'] and path in brain.PROCEDURE_PAGES:
            score *= 1.25
        if 'skills' in rules and m.get('category') == 'Skill' and not (ctx['procedure'] or ctx['qall'] & brain.SKILL_INTENT):
            score *= 0.7
        if 'decay' in rules and path in self._decay:
            score *= self._decay[path]
        return score


def get(brain, root, save=False):
    """The process-wide index for a vault, loaded from the snapshot if valid and refreshed against the files."""
    root = str(Path(root).resolve())
    with _lock:
        index = _cache.get(root)
        fingerprint = _fingerprint()
        if index is None or index.fingerprint != fingerprint:
            index = _load(root, fingerprint) or PageIndex(fingerprint)
            _cache[root] = index
        fresh = not index.signatures
        changed = index.refresh(brain, root)
        if save or (fresh and changed):
            _save(root, index)
        return index


def _load(root, fingerprint):
    path = os.path.join(root, SNAPSHOT)
    try:
        with open(path, 'rb') as f:
            index = pickle.load(f)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ValueError):
        return None
    return index if isinstance(index, PageIndex) and index.fingerprint == fingerprint else None


def _save(root, index):
    import storage
    try:
        storage.atomic_write_bytes(Path(root) / SNAPSHOT, pickle.dumps(index, protocol=4))
    except OSError:
        pass


def search(brain, root, query, limit=5, plain=False, rules=None):
    return get(brain, root).search(brain, Path(root).resolve(), query, limit, plain, rules)
