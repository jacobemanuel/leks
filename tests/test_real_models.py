"""The pinned embedder and reranker with their real weights, end to end.

Runs only with LEKS_TEST_MODELS=1 and LEKS_TEST_WEIGHTS set to a folder that holds
the weights `leks models fetch` downloaded (the folder models.weights_dir names).
The workflow "models" fetches them on Linux and runs this module with --no-skips,
so a skip there is a failure. Everywhere else the module skips: the local sandbox
of a normal test run has neither the models extra nor the weights.

The fixture is a dozen short notes. The question shares no content word with the
note that answers it, so the words lane alone has nothing to go on; the meaning
lane and the reranker have to carry it.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENABLED = os.environ.get('LEKS_TEST_MODELS') == '1'
WEIGHTS = os.environ.get('LEKS_TEST_WEIGHTS', '')
WHY = ('set LEKS_TEST_MODELS=1 and LEKS_TEST_WEIGHTS to fetched weights; the models workflow does, '
       'the local sandbox of a normal test run has no model weights')

ANSWER = 'notes/server-room.md'
QUESTION = 'what happens to the computers if the cooling breaks'
NOTES = {
    'server-room.md': '# Server room\n\nWhen the air conditioning fails, the racks overheat and the '
                      'machines shut themselves down after about ten minutes.\n',
    'release.md': '# Release checklist\n\nRun the render tests, tag the commit and write the change log.\n',
    'tile-cache.md': '# Tile cache\n\nThe viewer keeps tiles of 256 pixels and drops the oldest first.\n',
    'onboarding.md': '# Onboarding\n\nNew people get a laptop, an account and a buddy for the first week.\n',
    'invoices.md': '# Invoices\n\nInvoices are paid within thirty days; the finance inbox collects them.\n',
    'fonts.md': '# Fonts\n\nHeadings use a serif face, body text a sans face at sixteen points.\n',
    'holiday.md': '# Holiday plan\n\nThe office closes between the last week of December and early January.\n',
    'backups.md': '# Database backups\n\nA full dump runs every night at two and is kept for a month.\n',
    'logo.md': '# Logo colours\n\nThe logo is dark blue on white, never on a photo.\n',
    'meetings.md': '# Weekly meeting\n\nMondays at ten, thirty minutes, notes go into the shared folder.\n',
    'coffee.md': '# Kitchen\n\nThe espresso machine needs descaling once a month.\n',
    'travel.md': '# Travel budget\n\nTrain over plane below six hours; hotels up to the city rate.\n',
}
DISTRACTOR = NOTES['invoices.md']


def cosine(a, b):
    a, b = [float(x) for x in a], [float(x) for x in b]
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


@unittest.skipUnless(ENABLED, WHY)
class RealModelTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(WEIGHTS):
            raise RuntimeError('LEKS_TEST_WEIGHTS is not a folder: %r' % WEIGHTS)
        cls.dir = os.path.realpath(tempfile.mkdtemp(prefix='lkmodels'))
        try:
            cls._setup()
        except BaseException:
            cls._stop()
            shutil.rmtree(cls.dir, ignore_errors=True)
            raise

    @classmethod
    def _setup(cls):
        home = os.path.join(cls.dir, 'home')
        env = {k: v for k, v in os.environ.items() if not k.startswith('LEKS_') and k != 'PYTHONPATH'}
        env['LEKS_HOME'] = home
        cls.env = env
        made = cls.cli('init', '--home', home)
        notes = os.path.join(cls.dir, 'notes')
        os.makedirs(notes)
        for name, text in NOTES.items():
            with open(os.path.join(notes, name), 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
        with open(made['config'], encoding='utf-8') as f:
            cfg = json.load(f)
        # the starter config keeps its pinned embedder and reranker; only the folders change
        cfg['roots'] = [{'name': 'notes', 'path': notes, 'kind': 'notes'}]
        cfg['profiles'] = {'main': {'roots': ['notes']}}
        cfg['default_profile'] = 'main'
        cfg['models']['weights_dir'] = os.path.abspath(WEIGHTS)
        cfg['ocr'] = dict(cfg.get('ocr') or {}, enabled='off')
        with open(made['config'], 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
        cls.keys = {cfg['models'][r]['key'] for r in ('embedder', 'reranker')}
        cls.cli('build')
        cls.cli('embed')
        # the search starts the daemon and waits until both models answer
        cls.found = cls.cli('search', QUESTION, '--k', '5', '--json')

    @classmethod
    def cli(cls, *args, want_json=True):
        r = subprocess.run([sys.executable, '-B', '-m', 'leks'] + list(args), cwd=ROOT, env=cls.env,
                           capture_output=True, timeout=900)
        out, err = r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')
        if r.returncode != 0:
            raise AssertionError('leks %s failed (%d):\n%s\n%s' % (' '.join(args), r.returncode, out[-2000:],
                                                                    err[-2000:]))
        return json.loads(out) if want_json and out.strip().startswith('{') else out

    @classmethod
    def _stop(cls):
        if getattr(cls, 'env', None):
            try:
                subprocess.run([sys.executable, '-B', '-m', 'leks', 'modeld', 'stop'], cwd=ROOT, env=cls.env,
                               capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                pass

    @classmethod
    def tearDownClass(cls):
        cls._stop()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def client(self):
        from leks import config, modeld_client
        with mock.patch.dict(os.environ, {'LEKS_HOME': self.env['LEKS_HOME']}):
            os.environ.pop('LEKS_CONFIG', None)
            cfg = config.load()
        return modeld_client.Client.from_config(cfg, wake=True)

    def test_weights_match_their_manifest(self):
        got = self.cli('models', 'check')
        self.assertEqual(set(got), self.keys)
        for key, v in got.items():
            self.assertTrue(v.get('ok'), (key, v))

    def test_search_finds_the_answer_in_the_top_5(self):
        paths = [x['path'] for x in self.found['results']]
        self.assertIn(ANSWER, paths[:5], paths)
        # both models ran on every passage: no words-only fallback, no partial vectors
        self.assertEqual(self.found['lanes'], 'lanes: words, meaning, reranker', self.found['lanes'])

    def test_words_alone_do_not_find_it(self):
        # the control: without the models the answer is not at the top, so the test above
        # measures the models and not a lucky word match
        words = self.cli('search', QUESTION, '--k', '5', '--json', '--words-only')
        paths = [x['path'] for x in words['results']]
        self.assertNotEqual(paths[:1], [ANSWER], paths)

    def test_embedder_puts_the_answer_closer_than_an_unrelated_note(self):
        c = self.client()
        q = c.embed([QUESTION], kind='q', budget=300)
        d = c.embed([NOTES['server-room.md'], DISTRACTOR], kind='d', budget=300)
        self.assertIsNotNone(q, c.why)
        self.assertIsNotNone(d, c.why)
        self.assertEqual(len(q[0]), 768)
        self.assertAlmostEqual(math.sqrt(sum(float(x) ** 2 for x in q[0])), 1.0, places=3)
        self.assertGreater(cosine(q[0], d[0]), cosine(q[0], d[1]))

    def test_reranker_scores_the_answer_above_an_unrelated_note(self):
        c = self.client()
        s = c.rerank(QUESTION, [DISTRACTOR, NOTES['server-room.md']], budget=300)
        self.assertIsNotNone(s, c.why)
        self.assertEqual(len(s), 2)
        self.assertGreater(float(s[1]), float(s[0]))


if __name__ == '__main__':
    unittest.main()
