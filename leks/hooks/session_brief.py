"""Session start brief: at most five lines, read-only, no model, no network.

Next to the search tools it names the state notes, the notes changed in the last
days, the notes past their freshness budget (the config's freshness section) and
the revisit lines whose date has come (leks/revisit.py). A note's age comes from
its last commit when its folder is in a git repo, else from its file time. The
notes past their budget come most important first: state notes, then rules,
decisions, results, other notes and the rest, and within one class the note
furthest past its budget. A part that cannot be read is left out.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata

FIRST = ("Project search is on (leks): search for this project's notes, documents and code, "
         "read for cited lines, locate for files by name, tried before a proposal.")

# the index text goes into a hook output at every session start, so only plain display paths get through
PLAIN = re.compile(r"[\w ./()@+,&'-]{1,300}")
DAYS = 3
MAX_LIST = 6              # names per line of freshness and revisit
MAX_ROWS = 1000           # notes with a freshness budget that get an age
MAX_REVISIT = 200         # notes that mention a revisit date that get read
GIT_SECS = 2.0            # every git call of one brief together
# characters of note paths per git call: windows cuts a command line at 32,767, elsewhere the limit is far off
ARG_CHARS = 24000 if os.name == 'nt' else 1 << 20
# a record keeps what held at its time and a stale note says so already, no budget makes them stale
UNBUDGETED = ('superseded', 'stale', 'historical-record', 'record')
# the order of the freshness line: what claims to hold now comes first
IMPORTANCE = ('state', 'rule', 'decision', 'result', 'note', 'code', 'document', 'image')
# git reads the repo from these when set, a brief asks the repo of the note's own folder
_GIT_PLACE = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
              'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_NAMESPACE')
_GIT = ('-c', 'core.fsmonitor=false', '-c', 'log.showSignature=false', '--no-pager',
        '--no-optional-locks', '--literal-pathspecs')


def _open_ro(path):
    c = sqlite3.connect('file:' + path + '?mode=ro', uri=True, timeout=3)
    c.execute('PRAGMA temp_store=MEMORY')
    # a view with the right name must not run anything
    c.execute('PRAGMA trusted_schema=OFF')
    return c


def _plain(cfg, p):
    if not isinstance(p, str) or not PLAIN.fullmatch(p) or '..' in p:
        return False
    try:
        a = cfg.abs_path(p)
    except Exception:
        return False
    return bool(a) and os.path.exists(a)


# ---------- ages from git ----------

def _git(folder, args, end, feed=None):
    """Run git in folder until it exits or the deadline passes, its stdout going to feed(piece), which
    returns True to stop early. True when git ran to its end with exit code 0."""
    exe = shutil.which('git')
    left = end - time.monotonic()
    if not exe or left <= 0:
        return False
    env = {k: v for k, v in os.environ.items() if k not in _GIT_PLACE}
    env['GIT_TERMINAL_PROMPT'] = '0'
    try:
        p = subprocess.Popen((exe, '-C', folder) + _GIT + tuple(args), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    except OSError:
        return False
    timer = threading.Timer(left, p.kill)
    timer.start()
    stopped = False
    try:
        while True:
            b = p.stdout.read1(65536)
            if not b:
                break
            if feed is not None and feed(b):
                stopped = True
                break
    finally:
        timer.cancel()
        if p.poll() is None:
            p.kill()
        p.stdout.close()
        code = p.wait()
    return not stopped and code == 0 and time.monotonic() < end


def _nfc(b):
    return unicodedata.normalize('NFC', b.decode('utf-8', errors='replace'))


def _batches(rels):
    """The rels in runs of at most ARG_CHARS characters, so no git command line grows past what the system takes."""
    out, cur, size = [], [], 0
    for r in rels:
        if cur and size + len(r) + 1 > ARG_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += len(r) + 1
    if cur:
        out.append(cur)
    return out


def _uncommitted(folder, rels, end):
    """The rels with changes not committed yet, None when git cannot tell (no repo, no commit, no time)."""
    out = []
    for part in _batches(rels):
        ok = _git(folder, ('diff', 'HEAD', '--name-only', '-z', '--relative', '--no-renames', '--no-ext-diff',
                           '--no-textconv', '--') + tuple(part), end, lambda b: out.append(b))
        if not ok:
            return None
    return {_nfc(n) for n in b''.join(out).split(b'\0') if n}


def _is_meta(trailers):
    return any(v.strip().lower() == 'true' for v in trailers.decode('utf-8', errors='replace').splitlines())


def _commit_times(folder, rels, end):
    """{rel: unix time of the last commit that touched it}, commits with the trailer Meta: true skipped.
    A rel git does not reach in time stays out. A long list goes to git in parts (_batches)."""
    got = {}
    for part in _batches(rels):
        got.update(_commit_times_of(folder, part, end))
    return got


def _commit_times_of(folder, rels, end):
    if not rels:
        return {}
    want, got, buf = {unicodedata.normalize('NFC', r) for r in rels}, {}, [b'']

    def take(rec):
        # \x1e <committer time> \x1f <Meta trailer values> \x1f \0 \n name \0 name \0 ...
        parts = rec.split(b'\x1f', 2)
        if len(parts) != 3 or _is_meta(parts[1]):
            return
        try:
            t = int(parts[0])
        except ValueError:
            return
        for n in parts[2].split(b'\0'):
            n = _nfc(n.lstrip(b'\n'))
            if n in want and n not in got:
                got[n] = t

    def feed(b):
        recs = (buf[0] + b).split(b'\x1e')
        buf[0] = recs.pop()
        for rec in recs:
            take(rec)
        return len(got) == len(want)

    if _git(folder, ('log', '-z', '--name-only', '--relative', '--no-renames', '--no-ext-diff', '--no-textconv',
                     '--format=%x1e%ct%x1f%(trailers:key=Meta,valueonly)%x1f', '--') + tuple(rels), end, feed):
        take(buf[0])
    return got


def _git_times(cfg, rows, end):
    """{display path: time of its last commit} for the rows whose note is committed as it is on disk."""
    by_root = {}
    for path, root, rel in rows:
        by_root.setdefault(root, {})[unicodedata.normalize('NFC', rel)] = (rel, path)
    out = {}
    for root, paths in sorted(by_root.items()):
        try:
            folder = cfg.root(root).path
        except (KeyError, AttributeError):
            continue
        dirty = _uncommitted(folder, sorted(r for r, _ in paths.values()), end)
        if dirty is None:
            continue
        clean = sorted(r for k, (r, _) in paths.items() if k not in dirty)
        for key, t in _commit_times(folder, clean, end).items():
            out[paths[key][1]] = t
    return out


# ---------- freshness ----------

def _sql_glob(g):
    """A config glob as an sqlite GLOB that matches at least as much, for a first cut in sql."""
    return g.replace('**/', '*').replace('**', '*').replace('[!', '[^')


def _budgeted(c, fresh):
    """(display path, root, rel, budget days, kind) of the notes with a freshness budget. A path glob
    beats the class budget, and of several path globs the smallest budget wins. A note with a newer
    version is history and has no budget."""
    from leks import config
    classes, paths = fresh.get('classes') or {}, fresh.get('paths') or {}
    if not classes and not paths:
        return []
    where, args = [], list(UNBUDGETED)
    if classes:
        where.append('kind IN (%s)' % ','.join('?' * len(classes)))
        args += list(classes)
    for g in paths:
        where.append('rel GLOB ?')
        args.append(_sql_glob(g))
    newer = c.execute("SELECT type FROM sqlite_master WHERE name='newer'").fetchone() == ('table',)
    sql = ('SELECT path, root, rel, kind FROM docs WHERE status NOT IN (%s) AND (%s)%s ORDER BY mtime LIMIT %d'
           % (','.join('?' * len(UNBUDGETED)), ' OR '.join(where),
              ' AND id NOT IN (SELECT doc FROM newer)' if newer else '', MAX_ROWS))
    out = []
    for path, root, rel, kind in c.execute(sql, args):
        hit = [d for g, d in paths.items() if isinstance(rel, str) and config.glob_match(rel, g)]
        days = min(hit) if hit else classes.get(kind)
        if days:
            out.append((path, root, rel, days, kind))
    return out


def _overdue(cfg, c, fresh, now):
    """'path (N days, budget M)' for the notes past their budget, most important first: by class in the
    IMPORTANCE order, then the furthest past its budget (age over budget), then by path."""
    rows = [r for r in _budgeted(c, fresh) if _plain(cfg, r[0])]
    if not rows:
        return []
    times = _git_times(cfg, [r[:3] for r in rows], time.monotonic() + GIT_SECS) if fresh.get('git', True) else {}
    late = []
    for path, _, _, days, kind in rows:
        t = times.get(path)
        if t is None:
            try:
                t = os.stat(cfg.abs_path(path)).st_mtime
            except (OSError, TypeError, ValueError):
                continue
        age = int(max(0.0, now - t) // 86400)
        if age > days:
            rank = IMPORTANCE.index(kind) if kind in IMPORTANCE else len(IMPORTANCE)
            late.append((rank, -age / days, path, age, days))
    late.sort()
    return ['%s (%d days, budget %d)' % (p, age, days) for _, _, p, age, days in late[:MAX_LIST]]


# ---------- revisit dates ----------

def _due(cfg, c, today):
    """'path:line (date)' for the revisit lines whose date is today or earlier, oldest first."""
    from leks import revisit
    found = revisit.entries(c, cfg, today, dated_only=True, ok=lambda p: _plain(cfg, p),
                            limit=MAX_REVISIT)
    return ['%s:%d (%s)' % (e['path'], e['line'], e['date']) for e in found if e['state'] == 'due'][:MAX_LIST]


def _part(fn, *args):
    # a part that cannot be read is left out, the rest of the brief still goes out
    try:
        return fn(*args)
    except Exception:
        return []


def brief(cfg, profile, now=None):
    """The brief for one profile. Empty string when there is no readable index yet."""
    now = time.time() if now is None else now
    db = cfg.profile_db(profile)
    if not os.path.isfile(db):
        return ''
    fresh = getattr(cfg, 'freshness', None)
    c = _open_ro(db)
    try:
        if c.execute("SELECT type FROM sqlite_master WHERE name='docs'").fetchone() != ('table',):
            return ''
        st = [r[0] for r in c.execute(
            "SELECT path FROM docs WHERE kind='state' AND status!='historical-record' "
            "ORDER BY (name='STATE.md') DESC, date DESC, path LIMIT 12")]
        ch = [r[0] for r in c.execute(
            "SELECT path FROM docs WHERE mtime>? AND kind!='code' ORDER BY mtime DESC LIMIT 12",
            (now - DAYS * 86400,))]
        late = _part(_overdue, cfg, c, fresh, now) if isinstance(fresh, dict) else []
        due = _part(_due, cfg, c, datetime.date.fromtimestamp(now).isoformat())
    finally:
        c.close()
    st = [p for p in st if _plain(cfg, p)][:4]
    ch = [p for p in ch if _plain(cfg, p)][:6]
    lines = [FIRST]
    if st:
        lines.append('State notes: ' + ', '.join(st))
    if ch:
        lines.append('Changed in the last %d days: ' % DAYS + '; '.join(ch))
    if late:
        lines.append('Past their freshness budget: ' + '; '.join(late))
    if due:
        lines.append('Revisit date passed: ' + '; '.join(due))
    return '\n'.join(lines)


def hook_json(text):
    """The one json line a SessionStart hook prints. Plain text stdout gets dropped by some hosts."""
    return json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': text}})


def main():
    """Print the brief as hook json. Empty brief or any error prints nothing. Always 0."""
    try:
        from leks import config
        cfg = config.load()
        text = brief(cfg, config.active_profile(cfg))
        if text:
            sys.stdout.write(hook_json(text) + '\n')
            sys.stdout.flush()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
