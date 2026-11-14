"""PEP sources for the public benchmark: fetch them at one pinned commit, read the header, write notes.

The Python Enhancement Proposals live in github.com/python/peps. Their texts are fetched at run time
into a cache folder and never stored in this repository. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
import tempfile
import urllib.request

PEPS_REPO = 'https://github.com/python/peps'
# the commit every number in docs/benchmark.md comes from (committed 2026-11-13)
PINNED_SHA = '2a71e92663616ff5b2c3085ac8ed4f29d38e83d4'
PINNED_DATE = '2026-11-13'
ARCHIVE_URL = 'https://github.com/python/peps/archive/{sha}.tar.gz'
# sha256 over "name<TAB>sha256 of the bytes<LF>" of every pep-NNNN.rst or .md at the pinned commit,
# sorted by name. a tarball can be packed differently on another day, the files stay the same
SOURCE_DIGEST = '4de0c812013bced12b0b5c758d88ce5f0276d7759c294d917f72517353ab9fff'
MAX_SOURCE_BYTES = 4_000_000

SOURCE_NAME = re.compile(r'^pep-(\d{4})\.(rst|md)$')
_MEMBER = re.compile(r'^[^/]+/peps/(pep-\d{4}\.(?:rst|md))$')

# PEP status (PEP 1) -> the word written into the note's front matter. LEKS reads that word through
# STATUS_WORDS in leks/chunking.py. docs/benchmark.md gives the reason for every row
STATUS_WORD = {
    'active': 'current',
    'accepted': 'current',
    'provisional': 'current',
    'final': 'record',
    'superseded': 'superseded',
    # never took effect and nothing replaced them: kept for the record, like a file in an archive folder
    'rejected': 'archived',
    'withdrawn': 'archived',
    # open proposals, nothing decided yet: no claim either way
    'deferred': 'unknown',
    'draft': 'unknown',
}
DEFAULT_WORD = 'unknown'

MONTHS = {m: i + 1 for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split())}


# ---------------------------------------------------------------- fetch

def source_digest(folder):
    """The digest of the PEP sources in a folder, see SOURCE_DIGEST."""
    rows = []
    for name in sorted(os.listdir(folder)):
        if SOURCE_NAME.match(name):
            with open(os.path.join(folder, name), 'rb') as f:
                rows.append('%s\t%s\n' % (name, hashlib.sha256(f.read()).hexdigest()))
    return hashlib.sha256(''.join(rows).encode('ascii')).hexdigest()


def _extract(tar_path, dest):
    """Copy only peps/pep-NNNN.rst|md out of the archive, by name, as plain files. Returns the count."""
    n = 0
    with tarfile.open(tar_path, 'r:gz') as tf:
        for m in tf:
            hit = _MEMBER.match(m.name)
            if not hit or not m.isfile() or m.size > MAX_SOURCE_BYTES:
                continue
            f = tf.extractfile(m)
            if f is None:
                continue
            # the name comes from the pattern, never from the archive path as such
            with open(os.path.join(dest, hit.group(1)), 'wb') as out:
                out.write(f.read())
            n += 1
    return n


def fetch(cache_dir, sha=PINNED_SHA, offline=False, timeout=120, log=print):
    """A folder with the PEP sources at sha, downloaded once into cache_dir. Raises RuntimeError."""
    dest = os.path.join(cache_dir, 'peps-src-' + sha[:12])
    want = SOURCE_DIGEST if sha == PINNED_SHA else None
    if os.path.isdir(dest) and any(SOURCE_NAME.match(x) for x in os.listdir(dest)):
        got = source_digest(dest)
        if want is None or got == want:
            return dest
        raise RuntimeError('cached PEP sources in %s do not match the pinned digest, remove that folder' % dest)
    if offline:
        raise RuntimeError('no cached PEP sources and --offline is set')
    os.makedirs(cache_dir, exist_ok=True)
    url = ARCHIVE_URL.format(sha=sha)
    log('fetching %s' % url)
    fd, tar_path = tempfile.mkstemp(prefix='peps-', suffix='.tar.gz', dir=cache_dir)
    os.close(fd)
    tmp = tempfile.mkdtemp(prefix='peps-src-', dir=cache_dir)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'leks-public-bench'})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tar_path, 'wb') as out:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                out.write(b)
        log('fetched %d bytes' % os.path.getsize(tar_path))
        n = _extract(tar_path, tmp)
        if not n:
            raise RuntimeError('no PEP sources in the archive from %s' % url)
        got = source_digest(tmp)
        if want is not None and got != want:
            raise RuntimeError('PEP sources from %s do not match the pinned digest (%s)' % (url, got))
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tar_path):
            os.remove(tar_path)
        if os.path.isdir(tmp):
            for x in os.listdir(tmp):
                os.remove(os.path.join(tmp, x))
            os.rmdir(tmp)
    return dest


# ---------------------------------------------------------------- header

def split_header(text):
    """(header fields, body lines). The header is the RFC 822 style block at the top, up to the first blank line.
    Continuation lines start with white space. Keys keep their case, values are joined with one space."""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    fields, key, i = {}, None, 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            break
        m = re.match(r'^([A-Za-z][A-Za-z0-9-]*):\s*(.*)$', line)
        if m and not line[0].isspace():
            key = m.group(1)
            fields[key] = m.group(2).strip()
        elif key is not None and line[0].isspace():
            fields[key] = (fields[key] + ' ' + line.strip()).strip()
        else:
            break
        i += 1
    return fields, lines[i:]


def pep_numbers(value):
    """'649, 749' -> [649, 749]. Anything that is no number is skipped."""
    return [int(x) for x in re.findall(r'\b(\d{1,4})\b', value or '')]


def status_word(pep_status):
    """The front matter status word for a PEP status, see STATUS_WORD."""
    return STATUS_WORD.get((pep_status or '').strip().lower(), DEFAULT_WORD)


def iso_date(created):
    """'05-Jul-2001' -> '2001-07-05', None when the value has another form."""
    m = re.match(r'^\s*(\d{1,2})[- ]([A-Za-z]{3})[A-Za-z]*[- ](\d{4})\b', created or '')
    if not m or m.group(2).lower() not in MONTHS:
        m2 = re.match(r'^\s*(\d{4})-(\d{2})-(\d{2})\b', created or '')
        return '-'.join(m2.groups()) if m2 else None
    return '%s-%02d-%02d' % (m.group(3), MONTHS[m.group(2).lower()], int(m.group(1)))


def note_name(n):
    return 'pep-%04d' % int(n)


def license_of(body):
    """What the PEP's own copyright section says: public-domain, cc0, public-domain-or-cc0, opl or none."""
    text = '\n'.join(body) if isinstance(body, list) else body
    # the section under the last heading that starts with Copyright (or License)
    head = None
    lines = text.split('\n')
    for i in range(len(lines) - 1):
        if re.match(r'^\s*(Copyright|License)\b', lines[i], re.I) and re.match(r'^([=\-~^*#+])\1{3,}\s*$', lines[i + 1]):
            head = i
    part = '\n'.join(lines[head:]) if head is not None else text[-3000:]
    part = re.sub(r'\s+', ' ', part).lower()
    if 'open publication license' in part:
        return 'opl'
    pd = 'public domain' in part
    cc0 = 'cc0' in part
    if pd and cc0:
        return 'public-domain-or-cc0'
    if pd:
        return 'public-domain'
    if cc0:
        return 'cc0'
    return 'none'


FREE_LICENSES = ('public-domain', 'cc0', 'public-domain-or-cc0')


# ---------------------------------------------------------------- body

_ADORN = re.compile(r'^([=\-`:\'"~^_*+#<>])\1{2,}\s*$')
_DIRECTIVE = re.compile(r'^(\s*)\.\.\s+([A-Za-z][\w-]*(?::[\w-]+)?)::(?:\s+(.*))?$')
_TARGET = re.compile(r'^\.\.\s+_(`[^`]+`|[^:]+):\s*(.*)$')
_ANON_TARGET = re.compile(r'^\.\.\s+__:\s*(.*)$')
_FOOTNOTE = re.compile(r'^\.\.\s+\[([^\]]+)\]\s*(.*)$')
_SUBST = re.compile(r'^\.\.\s+\|[^|]+\|')
_OPTION = re.compile(r'^\s+:[\w][\w -]*:(\s.*)?$')
_ROLE = re.compile(r':([A-Za-z][\w.+-]*(?::[\w.+-]+)*):`([^`]+)`')
_LINK = re.compile(r'`([^`<]*?)\s*<([^>`]+)>`__?')
_REF = re.compile(r'`([^`<]+)`__?(?![\w])')
_PEP_URL = re.compile(r'https?://(?:[\w-]+\.)?python\.org/(?:dev/peps/|peps/)?pep-(\d{1,4})'
                      r'(?:\.html|\.txt|/)?(?:#[\w.:-]*)?', re.I)
_WIKI = re.compile(r'\[\[pep-(\d{4})(?:[|#][^\]]*)?\]\]')

# the banner a directive stands for on the PEP site (pep_sphinx_extensions, pep_banner_directive.py)
BANNERS = {
    'superseded': ('This PEP has been superseded by :pep:`{arg}`.', 'This PEP has been superseded.'),
    'rejected': ('{arg}', 'This PEP has been rejected.'),
    'withdrawn': ('{arg}', 'This PEP has been withdrawn.'),
    'canonical-doc': ('This PEP is a historical document. The up-to-date, canonical documentation can now be '
                      'found at {arg}.', 'This PEP is a historical document. The up-to-date, canonical '
                      'documentation can now be found elsewhere.'),
    'canonical-pypa-spec': ('This PEP is a historical document. The up-to-date, canonical spec, {arg}, is '
                            'maintained on the PyPA specs page.', 'This PEP is a historical document. The '
                            'up-to-date, canonical specifications are maintained on the PyPA specs page.'),
    'canonical-typing-spec': ('This PEP is a historical document: see {arg} for up-to-date specs and '
                              'documentation.', 'This PEP is a historical document.'),
}


def _wiki(n, text=None):
    text = re.sub(r'\s+', ' ', (text or '').replace('|', ' ')).strip()
    return '[[%s|%s]]' % (note_name(n), text) if text else '[[%s]]' % note_name(n)


def _pep_role(body):
    m = re.match(r'(?s)^(.*?)\s*<\s*(\d{1,4})(?:#[^>]*)?\s*>\s*$', body)
    if m:
        return _wiki(m.group(2), m.group(1))
    m = re.match(r'^\s*(\d{1,4})(?:#\S*)?\s*$', body)
    return _wiki(m.group(1)) if m else body


def _role(m):
    role, body = m.group(1).lower(), m.group(2)
    if role == 'pep':
        return _pep_role(body)
    if role == 'rfc':
        r = re.match(r'(?s)^(.*?)\s*<\s*(\d+)(?:#[^>]*)?\s*>\s*$', body)
        if r:
            return '%s (RFC %s)' % (re.sub(r'\s+', ' ', r.group(1)).strip(), r.group(2))
        return 'RFC ' + body.split('#')[0].strip()
    r = re.match(r'(?s)^(.*?)\s*<[^>]*>\s*$', body)
    text = r.group(1) if r and r.group(1).strip() else body
    return text.lstrip('~!').strip()


def _link(m):
    text, url = m.group(1).strip(), m.group(2).strip()
    p = _PEP_URL.fullmatch(url)
    if p:
        return _wiki(p.group(1), text)
    if url.endswith('_'):
        return text or url[:-1]
    return '[%s](%s)' % (text, url) if text else url


def inline(text):
    """rst inline markup to Markdown: PEP roles and PEP links become wikilinks, other roles their text."""
    text = _ROLE.sub(_role, text)
    text = _LINK.sub(_link, text)
    text = _REF.sub(lambda m: m.group(1), text)
    text = _PEP_URL.sub(lambda m: _wiki(m.group(1)), text)
    return text.replace('``', '`')


def _heading(level, text):
    return '#' * min(4, level + 1) + ' ' + re.sub(r'\s+', ' ', text).strip()


def rst_body(lines):
    """Body lines of a PEP as Markdown text: headings, banners, link targets and footnotes, inline markup.
    Code and indented blocks keep their indent, so no comment line in them reads as a heading."""
    out, styles = [], []
    i, n = 0, len(lines)

    def level(style):
        if style not in styles:
            styles.append(style)
        return styles.index(style) + 1

    def skip_indented(j, base):
        # lines that belong to a block opened at indent base: blank or indented deeper
        while j < n and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > base):
            j += 1
        return j

    while i < n:
        line = lines[i]
        s = line.rstrip()
        # overline, title, underline
        if (_ADORN.match(s) and i + 2 < n and lines[i + 1].strip() and not _ADORN.match(lines[i + 1].rstrip())
                and _ADORN.match(lines[i + 2].rstrip()) and lines[i + 2].strip()[0] == s[0]):
            out.append(_heading(level((s[0], True)), lines[i + 1]))
            i += 3
            continue
        # title, underline
        if (s and not s[0].isspace() and not _ADORN.match(s) and i + 1 < n and _ADORN.match(lines[i + 1].rstrip())
                and len(lines[i + 1].rstrip()) >= len(s) and not s.startswith('..')):
            out.append(_heading(level((lines[i + 1].strip()[0], False)), s))
            i += 2
            continue
        d = _DIRECTIVE.match(line)
        if d:
            name, arg = d.group(2).lower(), (d.group(3) or '').strip()
            base = len(d.group(1))
            j = i + 1
            while j < n and _OPTION.match(lines[j]):
                j += 1
            if name in BANNERS:
                with_arg, plain = BANNERS[name]
                out.append(' ' * base + (with_arg.format(arg=arg) if arg else plain))
            i = j
            continue
        if _SUBST.match(line):
            i = skip_indented(i + 1, 0)
            continue
        t = _TARGET.match(line)
        if t:
            out.append('%s: %s' % (t.group(1).strip('`'), t.group(2)))
            i += 1
            continue
        t = _ANON_TARGET.match(line)
        if t:
            out.append(t.group(1))
            i += 1
            continue
        f = _FOOTNOTE.match(line)
        if f:
            out.append('[%s] %s' % (f.group(1), f.group(2)))
            i += 1
            continue
        if re.match(r'^\s*\.\.(\s|$)', line):
            # a comment: drop it and the block under it
            base = len(line) - len(line.lstrip())
            i = skip_indented(i + 1, base)
            continue
        out.append(s)
        i += 1
    text = inline('\n'.join(out))
    return re.sub(r'\n{3,}', '\n\n', text).strip('\n') + '\n'


def links_in(md_text, own=None):
    """PEP numbers a note links to by wikilink, its own number left out."""
    found = {int(x) for x in _WIKI.findall(md_text)}
    found.discard(own)
    return sorted(found)


# ---------------------------------------------------------------- notes

def _q(s):
    return '"%s"' % s.replace('\\', '\\\\').replace('"', '\\"')


def _links_value(nums):
    vals = [_q('[[%s]]' % note_name(x)) for x in nums]
    return vals[0] if len(vals) == 1 else '[' + ', '.join(vals) + ']'


def front_matter(meta):
    """The front matter block of a note, from the dict convert() builds."""
    rows = [('title', _q(meta['title'])), ('pep', str(meta['pep'])), ('status', meta['status']),
            ('pep_status', meta['pep_status'] or 'unknown'), ('pep_type', meta['type'] or 'unknown')]
    if meta.get('date'):
        rows.append(('date', meta['date']))
    if meta.get('superseded_by'):
        rows.append(('superseded_by', _links_value(meta['superseded_by'])))
    if meta.get('replaces'):
        rows.append(('supersedes', _links_value(meta['replaces'])))
    rows.append(('license', meta['license']))
    return '---\n' + ''.join('%s: %s\n' % kv for kv in rows) + '---\n'


def convert(text, name=None):
    """(meta, note text) for one PEP source. name is the file name, used when the header lacks PEP:."""
    fields, body = split_header(text)
    num = pep_numbers(fields.get('PEP'))
    if not num and name:
        m = SOURCE_NAME.match(name)
        num = [int(m.group(1))] if m else []
    if not num:
        raise ValueError('no PEP number in %r' % (name or text[:40]))
    n = num[0]
    md = rst_body(body)
    meta = {
        'pep': n,
        'name': note_name(n) + '.md',
        'title': re.sub(r'\s+', ' ', fields.get('Title', '')).strip(),
        'pep_status': fields.get('Status', '').strip(),
        'status': status_word(fields.get('Status')),
        'type': fields.get('Type', '').strip(),
        'date': iso_date(fields.get('Created')),
        'superseded_by': [x for x in pep_numbers(fields.get('Superseded-By')) if x != n],
        'replaces': [x for x in pep_numbers(fields.get('Replaces')) if x != n],
        'license': license_of(body),
    }
    meta['links'] = links_in(md, n)
    note = front_matter(meta) + '# PEP %d: %s\n\n' % (n, meta['title']) + md
    return meta, note


def convert_tree(src, dest, keep=FREE_LICENSES):
    """Convert every PEP source in src into dest/peps/pep-NNNN.md. Returns the manifest dict.

    Only PEPs whose own text puts them in the public domain or under CC0 become notes, the rest are
    listed as left out."""
    notes = os.path.join(dest, 'peps')
    os.makedirs(notes, exist_ok=True)
    for x in os.listdir(notes):
        if x.endswith('.md'):
            os.remove(os.path.join(notes, x))
    peps, left_out = [], []
    for name in sorted(os.listdir(src)):
        if not SOURCE_NAME.match(name):
            continue
        with open(os.path.join(src, name), encoding='utf-8') as f:
            meta, note = convert(f.read(), name)
        if meta['license'] not in keep:
            left_out.append({'pep': meta['pep'], 'license': meta['license']})
            continue
        # newline='\n': the corpus is the same bytes on every system, windows would write crlf
        with open(os.path.join(notes, meta['name']), 'w', encoding='utf-8', newline='\n') as f:
            f.write(note)
        peps.append(meta)
    manifest = {'repo': PEPS_REPO, 'commit': PINNED_SHA if os.path.basename(src).endswith(PINNED_SHA[:12]) else None,
                'source_digest': source_digest(src), 'peps': peps, 'left_out': left_out}
    with open(os.path.join(dest, 'manifest.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    return manifest
