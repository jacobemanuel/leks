"""Notes check for the end of an agent's turn: python3 -m leks check.

It walks the notes (.md, .markdown) under the roots, the current folder by
default, and looks only at notes that changed since its last run, so the first
run records and reports nothing. A changed note is checked for being empty, for
dead links ([[wikilinks]] and relative markdown links) and, unless switched off,
for em dashes. A new note that no other note links to is reported, and so is a
link in another note to a note that was removed or renamed.

Exit codes follow the stop hook of Claude Code (Stop) and Copilot (agentStop).
0: clean, nothing printed. 2: problems, listed on stderr, which the host hands
back to the agent so it fixes them before it stops. When the hook input says
this stop was already held once (stop_hook_active), the check lets it through
at once and keeps its record, so a turn is held at most once and what changed
during the fix is looked at by the next check. 1: the check could not run.
Standard library only, no index and no model needed.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import stat
import sys
import threading
import time
import unicodedata
from urllib.parse import unquote

from . import config

EM_DASH = '\u2014'
NOTE_EXT = ('.md', '.markdown')
# entry notes, nothing has to link to these
ENTRY_STEMS = frozenset({'readme', 'index', 'home', 'agents', 'claude', 'changelog', 'license',
                         'contributing', 'security', 'code_of_conduct'})
MAX_FILES = 100000          # a bigger tree is most likely not a notes folder
MAX_READ = 2 * 1024 * 1024  # bytes read per note
MAX_INPUT = 1024 * 1024     # hook input
MAX_STATE = 64 * 1024 * 1024
MAX_LINES = 20              # header included, the host shows all of it to the agent
SCAN_SECS = 8.0             # the scan of all notes for orphans stops here, hook timeouts are about 20 s
WAIT = 1.0                  # a host that never closes stdin costs this much

_WIKI = re.compile(r'\[\[([^\[\]\n]+?)\]\]')
_MD = re.compile(r'\]\(\s*(?:<([^<>\n]+)>|([^()\s<>]+))(?:\s+"[^"\n]*")?\s*\)')
_SCHEME = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*:')
_FENCE = re.compile(r' {0,3}(`{3,}|~{3,})')
_INLINE = re.compile(r'(`+)(?!`)[^\n]+?(?<!`)\1(?!`)')
_COMMENT = re.compile(r'<!--.*?-->|%%.*?%%', re.S)
_TEMPLATING = ('{{', '}}', '<%', '%>')
ORPHAN = 'new note that no other note links to, link it from a related note or an index'


class _TooBig(Exception):
    pass


# ---------- hook input ----------

def _parse(raw):
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_INPUT:
        return {}
    try:
        d = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    return d if isinstance(d, dict) else {}


def hook_input(stream=None, wait=WAIT):
    """The host's hook json from stdin, {} when there is none. A terminal is never read, a pipe for at most `wait` s."""
    stream = sys.stdin if stream is None else stream
    try:
        if stream is None or stream.isatty():
            return {}
    except Exception:
        return {}
    try:
        fd = stream.fileno()
    except Exception:
        fd = None
    if fd is None:
        try:
            return _parse(stream.read(MAX_INPUT + 1))
        except Exception:
            return {}
    got = []

    def pull():
        # os.read and not the buffered reader: a thread stuck in a read must not hold its lock at exit
        n = 0
        try:
            while n <= MAX_INPUT:
                b = os.read(fd, 65536)
                if not b:
                    break
                got.append(b)
                n += len(b)
        except OSError:
            pass

    t = threading.Thread(target=pull, daemon=True)
    t.start()
    t.join(wait)
    return _parse(b''.join(list(got)))


def _held(d):
    return d.get('stop_hook_active') is True or d.get('stopHookActive') is True


def _base(d, cwd):
    """The folder a run walks by default and reads a relative --root from.

    claude code runs the stop hook in the session's current folder, and that follows a cd in
    its shell tool. its project folder stays put, so a hook run (host json on stdin) uses it.
    a run from a terminal keeps the current folder.
    """
    if d:
        p = os.environ.get('CLAUDE_PROJECT_DIR')
        if p:
            p = os.path.realpath(os.path.expanduser(p))
            if os.path.isdir(p):
                return p
    return cwd


# ---------- files and the record ----------

def _walk(dirs):
    """Every file under the roots (dot and build folders skipped) and the notes with their stamps."""
    files, notes, seen = [], {}, set()
    for root in dirs:
        for d, subs, names in os.walk(root):
            subs[:] = sorted(s for s in subs if not s.startswith('.') and s not in config.ALWAYS_EXCLUDE_DIRS)
            rel_d = os.path.relpath(d, root)
            for n in names:
                if n.startswith('.') or n.startswith(config.JUNK_PREFIXES):
                    continue
                p = os.path.join(d, n)
                if p in seen:
                    continue
                seen.add(p)
                if len(seen) > MAX_FILES:
                    raise _TooBig(root)
                rel = n if rel_d == '.' else os.path.join(rel_d, n)
                note = n.lower().endswith(NOTE_EXT)
                files.append((rel, note))
                if not note:
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                # a pipe or a socket named x.md would hang the read
                if stat.S_ISREG(st.st_mode):
                    notes[p] = (root, rel, [st.st_mtime_ns, st.st_size])
    return files, notes


def _config():
    try:
        return config.load()
    except Exception:
        return None


def _record_path(dirs, cfg):
    try:
        run_dir = cfg.run_dir() if cfg is not None else os.path.join(config.home_dir(None), 'run')
    except Exception:
        run_dir = os.path.join(os.path.expanduser('~'), '.leks', 'run')
    key = hashlib.sha256('\n'.join(sorted(dirs)).encode('utf-8', 'surrogatepass')).hexdigest()[:12]
    return os.path.join(run_dir, 'check-%s.json' % key)


def _load_record(path):
    """{note path: [mtime_ns, size]} from the last run, None when there is none (or it is broken)."""
    try:
        if os.path.getsize(path) > MAX_STATE:
            return None
        with open(path, 'rb') as f:
            d = json.loads(f.read().decode('utf-8'))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(d, dict) or d.get('v') != 1 or not isinstance(d.get('notes'), dict):
        return None
    return {k: v for k, v in d['notes'].items()
            if isinstance(v, list) and len(v) == 2 and all(type(x) is int for x in v)}


def _save_record(path, notes):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    data = json.dumps({'v': 1, 'notes': {p: s for p, (_, _, s) in notes.items()}}, ensure_ascii=True)
    try:
        with open(tmp, 'w', encoding='ascii') as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(p):
    try:
        with open(p, 'rb') as f:
            raw = f.read(MAX_READ)
    except OSError:
        return None
    return raw.decode('utf-8', errors='replace')


def _real_file(f):
    p = os.path.abspath(os.path.expanduser(f))
    return os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))


def _rel_under(p, dirs):
    for d in dirs:
        if p.startswith(d.rstrip(os.sep) + os.sep):
            return os.path.relpath(p, d)
    return os.path.basename(p)


# ---------- reading a note ----------

def _empty(text):
    """Nothing but blanks once a byte order mark and the yaml front matter are gone."""
    t = text.lstrip('\ufeff')
    if t.startswith('---'):
        lines = t.split('\n')
        if lines[0].strip() == '---':
            for i in range(1, len(lines)):
                if lines[i].strip() in ('---', '...'):
                    t = '\n'.join(lines[i + 1:])
                    break
    return not t.strip()


def _blank(m):
    return re.sub(r'[^\n]', ' ', m.group(0))


def _mask(text):
    """Code blocks, code spans and comments blanked out. Offsets and line breaks stay where they were."""
    lines = text.split('\n')
    fence = None
    for i, line in enumerate(lines):
        if fence is None:
            m = _FENCE.match(line)
            if m:
                fence = m.group(1)
                lines[i] = ' ' * len(line)
            continue
        s = line.strip()
        if len(s) >= len(fence) and s == fence[0] * len(s):
            fence = None
        lines[i] = ' ' * len(line)
    text = _COMMENT.sub(_blank, '\n'.join(lines))
    return _INLINE.sub(_blank, text)


def _links(text):
    """(offset, target, is_wikilink) for each link that names a file. Anchors, urls and templates are left out."""
    masked = _mask(text)
    out = []
    for m in _WIKI.finditer(masked):
        t = m.group(1).split('|', 1)[0]
        if t.endswith('\\'):
            t = t[:-1]   # [[a\|b]] inside a table
        t = t.split('#', 1)[0].strip()
        if t and not any(x in t for x in _TEMPLATING):
            out.append((m.start(), t, True))
    for m in _MD.finditer(masked):
        t = (m.group(1) or m.group(2) or '').strip()
        if not t or t.startswith('#') or _SCHEME.match(t):
            continue
        t = unquote(re.split(r'[#?]', t, maxsplit=1)[0]).strip()
        if t and not any(x in t for x in _TEMPLATING):
            out.append((m.start(), t, False))
    return out


def _line(text, pos):
    return text.count('\n', 0, pos) + 1


# ---------- resolving links ----------

def _norm(p):
    return unicodedata.normalize('NFC', p).replace('\\', '/').casefold()


def _tails(rel):
    """Every name a link may use for a note: each path suffix, with and without the extension."""
    parts = _norm(rel).split('/')
    out = set()
    for i in range(len(parts)):
        t = '/'.join(parts[i:])
        out.add(t)
        out.add(posixpath.splitext(t)[0])
    return out


class _Names:
    """Path suffix lookup over every file under the roots, notes also without their extension."""

    def __init__(self, files):
        self.by_base = {}
        for rel, note in files:
            r = _norm(rel)
            self._put(r)
            if note:
                self._put(posixpath.splitext(r)[0])

    def _put(self, r):
        self.by_base.setdefault(r.rsplit('/', 1)[-1], []).append(r)

    def has(self, key):
        tail = '/' + key
        return any(r == key or r.endswith(tail) for r in self.by_base.get(key.rsplit('/', 1)[-1], ()))


def _keys(target, rel_dir):
    """What a link can mean: the name as written and the path from the note's folder. None when it leaves the root."""
    t = _norm(target)
    while t.startswith('./'):
        t = t[2:]
    j = posixpath.normpath(posixpath.join(rel_dir, t))
    if j == '..' or j.startswith('../'):
        return None
    keys = {j.lstrip('/'), t.lstrip('/')}
    keys.discard('')
    keys.discard('.')
    return keys


def _resolves(keys, names, note_dir, root, target):
    if any(names.has(k) for k in keys):
        return True
    # folders, and files in folders the walk skips
    for base in (note_dir, root):
        p = os.path.join(base, target.lstrip('/'))
        try:
            if os.path.exists(p) or os.path.exists(p + '.md'):
                return True
        except (ValueError, OSError):
            pass
    return False


# ---------- output ----------

def _clean(s, cap=0):
    s = ''.join(c if c.isprintable() else '?' for c in s)
    if cap and len(s) > cap:
        s = s[:cap - 3] + '...'
    return s


def _show(p, cwd):
    if cwd and p.startswith(cwd.rstrip(os.sep) + os.sep):
        p = os.path.relpath(p, cwd)
    return _clean(p)


def _dead(target, wiki, why):
    t = _clean(target, 80)
    return 'dead link %s, %s' % ('[[%s]]' % t if wiki else '(%s)' % t, why)


def _say(err, lines):
    try:
        err.write('\n'.join(lines) + '\n')
        err.flush()
    except (OSError, ValueError):
        pass


def _fail(err, msg):
    _say(err, ['leks check: ' + msg])
    return 1


# ---------- the check ----------

def run(roots=None, changed=None, em_dash=True, stdin=None, err=None):
    """One check. Returns the exit code, findings go to err (stderr), stdout stays empty."""
    err = sys.stderr if err is None else err
    d = hook_input(stdin)
    if _held(d):
        return 0
    try:
        cwd = os.path.realpath(os.getcwd())
    except OSError:
        cwd = None
    base = _base(d, cwd)
    dirs = []
    for r in roots or ([base] if base else []):
        r = os.path.expanduser(r)
        p = os.path.realpath(os.path.join(base, r) if base else r)
        if not os.path.isdir(p):
            return _fail(err, 'no such folder: %s' % _show(p, base))
        if p not in dirs:
            dirs.append(p)
    if not dirs:
        return _fail(err, 'the current folder is gone, pass --root')
    cfg = _config()
    # a missing or broken config never stops the check, it only loses the switch
    if cfg is not None and cfg.check.get('em_dash') is False:
        em_dash = False
    try:
        files, notes = _walk(dirs)
    except _TooBig as e:
        return _fail(err, 'more than %s files under %s, nothing checked. Point --root at the notes folder'
                     % (format(MAX_FILES, ','), _show(str(e), base)))
    record = _record_path(dirs, cfg)
    old = _load_record(record)
    if changed:
        want = {_real_file(f) for f in changed}
        todo = sorted(p for p in notes if p in want)
    elif old is None:
        todo = []   # first run: record only
    else:
        todo = sorted(p for p, (_, _, s) in notes.items() if old.get(p) != s)
    new = set() if old is None else {p for p in todo if p not in old}
    gone = [] if old is None else [p for p in old if p not in notes]

    found = []
    texts = {}
    names = None
    for p in todo:
        root, rel, _ = notes[p]
        text = _read(p)
        if text is None:
            continue
        texts[p] = text
        if _empty(text):
            found.append((p, 0, 'empty note, write it or delete it'))
            new.discard(p)
            continue
        rel_dir = posixpath.dirname(_norm(rel))
        seen = set()
        for pos, t, wiki in _links(text):
            keys = _keys(t, rel_dir)
            if keys is None or t in seen:
                continue
            seen.add(t)
            if names is None:
                names = _Names(files)
            if not _resolves(keys, names, os.path.dirname(p), root, t):
                found.append((p, _line(text, pos), _dead(t, wiki, 'nothing by that name')))
        if em_dash:
            n = text.count(EM_DASH)
            if n:
                many = ' (%d in this note)' % n if n > 1 else ''
                found.append((p, _line(text, text.find(EM_DASH)), 'em dash%s, use a comma, colon or brackets' % many))

    # new notes need a link from another note, and links elsewhere to a removed note are dead now
    cands = {p: _tails(notes[p][1]) for p in new
             if posixpath.splitext(_norm(os.path.basename(p)))[0] not in ENTRY_STEMS}
    gone_tails = set()
    for g in gone:
        gone_tails |= _tails(_rel_under(g, dirs))
    if cands or gone_tails:
        linked, todo_set, complete = set(), set(todo), True
        end = time.monotonic() + SCAN_SECS
        for p in sorted(notes):
            if time.monotonic() > end:
                complete = False
                break
            text = texts.get(p)
            if text is None:
                text = _read(p)
            if text is None:
                continue
            root, rel, _ = notes[p]
            rel_dir = posixpath.dirname(_norm(rel))
            seen = set()
            for pos, t, wiki in _links(text):
                keys = _keys(t, rel_dir)
                if keys is None:
                    continue
                for c, tails in cands.items():
                    if c != p and keys & tails:
                        linked.add(c)
                if p in todo_set or t in seen or not keys & gone_tails:
                    continue
                seen.add(t)
                if names is None:
                    names = _Names(files)
                if not _resolves(keys, names, os.path.dirname(p), root, t):
                    found.append((p, _line(text, pos), _dead(t, wiki, 'its note was removed or renamed')))
        # an unfinished scan cannot tell an orphan, so it names none
        if complete:
            for c in sorted(cands):
                if c not in linked:
                    found.append((c, 0, ORPHAN))

    try:
        # nothing to write when no stamp moved
        if old != {p: s for p, (_, _, s) in notes.items()}:
            _save_record(record, notes)
    except OSError as e:
        if not found:
            return _fail(err, 'cannot keep its record in %s (%s)'
                         % (_show(os.path.dirname(record), base), e.strerror or type(e).__name__))
    if not found:
        return 0
    rows = []
    for p, ln, msg in sorted(found, key=lambda f: (_show(f[0], base), f[1])):
        rows.append('%s:%d: %s' % (_show(p, base), ln, msg) if ln else '%s: %s' % (_show(p, base), msg))
    if len(rows) > MAX_LINES - 1:
        rows = rows[:MAX_LINES - 2] + ['... and %d more' % (len(rows) - (MAX_LINES - 2))]
    one = len(found) == 1
    head = 'leks check: %d problem%s in the notes, fix %s before you stop' % (
        len(found), '' if one else 's', 'it' if one else 'them')
    _say(err, [head] + rows)
    return 2
