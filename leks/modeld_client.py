"""Client for the local model daemon.

One JSON line goes out over a unix socket, one JSON line comes back. Nothing a
reply says is kept as text: a reply is either numbers of a known count and shape,
or no answer at all. When there is no answer the lane that wanted it closes and
the caller reads `Client.why` (always one of the fixed strings below).
"""
from __future__ import annotations

import base64
import importlib.util
import inspect
import json
import math
import os
import socket
import sys
import time

# fixed reasons, never text taken from a reply
WHY_NO_MODELS = 'no models configured'
WHY_NO_BACKEND = 'model backend not installed'
WHY_NO_NUMPY = 'numpy not installed'
WHY_NO_WEIGHTS = 'model weights not found'
WHY_NO_MODEL = 'model unavailable'
WHY_BUDGET = 'model over its time budget'

# no real score or vector value comes near this, a bigger number is a broken model
NUM_MAX = 1e4
MAX_REPLY = 2 * 1024 * 1024
COOL = 30.0
# room for 256 descriptors in one message, a cut message disables the client
ANC = socket.CMSG_SPACE(4 * 256)
BYE_BUDGET = 5.0
OPS = ('ping', 'bye', 'embed', 'rerank')
BACKEND_MODULES = ('torch', 'transformers', 'safetensors')
DEFAULT_BUDGETS = {'embed': 45.0, 'rerank': 8.0, 'cold_rerank': 30.0, 'cold_after': 600.0,
                   'connect': 10.0, 'ping': 2.0}

# socket paths whose daemon sent a cut control message: closed for the rest of the process
_DEAD = set()


def _have(name):
    """True when a module can be found. Nothing is imported."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _weights_present(cfg, key):
    """Cheap weights check from the models module, loaded lazily."""
    try:
        from . import models
        return bool(models.weights_present(cfg, key))
    except Exception:
        return False


def _no_constant(x):
    raise ValueError('non finite number in a reply')


def _pairs(items):
    out = {}
    for k, v in items:
        if k in out:
            raise ValueError('duplicate key in a reply')
        out[k] = v
    return out


def _vectors(r, n, dim, unit=False):
    """Exactly two keys, the asked shape, exactly that many bytes, all finite."""
    try:
        import numpy as np
    except ImportError:
        return None
    if not isinstance(r, dict) or set(r) != {'shape', 'b64'}:
        return None
    if not isinstance(r['b64'], str) or not isinstance(r['shape'], list) or len(r['shape']) != 2:
        return None
    a, b = r['shape']
    if type(a) is not int or type(b) is not int or a != n or b != dim:
        return None
    raw = n * dim * 4
    if len(r['b64']) != 4 * math.ceil(raw / 3):
        return None
    try:
        data = base64.b64decode(r['b64'], validate=True)
    except ValueError:
        return None
    if len(data) != raw:
        return None
    v = np.frombuffer(data, dtype='<f4').reshape(n, dim).astype('float32')
    if not np.isfinite(v).all() or np.abs(v).max(initial=0.0) > NUM_MAX:
        return None
    # a model that normalizes gives unit rows, anything else is doing something it should not
    if unit and n and not (np.abs(np.linalg.norm(v, axis=1) - 1.0) < 0.02).all():
        return None
    return v


def _scores(r, n):
    if not isinstance(r, dict) or set(r) != {'scores'}:
        return None
    s = r['scores']
    if not isinstance(s, list) or len(s) != n:
        return None
    out = []
    for x in s:
        if type(x) not in (int, float):
            return None
        try:
            x = float(x)
        except OverflowError:
            return None
        if not math.isfinite(x) or abs(x) > NUM_MAX:
            return None
        out.append(x)
    return out


def check_reply(req, r, specs):
    """Strict check of one parsed reply. True, an array, a list of floats, or None."""
    if not isinstance(r, dict) or not isinstance(req, dict):
        return None
    op = req.get('op')
    if op in ('ping', 'bye'):
        return True if r == {'shape': [0, 0], 'b64': ''} else None
    try:
        if op == 'embed':
            spec = specs[req['model']]
            return _vectors(r, len(req['texts']), int(spec.dim), getattr(spec, 'normalize', False) is True)
        if op == 'rerank':
            return _scores(r, len(req['docs']))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return None


def _takes_arg(fn):
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    ok = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD,
          inspect.Parameter.VAR_POSITIONAL)
    return any(p.kind in ok for p in params)


class Client:
    """Talks to the daemon. One instance per search engine or per embed run."""

    def __init__(self, socket_path, budgets, specs, ready=None, wake=None):
        self.socket_path = socket_path
        self.budgets = dict(DEFAULT_BUDGETS)
        for k, v in (budgets or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self.budgets[k] = float(v)
        self.specs = dict(specs or {})
        self._ready = ready
        self._ready_takes_key = ready is not None and _takes_arg(ready)
        # starts the daemon when nothing answers; returns a word of warm_models.start
        self._wake = wake
        self.why = WHY_NO_MODEL
        self.clock = time.monotonic       # tests swap this for the cool-down and the cold budget
        self._down_until = 0.0
        self._last_rerank = None

    @classmethod
    def from_config(cls, cfg, wake=False):
        """wake: an embed or rerank that finds no daemon starts one and waits for it within its budget.

        The search engine sets it, so a host without a warm-up hook still gets its models.
        Bulk runs leave it off: they start the daemon themselves and end it with bye.
        """
        specs = {s.key: s for s in (cfg.embedder, cfg.reranker) if s is not None}

        def ready(key=None):
            # fixed reasons, nothing imported (find_spec only looks)
            if not specs:
                return WHY_NO_MODELS
            if getattr(cfg, 'backend', 'torch') == 'fake':
                return None
            if not all(_have(m) for m in BACKEND_MODULES):
                return WHY_NO_BACKEND
            if key in specs:
                return None if _weights_present(cfg, key) else WHY_NO_WEIGHTS
            return None if any(_weights_present(cfg, k) for k in specs) else WHY_NO_WEIGHTS

        def start():
            from .hooks import warm_models
            return warm_models.start(cfg)

        return cls(cfg.socket_path(), cfg.budgets, specs, ready, start if wake else None)

    def reset(self):
        """Forget the cool-down (a run that waits for the daemon on purpose)."""
        self._down_until = 0.0

    def _cool(self):
        self._down_until = self.clock() + COOL

    def _not_ready(self, req):
        if self._ready is not None:
            try:
                w = self._ready(req.get('model')) if self._ready_takes_key else self._ready()
            except Exception:
                return WHY_NO_MODEL
            if isinstance(w, str) and w:
                return w
        if req.get('op') == 'embed' and not _have('numpy'):
            return WHY_NO_NUMPY
        return None

    def not_ready(self, op='embed', model=None):
        """The fixed reason this op would not even be tried now, None when it would. Never connects."""
        return self._not_ready({'op': op, 'model': model})

    def _default_budget(self, op):
        if op == 'bye':
            return BYE_BUDGET
        return self.budgets.get(op, 8.0)

    def _start_daemon(self):
        """Ask once for a daemon start. True when it is up or on its way."""
        try:
            return self._wake() in ('up', 'started', 'locked')
        except Exception:
            return False

    def _talk(self, payload, end, wake=False):
        """Send one line, read one line. None on any failure (why is set).

        wake: when the first connect finds no daemon, start one and keep trying within the connect budget.
        """
        t0 = time.monotonic()
        try_until = min(end, t0 + self.budgets['connect'])
        woke = not (wake and self._wake is not None)
        while True:
            c = socket.socket(socket.AF_UNIX)
            try:
                c.settimeout(max(0.05, min(2.0, end - time.monotonic())))
                c.connect(self.socket_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                c.close()
                if not woke:
                    woke = True
                    if not self._start_daemon():
                        self._cool()
                        return None
                if time.monotonic() >= try_until:
                    self._cool()
                    return None
                time.sleep(max(0.0, min(0.25, try_until - time.monotonic())))
            except Exception:
                c.close()
                self._cool()
                return None
        buf = bytearray()
        try:
            c.settimeout(max(0.05, end - time.monotonic()))
            c.sendall(payload)
            while b'\n' not in buf:
                left = end - time.monotonic()
                if left <= 0:
                    self.why = WHY_BUDGET
                    return None
                c.settimeout(left)
                b, anc, flags, _ = c.recvmsg(65536, ANC)
                # a daemon has no reason to hand over a descriptor: close each one, drop the reply
                got_fd = False
                for lvl, typ, data in anc or ():
                    if lvl == socket.SOL_SOCKET and typ == socket.SCM_RIGHTS:
                        got_fd = True
                        for i in range(0, len(data) - len(data) % 4, 4):
                            try:
                                os.close(int.from_bytes(data[i:i + 4], sys.byteorder))
                            except OSError:
                                pass
                if flags & socket.MSG_CTRUNC:
                    _DEAD.add(self.socket_path)
                    return None
                if got_fd or anc:
                    return None
                if not b:
                    return None
                buf += b
                if len(buf) > MAX_REPLY:
                    return None
        except socket.timeout:
            # alive but busy or loading, the daemon finishes the job so the next call may find it warm
            self.why = WHY_BUDGET
            return None
        except OSError:
            self._cool()
            return None
        finally:
            c.close()
        return bytes(buf)

    def call(self, req, budget=None):
        """One request. True (ping, bye), an array (embed), floats (rerank) or None."""
        self.why = WHY_NO_MODEL
        if not isinstance(req, dict) or req.get('op') not in OPS:
            return None
        if self.socket_path in _DEAD:
            return None
        w = self._not_ready(req)
        if w:
            self.why = w
            return None
        if self.clock() < self._down_until:
            return None
        op = req['op']
        if budget is None:
            budget = self._default_budget(op)
        try:
            payload = json.dumps(req).encode('ascii') + b'\n'
        except (TypeError, ValueError):
            return None
        # only model work may start the daemon, never ping or bye
        raw = self._talk(payload, time.monotonic() + budget, wake=op in ('embed', 'rerank'))
        if raw is None:
            return None
        if not raw.endswith(b'\n') or raw.count(b'\n') != 1:
            return None
        try:
            r = json.loads(raw, parse_constant=_no_constant, object_pairs_hook=_pairs)
        except (ValueError, RecursionError, OverflowError, TypeError):
            return None
        if isinstance(r, dict) and set(r) == {'error'}:
            # only a fixed mapping, the code itself is never shown
            if r['error'] == 'model_missing':
                self.why = WHY_NO_WEIGHTS
            return None
        return check_reply(req, r, self.specs)

    def ping(self):
        return self.call({'op': 'ping'}) is True

    def _key_for(self, op, model):
        if model is not None:
            return model
        for k, s in self.specs.items():
            if getattr(s, 'op', None) == op:
                return k
        return None

    def embed(self, texts, kind='q', model=None, budget=None):
        key = self._key_for('embed', model)
        if key is None:
            self.why = WHY_NO_MODELS
            return None
        if kind not in ('q', 'd') or not isinstance(texts, (list, tuple)) or not texts \
                or not all(isinstance(t, str) for t in texts):
            self.why = WHY_NO_MODEL
            return None
        return self.call({'op': 'embed', 'model': key, 'texts': list(texts), 'kind': kind}, budget)

    def rerank(self, q, docs, model=None, budget=None):
        key = self._key_for('maxsim', model)
        if key is None:
            self.why = WHY_NO_MODELS
            return None
        if not isinstance(q, str) or not isinstance(docs, (list, tuple)) or not docs \
                or not all(isinstance(d, str) for d in docs):
            self.why = WHY_NO_MODEL
            return None
        return self.call({'op': 'rerank', 'model': key, 'q': q, 'docs': list(docs)}, budget)

    def rerank_budget(self):
        """Long wait on the first call and after a quiet spell, short after that."""
        now = self.clock()
        cold = self._last_rerank is None or now - self._last_rerank > self.budgets['cold_after']
        self._last_rerank = now
        return self.budgets['cold_rerank'] if cold else self.budgets['rerank']

    def bye(self):
        self.call({'op': 'bye'}, BYE_BUDGET)
