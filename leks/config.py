"""Config for leks: schema checks, defaults, path mapping and glob matching.

Stdlib only. Nothing here touches the network or a model.

Model files stay under LEKS_HOME: the weights go to models.weights_dir
(default <home>/models), and `leks models fetch` and the model daemon set
HF_HOME to <home>/models/hf-home, so the hub client keeps its cache there too
instead of in the user cache folder. An HF_HOME you set yourself wins.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

VERSION = 3
SOCK_MAX = 100          # unix socket paths stay under this many bytes
JSON_MAX_BYTES = 200000  # code roots only index json files below this size
MAX_CONFIG_BYTES = 1 << 20

KINDS = ('notes', 'code', 'docs', 'mixed')
IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.heic', '.tif', '.tiff', '.gif', '.bmp', '.webp')
# same list as the code lane, repeated here so config has no dependency on it
CODE_EXT = ('.py', '.swift', '.metal', '.cs', '.sh', '.bash', '.zsh', '.c', '.h', '.cpp', '.cc',
            '.hpp', '.m', '.mm', '.js', '.ts', '.go', '.rs', '.java', '.kt')

_NOTES_EXT = ('.md', '.markdown', '.txt', '.rst', '.tex', '.html', '.htm')
_CODE_CFG_EXT = ('.toml', '.yml', '.yaml', '.json')
_DOCS_EXT = ('.pdf', '.docx', '.txt', '.md') + IMAGE_EXT


def _globs(exts):
    return tuple('**/*' + e for e in exts)


def _union(*groups):
    out = []
    for g in groups:
        for p in g:
            if p not in out:
                out.append(p)
    return tuple(out)


DEFAULT_INCLUDE = {
    'notes': _globs(_NOTES_EXT),
    'code': _globs(CODE_EXT + _CODE_CFG_EXT),
    'docs': _globs(_DOCS_EXT),
}
DEFAULT_INCLUDE['mixed'] = _union(DEFAULT_INCLUDE['notes'], DEFAULT_INCLUDE['code'], DEFAULT_INCLUDE['docs'])

ALWAYS_EXCLUDE_DIRS = frozenset({
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', '.build', 'build', 'DerivedData',
    '.swiftpm', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.obsidian', '.Trash',
    '.ipynb_checkpoints', 'dist', 'target',
})
JUNK_NAMES = frozenset({'.DS_Store', 'Thumbs.db', '.localized'})
JUNK_PREFIXES = ('._', '~$')
JUNK_SUFFIXES = ('.pyc', '.swp', '.tmp', '.sqlite-wal', '.sqlite-shm')
SECRET_NAME = re.compile(
    r'(^|/)\.env($|\.)|\.pem$|\.key$|\.p12$|\.pfx$|(^|/)id_(rsa|ed25519|ecdsa)'
    r'|credential|secret|password|apikey|api_key', re.I)

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')
KEY_RE = re.compile(r'^[a-z0-9_]{1,32}$')
REV_RE = re.compile(r'^[0-9a-f]{40}$')
HF_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
SRC_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')

POOLINGS = ('cls', 'mean', 'last', 'maxsim')
BACKENDS = ('torch', 'fake')
DEVICES = ('auto', 'cpu', 'mps', 'cuda')
CLASSES = ('state', 'rule', 'decision', 'result', 'history')


class ConfigError(Exception):
    """Anything wrong with the config file or its values."""


# the template: what `leks init` writes and what every missing field falls back to
_TEMPLATE = {
    'version': 3,
    'home': '~/.leks',
    'roots': [
        {'name': 'notes', 'path': '~/projects/example/notes', 'kind': 'notes',
         'include': ['**/*.md'], 'exclude': ['drafts/**']},
        {'name': 'code', 'path': '~/projects/example/src', 'kind': 'code',
         'exclude': ['**/build/**']},
        {'name': 'docs', 'path': '~/projects/example/docs', 'kind': 'docs'},
        {'name': 'misc', 'path': '~/projects/example/misc', 'kind': 'mixed', 'follow_links': False},
    ],
    'profiles': {
        'architect': {'roots': ['notes', 'docs']},
        'orchestrator': {'roots': ['notes', 'code', 'docs']},
    },
    'default_profile': 'default',
    'classify': {
        'state': ['**/STATE.md', '**/NOW.md', '**/*_STATE.md'],
        'rule': ['**/CLAUDE.md', '**/AGENTS.md', '.claude/**', '.github/copilot-instructions.md'],
        'decision': ['**/decisions/**', '**/adr/**', '**/DEC-*', '**/ADR-*'],
        'result': ['**/results/**'],
        'history': ['**/archive/**', '**/history/**', '**/*_HISTORY*'],
    },
    'models': {
        'backend': 'torch',
        'device': 'auto',
        'weights_dir': None,
        'embedder': {
            'key': 'mdense', 'hf_id': 'lightonai/mDenseOn',
            'revision': 'a5fdb000f7a21da96c3bddde3a782ef777316df3',
            'op': 'embed', 'pooling': 'cls', 'normalize': True,
            'prefixes': {'q': 'query: ', 'd': 'document: '},
            'dim': 768, 'max_length': 512, 'dtype': 'float32',
        },
        'reranker': {
            'key': 'mlate', 'hf_id': 'lightonai/mLateOn',
            'revision': 'bbd883ae1fadb563056ff73b1061ae8172a3fcda',
            'op': 'maxsim', 'pooling': 'maxsim', 'normalize': True,
            'prefixes': {'q': '[Q] ', 'd': '[D] '},
            'dim': 128, 'max_length': 512, 'dtype': 'float32',
            'projection': [
                {'src': '1_Dense', 'in': 768, 'out': 1536, 'residual': True},
                {'src': '2_Dense', 'in': 1536, 'out': 768, 'residual': True},
                {'src': '3_Dense', 'in': 768, 'out': 128, 'residual': False},
            ],
        },
    },
    'budgets': {'embed': 45.0, 'rerank': 8.0, 'cold_rerank': 30.0, 'cold_after': 600.0,
                'connect': 10.0, 'ping': 2.0},
    'daemon': {'idle_exit': 3600, 'sandbox': 'auto'},
    'update': {'min_interval': 1800},
    'ocr': {'enabled': 'auto', 'images': True, 'helper': None, 'max_pages': 40, 'label_results': True},
    'limits': {'max_file_bytes': 1500000, 'chunk_chars': 1000, 'pdf_max_bytes': 209715200,
               'office_max_bytes': 419430400, 'max_names': 500000},
    'server': {'always_load': True},
}

# sections a config may have that `leks init` does not write, with their defaults.
# check.em_dash: false stops `leks check` from flagging em dashes in changed notes
_OPTIONAL = {
    'check': {'em_dash': True},
}

_TOP_KEYS = frozenset(_TEMPLATE) | frozenset(_OPTIONAL)
_ROOT_KEYS = frozenset({'name', 'path', 'kind', 'include', 'exclude', 'follow_links'})
_MODELS_KEYS = frozenset({'backend', 'device', 'weights_dir', 'embedder', 'reranker'})
_SPEC_KEYS = frozenset({'key', 'hf_id', 'revision', 'op', 'pooling', 'normalize', 'prefixes',
                        'dim', 'max_length', 'dtype', 'projection'})
_PROJ_KEYS = frozenset({'src', 'in', 'out', 'residual'})


def default_dict() -> dict:
    """A fresh copy of the template that `leks init` writes."""
    return copy.deepcopy(_TEMPLATE)


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Root:
    name: str
    path: str
    kind: str
    include: tuple
    exclude: tuple
    follow_links: bool = False


@dataclass(frozen=True)
class ModelSpec:
    key: str
    hf_id: str
    revision: str
    op: str
    pooling: str
    normalize: bool
    prefixes: dict
    dim: int
    max_length: int
    dtype: str
    projection: tuple = ()

    def to_dict(self) -> dict:
        d = {'key': self.key, 'hf_id': self.hf_id, 'revision': self.revision, 'op': self.op,
             'pooling': self.pooling, 'normalize': self.normalize, 'prefixes': dict(self.prefixes),
             'dim': self.dim, 'max_length': self.max_length, 'dtype': self.dtype}
        if self.projection:
            d['projection'] = [dict(p) for p in self.projection]
        return d


@dataclass
class Config:
    home: str
    roots: list
    profiles: dict
    default_profile: str
    classify: dict
    backend: str
    device: str
    weights_dir: str
    embedder: 'ModelSpec | None'
    reranker: 'ModelSpec | None'
    budgets: dict
    daemon: dict
    update: dict
    ocr: dict
    limits: dict
    server: dict
    source_path: 'str | None' = None
    check: dict = field(default_factory=lambda: copy.deepcopy(_OPTIONAL['check']))

    # ----- derived paths -----
    def index_dir(self) -> str:
        return os.path.join(self.home, 'index')

    def run_dir(self) -> str:
        return os.path.join(self.home, 'run')

    def master_path(self) -> str:
        return os.path.join(self.index_dir(), 'master.sqlite')

    def profile_db(self, profile: str) -> str:
        if profile not in self.profiles:
            raise ConfigError('unknown profile: %s' % profile)
        return os.path.join(self.index_dir(), profile, 'search.sqlite')

    def vectors_dir(self) -> str:
        return os.path.join(self.index_dir(), 'vectors')

    def socket_path(self) -> str:
        env = os.environ.get('LEKS_MODELD_SOCK')
        p = os.path.expanduser(env) if env else os.path.join(self.run_dir(), 'modeld.sock')
        if len(p.encode('utf-8')) >= SOCK_MAX:
            h = hashlib.sha256(self.home.encode('utf-8')).hexdigest()[:8]
            uid = os.getuid() if hasattr(os, 'getuid') else 0
            p = '/tmp/leks-%d-%s.sock' % (uid, h)
        return p

    def helper_path(self) -> str:
        h = self.ocr.get('helper')
        if h:
            return os.path.expanduser(h)
        return os.path.join(self.home, 'bin', 'extract_text')

    # ----- lookups -----
    def root(self, name: str) -> Root:
        for r in self.roots:
            if r.name == name:
                return r
        raise KeyError(name)

    def roots_for(self, profile: str) -> list:
        if profile not in self.profiles:
            raise KeyError(profile)
        names = set(self.profiles[profile])
        return [r for r in self.roots if r.name in names]

    def model(self, key: str):
        for m in (self.embedder, self.reranker):
            if m is not None and m.key == key:
                return m
        return None

    def abs_path(self, display: str):
        """'notes/a/b.md' to '<root path>/a/b.md'. None for anything odd."""
        if not isinstance(display, str) or not display or '\x00' in display or '\\' in display:
            return None
        if display.startswith('/'):
            return None
        parts = display.split('/')
        if len(parts) < 2:
            return None
        try:
            root = self.root(parts[0])
        except KeyError:
            return None
        rest = parts[1:]
        if any(p in ('', '.', '..') for p in rest):
            return None
        return root.path + '/' + '/'.join(rest)

    def display_path(self, abs_path: str):
        """Inverse of abs_path. None when the path is under no root."""
        if not isinstance(abs_path, str) or not abs_path or '\x00' in abs_path:
            return None
        if '..' in abs_path.split('/'):
            return None
        hit = self._display(abs_path)
        if hit is None:
            try:
                hit = self._display(os.path.realpath(abs_path), real=True)
            except OSError:
                hit = None
        return hit

    def _display(self, p: str, real: bool = False):
        best = None
        for r in self.roots:
            base = os.path.realpath(r.path) if real else r.path
            if p.startswith(base + '/') and (best is None or len(base) > len(best[1])):
                best = (r, base)
        if best is None:
            return None
        r, base = best
        rel = p[len(base) + 1:]
        if not rel or rel.endswith('/'):
            return None
        return r.name + '/' + rel

    def to_dict(self) -> dict:
        return {
            'version': VERSION,
            'home': self.home,
            'roots': [{'name': r.name, 'path': r.path, 'kind': r.kind, 'include': list(r.include),
                       'exclude': list(r.exclude), 'follow_links': r.follow_links} for r in self.roots],
            'profiles': {n: {'roots': list(v)} for n, v in self.profiles.items() if n != 'default'},
            'default_profile': self.default_profile,
            'classify': {k: list(v) for k, v in self.classify.items()},
            'models': {
                'backend': self.backend,
                'device': self.device,
                'weights_dir': self.weights_dir,
                'embedder': self.embedder.to_dict() if self.embedder else None,
                'reranker': self.reranker.to_dict() if self.reranker else None,
            },
            'budgets': dict(self.budgets),
            'daemon': dict(self.daemon),
            'update': dict(self.update),
            'ocr': dict(self.ocr),
            'limits': dict(self.limits),
            'server': dict(self.server),
            'check': dict(self.check),
        }


# ---------- glob matching, written by hand ----------

def _seg_regex(seg: str):
    """One path segment pattern to a compiled regex. * ? and [..] only."""
    out = []
    i, n = 0, len(seg)
    while i < n:
        c = seg[i]
        if c == '*':
            while i + 1 < n and seg[i + 1] == '*':
                i += 1
            out.append('[^/]*')
        elif c == '?':
            out.append('[^/]')
        elif c == '[':
            j = i + 1
            if j < n and seg[j] in '!^':
                j += 1
            if j < n and seg[j] == ']':
                j += 1
            while j < n and seg[j] != ']':
                j += 1
            if j >= n:
                out.append(re.escape(c))
            else:
                body = seg[i + 1:j]
                neg = body[:1] in ('!', '^')
                if neg:
                    body = body[1:]
                body = body.replace('\\', '\\\\')
                out.append('[' + ('^' if neg else '') + body + ']')
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    try:
        return re.compile(''.join(out), re.S)
    except re.error:
        return re.compile(re.escape(seg))


@lru_cache(maxsize=1024)
def _compile(pattern: str):
    segs = pattern.split('/')
    out = []
    for s in segs:
        if s == '**':
            if out and out[-1] is None:
                continue          # a run of ** is one **
            out.append(None)
        else:
            out.append(_seg_regex(s))
    return tuple(out)


def glob_match(rel: str, pattern: str) -> bool:
    """Match a relative path ('/' separators, case sensitive) against a glob.

    '*' and '?' stay inside one segment. A '**' segment crosses folders: it
    matches zero or more segments, except at the very end of a pattern where
    it needs at least one (so 'drafts/**' means "everything below drafts").
    """
    if not rel or not pattern:
        return False
    pp = _compile(pattern)
    ps = rel.split('/')
    memo = {}

    def go(i, j):
        key = (i, j)
        if key in memo:
            return memo[key]
        res = False
        if i == len(pp):
            res = j == len(ps)
        elif pp[i] is None:
            if i == len(pp) - 1:
                res = j < len(ps)
            else:
                res = any(go(i + 1, k) for k in range(j, len(ps) + 1))
        elif j < len(ps) and pp[i].fullmatch(ps[j]):
            res = go(i + 1, j + 1)
        memo[key] = res
        return res

    return go(0, 0)


def _any_match(rel: str, patterns) -> bool:
    return any(glob_match(rel, p) for p in patterns)


def _junk(name: str) -> bool:
    return (name in JUNK_NAMES or name.startswith(JUNK_PREFIXES) or name.endswith(JUNK_SUFFIXES))


def skipped(root: Root, rel: str) -> bool:
    """True for files that never enter the index, not even by name."""
    parts = rel.split('/')
    if any(p in ALWAYS_EXCLUDE_DIRS for p in parts[:-1]):
        return True
    if _junk(parts[-1]) or SECRET_NAME.search(rel):
        return True
    return _any_match(rel, root.exclude)


def wanted(root: Root, rel: str) -> bool:
    """True when the file gets its content indexed (include hit, nothing skips it)."""
    if skipped(root, rel):
        return False
    return _any_match(rel, root.include)


# ---------- validation helpers ----------

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _need_int(v, what, lo=1, hi=None) -> int:
    if not _is_int(v) or v < lo or (hi is not None and v > hi):
        if hi is None:
            raise ConfigError('%s must be an integer >= %d' % (what, lo))
        raise ConfigError('%s must be an integer from %d to %d' % (what, lo, hi))
    return v


def _need_num(v, what, lo=0.0, strict=True) -> float:
    if not _is_num(v) or v < lo or (strict and v == lo):
        raise ConfigError('%s must be a number > %s' % (what, lo) if strict
                          else '%s must be a number >= %s' % (what, lo))
    return float(v)


def _need_bool(v, what) -> bool:
    if not isinstance(v, bool):
        raise ConfigError('%s must be true or false' % what)
    return v


def _need_choice(v, what, choices) -> str:
    if not isinstance(v, str) or v not in choices:
        raise ConfigError('%s must be one of: %s' % (what, ', '.join(choices)))
    return v


def _need_str(v, what) -> str:
    if not isinstance(v, str) or not v or '\x00' in v:
        raise ConfigError('%s must be a non-empty string' % what)
    return v


def _unknown(given, allowed, what):
    bad = sorted(str(k) for k in given if k not in allowed)
    if bad:
        raise ConfigError('unknown key in %s: %s' % (what, ', '.join(bad)))


def _section(d: dict, name: str) -> dict:
    """Template section merged with what the file gives. Unknown keys fail."""
    defaults = _TEMPLATE[name] if name in _TEMPLATE else _OPTIONAL[name]
    given = d.get(name)
    if given is None:
        return copy.deepcopy(defaults)
    if not isinstance(given, dict):
        raise ConfigError('%s must be an object' % name)
    _unknown(given, defaults, name)
    out = copy.deepcopy(defaults)
    out.update(given)
    return out


def _patterns(v, what) -> tuple:
    if not isinstance(v, list):
        raise ConfigError('%s must be a list of globs' % what)
    out = []
    for p in v:
        if not isinstance(p, str) or not p or len(p) > 300 or '\x00' in p or '\\' in p:
            raise ConfigError('bad glob in %s' % what)
        if p.startswith('/') or '..' in p.split('/'):
            raise ConfigError('glob in %s must be relative and without ..' % what)
        out.append(p)
    return tuple(out)


def _abs(p: str, base_dir) -> str:
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(base_dir or os.getcwd(), p)
    return os.path.abspath(p)


def hf_home(home: str) -> str:
    """HF_HOME for the hub client: the user's own when set, else <home>/models/hf-home."""
    env = os.environ.get('HF_HOME')
    if env:
        return _abs(env, None)
    # the dash keeps it apart from any model key, keys are [a-z0-9_]
    return os.path.join(_abs(home, None), 'models', 'hf-home')


def home_dir(d: 'dict | None' = None, base_dir: 'str | None' = None) -> str:
    """LEKS_HOME beats the config field, which beats ~/.leks."""
    env = os.environ.get('LEKS_HOME')
    if env:
        return _abs(env, None)
    field = (d or {}).get('home')
    if field is not None:
        _need_str(field, 'home')
        return _abs(field, base_dir)
    return _abs('~/.leks', None)


# ---------- model specs ----------

def _spec(v, role: str):
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ConfigError('models.%s must be an object or null' % role)
    what = 'models.' + role
    _unknown(v, _SPEC_KEYS, what)
    for k in ('key', 'hf_id', 'revision', 'pooling', 'dim', 'max_length'):
        if k not in v:
            raise ConfigError('%s needs %s' % (what, k))
    key = v['key']
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise ConfigError('%s.key must match [a-z0-9_]{1,32}' % what)
    hf = v['hf_id']
    if not isinstance(hf, str) or not HF_RE.match(hf):
        raise ConfigError('%s.hf_id must look like owner/name' % what)
    rev = v['revision']
    if not isinstance(rev, str) or not REV_RE.match(rev):
        raise ConfigError('%s.revision must be 40 lowercase hex chars' % what)
    want_op = 'embed' if role == 'embedder' else 'maxsim'
    op = v.get('op', want_op)
    if op != want_op:
        raise ConfigError('%s.op must be %s' % (what, want_op))
    pooling = _need_choice(v['pooling'], what + '.pooling', POOLINGS)
    if role == 'embedder' and pooling == 'maxsim':
        raise ConfigError('%s.pooling cannot be maxsim' % what)
    if role == 'reranker' and pooling != 'maxsim':
        raise ConfigError('%s.pooling must be maxsim' % what)
    normalize = _need_bool(v.get('normalize', True), what + '.normalize')
    pre = v.get('prefixes', {'q': '', 'd': ''})
    if not isinstance(pre, dict) or set(pre) != {'q', 'd'} or not all(isinstance(x, str) for x in pre.values()):
        raise ConfigError('%s.prefixes needs string keys q and d' % what)
    dim = _need_int(v['dim'], what + '.dim', 1, 8192)
    mx = _need_int(v['max_length'], what + '.max_length', 8, 32768)
    dtype = v.get('dtype', 'float32')
    if dtype != 'float32':
        raise ConfigError('%s.dtype must be float32' % what)
    proj = []
    raw = v.get('projection', [])
    if not isinstance(raw, list):
        raise ConfigError('%s.projection must be a list' % what)
    if raw and role != 'reranker':
        raise ConfigError('%s.projection only applies to the reranker' % what)
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            raise ConfigError('%s.projection[%d] must be an object' % (what, i))
        _unknown(p, _PROJ_KEYS, '%s.projection[%d]' % (what, i))
        if not _PROJ_KEYS <= set(p):
            raise ConfigError('%s.projection[%d] needs src, in, out, residual' % (what, i))
        if not isinstance(p['src'], str) or not SRC_RE.match(p['src']) or p['src'] in ('.', '..'):
            raise ConfigError('%s.projection[%d].src must be a plain folder name' % (what, i))
        proj.append({'src': p['src'],
                     'in': _need_int(p['in'], what + '.projection.in', 1, 65536),
                     'out': _need_int(p['out'], what + '.projection.out', 1, 65536),
                     'residual': _need_bool(p['residual'], what + '.projection.residual')})
    if proj and proj[-1]['out'] != dim:
        raise ConfigError('%s.projection must end at dim %d' % (what, dim))
    return ModelSpec(key=key, hf_id=hf, revision=rev, op=op, pooling=pooling, normalize=normalize,
                     prefixes={'q': pre['q'], 'd': pre['d']}, dim=dim, max_length=mx, dtype=dtype,
                     projection=tuple(proj))


# ---------- from_dict ----------

def from_dict(d: dict, base_dir: 'str | None' = None) -> Config:
    if not isinstance(d, dict):
        raise ConfigError('config must be a JSON object')
    _unknown(d, _TOP_KEYS, 'config')
    if d.get('version') != VERSION or isinstance(d.get('version'), bool):
        raise ConfigError('version must be %d' % VERSION)

    home = home_dir(d, base_dir)

    # roots
    raw_roots = d.get('roots')
    if not isinstance(raw_roots, list) or not raw_roots:
        raise ConfigError('roots must be a non-empty list')
    roots, seen = [], set()
    for i, r in enumerate(raw_roots):
        if not isinstance(r, dict):
            raise ConfigError('roots[%d] must be an object' % i)
        _unknown(r, _ROOT_KEYS, 'roots[%d]' % i)
        name = r.get('name')
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ConfigError('root name must match ^[a-z0-9][a-z0-9_-]{0,39}$')
        if name in seen:
            raise ConfigError('duplicate root name: %s' % name)
        seen.add(name)
        kind = _need_choice(r.get('kind'), 'roots[%d].kind' % i, KINDS)
        path = _abs(_need_str(r.get('path'), 'roots[%d].path' % i), base_dir)
        inc = r.get('include')
        include = DEFAULT_INCLUDE[kind] if inc is None else _patterns(inc, 'roots[%d].include' % i)
        exc = r.get('exclude')
        exclude = () if exc is None else _patterns(exc, 'roots[%d].exclude' % i)
        follow = _need_bool(r.get('follow_links', False), 'roots[%d].follow_links' % i)
        roots.append(Root(name, path, kind, include, exclude, follow))

    # profiles
    raw_prof = d.get('profiles')
    if raw_prof is None:
        raw_prof = {}
    if not isinstance(raw_prof, dict):
        raise ConfigError('profiles must be an object')
    profiles = {'default': [r.name for r in roots]}
    for pname, pv in raw_prof.items():
        if pname == 'default':
            raise ConfigError('profile default is built in and cannot be defined')
        if not isinstance(pname, str) or not NAME_RE.match(pname):
            raise ConfigError('profile name must match ^[a-z0-9][a-z0-9_-]{0,39}$')
        if not isinstance(pv, dict):
            raise ConfigError('profile %s must be an object' % pname)
        _unknown(pv, {'roots'}, 'profile ' + pname)
        pr = pv.get('roots')
        if not isinstance(pr, list) or not pr or not all(isinstance(x, str) for x in pr):
            raise ConfigError('profile %s needs a non-empty roots list' % pname)
        for x in pr:
            if x not in seen:
                raise ConfigError('profile %s names unknown root: %s' % (pname, x))
        profiles[pname] = list(dict.fromkeys(pr))
    dp = d.get('default_profile', 'default')
    if not isinstance(dp, str) or dp not in profiles:
        raise ConfigError('default_profile names an unknown profile')

    # classify
    classify = copy.deepcopy(_TEMPLATE['classify'])
    given = d.get('classify')
    if given is not None:
        if not isinstance(given, dict):
            raise ConfigError('classify must be an object')
        _unknown(given, CLASSES, 'classify')
        for k, v in given.items():
            classify[k] = list(_patterns(v, 'classify.' + k))

    # models
    m = d.get('models')
    if m is None:
        m = {}
    if not isinstance(m, dict):
        raise ConfigError('models must be an object')
    _unknown(m, _MODELS_KEYS, 'models')
    backend = _need_choice(m.get('backend', 'torch'), 'models.backend', BACKENDS)
    if backend == 'fake' and os.environ.get('LEKS_ALLOW_FAKE') != '1':
        raise ConfigError('backend fake needs LEKS_ALLOW_FAKE=1')
    device = _need_choice(m.get('device', 'auto'), 'models.device', DEVICES)
    wd = m.get('weights_dir')
    weights_dir = os.path.join(home, 'models') if wd is None else _abs(_need_str(wd, 'models.weights_dir'), base_dir)
    emb = _spec(m['embedder'] if 'embedder' in m else _TEMPLATE['models']['embedder'], 'embedder')
    rer = _spec(m['reranker'] if 'reranker' in m else _TEMPLATE['models']['reranker'], 'reranker')
    if emb and rer and emb.key == rer.key:
        raise ConfigError('embedder and reranker need different keys')

    # plain sections
    budgets = _section(d, 'budgets')
    for k, v in budgets.items():
        budgets[k] = _need_num(v, 'budgets.' + k)
    daemon = _section(d, 'daemon')
    daemon['idle_exit'] = _need_int(daemon['idle_exit'], 'daemon.idle_exit')
    daemon['sandbox'] = _need_choice(daemon['sandbox'], 'daemon.sandbox', ('auto', 'on', 'off'))
    update = _section(d, 'update')
    update['min_interval'] = _need_num(update['min_interval'], 'update.min_interval', 0.0, strict=False)
    ocr = _section(d, 'ocr')
    en = ocr['enabled']
    if en is True:
        en = 'on'
    elif en is False:
        en = 'off'
    ocr['enabled'] = _need_choice(en, 'ocr.enabled', ('auto', 'on', 'off'))
    ocr['images'] = _need_bool(ocr['images'], 'ocr.images')
    if ocr['helper'] is not None:
        # relative to the config folder like every other path: hook jobs run in /
        ocr['helper'] = _abs(_need_str(ocr['helper'], 'ocr.helper'), base_dir)
    # the helper reads at most 2000 pages, so a bigger number never reaches it
    ocr['max_pages'] = _need_int(ocr['max_pages'], 'ocr.max_pages', 1, 2000)
    # on by default: results say when their text came from OCR, with its confidence. set it false to hide that line
    ocr['label_results'] = _need_bool(ocr['label_results'], 'ocr.label_results')
    limits = _section(d, 'limits')
    for k, v in limits.items():
        limits[k] = _need_int(v, 'limits.' + k)
    server = _section(d, 'server')
    server['always_load'] = _need_bool(server['always_load'], 'server.always_load')
    check = _section(d, 'check')
    check['em_dash'] = _need_bool(check['em_dash'], 'check.em_dash')

    return Config(home=home, roots=roots, profiles=profiles, default_profile=dp, classify=classify,
                  backend=backend, device=device, weights_dir=weights_dir, embedder=emb, reranker=rer,
                  budgets=budgets, daemon=daemon, update=update, ocr=ocr, limits=limits, server=server,
                  check=check)


# ---------- loading ----------

def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ConfigError('duplicate key: %s' % k)
        out[k] = v
    return out


def _bad_const(name):
    raise ConfigError('bad number in config: %s' % name)


def parse_text(text: str) -> dict:
    """JSON text to a dict, failing on duplicate keys and NaN/Infinity."""
    try:
        return json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_bad_const)
    except json.JSONDecodeError as e:
        raise ConfigError('config is not valid json (line %d, column %d)' % (e.lineno, e.colno)) from e
    except RecursionError:
        raise ConfigError('config is nested too deep') from None


def _find(path):
    """Config file by the documented order. Returns an absolute path."""
    if path:
        p = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(p):
            raise ConfigError('no config: run leks init (not found: %s)' % p)
        return p
    env = os.environ.get('LEKS_CONFIG')
    if env:
        p = os.path.abspath(os.path.expanduser(env))
        if not os.path.isfile(p):
            raise ConfigError('no config: run leks init (not found: %s)' % p)
        return p
    lh = os.environ.get('LEKS_HOME')
    cands = []
    if lh:
        cands.append(os.path.join(_abs(lh, None), 'config.json'))
    cands.append(os.path.join(_abs('~/.leks', None), 'config.json'))
    for p in cands:
        if os.path.isfile(p):
            return p
    raise ConfigError('no config: run leks init')


def load(path: 'str | None' = None) -> Config:
    p = _find(path)
    try:
        if os.path.getsize(p) > MAX_CONFIG_BYTES:
            raise ConfigError('config file is too big')
        with open(p, 'rb') as f:
            text = f.read().decode('utf-8')
    except UnicodeDecodeError:
        raise ConfigError('config is not utf-8') from None
    except OSError:
        raise ConfigError('cannot read config') from None
    cfg = from_dict(parse_text(text), base_dir=os.path.dirname(p))
    cfg.source_path = p
    return cfg


def active_profile(cfg: Config) -> str:
    name = os.environ.get('LEKS_PROFILE') or cfg.default_profile
    if name not in cfg.profiles:
        raise ConfigError('unknown profile: %s' % name)
    return name
