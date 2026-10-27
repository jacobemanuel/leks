"""Tests of the persistent page index (engine R1): rankings equal to the scan engine (R0), freshness after
in-place edits, new and removed pages, the cold-start snapshot, and relative paths for any spelling of the
vault root."""
import os
from pathlib import Path
import unittest

import brain
import page_index
import vault_fixture as vf

QUESTIONS = ['latest build on the headset', 'mirrors render per eye', 'archived proposal plan', 'mirrors',
             'what is the current state now', 'why did every product become its own app', 'How do I record a build?',
             'door grip review', 'old plan shared app', 'mirror pass one two']


class PageIndexTests(unittest.TestCase):
    def setUp(self):
        self.base, self.root = vf.make_vault(store=False)
        vf.page(self.root, 'wiki/codebase/2026-10-17-mirror-pass-one.md', 'Mirror pass one', 'Mirrors first pass.', ['mirrors'], [],
                body='Mirrors first pass mirrors. Every product became its own app.\n')
        vf.page(self.root, 'wiki/codebase/2026-10-19-mirror-pass-two.md', 'Mirror pass two', 'Mirrors second pass.',
                ['mirrors'], [], body='Mirrors second pass on the headset.\n')
        vf.page(self.root, 'wiki/reviews/2026-10-15-door-review.md', 'Door interactions review', 'Door grip review.',
                ['doors', 'grip'], [], category='Workspace and method', body='Door grip pull grip doors.\n')
        (self.root / 'system/WORKFLOWS.md').write_text('# Workflows\nRecord a build with the delivery skill.\n', encoding='utf-8')
        page_index._cache.clear()

    def tearDown(self):
        page_index._cache.clear()
        vf.cleanup(self.base)

    def assertSameAsScan(self):
        for q in QUESTIONS:
            self.assertEqual(brain.search(self.root, q, 10), brain.search_scan(self.root, q, 10), q)

    def test_rankings_equal_scan_engine(self):
        self.assertSameAsScan()
        order = [h['path'] for h in brain.search(self.root, 'mirrors', 10)]
        self.assertLess(order.index('wiki/codebase/2026-10-19-mirror-pass-two.md'),
                        order.index('wiki/codebase/2026-10-17-mirror-pass-one.md'))    # dated decay applies

    def test_in_place_edit_with_same_size_and_mtime_is_seen(self):
        path = self.root / 'wiki/mirrors.md'
        vf.page(self.root, 'wiki/mirrors.md', 'Mirrors', 'Stereo mirrors', ['mirrors'], [], body='alpha marker\n')
        brain.search(self.root, 'mirrors', 5)
        before = os.stat(path)
        path.write_text(path.read_text(encoding='utf-8').replace('alpha', 'omega'), encoding='utf-8')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))     # an editor that restores the time stamp
        after = os.stat(path)
        self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))
        self.assertEqual(brain.search(self.root, 'omega', 5)[0]['path'], 'wiki/mirrors.md')
        self.assertEqual(brain.search(self.root, 'alpha', 5), [])
        self.assertSameAsScan()

    def test_new_and_removed_pages_change_results_and_lengths(self):
        brain.search(self.root, 'mirrors', 5)
        vf.page(self.root, 'wiki/long.md', 'Long page', 'Many words.', ['long'], [], body='mirrors headset build ' * 200)
        self.assertIn('wiki/long.md', [h['path'] for h in brain.search(self.root, 'mirrors', 10)])
        self.assertSameAsScan()                                           # average length and its norms changed
        (self.root / 'wiki/long.md').unlink()
        self.assertNotIn('wiki/long.md', [h['path'] for h in brain.search(self.root, 'mirrors', 10)])
        self.assertSameAsScan()

    def test_snapshot_is_reused_only_for_the_same_engine_code(self):
        brain.search(self.root, 'mirrors', 5)
        page_index.get(brain._namespace(), self.root, save=True)
        fingerprint = page_index._fingerprint()
        root = str(self.root.resolve())
        loaded = page_index._load(root, fingerprint)
        self.assertIsNotNone(loaded)
        self.assertEqual(set(loaded.docs), set(page_index._cache[root].docs))
        self.assertIsNone(page_index._load(root, 'other engine code'))
        (self.root / page_index.SNAPSHOT).write_bytes(b'not a pickle')
        self.assertIsNone(page_index._load(root, fingerprint))
        page_index._cache.clear()
        self.assertSameAsScan()

    def test_plain_ablation_uses_bm25_alone(self):
        vf.page(self.root, 'wiki/vision.md', 'Vision', 'Apple device notes.', ['avp'], [], body='The avp runs the viewer.\n')
        ns = brain._namespace()
        self.assertIn('wiki/vision.md', [h['path'] for h in brain.search(self.root, 'headset', 10)])    # alias headset -> avp
        self.assertNotIn('wiki/vision.md', [h['path'] for h in page_index.search(ns, self.root, 'headset', 10, plain=True)])
        plain = page_index.search(ns, self.root, 'mirrors', 10, plain=True)
        self.assertEqual([h['score'] for h in plain], sorted((h['score'] for h in plain), reverse=True))
        self.assertSameAsScan()

    def test_rule_switches(self):
        ns = brain._namespace()
        for q in QUESTIONS:
            self.assertEqual(page_index.search(ns, self.root, q, 10, rules=page_index.RULES), brain.search_scan(self.root, q, 10))
            self.assertEqual(page_index.search(ns, self.root, q, 10, rules=()), page_index.search(ns, self.root, q, 10, plain=True))
        q = 'what is the current plan'                                    # current intent, no archive intent
        with_archive = page_index.search(ns, self.root, q, 10, rules={'archive'})
        plain = page_index.search(ns, self.root, q, 10, plain=True)
        score = {h['path']: h['score'] for h in with_archive}
        self.assertEqual(score['wiki/archive/old-plan.md'],
                         round(next(h['score'] for h in plain if h['path'] == 'wiki/archive/old-plan.md') * 0.35, 4))

    def test_scan_paths_are_relative_for_any_root_spelling(self):
        root = str(self.root.resolve())
        expected = page_index._scan(root)
        self.assertIn('wiki/mirrors.md', expected)
        self.assertIn('system/WORKFLOWS.md', expected)
        self.assertNotIn('wiki/index.md', expected)
        for spelling in (root + '/', os.path.join(root, 'wiki', '..'), os.path.relpath(root)):
            self.assertEqual(page_index._scan(spelling), expected, spelling)
        self.assertTrue(all(not os.path.isabs(p) and not p.startswith('..') for p in expected))


if __name__ == '__main__':
    unittest.main()
