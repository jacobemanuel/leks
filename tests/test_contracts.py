"""Tests for the edit contracts of leks check: write-once, living and frozen notes, and --accept."""
from __future__ import annotations

import io
import json
import os
import shlex
import sys
import unittest
from unittest import mock

from leks import check, config

from tests.test_check import Vault, run_cli

DECISION = '---\nstatus: accepted\n---\n# DEC-1\n\nWe keep 128 MB per scene, see [[budget]].\n'
RULES = [
    {'glob': 'decisions/**', 'type': 'write-once'},
    {'glob': 'sources/*.md', 'type': 'frozen'},
    {'glob': '**/STATE.md', 'type': 'living'},
]


class Contracts(Vault):
    def setUp(self):
        super().setUp()
        self.write_config(RULES)

    def write_config(self, contracts, broken=False):
        d = config.default_dict()
        d['home'] = self.home
        d['check'] = {'contracts': contracts}
        with open(os.environ['LEKS_CONFIG'], 'w', encoding='utf-8') as f:
            f.write('{"version": 3,' if broken else json.dumps(d))

    def record(self):
        run = os.path.join(self.home, 'run')
        names = [n for n in os.listdir(run) if n.startswith('check-')]
        self.assertEqual(len(names), 1)
        with open(os.path.join(run, names[0]), encoding='utf-8') as f:
            return json.load(f)

    def accept(self, *paths, roots=None):
        err = io.StringIO()
        return check.accept(list(paths), roots, err=err), err.getvalue().splitlines()

    def start(self, extra=None):
        files = {'hub.md': '# Hub\n\n[[DEC-1]] [[budget]] [[STATE]] [[a]] [[DEC-2]]\n',
                 'budget.md': '# Budget\n', 'decisions/DEC-1.md': DECISION, 'decisions/DEC-2.md': '# DEC-2\n',
                 'STATE.md': '# State\n\nAll good.\n', 'sources/a.md': 'imported\n'}
        files.update(extra or {})
        self.baseline(files)


class WriteOnce(Contracts):
    def test_first_run_keeps_the_text_then_a_body_change_is_reported(self):
        self.start()
        self.assertEqual(self.record()['v'], 2)
        self.put('decisions/DEC-1.md', DECISION.replace('128 MB', '256 MB'))
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(lines[1:], [
            'decisions/DEC-1.md:6: write-once note changed, it is a record: restore its text and put the change '
            'in a new note, or keep it with leks check --accept decisions/DEC-1.md'])
        # back to the kept text: clean again
        self.put('decisions/DEC-1.md', DECISION)
        self.assertEqual(self.run_check(), (0, []))

    def test_front_matter_status_lines_and_link_targets_may_change(self):
        self.start({'budget2.md': '# Budget 2\n[[hub]]\n',
                    'decisions/DEC-6.md': '# DEC-6\n\nSee [the budget](../budget.md) and [[hub]].\n'})
        changed = ('---\nstatus: superseded\nupdated: 2026-11-13\n---\n# DEC-1\n\n'
                   '> [!warning] Superseded by [[DEC-2]]\n**Status:** superseded\n\n'
                   'We keep 128 MB per scene, see [[budget2]].   \n\n\n')
        self.put('decisions/DEC-1.md', changed)
        # a markdown link target too, also in its bracket form
        self.put('decisions/DEC-6.md', '# DEC-6\n\nSee [the budget](<../budget2.md>) and [[hub#Top]].\n')
        self.assertEqual(self.run_check(), (0, []))
        # the link text is body
        self.put('decisions/DEC-6.md', '# DEC-6\n\nSee [the new budget](<../budget2.md>) and [[hub#Top]].\n')
        self.assertEqual(self.run_check()[0], 2)

    def test_a_sentence_that_starts_with_superseded_is_body(self):
        self.start({'decisions/DEC-3.md': '# DEC-3\n\nSuperseded designs had one cache.\n'})
        self.put('decisions/DEC-3.md', '# DEC-3\n\nSuperseded designs had two caches.\n')
        self.assertEqual(self.run_check()[0], 2)

    def test_an_empty_note_gets_its_baseline_once_it_has_a_body(self):
        self.start({'decisions/DEC-4.md': '---\nstatus: draft\n---\n'})
        self.put('decisions/DEC-4.md', '# DEC-4\n\nWe pick the second option.\n')
        self.assertEqual(self.run_check(), (0, []))
        self.put('decisions/DEC-4.md', '# DEC-4\n\nWe pick the first option.\n')
        self.assertEqual(self.run_check()[0], 2)

    def test_a_new_note_is_kept_at_its_first_sighting(self):
        self.start()
        self.put('decisions/DEC-5.md', '# DEC-5\n\nNew record, linked from [[hub]].\n')
        self.put('hub.md', '# Hub\n\n[[DEC-1]] [[budget]] [[STATE]] [[a]] [[DEC-2]] [[DEC-5]]\n')
        self.assertEqual(self.run_check(), (0, []))
        self.put('decisions/DEC-5.md', '# DEC-5\n\nEdited record, linked from [[hub]].\n')
        self.assertEqual(self.run_check()[0], 2)


class FirstChangedLine(Contracts):
    BODY = '---\nstatus: accepted\n---\n# DEC-7\n\nOne.\n\nTwo, see [[hub]].\nThree.\n'

    def check_line(self, text):
        self.put('decisions/DEC-7.md', text)
        code, lines = self.run_check()
        self.assertEqual(code, 2, lines)
        return lines[1].split(':')[1]

    def test_the_finding_names_the_first_changed_line(self):
        self.start({'decisions/DEC-7.md': self.BODY})
        self.assertEqual(self.check_line(self.BODY.replace('Three.', 'Four.')), '9')
        # a new status line and a moved link target are no change, the line after them is
        self.assertEqual(self.check_line(self.BODY.replace('---\n# DEC-7', '---\n# DEC-7\nStatus: done')
                                         .replace('[[hub]]', '[[budget]]').replace('Three.', 'Three!')), '10')
        # a line added at the end, and lines taken from the end
        self.assertEqual(self.check_line(self.BODY + 'Five.\n'), '10')
        self.assertEqual(self.check_line(self.BODY.replace('Three.\n', '')), '8')

    def test_a_kept_text_after_accept_names_its_own_lines(self):
        self.start({'decisions/DEC-7.md': self.BODY})
        self.put('decisions/DEC-7.md', 'Intro.\n' + self.BODY)
        self.assertEqual(self.accept('decisions/DEC-7.md')[0], 0)
        self.assertEqual(self.check_line('Intro.\n' + self.BODY.replace('One.', 'Uno.')), '7')

    def test_a_record_without_line_hashes_names_no_line(self):
        self.start({'decisions/DEC-7.md': self.BODY})
        rec = self.record()
        for c in rec['contracts'].values():
            c.pop('lines')
        run = os.path.join(self.home, 'run')
        name = [n for n in os.listdir(run) if n.startswith('check-')][0]
        with open(os.path.join(run, name), 'w', encoding='utf-8') as f:
            json.dump(rec, f)
        self.put('decisions/DEC-7.md', self.BODY.replace('Three.', 'Four.'))
        code, lines = self.run_check()
        self.assertTrue(lines[1].startswith('decisions/DEC-7.md: write-once note changed'), lines)


class KeepFields(Contracts):
    def setUp(self):
        super().setUp()
        self.write_config([{'glob': 'decisions/**', 'keep': ['status', 'Date', 'revisit'], 'successor': True}])

    def test_a_changed_note_keeps_its_fields(self):
        self.start({'decisions/DEC-1.md': '---\nstatus: accepted\ndate: 2026-11-01\n---\n# DEC-1\n\n'
                                          'Revisit: 2027-01-01\n'})
        self.put('decisions/DEC-1.md', '---\nstatus: accepted\n---\n# DEC-1\n\nWe keep it.\n')
        code, lines = self.run_check()
        self.assertEqual((code, lines[1:]), (2, [
            'decisions/DEC-1.md:1: missing fields date and revisit, the contract for decisions/** keeps '
            'status, date and revisit']))

    def test_where_a_field_may_stand(self):
        self.start()
        for text in ('---\nStatus: accepted\ndate: 2026-11-01\nrevisit:\n  - when the cache lands\n---\n# D\n',
                     '# D\n\n**Status:** accepted\n- **Date**: 2026-11-01\n> Revisit: when the cache lands\n',
                     '# D\n\nStatus: accepted\nDate: 2026-11-01\n\n## Revisit\n\nWhen the cache lands.\n'):
            with self.subTest(text=text):
                self.put('decisions/DEC-1.md', text)
                self.assertEqual(self.run_check(), (0, []))

    def test_what_is_no_field(self):
        self.start()
        for text in ('# D\n\nStatus: accepted\nDate:\nRevisit: 2027-01-01\n',
                     '# D\n\nStatus: accepted\nRevisit: 2027-01-01\n```\nDate: 2026-11-01\n```\n',
                     '# D\n\nStatus: accepted\nRevisit: 2027-01-01\nSee `Date: 2026-11-01` and https://x.org/date\n'):
            with self.subTest(text=text):
                self.put('decisions/DEC-1.md', text)
                code, lines = self.run_check()
                self.assertEqual(code, 2)
                self.assertIn('missing field date,', lines[1])

    def test_windows_line_ends_read_like_plain_ones(self):
        # a note saved with \r\n. before the fix `Date:` with nothing after it counted as filled
        self.start()
        p = os.path.join(self.vault, 'decisions', 'DEC-1.md')
        for text, code in (('# D\r\n\r\nStatus: accepted\r\nDate:\r\nRevisit: 2027-01-01\r\n', 2),
                           ('# D\r\n\r\nStatus: accepted\r\nDate: 2026-11-01\r\n\r\n## Revisit\r\n\r\n'
                            'When the cache lands.\r\n', 0)):
            with self.subTest(text=text):
                with open(p, 'wb') as f:
                    f.write(text.encode('utf-8'))
                self.tick += 10
                os.utime(p, (self.tick, self.tick))
                got, lines = self.run_check()
                self.assertEqual(got, code, lines)
                if code:
                    self.assertIn('decisions/DEC-1.md:1: missing field date,', lines[1])

    def test_a_superseded_note_names_its_successor(self):
        self.start()
        self.put('decisions/DEC-1.md', '# D\n\nDate: 2026-11-01\nRevisit: never\nStatus: superseded\n')
        code, lines = self.run_check()
        self.assertEqual((code, lines[1:]), (2, [
            'decisions/DEC-1.md:5: marked superseded but names no successor, add a superseded by line with a '
            'link to the note that replaces it']))
        for text in ('# D\n\nDate: 2026-11-01\nRevisit: never\nStatus: superseded by [[DEC-2]]\n',
                     '# D\n\nDate: 2026-11-01\nRevisit: never\nStatus: replaced\n\n> Replaced by DEC-2.\n',
                     '---\nstatus: superseded\nsuperseded_by: "[[DEC-2]]"\ndate: 2026-11-01\nrevisit: no\n---\n# D\n',
                     '---\nstatus: superseded\nsuperseded-by: "[[DEC-2]]"\ndate: 2026-11-01\nrevisit: no\n---\n# D\n',
                     '---\nstatus: replaced\nreplaced-by: DEC-2\ndate: 2026-11-01\nrevisit: no\n---\n# D\n',
                     '# D\n\nDate: 2026-11-01\nRevisit: never\nStatus: rejected\n'):
            with self.subTest(text=text):
                self.put('decisions/DEC-1.md', text)
                self.assertEqual(self.run_check(), (0, []))
        # a successor named only inside a code block does not count
        self.put('decisions/DEC-1.md', '# D\n\nDate: 2026-11-01\nRevisit: never\nStatus: superseded\n'
                                       '```\nsuperseded by DEC-2\n```\n')
        self.assertEqual(self.run_check()[0], 2)

    def test_the_first_glob_that_fits_wins_and_other_notes_are_free(self):
        self.write_config([{'glob': 'decisions/DEC-2.md', 'type': 'living'},
                           {'glob': 'decisions/**', 'keep': ['revisit']}])
        self.start()
        self.put('decisions/DEC-2.md', '# DEC-2\n\nNo revisit here.\n')
        self.put('budget.md', '# Budget\n\nStatus: superseded\n')
        self.assertEqual(self.run_check(), (0, []))
        self.put('decisions/DEC-1.md', DECISION + 'x\n')
        self.assertIn('missing field revisit', self.run_check()[1][1])


class Accept(Contracts):
    def test_accept_keeps_the_current_text(self):
        self.start()
        self.put('decisions/DEC-1.md', DECISION + '\nOne more line.\n')
        self.assertEqual(self.accept('decisions/DEC-1.md'),
                         (0, ['leks check: kept the current text of 1 note']))
        self.assertEqual(self.run_check(), (0, []))
        # the text before stays accepted as well
        self.put('decisions/DEC-1.md', DECISION)
        self.assertEqual(self.run_check(), (0, []))

    def test_five_texts_are_kept_newest_first(self):
        self.start()
        for i in range(1, 6):
            self.put('decisions/DEC-1.md', DECISION + '\nline %d\n' % i)
            self.assertEqual(self.accept('decisions/DEC-1.md')[0], 0)
        hashes = self.record()['contracts'][os.path.join(self.vault, 'decisions', 'DEC-1.md')]['hashes']
        self.assertEqual(len(hashes), check.KEEP)
        self.assertEqual(hashes[0], check._digest('write-once', DECISION + '\nline 5\n'))
        self.assertEqual(self.run_check(), (0, []))
        # the first text fell out, the oldest one still kept is fine
        self.put('decisions/DEC-1.md', DECISION)
        self.assertEqual(self.run_check()[0], 2)
        self.put('decisions/DEC-1.md', DECISION + '\nline 1\n')
        self.assertEqual(self.run_check(), (0, []))

    def test_accept_refuses_what_it_cannot_keep(self):
        self.start()
        self.assertEqual(self.accept('STATE.md')[1],
                         ['leks check: no write-once or frozen contract for STATE.md'])
        self.assertEqual(self.accept('nope.md')[0], 1)
        outside = os.path.join(self.tmp, 'other.md')
        with open(outside, 'w', encoding='utf-8') as f:
            f.write('x\n')
        code, lines = self.accept(outside)
        self.assertEqual(code, 1)
        self.assertIn('not under the checked folders', lines[0])
        self.write_config([])
        self.assertEqual(self.accept('decisions/DEC-1.md')[1], [
            'leks check: no write-once or frozen contract in the config (check.contracts), nothing to accept'])

    def test_accept_before_any_check(self):
        self.put('decisions/DEC-1.md', DECISION)
        code, lines = self.accept('decisions/DEC-1.md')
        self.assertEqual(code, 1)
        self.assertIn('no earlier check of', lines[0])
        self.assertIn('pass the same --root as the check', lines[0])

    def test_accept_with_a_root(self):
        notes = os.path.join(self.tmp, 'elsewhere')
        os.makedirs(os.path.join(notes, 'decisions'))
        p = os.path.join(notes, 'decisions', 'DEC-1.md')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(DECISION)
        with open(os.path.join(notes, 'budget.md'), 'w', encoding='utf-8') as f:
            f.write('# Budget\n')
        self.assertEqual(self.run_check(roots=[notes]), (0, []))
        with open(p, 'a', encoding='utf-8') as f:
            f.write('more\n')
        code, lines = self.run_check(roots=[notes], changed=[p])
        self.assertEqual(code, 2)
        # the hint names the same root, so the command runs as printed
        self.assertTrue(lines[1].endswith('--accept %s --root %s' % (shlex.quote(p), shlex.quote(notes))), lines[1])
        self.assertEqual(self.accept(p, roots=[notes])[0], 0)
        self.assertEqual(self.run_check(roots=[notes], changed=[p]), (0, []))

    def test_a_hook_run_from_another_folder_prints_a_command_that_runs_there(self):
        # the hook reads the project folder, the agent's shell sits in a sub folder: the printed command
        # names the note and the root in full, so it works from there as printed
        from leks import __main__ as cli
        stop = json.dumps({'hook_event_name': 'Stop', 'stop_hook_active': False}).encode()
        os.environ['CLAUDE_PROJECT_DIR'] = self.vault
        self.start()
        os.makedirs(os.path.join(self.vault, 'code'))
        os.chdir(os.path.join(self.vault, 'code'))
        self.put('decisions/DEC-1.md', DECISION + 'x\n')
        code, lines = self.run_check(stdin=stop)
        self.assertEqual(code, 2)
        p = os.path.join(self.vault, 'decisions', 'DEC-1.md')
        self.assertTrue(lines[1].startswith('decisions/DEC-1.md:7: write-once note changed'), lines[1])
        self.assertTrue(lines[1].endswith('keep it with leks check --accept %s --root %s'
                                          % (shlex.quote(p), shlex.quote(self.vault))), lines[1])
        a = cli._parser().parse_args(shlex.split(lines[1].split('keep it with leks ')[1]))
        self.assertEqual(self.accept(*a.accept, roots=a.root)[0], 0)
        self.assertEqual(self.run_check(stdin=stop, changed=[p]), (0, []))

    def test_parser_and_command(self):
        from leks import __main__ as cli
        a = cli._parser().parse_args(['check', '--accept', 'a.md', 'b.md', '--root', 'x'])
        self.assertEqual((a.accept, a.root), (['a.md', 'b.md'], ['x']))
        self.assertIsNone(cli._parser().parse_args(['check']).accept)
        self.start()
        self.put('decisions/DEC-1.md', DECISION + 'x\n')
        with mock.patch.object(sys, 'stdin', io.StringIO('')):
            self.assertEqual(run_cli('check', '--accept', 'decisions/DEC-1.md'),
                             (0, '', 'leks check: kept the current text of 1 note\n'))
            self.assertEqual(run_cli('check')[0], 0)
            self.assertEqual(run_cli('check', '--accept', 'STATE.md')[0], 1)


class Living(Contracts):
    def test_dated_lines_outside_a_log_section(self):
        self.start()
        self.put('STATE.md', '---\ndate: 2026-11-13\n---\n# State\n\nCache is 256 MB.\n'
                 '- 2026-11-12 moved to 256 MB\n1. **2026-11-10** first try\n'
                 '```\n2026-01-01 in a code block\n```\n'
                 '## Change log\n- 2026-11-01 started\n### Week 2\n2026-11-08 more\n'
                 '## Next\n2026-12-01 ship\n')
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(lines[1:], ['STATE.md:7: dated line outside a log section (3 in this note), '
                                     'rewrite it as what holds now or move it under a Log heading'])

    def test_all_dated_lines_in_logs_is_clean(self):
        self.start()
        self.put('STATE.md', '# State\n\nCache is 256 MB.\n\n## History\n2026-11-01 started\n'
                 '#### Deep\n- 2026-11-02 deeper\n## Journal\n* 2026-11-03 note\n')
        self.assertEqual(self.run_check(), (0, []))

    def test_first_run_reports_nothing(self):
        self.start({'docs/STATE.md': '# S\n2026-11-01 dated\n[[hub]]\n'})
        self.assertEqual(self.run_check(), (0, []))


class Source(Contracts):
    def test_any_change_is_reported(self):
        self.start()
        self.put('sources/a.md', 'imported \n')
        code, lines = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(lines[1:], ['sources/a.md:1: frozen note changed: restore its text, '
                                     'or keep the change with leks check --accept sources/a.md'])
        self.assertEqual(self.accept('sources/a.md')[0], 0)
        self.assertEqual(self.run_check(), (0, []))


class Record(Contracts):
    def path(self, rel):
        return os.path.join(self.vault, *rel.split('/'))

    def test_a_version_1_record_still_loads(self):
        self.write_config([])
        self.start()
        rec = self.record()
        run = os.path.join(self.home, 'run')
        name = [n for n in os.listdir(run) if n.startswith('check-')][0]
        with open(os.path.join(run, name), 'w', encoding='utf-8') as f:
            json.dump({'v': 1, 'notes': rec['notes']}, f)
        self.write_config(RULES)
        self.put('decisions/DEC-1.md', DECISION + 'changed before any baseline\n')
        # no stamp is lost and nothing is reported: the run keeps every text as it is now
        self.assertEqual(self.run_check(), (0, []))
        rec = self.record()
        self.assertEqual(rec['v'], 2)
        self.assertEqual(sorted(rec['contracts']), [self.path('decisions/DEC-1.md'),
                                                    self.path('decisions/DEC-2.md'), self.path('sources/a.md')])
        self.put('decisions/DEC-1.md', DECISION)
        self.assertEqual(self.run_check()[0], 2)

    def test_a_broken_config_keeps_the_kept_texts(self):
        self.start()
        kept = self.record()['contracts']
        self.write_config(RULES, broken=True)
        self.put('decisions/DEC-1.md', DECISION + 'x\n')
        self.assertEqual(self.run_check(), (0, []))
        self.assertEqual(self.record()['contracts'], kept)
        self.write_config(RULES)
        self.put('decisions/DEC-1.md', DECISION + 'y\n')
        self.assertEqual(self.run_check()[0], 2)

    def test_a_contract_that_goes_away_drops_its_texts(self):
        self.start()
        self.write_config([{'glob': 'decisions/**', 'type': 'frozen'}])
        self.assertEqual(self.run_check(), (0, []))
        rec = self.record()['contracts']
        self.assertEqual(sorted(rec), [self.path('decisions/DEC-1.md'), self.path('decisions/DEC-2.md')])
        self.assertEqual({c['type'] for c in rec.values()}, {'frozen'})

    def test_the_baseline_stays_inside_the_time_budget(self):
        with mock.patch.object(check, 'SCAN_SECS', -1.0):
            self.start()
        self.assertEqual(self.record()['contracts'], {})
        # the next run that has time keeps them
        self.assertEqual(self.run_check(), (0, []))
        self.assertEqual(len(self.record()['contracts']), 3)


class ConfigKeys(unittest.TestCase):
    def base(self, contracts):
        d = config.default_dict()
        d['home'] = '/tmp/leks-test-home'
        d['check'] = {'contracts': contracts}
        return d

    def test_read_and_kept(self):
        cfg = config.from_dict(self.base(RULES))
        self.assertEqual(cfg.check['contracts'], RULES)
        self.assertEqual(config.from_dict(cfg.to_dict()).check['contracts'], RULES)

    def test_keep_and_successor(self):
        rules = [{'glob': 'd/**', 'keep': ['Status', 'superseded_by', 'status'], 'successor': True},
                 {'glob': 'e/**', 'successor': False}]
        cfg = config.from_dict(self.base(rules))
        self.assertEqual(cfg.check['contracts'], [{'glob': 'd/**', 'keep': ['status', 'superseded by'],
                                                   'successor': True}, {'glob': 'e/**', 'successor': False}])
        self.assertEqual(config.from_dict(cfg.to_dict()).check['contracts'], cfg.check['contracts'])

    def test_checked(self):
        bad = [
            ({'glob': 'a', 'keep': []}, 'check.contracts\\[0\\].keep must be a list of 1 to 20 field names'),
            ({'glob': 'a', 'keep': 'status'}, 'check.contracts\\[0\\].keep must be a list'),
            ({'glob': 'a', 'keep': ['sta:tus']}, 'bad field name in check.contracts\\[0\\].keep'),
            ({'glob': 'a', 'successor': 'yes'}, 'check.contracts\\[0\\].successor must be true or false'),
            ({'type': 'frozen'}, 'needs a glob and at least one of'),
            ({'glob': 'a/**'}, 'check.contracts\\[0\\] needs a glob and at least one of type, keep and successor'),
            ({'glob': 'a/**', 'type': 'locked'}, 'check.contracts\\[0\\].type must be one of'),
            ({'glob': '../a', 'type': 'frozen'}, 'check.contracts\\[0\\].glob'),
            ({'glob': 'a', 'type': 'frozen', 'why': 1}, 'unknown key in check.contracts\\[0\\]: why'),
            ('a/**', 'check.contracts\\[0\\] must be an object'),
        ]
        for entry, msg in bad:
            with self.subTest(entry=entry), self.assertRaisesRegex(config.ConfigError, msg):
                config.from_dict(self.base([entry]))
        with self.assertRaisesRegex(config.ConfigError, 'check.contracts must be a list'):
            config.from_dict(self.base({'glob': 'a'}))
        with self.assertRaisesRegex(config.ConfigError, 'at most 200'):
            config.from_dict(self.base([{'glob': 'a', 'type': 'frozen'}] * 201))


if __name__ == '__main__':
    unittest.main()
