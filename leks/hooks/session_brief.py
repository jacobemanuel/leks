"""Session start brief: at most three lines, read-only, no model, no network."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time

FIRST = ("Project search is on (leks): search for this project's notes, documents and code, "
         "read for cited lines, locate for files by name, tried before a proposal.")

# the index text goes into a hook output at every session start, so only plain display paths get through
PLAIN = re.compile(r"[\w ./()@+,&'-]{1,300}")
DAYS = 3


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


def brief(cfg, profile):
    """The brief for one profile. Empty string when there is no readable index yet."""
    db = cfg.profile_db(profile)
    if not os.path.isfile(db):
        return ''
    c = _open_ro(db)
    try:
        if c.execute("SELECT type FROM sqlite_master WHERE name='docs'").fetchone() != ('table',):
            return ''
        st = [r[0] for r in c.execute(
            "SELECT path FROM docs WHERE kind='state' AND status!='historical-record' "
            "ORDER BY (name='STATE.md') DESC, date DESC, path LIMIT 12")]
        ch = [r[0] for r in c.execute(
            "SELECT path FROM docs WHERE mtime>? AND kind!='code' ORDER BY mtime DESC LIMIT 12",
            (time.time() - DAYS * 86400,))]
    finally:
        c.close()
    st = [p for p in st if _plain(cfg, p)][:4]
    ch = [p for p in ch if _plain(cfg, p)][:6]
    lines = [FIRST]
    if st:
        lines.append('State notes: ' + ', '.join(st))
    if ch:
        lines.append('Changed in the last %d days: ' % DAYS + '; '.join(ch))
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
