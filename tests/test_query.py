"""Engine tests: lanes line, priors, newer versions, read, locate, status, meaning lane with a stub client.

The profile database is built by hand here (core ddl from schema, code and locate ddl from their lanes),
so these tests do not depend on the builder.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import sqlite3
import time
import unittest
from unittest import mock

from leks import lane_code, lane_locate, query, schema
from tests import helpers

try:
    import numpy as np
except ImportError:
    np = None

# per file metadata: kind, status, where the status came from, date
META = {
    'notes/STATE.md': ('state', 'current', 'entry', '2026-03-02'),
    'notes/decisions/DEC-001-eviction-policy.md': ('decision', 'superseded', 'front-matter', '2026-01-10'),
    'notes/decisions/DEC-002-eviction-policy.md': ('decision', 'current', 'front-matter', '2026-02-01'),
    'notes/archive/old-frame-notes.md': ('note', 'historical-record', 'path', '2025-11-20'),
    'notes/frame_graph.md': ('note', 'unknown', 'rule', '2026-02-15'),
    'notes/build_cache.md': ('note', 'unknown', 'rule', '2026-02-20'),
    'code/tile_cache.py': ('code', 'unknown', 'rule', '2026-02-10'),
    'code/build.sh': ('code', 'unknown', 'rule', '2026-02-11'),
    'docs/release_checklist.txt': ('note', 'unknown', 'rule', '2026-02-12'),
}
LANG = {'.py': 'python', '.sh': 'shell'}
VOCAB = 'mixer fader channel reverb delay gain bus preset knob meter solo mute pan send return insert'.split()


def _words(path: str) -> str:
    return re.sub(r'[/_\-]', ' ', path)


def _sections(lines, is_note):
    """(start, end, heading) 1-based line ranges. Notes split at heading lines, code stays whole."""
    if not is_note:
        return [(1, len(lines), '')]
    marks = [i for i, ln in enumerate(lines) if ln.startswith('#')]
    cuts = sorted(set([0] + marks))
    out = []
    for n, a in enumerate(cuts):
        b = (cuts[n + 1] - 1) if n + 1 < len(cuts) else len(lines) - 1
        while b > a and not lines[b].strip():
            b -= 1
        head = lines[a].lstrip('#').strip() if lines[a].startswith('#') else ''
        if any(x.strip() for x in lines[a:b + 1]):
            out.append((a + 1, b + 1, head))
    return out


def build_db(cfg, path, filler=0, with_names=True):
    """A profile database over the real corpus files plus a pdf and an image written as rows only."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript(schema.PROFILE_DDL)
    lane_code.add_schema(c)
    did = cid = 0
    master = sqlite3.connect(':memory:')
    master.executescript(schema.MASTER_DDL)
    vec = [0]

    def add_doc(dpath, title, kind, status, src, date, unit, units, text_sections, mtime=0.0, size=0, sha='',
                copies='[]', summary=None):
        nonlocal did, cid
        did += 1
        root, _, rel = dpath.partition('/')
        c.execute('INSERT INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (did, dpath, root, rel, os.path.basename(dpath), os.path.splitext(dpath)[1], title, summary, None,
                   kind, status, src, date, mtime, size, sha, units, unit, copies))
        heads = []
        for (a, b, head, body, refs) in text_sections:
            cid += 1
            c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)', (cid, did, cid, a, b, head, body, vec[0], refs))
            vec[0] += 1
            idents = lane_code.ident_column(body)
            if kind in ('document', 'image'):
                c.execute('INSERT INTO dfts(rowid,title,heading,path,text) VALUES(?,?,?,?,?)',
                          (cid, title, head, _words(dpath), body))
            else:
                c.execute('INSERT INTO fts(rowid,title,heading,path,text,idents) VALUES(?,?,?,?,?,?)',
                          (cid, title, head, _words(dpath), body, idents))
            if head:
                heads.append(head)
        c.execute('INSERT INTO docfts(rowid,title,summary,path,headings) VALUES(?,?,?,?,?)',
                  (did, title, summary or '', _words(dpath), ' | '.join(heads)))
        return did

    for dpath, (kind, status, src, date) in META.items():
        ap = cfg.abs_path(dpath)
        with open(ap, encoding='utf-8') as f:
            body = f.read()
        lines = body.split('\n')
        if lines and lines[-1] == '':
            lines.pop()
        title = next((ln.lstrip('# ').strip() for ln in lines if ln.startswith('# ')), os.path.basename(dpath))
        st = os.stat(ap)
        secs = [(a, b, h, '\n'.join(lines[a - 1:b]), 0) for a, b, h in _sections(lines, kind != 'code')]
        copies = json.dumps(['notes/misc/build_cache_copy.md']) if dpath == 'notes/build_cache.md' else '[]'
        d = add_doc(dpath, title, kind, status, src, date, 'line', len(lines), secs, st.st_mtime, st.st_size,
                    hashlib.sha256(body.encode()).hexdigest(), copies)
        root, _, rel = dpath.partition('/')
        master.execute('INSERT INTO files(root,rel,ext,kind,title,status,status_src,date,mtime,size,sha256) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (root, rel, os.path.splitext(dpath)[1], kind, title, status, src, date, st.st_mtime,
                        st.st_size, hashlib.sha256(body.encode()).hexdigest()))
        master.execute('INSERT INTO names VALUES(?,?,?,?,?)',
                       (root, rel, st.st_size, st.st_mtime, lane_locate.class_of(os.path.splitext(dpath)[1])))
        if kind == 'code':
            ext = os.path.splitext(dpath)[1]
            syms, _how = lane_code.extract(body, ext)
            for name, skind, line, end, parent, sig in syms:
                sid = c.execute('SELECT COALESCE(MAX(id),0)+1 FROM symbols').fetchone()[0]
                c.execute('INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (sid, d, name, name.lower(), skind, LANG.get(ext, ''), line, end, parent, sig))
                c.execute('INSERT INTO symfts(rowid,name,parts,parent) VALUES(?,?,?,?)',
                          (sid, name, ' '.join(lane_code.split_ident(name)),
                           ' '.join(lane_code.split_ident(parent or ''))))
    # a pdf with two pages (second one is the reference list) and an image with ocr text, rows only
    add_doc('docs/cache-study.pdf', 'Cache study', 'document', 'unknown', 'rule', '2026-01-20', 'page', 2,
            [(1, 1, '', 'Cache study. Abstract. We measure eviction policies for a tile cache on three sample '
                       'scenes. The oldest first policy kept the frame time stable.', 0),
             (2, 2, 'References', 'References. 1. Tessel group, Tile caches in practice. 2. Quorbit report, Frame pacing notes.', 1)],
            summary='A measurement study of tile cache eviction')
    add_doc('docs/render-diagram.png', 'render-diagram.png', 'image', 'unknown', 'rule', '2026-01-21', 'image', 1,
            [(1, 1, 'OCR text', 'Render diagram: depth prepass, tile binning, shading, resolve.', 0)])
    for i in range(filler):
        w = ' '.join(VOCAB[(i * 3 + j * 5) % len(VOCAB)] for j in range(8))
        add_doc('notes/misc/f%02d.md' % i, 'Filler %d' % i, 'note', 'unknown', 'rule', '2026-01-01', 'line', 1,
                [(1, 1, '', 'Filler note. ' + w, 0)])
    # rows for the newer table: the first eviction decision is replaced by the second
    a = c.execute("SELECT id FROM docs WHERE path = 'notes/decisions/DEC-001-eviction-policy.md'").fetchone()[0]
    b = c.execute("SELECT id FROM docs WHERE path = 'notes/decisions/DEC-002-eviction-policy.md'").fetchone()[0]
    c.execute('INSERT INTO newer VALUES(?,?,?)', (a, b, 'the note says so'))
    # one data file that is a name only
    master.execute("INSERT INTO names VALUES('docs','measurements.csv',4096,1.0,'data')")
    master.execute("INSERT INTO names VALUES('notes','archive/plan.md',100,1.0,'note')")
    master.execute("INSERT INTO names VALUES('notes','plan.md',100,2.0,'note')")
    c.executemany('INSERT INTO meta VALUES(?,?)',
                  [('built', str(time.time())), ('profile', 'orchestrator'), ('roots', '["notes","code","docs"]'),
                   ('schema', '3')])
    if with_names:
        lane_locate.fill_profile(c, master, ['notes', 'code', 'docs'])
    c.commit()
    c.close()
    master.close()
    return path


class Stub:
    """A model client replacement: embed, rerank, rerank_budget and why."""

    def __init__(self, qvec=None, scorer=None, why='model unavailable'):
        self.qvec = qvec
        self.scorer = scorer
        self.why = why
        self.embed_calls = []
        self.rerank_docs = None

    def embed(self, texts, kind='q', model=None, budget=None):
        self.embed_calls.append((texts, kind, model))
        if self.qvec is None:
            return None
        return np.array([self.qvec], dtype='float32')

    def rerank(self, q, docs, model=None, budget=None):
        self.rerank_docs = docs
        if self.scorer is None:
            return None
        return [self.scorer(d) for d in docs]

    def rerank_budget(self):
        return 8.0


class Base(unittest.TestCase):
    models = False
    filler = 0

    def setUp(self):
        self.home, self.cfg = helpers.make_home(models=self.models)
        self.addCleanup(helpers.cleanup, self.home)
        self.db = os.path.join(self.home, 'index', 'orchestrator', 'search.sqlite')
        build_db(self.cfg, self.db, filler=self.filler)

    def engine(self, **kw):
        eng = query.Engine(self.cfg, 'orchestrator', db_path=self.db, **kw)
        self.addCleanup(eng.close)
        return eng

    def paths(self, r):
        return [x['path'] for x in r['results']]


class TestWordsOnly(Base):
    def test_missing_index_says_to_build(self):
        from leks.config import ConfigError
        with self.assertRaises(ConfigError) as cm:
            query.Engine(self.cfg, 'architect')
        self.assertIn('leks build', str(cm.exception))

    def test_current_intent_needs_more_than_a_bare_still(self):
        from leks import text
        self.assertIsNone(text.CURRENT.search('what is drawn while the tile is still on its way'))
        self.assertIsNone(text.CURRENT.search('what did the status report say'))
        self.assertTrue(text.CURRENT.search('is the migration still open'))
        self.assertTrue(text.CURRENT.search('what is the state of the cache'))
        self.assertTrue(text.CURRENT.search('what is the project status?'))

    def test_lanes_line_exact(self):
        eng = self.engine()
        r = eng.search('tile cache eviction')
        self.assertEqual(r['lanes'], 'words only: no models configured')
        self.assertEqual(query.lane_line(r), 'words only: no models configured; word lanes: notes, documents')
        self.assertEqual(eng.render(r).split('\n')[-1], query.lane_line(r))

    def test_vector_mode_and_status(self):
        eng = self.engine()
        self.assertEqual(eng.vector_mode(), 'words')
        self.assertEqual(eng.coverage(), 0.0)
        s = eng.status()
        self.assertEqual(s['vector_mode'], 'words')
        self.assertEqual(s['why_no_meaning'], 'no models configured')
        self.assertIsNone(s['embedder'])
        self.assertEqual(s['profile'], 'orchestrator')

    def test_current_question_puts_state_note_first(self):
        eng = self.engine()
        r = eng.search('what is the current state of the frame graph pass order')
        self.assertEqual(r['results'][0]['path'], 'notes/STATE.md')
        self.assertEqual(r['results'][0]['source'], 'state')

    def test_priors_off_do_not_force_state_note(self):
        eng = self.engine()
        q = 'what is the current state of the frame graph pass order'
        off = [x for x in eng.search(q, priors=False)['results'] if x['path'] == 'notes/STATE.md'][0]
        on = [x for x in eng.search(q)['results'] if x['path'] == 'notes/STATE.md'][0]
        # current 1.25 and state 1.3, both squared
        self.assertAlmostEqual(on['score'] / off['score'], 1.25 ** 2 * 1.3 ** 2, delta=0.03)

    def test_history_question_reaches_archive_note(self):
        eng = self.engine()
        r = eng.search('why did we drop the single pass render without a depth prepass')
        hit = [x for x in r['results'] if x['path'] == 'notes/archive/old-frame-notes.md']
        self.assertTrue(hit)
        self.assertEqual(hit[0]['source'], 'history')
        self.assertEqual(hit[0]['status'], 'historical-record')

    def test_newer_version_line_and_lift(self):
        eng = self.engine()
        r = eng.search('tile cache evicts the least recently used tile when the budget is full')
        paths = self.paths(r)
        self.assertIn('notes/decisions/DEC-001-eviction-policy.md', paths)
        old = [x for x in r['results'] if x['path'].endswith('DEC-001-eviction-policy.md')][0]
        self.assertEqual(old['newer'], 'notes/decisions/DEC-002-eviction-policy.md')
        self.assertEqual(old['source'], 'history')
        self.assertIn('   NEWER VERSION: notes/decisions/DEC-002-eviction-policy.md', eng.render(r).split('\n'))
        self.assertIn('notes/decisions/DEC-002-eviction-policy.md', paths)
        self.assertLess(paths.index('notes/decisions/DEC-002-eviction-policy.md'),
                        paths.index('notes/decisions/DEC-001-eviction-policy.md'))

    def test_current_question_demotes_note_with_newer_version(self):
        eng = self.engine()
        r = eng.search('what is the current eviction policy of the tile cache', k=12)
        paths = self.paths(r)
        self.assertNotEqual(paths[0], 'notes/decisions/DEC-001-eviction-policy.md')
        self.assertLess(paths.index('notes/decisions/DEC-002-eviction-policy.md'),
                        paths.index('notes/decisions/DEC-001-eviction-policy.md'))

    def test_date_in_query_lifts_matching_note(self):
        eng = self.engine()
        base = eng.search('state frame graph pass order', priors=False)
        dated = eng.search('state frame graph pass order on 2 March 2026', priors='intent')
        a = [x for x in base['results'] if x['path'] == 'notes/STATE.md'][0]['score']
        b = [x for x in dated['results'] if x['path'] == 'notes/STATE.md'][0]['score']
        self.assertGreater(b, a * 1.2)

    def test_no_match(self):
        eng = self.engine()
        r = eng.search('zzzqqq')
        self.assertEqual(r['results'], [])
        self.assertEqual(r['note'], 'no match in this profile')
        self.assertEqual(r['confidence'], 'low')
        text = eng.render(r)
        self.assertIn('no match in this profile', text)
        self.assertEqual(text.split('\n')[-1], query.lane_line(r))

    def test_empty_query(self):
        eng = self.engine()
        r = eng.search('   ')
        self.assertEqual(r['results'], [])

    def test_low_confidence_adds_line_and_note(self):
        eng = self.engine()
        r = eng.search('overdraw')
        self.assertEqual(r['confidence'], 'low')
        self.assertIn('Nothing here looks like a direct answer: treat this as not found', r['note'])
        lines = eng.render(r).split('\n')
        self.assertEqual(lines[-2], 'Nothing here looks like a direct answer. Rephrase once, then say not found '
                                    'unless the cited lines show it.')

    def test_normal_confidence_with_many_hits(self):
        eng = self.engine()
        r = eng.search('tile cache eviction policy oldest budget frame')
        self.assertEqual(r['confidence'], 'normal')
        self.assertTrue(r['note'].startswith('These are the closest passages, not a confirmed answer.'))

    def test_hit_shape(self):
        eng = self.engine()
        r = eng.search('shader blobs build cache key')
        x = r['results'][0]
        self.assertEqual(set(x), {'source', 'newer', 'path', 'unit', 'refs', 'lines', 'title', 'heading', 'status',
                                  'status_from', 'kind', 'date', 'sha', 'score', 'snippet', 'what', 'warning',
                                  'copies', '_copies', '_text'})
        self.assertEqual(x['path'], 'notes/build_cache.md')
        self.assertEqual(x['copies'], 1)
        self.assertEqual(x['_copies'], ['notes/misc/build_cache_copy.md'])
        self.assertEqual(len(x['sha']), 12)
        self.assertTrue(re.match(r'^\d+-\d+$', x['lines']))
        for key in ('query', 'profile', 'results', 'ms', 'confidence', 'ce_top', 'cos_top', 'lanes', 'word_lanes',
                    'note'):
            self.assertIn(key, r)

    def test_render_header_and_copies(self):
        eng = self.engine()
        r = eng.search('shader blobs build cache key')
        head = eng.render(r).split('\n')[0]
        self.assertRegex(head, r'^1\. notes/build_cache\.md:\d+-\d+ \| note \| unknown \| 2026-02-20 \| 1 copies$')

    def test_render_first_hit_longer_than_rest(self):
        eng = self.engine()
        r = eng.search('tile cache eviction policy oldest budget frame')
        lines = [ln for ln in eng.render(r).split('\n') if ln.startswith('   ') and not ln.startswith('   NEWER')]
        self.assertTrue(lines)
        self.assertTrue(all(len(ln) <= 3 + 700 + 40 for ln in lines))

    def test_root_and_status_filters(self):
        eng = self.engine()
        r = eng.search('tile cache evict', root='code')
        self.assertTrue(r['results'])
        self.assertTrue(all(x['path'].startswith('code/') for x in r['results']))
        r = eng.search('tile cache evict', status='current')
        self.assertTrue(r['results'])
        self.assertTrue(all(x['status'] == 'current' for x in r['results']))

    def test_per_doc_cap_and_k(self):
        eng = self.engine()
        r = eng.search('tile cache eviction policy oldest budget frame', k=3, per_doc=1)
        self.assertLessEqual(len(r['results']), 3)
        self.assertEqual(len(set(self.paths(r))), len(r['results']))

    def test_modes(self):
        eng = self.engine()
        for mode in ('bm25', 'dense', 'hybrid', 'full'):
            r = eng.search('tile cache evict oldest', mode=mode)
            self.assertIn('lanes', r)
        # dense without a meaning lane finds nothing and says why
        r = eng.search('tile cache evict oldest', mode='dense')
        self.assertEqual(r['results'], [])
        # bm25 is the notes list only: no documents lane
        r = eng.search('tile cache evict oldest', mode='bm25')
        self.assertEqual(r['word_lanes'], ['notes'])
        self.assertEqual(r['lanes'], 'words only: not asked for')

    def test_full_mode_phrase_for_exact_name(self):
        eng = self.engine()
        r = eng.search('DEC-002', mode='full')
        self.assertEqual(r['results'][0]['path'], 'notes/decisions/DEC-002-eviction-policy.md')

    def test_full_mode_whole_note_lane(self):
        eng = self.engine()
        r = eng.search('frame graph', mode='full')
        self.assertIn('notes/frame_graph.md', self.paths(r))

    def test_document_page_citation(self):
        eng = self.engine()
        r = eng.search('Cache study eviction policies tile cache sample scenes')
        pdf = [x for x in r['results'] if x['path'] == 'docs/cache-study.pdf']
        self.assertTrue(pdf)
        self.assertEqual(pdf[0]['unit'], 'page')
        self.assertEqual(pdf[0]['kind'], 'document')
        self.assertIn('docs/cache-study.pdf p.1 | document | unknown', eng.render(r))

    def test_few_document_pages_are_put_on_the_scale_of_the_notes(self):
        eng = self.engine()
        q = 'Cache study eviction policies tile cache sample scenes'
        a, b = eng._scored(q)
        self.assertTrue(a and b)
        k = eng._doc_scale(q, a, b)
        self.assertGreater(k, 1.0)
        # the best page now sits near the best note instead of at the bottom of the merged list
        top = abs(a[0][1])
        self.assertLessEqual(abs(b[0][1]) * k, top * 1.0001)
        eng._dn = query.DOC_SMALL
        self.assertEqual(eng._doc_scale(q, a, b), 1.0)

    def test_reference_list_flag_and_weight(self):
        eng = self.engine()
        r = eng.search('Tessel Quorbit tile caches frame pacing', k=12)
        refs = [x for x in r['results'] if x['refs']]
        self.assertTrue(refs)
        # a reference list counts little: its place in the merged list falls when REFS_ALPHA applies
        cid = eng.db.execute('SELECT id FROM chunks WHERE refs = 1').fetchone()[0]
        q = 'Tessel Quorbit tile caches frame pacing'
        low = eng.merged_words(q).index(cid)
        with mock.patch.object(query, 'REFS_ALPHA', 1.0):
            full = eng.merged_words(q).index(cid)
        self.assertGreater(low, full)
        self.assertIn(' | reference list', eng.render(r))

    def test_image_citation(self):
        eng = self.engine()
        r = eng.search('render diagram depth prepass tile binning shading resolve')
        img = [x for x in r['results'] if x['path'] == 'docs/render-diagram.png']
        self.assertTrue(img)
        self.assertEqual(img[0]['unit'], 'image')
        self.assertIn('docs/render-diagram.png image 1 | image |', eng.render(r))

    def test_symbol_query_opens_symbol_lane(self):
        eng = self.engine()
        r = eng.search('where is evict_oldest defined')
        self.assertIn('symbols', r['word_lanes'])
        self.assertEqual(r['results'][0]['path'], 'code/tile_cache.py')
        self.assertTrue(query.lane_line(r).endswith('word lanes: notes, documents, symbols'))
        r = eng.search('where is evict_oldest defined', sym_lane=False)
        self.assertNotIn('symbols', r['word_lanes'])

    def test_plain_words_do_not_open_symbol_lane(self):
        eng = self.engine()
        r = eng.search('tile cache eviction')
        self.assertNotIn('symbols', r['word_lanes'])

    def test_strict_raises_for_missing_lane(self):
        eng = self.engine()
        with self.assertRaises(RuntimeError):
            eng.search('tile cache', strict=True)

    def test_words_only_flag(self):
        eng = self.engine()
        r = eng.search('tile cache', words_only=True)
        self.assertEqual(r['lanes'], 'words only: not asked for')
        r = eng.search('tile cache', words_only=True, strict=False, rerank=True)
        self.assertEqual(r['lanes'], 'words only: not asked for')

    def test_public_strips_internals_and_keeps_original(self):
        eng = self.engine()
        r = eng.search('tile cache eviction policy oldest budget frame')
        p = eng.public(r)
        self.assertIn('_text', r['results'][0])
        for x in p['results']:
            for key in ('_text', '_copies', 'kind', 'sha', 'score', 'title', 'status_from'):
                self.assertNotIn(key, x)
            self.assertIn(x.get('unit', 'line'), ('line', 'page', 'image'))
            if x['path'].startswith('notes/'):
                self.assertNotIn('unit', x)
        self.assertNotIn('query', p)
        self.assertNotIn('ce_top', p)
        self.assertLessEqual(len(p['results'][0]['snippet']), 1300)
        if len(p['results']) > 2:
            self.assertLessEqual(len(p['results'][2]['snippet']), 300)
        json.dumps(p)

    def test_public_keeps_page_unit(self):
        eng = self.engine()
        r = eng.search('Cache study eviction policies tile cache sample scenes')
        p = eng.public(r)
        pdf = [x for x in p['results'] if x['path'] == 'docs/cache-study.pdf'][0]
        self.assertEqual(pdf['unit'], 'page')


class TestRead(Base):
    def test_line_range(self):
        eng = self.engine()
        r = eng.read('notes/STATE.md', 2, 4)
        self.assertEqual(r['path'], 'notes/STATE.md')
        self.assertEqual((r['from'], r['to']), (2, 4))
        rows = r['text'].split('\n')
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0].startswith('2\t'))
        self.assertEqual(r['status'], 'current')
        self.assertEqual(r['date'], '2026-03-02')
        with open(self.cfg.abs_path('notes/STATE.md'), encoding='utf-8') as f:
            real = f.read().split('\n')
        self.assertEqual(r['lines_total'], len(real) - 1)
        self.assertEqual(rows[0], '2\t' + real[1])

    def test_default_cap_of_120_lines(self):
        eng = self.engine()
        ap = self.cfg.abs_path('notes/frame_graph.md')
        with open(ap, 'a', encoding='utf-8') as f:
            f.write('\n'.join('extra line %d' % i for i in range(300)) + '\n')
        r = eng.read('notes/frame_graph.md', 1, 500)
        self.assertEqual(len(r['text'].split('\n')), 120)
        self.assertEqual(r['to'], 120)
        self.assertEqual(eng.read('notes/frame_graph.md', 10, None, maxlines=5)['to'], 14)

    def test_start_past_the_end(self):
        eng = self.engine()
        r = eng.read('notes/STATE.md', 5000, 5010)
        self.assertEqual(r['text'], '')

    def test_refuses_unindexed_and_odd_paths(self):
        eng = self.engine()
        err = {'error': 'not visible in this profile, or not indexed'}
        self.assertEqual(eng.read('notes/nope.md'), err)
        self.assertEqual(eng.read('notes/../notes/STATE.md'), err)
        self.assertEqual(eng.read(''), err)
        self.assertEqual(eng.read('/etc/hosts'), err)
        self.assertEqual(eng.read('other/STATE.md'), err)

    def test_absolute_path_maps_to_display_path(self):
        eng = self.engine()
        r = eng.read(self.cfg.abs_path('notes/STATE.md'), 1, 2)
        self.assertEqual(r['path'], 'notes/STATE.md')
        self.assertNotIn(self.home, json.dumps(r))

    def test_copy_path_reads_the_winner(self):
        eng = self.engine()
        r = eng.read('notes/misc/build_cache_copy.md', 1, 3)
        self.assertEqual(r['path'], 'notes/build_cache.md')

    def test_bad_numbers(self):
        eng = self.engine()
        self.assertIn('error', eng.read('notes/STATE.md', 'x', 'y'))

    def test_document_pages(self):
        eng = self.engine()
        r = eng.read('docs/cache-study.pdf', 1, 1)
        self.assertEqual(r['unit'], 'page')
        self.assertEqual(r['units_total'], 2)
        self.assertTrue(r['text'].startswith('[page 1] Cache study.'))
        self.assertNotIn('[page 2]', r['text'])
        r = eng.read('docs/cache-study.pdf', 1, 99)
        self.assertIn('[page 2]', r['text'])
        self.assertEqual((r['from'], r['to']), (1, 2))

    def test_document_page_cap(self):
        self.engine()
        c = sqlite3.connect(self.db)
        did = c.execute("SELECT id FROM docs WHERE path = 'docs/cache-study.pdf'").fetchone()[0]
        for p in range(3, 10):
            c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)', (900 + p, did, p, p, p, '', 'page text %d' % p,
                                                                      -1, 0))
        c.commit()
        c.close()
        eng2 = query.Engine(self.cfg, 'orchestrator', db_path=self.db)
        self.addCleanup(eng2.close)
        r = eng2.read('docs/cache-study.pdf', 1, 50)
        self.assertEqual(r['text'].count('[page '), 5)
        self.assertEqual(r['to'], 5)

    def test_image_text(self):
        eng = self.engine()
        r = eng.read('docs/render-diagram.png')
        self.assertEqual(r['unit'], 'image')
        self.assertIn('[image 1] Render diagram', r['text'])

    def test_missing_range_in_document(self):
        eng = self.engine()
        self.assertEqual(eng.read('docs/cache-study.pdf', 7, 8)['text'], 'no extracted text in this range')

    def test_file_gone_serves_indexed_text(self):
        eng = self.engine()
        os.remove(self.cfg.abs_path('notes/build_cache.md'))
        r = eng.read('notes/build_cache.md', 1, 40)
        self.assertEqual(r['note'], 'the file is not reachable now: this is the indexed text')
        self.assertIn('[line 1] ', r['text'])
        self.assertEqual(r['unit'], 'line')

    def test_changed_file_is_flagged(self):
        eng = self.engine()
        ap = self.cfg.abs_path('notes/frame_graph.md')
        self.assertFalse(eng.changed('notes/frame_graph.md'))
        with open(ap, 'a', encoding='utf-8') as f:
            f.write('\nA new paragraph appended later.\n')
        self.assertTrue(eng.changed('notes/frame_graph.md'))
        self.assertEqual(eng.read('notes/frame_graph.md', 1, 3)['warning'], query.CHANGED)
        r = eng.search('frame graph render passes resources')
        self.assertIn('   ' + query.CHANGED, eng.render(r).split('\n'))

    def test_symlink_out_of_root_is_not_read(self):
        eng = self.engine()
        ap = self.cfg.abs_path('notes/frame_graph.md')
        outside = os.path.join(self.home, 'outside.md')
        with open(outside, 'w', encoding='utf-8') as f:
            f.write('secret outside text\n')
        os.remove(ap)
        os.symlink(outside, ap)
        r = eng.read('notes/frame_graph.md', 1, 3)
        self.assertNotIn('secret outside text', json.dumps(r))
        self.assertIn('note', r)

    def test_changed_is_false_for_unknown(self):
        eng = self.engine()
        self.assertFalse(eng.changed(None))
        self.assertFalse(eng.changed('notes/unknown.md'))
        self.assertFalse(eng.changed('docs/cache-study.pdf'))


class TestSmallTools(Base):
    def test_state_lists_state_notes_only(self):
        eng = self.engine()
        s = eng.state()
        self.assertEqual(s, {'state_notes': [{'path': 'notes/STATE.md', 'date': '2026-03-02'}]})

    def test_state_skips_historical_record(self):
        c = sqlite3.connect(self.db)
        c.execute("UPDATE docs SET kind='state', status='historical-record' WHERE path = 'notes/frame_graph.md'")
        c.commit()
        c.close()
        eng = self.engine()
        self.assertEqual([x['path'] for x in eng.state()['state_notes']], ['notes/STATE.md'])

    def test_status_fields(self):
        eng = self.engine()
        s = eng.status()
        for key in ('version', 'profile', 'docs', 'chunks', 'documents', 'images', 'symbols', 'names', 'embedder',
                    'reranker', 'backend', 'vectors', 'vector_mode', 'coverage', 'why_no_meaning',
                    'why_no_reranker', 'pdf_text', 'ocr', 'sandbox', 'built_utc', 'by_root'):
            self.assertIn(key, s)
        self.assertEqual(s['docs'], len(META) + 2)
        self.assertEqual(s['documents'], 1)
        self.assertEqual(s['images'], 1)
        self.assertEqual(s['by_root'], {'code': 2, 'docs': 3, 'notes': 6})
        self.assertRegex(s['built_utc'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertTrue(s['names'] > 0)
        self.assertEqual(s['version'], '3.0.0')
        self.assertNotIn(self.home, json.dumps(s))
        self.assertGreaterEqual(s['symbols'], 3)

    def test_locate_goes_through_names(self):
        eng = self.engine()
        r = eng.locate('tile cache')
        self.assertEqual(r['files'][0]['path'], 'code/tile_cache.py')
        self.assertEqual(r['rule'], 'A row is a location: name, size, hash when indexed, root. '
                                    'Locate does not search content.')

    def test_tried_goes_through_prior_lane(self):
        eng = self.engine()
        out = eng.tried('orbital telescope calibration')
        self.assertTrue(out.startswith('nothing certain. No row holds enough of this proposal. '))

    def test_missing_names_table(self):
        c = sqlite3.connect(self.db)
        c.execute('DROP TABLE names')
        c.commit()
        c.close()
        eng = self.engine()
        r = eng.locate('tile')
        self.assertEqual(r['files'], [])
        self.assertEqual(eng.status()['names'], 0)


@unittest.skipUnless(np is not None, 'numpy not installed')
class TestMeaningLane(Base):
    models = True

    def prep(self, eng, rows=None):
        """Fake vector file: one unit row per chunk, built by hand."""
        n = eng.db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        rng = np.random.RandomState(5)
        m = rng.randn(n, 64).astype('float32')
        m /= np.linalg.norm(m, axis=1, keepdims=True)
        eng.vec = m
        eng.covered = n if rows is None else rows
        eng.vec_why = None
        eng._vmap_cache = None
        return m

    def chunk_row(self, eng, path):
        return eng.db.execute('SELECT c.vec FROM chunks c JOIN docs d ON d.id = c.doc WHERE d.path = ? '
                              'ORDER BY c.ord LIMIT 1', (path,)).fetchone()[0]

    def test_no_vectors_yet_reason(self):
        eng = self.engine(client=Stub())
        self.assertEqual(eng.vec_why, 'no vectors yet')
        r = eng.search('tile cache')
        self.assertEqual(r['lanes'], 'words only: no vectors yet')

    def test_meaning_lane_finds_chunk_without_word_overlap(self):
        stub = Stub()
        eng = self.engine(client=stub)
        m = self.prep(eng)
        stub.qvec = m[self.chunk_row(eng, 'notes/build_cache.md')]
        r = eng.search('unrelatedwordxyz', rerank=False)
        self.assertEqual(r['results'][0]['path'], 'notes/build_cache.md')
        self.assertEqual(r['lanes'], 'lanes: words, meaning')
        self.assertGreater(r['cos_top'], 0.99)
        self.assertEqual(r['confidence'], 'normal')
        self.assertEqual(stub.embed_calls[0][1], 'q')
        self.assertEqual(eng.vector_mode(), 'meaning')

    def test_lanes_all_three(self):
        stub = Stub(scorer=lambda d: 1.0 if 'blobs' in d else 0.0)
        eng = self.engine(client=stub)
        m = self.prep(eng)
        stub.qvec = m[0]
        r = eng.search('shader cache')
        self.assertEqual(r['lanes'], 'lanes: words, meaning, reranker')
        self.assertEqual(query.lane_line(r), 'lanes: words, meaning, reranker; word lanes: notes, documents')
        self.assertEqual(r['ce_top'], 1.0)

    def test_reranker_moves_its_favourite_up(self):
        stub = Stub(scorer=lambda d: 1.0 if d.startswith('Build cache') else 0.0)
        eng = self.engine(client=stub)
        m = self.prep(eng)
        stub.qvec = m[0]
        plain = eng.search('cache tile oldest budget', rerank=False)
        ranked = eng.search('cache tile oldest budget', rerank=True)
        self.assertEqual(ranked['results'][0]['path'], 'notes/build_cache.md')
        self.assertNotEqual(self.paths(plain)[0], 'notes/build_cache.md')
        # the texts sent to the reranker: title | heading, newline, text, cut to the fixed sizes
        self.assertTrue(all(' | ' in d.split('\n')[0] and len(d) <= 120 + 3 + 80 + 1 + 900 for d in stub.rerank_docs))

    def test_reranker_sees_at_most_two_chunks_per_doc(self):
        stub = Stub(scorer=lambda d: 0.5)
        eng = self.engine(client=stub)
        m = self.prep(eng)
        stub.qvec = m[0]
        eng.search('cache tile oldest budget frame graph', rerank=True, candidates=40)
        firsts = [d.split('\n')[0] for d in stub.rerank_docs]
        self.assertLessEqual(max(firsts.count(t) for t in set(firsts)), 2)
        eng.search('cache tile oldest budget frame graph', rerank=True, candidates=3)
        self.assertEqual(len(stub.rerank_docs), 3)

    def test_rows_outside_the_rerank_window_stay_below(self):
        stub = Stub(scorer=lambda d: float(len(d)))
        eng = self.engine(client=stub)
        m = self.prep(eng)
        stub.qvec = m[0]
        r = eng.search('cache tile oldest budget frame graph', rerank=True, candidates=2, k=12, per_doc=1)
        scores = [x['score'] for x in r['results']]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_reranker_missing_reason(self):
        stub = Stub(scorer=None, why='model over its time budget')
        eng = self.engine(client=stub)
        m = self.prep(eng)
        stub.qvec = m[0]
        r = eng.search('cache tile')
        self.assertEqual(r['lanes'], 'words and meaning, no reranker: model over its time budget')

    def test_no_reranker_configured(self):
        self.cfg.reranker = None
        stub = Stub()
        eng = self.engine(client=stub)
        m = self.prep(eng)
        stub.qvec = m[0]
        r = eng.search('cache tile')
        self.assertEqual(r['lanes'], 'words and meaning, no reranker: no reranker configured')
        self.assertEqual(eng.status()['why_no_reranker'], 'no reranker configured')
        r = eng.search('cache tile', rerank=False)
        self.assertEqual(r['lanes'], 'lanes: words, meaning')

    def test_meaning_missing_reranker_there(self):
        stub = Stub(qvec=None, scorer=lambda d: 1.0, why='model backend not installed')
        eng = self.engine(client=stub)
        self.prep(eng)
        r = eng.search('cache tile')
        self.assertEqual(r['lanes'], 'words and reranker: no meaning lane, model backend not installed')

    def test_reason_text_is_always_from_the_fixed_set(self):
        stub = Stub(qvec=None, why='<evil text from a reply>')
        eng = self.engine(client=stub)
        self.prep(eng)
        r = eng.search('cache tile', rerank=False)
        self.assertEqual(r['lanes'], 'words only: model unavailable')

    def test_coverage_note(self):
        stub = Stub()
        eng = self.engine(client=stub)
        m = self.prep(eng)
        eng.covered = len(m) // 2
        stub.qvec = m[0]
        r = eng.search('cache tile', rerank=False)
        self.assertRegex(r['lanes'], r'^lanes: words, meaning \(vectors for \d+% of passages\)$')
        self.assertEqual(eng.lanes(True, None, True, None),
                         'lanes: words, meaning, reranker (vectors for %d%% of passages)' % int(eng.coverage() * 100))
        full = eng.status()
        self.assertEqual(full['vectors'], len(m))
        self.assertEqual(full['vector_mode'], 'meaning')

    def test_zero_rows_have_no_say(self):
        stub = Stub()
        eng = self.engine(client=stub)
        m = self.prep(eng)
        eng.vec[:] = 0.0
        stub.qvec = m[0]
        r = eng.search('unrelatedwordxyz', rerank=False)
        self.assertEqual(r['results'], [])

    def test_strict_raises_when_lane_missing(self):
        stub = Stub(qvec=None)
        eng = self.engine(client=stub)
        self.prep(eng)
        with self.assertRaises(RuntimeError):
            eng.search('cache tile', strict=True)
        stub2 = Stub(scorer=None)
        eng2 = self.engine(client=stub2)
        m = self.prep(eng2)
        stub2.qvec = m[0]
        with self.assertRaises(RuntimeError):
            eng2.search('cache tile', strict=True, rerank=True)
        self.assertEqual(eng2.search('cache tile', strict=True, rerank=False)['lanes'], 'lanes: words, meaning')

    def test_numpy_missing_closes_meaning_lane(self):
        with mock.patch.object(query, 'np', None):
            eng = self.engine(client=Stub())
            r = eng.search('cache tile')
        self.assertEqual(eng.vec_why, 'numpy not installed')
        self.assertEqual(r['lanes'], 'words only: numpy not installed')

    def test_status_with_models(self):
        eng = self.engine(client=Stub())
        s = eng.status()
        self.assertEqual(s['embedder'], 'mdense')
        self.assertEqual(s['reranker'], 'mlate')
        self.assertEqual(s['backend'], 'fake')


class TestLanesStrings(Base):
    def test_all_forms(self):
        eng = self.engine()
        eng.coverage = lambda: 1.0
        self.assertEqual(eng.lanes(False, 'no models configured', False, None), 'words only: no models configured')
        self.assertEqual(eng.lanes(False, 'not asked for', True, None),
                         'words and reranker: no meaning lane, not asked for')
        self.assertEqual(eng.lanes(True, None, False, None), 'lanes: words, meaning')
        self.assertEqual(eng.lanes(True, None, False, 'no reranker configured'),
                         'words and meaning, no reranker: no reranker configured')
        self.assertEqual(eng.lanes(True, None, True, None), 'lanes: words, meaning, reranker')

    def test_lane_line_and_conf(self):
        self.assertEqual(query.lane_line({'lanes': 'x', 'word_lanes': []}), 'x')
        self.assertEqual(query.lane_line({'lanes': 'x', 'word_lanes': ['notes', 'symbols']}),
                         'x; word lanes: notes, symbols')
        self.assertEqual(query.conf(0.0, [1, 2]), 'low')
        self.assertEqual(query.conf(0.0, [1, 2, 3]), 'normal')
        self.assertEqual(query.conf(0.4, []), 'normal')

    def test_constants(self):
        self.assertEqual(query.RRF, 60)
        self.assertEqual(query.PRIOR_WINDOW, 30)
        self.assertEqual(query.BUDGET_KEYS, ('embed', 'rerank', 'connect', 'ping'))
        self.assertEqual(query.PRIOR['superseded'], 0.72)
        self.assertEqual(query.KIND['state'], 1.3)


class TestLaneHonesty(Base):
    """The lanes line names the documents lane only when it ran, else it says why it is off."""

    def empty_docs(self):
        c = sqlite3.connect(self.db)
        c.execute('DELETE FROM dfts')
        c.commit()
        c.close()

    def test_documents_off_without_a_pdf_reader(self):
        self.empty_docs()
        eng = self.engine()
        with mock.patch('leks.docs.capabilities', return_value={'pdf_text': False, 'ocr': False}):
            r = eng.search('cache study eviction')
        self.assertEqual(r['docs_off'], 'pdf extra not installed')
        self.assertEqual(query.lane_line(r), 'words only: no models configured; word lanes: notes; '
                                             'documents off: pdf extra not installed')
        self.assertEqual(eng.render(r).split('\n')[-1], query.lane_line(r))

    def test_documents_off_when_nothing_is_indexed(self):
        self.empty_docs()
        eng = self.engine()
        with mock.patch('leks.docs.capabilities', return_value={'pdf_text': True, 'ocr': False}):
            r = eng.search('cache study eviction')
        self.assertEqual(query.lane_line(r), 'words only: no models configured; word lanes: notes; '
                                             'documents off: no document indexed')

    def test_documents_on_when_pages_are_there(self):
        r = self.engine().search('cache study eviction')
        self.assertIsNone(r['docs_off'])
        self.assertEqual(query.lane_line(r), 'words only: no models configured; word lanes: notes, documents')

    def test_status_lanes_and_fix(self):
        self.empty_docs()
        eng = self.engine()
        with mock.patch('leks.docs.capabilities', return_value={'pdf_text': False, 'ocr': False}):
            s = eng.status()
        wl = 'notes, symbols' if eng.has_syms else 'notes'
        self.assertEqual(s['lanes'], 'words only: no models configured; word lanes: %s; '
                                     'documents off: pdf extra not installed' % wl)
        self.assertEqual(s['search'], 'search runs on words only: no models configured; documents off: pdf extra '
                                      'not installed. Fix: pip install -e ".[pdf]" in the LEKS clone, then leks build')

    def test_lane_line_with_docs_off(self):
        self.assertEqual(query.lane_line({'lanes': 'x', 'word_lanes': ['notes'], 'docs_off': 'no document indexed'}),
                         'x; word lanes: notes; documents off: no document indexed')


class _RealBackend(Base):
    models = True

    def setUp(self):
        super().setUp()
        # a real backend in the config, the fake one is always ready
        p = os.path.join(self.home, 'config.json')
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        d['models']['backend'] = 'torch'
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        from leks import config
        self.cfg = config.load(p)
        os.environ['LEKS_PROFILE'] = 'orchestrator'   # cleanup(home) puts it back


class TestStatusWithoutNumpy(_RealBackend):
    """CI installs no numpy: status names numpy and its fix before any later reason."""

    def test_numpy_is_named(self):
        with mock.patch('leks.query.np', None), \
                mock.patch('leks.modeld_client._have', return_value=True), \
                mock.patch('leks.modeld_client._weights_present', return_value=True):
            s = self.engine().status()
        self.assertEqual(s['search'], 'search runs on words and the reranker, no meaning lane: numpy not installed. '
                                      'Fix: pip install -e ".[models]" in the LEKS clone')
        self.assertEqual(s['why_no_meaning'], 'numpy not installed')


@unittest.skipUnless(np is not None, 'numpy not installed')
class TestStatusSaysWhy(_RealBackend):
    """Meaning search configured, packages or weights missing: status says words, why, and the fix."""

    NO_BACKEND = ('search runs on words only: model backend not installed. '
                  'Fix: pip install -e ".[models]" in the LEKS clone')

    def test_no_backend(self):
        with mock.patch('leks.modeld_client._have', return_value=False):
            s = self.engine().status()
        self.assertEqual(s['search'], self.NO_BACKEND)
        self.assertEqual(s['why_no_meaning'], 'model backend not installed')
        self.assertTrue(s['lanes'].startswith('words only: model backend not installed'))

    def test_no_weights(self):
        with mock.patch('leks.modeld_client._have', return_value=True), \
                mock.patch('leks.modeld_client._weights_present', return_value=False):
            s = self.engine().status()
        self.assertEqual(s['search'], 'search runs on words only: model weights not found. Fix: leks models fetch')

    def test_ready_but_no_vectors(self):
        with mock.patch('leks.modeld_client._have', return_value=True), \
                mock.patch('leks.modeld_client._weights_present', return_value=True):
            s = self.engine().status()
        self.assertEqual(s['search'], 'search runs on words and the reranker, no meaning lane: no vectors yet. '
                                      'Fix: leks embed')

    def test_cli_status(self):
        from leks import __main__ as cli
        out = io.StringIO()
        with mock.patch('leks.modeld_client._have', return_value=False), contextlib.redirect_stdout(out):
            code = cli.main(['status'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['search'], self.NO_BACKEND)

    def test_mcp_status(self):
        from leks import server
        server._S.update(cfg=self.cfg, own=False, engine=None, stamp=None)
        self.addCleanup(server._S.update, cfg=None, own=False, engine=None, stamp=None)
        self.addCleanup(lambda: server._S['engine'] and server._S['engine'].close())
        with mock.patch('leks.modeld_client._have', return_value=False):
            rep = server.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'status', 'arguments': {}}})
        self.assertNotIn('isError', rep['result'])
        self.assertEqual(json.loads(rep['result']['content'][0]['text'])['search'], self.NO_BACKEND)


class TestEngineMayStartTheHelper(Base):
    models = True

    def test_only_the_search_client_starts_it(self):
        # a host with no warm-up hook (Copilot) relies on this; bulk runs keep their own start and bye
        from leks import modeld_client
        self.assertIsNotNone(self.engine()._get_client()._wake)
        self.assertIsNone(modeld_client.Client.from_config(self.cfg)._wake)


class TestWithBuilder(unittest.TestCase):
    """The same engine over a database made by the real builder."""

    def setUp(self):
        from leks import build
        self.home, self.cfg = helpers.make_home()
        self.addCleanup(helpers.cleanup, self.home)
        build.update_all(self.cfg, log=lambda *a: None)
        self.eng = query.Engine(self.cfg, 'orchestrator')
        self.addCleanup(self.eng.close)

    def test_search_read_locate_state(self):
        r = self.eng.search('what is the current state of the frame graph pass order')
        self.assertEqual(r['results'][0]['path'], 'notes/STATE.md')
        self.assertEqual(r['lanes'], 'words only: no models configured')
        # the test corpus has no pdf or image, so the documents lane is off and says why
        self.assertEqual(self.eng.render(r).split('\n')[-1],
                         'words only: no models configured; word lanes: notes; documents off: no document indexed')
        cited = r['results'][0]['lines'].split('-')
        got = self.eng.read('notes/STATE.md', int(cited[0]), int(cited[1]))
        self.assertEqual(len(got['text'].split('\n')), int(cited[1]) - int(cited[0]) + 1)
        self.assertEqual(self.eng.locate('tile cache')['files'][0]['path'], 'code/tile_cache.py')
        self.assertEqual([x['path'] for x in self.eng.state()['state_notes']], ['notes/STATE.md'])
        self.assertEqual(self.eng.status()['docs'], 9)

    def test_symbol_and_newer(self):
        r = self.eng.search('where is evict_oldest defined')
        self.assertEqual(r['results'][0]['path'], 'code/tile_cache.py')
        r = self.eng.search('tile cache evicts the least recently used tile when the budget is full')
        old = [x for x in r['results'] if x['path'].endswith('DEC-001-eviction-policy.md')]
        self.assertTrue(old and old[0]['newer'].endswith('DEC-002-eviction-policy.md'))


class TestHygiene(unittest.TestCase):
    def test_no_dash_characters_in_the_search_modules(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = (chr(0x2014), chr(0x2013))
        names = ['text.py', 'source_class.py', 'query.py', 'lane_prior.py', 'lane_locate.py']
        for n in names:
            with open(os.path.join(here, 'leks', n), encoding='utf-8') as f:
                body = f.read()
            for ch in bad:
                self.assertNotIn(ch, body, n)


if __name__ == '__main__':
    unittest.main()
