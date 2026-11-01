"""Evidence anchors for structured claims (Phase 3): a claim points at an exact excerpt of a registered raw source.

An anchor in system/claims.json (optional list "anchors" on a claim):
    {"source": "S-…" (registry ID), "path": "raw/…" (informational; the ID decides), "lines": [first, last]
     (1-based, inclusive), "sha256": SHA-256 of the normalised excerpt, "heading": nearest Markdown heading
     above the excerpt (informational), "anchored_on": "YYYY-MM-DD", "method": "reviewed" | "manual",
     "support": "full" (the excerpt states the whole claim) | "partial" (it states part of it)}
The excerpt is normalised by stripping trailing whitespace of each line and blank lines at both ends.

Verification is deterministic. States:
    verified             the lines still hash to the stored value
    relocated            the stored lines differ, but exactly one window of the same length elsewhere in the same
                         source hashes to the stored value (reported with the new lines, never rewritten silently)
    ambiguous            the stored lines differ and several windows match: an explicit decision is needed
    changed              the excerpt no longer occurs in the source
    source-missing       the registered file is not on disk (restore it from the evidence store)
    source-modified      the file's SHA-256 differs from the registry (raw evidence changed: an integrity problem)
    source-unregistered  the anchor's source ID is not in the registry
    invalid-hash         the stored hash is not 64 hexadecimal characters
    invalid-locator      the line range is malformed
    unsupported-format   the source is not a UTF-8 text file
No fuzzy matching reconnects an anchor. Moves through `brain.py move` keep the registry ID, so anchors follow.
A verified or relocated anchor whose source has a recorded revision dated after the anchor ("superseded
evidence") is reported for review: the excerpt still occurs, but the source it was taken from has been replaced.

    python3 tools/anchors.py verify [--all]            # states of every anchor (active claims, or all)
    python3 tools/anchors.py propose <claim-id>       # candidate excerpts for review (key-token coverage)
    python3 tools/anchors.py accept <claim-id> <source-id> <first> <last>
    python3 tools/anchors.py relocate <claim-id>      # apply exact unique relocations of that claim
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

TOOLS = Path(__file__).resolve().parent
TEXT_SUFFIXES = {'.md', '.txt', '.json', '.py', '.cs', '.swift', '.yaml', '.yml', '.csv', '.log', '.html', '.xml', '.toml'}
HEX64 = re.compile(r'^[0-9a-f]{64}$')
BAD_STATES = {'changed', 'source-missing', 'source-modified', 'source-unregistered', 'invalid-hash', 'invalid-locator'}
NOTICE_STATES = {'relocated', 'ambiguous', 'unsupported-format'}
INACTIVE = {'archived', 'superseded'}
MAX_PROPOSAL_LINES = 4


def normalise(lines):
    return '\n'.join(line.rstrip() for line in lines).strip('\n')


def excerpt_hash(lines):
    return hashlib.sha256(normalise(lines).encode('utf-8')).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def registry_by_id(root):
    data = json.loads((Path(root) / 'system/source-registry.json').read_text(encoding='utf-8'))
    return {f['id']: f for f in data.get('files', [])}


class Sources:
    """Reads each registered source once per run: its lines, or why it cannot be read."""

    def __init__(self, root, registry):
        self.root, self.registry, self.cache = Path(root), registry, {}

    def get(self, source_id):
        if source_id not in self.cache:
            self.cache[source_id] = self._load(source_id)
        return self.cache[source_id]

    def _load(self, source_id):
        entry = self.registry.get(source_id)
        if entry is None:
            return 'source-unregistered', None
        path = self.root / entry['path']
        if not path.is_file():
            return 'source-missing', None
        if file_sha256(path) != entry.get('sha256'):
            return 'source-modified', None
        if path.suffix.lower() not in TEXT_SUFFIXES:
            return 'unsupported-format', None
        try:
            return None, path.read_text(encoding='utf-8').splitlines()
        except UnicodeDecodeError:
            return 'unsupported-format', None


def verify(anchor, sources):
    """{'state': …, 'lines': [first, last] when verified or relocated, 'candidates': n when ambiguous}."""
    if not isinstance(anchor.get('sha256'), str) or not HEX64.match(anchor['sha256']):
        return {'state': 'invalid-hash'}
    span = anchor.get('lines')
    if not (isinstance(span, list) and len(span) == 2 and all(isinstance(x, int) for x in span) and 1 <= span[0] <= span[1]):
        return {'state': 'invalid-locator'}
    problem, lines = sources.get(anchor.get('source'))
    if problem:
        return {'state': problem}
    first, last = span
    revised = max((r.get('on', '') for r in sources.registry.get(anchor.get('source'), {}).get('revisions') or []
                   if r.get('on', '') > (anchor.get('anchored_on') or '')), default=None)
    extra = {'revised_after_anchoring': revised} if revised else {}
    if last <= len(lines) and excerpt_hash(lines[first - 1:last]) == anchor['sha256']:
        return dict({'state': 'verified', 'lines': span}, **extra)
    width = last - first + 1
    hits = [i + 1 for i in range(0, max(0, len(lines) - width + 1)) if excerpt_hash(lines[i:i + width]) == anchor['sha256']]
    if len(hits) == 1:
        return dict({'state': 'relocated', 'lines': [hits[0], hits[0] + width - 1]}, **extra)
    if hits:
        return {'state': 'ambiguous', 'candidates': len(hits)}
    return {'state': 'changed'}


def verify_claims(root, claims, include_inactive=True):
    """{claim id: [result per anchor]} and counts, reading every source once."""
    sources = Sources(root, registry_by_id(root))
    results, counts = {}, {}
    for c in claims:
        if not include_inactive and c.get('lifecycle') in INACTIVE:
            continue
        rows = []
        for i, a in enumerate(c.get('anchors') or []):
            r = dict(verify(a, sources), index=i, source=a.get('source'))
            rows.append(r)
            counts[r['state']] = counts.get(r['state'], 0) + 1
        if rows:
            results[c['id']] = rows
    return results, counts


def schema_errors(claim):
    """Structural problems of a claim's anchors (types and required fields)."""
    errors = []
    anchors = claim.get('anchors')
    if anchors is None:
        return errors
    if not isinstance(anchors, list):
        return [claim['id'] + ': anchors must be a list']
    for i, a in enumerate(anchors):
        if not isinstance(a, dict):
            errors.append('%s: anchor %d is not an object' % (claim['id'], i))
            continue
        for field in ('source', 'lines', 'sha256', 'anchored_on', 'method'):
            if field not in a:
                errors.append('%s: anchor %d lacks %s' % (claim['id'], i, field))
        if a.get('method') not in (None, 'reviewed', 'manual'):
            errors.append('%s: anchor %d has unknown method %r' % (claim['id'], i, a.get('method')))
        if a.get('support', 'partial') not in ('full', 'partial'):
            errors.append('%s: anchor %d has unknown support %r' % (claim['id'], i, a.get('support')))
    return errors


def check_findings(root, claims):
    """(errors, warnings, summary) for brain.check: a broken anchor of an active claim is an error, of an archived
    or superseded claim a warning; relocated, ambiguous and unsupported anchors are warnings."""
    errors, warnings = [], []
    for c in claims:
        errors += schema_errors(c)
    results, counts = verify_claims(root, [c for c in claims if isinstance(c.get('anchors', []), list)])
    lifecycle = {c['id']: c.get('lifecycle') for c in claims}
    for cid, rows in results.items():
        for r in rows:
            text = '%s: anchor %d (%s) %s' % (cid, r['index'], r.get('source'), r['state'])
            if r['state'] == 'relocated':
                text += ' to lines %d-%d; run python3 tools/anchors.py relocate %s' % (r['lines'][0], r['lines'][1], cid)
            if r['state'] in BAD_STATES and lifecycle.get(cid) not in INACTIVE:
                errors.append(text)
            elif r['state'] in BAD_STATES or r['state'] in NOTICE_STATES:
                warnings.append(text)
            if r.get('revised_after_anchoring'):
                warnings.append('%s: anchor %d (%s) source revised on %s after anchoring (superseded evidence); review and re-anchor'
                                % (cid, r['index'], r.get('source'), r['revised_after_anchoring']))
    active = [c for c in claims if c.get('lifecycle') not in INACTIVE]
    anchored = [c for c in active if any(r['state'] in ('verified', 'relocated') for r in results.get(c['id'], []))]
    summary = {'active_claims': len(active), 'active_claims_with_verified_anchor': len(anchored), 'anchor_states': counts}
    return errors, warnings, summary


def heading_above(lines, first):
    for i in range(first - 1, -1, -1):
        if lines[i].lstrip().startswith('#'):
            return lines[i].strip()[:120]
    return None


def make_anchor(root, registry, source_id, first, last, method='reviewed', today=None, support='partial'):
    """A new anchor from the current file; refuses unregistered, modified or unreadable sources and bad ranges."""
    problem, lines = Sources(root, registry).get(source_id)
    if problem:
        raise ValueError('cannot anchor to %s: %s' % (source_id, problem))
    if not (1 <= first <= last <= len(lines)):
        raise ValueError('line range %d-%d outside 1-%d' % (first, last, len(lines)))
    if not normalise(lines[first - 1:last]):
        raise ValueError('the excerpt is empty')
    return {'source': source_id, 'path': registry[source_id]['path'], 'lines': [first, last],
            'sha256': excerpt_hash(lines[first - 1:last]), 'heading': heading_above(lines, first),
            'anchored_on': today or datetime.date.today().isoformat(), 'method': method, 'support': support}


def key_tokens(brain, text):
    """Distinct content tokens of a claim; numbers and identifiers with digits count three times."""
    words = [w for w in brain.tokens(text) if w not in brain.STOP and len(w) > 2]
    return {w: (3 if any(ch.isdigit() for ch in w) else 1) for w in words}


def propose(root, brain, claim, limit=3):
    """Best excerpts (up to MAX_PROPOSAL_LINES consecutive non-empty lines) of each registered source of the claim,
    by weighted coverage of the claim's key tokens; for review, never accepted automatically."""
    registry = registry_by_id(root)
    by_path = {e['path']: e for e in registry.values()}
    sources = Sources(root, registry)
    keys = key_tokens(brain, claim['text'])
    total = sum(keys.values()) or 1
    out = []
    for path in claim.get('sources', []):
        entry = by_path.get(path)
        if not entry:
            continue
        problem, lines = sources.get(entry['id'])
        if problem:
            out.append({'source': entry['id'], 'path': path, 'problem': problem})
            continue
        toks = [set(brain.tokens(line)) for line in lines]
        best = []
        for i in range(len(lines)):
            if not lines[i].strip():
                continue
            seen = set()
            for j in range(i, min(len(lines), i + MAX_PROPOSAL_LINES)):
                if not lines[j].strip():
                    break
                seen |= toks[j]
                score = sum(w for k, w in keys.items() if k in seen) / total
                best.append((-round(score, 4), j - i, i + 1, j + 1))
        best.sort()
        for neg, _, first, last in best[:limit]:
            out.append({'source': entry['id'], 'path': path, 'lines': [first, last], 'coverage': -neg,
                        'excerpt': normalise(lines[first - 1:last])[:600]})
    return sorted(out, key=lambda p: -p.get('coverage', -1))[:limit]


def _load_brain():
    sys.path.insert(0, str(TOOLS))
    import brain
    return brain


def update_claim(root, claim_id, change):
    """Apply change(claim) under the vault write lock and save claims.json atomically."""
    brain = _load_brain()
    import storage
    path = Path(root) / 'system/claims.json'
    with storage.vault_lock(root):
        data = json.loads(path.read_text(encoding='utf-8'))
        claim = next((c for c in data['claims'] if c['id'] == claim_id), None)
        if claim is None:
            raise ValueError('unknown claim ' + claim_id)
        result = change(claim)
        brain.save_json(path, data)
    return result


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='command', required=True)
    v = sub.add_parser('verify')
    v.add_argument('--all', action='store_true')
    p = sub.add_parser('propose')
    p.add_argument('claim')
    a = sub.add_parser('accept')
    a.add_argument('claim')
    a.add_argument('source')
    a.add_argument('first', type=int)
    a.add_argument('last', type=int)
    a.add_argument('--method', default='reviewed', choices=('reviewed', 'manual'))
    a.add_argument('--support', default='partial', choices=('full', 'partial'))
    r = sub.add_parser('relocate')
    r.add_argument('claim')
    args = ap.parse_args()
    root = TOOLS.parent
    brain = _load_brain()
    claims = json.loads((root / 'system/claims.json').read_text(encoding='utf-8'))['claims']
    if args.command == 'verify':
        results, counts = verify_claims(root, claims, include_inactive=args.all)
        print(json.dumps({'counts': counts, 'results': results}, indent=1))
    elif args.command == 'propose':
        claim = next(c for c in claims if c['id'] == args.claim)
        print(json.dumps(propose(root, brain, claim), indent=1, ensure_ascii=False))
    elif args.command == 'accept':
        anchor = make_anchor(root, registry_by_id(root), args.source, args.first, args.last, args.method, support=args.support)
        update_claim(root, args.claim, lambda c: c.setdefault('anchors', []).append(anchor))
        print(json.dumps(anchor, indent=1, ensure_ascii=False))
    elif args.command == 'relocate':
        def change(claim):
            sources = Sources(root, registry_by_id(root))
            moved = []
            for i, anchor in enumerate(claim.get('anchors') or []):
                result = verify(anchor, sources)
                if result['state'] == 'relocated':
                    anchor['lines'] = result['lines']
                    moved.append(i)
            return moved
        print(json.dumps({'relocated_anchors': update_claim(root, args.claim, change)}))


if __name__ == '__main__':
    main()
