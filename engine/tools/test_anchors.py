"""Tests of evidence anchors on a disposable fixture vault: verified, relocated, ambiguous, changed, modified,
missing, restored, moved, unregistered, malformed and unsupported anchors, and their findings in brain.check."""
import json
import unittest

import anchors
import brain
import evidence_store
import vault_fixture as vf

NOTE = ['# Delivery notes', '', '## Build', 'Build 261019120000 was signed with the team certificate.',
        'It was installed on the office headset with devicectl.', '', '## Build', 'A second section with the same heading.',
        'Launch was reported by the owner on 19 October.', '']


class AnchorTests(unittest.TestCase):
    def setUp(self):
        self.base, self.root = vf.make_vault()
        (self.root / 'raw/docs/delivery-note.md').write_text('\n'.join(NOTE), encoding='utf-8')
        brain.register(self.root, 'raw/docs/delivery-note.md', 'fixture')
        self.registry = anchors.registry_by_id(self.root)
        self.sid = next(i for i, e in self.registry.items() if e['path'] == 'raw/docs/delivery-note.md')

    def tearDown(self):
        vf.cleanup(self.base)

    def state(self, anchor):
        return anchors.verify(anchor, anchors.Sources(self.root, anchors.registry_by_id(self.root)))

    def rewrite(self, lines, reregister=True):
        """Change the source; with reregister the registry records the new bytes (an accounted revision)."""
        path = self.root / 'raw/docs/delivery-note.md'
        path.write_text('\n'.join(lines), encoding='utf-8')
        if reregister:
            data = json.loads((self.root / 'system/source-registry.json').read_text())
            for e in data['files']:
                if e['id'] == self.sid:
                    e['sha256'], e['bytes'] = anchors.file_sha256(path), path.stat().st_size
            brain.save_json(self.root / 'system/source-registry.json', data)

    def test_unchanged_source_and_heading(self):
        a = anchors.make_anchor(self.root, self.registry, self.sid, 4, 5, today='2026-10-24')
        self.assertEqual(a['heading'], '## Build')
        self.assertEqual(self.state(a), {'state': 'verified', 'lines': [4, 5]})
        b = anchors.make_anchor(self.root, self.registry, self.sid, 9, 9)        # under the second, identical heading
        self.assertEqual(self.state(b)['state'], 'verified')

    def test_moved_lines_are_relocated_only_when_unique(self):
        a = anchors.make_anchor(self.root, self.registry, self.sid, 4, 5)
        self.rewrite(['Inserted line.', 'Another.'] + NOTE)
        self.assertEqual(self.state(a), {'state': 'relocated', 'lines': [6, 7]})
        self.rewrite(['Inserted line.'] + NOTE + NOTE[3:5])                       # the same excerpt twice
        self.assertEqual(self.state(a), {'state': 'ambiguous', 'candidates': 2})

    def test_changed_text_and_modified_evidence(self):
        a = anchors.make_anchor(self.root, self.registry, self.sid, 4, 5)
        changed = list(NOTE)
        changed[3] = 'Build 261019120001 was signed with the team certificate.'
        self.rewrite(changed, reregister=False)
        self.assertEqual(self.state(a)['state'], 'source-modified')             # raw bytes differ from the registry
        self.rewrite(changed)
        self.assertEqual(self.state(a)['state'], 'changed')                      # a recorded revision changed the text

    def test_deleted_source_is_missing_until_restored(self):
        a = anchors.make_anchor(self.root, self.registry, self.sid, 4, 5)
        evidence_store.backup(self.root)
        (self.root / 'raw/docs/delivery-note.md').unlink()
        self.assertEqual(self.state(a)['state'], 'source-missing')
        evidence_store.restore(self.root, 'raw/docs/delivery-note.md')
        self.assertEqual(self.state(a)['state'], 'verified')

    def test_move_keeps_the_anchor(self):
        a = anchors.make_anchor(self.root, self.registry, self.sid, 4, 5)
        brain.move_many(self.root, [('raw/docs/delivery-note.md', 'raw/docs/2026-10-19-delivery-note.md')], reason='fixture')
        self.assertEqual(anchors.registry_by_id(self.root)[self.sid]['path'], 'raw/docs/2026-10-19-delivery-note.md')
        self.assertEqual(self.state(a)['state'], 'verified')

    def test_malformed_and_unknown_anchors(self):
        a = anchors.make_anchor(self.root, self.registry, self.sid, 4, 5)
        self.assertEqual(self.state(dict(a, sha256='abc'))['state'], 'invalid-hash')
        self.assertEqual(self.state(dict(a, sha256='0' * 64))['state'], 'changed')
        self.assertEqual(self.state(dict(a, lines=[5, 4]))['state'], 'invalid-locator')
        self.assertEqual(self.state(dict(a, lines=['4', 5]))['state'], 'invalid-locator')
        self.assertEqual(self.state(dict(a, source='S-000000000000'))['state'], 'source-unregistered')
        (self.root / 'raw/docs/blob.bin').write_bytes(b'\x00\xff\x00')
        brain.register(self.root, 'raw/docs/blob.bin', 'fixture')
        blob = next(i for i, e in anchors.registry_by_id(self.root).items() if e['path'] == 'raw/docs/blob.bin')
        self.assertEqual(self.state(dict(a, source=blob))['state'], 'unsupported-format')
        with self.assertRaises(ValueError):
            anchors.make_anchor(self.root, self.registry, self.sid, 2, 2)       # empty excerpt
        with self.assertRaises(ValueError):
            anchors.make_anchor(self.root, self.registry, self.sid, 9, 99)

    def test_check_findings_and_relocate(self):
        claims = json.loads((self.root / 'system/claims.json').read_text())
        good = anchors.make_anchor(self.root, self.registry, self.sid, 4, 5)
        claims['claims'][0]['anchors'] = [good]
        claims['claims'][1]['anchors'] = [dict(good, sha256='1' * 64)]
        claims['claims'][1]['lifecycle'] = 'archived'
        brain.save_json(self.root / 'system/claims.json', claims)
        brain.build(self.root)
        report = brain.check(self.root, False)
        self.assertTrue(report['ok'], report['errors'])
        self.assertEqual(report['claim_anchors']['active_claims_with_verified_anchor'], 1)
        self.assertTrue(any('C-2: anchor 0' in w and 'changed' in w for w in report['warnings']))    # archived: warning
        claims['claims'][1].pop('lifecycle')
        brain.save_json(self.root / 'system/claims.json', claims)
        report = brain.check(self.root, False)
        self.assertTrue(any('C-2: anchor 0' in e and 'changed' in e for e in report['errors']))     # active: error
        claims['claims'][1]['anchors'] = [{'source': self.sid}]
        brain.save_json(self.root / 'system/claims.json', claims)
        self.assertTrue(any('lacks sha256' in e for e in brain.check(self.root, False)['errors']))
        claims['claims'][1].pop('anchors')
        brain.save_json(self.root / 'system/claims.json', claims)
        self.rewrite(['Inserted line.'] + NOTE)
        self.assertTrue(any('relocated to lines 5-6' in w for w in brain.check(self.root, False)['warnings']))
        moved = anchors.update_claim(self.root, 'C-1', lambda c: [
            i for i, x in enumerate(c['anchors']) if anchors.verify(x, anchors.Sources(self.root, anchors.registry_by_id(self.root)))['state'] == 'relocated'
            and x.update(lines=[5, 6]) is None])
        self.assertEqual(moved, [0])
        self.assertEqual(self.state(json.loads((self.root / 'system/claims.json').read_text())['claims'][0]['anchors'][0])['state'], 'verified')

    def test_superseded_evidence_is_reported(self):
        a = anchors.make_anchor(self.root, self.registry, self.sid, 4, 5, today='2026-10-20')
        self.rewrite(NOTE + ['Appended in a later revision.'])
        data = json.loads((self.root / 'system/source-registry.json').read_text())
        for e in data['files']:
            if e['id'] == self.sid:
                e['revisions'] = [{'on': '2026-10-22', 'reason': 'fixture revision', 'previous_sha256': '0' * 64, 'previous_bytes': 1}]
        brain.save_json(self.root / 'system/source-registry.json', data)
        self.assertEqual(self.state(a), {'state': 'verified', 'lines': [4, 5], 'revised_after_anchoring': '2026-10-22'})
        self.assertNotIn('revised_after_anchoring', self.state(dict(a, anchored_on='2026-10-23')))
        claims = json.loads((self.root / 'system/claims.json').read_text())
        claims['claims'][0]['anchors'] = [a]
        found = anchors.check_findings(self.root, claims['claims'])
        self.assertTrue(any('superseded evidence' in w for w in found[1]))

    def test_proposals_rank_the_excerpt_with_the_build_number(self):
        claim = {'id': 'X', 'text': 'Build 261019120000 was installed on the office headset.', 'sources': ['raw/docs/delivery-note.md']}
        best = anchors.propose(self.root, brain, claim)[0]
        self.assertEqual(best['source'], self.sid)
        self.assertIn('261019120000', best['excerpt'])
        self.assertIn('installed', best['excerpt'])


if __name__ == '__main__':
    unittest.main()
