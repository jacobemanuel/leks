"""Run the unit suite and hold it to a floor of tests that must have run.

    python .github/scripts/run_suite.py --floor N
    python .github/scripts/run_suite.py --floor N --no-skips tests.test_real_models

Runs `python -B -m unittest discover -s tests -t .` from the repository root, prints
its output as it comes and then reads the "Ran N tests" line. Exit 0 only when
unittest passed and N is at least the floor. A suite that quietly lost tests (a
module that stopped importing counts as one failed test, a discovery change can
miss whole files) fails here even when every test that ran passed. unittest counts
skipped tests in N, so a skip on one system does not lower the count.

With test names it runs just those (python -m unittest -v NAMES). --no-skips also
fails the run when any test was skipped, for jobs whose whole point is to run tests
that skip elsewhere (the real model weights).

Every test runs verbose, one line each, so the log names the test a run stopped in.
A test that runs past --hang-after seconds (300 by default) makes faulthandler print
the stack of every thread and end the run, so a hang fails in minutes with its
stacks in the log instead of at the job timeout with nothing.

Standard library only.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAN = re.compile(r'^Ran (\d+) tests? in ', re.M)
SKIPPED = re.compile(r'^(?:OK|FAILED) \((?:.*, )?skipped=(\d+)', re.M)
# faulthandler's header, on the same line as the verbose "test_x (...) ... " it stopped in
HUNG = re.compile(r'Timeout \(\d+:\d\d:\d\d\)!')

# python -m unittest, plus a faulthandler timer that each test re-arms when it starts.
# armed once before discovery too, so a module that hangs on import is caught.
# runs with -c so sys.path[0] is the repository root, as with -m
BOOT = '''import faulthandler, sys, unittest
LIMIT = %d
if LIMIT > 0:
    faulthandler.dump_traceback_later(LIMIT, exit=True, file=sys.__stderr__)
class Result(unittest.TextTestResult):
    def startTest(self, test):
        if LIMIT > 0:
            faulthandler.dump_traceback_later(LIMIT, exit=True, file=sys.__stderr__)
        super().startTest(test)
    # each traceback also right after its test line, so a run that ends in a hang still shows them
    def addError(self, test, err):
        super().addError(test, err)
        self.now(self.errors)
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.now(self.failures)
    def now(self, found):
        if self.showAll and found:
            self.stream.write(found[-1][1])
            self.stream.flush()
class Runner(unittest.TextTestRunner):
    resultclass = Result
sys.argv[0] = 'python -m unittest'
unittest.main(module=None, testRunner=Runner)
'''


def ran_count(text):
    """The N of the last "Ran N tests in ..." line, or None."""
    found = RAN.findall(text)
    return int(found[-1]) if found else None


def skipped_count(text):
    """How many tests the last summary line says were skipped (0 when it names none)."""
    found = SKIPPED.findall(text)
    return int(found[-1]) if found else 0


def verdict(code, text, floor, no_skips=False):
    """(exit code, one line) for a finished unittest run."""
    n = ran_count(text)
    if n is None:
        if HUNG.search(text):
            return 1, 'a test hung past the per-test limit, or an import did: the last test line and the thread stacks are above'
        return 1, 'no "Ran N tests" line in the output, the suite did not finish'
    if code != 0:
        return code, 'unittest failed (exit %d) after %d tests' % (code, n)
    if n < floor:
        return 1, 'only %d tests ran, the floor is %d: tests were lost' % (n, floor)
    skipped = skipped_count(text)
    if no_skips and skipped:
        return 1, '%d of %d tests were skipped, this job must run them all' % (skipped, n)
    return 0, '%d tests ran (%d skipped), the floor is %d' % (n, skipped, floor)


def note(code, line):
    if os.environ.get('GITHUB_ACTIONS') == 'true' and code:
        print('::error title=unit suite::%s' % line)
    print(('ok: ' if code == 0 else 'FAILED: ') + line)
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write('### unit suite\n\n%s %s\n\n' % ('passed:' if code == 0 else '**failed**:', line))
        except OSError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description='Run the unit suite and hold it to a floor of tests.')
    ap.add_argument('--floor', type=int, required=True, help='fewest tests that must have run')
    ap.add_argument('--no-skips', action='store_true', help='a skipped test fails the run')
    ap.add_argument('--hang-after', type=int, default=300,
                    help='seconds one test may run before every thread stack is printed and the run ends (0: no limit)')
    ap.add_argument('tests', nargs='*', help='test names for python -m unittest (default: discover all)')
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    argv = [sys.executable, '-B', '-c', BOOT % max(0, a.hang_after)]
    argv += ['-v'] + a.tests if a.tests else ['discover', '-v', '-s', 'tests', '-t', '.']
    p = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    seen = []
    for raw in p.stdout:
        line = raw.decode('utf-8', 'replace')
        seen.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    code = p.wait()
    code, line = verdict(code, ''.join(seen), a.floor, a.no_skips)
    note(code, line)
    return code


if __name__ == '__main__':
    sys.exit(main())
