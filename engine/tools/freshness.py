"""Freshness of current wiki pages (Phase 3): deterministic signals that a page may no longer reflect its evidence.

Dates: a page has as_of (evidence date) and compiled_on (editing date); a source's evidence date is the first
YYYY-MM-DD in its path (unknown when the path has none); a claim has as_of. Archived pages are history and are
never stale. Rules, each a finding with the page, the rule and the reason:

    S1 claim-newer      an active claim on the page has a later as_of than the page
    S2 source-newer     a cited source's evidence date is later than the page's as_of
    S3 uncited-newer    a registered source in the folder of a cited source is dated after compiled_on and the page
                        does not cite it (newer evidence that may not be compiled in)
    S4 edited-after     a commit after compiled_on changed the page body meaningfully without updating compiled_on;
                        mechanical commits (front matter only, link targets only, whitespace, pure renames) and the
                        commit that sets compiled_on (it may land just after midnight) do not count
    S5 claim-corrected  the key tokens of an active claim on the page (numbers, identifiers, negations, evidence
                        words) changed in a commit after compiled_on, and no commit at or after it changed the page
                        body; wording-only edits (a pronoun replaced by a role) and claim metadata are mechanical
Per-type rules:
    T1 now              wiki/now.md is compiled on or after the newest as_of of any active claim
    T2 latest delivery  wiki/codebase/latest-delivery.md names the latest installed build of the structured facts

Configuration (optional "freshness" in system/vault-config.json; the information type of a page is its category):
    {"severity": {"S2": "warning"},                    # default severity per rule: warning | notice | off
     "types": {"Implementation reference": {"S2": "warning"}, "History": {"S1": "off"}},
     "now_tolerance_days": 1}                          # T1: days now.md may lag the newest claim
Defaults: S1, S5, T1, T2 warnings (brain.py check); S2, S3, S4 notices (this command only); tolerance 0.

    python3 tools/freshness.py [--json]
"""
import json
from pathlib import Path
import re
import subprocess
import sys

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import knowledge_time  # noqa: E402

DATE = re.compile(r'(20\d\d-\d\d-\d\d)')
LINK = re.compile(r'\[\[[^\]|]*(\|[^\]]*)?\]\]|\]\([^)]*\)')
NOW, DELIVERY = 'wiki/now.md', 'wiki/codebase/latest-delivery.md'
KEY = re.compile(r'\d[\d.,:/-]*|\b(?:no|not|never|without|unknown|unverified|verified|reported|planned|proposed|built|'
                 r'installed|launched|accepted|rejected|superseded|archived)\b|\b[A-Za-z]+-?\d[\w.-]*\b', re.I)
SEVERITY = {'S1': 'warning', 'S2': 'notice', 'S3': 'notice', 'S4': 'notice', 'S5': 'warning', 'T1': 'warning', 'T2': 'warning'}


def settings(root):
    """Severity per rule and per information type, and the T1 tolerance, from system/vault-config.json."""
    path = Path(root) / 'system/vault-config.json'
    cfg = (json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}).get('freshness') or {}
    return {'severity': dict(SEVERITY, **(cfg.get('severity') or {})), 'types': cfg.get('types') or {},
            'now_tolerance_days': int(cfg.get('now_tolerance_days', 0))}


def _git(root, *args):
    """git output, or '' when git is missing or fails (no history then: the working version only)."""
    try:
        return subprocess.run(['git', '-C', str(root)] + list(args), capture_output=True, text=True).stdout
    except OSError:
        return ''


def text_date(value):
    """An ISO date string, or '' for anything else (malformed dates are reported by the check, not here)."""
    return value if isinstance(value, str) and DATE.fullmatch(value) else ''


def source_date(path):
    m = DATE.search(path)
    return m.group(1) if m else None


def body(text):
    """Page text without front matter, link targets and whitespace differences (what a reader sees)."""
    if text.startswith('---\n') and text.find('\n---', 4) > 0:
        text = text[text.find('\n---', 4) + 4:]
    text = LINK.sub(lambda m: (m.group(1) or '|')[1:] if m.group(0).startswith('[[') else ']', text)
    return ' '.join(text.split())


class GitHistory:
    """Commits per wiki page and the page texts before and after each, read with one `git log` and one
    `git cat-file --batch` instead of two `git show` processes per commit."""

    def __init__(self, root):
        self.root, self.commits = root, {}
        for line in _git(root, 'log', '--format=@%H %cI', '--name-only', '--', 'wiki').splitlines():
            if line.startswith('@'):
                commit, when = line[1:].split(' ', 1)
            elif line.strip():
                self.commits.setdefault(line.strip(), []).append((when, commit))
        self.blobs = {}

    def fetch(self, specs):
        specs = [s for s in specs if s not in self.blobs]
        if not specs:
            return
        try:
            data = subprocess.run(['git', '-C', str(self.root), 'cat-file', '--batch'], input=('\n'.join(specs) + '\n').encode('utf-8'),
                                  capture_output=True).stdout
        except OSError:
            data = b''
        pos = 0
        for spec in specs:                      # "<sha> blob <bytes>\n<content>\n" or "<spec> missing\n"
            eol = data.find(b'\n', pos)
            header = data[pos:eol].split() if eol >= 0 else []
            if len(header) == 3 and header[1] == b'blob':
                size = int(header[2])
                self.blobs[spec] = data[eol + 1:eol + 1 + size].decode('utf-8', errors='replace')
                pos = eol + 1 + size + 1
            else:
                self.blobs[spec] = ''
                pos = eol + 1 if eol >= 0 else len(data)

    def edits(self, path, since=None):
        """[(commit time, commit, body changed, compiled_on changed)] oldest first; with `since` (a date) only
        commits from the day before it on (S4 and S5 look only after compiled_on)."""
        import datetime
        floor = (datetime.date.fromisoformat(since) - datetime.timedelta(days=1)).isoformat() if since else ''
        commits = [(w, c) for w, c in reversed(self.commits.get(path, [])) if w[:10] >= floor]
        self.fetch([s for _, c in commits for s in ('%s^:%s' % (c, path), '%s:%s' % (c, path))])
        meta = lambda text: (re.search(r'^compiled_on:\s*(.*)$', text, re.M) or [None, None])[1]
        out = []
        for when, commit in commits:
            before, after = self.blobs['%s^:%s' % (commit, path)], self.blobs['%s:%s' % (commit, path)]
            out.append((when, commit, bool(before and after and body(before) != body(after)), meta(before) != meta(after)))
        return out

def key_tokens(text):
    return sorted(m.group(0).lower() for m in KEY.finditer(text or ''))


def claim_corrections(versions, ids, since):
    """{claim id: (time, commit)} of the last change of its text after the date `since` (S5)."""
    out, previous = {}, {}
    for when, commit, claims in versions:
        texts = {c['id']: c.get('text') for c in claims if c['id'] in ids}
        for cid, text in texts.items():
            if cid in previous and key_tokens(previous[cid]) != key_tokens(text) and when.date().isoformat() > since:
                out[cid] = (when, commit)
        previous.update(texts)
    return out


def findings(root, brain, history=None, git=True):
    """[{'page', 'rule', 'reason', 'severity'}] for every current page; rules set to "off" are left out."""
    root = Path(root)
    config = settings(root)
    claims = json.loads((root / 'system/claims.json').read_text(encoding='utf-8'))['claims']
    active = [c for c in claims if c.get('lifecycle') not in ('archived', 'superseded')]
    registry = json.loads((root / 'system/source-registry.json').read_text(encoding='utf-8'))['files']
    by_folder = {}
    for e in registry:
        by_folder.setdefault(str(Path(e['path']).parent), []).append(e['path'])
    versions = history if history is not None else (knowledge_time.history(root) if git else [])
    out, repo = [], GitHistory(root) if git else None
    for p, meta, text in brain.pages(root):
        path = p.relative_to(root).as_posix()
        if brain.is_archived(path, meta):
            continue
        as_of, compiled = text_date(meta.get('as_of')), text_date(meta.get('compiled_on'))
        cited = [s for s in meta.get('sources') or [] if isinstance(s, str)]
        mine = [c for c in active if c.get('page') == path]
        newest = max((text_date(c.get('as_of')) for c in mine), default='')
        if as_of and newest > as_of:
            out.append({'page': path, 'rule': 'S1', 'reason': 'claim as_of %s is later than the page as_of %s' % (newest, as_of)})
        late = sorted(s for s in cited if (source_date(s) or '') > as_of) if as_of else []
        if late:
            out.append({'page': path, 'rule': 'S2', 'reason': '%d cited source(s) dated after as_of %s, e.g. %s' % (len(late), as_of, late[0])})
        if compiled:
            folders = {str(Path(s).parent) for s in cited if s.startswith('raw/') and str(Path(s).parent) != 'raw'}
            uncited = sorted(s for f in folders for s in by_folder.get(f, []) if s not in cited and (source_date(s) or '') > compiled)
            if uncited:
                out.append({'page': path, 'rule': 'S3', 'reason': '%d uncited source(s) in cited folders dated after compiled_on %s, e.g. %s'
                            % (len(uncited), compiled, uncited[0])})
            edits = repo.edits(path, compiled) if git else []
            late_edits = [(d[:10], c) for d, c, changed, bumped in edits if changed and not bumped and d[:10] > compiled]
            if late_edits:
                out.append({'page': path, 'rule': 'S4', 'reason': 'body changed after compiled_on %s without updating it: %s' % (
                    compiled, ', '.join('%s %s' % (d, c[:7]) for d, c in late_edits[:3]))})
            for cid, (when, commit) in sorted(claim_corrections(versions, {c['id'] for c in mine}, compiled).items()):
                if not any(changed and knowledge_time.parse_time(d) >= when for d, _, changed, _ in edits):
                    out.append({'page': path, 'rule': 'S5', 'reason': 'claim %s text changed on %s (%s), after compiled_on %s; page not edited since' % (
                        cid, when.date().isoformat(), commit[:7], compiled)})
        if path == NOW:
            import datetime
            latest = max((text_date(c.get('as_of')) for c in active), default='')
            allowed = (datetime.date.fromisoformat(compiled) + datetime.timedelta(days=config['now_tolerance_days'])).isoformat() if compiled else ''
            if compiled and latest > allowed:
                out.append({'page': path, 'rule': 'T1', 'reason': 'compiled_on %s is older than the newest claim (%s)' % (compiled, latest)})
        if path == DELIVERY:
            builds = [b for b in (knowledge_time.latest_build(claims, app, '9999-12-31', 'installed')
                                  for app in sorted({f['subject'] for c in claims for f in knowledge_time.facts_of(c)
                                                     if f.get('attribute') == 'build' and isinstance(f.get('subject'), str)}))
                      if b and 'build' in b]
            if builds:
                newest = max(builds, key=lambda b: (b['first_evidence'], b['build']))
                if newest['build'] not in text:
                    out.append({'page': path, 'rule': 'T2', 'reason': 'does not name the latest installed build %s (%s)' % (
                        newest['build'], ', '.join(newest['claims']))})
        for row in out:
            if row['page'] == path and 'severity' not in row:
                row['severity'] = (config['types'].get(meta.get('category')) or {}).get(row['rule'], config['severity'].get(row['rule'], 'notice'))
    return [row for row in out if row['severity'] != 'off']


def main():
    import brain
    root = TOOLS.parent
    rows = findings(root, brain)
    if '--json' in sys.argv:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        print('%-60s %s %-7s %s' % (r['page'], r['rule'], r['severity'], r['reason']))
    print('%d finding(s) on %d page(s)' % (len(rows), len({r['page'] for r in rows})))


if __name__ == '__main__':
    main()
