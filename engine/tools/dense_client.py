"""Client of the optional dense helper (standard library; any Python). One helper process per vault root
and process, started on first use. Every function returns None when the subsystem is not installed, the
helper fails, or it does not answer in time; callers then fall back to BM25 and say so.

The helper reads vault content, so it runs only inside a macOS sandbox profile that denies every network
operation (also for native code such as onnxruntime, which links networking frameworks) and every file
write outside the vault's .brain/ folder, with a minimal environment. Without /usr/bin/sandbox-exec the
helper is not started at all (fail closed)."""
import json
import os
from pathlib import Path
import select
import subprocess
import threading

TOOLS = Path(__file__).resolve().parent
PYTHON = TOOLS.parent / '.venv/bin/python3'
MODEL = TOOLS.parent / '.venv/models/bge-small-en-v1.5/onnx/model.onnx'
SANDBOX = '/usr/bin/sandbox-exec'
FIRST_TIMEOUT, TIMEOUT = 900, 30           # the first request may embed the whole vault
_helpers, _failed = {}, {}
_lock = threading.Lock()


def installed():
    return PYTHON.exists() and MODEL.exists() and os.path.exists(SANDBOX)


def unavailable(root):
    """Why dense retrieval is unavailable for this root, or None."""
    if not os.path.exists(SANDBOX):
        return 'no sandbox-exec: the dense helper only runs without network access'
    if not installed():
        return 'dense subsystem not installed'
    return _failed.get(str(Path(root).resolve()))


def profile(root):
    """Sandbox profile: no network of any kind; writes only in <root>/.brain, the null device and the DTrace
    helper device that the Python runtime registers its probes with at start-up."""
    def quote(p):
        return '"' + str(p).replace('\\', '\\\\').replace('"', '\\"') + '"'
    brain_dir = os.path.realpath(os.path.join(root, '.brain'))
    return ('(version 1)(allow default)(deny network*)(deny file-write*)'
            '(allow file-write* (subpath %s) (literal "/dev/null") (literal "/dev/dtracehelper"))' % quote(brain_dir))


def command(root):
    return [SANDBOX, '-p', profile(root), str(PYTHON), '-I', '-B', str(TOOLS / 'dense_helper.py'), root]


def environment():
    return {'PATH': '/usr/bin:/bin', 'HOME': os.environ.get('HOME', '/tmp'), 'LANG': 'en_US.UTF-8',
            'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
            'ORT_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false'}


def _start(root):
    return subprocess.Popen(command(root), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1, env=environment())


def _fail(root, why):
    proc = _helpers.pop(root, None)
    if proc is not None and proc.poll() is None:
        proc.kill()
    _failed[root] = why
    return None


def call(root, request):
    root = str(Path(root).resolve())
    with _lock:
        if not installed() or root in _failed:
            return None
        proc = _helpers.get(root)
        first = proc is None or proc.poll() is not None
        if first:
            proc = _helpers[root] = _start(root)
        try:
            proc.stdin.write(json.dumps(request) + '\n')
            proc.stdin.flush()
            ready, _, _ = select.select([proc.stdout], [], [], FIRST_TIMEOUT if first else TIMEOUT)
            line = proc.stdout.readline() if ready else ''
        except (OSError, ValueError):
            return _fail(root, 'dense helper stopped')
        if not line:
            return _fail(root, 'dense helper did not answer')
        try:
            reply = json.loads(line)
        except ValueError:                  # a garbled reply is a failure: fall back to BM25, never raise
            return _fail(root, 'dense helper sent an unreadable reply')
        return None if not isinstance(reply, dict) or 'error' in reply else reply


def search(root, query, k=20):
    reply = call(root, {'op': 'search', 'query': query, 'k': k})
    return reply and reply['hits']


def rerank(root, query, paths, per_page=2, variant='model.onnx'):
    reply = call(root, {'op': 'rerank', 'query': query, 'paths': list(paths), 'per_page': per_page, 'variant': variant})
    return reply and reply['scores']


def answerability(root, question, texts):
    reply = call(root, {'op': 'answerability', 'question': question, 'texts': list(texts)})
    return reply and reply['margins']


def top_passages(root, query, paths, per_page=2):
    reply = call(root, {'op': 'top_passages', 'query': query, 'paths': list(paths), 'per_page': per_page})
    return reply and reply['texts']


def passage_scores(root, query, paths):
    reply = call(root, {'op': 'passages', 'query': query, 'paths': list(paths)})
    return reply and reply['passages']


def status(root):
    return call(root, {'op': 'status'})


def close():
    """Stops every helper: end of input first, then a kill if it has not exited within 10 s."""
    with _lock:
        for proc in _helpers.values():
            if proc.poll() is None:
                try:
                    proc.stdin.close()
                    proc.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    proc.kill()
                    proc.wait()
            proc.stdout.close()
        _helpers.clear()
