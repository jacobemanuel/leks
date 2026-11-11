"""Session hooks: brief, warm models, update index.

Shared helpers sit here: directory locks, a detached spawn and the bootstrap
that lets a child process import the package without installing it. Hooks
print nothing on failure and never raise.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time


def pkg_parent():
    """The folder that holds the leks package."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def boot_argv(stmt, isolated=False):
    """argv for 'python -c <path setup>; <stmt>'. Isolated mode ignores env vars and user site."""
    code = 'import sys; sys.path.insert(0, %r); %s' % (pkg_parent(), stmt)
    argv = [sys.executable]
    if isolated:
        argv.append('-I')
    return argv + ['-c', code]


def child_env(cfg):
    """Environment for a detached child that has to load the same config."""
    env = dict(os.environ)
    env['LEKS_HOME'] = cfg.home
    sp = getattr(cfg, 'source_path', None)
    if sp:
        env['LEKS_CONFIG'] = os.path.abspath(sp)
    return env


def take_lock(path, stale_secs):
    """mkdir lock. A lock older than stale_secs is a leftover from a crash and gets removed once."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        return False
    for _ in range(2):
        try:
            os.mkdir(path)
            return True
        except FileExistsError:
            try:
                age = time.time() - os.stat(path).st_mtime
            except OSError:
                continue
            if age <= stale_secs:
                return False
            try:
                os.rmdir(path)
            except OSError:
                return False
        except OSError:
            return False
    return False


def drop_lock(path):
    try:
        os.rmdir(path)
    except OSError:
        pass


def spawn(argv, env=None, cwd='/'):
    """Start a detached process (own session, no stdio). True when it started."""
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=env, cwd=cwd, start_new_session=True, close_fds=True)
        return True
    except Exception:
        return False
