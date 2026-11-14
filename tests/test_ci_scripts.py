"""The scripts the workflows lean on: the suite runner with its floor, and the workflow rules.

run_suite.py decides whether a CI job passed, so its reading of the unittest output is
tested here line by line and once for real. check_workflows.py needs PyYAML; the lint
job installs it and runs this module with --no-skips.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, '.github', 'scripts')

try:
    import yaml
except ImportError:
    yaml = None


def _load(name):
    spec = importlib.util.spec_from_file_location('leks_ci_' + name, os.path.join(SCRIPTS, name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_suite = _load('run_suite')

PASSED = 'test_a ... ok\n\n----------------------------------------------------------------------\nRan 12 tests in 0.5s\n\nOK\n'


class RunSuiteTests(unittest.TestCase):
    def test_reads_the_last_ran_line(self):
        self.assertEqual(run_suite.ran_count(PASSED), 12)
        self.assertEqual(run_suite.ran_count('Ran 3 tests in 1s\n...\nRan 1 test in 0.1s\n'), 1)
        self.assertIsNone(run_suite.ran_count('Traceback (most recent call last):\n'))
        # a test that prints the words mid-line does not count
        self.assertIsNone(run_suite.ran_count('log: Ran 99 tests in 0s\n'))

    def test_reads_the_skip_count(self):
        self.assertEqual(run_suite.skipped_count(PASSED), 0)
        self.assertEqual(run_suite.skipped_count('Ran 5 tests in 1s\n\nOK (skipped=2)\n'), 2)
        self.assertEqual(run_suite.skipped_count('Ran 9 tests in 1s\n\nFAILED (failures=1, skipped=4)\n'), 4)
        self.assertEqual(run_suite.skipped_count('FAILED (errors=1, skipped=3, expected failures=1)\n'), 3)

    def test_verdicts(self):
        self.assertEqual(run_suite.verdict(0, PASSED, 12)[0], 0)
        self.assertEqual(run_suite.verdict(0, PASSED, 10)[0], 0)
        code, line = run_suite.verdict(0, PASSED, 13)
        self.assertEqual(code, 1)
        self.assertIn('only 12 tests ran, the floor is 13', line)
        code, line = run_suite.verdict(1, PASSED.replace('OK', 'FAILED (failures=1)'), 5)
        self.assertEqual(code, 1)
        self.assertIn('unittest failed', line)
        self.assertEqual(run_suite.verdict(0, 'killed\n', 1)[0], 1)
        # what faulthandler prints when a test passes the per-test limit, after the test's own line
        hung = 'test_x (tests.t.T.test_x) ... Timeout (0:05:00)!\nThread 0x1 (most recent call first):\n'
        code, line = run_suite.verdict(1, hung, 1)
        self.assertEqual(code, 1)
        self.assertIn('a test hung past the per-test limit', line)
        self.assertIn('did not finish', run_suite.verdict(1, 'test_x (tests.t.T.test_x) ... ', 1)[1])
        skipped = PASSED.replace('\nOK\n', '\nOK (skipped=1)\n')
        self.assertEqual(run_suite.verdict(0, skipped, 12)[0], 0)
        code, line = run_suite.verdict(0, skipped, 12, no_skips=True)
        self.assertEqual(code, 1)
        self.assertIn('1 of 12 tests were skipped', line)

    def run_script(self, *args, env=None):
        e = {k: v for k, v in os.environ.items() if not k.startswith('LEKS_') and k != 'GITHUB_STEP_SUMMARY'}
        e.update(env or {})
        r = subprocess.run([sys.executable, '-B', os.path.join(SCRIPTS, 'run_suite.py')] + list(args),
                           cwd=ROOT, env=e, capture_output=True, timeout=300)
        return r.returncode, r.stdout.decode('utf-8', 'replace')

    def test_a_real_run_against_the_floor(self):
        # the count comes from disk, so a test added to test_gate does not break this one
        n = unittest.defaultTestLoader.loadTestsFromName('tests.test_gate').countTestCases()
        code, out = self.run_script('--floor', str(n), 'tests.test_gate')
        self.assertEqual(code, 0, out)
        self.assertIn('ok: %d tests ran (0 skipped), the floor is %d' % (n, n), out)
        code, out = self.run_script('--floor', str(n + 1), 'tests.test_gate')
        self.assertEqual(code, 1, out)
        self.assertIn('only %d tests ran, the floor is %d' % (n, n + 1), out)

    def test_no_skips_fails_a_run_that_skipped(self):
        # without LEKS_TEST_MODELS every test there skips
        code, out = self.run_script('--floor', '1', '--no-skips', 'tests.test_real_models')
        self.assertEqual(code, 1, out)
        self.assertIn('tests were skipped, this job must run them all', out)

    def test_the_floor_in_tests_yml_is_reachable(self):
        with open(os.path.join(ROOT, '.github', 'workflows', 'tests.yml'), encoding='utf-8') as f:
            m = re.search(r'^\s*TESTS_FLOOR:\s*"(\d+)"', f.read(), re.M)
        self.assertIsNotNone(m, 'tests.yml has no TESTS_FLOOR')
        found = unittest.defaultTestLoader.discover(os.path.join(ROOT, 'tests'), top_level_dir=ROOT)
        self.assertLessEqual(int(m.group(1)), found.countTestCases())

    def test_every_named_floor_is_reachable(self):
        # a floor above the tests on disk fails its job on every run. the host format step once asked
        # for 17 tests from a module that had 16, and only a run on github would have shown it
        wf = os.path.join(ROOT, '.github', 'workflows')
        pat = re.compile(r'run_suite\.py\s+--floor\s+(\d+)((?:\s+(?:--no-skips|tests\.[\w.]+))+)')
        seen = 0
        for name in sorted(os.listdir(wf)):
            if not name.endswith(('.yml', '.yaml')):
                continue
            with open(os.path.join(wf, name), encoding='utf-8') as f:
                text = f.read()
            for m in pat.finditer(text):
                mods = [w for w in m.group(2).split() if w.startswith('tests.')]
                if not mods:
                    continue
                n = unittest.defaultTestLoader.loadTestsFromNames(mods).countTestCases()
                self.assertLessEqual(int(m.group(1)), n, '%s: --floor %s for %s, %d tests on disk'
                                     % (name, m.group(1), ' '.join(mods), n))
                seen += 1
        # the transport, conformance, host format, packaging, ci scripts and real model steps
        self.assertGreaterEqual(seen, 6)


@unittest.skipIf(yaml is None, 'PyYAML is not installed in this Python; the lint job installs it')
class CheckWorkflowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load('check_workflows')

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='lkwf')
        self.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copytree(os.path.join(ROOT, '.github'), os.path.join(self.dir, '.github'))

    def edit(self, name, old, new):
        p = os.path.join(self.dir, '.github', name)
        with open(p, encoding='utf-8') as f:
            text = f.read()
        self.assertIn(old, text)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text.replace(old, new, 1))

    def problems(self):
        return self.mod.check(self.dir)

    def floor_step(self, pattern):
        # floors go up as tests get added, so read the current one from the copied workflow
        with open(os.path.join(self.dir, '.github', 'workflows', 'tests.yml'), encoding='utf-8') as f:
            m = re.search(pattern, f.read())
        self.assertIsNotNone(m, pattern)
        return m.group(0)

    def test_the_repository_passes(self):
        self.assertEqual(self.mod.check(ROOT), [])
        self.assertEqual(self.problems(), [])

    def test_a_moving_tag_is_caught(self):
        self.edit('workflows/tests.yml', 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1',
                  'actions/checkout@v7')
        self.assertTrue(any('not pinned' in p for p in self.problems()))

    def test_continue_on_error_is_caught(self):
        self.edit('workflows/tests.yml', '    timeout-minutes: 40\n',
                  '    timeout-minutes: 40\n    continue-on-error: true\n')
        self.assertTrue(any('continue-on-error' in p for p in self.problems()))

    def test_a_missing_cell_is_caught(self):
        self.edit('workflows/tests.yml', 'os: [ubuntu-latest, macos-latest, windows-latest]',
                  'os: [ubuntu-latest, macos-latest]')
        self.assertTrue(any('unit must run on' in p for p in self.problems()))
        self.edit('workflows/tests.yml', 'python: ["3.10", "3.11", "3.12", "3.13"]', 'python: [3.10, "3.13"]')
        self.assertTrue(any('unit must run Python' in p for p in self.problems()))

    def test_an_unpinned_tool_is_caught(self):
        self.edit('workflows/security.yml', 'python -m pip install -r .github/ci/requirements-bandit.txt',
                  'python -m pip install bandit')
        self.assertTrue(any('without a pin: bandit' in p for p in self.problems()))
        with open(os.path.join(self.dir, '.github', 'ci', 'requirements-lint.txt'), 'a', encoding='utf-8') as f:
            f.write('requests>=2\n')
        self.assertTrue(any('not pinned with ==' in p for p in self.problems()))

    def test_models_on_every_pull_request_is_caught(self):
        self.edit('workflows/models.yml', '  workflow_dispatch:\n', '  workflow_dispatch:\n  pull_request:\n')
        self.assertTrue(any('no pull_request trigger' in p for p in self.problems()))

    def test_a_gate_that_may_skip_is_caught(self):
        self.edit('workflows/models.yml', '--no-skips tests.test_real_models', 'tests.test_real_models')
        self.assertTrue(any('--no-skips tests.test_real_models' in p for p in self.problems()))

    def test_a_test_step_that_loses_its_floor_or_no_skips_is_caught(self):
        old = self.floor_step(r'--floor \d+ --no-skips(?=\s+tests\.test_transport tests\.test_doctor)')
        self.edit('workflows/tests.yml', old, old.replace(' --no-skips', ''))
        self.assertTrue(any('model-daemon-tcp runs tests.test_transport tests.test_doctor without --no-skips' in p
                            for p in self.problems()))
        old = self.floor_step(r'--floor \d+ tests\.test_host_payloads')
        self.edit('workflows/tests.yml', old, '--floor 0 tests.test_host_payloads')
        self.assertTrue(any('(tests.test_host_payloads) has no positive whole --floor' in p for p in self.problems()))
        old = self.floor_step(r'--floor \d+ --no-skips tests\.test_ci_scripts')
        self.edit('workflows/tests.yml', old, 'tests.test_ci_scripts')
        probs = self.problems()
        self.assertTrue(any('(tests.test_ci_scripts) has no positive whole --floor' in p for p in probs))
        self.assertTrue(any('lint runs tests.test_ci_scripts without --no-skips' in p for p in probs))
        self.edit('workflows/tests.yml', 'tests.test_packaging', 'tests.test_gate')
        self.assertTrue(any('packaging does not run tests.test_packaging' in p for p in self.problems()))

    def test_a_shallow_secret_scan_is_caught(self):
        self.edit('workflows/security.yml', '          fetch-depth: 0\n', '          fetch-depth: 1\n')
        self.assertTrue(any('whole history' in p for p in self.problems()))


if __name__ == '__main__':
    unittest.main()
