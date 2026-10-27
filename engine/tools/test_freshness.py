"""Tests of the freshness rules on a fixture vault with its own git history: newer claims and sources, newer
uncited evidence, edits after compiled_on (and the mechanical commits that do not count), claim corrections,
and the per-type rules for wiki/now.md and the latest-delivery page."""
import json
import os
import subprocess
import unittest

import brain
import freshness
import vault_fixture as vf


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.base, self.root = vf.make_vault()
        for args in (('init', '-q'), ('config', 'user.email', 'fixture@example.invalid'), ('config', 'user.name', 'Fixture')):
            self.git(*args)
        data = json.loads((self.root / 'system/claims.json').read_text())
        for c in data['claims']:
            c['as_of'] = '2026-10-08'                                            # as old as the fixture pages
        brain.save_json(self.root / 'system/claims.json', data)
        self.commit('2026-10-08T12:00:00+02:00')

    def tearDown(self):
        vf.cleanup(self.base)

    def git(self, *args, when=None):
        env = dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when) if when else None
        subprocess.run(['git', '-C', str(self.root)] + list(args), check=True, capture_output=True, env=env)

    def commit(self, when):
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', 'fixture', when=when)

    def rules(self, page=None):
        return sorted((r['page'], r['rule']) for r in freshness.findings(self.root, brain) if page is None or r['page'] == page)

    def edit(self, path, old, new):
        p = self.root / path
        text = p.read_text(encoding='utf-8')
        self.assertIn(old, text)
        p.write_text(text.replace(old, new), encoding='utf-8')

    def test_clean_fixture_has_no_findings(self):
        self.assertEqual(self.rules(), [])

    def test_newer_claim_source_and_uncited_evidence(self):
        data = json.loads((self.root / 'system/claims.json').read_text())
        data['claims'][0]['as_of'] = '2026-10-20'                        # C-1 is on wiki/delivery.md (as_of 2026-10-08)
        brain.save_json(self.root / 'system/claims.json', data)
        (self.root / 'raw/docs/2026-10-21-newer-build-log.md').write_text('Build 261021000000.\n', encoding='utf-8')
        brain.register(self.root, 'raw/docs/2026-10-21-newer-build-log.md', 'fixture')
        self.assertEqual(self.rules('wiki/delivery.md'), [('wiki/delivery.md', 'S1'), ('wiki/delivery.md', 'S3')])
        self.edit('wiki/delivery.md', 'sources: ["raw/docs/build-log.md"]', 'sources: ["raw/docs/build-log.md", "raw/docs/2026-10-21-newer-build-log.md"]')
        self.assertEqual(self.rules('wiki/delivery.md'), [('wiki/delivery.md', 'S1'), ('wiki/delivery.md', 'S2')])

    def test_edits_after_compiled_on_and_mechanical_commits(self):
        page = 'wiki/mirrors.md'
        self.edit(page, 'keywords: ["mirrors"]', 'keywords: ["mirrors", "stereo"]')          # front matter only
        self.commit('2026-10-10T12:00:00+02:00')
        self.edit(page, '# Mirrors\n', '# Mirrors\nSee [[wiki/delivery|the delivery]].\n')
        self.commit('2026-10-11T12:00:00+02:00')
        self.assertEqual(self.rules(page), [('wiki/mirrors.md', 'S4')])          # a new sentence is a real edit
        self.edit(page, 'compiled_on: "2026-10-08"', 'compiled_on: "2026-10-12"')
        self.commit('2026-10-12T12:00:00+02:00')
        self.assertEqual(self.rules(page), [])                                   # compiled again
        self.edit(page, '[[wiki/delivery|the delivery]]', '[[wiki/codebase/delivery|the delivery]]')   # link target only
        self.edit(page, 'See ', 'See  ')                                                              # whitespace only
        self.commit('2026-10-13T12:00:00+02:00')
        self.assertEqual(self.rules(page), [])
        self.edit(page, 'compiled_on: "2026-10-12"', 'compiled_on: "2026-10-14"')
        self.edit(page, 'See', 'Read')                                           # the compile commit itself edits
        self.commit('2026-10-15T00:00:04+02:00')                                 # just after midnight
        self.assertEqual(self.rules(page), [])

    def test_claim_corrections(self):
        data = json.loads((self.root / 'system/claims.json').read_text())
        data['claims'][1]['text'] = 'He saw mirrors per eye.'                    # C-2 is on wiki/mirrors.md
        brain.save_json(self.root / 'system/claims.json', data)
        self.commit('2026-10-09T12:00:00+02:00')
        data['claims'][1]['text'] = 'The owner saw mirrors per eye.'             # wording only
        brain.save_json(self.root / 'system/claims.json', data)
        self.commit('2026-10-10T12:00:00+02:00')
        self.assertEqual(self.rules('wiki/mirrors.md'), [])
        data['claims'][1]['text'] = 'The owner did not see mirrors per eye on build 261019120000.'
        data['claims'][1]['anchors'] = []                                        # metadata changes alone never count
        brain.save_json(self.root / 'system/claims.json', data)
        self.commit('2026-10-11T12:00:00+02:00')
        self.assertEqual(self.rules('wiki/mirrors.md'), [('wiki/mirrors.md', 'S5')])
        self.edit('wiki/mirrors.md', '# Mirrors\n', '# Mirrors\nThe owner did not see mirrors per eye.\n')
        self.commit('2026-10-12T12:00:00+02:00')
        self.assertEqual(self.rules('wiki/mirrors.md'), [('wiki/mirrors.md', 'S4')])   # edited since: S5 resolved

    def test_per_type_rules(self):
        data = json.loads((self.root / 'system/claims.json').read_text())
        data['claims'][0]['as_of'] = '2026-10-08'
        data['claims'][0]['facts'] = [{'subject': 'com.example.viewer', 'attribute': 'build', 'value': '261019120000',
                                       'level': 'installed', 'anchors': []}]
        brain.save_json(self.root / 'system/claims.json', data)
        vf.page(self.root, 'wiki/codebase/latest-delivery.md', 'Latest delivery', 'Latest', ['latest'], [], body='Build 261018150000.\n')
        self.assertIn(('wiki/codebase/latest-delivery.md', 'T2'), self.rules())
        vf.page(self.root, 'wiki/codebase/latest-delivery.md', 'Latest delivery', 'Latest', ['latest'], [], body='Build 261019120000.\n')
        self.assertNotIn(('wiki/codebase/latest-delivery.md', 'T2'), self.rules())
        data['claims'][1]['as_of'] = '2026-10-19'
        brain.save_json(self.root / 'system/claims.json', data)
        self.assertIn(('wiki/now.md', 'T1'), self.rules())                       # now compiled 2026-10-08
        vf.page(self.root, 'wiki/archive/old-plan.md', 'Old plan', 'Archived', ['plan'], ['raw/docs/2099-01-01-x.md'],
                status='archived', category='Archive')
        self.assertFalse(any(p.startswith('wiki/archive/') for p, _ in self.rules()))   # archived pages are history

    def test_severity_is_configurable_per_information_type(self):
        data = json.loads((self.root / 'system/claims.json').read_text())
        data['claims'][0]['as_of'] = '2026-10-09'                                # S1 on wiki/delivery.md (Domains), T1 on now
        brain.save_json(self.root / 'system/claims.json', data)
        sev = lambda: sorted((r['page'], r['rule'], r['severity']) for r in freshness.findings(self.root, brain))
        self.assertEqual(sev(), [('wiki/delivery.md', 'S1', 'warning'), ('wiki/now.md', 'T1', 'warning')])
        cfg = self.root / 'system/vault-config.json'
        settings = json.loads(cfg.read_text())
        settings['freshness'] = {'types': {'Domains': {'S1': 'off'}}, 'severity': {'T1': 'notice'}, 'now_tolerance_days': 0}
        cfg.write_text(json.dumps(settings), encoding='utf-8')
        self.assertEqual(sev(), [('wiki/now.md', 'T1', 'notice')])
        settings['freshness']['now_tolerance_days'] = 1                          # now.md may lag one day
        cfg.write_text(json.dumps(settings), encoding='utf-8')
        self.assertEqual(sev(), [])

    def test_check_reports_content_staleness_as_warnings(self):
        data = json.loads((self.root / 'system/claims.json').read_text())
        data['claims'][0]['as_of'] = '2026-10-20'
        brain.save_json(self.root / 'system/claims.json', data)
        brain.build(self.root)
        report = brain.check(self.root, False)
        self.assertTrue(any(w.startswith('freshness S1 wiki/delivery.md') for w in report['warnings']), report['warnings'])
        self.assertTrue(any(w.startswith('freshness T1 wiki/now.md') for w in report['warnings']))   # now is older too
        self.assertEqual(report['freshness'], 2)
        self.assertIsNone(brain.check(self.root, False, fast=True)['freshness'])


if __name__ == '__main__':
    unittest.main()
