"""Documents and images to text units: pdf, docx, and pictures through on-device OCR.

Does the engine read images and photos? Yes, when the macOS helper is built
(`leks ocr build`): every png, jpg, heic, tif, gif, bmp and webp goes through Vision OCR, and so does
a scanned pdf. Without the helper, images stay name-only and the lanes keep working.

Order for a pdf: pypdf text layer first (when pypdf is installed). If no page has usable text, the
file goes to the helper. Without both, the file stays a name only.

Page separator of the helper, checked in the swift source: the original tool joined the pages of a
text-layer pdf with a plain newline (PDFKit whole-document string) and the OCR lines of all pages
with a plain newline too, so a page break was not recoverable. The helper shipped here writes one
form feed (U+000C) between pages, an empty page stays an empty field, and this module splits on it.
If an older helper answers without form feeds, the text becomes one unit and the warning says so.

Everything here is hostile-input careful: size bounds, zip bomb bounds, no dtd in xml, a wall clock
on every external process, and no exception text stored anywhere.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import zipfile
from collections import Counter
from typing import TypedDict

DOC_EXT = ('.pdf', '.docx')
IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.heic', '.tif', '.tiff', '.gif', '.bmp', '.webp')
EXTRACTOR_VERSION = 1

OCR_DEFAULTS = {'enabled': 'auto', 'images': True, 'helper': None, 'max_pages': 40}
LIMIT_DEFAULTS = {'pdf_max_bytes': 209715200, 'office_max_bytes': 419430400}
# zip bounds, can be lowered through the limits dict (tests do)
ZIP_DEFAULTS = {'zip_max_members': 20000, 'zip_max_member': 32 * 2**20, 'zip_max_total': 128 * 2**20}
XML_MAX_TEXT = 8 * 2**20
PDF_MAX_SECS = 120.0
PDF_MAX_PAGES = 5000
TEXT_MAX_CHARS = 48 * 2**20
OCR_MAX_OUT = 128 * 2**20
BUILD_SECS = 600.0
HELPER_KINDS = ('pdf-text', 'pdf-ocr', 'image-ocr')
HELPER_ERRORS = ('unreadable PDF', 'unreadable image')


class Extracted(TypedDict):
    unit: str                            # 'page' | 'paragraph' | 'image' | 'line'
    title: str
    units: list                          # (unit_no from 1, heading, text)
    extractor: str                       # 'pypdf', 'ocr', 'docx', 'text'
    version: int
    warning: str | None


# ---------- config access, works with any object that has home, ocr, limits

def _ocr_cfg(cfg) -> dict:
    d = dict(OCR_DEFAULTS)
    d.update(getattr(cfg, 'ocr', None) or {})
    return d


def _limits(cfg) -> dict:
    d = dict(LIMIT_DEFAULTS)
    d.update(getattr(cfg, 'limits', None) or {})
    return d


def _helper_path(cfg) -> str:
    fn = getattr(cfg, 'helper_path', None)
    if callable(fn):
        return fn()
    h = _ocr_cfg(cfg).get('helper')
    if h:
        return os.path.expanduser(h)
    return os.path.join(os.path.expanduser(cfg.home), 'bin', 'extract_text')


def helper_available(cfg) -> bool:
    """True when OCR is not switched off and the helper binary exists."""
    if os.name != 'posix':
        return False
    if _ocr_cfg(cfg).get('enabled') in ('off', False):
        return False
    p = _helper_path(cfg)
    return os.path.isfile(p) and os.access(p, os.X_OK)


def _pypdf_installed() -> bool:
    try:
        return importlib.util.find_spec('pypdf') is not None
    except (ImportError, ValueError):
        return False


def capabilities(cfg) -> dict:
    ocr = helper_available(cfg)
    return {'pdf_text': _pypdf_installed(), 'ocr': ocr, 'docx': True,
            'images': bool(ocr and _ocr_cfg(cfg).get('images'))}


# ---------- text cleaning, pure functions

LIGS = {'\ufb00': 'ff', '\ufb01': 'fi', '\ufb02': 'fl', '\ufb03': 'ffi', '\ufb04': 'ffl',
        '\ufb05': 'st', '\ufb06': 'st'}
LIG_RX = re.compile('[' + ''.join(LIGS) + ']')
# control chars and direction overrides from a hostile file never reach a passage
CTRL_RX = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\u200b-\u200d\ufeff]')
REF_WORDS = {'references', 'bibliography', 'literature', 'literaturverzeichnis', 'workscited',
             'literatur', 'referencesandnotes'}
AFTER_REFS = re.compile(r'^\s*((appendix|appendices|supplementary|supplemental)\b|[A-Z]\.\s+[A-Z][a-z]+)', re.I)
HEAD_RX = re.compile(r'^\s*((\d{1,2}(\.\d{1,2}){0,3}\.?)|([IVX]{1,5}\.)|([A-H]\.))\s+[A-Z][^\n]{2,70}$')
SOFT_REF = re.compile(r'^\s*(\[\d{1,3}\]|\d{1,3}\.\s+[A-Z])')


def sanitise(s: str) -> str:
    s = s.replace('\r\n', '\n').replace('\r', '\n').replace('\u00a0', ' ')
    return CTRL_RX.sub('', LIG_RX.sub(lambda m: LIGS[m.group(0)], s))


def fix_small_caps(line: str) -> str:
    # small capitals come out as "II. R ELATED S TUDY", only short all-caps lines get touched
    if len(line) < 70 and re.search(r'\b[A-Z] [A-Z]{2,}', line) and line.upper() == line:
        return re.sub(r'\b([A-Z]) (?=[A-Z]{2,}\b)', r'\1', line)
    return line


def drop_furniture(pages: list) -> tuple:
    """Remove running headers, footers and page numbers.

    A header or footer is a line in the top or bottom three of a page that repeats (digits aside)
    on at least 40 percent of the pages. Under four pages nothing can be told apart, so only bare
    page numbers go. Page one keeps its top lines, the title lives there.
    """
    n = len(pages)
    split = [p.split('\n') for p in pages]
    c = Counter()

    def edge(ls):
        idx = [i for i, line in enumerate(ls) if line.strip()]
        return idx[:3] + idx[-3:]

    def key(line):
        return re.sub(r'\d+', '#', line.strip().lower())[:120]

    for ls in split:
        for k in {key(ls[i]) for i in edge(ls)}:
            c[k] += 1
    need = max(3, int(0.4 * n + 0.999))
    bad = {k for k, v in c.items() if v >= need and len(k) > 1} if n >= 4 else set()
    # a repeated sentence is content, not furniture, unless it sits on nearly every page
    bad = {k for k in bad if not re.search(r'[a-z]{3,}[.?!]$', k) or c[k] >= n - 1}
    out, dropped = [], []
    for pn, ls in enumerate(split):
        kill = set()
        top = [i for i, line in enumerate(ls) if line.strip()][:3]
        for i in edge(ls):
            k = key(ls[i])
            if pn == 0 and i in top and not re.fullmatch(r'#', k):
                continue
            if k in bad or re.fullmatch(r'#|page #( of #)?|- ?# ?-', k):
                kill.add(i)
                dropped.append(ls[i].strip()[:80])
        out.append('\n'.join(line for i, line in enumerate(ls) if i not in kill))
    return out, sorted(set(dropped))[:12]


def _word_counts(pages: list) -> Counter:
    c = Counter()
    for p in pages:
        c.update(re.findall(r"[^\W\d_]{2,}(?:-[^\W\d_]{2,})*", p))
    return c


def join_hyphens(pages: list) -> list:
    """Join words cut at a line end, also across a page. If the text itself writes the word with a
    hyphen elsewhere, the hyphen stays."""
    vocab = _word_counts(pages)
    rx = re.compile(r'([^\W\d_]{2,})[-\u2010\u00ad]\n[ \t]*([a-z\u00df-\u024f]{2,})')

    def fix(m):
        a, b = m.group(1), m.group(2)
        if vocab.get(a + '-' + b, 0) > vocab.get(a + b, 0):
            return a + '-' + b + '\n'
        return a + b + '\n'

    out = [rx.sub(fix, p).replace('\u00ad', '') for p in pages]
    for i in range(len(out) - 1):
        m = re.search(r'([^\W\d_]{2,})[-\u2010]\s*$', out[i])
        n = re.match(r'\s*([a-z\u00df-\u024f]{2,})', out[i + 1])
        if m and n:
            out[i] = out[i][:m.start()] + m.group(1) + n.group(1) + '\n'
            out[i + 1] = out[i + 1][n.end():]
    return out


def resplit_glued(pages: list) -> tuple:
    # a compound cut at a line end can come out glued (card-board becomes cardboard). where the same
    # text writes the hyphen form elsewhere, the glued form gets its hyphen back
    vocab = _word_counts(pages)
    fix = {}
    for w, n in vocab.items():
        if '-' not in w:
            continue
        parts = w.split('-')
        for i in range(len(parts) - 1):
            g = '-'.join(parts[:i]) + ('-' if i else '') + parts[i] + parts[i + 1] \
                + ('-' if i + 2 < len(parts) else '') + '-'.join(parts[i + 2:])
            if g in vocab and vocab[g] <= n and g not in fix:
                fix[g] = w
    if not fix:
        return pages, 0
    items = sorted(fix, key=len, reverse=True)[:4000]
    rx = re.compile(r'(?<![\w-])(' + '|'.join(re.escape(x) for x in items) + r')(?![\w-])')
    hits = [0]

    def rep(m):
        hits[0] += 1
        return fix[m.group(1)]

    return [rx.sub(rep, p) for p in pages], hits[0]


def clean_pages(pages: list) -> list:
    """Ligatures, control chars, running headers and footers, hyphen joins. Same length in, same out."""
    pages = [sanitise(p) for p in pages]
    pages, _ = drop_furniture(pages)
    pages = ['\n'.join(fix_small_caps(line) for line in p.split('\n')) for p in pages]
    pages = join_hyphens(pages)
    pages, _ = resplit_glued(pages)
    return pages


def usable(text: str) -> bool:
    """False for empty text and for junk (a font without a unicode map comes out as symbols)."""
    t = re.sub(r'\s+', '', text)[:200000]
    if not t:
        return False
    return sum(c.isalpha() for c in t) / len(t) >= 0.45


def find_refs(pages: list):
    """(page index, line index) where the reference list starts, or None.

    The last heading that says references in the back part wins. A run of ten or more numbered
    entries with no heading counts too, from its first line.
    """
    n = len(pages)
    best = None
    for pi in range(n - 1, -1, -1):
        ls = pages[pi].split('\n')
        for li in range(len(ls) - 1, -1, -1):
            t = re.sub(r'^\s*(\d{1,2}\.?|[IVX]{1,5}\.)\s*', '', ls[li])
            t = re.sub(r'[^a-z]', '', t.lower()) if len(t) < 40 else ''
            if t in REF_WORDS and pi >= int(0.3 * n) and (n > 1 or li > 3):
                best = (pi, li)
                break
        if best:
            break
    if best:
        return best
    for pi in range(int(0.3 * n), n):
        ls = pages[pi].split('\n')
        hits = [i for i, line in enumerate(ls) if SOFT_REF.match(line)]
        if len(hits) >= 10:
            return (pi, hits[0])
    return None


def _reflow(par: str) -> str:
    return ' '.join(line.strip() for line in par.split('\n') if line.strip())


def chunks_of_pages(pages: list, refs, chunk_chars: int = 1000) -> list:
    """Passages as (first_page, last_page, heading, text), page numbers from 1.

    One page never shares a passage with another, so a citation is path and page. Reference
    lists carry the heading References.
    """
    out, head, in_refs = [], '', False
    buf, size = [], 0

    def flush(page, heading):
        nonlocal buf, size
        t = ' '.join(buf).strip()
        if t:
            out.append((page, page, heading, t))
        buf, size = [], 0

    for pi, p in enumerate(pages):
        ls = p.split('\n')
        if refs and pi == refs[0]:
            before = '\n'.join(ls[:refs[1]])
            after = '\n'.join(ls[refs[1] + 1:])
            parts = [(before, False), (after, True)]
        else:
            parts = [(p, in_refs)]
        for text, flag in parts:
            if flag:
                in_refs = True
            buf, size = [], 0
            cur = 'References' if flag else head
            for par in re.split(r'\n\s*\n', text):
                one = par.strip()
                if not one:
                    continue
                if flag and AFTER_REFS.match(one) and len(one) < 60 and refs and pi >= refs[0] \
                        and not SOFT_REF.match(one):
                    # reference list is over, appendix starts here
                    flush(pi + 1, cur)
                    flag = False
                    in_refs = False
                    cur = one[:120]
                    head = cur
                    continue
                if not flag and HEAD_RX.match(one) and '\n' not in one:
                    flush(pi + 1, cur)
                    head = cur = one[:120]
                    continue
                t = _reflow(par)
                if len(t) > chunk_chars:
                    flush(pi + 1, cur)
                    for k in range(0, len(t), max(1, chunk_chars - 100)):
                        out.append((pi + 1, pi + 1, cur, t[k:k + chunk_chars]))
                    continue
                if size + len(t) > chunk_chars and buf:
                    flush(pi + 1, cur)
                buf.append(t)
                size += len(t) + 1
            flush(pi + 1, cur)
    return out


def chunks_of_extracted(ex: Extracted, chunk_chars: int = 1000) -> list:
    """Passages for any Extracted: (first_unit, last_unit, heading, text).

    Pages go through chunks_of_pages. Paragraphs and image text are packed up to chunk_chars,
    a new heading starts a new passage, a very long unit is cut into pieces that cite the same unit.
    """
    units = ex['units']
    if not units:
        return []
    if ex['unit'] == 'page':
        pages = [''] * max(n for n, _, _ in units)
        for n, _, t in units:
            pages[n - 1] = t
        return chunks_of_pages(pages, find_refs(pages), chunk_chars)
    out, buf, first, last, head, size = [], [], 0, 0, '', 0

    def flush():
        nonlocal buf, size
        t = '\n'.join(buf).strip()
        if t:
            out.append((first, last, head, t))
        buf, size = [], 0

    for n, h, t in units:
        t = t.strip()
        if not t:
            continue
        if buf and (h != head or size + len(t) > chunk_chars):
            flush()
        if not buf:
            first, head = n, h
        if len(t) > chunk_chars:
            flush()
            for k in range(0, len(t), max(1, chunk_chars - 100)):
                out.append((n, n, h, t[k:k + chunk_chars]))
            continue
        buf.append(t)
        last = n
        size += len(t) + 1
    flush()
    return out


def title_of(pages: list, name: str) -> str:
    for line in (pages[0] if pages else '').split('\n')[:25]:
        t = line.strip()
        if len(t) < 12 or len(t) > 200 or re.search(
                r'(?i)arxiv|doi|https?:|\(c\)|received|accepted|proceedings|journal|vol\.|issn', t):
            continue
        return t
    return name


# ---------- pdf text layer through pypdf

def pdf_pages(path: str, max_secs: float = PDF_MAX_SECS) -> list | None:
    """Text of every page from the pypdf text layer. None without pypdf, on any error, or over a bound."""
    try:
        import pypdf
    except Exception:
        return None
    logging.getLogger('pypdf').setLevel(logging.CRITICAL)
    t0 = time.time()
    try:
        reader = pypdf.PdfReader(path)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(''):
                    return None
            except Exception:
                return None
        n = len(reader.pages)
        if n > PDF_MAX_PAGES:
            return None
        out, total = [], 0
        for i in range(n):
            if time.time() - t0 > max_secs:
                return None
            try:
                t = reader.pages[i].extract_text() or ''
            except Exception:
                t = ''
            total += len(t)
            if total > TEXT_MAX_CHARS:
                return None
            out.append(t)
        return out
    except Exception:
        return None


# ---------- docx: stdlib zipfile and xml

def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


class _Paras:
    # expat target: collects text of w:t, ends a paragraph at w:p, remembers heading styles
    def __init__(self):
        self.out, self.buf, self.in_t, self.head, self.size = [], [], 0, False, 0

    def start(self, tag, attrs):
        t = _local(tag)
        if t == 't':
            self.in_t += 1
        elif t in ('tab', 'br', 'cr'):
            self.buf.append(' ')
        elif t == 'pStyle':
            for k, v in attrs.items():
                if _local(k) == 'val' and re.match(r'(?i)(heading|title)', v or ''):
                    self.head = True

    def end(self, tag):
        t = _local(tag)
        if t == 't':
            self.in_t = max(0, self.in_t - 1)
        elif t == 'p':
            s = re.sub(r'\s+', ' ', ''.join(self.buf)).strip()
            if s:
                self.out.append((self.head, s))
            self.buf, self.head = [], False

    def data(self, d):
        if self.in_t > 0 and self.size < XML_MAX_TEXT:
            self.buf.append(d)
            self.size += len(d)

    def close(self):
        return self.out


def _xml_paragraphs(raw: bytes) -> list:
    # no doctype and no entity in any spelling: that closes entity expansion and outside references
    import xml.etree.ElementTree as ET
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        raise ValueError('xml encoding')
    low = raw.lower()
    if b'<!doctype' in low or b'<!entity' in low or b'<!element' in low:
        raise ValueError('xml dtd')
    m = re.search(rb'encoding=["\']([a-z0-9_-]+)', low[:200])
    if m and m.group(1) not in (b'utf-8', b'utf8', b'us-ascii', b'ascii'):
        raise ValueError('xml encoding')
    p = ET.XMLParser(target=_Paras())
    try:
        p.feed(raw)
        return p.close()
    except ET.ParseError:
        raise ValueError('xml broken') from None


def _zip_limits(limits: dict | None) -> dict:
    d = dict(ZIP_DEFAULTS)
    for k in d:
        v = (limits or {}).get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            d[k] = v
    return d


def _docx_parse(path: str, limits: dict | None = None) -> list:
    """[(is_heading, text)] per paragraph. Raises ValueError with a short fixed reason."""
    lim = _zip_limits(limits)
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError('broken') from None
    with z:
        infos = z.infolist()
        if len(infos) > lim['zip_max_members']:
            raise ValueError('over a bound: members')
        for info in infos:
            if info.filename != 'word/document.xml':
                continue
            if info.flag_bits & 0x1:
                raise ValueError('encrypted')
            if info.file_size > lim['zip_max_member']:
                raise ValueError('over a bound: member')
            # the size in the header can lie: read one byte past the bound and stop
            with z.open(info) as f:
                raw = f.read(lim['zip_max_member'] + 1)
            if len(raw) > lim['zip_max_member'] or len(raw) > lim['zip_max_total']:
                raise ValueError('over a bound: member')
            return _xml_paragraphs(raw)
    return []


def docx_paragraphs(path: str, limits: dict | None = None) -> list:
    """Paragraph texts of a docx. Raises ValueError on a bad or over-bound file."""
    return [t for _, t in _docx_parse(path, limits)]


# ---------- the OCR helper

def _fixed_error(e) -> str | None:
    if not e:
        return None
    return e if e in HELPER_ERRORS else 'helper error'


def _num(v, default=0):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else default


def _clean_obj(o: dict) -> dict:
    mc = o.get('mean_confidence')
    mc = float(mc) if isinstance(mc, (int, float)) and not isinstance(mc, bool) and 0.0 <= mc <= 1.0 else None
    sec = o.get('seconds')
    kind = o.get('kind')
    text = o.get('text')
    return {'path': o['path'],
            'kind': kind if kind in HELPER_KINDS else '',
            'pages': _num(o.get('pages')), 'chars': _num(o.get('chars')), 'lines': _num(o.get('lines')),
            'mean_confidence': mc,
            'low_confidence_lines': _num(o.get('low_confidence_lines')),
            'seconds': float(sec) if isinstance(sec, (int, float)) and not isinstance(sec, bool) else 0.0,
            'text': text if isinstance(text, str) else '',
            'upscaled': o.get('upscaled') is True,
            'error': _fixed_error(o.get('error'))}


def _feed(p, data: bytes) -> None:
    try:
        p.stdin.write(data)
        p.stdin.close()
    except (OSError, ValueError):
        pass


def _run_helper(argv: list, data: bytes, timeout: float, env: dict) -> tuple:
    # own process group so the whole tree dies on timeout. returns (stdout bytes, 'timeout' | 'output' | None)
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         env=env, start_new_session=True)
    threading.Thread(target=_feed, args=(p, data), daemon=True).start()
    out, size, why = [], 0, None
    t0 = time.time()
    fd = p.stdout.fileno()
    while True:
        left = timeout - (time.time() - t0)
        if left <= 0:
            why = 'timeout'
            break
        r, _, _ = select.select([fd], [], [], min(left, 1.0))
        if not r:
            continue
        b = os.read(fd, 1 << 16)
        if not b:
            break
        size += len(b)
        if size > OCR_MAX_OUT:
            why = 'output'
            break
        out.append(b)
    if why:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.wait()
    p.stdout.close()
    return b''.join(out), why


def ocr_batch(cfg, abs_paths: list, timeout: float = 600.0) -> dict:
    """One helper process for many files. Returns path -> helper object (cleaned, fixed fields).

    A file the helper did not answer for gets {'path', 'error'} with a fixed code: 'timeout',
    'no result', 'bad path' or 'ocr unavailable'. No exception text is ever kept.
    """
    paths = list(dict.fromkeys(abs_paths))
    res = {}
    if not paths:
        return res
    if not helper_available(cfg):
        return {p: {'path': p, 'error': 'ocr unavailable'} for p in paths}
    want = {}
    for p in paths:
        s = p.strip()
        if not s or '\n' in s or '\r' in s:
            res[p] = {'path': p, 'error': 'bad path'}
        else:
            want[s] = p
    if not want:
        return res
    env = dict(os.environ)
    env['LEKS_OCR_MAX_PAGES'] = str(_num(_ocr_cfg(cfg).get('max_pages'), 40) or 40)
    data = ('\n'.join(want) + '\n').encode('utf-8', 'surrogateescape')
    try:
        out, why = _run_helper([_helper_path(cfg)], data, float(timeout), env)
    except OSError:
        out, why = b'', 'no result'
    for line in out.split(b'\n'):
        if not line.strip():
            continue
        try:
            o = json.loads(line.decode('utf-8', 'replace'))
        except ValueError:
            continue
        if isinstance(o, dict) and isinstance(o.get('path'), str) and o['path'] in want:
            orig = want[o['path']]
            c = _clean_obj(o)
            c['path'] = orig
            res[orig] = c
    miss = 'timeout' if why == 'timeout' else 'no result'
    for orig in want.values():
        if orig not in res:
            res[orig] = {'path': orig, 'error': miss}
    return res


def build_helper(cfg) -> tuple:
    """Compile leks/ocr/extract_text.swift into the helper path. Returns (ok, message)."""
    if sys.platform != 'darwin':
        return False, 'the OCR helper needs macOS'
    swiftc = shutil.which('swiftc')
    if not swiftc:
        return False, 'swiftc not found: install the Xcode command line tools'
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ocr', 'extract_text.swift')
    if not os.path.isfile(src):
        return False, 'helper source not found'
    dst = _helper_path(cfg)
    tmp = dst + '.tmp%d' % os.getpid()
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        r = subprocess.run([swiftc, '-O', src, '-o', tmp], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=BUILD_SECS)
    except subprocess.TimeoutExpired:
        _rm(tmp)
        return False, 'swiftc timed out'
    except OSError:
        _rm(tmp)
        return False, 'swiftc could not run'
    if r.returncode != 0 or not os.path.isfile(tmp):
        _rm(tmp)
        return False, 'swiftc failed with exit code %d' % r.returncode
    os.chmod(tmp, 0o755)
    os.replace(tmp, dst)
    return True, 'built the OCR helper'


def _rm(p: str) -> None:
    try:
        os.remove(p)
    except OSError:
        pass


# ---------- extraction

def _name_of(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _alnum(text: str) -> int:
    return sum(c.isalnum() for c in text)


def _conf_warning(o: dict, extra: str = '') -> str:
    w = 'text from OCR'
    if o.get('mean_confidence') is not None:
        w += ', mean confidence %.2f' % o['mean_confidence']
    if o.get('low_confidence_lines'):
        w += ', %d low confidence lines' % o['low_confidence_lines']
    return w + extra


def _from_ocr_image(o: dict, name: str) -> Extracted | None:
    text = sanitise(o.get('text') or '').strip()
    if o.get('error') or _alnum(text) < 3:
        return None
    return {'unit': 'image', 'title': name, 'units': [(1, 'OCR text', text)], 'extractor': 'ocr',
            'version': EXTRACTOR_VERSION, 'warning': _conf_warning(o)}


def _from_ocr_pdf(o: dict, name: str, max_pages: int) -> Extracted | None:
    raw = o.get('text') or ''
    if o.get('error') or not raw.strip():
        return None
    extra = ''
    if '\f' in raw:
        pages = raw.split('\f')
        if len(pages) > 1 and not pages[-1].strip():
            pages.pop()
    else:
        pages = [raw]
        if o.get('pages', 1) > 1:
            extra = ', page breaks unknown'
    pages = clean_pages(pages)
    if not any(usable(p) for p in pages):
        return None
    pages = [p if usable(p) else '' for p in pages]
    if o.get('kind') == 'pdf-ocr':
        if o.get('pages', 0) > max_pages:
            extra += ', first %d of %d pages' % (max_pages, o['pages'])
        warning = _conf_warning(o, extra)
    else:
        warning = extra.lstrip(', ') or None
    refs = find_refs(pages)
    return {'unit': 'page', 'title': title_of(pages, name), 'units': _page_units(pages, refs),
            'extractor': 'ocr', 'version': EXTRACTOR_VERSION, 'warning': warning}


def _page_units(pages: list, refs) -> list:
    # one unit per non-empty page, heading is the section in force at the top of the page
    chunks = chunks_of_pages(pages, refs, 1000)
    heads = {}
    for first, _, h, _t in chunks:
        heads.setdefault(first, []).append(h)
    units = []
    for i, p in enumerate(pages):
        t = p.strip()
        if not t:
            continue
        hs = heads.get(i + 1, [''])
        h = 'References' if all(x == 'References' for x in hs) else hs[0]
        units.append((i + 1, h, t))
    return units


def needs_ocr(cfg, abs_path: str, ext: str) -> bool:
    """True when this file can only be read through the helper, so a caller can batch it.

    An image always. A pdf when pypdf is missing, or when its text layer has no usable page.
    """
    ext = ext.lower()
    if not helper_available(cfg):
        return False
    if ext in IMAGE_EXT:
        return bool(_ocr_cfg(cfg).get('images'))
    if ext != '.pdf':
        return False
    try:
        if os.path.getsize(abs_path) > _limits(cfg)['pdf_max_bytes']:
            return False
    except OSError:
        return False
    pages = pdf_pages(abs_path)
    return pages is None or not any(usable(p) for p in pages)


def skip_reason(cfg, abs_path: str, ext: str) -> str:
    """Short fixed reason for a file extract() returned None for."""
    ext = ext.lower()
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return 'unreadable'
    lim = _limits(cfg)
    if ext == '.pdf':
        if size > lim['pdf_max_bytes']:
            return 'over size limit'
        caps = capabilities(cfg)
        return 'no pdf text extractor' if not (caps['pdf_text'] or caps['ocr']) else 'no usable text'
    if ext == '.docx':
        return 'over size limit' if size > lim['office_max_bytes'] else 'no usable text'
    if ext in IMAGE_EXT:
        return 'ocr off or helper not built' if not capabilities(cfg)['images'] else 'no usable text'
    return 'unsupported type'


def _extract_pdf(cfg, path: str, ocr: dict | None) -> Extracted | None:
    name = _name_of(path)
    pages = pdf_pages(path)
    if pages is not None and any(usable(p) for p in pages):
        pages = clean_pages([p if usable(p) else '' for p in pages])
        refs = find_refs(pages)
        units = _page_units(pages, refs)
        if units:
            return {'unit': 'page', 'title': title_of(pages, name), 'units': units, 'extractor': 'pypdf',
                    'version': EXTRACTOR_VERSION, 'warning': None}
    if not helper_available(cfg):
        return None
    o = ocr.get(path) if ocr is not None else ocr_batch(cfg, [path]).get(path)
    if not o:
        return None
    return _from_ocr_pdf(o, name, _num(_ocr_cfg(cfg).get('max_pages'), 40) or 40)


def _extract_docx(cfg, path: str) -> Extracted | None:
    paras = _docx_parse(path, _limits(cfg))
    if not paras:
        return None
    units, cur = [], ''
    for n, (is_head, t) in enumerate(paras):
        if is_head:
            cur = t[:120]
        units.append((n + 1, cur, t))
    first = paras[0][1]
    title = first if len(first) <= 200 else _name_of(path)
    return {'unit': 'paragraph', 'title': title, 'units': units, 'extractor': 'docx',
            'version': EXTRACTOR_VERSION, 'warning': None}


def extract(cfg, abs_path: str, ext: str, ocr: dict | None = None) -> Extracted | None:
    """Text units for one pdf, docx or image. None: no usable text, the file stays a name only.

    `ocr` is an optional result of ocr_batch for a batch the caller ran up front (see needs_ocr).
    Left out, a scan or an image runs a one-file helper call of its own.
    Never raises on a bad file, and never keeps exception text.
    """
    ext = ext.lower()
    try:
        size = os.path.getsize(abs_path)
        lim = _limits(cfg)
        if ext == '.pdf':
            if size > lim['pdf_max_bytes']:
                return None
            return _extract_pdf(cfg, abs_path, ocr)
        if ext == '.docx':
            if size > lim['office_max_bytes']:
                return None
            return _extract_docx(cfg, abs_path)
        if ext in IMAGE_EXT:
            if not (_ocr_cfg(cfg).get('images') and helper_available(cfg)):
                return None
            o = ocr.get(abs_path) if ocr is not None else ocr_batch(cfg, [abs_path]).get(abs_path)
            return _from_ocr_image(o, _name_of(abs_path)) if o else None
    except Exception:
        return None
    return None
