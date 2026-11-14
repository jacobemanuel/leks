"""Tests for Revisit dates: the line rule, the head field that follows it and the session brief line."""
from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import shutil
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from leks import build, config, revisit, text
from leks.hooks import session_brief as sb

from tests import helpers

HEAD = 'Revisit date passed: '


class LineRule(unittest.TestCase):
    def test_lines_that_count(self):
        for line in ('Revisit: 2026-12-01', 'revisit: 2026-12-01 when the cache lands', '- Revisit: 2026-12-01',
                     '**Revisit:** 2026-12-01', '* **Revisit**: 2026-12-01', '  REVISIT : 2026-12-01',
                     '+ revisit: **2026-12-01**', 'revisit: "2026-12-01"', "revisit: '2026-12-01'",
                     'Revisit: `2026-12-01`'):
            with self.subTest(line=line):
                self.assertEqual(text.revisit_date(line), '2026-12-01')

    def test_lines_that_do_not(self):
        for line in ('Revisit soon', 'We revisit: 2026-12-01', '> Revisit: 2026-12-01', 'Revisit: 2026-02-30',
                     'Revisit: 2026-1-01', 'Revisit: next week', '1. Revisit: 2026-12-01', 'Revisited: 2026-12-01'):
            with self.subTest(line=line):
                self.assertIsNone(text.revisit_date(line))


class AnyLine(unittest.TestCase):
    def test_dates_and_conditions(self):
        self.assertEqual(text.revisit('Revisit: 2026-12-01'), ('2026-12-01', '2026-12-01'))
        self.assertEqual(text.revisit('- **Revisit:** 2026-12-01 if the cache lands **'),
                         ('2026-12-01', '2026-12-01 if the cache lands'))
        self.assertEqual(text.revisit('Revisit: when the cache lands'), (None, 'when the cache lands'))
        self.assertEqual(text.revisit('Revisit: 2026-02-30'), (None, '2026-02-30'))
        # yaml quotes or code marks around the whole value are no part of it
        self.assertEqual(text.revisit('revisit: "2026-12-01"'), ('2026-12-01', '2026-12-01'))
        self.assertEqual(text.revisit("revisit: 'when the cache lands'"), (None, 'when the cache lands'))
        self.assertEqual(text.revisit('Revisit: "2026-12-01" if the cache lands'),
                         ('2026-12-01', '"2026-12-01" if the cache lands'))

    def test_line_numbers_count_newlines_only(self):
        # read and search number lines at \n alone, so a line separator or form feed in a line moves nothing
        tmp = helpers.short_tmpdir()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, 'a.md')
        with open(p, 'w', encoding='utf-8', newline='') as f:
            f.write('# A\r\nSee section\u2028two\x0c and \x85 three.\r\nRevisit: 2026-01-01\n')
        self.assertEqual(revisit.lines_of(p), [(3, '2026-01-01', '2026-01-01', 'A')])
        for line in ('Revisit:', 'Revisit: **', 'We revisit: x', '> Revisit: x', 'Revisited: x'):
            with self.subTest(line=line):
                self.assertIsNone(text.revisit(line))


class Brief(unittest.TestCase):
    """A notes root of its own, built by the real builder, and the brief for it."""

    def setUp(self):
        self.tmp = os.path.realpath(helpers.short_tmpdir())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, 'notes')
        os.makedirs(self.root)
        env = {k: v for k, v in os.environ.items() if not k.startswith('LEKS_')}
        env['LEKS_HOME'] = os.path.join(self.tmp, 'home')
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        # noon, so the local date does not depend on the hour the tests run
        self.now = time.mktime((2026, 11, 13, 12, 0, 0, 0, 0, -1))

    def put(self, rel, body):
        p = os.path.join(self.root, *rel.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(body)

    def brief(self):
        d = config.default_dict()
        d['home'] = os.path.join(self.tmp, 'home')
        d['roots'] = [{'name': 'notes', 'path': self.root, 'kind': 'notes'}]
        d['profiles'] = {}
        d['models']['embedder'] = None
        d['models']['reranker'] = None
        cfg = config.from_dict(d)
        build.update_all(cfg, log=lambda *a: None)
        return sb.brief(cfg, 'default', now=self.now)

    def due(self):
        got = [ln[len(HEAD):] for ln in self.brief().split('\n') if ln.startswith(HEAD)]
        return got[0] if got else None

    def test_due_today_or_earlier_oldest_first(self):
        self.put('a.md', '# A\n\nRevisit: 2026-11-13\n')
        self.put('b.md', '---\nrevisit: "2026-01-05"\n---\n# B\n')
        self.put('c.md', '# C\n\nSome text.\n\n- **Revisit:** 2026-03-01\n')
        self.put('later.md', '# Later\n\nRevisit: 2026-11-14\n')
        self.assertEqual(self.due(), 'notes/b.md:2 (2026-01-05); notes/c.md:5 (2026-03-01); notes/a.md:3 (2026-11-13)')

    def test_left_out(self):
        self.put('gone.md', '# Gone\n\nStatus: superseded\n\nRevisit: 2026-01-01\n')
        self.put('archive/old.md', '# Old\n\nRevisit: 2026-01-01\n')
        self.put('fenced.md', '# Fenced\n\n```\nRevisit: 2026-01-01\n```\n\nwe should revisit 2026 plans\n')
        self.put('words.md', '# Words\n\nRevisit: when the cache lands, 2026-01-01\n')
        self.put('run.py', 'revisit: 2026-01-01\n')
        self.put('done.md', '# Done\n\nStatus: done\n\nRevisit: 2026-02-02\n')
        self.assertEqual(self.due(), 'notes/done.md:5 (2026-02-02)')

    def test_at_most_six(self):
        for i in range(8):
            self.put('n%d.md' % i, '# N\n\nRevisit: 2026-0%d-01\n' % (i + 1))
        got = self.due().split('; ')
        self.assertEqual(len(got), sb.MAX_LIST)
        self.assertEqual((got[0], got[-1]), ('notes/n0.md:3 (2026-01-01)', 'notes/n5.md:3 (2026-06-01)'))

    def test_the_date_is_the_local_one(self):
        self.put('a.md', '# A\n\nRevisit: 2026-11-13\n')
        today = datetime.date.fromtimestamp(self.now).isoformat()
        self.assertEqual(today, '2026-11-13')
        self.assertEqual(self.due(), 'notes/a.md:3 (2026-11-13)')

    def test_an_index_without_passages_gives_no_line(self):
        # the docs table alone, as an index from before passages or a test fixture has it
        db = os.path.join(self.tmp, 'search.sqlite')
        c = sqlite3.connect(db)
        c.execute('CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT, name TEXT, kind TEXT, status TEXT, '
                  'date TEXT, mtime REAL)')
        c.execute("INSERT INTO docs(path,name,kind,status,date,mtime) VALUES ('notes/a.md','a.md','note',"
                  "'unknown','2026-01-01',?)", (self.now,))
        c.commit()
        c.close()
        self.put('a.md', '# A\n\nRevisit: 2026-01-01\n')
        cfg = SimpleNamespace(profile_db=lambda p: db, abs_path=lambda d: os.path.join(self.tmp, d))
        self.assertEqual(sb.brief(cfg, 'x', now=self.now), sb.FIRST + '\nChanged in the last 3 days: notes/a.md')


class Command(Brief):
    LOG = ('# Decision log\n\n## D1 cache size\n\nWe keep 128 MB.\nRevisit: 2026-10-01\n\n'
           '## D2 eviction\n\nOldest first.\nRevisit: when the replay study is in\n\n'
           '```\nRevisit: 2026-01-01\n```\n\n## D3 tiles\n\nRevisit: 2026-12-24 at the year end\n')

    def setUp(self):
        super().setUp()
        d = config.default_dict()
        d['home'] = os.path.join(self.tmp, 'home')
        d['roots'] = [{'name': 'notes', 'path': self.root, 'kind': 'notes'}]
        d['profiles'] = {}
        d['models']['embedder'] = None
        d['models']['reranker'] = None
        self.cfg_path = os.path.join(self.tmp, 'config.json')
        with open(self.cfg_path, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        os.environ['LEKS_CONFIG'] = self.cfg_path

    def run_cli(self, *argv):
        from leks import __main__ as cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def build(self):
        build.update_all(config.load(), log=lambda *a: None)

    def test_a_decision_log_lists_each_entry(self):
        self.put('decisions/LOG.md', self.LOG)
        self.put('gone.md', '# Gone\n\nStatus: superseded\n\nRevisit: 2026-01-01\n')
        self.build()
        code, out, err = self.run_cli('revisit', '--by', '2026-11-13')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out.split('\n'), [
            'revisit lines in profile default, today 2026-11-13: 1 due, 1 ahead, 1 on a condition',
            'due: notes/decisions/LOG.md:6 | 2026-10-01 | D1 cache size | decision, unknown',
            'ahead: notes/decisions/LOG.md:19 | 2026-12-24 at the year end | D3 tiles | decision, unknown',
            'when: notes/decisions/LOG.md:11 | when the replay study is in | D2 eviction | decision, unknown',
            ''])

    def test_due_by_and_json(self):
        self.put('decisions/LOG.md', self.LOG)
        self.build()
        code, out, _ = self.run_cli('revisit', '--due', '--by', '2026-12-31', '--json')
        r = json.loads(out)
        self.assertEqual((code, r['profile'], r['today']), (0, 'default', '2026-12-31'))
        self.assertEqual([(e['line'], e['date'], e['title'], e['state']) for e in r['revisit']],
                         [(6, '2026-10-01', 'D1 cache size', 'due'), (19, '2026-12-24', 'D3 tiles', 'due')])

    def test_nothing_found_and_errors(self):
        code, out, err = self.run_cli('revisit')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('no index for profile default yet, run leks build', err)
        self.put('a.md', '# A\n\nNothing to revisit.\n')
        self.build()
        code, out, _ = self.run_cli('revisit')
        self.assertEqual(code, 0)
        self.assertIn('no revisit lines found', out)
        self.assertEqual(self.run_cli('revisit', '--by', '13.11.2026')[0], 1)

    def test_a_note_turned_into_a_link_out_of_its_root_is_left_out(self):
        if not hasattr(os, 'symlink'):
            self.skipTest('no symlinks here')
        self.put('a.md', '# A\n\nRevisit: 2026-01-01\n')
        self.put('b.md', '# B\n\nRevisit: 2026-01-03\n')
        self.build()
        outside = os.path.join(self.tmp, 'private.md')
        with open(outside, 'w', encoding='utf-8') as f:
            f.write('# Private heading\n\nRevisit: 2026-01-02 OUTSIDE-ROOT-TEXT\n')
        a = os.path.join(self.root, 'a.md')
        os.remove(a)
        try:
            os.symlink(outside, a)
        except OSError:
            self.skipTest('symlinks are not allowed here')
        code, out, _ = self.run_cli('revisit', '--by', '2026-11-13')
        self.assertEqual(code, 0)
        self.assertNotIn('OUTSIDE', out)
        self.assertNotIn('Private', out)
        self.assertIn('due: notes/b.md:3 | 2026-01-03 | B | note, unknown', out)
        c = sqlite3.connect(config.load().profile_db('default'))
        self.addCleanup(c.close)
        self.assertEqual([e['path'] for e in revisit.entries(c, config.load(), '2026-11-13')], ['notes/b.md'])

    def test_the_brief_names_each_due_entry(self):
        self.put('decisions/LOG.md', self.LOG)
        self.assertEqual(self.due(), 'notes/decisions/LOG.md:6 (2026-10-01)')


if __name__ == '__main__':
    unittest.main()
