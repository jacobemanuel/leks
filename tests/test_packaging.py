"""Packaging: build the sdist and the wheel, check them, install the wheel into a fresh
virtual environment (not editable) and use the installed command from outside the source tree.

What ships where: the wheel carries the package (every module under leks/ and the
package data leks/ocr/*.swift) and the `leks` command. The sdist carries the same plus
the project template under examples/, which docs/install.md tells people to copy.

Slow, and the build backend comes from the package index, so it runs only with
LEKS_TEST_PACKAGING=1 (the CI job "packaging" sets it). The running Python needs the
build and twine packages: pip install build twine.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

from leks import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENABLED = os.environ.get('LEKS_TEST_PACKAGING') == '1'
WIN = os.name == 'nt'
# left out of the build copy: vcs data, earlier build output, caches, and the readme media
SKIP_COPY = ('.git', 'build', 'dist', '*.egg-info', '__pycache__', '*.pyc', '.venv', 'venv',
             '.DS_Store', 'media')

NOTES = {
    'design.md': '# Design\n\nThe tile cache keeps tiles of 256 pixels. Back to [[release]].\n',
    'release.md': '# Release checklist\n\nRun the render tests before tagging. See [[design]].\n',
    'STATE.md': '# State\n\nThe tile cache is in review: [[design]], [[release]].\n',
}


def source_files(sub, suffix=''):
    """Files under ROOT/sub as forward-slash paths relative to ROOT, without caches."""
    out = []
    for d, dirs, files in os.walk(os.path.join(ROOT, sub)):
        dirs[:] = [x for x in dirs if x != '__pycache__']
        for f in files:
            if f.endswith(suffix) and f != '.DS_Store' and not f.endswith('.pyc'):
                out.append(os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, '/'))
    return sorted(out)


def pyproject_version():
    with open(os.path.join(ROOT, 'pyproject.toml'), 'rb') as f:
        raw = f.read()
    try:
        import tomllib
        return tomllib.loads(raw.decode('utf-8'))['project']['version']
    except ImportError:  # python 3.10
        m = re.search(r'^\[project\][^\[]*?^version\s*=\s*"([^"]+)"', raw.decode('utf-8'), re.M | re.S)
        return m.group(1)


def wheel_metadata(wheel):
    """The METADATA text of a wheel."""
    with zipfile.ZipFile(wheel) as z:
        name = [n for n in z.namelist() if n.endswith('.dist-info/METADATA')][0]
        return z.read(name).decode('utf-8')


def run(argv, **kw):
    kw.setdefault('capture_output', True)
    kw.setdefault('timeout', 600)
    r = subprocess.run(argv, **kw)
    r.out = r.stdout.decode('utf-8', 'replace') if isinstance(r.stdout, bytes) else (r.stdout or '')
    r.err = r.stderr.decode('utf-8', 'replace') if isinstance(r.stderr, bytes) else (r.stderr or '')
    return r


@unittest.skipUnless(ENABLED, 'set LEKS_TEST_PACKAGING=1 to build, install and run the package (needs build and twine); the CI job "packaging" does')
class PackagingTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dir = os.path.realpath(tempfile.mkdtemp(prefix='lekspkg'))
        try:
            cls._setup()
        except BaseException:
            shutil.rmtree(cls.dir, ignore_errors=True)
            raise

    @classmethod
    def _setup(cls):
        # build from a clean copy: nothing lands in the checkout, and no stale build/ folder
        # from an earlier run can sneak files into the wheel
        src = os.path.join(cls.dir, 'src')
        shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(*SKIP_COPY))
        dist = os.path.join(cls.dir, 'dist')
        # no --sdist/--wheel: build makes the sdist, then the wheel from that sdist,
        # so a file missing from the sdist shows up as a missing file in the wheel too
        r = run([sys.executable, '-m', 'build', '--outdir', dist, src])
        if r.returncode != 0:
            raise RuntimeError('python -m build failed:\n' + (r.out + r.err)[-2000:])
        wheels = glob.glob(os.path.join(dist, '*.whl'))
        sdists = glob.glob(os.path.join(dist, '*.tar.gz'))
        if len(wheels) != 1 or len(sdists) != 1:
            raise RuntimeError('expected one wheel and one sdist, found %s' % os.listdir(dist))
        cls.wheel, cls.sdist = wheels[0], sdists[0]
        venv = os.path.join(cls.dir, 'venv')
        r = run([sys.executable, '-m', 'venv', venv])
        if r.returncode != 0:
            raise RuntimeError('venv failed: ' + r.err[-800:])
        bindir = os.path.join(venv, 'Scripts' if WIN else 'bin')
        cls.py = os.path.join(bindir, 'python.exe' if WIN else 'python')
        cls.leks_cmd = os.path.join(bindir, 'leks.exe' if WIN else 'leks')
        cls.venv = venv
        # the core needs nothing beyond the standard library, so the wheel installs offline
        r = run([cls.py, '-m', 'pip', 'install', '--no-index', '--no-deps', '--disable-pip-version-check', cls.wheel])
        if r.returncode != 0:
            raise RuntimeError('pip install of the wheel failed:\n' + (r.out + r.err)[-2000:])
        user = os.path.join(cls.dir, 'user')
        os.makedirs(user)
        cls.work = os.path.join(cls.dir, 'work')
        os.makedirs(cls.work)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('LEKS_') and k not in ('PYTHONPATH', 'PYTHONHOME', 'CLAUDE_PROJECT_DIR')}
        env.update({'HOME': user, 'USERPROFILE': user, 'PYTHONDONTWRITEBYTECODE': '1'})
        cls.env = env

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def leks(self, *args, env=None, data=b''):
        return run([self.leks_cmd] + list(args), cwd=self.work, env=env or self.env, input=data, timeout=180)

    def test_versions_agree(self):
        want = pyproject_version()
        self.assertTrue(os.path.basename(self.wheel).startswith('leks-%s-' % want), self.wheel)
        self.assertTrue(os.path.basename(self.sdist).startswith('leks-%s.' % want), self.sdist)
        r = run([self.py, '-c', 'import importlib.metadata as m, leks; print(leks.__version__); '
                 'print(m.version("leks")); print(leks.__file__)'], cwd=self.work, env=self.env)
        self.assertEqual(r.returncode, 0, r.err)
        ver, meta, where = r.out.split('\n')[:3]
        self.assertEqual(ver.strip(), want)
        self.assertEqual(meta.strip(), want)
        # the import must come from the venv, never from the source tree
        self.assertTrue(os.path.realpath(where.strip()).startswith(os.path.realpath(self.venv)), where)

    def test_twine_check(self):
        r = run([sys.executable, '-m', 'twine', 'check', '--strict', self.wheel, self.sdist])
        self.assertEqual(r.returncode, 0, r.out + r.err)

    def test_license_is_an_spdx_expression(self):
        # the license table and license classifiers are deprecated in setuptools, which may refuse them
        meta = wheel_metadata(self.wheel)
        self.assertIn('License-Expression: MIT', meta.splitlines())
        self.assertIn('License-File: LICENSE', meta.splitlines())
        self.assertNotIn('Classifier: License ::', meta)

    def test_swift_sources_ship(self):
        want = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, 'leks', 'ocr', '*.swift')))
        self.assertTrue(want, 'no leks/ocr/*.swift in the source tree')
        with zipfile.ZipFile(self.wheel) as z:
            in_wheel = sorted(os.path.basename(n) for n in z.namelist()
                              if n.startswith('leks/ocr/') and n.endswith('.swift'))
        self.assertEqual(in_wheel, want)
        with tarfile.open(self.sdist) as t:
            in_sdist = sorted(os.path.basename(n) for n in t.getnames() if '/leks/ocr/' in n and n.endswith('.swift'))
        self.assertEqual(in_sdist, want)

    def test_wheel_holds_the_package_and_nothing_else(self):
        want = pyproject_version()
        with zipfile.ZipFile(self.wheel) as z:
            names = set(z.namelist())
            entry = z.read('leks-%s.dist-info/entry_points.txt' % want).decode('utf-8')
        for f in source_files('leks', '.py') + source_files('leks/ocr', '.swift'):
            self.assertIn(f, names)
        tops = {n.split('/')[0] for n in names}
        self.assertEqual(tops, {'leks', 'leks-%s.dist-info' % want})
        self.assertRegex(entry, r'(?m)^\[console_scripts\]\s*^leks\s*=\s*leks\.__main__:main\s*$')

    def test_sdist_holds_the_project_template(self):
        template = source_files('examples/project-template')
        self.assertTrue(template, 'no examples/project-template in the source tree')
        with tarfile.open(self.sdist) as t:
            # leks-3.0.0/examples/... -> examples/...
            names = {n.split('/', 1)[1] for n in t.getnames() if '/' in n}
        for f in template + ['pyproject.toml', 'README.md', 'LICENSE'] + source_files('leks', '.py'):
            self.assertIn(f, names)

    def test_entry_points_and_doctor(self):
        for argv in ([self.leks_cmd, '--help'], [self.py, '-m', 'leks', '--help']):
            r = run(argv, cwd=self.work, env=self.env)
            self.assertEqual(r.returncode, 0, r.err)
            self.assertIn('doctor', r.out)
        # no config yet: doctor still answers, with exit 1 and the reason
        r = self.leks('doctor', env=dict(self.env, LEKS_HOME=os.path.join(self.dir, 'nothing-here')))
        self.assertEqual(r.returncode, 1, r.out + r.err)
        d = json.loads(r.out)
        self.assertEqual(d['leks'], pyproject_version())
        self.assertFalse(d['config']['ok'])
        self.assertIn(d['model_transport'], ('unix', 'tcp'))
        if WIN:
            self.assertEqual(d['model_transport'], 'tcp')

    def test_cli_end_to_end(self):
        r = self.leks('--help')
        self.assertEqual(r.returncode, 0, r.err)
        self.assertIn('usage', r.out.lower())
        home = os.path.join(self.dir, 'leks-home')
        r = self.leks('init', '--home', home)
        self.assertEqual(r.returncode, 0, r.err)
        made = json.loads(r.out)
        self.assertTrue(made['created'])
        cfg_path = made['config']
        with open(cfg_path, encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertIn('roots', cfg)
        notes = os.path.join(self.dir, 'notes')
        os.makedirs(notes)
        for n, text in NOTES.items():
            with open(os.path.join(notes, n), 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
        # point the starter config at the tiny notes folder, words only
        cfg['roots'] = [{'name': 'notes', 'path': notes, 'kind': 'notes'}]
        cfg['profiles'] = {'main': {'roots': ['notes']}}
        cfg['default_profile'] = 'main'
        cfg['models'] = dict(cfg.get('models') or {}, embedder=None, reranker=None)
        cfg['ocr'] = dict(cfg.get('ocr') or {}, enabled='off')
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
        env = dict(self.env, LEKS_HOME=home)
        r = self.leks('config', 'check', env=env)
        self.assertEqual(r.returncode, 0, r.out + r.err)
        r = self.leks('doctor', env=env)
        self.assertEqual(r.returncode, 0, r.out + r.err)
        doc = json.loads(r.out)
        self.assertEqual(doc['config']['roots'], ['notes'])
        self.assertFalse(doc['index']['built'])
        r = self.leks('build', env=env)
        self.assertEqual(r.returncode, 0, r.out + r.err)
        built = json.loads(r.out)
        self.assertEqual(built['update']['indexed'], len(NOTES))
        self.assertEqual(built['update']['errors'], 0)
        r = self.leks('search', 'tile', 'cache', 'size', '--json', env=env)
        self.assertEqual(r.returncode, 0, r.out + r.err)
        found = json.loads(r.out)
        self.assertTrue(found['results'])
        self.assertEqual(found['results'][0]['path'], 'notes/design.md')
        r = self.leks('status', env=env)
        self.assertEqual(r.returncode, 0, r.out + r.err)
        r = self.leks('doctor', env=env)
        self.assertEqual(r.returncode, 0, r.out + r.err)
        self.assertTrue(json.loads(r.out)['index']['built'])
        # check: the first run records, a dead link in a new note is reported with exit 2
        r = self.leks('check', '--root', notes, env=env)
        self.assertEqual((r.returncode, r.err), (0, ''))
        with open(os.path.join(notes, 'broken.md'), 'w', encoding='utf-8', newline='\n') as f:
            f.write('# Broken\n\nThis links to [[no-such-note]].\n')
        r = self.leks('check', '--root', notes, env=env)
        self.assertEqual(r.returncode, 2, r.out + r.err)
        self.assertIn('no-such-note', r.err)
        # mcp handshake with the installed server
        lines = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'protocolVersion': '2025-11-25', 'capabilities': {},
                        'clientInfo': {'name': 'packaging-test', 'version': '0'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'search', 'arguments': {'query': 'tile cache'}}},
        ]
        data = ('\n'.join(json.dumps(m) for m in lines) + '\n').encode('utf-8')
        r = self.leks('serve', env=dict(env, LEKS_PROFILE='main'), data=data)
        self.assertEqual(r.returncode, 0, r.err)
        replies = [json.loads(x) for x in r.out.splitlines() if x.strip()]
        self.assertEqual([m['id'] for m in replies], [1, 2, 3])
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'leks')
        self.assertEqual(replies[0]['result']['serverInfo']['version'], pyproject_version())
        self.assertEqual(sorted(t['name'] for t in replies[1]['result']['tools']),
                         sorted(t['name'] for t in server.TOOLS))
        self.assertFalse(replies[2]['result'].get('isError', False), replies[2])
        self.assertIn('notes/design.md', replies[2]['result']['content'][0]['text'])


if __name__ == '__main__':
    unittest.main()
