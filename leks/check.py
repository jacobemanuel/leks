"""Notes check for the end of an agent's turn: python3 -m leks check.

It walks the notes (.md, .markdown) under the roots, the current folder by
default, and looks only at notes that changed since its last run, so the first
run records and reports nothing. A changed note is checked for being empty, for
dead links ([[wikilinks]] and relative markdown links) and, unless switched off,
for em dashes. A new note that no other note links to is reported, and so is a
link in another note to a note that was removed or renamed.

The config may give notes an edit contract by glob (check.contracts), the
first glob that fits wins. Once the check has seen a write-once note, a change
to its body is reported with its first changed line; its front matter, its
status and superseded lines and its link targets may change. A frozen note may
not change at all. A living note may not hold a line that starts with a date
outside a log, changelog, history or journal section. A contract may also name
fields a note keeps (keep: status, date, revisit) and ask a note marked
superseded to name its successor (successor: true).
`leks check --accept PATH` keeps the current text of a write-once or frozen note.

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
import shlex
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

# edit contracts
KEEP = 5                    # accepted texts kept per write-once or frozen note, newest first
KEPT_LINES = 5000           # longer kept texts get no line hashes, a change is then named without a line
HELD = ('write-once', 'frozen')
_HEX = re.compile(r'[0-9a-f]{32}')
_HEX8 = re.compile(r'[0-9a-f]{8}')
# a line that marks a record as replaced, also inside a callout: status, superseded, replaced by, deprecated
_MARK = re.compile(r'(?i)^[\s>*_+-]*(?:\[![\w-]+\][+-]?\s*)?[*_]*(?:status[*_]*\s*:|replaced\s+by\b'
                   r'|(?:superseded|deprecated)[*_]*(?:\s+by\b|\s*[:.(\[]|\s*$))')
_HEADING = re.compile(r' {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t#]*$')
_LOG_WORD = re.compile(r'(?i)\b(?:log|logs|changelog|history|journal)\b')
_DATED = re.compile(r'\s*(?:(?:[-*+]|\d{1,9}[.)])\s+)?[*_]{0,2}\d{4}-\d{2}-\d{2}(?!\d)')
# `Key: value` outside the front matter, maybe a list item, bold or quoted; a url is no field
_FIELD = re.compile(r'[\s>]*(?:[-*+]\s+)?[*_]{0,2}([A-Za-z][A-Za-z0-9 _-]{0,39}?)[*_]{0,2}\s*:(?!//)(.*)$')
_FM_KEY = re.compile(r'([A-Za-z][A-Za-z0-9 _-]{0,39}?)\s*:(?:\s|$)(.*)$')
_GONE_STATUS = re.compile(r'(?i)[\s*_]*(?:superseded|replaced)\b')
_BY = re.compile(r'(?i)\b(?:superseded|replaced)[\s*_]+by\b[\s:*_]*[^\s:*_]')
WRITE_ONCE = ('write-once note changed, it is a record: restore its text and put the change in a new note, '
              'or keep it with %s')
FROZEN = 'frozen note changed: restore its text, or keep the change with %s'
LIVING = 'dated line outside a log section%s, rewrite it as what holds now or move it under a Log heading'
MISSING = 'missing field%s %s, the contract for %s keeps %s'
SUCCESSOR = ('marked superseded but names no successor, add a superseded by line with a link to the note '
             'that replaces it')


class _TooBig(Exception):
    pass


class _Cannot(Exception):
    """The check cannot run, the message says why."""


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


def copilot_stop(d):
    """True for the stop payload of the GitHub Copilot hooks (agentStop or Stop): they carry a stop reason.

    Claude Code's Stop payload has no stop reason. Copilot treats exit 2 from a stop hook as a
    warning only; it holds the turn on {"decision": "block", "reason": ...} on stdout with exit 0.
    """
    return isinstance(d, dict) and ('stopReason' in d or 'stop_reason' in d)


def host_main(roots=None, changed=None, em_dash=True, stdin=None, out=None, err=None):
    """run() as a host hook. Problems go to stderr with exit 2, or as a block decision for Copilot."""
    import io
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    d = hook_input(stdin)
    said = io.StringIO()
    code = run(roots, changed, em_dash, stdin=io.StringIO(json.dumps(d) if d else ''), err=said)
    text = said.getvalue()
    if code == 2 and copilot_stop(d):
        try:
            out.write(json.dumps({'decision': 'block', 'reason': text.strip()}) + '\n')
            out.flush()
        except (OSError, ValueError):
            pass
        return 0
    if text:
        _say(err, [text.rstrip('\n')])
    return code


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


def _dirs(roots, base):
    """The folders to walk as real paths, no repeats. _Cannot when one is missing."""
    dirs = []
    for r in roots or ([base] if base else []):
        r = os.path.expanduser(r)
        p = os.path.realpath(os.path.join(base, r) if base else r)
        if not os.path.isdir(p):
            raise _Cannot('no such folder: %s' % _show(p, base))
        if p not in dirs:
            dirs.append(p)
    if not dirs:
        raise _Cannot('the current folder is gone, pass --root')
    return dirs


def _load_record(path):
    """(stamps, contracts) from the last run, None when there is none (or it is broken).

    stamps: {note path: [mtime_ns, size]}. contracts: {note path: {"type": ..., "hashes": [...],
    "lines": [...]}} for the write-once and frozen notes, newest accepted text first, with short hashes
    of the lines of the newest one. A version 1 record has no contracts.
    """
    try:
        if os.path.getsize(path) > MAX_STATE:
            return None
        with open(path, 'rb') as f:
            d = json.loads(f.read().decode('utf-8'))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(d, dict) or d.get('v') not in (1, 2) or not isinstance(d.get('notes'), dict):
        return None
    stamps = {k: v for k, v in d['notes'].items()
              if isinstance(v, list) and len(v) == 2 and all(type(x) is int for x in v)}
    got = d.get('contracts') if d.get('v') == 2 else None
    contracts = {}
    for k, c in (got.items() if isinstance(got, dict) else ()):
        hashes = c.get('hashes') if isinstance(c, dict) else None
        if (c.get('type') in HELD and isinstance(hashes, list) and hashes
                and all(isinstance(h, str) and _HEX.fullmatch(h) for h in hashes)):
            lines = c.get('lines')
            if not (isinstance(lines, list) and len(lines) <= KEPT_LINES
                    and all(isinstance(h, str) and _HEX8.fullmatch(h) for h in lines)):
                lines = []
            contracts[k] = {'type': c['type'], 'hashes': hashes[:KEEP], 'lines': lines}
    return stamps, contracts


def _save_record(path, stamps, contracts):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    data = json.dumps({'v': 2, 'notes': stamps, 'contracts': contracts}, ensure_ascii=True)
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
    # windows line ends read as plain ones, or a `Date:` with nothing after it counts as filled
    # and a heading stops matching
    return raw.decode('utf-8', errors='replace').replace('\r\n', '\n')


def _real_file(f):
    p = os.path.abspath(os.path.expanduser(f))
    return os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))


def _rel_under(p, dirs):
    for d in dirs:
        if p.startswith(d.rstrip(os.sep) + os.sep):
            return os.path.relpath(p, d)
    return os.path.basename(p)


# ---------- reading a note ----------

def _front_end(lines):
    """Index of the first line after the yaml front matter, 0 when there is none."""
    if lines and lines[0].lstrip('\ufeff').rstrip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() in ('---', '...'):
                return i + 1
    return 0


def _empty(text):
    """Nothing but blanks once a byte order mark and the yaml front matter are gone."""
    lines = text.lstrip('\ufeff').split('\n')
    return not '\n'.join(lines[_front_end(lines):]).strip()


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


# ---------- edit contracts ----------

def _rules(cfg):
    """check.contracts of the config, [] without a config."""
    try:
        return list(cfg.check.get('contracts') or []) if cfg is not None else []
    except AttributeError:
        return []


def _contract(rel, rules):
    """The contract entry of a note by its path under the walked folder: the first glob that fits wins."""
    r = rel.replace(os.sep, '/')
    for c in rules:
        if config.glob_match(r, c['glob']):
            return c
    return None


def _type(rel, rules):
    """write-once, living or frozen for a note, None when its contract has no type or there is none."""
    c = _contract(rel, rules)
    return c.get('type') if c else None


def _target_spans(masked):
    """Where link targets sit: a wikilink up to its |, the address of a markdown link."""
    out = []
    for m in _WIKI.finditer(masked):
        a = m.start(1)
        out.append((a, a + len(m.group(1).split('|', 1)[0])))
    for m in _MD.finditer(masked):
        # <a b.md> loses its brackets too, so the plain and the bracket form read the same
        out.append((m.start(1) - 1, m.end(1) + 1) if m.group(1) is not None else m.span(2))
    return sorted(out)


def _record_lines(text):
    """A write-once note as its contract reads it, as (line number, text): the body without front matter,
    status and superseded lines or blank lines, every link target cut out, so a moved target or a new
    status is no change. A link target never spans a line break, so the line numbers stay right."""
    parts, at = [], 0
    for a, b in _target_spans(_mask(text)):
        if a >= at:
            parts.append(text[at:a])
            at = b
    parts.append(text[at:])
    lines = ''.join(parts).split('\n')
    lines[0] = lines[0].lstrip('\ufeff')
    return [(i + 1, lines[i].rstrip()) for i in range(_front_end(lines), len(lines))
            if lines[i].strip() and not _MARK.match(lines[i])]


def _held_lines(kind, text):
    """(line number, text) of what a write-once or frozen contract holds a note to."""
    if kind == 'write-once':
        return _record_lines(text)
    return list(enumerate(text.split('\n'), 1))


def _digest(kind, text):
    """What a write-once or frozen contract holds a note to: a hash of its record text or of all of it."""
    s = '\n'.join(t for _, t in _record_lines(text)) if kind == 'write-once' else text
    return hashlib.sha256(s.encode('utf-8', 'surrogatepass')).hexdigest()[:32]


def _short(t):
    return hashlib.sha256(t.encode('utf-8', 'surrogatepass')).hexdigest()[:8]


def _kept_entry(kind, text, hashes=None):
    """A record entry: the accepted hashes, newest first, and line hashes of the newest text, so a later
    change can be named by its first line. A text longer than KEPT_LINES keeps no line hashes."""
    pairs = _held_lines(kind, text)
    lines = [_short(t) for _, t in pairs] if len(pairs) <= KEPT_LINES else []
    return {'type': kind, 'hashes': hashes or [_digest(kind, text)], 'lines': lines}


def _first_change(kind, text, kept):
    """The number of the first line that differs from the newest kept text, 0 when that cannot be told."""
    if not kept:
        return 0
    pairs = _held_lines(kind, text)
    for i, (n, t) in enumerate(pairs):
        if i >= len(kept) or _short(t) != kept[i]:
            return n
    # only lines at the end were taken out: the last line that is left
    if len(pairs) < len(kept):
        return pairs[-1][0] if pairs else 1
    return 0


def _fields(text):
    """{field: (line, value)} of a note, the first one of a name wins: front matter keys, `Key: value`
    lines outside code (also as a list item, in bold or in a quote) and headings, which have no value.
    Names are lowercased with runs of spaces, - and _ made one space."""
    lines = text.split('\n')
    lines[0] = lines[0].lstrip('\ufeff')
    out = {}
    fe = _front_end(lines)
    for i in range(1, max(fe - 1, 1)):
        m = _FM_KEY.match(lines[i])
        if not m:
            continue
        v = m.group(2).strip()
        # a key with its value on the next lines, as a yaml list or block
        if not v and i + 1 < fe - 1 and lines[i + 1][:1] in (' ', '\t', '-'):
            v = lines[i + 1].strip()
        if v.strip('\'"[] '):
            out.setdefault(_field_name(m.group(1)), (i + 1, v))
    masked = _mask(text).split('\n')
    for i in range(fe, len(lines)):
        if not masked[i].strip():
            continue
        h = _HEADING.match(masked[i])
        if h:
            if h.group(2):
                out.setdefault(_field_name(h.group(2)), (i + 1, ''))
            continue
        m = _FIELD.match(masked[i])
        if m:
            v = m.group(2).strip(' \t*_')
            if v:
                out.setdefault(_field_name(m.group(1)), (i + 1, v))
    return out


def _field_name(s):
    return re.sub(r'[\s_-]+', ' ', s.strip(' *_')).lower()


def _no_successor(text, fields):
    """The line of the status when a note is marked superseded or replaced and names no note after it,
    else None. A superseded by key or line with something after the by names one."""
    st = fields.get('status')
    if not st or not _GONE_STATUS.match(st[1]):
        return None
    if _BY.search(st[1]) or any(k in fields for k in ('superseded by', 'replaced by')):
        return None
    masked = _mask(text).split('\n')
    for i, ln in enumerate(text.split('\n')):
        if masked[i].strip() and _BY.search(ln):
            return None
    return st[0]


def _and(words):
    words = list(words)
    return words[0] if len(words) == 1 else ', '.join(words[:-1]) + ' and ' + words[-1]


def _undated(text):
    """(first line, count) of the lines that start with a date outside a log, changelog, history or
    journal section, None when there is none. A subsection of such a section is inside it."""
    lines = _mask(text).split('\n')
    stack, first, n = [], 0, 0
    for i in range(_front_end(lines), len(lines)):
        m = _HEADING.match(lines[i])
        if m:
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, bool(_LOG_WORD.search(m.group(2) or ''))))
        elif _DATED.match(lines[i]) and not any(log for _, log in stack):
            n += 1
            first = first or i + 1
    return (first, n) if n else None


def _accept_cmd(p, base, roots, moved=None):
    """The command that keeps a changed note's text, with the roots this check ran on. A hook run that
    reads the project folder while the shell sits in another one passes the folders it walked as moved:
    the command then names them and the note in full, so it runs as printed from any folder."""
    if moved:
        return ' '.join(['leks check --accept', shlex.quote(_clean(p))]
                        + ['--root ' + shlex.quote(_clean(d)) for d in moved])
    cmd = 'leks check --accept ' + shlex.quote(_show(p, base))
    for r in roots or ():
        cmd += ' --root ' + shlex.quote(r)
    return cmd


def _broken(p, entry, text, held_to, cmd):
    """(line, message) for each rule of its contract a changed note breaks. A write-once or frozen note
    the record does not know yet gets its text now as the baseline, with no finding."""
    out = []
    kind = entry.get('type')
    if kind == 'living':
        hit = _undated(text)
        if hit:
            out.append((hit[0], LIVING % (' (%d in this note)' % hit[1] if hit[1] > 1 else '')))
    elif kind in HELD:
        h = _digest(kind, text)
        c = held_to.get(p)
        if c is None:
            held_to[p] = _kept_entry(kind, text)
        elif h not in c['hashes']:
            out.append((_first_change(kind, text, c.get('lines')),
                        (WRITE_ONCE if kind == 'write-once' else FROZEN) % cmd))
    keep, successor = entry.get('keep') or (), entry.get('successor')
    if keep or successor:
        fields = _fields(text)
        miss = [k for k in keep if k not in fields]
        if miss:
            out.append((1, MISSING % ('s' if len(miss) > 1 else '', _and(miss), _clean(entry['glob'], 80),
                                      _and(keep))))
        line = _no_successor(text, fields) if successor else None
        if line:
            out.append((line, SUCCESSOR))
    return out


def _kept(kept, notes, rules, cfg):
    """The kept texts that still count: the note is there and its contract did not change.
    A missing or broken config drops none of them, so a typo in the config loses no baseline."""
    if cfg is None:
        return {p: c for p, c in kept.items() if p in notes}
    return {p: c for p, c in kept.items() if p in notes and _type(notes[p][1], rules) == c['type']}


def _baseline(notes, rules, held_to, texts, end):
    """Keep the text of each write-once and frozen note the record does not hold yet, until `end`.
    The first run does this for all of them, later runs for notes a new glob brought in."""
    if not any(c.get('type') in HELD for c in rules):
        return
    for p in sorted(notes):
        if time.monotonic() > end:
            return
        if p in held_to:
            continue
        kind = _type(notes[p][1], rules)
        if kind not in HELD:
            continue
        text = texts.get(p)
        if text is None:
            text = _read(p)
        # an empty note gets its baseline once it has a body
        if text is not None and not _empty(text):
            held_to[p] = _kept_entry(kind, text)


# ---------- output ----------

def _clean(s, cap=0):
    s = ''.join(c if c.isprintable() else '?' for c in s)
    if cap and len(s) > cap:
        s = s[:cap - 3] + '...'
    return s


def _show(p, cwd):
    # a path under the folder is shown with / on every system, the way links and contract globs write it
    if cwd and p.startswith(cwd.rstrip(os.sep) + os.sep):
        p = os.path.relpath(p, cwd).replace(os.sep, '/')
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
    try:
        dirs = _dirs(roots, base)
    except _Cannot as e:
        return _fail(err, str(e))
    cfg = _config()
    # a missing or broken config never stops the check, it only loses the switch and the contracts
    if cfg is not None and cfg.check.get('em_dash') is False:
        em_dash = False
    rules = _rules(cfg)
    try:
        files, notes = _walk(dirs)
    except _TooBig as e:
        return _fail(err, 'more than %s files under %s, nothing checked. Point --root at the notes folder'
                     % (format(MAX_FILES, ','), _show(str(e), base)))
    record = _record_path(dirs, cfg)
    old, kept = _load_record(record) or (None, {})
    held_to = _kept(kept, notes, rules, cfg)
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
        entry = _contract(rel, rules) if rules else None
        if entry:
            for ln, msg in _broken(p, entry, text, held_to,
                                     _accept_cmd(p, base, roots, dirs if base != cwd else None)):
                found.append((p, ln, msg))
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
    # the orphan scan and the contract baselines share one time budget
    end = time.monotonic() + SCAN_SECS
    if cands or gone_tails:
        linked, todo_set, complete = set(), set(todo), True
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

    if rules:
        _baseline(notes, rules, held_to, texts, end)
    stamps = {p: s for p, (_, _, s) in notes.items()}
    try:
        # nothing to write when no stamp moved and no kept text changed
        if old != stamps or kept != held_to:
            _save_record(record, stamps, held_to)
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


# ---------- accepting a change ----------

def _place(p, dirs):
    """(root, path under it) for a file under one of the walked folders, None outside them."""
    for d in dirs:
        if p.startswith(d.rstrip(os.sep) + os.sep):
            return d, os.path.relpath(p, d)
    return None


def accept(paths, roots=None, err=None):
    """Keep the current text of write-once and frozen notes, so the check measures their next change
    from it. Nothing is checked. Returns 0 when every note was kept, 1 when none was."""
    err = sys.stderr if err is None else err
    try:
        base = os.path.realpath(os.getcwd())
    except OSError:
        base = None
    try:
        dirs = _dirs(roots, base)
    except _Cannot as e:
        return _fail(err, str(e))
    cfg = _config()
    rules = _rules(cfg)
    if not any(c.get('type') in HELD for c in rules):
        return _fail(err, 'no write-once or frozen contract in the config (check.contracts), nothing to accept')
    record = _record_path(dirs, cfg)
    got = _load_record(record)
    if got is None:
        return _fail(err, 'no earlier check of %s, its first run keeps every note as it is: pass the same '
                     '--root as the check' % ', '.join(_show(d, base) or '.' for d in dirs))
    stamps, contracts = got
    for f in paths:
        p = _real_file(f)
        at = _place(p, dirs)
        if at is None:
            return _fail(err, 'not under the checked folders: %s' % _show(p, base))
        kind = _type(at[1], rules)
        if not p.lower().endswith(NOTE_EXT) or not os.path.isfile(p):
            return _fail(err, 'not a note: %s' % _show(p, base))
        if kind not in HELD:
            return _fail(err, 'no write-once or frozen contract for %s' % _show(p, base))
        text = _read(p)
        if text is None:
            return _fail(err, 'cannot read %s' % _show(p, base))
        h = _digest(kind, text)
        c = contracts.get(p)
        before = c['hashes'] if c and c['type'] == kind else []
        contracts[p] = _kept_entry(kind, text, ([h] + [x for x in before if x != h])[:KEEP])
    try:
        _save_record(record, stamps, contracts)
    except OSError as e:
        return _fail(err, 'cannot keep its record in %s (%s)'
                     % (_show(os.path.dirname(record), base), e.strerror or type(e).__name__))
    n = len({_real_file(f) for f in paths})
    _say(err, ['leks check: kept the current text of %d note%s' % (n, '' if n == 1 else 's')])
    return 0
