"""Fill the vector store: embed every distinct passage input of the index once.

Resumable: a hash that is already stored is never sent again. The daemon makes
the numbers, this module keeps nothing a model says except checked, quantised
vectors. Run it with `python3 -m leks embed [--profile P]`.

The last line is `DONE <key> <count>`, or `INCOMPLETE <key> <have> of <total>`
with exit code 1. Exit code 2 means the run was refused (no embedder, no index,
a store made by another model revision, no numpy).
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import time
import urllib.parse

from . import modeld_client, vecstore

BATCH = 16
BUDGET = 300.0
TRIES = 3
WAIT = 20

# lower number is embedded first
PRIORITY = {'state': 0, 'rule': 1, 'decision': 2}
LAST_PRIORITY = 3


class StoreRefused(ValueError):
    """The store on disk was made by another revision or size of the model."""


def _open_master(cfg):
    path = cfg.master_path()
    if not os.path.exists(path):
        raise FileNotFoundError('no index yet')
    uri = 'file:' + urllib.parse.quote(os.path.abspath(path)) + '?mode=ro'
    m = sqlite3.connect(uri, uri=True, timeout=60)
    m.execute('PRAGMA temp_store=MEMORY')
    m.execute('PRAGMA trusted_schema=OFF')
    return m


def _all_hashes(cfg):
    """Every chunk hash the master knows, whatever the profile."""
    m = _open_master(cfg)
    try:
        return {r[0] for r in m.execute('SELECT DISTINCT ehash FROM chunks') if r[0]}
    finally:
        m.close()


def passages(cfg, profiles):
    """Distinct (ehash, embed input text, priority) for the roots of these profiles."""
    from . import schema
    names = sorted({r.name for p in profiles for r in cfg.roots_for(p)})
    if not names:
        return []
    m = _open_master(cfg)
    try:
        marks = ','.join('?' * len(names))
        rows = m.execute(
            'SELECT c.ehash, f.title, c.heading, c.text, f.kind FROM chunks c '
            'JOIN files f ON f.root = c.root AND f.rel = c.rel '
            'WHERE c.root IN (%s)' % marks, names).fetchall()
    finally:
        m.close()
    best = {}
    for h, title, heading, text, kind in rows:
        s = schema.embed_input(title, heading, text)
        # the stored vector must belong to the text that made the hash
        if schema.ehash(s) != h:
            continue
        pr = PRIORITY.get(kind, LAST_PRIORITY)
        old = best.get(h)
        if old is None or pr < old[2]:
            best[h] = (h, s, pr)
    return list(best.values())


def quantise(y):
    """Round to a 1/512 grid and renormalise unit rows.

    A model that misbehaves could hide bytes in the low bits of what gets
    stored. The grid leaves about one byte of room per dimension, far below
    what ranking can see.
    """
    import numpy as np
    y = np.asarray(y, dtype='float32')
    q = np.round(y * 512.0) / 512.0
    if q.ndim == 2:
        raw = np.linalg.norm(y, axis=1)
        norm = np.linalg.norm(q, axis=1)
        keep = (np.abs(raw - 1.0) < 0.02) & (norm > 0)
        q[keep] = q[keep] / norm[keep][:, None]
    else:
        raw, norm = float(np.linalg.norm(y)), float(np.linalg.norm(q))
        if abs(raw - 1.0) < 0.02 and norm > 0:
            q = q / norm
    return q.astype('<f4')


def open_store(cfg, spec):
    """Open (and create) the store of one model. Refuses another revision or dim."""
    os.makedirs(vecstore.vec_dir(cfg, spec.key), exist_ok=True)
    db = sqlite3.connect(vecstore.store_path(cfg, spec.key), timeout=60)
    db.execute('PRAGMA temp_store=MEMORY')
    db.execute('CREATE TABLE IF NOT EXISTS emb(h TEXT PRIMARY KEY, vec BLOB)')
    db.execute('CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)')
    pins = (('revision', spec.revision), ('dim', str(int(spec.dim))), ('hf_id', spec.hf_id))
    for k, v in pins:
        r = db.execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
        if r and r[0] != v:
            db.close()
            raise StoreRefused('store was made by another revision of this model')
    for k, v in pins:
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (k, v))
    db.commit()
    return db


def _ask(client, spec, texts, tries, wait):
    """Embed a batch. A reply that fails the strict check counts as none."""
    for i in range(tries):
        reset = getattr(client, 'reset', None)
        if reset:
            reset()
        v = client.embed(texts, 'd', spec.key, budget=BUDGET)
        if v is not None:
            return v
        if i + 1 < tries and wait:
            time.sleep(wait)
    return None


def _count(db, wanted):
    return sum(1 for (h,) in db.execute('SELECT h FROM emb') if h in wanted)


def _write_profiles(cfg, spec, profiles, items):
    for p in profiles:
        dbp = cfg.profile_db(p)
        if not os.path.exists(dbp):
            continue
        try:
            c = sqlite3.connect('file:' + urllib.parse.quote(os.path.abspath(dbp)) + '?mode=ro',
                                uri=True, timeout=60)
            try:
                built = c.execute("SELECT v FROM meta WHERE k='built'").fetchone()
                mapped = c.execute("SELECT 1 FROM sqlite_master WHERE name='vecmap'").fetchone()
            finally:
                c.close()
        except sqlite3.Error:
            continue
        if not mapped or not built:
            continue
        side = vecstore.read_side(cfg, spec.key, p)
        fits = (side is not None and str(side.get('built')) == str(built[0])
                and os.path.exists(vecstore.current_file(cfg, spec.key, p, side)))
        # nothing new and the files fit this build: leave them alone
        if not items and fits:
            continue
        try:
            covered, n = vecstore.write_profile(cfg, spec.key, p)
        except (ValueError, OSError, sqlite3.Error):
            print('profile', p, 'vectors not written', flush=True)
            continue
        print('profile', p, 'covered', covered, 'of', n, flush=True)


def run(cfg, profile=None, client=None, tries=TRIES, wait=WAIT):
    """Embed what is missing. Returns 0 done, 1 incomplete, 2 refused."""
    spec = cfg.embedder
    if spec is None:
        print('refused: no embedder configured', flush=True)
        return 2
    try:
        importlib.import_module('numpy')
    except ImportError:
        print('refused: numpy not installed', flush=True)
        return 2
    if profile is not None and profile not in cfg.profiles:
        print('refused: unknown profile', flush=True)
        return 2
    profiles = [profile] if profile is not None else list(cfg.profiles)
    try:
        rows = passages(cfg, profiles)
        known = _all_hashes(cfg)
    except (OSError, sqlite3.Error):
        print('refused: no index yet, run a build first', flush=True)
        return 2
    try:
        db = open_store(cfg, spec)
    except StoreRefused as e:
        print('refused:', e, flush=True)
        return 2
    if client is None:
        client = modeld_client.Client.from_config(cfg)
    have = {r[0] for r in db.execute('SELECT h FROM emb')}
    # a stored hash that no chunk of the master has is stale
    gone = have - known
    db.executemany('DELETE FROM emb WHERE h=?', [(h,) for h in gone])
    db.commit()
    have -= gone
    print('pruned', len(gone), flush=True)
    wanted = {r[0] for r in rows}
    items = [r for r in rows if r[0] not in have]
    items.sort(key=lambda r: (r[2], len(r[1])))
    print('to embed', len(items), 'of', len(rows), flush=True)
    t0 = time.time()
    failed = 0
    stopped = False
    for i in range(0, len(items), BATCH):
        part = items[i:i + BATCH]
        v = _ask(client, spec, [r[1] for r in part], tries, wait)
        got = [(part, v)] if v is not None else []
        if v is None:
            # daemon gone: stop and resume later. one bad passage: only that one stays out
            if not client.ping():
                db.commit()
                print('STOPPED at', i, 'of', len(items), 'the daemon does not answer, run again to resume',
                      flush=True)
                stopped = True
                break
            for r in part:
                w = _ask(client, spec, [r[1]], 1, 0)
                if w is None:
                    failed += 1
                else:
                    got.append(([r], w))
        for pp, vv in got:
            db.executemany('INSERT OR REPLACE INTO emb VALUES(?,?)',
                           [(r[0], quantise(y).tobytes()) for r, y in zip(pp, vv, strict=True)])
        if (i // BATCH) % 10 == 0:
            db.commit()
        if (i // BATCH) % 50 == 0 or i + BATCH >= len(items):
            done = min(i + BATCH, len(items))
            print('embedded', done, 'of', len(items), 'per sec',
                  round(done / (time.time() - t0 + 1e-6), 1), flush=True)
    db.commit()
    count = _count(db, wanted)
    db.close()
    # coverage lines first, the verdict stays the last line
    _write_profiles(cfg, spec, profiles, items)
    complete = not stopped and not failed and count == len(wanted)
    if complete:
        print('DONE', spec.key, count, flush=True)
    else:
        print('INCOMPLETE', spec.key, count, 'of', len(wanted), flush=True)
    client.bye()
    return 0 if complete else 1
