#!/usr/bin/env python3
"""Run the same questions on one host in four arms, with and without LEKS, and keep what each session cost.

    python3 bench/hosts/run.py claude --dry-run --out /tmp/leks-hosts-dry
    python3 bench/hosts/run.py claude --model sonnet --effort medium --small-model haiku
    python3 bench/hosts/run.py codex --model MODEL --small-model SMALL
    python3 bench/hosts/run.py copilot --model auto --small-model SMALL
    python3 bench/hosts/run.py fake --format copilot --out /tmp/leks-hosts-fake

The arms, each in its own copy of the bench corpus:

    plain     one agent with the host's own file tools, no LEKS
    leks      one agent with the file tools and the LEKS server
    team      an orchestrator with two role agents (knowledge, review), all with LEKS
    subagent  the main agent hands every search to a search subagent on a small model, with LEKS

The index is built once, words only unless --models-from names a config with
models. Every question runs once per arm and repeat, each as one
non-interactive session, and the order of the arms rotates by question and
repeat. Each session keeps the host's event stream, its stderr, a meta.json
and a result.json with the tokens, the cost in the host's own unit, the wall
time and whether the automatic task check passed (the answer names a gold
file). The `fake` host is a script that prints a host's event stream with made
up numbers, so the harness can be tested with no account. bench/hosts/summarise.py
turns run folders into paired numbers with bootstrap intervals over questions.
README.md next to this file has the design.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TEMPLATE = os.path.join(REPO, 'examples', 'project-template')
FAKE_HOST = os.path.join(HERE, 'fake_host.py')
HOSTS = ('claude', 'codex', 'copilot', 'fake')
FORMATS = ('claude', 'codex', 'copilot')
ARMS = ('plain', 'leks', 'team', 'subagent')
LEKS_ARMS = ('leks', 'team', 'subagent')
SERVER = 'leks'
SERVER_ARGS = ['-m', 'leks', 'serve']
EXPECTED_TOOLS = ('locate', 'neighbours', 'read', 'search', 'state', 'status', 'tried')
MODEL_PLACEHOLDER = '<model>'
SMALL_PLACEHOLDER = '<small-model>'

# every arm reads with the host's own file tools; shell, writing, web and skills are off in all arms.
# the subagent tool is on only in the two arms that need it
CLAUDE_READ = ['Read', 'Grep', 'Glob']
CLAUDE_DENY = ['Bash', 'Edit', 'Write', 'NotebookEdit', 'WebFetch', 'WebSearch', 'Task', 'Agent',
               'ToolSearch', 'Skill']
CLAUDE_AGENT_TOOLS = ('Task', 'Agent')
COPILOT_EXCLUDE = ['bash', 'read_bash', 'write_bash', 'stop_bash', 'list_bash',
                   'powershell', 'read_powershell', 'write_powershell', 'stop_powershell', 'list_powershell',
                   'create', 'edit', 'apply_patch', 'web_fetch', 'task', 'list_agents', 'read_agent',
                   'write_agent', 'skill']
COPILOT_AGENT_TOOLS = ('task', 'list_agents', 'read_agent', 'write_agent')
# files a host could read from a folder above the projects, in every arm alike
INSTRUCTION_FILES = ('CLAUDE.md', 'CLAUDE.local.md', 'AGENTS.md', 'GEMINI.md',
                     os.path.join('.claude', 'CLAUDE.md'), os.path.join('.github', 'copilot-instructions.md'))

# the role agents of the two agent arms, fixed before any host run. small: runs on --small-model,
# the others on the main model. leks: may call the LEKS tools
AGENTS = {
    'knowledge': {
        'description': 'Finds what the project files say about a brief, with LEKS and the file tools, and reports '
                       'the passages with path:line. Edits nothing.',
        'prompt': 'You are the knowledge agent. Find what the project files say about the brief. Call `search` '
                  'first, open the lines you rely on with `read` or the file tools, and report the passages that '
                  'answer it, each with `path:line`. If nothing answers it, say so and say what you searched for. '
                  'Edit nothing.',
        'small': False, 'leks': True},
    'review': {
        'description': 'Checks a draft answer against the lines it cites and reports each claim that does not '
                       'hold. Edits nothing.',
        'prompt': 'You are the review agent. You get a question and a draft answer with citations. Open each cited '
                  'line and check that it supports the claim. Report each claim that does not hold, with the line '
                  'that shows it, or say that every claim holds. Edit nothing.',
        'small': False, 'leks': False},
    'searcher': {
        'description': 'Search helper on a small model: runs the searches and look-ups in the project files and '
                       'reports passages with path:line. Edits nothing.',
        'prompt': 'You are a search helper. Use the LEKS tools (`search`, `read`, `locate`) and the file tools to '
                  'find what the project files say about the request. Report the passages that answer it, each '
                  'with `path:line`, in at most 15 lines. If nothing answers it, say so and say what you searched '
                  'for. Edit nothing.',
        'small': True, 'leks': True},
}
ARM_AGENTS = {'team': ('knowledge', 'review'), 'subagent': ('searcher',)}
# sections of the template's AGENTS.md that point to files a bench project does not have (the skills folder and
# the agent team of the repository), so every LEKS arm starts from the same text without them
KIT_SECTIONS = ('Skills', 'Agent team')
# appended to the template's AGENTS.md in the agent arms
ARM_SECTIONS = {
    'team': '\n## Agent team\n\nYou are the orchestrator. Two role agents work for you as subagents: `knowledge` '
            'finds and reads the project material with the LEKS tools and the file tools and reports passages with '
            '`path:line`; `review` checks a draft answer against the lines it cites. For a question, brief '
            '`knowledge` with it, write the answer from its report, have `review` check the answer, fix what it '
            'finds, and then answer. Name the files the answer comes from.\n',
    'subagent': '\n## Search helper\n\nA subagent named `searcher` runs the searches. Hand every search and every '
                'look-up in the files to `searcher` with the question, and write the answer from the passages and '
                '`path:line` citations it reports. Open a file yourself only to check a line it cites.\n',
}


def _load_summarise():
    spec = importlib.util.spec_from_file_location('leks_hosts_summarise', os.path.join(HERE, 'summarise.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SUMMARISE = _load_summarise()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def write_json(path: str, obj) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2)
        f.write('\n')


def read_jsonl(path: str) -> list:
    return SUMMARISE.read_jsonl(path)


def load_tasks(path: str, all_questions: bool = False, ids=None, smoke: bool = False) -> dict:
    with open(path, encoding='utf-8') as f:
        t = json.load(f)
    base = os.path.dirname(os.path.abspath(path))

    def rel(p):
        return os.path.normpath(os.path.join(base, p))

    with open(rel(t['questions']), encoding='utf-8') as f:
        qs = json.load(f)['questions']
    by_id = {q['id']: q for q in qs}
    sm = t.get('smoke') or {}
    if all_questions:
        want = [q['id'] for q in qs]
    elif ids:
        want = list(ids)
    elif smoke:
        want = list(sm.get('ids') or [])
    else:
        want = list(t['ids'])
    missing = [i for i in want if i not in by_id]
    if missing:
        raise SystemExit('unknown question ids: ' + ', '.join(missing))
    if not want:
        raise SystemExit('no questions picked')
    if len(set(want)) != len(want):
        raise SystemExit('a question id is listed twice')
    arms = list(t.get('arms') or ARMS)
    bad = [x for x in arms if x not in ARMS]
    if bad:
        raise SystemExit('unknown arms in the task set: ' + ', '.join(bad))
    repeats = int(sm.get('repeats', 1)) if smoke and not ids and not all_questions else int(t.get('repeats', 2))
    return {'questions': [by_id[i] for i in want], 'questions_file': rel(t['questions']),
            'corpus': rel(t['corpus']), 'config': rel(t['config']), 'arms': arms,
            'profile': t.get('profile', 'orchestrator'), 'repeats': repeats,
            'timeout_s': int(t.get('timeout_s', 600)), 'prompt': t['prompt']}


def parse_arms(text: str) -> list:
    arms = [x.strip() for x in text.split(',') if x.strip()]
    bad = [x for x in arms if x not in ARMS]
    if bad or not arms:
        raise SystemExit('--arms takes a comma-separated list of: ' + ', '.join(ARMS))
    if len(set(arms)) != len(arms):
        raise SystemExit('an arm is listed twice')
    return arms


def schedule(questions: list, repeats: int, arms=ARMS) -> list:
    """(repeat, question, arm) in run order; the arm that goes first rotates by question and repeat"""
    arms = list(arms)
    n = len(arms)
    out = []
    for r in range(1, repeats + 1):
        for i, q in enumerate(questions):
            k = (i + r) % n
            for arm in arms[k:] + arms[:k]:
                out.append((r, q, arm))
    return out


def session_name(qid: str, repeat: int, arm: str) -> str:
    return '%s-r%d-%s' % (qid, repeat, arm)


def clean_env(extra=None) -> dict:
    """the caller's environment without LEKS_*, so no shell setting points a session at another index"""
    env = {k: v for k, v in os.environ.items() if not k.startswith('LEKS_')}
    env.update(extra or {})
    return env


def server_env(prep: dict) -> dict:
    return {'LEKS_CONFIG': prep['config'], 'LEKS_PROFILE': prep['profile'], 'PYTHONPATH': REPO}


def parent_instruction_files(project: str) -> list:
    """instruction files in the folders above a project, which a host may read in every arm"""
    found = []
    d = os.path.dirname(os.path.abspath(project))
    while True:
        for name in INSTRUCTION_FILES:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                found.append(p)
        up = os.path.dirname(d)
        if up == d:
            return found
        d = up


def agent_names(arm: str) -> tuple:
    return ARM_AGENTS.get(arm, ())


def claude_agents(arm: str, small_model: str) -> dict:
    """the --agents value for one arm: each role agent with its tools and model"""
    out = {}
    for name in agent_names(arm):
        d = AGENTS[name]
        tools = list(CLAUDE_READ) + (['mcp__%s__*' % SERVER] if d['leks'] else [])
        out[name] = {'description': d['description'], 'prompt': d['prompt'], 'tools': tools,
                     'model': small_model if d['small'] else 'inherit'}
    return out


def copilot_agent_md(name: str, small_model: str) -> str:
    """a .github/agents/NAME.agent.md profile; a json-quoted string is a valid yaml scalar"""
    d = AGENTS[name]
    lines = ['---', 'name: ' + name, 'description: ' + json.dumps(d['description'])]
    if d['small']:
        # required: the agent runs on this model even when the main session names another one
        lines += ['model: ' + json.dumps(small_model), 'model-policy: required']
    lines += ['---', '', d['prompt'], '']
    return '\n'.join(lines)


def codex_agent_toml(name: str, small_model: str) -> str:
    """a .codex/agents/NAME.toml file; a json string with ascii escapes is a valid toml basic string"""
    d = AGENTS[name]
    lines = ['name = ' + json.dumps(name), 'description = ' + json.dumps(d['description'])]
    if d['small']:
        lines.append('model = ' + json.dumps(small_model))
    lines += ['developer_instructions = ' + json.dumps(d['prompt']), '']
    return '\n'.join(lines)


def base_instructions(text: str) -> str:
    """the template's AGENTS.md without its kit sections"""
    out, keep = [], True
    for line in text.splitlines(keepends=True):
        if line.startswith('## '):
            keep = line[3:].strip() not in KIT_SECTIONS
        if keep:
            out.append(line)
    return ''.join(out).rstrip('\n') + '\n'


def make_projects(work: str, host: str, corpus: str, arms, small_model: str) -> None:
    """one project folder per arm with the same corpus, plus `corpus`, the copy the index is built from.
    the LEKS arms get the template's instruction files, the agent arms their section and agent files"""
    for name in ['corpus'] + list(arms):
        proj = os.path.join(work, name)
        shutil.copytree(corpus, proj)
        git = shutil.which('git') if name != 'corpus' else None
        if git:
            # a .git makes the folder the project root for hosts that walk up to one (codex, copilot)
            subprocess.run([git, 'init', '-q', proj], check=False, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(os.path.join(TEMPLATE, 'AGENTS.md'), encoding='utf-8') as f:
        agents_md = base_instructions(f.read())
    for arm in arms:
        if arm not in LEKS_ARMS:
            continue
        proj = os.path.join(work, arm)
        with open(os.path.join(proj, 'AGENTS.md'), 'w', encoding='utf-8') as f:
            f.write(agents_md.rstrip('\n') + '\n' + ARM_SECTIONS.get(arm, ''))
        if host == 'claude':
            # claude code reads CLAUDE.md, which imports AGENTS.md, as in the template
            shutil.copy2(os.path.join(TEMPLATE, 'CLAUDE.md'), proj)
        for name in agent_names(arm):
            if host == 'copilot':
                d = os.path.join(proj, '.github', 'agents')
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, name + '.agent.md'), 'w', encoding='utf-8') as f:
                    f.write(copilot_agent_md(name, small_model))
            elif host == 'codex':
                d = os.path.join(proj, '.codex', 'agents')
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, name + '.toml'), 'w', encoding='utf-8') as f:
                    f.write(codex_agent_toml(name, small_model))


def leks_config(work: str, fixture_config: str, profile: str, models_from=None) -> dict:
    with open(fixture_config, encoding='utf-8') as f:
        cfg = json.load(f)
    for r in cfg['roots']:
        r['path'] = os.path.join(work, 'corpus', os.path.basename(os.path.normpath(r['path'])))
    cfg['home'] = os.path.join(work, 'leks-home')
    cfg['default_profile'] = profile
    if models_from:
        with open(models_from, encoding='utf-8') as f:
            other = json.load(f)
        models = dict(other.get('models') or {})
        wd = models.get('weights_dir')
        if isinstance(wd, str) and wd and not os.path.isabs(os.path.expanduser(wd)):
            # relative paths in a config resolve against its own folder
            models['weights_dir'] = os.path.join(os.path.dirname(os.path.abspath(models_from)), wd)
        cfg['models'] = models
    return cfg


def check_server(py: str, env: dict, cwd: str, timeout: int = 60) -> dict:
    """start the server the way a host does, initialize it and list its tools"""
    msgs = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                        'clientInfo': {'name': 'bench-hosts', 'version': '0'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
    data = ''.join(json.dumps(m) + '\n' for m in msgs)
    try:
        p = subprocess.run([py] + SERVER_ARGS, cwd=cwd, env=env, input=data, capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'ok': False, 'error': type(e).__name__}
    tools, instructions = [], None
    for line in p.stdout.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        r = o.get('result') if isinstance(o, dict) else None
        if not isinstance(r, dict):
            continue
        if 'tools' in r:
            tools = [t.get('name') for t in r['tools']]
        if 'instructions' in r:
            instructions = len(r.get('instructions') or '')
    return {'ok': tuple(sorted(tools)) == EXPECTED_TOOLS, 'tools': tools, 'instructions_chars': instructions,
            'exit': p.returncode, 'stderr_tail': p.stderr[-400:]}


def mcp_json(prep: dict, host: str) -> dict:
    """the host's MCP config for the LEKS arms; the server starts as in examples/project-template"""
    e = {'command': prep['python'], 'args': list(SERVER_ARGS), 'env': server_env(prep)}
    if host == 'copilot':
        # copilot cli fields: type local, and tools, which its user config requires
        e = {'type': 'local', 'command': e['command'], 'args': e['args'], 'env': e['env'], 'tools': ['*']}
    return {'mcpServers': {SERVER: e}}


def codex_overrides(prep: dict) -> list:
    """-c overrides that register the server for one codex exec; a json string or array is valid toml"""
    o = ['mcp_servers.%s.command=%s' % (SERVER, json.dumps(prep['python'])),
         'mcp_servers.%s.args=%s' % (SERVER, json.dumps(SERVER_ARGS))]
    for k, v in server_env(prep).items():
        o.append('mcp_servers.%s.env.%s=%s' % (SERVER, k, json.dumps(v)))
    # required: exec stops with an error when the server fails to start, so no leks session runs without it.
    # approve: the docs' own example uses it to let one tool run without a prompt
    o += ['mcp_servers.%s.required=true' % SERVER, 'mcp_servers.%s.startup_timeout_sec=30' % SERVER,
          'mcp_servers.%s.default_tools_approval_mode="approve"' % SERVER]
    return o


def claude_argv(a, prompt: str, arm: str, prep: dict) -> list:
    agents = agent_names(arm)
    allowed = CLAUDE_READ + (['mcp__' + SERVER] if arm in LEKS_ARMS else []) + (['Agent'] if agents else [])
    deny = [t for t in CLAUDE_DENY if not (agents and t in CLAUDE_AGENT_TOOLS)]
    argv = ['claude', '-p', prompt, '--output-format', 'stream-json', '--verbose',
            '--setting-sources', 'project', '--strict-mcp-config', '--permission-mode', 'dontAsk',
            '--allowedTools', ','.join(allowed), '--disallowedTools', ','.join(deny),
            '--no-session-persistence', '--model', a.model or 'sonnet']
    if a.effort:
        argv += ['--effort', a.effort]
    if a.max_usd:
        argv += ['--max-budget-usd', '%g' % a.max_usd]
    if arm in LEKS_ARMS:
        argv += ['--mcp-config', prep['claude_mcp']]
    if agents:
        argv += ['--agents', json.dumps(claude_agents(arm, a.small_model or SMALL_PLACEHOLDER))]
    return argv


def codex_argv(a, prompt: str, arm: str, prep: dict, proj: str, sdir: str) -> list:
    argv = ['codex', 'exec', '--json', '--ephemeral', '--skip-git-repo-check', '--ignore-user-config',
            '--ignore-rules', '--sandbox', 'read-only', '--cd', proj, '--model', a.model or MODEL_PLACEHOLDER,
            '-c', 'web_search="disabled"', '-c', 'agents.enabled=%s' % ('true' if agent_names(arm) else 'false')]
    if a.effort:
        argv += ['-c', 'model_reasoning_effort=' + json.dumps(a.effort)]
    if arm in LEKS_ARMS:
        for o in codex_overrides(prep):
            argv += ['-c', o]
    argv += ['--output-last-message', os.path.join(sdir, 'last.txt'), '--', prompt]
    return argv


def copilot_argv(a, prompt: str, arm: str, prep: dict, sdir: str) -> list:
    agents = agent_names(arm)
    exclude = [t for t in COPILOT_EXCLUDE if not (agents and t in COPILOT_AGENT_TOOLS)]
    # --disable-builtin-mcps leaves computer-use on when a user switched it on, so it goes by name
    argv = ['copilot', '-p', prompt, '--output-format=json', '--no-ask-user', '--no-auto-update', '--no-color',
            '--disable-builtin-mcps', '--disable-mcp-server=computer-use',
            '--excluded-tools=' + ','.join(exclude), '--model=' + (a.model or MODEL_PLACEHOLDER)]
    if a.effort:
        argv.append('--effort=' + a.effort)
    if a.max_credits:
        argv.append('--max-ai-credits=%g' % a.max_credits)
    if arm in LEKS_ARMS:
        argv += ['--additional-mcp-config=@' + prep['copilot_mcp'], '--allow-tool=' + SERVER]
    else:
        # a leks server in the user's own copilot config stays out of the plain arm
        argv.append('--disable-mcp-server=' + SERVER)
    if a.usage_file:
        argv.append('--usage-output-file=' + os.path.join(sdir, 'usage.json'))
    return argv


def fake_argv(a, prompt: str, arm: str, prep: dict) -> list:
    return [prep['python'], FAKE_HOST, '--format', a.format, '--arm', arm, '--questions', prep['questions_file'],
            '--model', a.model or 'fake-main', '--small-model', a.small_model or 'fake-small', '--', prompt]


def session_argv(a, prompt: str, arm: str, prep: dict, sdir: str) -> list:
    proj = os.path.join(prep['work'], arm)
    if a.host == 'claude':
        return claude_argv(a, prompt, arm, prep)
    if a.host == 'codex':
        return codex_argv(a, prompt, arm, prep, proj, sdir)
    if a.host == 'copilot':
        return copilot_argv(a, prompt, arm, prep, sdir)
    return fake_argv(a, prompt, arm, prep)


def copilot_home(a) -> str:
    return a.copilot_home or os.environ.get('COPILOT_HOME') or os.path.join(os.path.expanduser('~'), '.copilot')


def copy_copilot_events(sdir: str, home: str):
    """copy the session's own events.jsonl from the cli's session-state folder, when it is there"""
    sid = None
    for o in read_jsonl(os.path.join(sdir, 'stdout.jsonl')):
        d = o.get('data') if isinstance(o.get('data'), dict) else {}
        if o.get('type') == 'session.start' and d.get('sessionId'):
            sid = str(d['sessionId'])
            break
    if not sid or not re.fullmatch(r'[A-Za-z0-9._-]+', sid):
        return {'session_id': sid, 'events_copied': False}
    src = os.path.join(home, 'session-state', sid, 'events.jsonl')
    ok = os.path.isfile(src)
    if ok:
        shutil.copy2(src, os.path.join(sdir, 'events.jsonl'))
    return {'session_id': sid, 'events_copied': ok}


def run_session(argv: list, cwd: str, env: dict, sdir: str, timeout: int):
    """one host session with stdin closed; on a timeout the whole process group goes, mcp servers included"""
    posix = hasattr(os, 'killpg')
    try:
        with open(os.path.join(sdir, 'stdout.jsonl'), 'w', encoding='utf-8') as fo, \
                open(os.path.join(sdir, 'stderr.txt'), 'w', encoding='utf-8') as fe:
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=fo, stderr=fe,
                                 start_new_session=posix)
            try:
                return p.wait(timeout=timeout), False, None
            except subprocess.TimeoutExpired:
                # by name: signal.SIGKILL and os.killpg are posix only, windows gets p.kill() twice
                for sig, grace in (('SIGTERM', 10), ('SIGKILL', 10)):
                    try:
                        if posix:
                            os.killpg(p.pid, getattr(signal, sig))
                        else:
                            p.kill()
                    except OSError:
                        pass
                    try:
                        p.wait(timeout=grace)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                return None, True, None
    except OSError as exc:
        return None, False, str(exc)


def host_version(host: str):
    if host == 'fake':
        return 'fake host'
    exe = shutil.which(host)
    if not exe:
        return None
    try:
        p = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                           env=clean_env({'COPILOT_AUTO_UPDATE': 'false'}))
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (p.stdout or p.stderr or '').strip()
    return text.splitlines()[0] if text else None


def leks_commit():
    git = shutil.which('git')
    if not git:
        return None
    p = subprocess.run([git, '-C', REPO, 'rev-parse', 'HEAD'], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    return p.stdout.strip() or None


def prepare(a, t: dict, out: str, arms) -> dict:
    """project folders, LEKS config, index, server check and the host's MCP config"""
    work = os.path.join(out, 'work')
    prep = {'work': work, 'python': os.path.abspath(a.python), 'profile': t['profile'],
            'config': os.path.join(work, 'leks-config.json'), 'questions_file': t['questions_file']}
    fresh = not os.path.isdir(os.path.join(work, 'corpus'))
    if fresh:
        os.makedirs(work, exist_ok=True)
        make_projects(work, a.host, t['corpus'], arms, a.small_model or SMALL_PLACEHOLDER)
        write_json(prep['config'], leks_config(work, t['config'], t['profile'], a.models_from))
    # no bytecode from these calls lands in the clone
    env = clean_env(dict(server_env(prep), PYTHONDONTWRITEBYTECODE='1'))
    if fresh or not os.path.isdir(os.path.join(work, 'leks-home')):
        with open(os.path.join(work, 'build.txt'), 'w', encoding='utf-8') as f:
            p = subprocess.run([prep['python'], '-m', 'leks', 'build'], cwd=work, env=env, stdin=subprocess.DEVNULL,
                               stdout=f, stderr=subprocess.STDOUT)
        if p.returncode != 0:
            raise SystemExit('leks build failed, see ' + os.path.join(work, 'build.txt'))
    with open(os.path.join(work, 'status.txt'), 'w', encoding='utf-8') as f:
        subprocess.run([prep['python'], '-m', 'leks', 'status'], cwd=work, env=env, stdin=subprocess.DEVNULL,
                       stdout=f, stderr=subprocess.STDOUT)
    prep['server_check'] = check_server(prep['python'], env, os.path.join(work, 'corpus'))
    write_json(os.path.join(work, 'server-check.json'), prep['server_check'])
    if not prep['server_check']['ok']:
        raise SystemExit('the LEKS server did not list its seven tools, see ' + os.path.join(work, 'server-check.json'))
    if a.host in ('claude', 'copilot'):
        key = a.host + '_mcp'
        prep[key] = os.path.join(work, a.host + '-mcp.json')
        write_json(prep[key], mcp_json(prep, a.host))
    prep['parent_instruction_files'] = parent_instruction_files(os.path.join(work, arms[0]))
    return prep


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('host', choices=HOSTS)
    ap.add_argument('--out', help='run folder (default: bench/hosts/runs/HOST-UTC time)')
    ap.add_argument('--tasks', default=os.path.join(HERE, 'tasks.json'), help='task set (default: tasks.json here)')
    ap.add_argument('--ids', help='comma-separated question ids in place of the task set')
    ap.add_argument('--all', action='store_true', help='all questions of the fixtures')
    ap.add_argument('--smoke', action='store_true', help='the small smoke set of the task set, one repeat')
    ap.add_argument('--arms', help='comma-separated arms (default: from the task set: %s)' % ', '.join(ARMS))
    ap.add_argument('--repeats', type=int, help='sessions per question and arm (default: from the task set)')
    ap.add_argument('--model', help='main model as the host names it; claude defaults to sonnet, codex and copilot '
                    'need one (copilot takes auto)')
    ap.add_argument('--small-model', help='model of the search subagent in the subagent arm; claude defaults to '
                    'haiku, codex and copilot need one when that arm runs')
    ap.add_argument('--effort', help='reasoning effort as the host names it; claude defaults to medium, the others to '
                    'the host default')
    ap.add_argument('--format', choices=FORMATS, default='claude', help='fake host only: whose event stream it prints')
    ap.add_argument('--timeout', type=int, help='seconds per session (default: from the task set)')
    ap.add_argument('--max-usd', type=float, default=1.0, help='claude: --max-budget-usd per session (default 1)')
    ap.add_argument('--max-credits', type=float, help='copilot: --max-ai-credits, a soft cap per response')
    ap.add_argument('--copilot-home', help='copilot: run with COPILOT_HOME set to this folder')
    ap.add_argument('--usage-file', action='store_true', help='copilot: also keep --usage-output-file per session')
    ap.add_argument('--rates', help='codex: USD per 1M input, cached input and output tokens, as IN,CACHED,OUT, '
                    'so result.json carries a USD figure')
    ap.add_argument('--python', default=sys.executable, help='interpreter for the build and the server')
    ap.add_argument('--models-from', help='LEKS config whose models block the index uses (default: words only)')
    ap.add_argument('--dry-run', action='store_true', help='prepare and check everything, write plan.json, run no session')
    ap.add_argument('--resume', action='store_true', help='continue a run folder, skipping sessions with a meta.json')
    return ap


def session_result(fmt: str, sdir: str, meta: dict, question: dict, rates=None) -> dict:
    """what one session cost and whether its task check passed, from the host's own event stream"""
    rec = SUMMARISE.parse_session(fmt, sdir, meta, rates)
    rec['check_passed'] = SUMMARISE.cites(rec['answer'], question.get('gold') or [])
    rec['cites_stale'] = SUMMARISE.cites(rec['answer'], question.get('stale') or [])
    keep = ('ok', 'check_passed', 'cites_stale', 'cost', 'usd', 'credits', 'premium_requests', 'input_tokens',
            'cached_tokens', 'cache_write_tokens', 'output_tokens', 'reasoning_tokens', 'turns', 'model_calls',
            'tool_calls', 'leks_calls', 'subagent_calls', 'shell_calls', 'models', 'errors', 'peeks', 'wall_s',
            'exit_code', 'timed_out')
    out = {k: rec.get(k) for k in keep}
    out.update({'session': meta.get('session'), 'question': meta.get('question'), 'arm': meta.get('arm'),
                'repeat': meta.get('repeat'), 'unit': SUMMARISE.unit_of(fmt, priced=bool(rates)),
                'answer_chars': len(rec['answer'] or '')})
    return out


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    t = load_tasks(a.tasks, a.all, [i.strip() for i in a.ids.split(',') if i.strip()] if a.ids else None, a.smoke)
    arms = parse_arms(a.arms) if a.arms else t['arms']
    repeats = a.repeats or t['repeats']
    timeout = a.timeout or t['timeout_s']
    if repeats < 1:
        ap.error('--repeats must be at least 1')
    rates = SUMMARISE.parse_rates(a.rates) if a.rates else None
    if rates and a.host not in ('codex', 'fake'):
        ap.error('--rates prices codex tokens; %s reports its own cost' % a.host)
    fmt = a.format if a.host == 'fake' else a.host
    if a.host == 'claude':
        # the same defaults as bench/integrations; haiku is the small model of the report's section 4.14
        a.model = a.model or 'sonnet'
        a.effort = a.effort or 'medium'
        a.small_model = a.small_model or 'haiku'
    if not a.dry_run and a.host != 'fake':
        if not a.model:
            ap.error('%s needs --model, so the run does not depend on a changing default' % a.host)
        if 'subagent' in arms and not a.small_model:
            ap.error('the subagent arm needs --small-model on %s' % a.host)
        if not shutil.which(a.host):
            ap.error('%s is not on PATH' % a.host)
    if a.copilot_home:
        a.copilot_home = os.path.abspath(a.copilot_home)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')
    out = os.path.abspath(a.out or os.path.join(HERE, 'runs', '%s-%s' % (a.host, stamp)))
    settings = {'host': a.host, 'format': fmt, 'arms': arms, 'ids': [q['id'] for q in t['questions']],
                'repeats': repeats, 'model': a.model, 'small_model': a.small_model, 'effort': a.effort,
                'max_usd': a.max_usd if a.host == 'claude' else None,
                'max_credits': a.max_credits if a.host == 'copilot' else None, 'rates': list(rates) if rates else None,
                'models_from': os.path.abspath(a.models_from) if a.models_from else None,
                'prompt': t['prompt'], 'profile': t['profile']}
    run_path = os.path.join(out, 'run.json')
    first_start = None
    if a.resume:
        if not os.path.isfile(run_path):
            ap.error('nothing to resume in %s' % out)
        with open(run_path, encoding='utf-8') as f:
            old = json.load(f)
        if old.get('settings') != settings:
            ap.error('--resume needs the same host, arms, questions, repeats, models, effort and caps as the first '
                     'start')
        first_start = old.get('first_started_utc') or old.get('started_utc')
    elif os.path.isdir(out) and os.listdir(out):
        ap.error('%s is not empty; pick a new --out, or pass --resume to continue a run there' % out)
    os.makedirs(os.path.join(out, 'sessions'), exist_ok=True)

    prep = prepare(a, t, out, arms)
    plan = schedule(t['questions'], repeats, arms)
    entries = []
    for r, q, arm in plan:
        name = session_name(q['id'], r, arm)
        sdir = os.path.join(out, 'sessions', name)
        argv_ = session_argv(a, t['prompt'].format(question=q['question']), arm, prep, sdir)
        entries.append({'session': name, 'question': q['id'], 'repeat': r, 'arm': arm, 'argv': argv_,
                        'cwd': os.path.join(prep['work'], arm)})
    write_json(os.path.join(out, 'plan.json'), entries)
    now = utc_now()
    run = {'settings': settings, 'dry_run': a.dry_run, 'first_started_utc': first_start or now, 'started_utc': now,
           'host_version': host_version(a.host),
           'leks_commit': leks_commit(), 'python': platform.python_version(), 'platform': platform.platform(),
           'timeout_s': timeout, 'sessions_planned': len(entries), 'work': prep['work'],
           'questions_file': t['questions_file'],
           'server_check': prep['server_check'], 'parent_instruction_files': prep['parent_instruction_files'],
           'copilot_home': a.copilot_home if a.host == 'copilot' else None}
    write_json(run_path, run)
    for p in prep['parent_instruction_files']:
        print('warning: %s sits above the projects and may reach every arm' % p, file=sys.stderr)
    if a.dry_run:
        print('dry run: %d sessions planned in %s (arms: %s)' % (len(entries), out, ', '.join(arms)))
        shown = set()
        for e in entries:
            if e['arm'] in shown:
                continue
            shown.add(e['arm'])
            print('%s (in %s):' % (e['session'], e['arm']))
            print('  ' + shlex.join(e['argv']))
        return 0

    questions = {q['id']: q for q in t['questions']}
    env_extra = {'COPILOT_HOME': a.copilot_home} if a.host == 'copilot' and a.copilot_home else {}
    finished = not_ok = 0
    with open(os.path.join(out, 'results.jsonl'), 'a', encoding='utf-8') as results:
        for n, e in enumerate(entries, 1):
            sdir = os.path.join(out, 'sessions', e['session'])
            if a.resume and os.path.isfile(os.path.join(sdir, 'meta.json')):
                continue
            os.makedirs(sdir, exist_ok=True)
            meta = {'session': e['session'], 'host': a.host, 'format': fmt, 'question': e['question'],
                    'repeat': e['repeat'], 'arm': e['arm'], 'argv': e['argv'], 'cwd': e['cwd'],
                    'started_utc': utc_now()}
            t0 = time.monotonic()
            code, timed_out, error = run_session(e['argv'], e['cwd'], clean_env(env_extra), sdir, timeout)
            meta.update({'ended_utc': utc_now(), 'wall_s': round(time.monotonic() - t0, 2), 'exit_code': code,
                         'timed_out': timed_out, 'error': error})
            if a.host == 'copilot':
                meta['copilot'] = copy_copilot_events(sdir, copilot_home(a))
            write_json(os.path.join(sdir, 'meta.json'), meta)
            res = session_result(fmt, sdir, meta, questions[e['question']], rates)
            write_json(os.path.join(sdir, 'result.json'), res)
            results.write(json.dumps(res) + '\n')
            results.flush()
            finished += 1
            not_ok += not res['ok']
            print('[%d/%d] %s exit %s %.1f s, cost %s %s, check %s' % (
                n, len(entries), e['session'], 'timeout' if timed_out else code, meta['wall_s'],
                '-' if res['cost'] is None else '%.4g' % res['cost'], res['unit'] or '(unpriced tokens)',
                {True: 'passed', False: 'failed', None: 'n/a'}[res['check_passed']]), flush=True)
    run['ended_utc'] = utc_now()
    run['sessions_run_this_start'] = finished
    write_json(run_path, run)
    print('done: %s; summarise with python3 bench/hosts/summarise.py %s' % (out, shlex.quote(out)))
    if not_ok:
        # a failed, timed-out or empty session is in the results, but the run did not go through
        print('%d of %d sessions run now did not end ok (failed, timed out or no answer)' % (not_ok, finished),
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
