"""bench/gate.py: the words-only quality gate every unit test job runs after the bench.

The results here are made by hand from the stored baseline, so each case moves one
number and checks that the gate says yes or no for the right reason.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, 'bench', 'baseline_words.json')


def _load_gate():
    spec = importlib.util.spec_from_file_location('leks_bench_gate', os.path.join(ROOT, 'bench', 'gate.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()


def stored():
    with open(BASELINE, encoding='utf-8') as f:
        return json.load(f)


def results_like(base):
    """A result file that matches the baseline number for number."""
    systems = {}
    for name, s in base['systems'].items():
        systems[name] = dict(s, status='ok', errors=0)
    return {'meta': {'models': base['models'], 'n_scored': base['n_scored'], 'version': 'test'},
            'systems': systems}


class GateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='lkgate')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.base = stored()
        # keep the job summary of a CI run out of these cases
        patcher = mock.patch.dict(os.environ, {'GITHUB_ACTIONS': '', 'GITHUB_STEP_SUMMARY': ''})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name, d):
        p = os.path.join(self.dir, name)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        return p

    def gate(self, results, base=None, extra=()):
        rp = self.write('results_20990101T000000Z.json', results)
        bp = self.write('baseline.json', self.base if base is None else base)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate.main([rp, '--baseline', bp] + list(extra))
        return code, out.getvalue()

    def drop(self, system, metric, questions):
        r = results_like(self.base)
        s = r['systems'][system]
        s[metric] = round(s[metric] - questions / s['n'], 6)
        return r

    def test_the_baseline_file_says_how_it_was_made(self):
        b = self.base
        self.assertEqual(b['models'], 'none')
        self.assertIn('words', b['systems'])
        self.assertIn('words', b['require'])
        tq = b['tolerance_questions']
        self.assertIsInstance(tq, (int, float))
        self.assertGreaterEqual(tq, 0)
        self.assertIn('bench/run_bench.py --models none', b['measured']['how'])
        self.assertIn('tolerance_questions', b['about'])
        for m in gate.METRICS:
            self.assertGreater(b['systems']['words'][m], 0)

    def test_same_numbers_pass(self):
        code, out = self.gate(results_like(self.base))
        self.assertEqual(code, 0, out)
        self.assertIn('gate passed', out)

    def test_a_drop_within_the_tolerance_passes(self):
        tq = self.base['tolerance_questions']
        if tq < 1:
            self.skipTest('the stored baseline allows no drop')
        code, out = self.gate(self.drop('words', 'top1', tq))
        self.assertEqual(code, 0, out)

    def test_a_drop_past_the_tolerance_fails(self):
        tq = self.base['tolerance_questions']
        for metric in gate.METRICS:
            code, out = self.gate(self.drop('words', metric, tq + 1))
            self.assertEqual(code, 1, out)
            self.assertIn('words %s' % metric, out)
            self.assertIn('below the floor', out)

    def test_more_questions_answered_is_no_failure(self):
        r = results_like(self.base)
        r['systems']['words']['top1'] = 1.0
        code, out = self.gate(r)
        self.assertEqual(code, 0, out)
        self.assertIn('above baseline', out)

    def test_a_different_question_count_fails(self):
        r = results_like(self.base)
        r['meta']['n_scored'] -= 2
        code, out = self.gate(r)
        self.assertEqual(code, 1, out)
        self.assertIn('the pdf questions need pypdf', out)

    def test_a_system_that_did_not_run_fails(self):
        r = results_like(self.base)
        r['systems']['words'] = {'status': 'skipped', 'reason': 'test'}
        code, out = self.gate(r)
        self.assertEqual(code, 1, out)
        self.assertIn('words: did not run', out)

    def test_a_baseline_for_another_mode_is_bad_input(self):
        b = copy.deepcopy(self.base)
        b['models'] = 'fake'
        code, out = self.gate(results_like(self.base), base=b)
        self.assertEqual(code, 2, out)

    def test_a_broken_tolerance_is_bad_input(self):
        b = copy.deepcopy(self.base)
        b['tolerance_questions'] = -1
        code, out = self.gate(results_like(self.base), base=b)
        self.assertEqual(code, 2, out)

    def test_no_baseline_only_checks_that_the_systems_ran(self):
        rp = self.write('results_20990101T000000Z.json', results_like(self.base))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate.main([rp, '--require', 'words'])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn('NO BASELINE', out.getvalue())

    def test_a_named_baseline_that_is_missing_is_bad_input(self):
        # a deleted or misspelled baseline must not turn the gate into a check of nothing
        rp = self.write('results_20990101T000000Z.json', results_like(self.base))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main([rp, '--baseline', os.path.join(self.dir, 'missing.json')])
        self.assertEqual(code, 2, out.getvalue())
        self.assertIn('does not exist', out.getvalue() + err.getvalue())

    def test_write_baseline_round_trip(self):
        rp = self.write('results_20990101T000000Z.json', results_like(self.base))
        made = os.path.join(self.dir, 'made.json')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main([rp, '--write-baseline', made, '--tolerance-questions', '1']), 0)
        with open(made, encoding='utf-8') as f:
            b = json.load(f)
        self.assertEqual(b['tolerance_questions'], 1)
        self.assertEqual(set(b['systems']), set(self.base['systems']))
        code, out = self.gate(results_like(self.base), base=b)
        self.assertEqual(code, 0, out)


if __name__ == '__main__':
    unittest.main()
