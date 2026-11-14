"""Tests for neighbours: the notes to bring in line after an edit, in the engine, the MCP tool and the CLI."""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from unittest import mock

from leks import __main__ as cli
from leks import build, query, server

from tests import helpers

try:
    import numpy as np
except ImportError:   # pragma: no cover
    np = None

BUDGET = '# Budget\n\nThe tile budget is 128 MB on the sample scene.\nSee [[STATE]] for the pass order.\n'
STATE_LINE = 'The tile budget is 128 MB and the frame time stays under 16.6 ms.\n'
EVICT = 'The tile cache evicts the oldest tile first, see the eviction decision.\n'


class Corpus(unittest.TestCase):
    """The shared test corpus plus a budget note, built by the real builder."""

    def setUp(self):
        self.home, self.cfg = helpers.make_home()
        self.addCleanup(helpers.cleanup, self.home)
        self.notes = os.path.join(self.home, 'corpus', 'notes')
        self.write('budget.md', BUDGET)
        self.write('plan.md', '# Plan\n\nKeep the [[budget]] in mind.\n')
        with open(self.file('STATE.md'), 'a', encoding='utf-8') as f:
            f.write(STATE_LINE)
        build.update_all(self.cfg, log=lambda *a: None)
        self.eng = query.Engine(self.cfg, 'orchestrator')
        self.addCleanup(self.eng.close)

    def file(self, rel):
        return os.path.join(self.notes, *rel.split('/'))

    def write(self, rel, text):
        with open(self.file(rel), 'w', encoding='utf-8') as f:
            f.write(text)

    def text(self, rel):
        with open(self.file(rel), encoding='utf-8') as f:
            return f.read()

    def paths(self, r):
        return [e['path'] for e in r['neighbours']]


class Removed(Corpus):
    def test_notes_that_still_hold_removed_text_come_first(self):
        self.write('STATE.md', self.text('STATE.md').replace('128 MB', '256 MB').replace(EVICT, ''))
        r = self.eng.neighbours('notes/STATE.md')
        self.assertIs(r['changed'], True)
        self.assertEqual(r['removed'], ['128 MB', '"the tile cache evicts the oldest tile first"'])
        first, second = r['neighbours'][:2]
        self.assertEqual((first['path'], first['line']), ('notes/budget.md', 3))
        self.assertTrue(first['why'].startswith('still holds what this note removed: 128 MB; links to this note'))
        self.assertEqual((second['path'], second['line']), ('notes/decisions/DEC-002-eviction-policy.md', 5))
        self.assertEqual(second['why'], 'still holds what this note removed: "the tile cache evicts the oldest tile first"')
        self.assertIn('Removed text: compared with the indexed text.', r['note'])

    def test_was_is_the_old_text(self):
        old = self.text('STATE.md') + 'Cache entries older than thirty days are removed by the bundle script.\n'
        r = self.eng.neighbours('notes/STATE.md', was=old)
        self.assertIs(r['changed'], False)
        self.assertEqual(r['neighbours'][0]['path'], 'notes/build_cache.md')
        self.assertEqual(r['neighbours'][0]['line'], 8)
        self.assertIn('Removed text: compared with the text given as was.', r['note'])

    def test_a_number_needs_its_digits_and_its_unit(self):
        holds = lambda t: query._nb_holds([{'show': '128 MB', 'num': ('128', 'mb')}], t)[0]   # noqa: E731
        self.assertEqual(holds('we keep 128 MB'), ['128 MB'])
        self.assertEqual(holds('we keep 128MB'), ['128 MB'])
        self.assertEqual(holds('we keep 1280 MB, 128 GB and v128 MB'), [])
        nums = [x[:2] for x in query._nb_numbers('DEC-002 took 12 runs, 15% and 5 ms over 1.5.2 at 3 seconds')]
        self.assertEqual(nums, [('15', '%'), ('5', 'ms'), ('1.5.2', ''), ('3', 's')])

    def test_a_sentence_that_only_lost_a_number_is_left_to_the_number(self):
        self.write('STATE.md', self.text('STATE.md').replace('128 MB', '256 MB'))
        self.assertEqual(self.eng.neighbours('notes/STATE.md')['removed'], ['128 MB'])

    def test_unchanged_file_without_was_has_no_removed_text(self):
        r = self.eng.neighbours('notes/budget.md')
        self.assertEqual((r['changed'], r['removed']), (False, []))
        self.assertTrue(r['note'].startswith('The file is as indexed, so there is no removed text to follow'))


class Links(Corpus):
    def test_backlinks_with_the_line_and_records_last(self):
        r = self.eng.neighbours('notes/decisions/DEC-002-eviction-policy.md')
        top = r['neighbours'][0]
        self.assertEqual((top['path'], top['line'], top['status']),
                         ('notes/decisions/DEC-001-eviction-policy.md', 3, 'superseded'))
        # the older version comes from the chain, the backlink adds its line
        self.assertTrue(top['why'].startswith('older version, this note replaces it; links to this note'))
        self.assertIn(query.NB_RECORDS, r['note'])

    def test_outlinks(self):
        r = self.eng.neighbours('notes/budget.md')
        # plan.md links here: a backlink comes before the outlink
        self.assertEqual(self.paths(r)[:2], ['notes/plan.md', 'notes/STATE.md'])
        out = r['neighbours'][1]
        self.assertEqual((out['status'], out['date']), ('current', '2026-03-02'))
        self.assertTrue(out['why'].startswith('this note links to it on line 4'))

    def test_a_gone_file_lists_the_notes_that_link_to_it(self):
        os.unlink(self.file('budget.md'))
        r = self.eng.neighbours('notes/budget.md')
        self.assertEqual(self.paths(r), ['notes/plan.md'])
        self.assertEqual(r['neighbours'][0]['line'], 3)
        self.assertEqual(r['note'], query.NB_GONE)


class Guards(Corpus):
    """The rules behind the order and the facts, each with a case that breaks when the rule goes."""

    def rebuild(self):
        build.update_all(self.cfg, log=lambda *a: None)
        self.eng.close()
        self.eng = query.Engine(self.cfg, 'orchestrator')

    def test_backlinks_from_records_come_last(self):
        self.write('target.md', '# Target\n\nThe target text.\n')
        self.write('a-done.md', '# A done\n\nStatus: done\n\nSee [[target]].\n')
        self.write('z-live.md', '# Z live\n\nStatus: current\n\nSee [[target]].\n')
        self.rebuild()
        top = self.eng.neighbours('notes/target.md')['neighbours'][:2]
        self.assertEqual([(e['path'], e['status'], e['why']) for e in top],
                         [('notes/z-live.md', 'current', 'links to this note'),
                          ('notes/a-done.md', 'record', 'links to this note')])

    def test_status_and_date_lines_give_no_facts(self):
        self.write('meta.md', '# Meta\n\nStatus: current\nDate: 2026-01-01\n\nThe meta body stays as it was.\n')
        self.rebuild()
        self.write('meta.md', '# Meta\n\nStatus: superseded\nDate: 2026-02-02\n\nThe meta body stays as it was.\n')
        r = self.eng.neighbours('notes/meta.md')
        self.assertEqual((r['changed'], r['removed']), (True, []))

    def test_a_candidate_that_now_leads_out_of_its_root_is_not_read(self):
        if not hasattr(os, 'symlink'):
            self.skipTest('no symlinks here')
        self.write('target.md', '# Target\n\nThe target text.\n')
        self.write('linker.md', '# Linker\n\nSee [[target]].\n')
        self.rebuild()
        backlinks = lambda: [e['path'] for e in self.eng.neighbours('notes/target.md')['neighbours']  # noqa: E731
                             if 'links to this note' in e['why']]
        self.assertEqual(backlinks(), ['notes/linker.md'])
        outside = os.path.join(self.home, 'outside.md')
        with open(outside, 'w', encoding='utf-8') as f:
            f.write('# Linker\n\nSee [[target]].\n')
        os.remove(self.file('linker.md'))
        try:
            os.symlink(outside, self.file('linker.md'))
        except OSError:
            self.skipTest('symlinks are not allowed here')
        self.assertEqual(backlinks(), [])

    def test_pieces_of_a_long_line_join_on_their_overlap(self):
        self.assertEqual(query._nb_join('the tile budget is 12', 'is 128 MB now'), 'the tile budget is 128 MB now')
        self.assertEqual(query._nb_join('abc', 'xyz'), 'abcxyz')
        # a line longer than a passage: its pieces join back into the line, so only the sentence that went
        # is removed, and no text twice over at a seam
        words = ' '.join('word%d' % i for i in range(build._chunk_chars(self.cfg) // 3))
        line = 'The long line starts here. ' + words + '. The eviction keeps the tile pinned for later frames.'
        self.write('long.md', '# Long\n\n' + line + '\n')
        self.rebuild()
        self.write('long.md', '# Long\n\n' + line.replace(' The eviction keeps the tile pinned for later frames.', '')
                   + '\n')
        self.assertEqual(self.eng.neighbours('notes/long.md')['removed'], ['"the eviction keeps the tile pinned for later"'])


class Chain(Corpus):
    def setUp(self):
        super().setUp()
        self.write('route-a.md', '# Route A\n\nSuperseded by [[route-b]].\n\nThe first route.\n')
        self.write('route-b.md', '# Route B\n\nSuperseded by [[route-c]].\n\nThe second route.\n')
        self.write('route-c.md', '# Route C\n\nThe current route.\n')
        build.update_all(self.cfg, log=lambda *a: None)
        self.eng.close()
        self.eng = query.Engine(self.cfg, 'orchestrator')

    def why(self, path):
        # the reasons from the chain and the links, the close in words reason left off
        return [(e['path'], e['why'].split('; close in words')[0])
                for e in self.eng.neighbours(path, n=2)['neighbours']]

    def test_the_chain_both_ways(self):
        self.assertEqual(self.why('notes/route-b.md'), [
            ('notes/route-c.md', 'newer version, it replaces this note; this note links to it on line 3'),
            ('notes/route-a.md', 'older version, this note replaces it; links to this note')])
        # the backlink gives the older note its line
        self.assertEqual(self.eng.neighbours('notes/route-b.md')['neighbours'][1]['line'], 3)

    def test_steps_along_the_chain(self):
        self.assertEqual(self.why('notes/route-a.md'), [
            ('notes/route-b.md', 'newer version, it replaces this note; this note links to it on line 3'),
            ('notes/route-c.md', 'newer version, 2 steps on in the supersession chain')])
        self.assertEqual(self.why('notes/route-c.md'), [
            ('notes/route-b.md', 'older version, this note replaces it; links to this note'),
            ('notes/route-a.md', 'older version, 2 steps back in the supersession chain')])

    def test_a_gone_file_still_lists_its_chain(self):
        os.unlink(self.file('route-b.md'))
        r = self.eng.neighbours('notes/route-b.md')
        self.assertEqual(self.paths(r), ['notes/route-c.md', 'notes/route-a.md'])
        self.assertEqual(r['neighbours'][0]['why'], 'newer version, it replaces this note')


class Close(Corpus):
    def test_close_in_words_without_vectors(self):
        r = self.eng.neighbours('code/tile_cache.py', n=30)
        self.assertTrue(r['neighbours'])
        self.assertTrue(all(e['why'] == 'close in words' for e in r['neighbours']))
        self.assertIn('Close notes by words: this note has no vectors.', r['note'])
        self.assertLessEqual(len(r['neighbours']), query.NB_CLOSE)

    @unittest.skipIf(np is None, 'numpy not installed')
    def test_close_in_meaning_with_vectors(self):
        n = self.eng.db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        m = np.random.RandomState(5).randn(n, 16).astype('float32')
        m /= np.linalg.norm(m, axis=1, keepdims=True)
        rows = dict(self.eng.db.execute('SELECT d.path, c.vec FROM chunks c JOIN docs d ON d.id = c.doc').fetchall())
        m[rows['notes/build_cache.md']] = m[rows['notes/plan.md']]
        self.eng.vec, self.eng.covered, self.eng.vec_why, self.eng._vmap_cache = m, n, None, None
        r = self.eng.neighbours('notes/plan.md', n=3)
        self.assertEqual(len(r['neighbours']), 3)
        self.assertEqual(r['neighbours'][1]['path'], 'notes/build_cache.md')
        self.assertTrue(r['neighbours'][1]['why'].startswith('close in meaning (1.00'))
        self.assertNotIn('no vectors', r['note'])


class Answers(Corpus):
    def test_errors(self):
        self.assertEqual(self.eng.neighbours('notes/nothing.md'), {'error': query.NOT_FOUND})
        self.assertEqual(self.eng.neighbours('notes/STATE.md', was=5), {'error': 'was must be the old text of the note'})
        with mock.patch.object(self.eng, '_doc_row', return_value=('docs/a.pdf', 'unknown', None, 1, 'document',
                                                                   '.pdf', 'page', 3, 'docs', None)):
            self.assertEqual(self.eng.neighbours('docs/a.pdf'),
                             {'error': 'neighbours works on notes and code files, this is a document'})
        self.assertEqual(self.eng.render_neighbours({'error': 'x'}), 'x')

    def test_n_bounds_the_list(self):
        self.assertEqual(len(self.eng.neighbours('notes/STATE.md', n=1)['neighbours']), 1)
        self.assertEqual(len(self.eng.neighbours('notes/STATE.md', n=0)['neighbours']), 1)

    def test_render(self):
        self.write('STATE.md', self.text('STATE.md').replace('128 MB', '256 MB'))
        lines = self.eng.render_neighbours(self.eng.neighbours('notes/STATE.md', n=2)).split('\n')
        self.assertEqual(lines[0], 'neighbours of notes/STATE.md, changed since indexed')
        self.assertEqual(lines[1], 'removed here: 128 MB')
        self.assertRegex(lines[2], r'^1\. notes/budget\.md:3 \| unknown \| \d{4}-\d\d-\d\d \| still holds what this '
                                   r'note removed: 128 MB; links to this note \(line 4\)')
        self.assertTrue(lines[3].startswith('2. '))
        self.assertTrue(lines[4].startswith('Removed text: compared with the indexed text.'))

    def test_reads_only(self):
        before = {p: os.stat(p).st_mtime_ns for p in (self.eng.db_path, self.file('budget.md'))}
        self.eng.neighbours('notes/STATE.md', was='old text with 64 MB in it, all gone now from here')
        self.assertEqual({p: os.stat(p).st_mtime_ns for p in before}, before)


class Tool(Corpus):
    def call(self, args):
        with mock.patch.object(server, '_engine', return_value=self.eng):
            r = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                               'params': {'name': 'neighbours', 'arguments': args}})['result']
        return r.get('isError', False), r['content'][0]['text']

    def test_tool(self):
        self.write('STATE.md', self.text('STATE.md').replace('128 MB', '256 MB'))
        err, text = self.call({'path': 'notes/STATE.md', 'n': 99})
        self.assertFalse(err, text)
        self.assertTrue(text.startswith('neighbours of notes/STATE.md, changed since indexed\nremoved here: 128 MB\n'))
        err, text = self.call({'path': 'notes/budget.md', 'was': BUDGET.replace('128', '64'), 'n': 1})
        lines = text.split('\n')
        self.assertEqual(lines[1], 'removed here: 64 MB')
        self.assertRegex(lines[2], r'^1\. notes/plan\.md:3 \| unknown \| \d{4}-\d\d-\d\d \| links to this note')
        self.assertEqual(len(lines), 4)
        self.assertEqual(self.call({'path': 'notes/budget.md', 'was': 3}),
                         (True, 'neighbours needs the argument was as text, it got a number'))
        self.assertEqual(self.call({}), (True, 'neighbours needs the argument path as text, it is missing'))
        self.assertIn('After editing a note, call neighbours', server.INSTRUCTIONS)


class Command(Corpus):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_parser(self):
        a = cli._parser().parse_args(['neighbours', 'notes/a.md', '--was', 'old.md', '--n', '3', '--json'])
        self.assertEqual((a.path, a.was, a.n, a.json), ('notes/a.md', 'old.md', 3, True))
        a = cli._parser().parse_args(['neighbours', 'notes/a.md'])
        self.assertEqual((a.was, a.n, a.json), (None, 10, False))

    def test_text_json_and_was_file(self):
        with mock.patch.dict(os.environ, {'LEKS_PROFILE': 'orchestrator', 'LEKS_CONFIG': self.cfg.source_path}):
            code, out, _ = self.run_cli('neighbours', 'notes/budget.md')
            self.assertEqual(code, 0)
            self.assertTrue(out.startswith('neighbours of notes/budget.md\n1. notes/plan.md:3 |'))
            # a file path from the shell is turned into its path under the root
            code, out, _ = self.run_cli('neighbours', self.file('budget.md'), '--json', '--n', '2')
            r = json.loads(out)
            self.assertEqual((code, r['path'], len(r['neighbours'])), (0, 'notes/budget.md', 2))
            old = os.path.join(self.home, 'old.md')
            with open(old, 'w', encoding='utf-8') as f:
                f.write(BUDGET.replace('128', '64'))
            code, out, _ = self.run_cli('neighbours', 'notes/budget.md', '--was', old, '--json')
            self.assertEqual(json.loads(out)['removed'], ['64 MB'])
            code, out, err = self.run_cli('neighbours', 'notes/budget.md', '--was', old + '.missing')
            self.assertEqual((code, out), (1, ''))
            self.assertIn('leks: cannot read the --was file', err)


if __name__ == '__main__':
    unittest.main()
