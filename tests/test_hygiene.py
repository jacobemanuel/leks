"""Repo hygiene: no em or en dashes, no absolute personal paths, install hints that point at the clone."""
from __future__ import annotations

import os
import re
import shutil
import unittest

from tests import helpers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'build', 'dist', '.pytest_cache',
             '.ruff_cache', '.mypy_cache', '.tox', '.nox', 'htmlcov'}
# in a git worktree .git is a file that holds the absolute path of the main clone
SKIP_FILES = {'SPEC.md', '.git', 'coverage.xml'}
SKIP_SUFFIX = ('.pyc', '.sqlite', '.f32', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.docx', '.zip', '.gz')

# built from parts so this file does not trip its own scan
EM = chr(0x2014)
EN = chr(0x2013)
_SEP = '/'
_FOLDERS = ('Users', 'home', 'Volumes', 'mnt', 'media')
ABS_PATH = re.compile(r'(?<![\w.~$<>{}\-])' + _SEP + '(?:' + '|'.join(_FOLDERS) + ')' + _SEP + r'[\w.\-]')
WIN_PATH = re.compile(r'\b[A-Za-z]:' + re.escape(chr(92)) + r'(?:Users|Documents)', re.I)


def repo_files(root: str):
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.endswith('.egg-info'))
        for fn in sorted(fns):
            if fn in SKIP_FILES or fn.endswith(SKIP_SUFFIX):
                continue
            # coverage data: .coverage, and .coverage.<host>.<pid>.<n> from parallel runs
            if fn == '.coverage' or fn.startswith('.coverage.'):
                continue
            yield os.path.join(dp, fn)


def read_text(path: str):
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError:
        return None
    if b'\x00' in raw:
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None


def scan_dashes(text: str):
    return [i for i, line in enumerate(text.split('\n'), 1) if EM in line or EN in line]


def scan_paths(text: str):
    return [i for i, line in enumerate(text.split('\n'), 1) if ABS_PATH.search(line) or WIN_PATH.search(line)]


class RepoScan(unittest.TestCase):
    def test_no_em_or_en_dash(self):
        bad = []
        for p in repo_files(REPO):
            t = read_text(p)
            if t is not None and scan_dashes(t):
                bad.append(os.path.relpath(p, REPO))
        self.assertEqual(bad, [], 'dash characters in these files')

    def test_no_absolute_personal_paths(self):
        bad = []
        for p in repo_files(REPO):
            t = read_text(p)
            if t is None:
                continue
            for n in scan_paths(t):
                bad.append('%s:%d' % (os.path.relpath(p, REPO), n))
        self.assertEqual(bad, [], 'absolute home or volume paths')

    def test_install_hints_point_at_the_clone(self):
        # leks installs from its clone; a hint that names leks on PyPI would fetch whatever package holds that name
        rx = re.compile(r'pip3? install +["\']?leks\b')
        bad = []
        for p in repo_files(REPO):
            t = read_text(p)
            if t is None:
                continue
            for i, line in enumerate(t.splitlines(), 1):
                if rx.search(line):
                    bad.append('%s:%d' % (os.path.relpath(p, REPO), i))
        self.assertEqual(bad, [])

    def test_ascii_only(self):
        # hidden or look-alike characters (bidi marks, zero width) never ship: use escapes in source
        bad = []
        for p in repo_files(REPO):
            t = read_text(p)
            if t is not None and any(ord(c) > 127 for c in t):
                bad.append(os.path.relpath(p, REPO))
        self.assertEqual(bad, [], 'non-ASCII characters in these files')

    def test_no_binary_surprises(self):
        # every file in the repo is text we can scan, apart from the known suffixes
        odd = []
        for p in repo_files(REPO):
            if read_text(p) is None:
                odd.append(os.path.relpath(p, REPO))
        self.assertEqual(odd, [])

    def test_license_is_mit(self):
        t = read_text(os.path.join(REPO, 'LICENSE'))
        self.assertTrue(t.startswith('MIT License'))
        self.assertRegex(t, r'Copyright \(c\) 2026 \S+ \S+\n')


class ScannerSelfTest(unittest.TestCase):
    """The scanners themselves, on made-up text."""

    def test_dash_scanner(self):
        self.assertEqual(scan_dashes('ok\nbad ' + EM + ' here\nalso' + EN + 'bad'), [2, 3])
        self.assertEqual(scan_dashes('plain - hyphen only'), [])

    def test_path_scanner_hits(self):
        for s in (_SEP + 'Users' + _SEP + 'someone' + _SEP + 'x', 'cd ' + _SEP + 'home' + _SEP + 'a',
                  '"' + _SEP + 'Volumes' + _SEP + 'Disk' + _SEP + 'f"', 'x=' + _SEP + 'mnt' + _SEP + 'd',
                  'C:' + chr(92) + 'Users' + chr(92) + 'me'):
            self.assertEqual(scan_paths(s), [1], s)

    def test_path_scanner_misses(self):
        for s in ('~/projects/example', '<home>/index/master.sqlite', 'cache/home/dir', '$HOME/x',
                  'the Users group', 'see /tmp/leks-1.sock', 'a/Users/b'):
            self.assertEqual(scan_paths(s), [], s)

    def test_walk_skips_spec_git_and_tool_output(self):
        tmp = helpers.short_tmpdir()
        self.addCleanup(shutil.rmtree, tmp, True)
        for rel in ('a.txt', 'SPEC.md', '.git/x', 'sub/b.py', 'sub/__pycache__/c.pyc'):
            full = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w') as f:
                f.write('x')
        # what the local ruff and coverage runs of docs/testing.md leave in the clone, and the .git file of
        # a git worktree. none of it ships, and the scans tripped on it on the next local run
        link = 'gitdir: ' + _SEP + 'Users' + _SEP + 'me' + _SEP + 'clone' + _SEP + '.git\n'
        for rel, data in (('wt/.git', link.encode()), ('wt/c.md', b'y'), ('.ruff_cache/0.17.4/1071', b'\x00\x01'),
                          ('.coverage', b'SQLite format 3\x00'), ('.coverage.host.12.34', b'\x00'),
                          ('htmlcov/index.html', b'<html></html>'), ('coverage.xml', b'<coverage/>'),
                          ('.mypy_cache/3.13/x.json', b'{}'), ('.coveragerc', b'[run]\n')):
            full = os.path.join(tmp, *rel.split('/'))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'wb') as f:
                f.write(data)
        got = sorted(os.path.relpath(p, tmp) for p in repo_files(tmp))
        self.assertEqual(got, ['.coveragerc', 'a.txt', os.path.join('sub', 'b.py'), os.path.join('wt', 'c.md')])


if __name__ == '__main__':
    unittest.main()
