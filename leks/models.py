"""Model weights: fetch once, then check before every load.

fetch() is the only place in the whole engine that touches the network, and
only when the user runs `leks models fetch` on purpose. Everything else here
is plain file work.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat

MANIFEST = 'manifest.json'
SMALL = 64 * 1024 * 1024          # files up to this size get their hash checked on every load
KEY_RE = re.compile(r'^[a-z0-9_]{1,32}$')
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
ROLES = ('embedder', 'reranker')

# alternate formats we never use, so they never get downloaded
SKIP_PATTERNS = ['*.bin', '*.onnx', '*.h5', '*.msgpack', '*.ot', '*.gguf', '*.mlpackage/*',
                 'onnx/*', 'openvino/*', 'coreml/*', 'flax/*', 'tf/*']


class ModelCheck(Exception):
    """Weights are present but wrong (file set, kind, size or hash)."""


class ModelMissing(ModelCheck):
    """No weights (or no manifest) where they should be."""


def _model_dir(cfg, key):
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise ModelMissing('bad model key')
    return os.path.join(cfg.weights_dir, key)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _listing(d, rel=''):
    """Every plain file under d as a relative path. Anything else (link, socket) is an error."""
    out = []
    try:
        entries = sorted(os.scandir(os.path.join(d, rel) if rel else d), key=lambda e: e.name)
    except OSError:
        raise ModelMissing('weights folder not readable') from None
    for e in entries:
        r = rel + '/' + e.name if rel else e.name
        try:
            mode = e.stat(follow_symlinks=False).st_mode
        except OSError:
            raise ModelCheck('file not readable: ' + r) from None
        if stat.S_ISDIR(mode):
            out += _listing(d, r)
        elif stat.S_ISREG(mode):
            if r != MANIFEST:
                out.append(r)
        else:
            raise ModelCheck('not a plain file: ' + r)
    return out


def write_manifest(dir):
    """Hash every file under dir, write manifest.json there, return the mapping."""
    entries = {}
    for r in _listing(dir):
        if r == MANIFEST + '.tmp':
            continue
        p = os.path.join(dir, r)
        entries[r] = {'size': os.lstat(p).st_size, 'sha256': _sha256(p)}
    tmp = os.path.join(dir, MANIFEST + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(entries, f, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, os.path.join(dir, MANIFEST))
    return entries


def _read_manifest(d):
    p = os.path.join(d, MANIFEST)
    try:
        st = os.lstat(p)
        if not stat.S_ISREG(st.st_mode):
            raise ModelCheck('manifest is not a plain file')
        with open(p, 'r', encoding='utf-8') as f:
            want = json.load(f)
    except OSError:
        raise ModelMissing('no manifest: run leks models fetch') from None
    except ValueError:
        raise ModelCheck('manifest is not valid json') from None
    if not isinstance(want, dict) or not want:
        raise ModelCheck('manifest is empty or malformed')
    for name, e in want.items():
        ok = (isinstance(name, str) and name and not name.startswith('/') and '..' not in name.split('/')
              and isinstance(e, dict) and isinstance(e.get('size'), int) and e['size'] >= 0
              and isinstance(e.get('sha256'), str) and SHA_RE.match(e['sha256']))
        if not ok:
            raise ModelCheck('manifest has a bad entry')
    return want


def check(cfg, key):
    """Compare the folder with its manifest. Returns the folder path, raises ModelCheck."""
    d = _model_dir(cfg, key)
    want = _read_manifest(d)
    have = set(_listing(d))
    if have != set(want):
        missing = sorted(set(want) - have)
        extra = sorted(have - set(want))
        raise ModelCheck('file set differs from the manifest: missing %s, extra %s' % (missing[:3], extra[:3]))
    for name, e in want.items():
        p = os.path.join(d, name)
        try:
            st = os.lstat(p)
        except OSError:
            raise ModelCheck('file vanished: ' + name) from None
        if not stat.S_ISREG(st.st_mode) or st.st_size != e['size']:
            raise ModelCheck('size differs from the manifest: ' + name)
        if st.st_size <= SMALL and _sha256(p) != e['sha256']:
            raise ModelCheck('hash differs from the manifest: ' + name)
    return d


def check_all(cfg):
    """Check every configured model. {key: {'ok': bool, 'why': str}}, never raises."""
    out = {}
    for role in ROLES:
        spec = getattr(cfg, role, None)
        if spec is None:
            continue
        try:
            check(cfg, spec.key)
            out[spec.key] = {'role': role, 'ok': True, 'why': ''}
        except ModelCheck as e:
            out[spec.key] = {'role': role, 'ok': False, 'why': str(e)}
    return out


def weights_present(cfg, key):
    """Cheap test: is there a manifest for this key. No hashing."""
    try:
        d = _model_dir(cfg, key)
        return stat.S_ISREG(os.lstat(os.path.join(d, MANIFEST)).st_mode)
    except (OSError, ModelCheck):
        return False


def projection_file(base, src):
    """The one .safetensors file inside base/src. Raises ModelCheck when it is not exactly one."""
    if not isinstance(src, str) or not src or src.startswith('/') or '..' in src.split('/'):
        raise ModelCheck('bad projection folder name')
    d = os.path.join(base, src)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith('.safetensors'))
    except OSError:
        raise ModelCheck('projection folder missing: ' + src) from None
    if len(names) != 1:
        raise ModelCheck('projection folder %s holds %d .safetensors files, expected exactly 1' % (src, len(names)))
    p = os.path.join(d, names[0])
    if not stat.S_ISREG(os.lstat(p).st_mode):
        raise ModelCheck('projection file is not a plain file: ' + src)
    return p


def _verify_layout(spec, d):
    """After a download: the folders the config relies on must be there."""
    if not os.path.isfile(os.path.join(d, 'config.json')):
        raise ModelCheck('%s@%s has no config.json at its top level; the hub layout differs from what the engine expects'
                         % (spec.hf_id, spec.revision[:8]))
    if not any(n.endswith('.safetensors') for n in os.listdir(d)):
        raise ModelCheck('%s@%s has no .safetensors weights at its top level; the hub layout differs from what the engine expects'
                         % (spec.hf_id, spec.revision[:8]))
    for p in spec.projection:
        try:
            projection_file(d, p['src'])
        except ModelCheck as e:
            found = sorted(os.listdir(d))[:20]
            raise ModelCheck('%s; the hub layout of %s@%s differs from the config (top level holds: %s)'
                             % (e, spec.hf_id, spec.revision[:8], ', '.join(found))) from e


def fetch(cfg, which=ROLES):
    """Download the pinned revision of each wanted model, then write its manifest.

    This is the only network use in the engine. Needs huggingface_hub.
    The hub cache goes under LEKS_HOME unless HF_HOME is set (see config).
    """
    if not os.environ.get('HF_HOME'):
        # the hub client reads this on import, so set it first
        from . import config
        os.environ['HF_HOME'] = config.hf_home(cfg.home)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ModelCheck('huggingface_hub is not installed: pip install huggingface_hub') from None
    out = {}
    for role in which:
        if role not in ROLES:
            raise ValueError('unknown model role: %r' % (role,))
        spec = getattr(cfg, role, None)
        if spec is None:
            continue
        d = _model_dir(cfg, spec.key)
        os.makedirs(d, mode=0o700, exist_ok=True)
        snapshot_download(repo_id=spec.hf_id, revision=spec.revision, local_dir=d, ignore_patterns=SKIP_PATTERNS)
        # the hub client leaves a bookkeeping folder behind, it would break the file set
        shutil.rmtree(os.path.join(d, '.cache'), ignore_errors=True)
        _verify_layout(spec, d)
        entries = write_manifest(d)
        out[spec.key] = {'role': role, 'files': len(entries), 'bytes': sum(e['size'] for e in entries.values())}
    return out
