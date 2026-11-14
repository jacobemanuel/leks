"""Rules for the workflow files that actionlint does not know about.

    python .github/scripts/check_workflows.py [REPO]

Every workflow under .github/workflows:
- states its permissions at the top and gives every job a timeout
- pins every action to a full 40 character commit SHA with the version in a comment
- never uses continue-on-error, so no step or job can fail quietly
- installs tools only pinned, through .github/ci/requirements-*.txt or name==version

tests.yml, models.yml and security.yml also keep the triggers, the matrix and the
jobs that docs/testing.md describes, and dependabot.yml watches the actions and the
pip files. Every run_suite.py step names a positive whole --floor, and the test steps
docs/testing.md lists run their modules, with --no-skips where that page says no test
may skip. Prints one line per problem and exits 1 when there is any. Needs PyYAML.
"""
from __future__ import annotations

import glob
import os
import re
import shlex
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHA_PIN = re.compile(r'^\s*(?:-\s+)?uses:\s*([^@\s]+)@([0-9a-f]{40})\s+#\s*v\d')
USES = re.compile(r'^\s*(?:-\s+)?uses:\s*(\S+)')
PIP = re.compile(r'pip install\b(.*)')
OSES = ['ubuntu-latest', 'macos-latest', 'windows-latest']
PYTHONS = ['3.10', '3.11', '3.12', '3.13']
# the run_suite.py steps docs/testing.md names: workflow, job, modules (none: the whole suite), --no-skips
SUITE_STEPS = (
    ('tests.yml', 'unit', (), False),
    ('tests.yml', 'model-daemon-tcp', ('tests.test_transport', 'tests.test_doctor'), True),
    ('tests.yml', 'mcp', ('tests.test_mcp_conformance',), True),
    ('tests.yml', 'mcp', ('tests.test_host_payloads',), False),
    ('tests.yml', 'packaging', ('tests.test_packaging',), True),
    ('tests.yml', 'lint', ('tests.test_ci_scripts',), True),
    ('models.yml', 'models', ('tests.test_real_models',), True),
)


def load(path):
    with open(path, encoding='utf-8') as f:
        text = f.read()
    return text, yaml.safe_load(text)


def triggers(doc):
    # yaml 1.1 reads the key on as true
    on = doc.get('on', doc.get(True))
    return on if isinstance(on, dict) else {k: None for k in (on if isinstance(on, list) else [on])}


def walk(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + (k,), v
            yield from walk(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, path + (i,))


def steps_text(job):
    return '\n'.join(str(s.get('run', '')) + ' ' + str(s.get('uses', '')) for s in job.get('steps') or [])


def suite_runs(doc):
    """(job id, floor, no_skips, modules) of every run_suite.py call; floor is None when it is not a
    positive whole number, $TESTS_FLOOR is read from the top level env"""
    env = doc.get('env') or {}
    out = []
    for job_id, job in doc['jobs'].items():
        for step in job.get('steps') or []:
            run = str(step.get('run') or '')
            if 'run_suite.py' not in run:
                continue
            try:
                words = shlex.split(run.replace('\\\n', ' '))
            except ValueError:
                out.append((job_id, None, False, ()))
                continue
            at = [i for i, w in enumerate(words) if w.endswith('run_suite.py')]
            for i in at:
                rest = words[i + 1:]
                cut = [j for j, w in enumerate(rest) if w in ('&&', '||', ';', '|')]
                rest = rest[:cut[0]] if cut else rest
                floor = None
                if '--floor' in rest and rest.index('--floor') + 1 < len(rest):
                    f = rest[rest.index('--floor') + 1]
                    if f.startswith('$'):
                        f = str(env.get(f.strip('${}'), ''))
                    floor = int(f) if f.isdigit() and int(f) > 0 else None
                mods = tuple(w for w in rest if w.startswith('tests.'))
                out.append((job_id, floor, '--no-skips' in rest, mods))
    return out


def suite_rules(name, doc):
    bad = []
    runs = suite_runs(doc)
    for job_id, floor, _, mods in runs:
        if floor is None:
            bad.append('%s: a run_suite.py step in %s (%s) has no positive whole --floor'
                       % (name, job_id, ' '.join(mods) or 'whole suite'))
    for wf, job_id, mods, no_skips in SUITE_STEPS:
        if wf != name:
            continue
        what = ' '.join(mods) or 'the whole suite'
        hit = [r for r in runs if r[0] == job_id and set(r[3]) == set(mods)]
        if not hit:
            bad.append('%s: job %s does not run %s through run_suite.py' % (name, job_id, what))
        elif no_skips and not all(r[2] for r in hit):
            bad.append('%s: job %s runs %s without --no-skips' % (name, job_id, what))
    return bad


def general(name, text, doc):
    bad = []
    if not isinstance(doc, dict) or not isinstance(doc.get('jobs'), dict):
        return ['%s: no jobs' % name]
    if 'permissions' not in doc:
        bad.append('%s: no top level permissions' % name)
    if not triggers(doc):
        bad.append('%s: no triggers' % name)
    for job_id, job in doc['jobs'].items():
        if 'uses' not in job and 'timeout-minutes' not in job:
            bad.append('%s: job %s has no timeout-minutes' % (name, job_id))
    for path, _ in walk(doc):
        if path and path[-1] == 'continue-on-error':
            bad.append('%s: continue-on-error at %s' % (name, '.'.join(map(str, path))))
    for n, line in enumerate(text.splitlines(), 1):
        m = USES.match(line)
        if m and not m.group(1).startswith('./') and not SHA_PIN.match(line):
            bad.append('%s:%d: action not pinned to a full SHA with a version comment: %s' % (name, n, line.strip()))
        p = PIP.search(line)
        if p and not line.lstrip().startswith('#'):
            for word in p.group(1).split():
                w = word.strip('"\'')
                if w.startswith('-') or w.startswith('.') or w.startswith('$') or w.startswith('http'):
                    continue
                if w.startswith('.github/ci/requirements-') or '==' in w or w in ('torch',):
                    continue
                bad.append('%s:%d: tool installed without a pin: %s' % (name, n, w))
    return bad


def tests_rules(doc):
    bad = []
    on = triggers(doc)
    push = on.get('push') or {}
    if not {'main', 'release-*'} <= set(push.get('branches') or []):
        bad.append('tests.yml: push must cover main and release-*')
    if 'v*' not in (push.get('tags') or []):
        bad.append('tests.yml: push must cover the tags v*')
    for t in ('pull_request', 'workflow_dispatch'):
        if t not in on:
            bad.append('tests.yml: no %s trigger' % t)
    unit = doc['jobs'].get('unit') or {}
    strat = unit.get('strategy') or {}
    matrix = strat.get('matrix') or {}
    if strat.get('fail-fast') is not False:
        bad.append('tests.yml: unit needs fail-fast: false')
    if sorted(matrix.get('os') or []) != sorted(OSES):
        bad.append('tests.yml: unit must run on %s' % ', '.join(OSES))
    if [str(v) for v in matrix.get('python') or []] != PYTHONS:
        bad.append('tests.yml: unit must run Python %s (quoted, 3.10 is no float)' % ', '.join(PYTHONS))
    if matrix.get('exclude') or matrix.get('include'):
        bad.append('tests.yml: unit must run every cell of the matrix')
    run = steps_text(unit)
    for want in ('.github/scripts/run_suite.py --floor "$TESTS_FLOOR"', 'bench/run_bench.py --models none',
                 'bench/gate.py', 'bench/baseline_words.json'):
        if want not in run:
            bad.append('tests.yml: unit does not run %s' % want)
    floor = str((doc.get('env') or {}).get('TESTS_FLOOR', ''))
    if not floor.isdigit() or int(floor) < 1:
        bad.append('tests.yml: TESTS_FLOOR must be a positive whole number')
    for job_id in ('model-daemon-tcp', 'mcp', 'packaging', 'coverage', 'lint'):
        if job_id not in doc['jobs']:
            bad.append('tests.yml: no %s job' % job_id)
    return bad + suite_rules('tests.yml', doc)


def models_rules(doc):
    bad = []
    on = triggers(doc)
    if 'pull_request' in on:
        bad.append('models.yml: no pull_request trigger, the weights download is too slow for every push')
    if not {'main', 'release-*'} <= set((on.get('push') or {}).get('branches') or []):
        bad.append('models.yml: push must cover main and release-*')
    for t in ('schedule', 'workflow_dispatch'):
        if t not in on:
            bad.append('models.yml: no %s trigger' % t)
    run = '\n'.join(steps_text(j) for j in doc['jobs'].values())
    for want in ('--no-skips tests.test_real_models', 'actions/cache/restore', 'leks models fetch', '--lanes'):
        if want not in run:
            bad.append('models.yml: missing %s' % want)
    return bad + suite_rules('models.yml', doc)


def security_rules(doc):
    bad = []
    run = '\n'.join(steps_text(j) for j in doc['jobs'].values())
    for want, what in (('github/codeql-action/analyze', 'CodeQL'), ('pip-audit', 'pip-audit'),
                       ('gitleaks', 'the secret scan'), ('bandit -c pyproject.toml', 'bandit')):
        if want not in run:
            bad.append('security.yml: no %s' % what)
    scan = [j for j in doc['jobs'].values() if 'gitleaks' in steps_text(j)]
    if scan and not any((s.get('with') or {}).get('fetch-depth') == 0 for s in scan[0].get('steps') or []):
        bad.append('security.yml: the secret scan needs the whole history (fetch-depth: 0)')
    return bad


def dependabot_rules(doc):
    seen = {(u.get('package-ecosystem'), u.get('directory')) for u in doc.get('updates') or []}
    bad = []
    for want in (('github-actions', '/'), ('pip', '/'), ('pip', '/.github/ci')):
        if want not in seen:
            bad.append('dependabot.yml: no %s updates for %s' % want)
    return bad


def check(root=ROOT):
    bad = []
    files = sorted(glob.glob(os.path.join(root, '.github', 'workflows', '*.yml')))
    if not files:
        return ['no workflow files under .github/workflows']
    docs = {}
    for path in files:
        name = os.path.basename(path)
        try:
            text, doc = load(path)
        except yaml.YAMLError as e:
            bad.append('%s: not valid yaml: %s' % (name, str(e).splitlines()[0]))
            continue
        docs[name] = doc
        bad += general(name, text, doc)
    for name, rules in (('tests.yml', tests_rules), ('models.yml', models_rules), ('security.yml', security_rules)):
        if name not in docs:
            bad.append('%s is missing' % name)
        elif isinstance(docs[name], dict) and isinstance(docs[name].get('jobs'), dict):
            bad += rules(docs[name])
    dep = os.path.join(root, '.github', 'dependabot.yml')
    try:
        bad += dependabot_rules(load(dep)[1] or {})
    except (OSError, yaml.YAMLError):
        bad.append('dependabot.yml is missing or not valid yaml')
    for req in sorted(glob.glob(os.path.join(root, '.github', 'ci', 'requirements-*.txt'))):
        with open(req, encoding='utf-8') as f:
            for n, line in enumerate(f, 1):
                s = line.split('#', 1)[0].strip()
                if s and not re.fullmatch(r'[A-Za-z0-9._-]+==[0-9][0-9A-Za-z.]*', s):
                    bad.append('%s:%d: not pinned with ==: %s' % (os.path.basename(req), n, s))
    return bad


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    bad = check(argv[0] if argv else ROOT)
    for b in bad:
        print(b)
    print('workflow rules: %s' % ('%d problem%s' % (len(bad), '' if len(bad) == 1 else 's') if bad else 'ok'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
