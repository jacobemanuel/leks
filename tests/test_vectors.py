"""Tests for the daemon client, the vector files and the embed run. Offline only."""
from __future__ import annotations

import array
import base64
import contextlib
import hashlib
import io
import json
import os
import select
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest import mock

from leks import embed_all, modeld_client, vecstore
from leks.modeld_client import Client, check_reply
from tests import helpers

try:
    import numpy as np
except ImportError:      # numpy is optional, the tests that need it skip
    np = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

needs_numpy = unittest.skipUnless(np is not None, 'numpy not installed')


def get_schema():
    """The real schema module when it exists, else a stand-in with the same two functions."""
    try:
        from leks import schema
        return schema
    except ImportError:
        import leks
        stub = types.ModuleType('leks.schema')
        stub.EMBED_CUT = 1200

        def embed_input(title, heading, text):
            return ((title or '') + '|' + (heading or '') + '\n' + (text or ''))[:1200]

        def ehash(s):
            return hashlib.sha256(s.encode('utf-8')).hexdigest()[:32]

        stub.embed_input, stub.ehash = embed_input, ehash
        sys.modules['leks.schema'] = stub
        leks.schema = stub
        return stub


def spec(key='emb', op='embed', dim=8, normalize=True):
    return types.SimpleNamespace(key=key, op=op, dim=dim, normalize=normalize)


def line(obj):
    return json.dumps(obj).encode() + b'\n'


def unit_rows(n, dim, seed=1):
    rng = np.random.RandomState(seed)
    v = rng.randn(n, dim).astype('float32')
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def embed_obj(v):
    return {'shape': list(v.shape), 'b64': base64.b64encode(v.astype('<f4').tobytes()).decode()}


class TinyServer:
    """A one-request-per-connection socket server living in a test thread."""

    def __init__(self, handler, path=None):
        self.dir = helpers.short_tmpdir()
        self.path = path or os.path.join(self.dir, 's.sock')
        self.handler = handler
        self.accepted = 0
        self.stop = False
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(self.path)
        self.srv.listen(8)
        self.srv.settimeout(0.2)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop:
            try:
                conn, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.accepted += 1
            try:
                conn.settimeout(5)
                buf = b''
                while not buf.endswith(b'\n'):
                    b = conn.recv(65536)
                    if not b:
                        break
                    buf += b
                out = self.handler(conn, json.loads(buf) if buf else None)
                if out is not None:
                    conn.sendall(out)
            except Exception:
                pass
            finally:
                conn.close()

    def close(self):
        self.stop = True
        self.srv.close()
        self.thread.join(2)
        shutil.rmtree(self.dir, ignore_errors=True)


def good_handler(conn, req):
    op = req['op']
    if op in ('ping', 'bye'):
        return line({'shape': [0, 0], 'b64': ''})
    if op == 'embed':
        return line(embed_obj(unit_rows(len(req['texts']), 8)))
    return line({'scores': [float(i) for i in range(len(req['docs']))]})


SPECS = {'emb': spec(), 'rr': spec('rr', 'maxsim', 4)}
BUDGETS = {'embed': 3.0, 'rerank': 3.0, 'connect': 0.4, 'ping': 1.0}


class ClientBase(unittest.TestCase):
    def serve(self, handler=good_handler, path=None):
        s = TinyServer(handler, path)
        self.addCleanup(s.close)
        return s

    def client(self, server, **kw):
        return Client(server.path, dict(BUDGETS, **kw), SPECS)


@needs_numpy
class ReplyChecks(unittest.TestCase):
    REQ = {'op': 'embed', 'model': 'emb', 'texts': ['a', 'b'], 'kind': 'q'}

    def good(self):
        return embed_obj(unit_rows(2, 8))

    def test_good_reply(self):
        v = check_reply(self.REQ, self.good(), SPECS)
        self.assertEqual(v.shape, (2, 8))
        self.assertEqual(str(v.dtype), 'float32')

    def test_ping_and_bye(self):
        for op in ('ping', 'bye'):
            self.assertIs(check_reply({'op': op}, {'shape': [0, 0], 'b64': ''}, SPECS), True)
            self.assertIsNone(check_reply({'op': op}, {'shape': [0, 0], 'b64': 'AAAA'}, SPECS))
            self.assertIsNone(check_reply({'op': op}, {'scores': []}, SPECS))

    def test_wrong_shape(self):
        for shape in ([3, 8], [2, 7], [2], [2, 8, 1], [2.0, 8], [True, 8], 'x'):
            r = self.good()
            r['shape'] = shape
            self.assertIsNone(check_reply(self.REQ, r, SPECS), shape)

    def test_wrong_byte_count(self):
        r = self.good()
        r['b64'] = base64.b64encode(b'\0' * (2 * 8 * 4 - 4)).decode()
        self.assertIsNone(check_reply(self.REQ, r, SPECS))
        r['b64'] = base64.b64encode(b'\0' * (2 * 8 * 4 + 4)).decode()
        self.assertIsNone(check_reply(self.REQ, r, SPECS))

    def test_bad_base64(self):
        r = self.good()
        r['b64'] = '!' * len(r['b64'])
        self.assertIsNone(check_reply(self.REQ, r, SPECS))
        r['b64'] = 5
        self.assertIsNone(check_reply(self.REQ, r, SPECS))

    def test_not_finite(self):
        for bad in (float('nan'), float('inf'), -float('inf')):
            v = unit_rows(2, 8)
            v[1, 3] = bad
            self.assertIsNone(check_reply(self.REQ, embed_obj(v), SPECS), bad)

    def test_too_big_value(self):
        sp = {'emb': spec(normalize=False)}
        v = np.full((2, 8), 2e4, dtype='float32')
        self.assertIsNone(check_reply(self.REQ, embed_obj(v), sp))
        v = np.full((2, 8), 5.0, dtype='float32')
        self.assertEqual(check_reply(self.REQ, embed_obj(v), sp).shape, (2, 8))

    def test_non_unit_rows(self):
        v = unit_rows(2, 8) * 1.5
        self.assertIsNone(check_reply(self.REQ, embed_obj(v), SPECS))
        v = unit_rows(2, 8)
        v[0] *= 1.1
        self.assertIsNone(check_reply(self.REQ, embed_obj(v), SPECS))

    def test_extra_or_missing_keys(self):
        r = self.good()
        r['note'] = 'hi'
        self.assertIsNone(check_reply(self.REQ, r, SPECS))
        r = self.good()
        del r['shape']
        self.assertIsNone(check_reply(self.REQ, r, SPECS))
        self.assertIsNone(check_reply(self.REQ, {'scores': [1.0, 2.0]}, SPECS))
        self.assertIsNone(check_reply(self.REQ, {'error': 'busy'}, SPECS))

    def test_unknown_model_and_junk(self):
        req = dict(self.REQ, model='nope')
        self.assertIsNone(check_reply(req, self.good(), SPECS))
        self.assertIsNone(check_reply(self.REQ, [1, 2], SPECS))
        self.assertIsNone(check_reply({'op': 'other'}, {}, SPECS))
        self.assertIsNone(check_reply({'op': 'embed'}, self.good(), SPECS))

    def test_scores(self):
        req = {'op': 'rerank', 'model': 'rr', 'q': 'x', 'docs': ['a', 'b', 'c']}
        self.assertEqual(check_reply(req, {'scores': [1, 2.5, -3]}, SPECS), [1.0, 2.5, -3.0])
        for bad in ({'scores': [1.0, 2.0]}, {'scores': [1.0, 2.0, 3.0, 4.0]},
                    {'scores': [1.0, float('nan'), 3.0]}, {'scores': [1.0, 1e5, 3.0]},
                    {'scores': [1.0, True, 3.0]}, {'scores': [1.0, '2', 3.0]},
                    {'scores': [1.0, 10 ** 400, 3.0]}, {'scores': 'abc'},
                    {'scores': [1, 2, 3], 'extra': 1}):
            self.assertIsNone(check_reply(req, bad, SPECS), bad)


@helpers.needs_unix_sockets
class SocketCalls(ClientBase):
    @needs_numpy
    def test_embed_and_rerank_and_ping(self):
        s = self.serve()
        c = self.client(s)
        self.assertTrue(c.ping())
        v = c.embed(['one', 'two', 'three'], 'q')
        self.assertEqual(v.shape, (3, 8))
        self.assertEqual(c.rerank('q', ['a', 'b']), [0.0, 1.0])
        self.assertEqual(s.accepted, 3)

    @needs_numpy
    def test_request_shape_on_the_wire(self):
        seen = []

        def h(conn, req):
            seen.append(req)
            return good_handler(conn, req)

        s = self.serve(h)
        c = self.client(s)
        c.embed(['x'], 'd')
        c.rerank('q', ['a'])
        self.assertEqual(seen[0], {'op': 'embed', 'model': 'emb', 'texts': ['x'], 'kind': 'd'})
        self.assertEqual(seen[1], {'op': 'rerank', 'model': 'rr', 'q': 'q', 'docs': ['a']})

    @needs_numpy
    def test_bad_replies_over_the_socket(self):
        cases = {
            'wrong shape': line(embed_obj(unit_rows(3, 8))),
            'wrong dim': line(embed_obj(unit_rows(1, 7))),
            'error code': line({'error': 'busy'}),
            'extra key': line(dict(embed_obj(unit_rows(1, 8)), more=1)),
            'two lines': line(embed_obj(unit_rows(1, 8))) * 2,
            'no newline': json.dumps(embed_obj(unit_rows(1, 8))).encode(),
            'not json': b'hello\n',
            'nan token': b'{"shape": [1, 8], "b64": NaN}\n',
            'duplicate key': b'{"shape": [1, 8], "shape": [1, 8], "b64": ""}\n',
            'non unit': line(embed_obj(unit_rows(1, 8) * 3)),
            'empty': b'',
        }
        box = {}
        s = self.serve(lambda conn, req: box['reply'])
        c = self.client(s)
        for name, reply in cases.items():
            box['reply'] = reply
            c.reset()
            self.assertIsNone(c.embed(['x'], 'q'), name)

    def test_bad_score_replies_over_the_socket(self):
        box = {}
        s = self.serve(lambda conn, req: box['reply'])
        c = self.client(s)
        cases = {
            'nan': b'{"scores": [NaN]}\n',
            'inf': b'{"scores": [1e999]}\n',
            'two lines': b'{"scores": [1.0]}\n{"scores": [1.0]}\n',
            'no newline': b'{"scores": [1.0]}',
            'duplicate key': b'{"scores": [1.0], "scores": [1.0]}\n',
            'extra key': b'{"scores": [1.0], "x": 1}\n',
            'count': b'{"scores": [1.0, 2.0]}\n',
            'bool': b'{"scores": [true]}\n',
        }
        for name, reply in cases.items():
            box['reply'] = reply
            c.reset()
            self.assertIsNone(c.rerank('q', ['a']), name)
        box['reply'] = b'{"scores": [0.25]}\n'
        self.assertEqual(c.rerank('q', ['a']), [0.25])

    def test_oversized_reply(self):
        s = self.serve(lambda conn, req: b'x' * (modeld_client.MAX_REPLY + 4096))
        c = self.client(s)
        self.assertIsNone(c.rerank('q', ['a']))

    def test_descriptor_is_closed_and_reply_dropped(self):
        r, w = os.pipe()
        self.addCleanup(lambda: os.close(r))
        wdup = os.dup(w)
        os.close(w)

        def h(conn, req):
            conn.sendmsg([line({'scores': [1.0]})],
                         [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [wdup]))])
            return None

        s = self.serve(h)
        c = self.client(s)
        self.assertIsNone(c.rerank('q', ['a']))
        os.close(wdup)       # our own copy, the client had one more
        ready, _, _ = select.select([r], [], [], 2.0)
        # every write end is closed only if the client closed the one it received
        self.assertTrue(ready)
        self.assertEqual(os.read(r, 10), b'')

    def test_cut_control_message_disables_the_client(self):
        path = os.path.join(helpers.short_tmpdir(), 'x.sock')
        self.addCleanup(modeld_client._DEAD.discard, path)
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)

        class FakeSock:
            def __init__(self, *a):
                pass

            def settimeout(self, t):
                pass

            def connect(self, p):
                pass

            def sendall(self, b):
                pass

            def recvmsg(self, n, anc):
                return b'{"scores": [1.0]}\n', [], socket.MSG_CTRUNC, None

            def close(self):
                pass

        fake = types.SimpleNamespace(socket=FakeSock, AF_UNIX=socket.AF_UNIX, timeout=socket.timeout,
                                     SOL_SOCKET=socket.SOL_SOCKET, SCM_RIGHTS=socket.SCM_RIGHTS,
                                     MSG_CTRUNC=socket.MSG_CTRUNC)
        c = Client(path, BUDGETS, SPECS)
        with mock.patch.object(modeld_client, 'socket', fake):
            self.assertIsNone(c.rerank('q', ['a']))
        self.assertIn(path, modeld_client._DEAD)
        # a real server on that path is never asked again
        s = self.serve(good_handler, path=path + '2')
        self.addCleanup(modeld_client._DEAD.discard, path + '2')
        modeld_client._DEAD.add(path + '2')
        c2 = Client(path + '2', BUDGETS, SPECS)
        self.assertIsNone(c2.rerank('q', ['a']))
        self.assertEqual(s.accepted, 0)

    def test_reply_over_the_budget(self):
        def slow(conn, req):
            time.sleep(1.2)
            return good_handler(conn, req)

        s = self.serve(slow)
        c = self.client(s)
        t0 = time.monotonic()
        self.assertIsNone(c.call({'op': 'rerank', 'model': 'rr', 'q': 'q', 'docs': ['a']}, budget=0.3))
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(c.why, modeld_client.WHY_BUDGET)

    def test_cool_down(self):
        d = helpers.short_tmpdir()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, 'late.sock')
        c = Client(path, dict(BUDGETS, connect=0.3), SPECS)
        t0 = time.monotonic()
        self.assertFalse(c.ping())
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)
        self.assertEqual(c.why, modeld_client.WHY_NO_MODEL)
        # the daemon shows up, the client still keeps away until the cool-down ends
        s = self.serve(good_handler, path=path)
        t0 = time.monotonic()
        self.assertFalse(c.ping())
        self.assertLess(time.monotonic() - t0, 0.2)
        self.assertEqual(s.accepted, 0)
        c.clock = lambda: time.monotonic() + modeld_client.COOL + 1
        self.assertTrue(c.ping())
        self.assertEqual(s.accepted, 1)

    def test_reset_clears_the_cool_down(self):
        d = helpers.short_tmpdir()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, 'late.sock')
        c = Client(path, dict(BUDGETS, connect=0.1), SPECS)
        self.assertFalse(c.ping())
        s = self.serve(good_handler, path=path)
        c.reset()
        self.assertTrue(c.ping())
        self.assertEqual(s.accepted, 1)

    def test_bad_requests_never_touch_the_socket(self):
        s = self.serve()
        c = self.client(s)
        self.assertIsNone(c.call({'op': 'shell'}))
        self.assertIsNone(c.call('ping'))
        self.assertIsNone(c.embed([], 'q'))
        self.assertIsNone(c.embed(['a'], 'z'))
        self.assertIsNone(c.embed(['a', 3], 'q'))
        self.assertIsNone(c.rerank('q', []))
        self.assertIsNone(c.rerank(5, ['a']))
        self.assertEqual(s.accepted, 0)

    def test_no_model_for_the_op(self):
        s = self.serve()
        c = Client(s.path, BUDGETS, {'emb': spec()})
        self.assertIsNone(c.rerank('q', ['a']))
        self.assertEqual(c.why, modeld_client.WHY_NO_MODELS)
        c = Client(s.path, BUDGETS, {})
        self.assertIsNone(c.embed(['a'], 'q'))
        self.assertEqual(c.why, modeld_client.WHY_NO_MODELS)
        self.assertEqual(s.accepted, 0)

    def test_missing_weights_code_gets_the_fixed_reason(self):
        s = self.serve(lambda conn, req: line({'error': 'model_missing'}))
        c = self.client(s)
        self.assertIsNone(c.rerank('q', ['a']))
        self.assertEqual(c.why, modeld_client.WHY_NO_WEIGHTS)

    def test_bye(self):
        seen = []

        def h(conn, req):
            seen.append(req['op'])
            return good_handler(conn, req)

        s = self.serve(h)
        self.client(s).bye()
        self.assertEqual(seen, ['bye'])


@helpers.needs_unix_sockets
class Wake(ClientBase):
    """No daemon answers and the client may start one: the search path of a host with no warm-up hook."""

    def setUp(self):
        d = helpers.short_tmpdir()
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, 'm.sock')
        self.starts = []
        self.box = {}
        self.addCleanup(lambda: self.box.get('s') and self.box['s'].close())

    def waker(self, word='started', delay=0.3):
        """A start that answers word and, when it says started, binds a server a little later."""
        def wake():
            self.starts.append(word)
            if word == 'started':
                t = threading.Timer(delay, lambda: self.box.setdefault('s', TinyServer(good_handler, self.path)))
                t.start()
                self.addCleanup(t.join)
            return word
        return wake

    def test_rerank_starts_the_daemon_and_waits_for_it(self):
        c = Client(self.path, dict(BUDGETS, connect=3.0), SPECS, wake=self.waker())
        self.assertEqual(c.rerank('q', ['a', 'b']), [0.0, 1.0])
        self.assertEqual(self.starts, ['started'])
        # up now: no second start
        self.assertEqual(c.rerank('q', ['a']), [0.0])
        self.assertEqual(self.starts, ['started'])
        self.assertEqual(self.box['s'].accepted, 2)

    def test_locked_means_another_start_is_on_its_way(self):
        c = Client(self.path, dict(BUDGETS, connect=3.0), SPECS, wake=self.waker('locked'))
        t = threading.Timer(0.3, lambda: self.box.setdefault('s', TinyServer(good_handler, self.path)))
        t.start()
        self.addCleanup(t.join)
        self.assertEqual(c.rerank('q', ['a']), [0.0])
        self.assertEqual(self.starts, ['locked'])

    def test_ping_and_bye_never_start_it(self):
        c = Client(self.path, dict(BUDGETS, connect=0.2), SPECS, wake=self.waker())
        self.assertFalse(c.ping())
        c.reset()
        c.bye()
        self.assertEqual(self.starts, [])

    def test_a_refused_start_gives_up_at_once_and_cools_down(self):
        for word in ('refused', 'no weights', 'no backend'):
            self.starts.clear()
            c = Client(self.path, dict(BUDGETS, connect=3.0), SPECS, wake=self.waker(word))
            t0 = time.monotonic()
            self.assertIsNone(c.rerank('q', ['a']))
            self.assertLess(time.monotonic() - t0, 1.0, word)
            self.assertEqual(c.why, modeld_client.WHY_NO_MODEL)
            # the cool-down holds: no second start inside it
            self.assertIsNone(c.rerank('q', ['a']))
            self.assertEqual(self.starts, [word])

    def test_a_start_that_raises_counts_as_refused(self):
        def boom():
            self.starts.append('boom')
            raise RuntimeError('x')

        c = Client(self.path, dict(BUDGETS, connect=3.0), SPECS, wake=boom)
        t0 = time.monotonic()
        self.assertIsNone(c.rerank('q', ['a']))
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(self.starts, ['boom'])

    def test_no_wake_keeps_the_old_wait(self):
        c = Client(self.path, dict(BUDGETS, connect=0.3), SPECS)
        t0 = time.monotonic()
        self.assertIsNone(c.rerank('q', ['a']))
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)
        self.assertEqual(c.why, modeld_client.WHY_NO_MODEL)

    def test_from_config_wakes_only_when_asked(self):
        home, cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, home)
        self.assertIsNone(Client.from_config(cfg)._wake)
        c = Client.from_config(cfg, wake=True)
        with mock.patch('leks.hooks.warm_models.start', return_value='refused') as st:
            self.assertIsNone(c.rerank('q', ['a']))
        st.assert_called_once_with(cfg)


class ColdBudget(unittest.TestCase):
    def test_cold_then_warm_then_cold_again(self):
        c = Client('/nonexistent/x.sock', {'rerank': 8.0, 'cold_rerank': 30.0, 'cold_after': 600.0}, SPECS)
        now = [100.0]
        c.clock = lambda: now[0]
        self.assertEqual(c.rerank_budget(), 30.0)
        now[0] = 101.0
        self.assertEqual(c.rerank_budget(), 8.0)
        now[0] = 700.0           # 599 s since the last try
        self.assertEqual(c.rerank_budget(), 8.0)
        now[0] = 1301.0          # 601 s since the last try
        self.assertEqual(c.rerank_budget(), 30.0)
        now[0] = 1302.0
        self.assertEqual(c.rerank_budget(), 8.0)

    def test_budgets_come_from_the_config(self):
        c = Client('/nonexistent/x.sock', {'rerank': 2.0, 'cold_rerank': 9.0, 'cold_after': 5.0}, SPECS)
        now = [0.0]
        c.clock = lambda: now[0]
        self.assertEqual(c.rerank_budget(), 9.0)
        now[0] = 1.0
        self.assertEqual(c.rerank_budget(), 2.0)
        now[0] = 7.0
        self.assertEqual(c.rerank_budget(), 9.0)


@helpers.needs_unix_sockets
class Readiness(ClientBase):
    def cfg(self, path, backend='torch', embedder=True, reranker=True):
        return types.SimpleNamespace(
            backend=backend, embedder=spec() if embedder else None,
            reranker=spec('rr', 'maxsim', 4) if reranker else None,
            budgets=dict(BUDGETS), socket_path=lambda: path)

    def test_no_models(self):
        s = self.serve()
        c = Client.from_config(self.cfg(s.path, embedder=False, reranker=False))
        self.assertIsNone(c.rerank('q', ['a']))
        self.assertIsNone(c.call({'op': 'ping'}))
        self.assertEqual(c.why, modeld_client.WHY_NO_MODELS)
        self.assertEqual(s.accepted, 0)

    def test_no_backend(self):
        s = self.serve()
        c = Client.from_config(self.cfg(s.path))
        with mock.patch.object(modeld_client, '_have', lambda name: False):
            self.assertIsNone(c.rerank('q', ['a']))
        self.assertEqual(c.why, modeld_client.WHY_NO_BACKEND)
        self.assertEqual(s.accepted, 0)

    def test_no_weights_for_the_asked_model(self):
        s = self.serve()
        c = Client.from_config(self.cfg(s.path))
        have = {'emb': True, 'rr': False}
        with mock.patch.object(modeld_client, '_have', lambda name: True), \
                mock.patch.object(modeld_client, '_weights_present', lambda cfg, key: have[key]):
            self.assertIsNone(c.rerank('q', ['a']))
            self.assertEqual(c.why, modeld_client.WHY_NO_WEIGHTS)
            self.assertEqual(s.accepted, 0)
            if np is not None:
                self.assertIsNotNone(c.embed(['a'], 'q'))
            self.assertTrue(c.ping())

    def test_no_weights_at_all(self):
        s = self.serve()
        c = Client.from_config(self.cfg(s.path))
        with mock.patch.object(modeld_client, '_have', lambda name: True), \
                mock.patch.object(modeld_client, '_weights_present', lambda cfg, key: False):
            self.assertFalse(c.ping())
        self.assertEqual(c.why, modeld_client.WHY_NO_WEIGHTS)
        self.assertEqual(s.accepted, 0)

    def test_fake_backend_is_always_ready(self):
        s = self.serve()
        c = Client.from_config(self.cfg(s.path, backend='fake'))
        with mock.patch.object(modeld_client, '_have', lambda name: name != 'torch'):
            self.assertEqual(c.rerank('q', ['a', 'b']), [0.0, 1.0])

    def test_ready_never_imports_the_backend(self):
        c = Client.from_config(self.cfg('/nonexistent/x.sock'))
        before = set(sys.modules)
        c.call({'op': 'ping'})
        self.assertFalse({'torch', 'transformers', 'safetensors'} & (set(sys.modules) - before))

    def test_no_numpy_closes_embed_only(self):
        s = self.serve()
        c = Client.from_config(self.cfg(s.path, backend='fake'))
        with mock.patch.object(modeld_client, '_have', lambda name: name != 'numpy'):
            self.assertIsNone(c.embed(['a'], 'q'))
            self.assertEqual(c.why, modeld_client.WHY_NO_NUMPY)
            self.assertEqual(c.rerank('q', ['a']), [0.0])

    def test_ready_callback_is_asked_before_the_socket(self):
        s = self.serve()
        c = Client(s.path, BUDGETS, SPECS, ready=lambda: 'model weights not found')
        self.assertIsNone(c.rerank('q', ['a']))
        self.assertEqual(c.why, 'model weights not found')
        self.assertEqual(s.accepted, 0)


# ---------------------------------------------------------------- vector files and the embed run

def unit_vec(text, dim):
    d = hashlib.sha256(text.encode('utf-8')).digest()
    raw = np.array([d[i % len(d)] - 127.5 for i in range(dim)], dtype='float64') + np.arange(dim) * 0.01
    return (raw / np.linalg.norm(raw)).astype('float32')


class StubClient:
    """Stands in for the daemon client: deterministic vectors, scripted failures."""

    def __init__(self, dim, fail_after=None, bad=None):
        self.dim, self.fail_after, self.bad = dim, fail_after, bad
        self.calls, self.byes, self.dead = [], 0, False

    def reset(self):
        pass

    def embed(self, texts, kind='q', model=None, budget=None):
        self.calls.append(list(texts))
        if self.dead:
            return None
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            self.dead = True
            return None
        if self.bad and any(self.bad in t for t in texts):
            return None
        return np.stack([unit_vec(t, self.dim) for t in texts])

    def ping(self):
        return not self.dead

    def bye(self):
        self.byes += 1

    def sent(self):
        return [t for c in self.calls for t in c]


MASTER_DDL = """
CREATE TABLE files(root TEXT, rel TEXT, ext TEXT, kind TEXT, title TEXT, summary TEXT, warning TEXT,
  status TEXT, status_src TEXT, date TEXT, mtime REAL, size INT, sha256 TEXT, units INT, unit TEXT,
  extractor TEXT, indexed_at REAL, PRIMARY KEY(root, rel));
CREATE TABLE chunks(root TEXT, rel TEXT, ord INT, start_line INT, end_line INT, heading TEXT,
  text TEXT, ehash TEXT, PRIMARY KEY(root, rel, ord));
"""


def kind_of(root, rel):
    if rel == 'STATE.md':
        return 'state'
    if rel.startswith('decisions/'):
        return 'decision'
    return {'code': 'code', 'docs': 'document'}.get(root, 'note')


def build_master(cfg, extra=40):
    """A small master database: the helper corpus plus many short generated passages."""
    schema = get_schema()
    os.makedirs(cfg.index_dir(), exist_ok=True)
    if os.path.exists(cfg.master_path()):
        os.remove(cfg.master_path())
    c = sqlite3.connect(cfg.master_path())
    c.executescript(MASTER_DDL)
    files = []
    for root, src in (('notes', helpers.NOTES), ('code', helpers.CODE), ('docs', helpers.DOCS)):
        for rel, text in src.items():
            title = next((x[2:].strip() for x in text.splitlines() if x.startswith('# ')), rel)
            paras = [p.strip() for p in text.split('\n\n') if p.strip()]
            files.append((root, rel, kind_of(root, rel), title, paras))
    many = ['Generated passage number %d about render pass %d%s.' % (i, i, ' and more words' * (i % 5))
            for i in range(extra)]
    files.append(('notes', 'many.md', 'note', 'Many passages', many))
    # the same passage under the same title in two files, one of them a state note
    files.append(('notes', 'dup_a.md', 'note', 'Twin', ['Twin passage text.']))
    files.append(('docs', 'dup_b.md', 'state', 'Twin', ['Twin passage text.']))
    for root, rel, kind, title, paras in files:
        c.execute('INSERT INTO files(root, rel, kind, title) VALUES(?,?,?,?)', (root, rel, kind, title))
        for i, p in enumerate(paras):
            h = schema.ehash(schema.embed_input(title, '', p))
            c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)', (root, rel, i, 1, 1, '', p, h))
    c.commit()
    c.close()


def build_profile(cfg, profile, built='1700000000.5'):
    """A profile database with only meta and vecmap. Returns (built, [ehash in vec order])."""
    names = [r.name for r in cfg.roots_for(profile)]
    m = sqlite3.connect(cfg.master_path())
    marks = ','.join('?' * len(names))
    hashes = []
    for (h,) in m.execute('SELECT ehash FROM chunks WHERE root IN (%s) ORDER BY root, rel, ord' % marks, names):
        if h not in hashes:
            hashes.append(h)
    m.close()
    p = cfg.profile_db(profile)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        os.remove(p)
    c = sqlite3.connect(p)
    c.execute('CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)')
    c.execute('CREATE TABLE vecmap(vec INTEGER PRIMARY KEY, ehash TEXT)')
    c.execute("INSERT INTO meta VALUES('built', ?)", (built,))
    c.executemany('INSERT INTO vecmap VALUES(?,?)', list(enumerate(hashes)))
    c.commit()
    c.close()
    return built, hashes


def run_quiet(cfg, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = embed_all.run(cfg, tries=1, wait=0, **kw)
    return rc, out.getvalue().strip().splitlines()


def store_hashes(cfg):
    c = sqlite3.connect(vecstore.store_path(cfg, cfg.embedder.key))
    try:
        return {r[0] for r in c.execute('SELECT h FROM emb')}
    finally:
        c.close()


@needs_numpy
class EmbedRun(unittest.TestCase):
    def setUp(self):
        get_schema()
        self.home, self.cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        self.dim = self.cfg.embedder.dim
        build_master(self.cfg)
        self.built = {p: build_profile(self.cfg, p) for p in ('architect', 'orchestrator')}

    def stub(self, **kw):
        return StubClient(self.dim, **kw)

    def texts(self, profiles):
        return {h: t for h, t, _ in embed_all.passages(self.cfg, profiles)}

    def test_full_run_writes_aligned_vectors(self):
        c = self.stub()
        rc, lines = run_quiet(self.cfg, client=c)
        self.assertEqual(rc, 0)
        total = len(self.texts(list(self.cfg.profiles)))
        self.assertEqual(lines[-1], 'DONE %s %d' % (self.cfg.embedder.key, total))
        self.assertEqual(c.byes, 1)
        self.assertEqual(len(store_hashes(self.cfg)), total)
        # every sent text was sent exactly once
        self.assertEqual(len(c.sent()), len(set(c.sent())))
        for prof in ('architect', 'orchestrator'):
            built, hashes = self.built[prof]
            mm, covered, why = vecstore.open_vectors(self.cfg, prof, built, self.cfg.embedder)
            self.assertIsNone(why)
            self.assertEqual(covered, len(hashes))
            self.assertEqual(mm.shape, (len(hashes), self.dim))
            text = self.texts([prof])
            for i, h in enumerate(hashes):
                self.assertTrue(np.array_equal(np.asarray(mm[i]), embed_all.quantise(unit_vec(text[h], self.dim))), h)

    def test_second_run_sends_nothing(self):
        run_quiet(self.cfg, client=self.stub())
        c = self.stub()
        before = os.stat(vecstore.profile_file(self.cfg, 'mdense', 'architect')).st_mtime
        rc, lines = run_quiet(self.cfg, client=c)
        self.assertEqual(rc, 0)
        self.assertEqual(c.calls, [])
        self.assertIn('to embed 0 of', ' '.join(lines))
        self.assertTrue(lines[-1].startswith('DONE '))
        # files that fit the build are left alone
        self.assertEqual(os.stat(vecstore.profile_file(self.cfg, 'mdense', 'architect')).st_mtime, before)

    def test_stale_hash_is_pruned_and_other_profiles_are_kept(self):
        run_quiet(self.cfg, client=self.stub(), profile='architect')
        notes_only = len(store_hashes(self.cfg))
        rc, lines = run_quiet(self.cfg, client=self.stub())
        self.assertEqual(rc, 0)
        full = len(store_hashes(self.cfg))
        self.assertGreater(full, notes_only)       # the code root came in
        db = sqlite3.connect(vecstore.store_path(self.cfg, 'mdense'))
        db.execute('INSERT INTO emb VALUES(?,?)', ('f' * 32, b'\0' * (self.dim * 4)))
        db.commit()
        db.close()
        # a run for one profile prunes only what no chunk of the master has
        rc, lines = run_quiet(self.cfg, client=self.stub(), profile='architect')
        self.assertEqual(rc, 0)
        self.assertIn('pruned 1', lines)
        self.assertEqual(len(store_hashes(self.cfg)), full)
        self.assertNotIn('f' * 32, store_hashes(self.cfg))

    def test_priority_order_and_short_before_long(self):
        c = self.stub()
        run_quiet(self.cfg, client=c)
        sent = c.sent()
        first = sent[0]
        self.assertTrue(first.startswith('Project state|') or first.startswith('Twin|'), first)
        pr = {t: p for _, t, p in embed_all.passages(self.cfg, list(self.cfg.profiles))}
        order = [pr[t] for t in sent]
        self.assertEqual(order, sorted(order))
        for p in set(order):
            group = [len(t) for t in sent if pr[t] == p]
            self.assertEqual(group, sorted(group))
        # the twin passage lives in a state note too, so it counts as state
        twin = [t for t in sent if t.startswith('Twin|')]
        self.assertEqual(len(twin), 1)
        self.assertEqual(pr[twin[0]], 0)

    def test_batches_stay_within_the_limit(self):
        c = self.stub()
        run_quiet(self.cfg, client=c)
        self.assertTrue(all(0 < len(b) <= embed_all.BATCH for b in c.calls))
        self.assertGreater(len(c.calls), 2)

    def test_stopped_daemon_resumes_later(self):
        c = self.stub(fail_after=1)
        rc, lines = run_quiet(self.cfg, client=c)
        total = len(self.texts(list(self.cfg.profiles)))
        self.assertEqual(rc, 1)
        self.assertEqual(lines[-1], 'INCOMPLETE mdense %d of %d' % (embed_all.BATCH, total))
        self.assertTrue(any(x.startswith('STOPPED') for x in lines))
        self.assertEqual(len(store_hashes(self.cfg)), embed_all.BATCH)
        # the half done state still writes honest coverage
        built, hashes = self.built['orchestrator']
        mm, covered, why = vecstore.open_vectors(self.cfg, 'orchestrator', built, self.cfg.embedder)
        self.assertIsNone(why)
        self.assertEqual(covered, embed_all.BATCH)
        # resume: only the missing ones are sent
        c2 = self.stub()
        rc, lines = run_quiet(self.cfg, client=c2)
        self.assertEqual(rc, 0)
        self.assertEqual(len(c2.sent()), total - embed_all.BATCH)
        self.assertEqual(lines[-1], 'DONE mdense %d' % total)
        mm, covered, why = vecstore.open_vectors(self.cfg, 'orchestrator', built, self.cfg.embedder)
        self.assertEqual(covered, len(hashes))

    def test_a_mapped_vector_file_gets_a_spare_name_then_its_own_again(self):
        # windows refuses to replace a vector file a running server maps. before the fix the new rows were
        # dropped there and the meaning lane kept the old coverage
        run_quiet(self.cfg, client=self.stub(fail_after=1))
        built, hashes = self.built['orchestrator']
        f = vecstore.profile_file(self.cfg, 'mdense', 'orchestrator')
        real = os.replace

        def refuse(src, dst):
            if dst == f:
                raise PermissionError(13, 'the file is in use', dst)
            return real(src, dst)

        with mock.patch.object(vecstore.os, 'replace', refuse):
            rc, _ = run_quiet(self.cfg, client=self.stub())
        self.assertEqual(rc, 0)
        spare = os.path.splitext(f)[0] + '.1.f32'
        self.assertEqual(vecstore.read_side(self.cfg, 'mdense', 'orchestrator')['file'], os.path.basename(spare))
        mm, covered, why = vecstore.open_vectors(self.cfg, 'orchestrator', built, self.cfg.embedder)
        self.assertIsNone(why)
        self.assertEqual(covered, len(hashes))
        del mm
        # once the old file is free the next write takes the own name back and drops the spare
        self.assertEqual(vecstore.write_profile(self.cfg, 'mdense', 'orchestrator'), (len(hashes), len(hashes)))
        self.assertEqual(vecstore.read_side(self.cfg, 'mdense', 'orchestrator')['file'], os.path.basename(f))
        self.assertFalse(os.path.exists(spare))
        self.assertEqual(vecstore.open_vectors(self.cfg, 'orchestrator', built, self.cfg.embedder)[1], len(hashes))

    def test_a_view_left_mapped_counts_as_in_use_and_other_errors_still_raise(self):
        # 1224 is what windows says when only a mapped view holds the file; python makes it a plain OSError
        run_quiet(self.cfg, client=self.stub())
        f = vecstore.profile_file(self.cfg, 'mdense', 'orchestrator')
        real = os.replace

        def mapped(src, dst):
            if dst == f:
                e = OSError(22, 'the file has a user-mapped section open', dst)
                e.winerror = 1224
                raise e
            return real(src, dst)

        with mock.patch.object(vecstore.os, 'replace', mapped):
            vecstore.write_profile(self.cfg, 'mdense', 'orchestrator')
        self.assertEqual(vecstore.read_side(self.cfg, 'mdense', 'orchestrator')['file'], 'orchestrator.1.f32')

        def full(src, dst):
            raise OSError(28, 'no space left on device', dst)

        with mock.patch.object(vecstore.os, 'replace', full), self.assertRaises(OSError) as cm:
            vecstore.write_profile(self.cfg, 'mdense', 'orchestrator')
        self.assertEqual(cm.exception.errno, 28)
        self.assertFalse(os.path.exists(f + '.tmp'))
        # the side file still names the spare that holds the rows
        self.assertEqual(vecstore.read_side(self.cfg, 'mdense', 'orchestrator')['file'], 'orchestrator.1.f32')

    def test_a_side_file_names_no_file_outside_its_folder(self):
        run_quiet(self.cfg, client=self.stub())
        side = vecstore.read_side(self.cfg, 'mdense', 'orchestrator')
        f = vecstore.profile_file(self.cfg, 'mdense', 'orchestrator')
        for bad in ('../x.f32', os.path.join('..', 'x.f32'), f, 'orchestrator.9.f32', None, 7):
            self.assertEqual(vecstore.current_file(self.cfg, 'mdense', 'orchestrator', dict(side, file=bad)), f)

    def test_one_bad_passage_stays_out_alone(self):
        c = self.stub(bad='passage number 7 ')
        rc, lines = run_quiet(self.cfg, client=c)
        total = len(self.texts(list(self.cfg.profiles)))
        self.assertEqual(rc, 1)
        self.assertEqual(lines[-1], 'INCOMPLETE mdense %d of %d' % (total - 1, total))
        self.assertEqual(len(store_hashes(self.cfg)), total - 1)
        # the batch that failed was retried text by text
        self.assertTrue(any(len(b) == 1 for b in c.calls))
        c2 = self.stub()
        rc, lines = run_quiet(self.cfg, client=c2)
        self.assertEqual(rc, 0)
        self.assertEqual(len(c2.sent()), 1)

    def test_refuses_a_store_from_another_revision(self):
        db = embed_all.open_store(self.cfg, self.cfg.embedder)
        db.execute("UPDATE meta SET v=? WHERE k='revision'", ('0' * 40,))
        db.commit()
        db.close()
        c = self.stub()
        rc, lines = run_quiet(self.cfg, client=c)
        self.assertEqual(rc, 2)
        self.assertEqual(c.calls, [])
        self.assertTrue(lines[-1].startswith('refused'))

    def test_refuses_a_store_of_another_size(self):
        db = embed_all.open_store(self.cfg, self.cfg.embedder)
        db.execute("UPDATE meta SET v='999' WHERE k='dim'")
        db.commit()
        db.close()
        with self.assertRaises(embed_all.StoreRefused):
            embed_all.open_store(self.cfg, self.cfg.embedder)
        self.assertEqual(run_quiet(self.cfg, client=self.stub())[0], 2)

    def test_store_pins_revision_and_dim(self):
        db = embed_all.open_store(self.cfg, self.cfg.embedder)
        meta = dict(db.execute('SELECT k, v FROM meta').fetchall())
        db.close()
        self.assertEqual(meta['revision'], self.cfg.embedder.revision)
        self.assertEqual(meta['dim'], str(self.dim))
        self.assertEqual(meta['hf_id'], self.cfg.embedder.hf_id)

    def test_no_embedder_or_unknown_profile_or_no_index(self):
        cfg = self.cfg
        self.assertEqual(run_quiet(cfg, client=self.stub(), profile='nope')[0], 2)
        os.remove(cfg.master_path())
        self.assertEqual(run_quiet(cfg, client=self.stub())[0], 2)
        cfg.embedder = None
        c = self.stub()
        self.assertEqual(run_quiet(cfg, client=c)[0], 2)
        self.assertEqual(c.calls, [])

    def test_hash_that_does_not_match_its_text_is_skipped(self):
        m = sqlite3.connect(self.cfg.master_path())
        m.execute("UPDATE chunks SET ehash='0' WHERE root='code' AND ord=0")
        m.commit()
        m.close()
        rows = embed_all.passages(self.cfg, ['orchestrator'])
        self.assertNotIn('0', [r[0] for r in rows])
        self.assertTrue(all(len(r[0]) == 32 for r in rows))

    def test_passages_use_the_embed_input(self):
        schema = get_schema()
        rows = embed_all.passages(self.cfg, ['architect'])
        self.assertTrue(rows)
        for h, text, _pr in rows:
            self.assertEqual(h, schema.ehash(text))
            self.assertLessEqual(len(text), schema.EMBED_CUT)
        # profile scope: the code root is not in architect
        self.assertFalse([1 for _, t, _ in rows if 'TileCache' in t])
        self.assertTrue([1 for _, t, _ in embed_all.passages(self.cfg, ['orchestrator']) if 'TileCache' in t])


@needs_numpy
class Quantise(unittest.TestCase):
    def test_grid_and_unit_length(self):
        v = unit_rows(5, 32)
        q = embed_all.quantise(v)
        self.assertEqual(q.shape, v.shape)
        self.assertLess(np.abs(q - v).max(), 0.02)
        self.assertTrue((np.abs(np.linalg.norm(q, axis=1) - 1.0) < 1e-5).all())
        # one more round changes nothing worth keeping
        self.assertLess(np.abs(embed_all.quantise(q) - q).max(), 1e-3)

    def test_one_row(self):
        v = unit_rows(1, 16)[0]
        q = embed_all.quantise(v)
        self.assertEqual(q.shape, (16,))
        self.assertAlmostEqual(float(np.linalg.norm(q)), 1.0, places=5)

    def test_hidden_bits_are_gone(self):
        base = unit_rows(1, 64)[0]
        nudged = (base + 1e-4 * np.sin(np.arange(64))).astype('float32')
        nudged /= np.linalg.norm(nudged)
        a, b = embed_all.quantise(base), embed_all.quantise(nudged)
        # tiny differences below the grid collapse to the same bytes for most entries
        self.assertGreater(int((a == b).sum()), 40)

    def test_non_unit_rows_stay_as_rounded(self):
        v = (unit_rows(2, 8) * 3).astype('float32')
        q = embed_all.quantise(v)
        self.assertTrue(np.allclose(q, np.round(v * 512) / 512, atol=1e-6))


@needs_numpy
class VectorFiles(unittest.TestCase):
    def setUp(self):
        get_schema()
        self.home, self.cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        self.spec = self.cfg.embedder
        build_master(self.cfg, extra=5)
        self.built, self.hashes = build_profile(self.cfg, 'architect')
        self.texts = {h: t for h, t, _ in embed_all.passages(self.cfg, ['architect'])}

    def fill(self, keep=None):
        db = embed_all.open_store(self.cfg, self.spec)
        for h in self.hashes[:keep]:
            db.execute('INSERT OR REPLACE INTO emb VALUES(?,?)',
                       (h, embed_all.quantise(unit_vec(self.texts[h], self.spec.dim)).tobytes()))
        db.commit()
        db.close()

    def write(self):
        return vecstore.write_profile(self.cfg, self.spec.key, 'architect')

    def open(self, built=None, spec=-1):
        sp = self.spec if spec == -1 else spec
        return vecstore.open_vectors(self.cfg, 'architect', built or self.built, sp)

    def side(self, **change):
        p = vecstore.side_file(self.cfg, self.spec.key, 'architect')
        with open(p) as f:
            d = json.load(f)
        d.update(change)
        with open(p, 'w') as f:
            json.dump(d, f)

    def test_paths(self):
        d = os.path.join(self.cfg.vectors_dir(), 'mdense')
        self.assertEqual(vecstore.vec_dir(self.cfg, 'mdense'), d)
        self.assertEqual(vecstore.store_path(self.cfg, 'mdense'), os.path.join(d, 'store.sqlite'))
        self.assertEqual(vecstore.profile_file(self.cfg, 'mdense', 'architect'), os.path.join(d, 'architect.f32'))
        self.assertEqual(vecstore.side_file(self.cfg, 'mdense', 'architect'), os.path.join(d, 'architect.json'))
        for bad in ('../x', 'a/b', '', 'A', 'x' * 60):
            with self.assertRaises(ValueError):
                vecstore.profile_file(self.cfg, 'mdense', bad)
        with self.assertRaises(ValueError):
            vecstore.vec_dir(self.cfg, '../x')

    def test_write_with_gaps_puts_zeros_and_counts_coverage(self):
        self.fill(keep=3)
        covered, n = self.write()
        self.assertEqual((covered, n), (3, len(self.hashes)))
        side = vecstore.read_side(self.cfg, 'mdense', 'architect')
        self.assertEqual(set(side), {'key', 'dim', 'n', 'covered', 'built', 'mtime', 'file'})
        self.assertEqual((side['key'], side['dim'], side['n'], side['covered'], side['built'], side['file']),
                         ('mdense', self.spec.dim, n, 3, self.built, 'architect.f32'))
        mm, cov, why = self.open()
        self.assertIsNone(why)
        self.assertEqual(cov, 3)
        self.assertTrue(np.all(np.asarray(mm[3:]) == 0))
        self.assertTrue(np.all(np.abs(np.linalg.norm(np.asarray(mm[:3]), axis=1) - 1) < 1e-5))

    def test_write_from_a_temporary_database_path(self):
        self.fill()
        tmp = self.cfg.profile_db('architect') + '.tmp'
        os.replace(self.cfg.profile_db('architect'), tmp)
        self.assertEqual(vecstore.write_profile(self.cfg, 'mdense', 'architect', tmp), (len(self.hashes),) * 2)

    def test_gate_no_vectors_yet(self):
        mm, cov, why = self.open()
        self.assertEqual((mm, cov, why), (None, 0, 'no vectors yet'))

    def test_gate_zero_covered(self):
        self.fill(keep=0)
        self.write()
        self.assertEqual(self.open(), (None, 0, 'no vectors yet'))

    def test_gate_no_models(self):
        self.assertEqual(self.open(spec=None), (None, 0, 'no models configured'))

    def test_gate_open_when_everything_fits(self):
        self.fill()
        self.write()
        mm, cov, why = self.open()
        self.assertIsNone(why)
        self.assertEqual(cov, len(self.hashes))

    def test_gate_other_build(self):
        self.fill()
        self.write()
        self.assertEqual(self.open(built='1700000009.0'), (None, 0, 'vectors out of step with the index'))

    def test_gate_side_fields(self):
        self.fill()
        self.write()
        orig = vecstore.read_side(self.cfg, 'mdense', 'architect')
        for change in ({'dim': 7}, {'n': 0}, {'n': orig['n'] + 1}, {'n': True}, {'n': 'x'},
                       {'covered': orig['n'] + 1}, {'covered': -1}, {'mtime': orig['mtime'] + 5},
                       {'key': 'other'}):
            self.side(**change)
            self.assertEqual(self.open(), (None, 0, 'vectors out of step with the index'), change)
            self.side(**orig)
        self.assertIsNone(self.open()[2])

    def test_gate_missing_or_broken_side(self):
        self.fill()
        self.write()
        p = vecstore.side_file(self.cfg, 'mdense', 'architect')
        with open(p, 'w') as f:
            f.write('{not json')
        self.assertEqual(self.open(), (None, 0, 'vectors out of step with the index'))
        os.remove(p)
        self.assertEqual(self.open(), (None, 0, 'vectors out of step with the index'))
        self.assertIsNone(vecstore.read_side(self.cfg, 'mdense', 'architect'))

    def test_gate_file_size(self):
        self.fill()
        self.write()
        f = vecstore.profile_file(self.cfg, 'mdense', 'architect')
        st = os.stat(f)
        with open(f, 'ab') as out:
            out.write(b'\0' * 4)
        self.assertEqual(self.open()[2], 'vectors out of step with the index')
        with open(f, 'r+b') as out:
            out.truncate(st.st_size)
        os.utime(f, (st.st_atime, st.st_mtime))
        self.assertIsNone(self.open()[2])

    def test_gate_file_rewritten_without_side(self):
        # a file replaced after the side file was written reads as out of step
        self.fill()
        self.write()
        f = vecstore.profile_file(self.cfg, 'mdense', 'architect')
        st = os.stat(f)
        os.utime(f, (st.st_atime, st.st_mtime + 3))
        self.assertEqual(self.open()[2], 'vectors out of step with the index')

    def test_gate_other_revision_of_the_model(self):
        self.fill()
        self.write()
        other = types.SimpleNamespace(key=self.spec.key, dim=self.spec.dim, op='embed', revision='1' * 40)
        self.assertEqual(self.open(spec=other)[2], 'vectors out of step with the index')

    def test_gate_without_numpy(self):
        self.fill()
        self.write()
        with mock.patch.dict(sys.modules, {'numpy': None}):
            self.assertEqual(self.open(), (None, 0, 'numpy not installed'))

    def test_write_refuses_a_gap_in_vecmap(self):
        self.fill()
        p = self.cfg.profile_db('architect')
        c = sqlite3.connect(p)
        c.execute('DELETE FROM vecmap WHERE vec=1')
        c.commit()
        c.close()
        with self.assertRaises(ValueError):
            self.write()
        self.assertFalse(os.path.exists(vecstore.profile_file(self.cfg, 'mdense', 'architect') + '.tmp'))
        self.assertFalse(os.path.exists(vecstore.profile_file(self.cfg, 'mdense', 'architect')))

    def test_write_refuses_a_store_of_another_revision(self):
        self.fill()
        other = types.SimpleNamespace(key='mdense', dim=self.spec.dim, op='embed', revision='2' * 40)
        cfg = types.SimpleNamespace(vectors_dir=self.cfg.vectors_dir, profile_db=self.cfg.profile_db,
                                    model=lambda key: other)
        with self.assertRaises(ValueError):
            vecstore.write_profile(cfg, 'mdense', 'architect')

    def test_write_unknown_key(self):
        with self.assertRaises(ValueError):
            vecstore.write_profile(self.cfg, 'nokey', 'architect')

    def test_write_ignores_a_blob_of_the_wrong_size(self):
        self.fill()
        db = sqlite3.connect(vecstore.store_path(self.cfg, 'mdense'))
        db.execute('UPDATE emb SET vec=? WHERE h=?', (b'\1\2\3', self.hashes[0]))
        db.commit()
        db.close()
        covered, n = self.write()
        self.assertEqual(covered, n - 1)

    def test_stored_keys(self):
        self.assertEqual(vecstore.stored_keys(self.cfg), [])
        self.fill()
        self.assertEqual(vecstore.stored_keys(self.cfg), ['mdense'])
        # a store the config no longer names is not listed
        self.cfg.embedder = None
        self.assertEqual(vecstore.stored_keys(self.cfg), [])

    def test_stamp(self):
        self.cfg.source_path = os.path.join(self.home, 'config.json')
        s = vecstore.stamp(self.cfg, 'architect')
        self.assertEqual(len(s), 3)
        self.assertIsNotNone(s[0])
        self.assertIsNotNone(s[1])
        self.assertIsNone(s[2])
        self.fill()
        self.write()
        s2 = vecstore.stamp(self.cfg, 'architect')
        self.assertIsNotNone(s2[2])
        self.assertEqual(s2[:2], s[:2])
        self.cfg.source_path = None
        self.assertIsNone(vecstore.stamp(self.cfg, 'architect')[1])
        os.remove(self.cfg.profile_db('architect'))
        self.assertIsNone(vecstore.stamp(self.cfg, 'architect')[0])

    def test_write_is_atomic_for_the_vector_file(self):
        self.fill()
        self.write()
        f = vecstore.profile_file(self.cfg, 'mdense', 'architect')
        self.assertFalse(os.path.exists(f + '.tmp'))
        self.assertFalse(os.path.exists(vecstore.side_file(self.cfg, 'mdense', 'architect') + '.tmp'))


@unittest.skipUnless(np is not None, 'needs numpy')
@helpers.needs_unix_sockets
class FakeDaemonRun(unittest.TestCase):
    """embed_all against the real fake daemon, started as its own process."""

    def test_full_run_through_the_socket(self):
        get_schema()
        home, cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, home)
        build_master(cfg, extra=20)
        built, hashes = build_profile(cfg, 'architect')
        env = dict(os.environ, LEKS_CONFIG=os.path.join(home, 'config.json'), LEKS_ALLOW_FAKE='1',
                   PYTHONPATH=REPO)
        code = 'import leks.config as c, leks.modeld as m; m.main(c.load())'
        proc = subprocess.Popen([sys.executable, '-c', code], cwd=REPO, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        client = Client.from_config(cfg)
        end = time.time() + 20
        while time.time() < end and not client.ping():
            client.reset()
            time.sleep(0.2)
        self.assertTrue(client.ping(), 'daemon did not come up')
        rc, lines = run_quiet(cfg)
        self.assertEqual(rc, 0, lines)
        self.assertTrue(lines[-1].startswith('DONE mdense '))
        mm, covered, why = vecstore.open_vectors(cfg, 'architect', built, cfg.embedder)
        self.assertIsNone(why)
        self.assertEqual(covered, len(hashes))
        # the run said bye, so the daemon leaves
        proc.wait(timeout=15)


if __name__ == '__main__':
    unittest.main()
