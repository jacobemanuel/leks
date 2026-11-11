"""Sandbox profile and wrapper. The real sandbox-exec runs only on macOS."""
from __future__ import annotations

import importlib
import json
import os
import socket
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from leks import sandbox
from tests import helpers
from tests.test_modeld import BOOT, ask, decode, wait_ready

DARWIN = sys.platform == 'darwin' and os.access('/usr/bin/sandbox-exec', os.X_OK)

ENV_KEYS = {'PATH', 'HOME', 'TMPDIR', 'PYTHONNOUSERSITE', 'PYTHONDONTWRITEBYTECODE', 'HF_HUB_OFFLINE',
            'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK', 'TOKENIZERS_PARALLELISM',
            'TORCHINDUCTOR_CACHE_DIR', 'LEKS_HOME', 'LEKS_CONFIG', 'LEKS_MODELD_SOCK', 'HF_HOME'}

# runs inside the sandbox: reports what it may and may not do
PROBE = r"""
import json, os, socket, subprocess, sys
port, sock, outside, inside = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
res = {}
def err(e):
    return type(e).__name__
try:
    socket.create_connection(('127.0.0.1', port), timeout=3).close()
    res['tcp'] = 'connected'
except OSError as e:
    res['tcp'] = err(e)
try:
    s = socket.socket(socket.AF_UNIX)
    s.bind(sock)
    s.listen(1)
    s.close()
    os.remove(sock)
    res['bind'] = 'ok'
except OSError as e:
    res['bind'] = err(e)
for name, path in (('write_outside', outside), ('write_inside', inside)):
    try:
        with open(path, 'w') as f:
            f.write('x')
        res[name] = 'ok'
    except OSError as e:
        res[name] = err(e)
try:
    subprocess.run(['/bin/echo', 'hi'], capture_output=True, timeout=5)
    res['exec'] = 'ran'
except OSError as e:
    res['exec'] = err(e)
print(json.dumps(res))
"""


def fake_cfg(run, sock, daemon=None, home='~/projects/example/.leks', source=None):
    return SimpleNamespace(run_dir=lambda: run, socket_path=lambda: sock, daemon=daemon or {'sandbox': 'auto'},
                           home=home, source_path=source)


class ProfileTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='lk', dir='/tmp')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run = os.path.join(self.tmp, 'run')
        self.sock = os.path.join(self.run, 'modeld.sock')
        self.cfg = fake_cfg(self.run, self.sock)
        self.real_run = os.path.realpath(self.run)

    def test_core_rules_in_order(self):
        t = sandbox.profile_text(self.cfg, sys.executable)
        want = ['(version 1)', '(allow default)', '(deny network*)', '(allow network-bind network-inbound',
                '(deny file-write*)', '(allow file-write*', '(deny process-exec)', '(allow process-exec']
        pos = [t.index(w) for w in want]
        self.assertEqual(pos, sorted(pos))
        self.assertIn('(local unix-socket (path-literal "%s"))' % os.path.join(self.real_run, 'modeld.sock'), t)
        self.assertIn('(subpath "%s")' % self.real_run, t)
        self.assertIn('(literal "/dev/null")', t)
        self.assertIn('(literal "/dev/dtracehelper")', t)
        self.assertIn('(literal "%s")' % os.path.realpath(sys.executable), t)

    def test_every_line_has_a_short_comment(self):
        for ln in sandbox.profile_text(self.cfg, sys.executable).splitlines():
            self.assertIn(';', ln, ln)
            comment = ln.split(';', 1)[1].strip()
            self.assertTrue(0 < len(comment) < 120, ln)

    def test_no_dashes_and_no_tabs(self):
        t = sandbox.profile_text(self.cfg, sys.executable)
        self.assertNotIn(chr(0x2014), t)
        self.assertNotIn(chr(0x2013), t)
        self.assertNotIn('\t', t)

    @unittest.skipUnless(sys.platform == 'darwin', 'darwin cache dirs')
    def test_darwin_dirs_are_writable(self):
        t = sandbox.profile_text(self.cfg, sys.executable)
        self.assertIn(os.path.realpath(os.confstr(65538).rstrip('/')), t)
        self.assertIn(os.path.realpath(os.confstr(65537).rstrip('/')), t)

    def test_extra_read(self):
        t = sandbox.profile_text(self.cfg, sys.executable, extra_read=[self.tmp])
        self.assertIn('(allow file-read* (subpath "%s"))' % os.path.realpath(self.tmp), t)
        self.assertNotIn('file-read*', sandbox.profile_text(self.cfg, sys.executable))

    def test_quotes_in_paths_are_escaped(self):
        cfg = fake_cfg('/tmp/a"b/run', '/tmp/a"b/run/s.sock')
        t = sandbox.profile_text(cfg, sys.executable)
        self.assertIn('a\\"b', t)
        with self.assertRaises(ValueError):
            sandbox.profile_text(fake_cfg('/tmp/a\nb/run', '/tmp/a\nb/run/s.sock'), sys.executable)

    def test_write_profile(self):
        p = sandbox.write_profile(self.cfg)
        self.assertEqual(p, os.path.join(self.run, 'modeld.sb'))
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        with open(p, encoding='utf-8') as f:
            self.assertEqual(f.read(), sandbox.profile_text(self.cfg, sys.executable))


class EnvTests(unittest.TestCase):
    def setUp(self):
        self.home, self.cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        self.run = os.path.realpath(self.cfg.run_dir())

    def test_exact_whitelist(self):
        env = sandbox.clean_env(self.cfg)
        self.assertEqual(set(env), ENV_KEYS | {'LEKS_ALLOW_FAKE'})
        self.assertEqual(env['PATH'], '/usr/bin:/bin')
        self.assertEqual(env['HOME'], os.path.join(self.run, 'home'))
        self.assertEqual(env['TMPDIR'], os.path.join(self.run, 'tmp'))
        self.assertEqual(env['TORCHINDUCTOR_CACHE_DIR'], os.path.join(self.run, 'cache'))
        self.assertEqual(env['LEKS_HOME'], self.cfg.home)
        self.assertEqual(env['LEKS_CONFIG'], self.cfg.source_path)
        self.assertTrue(os.path.isabs(env['LEKS_CONFIG']))
        self.assertEqual(env['LEKS_MODELD_SOCK'], self.cfg.socket_path())
        for k in ('PYTHONNOUSERSITE', 'PYTHONDONTWRITEBYTECODE', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE',
                  'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK'):
            self.assertEqual(env[k], '1')
        self.assertEqual(env['TOKENIZERS_PARALLELISM'], 'false')
        for d in ('home', 'tmp', 'cache'):
            self.assertTrue(os.path.isdir(os.path.join(self.run, d)))

    def test_nothing_leaks_from_the_parent(self):
        dirty = {'DYLD_LIBRARY_PATH': '/x', 'DYLD_INSERT_LIBRARIES': '/y', 'PYTHONPATH': '/z',
                 'PYTHONSTARTUP': '/s', 'HF_TOKEN': 'abc', 'SOME_SECRET': 'q', 'LEKS_PROFILE': 'architect'}
        with mock.patch.dict(os.environ, dirty):
            env = sandbox.clean_env(self.cfg)
        self.assertEqual(set(env), ENV_KEYS | {'LEKS_ALLOW_FAKE'})
        self.assertFalse(set(dirty) & set(env))

    def test_hub_cache_goes_under_leks_home(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('HF_HOME', None)
            env = sandbox.clean_env(self.cfg)
        self.assertEqual(env['HF_HOME'], os.path.join(self.cfg.home, 'models', 'hf-home'))

    def test_hub_cache_of_the_user_wins(self):
        with mock.patch.dict(os.environ, {'HF_HOME': '/srv/hf-cache'}):
            self.assertEqual(sandbox.clean_env(self.cfg)['HF_HOME'], '/srv/hf-cache')

    def test_fake_switch_only_when_set(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('LEKS_ALLOW_FAKE', None)
            self.assertNotIn('LEKS_ALLOW_FAKE', sandbox.clean_env(self.cfg))

    def test_no_config_path_no_variable(self):
        cfg = fake_cfg(self.cfg.run_dir(), self.cfg.socket_path(), home=self.home, source=None)
        self.assertNotIn('LEKS_CONFIG', sandbox.clean_env(cfg))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='lk', dir='/tmp')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run = os.path.join(self.tmp, 'run')
        self.argv = [sys.executable, '-I', '-c', 'pass']

    def cfg(self, mode):
        return fake_cfg(self.run, os.path.join(self.run, 's.sock'), daemon={'sandbox': mode})

    def test_off(self):
        argv, note = sandbox.command(self.cfg('off'), self.argv)
        self.assertEqual(argv, self.argv)
        self.assertTrue(note)

    def test_auto_without_sandbox_says_why(self):
        with mock.patch.object(sandbox, 'available', return_value=(False, sandbox.NOTE_MISSING)):
            argv, note = sandbox.command(self.cfg('auto'), self.argv)
        self.assertEqual(argv, self.argv)
        self.assertEqual(note, 'sandbox-exec not available on this system')
        self.assertFalse(os.path.exists(self.run))

    def test_on_without_sandbox_refuses(self):
        with mock.patch.object(sandbox, 'available', return_value=(False, sandbox.NOTE_MISSING)):
            with self.assertRaises(sandbox.SandboxError):
                sandbox.command(self.cfg('on'), self.argv)

    def test_wrapped_when_available(self):
        for mode in ('auto', 'on'):
            with mock.patch.object(sandbox, 'available', return_value=(True, '')):
                argv, note = sandbox.command(self.cfg(mode), self.argv)
            self.assertEqual(argv[:2], ['/usr/bin/sandbox-exec', '-f'])
            self.assertEqual(argv[3:], self.argv)
            self.assertTrue(os.path.isfile(argv[2]))
            self.assertEqual(note, '')

    def test_available_elsewhere(self):
        with mock.patch.object(sandbox.sys, 'platform', 'linux'):
            self.assertEqual(sandbox.available(), (False, 'sandbox-exec not available on this system'))
        if DARWIN:
            self.assertEqual(sandbox.available(), (True, ''))


@unittest.skipUnless(DARWIN, 'needs macOS sandbox-exec')
class RealSandboxTests(unittest.TestCase):
    def setUp(self):
        self.home, self.cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        self.procs = []
        self.addCleanup(self._stop)
        os.makedirs(self.cfg.run_dir(), exist_ok=True)

    def _stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()

    def test_profile_closes_what_it_should(self):
        # a real listener outside the sandbox, so a refused connect can only come from the profile
        lst = socket.socket()
        lst.bind(('127.0.0.1', 0))
        lst.listen(4)
        self.addCleanup(lst.close)
        port = lst.getsockname()[1]
        outside = os.path.join(self.home, 'outside.txt')
        inside = os.path.join(self.cfg.run_dir(), 'inside.txt')
        argv, note = sandbox.command(self.cfg, [sys.executable, '-I', '-c', PROBE, str(port), self.cfg.socket_path(), outside, inside])
        self.assertEqual(note, '')
        out = subprocess.run(argv, env=sandbox.clean_env(self.cfg), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        res = json.loads(out.stdout)
        self.assertEqual(res['tcp'], 'PermissionError')
        self.assertEqual(res['bind'], 'ok')
        self.assertEqual(res['write_outside'], 'PermissionError')
        self.assertEqual(res['write_inside'], 'ok')
        self.assertEqual(res['exec'], 'PermissionError')
        self.assertFalse(os.path.exists(outside))
        self.assertTrue(os.path.exists(inside))

    def test_same_probe_without_the_profile_connects(self):
        # the control: outside the sandbox the same connect works
        lst = socket.socket()
        lst.bind(('127.0.0.1', 0))
        lst.listen(4)
        self.addCleanup(lst.close)
        out = subprocess.run([sys.executable, '-I', '-c', PROBE, str(lst.getsockname()[1]), self.cfg.socket_path(),
                              os.path.join(self.home, 'o.txt'), os.path.join(self.home, 'i.txt')],
                             capture_output=True, text=True, timeout=60)
        res = json.loads(out.stdout)
        self.assertEqual(res['tcp'], 'connected')
        self.assertEqual(res['write_outside'], 'ok')

    def test_daemon_runs_inside_the_sandbox(self):
        try:
            importlib.import_module('numpy')
        except ImportError:
            self.skipTest('numpy not installed')
        env = sandbox.clean_env(self.cfg)
        argv, note = sandbox.command(self.cfg, [sys.executable, '-c', BOOT])
        self.assertEqual(note, '')
        # the clean environment hides the user site, so numpy must be visible to it too
        probe = subprocess.run([sys.executable, '-c', 'import numpy'], env=env, capture_output=True)
        if probe.returncode != 0:
            self.skipTest('numpy is not visible without the user site')
        p = subprocess.Popen(argv, env=env, cwd='/', stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self.procs.append(p)
        sock = self.cfg.socket_path()
        wait_ready(p, sock, secs=30)
        v = decode(ask(sock, {'op': 'embed', 'texts': ['tile cache'], 'kind': 'q'}))
        self.assertEqual(v.shape, (1, 64))
        self.assertEqual(ask(sock, {'op': 'rerank', 'q': 'tile', 'docs': ['tile cache']})['scores'], [1.0])
        ask(sock, {'op': 'bye'})
        self.assertEqual(p.wait(timeout=15), 0)
        self.assertFalse(os.path.exists(sock))


if __name__ == '__main__':
    unittest.main()
