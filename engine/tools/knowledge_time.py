"""Bitemporal view of the structured claims (Phase 3).

Two kinds of time are kept apart:
- valid time, when a statement held in the project: valid_from (default: as_of, the evidence date) until
  valid_to (explicit; else the valid_from of the claim that supersedes it; else, for an archived claim,
  archived_on as an upper bound; else open);
- transaction time, when the vault recorded the statement: the commit times of system/claims.json in git.
  The history starts with the first commit that contains the file; what the
  vault knew earlier is reported as unknown, never guessed. Uncommitted edits count as recorded now.
File modification times, document creation times and compiled_on dates are not used for either.

    python3 tools/knowledge_time.py known <date-or-time> [--id CLAIM]   # what the vault had recorded then
    python3 tools/knowledge_time.py valid <date> [--known <time>]      # statements valid at a date
    python3 tools/knowledge_time.py latest-build <app bundle ID> <date> [--min-level installed] [--known <time>]
    python3 tools/knowledge_time.py value <attribute> <subject> <date> [--known <time>]
    python3 tools/knowledge_time.py superseded <claim-id>
    python3 tools/knowledge_time.py evidence <claim-id> [--known <time>]
A bare date as a transaction time means the end of that day in local time.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

TOOLS = Path(__file__).resolve().parent
CLAIMS = 'system/claims.json'
CACHE = '.brain/claims-history.json'
CACHE_VERSION = 1
EVIDENCE_LEVELS = ('proposed', 'implemented', 'mac-tested', 'simulator-tested', 'built', 'installed', 'launched', 'device-accepted')


def _git(root, *args):
    """git output, or '' when git is missing or fails (no history then: the working version only)."""
    try:
        return subprocess.run(['git', '-C', str(root)] + list(args), capture_output=True, text=True).stdout
    except OSError:
        return ''


def parse_time(value, end_of_day=True):
    """A timezone-aware datetime from an ISO date or time; a bare date is the end (or start) of that local day."""
    if isinstance(value, datetime.datetime):
        moment = value
    elif len(value) == 10:
        day = datetime.date.fromisoformat(value)
        moment = datetime.datetime.combine(day, datetime.time(23, 59, 59) if end_of_day else datetime.time(0, 0))
    else:
        moment = datetime.datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.astimezone()


def facts_of(claim):
    """The claim's facts that are objects (malformed ones are check findings, never a crash here)."""
    facts = claim.get('facts') if isinstance(claim, dict) else None
    return [f for f in facts if isinstance(f, dict)] if isinstance(facts, list) else []


def build_view(claims):
    """Per claim with build facts: its ID, a fingerprint of its anchors and the build facts (all rule E5 needs)."""
    out = []
    for c in claims:
        builds = [f for f in facts_of(c) if f.get('attribute') == 'build']
        if builds and isinstance(c, dict):
            key = hashlib.sha256(json.dumps(c.get('anchors') or [], sort_keys=True).encode('utf-8')).hexdigest()
            out.append({'id': c.get('id'), 'anchors_key': key, 'facts': builds})
    return out


VIEWS = {'full': (CACHE, lambda claims: claims), 'builds': (CACHE.replace('.json', '-builds.json'), build_view)}


def history(root, view='full'):
    """[(commit time, commit, claims)] oldest first, plus the uncommitted working version if it differs.
    view 'builds' keeps only what rule E5 needs, so a check never holds every full version in memory (the full
    history grows with every commit). Cached in .brain/claims-history*.json, keyed by HEAD and the working
    file's hash; each version is projected as soon as it is read."""
    root = Path(root)
    cache_name, project = VIEWS[view]
    head = _git(root, 'rev-parse', 'HEAD').strip()
    working = (root / CLAIMS).read_bytes() if (root / CLAIMS).is_file() else b''
    key = [CACHE_VERSION, head, hashlib.sha256(working).hexdigest()]
    try:
        cached = json.loads((root / cache_name).read_text(encoding='utf-8'))
        if cached.get('key') == key:
            return [(parse_time(t), c, claims) for t, c, claims in cached['versions']]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    versions = []
    for line in reversed(_git(root, 'log', '--format=%H %cI', '--', CLAIMS).splitlines()):
        commit, when = line.split(' ', 1)
        try:
            data = json.loads(_git(root, 'show', '%s:%s' % (commit, CLAIMS)))
        except ValueError:
            continue
        versions.append((when, commit, project(data.get('claims', []))))
    committed = _git(root, 'show', 'HEAD:' + CLAIMS) if head else ''
    if working and working.decode('utf-8') != committed:
        versions.append((datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'uncommitted',
                         project(json.loads(working.decode('utf-8')).get('claims', []))))
    try:
        import storage
        storage.atomic_write_json(root / cache_name, {'key': key, 'versions': versions})
    except OSError:
        pass
    return [(parse_time(t), c, claims) for t, c, claims in versions]


def known(root, at):
    """(claims recorded at time `at`, the version's commit) or (None, reason) before the recorded history."""
    moment = parse_time(at)
    versions = history(root)
    if not versions:
        return None, 'no recorded history of %s' % CLAIMS
    if moment < versions[0][0]:
        return None, 'before the recorded history (starts %s); what the vault knew then is unknown' % versions[0][0].isoformat()
    when, commit, claims = [v for v in versions if v[0] <= moment][-1]
    return claims, '%s (%s)' % (commit, when.isoformat())


def interval_detail(claim, claims_by_id):
    """(valid_from, valid_to or None, how the end is known: 'explicit' | 'superseded' | 'archived' | None).
    An archived_on date is an upper bound: the statement held at most until the vault archived it."""
    date = lambda value: value if isinstance(value, str) and value else None      # malformed dates are check findings
    start = date(claim.get('valid_from')) or date(claim.get('as_of'))
    if date(claim.get('valid_to')):
        return start, claim['valid_to'], 'explicit'
    if isinstance(claim.get('superseded_by'), str) and claim['superseded_by'] in claims_by_id:
        newer = claims_by_id[claim['superseded_by']]
        return start, date(newer.get('valid_from')) or date(newer.get('as_of')), 'superseded'
    if claim.get('lifecycle') == 'archived' and date(claim.get('archived_on')):
        return start, claim['archived_on'], 'archived'
    return start, None, None


def interval(claim, claims_by_id):
    """(valid_from, valid_to or None) as ISO dates, by the rules in the module docstring."""
    return interval_detail(claim, claims_by_id)[:2]


def valid(claims, at):
    """Claims whose valid interval contains the date `at` (start inclusive, end exclusive; open end allowed). Dates
    have day resolution, so a claim superseded on the day it starts counts as valid on that day."""
    by_id = {c['id']: c for c in claims}
    out = []
    for c in claims:
        start, end = interval(c, by_id)
        if start and start <= at and (not end or at < end or start == end == at):
            out.append(c)
    return out


def level_rank(level):
    return EVIDENCE_LEVELS.index(level) if level in EVIDENCE_LEVELS else -1


def _confidence(claim, fact):
    return fact.get('confidence') or claim.get('confidence')


def latest_build(claims, app, at, min_level=None, confidences=('verified', 'reported')):
    """The build of `app` with the latest evidence date on or before `at` that reached min_level by then, from facts
    of the given confidences. Its level is the highest any claim established by `at`; builds with the same latest
    date are all returned (ambiguous). Build facts are events: a superseded claim still records its build."""
    builds = {}
    for c in claims:
        start = c.get('valid_from') or c.get('as_of')
        if not start or start > at:
            continue
        for f in facts_of(c):
            if f.get('attribute') != 'build' or f.get('subject') != app or _confidence(c, f) not in confidences:
                continue
            b = builds.setdefault(f['value'], {'build': f['value'], 'first_evidence': start, 'level': None, 'claims': []})
            b['first_evidence'] = min(b['first_evidence'], start)
            if level_rank(f.get('level')) > level_rank(b['level']):
                b['level'] = f.get('level')
            b['claims'].append(c['id'])
    ok = [b for b in builds.values() if not min_level or level_rank(b['level']) >= level_rank(min_level)]
    if not ok:
        return None
    newest = max(b['first_evidence'] for b in ok)
    best = sorted((b for b in ok if b['first_evidence'] == newest), key=lambda b: b['build'])
    return best[0] if len(best) == 1 else {'ambiguous': best}


def value_at(claims, attribute, subject, at, device=None):
    """The value of a one-at-a-time attribute at date `at`: the fact with the latest valid_from on or before `at`
    whose claim's validity had not ended (explicitly or by supersession). An archived claim's value is returned
    with its archived_on bound when `at` is later; equal latest starts with different values are ambiguous."""
    by_id = {c['id']: c for c in claims}
    rows = []
    for c in claims:
        start, end, kind = interval_detail(c, by_id)
        for f in facts_of(c):
            if f.get('attribute') == attribute and f.get('subject') == subject and f.get('device') == device \
                    and start and start <= at:
                rows.append({'value': f['value'], 'claim': c['id'], 'valid_from': start, 'valid_to': end, 'end': kind,
                             'confidence': _confidence(c, f)})
    if not rows:
        return None
    newest = max(r['valid_from'] for r in rows)
    current = [r for r in rows if r['valid_from'] == newest]
    if len({r['value'] for r in current}) > 1:
        return {'ambiguous': current}
    row = dict(current[0])
    if row['end'] in ('explicit', 'superseded') and row['valid_to'] <= at:
        return {'value': None, 'reason': 'claim %s stopped being valid on %s (%s)' % (row['claim'], row['valid_to'], row['end'])}
    if row['end'] == 'archived' and row['valid_to'] <= at:
        row['note'] = 'archived on %s; valid at most until then' % row['valid_to']
    return row


def superseded(root, claim_id):
    """When a claim stopped being valid (valid time) and when the vault recorded the supersession (transaction time)."""
    versions = history(root)
    if not versions:
        return {'claim': claim_id, 'status': 'no recorded history'}
    current = {c['id']: c for c in versions[-1][2]}
    claim = current.get(claim_id)
    if claim is None:
        return {'claim': claim_id, 'status': 'unknown claim'}
    start, end = interval(claim, current)
    recorded = next((when.isoformat() for when, _, claims in versions
                     if any(c['id'] == claim_id and (c.get('superseded_by') or c.get('lifecycle') in ('superseded', 'archived'))
                            for c in claims)), None)
    first = versions[0][0].isoformat()
    return {'claim': claim_id, 'lifecycle': claim.get('lifecycle'), 'superseded_by': claim.get('superseded_by'),
            'valid_from': start, 'valid_until': end,
            'recorded_as_superseded': recorded if recorded != first else 'on or before %s (start of the recorded history)' % first}


def evidence(root, claim_id, at=None):
    """The claim's sources and anchors as recorded at time `at` (default: now)."""
    claims, version = known(root, at) if at else (history(root)[-1][2], 'latest')
    if claims is None:
        return {'claim': claim_id, 'status': version}
    claim = next((c for c in claims if c['id'] == claim_id), None)
    if claim is None:
        return {'claim': claim_id, 'status': 'not recorded at %s' % version}
    return {'claim': claim_id, 'version': version, 'text': claim['text'], 'confidence': claim['confidence'],
            'sources': claim.get('sources', []), 'anchors': claim.get('anchors', [])}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='command', required=True)
    k = sub.add_parser('known')
    k.add_argument('at')
    k.add_argument('--id')
    v = sub.add_parser('valid')
    v.add_argument('date')
    v.add_argument('--known')
    l = sub.add_parser('latest-build')
    l.add_argument('app')
    l.add_argument('date')
    l.add_argument('--min-level', choices=EVIDENCE_LEVELS)
    l.add_argument('--known')
    w = sub.add_parser('value')
    w.add_argument('attribute')
    w.add_argument('subject')
    w.add_argument('date')
    w.add_argument('--device')
    w.add_argument('--known')
    s = sub.add_parser('superseded')
    s.add_argument('claim')
    e = sub.add_parser('evidence')
    e.add_argument('claim')
    e.add_argument('--known')
    args = ap.parse_args()
    root = TOOLS.parent
    sys.path.insert(0, str(TOOLS))
    if args.command == 'known':
        claims, version = known(root, args.at)
        out = {'version': version, 'claims': len(claims) if claims is not None else None}
        if args.id and claims is not None:
            out['claim'] = next((c for c in claims if c['id'] == args.id), 'not recorded')
    elif args.command in ('valid', 'latest-build', 'value'):
        claims, version = known(root, args.known) if args.known else (history(root)[-1][2], 'latest')
        if claims is None:
            out = {'status': version}
        elif args.command == 'valid':
            out = {'known_at': version, 'valid': [c['id'] for c in valid(claims, args.date)]}
        elif args.command == 'latest-build':
            out = {'known_at': version, 'latest_build': latest_build(claims, args.app, args.date, args.min_level)}
        else:
            out = {'known_at': version, 'value': value_at(claims, args.attribute, args.subject, args.date, args.device)}
    elif args.command == 'superseded':
        out = superseded(root, args.claim)
    else:
        out = evidence(root, args.claim, args.known)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
