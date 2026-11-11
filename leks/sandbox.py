"""macOS sandbox for the model daemon (sandbox-exec with a generated profile).

The daemon only turns text into numbers. The profile takes away the network,
every write outside the run folder and every exec but python, so a bad
weights file or a library bug has nowhere to go. File reads stay open on
purpose (the profile has to work on any Mac). On other systems the daemon runs
without it and says so.
"""
from __future__ import annotations

import os
import sys

SANDBOX_EXEC = '/usr/bin/sandbox-exec'
NOTE_MISSING = 'sandbox-exec not available on this system'
NOTE_OFF = 'turned off in the config'

# confstr ids for the per-user cache and temp folders (not named in os.confstr_names)
CS_USER_TEMP = 65537
CS_USER_CACHE = 65538


class SandboxError(RuntimeError):
    """The config demands a sandbox and there is none."""


def available():
    """(True, '') when sandbox-exec is here, else (False, reason)."""
    if sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK):
        return True, ''
    return False, NOTE_MISSING


def _q(path):
    """Quote a path for the profile language."""
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise ValueError('control character in a path')
    return '"' + path.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _real(path):
    return os.path.realpath(os.path.expanduser(path))


def _sock_real(cfg):
    sock = cfg.socket_path()
    return os.path.join(_real(os.path.dirname(os.path.abspath(sock))), os.path.basename(sock))


def _darwin_dirs():
    """Per-user cache and temp folders (Metal writes shader caches there)."""
    out = []
    if sys.platform != 'darwin':
        return out
    for n in (CS_USER_CACHE, CS_USER_TEMP):
        try:
            p = os.confstr(n)
        except (ValueError, OSError):
            continue
        if p:
            out.append(_real(p.rstrip('/')))
    return out


def _exec_targets(python):
    """The interpreter, plus the app launcher a framework build re-executes into."""
    real = _real(python)
    out = [real]
    app = os.path.join(os.path.dirname(os.path.dirname(real)), 'Resources', 'Python.app', 'Contents', 'MacOS', 'Python')
    if os.path.isfile(app):
        out.append(_real(app))
    return out


def profile_text(cfg, python, extra_read=()):
    """The profile as text. Network off except one unix socket, writes only in run, exec only python.

    extra_read adds read allowances for people who tighten reads on top of this
    profile; with the default (reads open) they change nothing.
    """
    run = _real(cfg.run_dir())
    sock = _sock_real(cfg)
    writes = [
        '(subpath %s)' % _q(run),
        '(literal %s)' % _q(sock),
        '(literal "/dev/null")',
        '(literal "/dev/dtracehelper")',
    ] + ['(subpath %s)' % _q(d) for d in _darwin_dirs()]
    lines = [
        '(version 1) ; profile language version',
        '(allow default) ; start open, then close what matters',
        '(deny network*) ; no network of any kind',
        '(allow network-bind network-inbound (local unix-socket (path-literal %s))) ; except the one local socket we listen on' % _q(sock),
        '(deny file-write*) ; no writes anywhere',
        '(allow file-write* %s) ; except the run folder, our socket, null and the user cache and temp folders' % ' '.join(writes),
        '(deny process-exec) ; no new programs',
        '(allow process-exec %s) ; except the python that runs the daemon' % ' '.join('(literal %s)' % _q(p) for p in _exec_targets(python)),
    ]
    for p in extra_read:
        lines.append('(allow file-read* (subpath %s)) ; extra read path' % _q(_real(p)))
    return '\n'.join(lines) + '\n'


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def write_profile(cfg, python=None):
    """Write <run>/modeld.sb and return its path."""
    run = _private_dir(cfg.run_dir())
    path = os.path.join(run, 'modeld.sb')
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(profile_text(cfg, python or sys.executable))
    os.replace(tmp, path)
    return path


def clean_env(cfg):
    """The only environment the daemon sees. Nothing is inherited."""
    run = _real(cfg.run_dir())
    for sub in ('home', 'tmp', 'cache'):
        _private_dir(os.path.join(run, sub))
    env = {
        'PATH': '/usr/bin:/bin',
        'HOME': os.path.join(run, 'home'),
        'TMPDIR': os.path.join(run, 'tmp'),
        'PYTHONNOUSERSITE': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1',
        'DO_NOT_TRACK': '1',
        'TOKENIZERS_PARALLELISM': 'false',
        'TORCHINDUCTOR_CACHE_DIR': os.path.join(run, 'cache'),
        'LEKS_HOME': os.path.abspath(os.path.expanduser(cfg.home)),
        'LEKS_MODELD_SOCK': cfg.socket_path(),
    }
    # hub files under LEKS_HOME, or where the user put HF_HOME
    from . import config
    env['HF_HOME'] = config.hf_home(cfg.home)
    if cfg.source_path:
        env['LEKS_CONFIG'] = os.path.abspath(os.path.expanduser(cfg.source_path))
    if 'LEKS_ALLOW_FAKE' in os.environ:
        env['LEKS_ALLOW_FAKE'] = os.environ['LEKS_ALLOW_FAKE']
    return env


def command(cfg, argv):
    """Wrap argv for the daemon. Returns (argv, note).

    argv[0] should be the real interpreter path (sys.executable). note is ''
    when the sandbox is on, else the reason it is off. daemon.sandbox 'on'
    raises SandboxError when there is no sandbox-exec.
    """
    mode = (cfg.daemon or {}).get('sandbox', 'auto')
    if mode == 'off':
        return list(argv), NOTE_OFF
    ok, why = available()
    if not ok:
        if mode == 'on':
            raise SandboxError('daemon.sandbox is "on" but ' + why)
        return list(argv), why
    profile = write_profile(cfg, argv[0])
    return [SANDBOX_EXEC, '-f', profile] + list(argv), ''
