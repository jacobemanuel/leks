"""Safe persistence for the vault's shared files (registry, claims, journal, indexes).

- atomic_write_*: serialise fully, write a temporary file in the same folder, flush, F_FULLFSYNC on macOS
  (fsync elsewhere), rename over the target, sync the folder, then read the result back and validate it.
  An interrupted write leaves the previous complete file; the temporary file is reported by check.
- vault_lock: one inter-process write lock (flock on .brain/write.lock) for read-modify-write sequences;
  re-entrant within a process, with a timeout so a stuck writer is reported instead of waited on forever.
Standard library only.
"""
import contextlib
import fcntl
import json
import os
import stat
from pathlib import Path
import tempfile
import threading
import time

LOCK_PATH = '.brain/write.lock'
TEMP_MARK = '.vault-tmp-'
_held = threading.local()


def _durable(fd):
    if hasattr(fcntl, 'F_FULLFSYNC'):
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(fd)


def atomic_write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=TEMP_MARK + path.name + '.', dir=str(path.parent))
    try:
        mode = stat.S_IMODE(os.stat(str(path)).st_mode) if path.exists() else 0o644
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), mode)     # keep the file's mode (mkstemp creates 0600)
            _durable(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    dirfd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dirfd)
    except OSError:
        pass
    finally:
        os.close(dirfd)


def atomic_write_text(path, text):
    atomic_write_bytes(path, text.encode('utf-8'))
    if Path(path).read_text(encoding='utf-8') != text:
        raise IOError('write verification failed: ' + str(path))


def atomic_write_json(path, data, indent=2):
    text = json.dumps(data, ensure_ascii=False, indent=indent) + '\n'
    json.loads(text)
    atomic_write_bytes(path, text.encode('utf-8'))
    json.loads(Path(path).read_text(encoding='utf-8'))


def stray_temporaries(root, folders=('system', '.brain', 'wiki')):
    """Temporary files left by an interrupted atomic write."""
    found = []
    for folder in folders:
        base = Path(root) / folder
        if base.is_dir():
            found += [p for p in base.rglob(TEMP_MARK + '*') if p.is_file()]
    return sorted(found)


@contextlib.contextmanager
def vault_lock(root, timeout=60.0):
    depth = getattr(_held, 'depth', 0)
    if depth:
        _held.depth = depth + 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    lock = Path(root) / LOCK_PATH
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, 'a+') as f:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError('another process holds the vault write lock ' + str(lock))
                time.sleep(0.02)
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
