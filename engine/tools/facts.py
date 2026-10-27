"""Structured facts on claims and the executable evidence rules (Phase 3).

A claim may carry "facts": typed statements a program can compare. A fact restates part of its claim, never
more than the claim text, and names the anchors (indexes into the claim's "anchors") that support it:
    {"subject": "...", "attribute": "...", "value": "...", "anchors": [0],
     optional "level" and "device" (build facts), "confidence" (default: the claim's), "basis" (required when the
     fact is inferred), "note"}

Attributes (ONE: one value at a time per subject and device):
    build                 subject: app bundle ID; value: build number (12 digits); level: the highest evidence level
                          the claim establishes for that build; device: where it was installed or launched
    unity-version         ONE; subject: a Unity project or snapshot; value: an editor version such as 6000.3.19f1
    decision-status       ONE; subject: a decision record (ADR-0007); value: proposed | active | superseded | archived
    owner-verdict         ONE; subject: a build number; value: accepted | not-accepted | partial
    source-changeset      ONE; subject: a build number; value: the version-control changeset it was built from (for example cs:12)
    source-state          subject: a project; value: the date of a saved source state (the claim's evidence date);
                          level: the highest step the claim itself establishes for that state, one of proposed |
                          implemented | mac-tested | simulator-tested (a build of the same state is a build fact)
    device-compatibility  ONE; subject: an app bundle ID or build; device: a device or OS; value: compatible | incompatible

Rules; a finding is an error for an active claim and a warning for an archived or superseded one:
    F1  schema: known attribute, valid value, level and confidence; anchors index the claim's anchors
    F2  fact-level verification: the value (for builds also the subject is checked by F2b) occurs in a verified
        excerpt of a cited anchor, unless the fact is inferred and states its basis
    E1  a build fact has a level from EVIDENCE_LEVELS
    E2  a planned claim (a proposal) establishes nothing above "proposed" and no decision-status "active":
        a proposal is never promoted by its own text
    E3  installed, launched and device-accepted need a verified or reported fact and a cited verified excerpt with
        the marker of that level, and the cited excerpts must name a device (AVP, Vision Pro, headset, device,
        devicectl): a build is never proof of deployment, and "tested on the Mac" is no device step
    E4  for a claim with one build fact whose number the claim text names, or with one source-state fact: a step
        its text or scope affirms (built, installed,
        launched, accepted) needs that level; one it negates ("no headset install", "No Player build") or leaves
        open ("launch ... not yet recorded") forbids it. A negation word governs the eight words after it within
        a clause, an open marker the twelve words before it (clauses end at ; . "but" and "while"), so "No Player build, install or acceptance" negates all three
    E5  in the recorded history of system/claims.json, a fact's level never rises while its anchors stay the same
    E6  an unknown claim carries no fact except the value "unknown"; device-accepted needs an owner-verdict
        "accepted" for that build in a verified or reported claim
    I1  every build number, Unity editor version and changeset that an anchored claim's text names occurs in one
        of its verified excerpts (a number changed in the text alone is caught)
Marker words in excerpts count only when no negation word comes among the eight words before them ("Final build
has not been installed" is no proof of installation).
Contradictions (ONE attributes): facts on the same subject, attribute and device with different values whose valid
intervals overlap, after an open-ended fact is closed by the next fact's start (that is a revision, not a
contradiction). Facts of claims linked by supersedes or conflicts are history or a recorded conflict. A build
number under two app bundles, and a deployment one claim negates while another establishes it on or before that
date, are contradictions too.

    python3 tools/facts.py check        # rule findings, contradictions and revisions
    python3 tools/facts.py coverage     # critical-claim coverage (population rule and an audit sample)
"""
import json
from pathlib import Path
import random
import re
import sys

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import anchors  # noqa: E402
import knowledge_time  # noqa: E402

LEVELS = knowledge_time.EVIDENCE_LEVELS
DEPLOYMENT = ('installed', 'launched', 'device-accepted')
CONFIDENCE = ('verified', 'reported', 'inferred', 'planned', 'unknown')
PROOF = ('verified', 'reported')
ATTRIBUTES = {
    'build': {'one': False, 'pattern': r'\d{12}'},
    'unity-version': {'one': True, 'pattern': r'\d{4}\.\d+\.\d+f\d+'},
    'decision-status': {'one': True, 'values': ('proposed', 'active', 'superseded', 'archived')},
    'owner-verdict': {'one': True, 'values': ('accepted', 'not-accepted', 'partial')},
    'source-changeset': {'one': True, 'pattern': r'cs:\d+'},
    'source-state': {'one': False, 'pattern': r'\d{4}-\d{2}-\d{2}', 'levels': ('proposed', 'implemented', 'mac-tested', 'simulator-tested')},
    'device-compatibility': {'one': True, 'values': ('compatible', 'incompatible'), 'device': True},
}
# Words in a cited excerpt that support a value or a level (case-insensitive).
MARKERS = {
    'installed': r'\binstall(?:ed|s|ing|ation)?\b',
    'launched': r'\b(?:launch(?:ed|es|ing)?|smoke-launched|started|ran|running|tested|testing|checked it)\b',
    'device-accepted': r'\b(?:accept(?:ed|ance|s)?|approved)\b',
    'accepted': r'\b(?:accept(?:ed|ance|s)?|approved|looks? (?:really |very )?good)\b',
    'not-accepted': r"\b(?:not (?:accepted|satisfied)|rejected|do(?:es)? not work|don't work|(?:does|do) not like|don't (?:really )?like)\b",
    'partial': r'\b(?:partial(?:ly)?|except|but)\b',
    'implemented': r'\b(?:saved|implement\w*|add(?:s|ed)?|gained|remov(?:es|ed)|renders?)\b',
    'mac-tested': r'\b(?:pass(?:es|ed)?|verified|compil(?:es|ed)|checks?|validat\w*)\b',
    'proposed': r'\b(?:propos(?:ed|al)|draft)\b',
    'active': r'\bstatus:\s*"?(?:active|accepted|approved)\b|\b(?:accepted|approved|decided)\b',   # not "for decision"
    'superseded': r'\bsupersed(?:ed|es)\b',
    'archived': r'\barchiv(?:ed|e)\b',
}
# Steps in claim text and scope, and the words that negate them or leave them open (NegEx-style scope).
VERBS = {
    'built': r'\bbuilt\b|\b(?:player|app|release|test|official|signed|device|native|new|earlier|final|a|no)\s+(?:\w+\s+)?build\b|\bbuild\s+\d{12}\b',
    'installed': r'\binstall(?:ed|s|ing|ation)?\b',
    'launched': r'\b(?:launch(?:ed|es|ing)?|smoke-launched|device (?:pass|result|test)\w*)\b|\btest(?:ed|ing)\b(?=[^;.]*\b(?:on|with)\b[^;.]*\b(?:AVP|Vision Pro|headset|device)\b)',
    'device-accepted': r'\baccept(?:ed|ance|s)?\b',
}
DEVICE = r'\b(?:AVP|Vision Pro|headset|device|devicectl)\b'
NEEDS_DEVICE = ('installed', 'device-accepted')    # "installed-package conversion", "accepted code" are no device steps
NEGATION = r"\b(?:no|not|never|without|cannot|failed|didn't|wasn't)\b"
OPEN = r'\b(?:unknown|pending|unverified|not (?:yet|recorded|verified|measured))\b'
WINDOW, OPEN_WINDOW = 8, 12
# Critical-claim population rules (see coverage()). v1 was the first rule; v2 replaced it after v1's false
# positives were inspected, and both are reported.
CRITICAL = re.compile(r'\b\d{12}\b|\b\d{4}\.\d+\.\d+f\d+\b|\bADR-\d{4}\b|\bcs:\d+\b|'
                      r'\b(?:install\w*|launch\w*|accept\w*|test(?:ed|ing))\b.*\b(?:AVP|Vision Pro|headset|device)\b', re.I)
IDENTIFIERS = re.compile(r'\b\d{12}\b|\b\d{4}\.\d+\.\d+f\d+\b|\bADR-\d{4}\b|\bcs:\d+\b')
APP_PAGES = ('wiki/codebase/', 'wiki/projects/', 'wiki/now.md')
INACTIVE = anchors.INACTIVE
IDENTIFIER = re.compile(r'\b\d{12}\b|\b\d{4}\.\d+\.\d+f\d+\b|\bcs:\d+\b')


def claim_facts(claim):
    """The claim's well-formed facts (objects with a known attribute and string subject and value); schema_errors
    reports the others, so a malformed fact can never stop the rules."""
    facts = claim.get('facts')
    if not isinstance(facts, list):
        return []
    return [f for f in facts if isinstance(f, dict) and f.get('attribute') in ATTRIBUTES
            and isinstance(f.get('subject'), str) and isinstance(f.get('value'), str)]


def fact_confidence(claim, fact):
    return fact.get('confidence') or claim.get('confidence')


def date_errors(claim):
    """Validity dates: ISO dates, and valid_to not before valid_from (or as_of)."""
    import datetime
    out, cid = [], claim.get('id')
    for key in ('as_of', 'valid_from', 'valid_to', 'archived_on'):
        value = claim.get(key)
        if value is None:
            continue
        try:
            datetime.date.fromisoformat(value)
        except (TypeError, ValueError):
            out.append('%s: %s %r is not an ISO date' % (cid, key, value))
    start, end = claim.get('valid_from') or claim.get('as_of'), claim.get('valid_to')
    if isinstance(start, str) and isinstance(end, str) and end < start:
        out.append('%s: valid_to %s is before valid_from %s' % (cid, end, start))
    return out


def schema_errors(claim):
    """F1 and E1 findings of one claim (strings)."""
    out, cid = date_errors(claim), claim.get('id')
    if 'facts' in claim and not isinstance(claim['facts'], list):
        return out + ['%s: "facts" must be a list' % cid]
    n_anchors = len(claim.get('anchors') or [])
    for i, f in enumerate(claim['facts'] if isinstance(claim.get('facts'), list) else []):
        where = '%s: fact %d' % (cid, i)
        if not isinstance(f, dict):
            out.append(where + ' is not an object')
            continue
        spec = ATTRIBUTES.get(f.get('attribute'))
        if spec is None:
            out.append('%s has unknown attribute %r' % (where, f.get('attribute')))
            continue
        if not isinstance(f.get('subject'), str) or not f['subject'].strip():
            out.append(where + ' lacks a subject')
        value = f.get('value')
        if not isinstance(value, str) or not (value == 'unknown' or (re.fullmatch(spec['pattern'], value) if 'pattern' in spec
                                                                   else value in spec['values'])):
            out.append('%s has invalid value %r for %s' % (where, value, f['attribute']))
        if f['attribute'] in ('build', 'source-state') and f.get('level') not in spec.get('levels', LEVELS):
            out.append('%s has invalid level %r (E1)' % (where, f.get('level')))
        if f['attribute'] not in ('build', 'source-state') and 'level' in f:
            out.append(where + ': only build and source-state facts carry a level')
        if spec.get('device') and not f.get('device'):
            out.append(where + ' needs a device')
        conf = fact_confidence(claim, f)
        if conf not in CONFIDENCE:
            out.append('%s has invalid confidence %r' % (where, conf))
        elif claim.get('confidence') in CONFIDENCE and CONFIDENCE.index(conf) < CONFIDENCE.index(claim['confidence']):
            out.append('%s claims more confidence (%s) than its claim (%s)' % (where, conf, claim['confidence']))
        if fact_confidence(claim, f) == 'inferred' and claim.get('confidence') != 'inferred' and not f.get('basis'):
            out.append(where + ' is inferred and needs a basis')
        refs = f.get('anchors', [])
        if not (isinstance(refs, list) and all(isinstance(r, int) and 0 <= r < n_anchors for r in refs)):
            out.append('%s cites anchors %r; the claim has %d' % (where, refs, n_anchors))
    return out


def clauses(text):
    return [c for c in re.split(r'[;.]\s+|[;.]$|\s+but\s+|\s+while\s+', text or '') if c.strip()]


def _opens(after):
    """An open marker follows, and no negation comes first (a negation starts another scope: "the build is not
    installed and its appearance is unknown" leaves the build affirmed)."""
    o = re.search(OPEN, after, re.I)
    if not o:
        return False
    n = next((x for x in re.finditer(NEGATION, after, re.I)), None)
    return n is None or n.start() >= o.start()


def deployment_wording(claim):
    """{step: 'affirmed' | 'open' | 'negated'} for the steps named in the claim's text and scope (an install or an
    acceptance only with a device word in the same clause). An occurrence is negated by a negation word among the
    WINDOW words before it, else open when an open marker follows within OPEN_WINDOW words ("are not yet recorded", "unknown"); across occurrences an affirmation wins (another clause may be about
    something else), then an open state, then a negation."""
    states = {}
    for clause in clauses(claim.get('text', '')) + clauses(claim.get('scope', '')):
        for level, verb in VERBS.items():
            if level in NEEDS_DEVICE and not re.search(DEVICE, clause, re.I):
                continue
            for m in re.finditer(verb, clause, re.I):
                words = re.findall(r"[\w'-]+", clause[:m.start()]) + re.findall(r"[\w'-]+", m.group(0))[:-1]   # "No Player build"
                before = ' '.join(words[-WINDOW:])
                after = ' '.join(re.findall(r"[\w'-]+", clause[m.end():])[:OPEN_WINDOW])
                state = 'negated' if re.search(NEGATION, before, re.I) else 'open' if _opens(after) else 'affirmed'
                states.setdefault(level, set()).add(state)
    return {level: next(s for s in ('affirmed', 'open', 'negated') if s in found) for level, found in states.items()}


def mentions(identifier, text):
    """`identifier` occurs in `text` as a whole token: not as part of a longer build number, version or bundle ID
    (com.example.viewer-6.3 is not mentioned by com.example.viewer-6.3-variant)."""
    return re.search(r'(?<![\w.-])' + re.escape(identifier) + r'(?![\w-]|\.\w)', text, re.I) is not None


def affirmed(pattern, text):
    """A match of `pattern` in `text` with no negation word among the WINDOW words before it in its clause."""
    for clause in clauses(text):
        for m in re.finditer(pattern, clause, re.I):
            if not re.search(NEGATION, ' '.join(re.findall(r"[\w'-]+", clause[:m.start()])[-WINDOW:]), re.I):
                return True
    return False


def _excerpts(claim, fact, results):
    """Normalised texts of the cited anchors that verify (or relocate exactly)."""
    rows = results.get(claim['id'], [])
    out = []
    for r in fact['anchors'] if isinstance(fact.get('anchors'), list) else []:
        row = next((x for x in rows if x['index'] == r), None)
        if row and row['state'] in ('verified', 'relocated'):
            out.append(row['text'])
    return out


def rule_findings(claims, results, history=None):
    """Findings of F2 and E2-E6 as (claim id, message) pairs. results: anchor verification with excerpt texts."""
    out = []
    verdicts = {(f['subject'], f['value']) for c in claims for f in claim_facts(c)
                if f.get('attribute') == 'owner-verdict' and fact_confidence(c, f) in PROOF}
    for c in claims:
        facts = claim_facts(c)
        raw = c['facts'] if isinstance(c.get('facts'), list) else []
        for i, f in enumerate(raw):             # numbered as in the file; malformed facts are schema findings
            if not any(f is g for g in facts):
                continue
            conf, texts = fact_confidence(c, f), _excerpts(c, f, results)
            joined = '\n'.join(texts)
            if f['attribute'] == 'source-state':
                if f.get('value') != (c.get('valid_from') or c.get('as_of')):
                    out.append((c['id'], 'fact %d: a source state is dated by its claim (%s), not %s (F2)' % (i, c.get('as_of'), f.get('value'))))
                if f.get('level') in MARKERS and not affirmed(MARKERS[f['level']], joined):
                    out.append((c['id'], 'fact %d: no verified cited excerpt supports %r (F2)' % (i, f.get('level'))))
            elif f.get('value') != 'unknown':
                literal = 'pattern' in ATTRIBUTES[f['attribute']]
                if literal and not mentions(f['value'], joined):
                    out.append((c['id'], 'fact %d: value %s does not occur in a verified cited excerpt (F2)' % (i, f['value'])))
                marker = MARKERS.get(f['value'], re.escape(f['value']))
                if not literal and not (re.search(marker, joined, re.I) if f['value'] == 'not-accepted' else affirmed(marker, joined)):
                    out.append((c['id'], 'fact %d: no verified cited excerpt supports %r (F2)' % (i, f['value'])))
                identified = re.fullmatch(r'\d{12}|[a-z0-9-]+(?:\.[a-z0-9-]+){2,}', f['subject'])
                if identified and not (conf == 'inferred' and f.get('basis')) and \
                        not mentions(f['subject'], joined + '\n' + c.get('text', '')):
                    out.append((c['id'], 'fact %d: subject %s occurs neither in the claim nor in a cited excerpt (F2)' % (i, f['subject'])))
            if c.get('confidence') == 'planned':
                if f['attribute'] == 'build' and f.get('level') != 'proposed':
                    out.append((c['id'], 'fact %d: a planned claim establishes nothing above "proposed" (E2)' % i))
                if f['attribute'] == 'decision-status' and f['value'] == 'active':
                    out.append((c['id'], 'fact %d: a proposal cannot record its decision as active (E2)' % i))
            level = f.get('level')
            if f['attribute'] == 'build' and level in DEPLOYMENT and conf in PROOF:
                if not any(affirmed(MARKERS[l], joined) for l in DEPLOYMENT[DEPLOYMENT.index(level):]):
                    out.append((c['id'], 'fact %d: level %s has no cited verified excerpt that says so (E3)' % (i, level)))
                elif not re.search(DEVICE, joined, re.I):
                    out.append((c['id'], 'fact %d: level %s is supported by no excerpt that names a device (E3)' % (i, level)))
            if f['attribute'] == 'build' and level in DEPLOYMENT and conf not in PROOF + ('inferred',):
                out.append((c['id'], 'fact %d: a %s claim is no proof of %s (E3)' % (i, conf, level)))
            if c.get('confidence') == 'unknown' and f.get('value') != 'unknown':
                out.append((c['id'], 'fact %d: an unknown claim can only record "unknown" (E6)' % i))
            if level == 'device-accepted' and (f['value'], 'accepted') not in verdicts:
                out.append((c['id'], 'fact %d: device-accepted needs an owner-verdict "accepted" for build %s (E6)' % (i, f['value'])))
        verified = [r['text'] for r in results.get(c['id'], []) if 'text' in r]
        if verified:
            evidence = '\n'.join(verified)
            missing = sorted({i for i in IDENTIFIER.findall(c.get('text', '')) if not mentions(i, evidence)})
            if missing:
                out.append((c['id'], 'the text names %s, which no verified excerpt contains (I1)' % ', '.join(missing)))
        steps = [f for f in facts if f['attribute'] in ('build', 'source-state')]
        if len(steps) == 1 and (steps[0]['attribute'] == 'source-state' or mentions(steps[0]['value'], c.get('text', ''))):
            level = steps[0].get('level')
            for wording_level, state in deployment_wording(c).items():
                has = knowledge_time.level_rank(level) >= LEVELS.index(wording_level)
                if state == 'affirmed' and not has and fact_confidence(c, steps[0]) in PROOF:
                    out.append((c['id'], 'the text says %s but the %s fact records %s (E4)' % (wording_level, steps[0]['attribute'], level)))
                if state in ('negated', 'open') and has:
                    out.append((c['id'], 'the text leaves %s %s but the fact records %s (E4)' % (
                        wording_level, 'denied' if state == 'negated' else 'open', level)))
    if history:
        out += promotion_findings(history)
    return out


def promotion_findings(history):
    """E5: a build fact whose level rose between recorded versions while its claim's anchors stayed the same."""
    out, seen = [], {}
    for _, version, claims in history:
        for c in claims:
            key_anchors = c.get('anchors_key') or json.dumps(c.get('anchors') or [], sort_keys=True)   # either view
            for f in claim_facts(c):
                if not isinstance(f, dict) or f.get('attribute') != 'build':
                    continue
                key = (c.get('id'), f.get('subject'), f.get('value'), f.get('device'))
                rank = knowledge_time.level_rank(f.get('level'))
                if key in seen:
                    old_rank, old_anchors, old_version = seen[key]
                    if rank > old_rank and key_anchors == old_anchors:
                        out.append((c['id'], 'build %s rose to %s after %s without new anchors (E5)' % (f.get('value'), f.get('level'), old_version)))
                seen[key] = (rank, key_anchors, version)
    return out


def _related(a, b):
    """'superseded' when one claim supersedes the other (directly), 'conflict' when they list each other."""
    ids = (a['id'], b['id'])
    if a.get('superseded_by') == b['id'] or b.get('superseded_by') == a['id'] or \
            b['id'] in (a.get('supersedes') or []) or a['id'] in (b.get('supersedes') or []):
        return 'superseded'
    if ids[1] in (a.get('conflicts') or []) or ids[0] in (b.get('conflicts') or []):
        return 'conflict'
    return None


def contradictions(claims):
    """[{kind: contradiction | revision | superseded | recorded-conflict, ...}] over the claims' facts."""
    by_id = {c['id']: c for c in claims}
    groups, out = {}, []
    for c in claims:
        start, end, kind = knowledge_time.interval_detail(c, by_id)
        end = end if kind == 'explicit' else None      # archived_on bounds validity; it does not mark its end
        for f in claim_facts(c):
            spec = ATTRIBUTES.get(f.get('attribute')) if isinstance(f, dict) else None
            if spec and spec['one'] and start and f.get('value') != 'unknown':
                groups.setdefault((f['attribute'], f['subject'], f.get('device')), []).append((start, end, f['value'], c))
    # facts of one claim are compared too: two values for one subject in a single claim contradict each other
    for (attribute, subject, device), rows in sorted(groups.items(), key=lambda x: str(x[0])):
        rows.sort(key=lambda r: (r[0], r[3]['id']))
        for i, (s1, e1, v1, c1) in enumerate(rows):
            for s2, e2, v2, c2 in rows[i + 1:]:
                if v1 == v2:
                    continue
                row = {'attribute': attribute, 'subject': subject, 'device': device, 'claims': [c1['id'], c2['id']],
                       'values': [v1, v2], 'valid_from': [s1, s2]}
                overlap = s1 == s2 or (e1 is not None and e1 > s2)
                relation = _related(c1, c2)
                if relation == 'superseded':
                    row['kind'] = 'superseded'
                elif relation == 'conflict':
                    row['kind'] = 'recorded-conflict'
                elif overlap:
                    row['kind'] = 'contradiction'
                else:
                    row['kind'] = 'revision'
                if row['kind'] != 'revision' or s2 == next((r[0] for r in rows if r[0] > s1), None):
                    out.append(row)
    apps = {}
    for c in claims:
        for f in claim_facts(c):
            if isinstance(f, dict) and f.get('attribute') == 'build' and f.get('value') != 'unknown':
                apps.setdefault(f['value'], {}).setdefault(f['subject'], []).append(c['id'])
    for build, subjects in sorted(apps.items()):
        if len(subjects) > 1:
            out.append({'kind': 'contradiction', 'attribute': 'build', 'subject': build, 'values': sorted(subjects),
                        'claims': sorted({i for ids in subjects.values() for i in ids}), 'reason': 'one build under several apps'})
    out += deployment_contradictions(claims, by_id)
    return out


def deployment_contradictions(claims, by_id):
    """A claim whose text denies a deployment of its one build, while another claim establishes it on or before
    that date (a deployment left open, "not yet recorded", is never contradicted)."""
    out, positive = [], []
    for c in claims:
        start = knowledge_time.interval(c, by_id)[0]
        for f in claim_facts(c):
            if isinstance(f, dict) and f.get('attribute') == 'build' and fact_confidence(c, f) in PROOF:
                positive.append((f['value'], knowledge_time.level_rank(f.get('level')), start, c['id']))
    for c in claims:
        builds = [f for f in claim_facts(c) if isinstance(f, dict) and f.get('attribute') == 'build']
        if len(builds) != 1 or not mentions(builds[0].get('value', ''), c.get('text', '')):
            continue                    # the wording is attributed to a build only when the claim names it
        start = knowledge_time.interval(c, by_id)[0]
        for level, state in deployment_wording(c).items():
            if state != 'negated':
                continue
            for build, rank, when, other in positive:
                if build == builds[0]['value'] and other != c['id'] and rank >= LEVELS.index(level) and when and start and when <= start:
                    out.append({'kind': 'contradiction', 'attribute': 'deployment', 'subject': build, 'values': ['not ' + level, level],
                                'claims': [c['id'], other], 'valid_from': [start, when]})
    return out


def verify_with_text(root, claims):
    """Anchor verification results with the verified excerpt text of each anchor."""
    sources = anchors.Sources(root, anchors.registry_by_id(root))
    results = {}
    for c in claims:
        rows = []
        for i, a in enumerate(c.get('anchors') or []):
            r = dict(anchors.verify(a, sources), index=i)
            if r['state'] in ('verified', 'relocated'):
                lines = sources.get(a['source'])[1]
                r['text'] = anchors.normalise(lines[r['lines'][0] - 1:r['lines'][1]])
            rows.append(r)
        results[c['id']] = rows
    return results


def population(claims, rule='v2'):
    """Active claims that carry critical facts.
    v1: text or scope matches CRITICAL (an identifier, or a deployment word anywhere before a device word).
    v2: the text names a build number, a Unity editor version, a changeset or an ADR; or the claim is on a page of
        the app (wiki/codebase/, wiki/projects/, wiki/now.md) and its text or scope states a step of the evidence
        ladder (built, installed, launched, accepted; affirmed, negated or open)."""
    active = [c for c in claims if c.get('lifecycle') not in INACTIVE]
    if rule == 'v1':
        return [c for c in active if CRITICAL.search(c.get('text', '') + ' ' + c.get('scope', ''))]
    return [c for c in active if IDENTIFIERS.search(c.get('text', '')) or
            (c.get('page', '').startswith(APP_PAGES) and deployment_wording(c))]


def coverage(claims, results, rule='v2', sample_size=15, seed=20261024):
    """Critical-claim coverage: the share of the population with at least one fact and no F1-F2 or E-rule finding.
    The audit sample draws active claims outside the population for a manual check of the rule (false negatives)."""
    findings = {cid for cid, _ in rule_findings(claims, results)}
    members = population(claims, rule)
    rows = []
    for c in members:
        facts = claim_facts(c)
        rows.append({'claim': c['id'], 'covered': bool(facts) and not schema_errors(c) and c['id'] not in findings, 'facts': len(facts)})
    outside = sorted(c['id'] for c in claims if c.get('lifecycle') not in INACTIVE and c not in members)
    sample = sorted(random.Random(seed).sample(outside, min(sample_size, len(outside))))
    covered = sum(r['covered'] for r in rows)
    return {'rule': rule, 'population': len(rows), 'covered': covered,
            'pct': round(100.0 * covered / len(rows), 1) if rows else None, 'claims': rows,
            'audit_sample_outside_population': sample, 'seed': seed}


def check_findings(root, claims, history=None):
    """(errors, warnings, summary) for brain.check."""
    errors, warnings = [], []
    lifecycle = {c['id']: c.get('lifecycle') for c in claims}
    results = verify_with_text(root, claims)
    found = [(c['id'], m.split(': ', 1)[1]) for c in claims for m in schema_errors(c)] + rule_findings(claims, results, history)
    for cid, message in found:
        (warnings if lifecycle.get(cid) in INACTIVE else errors).append('%s: %s' % (cid, message))
    rows = contradictions(claims)
    for r in rows:
        if r['kind'] == 'contradiction':
            text = 'contradiction on %s of %s: %s (%s)' % (r['attribute'], r['subject'], ' vs '.join(r['values']), ', '.join(r['claims']))
            (warnings if all(lifecycle.get(i) in INACTIVE for i in r['claims']) else errors).append(text)
    for c in claims:
        if c.get('archived_on') and c.get('lifecycle') not in INACTIVE and c.get('page', '').startswith('wiki/archive/'):
            warnings.append('%s: on an archived page with archived_on %s but lifecycle %s; current or archived?' % (
                c['id'], c['archived_on'], c.get('lifecycle') or 'unset'))
    kinds = {}
    for r in rows:
        kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
    summary = {'claims_with_facts': sum(1 for c in claims if claim_facts(c)), 'facts': sum(len(claim_facts(c)) for c in claims),
               'relations': kinds}
    return errors, warnings, summary


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('command', choices=('check', 'coverage'))
    args = ap.parse_args()
    root = TOOLS.parent
    claims = json.loads((root / 'system/claims.json').read_text(encoding='utf-8'))['claims']
    if args.command == 'check':
        errors, warnings, summary = check_findings(root, claims, knowledge_time.history(root, 'builds'))
        print(json.dumps({'errors': errors, 'warnings': warnings, 'summary': summary,
                          'relations': [r for r in contradictions(claims)]}, indent=1, ensure_ascii=False))
    else:
        results = verify_with_text(root, claims)
        print(json.dumps([coverage(claims, results, rule) for rule in ('v1', 'v2')], indent=1))


if __name__ == '__main__':
    main()
