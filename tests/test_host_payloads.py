"""The hook commands fed the JSON payloads the hosts send on stdin.

Commands as examples/project-template wires them up: `leks hook brief` and
`leks hook update` on session start, `leks hook warm` before a search tool call,
`leks check` when the agent stops. Payload shapes, from the host documentation:

- Claude Code: https://code.claude.com/docs/en/hooks
  common fields session_id, transcript_path, cwd, permission_mode, hook_event_name;
  SessionStart adds source and model, PreToolUse adds tool_name, tool_input,
  tool_use_id (and mcp_server for an MCP tool), Stop adds stop_hook_active and
  last_assistant_message. CLAUDE_PROJECT_DIR is set for every hook command.
- GitHub Copilot hooks reference: https://docs.github.com/en/copilot/reference/hooks-reference
  camelCase sessionStart {sessionId, timestamp (ms), cwd, source, initialPrompt},
  preToolUse {sessionId, timestamp, cwd, toolName, toolArgs},
  agentStop {sessionId, timestamp, cwd, transcriptPath, stopReason, stop_hook_active},
  and the VS Code compatible PascalCase forms with snake_case fields.
- VS Code: https://code.visualstudio.com/docs/copilot/customization/hooks
  a command hook reads the event as JSON on stdin and may print one JSON object.
- Codex hooks: https://learn.chatgpt.com/docs/hooks (read 2026-11-13)
  common fields session_id, transcript_path, cwd, hook_event_name, model; SessionStart,
  PreToolUse and Stop add permission_mode, SessionStart adds source, Stop adds turn_id,
  stop_hook_active and last_assistant_message. A command runs in the session's cwd.
  PreToolUse adds turn_id, tool_name, tool_use_id and tool_input, and its matcher is a regex
  on the tool name, mcp__<server>__<tool> for an MCP tool.
  SessionStart takes plain text or hookSpecificOutput.additionalContext; a Stop hook
  continues the turn with exit 2 and the reason on stderr, and prints JSON or nothing
  on stdout when it exits 0.

Every command must exit 0 (check: 0 or 2), finish inside the timeout the template
gives it, and print on stdout nothing or exactly one JSON object. A stop with
problems exits 2 for Claude Code and Codex; for the Copilot forms, where exit 2 from a stop
hook is only a warning, it prints {"decision": "block", "reason": ...} and exits 0.

HostConfigTests check the config files of examples/project-template against the
formats those pages and these ones document:
- Claude Code MCP config (.mcp.json, key mcpServers): https://code.claude.com/docs/en/mcp
- VS Code MCP config (.vscode/mcp.json, key servers):
  https://code.visualstudio.com/docs/agents/reference/mcp-configuration
- Codex MCP config ([mcp_servers.<name>] in .codex/config.toml):
  https://learn.chatgpt.com/docs/extend/mcp (developers.openai.com/codex/mcp redirects there)
- Codex hooks (.codex/hooks.json, {"hooks": {Event: [{"matcher"?, "hooks": [handler]}]}}, a command
  handler with type, command, commandWindows, timeout, statusMessage, async):
  https://learn.chatgpt.com/docs/hooks
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

try:
    import tomllib
except ImportError:            # python 3.10
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = 'main'
# timeouts from examples/project-template (.claude/settings.json and .github/hooks/leks.json)
T_START = 10.0
T_CHECK = 20.0

NOTES = {
    'STATE.md': '# State\n\nThe tile cache is in review. See [[design]] and [[release]].\n',
    'design.md': '# Design\n\nThe tile cache keeps tiles of 256 pixels. Back to [[STATE]].\n',
    'release.md': '# Release checklist\n\nRun the render tests before tagging. See [[design]].\n',
}
BAD_NOTE = ('broken.md', '# Broken\n\nThis links to [[no-such-note]].\n')

ISO = '2026-11-13T10:00:00.000Z'
MS = 1763028000000


def claude_code(event, cwd, transcript, **extra):
    d = {'session_id': 'abc123', 'transcript_path': transcript, 'cwd': cwd,
         'permission_mode': 'default', 'hook_event_name': event}
    d.update(extra)
    return d


def codex(event, cwd, transcript, **extra):
    d = {'session_id': 'c0dex-1', 'transcript_path': transcript, 'cwd': cwd, 'hook_event_name': event,
         'model': 'example-model', 'permission_mode': 'default'}
    d.update(extra)
    return d


def payloads(cwd, transcript):
    """{host form: {event: payload}} for one project folder."""
    return {
        'codex': {
            'start': codex('SessionStart', cwd, transcript, source='startup'),
            'tool': codex('PreToolUse', cwd, transcript, turn_id='turn-1', tool_name='mcp__leks__search',
                          tool_use_id='call_1', tool_input={'query': 'tile cache'}),
            'stop': codex('Stop', cwd, transcript, turn_id='turn-1', stop_hook_active=False,
                          last_assistant_message='Done.'),
        },
        'claude-code': {
            'start': claude_code('SessionStart', cwd, transcript, source='startup', model='example-model'),
            'tool': claude_code('PreToolUse', cwd, transcript, tool_name='mcp__leks__search',
                                tool_input={'query': 'tile cache'}, tool_use_id='toolu_01ABC123',
                                mcp_server={'name': 'leks', 'source': 'project'}),
            'stop': claude_code('Stop', cwd, transcript, stop_hook_active=False, last_assistant_message='Done.'),
        },
        'copilot': {
            'start': {'sessionId': 's-1', 'timestamp': MS, 'cwd': cwd, 'source': 'new',
                      'initialPrompt': 'update the release notes'},
            'tool': {'sessionId': 's-1', 'timestamp': MS, 'cwd': cwd, 'toolName': 'search',
                     'toolArgs': {'query': 'tile cache'}},
            'stop': {'sessionId': 's-1', 'timestamp': MS, 'cwd': cwd, 'transcriptPath': transcript,
                     'stopReason': 'end_turn', 'stop_hook_active': False},
        },
        'vscode-compatible': {
            'start': {'hook_event_name': 'SessionStart', 'session_id': 's-2', 'timestamp': ISO, 'cwd': cwd,
                      'source': 'startup'},
            'tool': {'hook_event_name': 'PreToolUse', 'session_id': 's-2', 'timestamp': ISO, 'cwd': cwd,
                     'tool_name': 'search', 'tool_input': {'query': 'tile cache'}},
            'stop': {'hook_event_name': 'Stop', 'session_id': 's-2', 'timestamp': ISO, 'cwd': cwd,
                     'transcript_path': transcript, 'stop_reason': 'end_turn', 'stop_hook_active': False},
        },
    }


def held(stop):
    d = dict(stop)
    d['stop_hook_active'] = True
    return d


class HostPayloadTests(unittest.TestCase):

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix='lekshook'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.user = os.path.join(self.dir, 'user')
        self.home = os.path.join(self.dir, 'leks-home')
        os.makedirs(os.path.join(self.user, '.leks'))
        self.transcript = os.path.join(self.dir, 'transcript.jsonl')
        with open(self.transcript, 'w', encoding='utf-8') as f:
            f.write('{}\n')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('LEKS_') and k != 'CLAUDE_PROJECT_DIR'}
        # the hosts set only LEKS_PROFILE; the config sits where leks init puts it, in the user's ~/.leks
        self.env.update({'LEKS_PROFILE': PROFILE, 'PYTHONPATH': ROOT, 'HOME': self.user, 'USERPROFILE': self.user,
                         'PYTHONDONTWRITEBYTECODE': '1'})

    def project(self, name, build=True):
        """A project folder with notes, the user config pointing at it, and (by default) its index."""
        proj = os.path.join(self.dir, name)
        notes = os.path.join(proj, 'notes')
        os.makedirs(notes)
        os.makedirs(os.path.join(proj, 'sub'))
        for n, text in NOTES.items():
            self.write(os.path.join(notes, n), text)
        cfg = {
            'version': 3, 'home': self.home,
            'roots': [{'name': 'notes', 'path': notes, 'kind': 'notes'}],
            'profiles': {PROFILE: {'roots': ['notes']}}, 'default_profile': PROFILE,
            'models': {'backend': 'torch', 'device': 'auto', 'weights_dir': None, 'embedder': None, 'reranker': None},
            'ocr': {'enabled': 'off', 'images': False, 'helper': None, 'max_pages': 40},
        }
        self.write(os.path.join(self.user, '.leks', 'config.json'), json.dumps(cfg))
        if build:
            r = self.leks(['build'], cwd=proj, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
        return proj

    @staticmethod
    def write(path, text):
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)

    def leks(self, args, payload=None, cwd=None, env=None, timeout=60):
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, '-m', 'leks'] + args, cwd=cwd or self.dir, env=env or self.env,
                           input=json.dumps(payload).encode('utf-8') if payload is not None else b'',
                           capture_output=True, timeout=timeout)
        r.secs = time.monotonic() - t0
        r.out = r.stdout.decode('utf-8')
        r.err = r.stderr.decode('utf-8', 'replace')
        return r

    def one_json(self, out, what):
        """stdout must be empty or one JSON object (Copilot parses it with a single JSON.parse)."""
        text = out.strip()
        if not text:
            return None
        self.assertEqual(len(text.splitlines()), 1, '%s printed more than one line: %r' % (what, out[:300]))
        d = json.loads(text)
        self.assertIsInstance(d, dict, what)
        return d

    def test_brief_on_session_start(self):
        proj = self.project('p')
        for host, ev in payloads(proj, self.transcript).items():
            r = self.leks(['hook', 'brief'], ev['start'], cwd=proj)
            self.assertEqual(r.returncode, 0, host)
            self.assertLess(r.secs, T_START, host)
            self.assertEqual(r.err, '', host)
            d = self.one_json(r.out, host)
            self.assertIsNotNone(d, '%s: the brief is empty although an index exists' % host)
            # the Claude Code SessionStart output shape
            self.assertEqual(set(d), {'hookSpecificOutput'}, host)
            spec = d['hookSpecificOutput']
            self.assertEqual(spec.get('hookEventName'), 'SessionStart', host)
            self.assertIsInstance(spec.get('additionalContext'), str, host)
            self.assertIn('STATE.md', spec['additionalContext'], host)

    def test_brief_without_an_index_prints_nothing(self):
        proj = self.project('p', build=False)
        for host, ev in payloads(proj, self.transcript).items():
            r = self.leks(['hook', 'brief'], ev['start'], cwd=proj)
            self.assertEqual((r.returncode, r.out, r.err), (0, '', ''), host)

    def test_hooks_without_a_config_stay_quiet(self):
        proj = os.path.join(self.dir, 'bare')
        os.makedirs(proj)
        ev = payloads(proj, self.transcript)['claude-code']
        for args, p in ((['hook', 'brief'], ev['start']), (['hook', 'update'], ev['start']),
                        (['hook', 'warm'], ev['tool'])):
            r = self.leks(args, p, cwd=proj)
            self.assertEqual((r.returncode, r.out), (0, ''), args)
            self.assertLess(r.secs, T_START, args)

    def test_warm_on_pre_tool_use(self):
        proj = self.project('p')
        for host, ev in payloads(proj, self.transcript).items():
            if 'tool' not in ev:
                continue
            r = self.leks(['hook', 'warm'], ev['tool'], cwd=proj)
            self.assertEqual((r.returncode, r.out, r.err), (0, '', ''), host)
            self.assertLess(r.secs, T_START, host)

    def test_update_on_session_start(self):
        proj = self.project('p')
        ev = payloads(proj, self.transcript)['claude-code']['start']
        r = self.leks(['hook', 'update'], ev, cwd=proj)
        self.assertEqual((r.returncode, r.out, r.err), (0, '', ''))
        self.assertLess(r.secs, T_START)
        # the update runs on in a detached child; its log says when it is through
        log = os.path.join(self.home, 'run', 'update.log')
        end = time.monotonic() + 90
        text = ''
        while time.monotonic() < end:
            try:
                with open(log, encoding='utf-8') as f:
                    text = f.read()
            except OSError:
                text = ''
            if 'update end' in text:
                break
            time.sleep(0.2)
        self.assertIn('update end', text)
        self.assertNotIn('error:', text)
        self.assertTrue(os.path.isfile(os.path.join(self.home, 'run', 'update.stamp')))
        # a second session start inside update.min_interval does nothing and still exits 0
        r = self.leks(['hook', 'update'], ev, cwd=proj)
        self.assertEqual((r.returncode, r.out), (0, ''))

    def _check_round(self, host, proj, stop, cwd, env):
        """First run records, a dead link is reported, a held stop passes and keeps the record.

        Claude Code and Codex get exit 2 and the list on stderr. The Copilot forms get exit 0 and a block
        decision on stdout, because Copilot only warns on exit 2 from a stop hook and lets the turn end.
        """
        r = self.leks(['check'], stop, cwd=cwd, env=env)
        self.assertEqual((r.returncode, r.out, r.err), (0, '', ''), '%s first run: %s' % (host, r.err))
        self.write(os.path.join(proj, 'notes', BAD_NOTE[0]), BAD_NOTE[1])
        r = self.leks(['check'], held(stop), cwd=cwd, env=env)
        self.assertEqual((r.returncode, r.out, r.err), (0, '', ''), '%s held stop' % host)
        r = self.leks(['check'], stop, cwd=cwd, env=env)
        self.assertLess(r.secs, T_CHECK, host)
        if host in ('claude-code', 'codex'):
            self.assertEqual(r.returncode, 2, '%s: %s' % (host, r.err))
            self.assertEqual(r.out, '', host)
            said = r.err
        else:
            self.assertEqual((r.returncode, r.err), (0, ''), host)
            d = self.one_json(r.out, host)
            self.assertEqual(set(d), {'decision', 'reason'}, host)
            self.assertEqual(d['decision'], 'block', host)
            said = d['reason']
        self.assertTrue(said.startswith('leks check: '), host)
        self.assertIn(BAD_NOTE[0], said, host)
        self.assertIn('no-such-note', said, host)

    def test_check_on_claude_code_stop(self):
        proj = self.project('cc', build=False)
        stop = payloads(proj, self.transcript)['claude-code']['stop']
        env = dict(self.env, CLAUDE_PROJECT_DIR=proj)
        # claude code runs the hook in the session's current folder, a cd moves it; the project folder stays
        self._check_round('claude-code', proj, stop, os.path.join(proj, 'sub'), env)

    def test_check_on_copilot_agent_stop(self):
        proj = self.project('cp', build=False)
        stop = payloads(proj, self.transcript)['copilot']['stop']
        # the template runs it with "cwd": "." from the repository root
        self._check_round('copilot', proj, stop, proj, self.env)

    def test_check_on_vscode_compatible_stop(self):
        proj = self.project('vs', build=False)
        stop = payloads(proj, self.transcript)['vscode-compatible']['stop']
        self._check_round('vscode-compatible', proj, stop, proj, self.env)

    def test_check_on_codex_stop(self):
        proj = self.project('cx', build=False)
        stop = payloads(proj, self.transcript)['codex']['stop']
        # codex runs a hook command in the session's cwd, the folder codex was started in
        self._check_round('codex', proj, stop, proj, self.env)


TEMPLATE = os.path.join(ROOT, 'examples', 'project-template')


def jsonc(text):
    """JSON with comments and trailing commas (VS Code settings and mcp.json) as plain JSON data."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == '\\' else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith('//', i):
            while i < n and text[i] != '\n':
                i += 1
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r',(\s*[}\]])', r'\1', ''.join(out)))


def load(rel):
    with open(os.path.join(TEMPLATE, rel), encoding='utf-8') as f:
        text = f.read()
    return jsonc(text) if rel.startswith('.vscode') else json.loads(text)


def leks_argv(command):
    """The leks arguments of a hook command such as 'python3 -m leks hook brief', else None."""
    words = shlex.split(command)
    if len(words) >= 3 and words[1:3] == ['-m', 'leks'] and re.fullmatch(r'python(3(\.\d+)?)?(\.exe)?', words[0]):
        return words[3:]
    return None


def strip_globals(argv):
    """argv without --profile P and --config PATH, which leks takes anywhere on the line (as _pull_globals
    does, without setting the environment of this test process)."""
    rest, i = [], 0
    while i < len(argv):
        if argv[i] in ('--profile', '--config') and i + 1 < len(argv):
            i += 2
            continue
        if argv[i].startswith(('--profile=', '--config=')):
            i += 1
            continue
        rest.append(argv[i])
        i += 1
    return rest


def stdio_problems(name, e, kinds=('stdio',)):
    """What breaks the documented shape of one stdio MCP server entry."""
    bad = []
    if 'type' in e and e['type'] not in kinds:
        bad.append('%s: type %r' % (name, e['type']))
    if not isinstance(e.get('command'), str) or not e['command']:
        bad.append('%s: command must be text' % name)
    if 'url' in e:
        bad.append('%s: a stdio server has no url' % name)
    a = e.get('args', [])
    if not isinstance(a, list) or not all(isinstance(x, str) for x in a):
        bad.append('%s: args must be a list of text' % name)
    env = e.get('env', {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        bad.append('%s: env must map text to text' % name)
    return bad


CODEX_EVENTS = {'PreToolUse', 'PermissionRequest', 'PostToolUse', 'PreCompact', 'PostCompact', 'UserPromptSubmit',
                'SubagentStop', 'Stop', 'PreModelRequest', 'PostModelRequest', 'Interrupt', 'SessionStart',
                'SubagentStart', 'SessionEnd'}


def codex_hook_problems(doc):
    """A Codex hooks.json against the documented shape: events, matcher groups, command handlers."""
    if not isinstance(doc, dict) or not isinstance(doc.get('hooks'), dict) or not doc['hooks']:
        return ['no hooks object']
    bad = ['unknown top-level key %s' % k for k in doc if k not in ('hooks', 'description')]
    for ev, groups in doc['hooks'].items():
        if ev not in CODEX_EVENTS:
            bad.append('unknown event %s' % ev)
        if not isinstance(groups, list) or not groups:
            bad.append('%s: not a list of matcher groups' % ev)
            continue
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get('hooks'), list) or not g['hooks']:
                bad.append('%s: a group needs a hooks list' % ev)
                continue
            bad += ['%s: unknown group key %s' % (ev, k) for k in g if k not in ('matcher', 'hooks')]
            for h in g['hooks']:
                if not isinstance(h, dict) or h.get('type') != 'command':
                    bad.append('%s: not a command handler' % ev)
                    continue
                bad += ['%s: unknown handler key %s' % (ev, k) for k in h if k not in
                        ('type', 'command', 'commandWindows', 'timeout', 'statusMessage', 'additionalContextLimit',
                         'async')]
                if not isinstance(h.get('command'), str) or not h['command']:
                    bad.append('%s: command must be text' % ev)
                t = h.get('timeout', 600)
                if not isinstance(t, (int, float)) or isinstance(t, bool) or t <= 0:
                    bad.append('%s: timeout must be a positive number of seconds' % ev)
    return bad


def codex_problems(doc):
    """[mcp_servers.<name>] tables of a Codex config.toml, against the documented stdio fields."""
    known = {'command': str, 'args': list, 'env': dict, 'cwd': str, 'startup_timeout_sec': (int, float),
             'tool_timeout_sec': (int, float), 'enabled': bool, 'enabled_tools': list, 'disabled_tools': list,
             'env_vars': list}
    bad = []
    servers = doc.get('mcp_servers')
    if not isinstance(servers, dict) or not servers:
        return ['no [mcp_servers.<name>] table']
    for name, e in servers.items():
        if not isinstance(e, dict):
            bad.append('%s: not a table' % name)
            continue
        for k, v in e.items():
            if k not in known:
                bad.append('%s: unknown key %s' % (name, k))
            elif not isinstance(v, known[k]) or (known[k] == (int, float) and isinstance(v, bool)):
                bad.append('%s: %s has the wrong type' % (name, k))
        bad += [b for b in stdio_problems(name, e) if 'type' not in b]
    return bad


class HostConfigTests(unittest.TestCase):
    """The template's host files have the documented shape and run commands leks knows."""

    @classmethod
    def setUpClass(cls):
        from leks import __main__ as cli
        from leks import server
        cls.parser = cli._parser()
        cls.tools = [t['name'] for t in server.TOOLS]

    def assert_leks_command(self, command, where):
        argv = leks_argv(command)
        self.assertIsNotNone(argv, '%s: %r does not run python -m leks' % (where, command))
        try:
            self.parser.parse_args(strip_globals(argv))
        except SystemExit:
            self.fail('%s: leks does not know %r' % (where, command))

    def test_claude_code_mcp_json(self):
        d = load('.mcp.json')
        self.assertEqual(set(d), {'mcpServers'})
        self.assertIn('leks', d['mcpServers'])
        for name, e in d['mcpServers'].items():
            self.assertEqual(stdio_problems(name, e), [])
            self.assertEqual(e['args'][-3:], ['-m', 'leks', 'serve'])

    def test_vscode_mcp_json(self):
        d = load('.vscode/mcp.json')
        self.assertLessEqual(set(d), {'servers', 'inputs', 'sandbox'})
        for name, e in d['servers'].items():
            # vs code wants the type; env values may also be numbers or null there
            self.assertEqual(e.get('type'), 'stdio', name)
            self.assertLessEqual(set(e), {'type', 'command', 'args', 'env', 'envFile', 'cwd', 'dev'}, name)
            env = e.pop('env', {})
            self.assertTrue(all(v is None or isinstance(v, (str, int, float)) for v in env.values()), name)
            self.assertEqual(stdio_problems(name, e), [])

    def test_vscode_settings_are_jsonc(self):
        d = load('.vscode/settings.json')
        self.assertIsInstance(d, dict)
        if 'chat.useClaudeHooks' in d:
            self.assertIsInstance(d['chat.useClaudeHooks'], bool)

    def test_claude_code_hooks(self):
        d = load('.claude/settings.json')
        events = {'SessionStart', 'SessionEnd', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Notification',
                  'Stop', 'SubagentStop', 'PreCompact'}
        self.assertLessEqual(set(d['hooks']), events)
        server_name = next(iter(load('.mcp.json')['mcpServers']))
        for ev, groups in d['hooks'].items():
            self.assertIsInstance(groups, list, ev)
            for g in groups:
                self.assertLessEqual(set(g), {'matcher', 'hooks'}, ev)
                if 'matcher' in g:
                    rx = re.compile(g['matcher'])
                    # the warm hook has to fire for leks tools, under the name the host gives them
                    hit = [t for t in self.tools if rx.fullmatch('mcp__%s__%s' % (server_name, t))]
                    self.assertTrue(hit, '%s matcher %r names no leks tool' % (ev, g['matcher']))
                for h in g['hooks']:
                    self.assertEqual(h.get('type'), 'command', ev)
                    self.assertIsInstance(h.get('timeout'), (int, float), ev)
                    self.assertGreater(h['timeout'], 0, ev)
                    self.assert_leks_command(h['command'], ev)
        self.assertEqual(d['hooks']['Stop'][0]['hooks'][0]['timeout'], T_CHECK)

    def test_copilot_hooks(self):
        d = load('.github/hooks/leks.json')
        self.assertEqual(d.get('version'), 1)
        self.assertLessEqual(set(d), {'version', 'hooks', 'disableAllHooks'})
        events = {'sessionStart', 'sessionEnd', 'userPromptSubmitted', 'preToolUse', 'postToolUse',
                  'permissionRequest', 'agentStop', 'subagentStop', 'errorOccurred'}
        self.assertLessEqual(set(d['hooks']), events)
        for ev, entries in d['hooks'].items():
            for h in entries:
                self.assertEqual(h.get('type', 'command'), 'command', ev)
                cmds = [h[k] for k in ('bash', 'powershell', 'command') if k in h]
                self.assertTrue(cmds, '%s: one of bash, powershell or command is required' % ev)
                for c in cmds:
                    self.assertIsInstance(c, str, ev)
                    self.assert_leks_command(c, ev)
                t = h.get('timeoutSec', h.get('timeout', 30))
                self.assertIsInstance(t, (int, float), ev)
                self.assertGreater(t, 0, ev)
                self.assertTrue(all(isinstance(v, str) for v in h.get('env', {}).values()), ev)
        self.assertEqual(d['hooks']['agentStop'][0]['timeoutSec'], T_CHECK)

    def test_agents_md_is_what_claude_md_pulls_in(self):
        with open(os.path.join(TEMPLATE, 'AGENTS.md'), encoding='utf-8') as f:
            agents = f.read()
        with open(os.path.join(TEMPLATE, 'CLAUDE.md'), encoding='utf-8') as f:
            claude = f.read()
        self.assertTrue(agents.strip())
        self.assertIn('@AGENTS.md', claude)

    @unittest.skipIf(tomllib is None, 'no TOML reader (tomllib or tomli) in this Python')
    def test_codex_config_toml(self):
        # the shape the template uses, plus what the checker must reject
        good = tomllib.loads('[mcp_servers.leks]\ncommand = "python3"\nargs = ["-m", "leks", "serve"]\n'
                             'tool_timeout_sec = 120\n[mcp_servers.leks.env]\nLEKS_PROFILE = "architect"\n')
        self.assertEqual(codex_problems(good), [])
        for bad in ('[mcp_servers.leks]\nargs = ["serve"]\n', '[mcp_servers.leks]\ncommand = "x"\nargs = "serve"\n',
                    '[mcp_servers.leks]\ncommand = "x"\ntool_timeout_sec = true\n',
                    '[mcp_servers.leks]\ncommand = "x"\nurl = "http://h"\n', '[other]\nx = 1\n'):
            self.assertNotEqual(codex_problems(tomllib.loads(bad)), [], bad)
        path = os.path.join(TEMPLATE, '.codex', 'config.toml')
        if not os.path.exists(path):
            self.skipTest('the template has no .codex/config.toml')
        with open(path, 'rb') as f:
            doc = tomllib.load(f)
        self.assertEqual(codex_problems(doc), [])
        for name, e in doc['mcp_servers'].items():
            self.assertEqual(e['args'][-3:], ['-m', 'leks', 'serve'], name)

    def test_codex_hooks(self):
        d = load('.codex/hooks.json')
        self.assertEqual(codex_hook_problems(d), [])

        def shape(hooks, strip):
            # event -> [(matcher, [(leks arguments, timeout)])]
            return {ev: [(g.get('matcher'), [(' '.join(strip(leks_argv(x['command']) or [x['command']])),
                                              x.get('timeout')) for x in g['hooks']]) for g in groups]
                    for ev, groups in hooks.items()}

        # the events, matchers, commands and timeouts of the claude code hooks; codex names an mcp tool
        # mcp__<server>__<tool> for a matcher, as claude code does, and reads the matcher as a regex
        cc = load('.claude/settings.json')
        self.assertEqual(shape(d['hooks'], strip_globals), shape(cc['hooks'], list))
        # a codex hook handler has no env of its own, so the profile rides on each command line
        profiles = set()
        for ev, groups in d['hooks'].items():
            for g in groups:
                for x in g['hooks']:
                    self.assert_leks_command(x['command'], ev)
                    a = leks_argv(x['command'])
                    self.assertIn('--profile', a, ev)
                    profiles.add(a[a.index('--profile') + 1])
        self.assertEqual(profiles, {cc['env']['LEKS_PROFILE']})
        if tomllib is not None:
            with open(os.path.join(TEMPLATE, '.codex', 'config.toml'), 'rb') as f:
                self.assertEqual(profiles, {tomllib.load(f)['mcp_servers']['leks']['env']['LEKS_PROFILE']})

    def test_checkers_reject_broken_shapes(self):
        good = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'python3 -m leks check'}]}]}}
        self.assertEqual(codex_hook_problems(good), [])
        for bad in ({'Stop': []}, {'hooks': {'stop': good['hooks']['Stop']}},
                    {'hooks': {'Stop': [{'type': 'command', 'command': 'x'}]}},
                    {'hooks': {'Stop': [{'hooks': [{'type': 'prompt', 'command': 'x'}]}]}},
                    {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'x', 'timeout': 0}]}]}},
                    {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'x', 'env': {}}]}]}}):
            self.assertTrue(codex_hook_problems(bad), bad)
        self.assertTrue(stdio_problems('x', {'type': 'http', 'command': 'p'}))
        self.assertTrue(stdio_problems('x', {'command': ['p']}))
        self.assertTrue(stdio_problems('x', {'command': 'p', 'env': {'A': 1}}))
        self.assertIsNone(leks_argv('node server.js'))
        self.assertEqual(leks_argv('python3 -m leks hook warm'), ['hook', 'warm'])
        self.assertEqual(jsonc('{"a": "x // y", /* c */ "b": [1,],}\n// end'), {'a': 'x // y', 'b': [1]})


if __name__ == '__main__':
    unittest.main()
