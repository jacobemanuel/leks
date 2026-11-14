"""Index builder.

update(): walks every configured root and keeps one master database current, file by file.
materialize(): cuts one search database per profile out of the master and swaps it in whole.

No model in here. Vectors are made later by embed_all; a passage without one is still found by words.
Documents and OCR come from docs, locate rows from lane_locate, vector side files from vecstore.
Those three are imported lazily, so the builder works on notes and code without them.
"""
import hashlib
import importlib
import inspect
import json
import os
import re
import sqlite3
import stat as statmod
import time

from . import chunking, config, lane_code, schema

BUILD_VERSION = '2'
LOCK_STALE_SECS = 3600
COMMIT_EVERY = 200
MAX_DEPTH = 64
JSON_MAX = getattr(config, 'JSON_MAX_BYTES', 200000)

# text that must never reach an index. a hit anywhere in a file keeps the whole file out
SECRET_TEXT = [
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}'),
    re.compile(r'\bxox[bp]-[A-Za-z0-9-]{10,}'),
    re.compile(r'\bsk-[A-Za-z0-9_-]{40,}'),
    re.compile(r'(?i)password\s*[:=]\s*\S{8,}'),
]

# the only reasons that ever land in the skipped table, never text from an exception
WHY_SECRET = 'secret pattern'
WHY_SIZE = 'over size limit'
WHY_BINARY = 'binary content'
WHY_NO_DOCS = 'no document extractor'
WHY_NO_PDF = 'no pdf text extractor'
WHY_NO_OCR = 'ocr off or helper not built'
WHY_NO_TEXT = 'no usable text'
WHY_FAILED = 'extract failed'
WHY_UNREADABLE = 'unreadable'
VOCAB = {WHY_SECRET, WHY_SIZE, WHY_BINARY, WHY_NO_DOCS, WHY_NO_PDF, WHY_NO_OCR, WHY_NO_TEXT, WHY_FAILED,
         WHY_UNREADABLE, 'unsupported type'}
# these may clear up when a tool shows up, so a change of capabilities makes the files try again
RETRY = (WHY_NO_DOCS, WHY_NO_PDF, WHY_NO_OCR, WHY_NO_TEXT)

PDF_EXT = '.pdf'
DOCX_EXT = '.docx'


def _mod(name):
    """Optional sibling module, None when it is missing or broken."""
    try:
        return importlib.import_module('.' + name, __package__)
    except Exception:
        return None


def _category(ext):
    if ext == PDF_EXT:
        return 'pdf'
    if ext == DOCX_EXT:
        return 'docx'
    if ext in config.IMAGE_EXT:
        return 'image'
    return 'text'


def _limit(cfg, ext):
    lim = cfg.limits or {}
    cat = _category(ext)
    if cat in ('pdf', 'image'):
        return lim.get('pdf_max_bytes', 209715200)
    if cat == 'docx':
        return lim.get('office_max_bytes', 419430400)
    base = lim.get('max_file_bytes', 1500000)
    return min(base, JSON_MAX) if ext == '.json' else base


def _chunk_chars(cfg):
    return int((cfg.limits or {}).get('chunk_chars', 1000))


def _text_extractor(ext):
    if ext in lane_code.LANG:
        return 'code:%s.%s' % (BUILD_VERSION, lane_code.EXTRACT_VERSION)
    return 'text:%s' % BUILD_VERSION


def _docs_version(dm):
    return getattr(dm, 'EXTRACTOR_VERSION', 1) if dm is not None else None


def _version_ok(ext, extractor, dm):
    if not extractor:
        return False
    if _category(ext) == 'text':
        return extractor == _text_extractor(ext)
    if dm is None:
        return True    # nothing to compare with, keep what the index has
    return str(extractor).rsplit(':', 1)[-1] == str(_docs_version(dm))


def has_secret(text):
    return any(rx.search(text) for rx in SECRET_TEXT)


def _caps(cfg, dm):
    keys = ('pdf_text', 'ocr', 'docx', 'images')
    if dm is None:
        return {k: False for k in keys}
    try:
        c = dm.capabilities(cfg)
        return {k: bool(c.get(k)) for k in keys}
    except Exception:
        return {k: False for k in keys}


def _ocr_aware(dm):
    """True when docs can take a batch result in extract() and say which files need one."""
    if dm is None or not callable(getattr(dm, 'needs_ocr', None)):
        return False
    try:
        return 'ocr' in inspect.signature(dm.extract).parameters
    except (TypeError, ValueError):
        return False


def _gate(cat, caps, dm):
    """Reason a document type cannot be read right now, None when it can."""
    if dm is None:
        return WHY_NO_DOCS
    if cat == 'pdf' and not (caps['pdf_text'] or caps['ocr']):
        return WHY_NO_PDF
    if cat == 'docx' and not caps['docx']:
        return WHY_NO_DOCS
    if cat == 'image' and not caps['images']:
        return WHY_NO_OCR
    return None


def _class_of(ext):
    lm = _mod('lane_locate')
    if lm is not None and hasattr(lm, 'class_of'):
        try:
            return lm.class_of(ext)
        except Exception:
            pass
    ext = (ext or '').lower()
    if ext in ('.md', '.markdown', '.txt', '.rst', '.tex', '.html', '.htm'):
        return 'note'
    if ext in lane_code.CODE_EXT or ext in chunking.CONFIG_EXT:
        return 'code'
    if ext in (PDF_EXT, DOCX_EXT):
        return 'document'
    if ext in config.IMAGE_EXT:
        return 'image'
    if ext in ('.csv', '.npy', '.bin', '.sqlite', '.parquet', '.ply', '.obj', '.xml'):
        return 'data'
    return 'other'


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _lines_of(text):
    lines = text.replace('\r\n', '\n').split('\n')
    if len(lines) > 1 and lines[-1] == '':
        lines.pop()
    return lines


def _summary(lines, fm):
    for k in ('summary', 'description'):
        v = chunking._unquote(fm.get(k))
        if v:
            return v[:300]
    para = []
    in_fence = False
    for line in lines[chunking.body_start(lines):chunking.body_start(lines) + 120]:
        s = line.strip()
        if s.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not s:
            if para:
                break
            continue
        if s.startswith(('#', '---', '|', '>', '<', '![')):
            if para:
                break
            continue
        para.append(s)
    return ' '.join(para)[:300]


# walking

def walk(root, limits):
    """Yield (rel, stat) for every file of a root that is not skipped. rel uses '/'.
    Directory links are followed only when the root says so. `limits` is kept for the
    signature, the walk itself has a fixed depth bound."""
    base = root.path
    seen_dirs = set()
    for dp, dn, fn in os.walk(base, followlinks=root.follow_links):
        rel_d = os.path.relpath(dp, base)
        rel_d = '' if rel_d == '.' else rel_d.replace(os.sep, '/')
        if rel_d.count('/') >= MAX_DEPTH:
            dn[:] = []
            continue
        if root.follow_links:
            try:
                st = os.stat(dp)
            except OSError:
                dn[:] = []
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen_dirs:
                dn[:] = []
                continue
            seen_dirs.add(key)
        keep = []
        for d in sorted(dn):
            probe = (rel_d + '/' + d if rel_d else d) + '/x'
            if config.skipped(root, probe):
                continue
            keep.append(d)
        dn[:] = keep
        for f in sorted(fn):
            rel = rel_d + '/' + f if rel_d else f
            try:
                rel.encode('utf-8')
            except UnicodeEncodeError:
                continue
            if config.skipped(root, rel):
                continue
            p = os.path.join(dp, f)
            try:
                st = os.lstat(p)
                if statmod.S_ISLNK(st.st_mode):
                    if not root.follow_links:
                        continue
                    st = os.stat(p)
            except OSError:
                continue
            if statmod.S_ISREG(st.st_mode):
                yield rel, st


# one file to rows

def _doc_row(root, rel, ext, kind, title, summary, warning, status, src, date, st, sha, units, unit,
             extractor):
    return dict(root=root.name, rel=rel, ext=ext, kind=kind, title=title, summary=summary or '',
                warning=warning or '', status=status, status_src=src, date=date, mtime=st.st_mtime,
                size=st.st_size, sha256=sha, units=units, unit=unit, extractor=extractor,
                indexed_at=time.time())


def _chunk_rows(title, chunks):
    rows = []
    for i, (a, b, h, t) in enumerate(chunks):
        rows.append((i, a, b, h, t, schema.ehash(schema.embed_input(title, h, t))))
    return rows


def _index_text(cfg, root, rel, st, raw, sha, ext, kind):
    if b'\0' in raw[:8000]:
        return None, WHY_BINARY
    text = raw.decode('utf-8', 'replace')
    if has_secret(text):
        return None, WHY_SECRET
    if ext in ('.html', '.htm'):
        text = chunking.strip_html(text)
    lines = _lines_of(text)
    name = os.path.basename(rel)
    fm, head = chunking.meta_of(lines)
    title = chunking.title_of(lines, fm, name)
    status, src = chunking.status_of(kind, rel, fm, cfg.classify, head)
    is_code = kind == 'code'
    chunks = chunking.chunks_of(lines, is_code, _chunk_chars(cfg))
    syms = []
    if ext in lane_code.LANG:
        found, _how = lane_code.extract(text, ext)
        lang = lane_code.LANG[ext]
        syms = [(s[0], s[1], lang, s[2], s[3], s[4], s[5]) for s in found]
    doc = _doc_row(root, rel, ext, kind, title, '' if is_code else _summary(lines, fm),
                   fm.get('warning', ''), status, src, chunking.date_of(name, fm, st.st_mtime), st, sha,
                   len(lines), 'line', _text_extractor(ext))
    return (doc, _chunk_rows(title, chunks), syms)


def _cache_get(master, sha, version):
    r = master.execute('SELECT extractor,version,unit,title,units FROM extract_cache WHERE sha256=?',
                       (sha,)).fetchone()
    if not r or str(r[1]) != str(version):
        return None
    try:
        units = [(int(u[0]), str(u[1] or ''), str(u[2] or '')) for u in json.loads(r[4])]
    except (ValueError, TypeError, IndexError):
        return None
    w = master.execute("SELECT warning FROM files WHERE sha256=? AND warning!='' LIMIT 1", (sha,)).fetchone()
    warn = w[0] if w else ('text from OCR' if r[0] == 'ocr' else None)
    return {'unit': r[2], 'title': r[3], 'units': units, 'extractor': r[0], 'version': r[1], 'warning': warn}


def _cache_put(master, sha, ex):
    master.execute('INSERT OR REPLACE INTO extract_cache VALUES(?,?,?,?,?,?)',
                   (sha, ex['extractor'], int(ex.get('version') or 0), ex['unit'], ex.get('title') or '',
                    json.dumps([[int(u[0]), u[1] or '', u[2] or ''] for u in ex['units']])))


def _image_from_ocr(o, name):
    """Extracted dict from one helper object, for a docs module that cannot take a batch itself."""
    if not isinstance(o, dict) or o.get('error'):
        return None
    text = str(o.get('text') or '').strip()
    if sum(c.isalnum() for c in text) < 3:
        return None
    warn = 'text from OCR'
    if isinstance(o.get('mean_confidence'), (int, float)):
        warn += ', mean confidence %.2f' % o['mean_confidence']
    return {'unit': 'image', 'title': name, 'units': [(1, 'OCR text', text)], 'extractor': 'ocr',
            'version': 1, 'warning': warn}


def _why_none(cfg, dm, abs_path, ext, caps):
    fn = getattr(dm, 'skip_reason', None)
    if callable(fn):
        try:
            r = fn(cfg, abs_path, ext)
            return r if r in VOCAB else WHY_NO_TEXT
        except Exception:
            pass
    cat = _category(ext)
    if cat == 'pdf' and not (caps['pdf_text'] or caps['ocr']):
        return WHY_NO_PDF
    if cat == 'image' and not caps['images']:
        return WHY_NO_OCR
    return WHY_NO_TEXT


def _page_chunks(dm, ex, chunk_chars):
    """Passages of a page document. docs knows how to find reference lists, so ask it first."""
    units = ex['units']
    try:
        top = max(u[0] for u in units)
        pages = [''] * top
        for n, _h, t in units:
            pages[n - 1] = t
        return [tuple(c) for c in dm.chunks_of_pages(pages, dm.find_refs(pages), chunk_chars)]
    except Exception:
        return chunking.chunks_of_units(units, False, chunk_chars)


def _index_doc(cfg, root, rel, st, sha, ext, kind, master, ocr, dm):
    abs_path = os.path.join(root.path, rel)
    cat = _category(ext)
    ver = _docs_version(dm)
    caps = _caps(cfg, dm)
    ex = _cache_get(master, sha, ver)
    if ex is None:
        if cat == 'image' and not _ocr_aware(dm) and ocr is not None:
            ex = _image_from_ocr(ocr.get(abs_path), os.path.splitext(os.path.basename(rel))[0])
        elif ocr is not None and _ocr_aware(dm):
            ex = dm.extract(cfg, abs_path, ext, ocr=ocr)
        else:
            ex = dm.extract(cfg, abs_path, ext)
        if ex is None or not ex.get('units'):
            return None, _why_none(cfg, dm, abs_path, ext, caps)
        ex = dict(ex)
        ex['units'] = [(int(u[0]), str(u[1] or ''), str(u[2] or '')) for u in ex['units']]
        if has_secret('\n'.join(u[2] for u in ex['units'])):
            return None, WHY_SECRET
        _cache_put(master, sha, ex)
    elif has_secret('\n'.join(u[2] for u in ex['units'])):
        return None, WHY_SECRET
    units = ex['units']
    unit = ex['unit']
    if unit == 'page':
        chunks = _page_chunks(dm, ex, _chunk_chars(cfg))
    else:
        chunks = chunking.chunks_of_units(units, unit == 'paragraph', _chunk_chars(cfg))
    name = os.path.splitext(os.path.basename(rel))[0]
    title = (ex.get('title') or name)[:200]
    status, src = chunking.status_of(kind, rel, {}, cfg.classify)
    first = re.sub(r'\s+', ' ', units[0][2]).strip()[:300]
    count = max(u[0] for u in units) if unit == 'page' else len(units)
    doc = _doc_row(root, rel, ext, kind, title, first, ex.get('warning'), status, src,
                   chunking.date_of(os.path.basename(rel), {}, st.st_mtime), st, sha, count, unit,
                   '%s:%s' % (ex['extractor'], ex.get('version', ver)))
    return (doc, _chunk_rows(title, chunks), []), None


def index_file(cfg, root, rel, st, raw, master, sha=None, ocr=None):
    """Rows for one file: (doc, chunks, symbols), or (None, why).

    raw is the file's bytes for notes and code; documents and images are read by docs from the
    path, so raw may be empty and sha should be given. ocr is an optional batch result
    (path -> helper object) from docs.ocr_batch. Rows are returned, not stored. The only write
    to the master is the extract cache.
    """
    ext = os.path.splitext(rel)[1].lower()
    kind = chunking.kind_of(root, rel, ext, cfg.classify)
    cat = _category(ext)
    if cat == 'text':
        sha = sha or hashlib.sha256(raw).hexdigest()
        r = _index_text(cfg, root, rel, st, raw, sha, ext, kind)
        return r if r[0] is not None else (None, r[1])
    dm = _mod('docs')
    if dm is None:
        return None, WHY_NO_DOCS
    try:
        sha = sha or _sha_file(os.path.join(root.path, rel))
        r = _index_doc(cfg, root, rel, st, sha, ext, kind, master, ocr, dm)
    except Exception:
        return None, WHY_FAILED
    if r[0] is None:
        return None, r[1]
    return r[0]


# the master

def _lock(path):
    """mkdir lock. A lock older than an hour is a crash leftover and gets removed."""
    for _ in range(3):
        try:
            os.mkdir(path)
            return True
        except FileExistsError:
            try:
                age = time.time() - os.stat(path).st_mtime
            except OSError:
                continue
            if age <= LOCK_STALE_SECS:
                return False
            try:
                os.rmdir(path)
            except OSError:
                return False
        except OSError:
            return False
    return False


def _unlock(path):
    try:
        os.rmdir(path)
    except OSError:
        pass


def _cfg_sig(cfg):
    blob = json.dumps({'classify': cfg.classify, 'chunk': _chunk_chars(cfg), 'build': BUILD_VERSION},
                      sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()


def _store(master, doc, chunks, syms):
    r, p = doc['root'], doc['rel']
    master.execute('DELETE FROM chunks WHERE root=? AND rel=?', (r, p))
    master.execute('DELETE FROM symbols WHERE root=? AND rel=?', (r, p))
    master.execute('DELETE FROM skipped WHERE root=? AND rel=?', (r, p))
    master.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (r, p, doc['ext'], doc['kind'], doc['title'], doc['summary'], doc['warning'],
                    doc['status'], doc['status_src'], doc['date'], doc['mtime'], doc['size'],
                    doc['sha256'], doc['units'], doc['unit'], doc['extractor'], doc['indexed_at']))
    master.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)', [(r, p) + c for c in chunks])
    master.executemany('INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?,?)', [(r, p) + s for s in syms])


def _drop_file(master, r, p):
    for t in ('files', 'chunks', 'symbols'):
        master.execute('DELETE FROM %s WHERE root=? AND rel=?' % t, (r, p))


def update(cfg, force=False, log=print):
    """Bring the master up to date. Returns the counters, or {'skipped': 'locked'}."""
    run = cfg.run_dir()
    os.makedirs(run, exist_ok=True)
    # a caller that already holds update.lock says so in the environment
    held = os.environ.get('LEKS_LOCK_HELD') == '1'
    lock = os.path.join(run, 'update.lock')
    if not held and not _lock(lock):
        return {'skipped': 'locked'}
    try:
        return _update(cfg, force, log)
    finally:
        if not held:
            _unlock(lock)


def _update(cfg, force, log):
    t0 = time.time()
    master = schema.open_rw(cfg.master_path())
    dm = _mod('docs')
    caps = _caps(cfg, dm)
    aware = _ocr_aware(dm)
    st_ = dict(roots=0, seen=0, indexed=0, unchanged=0, removed=0, skipped=0, errors=0, ocr=0)
    meta = dict(master.execute('SELECT k,v FROM meta').fetchall())
    sig = _cfg_sig(cfg)
    if meta.get('sig') != sig:
        force = True
    caps_sig = json.dumps(caps, sort_keys=True)
    if meta.get('caps') != caps_sig:
        master.execute('DELETE FROM skipped WHERE why IN (%s)' % ','.join('?' * len(RETRY)), RETRY)
    known = {(r[0], r[1]): r[2:] for r in master.execute('SELECT root,rel,size,mtime,sha256,extractor FROM files')}
    skip_known = {(r[0], r[1]): r[2:] for r in master.execute('SELECT root,rel,why,size FROM skipped')}
    prev_names = {(r[0], r[1]): r[2:] for r in master.execute('SELECT root,rel,size,mtime FROM names')}
    indexed_seen, skip_seen, names_seen = set(), set(), set()
    untouched = set()
    pending = []
    changed_names = 0
    names_budget = int((cfg.limits or {}).get('max_names', 500000))
    n_names = 0
    since_commit = 0

    def skip(root, rel, why, size):
        master.execute('INSERT OR REPLACE INTO skipped VALUES(?,?,?,?)', (root.name, rel, why, size))
        skip_seen.add((root.name, rel))
        st_['skipped'] += 1

    for root in cfg.roots:
        if not os.path.isdir(root.path):
            # a missing folder (unmounted disk, moved project) must not empty the index
            log('root not reachable, kept as is: ' + root.name)
            untouched.add(root.name)
            continue
        st_['roots'] += 1
        name_rows = []
        for rel, st in walk(root, cfg.limits):
            key = (root.name, rel)
            st_['seen'] += 1
            ext = os.path.splitext(rel)[1].lower()
            if n_names < names_budget:
                n_names += 1
                name_rows.append((root.name, rel, st.st_size, st.st_mtime, _class_of(ext)))
                names_seen.add(key)
                pn = prev_names.get(key)
                if not pn or pn[0] != st.st_size or abs(pn[1] - st.st_mtime) > 1e-3:
                    changed_names += 1
            if not config.wanted(root, rel):
                continue
            cat = _category(ext)
            if st.st_size > _limit(cfg, ext):
                skip(root, rel, WHY_SIZE, st.st_size)
                continue
            k = known.get(key)
            if (not force and k and k[0] == st.st_size and abs(k[1] - st.st_mtime) < 1e-3
                    and _version_ok(ext, k[3], dm)):
                indexed_seen.add(key)
                st_['unchanged'] += 1
                continue
            sk = skip_known.get(key)
            pn = prev_names.get(key)
            if (not force and sk and sk[1] == st.st_size and pn and abs(pn[1] - st.st_mtime) < 1e-3
                    and sk[0] in VOCAB):
                skip_seen.add(key)
                st_['skipped'] += 1
                continue
            abs_path = os.path.join(root.path, rel)
            raw = b''
            try:
                if cat == 'text':
                    with open(abs_path, 'rb') as f:
                        raw = f.read(_limit(cfg, ext) + 1)
                    if len(raw) > _limit(cfg, ext):
                        skip(root, rel, WHY_SIZE, st.st_size)
                        continue
                    sha = hashlib.sha256(raw).hexdigest()
                else:
                    why = _gate(cat, caps, dm)
                    if why:
                        skip(root, rel, why, st.st_size)
                        continue
                    sha = _sha_file(abs_path)
            except OSError:
                st_['errors'] += 1
                if k:
                    indexed_seen.add(key)    # could not read it now, keep the old rows
                continue
            if not force and k and k[2] == sha and _version_ok(ext, k[3], dm):
                master.execute('UPDATE files SET mtime=?,size=? WHERE root=? AND rel=?',
                               (st.st_mtime, st.st_size, root.name, rel))
                indexed_seen.add(key)
                st_['unchanged'] += 1
                continue
            if cat in ('pdf', 'image') and _cache_get(master, sha, _docs_version(dm)) is None:
                need = cat == 'image' and not aware
                if aware:
                    try:
                        need = bool(dm.needs_ocr(cfg, abs_path, ext))
                    except Exception:
                        need = False
                if need:
                    pending.append((root, rel, st, sha, abs_path))
                    continue
            if _file_done(cfg, master, root, rel, st, raw, sha, k, key, indexed_seen, st_, skip):
                since_commit += 1
            if since_commit >= COMMIT_EVERY:
                master.commit()
                since_commit = 0
        master.executemany('INSERT OR REPLACE INTO names VALUES(?,?,?,?,?)', name_rows)
        master.commit()
    if pending:
        try:
            ocr = dm.ocr_batch(cfg, [p[4] for p in pending]) or {}
        except Exception:
            ocr = {}
        for root, rel, st, sha, _abs_path in pending:
            key = (root.name, rel)
            if _file_done(cfg, master, root, rel, st, b'', sha, known.get(key), key, indexed_seen, st_, skip,
                          ocr=ocr):
                st_['ocr'] += 1
    # what is gone: vanished, newly unwanted, or a root that left the config
    for key in list(known):
        if key not in indexed_seen and key[0] not in untouched:
            _drop_file(master, key[0], key[1])
            st_['removed'] += 1
    for key in list(prev_names):
        if key not in names_seen and key[0] not in untouched:
            master.execute('DELETE FROM names WHERE root=? AND rel=?', key)
            changed_names += 1
    for key in list(skip_known):
        if key not in skip_seen and key[0] not in untouched:
            master.execute('DELETE FROM skipped WHERE root=? AND rel=?', key)
    rev = int(meta.get('rev', '0'))
    if st_['indexed'] or st_['removed'] or changed_names:
        rev += 1
    for k, v in (('updated', str(time.time())), ('sig', sig), ('caps', caps_sig), ('rev', str(rev))):
        master.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (k, v))
    master.commit()
    master.close()
    st_['secs'] = round(time.time() - t0, 2)
    log('update: seen %d, indexed %d, unchanged %d, removed %d, skipped %d, errors %d, ocr %d, %.1fs' % (
        st_['seen'], st_['indexed'], st_['unchanged'], st_['removed'], st_['skipped'], st_['errors'],
        st_['ocr'], st_['secs']))
    return st_


def _file_done(cfg, master, root, rel, st, raw, sha, k, key, indexed_seen, st_, skip, ocr=None):
    """Index one file and store it. True when rows were written."""
    try:
        res = index_file(cfg, root, rel, st, raw, master, sha=sha, ocr=ocr)
    except Exception:
        res = (None, WHY_FAILED)
    if res[0] is None:
        if res[1] == WHY_FAILED:
            st_['errors'] += 1
        skip(root, rel, res[1] if res[1] in VOCAB else WHY_FAILED, st.st_size)
        return False
    doc, chunks, syms = res
    _store(master, doc, chunks, syms)
    indexed_seen.add(key)
    st_['indexed'] += 1
    return True


# profiles

def _hist(cfg, rel):
    return any(config.glob_match(rel, p) for p in (cfg.classify.get('history') or ()))


def _words(path):
    return path.replace('/', ' ').replace('_', ' ').replace('-', ' ')


def _master_reader(cfg):
    """Plain connection to the master that never writes, so it cannot block a running update."""
    if not os.path.exists(cfg.master_path()):
        schema.open_rw(cfg.master_path()).close()
    return sqlite3.connect(cfg.master_path(), timeout=120)


def _put_in_place(tmp, final):
    """Swap the new profile database in. Windows refuses to rename over a file another process has open,
    and a running server keeps its index open: then sqlite copies the new pages into the open file, which
    it allows between two reads, and the server sees the new index at its next call."""
    try:
        os.replace(tmp, final)
        return
    except PermissionError:
        if not os.path.exists(final):
            raise
    src = sqlite3.connect(tmp)
    try:
        dst = sqlite3.connect(final, timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.remove(tmp)


def materialize(cfg, profile, log=print):
    """Build search.sqlite for one profile from the master and swap it in whole."""
    t0 = time.time()
    roots = cfg.roots_for(profile)
    names = [r.name for r in roots]
    master = _master_reader(cfg)
    order = {n: i for i, n in enumerate(names)}
    rows = []
    if names:
        rows = master.execute(
            'SELECT root,rel,ext,kind,title,summary,warning,status,status_src,date,mtime,size,sha256,units,unit '
            'FROM files WHERE root IN (%s)' % ','.join('?' * len(names)), names).fetchall()
    rows.sort(key=lambda r: (order[r[0]], r[1]))
    # identical bytes appear once: the live, shortest path wins, the others are listed as copies
    best = {}
    for r in rows:
        if r[12] and (r[11] or 0) > 0:
            disp = r[0] + '/' + r[1]
            rank = (1 if _hist(cfg, r[1]) else 0, len(disp), disp)
            cur = best.get(r[12])
            if cur is None or rank < cur[0]:
                best[r[12]] = (rank, disp)
    winners, copies = [], {}
    for r in rows:
        disp = r[0] + '/' + r[1]
        if r[12] and (r[11] or 0) > 0 and best[r[12]][1] != disp:
            copies.setdefault(r[12], []).append(disp)
        else:
            winners.append(r)
    d = os.path.dirname(cfg.profile_db(profile))
    os.makedirs(d, exist_ok=True)
    final = cfg.profile_db(profile)
    tmp = final + '.tmp'
    for p in (tmp, tmp + '-journal'):
        if os.path.exists(p):
            os.remove(p)
    c = sqlite3.connect(tmp)
    try:
        out = _fill(cfg, c, master, profile, names, winners, copies, log)
        c.commit()
        c.close()
        c = None
        cov = {}
        vs = _mod('vecstore')
        if vs is not None:
            # a store that cannot be read only closes the meaning lane
            try:
                keys = vs.stored_keys(cfg)
            except Exception:
                keys = []
            for key in keys:
                try:
                    cov[key] = vs.write_profile(cfg, key, profile, tmp)
                except Exception:
                    cov[key] = None
        _put_in_place(tmp, final)
        master.close()
    except BaseException:
        master.close()
        if c is not None:
            c.close()
        for p in (tmp, tmp + '-journal'):
            if os.path.exists(p):
                os.remove(p)
        raise
    out['secs'] = round(time.time() - t0, 2)
    out['profile'] = profile
    log('profile %s: docs %d, chunks %d, distinct inputs %d, symbols %d, names %d, newer %d, %.1fs' % (
        profile, out['docs'], out['chunks'], out['distinct_inputs'], out['symbols'], out['names'],
        out['newer'], out['secs']))
    return out


def _fill(cfg, c, master, profile, names, winners, copies, log):
    c.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;')
    c.executescript(schema.PROFILE_DDL)
    lane_code.add_schema(c)
    emap = {}
    ids = {}
    n_chunks = 0
    n_documents = 0
    for i, r in enumerate(winners, 1):
        root, rel, ext, kind, title, summary, warning, status, src, date, mtime, size, sha, units, unit = r
        disp = root + '/' + rel
        ids[(root, rel)] = i
        cp = json.dumps(sorted(copies.get(sha, []))) if sha else '[]'
        c.execute('INSERT INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (i, disp, root, rel, os.path.basename(rel), ext, title, summary, warning, kind, status, src,
                   date, mtime, size, sha, units, unit, cp))
        is_doc = kind in ('document', 'image')
        n_documents += 1 if is_doc else 0
        heads = {}
        words = _words(disp)
        for o, a, b, h, t, e in master.execute(
                'SELECT ord,start_line,end_line,heading,text,ehash FROM chunks WHERE root=? AND rel=? ORDER BY ord',
                (root, rel)):
            v = emap.get(e)
            if v is None:
                v = len(emap)
                emap[e] = v
            n_chunks += 1
            if h:
                heads[h] = 1
            c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)',
                      (n_chunks, i, o, a, b, h, t, v, 1 if is_doc and h == 'References' else 0))
            head_title = ((title or '') + ' ' + (summary or '')).strip()
            if is_doc:
                c.execute('INSERT INTO dfts(rowid,title,heading,path,text) VALUES(?,?,?,?,?)',
                          (n_chunks, head_title, h, words, t))
            else:
                c.execute('INSERT INTO fts(rowid,title,heading,path,text,idents) VALUES(?,?,?,?,?,?)',
                          (n_chunks, head_title, h, words, t, lane_code.ident_column(t)))
        c.execute('INSERT INTO docfts(rowid,title,summary,path,headings) VALUES(?,?,?,?,?)',
                  (i, title or '', summary or '', words, ' | '.join(list(heads)[:40])))
    sy = lane_code.fill_profile(c, master, ids)
    nm = {}
    lm = _mod('lane_locate')
    if lm is not None and hasattr(lm, 'fill_profile'):
        try:
            nm = lm.fill_profile(c, master, names) or {}
        except Exception:
            nm = {}
    heads = []
    for i, pth, nme, dt, mt in c.execute('SELECT id,path,name,date,mtime FROM docs').fetchall():
        hd = c.execute('SELECT text FROM chunks WHERE doc=? ORDER BY ord LIMIT 3', (i,)).fetchall()
        heads.append((i, pth, nme, dt, mt, '\n'.join(x[0] for x in hd)[:4000]))
    ed = chunking.succession(heads)
    c.executemany('INSERT INTO newer VALUES(?,?,?)', [(a, b[0], b[1]) for a, b in ed.items()])
    c.executemany('INSERT INTO vecmap VALUES(?,?)', [(v, e) for e, v in emap.items()])
    mrev = master.execute("SELECT v FROM meta WHERE k='rev'").fetchone()
    for k, v in (('built', str(time.time())), ('profile', profile), ('roots', json.dumps(names)),
                 ('schema', schema.SCHEMA_VERSION), ('rev', mrev[0] if mrev else '0'), ('sig', _cfg_sig(cfg))):
        c.execute('INSERT INTO meta VALUES(?,?)', (k, v))
    c.execute("INSERT INTO fts(fts) VALUES('optimize')")
    c.execute("INSERT INTO dfts(dfts) VALUES('optimize')")
    c.execute("INSERT INTO docfts(docfts) VALUES('optimize')")
    return dict(docs=len(winners), chunks=n_chunks, distinct_inputs=len(emap), symbols=sy.get('symbols', 0),
                names=nm.get('names', 0), newer=len(ed), documents=n_documents)


def _fresh(cfg, profile):
    """True when the profile database already reflects the current master and config."""
    db = cfg.profile_db(profile)
    if not os.path.exists(db):
        return False
    try:
        c = schema.open_ro(db)
        try:
            m = dict(c.execute('SELECT k,v FROM meta').fetchall())
        finally:
            c.close()
        master = _master_reader(cfg)
        try:
            r = master.execute("SELECT v FROM meta WHERE k='rev'").fetchone()
        finally:
            master.close()
    except sqlite3.Error:
        return False
    names = [x.name for x in cfg.roots_for(profile)]
    return (m.get('rev') == (r[0] if r else '0') and m.get('sig') == _cfg_sig(cfg)
            and m.get('schema') == schema.SCHEMA_VERSION and json.loads(m.get('roots') or '[]') == names)


def update_all(cfg, force=False, log=print):
    """update(), then materialize every profile whose database is out of date."""
    u = update(cfg, force, log)
    out = {'update': u, 'profiles': {}}
    if u.get('skipped') == 'locked':
        return out
    for p in cfg.profiles:
        if not force and _fresh(cfg, p):
            out['profiles'][p] = {'profile': p, 'unchanged': True}
            continue
        out['profiles'][p] = materialize(cfg, p, log)
    return out
