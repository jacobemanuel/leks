"""Status, date and replacement written as plain lines under a note's title, the way many decision logs
keep them: "Date: 2026-08-14. Status: superseded by D-07." The tool-server test notes are the case that
showed the gap: on 3.0 both pedestal decisions came out with status unknown and the old one ranked first."""
from __future__ import annotations

import os
import shutil
import unittest

from leks import build, chunking, query
from tests import helpers

HERE = os.path.dirname(os.path.abspath(__file__))
INTEG = os.path.join(os.path.dirname(HERE), 'bench', 'integrations')

# the first search of every agent session in the tool-server test, plus a few other ways to ask
QUESTIONS = (
    'display pedestal spec dimensions',
    'pedestal spec dimensions',
    'pedestal size and material',
    'what pedestal do we build',
    'pedestal colour roughness',
    'Pedestal object',
)


class HeadFields(unittest.TestCase):
    def fields(self, text):
        return chunking.head_fields(text.split('\n'))

    def test_two_keys_on_one_line(self):
        hf = self.fields('# D-03: display pedestal\n\nDate: 2026-08-14. Status: superseded by D-07.\n\nBody.')
        self.assertEqual(hf['date'], '2026-08-14')
        self.assertEqual(hf['status'], 'superseded by D-07')

    def test_bold_list_and_table_forms(self):
        self.assertEqual(self.fields('# T\n\n**Status:** accepted\n')['status'], 'accepted')
        self.assertEqual(self.fields('# T\n\n- Status: current\n- Date: 2026-01-02\n')['date'], '2026-01-02')
        self.assertEqual(self.fields('# T\n\n| Status: draft | Owner: me |\n')['status'], 'draft')
        self.assertEqual(self.fields('# T\n\nRevisit: 2026-12-01\n')['revisit'], '2026-12-01')
        # bold on the value, or inside it, is markup
        self.assertEqual(self.fields('# T\nStatus: **current**\n')['status'], 'current')
        self.assertEqual(self.fields('# T\n\nStatus: **superseded** by D-07\n')['status'], 'superseded by D-07')
        self.assertEqual(self.fields('# T\n\nDate: 2026-01-02. **Status:** __done__\n')['status'], 'done')
        fm, took = chunking.meta_of('# T\n\nStatus: **superseded** by D-07.\n'.split('\n'))
        self.assertEqual(chunking.status_of('decision', 'd/x.md', fm, {}, took), ('superseded', 'status-line'))

    def test_only_the_head_counts(self):
        text = '# T\n\nIntro line.\n\n## Later\n\nStatus: superseded by D-09.\n'
        self.assertNotIn('status', self.fields(text))
        self.assertNotIn('status', self.fields('# T\n\n```\nStatus: done\n```\n'))
        many = '# T\n\n' + '\n'.join('line %d.' % i for i in range(12)) + '\nStatus: done\n'
        self.assertNotIn('status', self.fields(many))

    def test_words_that_only_look_like_keys(self):
        hf = self.fields('# T\n\nThe status of the cache is fine. We dated it later.\n')
        self.assertEqual(hf, {})
        # a key inside a longer label, or in brackets, is a word of the sentence
        for text in ('# Launch plan\n\nOwner: Ana. Due date: 2027-03-01.\n',
                     '# Report\n\nBuild status: failing; Last updated: 2026-01-02\n',
                     '# T\n\nProject status: on hold\n', '# T\n\nTicket status: closed\n',
                     '# T\n\nThe old rule (status: draft) stays.\n', '# T\n\nOwner: Ana, date: 2027-03-01\n'):
            with self.subTest(text=text):
                self.assertEqual(self.fields(text), {})
        # after a full stop and a space, a semicolon or a table bar a key still counts
        self.assertEqual(self.fields('# T\n\nOwner: Ana. Date: 2027-03-01; Status: done | Updated: 2027-03-02\n'),
                         {'date': '2027-03-01', 'status': 'done', 'updated': '2027-03-02'})

    def test_front_matter_wins(self):
        lines = '---\nstatus: current\n---\n# T\n\nStatus: superseded by D-01.\n'.split('\n')
        fm, took = chunking.meta_of(lines)
        self.assertEqual(fm['status'], 'current')
        self.assertNotIn('status', took)
        self.assertEqual(chunking.status_of('decision', 'd/x.md', fm, {}, took), ('current', 'front-matter'))

    def test_status_line_source(self):
        fm, took = chunking.meta_of('# T\n\nStatus: superseded by D-07.\n'.split('\n'))
        self.assertEqual(chunking.status_of('decision', 'd/x.md', fm, {}, took), ('superseded', 'status-line'))
        fm, took = chunking.meta_of('# T\n\nStatus: pending review\n'.split('\n'))
        self.assertEqual(chunking.status_of('decision', 'd/x.md', fm, {}, took),
                         ('unknown', 'status-line-unchecked'))

    def test_new_status_words(self):
        for word, group in (('needs-update', 'stale'), ('needs update', 'stale'), ('archive', 'historical-record'),
                            ('archived', 'historical-record'), ('stable', 'current')):
            self.assertEqual(chunking.status_of('note', 'x.md', {'status': word}, {}),
                             (group, 'front-matter'), word)


class Succession(unittest.TestCase):
    def rows(self, notes):
        return [(i, p, os.path.basename(p), '', 0.0, t) for i, (p, t) in enumerate(notes.items(), 1)]

    def test_ids_in_status_lines(self):
        ed = chunking.succession(self.rows({
            'n/D-03_pedestal.md': '# D-03\nDate: 2026-08-14. Status: superseded by D-07.\nBox.',
            'n/D-07_pedestal.md': '# D-07\nDate: 2026-10-02. Status: current. Supersedes D-03.\nCylinder.',
            'n/D-05_lighting.md': '# D-05\nStatus: current.\nOne light.',
        }))
        self.assertEqual(ed, {1: (2, 'the note says so')})

    def test_only_the_newer_note_says_so(self):
        ed = chunking.succession(self.rows({
            'n/ADR-0001-cache.md': '# ADR-0001\nStatus: accepted\nLRU.',
            'n/ADR-0002-cache.md': '# ADR-0002\nStatus: accepted\nReplaces: ADR-0001\nFIFO.',
        }))
        self.assertEqual(ed, {1: (2, 'the newer note says so')})

    def test_no_edge_from_a_mention_further_down(self):
        ed = chunking.succession(self.rows({
            'n/D-09_x.md': '# D-09\nStatus: current.\nX.',
            'n/D-11_x.md': '# D-11\nStatus: current.\nY.',
            'n/notes.md': '# Notes\nIntro.\n## History\nD-09 was superseded by D-11 in May.',
        }))
        self.assertEqual(ed, {})

    def test_id_must_match_the_whole_id(self):
        ed = chunking.succession(self.rows({
            'n/D-0_x.md': '# D-0\nStatus: superseded by D-07.\nX.',
            'n/D-070_y.md': '# D-070\nStatus: current.\nY.',
        }))
        self.assertEqual(ed, {})

    def test_ambiguous_id_gives_no_edge(self):
        ed = chunking.succession(self.rows({
            'a/D-01_x.md': '# D-01\nStatus: superseded by D-02.\nX.',
            'b/D-02_y.md': '# D-02\nStatus: current.\nY.',
            'c/D-02_z.md': '# D-02\nStatus: current.\nZ.',
        }))
        self.assertEqual(ed, {})

    def test_ambiguous_id_takes_the_one_in_the_same_folder(self):
        ed = chunking.succession(self.rows({
            'a/D-05_x.md': '# D-05\nStatus: current. Supersedes D-02.\nX.',
            'a/D-02_y.md': '# D-02\nStatus: superseded.\nY.',
            'b/D-02_z.md': '# D-02\nStatus: current.\nZ.',
        }))
        self.assertEqual(ed, {2: (1, 'the newer note says so')})

    def test_a_long_decision_log_builds_in_linear_time(self):
        # each id is one lookup: 20,000 notes that each name the one before took 14 s with a scan per id
        import time
        rows = [(i, 'n/D-%05d.md' % i, 'D-%05d.md' % i, '', 0.0, '# D\nStatus: current. Supersedes D-%05d.\n' % (i - 1))
                for i in range(1, 20001)]
        t = time.monotonic()
        ed = chunking.succession(rows)
        self.assertLess(time.monotonic() - t, 5.0)
        self.assertEqual((len(ed), ed[1], ed[19999]), (19999, (2, 'the newer note says so'),
                                                      (20000, 'the newer note says so')))

    def test_front_matter_supersedes(self):
        ed = chunking.succession(self.rows({
            'n/pep-0001.md': '---\nstatus: superseded\n---\n# PEP 1',
            'n/pep-0002.md': '---\nsupersedes: "[[pep-0001]]"\n---\n# PEP 2',
        }))
        self.assertEqual(ed, {1: (2, 'the newer note says so')})


class ToolServerNotes(unittest.TestCase):
    """The current decision ranks above the one it replaced, in both tools' note sets."""

    def check(self, tool):
        home, cfg = helpers.make_home(profiles={'architect': {'roots': ['notes']}})
        try:
            dst = [r for r in cfg.roots if r.name == 'notes'][0].path
            shutil.rmtree(dst)
            shutil.copytree(os.path.join(INTEG, tool, 'notes'), dst)
            build.update_all(cfg, log=lambda *a: None)
            eng = query.Engine(cfg, 'architect')
            for q in QUESTIONS:
                res = eng.search(q, k=5)['results']
                paths = [r['path'] for r in res]
                new = [i for i, p in enumerate(paths) if p.endswith('D-07_pedestal.md')]
                old = [i for i, p in enumerate(paths) if p.endswith('D-03_pedestal.md')]
                self.assertTrue(new, (tool, q, paths))
                if old:
                    self.assertLess(new[0], old[0], (tool, q, paths))
                    r3 = res[old[0]]
                    self.assertEqual(r3['status'], 'superseded', (tool, q))
                    self.assertTrue((r3['newer'] or '').endswith('D-07_pedestal.md'), (tool, q))
                r7 = res[new[0]]
                self.assertEqual(r7['status'], 'current', (tool, q))
                self.assertEqual(r7['date'], '2026-10-02', (tool, q))
        finally:
            helpers.cleanup(home)

    def test_blender(self):
        self.check('blender')

    def test_unity(self):
        self.check('unity')


if __name__ == '__main__':
    unittest.main()
