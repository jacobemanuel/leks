"""Start the model daemon if it is not up, then warm both models.

Runs before a search so the first search does not meet cold models. It
returns a word, prints nothing and never raises: a search must not hang or
fail because of this hook.
"""
from __future__ import annotations

import importlib.util
import json
import os
import site
import sys
import time

from leks import hooks, transport

STALE_LOCK = 120.0       # a start lock older than two minutes is a leftover
SOCKET_WAIT = 20.0       # how long the follow-up waits for the daemon
WARM_TIMEOUT = 90.0      # the first model load can be slow
MAX_REPLY = 3 * 1024 * 1024
BACKEND_MODULES = ('numpy', 'torch', 'transformers', 'safetensors')

# the daemon runs the package main with 'modeld serve' appended
DAEMON_STMT = "import runpy; runpy.run_module('leks', run_name='__main__')"
FOLLOW_STMT = 'from leks.hooks import warm_models; warm_models.child()'


def _lock_path(cfg):
    return os.path.join(cfg.run_dir(), 'start.lock')


def _ask(sock_path, req, timeout):
    """One JSON line out, one back. A dict, or None on any failure."""
    s = None
    try:
        s, token = transport.connect(sock_path, timeout)
        s.sendall((json.dumps(transport.with_token(req, token)) + '\n').encode('utf-8'))
        buf = b''
        while b'\n' not in buf and len(buf) < MAX_REPLY:
            part = s.recv(65536)
            if not part:
                break
            buf += part
        r = json.loads(buf.split(b'\n', 1)[0].decode('utf-8'))
        return r if isinstance(r, dict) else None
    except Exception:
        return None
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def _ping(cfg):
    """True when a daemon answers on the socket. A missing socket fails at once."""
    r = _ask(cfg.socket_path(), {'op': 'ping'}, 2.0)
    return r is not None and 'error' not in r


def _seen_by_daemon(name):
    """The module can be found, and not only in the user site (the daemon runs isolated, without it)."""
    spec = importlib.util.find_spec(name)
    if spec is None:
        return False
    where = spec.origin or ''
    if not where and spec.submodule_search_locations:
        where = list(spec.submodule_search_locations)[0]
    try:
        user = site.getusersitepackages()
    except Exception:
        user = ''
    return not (user and where.startswith(user))


def _backend_ok(cfg):
    """The model backend can be imported by the daemon (checked without importing it)."""
    names = ('numpy',) if cfg.backend == 'fake' else BACKEND_MODULES
    try:
        return all(_seen_by_daemon(m) for m in names)
    except Exception:
        return False


def _weights_ok(cfg):
    """At least one configured model has its weights on disk."""
    if cfg.backend == 'fake':
        return True
    try:
        from leks import models
        return any(models.weights_present(cfg, spec.key) for spec in (cfg.embedder, cfg.reranker) if spec)
    except Exception:
        return False


def _daemon_argv(cfg):
    """argv and environment of the daemon, wrapped by the sandbox when there is one."""
    from leks import sandbox
    inner = hooks.boot_argv(DAEMON_STMT, isolated=True) + ['modeld', 'serve']
    argv, _note = sandbox.command(cfg, inner)
    return argv, sandbox.clean_env(cfg)


def warm_requests(cfg):
    """One dummy request per configured model, reranker first."""
    reqs = []
    if cfg.reranker:
        reqs.append({'op': 'rerank', 'model': cfg.reranker.key, 'q': 'warm', 'docs': ['warm']})
    if cfg.embedder:
        reqs.append({'op': 'embed', 'model': cfg.embedder.key, 'texts': ['warm'], 'kind': 'q'})
    return reqs


def _wait_up(cfg, secs):
    end = time.time() + secs
    while time.time() < end:
        try:
            if transport.present(cfg.socket_path()) and _ping(cfg):
                return True
        except OSError:
            pass
        time.sleep(0.25)
    return False


def listening(cfg):
    """The daemon calls this once it listens: the start that spawned it is done, so its lock goes.

    The follow-up frees the lock too, but only once it has seen the daemon or waited SOCKET_WAIT.
    A quick bulk run can end the daemon with bye before the follow-up looks; the lock then stayed
    for SOCKET_WAIT seconds and every start in that time got 'locked' for a daemon that had left.
    """
    hooks.drop_lock(_lock_path(cfg))


def _after_start(cfg):
    """Wait for the socket, free the start lock, then warm the models."""
    try:
        up = _wait_up(cfg, SOCKET_WAIT)
    finally:
        # the daemon has freed it already when it came up; this covers a daemon that never did
        hooks.drop_lock(_lock_path(cfg))
    if not up:
        return
    for req in warm_requests(cfg):
        _ask(cfg.socket_path(), req, WARM_TIMEOUT)


def child():
    """Entry of the detached follow-up process."""
    try:
        from leks import config
        _after_start(config.load())
    except Exception:
        pass


def _start(cfg, wait):
    if _ping(cfg):
        return 'up'
    if cfg.embedder is None and cfg.reranker is None:
        return 'no models'
    if not _backend_ok(cfg):
        return 'no backend'
    if not _weights_ok(cfg):
        return 'no weights'
    os.makedirs(cfg.run_dir(), exist_ok=True)
    lock = _lock_path(cfg)
    if not hooks.take_lock(lock, STALE_LOCK):
        return 'locked'
    try:
        argv, env = _daemon_argv(cfg)
        ok = hooks.spawn(argv, env)
    except Exception:
        ok = False
    if not ok:
        hooks.drop_lock(lock)
        return 'refused'
    if wait:
        _after_start(cfg)
    elif not hooks.spawn(hooks.boot_argv(FOLLOW_STMT), hooks.child_env(cfg)):
        hooks.drop_lock(lock)
    return 'started'


def start(cfg, wait=False):
    """'up' | 'started' | 'no models' | 'no backend' | 'no weights' | 'locked' | 'refused'.

    Returns at once unless wait is set; then it also waits for the socket and warms the models.
    """
    try:
        return _start(cfg, wait)
    except Exception:
        return 'refused'


def main():
    """Hook entry. Prints nothing, always 0."""
    try:
        from leks import config
        start(config.load())
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
