"""Search one profile database.

Passages are ranked by words (bm25) and, when the models are there, by meaning (dense vectors), with
exact names as phrases and whole notes by title, summary, path and headings. An optional late
interaction reranker works over the top passages. This process holds no model code: query vectors and
scores come from the model daemon through modeld_client.Client, or not at all, and then search answers
from the lanes it has and says so on its last line.

Status is used only when the question asks for the current state, so a history question can still
reach an old note.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import posixpath
import re
import sqlite3
import time
import unicodedata

try:
    import numpy as np
except ImportError:  # the meaning lane stays closed without it
    np = None

from . import lane_locate, lane_prior, source_class, text

BUDGET_KEYS = ('embed', 'rerank', 'connect', 'ping')
RRF = 60
PRIOR = {'current': 1.25, 'record': 1.0, 'unknown': 1.0, 'historical-record': 0.9, 'superseded': 0.72, 'stale': 0.6}
KIND = {'state': 1.3, 'rule': 1.05, 'decision': 1.05, 'result': 1.0, 'note': 1.0, 'code': 0.95,
        'document': 0.8, 'image': 0.8}
# the intent priors act on a flat rrf list, so they only reach into the first passages of it
PRIOR_WINDOW = 30
W_IDENTS = 0.5
DOC_ALPHA = 0.9
DOC_SMALL = 300   # fewer document passages than this: rescale their bm25 against the notes
REFS_ALPHA = 0.3
W_SYMBOLS = 1.0
W_TITLE = 2.0
MAX_READ_PAGES = 5
MAX_READ_CHARS = 16000
MAX_Q = 2000
MAX_TEXT = 4000
CHANGED = 'file changed since indexed: read the file'
NO_MATCH = 'no match in this profile'
NOT_FOUND = 'not visible in this profile, or not indexed'

# neighbours: the notes to bring in line after an edit
NOTE_KINDS = ('state', 'rule', 'decision', 'result', 'note')
NB_MAX = 30           # most notes one call lists
NB_CLOSE = 5          # notes close in meaning, or in words without vectors
NB_CHAIN = 10         # steps followed each way along a supersession chain
NB_NUMBERS = 24       # removed numbers followed per call
NB_SENTENCES = 16     # removed sentences followed per call
NB_ROWS = 200         # candidate notes per removed fact
NB_LINK_ROWS = 400    # candidate notes that may link here
NB_DOCS = 300         # candidate notes read from disk per call
NB_WINDOW = 8         # words in a sentence window
NB_MIN_SENT = 6       # a shorter sentence says too little to follow
NB_MAX_BYTES = 2 * 1024 * 1024
MAX_WAS = 2 * 1024 * 1024
NB_GONE = ('the file is gone from disk, so only the notes that link to it are listed: fix or remove those links.')
NB_RECORDS = ('A note marked record, superseded or historical-record keeps what held at its time: leave its '
              'text as it is.')

# fixed reasons a lane can be closed. nothing a reply says ever lands in a lane line
W_NO_MODELS = 'no models configured'
W_NO_BACKEND = 'model backend not installed'
W_NO_NUMPY = 'numpy not installed'
W_NO_WEIGHTS = 'model weights not found'
W_NO_MODEL = 'model unavailable'
W_BUDGET = 'model over its time budget'
W_NO_VECTORS = 'no vectors yet'
W_STEP = 'vectors out of step with the index'
W_NOT_ASKED = 'not asked for'
W_NO_RERANKER = 'no reranker configured'
WHY_FIXED = frozenset({W_NO_MODELS, W_NO_BACKEND, W_NO_NUMPY, W_NO_WEIGHTS, W_NO_MODEL, W_BUDGET, W_NO_VECTORS,
                       W_STEP, W_NOT_ASKED, W_NO_RERANKER})
# why the documents lane is off
D_NO_PDF = 'pdf extra not installed'
D_NONE = 'no document indexed'
# the command that fixes a closed lane, status names it
# leks is not on PyPI: the extras install from the clone
PIP_MODELS = 'pip install -e ".[models]" in the LEKS clone'
FIX = {W_NO_BACKEND: PIP_MODELS, W_NO_NUMPY: PIP_MODELS,
       W_NO_WEIGHTS: 'leks models fetch', W_NO_VECTORS: 'leks embed', W_STEP: 'leks embed',
       D_NO_PDF: 'pip install -e ".[pdf]" in the LEKS clone, then leks build'}


def _fixed(why) -> str:
    """A reason from the fixed set, else the generic one."""
    return why if why in WHY_FIXED else W_NO_MODEL


def _code():
    """The code lane module, or None when it is not importable."""
    try:
        from . import lane_code
        return lane_code
    except ImportError:
        return None


_HIT_COLS = ('c.id', 'c.doc', 'c.start_line', 'c.end_line', 'c.heading', 'c.text', 'c.refs', 'd.path', 'd.root',
             'd.title', 'd.kind', 'd.status', 'd.status_src', 'd.date', 'd.sha256', 'd.summary', 'd.warning',
             'd.copies', 'd.unit')
_HIT_KEYS = tuple(c.split('.')[1] for c in _HIT_COLS)



def _ocr_label_on(cfg):
    try:
        return bool((getattr(cfg, 'ocr', None) or {}).get('label_results'))
    except AttributeError:
        return False


# neighbours: numbers and sentences a note no longer has, and who still has them

# a unit after a number, every spelling with the unit it means
_UNIT = {u: u for u in ('x k ms us s min h day week month kb mb gb tb kib mib gib tib px fps hz khz mhz ghz '
                        'mm cm m km g kg usd eur').split()}
_UNIT.update({w: u for u, ws in (('s', 'sec secs second seconds'), ('min', 'mins minute minutes'),
                                  ('h', 'hr hrs hour hours'), ('day', 'days'), ('week', 'weeks'),
                                  ('month', 'months')) for w in ws.split()})
_SPELLINGS = {u: [w for w in _UNIT if _UNIT[w] == u] for u in set(_UNIT.values())}
# a number on digit boundaries: not the 002 of DEC-002, not the 16 of T16, not a piece of 1.5.2
_NUMBER = re.compile(r'(?i)(?<![\w.,:/-])(\d+(?:[.,:/-]\d+)*)(?:\s?(%)|\s?('
                     + '|'.join(sorted(_UNIT, key=len, reverse=True)) + r')\b)?(?![\w%])')
_META_LINE = re.compile(r'(?i)^\s*(?:[-*+>]\s*)*[*_]*(?:status|date|updated|created|revisit|superseded[\s-]+by'
                        r'|supersedes|replaced[\s-]+by|replaces)[*_]*\s*:')
_LIST_ITEM = re.compile(r'\s*(?:[-*+]|\d+[.)])\s')
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')
# a record keeps what held at its time, so current notes come first
_NB_RANK = {'record': 1, 'superseded': 2, 'historical-record': 2}
_NB_COLS = ('id', 'path', 'root', 'rel', 'kind', 'status', 'date')


def _nb_numbers(line):
    """(number, unit, as written) on one line. A bare number counts from three characters on."""
    out = []
    for m in _NUMBER.finditer(line):
        num = m.group(1)
        unit = '%' if m.group(2) else _UNIT.get((m.group(3) or '').lower(), '')
        if unit or len(num) >= 3:
            out.append((num, unit, ' '.join(m.group(0).split())))
    return out


def _nb_tokens(s):
    """Words and numbers, casefolded and without accents, split the way the full-text index splits them."""
    if not s.isascii():
        s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    return re.findall(r'[^\W_]+', s.casefold())


def _nb_text(raw, ext):
    """File bytes to the text the index reads: html without its tags, plain line ends."""
    t = raw.decode('utf-8', 'replace')
    if ext in ('.html', '.htm'):
        from . import chunking
        t = chunking.strip_html(t)
    return t.replace('\r\n', '\n')


def _nb_join(a, b):
    """Two index pieces of one long line: the second starts with the end of the first."""
    for k in range(min(len(a), len(b), 200), 0, -1):
        if a.endswith(b[:k]):
            return a + b[k:]
    return a + b


def _nb_body(text, code):
    """Lines of the old text to take facts from, (for numbers, for sentences). The front matter and the
    status and date lines are out of both; a code block or comment is a blank line for sentences. A code
    file gives numbers only."""
    lines = text.split('\n')
    if code:
        return lines, []
    from . import check, chunking
    start = chunking.body_start(lines)
    masked = check._mask(text).split('\n')
    nums, prose = [], []
    for i, ln in enumerate(lines):
        if i < start or _META_LINE.match(ln):
            prose.append('')
            continue
        nums.append(ln)
        prose.append(ln if masked[i].strip() or not ln.strip() else '')
    return nums, prose


def _nb_blocks(lines):
    """Paragraphs, list items and table rows as one string each, so a sentence wrapped over lines stays whole."""
    out, cur = [], []
    for ln in lines:
        s = ln.strip()
        if (not s or s.startswith('#') or s.startswith('|') or _LIST_ITEM.match(ln)) and cur:
            out.append(' '.join(cur))
            cur = []
        if s and not s.startswith('#'):
            cur.append(s)
    if cur:
        out.append(' '.join(cur))
    return out


def _nb_grams(toks, k):
    return {hash(tuple(toks[i:i + k])) for i in range(len(toks) - k + 1)}


def _nb_numless(toks):
    return ['0' if t.isdigit() else t for t in toks]


def _nb_sentences(blocks, after, limit):
    """For each old sentence the new text lacks, its first window of words that is gone, at most limit.
    A window that is still there with other numbers is skipped, the numbers carry that change."""
    grams, out, seen = {}, [], set()
    numless = _nb_numless(after)
    for b in blocks:
        for s in _SENTENCE_END.split(b):
            toks = _nb_tokens(s)
            if len(toks) < NB_MIN_SENT:
                continue
            k = min(len(toks), NB_WINDOW)
            if k not in grams:
                grams[k] = (_nb_grams(after, k), _nb_grams(numless, k))
            words, shapes = grams[k]
            for i in range(len(toks) - k + 1):
                w = tuple(toks[i:i + k])
                if hash(w) in words or hash(tuple(_nb_numless(w))) in shapes:
                    continue
                if w not in seen:
                    seen.add(w)
                    out.append(w)
                break
            if len(out) >= limit:
                return out
    return out


def _nb_num_query(num, unit):
    """An fts5 match on the text column for a number, glued to its unit or not. The confirm step decides."""
    ph = ' '.join(re.findall(r'\d+', num))
    if not unit:
        return '{text} : "%s" *' % ph
    if unit == '%':
        return '{text} : "%s"' % ph
    alts = []
    for w in _SPELLINGS[unit]:
        alts += ['"%s %s"' % (ph, w), '"%s%s"' % (ph, w)]
    return '{text} : (%s)' % ' OR '.join(alts)


def _nb_removed(before, after, code):
    """Facts of the old text the new one lacks, numbers first, as dicts with show, query and num or words."""
    nums, prose = _nb_body(before, code)
    have, bare = set(), set()
    for ln in after.split('\n'):
        for num, unit, _ in _nb_numbers(ln):
            have.add((num, unit))
            bare.add(num)
    facts, seen = [], set()
    for ln in nums:
        for num, unit, show in _nb_numbers(ln):
            key = (num, unit)
            # a bare number stays when the new text has it with any unit
            if key in seen or (key in have if unit else num in bare):
                continue
            seen.add(key)
            if len(facts) < NB_NUMBERS:
                facts.append({'show': show, 'query': _nb_num_query(num, unit), 'num': key})
    for w in ([] if code else _nb_sentences(_nb_blocks(prose), _nb_tokens(after), NB_SENTENCES)):
        s = ' '.join(w)
        facts.append({'show': '"%s"' % (s if len(s) <= 80 else s[:77] + '...'),
                      'query': '{text} : "%s"' % s, 'words': ' %s ' % s})
    return facts


def _nb_holds(facts, text):
    """(what a candidate's text still holds of the facts, the first line that holds one)."""
    lines = text.split('\n')
    nums, bare, norm, tline = {}, {}, '', []
    if any('num' in f for f in facts):
        for i, ln in enumerate(lines, 1):
            for num, unit, _ in _nb_numbers(ln):
                nums.setdefault((num, unit), i)
                bare.setdefault(num, i)
    if any('words' in f for f in facts):
        toks = []
        for i, ln in enumerate(lines, 1):
            t = _nb_tokens(ln)
            toks += t
            tline += [i] * len(t)
        norm = ' %s ' % ' '.join(toks)
    held, first = [], None
    for f in facts:
        if 'num' in f:
            num, unit = f['num']
            line = nums.get(f['num']) if unit else bare.get(num)
        else:
            pos = norm.find(f['words'])
            line = tline[norm.count(' ', 0, pos + 1) - 1] if pos >= 0 else None
        if line is not None:
            held.append(f['show'])
            first = line if first is None else min(first, line)
    return held, first


def _nb_why(held):
    s = ', '.join(held[:3]) + (' and %d more' % (len(held) - 3) if len(held) > 3 else '')
    return 'still holds what this note removed: ' + s


class Engine:
    """Read-only search over one profile database."""

    def __init__(self, cfg, profile=None, db_path=None, immutable=False, client=None):
        from . import schema
        self.cfg = cfg
        if profile is None:
            from . import config
            profile = config.active_profile(cfg)
        self.profile = profile
        self.db_path = db_path or cfg.profile_db(profile)
        self.immutable = immutable
        if not os.path.exists(self.db_path):
            from .config import ConfigError
            raise ConfigError('no index for profile %s yet: run leks build' % profile)
        self.db = schema.open_ro(self.db_path, immutable)
        self._client = client
        self.embedder = cfg.embedder
        self.reranker = cfg.reranker
        try:
            self.meta = dict(self.db.execute('SELECT k, v FROM meta').fetchall())
        except sqlite3.Error:
            self.meta = {}
        self._nw = None
        self._dn = None
        self._doff = None
        self._doff_known = False
        # a caller that knows the models are not there sets this: search runs on words and says why
        self.words_why = None
        self._vmap_cache = None
        self._nb_names_cache = None
        self.vec = None
        self.covered = 0
        self.vec_why = None
        if self.reranker is None:
            self.rer_why = W_NO_MODELS if self.embedder is None else W_NO_RERANKER
        else:
            self.rer_why = None
        self._open_vectors()
        self.has_docfts = self._table('docfts')
        self.has_dfts = self._table('dfts')
        lc = _code()
        try:
            self.has_syms = bool(lc and lc.has_symbols(self.db))
        except sqlite3.Error:
            self.has_syms = False

    # setup helpers


    def _show_warning(self, w):
        return not str(w).startswith('text from OCR') or _ocr_label_on(self.cfg)

    def _table(self, name: str) -> bool:
        try:
            return bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone())
        except sqlite3.Error:
            return False

    def docs_off(self):
        """None when the documents lane has pages to search, else the reason it is off."""
        if not self._doff_known:
            try:
                has_rows = self.has_dfts and bool(self.db.execute('SELECT 1 FROM dfts LIMIT 1').fetchone())
            except sqlite3.Error:
                has_rows = False
            self._doff = None if has_rows else (D_NO_PDF if self._pdf_unread() else D_NONE)
            self._doff_known = True
        return self._doff

    def _pdf_unread(self) -> bool:
        """A pdf the roots want indexed sits there, and nothing here can read pdf text."""
        try:
            from . import docs
            cap = docs.capabilities(self.cfg)
        except Exception:
            cap = {}
        if cap.get('pdf_text') or cap.get('ocr'):
            return False
        from . import config
        roots = {r.name: r for r in getattr(self.cfg, 'roots', ())}
        try:
            rows = self.db.execute("SELECT path FROM names WHERE ext = '.pdf' LIMIT 500").fetchall() \
                if lane_locate.has_names(self.db) else []
        except sqlite3.Error:
            rows = []
        for (p,) in rows:
            root, _, rel = (p or '').partition('/')
            if root in roots and rel and config.wanted(roots[root], rel):
                return True
        return False

    def _ready_why(self, op, key):
        """Why a model call would not even be tried now, None when it would. Never talks to the daemon."""
        c = self._get_client()
        if c is None:
            return W_NO_MODEL
        fn = getattr(c, 'not_ready', None)
        if not callable(fn):
            return None   # a client that cannot tell is taken at its word
        try:
            w = fn(op, key)
        except Exception:
            return W_NO_MODEL
        return _fixed(w) if w else None

    def _open_vectors(self):
        """The meaning lane opens only when the vector file is exactly the one written for this database."""
        spec = self.embedder
        if spec is None:
            self.vec_why = W_NO_MODELS
            return
        if np is None:
            self.vec_why = W_NO_NUMPY
            return
        try:
            from . import vecstore
            vec, covered, why = vecstore.open_vectors(self.cfg, self.profile, self.meta.get('built'), spec)
        except (ImportError, OSError, ValueError, sqlite3.Error):
            self.vec_why = W_NO_VECTORS
            return
        if vec is None:
            self.vec_why = why if why in WHY_FIXED else W_NO_VECTORS
            return
        if getattr(vec, 'ndim', 2) == 1:
            vec = vec.reshape(-1, spec.dim)
        self.vec = vec
        self.covered = int(covered or 0)
        self.vec_why = None

    def _get_client(self):
        if self._client is None:
            try:
                from . import modeld_client
                # a search starts the daemon when it is down: Copilot has no warm-up hook, and a bulk run ends it
                self._client = modeld_client.Client.from_config(self.cfg, wake=True)
            except (ImportError, OSError, ValueError, KeyError):
                return None
        return self._client

    def close(self):
        try:
            self.db.close()
        except sqlite3.Error:
            pass

    def coverage(self) -> float:
        return self.covered / len(self.vec) if self.vec is not None and len(self.vec) else 0.0

    def vector_mode(self) -> str:
        return 'meaning' if self.vec is not None else 'words'

    def _vmap(self) -> dict:
        """Vector row -> chunk ids."""
        if self._vmap_cache is None:
            m: dict = {}
            for v, i in self.db.execute('SELECT vec, id FROM chunks WHERE vec >= 0').fetchall():
                m.setdefault(v, []).append(i)
            self._vmap_cache = m
        return self._vmap_cache

    def _newer(self) -> dict:
        """doc id -> (display path of the newer doc, how, target id)."""
        if self._nw is None:
            nw = {}
            try:
                for a, t, p, how in self.db.execute(
                        'SELECT n.doc, n.target, d.path, n.how FROM newer n JOIN docs d ON d.id = n.target'):
                    nw[a] = (p, how, t)
            except sqlite3.OperationalError:
                pass
            self._nw = nw
        return self._nw

    # word lanes

    def _match(self, q: str):
        t = text.terms_of(q)
        return text.fts_or(t) if t else None

    def _scored(self, q, n=100, w_idents=W_IDENTS):
        """Notes list and documents list with their bm25 values, best first (values are negative)."""
        m = self._match(q)
        if not m:
            return [], []
        try:
            a = self.db.execute('SELECT rowid, bm25(fts, 6.0, 3.0, 4.0, 1.0, ?) AS s FROM fts WHERE fts MATCH ? '
                                'ORDER BY s LIMIT ?', (w_idents, m, n)).fetchall()
            b = []
            if self.has_dfts:
                b = self.db.execute('SELECT rowid, bm25(dfts, 6.0, 3.0, 4.0, 1.0) AS s FROM dfts WHERE dfts MATCH ? '
                                    'ORDER BY s LIMIT ?', (m, n)).fetchall()
        except sqlite3.OperationalError:
            return [], []
        return a, b

    def bm25(self, q, n=100, w_idents=W_IDENTS) -> list:
        """Chunk ids from the notes words lane."""
        a, _ = self._scored(q, n, w_idents)
        return [r[0] for r in a]

    def merged_words(self, q, alpha=DOC_ALPHA, n=100, w_idents=W_IDENTS) -> list:
        """One list by merit: a document page that fits better than a note stands above it, a reference list counts little."""
        a, b = self._scored(q, n, w_idents)
        if not b:
            return [r[0] for r in a]
        marks = ','.join('?' * len(b))
        refs = {r[0] for r in self.db.execute('SELECT id FROM chunks WHERE refs = 1 AND id IN (%s)' % marks,
                                              [r[0] for r in b])}
        k = self._doc_scale(q, a, b)
        rows = [(s, c) for c, s in a] + [(s * k * alpha * (REFS_ALPHA if c in refs else 1.0), c) for c, s in b]
        rows.sort(key=lambda x: x[0])
        return [c for _, c in rows]

    def _doc_scale(self, q, a, b) -> float:
        """bm25 depends on the size of its table, so a handful of document pages scores near zero next to
        many notes. With few pages the best page is put on the scale of the best note, times the share of
        the query words it holds. With many pages the raw values already compare."""
        if not a or not b:
            return 1.0
        if self._dn is None:
            try:
                self._dn = self.db.execute('SELECT count(*) FROM dfts').fetchone()[0]
            except sqlite3.Error:
                self._dn = 0
        if self._dn >= DOC_SMALL or not b[0][1]:
            return 1.0
        row = self.db.execute('SELECT d.title, c.heading, c.text FROM chunks c JOIN docs d ON d.id = c.doc '
                              'WHERE c.id = ?', (b[0][0],)).fetchone()
        terms = text.terms_of(q)
        if not row or not terms:
            return 1.0
        blob = ' '.join(x or '' for x in row).lower()
        cov = sum(1 for t in terms if t.lower()[:5] in blob) / len(terms)
        return abs(a[0][1]) * cov / abs(b[0][1])

    def title_hits(self, q, n=4) -> list:
        """The question is a stretch of a document title. Three words at least, or it says nothing."""
        w = re.findall(r'[A-Za-z0-9]+', q)
        if len(w) < 3 or not self.has_dfts:
            return []
        try:
            docs = self.db.execute('SELECT DISTINCT c.doc FROM dfts f JOIN chunks c ON c.id = f.rowid '
                                   'WHERE dfts MATCH ? LIMIT 200', ('{title} : "' + ' '.join(w) + '"',)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [c for c in (self.best_chunk(d[0], q) for d in docs[:n]) if c]

    def phrases(self, q, n=40) -> list:
        """Exact names survive only as a phrase: ADR-004 must not turn into adr or 004."""
        out = []
        for nm in dict.fromkeys(text.NAME.findall(q)):
            toks = re.findall(r'[A-Za-z0-9]+', nm)
            if not toks:
                continue
            ph = '"' + ' '.join(toks) + '"'
            try:
                out += [r[0] for r in self.db.execute(
                    'SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY bm25(fts, 6.0, 3.0, 8.0, 1.0, 0.5) LIMIT ?',
                    (ph, n))]
                if self.has_dfts:
                    out += [r[0] for r in self.db.execute(
                        'SELECT rowid FROM dfts WHERE dfts MATCH ? ORDER BY bm25(dfts, 6.0, 3.0, 8.0, 1.0) LIMIT ?',
                        (ph, n // 2))]
            except sqlite3.OperationalError:
                pass
        return list(dict.fromkeys(out))

    def doc_bm25(self, q, n=60) -> list:
        """Doc ids from the whole-note lane (title, summary, path, headings)."""
        m = self._match(q)
        if not self.has_docfts or not m:
            return []
        try:
            return [r[0] for r in self.db.execute(
                'SELECT rowid FROM docfts WHERE docfts MATCH ? ORDER BY bm25(docfts, 8.0, 4.0, 5.0, 2.0) LIMIT ?',
                (m, n))]
        except sqlite3.OperationalError:
            return []

    def best_chunk(self, doc, q):
        """The chunk of one doc that fits the query best, else its first chunk."""
        m = self._match(q)
        a = self.db.execute('SELECT MIN(id), MAX(id) FROM chunks WHERE doc = ?', (doc,)).fetchone()
        if not a or a[0] is None:
            return None
        if m:
            try:
                r = self.db.execute('SELECT rowid FROM fts WHERE fts MATCH ? AND rowid BETWEEN ? AND ? '
                                    'ORDER BY bm25(fts, 1.0, 3.0, 1.0, 4.0, 0.5) LIMIT 1', (m, a[0], a[1])).fetchone()
                if r:
                    return r[0]
                if self.has_dfts:
                    r = self.db.execute('SELECT rowid FROM dfts WHERE dfts MATCH ? AND rowid BETWEEN ? AND ? '
                                        'ORDER BY bm25(dfts, 1.0, 3.0, 1.0, 4.0) LIMIT 1', (m, a[0], a[1])).fetchone()
                    if r:
                        return r[0]
            except sqlite3.OperationalError:
                pass
        return a[0]

    def _symbols(self, q) -> list:
        lc = _code()
        if lc is None:
            return []
        try:
            return lc.symbol_lane(self.db, q)
        except sqlite3.Error:
            return []

    # meaning lane

    def qvec(self, q):
        """(query vector, None) or (None, fixed reason)."""
        if self.vec is None:
            return None, self.vec_why
        c = self._get_client()
        if c is None:
            return None, W_NO_MODEL
        try:
            v = c.embed([q[:MAX_TEXT]], 'q', model=self.embedder.key, budget=self.cfg.budgets.get('embed'))
        except (OSError, ValueError, TypeError):
            v = None
        if v is None or len(v) != 1:
            return None, _fixed(getattr(c, 'why', None))
        return v[0], None

    def dense(self, qv, n=100):
        """Chunk ids by cosine against the vector file, and the best cosine."""
        if qv is None or self.vec is None or not len(self.vec) or np is None:
            return [], 0.0
        qv = np.asarray(qv, dtype='float32')
        if qv.ndim != 1 or self.vec.shape[1] != qv.shape[0]:
            return [], 0.0
        sims = np.asarray(self.vec) @ qv
        m = min(n * 2, len(sims) - 1)
        top = np.argpartition(-sims, m)[:n * 2]
        top = top[np.argsort(-sims[top])]
        # a passage with no vector yet is all zeros: it stays reachable by words and has no say here
        top = top[sims[top] != 0]
        vm = self._vmap()
        out = []
        best = float(sims[top[0]]) if len(top) else 0.0
        for v in top:
            out += vm.get(int(v), [])[:3]
        return out[:n], best

    # search

    def _empty(self, q, t0, qv, mwhy, ran, best=0.0, doff=None):
        return {'query': q, 'profile': self.profile, 'results': [], 'ms': int((time.time() - t0) * 1000),
                'confidence': 'low', 'ce_top': None, 'cos_top': best,
                'lanes': self.lanes(qv is not None, mwhy, False, None), 'word_lanes': ran, 'docs_off': doff,
                'note': NO_MATCH}

    def search(self, q, k=5, root=None, status=None, mode='hybrid', priors='intent', per_doc=2, rerank=True,
               candidates=40, w_doc=0.5, w_phrase=2.0, w_bm25=1.0, words_only=False, strict=False, docs_lane=True,
               sym_lane=True, doc_alpha=DOC_ALPHA, w_idents=W_IDENTS, w_sym=W_SYMBOLS, lift_newer=True) -> dict:
        """Ranked passages for a query.

        mode: bm25 (notes words only, the plain baseline), dense, hybrid (default), full (adds phrases for exact
        names and notes as a whole). priors: False, 'always' or 'intent'. strict raises RuntimeError when a lane
        that was asked for is missing, so a benchmark never mislabels another system's numbers.
        """
        t0 = time.time()
        q = (q or '')[:MAX_Q]
        full = mode == 'full'
        words = mode in ('hybrid', 'bm25', 'full')
        forced = None if words_only else self.words_why
        words_only = words_only or bool(forced)
        # an empty documents lane is off, and the lanes line says why
        doff = self.docs_off() if docs_lane and mode in ('hybrid', 'full') else None
        use_docs = bool(docs_lane and self.has_dfts and not doff and mode in ('hybrid', 'full', 'dense'))
        if use_docs and mode != 'dense':
            L = self.merged_words(q, doc_alpha, w_idents=w_idents)
        else:
            L = self.bm25(q, w_idents=w_idents) if words else []
        Ti = self.title_hits(q) if use_docs and mode != 'dense' else []
        lc = _code()
        use_sym = bool(sym_lane and self.has_syms and mode in ('hybrid', 'full'))
        Sy = self._symbols(q) if use_sym else []
        ran = (['notes'] if words else []) + (['documents'] if use_docs and mode != 'dense' else [])
        if use_sym and lc is not None and lc.looks_like_code(q):
            ran.append('symbols')
        qv, mwhy = None, forced or W_NOT_ASKED
        if forced and strict and mode in ('hybrid', 'dense', 'full'):
            raise RuntimeError('meaning lane unavailable: ' + str(forced))
        if mode in ('hybrid', 'dense', 'full') and not words_only:
            qv, mwhy = self.qvec(q)
            if qv is None and strict:
                raise RuntimeError('meaning lane unavailable: ' + str(mwhy))
        D, best = self.dense(qv) if qv is not None else ([], 0.0)
        P = self.phrases(q) if full else []
        DB = self.doc_bm25(q) if full else []
        score: dict = {}

        def fuse(ids, w):
            for r, c in enumerate(ids):
                score[c] = score.get(c, 0.0) + w / (RRF + r)

        fuse(L, w_bm25)
        fuse(Ti, W_TITLE)
        fuse(Sy, w_sym)
        fuse(D, 1.0)
        fuse(P, w_phrase)
        dscore: dict = {}
        for r, d in enumerate(DB):
            dscore[d] = dscore.get(d, 0.0) + w_doc / (RRF + r)
        if not score and not dscore:
            return self._empty(q, t0, qv, mwhy, ran, best, doff)
        # whole-note hit but none of its passages ranked yet: pull its best passage in
        ids = sorted(score, key=lambda c: -score[c])[:220]
        have = set()
        if ids:
            marks = ','.join('?' * len(ids))
            have = {r[0] for r in self.db.execute('SELECT DISTINCT doc FROM chunks WHERE id IN (%s)' % marks, ids)}
        for d in (sorted(dscore, key=lambda x: -dscore[x])[:40] if w_doc else []):
            if d not in have:
                c = self.best_chunk(d, q)
                if c:
                    score.setdefault(c, 0.0)
                    ids.append(c)
        if not ids:
            return self._empty(q, t0, qv, mwhy, ran, best)
        marks = ','.join('?' * len(ids))
        rows = [dict(zip(_HIT_KEYS, r, strict=True)) for r in self.db.execute(
            'SELECT %s FROM chunks c JOIN docs d ON d.id = c.doc WHERE c.id IN (%s)' % (','.join(_HIT_COLS), marks),
            ids)]
        if root:
            rows = [r for r in rows if r['root'] == root]
        if status:
            rows = [r for r in rows if r['status'] == status]
        when = text.dates_in(q)
        wants_current = bool(text.CURRENT.search(q)) and not when
        win = None
        if priors == 'intent' and wants_current:
            # counted over the notes: a list full of document pages must not push the right note out of the window
            win = {r['id'] for r in sorted((r for r in rows if r['kind'] not in ('document', 'image')),
                                           key=lambda r: -score.get(r['id'], 0.0))[:PRIOR_WINDOW]}
        scored = []
        for r in rows:
            s = score.get(r['id'], 0.0) + dscore.get(r['doc'], 0.0)
            if priors == 'always':
                e = 2 if wants_current else 1
                s *= PRIOR.get(r['status'], 1.0) ** e * KIND.get(r['kind'], 1.0) ** e
            elif priors == 'intent' and wants_current:
                pr = PRIOR.get(r['status'], 1.0) ** 2 * KIND.get(r['kind'], 1.0) ** 2
                if pr <= 1.0 or win is None or r['id'] in win:
                    s *= pr
            if when and r['date']:
                m = re.match(r'\d{4}-(\d\d)-(\d\d)', r['date'])
                if m:
                    mm, dd = int(m.group(1)), int(m.group(2))
                    if any(mm == a and (b is None or dd == b) for a, b in when):
                        s *= 1.6 if any(b is not None and mm == a and dd == b for a, b in when) else 1.25
            scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        ce_top, ces, rwhy = None, None, None
        if rerank and words_only:
            rwhy = forced or W_NOT_ASKED
        elif rerank:
            # two passages per note max for the reranker, a long note must not hog its window
            seen: dict = {}
            cand = []
            for s, r in scored:
                if seen.get(r['doc'], 0) >= 2:
                    continue
                seen[r['doc']] = seen.get(r['doc'], 0) + 1
                cand.append((s, r))
                if len(cand) >= candidates:
                    break
            if self.reranker is None:
                rwhy = self.rer_why
            elif cand:
                texts = [(r['title'] or '')[:120] + ' | ' + (r['heading'] or '')[:80] + '\n' + r['text'][:900]
                         for s, r in cand]
                c = self._get_client()
                if c is None:
                    rwhy = W_NO_MODEL
                else:
                    try:
                        budget = 300.0 if strict else c.rerank_budget()
                        ces = c.rerank(q[:MAX_Q], texts, model=self.reranker.key, budget=budget)
                    except (OSError, ValueError, TypeError):
                        ces = None
                    if ces is None or len(ces) != len(cand):
                        ces = None
                        rwhy = _fixed(getattr(c, 'why', None))
            if strict and ces is None and rwhy:
                raise RuntimeError('reranker unavailable: ' + str(rwhy))
        if ces is not None:
            order = sorted(range(len(cand)), key=lambda i: -ces[i])
            ce_top = float(ces[order[0]]) if order else None
            # the first stage rank stays in the mix: it carries the note level and status evidence the reranker cannot see
            base = {cand[i][1]['id']: i for i in range(len(cand))}
            ce_rank = {cand[i][1]['id']: pos for pos, i in enumerate(order)}
            mix = {r['id']: 0.35 / (10 + base[r['id']]) + 1.0 / (10 + ce_rank[r['id']]) for s, r in cand}
            mixed = sorted(cand, key=lambda x: -mix[x[1]['id']])
            # later steps sort by score, so the mixed order has to live in the score itself
            rest = [x for x in scored if x[1]['id'] not in base]
            floor = 0.99 * min(mix.values())
            top = max((s for s, r in rest), default=0.0)
            scored = [(mix[r['id']], r) for s, r in mixed] + [(floor * s / top if top > 0 else 0.0, r) for s, r in rest]
        nw = self._newer()
        if wants_current and nw:
            # question asks what holds now: notes with a newer version drop down
            scored = sorted(((s * (0.6 if r['doc'] in nw else 1.0), r) for s, r in scored), key=lambda x: -x[0])
        if lift_newer and nw:
            # a hit whose newer version is also in the list: the newer one goes right above it
            lift: dict = {}
            for s, r in scored:
                n = nw.get(r['doc'])
                if n is not None and s * 1.001 > lift.get(n[2], 0.0):
                    lift[n[2]] = s * 1.001
            scored = sorted(((max(s, lift.get(r['doc'], 0.0)), r) for s, r in scored), key=lambda x: -x[0])
        seen = {}
        out = []
        for s, r in scored:
            if seen.get(r['doc'], 0) >= per_doc:
                continue
            seen[r['doc']] = seen.get(r['doc'], 0) + 1
            try:
                cop = json.loads(r['copies'] or '[]')
            except ValueError:
                cop = []
            newer = nw[r['doc']][0] if r['doc'] in nw else None
            out.append({
                'source': source_class.of(r['kind'], r['status'], r['status_src'], r['doc'] in nw),
                'newer': newer,
                'path': r['path'],
                'unit': text.unit_of(r['kind'], r['unit']),
                'refs': bool(r['refs']),
                'lines': '%d-%d' % (r['start_line'], r['end_line']),
                'title': (r['title'] or '')[:90],
                'heading': (r['heading'] or '')[:80],
                'status': r['status'],
                'status_from': r['status_src'],
                'kind': r['kind'],
                'date': r['date'],
                'sha': (r['sha256'] or '')[:12],
                'score': round(s * 100, 3),
                'snippet': text.snippet(r['text'], q),
                'what': r['summary'],
                'warning': r['warning'],
                'copies': len(cop),
                '_copies': cop[:8],
                '_text': r['text'],
            })
            if len(out) >= max(1, int(k)):
                break
        low = conf(best, L) == 'low'
        note = NO_MATCH if not out else (
            'These are the closest passages, not a confirmed answer. Read the cited lines before relying on them.'
            + (' Nothing here looks like a direct answer: treat this as not found unless the cited lines say otherwise.'
               if low else ''))
        return {'query': q, 'profile': self.profile, 'results': out, 'ms': int((time.time() - t0) * 1000),
                'confidence': 'low' if low else 'normal', 'ce_top': ce_top, 'cos_top': best,
                'lanes': self.lanes(qv is not None, mwhy, ces is not None, rwhy if rerank else None),
                'word_lanes': ran, 'docs_off': doff, 'note': note}

    def lanes(self, meant, mwhy, ranked, rwhy) -> str:
        """One plain line that says what really ran, so a thin answer is not read as all the system knows."""
        cov = ''
        if meant and self.coverage() < 0.995:
            cov = ' (vectors for %d%% of passages)' % int(self.coverage() * 100)
        if meant and ranked:
            return 'lanes: words, meaning, reranker' + cov
        if meant:
            return ('words and meaning, no reranker: ' + str(rwhy) + cov) if rwhy else 'lanes: words, meaning' + cov
        if ranked:
            return 'words and reranker: no meaning lane, ' + str(mwhy)
        return 'words only: ' + str(mwhy)

    # output

    def render(self, r: dict, full: int = 1) -> str:
        """The answer an agent reads: short, one citation per passage. The lanes line is always last."""
        out = []
        for i, x in enumerate(r.get('results', [])):
            head = '%d. %s | %s | %s' % (i + 1, text.cite(x), x['source'], x['status'])
            if x.get('refs'):
                head += ' | reference list'
            if x.get('date'):
                head += ' | ' + x['date']
            if x.get('copies'):
                head += ' | %d copies' % x['copies']
            out.append(head)
            if self.changed(x.get('path')):
                out.append('   ' + CHANGED)
            if x.get('newer'):
                out.append('   NEWER VERSION: ' + x['newer'])
            if x.get('warning') and self._show_warning(x['warning']):
                out.append('   caution: ' + str(x['warning'])[:160])
            out.append('   ' + text.best_window(x.get('_text') or x['snippet'], r.get('query', ''),
                                                700 if i < full else 260))
        if not out:
            out.append(r.get('note', NO_MATCH))
        if r.get('confidence') == 'low':
            out.append('Nothing here looks like a direct answer. Rephrase once, then say not found unless the '
                       'cited lines show it.')
        if r.get('lanes'):
            out.append(lane_line(r))
        return '\n'.join(out)

    def public(self, r: dict, full: int = 2) -> dict:
        """What an agent receives as data. The best passages come whole, the rest stay short. Works on a copy."""
        r = copy.deepcopy(r)
        for i, x in enumerate(r.get('results', [])):
            t = x.pop('_text', None)
            x.pop('_copies', None)
            if not x.get('unit') or x['unit'] == 'line':
                x.pop('unit', None)
            if not x.get('refs'):
                x.pop('refs', None)
            if i < full and t:
                x['snippet'] = re.sub(r'[ \t]+', ' ', t)[:1300]
            else:
                x['snippet'] = x['snippet'][:300]
            for f in ('warning', 'what', 'heading', 'copies'):
                if not x.get(f):
                    x.pop(f, None)
            for f in ('status_from', 'sha', 'score', 'title', 'kind'):
                x.pop(f, None)
        r.pop('ce_top', None)
        r.pop('cos_top', None)
        r.pop('query', None)
        return r

    def changed(self, display_path) -> bool:
        """One stat and one row per cited file, no content read. True when the file is not what was indexed."""
        if not display_path:
            return False
        row = self.db.execute('SELECT mtime, size FROM docs WHERE path = ?', (display_path,)).fetchone()
        if not row:
            return False
        try:
            ap = self.cfg.abs_path(display_path)
            st = os.stat(ap) if ap else None
        except (OSError, KeyError, ValueError, TypeError):
            return False
        if st is None:
            return False
        return st.st_size != row[1] or bool(row[0]) and abs(st.st_mtime - row[0]) >= 1e-3

    # read

    def _doc_row(self, path):
        cols = 'path, status, date, id, kind, ext, unit, units, root, warning'
        p = path.strip() if isinstance(path, str) else ''
        # an absolute path in either form, C:\... on windows included
        if p.startswith('/') or os.path.isabs(p):
            try:
                p = self.cfg.display_path(p) or ''
            except (KeyError, ValueError, TypeError):
                p = ''
        if p.startswith('./'):
            p = p[2:]
        if not p:
            return None
        r = self.db.execute('SELECT %s FROM docs WHERE path = ?' % cols, (p,)).fetchone()
        if r:
            return r
        # a copy: same bytes as an indexed doc, so the winner's text answers
        for (w, cp) in self.db.execute('SELECT path, copies FROM docs WHERE copies LIKE ?',
                                       ('%' + json.dumps(p)[1:-1] + '%',)):
            try:
                if p in json.loads(cp or '[]'):
                    return self.db.execute('SELECT %s FROM docs WHERE path = ?' % cols, (w,)).fetchone()
            except ValueError:
                continue
        return None

    def _inside(self, abs_path, root_name) -> bool:
        """The real file lies under its root, unless links are followed on purpose."""
        try:
            root = self.cfg.root(root_name)
        except KeyError:
            return False
        if root.follow_links:
            return True
        rp, base = os.path.realpath(abs_path), os.path.realpath(root.path)
        return rp == base or rp.startswith(base + os.sep)

    def read(self, path: str, start: int = 1, end: int | None = None, maxlines: int = 120) -> dict:
        """Lines of a note or code file, or pages, paragraphs or image text of a document, as the index holds them."""
        r = self._doc_row(path)
        if not r:
            return {'error': NOT_FOUND}
        try:
            start = max(1, int(start or 1))
            end = int(end) if end else None
        except (TypeError, ValueError):
            return {'error': 'start and end must be numbers'}
        p, st, dt, did, kind, ext, unit, units, root, warn = r
        if kind in ('document', 'image'):
            out = self._read_units(r, start, end, unit or 'page')
            if self.changed(p):
                out['warning'] = CHANGED
            if warn and self._show_warning(warn):
                out['caution'] = str(warn)[:160]
            return out
        end = min(end or start + maxlines - 1, start + maxlines - 1)
        end = max(end, start)
        try:
            ap = self.cfg.abs_path(p)
            if not ap or not self._inside(ap, root):
                raise OSError('outside')
            with open(ap, 'rb') as f:
                body = f.read().decode('utf-8', errors='replace')
        except (OSError, KeyError, ValueError, TypeError):
            out = self._read_units(r, start, end, 'line')
            out['note'] = 'the file is not reachable now: this is the indexed text'
            return out
        lines = body.split('\n')
        if lines and lines[-1] == '':
            lines.pop()
        out = {'path': p, 'status': st, 'date': dt, 'lines_total': len(lines), 'from': start,
               'to': min(end, len(lines)),
               'text': '\n'.join('%d\t%s' % (i + start, ln.rstrip('\r')[:1500])
                                 for i, ln in enumerate(lines[start - 1:end]))}
        if self.changed(p):
            out['warning'] = CHANGED
        return out

    def _read_units(self, r, start, end, unit) -> dict:
        """Pages, paragraphs or lines as the index cut them. A citation's numbers work here unchanged."""
        p, st, dt, did, kind, ext, _u, units, root, warn = r
        wide = MAX_READ_PAGES if unit == 'page' else 120
        end = min(end or start + wide - 1, start + wide - 1)
        end = max(end, start)
        out, size, last = [], 0, start - 1
        for a, b, t in self.db.execute('SELECT start_line, end_line, text FROM chunks '
                                       'WHERE doc = ? AND end_line >= ? AND start_line <= ? ORDER BY ord',
                                       (did, start, end)):
            if size + len(t) > MAX_READ_CHARS and out:
                break
            out.append('[%s %d] %s' % (unit, a, t))
            size += len(t)
            last = max(last, b)
        return {'path': p, 'status': st, 'date': dt, 'unit': unit, 'units_total': units, 'from': start, 'to': last,
                'text': '\n'.join(out) if out else 'no extracted text in this range'}

    # neighbours

    def neighbours(self, path, n=10, was=None) -> dict:
        """The notes to bring in line after an edit of one note or code file, the strongest reason first.

        In order: notes that still hold numbers or sentences this one no longer has, the notes in its
        supersession chain (newer versions, then older ones), notes that link to it, files it links to, then
        the files closest in meaning (in words when there are no vectors). The old text is `was` when given,
        else the indexed text when the file changed since the build. Reads files, writes nothing.
        """
        r = self._doc_row(path)
        if not r:
            return {'error': NOT_FOUND}
        p, st, dt, did, kind, ext, _u, _units, root, _w = r
        if kind in ('document', 'image'):
            return {'error': 'neighbours works on notes and code files, this is a %s' % kind}
        if was is not None and not isinstance(was, str):
            return {'error': 'was must be the old text of the note'}
        try:
            n = max(1, min(int(n), NB_MAX))
        except (TypeError, ValueError):
            n = 10
        rel, size, sha = self.db.execute('SELECT rel, size, sha256 FROM docs WHERE id = ?', (did,)).fetchone()
        own = dict(zip(_NB_COLS, (did, p, root, rel, kind, st, dt), strict=True))
        cache = {}
        try:
            raw = self._nb_file(p, root)
        except (OSError, ValueError):
            return {'error': 'the file cannot be read now'}
        if raw is None:
            back = [(row, line, 'links to this note') for row, line in self._nb_backlinks(own, cache)]
            return self._nb_result(p, True, [], [self._nb_chain(did), back], n, [NB_GONE])
        if len(raw) > NB_MAX_BYTES:
            changed = self.changed(p)
            raw = raw[:NB_MAX_BYTES]
        else:
            changed = len(raw) != size or hashlib.sha256(raw).hexdigest() != sha
        now = _nb_text(raw, ext)
        if was is not None:
            before, notes = was[:MAX_WAS].replace('\r\n', '\n'), ['Removed text: compared with the text given as was.']
        elif changed:
            before, notes = self._nb_indexed(did), ['Removed text: compared with the indexed text.']
        else:
            before, notes = None, ['The file is as indexed, so there is no removed text to follow: pass was with '
                                   'the old text to compare against it.']
        facts = _nb_removed(before, now, kind == 'code') if before is not None else []
        groups = [
            [(row, line, _nb_why(held)) for row, held, line in self._nb_holders(facts, did, cache)],
            self._nb_chain(did),
            [(row, line, 'links to this note') for row, line in self._nb_backlinks(own, cache)],
            [(row, None, 'this note links to it on line %d' % line) for row, line in self._nb_outlinks(own, now)],
        ]
        close = self._nb_meaning(did)
        if close is None:
            groups.append([(row, line, 'close in words') for row, line in self._nb_words(did)])
            notes.append('Close notes by words: this note has no vectors.')
        else:
            groups.append([(row, line, 'close in meaning (%.2f)' % s) for row, line, s in close])
        return self._nb_result(p, changed, facts, groups, n, notes)

    def render_neighbours(self, r: dict) -> str:
        """The neighbours answer an agent reads: one line per note with path, line, status, date and why."""
        if r.get('error'):
            return r['error']
        out = ['neighbours of %s%s' % (r['path'], ', changed since indexed' if r.get('changed') else '')]
        if r.get('removed'):
            out.append('removed here: ' + '; '.join(r['removed']))
        for i, e in enumerate(r.get('neighbours') or []):
            where = e['path'] + (':%d' % e['line'] if e.get('line') else '')
            cols = [where, e.get('status') or 'unknown'] + ([e['date']] if e.get('date') else []) + [e['why']]
            out.append('%d. %s' % (i + 1, ' | '.join(cols)))
        if not r.get('neighbours'):
            out.append('no neighbours found')
        if r.get('note'):
            out.append(r['note'])
        return '\n'.join(out)

    def _nb_result(self, p, changed, facts, groups, n, notes) -> dict:
        """One list from the groups in their order: a note met again only adds its reason."""
        out, at = [], {}
        for g in groups:
            for row, line, why in g:
                e = at.get(row['path'])
                if e is None:
                    e = at[row['path']] = {'path': row['path'], 'why': why, 'line': line,
                                           'status': row['status'], 'date': row['date']}
                    out.append(e)
                    continue
                if e['line'] is None:
                    e['line'] = line
                elif line and line != e['line']:
                    why += ' (line %d)' % line
                e['why'] += '; ' + why
        out = out[:n]
        if any(_NB_RANK.get(e['status'], 0) for e in out):
            notes.append(NB_RECORDS)
        return {'path': p, 'changed': changed, 'removed': [f['show'] for f in facts], 'neighbours': out,
                'note': ' '.join(notes)}

    def _nb_file(self, p, root):
        """The file's bytes now, None when it is gone. OSError when it cannot be read or lies outside its root."""
        ap = self.cfg.abs_path(p)
        if not ap:
            raise OSError('no path')
        if not os.path.lexists(ap):
            return None
        if not self._inside(ap, root):
            raise OSError('outside its root')
        with open(ap, 'rb') as f:
            return f.read(NB_MAX_BYTES + 1)

    def _nb_read(self, p, root, cache):
        """A candidate's text as it is on disk now, None when it cannot be read. Each file is read once."""
        if p not in cache:
            try:
                raw = self._nb_file(p, root)
                cache[p] = None if raw is None else _nb_text(raw[:NB_MAX_BYTES], os.path.splitext(p)[1].lower())
            except (OSError, ValueError):
                cache[p] = None
        return cache[p]

    def _nb_indexed(self, did) -> str:
        """The note's text as the index holds it, the pieces of a long line joined back."""
        out, last = [], None
        for a, b, t in self.db.execute('SELECT start_line, end_line, text FROM chunks WHERE doc = ? ORDER BY ord',
                                       (did,)):
            if out and a == b == last:
                out[-1] = _nb_join(out[-1], t)
            else:
                out.append(t)
            last = a if a == b else None
        return '\n'.join(out)

    def _nb_rows(self, ids) -> list:
        """Doc rows as dicts, in the order of ids."""
        rows = {}
        for i in range(0, len(ids), 500):
            part = list(ids[i:i + 500])
            for r in self.db.execute('SELECT %s FROM docs WHERE id IN (%s)' % (', '.join(_NB_COLS),
                                                                               ','.join('?' * len(part))), part):
                rows[r[0]] = dict(zip(_NB_COLS, r, strict=True))
        return [rows[i] for i in ids if i in rows]

    def _nb_fts(self, q, did, limit) -> list:
        """Ids of notes (no code, no documents) with a passage that matches q, this one left out."""
        try:
            return [d for (d,) in self.db.execute(
                'SELECT DISTINCT c.doc FROM fts JOIN chunks c ON c.id = fts.rowid JOIN docs d ON d.id = c.doc '
                'WHERE fts MATCH ? AND c.doc != ? AND d.kind IN (%s) LIMIT ?' % ','.join('?' * len(NOTE_KINDS)),
                (q, did) + NOTE_KINDS + (limit,))]
        except sqlite3.OperationalError:
            return []

    def _nb_holders(self, facts, did, cache) -> list:
        """(row, facts it holds, first line) for notes that still hold removed facts, current notes and the
        most facts first. The index finds the candidates, their text on disk confirms them."""
        if not facts:
            return []
        hits = {}
        for i, f in enumerate(facts):
            for d in self._nb_fts(f['query'], did, NB_ROWS):
                hits.setdefault(d, set()).add(i)
        out = []
        for row in self._nb_rows(sorted(hits, key=lambda d: (-len(hits[d]), d))[:NB_DOCS]):
            text = self._nb_read(row['path'], row['root'], cache)
            if text is None:
                continue
            held, line = _nb_holds(facts, text)
            if held:
                out.append((row, held, line))
        out.sort(key=lambda x: (_NB_RANK.get(x[0]['status'], 0), -len(x[1]), x[0]['path']))
        return out

    def _nb_chain(self, did) -> list:
        """(row, None, why) for the notes in this one's supersession chain, as the build linked them
        (Newer versions): the newer versions nearest first, then the older ones nearest first, at most
        NB_CHAIN steps each way."""
        nw = self._newer()
        if not nw:
            return []
        back: dict = {}
        for a, (_p, _how, t) in nw.items():
            back.setdefault(t, []).append(a)
        found, seen, cur = [], {did}, did
        for step in range(1, NB_CHAIN + 1):
            cur = nw[cur][2] if cur in nw else None
            if cur is None or cur in seen:
                break
            seen.add(cur)
            found.append((cur, step, True))
        frontier = [did]
        for step in range(1, NB_CHAIN + 1):
            nxt = []
            for x in frontier:
                for o in sorted(back.get(x, ())):
                    if o not in seen:
                        seen.add(o)
                        found.append((o, step, False))
                        nxt.append(o)
            if not nxt:
                break
            frontier = nxt
        rows = {r['id']: r for r in self._nb_rows([i for i, _, _ in found])}
        out = []
        for i, step, newer in found[:NB_MAX]:
            if i not in rows:
                continue
            if step == 1:
                why = 'newer version, it replaces this note' if newer else 'older version, this note replaces it'
            else:
                why = ('newer version, %d steps on in the supersession chain' if newer
                       else 'older version, %d steps back in the supersession chain') % step
            out.append((rows[i], None, why))
        return out

    def _nb_backlinks(self, own, cache) -> list:
        """(row, line) for notes with a link that names this file, by the rules leks check resolves links with."""
        from . import check
        toks = re.findall(r'[^\W_]+', posixpath.splitext(posixpath.basename(own['rel']))[0])
        if not toks:
            return []
        tails = check._tails(own['rel'])
        out = []
        for row in self._nb_rows(self._nb_fts('{text} : "%s"' % ' '.join(toks), own['id'], NB_LINK_ROWS)):
            text = self._nb_read(row['path'], row['root'], cache)
            if text is None:
                continue
            rel_dir = posixpath.dirname(check._norm(row['rel']))
            for pos, t, _wiki in check._links(text):
                keys = check._keys(t, rel_dir)
                if keys and keys & tails:
                    out.append((row, check._line(text, pos)))
                    break
        out.sort(key=lambda x: (_NB_RANK.get(x[0]['status'], 0), x[0]['path']))
        return out

    def _nb_names(self) -> dict:
        """Last part of a path, with and without its extension, to the rows of notes and code. Once per engine."""
        if self._nb_names_cache is None:
            from . import check
            m: dict = {}
            for r in self.db.execute("SELECT %s FROM docs WHERE kind NOT IN ('document', 'image')"
                                     % ', '.join(_NB_COLS)):
                row = dict(zip(_NB_COLS, r, strict=True))
                base = check._norm(row['rel']).rsplit('/', 1)[-1]
                for k in {base, posixpath.splitext(base)[0]}:
                    m.setdefault(k, []).append(row)
            self._nb_names_cache = m
        return self._nb_names_cache

    def _nb_outlinks(self, own, text) -> list:
        """(row, line of the link here) for the notes and code files this text links to, in link order.
        A name that fits several files goes to one: same root first, then the shortest path."""
        from . import check
        names = self._nb_names()
        rel_dir = posixpath.dirname(check._norm(own['rel']))
        out, seen = [], {own['id']}
        for pos, t, _wiki in check._links(text):
            keys = check._keys(t, rel_dir)
            if not keys:
                continue
            hits = [row for k in keys for row in names.get(k.rsplit('/', 1)[-1], ())
                    if keys & check._tails(row['rel'])]
            if not hits:
                continue
            best = min(hits, key=lambda x: (x['root'] != own['root'], len(x['rel']), x['path']))
            if best['id'] not in seen:
                seen.add(best['id'])
                out.append((best, check._line(text, pos)))
        return out

    def _nb_meaning(self, did):
        """(row, line, cosine) for the notes and code files closest to the mean of this note's passage
        vectors. None when there are no vectors, or none for this note yet."""
        if self.vec is None or np is None:
            return None
        vec = np.asarray(self.vec)
        rows = [v for (v,) in self.db.execute('SELECT vec FROM chunks WHERE doc = ? AND vec >= 0', (did,))
                if v < len(vec)]
        mine = vec[rows] if rows else vec[:0]
        mine = mine[np.any(mine != 0, axis=1)]
        if not len(mine):
            return None
        q = mine.mean(axis=0)
        norm = float(np.linalg.norm(q))
        if not norm:
            return None
        sims = vec @ (q / norm)
        m = min(NB_ROWS, len(sims))
        top = np.argpartition(-sims, m - 1)[:m]
        top = top[np.argsort(-sims[top])]
        # a passage with no vector yet is all zeros and has no say
        top = top[sims[top] != 0]
        vm = self._vmap()
        order = [(c, float(sims[v])) for v in top for c in vm.get(int(v), [])]
        if not order:
            return []
        info = {}
        ids = [c for c, _ in order]
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for r in self.db.execute('SELECT c.id, c.start_line, %s FROM chunks c JOIN docs d ON d.id = c.doc '
                                     'WHERE c.id IN (%s)' % (', '.join('d.' + x for x in _NB_COLS),
                                                             ','.join('?' * len(part))), part):
                info[r[0]] = (r[1], dict(zip(_NB_COLS, r[2:], strict=True)))
        out, seen = [], {did}
        for c, s in order:
            line, row = info.get(c, (None, None))
            if row is None or row['id'] in seen or row['kind'] in ('document', 'image'):
                continue
            seen.add(row['id'])
            out.append((row, line, s))
            if len(out) >= NB_CLOSE:
                break
        return out

    def _nb_words(self, did) -> list:
        """(row, line) for the notes and code files closest in words: this note's title, summary and
        headings as the question."""
        if not self.has_docfts:
            return []
        r = self.db.execute('SELECT title, summary, headings FROM docfts WHERE rowid = ?', (did,)).fetchone()
        q = ' '.join(x or '' for x in (r or ()))[:600]
        if not q.strip():
            return []
        out = []
        for row in self._nb_rows([d for d in self.doc_bm25(q, n=60) if d != did]):
            if row['kind'] in ('document', 'image'):
                continue
            c = self.best_chunk(row['id'], q)
            line = self.db.execute('SELECT start_line FROM chunks WHERE id = ?', (c,)).fetchone() if c else None
            out.append((row, line[0] if line else None))
            if len(out) >= NB_CLOSE:
                break
        return out

    # small tools

    def locate(self, q: str, n: int = 15) -> dict:
        return lane_locate.locate(self.db, q, n=n)

    def tried(self, proposal: str) -> str:
        return lane_prior.tried(self, proposal)

    def state(self) -> dict:
        rows = self.db.execute("SELECT path, date FROM docs WHERE kind = 'state' AND status IS NOT 'historical-record' "
                               "ORDER BY date DESC, path LIMIT 12").fetchall()
        return {'state_notes': [{'path': p, 'date': d} for p, d in rows]}

    def _sandbox_state(self) -> str:
        mode = (getattr(self.cfg, 'daemon', None) or {}).get('sandbox', 'auto')
        if mode == 'off':
            return 'off'
        try:
            from . import sandbox
            ok, note = sandbox.available()
        except Exception:
            return 'unknown'
        return 'on' if ok else 'off (' + str(note) + ')'

    def _why_now(self):
        """(why no meaning lane, why no reranker) for a search started now, None where the lane can run.
        The first thing to fix comes first: packages, then weights, then vectors."""
        mwhy = self.vec_why
        if self.embedder is not None and mwhy not in (W_NO_MODELS, W_NO_NUMPY):
            mwhy = self._ready_why('embed', self.embedder.key) or mwhy
        rwhy = self.rer_why
        if self.reranker is not None:
            rwhy = self._ready_why('rerank', self.reranker.key)
        return mwhy, rwhy

    def _says(self, mwhy, rwhy, doff=None) -> str:
        """What search runs on, in one sentence, with the command that fixes a lane that is off."""
        if mwhy is None and (rwhy is None or self.reranker is None):
            s = 'search runs on words and meaning' + (' with the reranker' if rwhy is None else '')
        else:
            if mwhy is None:
                head, why = 'search runs on words and meaning, no reranker: ', rwhy
            elif rwhy is None:
                head, why = 'search runs on words and the reranker, no meaning lane: ', mwhy
            else:
                head, why = 'search runs on words only: ', mwhy
            fix = FIX.get(why)
            s = head + str(why) + ('. Fix: ' + fix if fix else '')
        # pdfs sit in the roots and nothing reads them: worth a fix line too
        if doff == D_NO_PDF:
            s += '; documents off: ' + doff + '. Fix: ' + FIX[doff]
        return s

    def status(self) -> dict:
        def one(sql):
            return self.db.execute(sql).fetchone()[0]

        built = self.db.execute("SELECT v FROM meta WHERE k = 'built'").fetchone() if self.meta else None
        try:
            built_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(float(built[0]))) if built else None
        except (TypeError, ValueError, OverflowError):
            built_utc = None
        try:
            from . import docs
            cap = docs.capabilities(self.cfg)
        except Exception:
            cap = {}
        try:
            from . import __version__
        except ImportError:
            __version__ = '3.1.0'
        has_names = lane_locate.has_names(self.db)
        mwhy, rwhy = self._why_now()
        doff = self.docs_off()
        wl = ['notes'] + ([] if doff else ['documents']) + (['symbols'] if self.has_syms else [])
        return {
            'version': __version__,
            'profile': self.profile,
            'search': self._says(mwhy, rwhy, doff),
            'lanes': lane_line({'lanes': self.lanes(mwhy is None, mwhy, rwhy is None, rwhy), 'word_lanes': wl,
                                'docs_off': doff}),
            'docs': one('SELECT COUNT(*) FROM docs'),
            'chunks': one('SELECT COUNT(*) FROM chunks'),
            'documents': one("SELECT COUNT(*) FROM docs WHERE kind = 'document'"),
            'images': one("SELECT COUNT(*) FROM docs WHERE kind = 'image'"),
            'symbols': one('SELECT COUNT(*) FROM symbols') if self.has_syms else 0,
            'names': one('SELECT COUNT(*) FROM names') if has_names else 0,
            'embedder': self.embedder.key if self.embedder else None,
            'reranker': self.reranker.key if self.reranker else None,
            'backend': getattr(self.cfg, 'backend', None),
            'vectors': 0 if self.vec is None else int(len(self.vec)),
            'vector_mode': self.vector_mode(),
            'coverage': round(self.coverage(), 6),
            'why_no_meaning': mwhy,
            'why_no_reranker': rwhy,
            'pdf_text': cap.get('pdf_text'),
            'ocr': cap.get('ocr'),
            'sandbox': self._sandbox_state(),
            'built_utc': built_utc,
            'by_root': dict(self.db.execute('SELECT root, COUNT(*) FROM docs GROUP BY root ORDER BY root').fetchall()),
        }


def lane_line(r: dict) -> str:
    """The lanes line plus which word lanes ran, and why the documents lane did not."""
    s = r['lanes'] + ('; word lanes: ' + ', '.join(r['word_lanes']) if r.get('word_lanes') else '')
    if r.get('docs_off'):
        s += '; documents off: ' + str(r['docs_off'])
    return s


def conf(best: float, word_hits: list) -> str:
    """low when there is no meaning score and fewer than three word hits."""
    return 'low' if not best and len(word_hits) < 3 else 'normal'
