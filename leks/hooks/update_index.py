"""Bring the index up to date in the background, at most once per interval.

Runs at session start. It returns a word at once and prints nothing. The
work happens in a detached child that holds a directory lock, writes a
rotated log under the run folder and touches a stamp when it is done.
"""
from __future__ import annotations

import os
import sys
import time
import traceback

from leks import hooks
from leks.hooks import warm_models

STALE_LOCK = 3600.0
LOG_MAX = 5 * 1024 * 1024
CHILD_STMT = 'from leks.hooks import update_index; update_index.child()'


def _stamp_path(cfg):
    return os.path.join(cfg.run_dir(), 'update.stamp')


def _lock_path(cfg):
    # not update.lock: the build takes that one itself, inside this job
    return os.path.join(cfg.run_dir(), 'update_job.lock')


def _log_path(cfg):
    return os.path.join(cfg.run_dir(), 'update.log')


def log(cfg, msg):
    """Append a timestamped line to the log. The log rotates once at 5 MB."""
    try:
        path = _log_path(cfg)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > LOG_MAX:
                os.replace(path, path + '.1')
        except OSError:
            pass
        with open(path, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + str(msg) + '\n')
    except Exception:
        pass


def due(cfg):
    """True when the stamp is missing or older than update.min_interval."""
    try:
        age = time.time() - os.stat(_stamp_path(cfg)).st_mtime
    except OSError:
        return True
    return age >= float((cfg.update or {}).get('min_interval', 1800))


def _build_all(cfg, say):
    from leks import build
    return build.update_all(cfg, False, say)


class _KeepOpen:
    """Client wrapper whose bye does nothing, so one daemon serves every profile."""

    def __init__(self, client):
        self._c = client

    def __getattr__(self, name):
        return getattr(self._c, name)

    def bye(self):
        pass


def _embed_profiles(cfg, say):
    """Embed per profile. One daemon for all of them; it gets its bye at the end."""
    from leks import embed_all
    from leks.modeld_client import Client
    client = Client.from_config(cfg)
    names = list(cfg.profiles)
    try:
        for i, p in enumerate(names):
            c = client if i == len(names) - 1 else _KeepOpen(client)
            say('embed %s: exit %s' % (p, embed_all.run(cfg, p, client=c)))
    finally:
        try:
            client.bye()
        except Exception:
            pass


def _job(cfg):
    """The update itself: daemon, build, vectors, stamp. Runs inside the lock."""
    say = lambda *a: log(cfg, ' '.join(str(x) for x in a))  # noqa: E731
    log(cfg, 'update start')
    try:
        # the daemon loads its models while the build runs
        say('daemon: ' + warm_models.start(cfg))
        say('build: ' + str(_build_all(cfg, say)))
        if cfg.embedder is not None:
            st = warm_models.start(cfg)
            say('daemon: ' + st)
            if st in ('up', 'started', 'locked'):
                _embed_profiles(cfg, say)
    except Exception as e:
        log(cfg, 'error: %s: %s' % (type(e).__name__, str(e)[:300]))
        log(cfg, traceback.format_exc().strip().splitlines()[-1])
    finally:
        try:
            with open(_stamp_path(cfg), 'a'):
                pass
            os.utime(_stamp_path(cfg), None)
        except OSError:
            pass
        hooks.drop_lock(_lock_path(cfg))
        log(cfg, 'update end')


def child():
    """Entry of the detached child process."""
    try:
        from leks import config
        _job(config.load())
    except Exception:
        pass


def run(cfg, force=False, background=True):
    """'not due' | 'locked' | 'started' | 'done' (and 'refused' when the child cannot start)."""
    try:
        if not force and not due(cfg):
            return 'not due'
        os.makedirs(cfg.run_dir(), exist_ok=True)
        if not hooks.take_lock(_lock_path(cfg), STALE_LOCK):
            return 'locked'
        if not background:
            _job(cfg)
            return 'done'
        if not hooks.spawn(hooks.boot_argv(CHILD_STMT), hooks.child_env(cfg)):
            hooks.drop_lock(_lock_path(cfg))
            return 'refused'
        return 'started'
    except Exception:
        return 'refused'


def main():
    """Hook entry. Prints nothing, always 0."""
    try:
        from leks import config
        run(config.load())
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
