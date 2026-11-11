"""Build: chunking rules, incremental master update, secret skips, profile databases. Offline."""
import json
import os
import sqlite3
import sys
import time
import types
import unittest
import zipfile
from unittest import mock

from leks import build, chunking, lane_code, schema
from tests import helpers


def quiet(*a, **k):
    pass


def put(cfg, root, rel, text, binary=False):
    p = os.path.join(cfg.root(root).path, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb' if binary else 'w', **({} if binary else {'encoding': 'utf-8'})) as f:
        f.write(text)
    return p


def read_bytes(p):
    with open(p, 'rb') as f:
        return f.read()


def master_rows(cfg, sql, args=()):
    c = sqlite3.connect(cfg.master_path())
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def profile_rows(cfg, profile, sql, args=()):
    c = schema.open_ro(cfg.profile_db(profile))
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.home, self.cfg = helpers.make_home()

    def tearDown(self):
        helpers.cleanup(self.home)

    def update(self, **kw):
        return build.update(self.cfg, log=quiet, **kw)


class ChunkRuleTests(unittest.TestCase):
    classify = {
        'state': ['**/STATE.md'], 'rule': ['**/CLAUDE.md', '.claude/**'],
        'decision': ['**/decisions/**', '**/DEC-*'], 'result': ['**/results/**'],
        'history': ['**/archive/**', '**/*_HISTORY*'],
    }

    def kind(self, rel):
        return chunking.kind_of(None, rel, os.path.splitext(rel)[1], self.classify)

    def test_kind_order_and_extensions(self):
        self.assertEqual(self.kind('a/STATE.md'), 'state')
        self.assertEqual(self.kind('STATE.md'), 'state')
        self.assertEqual(self.kind('CLAUDE.md'), 'rule')
        self.assertEqual(self.kind('.claude/rules/x.md'), 'rule')
        self.assertEqual(self.kind('decisions/DEC-004.md'), 'decision')
        self.assertEqual(self.kind('results/run1.md'), 'result')
        self.assertEqual(self.kind('src/a.py'), 'code')
        self.assertEqual(self.kind('conf/a.yaml'), 'code')
        self.assertEqual(self.kind('docs/a.pdf'), 'document')
        self.assertEqual(self.kind('docs/a.docx'), 'document')
        self.assertEqual(self.kind('docs/a.png'), 'image')
        self.assertEqual(self.kind('notes/a.md'), 'note')
        # state wins over decision, a history glob leaves the kind alone
        self.assertEqual(self.kind('decisions/STATE.md'), 'state')
        self.assertEqual(self.kind('archive/decisions/x.md'), 'decision')
        self.assertEqual(self.kind('archive/x.md'), 'note')

    def status(self, rel, fm=None, kind=None):
        kind = kind or self.kind(rel)
        return chunking.status_of(kind, rel, fm or {}, self.classify)

    def test_status_rules(self):
        self.assertEqual(self.status('archive/a.md'), ('historical-record', 'path'))
        self.assertEqual(self.status('archive/STATE.md'), ('historical-record', 'path'))
        self.assertEqual(self.status('STATE.md'), ('current', 'entry'))
        self.assertEqual(self.status('a.md', {'frozen': 'true'}), ('record', 'frozen'))
        self.assertEqual(self.status('a.md', {'status': 'Locked'}), ('record', 'frozen'))
        self.assertEqual(self.status('a.md', {'status': 'frozen'}), ('record', 'frozen'))
        for word, want in (('accepted', 'current'), ('Active', 'current'), ('in progress', 'current'),
                           ('"approved"', 'current'), ('done', 'record'), ('final', 'record'),
                           ('released', 'record'), ('superseded', 'superseded'),
                           ('deprecated', 'superseded'), ('rejected', 'superseded'),
                           ('stale', 'stale'), ('outdated', 'stale'), ('accepted (2026-01-02)', 'current')):
            self.assertEqual(self.status('a.md', {'status': word}), (want, 'front-matter'), word)
        self.assertEqual(self.status('a.md', {'status': 'wip maybe'}), ('unknown', 'front-matter-unchecked'))
        self.assertEqual(self.status('a.md'), ('unknown', 'rule'))
        self.assertEqual(self.status('a.md', {'status': ''}), ('unknown', 'rule'))

    def test_front_title_date(self):
        lines = ['---', 'Title: "Cache plan"', 'status: done', 'updated: 2026-02-03', '---', 'body']
        fm = chunking.front(lines)
        self.assertEqual(fm['title'], '"Cache plan"')
        self.assertEqual(fm['status'], 'done')
        self.assertEqual(chunking.title_of(lines, fm, 'x.md'), 'Cache plan')
        self.assertEqual(chunking.title_of(['# Real title'] + lines, {}, 'x.md'), 'Real title')
        self.assertEqual(chunking.title_of(['nothing'], {}, 'x.md'), 'x.md')
        self.assertEqual(chunking.date_of('a.md', fm, 0), '2026-02-03')
        self.assertEqual(chunking.date_of('2025-12-01-plan.md', {}, 0), '2025-12-01')
        self.assertEqual(chunking.date_of('plan.md', {}, 86400 * 365), '1971-01-01')
        self.assertEqual(chunking.front(['no front matter']), {})

    def test_chunk_line_numbers_and_headings(self):
        lines = ['intro line', '', '# One', 'alpha', 'beta', '## Two', 'gamma'] + ['x' * 50] * 30
        ch = chunking.chunks_of(lines, False, 1000)
        self.assertEqual(ch[0][:3], (1, 2, ''))
        self.assertEqual(ch[1][:3], (3, 5, 'One'))
        self.assertEqual(ch[2][2], 'Two')
        for a, b, _h, t in ch:
            self.assertEqual('\n'.join(lines[a - 1:b]).strip(), t)
        # size split keeps the heading and the file's own numbering
        self.assertTrue(all(c[2] == 'Two' for c in ch[2:]))
        self.assertEqual(ch[-1][1], len(lines))

    def test_front_matter_rides_with_the_first_heading(self):
        lines = ['---', 'title: A note', 'status: current', '---', '# Tiles', 'body text here']
        ch = chunking.chunks_of(lines, False, 1000)
        self.assertEqual(len(ch), 1)
        self.assertEqual(ch[0][:3], (1, 6, 'Tiles'))
        # text before the first heading, front matter or not, still gets its own passage
        lines = ['---', 'title: A', '---', 'intro words', '# Tiles', 'body']
        ch = chunking.chunks_of(lines, False, 1000)
        self.assertEqual([c[:3] for c in ch], [(1, 4, ''), (5, 6, 'Tiles')])

    def test_long_line_pieces_cite_the_same_line(self):
        lines = ['# H', 'y' * 2500, 'tail']
        ch = chunking.chunks_of(lines, False, 1000)
        pieces = [c for c in ch if c[0] == 2 and c[1] == 2]
        self.assertEqual(len(pieces), 3)
        self.assertTrue(all(len(c[3]) <= 1000 for c in pieces))
        self.assertEqual(ch[-1][:2], (3, 3))

    def test_code_split_on_definitions(self):
        body = ['    x = %d' % i for i in range(60)]
        lines = ['def first():'] + body + ['def second():', '    pass']
        ch = chunking.chunks_of(lines, True, 1000)
        heads = [c[2] for c in ch]
        self.assertIn('def second():', heads)

    def test_chunk_chars_is_used(self):
        lines = ['word ' * 10] * 20
        small = chunking.chunks_of(lines, False, 120)
        big = chunking.chunks_of(lines, False, 1000)
        self.assertGreater(len(small), len(big))

    def test_units_chunks(self):
        units = [(1, '', 'page one text'), (2, '', 'page two text'), (3, 'References', '[1] a ref')]
        pages = chunking.chunks_of_units(units, False, 1000)
        self.assertEqual([(c[0], c[1], c[2]) for c in pages], [(1, 1, ''), (2, 2, ''), (3, 3, 'References')])
        paras = chunking.chunks_of_units([(1, 'A', 'x'), (2, 'A', 'y'), (3, 'B', 'z')], True, 1000)
        self.assertEqual([(c[0], c[1], c[2]) for c in paras], [(1, 2, 'A'), (3, 3, 'B')])
        long = chunking.chunks_of_units([(4, '', 'q' * 2500)], False, 1000)
        self.assertEqual({(c[0], c[1]) for c in long}, {(4, 4)})
        self.assertEqual(chunking.chunks_of_units([(1, '', '   ')], False, 1000), [])

    def test_strip_html(self):
        t = chunking.strip_html('<p>Tiles &amp; caches</p>\n<script>\nvar a;\n</script>\n<b>done</b>')
        self.assertIn('Tiles & caches', t)
        self.assertNotIn('var a', t)
        self.assertEqual(t.count('\n'), 4)


class SuccessionTests(unittest.TestCase):
    def test_note_says_so(self):
        rows = [(1, 'notes/a/old-plan.md', 'old-plan.md', '2026-01-01', 1.0, 'Superseded by [[new-plan]]'),
                (2, 'notes/a/new-plan.md', 'new-plan.md', '2026-02-01', 2.0, 'the plan'),
                (3, 'notes/b/other.md', 'other.md', '2026-02-01', 2.0, 'replaced by [[missing-note]]'),
                (4, 'notes/b/third.md', 'third.md', '2026-02-01', 2.0, 'superseded_by: new-plan.md\nbody')]
        # the pointer sits in the head text, a front matter line is read there too
        ed = chunking.succession(rows)
        self.assertEqual(ed[1], (2, 'the note says so'))
        self.assertNotIn(3, ed)
        self.assertEqual(ed[4], (2, 'the note says so'))

    def test_wikilink_forms(self):
        rows = [(1, 'n/a-b.md', 'a-b.md', '', 0, 'moved to: [[c-d|other label]]'),
                (2, 'n/c-d.md', 'c-d.md', '', 0, ''),
                (3, 'n/e-f.md', 'e-f.md', '', 0, 'see instead `[[c-d]]`')]
        ed = chunking.succession(rows)
        self.assertEqual(ed[1][0], 2)
        self.assertEqual(ed[3][0], 2)

    def test_version_series(self):
        rows = [(1, 'notes/render-plan-v1.md', 'render-plan-v1.md', '2026-01-01', 1.0, ''),
                (2, 'notes/render-plan-v3.md', 'render-plan-v3.md', '2026-03-01', 3.0, ''),
                (3, 'notes/render-plan-v2.md', 'render-plan-v2.md', '2026-02-01', 2.0, ''),
                (4, 'notes/x/render-plan-v9.md', 'render-plan-v9.md', '2026-03-01', 3.0, ''),
                (5, 'notes/a-v1.md', 'a-v1.md', '', 0, ''), (6, 'notes/a-v2.md', 'a-v2.md', '', 0, '')]
        ed = chunking.succession(rows)
        self.assertEqual(ed[1], (2, 'version number in the name'))
        self.assertEqual(ed[3], (2, 'version number in the name'))
        self.assertNotIn(2, ed)
        # another folder is another series, and a stem with fewer than two words is no series
        self.assertNotIn(4, ed)
        self.assertNotIn(5, ed)

    def test_dates_alone_prove_nothing(self):
        rows = [(1, 'n/alpha-notes.md', 'alpha-notes.md', '2026-01-01', 1.0, ''),
                (2, 'n/alpha-more.md', 'alpha-more.md', '2026-02-01', 2.0, '')]
        self.assertEqual(chunking.succession(rows), {})


class UpdateTests(Base):
    def test_first_run_counts(self):
        r = self.update()
        self.assertEqual(set(r), {'roots', 'seen', 'indexed', 'unchanged', 'removed', 'skipped', 'errors',
                                  'ocr', 'secs'})
        self.assertEqual(r['roots'], 3)
        self.assertEqual(r['indexed'], 9)        # 6 notes, 2 code files, 1 text document
        self.assertEqual(r['seen'], 9)
        self.assertEqual(r['unchanged'], 0)
        self.assertEqual(r['errors'], 0)
        kinds = dict(master_rows(self.cfg, 'SELECT rel,kind FROM files'))
        self.assertEqual(kinds['STATE.md'], 'state')
        self.assertEqual(kinds['decisions/DEC-001-eviction-policy.md'], 'decision')
        self.assertEqual(kinds['tile_cache.py'], 'code')
        self.assertEqual(kinds['build.sh'], 'code')
        status = dict(master_rows(self.cfg, 'SELECT rel,status FROM files'))
        self.assertEqual(status['STATE.md'], 'current')
        self.assertEqual(status['archive/old-frame-notes.md'], 'historical-record')
        src = dict(master_rows(self.cfg, 'SELECT rel,status_src FROM files'))
        self.assertEqual(src['archive/old-frame-notes.md'], 'path')
        self.assertEqual(src['STATE.md'], 'entry')
        meta = dict(master_rows(self.cfg, 'SELECT k,v FROM meta'))
        self.assertEqual(meta['schema'], '3')
        self.assertTrue(float(meta['updated']) > 0)

    def test_stats_are_consistent(self):
        # the first test above hard-codes counts, this one checks them against the database
        r = self.update()
        n = master_rows(self.cfg, 'SELECT COUNT(*) FROM files')[0][0]
        self.assertEqual(r['indexed'], n)
        self.assertEqual(r['seen'], master_rows(self.cfg, 'SELECT COUNT(*) FROM names')[0][0])

    def test_unchanged_files_are_not_read_again(self):
        first = self.update()
        with mock.patch.object(build, 'index_file', wraps=build.index_file) as m:
            r = self.update()
        self.assertEqual(m.call_count, 0)
        self.assertEqual(r['indexed'], 0)
        self.assertEqual(r['unchanged'], first['indexed'])

    def test_changed_file_is_rechunked(self):
        self.update()
        put(self.cfg, 'notes', 'frame_graph.md', '# Frame graph\n\nNow with a fresh paragraph about mesh shaders.\n')
        with mock.patch.object(build, 'index_file', wraps=build.index_file) as m:
            r = self.update()
        self.assertEqual(m.call_count, 1)
        self.assertEqual(r['indexed'], 1)
        text = ' '.join(t for (t,) in master_rows(self.cfg, 'SELECT text FROM chunks WHERE rel=?', ('frame_graph.md',)))
        self.assertIn('mesh shaders', text)
        self.assertNotIn('Transient', text)

    def test_same_bytes_new_mtime_refreshes_only_the_stamp(self):
        self.update()
        p = os.path.join(self.cfg.root('notes').path, 'build_cache.md')
        t = time.time() + 500
        os.utime(p, (t, t))
        with mock.patch.object(build, 'index_file', wraps=build.index_file) as m:
            r = self.update()
        self.assertEqual(m.call_count, 0)
        self.assertEqual(r['indexed'], 0)
        mt = master_rows(self.cfg, 'SELECT mtime FROM files WHERE rel=?', ('build_cache.md',))[0][0]
        self.assertAlmostEqual(mt, t, places=2)
        # and the next run sees it as unchanged by size and time
        with mock.patch.object(build, 'index_file', wraps=build.index_file) as m:
            self.update()
        self.assertEqual(m.call_count, 0)

    def test_deleted_file_is_gone_everywhere(self):
        self.update()
        os.remove(os.path.join(self.cfg.root('notes').path, 'build_cache.md'))
        r = self.update()
        self.assertEqual(r['removed'], 1)
        for t in ('files', 'chunks', 'symbols', 'names'):
            self.assertEqual(master_rows(self.cfg, 'SELECT COUNT(*) FROM %s WHERE rel=?' % t,
                                         ('build_cache.md',))[0][0], 0)

    def test_file_that_becomes_unwanted_leaves_the_index_but_stays_a_name(self):
        self.update()
        # frozen dataclass, so swap the root in the list
        old = self.cfg.roots[0]
        import dataclasses
        self.cfg.roots[0] = dataclasses.replace(old, include=('**/STATE.md',))
        r = self.update()
        self.assertGreaterEqual(r['removed'], 4)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM files WHERE root='notes'")[0][0], 1)
        self.assertGreater(master_rows(self.cfg, "SELECT COUNT(*) FROM names WHERE root='notes'")[0][0], 5)

    def test_vanished_root_keeps_its_rows(self):
        self.update()
        before = master_rows(self.cfg, "SELECT COUNT(*) FROM files WHERE root='code'")[0][0]
        d = self.cfg.root('code').path
        os.rename(d, d + '_away')
        r = self.update()
        self.assertEqual(r['removed'], 0)
        self.assertEqual(r['roots'], 2)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM files WHERE root='code'")[0][0], before)
        os.rename(d + '_away', d)
        self.assertEqual(self.update()['removed'], 0)

    def test_root_removed_from_config_is_dropped(self):
        self.update()
        self.cfg.roots[:] = [r for r in self.cfg.roots if r.name != 'code']
        r = self.update()
        self.assertEqual(r['removed'], 2)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM symbols WHERE root='code'")[0][0], 0)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM names WHERE root='code'")[0][0], 0)

    def test_names_cover_unindexed_files(self):
        put(self.cfg, 'code', 'data/points.csv', 'a,b\n1,2\n')
        put(self.cfg, 'code', 'notes.md', '# not in a code root include list\n')
        self.update()
        names = {rel: cls for rel, cls in master_rows(self.cfg, "SELECT rel,class FROM names WHERE root='code'")}
        self.assertIn('data/points.csv', names)
        self.assertEqual(names['tile_cache.py'], 'code')
        indexed = [r for (r,) in master_rows(self.cfg, "SELECT rel FROM files WHERE root='code'")]
        self.assertNotIn('data/points.csv', indexed)
        self.assertNotIn('notes.md', indexed)
        n = master_rows(self.cfg, "SELECT size FROM names WHERE rel='data/points.csv'")[0][0]
        self.assertEqual(n, 8)

    def test_walk_skips_junk_secrets_and_links(self):
        for rel in ('node_modules/x.md', '.git/y.md', 'secrets/z.md', '.env', 'a/.DS_Store', 'cred.pem',
                    'build/o.md', 'drafts/q.md'):
            put(self.cfg, 'notes', rel, 'text\n')
        base = self.cfg.root('notes').path
        os.symlink(os.path.join(base, 'frame_graph.md'), os.path.join(base, 'link.md'))
        os.symlink(base, os.path.join(base, 'loop'))
        import dataclasses
        self.cfg.roots[0] = dataclasses.replace(self.cfg.roots[0], exclude=('drafts/**',))
        self.update()
        rels = {r for (r,) in master_rows(self.cfg, "SELECT rel FROM names WHERE root='notes'")}
        for bad in ('node_modules/x.md', '.git/y.md', 'secrets/z.md', '.env', 'a/.DS_Store', 'cred.pem',
                    'build/o.md', 'drafts/q.md', 'link.md', 'loop/frame_graph.md'):
            self.assertNotIn(bad, rels)
        self.assertIn('frame_graph.md', rels)

    def test_size_limit_and_binary(self):
        self.cfg.limits['max_file_bytes'] = 300
        put(self.cfg, 'notes', 'big.md', '# big\n' + 'line of words\n' * 100)
        put(self.cfg, 'notes', 'blob.md', 'abc\0def\n' * 5)
        r = self.update()
        why = dict(master_rows(self.cfg, 'SELECT rel,why FROM skipped'))
        self.assertEqual(why['big.md'], 'over size limit')
        self.assertEqual(why['blob.md'], 'binary content')
        self.assertGreaterEqual(r['skipped'], 2)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM files WHERE rel='big.md'")[0][0], 0)

    def test_json_in_a_code_root_has_its_own_cap(self):
        put(self.cfg, 'code', 'small.json', '{"a": 1}\n')
        put(self.cfg, 'code', 'huge.json', '{"a": "' + 'x' * 250000 + '"}\n')
        self.update()
        got = {r for (r,) in master_rows(self.cfg, "SELECT rel FROM files WHERE root='code'")}
        self.assertIn('small.json', got)
        self.assertNotIn('huge.json', got)

    def test_html_is_stripped(self):
        put(self.cfg, 'notes', 'page.html', '<html><body><h1>Tile report</h1><p>Cache &amp; LOD</p>'
            '<script>var secret_free = 1;</script></body></html>\n')
        self.update()
        text = ' '.join(t for (t,) in master_rows(self.cfg, "SELECT text FROM chunks WHERE rel='page.html'"))
        self.assertIn('Cache & LOD', text)
        self.assertNotIn('<p>', text)
        self.assertNotIn('var secret_free', text)

    def test_unchanged_skip_is_remembered(self):
        self.cfg.limits['max_file_bytes'] = 300
        put(self.cfg, 'notes', 'big.md', 'line of words\n' * 100)
        self.update()
        with mock.patch.object(build, 'index_file', wraps=build.index_file) as m:
            r = self.update()
        self.assertEqual(m.call_count, 0)
        self.assertGreaterEqual(r['skipped'], 1)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM skipped WHERE rel='big.md'")[0][0], 1)

    def test_config_change_that_matters_reindexes_everything(self):
        first = self.update()
        self.cfg.limits['chunk_chars'] = 400
        r = self.update()
        self.assertEqual(r['indexed'], first['indexed'])

    def test_symbols_for_python_and_shell(self):
        self.update()
        rows = master_rows(self.cfg, 'SELECT rel,name,kind,lang,line,parent FROM symbols')
        by = {(r[0], r[1]): r for r in rows}
        self.assertEqual(by[('tile_cache.py', 'TileCache')][2:4], ('class', 'python'))
        self.assertEqual(by[('tile_cache.py', 'evict_oldest')][2], 'method')
        self.assertEqual(by[('tile_cache.py', 'evict_oldest')][5], 'TileCache')
        self.assertEqual(by[('build.sh', 'bundle_assets')][2:4], ('function', 'shell'))

    def test_changed_code_replaces_its_symbols(self):
        self.update()
        put(self.cfg, 'code', 'tile_cache.py', 'def only_one():\n    return 1\n')
        self.update()
        names = [n for (n,) in master_rows(self.cfg, "SELECT name FROM symbols WHERE rel='tile_cache.py'")]
        self.assertEqual(names, ['only_one'])


class SecretTests(Base):
    def test_secret_patterns(self):
        pats = {
            'aws': 'key AKIA' + 'ABCDEFGHIJKLMNOP here',
            'gh': 'token gh' + 'p_' + 'a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8',
            'slack': 'xo' + 'xb-123456789012-abcdefghij',
            'sk': 'sk' + '-' + 'A' * 44,
            'pw': 'pass' + 'word = hunter2hunter2',
            'pem': '-----BEGIN RSA PRIV' + 'ATE KEY-----\nMIIB\n-----END',
        }
        for k, v in pats.items():
            self.assertTrue(build.has_secret(v), k)
        self.assertFalse(build.has_secret('the password field is documented in the build guide'))
        self.assertFalse(build.has_secret('sk-short'))

    def test_secret_file_is_skipped_and_not_indexed(self):
        put(self.cfg, 'notes', 'leaky.md', '# Notes\n\nthe key is AKIA' + 'ABCDEFGHIJKLMNOP for staging\n')
        put(self.cfg, 'code', 'cfg.py', 'PASS' + 'WORD = "hunter2hunter2"\ndef ok():\n    pass\n')
        r = self.update()
        why = dict(master_rows(self.cfg, 'SELECT rel,why FROM skipped'))
        self.assertEqual(why['leaky.md'], 'secret pattern')
        self.assertEqual(why['cfg.py'], 'secret pattern')
        for rel in ('leaky.md', 'cfg.py'):
            for t in ('files', 'chunks', 'symbols'):
                self.assertEqual(master_rows(self.cfg, 'SELECT COUNT(*) FROM %s WHERE rel=?' % t, (rel,))[0][0], 0)
        self.assertEqual(r['skipped'], 2)
        # nothing of the text reaches any column of the master
        blob = json.dumps(master_rows(self.cfg, 'SELECT * FROM chunks'))
        self.assertNotIn('AKIA', blob)

    def test_file_that_gains_a_secret_leaves_the_index(self):
        put(self.cfg, 'notes', 'later.md', '# Later\n\nclean text\n')
        self.update()
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM files WHERE rel='later.md'")[0][0], 1)
        put(self.cfg, 'notes', 'later.md', '# Later\n\nAKIA' + 'ABCDEFGHIJKLMNOP\n')
        r = self.update()
        self.assertEqual(r['removed'], 1)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM chunks WHERE rel='later.md'")[0][0], 0)
        put(self.cfg, 'notes', 'later.md', '# Later\n\nclean again and longer\n')
        self.update()
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM files WHERE rel='later.md'")[0][0], 1)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM skipped WHERE rel='later.md'")[0][0], 0)

    def test_index_file_reports_the_reason(self):
        root = self.cfg.root('notes')
        p = put(self.cfg, 'notes', 's.md', 'AKIA' + 'ABCDEFGHIJKLMNOP\n')
        res = build.index_file(self.cfg, root, 's.md', os.stat(p), read_bytes(p), mock.Mock())
        self.assertEqual(res, (None, 'secret pattern'))
        p = put(self.cfg, 'notes', 't.md', '# T\n\nplain words about tiles\n')
        doc, chunks, syms = build.index_file(self.cfg, root, 't.md', os.stat(p), read_bytes(p), mock.Mock())
        self.assertEqual(doc['title'], 'T')
        self.assertEqual((doc['root'], doc['rel'], doc['unit'], doc['units']), ('notes', 't.md', 'line', 3))
        self.assertEqual(len(doc['sha256']), 64)
        self.assertEqual(syms, [])
        self.assertEqual(chunks[0][1:3], (1, 3))


class LockTests(Base):
    def lock(self):
        return os.path.join(self.cfg.run_dir(), 'update.lock')

    def test_held_lock_skips(self):
        os.makedirs(self.lock())
        self.assertEqual(self.update(), {'skipped': 'locked'})
        self.assertTrue(os.path.isdir(self.lock()))
        self.assertFalse(os.path.exists(self.cfg.master_path()))

    def test_stale_lock_is_taken_and_released(self):
        os.makedirs(self.lock())
        old = time.time() - 2 * 3600
        os.utime(self.lock(), (old, old))
        r = self.update()
        self.assertGreater(r['indexed'], 0)
        self.assertFalse(os.path.exists(self.lock()))

    def test_lock_is_released_after_a_normal_run(self):
        self.update()
        self.assertFalse(os.path.exists(self.lock()))

    def test_caller_that_holds_the_lock(self):
        os.makedirs(self.lock())
        with mock.patch.dict(os.environ, {'LEKS_LOCK_HELD': '1'}):
            r = self.update()
        self.assertGreater(r['indexed'], 0)
        self.assertTrue(os.path.isdir(self.lock()))


class MaterializeTests(Base):
    def setUp(self):
        super().setUp()
        self.update()

    def mat(self, profile='orchestrator'):
        return build.materialize(self.cfg, profile, log=quiet)

    def test_counts_and_display_paths(self):
        r = self.mat()
        self.assertEqual(set(r), {'profile', 'docs', 'chunks', 'distinct_inputs', 'symbols', 'names', 'newer',
                                  'documents', 'secs'})
        self.assertEqual(r['profile'], 'orchestrator')
        self.assertEqual(r['docs'], 9)
        self.assertGreater(r['symbols'], 4)
        self.assertEqual(r['documents'], 0)
        paths = [p for (p,) in profile_rows(self.cfg, 'orchestrator', 'SELECT path FROM docs')]
        self.assertIn('notes/STATE.md', paths)
        self.assertIn('code/tile_cache.py', paths)
        self.assertIn('docs/release_checklist.txt', paths)
        self.assertFalse(os.path.exists(self.cfg.profile_db('orchestrator') + '.tmp'))

    def test_no_absolute_path_inside_the_database(self):
        self.mat()
        c = schema.open_ro(self.cfg.profile_db('orchestrator'))
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if name.endswith(('_data', '_idx', '_content', '_docsize', '_config')):
                continue
            for row in c.execute('SELECT * FROM "%s"' % name):
                for v in row:
                    if isinstance(v, str):
                        self.assertNotIn(self.home, v, name)
                        self.assertFalse(v.startswith('/tmp'), name)
        c.close()

    def test_profile_roots_decide_what_is_in(self):
        r = self.mat('architect')
        paths = [p for (p,) in profile_rows(self.cfg, 'architect', 'SELECT path FROM docs')]
        self.assertFalse(any(p.startswith('code/') for p in paths))
        self.assertEqual(r['symbols'], 0)
        meta = dict(profile_rows(self.cfg, 'architect', 'SELECT k,v FROM meta'))
        self.assertEqual(json.loads(meta['roots']), ['notes', 'docs'])
        self.assertEqual(meta['profile'], 'architect')
        self.assertEqual(meta['schema'], '3')
        self.assertGreater(float(meta['built']), 0)
        # the default profile sees every root
        self.mat('default')
        self.assertEqual(json.loads(dict(profile_rows(self.cfg, 'default', 'SELECT k,v FROM meta'))['roots']),
                         ['notes', 'code', 'docs'])

    def test_fts_tables(self):
        self.mat()
        rows = profile_rows(self.cfg, 'orchestrator',
                            "SELECT d.path FROM fts JOIN chunks c ON c.id=fts.rowid JOIN docs d ON d.id=c.doc "
                            "WHERE fts MATCH 'transient'")
        self.assertEqual([r[0] for r in rows], ['notes/frame_graph.md'])
        # identifier parts come from the idents column
        rows = profile_rows(self.cfg, 'orchestrator',
                            "SELECT d.path FROM fts JOIN chunks c ON c.id=fts.rowid JOIN docs d ON d.id=c.doc "
                            "WHERE fts MATCH 'idents:oldest'")
        self.assertIn('code/tile_cache.py', [r[0] for r in rows])
        # path words and prefix index
        rows = profile_rows(self.cfg, 'orchestrator', "SELECT COUNT(*) FROM fts WHERE fts MATCH 'path:frame'")
        self.assertGreater(rows[0][0], 0)
        rows = profile_rows(self.cfg, 'orchestrator', "SELECT COUNT(*) FROM fts WHERE fts MATCH 'evic*'")
        self.assertGreater(rows[0][0], 0)
        n_docs = profile_rows(self.cfg, 'orchestrator', 'SELECT COUNT(*) FROM docs')[0][0]
        self.assertEqual(profile_rows(self.cfg, 'orchestrator', 'SELECT COUNT(*) FROM docfts')[0][0], n_docs)
        self.assertEqual(profile_rows(self.cfg, 'orchestrator', 'SELECT COUNT(*) FROM dfts')[0][0], 0)
        rows = profile_rows(self.cfg, 'orchestrator',
                            "SELECT rowid FROM docfts WHERE docfts MATCH 'headings:\"Pass order\"'")
        self.assertEqual(len(rows), 1)

    def test_symbols_reach_the_profile(self):
        self.mat()
        c = schema.open_ro(self.cfg.profile_db('orchestrator'))
        hits = lane_code.locate_symbol(c, 'evict_oldest')
        self.assertEqual(hits[0]['path'], 'code/tile_cache.py')
        hits = lane_code.locate_symbol(c, 'bundle_assets')
        self.assertEqual(hits[0]['path'], 'code/build.sh')
        self.assertTrue(lane_code.symbol_lane(c, 'where is TileCache defined'))
        c.close()

    def test_vecmap_and_embedding_hash(self):
        self.mat()
        c = schema.open_ro(self.cfg.profile_db('orchestrator'))
        vecs = [v for (v,) in c.execute('SELECT vec FROM chunks')]
        mapped = dict(c.execute('SELECT vec, ehash FROM vecmap').fetchall())
        self.assertEqual(sorted(set(vecs)), list(range(len(mapped))))
        # first seen order: the first chunk gets row 0
        self.assertEqual(vecs[0], 0)
        m = sqlite3.connect(self.cfg.master_path())
        want = {}
        for _root, _rel, title, h, t, e in m.execute('SELECT c.root,c.rel,f.title,c.heading,c.text,c.ehash '
                                                   'FROM chunks c JOIN files f ON f.root=c.root AND f.rel=c.rel'):
            self.assertEqual(e, schema.ehash(schema.embed_input(title, h, t)))
            want[e] = 1
        self.assertEqual(set(mapped.values()), set(want))
        c.close()
        m.close()

    def test_shared_passage_gets_one_vector_row(self):
        shared = '# Same\n\n' + 'one passage that appears in two different files with the same title\n'
        put(self.cfg, 'notes', 'twin_a.md', shared + '## Tail\ntail a\n')
        put(self.cfg, 'notes', 'twin_b.md', shared + '## Tail\ntail b\n')
        self.update()
        r = self.mat()
        self.assertEqual(r['distinct_inputs'], r['chunks'] - 1)

    def test_dedupe_and_copies(self):
        body = '# Twin\n\nthe same bytes in three places about shader blobs and cache keys\n'
        put(self.cfg, 'notes', 'twin.md', body)
        put(self.cfg, 'notes', 'archive/twin.md', body)
        put(self.cfg, 'notes', 'sub/dir/twin_copy.md', body)
        self.update()
        self.mat()
        rows = profile_rows(self.cfg, 'orchestrator', "SELECT path, copies FROM docs WHERE title='Twin'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 'notes/twin.md')
        self.assertEqual(json.loads(rows[0][1]), ['notes/archive/twin.md', 'notes/sub/dir/twin_copy.md'])
        # the losing copies still exist as names
        n = profile_rows(self.cfg, 'orchestrator', "SELECT COUNT(*) FROM names WHERE name LIKE 'twin%'")
        if n:
            self.assertGreaterEqual(n[0][0], 1)

    def test_history_path_loses_the_dedupe_even_when_shorter(self):
        body = '# Dup\n\nidentical text kept in two folders for the dedupe check\n'
        put(self.cfg, 'notes', 'archive/a.md', body)
        put(self.cfg, 'notes', 'much/longer/path/to/dup.md', body)
        self.update()
        self.mat()
        rows = profile_rows(self.cfg, 'orchestrator', "SELECT path FROM docs WHERE title='Dup'")
        self.assertEqual(rows, [('notes/much/longer/path/to/dup.md',)])

    def test_newer_table(self):
        put(self.cfg, 'notes', 'render-plan-v1.md', '# Render plan\n\nfirst version\n')
        put(self.cfg, 'notes', 'render-plan-v2.md', '# Render plan\n\nsecond version with changes\n')
        self.update()
        r = self.mat()
        self.assertEqual(r['newer'], 2)
        c = schema.open_ro(self.cfg.profile_db('orchestrator'))
        ids = dict(c.execute('SELECT path, id FROM docs').fetchall())
        edges = {a: (b, how) for a, b, how in c.execute('SELECT doc, target, how FROM newer')}
        c.close()
        self.assertEqual(edges[ids['notes/render-plan-v1.md']],
                         (ids['notes/render-plan-v2.md'], 'version number in the name'))
        self.assertEqual(edges[ids['notes/decisions/DEC-001-eviction-policy.md']],
                         (ids['notes/decisions/DEC-002-eviction-policy.md'], 'the note says so'))

    def test_replace_is_atomic(self):
        self.mat()
        db = self.cfg.profile_db('orchestrator')
        with open(db, 'rb') as f:
            before = f.read()
        with mock.patch.object(lane_code, 'fill_profile', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.mat()
        with open(db, 'rb') as f:
            self.assertEqual(f.read(), before)
        self.assertFalse(os.path.exists(db + '.tmp'))

    def test_rebuild_replaces_the_file(self):
        self.mat()
        put(self.cfg, 'notes', 'late.md', '# Late\n\nadded after the first profile build\n')
        self.update()
        self.mat()
        paths = [p for (p,) in profile_rows(self.cfg, 'orchestrator', 'SELECT path FROM docs')]
        self.assertIn('notes/late.md', paths)

    def test_vector_side_files_are_asked_for_on_the_temp_file(self):
        fake = types.SimpleNamespace()
        seen = []

        def stored_keys(cfg):
            return ['key_a', 'key_b']

        def write_profile(cfg, key, profile, db_path):
            seen.append((key, profile, db_path, os.path.exists(db_path)))
            if key == 'key_b':
                raise sqlite3.DatabaseError('broken store')
            return (3, 5)
        fake.stored_keys = stored_keys
        fake.write_profile = write_profile
        with mock.patch.dict(sys.modules, {'leks.vecstore': fake}):
            r = self.mat()
        self.assertEqual([s[:2] for s in seen], [('key_a', 'orchestrator'), ('key_b', 'orchestrator')])
        self.assertTrue(all(s[2].endswith('search.sqlite.tmp') and s[3] for s in seen))
        self.assertGreater(r['docs'], 0)
        self.assertTrue(os.path.exists(self.cfg.profile_db('orchestrator')))

    def test_locate_rows_come_from_lane_locate(self):
        fake = types.SimpleNamespace()
        calls = []

        def fill_profile(c, master, roots):
            calls.append((c.execute('SELECT COUNT(*) FROM docs').fetchone()[0], roots))
            return {'names': 42}
        fake.fill_profile = fill_profile
        with mock.patch.dict(sys.modules, {'leks.lane_locate': fake}):
            r = self.mat()
        self.assertEqual(calls, [(9, ['notes', 'code', 'docs'])])
        self.assertEqual(r['names'], 42)

    def test_builds_without_the_optional_modules(self):
        with mock.patch.dict(sys.modules, {'leks.lane_locate': None, 'leks.vecstore': None, 'leks.docs': None}):
            r = self.mat()
        self.assertEqual(r['names'], 0)
        self.assertEqual(r['docs'], 9)

    def test_update_all(self):
        out = build.update_all(self.cfg, log=quiet)
        self.assertEqual(set(out['profiles']), {'architect', 'orchestrator', 'default'})
        self.assertTrue(all(os.path.exists(self.cfg.profile_db(p)) for p in out['profiles']))
        again = build.update_all(self.cfg, log=quiet)
        self.assertEqual(again['update']['indexed'], 0)
        self.assertTrue(all(v.get('unchanged') for v in again['profiles'].values()))
        # a change brings the profiles back
        put(self.cfg, 'notes', 'fresh.md', '# Fresh\n\nnew note\n')
        third = build.update_all(self.cfg, log=quiet)
        self.assertEqual(third['update']['indexed'], 1)
        self.assertTrue(all('docs' in v for v in third['profiles'].values()))
        forced = build.update_all(self.cfg, force=True, log=quiet)
        self.assertTrue(all('docs' in v for v in forced['profiles'].values()))

    def test_update_all_with_skipped_files_still_builds_profiles(self):
        # the skipped counter and the locked marker share a key name
        put(self.cfg, 'notes', 'blob.md', 'abc\0def\n')
        out = build.update_all(self.cfg, log=quiet)
        self.assertEqual(out['update']['skipped'], 1)
        self.assertEqual(set(out['profiles']), {'architect', 'orchestrator', 'default'})

    def test_update_all_when_locked(self):
        os.makedirs(os.path.join(self.cfg.run_dir(), 'update.lock'))
        out = build.update_all(self.cfg, log=quiet)
        self.assertEqual(out['update'], {'skipped': 'locked'})
        self.assertEqual(out['profiles'], {})


# documents and images, with a stand-in for the docs module

def fake_docs(calls, images=True, pdf=True, aware=False):
    ns = types.SimpleNamespace()
    ns.EXTRACTOR_VERSION = 1
    ns.capabilities = lambda cfg: {'pdf_text': pdf, 'ocr': images, 'docx': True, 'images': images}

    def pdf_ex(path):
        return {'unit': 'page', 'title': 'Cache study', 'extractor': 'pypdf', 'version': 1, 'warning': None,
                'units': [(1, 'Intro', 'The cache study measures eviction on the sample scene.'),
                          (2, 'References', '[1] A paper about tile caches.')]}

    def extract(cfg, abs_path, ext, ocr=None):
        calls['extract'].append((os.path.basename(abs_path), ocr is not None))
        base = os.path.basename(abs_path)
        if base.startswith('leak'):
            return dict(pdf_ex(abs_path), units=[(1, '', 'token AKIA' + 'ABCDEFGHIJKLMNOP')])
        if base.startswith('empty'):
            return None
        if ext == '.pdf':
            if ocr is not None and abs_path in ocr:
                return {'unit': 'page', 'title': 'Scan', 'extractor': 'ocr', 'version': 1,
                        'warning': 'text from OCR, mean confidence 0.80',
                        'units': [(1, '', ocr[abs_path].get('text', ''))]}
            return pdf_ex(abs_path)
        if ext == '.docx':
            return {'unit': 'paragraph', 'title': 'Guide', 'extractor': 'docx', 'version': 1, 'warning': None,
                    'units': [(1, 'Guide', 'First paragraph.'), (2, 'Guide', 'Second paragraph.'),
                              (3, 'Setup', 'Third paragraph.')]}
        o = (ocr or {}).get(abs_path)
        return None if o is None else {'unit': 'image', 'title': 'x', 'extractor': 'ocr', 'version': 1,
                                       'warning': 'text from OCR', 'units': [(1, 'OCR text', o['text'])]}
    ns.extract = extract

    def ocr_batch(cfg, paths):
        calls['ocr'].append(sorted(os.path.basename(p) for p in paths))
        return {p: {'path': p, 'text': 'Exit sign points left', 'mean_confidence': 0.9} for p in paths}
    ns.ocr_batch = ocr_batch
    if aware:
        ns.needs_ocr = lambda cfg, p, ext: os.path.basename(p).startswith('scan') or ext != '.pdf'
    return ns


class DocumentTests(Base):
    def setUp(self):
        super().setUp()
        self.calls = {'extract': [], 'ocr': []}

    def run_update(self, fake, **kw):
        with mock.patch.dict(sys.modules, {'leks.docs': fake}):
            return build.update(self.cfg, log=quiet, **kw)

    def test_pdf_pages_become_chunks_per_page(self):
        put(self.cfg, 'docs', 'cache-study.pdf', b'%PDF fake one', binary=True)
        r = self.run_update(fake_docs(self.calls))
        self.assertEqual(r['indexed'], 10)
        row = master_rows(self.cfg, "SELECT kind,unit,units,title,extractor,status,status_src FROM files "
                          "WHERE rel='cache-study.pdf'")[0]
        self.assertEqual(row, ('document', 'page', 2, 'Cache study', 'pypdf:1', 'unknown', 'rule'))
        ch = master_rows(self.cfg, "SELECT ord,start_line,end_line,heading FROM chunks WHERE rel='cache-study.pdf' "
                         "ORDER BY ord")
        self.assertEqual(ch, [(0, 1, 1, 'Intro'), (1, 2, 2, 'References')])
        cache = master_rows(self.cfg, 'SELECT extractor,version,unit,title FROM extract_cache')
        self.assertEqual(cache, [('pypdf', 1, 'page', 'Cache study')])
        # an unchanged document is not extracted again
        n = len(self.calls['extract'])
        self.run_update(fake_docs(self.calls))
        self.assertEqual(len(self.calls['extract']), n)

    def test_same_bytes_use_the_extract_cache(self):
        put(self.cfg, 'docs', 'one.pdf', b'%PDF same bytes', binary=True)
        put(self.cfg, 'docs', 'two.pdf', b'%PDF same bytes', binary=True)
        self.run_update(fake_docs(self.calls))
        self.assertEqual(len([c for c in self.calls['extract'] if c[0].endswith('.pdf')]), 1)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM files WHERE ext='.pdf'")[0][0], 2)

    def test_extractor_version_bump_reextracts(self):
        put(self.cfg, 'docs', 'one.pdf', b'%PDF v', binary=True)
        self.run_update(fake_docs(self.calls))
        n = len(self.calls['extract'])
        fake = fake_docs(self.calls)
        fake.EXTRACTOR_VERSION = 2
        self.run_update(fake)
        self.assertEqual(len(self.calls['extract']), n + 1)

    def test_document_in_the_profile(self):
        put(self.cfg, 'docs', 'cache-study.pdf', b'%PDF fake one', binary=True)
        put(self.cfg, 'docs', 'copy-of-study.pdf', b'%PDF fake one', binary=True)
        self.run_update(fake_docs(self.calls))
        with mock.patch.dict(sys.modules, {'leks.docs': fake_docs(self.calls)}):
            r = build.materialize(self.cfg, 'orchestrator', log=quiet)
        self.assertEqual(r['documents'], 1)
        rows = profile_rows(self.cfg, 'orchestrator', "SELECT path,kind,unit,units,copies FROM docs WHERE kind='document'")
        self.assertEqual(rows[0][0], 'docs/cache-study.pdf')
        self.assertEqual(rows[0][2:4], ('page', 2))
        self.assertEqual(json.loads(rows[0][4]), ['docs/copy-of-study.pdf'])
        refs = profile_rows(self.cfg, 'orchestrator', "SELECT c.start_line, c.refs FROM chunks c JOIN docs d ON d.id=c.doc "
                            "WHERE d.kind='document' ORDER BY c.ord")
        self.assertEqual(refs, [(1, 0), (2, 1)])
        self.assertEqual(profile_rows(self.cfg, 'orchestrator', "SELECT COUNT(*) FROM dfts WHERE dfts MATCH 'text:eviction'")[0][0], 1)
        # documents stay out of the notes table
        hit = profile_rows(self.cfg, 'orchestrator',
                           "SELECT d.kind FROM fts JOIN chunks c ON c.id=fts.rowid JOIN docs d ON d.id=c.doc "
                           "WHERE fts MATCH 'eviction'")
        self.assertNotIn('document', [k for (k,) in hit])

    def test_docs_page_chunker_is_used_when_present(self):
        fake = fake_docs(self.calls)
        seen = {}

        def find_refs(pages):
            seen['pages'] = list(pages)
            return None

        def chunks_of_pages(pages, refs, chunk_chars=1000):
            return [(1, 2, 'Whole', ' '.join(pages))]
        fake.find_refs = find_refs
        fake.chunks_of_pages = chunks_of_pages
        put(self.cfg, 'docs', 'cache-study.pdf', b'%PDF fake one', binary=True)
        self.run_update(fake)
        ch = master_rows(self.cfg, "SELECT start_line,end_line,heading FROM chunks WHERE rel='cache-study.pdf'")
        self.assertEqual(ch, [(1, 2, 'Whole')])
        self.assertEqual(len(seen['pages']), 2)

    def test_docx_paragraphs(self):
        put(self.cfg, 'docs', 'guide.docx', b'PK fake docx', binary=True)
        self.run_update(fake_docs(self.calls))
        row = master_rows(self.cfg, "SELECT unit,units FROM files WHERE rel='guide.docx'")[0]
        self.assertEqual(row, ('paragraph', 3))
        ch = master_rows(self.cfg, "SELECT start_line,end_line,heading FROM chunks WHERE rel='guide.docx' ORDER BY ord")
        self.assertEqual(ch, [(1, 2, 'Guide'), (3, 3, 'Setup')])

    def test_images_go_through_one_ocr_batch(self):
        put(self.cfg, 'docs', 'sign.png', b'png bytes 1', binary=True)
        put(self.cfg, 'docs', 'plan.jpg', b'jpg bytes 2', binary=True)
        r = self.run_update(fake_docs(self.calls))
        self.assertEqual(self.calls['ocr'], [['plan.jpg', 'sign.png']])
        self.assertEqual(r['ocr'], 2)
        row = master_rows(self.cfg, "SELECT kind,unit,units,warning,extractor FROM files WHERE rel='sign.png'")[0]
        self.assertEqual(row[:3], ('image', 'image', 1))
        self.assertIn('mean confidence 0.90', row[3])
        self.assertTrue(row[4].startswith('ocr:'))
        text = master_rows(self.cfg, "SELECT text, heading FROM chunks WHERE rel='sign.png'")
        self.assertEqual(text, [('Exit sign points left', 'OCR text')])
        # nothing changed: no second batch
        self.run_update(fake_docs(self.calls))
        self.assertEqual(len(self.calls['ocr']), 1)

    def test_scan_in_a_pdf_joins_the_batch_when_docs_can_say_so(self):
        put(self.cfg, 'docs', 'scan-1.pdf', b'%PDF scan one', binary=True)
        put(self.cfg, 'docs', 'plain.pdf', b'%PDF plain', binary=True)
        put(self.cfg, 'docs', 'photo.png', b'png photo', binary=True)
        self.run_update(fake_docs(self.calls, aware=True))
        self.assertEqual(self.calls['ocr'], [['photo.png', 'scan-1.pdf']])
        with_ocr = [c for c in self.calls['extract'] if c[1]]
        self.assertEqual({c[0] for c in with_ocr}, {'photo.png', 'scan-1.pdf'})
        self.assertEqual(master_rows(self.cfg, "SELECT extractor FROM files WHERE rel='scan-1.pdf'")[0][0], 'ocr:1')
        self.assertEqual(master_rows(self.cfg, "SELECT extractor FROM files WHERE rel='plain.pdf'")[0][0], 'pypdf:1')

    def test_failed_ocr_leaves_a_fixed_reason(self):
        fake = fake_docs(self.calls)
        fake.ocr_batch = lambda cfg, paths: {p: {'path': p, 'error': 'timeout'} for p in paths}
        put(self.cfg, 'docs', 'sign.png', b'png bytes 1', binary=True)
        r = self.run_update(fake)
        self.assertEqual(r['ocr'], 0)
        self.assertEqual(master_rows(self.cfg, "SELECT why FROM skipped WHERE rel='sign.png'"), [('no usable text',)])

    def test_images_without_ocr_are_skipped_then_retried(self):
        put(self.cfg, 'docs', 'sign.png', b'png bytes 1', binary=True)
        r = self.run_update(fake_docs(self.calls, images=False))
        self.assertEqual(self.calls['ocr'], [])
        self.assertEqual(master_rows(self.cfg, "SELECT why FROM skipped WHERE rel='sign.png'"),
                         [('ocr off or helper not built',)])
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM names WHERE rel='sign.png'")[0][0], 1)
        # same capabilities: the skip is remembered, nothing is read
        self.run_update(fake_docs(self.calls, images=False))
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM skipped")[0][0], 1)
        # the helper shows up
        r = self.run_update(fake_docs(self.calls, images=True))
        self.assertEqual(r['ocr'], 1)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM skipped")[0][0], 0)

    def test_pdf_without_any_extractor(self):
        put(self.cfg, 'docs', 'a.pdf', b'%PDF a', binary=True)
        self.run_update(fake_docs(self.calls, images=False, pdf=False))
        self.assertEqual(master_rows(self.cfg, "SELECT why FROM skipped WHERE rel='a.pdf'"),
                         [('no pdf text extractor',)])
        self.assertEqual(self.calls['extract'], [])

    def test_known_document_survives_losing_the_extractor(self):
        put(self.cfg, 'docs', 'a.pdf', b'%PDF a', binary=True)
        self.run_update(fake_docs(self.calls))
        r = self.run_update(fake_docs(self.calls, images=False, pdf=False))
        self.assertEqual(r['removed'], 0)
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM files WHERE rel='a.pdf'")[0][0], 1)

    def test_no_docs_module_at_all(self):
        put(self.cfg, 'docs', 'a.pdf', b'%PDF a', binary=True)
        with mock.patch.dict(sys.modules, {'leks.docs': None}):
            r = build.update(self.cfg, log=quiet)
        self.assertEqual(master_rows(self.cfg, "SELECT why FROM skipped WHERE rel='a.pdf'"),
                         [('no document extractor',)])
        self.assertGreaterEqual(r['indexed'], 9)

    def test_secret_in_a_document_and_empty_result(self):
        put(self.cfg, 'docs', 'leak.pdf', b'%PDF leak', binary=True)
        put(self.cfg, 'docs', 'empty.pdf', b'%PDF empty', binary=True)
        self.run_update(fake_docs(self.calls))
        why = dict(master_rows(self.cfg, 'SELECT rel,why FROM skipped'))
        self.assertEqual(why['leak.pdf'], 'secret pattern')
        self.assertEqual(why['empty.pdf'], 'no usable text')
        self.assertEqual(master_rows(self.cfg, "SELECT COUNT(*) FROM extract_cache")[0][0], 0)

    def test_exception_in_docs_never_reaches_the_index(self):
        fake = fake_docs(self.calls)

        def boom(cfg, p, ext, ocr=None):
            raise RuntimeError('/private/place/secret detail')
        fake.extract = boom
        put(self.cfg, 'docs', 'a.pdf', b'%PDF a', binary=True)
        r = self.run_update(fake)
        self.assertEqual(r['errors'], 1)
        why = master_rows(self.cfg, "SELECT why FROM skipped WHERE rel='a.pdf'")
        self.assertEqual(why, [('extract failed',)])

    def test_real_docx(self):
        xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
               '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Mesh budget guide</w:t></w:r></w:p>'
               '<w:p><w:r><w:t>Triangles per tile stay under the budget.</w:t></w:r></w:p>'
               '</w:body></w:document>')
        p = os.path.join(self.cfg.root('docs').path, 'budget.docx')
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('[Content_Types].xml', '<Types/>')
            z.writestr('word/document.xml', xml)
        r = build.update(self.cfg, log=quiet)
        row = master_rows(self.cfg, "SELECT kind,unit,units,title FROM files WHERE rel='budget.docx'")
        self.assertEqual(row, [('document', 'paragraph', 2, 'Mesh budget guide')])
        self.assertEqual(r['errors'], 0)


class HygieneTests(unittest.TestCase):
    def test_no_dash_characters_in_my_files(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = (chr(0x2014), chr(0x2013))
        for rel in ('leks/schema.py', 'leks/chunking.py', 'leks/lane_code.py', 'leks/build.py',
                    'tests/test_build.py', 'tests/test_lane_code.py'):
            with open(os.path.join(here, rel), encoding='utf-8') as f:
                text = f.read()
            for ch in bad:
                self.assertNotIn(ch, text, rel)


if __name__ == '__main__':
    unittest.main()
