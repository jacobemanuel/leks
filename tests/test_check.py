"""Tests for leks check, the notes check a host runs when the agent finishes a turn."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

from leks import __main__ as cli
from leks import check, config

from tests import helpers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EM = chr(0x2014)   # built here so the test files stay ascii


class Vault(unittest.TestCase):
    """A temp notes folder as the current folder, a temp leks home for the record, no config."""

    def setUp(self):
        self.tmp = os.path.realpath(helpers.short_tmpdir())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.vault = os.path.join(self.tmp, 'vault')
        os.makedirs(self.vault)
        # a test run inside claude code must not see the outer project folder
        env = {k: v for k, v in os.environ.items() if not k.startswith('LEKS_') and k != 'CLAUDE_PROJECT_DIR'}
        env['LEKS_HOME'] = self.home
        env['LEKS_CONFIG'] = os.path.join(self.tmp, 'no-config.json')
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        here = os.getcwd()
        os.chdir(self.vault)
        self.addCleanup(os.chdir, here)
        self.tick = time.time() - 10000

    def put(self, rel, text):
        p = os.path.join(self.vault, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        # a new stamp every write, two writes can land inside one clock tick
        self.tick += 10
        os.utime(p, (self.tick, self.tick))
        return p

    def run_check(self, *args, stdin=b'', **kw):
        err = io.StringIO()
        code = check.run(*args, stdin=io.BytesIO(stdin), err=err, **kw)
        return code, err.getvalue().splitlines()

    def baseline(self, files):
        for rel, text in files.items():
            self.put(rel, text)
        self.assertEqual(self.run_check(), (0, []))


class FindsProblems(Vault):
    def test_first_run_records_then_reports_changed_notes(self):
        self.baseline({'hub.md': '# Hub\n\n[[a]]\n', 'a.md': '# A\n'})
        self.assertEqual(len(os.listdir(os.path.join(self.home, 'run'))), 1)
        self.put('a.md', '# A\n\nsee [[missing note]] and [[hub]]\nlong pause %s here %s twice\n' % (EM, EM))
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(lines, [
            'leks check: 2 problems in the notes, fix them before you stop',
            'a.md:3: dead link [[missing note]], nothing by that name',
            'a.md:4: em dash (2 in this note), use a comma, colon or brackets',
        ])
        # reported once: an unchanged note is not looked at again
        self.assertEqual(self.run_check(), (0, []))

    def test_empty_note_gets_one_line(self):
        self.baseline({'hub.md': '# Hub\n'})
        self.put('front.md', '---\ntitle: only front matter\n---\n\n  \n')
        self.put('zero.md', '')
        self.put('fine.md', '---\ntitle: x\n---\nbody\n')
        self.put('hub.md', '# Hub\n\n[[fine]]\n')
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(lines[1:], ['front.md: empty note, write it or delete it',
                                     'zero.md: empty note, write it or delete it'])

    def test_new_note_nothing_links_to(self):
        self.baseline({'hub.md': '# Hub\n'})
        self.put('lonely.md', '# Lonely\n\nlinks out to [[hub]] only\n')
        self.put('wiki.md', '# Wiki\n')
        self.put('md.md', '# Md\n')
        self.put('sub/README.md', '# Entry notes need no link\n')
        self.put('hub.md', '# Hub\n\n[[wiki]] and [the md one](md.md)\n')
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(lines, [
            'leks check: 1 problem in the notes, fix it before you stop',
            'lonely.md: new note that no other note links to, link it from a related note or an index',
        ])

    def test_a_link_to_itself_does_not_count(self):
        self.baseline({'hub.md': '# Hub\n'})
        self.put('self.md', '# Self\n\n[[self]]\n')
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn('self.md: new note that no other note links to', lines[1])

    def test_link_forms_that_resolve(self):
        self.baseline({
            'Sub/Deep Note.md': '# Deep\n', 'img/pic.png': 'x', 'other.md': '# Other\n',
            'n/a.md': '# A\n',
            'hub.md': '[[Deep Note]] [[pic.png]] [[other]] [[a]]\n',
        })
        body = '\n'.join([
            '[[Deep Note]] [[Sub/Deep Note]] [[deep note]] [[Deep Note|alias]] [[Deep Note#Heading]]',
            '[[#Local heading]] ![[pic.png]] ![[img/pic.png|200]] [[other.md]] [[../other]]',
            r'| [[other\|in a table]] |',
            '[x](../other.md) [x](<../Sub/Deep Note.md>) [x](../Sub/Deep%20Note.md "title") [x](../img)',
            '[web](https://example.com/x) [mail](mailto:a@example.com) [anchor](#top) [up](../../outside.md)',
            '`[[in code]]` and ``[[in code too]]``',
            '```',
            '[[in a fence]]',
            '```',
            '~~~text',
            '[[in a tilde fence]]',
            '~~~',
            '<!-- [[in a comment]] -->',
            '%% [[in an obsidian comment]]',
            'that runs on %%',
            '[[{{title}}]] [[<% tp.file.title %>]]',
            '',
        ])
        self.put('n/a.md', body)
        self.assertEqual(self.run_check(), (0, []))
        self.put('n/a.md', body + 'and one [[nope]] [nope too](nope.md) [[nope]]\n')
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(lines[1:], ['n/a.md:17: dead link [[nope]], nothing by that name',
                                     'n/a.md:17: dead link (nope.md), nothing by that name'])

    def test_removed_note_breaks_links_in_other_notes(self):
        self.baseline({'hub.md': '# Hub\n\nsee [[gone]]\n', 'gone.md': '# Gone\n'})
        # moved: a link by name still finds it
        os.makedirs(os.path.join(self.vault, 'archive'))
        os.rename(os.path.join(self.vault, 'gone.md'), os.path.join(self.vault, 'archive', 'gone.md'))
        self.assertEqual(self.run_check(), (0, []))
        os.remove(os.path.join(self.vault, 'archive', 'gone.md'))
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(lines[1:], ['hub.md:3: dead link [[gone]], its note was removed or renamed'])

    def test_twenty_lines_at_most(self):
        self.baseline({'hub.md': '# Hub\n'})
        self.put('hub.md', '# Hub\n' + ''.join('[[missing %02d]]\n' % i for i in range(30)))
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(len(lines), 20)
        self.assertEqual(lines[0], 'leks check: 30 problems in the notes, fix them before you stop')
        self.assertEqual(lines[1], 'hub.md:2: dead link [[missing 00]], nothing by that name')
        self.assertEqual(lines[-1], '... and 12 more')

    def test_changed_files_are_checked_even_on_the_first_run(self):
        self.put('a.md', '# A\n[[nope]]\n')
        code, lines = self.run_check(changed=['a.md'])
        self.assertEqual(code, 2)
        self.assertEqual(lines[1:], ['a.md:2: dead link [[nope]], nothing by that name'])
        # the record was kept all the same, so the next plain run has a base
        self.assertEqual(self.run_check(), (0, []))

    def test_folders_named_like_build_output_and_dot_folders_are_skipped(self):
        self.baseline({'hub.md': '# Hub\n'})
        self.put('node_modules/pkg/README.md', '[[nope]]\n')
        self.put('.obsidian/x.md', '[[nope]]\n')
        self.assertEqual(self.run_check(), (0, []))

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'needs fifos')
    def test_a_pipe_named_like_a_note_is_never_read(self):
        self.baseline({'hub.md': '# Hub\n'})
        os.mkfifo(os.path.join(self.vault, 'pipe.md'))
        self.assertEqual(self.run_check(), (0, []))

    def test_an_unfinished_scan_names_no_orphan(self):
        self.baseline({'hub.md': '# Hub\n'})
        self.put('lonely.md', '# Lonely\n')
        with mock.patch.object(check, 'SCAN_SECS', -1.0):
            self.assertEqual(self.run_check(), (0, []))


class EmDashSwitch(Vault):
    def setUp(self):
        super().setUp()
        self.baseline({'hub.md': '# Hub\n\n[[a]]\n', 'a.md': '# A\n'})
        self.put('a.md', '# A\n\na pause %s here\n' % EM)

    def write_config(self, check_section):
        d = config.default_dict()
        d['home'] = self.home
        if check_section is not None:
            d['check'] = check_section
        path = os.environ['LEKS_CONFIG']
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(d, f)

    def test_on_by_default(self):
        code, lines = self.run_check(changed=['a.md'])
        self.assertEqual((code, lines[1:]), (2, ['a.md:3: em dash, use a comma, colon or brackets']))

    def test_flag_turns_it_off(self):
        self.assertEqual(self.run_check(changed=['a.md'], em_dash=False), (0, []))
        with mock.patch.object(sys, 'stdin', io.StringIO('')):
            code, out, err = run_cli('check', '--changed', 'a.md', '--no-em-dash')
        self.assertEqual((code, out, err), (0, '', ''))
        with mock.patch.object(sys, 'stdin', io.StringIO('')):
            code, out, err = run_cli('check', '--changed', 'a.md')
        self.assertEqual((code, out), (2, ''))
        self.assertIn('a.md:3: em dash', err)

    def test_config_key_turns_it_off(self):
        self.write_config({'em_dash': False})
        self.assertEqual(self.run_check(changed=['a.md']), (0, []))
        self.write_config({'em_dash': True})
        self.assertEqual(self.run_check(changed=['a.md'])[0], 2)
        self.write_config(None)
        self.assertEqual(self.run_check(changed=['a.md'])[0], 2)

    def test_a_broken_config_does_not_stop_the_check(self):
        with open(os.environ['LEKS_CONFIG'], 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertEqual(self.run_check(changed=['a.md'])[0], 2)


class ConfigSection(unittest.TestCase):
    def base(self):
        d = config.default_dict()
        d['home'] = '/tmp/leks-test-home'
        return d

    def test_optional_and_on_by_default(self):
        self.assertNotIn('check', config.default_dict())
        cfg = config.from_dict(self.base())
        self.assertEqual(cfg.check, {'em_dash': True})
        self.assertEqual(cfg.to_dict()['check'], {'em_dash': True})

    def test_read_and_checked(self):
        d = self.base()
        d['check'] = {'em_dash': False}
        cfg = config.from_dict(d)
        self.assertIs(cfg.check['em_dash'], False)
        self.assertEqual(config.from_dict(cfg.to_dict()).check, {'em_dash': False})
        d['check'] = {'em_dash': 'no'}
        with self.assertRaisesRegex(config.ConfigError, 'check.em_dash must be true or false'):
            config.from_dict(d)
        d['check'] = {'em_dashes': False}
        with self.assertRaisesRegex(config.ConfigError, 'unknown key in check: em_dashes'):
            config.from_dict(d)
        d['check'] = []
        with self.assertRaisesRegex(config.ConfigError, 'check must be an object'):
            config.from_dict(d)


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Command(Vault):
    def test_parser(self):
        a = cli._parser().parse_args(['check', '--root', 'a', 'b', '--root', 'c', '--changed', 'x.md',
                                      '--no-em-dash'])
        self.assertEqual((a.root, a.changed, a.no_em_dash), (['a', 'b', 'c'], ['x.md'], True))
        a = cli._parser().parse_args(['check'])
        self.assertEqual((a.root, a.changed, a.no_em_dash), (None, None, False))

    def test_a_bad_flag_is_not_a_block(self):
        # 2 would hold every turn of the host, so a usage error is 1 here
        code, out, err = run_cli('check', '--roots', 'x')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('unrecognized arguments', err)
        self.assertEqual(run_cli('search')[0], 2)

    def test_root_flag(self):
        notes = os.path.join(self.tmp, 'elsewhere')
        os.makedirs(notes)
        with open(os.path.join(notes, 'a.md'), 'w', encoding='utf-8') as f:
            f.write('[[nope]]\n')
        code, lines = self.run_check(roots=[notes], changed=[os.path.join(notes, 'a.md')])
        self.assertEqual(code, 2)
        # outside the current folder the path is shown in full
        self.assertEqual(lines[1], os.path.join(notes, 'a.md') + ':1: dead link [[nope]], nothing by that name')

    def test_missing_root_or_a_huge_tree_is_exit_1(self):
        code, lines = self.run_check(roots=[os.path.join(self.tmp, 'nope')])
        self.assertEqual(code, 1)
        self.assertEqual(lines, ['leks check: no such folder: ' + os.path.join(self.tmp, 'nope')])
        for i in range(5):
            self.put('n%d.md' % i, 'x\n')
        with mock.patch.object(check, 'MAX_FILES', 3):
            code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        self.assertIn('more than 3 files under', lines[0])

    def test_no_place_for_the_record_is_exit_1(self):
        with open(self.home, 'w') as f:
            f.write('a file where the home folder should be')
        code, lines = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('leks check: cannot keep its record in'))

    def test_a_held_stop_passes_and_keeps_the_record(self):
        self.baseline({'hub.md': '# Hub\n\n[[a]]\n', 'a.md': '# A\n'})
        self.put('a.md', '# A\n[[nope]]\n')
        for held in (b'{"stop_hook_active": true}', b'{"stopHookActive": true}'):
            self.assertEqual(self.run_check(stdin=held), (0, []))
        code, lines = self.run_check(stdin=b'{"stop_hook_active": false}')
        self.assertEqual((code, lines[1:]), (2, ['a.md:2: dead link [[nope]], nothing by that name']))


class HookInput(unittest.TestCase):
    def test_json_object_only(self):
        self.assertEqual(check.hook_input(io.BytesIO(b'{"stop_hook_active": true}')), {'stop_hook_active': True})
        self.assertEqual(check.hook_input(io.StringIO('{"a": 1}')), {'a': 1})
        for raw in (b'', b'not json', b'[1, 2]', b'"x"', b'{"a": ' + b'1' * (check.MAX_INPUT + 1) + b'}'):
            self.assertEqual(check.hook_input(io.BytesIO(raw)), {})

    def test_a_terminal_is_never_read(self):
        class Tty(io.BytesIO):
            def isatty(self):
                return True
        self.assertEqual(check.hook_input(Tty(b'{"stop_hook_active": true}')), {})

    def test_a_pipe_that_never_closes_costs_the_wait_only(self):
        r, w = os.pipe()
        self.addCleanup(os.close, w)
        with os.fdopen(r, 'rb') as stream:
            t0 = time.monotonic()
            self.assertEqual(check.hook_input(stream, wait=0.2), {})
            self.assertLess(time.monotonic() - t0, 2.0)
        r2, w2 = os.pipe()
        self.addCleanup(os.close, w2)
        os.write(w2, b'{"stop_hook_active": true}')
        with os.fdopen(r2, 'rb') as stream:
            self.assertEqual(check.hook_input(stream, wait=0.2), {'stop_hook_active': True})


class AsTheHostRunsIt(Vault):
    """python3 -m leks check in a child, hook json on stdin, the way Claude Code and Copilot call it."""

    def hook(self, held):
        env = dict(os.environ)
        env['PYTHONPATH'] = REPO
        p = subprocess.run([sys.executable, '-B', '-m', 'leks', 'check'], cwd=self.vault, env=env,
                           input=json.dumps({'hook_event_name': 'Stop', 'stop_hook_active': held}),
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def test_findings_on_stderr_exit_2_and_one_hold_per_turn(self):
        self.put('hub.md', '# Hub\n\n[[a]]\n')
        self.put('a.md', '# A\n')
        self.assertEqual(self.hook(False), (0, '', ''))
        self.put('a.md', '# A\n\n[[nope]]\n')
        code, out, err = self.hook(False)
        self.assertEqual((code, out), (2, ''))
        self.assertEqual(err.splitlines(), [
            'leks check: 1 problem in the notes, fix it before you stop',
            'a.md:3: dead link [[nope]], nothing by that name',
        ])
        # the host holds the stop once: the next stop of that turn goes through, whatever changed
        self.put('hub.md', '# Hub\n\n[[a]] [[also nope]]\n')
        self.assertEqual(self.hook(True), (0, '', ''))
        # and what changed during the fix is checked at the next stop
        code, out, err = self.hook(False)
        self.assertEqual((code, out), (2, ''))
        self.assertEqual(err.splitlines()[1:], ['hub.md:3: dead link [[also nope]], nothing by that name'])


class ProjectFolder(Vault):
    """claude code runs the stop hook in the session's current folder, and a cd in its shell moves that."""

    STOP = json.dumps({'hook_event_name': 'Stop', 'stop_hook_active': False}).encode()

    def setUp(self):
        super().setUp()
        os.environ['CLAUDE_PROJECT_DIR'] = self.vault
        os.makedirs(os.path.join(self.vault, 'code'))

    def test_a_hook_run_after_a_cd_still_checks_the_project(self):
        self.put('hub.md', '# Hub\n\n[[a]]\n')
        self.put('a.md', '# A\n')
        self.assertEqual(self.run_check(stdin=self.STOP), (0, []))
        os.chdir(os.path.join(self.vault, 'code'))
        self.put('a.md', '# A\n\n[[nope]]\n')
        code, lines = self.run_check(stdin=self.STOP)
        self.assertEqual(code, 2)
        self.assertEqual(lines[1:], ['a.md:3: dead link [[nope]], nothing by that name'])

    def test_a_relative_root_in_a_hook_run_is_read_from_the_project(self):
        self.put('notes/hub.md', '# Hub\n\n[[a]]\n')
        self.put('notes/a.md', '# A\n')
        os.chdir(os.path.join(self.vault, 'code'))
        self.assertEqual(self.run_check(['notes'], stdin=self.STOP), (0, []))
        self.put('notes/a.md', '# A\n\n[[nope]]\n')
        code, lines = self.run_check(['notes'], stdin=self.STOP)
        self.assertEqual(code, 2)
        self.assertEqual(lines[1:], [os.path.join('notes', 'a.md') + ':3: dead link [[nope]], nothing by that name'])

    def test_a_terminal_run_keeps_the_current_folder(self):
        self.put('notes/a.md', '# A\n')
        os.chdir(os.path.join(self.vault, 'code'))
        code, lines = self.run_check(['notes'])
        self.assertEqual(code, 1)
        self.assertEqual(lines, ['leks check: no such folder: notes'])

    def test_a_project_folder_that_is_gone_falls_back_to_the_current_one(self):
        os.environ['CLAUDE_PROJECT_DIR'] = os.path.join(self.tmp, 'gone')
        self.put('a.md', '# A\n')
        self.assertEqual(self.run_check(stdin=self.STOP), (0, []))
        self.put('a.md', '# A\n\n[[nope]]\n')
        code, lines = self.run_check(stdin=self.STOP)
        self.assertEqual((code, lines[1:]), (2, ['a.md:3: dead link [[nope]], nothing by that name']))


if __name__ == '__main__':
    unittest.main()
