"""Daemon, backends and weights checks. All offline, models are the fake backend."""
from __future__ import annotations

import array
import base64
import dataclasses
import json
import os
import socket
import stat
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from leks import backends, models, modeld
from tests import helpers

try:
    import numpy as np
    from leks.backends import fake_backend
except ImportError:           # the fake backend needs numpy
    np = None
    fake_backend = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOT = "import sys; sys.path.insert(0, %r); from leks import modeld; modeld.main()" % ROOT

needs_numpy = unittest.skipIf(np is None, 'numpy not installed')


def line(obj):
    return json.dumps(obj).encode() + b'\n'


def ask(path, payload, timeout=10.0):
    """One request over the socket, the way a client does it. payload: dict or raw bytes."""
    data = line(payload) if isinstance(payload, dict) else payload
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall(data)
        s.shutdown(socket.SHUT_WR)
        # one reply is one line, so stop at the newline like the real clients do.
        # reading on to EOF breaks on linux: the busy answer closes before reading our request,
        # and linux then resets the socket instead of a clean EOF
        buf = b''
        while b'\n' not in buf:
            b = s.recv(65536)
            if not b:
                break
            buf += b
    finally:
        s.close()
    return json.loads(buf.split(b'\n', 1)[0])


def set_daemon(home, **kw):
    """Rewrite the daemon section of the config file in home and reload it."""
    from leks import config
    p = os.path.join(home, 'config.json')
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    d['daemon'].update(kw)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(d, f)
    return config.load(p)


def start_daemon(cfg):
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LEKS_CONFIG': cfg.source_path,
           'LEKS_HOME': cfg.home, 'LEKS_ALLOW_FAKE': '1'}
    # no -I here: numpy may live in the user site, which isolated mode hides
    return subprocess.Popen([sys.executable, '-c', BOOT], env=env, cwd='/', stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(proc, path, secs=15.0):
    end = time.time() + secs
    while time.time() < end:
        if proc.poll() is not None:
            raise AssertionError('daemon exited early with %s' % proc.returncode)
        try:
            if ask(path, {'op': 'ping'}, timeout=2).get('shape') == [0, 0]:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError('daemon did not answer in time')


def decode(reply):
    n, d = reply['shape']
    return np.frombuffer(base64.b64decode(reply['b64']), dtype='<f4').reshape(n, d)


class Base(unittest.TestCase):
    models_on = True

    def setUp(self):
        self.home, self.cfg = helpers.make_home(models=self.models_on)
        self.procs = []
        self.addCleanup(helpers.cleanup, self.home)
        self.addCleanup(self._stop_all)

    def _stop_all(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()

    def daemon(self, cfg=None):
        cfg = cfg or self.cfg
        os.makedirs(cfg.run_dir(), exist_ok=True)
        p = start_daemon(cfg)
        self.procs.append(p)
        return p


@needs_numpy
class ReplyTests(Base):
    """reply_for and handle in this process, no socket."""

    def setUp(self):
        super().setUp()
        modeld.configure(self.cfg)
        self.addCleanup(modeld.configure, None)

    def r(self, req):
        return modeld.reply_for(line(req))

    def test_ping_and_bye(self):
        self.assertEqual(self.r({'op': 'ping'}), {'shape': [0, 0], 'b64': ''})
        self.assertEqual(self.r({'op': 'bye'}), {'shape': [0, 0], 'b64': ''})
        self.assertTrue(modeld._leaving)

    def test_embed_matches_backend(self):
        out = self.r({'op': 'embed', 'texts': ['tile cache', 'frame graph'], 'kind': 'q'})
        v = decode(out)
        self.assertEqual(out['shape'], [2, 64])
        want = fake_backend.Enc(None, self.cfg.embedder)(['tile cache', 'frame graph'], 'q')
        self.assertTrue(np.array_equal(v, want))

    def test_embed_with_model_key_and_default_kind(self):
        key = self.cfg.embedder.key
        a = self.r({'op': 'embed', 'model': key, 'texts': ['x y']})
        b = self.r({'op': 'embed', 'texts': ['x y'], 'kind': 'd'})
        self.assertEqual(a, b)

    def test_rerank_scores(self):
        out = self.r({'op': 'rerank', 'q': 'tile cache', 'docs': ['tile cache eviction', '', 'tile']})
        self.assertEqual(len(out['scores']), 3)
        self.assertEqual(out['scores'][0], 2.0)
        self.assertEqual(out['scores'][1], 0.0)
        self.assertTrue(all(isinstance(x, float) for x in out['scores']))

    def test_rerank_empty_docs(self):
        self.assertEqual(self.r({'op': 'rerank', 'q': 'a', 'docs': []}), {'scores': []})

    def test_error_bad_request(self):
        self.assertEqual(modeld.reply_for(b'not json\n'), {'error': 'bad_request'})
        self.assertEqual(modeld.reply_for(b'[1, 2]\n'), {'error': 'bad_request'})
        self.assertEqual(modeld.reply_for(b'{"op": "ping"}\n{"op": "ping"}\n'), {'error': 'bad_request'})
        self.assertEqual(modeld.reply_for(b'[' * 100000 + b'\n'), {'error': 'bad_request'})

    def test_error_too_big(self):
        self.assertEqual(modeld.reply_for(b'x' * (modeld.MAX_LINE + 1) + b'\n'), {'error': 'too_big'})
        self.assertEqual(modeld.reply_for(b'{"op": "ping"}'), {'error': 'too_big'})
        self.assertEqual(modeld.reply_for(b''), {'error': 'too_big'})

    def test_error_unknown_op(self):
        self.assertEqual(self.r({'op': 'shell'}), {'error': 'unknown_op'})
        self.assertEqual(self.r({}), {'error': 'unknown_op'})
        self.assertEqual(self.r({'op': 'shell', 'model': 5}), {'error': 'unknown_op'})

    def test_error_bad_input(self):
        k = 'embed'
        bad = [
            {'op': k},
            {'op': k, 'texts': []},
            {'op': k, 'texts': 'abc'},
            {'op': k, 'texts': [1]},
            {'op': k, 'texts': ['a'] * 65},
            {'op': k, 'texts': ['a' * 8001]},
            {'op': k, 'texts': ['a'], 'kind': 'x'},
            {'op': 'rerank', 'docs': ['a']},
            {'op': 'rerank', 'q': 5, 'docs': ['a']},
            {'op': 'rerank', 'q': 'a' * 2001, 'docs': ['a']},
            {'op': 'rerank', 'q': 'a', 'docs': 'a'},
            {'op': 'rerank', 'q': 'a', 'docs': ['a'] * 65},
            {'op': 'rerank', 'q': 'a', 'docs': ['a' * 8001]},
        ]
        for req in bad:
            self.assertEqual(self.r(req), {'error': 'bad_input'}, req)

    def test_limits_are_inclusive(self):
        self.assertEqual(self.r({'op': 'embed', 'texts': ['a' * 8000] * 64})['shape'], [64, 64])
        self.assertIn('scores', self.r({'op': 'rerank', 'q': 'a' * 2000, 'docs': ['a'] * 64}))

    def test_error_model_refused(self):
        self.assertEqual(self.r({'op': 'embed', 'model': 'other', 'texts': ['a']}), {'error': 'model_refused'})
        self.assertEqual(self.r({'op': 'embed', 'model': 7, 'texts': ['a']}), {'error': 'model_refused'})
        # the reranker key is not an embedder
        rk = self.cfg.reranker.key
        self.assertEqual(self.r({'op': 'embed', 'model': rk, 'texts': ['a']}), {'error': 'model_refused'})
        ek = self.cfg.embedder.key
        self.assertEqual(self.r({'op': 'rerank', 'model': ek, 'q': 'a', 'docs': ['a']}), {'error': 'model_refused'})
        # a role with no model configured
        modeld.configure(dataclasses.replace(self.cfg, reranker=None))
        self.assertEqual(self.r({'op': 'rerank', 'q': 'a', 'docs': ['a']}), {'error': 'model_refused'})

    def test_error_internal(self):
        with mock.patch.object(fake_backend.Enc, '__call__', side_effect=RuntimeError('boom text')):
            out = self.r({'op': 'embed', 'texts': ['a']})
        self.assertEqual(out, {'error': 'internal'})
        self.assertNotIn('boom', json.dumps(out))

    def test_unconfigured_daemon_is_internal(self):
        modeld.configure(None)
        self.assertEqual(self.r({'op': 'embed', 'texts': ['a']}), {'error': 'internal'})

    def test_slot_swaps_and_releases(self):
        self.r({'op': 'embed', 'texts': ['a']})
        first = modeld._slots['embedder'].obj
        self.r({'op': 'embed', 'texts': ['b']})
        self.assertIs(modeld._slots['embedder'].obj, first)
        alt = dataclasses.replace(self.cfg.embedder, key='alt')
        modeld.configure(dataclasses.replace(self.cfg, embedder=alt))
        self.assertEqual(self.r({'op': 'embed', 'model': 'alt', 'texts': ['a']})['shape'], [1, 64])
        self.assertEqual(modeld._slots['embedder'].key, 'alt')
        # swap a loaded slot to another key: the old one is released first
        alt2 = dataclasses.replace(self.cfg.embedder, key='alt2')
        modeld._cfg = dataclasses.replace(self.cfg, embedder=alt2)
        with mock.patch.object(fake_backend, 'release') as rel:
            self.r({'op': 'embed', 'texts': ['a']})
            rel.assert_called_once()
        self.assertEqual(modeld._slots['embedder'].key, 'alt2')


class WeightsErrorTests(Base):
    """model_missing and model_check, with the torch name configured but no real torch needed."""

    def setUp(self):
        super().setUp()
        self.cfg.backend = 'torch'
        modeld.configure(self.cfg)
        self.addCleanup(modeld.configure, None)
        self.dir = os.path.join(self.cfg.weights_dir, self.cfg.embedder.key)

    def embed(self):
        return modeld.reply_for(line({'op': 'embed', 'texts': ['a']}))

    def test_model_missing(self):
        self.assertEqual(self.embed(), {'error': 'model_missing'})

    def test_model_check_on_changed_file(self):
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, 'config.json'), 'w') as f:
            f.write('{"a": 1}')
        models.write_manifest(self.dir)
        with open(os.path.join(self.dir, 'config.json'), 'w') as f:
            f.write('{"a": 2}')
        self.assertEqual(self.embed(), {'error': 'model_check'})

    def test_model_check_when_backend_cannot_load(self):
        # good manifest, but the folder is no real model: the backend refuses (or torch is absent)
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, 'config.json'), 'w') as f:
            f.write('{}')
        models.write_manifest(self.dir)
        self.assertEqual(self.embed(), {'error': 'model_check'})
        self.assertEqual(modeld._slots['embedder'].obj, None)


@needs_numpy
@helpers.needs_unix_sockets
class DaemonTests(Base):
    """A real daemon process on a short socket."""

    def up(self, cfg=None):
        cfg = cfg or self.cfg
        p = self.daemon(cfg)
        wait_ready(p, cfg.socket_path())
        return p, cfg.socket_path()

    def test_ping_embed_rerank_bye(self):
        p, sock = self.up()
        self.assertEqual(ask(sock, {'op': 'ping'}), {'shape': [0, 0], 'b64': ''})
        out = ask(sock, {'op': 'embed', 'model': self.cfg.embedder.key, 'texts': ['render pass', 'build cache'], 'kind': 'd'})
        v = decode(out)
        self.assertEqual(v.shape, (2, 64))
        self.assertTrue(np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5))
        sc = ask(sock, {'op': 'rerank', 'q': 'render pass', 'docs': ['render pass order', 'shader blobs']})
        self.assertEqual(list(sc), ['scores'])
        self.assertEqual(sc['scores'][0], 2.0)
        self.assertEqual(ask(sock, {'op': 'bye'}), {'shape': [0, 0], 'b64': ''})
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertFalse(os.path.exists(sock))

    def test_listening_frees_the_start_lock(self):
        # a quick bulk run can end the daemon before the start's follow-up looks: the lock must not outlive it
        lock = os.path.join(self.cfg.run_dir(), 'start.lock')
        os.makedirs(lock)
        self.up()
        self.assertFalse(os.path.exists(lock))

    def test_socket_mode_is_private(self):
        _, sock = self.up()
        self.assertEqual(stat.S_IMODE(os.lstat(sock).st_mode), 0o600)

    def test_error_codes_over_the_socket(self):
        _, sock = self.up()
        self.assertEqual(ask(sock, b'nope\n'), {'error': 'bad_request'})
        self.assertEqual(ask(sock, b'{"op": "ping"}'), {'error': 'too_big'})
        self.assertEqual(ask(sock, {'op': 'what'}), {'error': 'unknown_op'})
        self.assertEqual(ask(sock, {'op': 'embed', 'texts': []}), {'error': 'bad_input'})
        self.assertEqual(ask(sock, {'op': 'embed', 'model': 'zzz', 'texts': ['a']}), {'error': 'model_refused'})
        # nothing but the fixed set ever comes back
        for req in ({'op': 'embed'}, {'op': 'rerank'}, {'op': 'x'}):
            self.assertTrue(set(ask(sock, req)) <= {'error'})

    def test_descriptor_message_is_refused(self):
        _, sock = self.up()
        r, w = os.pipe()
        try:
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(10)
            s.connect(sock)
            s.sendmsg([b'{"op": "ping"}\n'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [r]).tobytes())])
            buf = b''
            while True:
                b = s.recv(4096)
                if not b:
                    break
                buf += b
            s.close()
        finally:
            os.close(r)
            os.close(w)
        self.assertEqual(json.loads(buf), {'error': 'bad_request'})
        self.assertEqual(ask(sock, {'op': 'ping'})['shape'], [0, 0])

    def test_busy_beyond_32_connections(self):
        _, sock = self.up()
        held = []
        try:
            for _ in range(modeld.MAX_CONN):
                for _ in range(50):
                    s = socket.socket(socket.AF_UNIX)
                    s.settimeout(10)
                    try:
                        s.connect(sock)
                        break
                    except OSError:         # accept queue full for a moment
                        s.close()
                        time.sleep(0.02)
                else:
                    self.fail('could not open all connections')
                held.append(s)
                time.sleep(0.01)
            time.sleep(0.8)         # let the accept loop take all of them
            self.assertEqual(ask(sock, {'op': 'ping'}), {'error': 'busy'})
        finally:
            for s in held:
                s.close()
        time.sleep(0.5)
        self.assertEqual(ask(sock, {'op': 'ping'})['shape'], [0, 0])

    def test_idle_exit(self):
        cfg = set_daemon(self.home, idle_exit=2)
        p, sock = self.up(cfg)
        self.assertEqual(p.wait(timeout=15), 0)
        self.assertFalse(os.path.exists(sock))

    def test_pings_do_not_keep_it_alive(self):
        cfg = set_daemon(self.home, idle_exit=2)
        p, sock = self.up(cfg)
        end = time.time() + 6
        while p.poll() is None and time.time() < end:
            try:
                ask(sock, {'op': 'ping'}, timeout=1)
            except OSError:
                pass
            time.sleep(0.2)
        self.assertEqual(p.poll(), 0)

    def test_requests_keep_it_alive(self):
        cfg = set_daemon(self.home, idle_exit=2)
        p, sock = self.up(cfg)
        for _ in range(4):
            time.sleep(0.8)
            self.assertEqual(ask(sock, {'op': 'embed', 'texts': ['a']})['shape'], [1, 64])
        self.assertIsNone(p.poll())

    def test_second_daemon_exits_quietly(self):
        p1, sock = self.up()
        p2 = self.daemon()
        self.assertEqual(p2.wait(timeout=15), 0)
        self.assertIsNone(p1.poll())
        self.assertEqual(ask(sock, {'op': 'ping'})['shape'], [0, 0])

    def test_stale_socket_file_is_replaced(self):
        os.makedirs(self.cfg.run_dir(), exist_ok=True)
        sock = self.cfg.socket_path()
        s = socket.socket(socket.AF_UNIX)
        s.bind(sock)
        s.close()                   # the file stays, nobody listens
        self.assertTrue(os.path.exists(sock))
        p, _ = self.up()
        self.assertEqual(ask(sock, {'op': 'ping'})['shape'], [0, 0])

    def test_other_file_at_the_path_stops_the_daemon(self):
        os.makedirs(self.cfg.run_dir(), exist_ok=True)
        with open(self.cfg.socket_path(), 'w') as f:
            f.write('not a socket')
        p = self.daemon()
        self.assertEqual(p.wait(timeout=15), 1)
        self.assertTrue(os.path.exists(self.cfg.socket_path()))

    def test_socket_file_of_a_later_owner_stays(self):
        cfg = set_daemon(self.home, idle_exit=2)
        p, sock = self.up(cfg)
        os.remove(sock)
        other = socket.socket(socket.AF_UNIX)
        other.bind(sock)
        other.listen(1)
        try:
            self.assertEqual(p.wait(timeout=15), 0)
            self.assertTrue(os.path.exists(sock))
        finally:
            other.close()


@needs_numpy
class FakeBackendTests(unittest.TestCase):
    def setUp(self):
        self.home, self.cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        self.enc = fake_backend.Enc(None, self.cfg.embedder, 'cpu')
        self.max = fake_backend.MaxSim(None, self.cfg.reranker, 'cpu')

    def test_deterministic(self):
        a = self.enc(['render pass order', 'tile cache'], 'd')
        b = self.enc(['render pass order', 'tile cache'], 'd')
        self.assertTrue(np.array_equal(a, b))
        self.assertEqual(a.dtype, np.float32)
        self.assertEqual(a.shape, (2, 64))

    def test_rows_are_unit(self):
        v = self.enc(['one', 'two words here', 'x' * 3000, 'Mixed CASE, punctuation! 42'], 'q')
        self.assertTrue(np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-6))

    def test_empty_text_is_e0(self):
        v = self.enc(['', '   ', '...'], 'd')
        e0 = np.zeros(64, dtype=np.float32)
        e0[0] = 1.0
        for row in v:
            self.assertTrue(np.array_equal(row, e0))

    def test_same_words_same_vector_any_case_and_kind(self):
        a = self.enc(['Tile Cache'], 'q')
        b = self.enc(['tile, cache!'], 'd')
        self.assertTrue(np.array_equal(a, b))

    def test_different_text_differs(self):
        a, b = self.enc(['shader blobs bundle'], 'd'), self.enc(['frame graph resolve pass'], 'd')
        self.assertFalse(np.array_equal(a, b))

    def test_related_texts_are_closer(self):
        q = self.enc(['tile cache eviction policy'], 'q')[0]
        near = self.enc(['the tile cache eviction policy keeps the oldest'], 'd')[0]
        far = self.enc(['release checklist for the documentation site'], 'd')[0]
        self.assertGreater(float(q @ near), float(q @ far))

    def test_hash_scheme_by_hand(self):
        import hashlib
        h = hashlib.sha1(b'cache').hexdigest()
        idx = int(h[1:9], 16) % 64
        sign = 1.0 if int(h[0], 16) & 1 else -1.0
        v = self.enc(['cache'], 'd')[0]
        self.assertAlmostEqual(float(v[idx]), sign, places=6)
        self.assertAlmostEqual(float(np.abs(v).sum()), 1.0, places=6)

    def test_bad_kind(self):
        with self.assertRaises(ValueError):
            self.enc(['a'], 'x')

    def test_maxsim_scores(self):
        s = self.max.rerank('tile cache', ['tile cache eviction', 'tile', '', 'cache tile cache'])
        self.assertEqual(s[0], 2.0)
        self.assertEqual(s[2], 0.0)
        self.assertEqual(s[3], 2.0)
        self.assertGreaterEqual(s[1], 0.0)
        self.assertLessEqual(s[1], 2.0)
        self.assertEqual(self.max.rerank('', ['anything']), [0.0])
        self.assertEqual(self.max.rerank('a b c', ['a b c']), [3.0])
        self.assertEqual(self.max.rerank('a b c', []), [])

    def test_release_is_harmless(self):
        fake_backend.release()


class BackendLoaderTests(unittest.TestCase):
    def test_fake_needs_the_switch(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LEKS_ALLOW_FAKE', None)
            with self.assertRaises(PermissionError):
                backends.load_backend('fake')
            os.environ['LEKS_ALLOW_FAKE'] = '0'
            with self.assertRaises(PermissionError):
                backends.load_backend('fake')

    @needs_numpy
    def test_fake_loads_with_the_switch(self):
        with mock.patch.dict(os.environ, {'LEKS_ALLOW_FAKE': '1'}):
            m = backends.load_backend('fake')
        self.assertTrue(hasattr(m, 'Enc') and hasattr(m, 'MaxSim') and hasattr(m, 'release'))

    def test_unknown_name(self):
        with self.assertRaises(ValueError):
            backends.load_backend('onnx')

    def test_package_import_pulls_no_heavy_modules(self):
        code = ("import sys; sys.path.insert(0, %r); import leks.backends, leks.models, leks.modeld, leks.sandbox; "
                "bad = [m for m in ('torch', 'numpy', 'transformers', 'huggingface_hub', 'safetensors') if m in sys.modules]; "
                "print(bad)" % ROOT)
        out = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), '[]', out.stderr)

    def test_pick_device(self):
        self.assertEqual(backends.pick_device('auto', True, True), 'mps')
        self.assertEqual(backends.pick_device('auto', False, True), 'cuda')
        self.assertEqual(backends.pick_device('auto', False, False), 'cpu')
        self.assertEqual(backends.pick_device('cpu', True, True), 'cpu')
        with self.assertRaises(RuntimeError):
            backends.pick_device('mps', False, True)
        with self.assertRaises(RuntimeError):
            backends.pick_device('cuda', True, False)
        with self.assertRaises(ValueError):
            backends.pick_device('tpu', True, True)

    def test_batch_plan(self):
        lengths = [10, 500, 30, 20, 500, 5]
        got = list(backends.batch_plan(lengths, max_tokens=1100, max_texts=32))
        flat = [i for b in got for i in b]
        self.assertEqual(sorted(flat), list(range(6)))
        self.assertEqual(got[0], [1, 4])           # the longest go first
        for b in got:
            self.assertLessEqual(max(lengths[i] for i in b) * len(b), 1100)
        self.assertEqual([len(b) for b in backends.batch_plan([100] * 5, 250, 32)], [2, 2, 1])
        self.assertEqual([len(b) for b in backends.batch_plan([1] * 70, 16384, 32)], [32, 32, 6])
        self.assertEqual(list(backends.batch_plan([], 100, 4)), [])
        # one text above the budget still gets a batch of its own
        self.assertEqual(list(backends.batch_plan([900, 5], 500, 32)), [[0], [1]])


class ModelsTests(unittest.TestCase):
    def setUp(self):
        self.home, self.cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        self.key = self.cfg.embedder.key
        self.dir = os.path.join(self.cfg.weights_dir, self.key)
        os.makedirs(os.path.join(self.dir, 'sub'))
        self.put('config.json', '{"a": 1}')
        self.put('model.safetensors', 'weights' * 20)
        self.put('sub/tokenizer.json', '{"t": 2}')

    def put(self, rel, text):
        with open(os.path.join(self.dir, rel), 'w') as f:
            f.write(text)

    def test_manifest_round_trip(self):
        m = models.write_manifest(self.dir)
        self.assertEqual(sorted(m), ['config.json', 'model.safetensors', 'sub/tokenizer.json'])
        self.assertEqual(m['config.json']['size'], 8)
        self.assertEqual(len(m['config.json']['sha256']), 64)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, 'manifest.json')))
        self.assertEqual(models.check(self.cfg, self.key), self.dir)
        # writing it again does not list the manifest itself
        self.assertEqual(sorted(models.write_manifest(self.dir)), sorted(m))

    def test_present_and_missing(self):
        self.assertFalse(models.weights_present(self.cfg, self.key))
        with self.assertRaises(models.ModelMissing):
            models.check(self.cfg, self.key)
        models.write_manifest(self.dir)
        self.assertTrue(models.weights_present(self.cfg, self.key))
        self.assertFalse(models.weights_present(self.cfg, 'nothing_here'))
        self.assertFalse(models.weights_present(self.cfg, '../escape'))
        with self.assertRaises(models.ModelMissing):
            models.check(self.cfg, '../escape')

    def test_changed_content(self):
        models.write_manifest(self.dir)
        self.put('config.json', '{"a": 2}')
        with self.assertRaisesRegex(models.ModelCheck, 'hash'):
            models.check(self.cfg, self.key)

    def test_changed_size(self):
        models.write_manifest(self.dir)
        self.put('model.safetensors', 'short')
        with self.assertRaisesRegex(models.ModelCheck, 'size'):
            models.check(self.cfg, self.key)

    def test_extra_and_missing_files(self):
        models.write_manifest(self.dir)
        self.put('extra.bin', 'x')
        with self.assertRaisesRegex(models.ModelCheck, 'file set'):
            models.check(self.cfg, self.key)
        os.remove(os.path.join(self.dir, 'extra.bin'))
        os.remove(os.path.join(self.dir, 'sub', 'tokenizer.json'))
        with self.assertRaisesRegex(models.ModelCheck, 'file set'):
            models.check(self.cfg, self.key)

    def test_symlink_is_refused(self):
        models.write_manifest(self.dir)
        os.remove(os.path.join(self.dir, 'config.json'))
        os.symlink(os.path.join(self.dir, 'model.safetensors'), os.path.join(self.dir, 'config.json'))
        with self.assertRaises(models.ModelCheck):
            models.check(self.cfg, self.key)

    def test_big_files_are_checked_by_size_only(self):
        models.write_manifest(self.dir)
        with mock.patch.object(models, 'SMALL', 10):
            self.put('model.safetensors', 'WEIGHTS' * 20)       # same size, other bytes
            models.check(self.cfg, self.key)
            self.put('config.json', '{"a": 9}')                  # 8 bytes, under the cut: hashed
            with self.assertRaisesRegex(models.ModelCheck, 'hash'):
                models.check(self.cfg, self.key)

    def test_bad_manifests(self):
        p = os.path.join(self.dir, 'manifest.json')
        for text in ('not json', '[]', '{}', '{"../x": {"size": 1, "sha256": "%s"}}' % ('a' * 64),
                     '{"a": {"size": "1", "sha256": "%s"}}' % ('a' * 64), '{"a": {"size": 1, "sha256": "zz"}}'):
            with open(p, 'w') as f:
                f.write(text)
            with self.assertRaises(models.ModelCheck, msg=text):
                models.check(self.cfg, self.key)

    def test_fetch_keeps_the_hub_cache_under_leks_home(self):
        # a stand-in hub client, nothing is downloaded
        seen = []

        def snapshot_download(repo_id, revision, local_dir, ignore_patterns):
            seen.append(os.environ.get('HF_HOME'))
            for rel in ('config.json', 'model.safetensors'):
                with open(os.path.join(local_dir, rel), 'w') as f:
                    f.write('x')

        hub = SimpleNamespace(snapshot_download=snapshot_download)
        with mock.patch.dict(sys.modules, {'huggingface_hub': hub}), mock.patch.dict(os.environ):
            os.environ.pop('HF_HOME', None)
            models.fetch(self.cfg, ('embedder',))
            os.environ['HF_HOME'] = '/srv/hf-cache'
            models.fetch(self.cfg, ('embedder',))
        self.assertEqual(seen, [os.path.join(self.cfg.home, 'models', 'hf-home'), '/srv/hf-cache'])

    def test_check_all(self):
        models.write_manifest(self.dir)
        out = models.check_all(self.cfg)
        self.assertTrue(out[self.key]['ok'])
        rk = self.cfg.reranker.key
        self.assertFalse(out[rk]['ok'])
        self.assertTrue(out[rk]['why'])
        self.assertEqual(sorted(out), sorted([self.key, rk]))

    def test_projection_file(self):
        base = os.path.join(self.dir, 'proj')
        os.makedirs(os.path.join(base, '1_Dense'))
        os.makedirs(os.path.join(base, '2_Dense'))
        with open(os.path.join(base, '1_Dense', 'w.safetensors'), 'w') as f:
            f.write('x')
        with open(os.path.join(base, '2_Dense', 'a.safetensors'), 'w') as f:
            f.write('x')
        with open(os.path.join(base, '2_Dense', 'b.safetensors'), 'w') as f:
            f.write('x')
        self.assertTrue(models.projection_file(base, '1_Dense').endswith('w.safetensors'))
        with self.assertRaisesRegex(models.ModelCheck, 'exactly 1'):
            models.projection_file(base, '2_Dense')
        with self.assertRaisesRegex(models.ModelCheck, 'missing'):
            models.projection_file(base, '3_Dense')
        with self.assertRaises(models.ModelCheck):
            models.projection_file(base, '../1_Dense')


class FetchTests(unittest.TestCase):
    """fetch with a stand-in for the hub client, so nothing touches the network."""

    def setUp(self):
        self.home, self.cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        proj = ({'src': '1_Dense', 'in': 8, 'out': 16, 'residual': True},
                {'src': '2_Dense', 'in': 16, 'out': 16, 'residual': False})
        self.cfg.reranker = dataclasses.replace(self.cfg.reranker, projection=proj)
        self.calls = []

    def fake_hub(self, layout):
        calls = self.calls

        def snapshot_download(repo_id, revision, local_dir, ignore_patterns=None):
            calls.append((repo_id, revision, local_dir))
            for rel in layout:
                p = os.path.join(local_dir, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, 'w') as f:
                    f.write('data of ' + rel)
            os.makedirs(os.path.join(local_dir, '.cache', 'hub'), exist_ok=True)
            with open(os.path.join(local_dir, '.cache', 'hub', 'meta'), 'w') as f:
                f.write('bookkeeping')
            return local_dir

        mod = mock.MagicMock()
        mod.snapshot_download = snapshot_download
        return mock.patch.dict(sys.modules, {'huggingface_hub': mod})

    GOOD = ['config.json', 'model.safetensors', 'tokenizer.json', '1_Dense/dense_1.safetensors', '2_Dense/dense_2.safetensors']

    def test_fetch_writes_manifest_and_drops_bookkeeping(self):
        with self.fake_hub(self.GOOD):
            out = models.fetch(self.cfg)
        self.assertEqual(sorted(out), sorted([self.cfg.embedder.key, self.cfg.reranker.key]))
        self.assertEqual(self.calls[0][:2], (self.cfg.embedder.hf_id, self.cfg.embedder.revision))
        self.assertEqual(self.calls[1][:2], (self.cfg.reranker.hf_id, self.cfg.reranker.revision))
        d = os.path.join(self.cfg.weights_dir, self.cfg.reranker.key)
        self.assertFalse(os.path.exists(os.path.join(d, '.cache')))
        self.assertEqual(models.check(self.cfg, self.cfg.reranker.key), d)
        self.assertEqual(out[self.cfg.reranker.key]['files'], len(self.GOOD))

    def test_fetch_one_role(self):
        with self.fake_hub(self.GOOD):
            out = models.fetch(self.cfg, which=('embedder',))
        self.assertEqual(list(out), [self.cfg.embedder.key])
        self.assertEqual(len(self.calls), 1)

    def test_layout_mismatch_fails_clearly(self):
        layout = [x for x in self.GOOD if not x.startswith('2_Dense')]
        with self.fake_hub(layout):
            with self.assertRaises(models.ModelCheck) as cm:
                models.fetch(self.cfg, which=('reranker',))
        msg = str(cm.exception)
        self.assertIn('2_Dense', msg)
        self.assertIn('layout', msg)
        d = os.path.join(self.cfg.weights_dir, self.cfg.reranker.key)
        self.assertFalse(os.path.exists(os.path.join(d, 'manifest.json')))

    def test_two_weight_files_in_a_projection_folder(self):
        with self.fake_hub(self.GOOD + ['1_Dense/other.safetensors']):
            with self.assertRaisesRegex(models.ModelCheck, 'exactly 1'):
                models.fetch(self.cfg, which=('reranker',))

    def test_no_top_level_weights(self):
        with self.fake_hub(['config.json']):
            with self.assertRaisesRegex(models.ModelCheck, 'safetensors'):
                models.fetch(self.cfg, which=('embedder',))

    def test_no_hub_client(self):
        with mock.patch.dict(sys.modules, {'huggingface_hub': None}):
            with self.assertRaisesRegex(models.ModelCheck, 'huggingface_hub'):
                models.fetch(self.cfg)

    def test_unknown_role(self):
        with self.fake_hub(self.GOOD):
            with self.assertRaises(ValueError):
                models.fetch(self.cfg, which=('judge',))


if __name__ == '__main__':
    unittest.main()
