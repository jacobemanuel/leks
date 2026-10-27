"""Diagnosis and idempotent setup of the vault tools (standard library, system Python 3.9 or newer).

    python3 tools/brain.py doctor [--json]     read-only: every problem with its fix
    python3 tools/brain.py setup --dry-run     what setup would do
    python3 tools/brain.py setup               rebuild stale indexes, refresh the routing evaluation, copy missing
                                               sources into the evidence store, set the immutable flag on raw files
                                               when protect_raw is on; nothing else

doctor writes nothing into the vault: it runs the fast check without a report, starts the MCP server once and
feeds the hooks test input (an allowed command, a refused one and unreadable input). setup never changes evidence,
configuration or git and runs only the steps that are out of date, so a second run does nothing."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import brain  # noqa: E402
import evidence_store  # noqa: E402
import retrieval  # noqa: E402

JSON_FILES = ('system/vault-config.json', 'system/source-registry.json', 'system/claims.json', '.github/hooks/xr-vault.json',
              '.vscode/mcp.json')
EXPECTED_TOOLS = 7
LOW_DISK = 2 * 1024 ** 3


def finding(name, status, detail, fix=''):
    return {'check': name, 'status': status, 'detail': detail, **({'fix': fix} if fix else {})}


def python_on_path():
    """The python3 that the hooks and the MCP server start (they name python3, not a path)."""
    exe = shutil.which('python3')
    if not exe:
        return None, None
    done = subprocess.run([exe, '-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3])'], capture_output=True, text=True, timeout=30)
    return exe, done.stdout.strip() or None


def check_python():
    here = '%d.%d.%d' % sys.version_info[:3]
    exe, version = python_on_path()
    detail = 'running %s (%s); python3 on PATH: %s %s' % (here, sys.executable, exe or 'none', version or '')
    old = lambda v: tuple(int(x) for x in v.split('.')[:2]) < (3, 9)
    if sys.version_info < (3, 9) or not exe or not version or old(version):
        return finding('python', 'fail', detail, 'the tools need Python 3.9 or newer as python3 on PATH; on macOS /usr/bin/python3 '
                       '(Xcode command line tools: xcode-select --install) is enough')
    return finding('python', 'ok', detail)


def check_git(root):
    git = shutil.which('git')
    if not git:
        return finding('git', 'warn', 'git not found', 'install the Xcode command line tools (xcode-select --install); without git '
                       'the as-of history of claims and the change history are unavailable, everything else works')
    done = subprocess.run([git, '-C', str(root), 'rev-parse', '--is-inside-work-tree'], capture_output=True, text=True, timeout=30)
    if done.stdout.strip() != 'true':
        return finding('git', 'warn', 'the vault is not a git working tree', 'clone the vault with git to keep the history of the curated layers')
    return finding('git', 'ok', git)


def check_json(root):
    broken = []
    for name in JSON_FILES:
        p = root / name
        if p.is_file():
            try:
                json.loads(p.read_text(encoding='utf-8'))
            except ValueError as exc:
                broken.append('%s (%s)' % (name, exc))
    if broken:
        return finding('json files', 'fail', '; '.join(broken), 'repair the JSON at the line and column shown; '
                       '`git diff <file>` shows what changed since the last commit')
    return finding('json files', 'ok', 'readable: ' + ', '.join(n for n in JSON_FILES if (root / n).is_file()))


def check_config(root):
    p = root / brain.CONFIG
    if not p.is_file():
        return finding('configuration', 'warn', brain.CONFIG + ' is missing', 'copy it from the vault\'s git history; defaults are used meanwhile')
    try:
        cfg = json.loads(p.read_text(encoding='utf-8'))
    except ValueError:
        return finding('configuration', 'fail', brain.CONFIG + ' is not valid JSON', 'see the json files check')
    problems = []
    if 'evidence_store' in cfg and not isinstance(cfg['evidence_store'], str):
        problems.append('evidence_store must be a path')
    if 'protect_raw' in cfg and not isinstance(cfg['protect_raw'], bool):
        problems.append('protect_raw must be true or false')
    problems += retrieval.config_problems(cfg.get('retrieval'))
    engine, _ = retrieval.configured(brain._namespace(), root)
    if problems:
        return finding('configuration', 'fail', '; '.join(problems), 'correct ' + brain.CONFIG)
    return finding('configuration', 'ok', 'retrieval engine %s; protect_raw %s' % (engine, bool(cfg.get('protect_raw', False))))


def run_hook(root, command, payload, timeout=60):
    started = time.monotonic()
    done = subprocess.run(shlex.split(command), cwd=str(root), input=payload, capture_output=True, text=True, timeout=timeout)
    return done.returncode, done.stdout.strip(), round(time.monotonic() - started, 2)


def check_hooks(root):
    p = root / '.github/hooks/xr-vault.json'
    if not p.is_file():
        return [finding('hooks', 'warn', 'no hook configuration (.github/hooks/xr-vault.json)', 'the vault\'s hooks only run when it is configured')]
    try:
        hooks = json.loads(p.read_text(encoding='utf-8'))['hooks']
    except (ValueError, KeyError, TypeError):
        return [finding('hooks', 'fail', 'unreadable hook configuration', 'repair .github/hooks/xr-vault.json')]
    commands = {event: entry['command'] for event, entries in hooks.items() for entry in entries if entry.get('type') == 'command'}
    out = []
    for event, command in sorted(commands.items()):
        script = next((w for w in shlex.split(command) if w.endswith('.py')), None)
        if script and not (root / script).is_file():
            out.append(finding('hook ' + event, 'fail', 'missing script ' + script, 'restore it from git'))
    guard = commands.get('preToolUse')
    if guard:
        allowed = run_hook(root, guard, json.dumps({'toolName': 'runInTerminal', 'toolArgs': {'command': 'ls wiki'}, 'cwd': str(root)}))
        refused = run_hook(root, guard, json.dumps({'toolName': 'runInTerminal', 'toolArgs': {'command': 'echo x > wiki/index.md'},
                                                    'cwd': str(root)}))
        garbled = run_hook(root, guard, 'not json')
        good = allowed[:2] == (0, '{}') and refused[0] == 2 and garbled[0] == 2
        out.append(finding('hook preToolUse (guard)', 'ok' if good else 'fail',
                           'allowed a listing (exit %d), refused a write to a generated file (exit %d), refused unreadable input '
                           '(exit %d); %.2f s per call' % (allowed[0], refused[0], garbled[0], max(allowed[2], refused[2])),
                           '' if good else 'the guard does not behave as expected'))
    session = commands.get('sessionStart')
    if session:
        code, text, _ = run_hook(root, session, '{}')
        out.append(finding('hook sessionStart', 'ok' if code == 0 and text.startswith('{') else 'fail', 'exit %d' % code,
                           '' if code == 0 else 'run %s by hand to see the error' % session))
    stop = commands.get('agentStop')
    if stop:
        code, text, _ = run_hook(root, stop, json.dumps({'stopHookActive': True}))
        out.append(finding('hook agentStop', 'ok' if (code, text) == (0, '{}') else 'fail', 'exit %d' % code,
                           '' if code == 0 else 'run %s by hand to see the error' % stop))
    return out


def check_mcp(root):
    p = root / '.vscode/mcp.json'
    if not p.is_file():
        return finding('mcp server', 'warn', 'no .vscode/mcp.json', 'VS Code needs it to start the xr-vault MCP server')
    try:
        server = json.loads(p.read_text(encoding='utf-8'))['servers']['xr-vault']
        argv = [server['command']] + [a.replace('${workspaceFolder}', str(root)) for a in server.get('args', [])]
    except (ValueError, KeyError, TypeError):
        return finding('mcp server', 'fail', 'no usable "xr-vault" entry in .vscode/mcp.json', 'restore .vscode/mcp.json from git')
    messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
    started = time.monotonic()
    try:
        done = subprocess.run(argv, cwd=str(root), input=''.join(json.dumps(m) + '\n' for m in messages), capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return finding('mcp server', 'fail', 'could not start or stop: %s' % exc, 'run %s by hand' % ' '.join(argv))
    seconds = time.monotonic() - started
    try:
        replies = {m.get('id'): m for m in map(json.loads, done.stdout.splitlines())}
        tools = len(replies[2]['result']['tools'])
    except (ValueError, KeyError, TypeError):
        return finding('mcp server', 'fail', 'no valid answer (exit %d): %s' % (done.returncode, (done.stderr or done.stdout)[-300:]),
                       'run %s by hand to see the error' % ' '.join(argv))
    good = done.returncode == 0 and tools == EXPECTED_TOOLS
    return finding('mcp server', 'ok' if good else 'fail', 'started, listed %d tools and stopped at end of input (exit %d) in %.2f s'
                   % (tools, done.returncode, seconds), '' if good else 'expected %d tools and exit 0' % EXPECTED_TOOLS)


def check_vault(root):
    result = brain.check(root, write_report=False, fast=True)
    if result['errors']:
        stale = any('build required' in e for e in result['errors'])
        return finding('vault check', 'fail', '%d error(s), first: %s' % (len(result['errors']), result['errors'][0]),
                       'run python3 tools/brain.py setup' if stale and all('build required' in e for e in result['errors'])
                       else 'run python3 tools/brain.py check and fix what it reports')
    return finding('vault check', 'ok', 'fast check: 0 errors, %d warning(s); %d raw files, %d pages, %d claims'
                   % (len(result['warnings']), result['raw_files'], result['wiki_pages'], result['claims']))


def check_store(root):
    s = evidence_store.settings(root)
    if not s['store']:
        return finding('evidence store', 'warn', 'not configured', 'set "evidence_store" in system/vault-config.json (a folder outside the '
                       'vault), then run python3 tools/brain.py setup')
    if not evidence_store.store_ready(root):
        return finding('evidence store', 'warn', 'not initialised at %s' % s['store'], 'run python3 tools/brain.py setup')
    state = evidence_store.verify_store(root)
    if state['corrupt']:
        return finding('evidence store', 'fail', '%d corrupt object(s)' % len(state['corrupt']), 'run python3 tools/brain.py backup '
                       '--verify --deep and restore the store folder from a backup')
    if state['missing']:
        return finding('evidence store', 'warn', '%d source(s) not yet copied' % len(state['missing']), 'run python3 tools/brain.py setup')
    return finding('evidence store', 'ok', '%d sources present in %s' % (state['checked'], s['store']))


def check_journal_and_incidents(root):
    out = []
    errors = evidence_store.journal_verify(root)
    out.append(finding('journal', 'fail' if errors else 'ok', '; '.join(errors[:3]) or '%d entries, chain and checkpoints consistent'
                       % len(evidence_store.journal_lines(root)), 'compare the journal with the store checkpoints' if errors else ''))
    incidents = evidence_store.open_incidents(root)
    out.append(finding('integrity incidents', 'fail' if incidents else 'ok', ', '.join(i['id'] for i in incidents) or 'none open',
                       'restore the evidence (python3 tools/brain.py restore <path> --replace), check, then python3 tools/brain.py '
                       'incident resolve <ID> --reason "..."' if incidents else ''))
    return out


def check_protection(root):
    s = evidence_store.settings(root)
    if not evidence_store.flags_supported():
        return finding('raw protection', 'warn', 'immutable flags are not supported on this system', 'rely on the checks and the store')
    if not s['protect_raw']:
        return finding('raw protection', 'warn', 'protect_raw is off: raw files are writable at the file-system level and only the '
                       'guard, the checks and the evidence store protect them', 'owner decision: set "protect_raw": true in '
                       'system/vault-config.json, then run python3 tools/brain.py setup')
    state = evidence_store.protect(root, 'check')
    if state['protected'] < state['files']:
        return finding('raw protection', 'fail', '%d of %d raw files without the immutable flag' % (state['files'] - state['protected'],
                       state['files']), 'run python3 tools/brain.py protect apply')
    return finding('raw protection', 'ok', 'all %d registered raw files immutable' % state['files'])


def check_dense(root):
    engine, problem = retrieval.configured(brain._namespace(), root)
    lexical, dense = retrieval.parse(engine) or (None, False)
    if not dense:
        return finding('dense subsystem', 'ok', 'not needed: engine %s is lexical (the optional R2/R4/R5 need the dense helper, '
                       'which this release leaves out)' % engine)
    import dense_client
    if not dense_client.installed():
        return finding('dense subsystem', 'fail', 'engine %s is configured but the dense subsystem is not installed; searches fall back '
                       'to BM25' % engine, 'install the dense helper, or set the engine back to R1')
    return finding('dense subsystem', 'ok', 'installed for engine %s' % engine)


def check_disk(root):
    free = shutil.disk_usage(str(root)).free
    status = 'warn' if free < LOW_DISK else 'ok'
    return finding('disk space', status, '%.1f GB free' % (free / 1024 ** 3), 'free disk space before building or backing up' if status == 'warn' else '')


def diagnose(root):
    root = Path(root)
    findings = [check_python(), check_git(root), check_json(root), check_config(root)]
    if findings[2]['status'] != 'fail':
        findings += check_hooks(root) + [check_mcp(root), check_vault(root), check_store(root)] + check_journal_and_incidents(root)
        findings += [check_protection(root), check_dense(root)]
    findings.append(check_disk(root))
    counts = {s: sum(f['status'] == s for f in findings) for s in ('ok', 'warn', 'fail')}
    return {'ok': counts['fail'] == 0, 'counts': counts, 'findings': findings}


def plan(root):
    """The setup steps that are out of date, with the reason for each."""
    root, steps = Path(root), []
    result = brain.check(root, write_report=False, fast=True)
    manifest, evaluation = root / '.brain/build-manifest.json', root / '.brain/retrieval-evaluation.json'
    if not manifest.exists() or any('build required' in e for e in result['errors']):
        steps.append(('build', 'derived indexes are missing or older than the pages'))
    if steps or not evaluation.exists() or evaluation.stat().st_mtime < manifest.stat().st_mtime:
        steps.append(('evaluate', 'the routing evaluation is missing or older than the last build'))
    s = evidence_store.settings(root)
    if s['store'] and (not evidence_store.store_ready(root) or evidence_store.verify_store(root)['missing']):
        steps.append(('backup', 'sources are missing from the evidence store at %s' % s['store']))
    if s['protect_raw'] and evidence_store.flags_supported():
        state = evidence_store.protect(root, 'check')
        if state['protected'] < state['files']:
            steps.append(('protect apply', '%d raw files lack the immutable flag' % (state['files'] - state['protected'])))
    return steps


def setup(root, dry_run=False):
    root = Path(root)
    steps = plan(root)
    if dry_run:
        return {'dry_run': True, 'would_run': [{'step': name, 'reason': why} for name, why in steps]}
    ran = []
    for name, why in steps:
        started = time.monotonic()
        if name == 'build':
            brain.build(root)
        elif name == 'evaluate':
            brain.evaluate(root)
        elif name == 'backup':
            evidence_store.backup(root)
        elif name == 'protect apply':
            evidence_store.protect(root, 'apply')
        ran.append({'step': name, 'reason': why, 'seconds': round(time.monotonic() - started, 1)})
    left = plan(root)
    return {'dry_run': False, 'ran': ran, 'still_needed': [{'step': name, 'reason': why} for name, why in left],
            'status': 'ok' if not left else 'error'}


def explain(command, exc):
    """A failed command as one readable message with the likely fix instead of a traceback (BRAIN_DEBUG=1 shows it)."""
    if os.environ.get('BRAIN_DEBUG'):
        raise exc
    root = brain.ROOT
    broken = [n for n in JSON_FILES if (root / n).is_file() and not _readable_json(root / n)]
    hint = ('%s is not valid JSON: repair it (git diff shows the change)' % ', '.join(broken) if broken
            else 'run python3 tools/brain.py doctor for a diagnosis')
    print(json.dumps({'status': 'error', 'command': command, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:500]), 'fix': hint,
                      'debug': 'set BRAIN_DEBUG=1 to see the traceback'}, indent=2), file=sys.stderr)
    return 2


def _readable_json(path):
    try:
        json.loads(path.read_text(encoding='utf-8'))
        return True
    except (ValueError, OSError):
        return False


def report(result):
    marks = {'ok': 'ok  ', 'warn': 'WARN', 'fail': 'FAIL'}
    lines = ['doctor: %d checks, %d failed, %d warning(s)' % (len(result['findings']), result['counts']['fail'], result['counts']['warn'])]
    for f in result['findings']:
        lines.append('  %s  %-26s %s' % (marks[f['status']], f['check'], f['detail']))
        if f.get('fix'):
            lines.append('        %-26s fix: %s' % ('', f['fix']))
    return '\n'.join(lines)


def run(root, args):
    """brain.py doctor / setup."""
    if args.command == 'setup':
        result = setup(root, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return 0 if result.get('status', 'ok') == 'ok' else 1
    result = diagnose(root)
    print(json.dumps(result, indent=2) if args.json else report(result))
    return 0 if result['ok'] else 1
