"""Evidence protection and recovery: content-addressed evidence store, immutable flags, audit journal and
integrity incidents.

- Store (outside the vault; the folder named by `evidence_store` in system/vault-config.json; no store without it):
  objects/<sha[:2]>/<sha> hold the exact bytes of every registered version, read-only and immutable;
  manifest.jsonl records every object; checkpoints/ keep copies of the registry and the journal head, so
  sources stay recoverable when the working registry or journal is damaged.
- Flags: with `protect_raw` the macOS user-immutable flag is set on registered raw files. It stops accidental
  writes, renames and deletes; the same user can clear it, so it is not a security boundary.
- Journal (system/evidence-journal.jsonl): append-only, hash-chained evidence events; its head is copied into
  the store on every checkpoint, which is the independent anchor the chain is verified against.
- Incidents (.brain/integrity-incidents.json): integrity errors found by check stay open until resolved
  explicitly, and only once the error is gone.
Standard library only.
"""
import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import storage

JOURNAL = 'system/evidence-journal.jsonl'
INCIDENTS = '.brain/integrity-incidents.json'
INTEGRITY_PREFIXES = ('raw changed:', 'missing raw source:', 'unregistered raw source:', 'symbolic link inside raw/',
                      'duplicate source ID/path', 'former path reused', 'source outside raw:', 'evidence store:',
                      'evidence journal:')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def settings(root):
    cfg = {}
    p = Path(root) / 'system/vault-config.json'
    if p.is_file():
        cfg = json.loads(p.read_text(encoding='utf-8'))
    store = cfg.get('evidence_store')
    return {'store': Path(os.path.expanduser(store)) if store else None, 'protect_raw': bool(cfg.get('protect_raw', False))}


def object_path(store, sha):
    return Path(store) / 'objects' / sha[:2] / sha


def registry(root):
    return json.loads((Path(root) / 'system/source-registry.json').read_text(encoding='utf-8'))


# ---- immutable flags ---------------------------------------------------------------------------

def flags_supported():
    return hasattr(os, 'chflags') and hasattr(stat, 'UF_IMMUTABLE')


def is_immutable(path):
    return flags_supported() and bool(os.lstat(path).st_flags & stat.UF_IMMUTABLE)


def set_immutable(path, on):
    if not flags_supported():
        return
    current = os.lstat(path).st_flags
    wanted = current | stat.UF_IMMUTABLE if on else current & ~stat.UF_IMMUTABLE
    if wanted != current:
        os.chflags(str(path), wanted)


@contextlib.contextmanager
def writable(paths):
    """Clear the flag on the given files for the duration of an authorised operation; flags are set again on
    every original path that still exists, also when the operation fails."""
    cleared = [Path(p) for p in paths if Path(p).exists() and is_immutable(p)]
    for p in cleared:
        set_immutable(p, False)
    try:
        yield
    finally:
        for p in cleared:
            if p.exists():
                set_immutable(p, True)


def protect(root, mode='check'):
    root = Path(root)
    result = {'mode': mode, 'supported': flags_supported(), 'files': 0, 'protected': 0, 'changed': 0, 'missing': []}
    for r in registry(root)['files']:
        p = root / r['path']
        if not p.is_file() or p.is_symlink():
            result['missing'].append(r['path'])
            continue
        result['files'] += 1
        before = is_immutable(p)
        if mode == 'apply' and not before:
            set_immutable(p, True)
            result['changed'] += 1
        elif mode == 'release' and before:
            set_immutable(p, False)
            result['changed'] += 1
        result['protected'] += int(is_immutable(p))
    return result


# ---- audit journal ------------------------------------------------------------------------------

def _chain(prev, event):
    body = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256((prev + '\n' + body).encode('utf-8')).hexdigest()


def journal_lines(root):
    """Parsed journal entries; an unreadable line (for example an interrupted append) becomes {'unreadable': n}."""
    p = Path(root) / JOURNAL
    if not p.is_file():
        return []
    out = []
    for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), start=1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({'unreadable': n})
    return out


def journal_append(root, event):
    root = Path(root)
    with storage.vault_lock(root):
        lines = journal_lines(root)
        if lines and 'unreadable' in lines[-1]:
            raise IOError('evidence journal has an unreadable last line; repair it before appending')
        prev = lines[-1]['hash'] if lines else '0' * 64
        entry = dict(event, at=now())
        entry = dict(entry, prev=prev, hash=_chain(prev, entry))
        path = root / JOURNAL
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + '\n')
            f.flush()
            os.fsync(f.fileno())
        journal_checkpoint(root, len(lines) + 1, entry['hash'])
        return entry


HEADS = 'checkpoints/journal-heads.jsonl'


def journal_checkpoint(root, n, head):
    """Copy of the journal head into the store after every append, so no entry waits for the next backup to be
    anchored outside the vault. One append-only log per store (a locked line per event, not a file per event),
    each line tagged with its vault, so vaults sharing one store never compare each other's heads."""
    store = settings(root)['store']
    if not store_ready(root):
        return None
    import fcntl
    path = store / HEADS
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({'lines': n, 'hash': head, 'at': now(), 'vault': str(Path(root).resolve())}, sort_keys=True)
    with open(path, 'a', encoding='utf-8') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return n


def store_heads(store):
    """[(name, head or None if unreadable)] from the backup checkpoints and the per-append head log."""
    folder = store / 'checkpoints' if store else None
    if not folder or not folder.is_dir():
        return []
    heads = [(cp.name, _read_checkpoint(cp)) for cp in sorted(folder.glob('*-journal-head.json'))]
    log = store / HEADS
    if log.is_file():
        for n, text in enumerate(log.read_text(encoding='utf-8').splitlines(), 1):
            try:
                heads.append(('%s line %d' % (HEADS, n), json.loads(text)))
            except ValueError:
                heads.append(('%s line %d' % (HEADS, n), None))
    return heads


def journal_verify(root):
    """Errors in the chain itself, then divergence from the heads copied into the store (the independent anchor)."""
    errors, prev = [], '0' * 64
    lines = journal_lines(root)
    for i, entry in enumerate(lines):
        if 'unreadable' in entry:
            errors.append('evidence journal: unreadable line %d (interrupted append?)' % entry['unreadable'])
            break
        body = {k: v for k, v in entry.items() if k not in ('prev', 'hash')}
        if entry.get('prev') != prev or entry.get('hash') != _chain(prev, body):
            errors.append('evidence journal: chain broken at line %d' % (i + 1))
            break
        prev = entry['hash']
    mine, foreign = 0, 0
    for name, head in store_heads(settings(root)['store']):
        if not isinstance(head, dict):
            errors.append('evidence store: unreadable checkpoint ' + name)
            continue
        if head.get('vault') not in (None, str(Path(root).resolve())):
            foreign += 1                              # another vault's head in a shared store
            continue
        mine += 1
        n = head.get('lines', 0)
        if n and (n > len(lines) or lines[n - 1].get('hash') != head.get('hash')):
            errors.append('evidence journal: differs from the independent checkpoint ' + name)
    if lines and foreign and not mine:
        errors.append('evidence journal: no checkpoint in the store belongs to this vault (was the vault moved? run '
                      'python3 tools/brain.py backup to anchor it again)')
    return errors


# ---- evidence store -----------------------------------------------------------------------------

def _copy_into(src, dest):
    """Clone on APFS (no extra space, independent of later in-place changes), otherwise copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + '.partial')
    if tmp.exists():
        set_immutable(tmp, False)
        tmp.unlink()
    done = subprocess.run(['cp', '-c', str(src), str(tmp)], capture_output=True)
    if done.returncode != 0:
        shutil.copyfile(str(src), str(tmp))
    set_immutable(tmp, False)          # a clone carries the source's flags
    os.chmod(str(tmp), 0o644)
    return tmp


def put_object(store, src, sha):
    obj = object_path(store, sha)
    if obj.exists():
        return False
    tmp = _copy_into(src, obj)
    if sha256_file(tmp) != sha:
        tmp.unlink()
        raise IOError('evidence store: copy of %s does not match its SHA-256' % src)
    os.chmod(str(tmp), 0o444)
    os.replace(str(tmp), str(obj))
    set_immutable(obj, True)
    with open(Path(store) / 'manifest.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps({'event': 'put', 'sha256': sha, 'bytes': obj.stat().st_size, 'at': now()}) + '\n')
    return True


def checkpoint(root):
    root, store = Path(root), settings(Path(root))['store']
    if not store:
        raise ValueError('no evidence store configured')
    stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    folder = store / 'checkpoints'
    storage.atomic_write_bytes(folder / (stamp + '-registry.json'), (root / 'system/source-registry.json').read_bytes())
    lines = journal_lines(root)
    storage.atomic_write_json(folder / (stamp + '-journal-head.json'),
                              {'lines': len(lines), 'hash': lines[-1]['hash'] if lines else None, 'at': now(),
                               'vault': str(root.resolve())})
    return stamp


def backup(root):
    root = Path(root)
    store = settings(root)['store']
    if not store:
        return {'status': 'error', 'message': 'no evidence store configured: set "evidence_store" in system/vault-config.json'}
    added = present = size = 0
    with storage.vault_lock(root):
        for r in registry(root)['files']:
            src = root / r['path']
            if object_path(store, r['sha256']).exists():
                present += 1
            elif src.is_file() and sha256_file(src) == r['sha256']:
                put_object(store, src, r['sha256'])
                added += 1
                size += r['bytes']
        stamp = checkpoint(root)
    return {'store': str(store), 'objects_added': added, 'objects_present': present, 'bytes_added': size, 'checkpoint': stamp}


def verify_store(root, deep=False):
    root = Path(root)
    store = settings(root)['store']
    if not store:
        return {'status': 'error', 'message': 'no evidence store configured', 'errors': ['evidence store: not configured']}
    missing, corrupt, checked = [], [], 0
    for r in registry(root)['files']:
        obj = object_path(store, r['sha256'])
        checked += 1
        if not obj.is_file():
            missing.append(r['path'])
        elif obj.stat().st_size != r['bytes'] or (deep and sha256_file(obj) != r['sha256']):
            corrupt.append(r['path'])
    return {'store': str(store), 'checked': checked, 'missing': missing, 'corrupt': corrupt,
            'errors': ['evidence store: missing object for ' + p for p in missing] + ['evidence store: corrupt object for ' + p for p in corrupt]}


def _read_checkpoint(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def latest_checkpoint_registry(root):
    """The newest readable registry checkpoint (an unreadable one is skipped; check_findings reports it)."""
    store = settings(root)['store']
    folder = store / 'checkpoints' if store else None
    found = sorted(folder.glob('*-registry.json')) if folder and folder.is_dir() else []
    for cp in reversed(found):
        data = _read_checkpoint(cp)
        if isinstance(data, dict) and isinstance(data.get('files'), list):
            return data
    return None


def find_records(reg, query):
    q = query.strip()
    return [r for r in reg['files'] if q in (r['id'], r['path'], r.get('original_path'), r['sha256'])
            or q in r.get('former_paths', [])]


def restore(root, query, revision=None, sha=None, dry_run=False, replace=False):
    """Restore one source from the store. Never overwrites a changed working file unless replace is set, and then
    keeps the changed bytes in .brain/quarantine/ first. Falls back to the latest registry checkpoint when the
    working registry is unreadable or does not know the source."""
    root = Path(root)
    store, protect_raw = settings(root)['store'], settings(root)['protect_raw']
    if not store:
        return {'status': 'error', 'message': 'no evidence store configured'}
    try:
        records, origin = find_records(registry(root), query), 'working registry'
    except (OSError, ValueError):
        records, origin = [], 'working registry unreadable'
    if not records:
        reg = latest_checkpoint_registry(root)
        records, origin = (find_records(reg, query) if reg else []), 'store checkpoint'
    if len(records) != 1:
        return {'status': 'error', 'message': '%d sources match %r (%s)' % (len(records), query, origin)}
    rec = records[0]
    target = sha or rec['sha256']
    if revision is not None:
        revs = rec.get('revisions', [])
        if not 0 <= revision < len(revs):
            return {'status': 'error', 'message': 'no revision %d for %s' % (revision, rec['path'])}
        target = revs[revision]['previous_sha256']
    obj = object_path(store, target)
    if not obj.is_file() and revision is None and not sha:
        saved = latest_checkpoint_registry(root)
        alt = [r for r in (saved or {'files': []})['files'] if r['id'] == rec['id']]
        if alt and object_path(store, alt[0]['sha256']).is_file():
            rec, target, origin = alt[0], alt[0]['sha256'], 'store checkpoint (working record does not match any stored object)'
            obj = object_path(store, target)
    if not obj.is_file():
        return {'status': 'error', 'message': 'evidence store has no object ' + target}
    if sha256_file(obj) != target:
        return {'status': 'error', 'message': 'evidence store object is corrupt: ' + target}
    dest = root / rec['path']
    state = 'missing' if not dest.exists() else ('identical' if sha256_file(dest) == target else 'different')
    plan = {'status': 'planned' if dry_run else 'restored', 'source': rec['id'], 'path': rec['path'], 'sha256': target,
            'working_file': state, 'registry': origin}
    if state == 'identical':
        return dict(plan, status='unchanged')
    if state == 'different' and not replace:
        return dict(plan, status='conflict', message='the working file differs; rerun with --replace (the changed bytes are kept in .brain/quarantine/)')
    if dry_run:
        return plan
    with storage.vault_lock(root), writable([dest]):
        if state == 'different':
            keep = root / '.brain/quarantine' / datetime.datetime.now().strftime('%Y%m%dT%H%M%S') / rec['path']
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(dest), str(keep))
            plan['quarantine'] = str(keep.relative_to(root))
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = _copy_into(obj, dest.with_name(dest.name + '.restore'))
        os.chmod(str(tmp), 0o644)
        os.replace(str(tmp), str(dest))
    if protect_raw:
        set_immutable(dest, True)          # before the check, so that a raw file is never left writable
    if sha256_file(dest) != target:
        return dict(plan, status='error', message='restored file does not match ' + target)
    journal_append(root, {'event': 'restore', 'source': rec['id'], 'path': rec['path'], 'sha256': target,
                          'replaced': state == 'different'})
    return plan


# ---- integration with register, move and check ------------------------------------------------------

def store_ready(root):
    store = settings(root)['store']
    return bool(store) and (store / 'objects').is_dir()


def after_register(root, path, record):
    """A newly registered source: copy it into the store (once the store exists), protect it, journal it."""
    s = settings(root)
    if store_ready(root):
        put_object(s['store'], path, record['sha256'])
    if s['protect_raw']:
        set_immutable(path, True)
    journal_append(root, {'event': 'register', 'source': record['id'], 'path': record['path'], 'sha256': record['sha256'],
                          'bytes': record['bytes']})


@contextlib.contextmanager
def guarded_change(root):
    """Around an authorised move: keep the previous bytes of rewritable sources in the store, lift the flags,
    and afterwards (also after a failure) journal every move and revision and protect raw files again."""
    root, s = Path(root), settings(Path(root))
    before = {r['id']: (r['path'], r['sha256']) for r in registry(root)['files']}
    ready = store_ready(root)
    if ready:
        for r in registry(root)['files']:
            src = root / r['path']
            if src.suffix.lower() in ('.md', '.canvas') and src.is_file() and not object_path(s['store'], r['sha256']).exists():
                put_object(s['store'], src, r['sha256'])
    cleared = [p for p in (root / 'raw').rglob('*') if p.is_file() and not p.is_symlink() and is_immutable(p)]
    for p in cleared:
        set_immutable(p, False)
    try:
        yield
    finally:
        after = registry(root)['files']
        for r in after:
            old, p = before.get(r['id']), root / r['path']
            if old and old[0] != r['path']:
                journal_append(root, {'event': 'move', 'source': r['id'], 'from': old[0], 'to': r['path']})
            if old and old[1] != r['sha256']:
                if ready and p.is_file():
                    put_object(s['store'], p, r['sha256'])
                journal_append(root, {'event': 'revision', 'source': r['id'], 'previous_sha256': old[1], 'sha256': r['sha256']})
        if s['protect_raw'] or cleared:
            for r in after:
                p = root / r['path']
                if p.is_file() and not p.is_symlink():
                    set_immutable(p, True)


def check_findings(root):
    """(errors, warnings) about the store, the journal, flags and interrupted writes, for brain.py check."""
    root, s = Path(root), settings(Path(root))
    errors, warnings = list(journal_verify(root)), []
    if store_ready(root):
        errors += verify_store(root)['errors']
        folder = s['store'] / 'checkpoints'
        for cp in sorted(folder.glob('*-registry.json')) if folder.is_dir() else []:
            if not isinstance((_read_checkpoint(cp) or {}).get('files'), list):
                errors.append('evidence store: unreadable checkpoint ' + cp.name)
    elif s['store']:
        warnings.append('evidence store not initialised at %s: run python3 tools/brain.py backup' % s['store'])
    else:
        warnings.append('no evidence store configured: set "evidence_store" in system/vault-config.json, then run python3 tools/brain.py backup')
    if s['protect_raw'] and flags_supported():
        state = protect(root, 'check')
        if state['protected'] < state['files']:            # protection promised by the configuration is missing
            errors.append('%d registered raw files are not immutable although protect_raw is on: run python3 '
                          'tools/brain.py protect apply' % (state['files'] - state['protected']))
    warnings += ['stray temporary file from an interrupted write: ' + str(p.relative_to(root)) for p in storage.stray_temporaries(root)]
    return errors, warnings


# ---- integrity incidents ------------------------------------------------------------------------

UNREADABLE_LOG = 'I-incident-log'


def incidents(root):
    """All recorded incidents. An unreadable incident log counts as one open incident, never as none."""
    p = Path(root) / INCIDENTS
    if not p.is_file():
        return []
    try:
        items = json.loads(p.read_text(encoding='utf-8'))
        if isinstance(items, list) and all(isinstance(i, dict) and 'id' in i and 'status' in i for i in items):
            return items
    except (ValueError, OSError):
        pass
    return [{'id': UNREADABLE_LOG, 'error': 'the integrity incident log %s is unreadable: earlier incidents are unknown; check the '
             'evidence, then resolve this incident' % INCIDENTS, 'first_seen': now(), 'last_seen': now(), 'status': 'open', 'damaged': True}]


def _keep_damaged_log(root, current):
    """Before an unreadable log is rewritten, it is moved aside for inspection, never overwritten."""
    for item in current:
        if item.pop('damaged', False):
            p = Path(root) / INCIDENTS
            keep = p.with_name('integrity-incidents.damaged-%s.json' % now().replace(':', '').replace('-', ''))
            os.replace(str(p), str(keep))
            item['error'] += ' (the damaged log was kept as .brain/%s)' % keep.name


def open_incidents(root):
    return [i for i in incidents(root) if i.get('status') == 'open']


def record_incidents(root, errors):
    found = [e for e in errors if e.startswith(INTEGRITY_PREFIXES)]
    current = incidents(root)
    if not found and not current:
        return []
    _keep_damaged_log(root, current)
    by_error = {i['error']: i for i in current if i.get('status') == 'open'}
    for e in found:
        if e in by_error:
            by_error[e]['last_seen'] = now()
        else:
            item = {'id': 'I-' + hashlib.sha256(e.encode()).hexdigest()[:10], 'error': e, 'first_seen': now(),
                    'last_seen': now(), 'status': 'open'}
            current.append(item)
            by_error[e] = item
    storage.atomic_write_json(Path(root) / INCIDENTS, current)
    return [i for i in current if i['status'] == 'open']


def resolve_incident(root, incident_id, reason, current_errors):
    current = incidents(root)
    _keep_damaged_log(root, current)
    for item in current:
        if item['id'] == incident_id and item['status'] == 'open':
            if item['error'] in current_errors:
                return {'status': 'refused', 'message': 'the error is still present: ' + item['error']}
            item.update(status='resolved', resolved_at=now(), reason=reason)
            storage.atomic_write_json(Path(root) / INCIDENTS, current)
            journal_append(root, {'event': 'incident-resolved', 'incident': incident_id, 'reason': reason})
            return {'status': 'resolved', 'incident': incident_id}
    return {'status': 'error', 'message': 'no open incident ' + incident_id}


# ---- command line (brain.py backup | restore | protect | journal | incident) -----------------------

def cli(root, args, check):
    """brain.py backup, restore, protect, journal and incident; check(root, write_report=False) gives the current errors."""
    if args.command == 'backup':
        return verify_store(root, deep=args.deep) if args.verify else backup(root)
    if args.command == 'restore':
        return restore(root, args.query, args.revision, args.sha, args.dry_run, args.replace)
    if args.command == 'protect':
        return protect(root, args.mode)
    if args.command == 'journal':
        if args.action == 'checkpoint':
            return {'status': 'ok', 'checkpoint': checkpoint(root)}
        return {'errors': journal_verify(root), 'entries': len(journal_lines(root))}
    if args.action == 'list':
        return {'open_incidents': open_incidents(root)}
    if not args.id or not args.reason:
        return {'status': 'error', 'message': 'incident resolve needs an ID and --reason'}
    return resolve_incident(root, args.id, args.reason, check(root, write_report=False)['errors'])
