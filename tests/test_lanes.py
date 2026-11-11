"""Tests for the text helpers, source classes, the tried lane and the locate lane."""
from __future__ import annotations

import os
import re
import sqlite3
import unittest

from leks import lane_locate, lane_prior, query, schema, source_class, text
from tests import test_query as tq


class TestText(unittest.TestCase):
    def test_terms_of(self):
        self.assertEqual(text.terms_of('What is the current eviction policy?'), ['current', 'eviction', 'policy'])
        self.assertEqual(text.terms_of('the of'), ['the', 'of'])
        self.assertEqual(text.terms_of('show the note about tile cache'), ['tile', 'cache'])
        self.assertEqual(text.terms_of('tile tile cache'), ['tile', 'cache'])
        self.assertEqual(text.terms_of(''), [])

    def test_regexes(self):
        self.assertTrue(text.CURRENT.search('what is the current state'))
        self.assertTrue(text.CURRENT.search('has the cache been moved'))
        self.assertFalse(text.CURRENT.search('tile cache eviction'))
        self.assertTrue(text.WHICH.search('Where is the build script'))
        self.assertTrue(text.WHICH.search('which note covers shaders'))
        self.assertFalse(text.WHICH.search('the build script, where'))
        found = text.NAME.findall('ADR-004 and tile_cache.py and v2.1 and T16')
        self.assertIn('ADR-004', found)
        self.assertIn('tile_cache.py', found)
        self.assertIn('2.1', ' '.join(found))
        self.assertEqual(text.MONTHS['march'], 3)
        self.assertEqual(len(text.MONTHS), 12)

    def test_dates_in(self):
        self.assertEqual(text.dates_in('notes of 2026-03-02'), [(3, 2)])
        self.assertEqual(text.dates_in('on 2 March'), [(3, 2)])
        self.assertEqual(text.dates_in('on 2nd March'), [(3, 2)])
        self.assertEqual(text.dates_in('on 2nd of March'), [(3, None)])
        self.assertEqual(text.dates_in('on March 2'), [(3, 2)])
        self.assertEqual(text.dates_in('what happened in April'), [(4, None)])
        self.assertEqual(text.dates_in('we may add a cache'), [])
        self.assertEqual(text.dates_in('what happened in May'), [(5, None)])
        self.assertEqual(text.dates_in('no date here'), [])
        # a full date wins over a bare month
        self.assertEqual(text.dates_in('2026-03-02 and April'), [(3, 2)])

    def test_snippet(self):
        t = 'x ' * 100 + 'the tile cache evicts the oldest tile ' + 'y ' * 100
        s = text.snippet(t, 'oldest tile', 200)
        self.assertIn('oldest', s)
        self.assertLessEqual(len(s), 200)
        self.assertEqual(text.snippet('a\n\n  b   c', 'zzz'), 'a b c')

    def test_best_window(self):
        t = 'Intro sentence about nothing. ' * 20 + 'The shader cache key hashes the include files. ' + 'Tail words. ' * 20
        w = text.best_window(t, 'shader cache key', 120)
        self.assertIn('shader cache key', w)
        self.assertLess(len(w), 140)
        self.assertEqual(text.best_window('short\n\ntext', 'x', 100), 'short / text')
        self.assertEqual(text.best_window('a' * 50, 'zzz', 10), 'a' * 10)

    def test_unit_and_cite(self):
        self.assertEqual(text.unit_of('note', 'line'), 'line')
        self.assertEqual(text.unit_of('code', None), 'line')
        self.assertEqual(text.unit_of('document', 'page'), 'page')
        self.assertEqual(text.unit_of('document', 'paragraph'), 'paragraph')
        self.assertEqual(text.unit_of('image', 'image'), 'image')
        self.assertEqual(text.unit_of('document', None), 'line')
        self.assertEqual(text.cite({'path': 'notes/a.md', 'lines': '12-30', 'unit': 'line'}), 'notes/a.md:12-30')
        self.assertEqual(text.cite({'path': 'notes/a.md', 'lines': '12-30'}), 'notes/a.md:12-30')
        self.assertEqual(text.cite({'path': 'docs/x.pdf', 'lines': '3-3', 'unit': 'page'}), 'docs/x.pdf p.3')
        self.assertEqual(text.cite({'path': 'docs/x.pdf', 'lines': '3-5', 'unit': 'page'}), 'docs/x.pdf p.3-5')
        self.assertEqual(text.cite({'path': 'docs/y.docx', 'lines': '4-4', 'unit': 'paragraph'}),
                         'docs/y.docx par. 4')
        self.assertEqual(text.cite({'path': 'docs/z.png', 'lines': '1-1', 'unit': 'image'}), 'docs/z.png image 1')

    def test_fts_or(self):
        self.assertEqual(text.fts_or(['a', 'b']), '"a" OR "b"')
        self.assertEqual(text.fts_or(['a"b']), '"ab"')
        self.assertEqual(text.fts_or([]), '')

    def test_english_only(self):
        # only English is handled: a month name in another language is no date
        self.assertEqual(text.dates_in('2 marca'), [])
        self.assertFalse(text.CURRENT.search('teraz aktualny stan'))


class TestSourceClass(unittest.TestCase):
    def test_frozen(self):
        self.assertEqual(source_class.of('decision', 'record', 'frozen', False), 'frozen')
        self.assertEqual(source_class.of('decision', 'record', 'frozen', True), 'history')
        self.assertEqual(source_class.of('decision', 'superseded', 'frozen', False), 'history')
        self.assertEqual(source_class.of('decision', 'stale', 'frozen', False), 'history')

    def test_history(self):
        self.assertEqual(source_class.of('note', 'superseded', 'front-matter', False), 'history')
        self.assertEqual(source_class.of('note', 'stale', 'front-matter', False), 'history')
        self.assertEqual(source_class.of('note', 'unknown', 'rule', True), 'history')
        self.assertEqual(source_class.of('decision', 'historical-record', 'path', False), 'history')
        self.assertEqual(source_class.of('code', 'historical-record', 'path', False), 'history')
        # old but not replaced and not from a history folder: the kind still speaks
        self.assertEqual(source_class.of('decision', 'historical-record', 'front-matter', False), 'decision')

    def test_by_kind(self):
        for kind in ('state', 'rule', 'code', 'decision', 'result', 'document', 'image'):
            self.assertEqual(source_class.of(kind, 'current', 'rule', False), kind)
        self.assertEqual(source_class.of('note', 'unknown', 'rule', False), 'note')
        self.assertEqual(source_class.of('something', 'unknown', 'rule', False), 'note')

    def test_trust_order(self):
        self.assertEqual(source_class.TRUST, ('frozen', 'decision', 'result', 'state', 'note'))


class TestPrior(tq.Base):
    filler = 45

    def test_constants(self):
        self.assertEqual((lane_prior.MAX_PROPOSAL, lane_prior.MIN_COVER, lane_prior.MIN_RARE), (600, 0.65, 2))
        self.assertEqual((lane_prior.RARE_IDF, lane_prior.MAX_ROWS, lane_prior.CLOSEST), (2.5, 5, 3))
        self.assertEqual(lane_prior.CLOSEST_CLASSES, ('decision', 'result'))
        self.assertEqual(lane_prior.CLASSES, ('frozen', 'decision', 'result', 'state', 'history', 'note'))
        self.assertEqual(lane_prior.KINDS, ('decision', 'result', 'note', 'state'))

    def test_cover(self):
        idf = {'evict': 3.0, 'tile': 1.0, 'random': 3.0}
        c, rare = lane_prior.cover(idf, 'The cache evicts tiles. Evict first.')
        # evict matches (3 of 7), tiles does not match the stem of tile
        self.assertAlmostEqual(c, 3.0 / 7.0)
        self.assertEqual(rare, 1)
        self.assertEqual(lane_prior.cover({}, 'anything'), (0.0, 0))

    def test_row_for_a_tried_proposal(self):
        eng = self.engine()
        out = eng.tried('evict the oldest tile first and keep the frame time flat while the camera moves')
        lines = out.split('\n')
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r'^1\. DEC-002 eviction policy: .+ \| 2026-02-01 \| current \| decision \| '
                                   r'notes/decisions/DEC-002-eviction-policy\.md:\d+-\d+$')
        self.assertEqual(lines[1], 'Evidence only, it decides nothing: read the cited lines. '
                                   'words only: no models configured; word lanes: notes, documents')

    def test_row_text_is_cut(self):
        eng = self.engine()
        line = eng.tried('evict the oldest tile first and keep the frame time flat while the camera moves').split('\n')[0]
        what = line.split(': ', 1)[1].rsplit(' | ', 4)[0]
        self.assertLessEqual(len(what), 180)

    def test_history_note_that_reports_an_outcome_counts(self):
        eng = self.engine()
        out = eng.tried('render the scene in one pass without a depth prepass to cut overdraw')
        self.assertIn('notes/archive/old-frame-notes.md:1-4', out)
        self.assertIn('| historical-record | history |', out)

    def test_plain_note_without_outcome_words_is_skipped(self):
        eng = self.engine()
        out = eng.tried('the frame graph lists the render passes and the resources each pass reads and writes')
        self.assertTrue(out.startswith('nothing certain.'))
        self.assertNotIn('1. Frame graph', out)

    def test_nothing_certain_without_closest_rows(self):
        eng = self.engine()
        out = eng.tried('orbital telescope calibration with solar panels')
        self.assertEqual(out, 'nothing certain. No row holds enough of this proposal. '
                              'words only: no models configured; word lanes: notes, documents')

    def test_nothing_certain_with_closest_rows(self):
        eng = self.engine()
        lines = eng.tried('evict tiles by random sampling to flatten latency').split('\n')
        self.assertTrue(lines[0].startswith('nothing certain. No row holds enough of this proposal. The nearest '
                                            'decision or result rows follow'))
        near = [ln for ln in lines if ln.startswith('closest, not a match: ')]
        self.assertTrue(0 < len(near) <= lane_prior.CLOSEST)
        self.assertIn('DEC-002 eviction policy', near[0])
        self.assertEqual(lines[-1], 'words only: no models configured; word lanes: notes, documents')

    def test_empty_and_long_proposals(self):
        eng = self.engine()
        self.assertTrue(eng.tried('').startswith('nothing certain.'))
        long = 'evict the oldest tile first ' * 100
        self.assertIsInstance(eng.tried(long), str)

    def test_changed_file_is_marked(self):
        eng = self.engine()
        with open(self.cfg.abs_path('notes/decisions/DEC-002-eviction-policy.md'), 'a', encoding='utf-8') as f:
            f.write('\nLater remark.\n')
        out = eng.tried('evict the oldest tile first and keep the frame time flat while the camera moves')
        self.assertIn(' | ' + query.CHANGED, out.split('\n')[0])

    def test_at_most_five_rows(self):
        # enough passages that the shared words count as rare
        os.remove(self.db)
        tq.build_db(self.cfg, self.db, filler=130)
        c = sqlite3.connect(self.db)
        did = c.execute('SELECT MAX(id) FROM docs').fetchone()[0]
        cid = c.execute('SELECT MAX(id) FROM chunks').fetchone()[0]
        for i in range(8):
            did += 1
            cid += 1
            body = 'Decision: the team decided to evict the oldest tile first in variant %d and measured results.' % i
            c.execute('INSERT INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (did, 'notes/decisions/extra%d.md' % i, 'notes', 'decisions/extra%d.md' % i, 'x', '.md',
                       'Extra %d' % i, None, None, 'decision', 'current', 'front-matter', '2026-02-0%d' % (i + 1),
                       0.0, 0, '', 1, 'line', '[]'))
            c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)', (cid, did, 0, 1, 1, '', body, 5000 + i, 0))
            c.execute('INSERT INTO fts(rowid,title,heading,path,text,idents) VALUES(?,?,?,?,?,?)',
                      (cid, 'Extra %d' % i, '', 'notes decisions extra%d md' % i, body, ''))
        c.commit()
        c.close()
        eng2 = query.Engine(self.cfg, 'orchestrator', db_path=self.db)
        self.addCleanup(eng2.close)
        out = eng2.tried('decided to evict the oldest tile first measured results')
        rows = [ln for ln in out.split('\n') if re.match(r'^\d+\. ', ln)]
        self.assertEqual(len(rows), 5)

    def test_lane_line_follows_the_search(self):
        eng = self.engine()
        out = eng.tried('orbital telescope calibration with solar panels')
        self.assertTrue(out.endswith(query.lane_line(eng.search('orbital telescope calibration'))))


class TestLocate(tq.Base):
    def test_parse(self):
        f = lane_locate.parse('tile cache ext:py,sh folder:src in:lib root:Code class:Code size:>2k n:5 symbol:evict')
        self.assertEqual(f['terms'], ['tile', 'cache'])
        self.assertEqual(f['ext'], ['.py', '.sh'])
        self.assertEqual(f['folder'], ['src', 'lib'])
        self.assertEqual(f['root'], 'code')
        self.assertEqual(f['cls'], 'code')
        self.assertEqual(f['smin'], 2048)
        self.assertIsNone(f['smax'])
        self.assertEqual(f['n'], 5)
        self.assertEqual(f['symbol'], 'evict')

    def test_parse_sizes_and_unknown_filters(self):
        self.assertEqual(lane_locate.parse('size:>1mb')['smin'], 1024 ** 2)
        self.assertEqual(lane_locate.parse('size:<3g')['smax'], 3 * 1024 ** 3)
        self.assertEqual(lane_locate.parse('size:500')['smin'], 500)
        self.assertEqual(lane_locate.parse('size:big')['smin'], None)
        f = lane_locate.parse('place:mac a:b "quoted words" n:x n:999')
        # a known filter with a bad value is dropped, an unknown filter word stays a free word
        self.assertEqual(f['terms'], ['place:mac', 'a:b', 'quoted words'])
        self.assertEqual(f['n'], 200)
        self.assertEqual(lane_locate.parse('')['terms'], [])
        self.assertEqual(lane_locate.parse(None)['terms'], [])

    def test_class_of(self):
        for ext, cls in (('.md', 'note'), ('.TXT', 'note'), ('.py', 'code'), ('.sh', 'code'), ('.swift', 'code'),
                         ('.yaml', 'code'), ('.pdf', 'document'), ('.docx', 'document'), ('.png', 'image'),
                         ('.json', 'data'), ('.csv', 'data'), ('.xyz', 'other'), ('', 'other')):
            self.assertEqual(lane_locate.class_of(ext), cls, ext)

    def test_name_search_ranks_the_file_first(self):
        eng = self.engine()
        r = eng.locate('tile cache')
        self.assertEqual(r['files'][0]['path'], 'code/tile_cache.py')
        self.assertEqual(r['matched_by'], 'all words')
        self.assertGreaterEqual(r['total'], 1)
        row = r['files'][0]
        self.assertEqual(set(row), {'path', 'size', 'root', 'class', 'sha', 'kind', 'status', 'date'})
        self.assertEqual((row['root'], row['class'], row['kind']), ('code', 'code', 'code'))
        self.assertEqual(set(r), {'files', 'total', 'matched_by', 'by_folder', 'symbols', 'rule'})
        self.assertEqual(r['rule'], lane_locate.RULE)

    def test_exact_file_name_beats_other_matches(self):
        eng = self.engine()
        r = eng.locate('build.sh')
        self.assertEqual(r['files'][0]['path'], 'code/build.sh')
        r = eng.locate('plan.md')
        self.assertEqual(r['files'][0]['path'], 'notes/plan.md')

    def test_live_above_history_and_short_above_long(self):
        eng = self.engine()
        r = eng.locate('plan')
        paths = [x['path'] for x in r['files']]
        self.assertEqual(paths.index('notes/plan.md') < paths.index('notes/archive/plan.md'), True)
        r = eng.locate('eviction policy')
        paths = [x['path'] for x in r['files']]
        # superseded one stands below the current one
        self.assertLess(paths.index('notes/decisions/DEC-002-eviction-policy.md'),
                        paths.index('notes/decisions/DEC-001-eviction-policy.md'))

    def test_any_word_fallback(self):
        eng = self.engine()
        r = eng.locate('tile zzzword')
        self.assertEqual(r['matched_by'], 'any word')
        self.assertEqual(r['files'][0]['path'], 'code/tile_cache.py')

    def test_filters(self):
        eng = self.engine()
        r = eng.locate('ext:py')
        self.assertEqual([x['path'] for x in r['files']], ['code/tile_cache.py'])
        self.assertEqual(r['matched_by'], 'filters only')
        r = eng.locate('root:code')
        self.assertEqual({x['root'] for x in r['files']}, {'code'})
        self.assertEqual(r['total'], 2)
        r = eng.locate('class:data')
        self.assertEqual([x['path'] for x in r['files']], ['docs/measurements.csv'])
        r = eng.locate('cache class:code')
        self.assertTrue(r['files'])
        self.assertTrue(all(x['class'] == 'code' for x in r['files']))
        r = eng.locate('cache root:notes')
        self.assertTrue(all(x['root'] == 'notes' for x in r['files']))
        r = eng.locate('folder:decisions')
        self.assertEqual(len(r['files']), 2)
        r = eng.locate('size:>3000')
        self.assertIn('docs/measurements.csv', [x['path'] for x in r['files']])
        r = eng.locate('size:<200 ext:md')
        self.assertTrue(all(x['size'] <= 200 for x in r['files']))
        r = eng.locate('ext:md n:2')
        self.assertEqual(len(r['files']), 2)
        self.assertGreater(r['total'], 2)

    def test_a_name_only_file_is_listed(self):
        eng = self.engine()
        r = eng.locate('measurements')
        self.assertEqual(r['files'][0]['path'], 'docs/measurements.csv')
        self.assertEqual(r['files'][0]['class'], 'data')
        self.assertIsNone(r['files'][0]['kind'])
        self.assertEqual(r['files'][0]['sha'], '')

    def test_by_folder(self):
        eng = self.engine()
        r = eng.locate('ext:md')
        top = {x['folder']: x['files'] for x in r['by_folder']}
        self.assertEqual(top['notes/decisions'], 2)
        self.assertTrue(all(set(x) == {'folder', 'files', 'bytes'} for x in r['by_folder']))

    def test_name_parts_are_searchable(self):
        eng = self.engine()
        r = eng.locate('TileCache')
        self.assertEqual(r['files'][0]['path'], 'code/tile_cache.py')
        r = eng.locate('cache study')
        self.assertEqual(r['files'][0]['path'], 'docs/cache-study.pdf')

    def test_no_match_and_odd_input(self):
        eng = self.engine()
        r = eng.locate('zzzqqq')
        self.assertEqual((r['files'], r['total']), ([], 0))
        self.assertEqual(eng.locate('')['files'], [])
        self.assertEqual(eng.locate('"')['files'], [])
        self.assertEqual(eng.locate('***')['files'], [])
        self.assertEqual(eng.locate('tile" OR "')['matched_by'] in ('all words', 'any word', None), True)

    def test_symbol_lookup(self):
        eng = self.engine()
        r = eng.locate('symbol:evict_oldest')
        self.assertEqual(r['files'], [])
        self.assertEqual(r['symbols'][0]['name'], 'evict_oldest')
        self.assertEqual(r['symbols'][0]['path'], 'code/tile_cache.py')
        r = eng.locate('symbol:bundle_assets')
        self.assertEqual(r['symbols'][0]['path'], 'code/build.sh')
        # a query that looks like a name also lists the definition
        r = eng.locate('evict_oldest')
        self.assertEqual(r['symbols'][0]['name'], 'evict_oldest')

    def test_every_path_is_a_display_path(self):
        eng = self.engine()
        text_out = repr([eng.locate(q) for q in ('tile', 'ext:md', 'symbol:TileCache', 'root:code')])
        self.assertNotIn(self.home, text_out)
        self.assertNotIn("'/", text_out)

    def test_no_names_table(self):
        c = sqlite3.connect(':memory:')
        self.addCleanup(c.close)
        r = lane_locate.locate(c, 'x')
        self.assertEqual(r['files'], [])
        self.assertEqual(r['rule'], lane_locate.RULE)
        self.assertFalse(lane_locate.has_names(c))


class TestFillProfile(unittest.TestCase):
    def setUp(self):
        self.p = sqlite3.connect(':memory:')
        self.p.executescript(schema.PROFILE_DDL)
        self.m = sqlite3.connect(':memory:')
        self.m.executescript(schema.MASTER_DDL)
        self.addCleanup(self.p.close)
        self.addCleanup(self.m.close)

    def doc(self, path, kind='note', status='unknown', date='2026-01-01', size=10, sha='abc'):
        root, _, rel = path.partition('/')
        self.p.execute('INSERT INTO docs(path,root,rel,name,ext,title,kind,status,date,mtime,size,sha256) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (path, root, rel, os.path.basename(path), os.path.splitext(path)[1], 't', kind, status, date,
                        5.0, size, sha))

    def test_names_from_master_and_docs(self):
        self.doc('notes/a/tileCache.md', 'decision', 'current', '2026-02-02', 40, 'h1')
        self.m.execute("INSERT INTO names VALUES('notes','a/tileCache.md',41,6.0,'note')")
        self.m.execute("INSERT INTO names VALUES('notes','a/data.json',900,7.0,'data')")
        self.m.execute("INSERT INTO names VALUES('other','x.md',5,1.0,'note')")
        self.m.execute("INSERT INTO files(root,rel,kind,status,date,sha256) VALUES('notes','a/data.json','note','unknown','2026-01-05','h2')")
        r = lane_locate.fill_profile(self.p, self.m, ['notes'])
        self.assertEqual(r, {'names': 2, 'folders': 2})
        rows = {x[0]: x for x in self.p.execute('SELECT path,name,ext,folder,size,sha,root,class,mtime,kind,status,date '
                                                'FROM names')}
        self.assertEqual(set(rows), {'notes/a/tileCache.md', 'notes/a/data.json'})
        a = rows['notes/a/tileCache.md']
        # size and mtime from the master, kind status date and hash from the profile docs
        self.assertEqual((a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11]),
                         ('tileCache.md', '.md', 'notes/a', 41, 'h1', 'notes', 'note', 6.0, 'decision', 'current',
                          '2026-02-02'))
        b = rows['notes/a/data.json']
        self.assertEqual((b[7], b[5], b[11]), ('data', 'h2', '2026-01-05'))
        counts = dict((k, (f, y)) for k, f, y in self.p.execute('SELECT folder, files, bytes FROM folder_counts'))
        self.assertEqual(counts['notes'], (2, 941))
        self.assertEqual(counts['notes/a'], (2, 941))
        hits = self.p.execute("SELECT n.path FROM namefts f JOIN names n ON n.id = f.rowid WHERE namefts MATCH 'cache'"
                              ).fetchall()
        self.assertEqual(hits, [('notes/a/tileCache.md',)])
        self.assertEqual(lane_locate.locate(self.p, 'tile cache')['files'][0]['path'], 'notes/a/tileCache.md')

    def test_docs_without_a_master_row_still_get_a_name(self):
        self.doc('notes/b.md')
        r = lane_locate.fill_profile(self.p, None, ['notes'])
        self.assertEqual(r['names'], 1)
        self.assertEqual(self.p.execute('SELECT class, size FROM names').fetchone(), ('note', 10))

    def test_empty(self):
        r = lane_locate.fill_profile(self.p, self.m, [])
        self.assertEqual(r, {'names': 0, 'folders': 0})
        self.assertTrue(lane_locate.has_names(self.p))

    def test_running_it_twice_does_not_break(self):
        self.doc('notes/b.md')
        lane_locate.fill_profile(self.p, None, ['notes'])
        self.p.execute('DELETE FROM names')
        self.p.execute("INSERT INTO namefts(namefts) VALUES('delete-all')")
        lane_locate.fill_profile(self.p, None, ['notes'])
        self.assertEqual(self.p.execute('SELECT COUNT(*) FROM names').fetchone()[0], 1)


class TestHygiene(unittest.TestCase):
    def test_no_dash_characters_in_my_tests(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for n in ('test_lanes.py', 'test_query.py'):
            with open(os.path.join(here, n), encoding='utf-8') as f:
                body = f.read()
            self.assertNotIn(chr(0x2014), body, n)
            self.assertNotIn(chr(0x2013), body, n)


if __name__ == '__main__':
    unittest.main()
