"""Peak resident memory of a whole process tree, for runs whose helpers getrusage cannot see.

The LEKS model daemon is started detached by the worker (a new session that nobody waits for), so
getrusage(RUSAGE_CHILDREN) in the runner only ever sees the worker. TreePeak samples every process below
the runner and keeps the largest sum of their resident sizes. macOS only (libproc); elsewhere mb() is None.

    with TreePeak() as tp:
        run_the_system()
    print(tp.mb(), tp.most)
"""
import ctypes
import ctypes.util
import os
import sys
import threading

PROC_PIDTASKINFO = 4
TASKINFO_BYTES = 96  # sizeof(struct proc_taskinfo); the resident size is its second uint64


class TreePeak:
    def __init__(self, every=0.1):
        self.every = every
        self.peak = 0  # bytes, the largest sum seen
        self.most = 0  # the most processes seen at once
        self.looks = 0
        self._halt = threading.Event()
        self._thread = None
        self._lib = None
        if sys.platform == 'darwin':
            lib = ctypes.CDLL(ctypes.util.find_library('proc') or '/usr/lib/libproc.dylib', use_errno=True)
            lib.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
            self._lib = lib

    def _kids(self, pid):
        buf = (ctypes.c_int * 1024)()
        if self._lib.proc_listchildpids(pid, buf, ctypes.sizeof(buf)) <= 0:
            return []
        return [p for p in buf if p > 0]

    def _rss(self, pid):
        info = (ctypes.c_uint64 * (TASKINFO_BYTES // 8))()
        if self._lib.proc_pidinfo(pid, PROC_PIDTASKINFO, 0, info, TASKINFO_BYTES) != TASKINFO_BYTES:
            return 0  # gone already, or a zombie
        return info[1]

    def look(self):
        seen, todo, total = set(), [os.getpid()], 0
        while todo:
            for kid in self._kids(todo.pop()):
                if kid not in seen:
                    seen.add(kid)
                    todo.append(kid)
                    total += self._rss(kid)
        self.looks += 1
        self.peak = max(self.peak, total)
        self.most = max(self.most, len(seen))

    def _run(self):
        while not self._halt.wait(self.every):
            self.look()

    def __enter__(self):
        if self._lib is not None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._halt.set()
        if self._thread is not None:
            self._thread.join()

    def mb(self):
        return round(self.peak / (1024 * 1024), 1) if self._lib is not None else None
