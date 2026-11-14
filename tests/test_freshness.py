"""Tests for freshness budgets: the config section and the session brief line, ages from git or the file time."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import unittest
from unittest import mock

from leks import build, config
from leks.hooks import session_brief as sb

from tests import helpers

DAY = 86400
HEAD = 'Past their freshness budget: '


class Budgets(unittest.TestCase):
    """A notes root of its own, built by the real builder, and the brief for it."""

    def setUp(self):
        self.tmp = os.path.realpath(helpers.short_tmpdir())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, 'notes')
        os.makedirs(self.root)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('LEKS_', 'GIT_'))}
        # git here reads no config of the machine, so the tests mean the same everywhere
        env.update(LEKS_HOME=os.path.join(self.tmp, 'home'), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.now = time.time()

    def put(self, rel, days, text=None):
        p = os.path.join(self.root, *rel.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text or '# %s\n\nSome text.\n' % rel)
        t = self.now - days * DAY
        os.utime(p, (t, t))

    def brief(self, fresh):
        d = config.default_dict()
        d['home'] = os.path.join(self.tmp, 'home')
        d['roots'] = [{'name': 'notes', 'path': self.root, 'kind': 'notes'}]
        d['profiles'] = {}
        d['models']['embedder'] = None
        d['models']['reranker'] = None
        if fresh is not None:
            d['freshness'] = fresh
        cfg = config.from_dict(d)
        build.update_all(cfg, log=lambda *a: None)
        return sb.brief(cfg, 'default', now=self.now)

    def late(self, fresh):
        got = [ln[len(HEAD):] for ln in self.brief(fresh).split('\n') if ln.startswith(HEAD)]
        return got[0] if got else None


class FileTime(Budgets):
    def test_by_class(self):
        self.put('STATE.md', 20)
        self.put('NOW.md', 5)
        self.put('ideas.md', 400)
        self.assertEqual(self.late({'classes': {'state': 14}, 'git': False}), 'notes/STATE.md (20 days, budget 14)')

    def test_a_path_glob_beats_the_class_and_the_smallest_glob_wins(self):
        self.put('projects/x/STATUS.md', 10)
        self.put('projects/x/plan.md', 40)
        self.put('other.md', 61)
        self.put('projects/y/plan.md', 20)
        fresh = {'classes': {'note': 60}, 'paths': {'projects/*/STATUS.md': 7, 'projects/**': 30}, 'git': False}
        # one class, so the note furthest past its budget comes first: 10/7, 40/30, 61/60
        self.assertEqual(self.late(fresh), 'notes/projects/x/STATUS.md (10 days, budget 7); '
                                           'notes/projects/x/plan.md (40 days, budget 30); '
                                           'notes/other.md (61 days, budget 60)')

    def test_most_important_first(self):
        self.put('ideas.md', 300)
        self.put('decisions/DEC-1.md', 100)
        self.put('STATE.md', 15)
        self.put('CLAUDE.md', 40)
        self.put('results/run.md', 50)
        fresh = {'classes': {'note': 30, 'decision': 90, 'state': 14, 'rule': 30, 'result': 30}, 'git': False}
        self.assertEqual(self.late(fresh), 'notes/STATE.md (15 days, budget 14); notes/CLAUDE.md (40 days, budget 30); '
                                           'notes/decisions/DEC-1.md (100 days, budget 90); '
                                           'notes/results/run.md (50 days, budget 30); '
                                           'notes/ideas.md (300 days, budget 30)')

    def test_a_note_with_a_newer_version_has_no_budget(self):
        self.put('plan.md', 50, '# Plan\n\nSuperseded by [[plan-2]].\n')
        self.put('plan-2.md', 50)
        self.assertEqual(self.late({'classes': {'note': 10}, 'git': False}), 'notes/plan-2.md (50 days, budget 10)')

    def test_at_most_six_most_overdue_first(self):
        for i in range(8):
            self.put('%s_STATE.md' % 'ABCDEFGH'[i], 20 + i)
        got = self.late({'classes': {'state': 14}, 'git': False}).split('; ')
        self.assertEqual(len(got), sb.MAX_LIST)
        self.assertEqual(got[0], 'notes/H_STATE.md (27 days, budget 14)')
        self.assertEqual(got[-1], 'notes/C_STATE.md (22 days, budget 14)')

    def test_records_superseded_stale_and_history_are_left_out(self):
        self.put('a.md', 50, '---\nstatus: done\n---\n# A\n')
        self.put('b.md', 50, '# B\n\nStatus: superseded by [[c]]\n')
        self.put('archive/c.md', 50)
        self.put('d.md', 50)
        self.put('e.md', 50, '---\nstatus: stale\n---\n# E\n')
        self.assertEqual(self.late({'paths': {'**/*.md': 10}, 'git': False}), 'notes/d.md (50 days, budget 10)')

    def test_no_line_when_nothing_is_over_or_nothing_is_set(self):
        self.put('STATE.md', 14)
        brief = self.brief({'classes': {'state': 14}, 'git': False})
        self.assertNotIn(HEAD, brief)
        self.assertNotIn(HEAD, self.brief(None))
        self.assertEqual(brief, sb.FIRST + '\nState notes: notes/STATE.md')


@unittest.skipIf(shutil.which('git') is None, 'git not installed')
class Git(Budgets):
    def git(self, *args, days=None):
        env = dict(os.environ)
        if days is not None:
            env['GIT_AUTHOR_DATE'] = env['GIT_COMMITTER_DATE'] = '@%d +0000' % int(self.now - days * DAY)
        subprocess.run(('git', '-C', self.root, '-c', 'user.name=t', '-c', 'user.email=t@example.org',
                        '-c', 'commit.gpgsign=false') + args, check=True, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def setUp(self):
        super().setUp()
        # every file looks fresh on disk, as after a clone
        self.put('STATE.md', 0)
        self.put('NOW.md', 0)
        self.put('projects/x/STATUS.md', 0)
        self.git('init', '-q')
        self.git('add', '.')
        self.git('commit', '-qm', 'notes', days=30)
        self.fresh = {'classes': {'state': 14}, 'paths': {'projects/*/STATUS.md': 7}}

    def test_age_from_the_last_commit(self):
        self.git('commit', '-q', '--allow-empty', '-m', 'later', days=2)
        self.assertEqual(self.late(self.fresh), 'notes/NOW.md (30 days, budget 14); notes/STATE.md (30 days, budget 14); '
                                                'notes/projects/x/STATUS.md (30 days, budget 7)')

    def test_a_meta_commit_does_not_count(self):
        for rel in ('STATE.md', 'NOW.md'):
            with open(os.path.join(self.root, rel), 'a', encoding='utf-8') as f:
                f.write('\nsee [[hub]]\n')
        self.git('commit', '-qam', 'fix links', '-m', 'Meta: true', days=1)
        self.git('commit', '-q', '--allow-empty', '-m', 'x', days=1)
        with open(os.path.join(self.root, 'NOW.md'), 'a', encoding='utf-8') as f:
            f.write('\nreal change\n')
        self.git('commit', '-qam', 'update now', '-m', 'meta: no', days=3)
        self.assertEqual(self.late(self.fresh), 'notes/STATE.md (30 days, budget 14); '
                                                'notes/projects/x/STATUS.md (30 days, budget 7)')

    def test_a_change_not_committed_takes_the_file_time(self):
        self.put('STATE.md', 1, '# State\n\nchanged, not committed\n')
        self.assertEqual(self.late(self.fresh), 'notes/NOW.md (30 days, budget 14); '
                                                'notes/projects/x/STATUS.md (30 days, budget 7)')

    def test_a_long_path_list_goes_to_git_in_parts(self):
        # windows cuts a command line at 32,767 characters, so no git call may carry more paths than ARG_CHARS
        calls, real = [], sb._git

        def spy(folder, args, end, feed=None):
            calls.append(args[args.index('--') + 1:])
            return real(folder, args, end, feed)

        with mock.patch.object(sb, 'ARG_CHARS', 20), mock.patch.object(sb, '_git', spy):
            got = self.late(self.fresh)
        self.assertEqual(got, 'notes/NOW.md (30 days, budget 14); notes/STATE.md (30 days, budget 14); '
                              'notes/projects/x/STATUS.md (30 days, budget 7)')
        self.assertGreater(len(calls), 2)
        for paths in calls:
            self.assertTrue(len(paths) == 1 or sum(len(p) + 1 for p in paths) <= 20, paths)
        with mock.patch.object(sb, 'ARG_CHARS', 20):
            self.assertEqual(sb._batches(['a' * 30, 'b', 'c', 'd' * 18]), [['a' * 30], ['b', 'c'], ['d' * 18]])

    def test_git_off_or_out_of_time_takes_the_file_time(self):
        self.assertIsNone(self.late(dict(self.fresh, git=False)))
        with mock.patch.object(sb, 'GIT_SECS', 0.0):
            self.assertIsNone(self.late(self.fresh))

    def test_git_reads_nothing_from_a_repo_named_in_the_environment(self):
        other = os.path.join(self.tmp, 'other')
        os.makedirs(other)
        subprocess.run(('git', 'init', '-q', other), check=True)
        with mock.patch.dict(os.environ, {'GIT_DIR': os.path.join(other, '.git')}):
            self.assertIn('notes/STATE.md (30 days, budget 14)', self.late(self.fresh))


class ConfigKeys(unittest.TestCase):
    def base(self, fresh):
        d = config.default_dict()
        d['home'] = '/tmp/leks-test-home'
        if fresh is not None:
            d['freshness'] = fresh
        return d

    def test_defaults_and_round_trip(self):
        self.assertNotIn('freshness', config.default_dict())
        self.assertEqual(config.from_dict(self.base(None)).freshness, {'classes': {}, 'paths': {}, 'git': True})
        fresh = {'classes': {'state': 14, 'decision': 365}, 'paths': {'projects/*/STATUS.md': 7}, 'git': False}
        cfg = config.from_dict(self.base(fresh))
        self.assertEqual(cfg.freshness, fresh)
        self.assertEqual(config.from_dict(cfg.to_dict()).freshness, fresh)
        # a section given in part keeps the other defaults
        self.assertEqual(config.from_dict(self.base({'classes': {'state': 3}})).freshness,
                         {'classes': {'state': 3}, 'paths': {}, 'git': True})

    def test_checked(self):
        bad = [
            ({'classes': {'status': 14}}, 'unknown class in freshness.classes: status'),
            ({'classes': {'frozen': 14}}, 'freshness.classes: frozen notes keep what held'),
            ({'classes': {'state': 0}}, 'freshness.classes.state must be an integer from 1 to 36500'),
            ({'classes': {'state': True}}, 'freshness.classes.state must be an integer'),
            ({'classes': {'state': 36501}}, 'freshness.classes.state must be an integer from 1 to 36500'),
            ({'paths': {'../x': 7}}, 'freshness.paths'),
            ({'paths': ['a']}, 'freshness.paths must be an object of name: days'),
            ({'git': 'yes'}, 'freshness.git must be true or false'),
            ({'days': 3}, 'unknown key in freshness: days'),
            ([], 'freshness must be an object'),
        ]
        for fresh, msg in bad:
            with self.subTest(fresh=fresh), self.assertRaisesRegex(config.ConfigError, msg):
                config.from_dict(self.base(fresh))
        with self.assertRaisesRegex(config.ConfigError, 'at most 200'):
            config.from_dict(self.base({'paths': {'a%d.md' % i: 1 for i in range(201)}}))


if __name__ == '__main__':
    unittest.main()
