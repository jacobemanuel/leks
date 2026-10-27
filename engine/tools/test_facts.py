"""Tests of structured facts on a disposable fixture vault: the evidence rules (a build is never deployment proof,
a proposal is never promoted, unknown stays unknown), fact-level verification against anchors, seeded
contradictions next to legitimate revisions, and the bitemporal queries over a git history of claims.json."""
import json
import subprocess
import unittest

import anchors
import brain
import facts
import knowledge_time as kt
import vault_fixture as vf

NOTE = ['# Delivery', 'Demo Viewer, com.example.viewer, build 261019120000, Release.',
        'Installed and launched on the office headset after the owner unlocked it.',
        'The owner checked it; everything looks really good.', 'Built and signed only: build 261018010000.',
        'Unity editor 6000.3.19f1 is used by the project.', 'ADR-0009 status: proposed draft.',
        'Editor checks passed; no player build.', '']
APP = 'com.example.viewer'


def claim(cid, text, as_of='2026-10-19', confidence='verified', scope='fixture', **extra):
    return dict({'id': cid, 'text': text, 'page': 'wiki/delivery.md', 'confidence': confidence, 'scope': scope,
                 'as_of': as_of, 'sources': ['raw/docs/delivery-note.md']}, **extra)


def build(value, level, anchors_=(0,), **extra):
    return dict({'subject': APP, 'attribute': 'build', 'value': value, 'level': level, 'anchors': list(anchors_)}, **extra)


class FactTests(unittest.TestCase):
    def setUp(self):
        self.base, self.root = vf.make_vault()
        (self.root / 'raw/docs/delivery-note.md').write_text('\n'.join(NOTE), encoding='utf-8')
        brain.register(self.root, 'raw/docs/delivery-note.md', 'fixture')
        self.registry = anchors.registry_by_id(self.root)
        self.sid = next(i for i, e in self.registry.items() if e['path'] == 'raw/docs/delivery-note.md')

    def tearDown(self):
        vf.cleanup(self.base)

    def anchor(self, first, last=None):
        return anchors.make_anchor(self.root, self.registry, self.sid, first, last or first)

    def findings(self, claims):
        return [m for _, m in facts.rule_findings(claims, facts.verify_with_text(self.root, claims))]

    def test_schema(self):
        c = claim('X', 'Build 261019120000 was built.', anchors=[self.anchor(2)], facts=[
            {'subject': APP, 'attribute': 'colour', 'value': 'red'},
            build('26101912000', 'built'), build('261019120000', 'shipped'), build('261019120000', 'built', anchors_=(3,)),
            dict(build('261019120000', 'built'), confidence='verified'),
            {'subject': '261019120000', 'attribute': 'owner-verdict', 'value': 'accepted', 'level': 'launched', 'anchors': [0]}])
        c['confidence'] = 'reported'
        problems = ' | '.join(facts.schema_errors(c))
        for text in ('unknown attribute', "invalid value '26101912000'", "invalid level 'shipped'", 'cites anchors [3]',
                     'claims more confidence (verified) than its claim (reported)', 'only build and source-state facts carry a level'):
            self.assertIn(text, problems)
        self.assertEqual(facts.schema_errors(claim('T', 'x', valid_to='2026-10-01')), ['T: valid_to 2026-10-01 is before valid_from 2026-10-19'])
        self.assertEqual(facts.schema_errors(claim('T', 'x', as_of='19 Oct')), ["T: as_of '19 Oct' is not an ISO date"])
        inferred = claim('Y', 'x', anchors=[self.anchor(2)], facts=[dict(build('261019120000', 'built'), confidence='inferred')])
        self.assertIn('needs a basis', ' '.join(facts.schema_errors(inferred)))

    def test_a_build_is_never_deployment_proof(self):
        ok = claim('A', 'Build 261019120000 was installed and launched on the headset.', anchors=[self.anchor(2), self.anchor(3)],
                   facts=[build('261019120000', 'launched', (0, 1))])
        self.assertEqual(self.findings([ok]), [])
        only_built = claim('B', 'Build 261018010000 was built and signed.', anchors=[self.anchor(5)],
                           facts=[build('261018010000', 'installed')])
        self.assertTrue(any('(E3)' in m for m in self.findings([only_built])))
        denied = claim('C', 'Build 261019120000 was built.', scope='no headset install or acceptance', anchors=[self.anchor(2), self.anchor(3)],
                       facts=[build('261019120000', 'installed', (0, 1))])
        self.assertTrue(any('denied' in m and '(E4)' in m for m in self.findings([denied])))
        open_ = claim('D', 'Build 261019120000 was installed on the headset. Launch and acceptance are not yet recorded.',
                      anchors=[self.anchor(2), self.anchor(3)], facts=[build('261019120000', 'launched', (0, 1))])
        self.assertTrue(any('open' in m and '(E4)' in m for m in self.findings([open_])))
        under = claim('E', 'Build 261019120000 was installed on the office headset.', anchors=[self.anchor(2), self.anchor(3)],
                      facts=[build('261019120000', 'built', (0, 1))])
        self.assertTrue(any('the text says installed' in m for m in self.findings([under])))
        (self.root / 'raw/docs/mac-note.md').write_text('Build 261019120000 was installed and tested on the Mac.\n', encoding='utf-8')
        brain.register(self.root, 'raw/docs/mac-note.md', 'fixture')
        mac = next(i for i, e in anchors.registry_by_id(self.root).items() if e['path'] == 'raw/docs/mac-note.md')
        on_mac = claim('M', 'Build 261019120000 was installed.', anchors=[
            anchors.make_anchor(self.root, anchors.registry_by_id(self.root), mac, 1, 1)], facts=[build('261019120000', 'installed')])
        self.assertTrue(any('names a device (E3)' in m for m in self.findings([on_mac])))
        accepted = claim('F', 'Build 261019120000 was installed and launched.', anchors=[self.anchor(2), self.anchor(3)],
                         facts=[build('261019120000', 'device-accepted', (0, 1))])
        self.assertTrue(any('(E6)' in m for m in self.findings([accepted])))
        verdict = claim('G', 'The owner accepted build 261019120000 on the headset.', confidence='reported', anchors=[self.anchor(4)],
                        facts=[{'subject': '261019120000', 'attribute': 'owner-verdict', 'value': 'accepted', 'anchors': [0]}])
        self.assertFalse(any('(E6)' in m for m in self.findings([accepted, verdict])))

    def test_proposal_is_never_promoted_and_unknown_stays_unknown(self):
        plan = claim('P', 'ADR-0009 is a proposed draft.', confidence='planned', anchors=[self.anchor(7)],
                     facts=[{'subject': 'ADR-0009', 'attribute': 'decision-status', 'value': 'active', 'anchors': [0]}])
        found = self.findings([plan])
        self.assertTrue(any('(E2)' in m for m in found))
        self.assertTrue(any("supports 'active'" in m for m in found))           # F2: the excerpt says proposed
        plan['facts'][0]['value'] = 'proposed'
        self.assertEqual(self.findings([plan]), [])
        unknown = claim('U', 'The Unity version is unknown.', confidence='unknown', anchors=[self.anchor(6)],
                        facts=[{'subject': 'project', 'attribute': 'unity-version', 'value': '6000.3.19f1', 'anchors': [0]}])
        self.assertTrue(any('(E6)' in m for m in self.findings([unknown])))
        unknown['facts'][0]['value'] = 'unknown'
        self.assertEqual(self.findings([unknown]), [])

    def test_negation_scope_and_source_states(self):
        w = lambda text, scope='fixture': facts.deployment_wording({'text': text, 'scope': scope})
        self.assertEqual(w('No Player build, install, commit or headset acceptance was performed.'),
                         {'built': 'negated', 'installed': 'negated', 'device-accepted': 'negated'})
        self.assertEqual(w('Built, signature-verified, installed on the test headset and smoke-launched for 2 minutes with zero exceptions, '
                           'zero memory warnings and no new crash report.'),
                         {'built': 'affirmed', 'installed': 'affirmed', 'launched': 'affirmed'})
        self.assertEqual(w('Build 261019120000 was installed on the headset. Launch and headset acceptance belong to the owner and are not yet recorded.'),
                         {'built': 'affirmed', 'installed': 'affirmed', 'launched': 'open', 'device-accepted': 'open'})
        self.assertEqual(w('After testing build 261019120000 on the office Apple Vision Pro, the owner said the feature works but is not satisfied.'),
                         {'built': 'affirmed', 'launched': 'affirmed'})
        self.assertEqual(w('Editor checks passed.', 'Source only; not a device result'), {'launched': 'negated'})
        self.assertEqual(w('The final build is not installed on AVP and its headset appearance is unknown.'),
                         {'built': 'affirmed', 'installed': 'negated'})          # the negation before the verb wins
        self.assertEqual(w('Accepted code remains; installed-package conversion passed; generated build caches removed.'), {})
        state = lambda level, text='Editor checks passed. No player build or new device pass.': claim(
            'S', text, as_of='2026-10-17', anchors=[self.anchor(6)],
            facts=[{'subject': 'project', 'attribute': 'source-state', 'value': '2026-10-17', 'level': level, 'anchors': [0]}])
        self.assertEqual(facts.schema_errors(state('built')), ["S: fact 0 has invalid level 'built' (E1)"])
        self.assertTrue(any("supports 'mac-tested'" in m for m in self.findings([state('mac-tested')])))   # line 6 says nothing of tests
        ok = state('mac-tested')
        ok['anchors'] = [self.anchor(8)]
        self.assertEqual(self.findings([ok]), [])
        built = state('mac-tested', 'Editor checks passed and the player was built.')
        built['anchors'] = [self.anchor(8)]
        self.assertTrue(any('the text says built' in m for m in self.findings([built])))
        dated = state('implemented')
        dated['facts'][0]['value'] = '2026-10-18'
        self.assertTrue(any('dated by its claim' in m for m in self.findings([dated])))

    def test_identifiers_in_text_and_negated_excerpts(self):
        ok = claim('N', 'Build 261019120000 is the release.', anchors=[self.anchor(2)])
        self.assertEqual(self.findings([ok]), [])
        drifted = dict(ok, text='Build 261019129999 is the release.')            # a number changed in the text alone
        self.assertTrue(any('261019129999' in m and '(I1)' in m for m in self.findings([drifted])))
        (self.root / 'raw/docs/denied.md').write_text('Final build 261019120000 has not been installed on the headset.\n', encoding='utf-8')
        brain.register(self.root, 'raw/docs/denied.md', 'fixture')
        sid = next(i for i, e in anchors.registry_by_id(self.root).items() if e['path'] == 'raw/docs/denied.md')
        denied = claim('D', 'Build 261019120000 was installed on the headset.', anchors=[
            anchors.make_anchor(self.root, anchors.registry_by_id(self.root), sid, 1, 1)], facts=[build('261019120000', 'installed')])
        self.assertTrue(any('(E3)' in m for m in self.findings([denied])))       # "has not been installed" proves nothing
        proposal = claim('R', 'ADR-0009 is for decision.', anchors=[self.anchor(7)],
                         facts=[{'subject': 'ADR-0009', 'attribute': 'decision-status', 'value': 'active', 'anchors': [0]}])
        self.assertTrue(any("supports 'active'" in m for m in self.findings([proposal])))

    def test_fact_level_verification(self):
        wrong = claim('W', 'Unity 6000.3.20f1 is used.', anchors=[self.anchor(6)],
                      facts=[{'subject': 'project', 'attribute': 'unity-version', 'value': '6000.3.20f1', 'anchors': [0]}])
        self.assertTrue(any('does not occur' in m for m in self.findings([wrong])))
        uncited = claim('V', 'Unity 6000.3.19f1 is used.', anchors=[self.anchor(6)],
                        facts=[{'subject': 'project', 'attribute': 'unity-version', 'value': '6000.3.19f1', 'anchors': []}])
        self.assertTrue(any('does not occur' in m for m in self.findings([uncited])))
        broken = claim('Z', 'Unity 6000.3.19f1 is used.', anchors=[dict(self.anchor(6), sha256='0' * 64)],
                       facts=[{'subject': 'project', 'attribute': 'unity-version', 'value': '6000.3.19f1', 'anchors': [0]}])
        self.assertTrue(any('does not occur' in m for m in self.findings([broken])))  # a changed anchor proves nothing
        other_app = claim('O', 'Build 261018010000 was built.', anchors=[self.anchor(5)],
                          facts=[dict(build('261018010000', 'built'), subject='com.example.other')])
        self.assertTrue(any('subject com.example.other' in m for m in self.findings([other_app])))
        prefix = claim('Q', 'Build 261019120000 was built.', anchors=[self.anchor(2)],
                       facts=[dict(build('261019120000', 'built'), subject='com.example.view')])   # a prefix of com.example.viewer
        self.assertTrue(any('subject com.example.view ' in m for m in self.findings([prefix])))
        self.assertTrue(facts.mentions('6000.3.19f1', 'Unity 6000.3.19f1.') and not facts.mentions('6000.3.19f1', 'Unity 6000.3.19f12'))
        self.assertFalse(facts.mentions('26101912000', 'build 261019120000'))
        inferred = claim('I', 'The owner judged the final build good.', confidence='reported', anchors=[self.anchor(4)],
                         facts=[{'subject': '261019120000', 'attribute': 'owner-verdict', 'value': 'accepted', 'anchors': [0],
                                 'confidence': 'inferred', 'basis': 'the verdict follows the install of that build'}])
        self.assertEqual(self.findings([inferred]), [])

    def test_seeded_contradictions_and_legitimate_revisions(self):
        v = lambda cid, value, as_of, **x: claim(cid, 'Unity %s.' % value, as_of=as_of, facts=[
            {'subject': 'project', 'attribute': 'unity-version', 'value': value, 'anchors': []}], **x)
        kinds = lambda claims: sorted((r['kind'], tuple(r['claims'])) for r in facts.contradictions(claims) if r['attribute'] == 'unity-version')
        # a later value closes an open-ended earlier one: a revision
        self.assertEqual(kinds([v('U1', '6000.0.56f1', '2026-09-01'), v('U2', '6000.3.19f1', '2026-10-08')]), [('revision', ('U1', 'U2'))])
        # the same evidence date with two values: a contradiction
        self.assertEqual(kinds([v('U1', '6000.0.56f1', '2026-10-08'), v('U2', '6000.3.19f1', '2026-10-08')]), [('contradiction', ('U1', 'U2'))])
        # an explicit validity that overlaps the next value: a contradiction
        self.assertEqual(kinds([v('U1', '6000.0.56f1', '2026-09-01', valid_to='2026-10-15'), v('U2', '6000.3.19f1', '2026-10-08')]),
                         [('contradiction', ('U1', 'U2'))])
        # supersession and a recorded conflict are history, not new contradictions
        self.assertEqual(kinds([v('U1', '6000.0.56f1', '2026-10-08', superseded_by='U2', lifecycle='superseded'),
                                v('U2', '6000.3.19f1', '2026-10-08', supersedes=['U1'])]), [('superseded', ('U1', 'U2'))])
        self.assertEqual(kinds([v('U1', '6000.0.56f1', '2026-10-08', conflicts=['U2']), v('U2', '6000.3.19f1', '2026-10-08', conflicts=['U1'])]),
                         [('recorded-conflict', ('U1', 'U2'))])
        # an archived value followed by a current one: archived_on bounds validity, so a revision
        self.assertEqual(kinds([v('U1', '6000.0.56f1', '2026-09-01', lifecycle='archived', archived_on='2026-10-21'),
                                v('U2', '6000.3.19f1', '2026-10-08')]), [('revision', ('U1', 'U2'))])
        both = claim('U3', 'x', as_of='2026-10-08', facts=[{'subject': 'project', 'attribute': 'unity-version', 'value': v, 'anchors': []}
                                                          for v in ('6000.0.56f1', '6000.3.19f1')])
        self.assertEqual(kinds([both]), [('contradiction', ('U3', 'U3'))])
        # verdicts belong to their build: different builds never contradict
        verdict = lambda cid, b, value: claim(cid, 'x', facts=[{'subject': b, 'attribute': 'owner-verdict', 'value': value, 'anchors': []}])
        self.assertEqual(facts.contradictions([verdict('V1', '261018150000', 'accepted'), verdict('V2', '261019120000', 'not-accepted')]), [])
        same = facts.contradictions([verdict('V1', '261019120000', 'accepted'), verdict('V2', '261019120000', 'not-accepted')])
        self.assertEqual([r['kind'] for r in same], ['contradiction'])
        # one build number under two apps
        two = facts.contradictions([claim('B1', 'x', facts=[build('261019120000', 'built')]),
                                    claim('B2', 'x', facts=[dict(build('261019120000', 'built'), subject='com.example.other')])])
        self.assertEqual([r.get('reason') for r in two], ['one build under several apps'])
        # a deployment denied on a date while another claim established it by then; "not yet recorded" is no denial
        installed = claim('D1', 'Build 261019120000 was installed on the headset.', as_of='2026-10-18', facts=[build('261019120000', 'installed')])
        denied = claim('D2', 'Build 261019120000 was built.', as_of='2026-10-19', scope='no headset install', facts=[build('261019120000', 'built')])
        pending = claim('D3', 'Build 261019120000 was built; installation not yet recorded.', as_of='2026-10-19', facts=[build('261019120000', 'built')])
        self.assertEqual([r['attribute'] for r in facts.contradictions([installed, denied])], ['deployment'])
        self.assertEqual(facts.contradictions([installed, pending]), [])
        earlier_denial = dict(denied, as_of='2026-10-17')
        self.assertEqual(facts.contradictions([installed, earlier_denial]), [])  # installed later: a revision

    def test_check_reports_errors_for_active_and_warnings_for_archived(self):
        data = json.loads((self.root / 'system/claims.json').read_text())
        data['claims'][0].update(text='Build 261018010000 was built.', anchors=[self.anchor(5)], facts=[build('261018010000', 'installed')])
        brain.save_json(self.root / 'system/claims.json', data)
        brain.build(self.root)
        report = brain.check(self.root, False)
        self.assertTrue(any(e.startswith('C-1: fact 0: level installed') for e in report['errors']), report['errors'])
        self.assertEqual(report['claim_facts']['facts'], 1)
        data['claims'][0]['lifecycle'] = 'archived'
        brain.save_json(self.root / 'system/claims.json', data)
        brain.build(self.root)
        report = brain.check(self.root, False)
        self.assertTrue(report['ok'], report['errors'])
        self.assertTrue(any(w.startswith('C-1: fact 0: level installed') for w in report['warnings']))


class TimeTests(unittest.TestCase):
    """Bitemporal queries over a small git history of claims.json in a fixture vault."""

    def setUp(self):
        self.base, self.root = vf.make_vault()
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')

    def tearDown(self):
        vf.cleanup(self.base)

    def git(self, *args, when=None):
        env = None
        if when:
            import os
            env = dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
        subprocess.run(['git', '-C', str(self.root)] + list(args), check=True, capture_output=True, env=env)

    def record(self, claims, when):
        brain.save_json(self.root / 'system/claims.json', {'claims': claims})
        self.git('add', 'system/claims.json')
        self.git('commit', '-q', '-m', 'claims', when=when)

    def test_valid_and_transaction_time(self):
        b1 = claim('B1', 'Build 261018150000 installed and launched.', as_of='2026-10-18',
                   facts=[build('261018150000', 'launched', device='test headset')])
        b2 = claim('B2', 'Build 261019120000 installed.', as_of='2026-10-19', facts=[build('261019120000', 'installed', device='office')])
        fb = claim('FB', 'Owner tested 261019120000.', as_of='2026-10-19', confidence='reported',
                   facts=[dict(build('261019120000', 'launched', device='office'), confidence='inferred', basis='owner feedback')])
        self.record([b1], '2026-10-21T10:00:00+02:00')
        self.record([b1, b2], '2026-10-22T10:00:00+02:00')
        self.record([b1, b2, fb], '2026-10-23T10:00:00+02:00')
        claims, version = kt.known(self.root, '2026-10-15')
        self.assertIsNone(claims)
        self.assertIn('before the recorded history', version)
        claims, _ = kt.known(self.root, '2026-10-21T12:00:00+02:00')
        self.assertEqual([c['id'] for c in claims], ['B1'])
        latest = kt.history(self.root)[-1][2]
        self.assertEqual(kt.latest_build(latest, APP, '2026-10-18')['build'], '261018150000')
        self.assertEqual(kt.latest_build(latest, APP, '2026-10-24')['build'], '261019120000')
        self.assertEqual(kt.latest_build(latest, APP, '2026-10-24', min_level='launched')['build'], '261018150000')  # inferred excluded
        self.assertEqual(kt.latest_build(latest, APP, '2026-10-24', 'launched', ('verified', 'reported', 'inferred'))['build'], '261019120000')
        known_then = kt.known(self.root, '2026-10-21T12:00:00+02:00')[0]
        self.assertEqual(kt.latest_build(known_then, APP, '2026-10-24')['build'], '261018150000')   # what the vault knew then
        self.assertIsNone(kt.latest_build(latest, APP, '2026-10-17'))
        tie = claim('B10', 'x', as_of='2026-10-19', facts=[build('261019170000', 'installed')])
        self.assertIn('ambiguous', kt.latest_build(latest + [tie], APP, '2026-10-24'))

    def test_supersession_and_evidence_at_decision_time(self):
        a = claim('D-1', 'ADR-0009 proposed.', as_of='2026-10-09', confidence='planned',
                  facts=[{'subject': 'ADR-0009', 'attribute': 'decision-status', 'value': 'proposed', 'anchors': []}])
        self.record([a], '2026-10-21T09:00:00+02:00')
        a2 = dict(a, lifecycle='superseded', superseded_by='D-2')
        b = claim('D-2', 'ADR-0009 approved.', as_of='2026-10-20', supersedes=['D-1'],
                  facts=[{'subject': 'ADR-0009', 'attribute': 'decision-status', 'value': 'active', 'anchors': []}])
        self.record([a2, b], '2026-10-23T09:00:00+02:00')
        s = kt.superseded(self.root, 'D-1')
        self.assertEqual((s['valid_from'], s['valid_until']), ('2026-10-09', '2026-10-20'))
        self.assertTrue(s['recorded_as_superseded'].startswith('2026-10-23'))
        latest = kt.history(self.root)[-1][2]
        self.assertEqual(kt.value_at(latest, 'decision-status', 'ADR-0009', '2026-10-15')['value'], 'proposed')
        self.assertEqual(kt.value_at(latest, 'decision-status', 'ADR-0009', '2026-10-20')['value'], 'active')
        self.assertIsNone(kt.value_at(latest, 'decision-status', 'ADR-0009', '2026-10-08'))     # before any evidence
        ended = dict(b, valid_to='2026-10-22')
        self.assertIn('stopped being valid', kt.value_at([a2, ended], 'decision-status', 'ADR-0009', '2026-10-23')['reason'])
        then = kt.evidence(self.root, 'D-1', '2026-10-22')
        self.assertEqual(then['confidence'], 'planned')
        self.assertIn('not recorded', kt.evidence(self.root, 'D-2', '2026-10-22')['status'])
        archived = claim('A-1', 'Unity 6000.0.56f1.', as_of='2026-09-01', lifecycle='archived', archived_on='2026-10-21',
                         facts=[{'subject': 'p', 'attribute': 'unity-version', 'value': '6000.0.56f1', 'anchors': []}])
        self.assertIn('archived on 2026-10-21', kt.value_at([archived], 'unity-version', 'p', '2026-10-22')['note'])

    def test_uncommitted_edits_and_retrospective_correction(self):
        a = claim('R-1', 'Build 261019120000 installed.', as_of='2026-10-19', facts=[build('261019120000', 'installed')])
        self.record([a], '2026-10-21T09:00:00+02:00')
        corrected = dict(a, valid_from='2026-10-18', facts=[build('261019120000', 'installed')])   # evidence date corrected later
        brain.save_json(self.root / 'system/claims.json', {'claims': [corrected]})
        versions = kt.history(self.root)
        self.assertEqual(versions[-1][1], 'uncommitted')
        self.assertEqual(kt.latest_build(versions[-1][2], APP, '2026-10-18')['build'], '261019120000')
        self.assertIsNone(kt.latest_build(versions[0][2], APP, '2026-10-18'))      # the vault did not know it then
        missing = claim('M-1', 'x', as_of=None)
        same_day = [dict(a, id='S-1', as_of='2026-10-18', superseded_by='S-2'), claim('S-2', 'x', as_of='2026-10-18')]
        self.assertEqual([c['id'] for c in kt.valid(same_day, '2026-10-18')], ['S-1', 'S-2'])
        self.assertEqual([c['id'] for c in kt.valid(same_day, '2026-10-19')], ['S-2'])
        self.assertEqual(kt.valid([missing], '2026-10-20'), [])                      # no evidence date: never valid

    def test_promotion_without_new_anchors_is_reported(self):
        a = claim('P-1', 'Build 261019120000 built.', as_of='2026-10-19', anchors=[], facts=[build('261019120000', 'built', ())])
        self.record([a], '2026-10-21T09:00:00+02:00')
        self.record([dict(a, facts=[build('261019120000', 'installed', ())])], '2026-10-22T09:00:00+02:00')
        found = facts.promotion_findings(kt.history(self.root))
        self.assertTrue(any('(E5)' in m for _, m in found))
        self.assertEqual(facts.promotion_findings(kt.history(self.root, 'builds')), found)       # same finding, compact view
        compact = kt.history(self.root, 'builds')[-1][2]
        self.assertEqual(sorted(compact[0]), ['anchors_key', 'facts', 'id'])


if __name__ == '__main__':
    unittest.main()
