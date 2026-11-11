"""Code lane: symbol scanners, identifier parts, profile tables and the lookups on top."""
import os
import shutil
import sqlite3
import unittest

from leks import lane_code, schema
from tests import helpers


class SplitTests(unittest.TestCase):
    def test_split_ident(self):
        self.assertEqual(lane_code.split_ident('computeTileCost'), ['compute', 'tile', 'cost'])
        self.assertEqual(lane_code.split_ident('HTTPServer2'), ['http', 'server', '2'])
        self.assertEqual(lane_code.split_ident('t16_costModel'), ['t', '16', 'cost', 'model'])
        self.assertEqual(lane_code.split_ident('evict_oldest'), ['evict', 'oldest'])

    def test_ident_column(self):
        col = lane_code.ident_column('call frameGraphPass and tile_cache with hash 8d144812abc')
        self.assertIn('graph', col.split())
        self.assertIn('cache', col.split())
        self.assertNotIn('8d144812abc', col)
        # plain words stay out, each part appears once
        self.assertEqual(lane_code.ident_column('render render render'), '')
        self.assertEqual(lane_code.ident_column('fooBar fooBar').split(), ['foo', 'bar'])

    def test_code_ext_matches_config(self):
        from leks import config
        self.assertEqual(set(config.CODE_EXT), set(lane_code.CODE_EXT))

    def test_code_ext(self):
        self.assertTrue({'.py', '.swift', '.sh', '.c', '.js', '.kt'} <= lane_code.CODE_EXT)
        self.assertNotIn('.md', lane_code.CODE_EXT)
        self.assertEqual(lane_code.extract('const a = 1;', '.js'), ([], 'no symbol scanner'))


class ExtractTests(unittest.TestCase):
    def names(self, text, ext):
        syms, how = lane_code.extract(text, ext)
        return syms, how

    def test_python(self):
        src = 'class Box:\n    def put(self, x):\n        pass\nLIMIT = 3\nZ = 1\n\ndef free():\n    return 1\n'
        syms, how = self.names(src, '.py')
        self.assertEqual(how, 'python ast')
        self.assertEqual(syms[0][:2], ('Box', 'class'))
        self.assertEqual(syms[1][:5], ('put', 'method', 2, 3, 'Box'))
        got = [s[0] for s in syms]
        self.assertIn('LIMIT', got)
        self.assertNotIn('Z', got)          # one letter variable is dropped
        self.assertIn(('free', 'function', 7, 8, '', 'def free():'), syms)

    def test_python_decorator_line_and_async(self):
        src = '@cache\n@other(1)\nasync def fetch_all():\n    pass\n'
        syms, _ = self.names(src, '.py')
        self.assertEqual(syms[0][:3], ('fetch_all', 'function', 3))

    def test_python_refused_falls_back(self):
        syms, how = self.names('def broken(:\n    pass\nclass Still:\n    pass\n', '.py')
        self.assertTrue(how.startswith('python regex'))
        self.assertEqual([s[0] for s in syms], ['broken', 'Still'])

    def test_python_escape_warning_is_quiet(self):
        syms, how = self.names('PAT = "\\d+"\ndef ok():\n    pass\n', '.py')
        self.assertEqual(how, 'python ast')
        self.assertIn('ok', [s[0] for s in syms])

    def test_swift(self):
        src = ('public final class Foo {\n  let ab = 1\n  func barBaz(x: Int) -> Int {\n    let local = 2\n'
               '    return local\n  }\n}\n')
        syms, _ = self.names(src, '.swift')
        self.assertEqual([s[0] for s in syms], ['Foo', 'ab', 'barBaz'])
        self.assertEqual(syms[2][4], 'Foo')
        self.assertEqual(syms[0][3], 7)

    def test_metal_and_c(self):
        src = ('#define N 4\nstruct V { float x; };\nkernel void cullTiles(uint id [[thread_position_in_grid]]) {\n}\n'
               'vertex VOut vs_main(uint v [[vertex_id]]) {\n}\n')
        syms, _ = self.names(src, '.metal')
        self.assertTrue({'N', 'V', 'cullTiles', 'vs_main'} <= {s[0] for s in syms})
        c_src = 'typedef struct Pair {\n  int a;\n} Pair;\nstatic int add_pair(Pair p) {\n  return p.a;\n}\n'
        syms, _ = self.names(c_src, '.c')
        self.assertTrue({'Pair', 'add_pair'} <= {s[0] for s in syms})

    def test_csharp(self):
        src = ('namespace N {\npublic class C : B {\n  public int Prop { get; set; }\n'
               '  public void DoThing(int a) {\n    if (a) { Foo(); }\n  }\n}\n}\n')
        syms, _ = self.names(src, '.cs')
        self.assertEqual([s[0] for s in syms], ['N', 'C', 'Prop', 'DoThing'])
        self.assertEqual(syms[3][4], 'N.C')

    def test_shell(self):
        src = '#!/bin/sh\nFOO=1\nbar() {\n  baz=2\n}\nfunction qux {\n}\ncat <<EOF\nfake() {\nEOF\n'
        syms, _ = self.names(src, '.sh')
        self.assertEqual([s[0] for s in syms], ['FOO', 'bar', 'qux'])

    def test_every_symbol_stands_on_its_line(self):
        src = 'class A:\n    def b(self):\n        pass\n'
        lines = src.split('\n')
        for name, _kind, ln, end, _parent, sig in lane_code.extract(src, '.py')[0]:
            self.assertIn(name, lines[ln - 1])
            self.assertGreaterEqual(end, ln)
            self.assertEqual(sig, lines[ln - 1].strip())

    def test_unknown_ext_and_deep_nesting(self):
        self.assertEqual(lane_code.extract('x', '.md')[0], [])
        deep = '(' * 3000 + ')' * 3000
        syms, how = lane_code.extract('def f():\n    return ' + deep + '\n', '.py')
        self.assertIsInstance(syms, list)


class QueryShapeTests(unittest.TestCase):
    def test_looks_like_code(self):
        self.assertTrue(lane_code.looks_like_code('where is computeTileCost defined'))
        self.assertTrue(lane_code.looks_like_code('slice_cache cut'))
        self.assertFalse(lane_code.looks_like_code('which method did the class use'))
        self.assertFalse(lane_code.looks_like_code('what did we decide about pricing'))

    def test_code_terms(self):
        self.assertEqual(lane_code.code_terms('where is evict_oldest defined and TileCache'),
                         ['evict_oldest', 'TileCache'])
        self.assertEqual(lane_code.code_terms('nothing here'), [])

    def test_normal_hash(self):
        a = 'The tile cache evicts the oldest tile first, measured on 2026-01-02 with 12 tiles.'
        b = 'the tile cache evicts the oldest tile first measured on 2027-05-09 with 99 tiles'
        self.assertEqual(lane_code.normal_hash(a), lane_code.normal_hash(b))
        self.assertIsNone(lane_code.normal_hash('too short'))


class ProfileTests(unittest.TestCase):
    """Build a tiny master and profile by hand and use the lookups on it."""

    def setUp(self):
        self.tmp = helpers.short_tmpdir()
        self.master = schema.open_rw(os.path.join(self.tmp, 'm.sqlite'))
        self.c = sqlite3.connect(os.path.join(self.tmp, 'p.sqlite'))
        self.c.executescript(schema.PROFILE_DDL)
        lane_code.add_schema(self.c)
        src = ('class TileCache:\n    def put(self, k):\n        pass\n    def evict_oldest(self):\n        pass\n'
               'def helper_one():\n    pass\n')
        self.syms, _ = lane_code.extract(src, '.py')
        docs = [(1, 'code/tile_cache.py', 'code', 'tile_cache.py', 'current'),
                (2, 'code/old/tile_cache.py', 'code', 'old/tile_cache.py', 'historical-record'),
                (3, 'code/lost.py', 'code', 'lost.py', 'current')]
        for i, path, root, rel, status in docs:
            self.c.execute('INSERT INTO docs(id,path,root,rel,name,kind,status,date) VALUES(?,?,?,?,?,?,?,?)',
                           (i, path, root, rel, os.path.basename(rel), 'code', status, '2026-01-01'))
            self.c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)',
                           (i, i, 0, 1, 20, '', src, i - 1, 0))
        for root, rel in (('code', 'tile_cache.py'), ('code', 'old/tile_cache.py'), ('code', 'lost.py')):
            self.master.executemany('INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?,?)',
                                    [(root, rel, s[0], s[1], 'python', s[2], s[3], s[4], s[5]) for s in self.syms])
        # the third file lost the dedupe: it has no id, so its symbols stay out
        self.stats = lane_code.fill_profile(self.c, self.master,
                                            {('code', 'tile_cache.py'): 1, ('code', 'old/tile_cache.py'): 2})
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.master.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fill_counts(self):
        n = len(self.syms)
        self.assertEqual(self.stats['symbols'], 2 * n)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM symbols').fetchone()[0], 2 * n)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM chunk_nhash').fetchone()[0], self.stats['nhash'])
        self.assertTrue(lane_code.has_symbols(self.c))

    def test_locate_exact_and_order(self):
        rows = lane_code.locate_symbol(self.c, 'evict_oldest')
        self.assertEqual([r['path'] for r in rows], ['code/tile_cache.py', 'code/old/tile_cache.py'])
        self.assertEqual(rows[0]['kind'], 'method')
        self.assertEqual(rows[0]['parent'], 'TileCache')
        self.assertEqual(set(rows[0]), {'name', 'kind', 'lang', 'line', 'end_line', 'parent', 'signature',
                                        'path', 'status', 'date'})

    def test_locate_case_parent_and_parts(self):
        self.assertEqual(lane_code.locate_symbol(self.c, 'EVICT_OLDEST')[0]['name'], 'evict_oldest')
        self.assertEqual(lane_code.locate_symbol(self.c, 'TileCache.put')[0]['name'], 'put')
        self.assertEqual(lane_code.locate_symbol(self.c, 'helper_one()')[0]['name'], 'helper_one')
        # parts of a name through symfts, but not when exact_only is set
        self.assertTrue(lane_code.locate_symbol(self.c, 'evictOldest'))
        self.assertEqual(lane_code.locate_symbol(self.c, 'evictOldest', exact_only=True), [])
        self.assertEqual(lane_code.locate_symbol(self.c, 'nothing_like_it'), [])
        self.assertEqual(lane_code.locate_symbol(self.c, 'put', kind='class'), [])
        self.assertEqual(lane_code.locate_symbol(self.c, 'put', lang='shell'), [])

    def test_symbol_lane_points_at_the_chunk(self):
        ids = lane_code.symbol_lane(self.c, 'where is evict_oldest defined')
        self.assertEqual(ids, [1, 2])
        self.assertEqual(lane_code.symbol_lane(self.c, 'no names in this sentence'), [])

    def test_drop_near_dups(self):
        c = self.c
        c.execute('INSERT INTO chunk_nhash VALUES(100, ?)', ('abc',))
        c.execute('INSERT INTO chunk_nhash VALUES(101, ?)', ('abc',))
        kept, gone = lane_code.drop_near_dups([100, 101, 1], c)
        self.assertEqual(kept, [100, 1])
        self.assertEqual(gone, {101: 100})
        self.assertEqual(lane_code.drop_near_dups([], c), ([], {}))

    def test_no_symbols_table(self):
        bare = sqlite3.connect(':memory:')
        self.addCleanup(bare.close)
        self.assertFalse(lane_code.has_symbols(bare))
        self.assertEqual(lane_code.locate_symbol(bare, 'x'), [])
        self.assertEqual(lane_code.symbol_lane(bare, 'where is fooBar defined'), [])


if __name__ == '__main__':
    unittest.main()
