"""Model daemon: one shared process that holds the two models.

Several agent sessions can search at once without each loading the weights.
The daemon holds no notes. It turns text it is given into numbers and hands
only numbers back.

Protocol: one JSON line in, one JSON line out, then the server closes
(section 3.5 of the spec). The line goes over a unix socket, or over
127.0.0.1 with a token where there are no unix sockets (see transport.py).
Replies are only
  {"shape": [n, d], "b64": "..."}   embed (ping and bye answer shape [0, 0])
  {"scores": [...]}                 rerank
  {"error": "<fixed code>"}         never any free text
Only the configured embedder and reranker keys load. A message that carries
file descriptors is refused and they are closed. Over tcp a request without
the right token gets {"error": "denied"} and nothing else.
"""
from __future__ import annotations

import array
import base64
import hmac
import json
import math
import os
import secrets
import signal
import socket
import stat
import sys
import threading
import time

from leks import transport

MAX_LINE = 3 * 1024 * 1024
MAX_TEXTS = 64
MAX_CHARS = 8000
MAX_Q = 2000
MAX_CONN = 32
READ_DEADLINE = 30.0
ERR = frozenset({'bad_request', 'bad_input', 'too_big', 'unknown_op', 'model_refused',
                 'model_missing', 'model_check', 'busy', 'internal', 'denied'})
PING_REPLY = {'shape': [0, 0], 'b64': ''}

# unix sockets with descriptor passing; without them (windows) the daemon listens on tcp
UNIX_OK = transport.UNIX_OK
# room for 256 passed descriptors: a message carries up to 253, all of them are
# installed before the control data is cut, so the buffer must hold them all
ANC = socket.CMSG_SPACE(256 * array.array('i').itemsize) if UNIX_OK else 0


class Refuse(Exception):
    """Carries one fixed error code."""


class _Slot:
    def __init__(self):
        self.key = None
        self.obj = None


_cfg = None
_backend = None
_slots = {'embedder': _Slot(), 'reranker': _Slot()}
_lock = threading.Lock()                    # model work, one request at a time
_sem = threading.BoundedSemaphore(MAX_CONN)
_last = time.time()
_leaving = False
_threads = set()
_token = None                               # tcp only: every request has to carry it


def configure(cfg):
    """Set the config the handlers use. main() calls this; tests can too."""
    global _cfg, _backend, _last, _leaving, _token
    _cfg = cfg
    _token = None
    _backend = None
    _last = time.time()
    _leaving = False
    for s in _slots.values():
        s.key = s.obj = None


def _be():
    global _backend
    if _backend is None:
        from leks import backends
        _backend = backends.load_backend(_cfg.backend)
    return _backend


def _spec_for(role, model):
    """The only gate: the configured spec for the role, and the model name must match it."""
    spec = getattr(_cfg, role, None)
    if spec is None:
        raise Refuse('model_refused')
    if model is not None and model != spec.key:
        raise Refuse('model_refused')
    return spec


def _build(role, spec):
    from leks import models
    path = None
    try:
        if _cfg.backend != 'fake':
            models.check(_cfg, spec.key)
            path = os.path.join(_cfg.weights_dir, spec.key)
        be = _be()
        cls = be.Enc if role == 'embedder' else be.MaxSim
        return cls(path, spec, _cfg.device)
    except models.ModelMissing:
        raise Refuse('model_missing') from None
    except Exception as e:
        raise Refuse('model_check') from e


def _model(role, model):
    """The loaded model of a role. The old one is released before a new one loads."""
    spec = _spec_for(role, model)
    slot = _slots[role]
    if slot.key != spec.key or slot.obj is None:
        slot.key = slot.obj = None
        if _backend is not None:
            _backend.release()
        slot.obj = _build(role, spec)
        slot.key = spec.key
    return slot.obj


def _texts_ok(t, limit):
    return isinstance(t, list) and len(t) <= limit and all(isinstance(x, str) and len(x) <= MAX_CHARS for x in t)


def handle(req):
    """Run one parsed request. Raises Refuse with a fixed code."""
    if _cfg is None:
        raise Refuse('internal')
    op = req.get('op')
    model = req.get('model')
    if op in ('embed', 'rerank') and model is not None and not isinstance(model, str):
        raise Refuse('model_refused')
    if op == 'embed':
        texts, kind = req.get('texts'), req.get('kind', 'd')
        if not _texts_ok(texts, MAX_TEXTS) or not texts or kind not in ('q', 'd'):
            raise Refuse('bad_input')
        enc = _model('embedder', model)
        v = enc(texts, kind=kind)
        if getattr(v, 'ndim', 0) != 2 or v.shape[0] != len(texts):
            raise Refuse('internal')
        v = v.astype('<f4', copy=False)
        return {'shape': [int(v.shape[0]), int(v.shape[1])], 'b64': base64.b64encode(v.tobytes()).decode('ascii')}
    if op == 'rerank':
        q, docs = req.get('q'), req.get('docs')
        if not isinstance(q, str) or len(q) > MAX_Q or not _texts_ok(docs, MAX_TEXTS):
            raise Refuse('bad_input')
        rer = _model('reranker', model)
        if not docs:
            return {'scores': []}
        scores = [float(x) for x in rer.rerank(q, docs)]
        if len(scores) != len(docs) or not all(math.isfinite(x) for x in scores):
            raise Refuse('internal')
        return {'scores': scores}
    raise Refuse('unknown_op')


def _token_ok(got):
    return isinstance(got, str) and hmac.compare_digest(got.encode('utf-8'), _token.encode('utf-8'))


def reply_for(line):
    """The reply dict for one raw request line. Never raises."""
    global _last, _leaving
    if len(line) > MAX_LINE or not line.endswith(b'\n'):
        return {'error': 'too_big'}
    if line.count(b'\n') != 1:
        return {'error': 'bad_request'}
    try:
        req = json.loads(line)
    except (ValueError, RecursionError):
        return {'error': 'bad_request'}
    if not isinstance(req, dict):
        return {'error': 'bad_request'}
    if _token is not None and not _token_ok(req.get('token')):
        # tcp: anyone on this machine can connect, only the holder of the endpoint file may ask
        return {'error': 'denied'}
    op = req.get('op')
    if op == 'ping':
        # answers right away, even while a model loads or works
        return dict(PING_REPLY)
    if op == 'bye':
        # end of a bulk run: a client can only end the daemon, the next search starts a fresh one
        _leaving = True
        return dict(PING_REPLY)
    try:
        with _lock:
            out = handle(req)
            _last = time.time()
        return out
    except Refuse as e:
        _last = time.time()
        return {'error': str(e) if str(e) in ERR else 'internal'}
    except BaseException:
        # whatever fails inside the model stack stays here: its words could carry anything.
        # BaseException because a panic from the tokenizer is one
        return {'error': 'internal'}


def read_line(conn):
    """Read one request line. None when the message carried descriptors."""
    buf = bytearray()
    end = time.monotonic() + READ_DEADLINE
    # tcp carries no descriptors, a plain recv is enough there
    unix = bool(ANC) and conn.family == getattr(socket, 'AF_UNIX', None)
    while len(buf) <= MAX_LINE and b'\n' not in buf and time.monotonic() < end:
        if not unix:
            data = conn.recv(65536)
            if not data:
                break
            buf += data
            continue
        data, anc, flags, _ = conn.recvmsg(65536, ANC)
        if flags & socket.MSG_CTRUNC:
            # descriptors we cannot see and cannot close: only the end of this process closes them
            os._exit(3)
        if anc:
            for level, kind, payload in anc:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    fds = array.array('i')
                    fds.frombytes(payload[:len(payload) - len(payload) % fds.itemsize])
                    for fd in fds:
                        os.close(fd)
            return None
        if not data:
            break
        buf += data
    return bytes(buf)


def serve(conn):
    try:
        conn.settimeout(READ_DEADLINE)
        line = read_line(conn)
        if line is None:
            conn.sendall(b'{"error": "bad_request"}\n')
        elif line:
            conn.sendall(json.dumps(reply_for(line)).encode('ascii') + b'\n')
    except OSError:
        pass
    finally:
        conn.close()
        _threads.discard(threading.current_thread())
        _sem.release()


def _another_daemon_runs(path):
    """True when a live daemon owns the path. A stale socket file is removed."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISSOCK(st.st_mode):
        sys.exit(1)
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(1)
        s.connect(path)
        s.close()
        return True
    except OSError:
        os.remove(path)
        return False


def _tcp_daemon_runs(path):
    """True when a live tcp daemon answers with the token of the endpoint file. A stale file is removed."""
    where = transport.endpoint_path(path)
    if not os.path.lexists(where):
        return False
    ep = transport.read_endpoint(path)
    if ep is None:
        # a file we did not write or cannot trust: leave it alone, like a non-socket in unix mode
        sys.exit(1)
    try:
        with socket.create_connection((transport.HOST, ep[0]), timeout=1) as s:
            s.sendall(json.dumps({'op': 'ping', 'token': ep[1]}).encode('ascii') + b'\n')
            buf = b''
            while b'\n' not in buf and len(buf) < 256:
                part = s.recv(256)
                if not part:
                    break
                buf += part
        if json.loads(buf.split(b'\n', 1)[0]) == PING_REPLY:
            return True
    except (OSError, ValueError):
        pass
    try:
        os.remove(where)
    except OSError:
        pass
    return False


def _free_start_lock(cfg):
    # listening now: the start that spawned us is done, so the next start is not held off
    try:
        from leks.hooks import warm_models
        warm_models.listening(cfg)
    except Exception:
        pass


def _leave(*_):
    global _leaving
    _leaving = True


def main(cfg=None):
    """Bind, serve, leave after daemon.idle_exit quiet seconds or on bye."""
    global _last, _token
    if cfg is None:
        from leks import config
        cfg = config.load()
    configure(cfg)
    path = cfg.socket_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    os.environ.setdefault('TORCHINDUCTOR_CACHE_DIR', os.path.join(cfg.run_dir(), 'cache'))
    tcp = transport.kind() == 'tcp'
    if (_tcp_daemon_runs if tcp else _another_daemon_runs)(path):
        return
    idle = float((cfg.daemon or {}).get('idle_exit', 3600) or 0)
    tick = min(1.0, max(0.05, idle / 4)) if idle > 0 else 1.0
    mine = None
    if tcp:
        srv = transport.listen_tcp()
        _token = secrets.token_hex(32)
        transport.write_endpoint(path, srv.getsockname()[1], _token)
    else:
        srv = socket.socket(socket.AF_UNIX)
        old = os.umask(0o177)
        try:
            srv.bind(path)
        finally:
            os.umask(old)
        srv.listen(16)
        mine = os.stat(path).st_ino
    _free_start_lock(cfg)
    srv.settimeout(tick)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _leave)
        except ValueError:
            pass            # not the main thread
    _last = time.time()
    while True:
        if not _lock.locked() and (_leaving or (idle > 0 and time.time() - _last > idle)):
            break
        try:
            c, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        if not _sem.acquire(blocking=False):
            try:
                c.sendall(b'{"error": "busy"}\n')
            except OSError:
                pass
            c.close()
            continue
        t = threading.Thread(target=serve, args=(c,), daemon=True)
        _threads.add(t)
        t.start()
    srv.close()
    # let a reply that is still being written (the bye answer) go out
    for t in list(_threads):
        t.join(1.0)
    # a later daemon may own the path by now
    if tcp:
        transport.drop_endpoint(path, _token)
    else:
        try:
            if os.stat(path).st_ino == mine:
                os.remove(path)
        except OSError:
            pass
    # a plain exit waits on the gpu stack's teardown, this gives the memory back at once
    os._exit(0)


if __name__ == '__main__':
    main()
