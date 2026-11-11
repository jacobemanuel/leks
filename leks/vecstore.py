"""Where the vectors live, and how a search opens them.

One store per model key, shared by every profile (a profile is a relevance scope,
not an access boundary):

    <home>/index/vectors/<key>/store.sqlite   emb(h, vec), meta(k, v)
    <home>/index/vectors/<key>/<profile>.f32  n * dim float32, row i = vecmap row i
    <home>/index/vectors/<key>/<profile>.json side file: key, dim, n, covered, built, mtime

The side file is written last, so a half written state reads as out of step and
the meaning lane stays closed. Stdlib only, numpy is needed just to map the file.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.parse

WHY_NO_MODELS = 'no models configured'
WHY_NO_NUMPY = 'numpy not installed'
WHY_NO_VECTORS = 'no vectors yet'
WHY_OUT_OF_STEP = 'vectors out of step with the index'

_KEY = re.compile(r'^[a-z0-9_]{1,32}$')
_PROFILE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')
_BLOCK = 400


def _key(key):
    if not isinstance(key, str) or not _KEY.match(key):
        raise ValueError('bad model key')
    return key


def _profile(profile):
    if not isinstance(profile, str) or not _PROFILE.match(profile):
        raise ValueError('bad profile name')
    return profile


def vec_dir(cfg, key):
    return os.path.join(cfg.vectors_dir(), _key(key))


def store_path(cfg, key):
    return os.path.join(vec_dir(cfg, key), 'store.sqlite')


def profile_file(cfg, key, profile):
    return os.path.join(vec_dir(cfg, key), _profile(profile) + '.f32')


def side_file(cfg, key, profile):
    return os.path.join(vec_dir(cfg, key), _profile(profile) + '.json')


def _open_ro(path):
    uri = 'file:' + urllib.parse.quote(os.path.abspath(path)) + '?mode=ro'
    c = sqlite3.connect(uri, uri=True, timeout=60)
    c.execute('PRAGMA temp_store=MEMORY')
    c.execute('PRAGMA trusted_schema=OFF')
    return c


def read_side(cfg, key, profile):
    """The side file as a dict, or None when it is missing or broken."""
    try:
        with open(side_file(cfg, key, profile), 'rb') as f:
            d = json.loads(f.read().decode('utf-8'))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _embedders(cfg):
    return [s for s in (cfg.embedder, cfg.reranker) if s is not None and s.op == 'embed']


def stored_keys(cfg):
    """Embedder keys that have a store file and are still named by the config."""
    out = []
    for s in _embedders(cfg):
        if s.key not in out and os.path.exists(store_path(cfg, s.key)):
            out.append(s.key)
    return out


def _spec(cfg, key):
    spec = cfg.model(key)
    if spec is None:
        raise ValueError('model key is not in the config')
    return spec


def _store_meta(e):
    return dict(e.execute('SELECT k, v FROM meta').fetchall())


def write_profile(cfg, key, profile, db_path=None):
    """Write <profile>.f32 and its side file. Returns (covered, n).

    Rows follow vecmap order, zeros where the store has no vector. The side
    file goes last. db_path may point at a profile database that is not in
    place yet (the build writes it under a temporary name first).
    """
    spec = _spec(cfg, key)
    dim = int(spec.dim)
    db_path = db_path or cfg.profile_db(profile)
    zero = b'\0' * (dim * 4)
    c = _open_ro(db_path)
    try:
        r = c.execute("SELECT v FROM meta WHERE k='built'").fetchone()
        if r is None:
            raise ValueError('profile database has no build stamp')
        built = r[0]
        rows = c.execute('SELECT vec, ehash FROM vecmap ORDER BY vec').fetchall()
    finally:
        c.close()
    for i, (v, _) in enumerate(rows):
        if v != i:
            raise ValueError('vecmap has a gap')
    e = _open_ro(store_path(cfg, key))
    f = profile_file(cfg, key, profile)
    side = side_file(cfg, key, profile)
    hit = 0
    try:
        meta = _store_meta(e)
        if meta.get('revision') != spec.revision or meta.get('dim') != str(dim):
            raise ValueError('store was made by another revision of this model')
        try:
            with open(f + '.tmp', 'wb') as out:
                for i in range(0, len(rows), _BLOCK):
                    part = rows[i:i + _BLOCK]
                    marks = ','.join('?' * len(part))
                    have = dict(e.execute('SELECT h, vec FROM emb WHERE h IN (%s)' % marks,
                                          [h for _, h in part]).fetchall())
                    for _, h in part:
                        blob = have.get(h)
                        if blob is not None and len(blob) == len(zero):
                            out.write(blob)
                            hit += 1
                        else:
                            out.write(zero)
            os.replace(f + '.tmp', f)
        except BaseException:
            try:
                os.remove(f + '.tmp')
            except OSError:
                pass
            raise
    finally:
        e.close()
    d = {'key': key, 'dim': dim, 'n': len(rows), 'covered': hit, 'built': built,
         'mtime': os.stat(f).st_mtime}
    with open(side + '.tmp', 'w') as out:
        json.dump(d, out)
    os.replace(side + '.tmp', side)
    return hit, len(rows)


def _store_matches(cfg, spec):
    """True when the store file (if there is one) was made by this model and size."""
    p = store_path(cfg, spec.key)
    if not os.path.exists(p):
        return True
    try:
        e = _open_ro(p)
        try:
            meta = _store_meta(e)
        finally:
            e.close()
    except sqlite3.Error:
        return False
    return meta.get('revision') == spec.revision and meta.get('dim') == str(int(spec.dim))


def open_vectors(cfg, profile, built, spec):
    """Map the vector file of a profile. Returns (memmap or None, covered, why)."""
    if spec is None:
        return None, 0, WHY_NO_MODELS
    try:
        import numpy as np
    except ImportError:
        return None, 0, WHY_NO_NUMPY
    try:
        f = profile_file(cfg, spec.key, profile)
        st = os.stat(f)
    except (OSError, ValueError):
        return None, 0, WHY_NO_VECTORS
    s = read_side(cfg, spec.key, profile)
    dim = int(spec.dim)
    try:
        n, covered = s['n'], s['covered']
        ok = (s['key'] == spec.key and str(s['built']) == str(built) and s['dim'] == dim
              and type(n) is int and type(covered) is int and n > 0 and 0 <= covered <= n
              and st.st_size == n * dim * 4 and abs(st.st_mtime - s['mtime']) < 1e-3)
    except (KeyError, TypeError):
        ok = False
    if not ok or not _store_matches(cfg, spec):
        return None, 0, WHY_OUT_OF_STEP
    if covered == 0:
        return None, 0, WHY_NO_VECTORS
    mm = np.memmap(f, dtype='<f4', mode='r').reshape(n, dim)
    return mm, covered, None


def stamp(cfg, profile):
    """mtimes a long running server compares to know it must reopen."""
    paths = [cfg.profile_db(profile), getattr(cfg, 'source_path', None)]
    for s in _embedders(cfg):
        paths.append(side_file(cfg, s.key, profile))
    out = []
    for p in paths:
        try:
            out.append(os.stat(p).st_mtime if p else None)
        except OSError:
            out.append(None)
    return out
