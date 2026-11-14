"""The tcp transport of the model daemon (the windows path), tested on any system.

LEKS_MODELD_TRANSPORT=tcp forces it, so macOS and Linux run the same code a
windows python takes. Tests that need a real listening port skip where the
local sandbox refuses to bind one; CI runs them on every system.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from leks import hooks, modeld, modeld_client, sandbox, transport
from leks.hooks import warm_models
from tests import helpers

try:
    import numpy as np
except ImportError:
    np = None

POSIX = os.name == 'posix'
TOKEN = 'ab' * 32
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOT = "import sys; sys.path.insert(0, %r); from leks import modeld; modeld.main()" % ROOT


def _can_bind():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        s.bind(('127.0.0.1', 0))
        return True
    except OSError:
        return False
    finally:
        s.close()


BIND_OK = _can_bind()
NO_BIND = 'the local sandbox refuses to bind a tcp port here; CI runs this test'
TCP = {transport.ENV: 'tcp'}


def socket_without_unix():
    """The socket module the way windows python has it: no AF_UNIX, no CMSG_SPACE, no SCM_RIGHTS."""
    fake = types.ModuleType('socket')
    for k in dir(socket):
        if k not in ('AF_UNIX', 'CMSG_SPACE', 'CMSG_LEN', 'SCM_RIGHTS'):
            setattr(fake, k, getattr(socket, k))
    return fake


def socket_without_recvmsg():
    """AF_UNIX and CMSG_SPACE there, but a socket class without recvmsg."""
    fake = types.ModuleType('socket')
    for k in dir(socket):
        setattr(fake, k, getattr(socket, k))
    fake.AF_UNIX = getattr(socket, 'AF_UNIX', 1)
    fake.CMSG_SPACE = getattr(socket, 'CMSG_SPACE', lambda n: n)

    class NoRecvmsg:
        """stands in for socket.socket, only the missing method matters at import"""

    fake.socket = NoRecvmsg
    return fake


def fresh_import(name, sock_module):
    """Run leks.<name> again as a separate module object that sees sock_module as socket."""
    spec = importlib.util.find_spec('leks.' + name)
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {'socket': sock_module}):
        spec.loader.exec_module(mod)
    return mod


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='lkt')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.sock = os.path.join(self.dir, 'modeld.sock')


class KindTests(unittest.TestCase):
    def test_setting_forces_tcp(self):
        with mock.patch.dict(os.environ, {transport.ENV: ' TCP '}):
            self.assertEqual(transport.kind(), 'tcp')

    def test_default_follows_the_system(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(transport.ENV, None)
            self.assertEqual(transport.kind(), 'unix' if transport.UNIX_OK else 'tcp')
            with mock.patch.object(transport, 'UNIX_OK', False):
                self.assertEqual(transport.kind(), 'tcp')
            # any other value keeps the default
            os.environ[transport.ENV] = 'unix'
            with mock.patch.object(transport, 'UNIX_OK', False):
                self.assertEqual(transport.kind(), 'tcp')

    def test_windows_socket_module_takes_tcp(self):
        for fake in (socket_without_unix(), socket_without_recvmsg()):
            t = fresh_import('transport', fake)
            self.assertFalse(t.UNIX_OK)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(transport.ENV, None)
                self.assertEqual(t.kind(), 'tcp')

    def test_daemon_and_client_import_without_unix_sockets(self):
        with mock.patch.object(transport, 'UNIX_OK', False):
            md = fresh_import('modeld', socket_without_unix())
            mc = fresh_import('modeld_client', socket_without_unix())
        self.assertEqual((md.ANC, mc.ANC), (0, 0))

    def test_this_python(self):
        want = (hasattr(socket, 'AF_UNIX') and hasattr(socket, 'CMSG_SPACE')
                and hasattr(socket.socket, 'recvmsg'))
        self.assertEqual(transport.UNIX_OK, want)
        self.assertEqual(modeld.UNIX_OK, want)
        self.assertEqual(modeld_client.UNIX_OK, want)


class EndpointFileTests(TmpCase):
    def test_path_next_to_the_socket(self):
        self.assertEqual(transport.endpoint_path(os.path.join('r', 'modeld.sock')), os.path.join('r', 'modeld.port'))
        self.assertEqual(transport.endpoint_path(os.path.join('r', 'm')), os.path.join('r', 'm.port'))

    def test_round_trip(self):
        path = transport.write_endpoint(self.sock, 50123, TOKEN)
        self.assertEqual(path, os.path.join(self.dir, 'modeld.port'))
        self.assertEqual(transport.read_endpoint(self.sock), (50123, TOKEN))
        with open(path, encoding='ascii') as f:
            d = json.load(f)
        self.assertEqual(d['pid'], os.getpid())
        self.assertFalse(os.path.exists(path + '.tmp'))
        self.assertTrue(transport.present(self.sock, 'tcp'))

    @unittest.skipUnless(POSIX, 'file modes exist on posix only')
    def test_mode_is_user_only(self):
        path = transport.write_endpoint(self.sock, 50123, TOKEN)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        os.chmod(path, 0o644)
        self.assertIsNone(transport.read_endpoint(self.sock))

    @unittest.skipUnless(POSIX, 'symlinks need extra rights on windows')
    def test_symlink_is_refused(self):
        real = os.path.join(self.dir, 'real.json')
        fd = os.open(real, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump({'port': 50123, 'token': TOKEN}, f)
        os.symlink(real, transport.endpoint_path(self.sock))
        self.assertIsNone(transport.read_endpoint(self.sock))

    def test_bad_contents(self):
        path = transport.endpoint_path(self.sock)
        for body in ('not json', '[]', '{"port": 0, "token": "%s"}' % TOKEN,
                     '{"port": 70000, "token": "%s"}' % TOKEN, '{"port": "1", "token": "%s"}' % TOKEN,
                     '{"port": true, "token": "%s"}' % TOKEN, '{"port": 5, "token": "short"}',
                     '{"port": 5, "token": 7}', '{"port": 5}', 'x' * (transport.MAX_FILE + 1)):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(body)
            self.assertIsNone(transport.read_endpoint(self.sock), body[:40])
        os.remove(path)
        self.assertIsNone(transport.read_endpoint(self.sock))
        self.assertFalse(transport.present(self.sock, 'tcp'))

    def test_leftover_tmp_is_replaced(self):
        tmp = transport.endpoint_path(self.sock) + '.tmp'
        with open(tmp, 'w') as f:
            f.write('half')
        transport.write_endpoint(self.sock, 50124, TOKEN)
        self.assertEqual(transport.read_endpoint(self.sock), (50124, TOKEN))

    def test_drop_only_with_the_own_token(self):
        transport.write_endpoint(self.sock, 50123, TOKEN)
        transport.drop_endpoint(self.sock, 'cd' * 32)
        self.assertIsNotNone(transport.read_endpoint(self.sock))
        transport.drop_endpoint(self.sock, TOKEN)
        self.assertIsNone(transport.read_endpoint(self.sock))
        transport.drop_endpoint(self.sock, TOKEN)        # gone already: nothing raises

    def test_connect_without_endpoint(self):
        with self.assertRaises(FileNotFoundError):
            transport.connect(self.sock, 1.0, 'tcp')

    def test_with_token(self):
        req = {'op': 'ping'}
        self.assertIs(transport.with_token(req, None), req)
        self.assertEqual(transport.with_token(req, TOKEN), {'op': 'ping', 'token': TOKEN})
        self.assertEqual(req, {'op': 'ping'})


class DaemonTokenTests(unittest.TestCase):
    """reply_for over tcp: the token gates every op, ping and bye included."""

    def setUp(self):
        modeld.configure(SimpleNamespace(backend='fake', embedder=None, reranker=None))
        self.addCleanup(modeld.configure, None)

    def ask(self, req):
        return modeld.reply_for(json.dumps(req).encode() + b'\n')

    def test_unix_mode_needs_no_token(self):
        self.assertEqual(self.ask({'op': 'ping'}), modeld.PING_REPLY)

    def test_tcp_mode_checks_the_token(self):
        modeld._token = TOKEN
        for bad in (None, '', 'cd' * 32, TOKEN[:-1], 5, ['x'], '\u00e9' * 64):
            req = {'op': 'ping'} if bad is None else {'op': 'ping', 'token': bad}
            self.assertEqual(self.ask(req), {'error': 'denied'}, repr(bad))
        self.assertEqual(self.ask({'op': 'bye', 'token': 'cd' * 32}), {'error': 'denied'})
        self.assertFalse(modeld._leaving)
        self.assertEqual(self.ask({'op': 'ping', 'token': TOKEN}), modeld.PING_REPLY)
        self.assertIn('denied', modeld.ERR)

    def test_configure_clears_the_token(self):
        modeld._token = TOKEN
        modeld.configure(SimpleNamespace(backend='fake', embedder=None, reranker=None))
        self.assertIsNone(modeld._token)

    def test_read_line_uses_recv_over_tcp(self):
        class Conn:
            family = socket.AF_INET

            def __init__(self):
                self.parts = [b'{"op": ', b'"ping"}\n']

            def recv(self, n):
                return self.parts.pop(0) if self.parts else b''

        self.assertEqual(modeld.read_line(Conn()), b'{"op": "ping"}\n')

    def test_stale_endpoint_is_removed(self):
        d = tempfile.mkdtemp(prefix='lkt')
        self.addCleanup(shutil.rmtree, d, True)
        sock = os.path.join(d, 'modeld.sock')
        self.assertFalse(modeld._tcp_daemon_runs(sock))
        transport.write_endpoint(sock, 9, TOKEN)
        with mock.patch.object(modeld.socket, 'create_connection', side_effect=ConnectionRefusedError):
            self.assertFalse(modeld._tcp_daemon_runs(sock))
        self.assertFalse(os.path.exists(transport.endpoint_path(sock)))

    def test_untrusted_endpoint_stops_the_start(self):
        d = tempfile.mkdtemp(prefix='lkt')
        self.addCleanup(shutil.rmtree, d, True)
        sock = os.path.join(d, 'modeld.sock')
        with open(transport.endpoint_path(sock), 'w') as f:
            f.write('{}')
        with self.assertRaises(SystemExit):
            modeld._tcp_daemon_runs(sock)
        self.assertTrue(os.path.exists(transport.endpoint_path(sock)))


def _pair():
    """A connected socket pair. AF_UNIX where there is one; windows builds it over tcp itself."""
    return socket.socketpair()


class FakeTcpServer:
    """Answers one line on the far end of a socket pair, records the request."""

    def __init__(self, reply):
        self.here, self.there = _pair()
        self.got = None
        self.t = threading.Thread(target=self._run, args=(reply,), daemon=True)
        self.t.start()

    def _run(self, reply):
        buf = b''
        while b'\n' not in buf:
            b = self.there.recv(65536)
            if not b:
                return
            buf += b
        self.got = json.loads(buf.split(b'\n', 1)[0])
        self.there.sendall(json.dumps(reply).encode() + b'\n')
        self.there.close()


class ClientOverTcpTests(TmpCase):
    """The client and the warm hook over the tcp path, with the connect replaced by a socket pair."""

    def test_client_sends_the_token_and_reads_with_recv(self):
        srv = FakeTcpServer({'scores': [0.5]})
        spec = SimpleNamespace(key='rr', op='maxsim')
        cl = modeld_client.Client(self.sock, {}, {'rr': spec})
        with mock.patch.dict(os.environ, TCP), \
                mock.patch.object(transport, 'connect', return_value=(srv.here, TOKEN)) as conn:
            self.assertEqual(cl.rerank('q', ['a']), [0.5])
        srv.t.join(5)
        self.assertEqual(srv.got, {'op': 'rerank', 'model': 'rr', 'q': 'q', 'docs': ['a'], 'token': TOKEN})
        self.assertEqual(conn.call_args[0][0], self.sock)
        self.assertEqual(conn.call_args[0][2], 'tcp')

    def test_client_without_endpoint_wakes_the_daemon(self):
        woke = []
        spec = SimpleNamespace(key='rr', op='maxsim')
        cl = modeld_client.Client(self.sock, {'connect': 0.3}, {'rr': spec}, wake=lambda: woke.append(1) or 'locked')
        with mock.patch.dict(os.environ, TCP):
            self.assertIsNone(cl.call({'op': 'rerank', 'model': 'rr', 'q': 'q', 'docs': ['a']}, budget=0.5))
        self.assertEqual(woke, [1])

    def test_client_is_ready_without_unix_sockets(self):
        spec = SimpleNamespace(key='rr', op='maxsim')
        cl = modeld_client.Client(self.sock, {}, {'rr': spec}, ready=lambda key=None: None)
        with mock.patch.object(transport, 'UNIX_OK', False):
            self.assertIsNone(cl.not_ready('rerank'))

    def test_warm_ask_sends_the_token(self):
        srv = FakeTcpServer(modeld.PING_REPLY)
        with mock.patch.dict(os.environ, TCP), \
                mock.patch.object(transport, 'connect', return_value=(srv.here, TOKEN)):
            self.assertEqual(warm_models._ask(self.sock, {'op': 'ping'}, 2.0), modeld.PING_REPLY)
        srv.t.join(5)
        self.assertEqual(srv.got, {'op': 'ping', 'token': TOKEN})

    def test_wait_up_looks_for_the_endpoint_file(self):
        cfg = SimpleNamespace(socket_path=lambda: self.sock)
        with mock.patch.dict(os.environ, TCP), mock.patch.object(warm_models, '_ping', return_value=True):
            self.assertFalse(warm_models._wait_up(cfg, 0.3))
            transport.write_endpoint(self.sock, 50123, TOKEN)
            self.assertTrue(warm_models._wait_up(cfg, 0.3))

    def test_warm_start_spawns_without_unix_sockets(self):
        # before the tcp path a windows python got 'refused' here and never had models
        cfg = SimpleNamespace(embedder=SimpleNamespace(key='emb'), reranker=None, home=self.dir, source_path=None,
                              run_dir=lambda: self.dir, socket_path=lambda: self.sock)
        with mock.patch.object(transport, 'UNIX_OK', False), \
                mock.patch.object(warm_models, '_ping', return_value=False), \
                mock.patch.object(warm_models, '_backend_ok', return_value=True), \
                mock.patch.object(warm_models, '_weights_ok', return_value=True), \
                mock.patch.object(warm_models, '_daemon_argv', return_value=([sys.executable], {})), \
                mock.patch.object(warm_models.hooks, 'spawn', return_value=True) as spawn:
            self.assertEqual(warm_models.start(cfg), 'started')
        self.assertEqual(spawn.call_count, 2)


class WindowsSpawnTests(unittest.TestCase):
    def test_posix_options(self):
        with mock.patch.object(hooks.os, 'name', 'posix'):
            self.assertEqual(hooks._detach_options(), [{'start_new_session': True}])

    def test_windows_tries_breakaway_first(self):
        with mock.patch.object(hooks.os, 'name', 'nt'):
            opts = hooks._detach_options()
        base = hooks.DETACHED_PROCESS | hooks.CREATE_NEW_PROCESS_GROUP
        self.assertEqual(opts, [{'creationflags': base | hooks.CREATE_BREAKAWAY_FROM_JOB},
                                {'creationflags': base}])
        # the values the win32 docs give
        self.assertEqual((hooks.DETACHED_PROCESS, hooks.CREATE_NEW_PROCESS_GROUP, hooks.CREATE_BREAKAWAY_FROM_JOB),
                         (0x8, 0x200, 0x1000000))
        for name in ('DETACHED_PROCESS', 'CREATE_NEW_PROCESS_GROUP', 'CREATE_BREAKAWAY_FROM_JOB'):
            if hasattr(subprocess, name):
                self.assertEqual(getattr(subprocess, name), getattr(hooks, name))

    def test_job_without_breakaway_falls_back(self):
        calls = []

        def popen(argv, **kw):
            calls.append(kw)
            if kw.get('creationflags', 0) & hooks.CREATE_BREAKAWAY_FROM_JOB:
                raise PermissionError(5, 'Access is denied')
            return mock.Mock()

        with mock.patch.object(hooks, '_detach_options',
                               return_value=[{'creationflags': 0x1000208}, {'creationflags': 0x208}]), \
                mock.patch('subprocess.Popen', side_effect=popen):
            self.assertTrue(hooks.spawn(['prog'], {'A': 'b'}))
        self.assertEqual([c['creationflags'] for c in calls], [0x1000208, 0x208])
        self.assertNotIn('start_new_session', calls[0])
        self.assertEqual(calls[1]['cwd'], os.path.abspath(os.sep))

    def test_nothing_starts(self):
        with mock.patch('subprocess.Popen', side_effect=OSError):
            self.assertFalse(hooks.spawn(['prog']))


class SandboxEnvTests(TmpCase):
    def cfg(self):
        return SimpleNamespace(home=self.dir, source_path=None, daemon={},
                               run_dir=lambda: os.path.join(self.dir, 'run'), socket_path=lambda: self.sock)

    def test_transport_setting_reaches_the_daemon(self):
        with mock.patch.dict(os.environ, TCP):
            self.assertEqual(sandbox.clean_env(self.cfg())[transport.ENV], 'tcp')
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(transport.ENV, None)
            self.assertNotIn(transport.ENV, sandbox.clean_env(self.cfg()))

    def test_windows_additions(self):
        env = {'TMPDIR': 'T', 'HOME': 'H', 'PATH': '/usr/bin:/bin'}
        with mock.patch.dict(os.environ, {'SYSTEMROOT': 'C:\\Windows', 'WINDIR': 'C:\\Windows',
                                          'SECRET_TOKEN': 'x'}):
            out = sandbox._windows_env(env)
        self.assertEqual(out['SYSTEMROOT'], 'C:\\Windows')
        self.assertEqual((out['TEMP'], out['TMP'], out['USERPROFILE']), ('T', 'T', 'H'))
        self.assertIn(os.path.dirname(sys.executable), out['PATH'].split(os.pathsep))
        self.assertNotIn('SECRET_TOKEN', out)

    def test_profile_for_tcp(self):
        with mock.patch.dict(os.environ, TCP):
            text = sandbox.profile_text(self.cfg(), sys.executable)
        self.assertIn('(allow network-bind network-inbound (local tcp "localhost:*"))', text)
        self.assertNotIn('unix-socket', text)
        ep = os.path.join(os.path.realpath(self.dir), 'modeld.port')
        self.assertIn('(literal %s)' % sandbox._q(ep), text)
        self.assertIn('(literal %s)' % sandbox._q(ep + '.tmp'), text)
        self.assertNotIn('remote', text)


def _daemon_env(home, cfg):
    env = dict(os.environ)
    env.update({'LEKS_CONFIG': cfg.source_path, 'LEKS_HOME': cfg.home, 'LEKS_ALLOW_FAKE': '1',
                transport.ENV: 'tcp'})
    env.pop('LEKS_MODELD_SOCK', None)
    return env


@unittest.skipUnless(BIND_OK, NO_BIND)
@unittest.skipIf(np is None, 'numpy not installed')
class RealTcpDaemonTests(unittest.TestCase):
    """A real daemon on 127.0.0.1, the way it runs on windows."""

    def setUp(self):
        self.home, self.cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        p = mock.patch.dict(os.environ, TCP)
        p.start()
        self.addCleanup(p.stop)
        os.makedirs(self.cfg.run_dir(), exist_ok=True)
        self.sock = self.cfg.socket_path()
        self.procs = []
        self.addCleanup(self._stop)

    def _stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()

    def start(self):
        p = subprocess.Popen([sys.executable, '-c', BOOT], env=_daemon_env(self.home, self.cfg),
                             cwd=os.path.abspath(os.sep), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def wait_up(self, p, secs=30.0):
        end = time.time() + secs
        while time.time() < end:
            if p.poll() is not None:
                raise AssertionError('daemon exited early with %s' % p.returncode)
            if warm_models._ask(self.sock, {'op': 'ping'}, 2.0) == modeld.PING_REPLY:
                return
            time.sleep(0.05)
        raise AssertionError('daemon did not answer in time')

    def raw(self, req):
        port, _ = transport.read_endpoint(self.sock)
        with socket.create_connection(('127.0.0.1', port), timeout=10) as s:
            s.sendall(json.dumps(req).encode() + b'\n')
            buf = b''
            while b'\n' not in buf:
                b = s.recv(65536)
                if not b:
                    break
                buf += b
        return json.loads(buf.split(b'\n', 1)[0])

    def test_serve_with_token_and_leave(self):
        p = self.start()
        self.wait_up(p)
        ep = transport.read_endpoint(self.sock)
        self.assertIsNotNone(ep)
        self.assertFalse(os.path.exists(self.sock))
        if POSIX:
            self.assertEqual(stat.S_IMODE(os.stat(transport.endpoint_path(self.sock)).st_mode), 0o600)
        self.assertEqual(self.raw({'op': 'ping'}), {'error': 'denied'})
        self.assertEqual(self.raw({'op': 'bye', 'token': 'cd' * 32}), {'error': 'denied'})
        self.assertEqual(self.raw({'op': 'ping', 'token': ep[1]}), modeld.PING_REPLY)
        cl = modeld_client.Client.from_config(self.cfg)
        self.assertTrue(cl.ping())
        v = cl.embed(['tile cache'], kind='q')
        self.assertEqual(v.shape, (1, 64))
        self.assertEqual(len(cl.rerank('tile', ['tile cache', 'shader blobs'])), 2)
        # a second daemon sees the first one and leaves the endpoint alone
        p2 = self.start()
        self.assertEqual(p2.wait(timeout=30), 0)
        self.assertEqual(transport.read_endpoint(self.sock), ep)
        cl.bye()
        self.assertEqual(p.wait(timeout=30), 0)
        self.assertFalse(os.path.exists(transport.endpoint_path(self.sock)))

    def test_warm_start_brings_it_up(self):
        # the same path a session hook takes: spawn detached (sandboxed on macOS), wait, warm
        got = warm_models.start(self.cfg, wait=True)
        self.assertEqual(got, 'started')
        cl = modeld_client.Client.from_config(self.cfg)
        self.assertTrue(cl.ping())
        cl.bye()
        end = time.time() + 30
        while time.time() < end and os.path.exists(transport.endpoint_path(self.sock)):
            time.sleep(0.1)
        self.assertFalse(os.path.exists(transport.endpoint_path(self.sock)))


if __name__ == '__main__':
    unittest.main()
