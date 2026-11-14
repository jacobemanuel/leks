"""The agent kit: the skills and files of examples/project-template, the agent team in examples/agent-team for
Claude Code, Copilot and Codex, the decision log, and docs/agents.md, docs/skills.md and docs/hosts.md.

Each file is checked against the fields its host documents (the sources are cited in docs/agents.md and
docs/skills.md), and every LEKS tool, command and flag a file names must exist in leks/server.py and
leks/__main__.py. Every check returns a list of problems, and CheckerSelfTest feeds each one broken files, so a
clean run means the checks looked."""
from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import unittest

from leks import build, check, chunking, config, query
from leks.server import TOOLS
from tests import helpers

try:
    import tomllib
except ImportError:   # 3.10, the subset parser below runs alone
    tomllib = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, 'examples', 'project-template')
TEAM = os.path.join(REPO, 'examples', 'agent-team')
SKILLS = os.path.join(TEMPLATE, '.claude', 'skills')
KIT_DOCS = ('docs/agents.md', 'docs/skills.md', 'docs/hosts.md')

LEKS_TOOLS = frozenset(t['name'] for t in TOOLS)
ORCHESTRATOR = 'orchestrator'

# agent skills spec: outside claude code only these fields work
SKILL_KEYS = frozenset({'name', 'description', 'license', 'compatibility', 'metadata', 'allowed-tools'})
SKILL_NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')

# vs code custom agent file reference plus github's custom agents configuration
COPILOT_KEYS = frozenset({'name', 'description', 'argument-hint', 'tools', 'agents', 'model', 'handoffs', 'target',
                          'user-invocable', 'disable-model-invocation', 'mcp-servers', 'metadata', 'hooks'})
HANDOFF_KEYS = frozenset({'label', 'agent', 'prompt', 'send', 'model'})
HANDOFF_NEEDS = ('label', 'agent', 'prompt')
COPILOT_ALIASES = frozenset({'execute', 'read', 'edit', 'search', 'agent', 'web', 'todo', '*'})
# server names in the tool lists: leks, and the names the unity and blender servers use in their own docs
MCP_SERVERS = frozenset({'leks', 'unityMCP', 'blender'})

# claude code subagents and its tools reference
CLAUDE_KEYS = frozenset({'name', 'description', 'tools', 'disallowedTools', 'model', 'permissionMode', 'maxTurns',
                         'autoCompactWindow', 'skills', 'mcpServers', 'hooks', 'memory', 'background', 'omitClaudeMd',
                         'effort', 'isolation', 'color', 'initialPrompt', 'experimental'})
CLAUDE_TOOLS = frozenset({'Agent', 'AskUserQuestion', 'Bash', 'Edit', 'Glob', 'Grep', 'LSP', 'NotebookEdit', 'Read',
                          'Skill', 'TodoWrite', 'ToolSearch', 'WebFetch', 'WebSearch', 'Write'})
CLAUDE_MCP = re.compile(r'^mcp__leks__(?:%s)$' % '|'.join(sorted(LEKS_TOOLS)))
AGENT_NAME = re.compile(r'^[a-z][a-z0-9-]*$')

# codex subagents: three required keys, the rest optional
CODEX_KEYS = frozenset({'name', 'description', 'developer_instructions', 'model', 'model_reasoning_effort',
                        'model_reasoning_summary', 'model_verbosity', 'features', 'skills'})
CODEX_NEEDS = ('name', 'description', 'developer_instructions')
CODEX_NAME = re.compile(r'^[a-z][a-z0-9_]*$')


def _w(*parts):
    return ''.join(parts)


# built from parts so this file does not trip its own scan
FORBIDDEN = [re.compile(p, re.I) for p in (
    r'\b' + _w('the', 'sis') + r'\b', r'\b' + _w('the', 'ses') + r'\b', _w('master', "'?s ", 'degree'),
    r'\b' + _w('va', 'ult') + r's?\b', r'\b' + _w('s', 'sd') + r's?\b', r'\b' + _w('gau', 'ssian') + r'\b',
    r'\b' + _w('spl', 'at'), r'\b' + _w('head', 'set') + r's?\b', r'\b' + _w('vision', ' ?', 'pro') + r'\b',
    r'\b' + _w('vision', 'os') + r'\b', _w('co-', 'authored-by'), _w('generated ', 'with'),
    r'\b' + _w('emp', 'loyer') + r'\b', r'\b' + _w('com', 'pany') + r'\b', r'\b' + _w('nov', 'el') + r'\b',
    r'\b' + _w('inv', 'ented') + r'\b', _w('state', '[ -]of[ -]', 'the[ -]art'), _w('first', '[ -]ever'),
    r'\b(?:written|tested|reviewed) (?:with|by) (?:an? )?(?:ai|' + _w('ll', 'ms?') + r')\b',
)] + [re.compile(r'\bE[0-9]\b')]   # an experiment code, case matters


class FrontMatterError(ValueError):
    pass


class TomlError(ValueError):
    pass


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


# ---- the yaml subset of the front matter -------------------------------------------------------------------

_KEY = re.compile(r'^([A-Za-z][A-Za-z0-9_-]*):(?: (.*))?$')
_ITEM_FIRST = re.compile(r'^  - ([A-Za-z][A-Za-z0-9_-]*):(?: (.*))?$')
_ITEM_MORE = re.compile(r'^    ([A-Za-z][A-Za-z0-9_-]*):(?: (.*))?$')


def split_front(text):
    """(front matter lines, body) of a file that opens with a --- block"""
    lines = text.split('\n')
    if not lines or lines[0] != '---':
        raise FrontMatterError('no front matter')
    for i in range(1, len(lines)):
        if lines[i] == '---':
            return lines[1:i], '\n'.join(lines[i + 1:])
    raise FrontMatterError('the front matter never closes')


def _split_items(inner, where):
    out, buf, q = [], '', None
    for ch in inner:
        if q:
            buf += ch
            if ch == q:
                q = None
        elif ch in '\'"':
            q = ch
            buf += ch
        elif ch == ',':
            out.append(buf)
            buf = ''
        else:
            buf += ch
    if q:
        raise FrontMatterError('%s: a quote in a list never closes' % where)
    out.append(buf)
    return out


def scalar(raw, where):
    """one value: 'x' or "x", [a, 'b'], true or false, or plain text that yaml reads the same way"""
    v = (raw or '').strip()
    if not v:
        raise FrontMatterError('%s: empty value' % where)
    if v[0] == '[':
        if not v.endswith(']'):
            raise FrontMatterError('%s: a list never closes' % where)
        inner = v[1:-1].strip()
        return [scalar(x, where) for x in _split_items(inner, where)] if inner else []
    if v[0] in '\'"':
        q = v[0]
        if len(v) < 2 or v[-1] != q or q in v[1:-1] or (q == '"' and '\\' in v):
            raise FrontMatterError('%s: bad quoted value %r' % (where, v))
        return v[1:-1]
    if v in ('true', 'false'):
        return v == 'true'
    if v[0] in '-?:,[]{}#&*!|>%@`':
        raise FrontMatterError('%s: a plain value starts with %r' % (where, v[0]))
    if ': ' in v or ' #' in v or v.endswith(':'):
        raise FrontMatterError('%s: a plain value holds ": " or " #": %r' % (where, v))
    return v


def parse_front(lines, where):
    """the yaml subset the kit uses: comment lines, key: value, and key: over a list of small maps.
    anything else raises, so no file drifts into syntax a host could read another way"""
    out = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith('#'):
            i += 1
            continue
        m = _KEY.match(line)
        if not m:
            raise FrontMatterError('%s: line %d is outside the subset: %r' % (where, i + 2, line))
        key, raw = m.group(1), m.group(2)
        if key in out:
            raise FrontMatterError('%s: %s twice' % (where, key))
        i += 1
        if raw is not None and raw.strip():
            out[key] = scalar(raw, where)
            continue
        items = []
        while i < len(lines):
            first, more = _ITEM_FIRST.match(lines[i]), _ITEM_MORE.match(lines[i])
            if first:
                items.append({first.group(1): scalar(first.group(2), where)})
            elif more and items:
                if more.group(1) in items[-1]:
                    raise FrontMatterError('%s: %s twice in one item' % (where, more.group(1)))
                items[-1][more.group(1)] = scalar(more.group(2), where)
            else:
                break
            i += 1
        if not items:
            raise FrontMatterError('%s: %s has no value' % (where, key))
        out[key] = items
    return out


def front_of(path):
    head, body = split_front(read(path))
    return parse_front(head, os.path.basename(path)), body


# ---- the toml subset of the codex files ---------------------------------------------------------------------

_THEAD = re.compile(r'^\[([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\]\s*(?:#.*)?$')
_TKEY = re.compile(r'^([A-Za-z0-9_-]+)\s*=\s*(.*)$')
_TSTR = re.compile(r'^"([^"\\]*)"\s*(?:#.*)?$')
_TINT = re.compile(r'^([1-9][0-9]*|0)\s*(?:#.*)?$')
_TITEM = re.compile(r'"([^"\\]*)"')


def toml_subset(text, where):
    """tables, "strings", whole numbers, arrays of strings and \"\"\" blocks without escapes. raises on anything else"""
    root = {}
    cur = root
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith('#'):
            continue
        m = _THEAD.match(line)
        if m:
            cur = root
            for part in m.group(1).split('.'):
                cur = cur.setdefault(part, {})
                if not isinstance(cur, dict):
                    raise TomlError('%s: %s is not a table' % (where, m.group(1)))
            continue
        m = _TKEY.match(line)
        if not m:
            raise TomlError('%s: line %d is outside the subset: %r' % (where, i, line))
        key, raw = m.group(1), m.group(2).strip()
        if key in cur:
            raise TomlError('%s: %s twice' % (where, key))
        if raw.startswith('"""'):
            rest = raw[3:]
            if '"""' in rest:
                raise TomlError('%s: one-line """ string' % where)
            buf = [rest] if rest else []   # the newline right after the opening quotes is dropped
            while True:
                if i >= len(lines):
                    raise TomlError('%s: a """ string never closes' % where)
                part = lines[i]
                i += 1
                if '"""' in part:
                    body, _, tail = part.partition('"""')
                    if tail.strip() and not tail.strip().startswith('#'):
                        raise TomlError('%s: text after a closing """' % where)
                    buf.append(body)
                    break
                buf.append(part)
            val = '\n'.join(buf)
            if '\\' in val:
                raise TomlError('%s: a backslash in a """ string' % where)
            cur[key] = val
            continue
        m = _TSTR.match(raw)
        if m:
            cur[key] = m.group(1)
            continue
        m = _TINT.match(raw)
        if m:
            cur[key] = int(m.group(1))
            continue
        if raw.startswith('['):
            end = raw.rfind(']')
            tail = raw[end + 1:].strip() if end >= 0 else 'x'
            inner = raw[1:end] if end >= 0 else ''
            if (tail and not tail.startswith('#')) or _TITEM.sub('', inner).replace(',', '').strip():
                raise TomlError('%s: %s is not an array of plain strings' % (where, key))
            cur[key] = _TITEM.findall(inner)
            continue
        raise TomlError('%s: the value of %s is outside the subset' % (where, key))
    return root


def load_toml(text, where):
    got = toml_subset(text, where)
    if tomllib is not None and got != tomllib.loads(text):
        raise TomlError('%s: the subset parser and tomllib disagree' % where)
    return got


def strip_jsonc(text):
    """// and /* */ comments out, strings kept, for .vscode/settings.json"""
    out, i, n, q = [], 0, len(text), False
    while i < n:
        c = text[i]
        if q:
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                q = False
            i += 1
        elif c == '"':
            q = True
            out.append(c)
            i += 1
        elif text.startswith('//', i):
            j = text.find('\n', i)
            i = n if j < 0 else j
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2)
            if j < 0:
                raise ValueError('a block comment never closes')
            i = j + 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


# ---- markdown -------------------------------------------------------------------------------------------------

_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
_COMMENT = re.compile(r'<!--.*?-->', re.S)
_INLINE = re.compile(r'(`+)(?!`)(.+?)(?<!`)\1(?!`)')
_LINK = re.compile(r'!?\[[^\]\n]*\]\(([^()\s]+)\)')
_SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')
_HEADING = re.compile(r'^(#{1,6}) +(.+?) *#*$')


def _fenced(text):
    """yields (line, inside a fenced block, info string of that block)"""
    fence, info = None, ''
    for line in text.split('\n'):
        m = _FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                fence = None
                yield line, True, None
            else:
                yield line, True, info
            continue
        if m:
            fence, info = m.group(1), m.group(2).strip()
            yield line, True, None
            continue
        yield line, False, None


def md_blocks(text):
    """(info string, code) of every fenced block that closes"""
    out, cur = [], None
    for line, _, info in _fenced(text):
        if info is not None:
            if cur is None:
                cur = (info, [])
            cur[1].append(line)
        elif cur is not None:   # the closing fence
            out.append((cur[0], '\n'.join(cur[1])))
            cur = None
    return out


def section(text, title):
    """the lines under '## title' up to the next ## heading"""
    m = re.search(r'^## %s\n(.*?)(?=^## |\Z)' % re.escape(title), text, re.M | re.S)
    return m.group(1) if m else ''


def md_prose(text):
    """the text with code blocks, code spans and html comments blanked, line numbers kept"""
    t = '\n'.join('' if inside else line for line, inside, _ in _fenced(text))
    t = _COMMENT.sub(lambda m: '\n' * m.group(0).count('\n'), t)
    return _INLINE.sub(lambda m: ' ' * len(m.group(0)), t)


def anchors(text):
    """github style heading ids"""
    out, seen = set(), {}
    for line, inside, _ in _fenced(text):
        m = None if inside else _HEADING.match(line)
        if not m:
            continue
        s = re.sub(r'[^a-z0-9 _-]', '', m.group(2).lower()).replace(' ', '-')
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(s if n == 0 else '%s-%d' % (s, n))
    return out


def link_problems(path, top=REPO):
    """dead relative links and anchors in one markdown file. wikilinks outside code are problems too: in the kit
    they could only point into a project that does not exist yet"""
    text = read(path)
    prose = md_prose(text)
    rel = os.path.relpath(path, top)
    out = []
    for m in _LINK.finditer(prose):
        target = m.group(1)
        line = prose.count('\n', 0, m.start()) + 1
        if _SCHEME.match(target):
            continue
        part, _, anchor = target.partition('#')
        dest = os.path.normpath(os.path.join(os.path.dirname(path), part)) if part else path
        if not (dest + os.sep).startswith(os.path.normpath(top) + os.sep) or not os.path.exists(dest):
            out.append('%s:%d: %s does not resolve' % (rel, line, target))
        elif anchor and (os.path.isdir(dest) or anchor not in anchors(read(dest))):
            out.append('%s:%d: no heading for #%s' % (rel, line, anchor))
    for m in re.finditer(r'\[\[[^\]\n]+\]\]', prose):
        out.append('%s:%d: wikilink %s' % (rel, prose.count('\n', 0, m.start()) + 1, m.group(0)))
    return out


def word_problems(path, top=REPO):
    """forbidden words and anything outside ascii"""
    with open(path, 'rb') as f:
        raw = f.read()
    rel = os.path.relpath(path, top)
    out = []
    bad = [b for b in raw if b > 127]
    if bad:
        out.append('%s: %d bytes outside ascii' % (rel, len(bad)))
    text = raw.decode('utf-8', 'replace')
    for rx in FORBIDDEN:
        for m in rx.finditer(text):
            out.append('%s:%d: %r' % (rel, text.count('\n', 0, m.start()) + 1, m.group(0)))
    return out


def kit_files(include_docs=True):
    out = []
    for base in (TEMPLATE, TEAM):
        for dp, dns, fns in os.walk(base):
            dns[:] = sorted(d for d in dns if d != '__pycache__')
            out += [os.path.join(dp, fn) for fn in sorted(fns) if fn != '.DS_Store']
    if include_docs:
        out += [os.path.join(REPO, p) for p in KIT_DOCS]
    return out


# ---- the checks -----------------------------------------------------------------------------------------------

def skill_problems(folder):
    """problems of one skill folder"""
    name = os.path.basename(folder)
    path = os.path.join(folder, 'SKILL.md')
    if not os.path.isfile(path):
        return ['%s: no SKILL.md' % name]
    try:
        fm, body = front_of(path)
    except FrontMatterError as e:
        return ['%s: %s' % (name, e)]
    out = []
    extra = set(fm) - SKILL_KEYS
    if extra:
        out.append('%s: keys outside the spec: %s' % (name, sorted(extra)))
    n = fm.get('name')
    if not isinstance(n, str) or not SKILL_NAME.match(n) or len(n) > 64:
        out.append('%s: bad name %r' % (name, n))
    elif n != name:
        out.append('%s: name %r differs from the folder' % (name, n))
    d = fm.get('description')
    if not isinstance(d, str) or not 1 <= len(d) <= 1024:
        out.append('%s: the description needs 1 to 1024 characters' % name)
    if body.count('\n') >= 500:
        out.append('%s: body of 500 lines or more' % name)
    if not re.search(r'^# \S', body, re.M):
        out.append('%s: no title' % name)
    if len(re.findall(r'^\d+\. \S', body, re.M)) < 3:
        out.append('%s: fewer than three numbered steps' % name)
    if not re.search(r'^## Done when$', body, re.M):
        out.append('%s: no "## Done when" section' % name)
    named = set(re.findall(r'`([a-z]+)`', '\n'.join(line for line, inside, _ in _fenced(body) if not inside)))
    if not named & LEKS_TOOLS:
        out.append('%s: names no LEKS tool' % name)
    for ref in re.findall(r'\b([a-z0-9]+(?:-[a-z0-9]+)+) skill\b', body):
        if not os.path.isfile(os.path.join(os.path.dirname(folder), ref, 'SKILL.md')):
            out.append('%s: names the %s skill, which does not exist' % (name, ref))
    return out


def _copilot_tool_ok(t):
    if t in COPILOT_ALIASES:
        return True
    server, sep, tool = t.partition('/')
    if not sep or server not in MCP_SERVERS:
        return False
    return tool == '*' or (server == 'leks' and tool in LEKS_TOOLS)


def copilot_problems(folder):
    """problems across one .github/agents folder"""
    out = []
    files = sorted(f for f in os.listdir(folder) if f.endswith('.agent.md'))
    names = {f[:-len('.agent.md')] for f in files}
    if ORCHESTRATOR not in names:
        out.append('no %s.agent.md' % ORCHESTRATOR)
    for f in files:
        stem = f[:-len('.agent.md')]
        try:
            fm, body = front_of(os.path.join(folder, f))
        except FrontMatterError as e:
            out.append(str(e))
            continue
        extra = set(fm) - COPILOT_KEYS
        if extra:
            out.append('%s: undocumented keys %s' % (f, sorted(extra)))
        if fm.get('name') != stem or not AGENT_NAME.match(stem):
            out.append('%s: name %r should be the file name' % (f, fm.get('name')))
        if not isinstance(fm.get('description'), str):
            out.append('%s: no description' % f)
        if len(body) > 30000:
            out.append('%s: prompt over 30,000 characters' % f)
        tools = fm.get('tools', [])
        tools = [tools] if isinstance(tools, str) else tools
        if not isinstance(tools, list):
            out.append('%s: tools is no list' % f)
            tools = []
        for t in tools:
            if not isinstance(t, str) or not _copilot_tool_ok(t):
                out.append('%s: unknown tool %r' % (f, t))
        # only the orchestrator starts agents
        if stem == ORCHESTRATOR and 'agent' not in tools:
            out.append('%s: the orchestrator needs the agent tool' % f)
        if stem != ORCHESTRATOR and ('agent' in tools or '*' in tools):
            out.append('%s: a role agent must not start agents' % f)
        if stem == 'review' and ('edit' in tools or '*' in tools):
            out.append('%s: the review agent must not edit' % f)
        hand = fm.get('handoffs', [])
        if not isinstance(hand, list):
            out.append('%s: handoffs is no list' % f)
            hand = []
        targets = []
        for h in hand:
            if not isinstance(h, dict):
                out.append('%s: a handoff is no map' % f)
                continue
            extra = set(h) - HANDOFF_KEYS
            if extra:
                out.append('%s: undocumented handoff keys %s' % (f, sorted(extra)))
            for k in HANDOFF_NEEDS:
                if not isinstance(h.get(k), str):
                    out.append('%s: a handoff without %s' % (f, k))
            if 'send' in h and not isinstance(h['send'], bool):
                out.append('%s: send is true or false' % f)
            if h.get('agent') not in names or h.get('agent') == stem:
                out.append('%s: handoff to %r, no such agent' % (f, h.get('agent')))
            targets.append(h.get('agent'))
        if stem != ORCHESTRATOR and ORCHESTRATOR not in targets:
            out.append('%s: no handoff back to the orchestrator' % f)
    return out


def _tool_list(v):
    if v is None:
        return None
    return [x.strip() for x in v.split(',')] if isinstance(v, str) else v


def claude_problems(folder):
    """problems across one .claude/agents folder"""
    out = []
    files = sorted(f for f in os.listdir(folder) if f.endswith('.md'))
    if ORCHESTRATOR + '.md' not in files:
        out.append('no %s.md' % ORCHESTRATOR)
    for f in files:
        stem = f[:-3]
        try:
            fm, _ = front_of(os.path.join(folder, f))
        except FrontMatterError as e:
            out.append(str(e))
            continue
        for k in ('name', 'description'):
            if not isinstance(fm.get(k), str):
                out.append('%s: no %s' % (f, k))
        extra = set(fm) - CLAUDE_KEYS
        if extra:
            out.append('%s: undocumented keys %s' % (f, sorted(extra)))
        if fm.get('name') != stem or not AGENT_NAME.match(stem):
            out.append('%s: name %r should be the file name' % (f, fm.get('name')))
        allowed, denied = _tool_list(fm.get('tools')), _tool_list(fm.get('disallowedTools'))
        for t in (allowed or []) + (denied or []):
            if not isinstance(t, str) or not (t in CLAUDE_TOOLS or CLAUDE_MCP.match(t)):
                out.append('%s: unknown tool %r' % (f, t))
        can_spawn = 'Agent' not in (denied or []) and (allowed is None or 'Agent' in allowed)
        if stem == ORCHESTRATOR and not can_spawn:
            out.append('%s: the orchestrator needs the Agent tool' % f)
        if stem != ORCHESTRATOR and can_spawn:
            out.append('%s: a role agent must not start agents' % f)
        if stem == 'review' and allowed is None and not {'Edit', 'Write', 'NotebookEdit'} <= set(denied or []):
            out.append('%s: the review agent must not edit' % f)
    return out


def codex_problems(folder):
    """problems across one .codex/agents folder"""
    out = []
    for f in sorted(os.listdir(folder)):
        if not f.endswith('.toml'):
            continue
        try:
            d = load_toml(read(os.path.join(folder, f)), f)
        except ValueError as e:
            out.append(str(e))
            continue
        for k in CODEX_NEEDS:
            if not isinstance(d.get(k), str) or not d[k].strip():
                out.append('%s: no %s' % (f, k))
        extra = set(d) - CODEX_KEYS
        if extra:
            out.append('%s: undocumented keys %s' % (f, sorted(extra)))
        n = d.get('name')
        if not isinstance(n, str) or not CODEX_NAME.match(n) or n != f[:-5].replace('-', '_'):
            out.append('%s: name %r should be the file name with _ for -' % (f, n))
    return out


def _team_rows(text):
    return re.findall(r'^\| ([a-z][a-z0-9-]*)(?: \(Codex: ([a-z0-9_]+)\))? \|', text, re.M)


def team_problems(team, skills=SKILLS):
    """the hosts define the same roles, and every role and skill the team names exists"""
    out = []
    gh_dir, cl_dir, cx_dir = (os.path.join(team, *p) for p in
                              (('.github', 'agents'), ('.claude', 'agents'), ('.codex', 'agents')))
    gh = {f[:-len('.agent.md')]: os.path.join(gh_dir, f) for f in os.listdir(gh_dir) if f.endswith('.agent.md')}
    cl = {f[:-3]: os.path.join(cl_dir, f) for f in os.listdir(cl_dir) if f.endswith('.md')}
    cx = {}
    for f in os.listdir(cx_dir):
        if f.endswith('.toml'):
            try:
                cx[f[:-5]] = load_toml(read(os.path.join(cx_dir, f)), f)
            except ValueError as e:
                out.append(str(e))
    if set(gh) != set(cl):
        out.append('copilot and claude code differ: %s' % sorted(set(gh) ^ set(cl)))
    roles = set(gh) - {ORCHESTRATOR}
    if set(cx) != roles:
        out.append('codex differs: %s' % sorted(set(cx) ^ roles))
    for r in sorted(roles & set(cl) & set(cx)):
        descs = set()
        for p in (gh[r], cl[r]):
            try:
                descs.add(front_of(p)[0].get('description'))
            except FrontMatterError as e:
                out.append(str(e))
        descs.add(cx[r].get('description'))
        if len(descs) != 1:
            out.append('%s: the hosts disagree on its description' % r)
        # the role's instructions are one text on every host, codex only writes its name with _
        bodies = set()
        for p in (gh[r], cl[r]):
            try:
                bodies.add(front_of(p)[1].strip())
            except FrontMatterError:
                pass
        body = cx[r].get('developer_instructions')
        if isinstance(body, str):
            cname = r.replace('-', '_')
            bodies.add(body.strip().replace('named %s ' % cname, 'named %s ' % r))
        if len(bodies) != 1:
            out.append('%s: the hosts disagree on its instructions' % r)
    have = {d for d in os.listdir(skills) if os.path.isfile(os.path.join(skills, d, 'SKILL.md'))}
    named = [p for p in (gh.get(ORCHESTRATOR), cl.get(ORCHESTRATOR)) if p]
    named.append(os.path.join(team, 'AGENTS.team.md'))
    for p in named:
        rows = _team_rows(read(p))
        if {r for r, _ in rows} != roles:
            out.append('%s: its team table lists %s' % (os.path.basename(p), sorted(r for r, _ in rows)))
        for r, cname in rows:
            if cname and cname != r.replace('-', '_'):
                out.append('%s: Codex name %s for %s' % (os.path.basename(p), cname, r))
    texts = [read(p) for p in list(gh.values()) + list(cl.values())]
    texts += [v.get('developer_instructions', '') for v in cx.values()]
    texts += [read(os.path.join(team, n)) for n in ('AGENTS.team.md', 'README.md')]
    for t in texts:
        for ref in re.findall(r'\b([a-z0-9]+(?:-[a-z0-9]+)+) skill\b', t):
            if ref not in have:
                out.append('the team names the %s skill, which does not exist' % ref)
    # the README table: | agent | works on | skills it follows |
    readme = read(os.path.join(team, 'README.md'))
    rows = re.findall(r'^\| ([a-z][a-z0-9-]*) \| [^|\n]+ \| ([a-z0-9, -]+) \|$', readme, re.M)
    if {r for r, _ in rows} != roles | {ORCHESTRATOR}:
        out.append('README.md: the team table lists %s' % sorted(r for r, _ in rows))
    for r, cell in rows:
        body = read(cl[r]) if r in cl else ''
        for ref in (x.strip() for x in cell.split(',')):
            if ref not in have:
                out.append('README.md: %s follows %s, which does not exist' % (r, ref))
            elif not re.search(r'\b%s\b' % re.escape(ref), body):
                out.append('README.md: %s follows %s, its instructions never name it' % (r, ref))
    return out


def config_problems(template):
    """the configs parse, and every one starts the same leks server with the same profile"""
    out = []
    seen = {}

    def load(rel):
        text = read(os.path.join(template, rel))
        if rel.endswith('.toml'):
            return load_toml(text, rel)
        return json.loads(strip_jsonc(text) if rel == '.vscode/settings.json' else text)

    for dp, _dns, fns in os.walk(template):
        for fn in fns:
            if fn.endswith(('.json', '.toml')):
                rel = os.path.relpath(os.path.join(dp, fn), template).replace(os.sep, '/')
                try:
                    load(rel)
                except (ValueError, OSError) as e:
                    out.append('%s does not parse: %s' % (rel, e))
    for rel, path in (('.mcp.json', ('mcpServers', 'leks')), ('.vscode/mcp.json', ('servers', 'leks')),
                      ('.codex/config.toml', ('mcp_servers', 'leks'))):
        try:
            srv = load(rel)
            for k in path:
                srv = srv[k]
            seen[rel] = (srv['command'], tuple(srv['args']), srv.get('env', {}).get('LEKS_PROFILE'))
        except (ValueError, OSError, KeyError, TypeError) as e:
            out.append('%s: no leks server (%s)' % (rel, e))
            continue
        if rel == '.codex/config.toml':
            # codex cuts a tool call at 60 s unless told otherwise, a cold search may wait for both models
            b = config.default_dict()['budgets']
            need = b['embed'] + b['cold_rerank']
            if not isinstance(srv.get('tool_timeout_sec'), int) or srv['tool_timeout_sec'] <= need:
                out.append('%s: tool_timeout_sec should be above %d s' % (rel, need))
    if len(set(seen.values())) > 1:
        out.append('the leks servers differ: %s' % seen)
    if not seen:
        return out
    cmd, args, profile = sorted(seen.values())[0]
    if args != ('-m', 'leks', 'serve'):
        out.append('the server args are %s' % (args,))
    start = cmd + ' -m leks '
    try:
        s = load('.claude/settings.json')
        hooks = [x['command'] for ev in s['hooks'].values() for e in ev for x in e['hooks']]
        if s['env'].get('LEKS_PROFILE') != profile:
            out.append('.claude/settings.json: another profile')
        g = load('.github/hooks/leks.json')['hooks']
        hooks += [x['bash'] for ev in g.values() for x in ev]
        if any(x['env'].get('LEKS_PROFILE') != profile for ev in g.values() for x in ev):
            out.append('.github/hooks/leks.json: another profile')
        c = [x['command'] for ev in load('.codex/hooks.json')['hooks'].values() for e in ev for x in e['hooks']]
        hooks += c
        # a codex hook has no env of its own, so the profile rides on the command line
        for x in c:
            m = re.search(r'--profile[= ](\S+)', x)
            if not m or m.group(1) != profile:
                out.append('.codex/hooks.json: %r runs another profile' % x)
    except (ValueError, OSError, KeyError, TypeError) as e:
        out.append('a hook file does not parse: %s' % e)
        hooks = []
    for h in hooks:
        if not h.startswith(start):
            out.append('a hook runs %r, not %r' % (h, start))
    return out


# copilot event -> claude code event. copilot hooks have no tool matcher, so it gets no warm-up hook (docs/hosts.md)
COPILOT_EVENTS = {'sessionStart': 'SessionStart', 'agentStop': 'Stop'}
NO_COPILOT = frozenset({'PreToolUse'})
# the env keys a codex hook can carry, as flags on its command line
CODEX_FLAGS = {'LEKS_PROFILE': '--profile', 'LEKS_CONFIG': '--config'}


def _leks_args(command):
    """(the leks arguments without the global flags, {global flag: value}), None for no python -m leks line"""
    try:
        a = shlex.split(command)
    except ValueError:
        return None
    if len(a) < 3 or a[1:3] != ['-m', 'leks']:
        return None
    rest, flags, i = [], {}, 3
    while i < len(a):
        k, eq, v = a[i].partition('=')
        if k in ('--profile', '--config'):
            if not eq:
                i += 1
                v = a[i] if i < len(a) else ''
            flags[k] = v
        else:
            rest.append(a[i])
        i += 1
    return ' '.join(rest), flags


def hook_problems(template):
    """the three hosts run the same leks hooks with the same timeouts and the same env, copilot's stop hook runs in
    the repository root, and vs code leaves the claude code hooks to claude code"""
    out = []

    def load(rel):
        text = read(os.path.join(template, *rel.split('/')))
        if rel.endswith('.toml'):
            return load_toml(text, rel)
        return json.loads(strip_jsonc(text) if rel == '.vscode/settings.json' else text)

    try:
        cc = load('.claude/settings.json')
        gh = load('.github/hooks/leks.json')['hooks']
        cx = load('.codex/hooks.json')['hooks']
        vs = load('.vscode/settings.json')
        envs = {'.claude/settings.json': cc.get('env', {}),
                '.mcp.json': load('.mcp.json')['mcpServers']['leks'].get('env', {}),
                '.vscode/mcp.json': load('.vscode/mcp.json')['servers']['leks'].get('env', {}),
                '.codex/config.toml': load('.codex/config.toml')['mcp_servers']['leks'].get('env', {})}
        want = {ev: [(_leks_args(x['command']), x.get('timeout')) for g in groups for x in g['hooks']]
                for ev, groups in cc['hooks'].items()}
        cc_shape = {ev: [(g.get('matcher'), [(_leks_args(x['command']) or (x['command'], {}))[0]
                                             for x in g['hooks']], [x.get('timeout') for x in g['hooks']])
                         for g in groups] for ev, groups in cc['hooks'].items()}
        cx_shape = {ev: [(g.get('matcher'), [(_leks_args(x['command']) or (x['command'], {}))[0]
                                             for x in g['hooks']], [x.get('timeout') for x in g['hooks']])
                         for g in groups] for ev, groups in cx.items()}
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as e:
        return ['a hook or server file does not parse: %s' % e]
    if vs.get('chat.useClaudeHooks') is not False:
        out.append('.vscode/settings.json: chat.useClaudeHooks should be false, or vs code runs both hook files')
    # codex: the same events, matchers, commands and timeouts as claude code
    if cx_shape != cc_shape:
        out.append('.codex/hooks.json runs other hooks than .claude/settings.json: %s' % cx_shape)
    # copilot: the same commands and timeouts per event, minus the warm-up hook
    mapped = {COPILOT_EVENTS.get(ev, ev) for ev in gh}
    if mapped != set(want) - NO_COPILOT:
        out.append('.github/hooks/leks.json has the events %s' % sorted(gh))
    for ev, entries in gh.items():
        got = []
        for x in entries:
            if not isinstance(x, dict):
                out.append('.github/hooks/leks.json: an entry of %s is no map' % ev)
                continue
            got.append((_leks_args(x.get('bash', '')), x.get('timeoutSec')))
            envs['.github/hooks/leks.json %s' % ev] = x.get('env', {})
            if ev == 'agentStop' and x.get('cwd') != '.':
                out.append('.github/hooks/leks.json: agentStop needs "cwd": "." to check the repository root')
        if got != want.get(COPILOT_EVENTS.get(ev, ev)):
            out.append('.github/hooks/leks.json: %s runs %s' % (ev, got))
    # one env everywhere. vs code may write a path from ${workspaceFolder}, so only the keys count there
    keys = {k: frozenset(v) for k, v in envs.items()}
    if len(set(keys.values())) != 1:
        out.append('the env blocks name other variables: %s' % {k: sorted(v) for k, v in keys.items()})
    base = envs['.claude/settings.json']
    for where, env in envs.items():
        for k, v in env.items():
            if base.get(k) != v and not (where == '.vscode/mcp.json' and str(v).startswith('${workspaceFolder}')):
                out.append('%s: %s=%r, .claude/settings.json has %r' % (where, k, v, base.get(k)))
    # a codex hook has no env, its command line carries the same values
    for k in base:
        if k not in CODEX_FLAGS:
            out.append('.codex/hooks.json: a codex hook cannot carry %s' % k)
    for groups in cx.values():
        for g in groups:
            for x in g['hooks']:
                got = (_leks_args(x['command']) or ('', {}))[1]
                need = {CODEX_FLAGS[k]: v for k, v in base.items() if k in CODEX_FLAGS}
                if got != need:
                    out.append('.codex/hooks.json: %r should carry %s' % (x['command'], need))
    return out


_HOOK_CELL = re.compile(r'`([A-Za-z]+)`')


def hosts_doc_problems(doc, template):
    """the hook table of docs/hosts.md names, per host, the events and leks commands the hook files run"""
    out = []
    rows = [r for r in section(read(doc), 'Hooks').splitlines() if r.startswith('| ') and '---' not in r]
    said = set()
    for r in rows[1:]:
        cells = [c.strip() for c in r.strip('|').split('|')]
        if len(cells) < 5:
            out.append('hosts.md: a hook row without five cells: %s' % r)
            continue
        m = re.fullmatch(r'`leks ([a-z ]+)`', cells[4])
        if not m:
            out.append('hosts.md: no leks command in %s' % r)
            continue
        for host, cell in zip(('claude', 'copilot', 'codex'), cells[1:4], strict=True):
            ev = _HOOK_CELL.match(cell)
            if ev:
                said.add((host, ev.group(1), m.group(1)))
            elif cell != 'none':
                out.append('hosts.md: %s cell %r names no event' % (host, cell))
    try:
        cc = json.loads(read(os.path.join(template, '.claude', 'settings.json')))['hooks']
        gh = json.loads(read(os.path.join(template, '.github', 'hooks', 'leks.json')))['hooks']
        cx = json.loads(read(os.path.join(template, '.codex', 'hooks.json')))['hooks']
        run = {('claude', ev, (_leks_args(x['command']) or ('',))[0]) for ev, gs in cc.items()
               for g in gs for x in g['hooks']}
        run |= {('codex', ev, (_leks_args(x['command']) or ('',))[0]) for ev, gs in cx.items()
                for g in gs for x in g['hooks']}
        run |= {('copilot', ev, (_leks_args(x['bash']) or ('',))[0]) for ev, xs in gh.items() for x in xs}
    except (ValueError, OSError, KeyError, TypeError) as e:
        return out + ['a hook file does not parse: %s' % e]
    for host, ev, cmd in sorted(said - run):
        out.append('hosts.md: %s %s runs leks %s, the hook files do not' % (host, ev, cmd))
    for host, ev, cmd in sorted(run - said):
        out.append('hosts.md: the hook table leaves out %s %s, leks %s' % (host, ev, cmd))
    return out


def block_problems(paths, top=REPO):
    """```json and ```toml blocks in the markdown parse"""
    out = []
    for p in paths:
        if not p.endswith('.md'):
            continue
        for info, code in md_blocks(read(p)):
            lang = info.split()[0] if info else ''
            try:
                if lang == 'json':
                    json.loads(code)
                elif lang == 'toml':
                    load_toml(code, os.path.relpath(p, top))
            except ValueError as e:
                out.append('%s: a %s block does not parse: %s' % (os.path.relpath(p, top), lang, e))
    return out


# ---- the leks names a file uses -------------------------------------------------------------------------------

def _cli():
    """{command: its option strings} of the leks command line, plus the flags every command takes"""
    import argparse
    from leks.__main__ import _parser
    p = _parser()
    sub = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)][0]
    return {n: set(s._option_string_actions) for n, s in sub.choices.items()}


CLI = _cli()
# _pull_globals takes --profile and --config anywhere on the line
GLOBAL_FLAGS = frozenset({'--profile', '--config', '--help', '-h'})
# tools a file may name that are no leks tools: copilot's delegation tools
HOST_TOOLS = frozenset({'agent', 'task'})

_CMD = re.compile(r'(?<![\w./-])leks ([a-z][a-z-]*)([^\n`]*)')
_FLAG = re.compile(r'(?<![\w-])(--?[a-z][a-z-]*)')
_TOOL_REFS = (re.compile(r'\b[Cc]alls? `([a-z_]+)`'), re.compile(r'`([a-z_]+)` tool\b'),
              re.compile(r'\bmcp__leks__([a-z_]+)'))


def _code_text(text):
    """the inside of every code span and fenced block, one piece per line"""
    out = []
    for line, inside, info in _fenced(text):
        if inside:
            if info is not None:
                out.append(line)
            continue
        out += [m.group(2) for m in _INLINE.finditer(line)]
    return out


def leks_name_problems(path, top=REPO):
    """every leks command, flag and MCP tool a file names exists in the code. toml files count by their strings,
    the comments there are prose"""
    text = read(path)
    rel = os.path.relpath(path, top)
    if path.endswith('.toml'):
        text = '\n'.join(v for v in toml_subset(text, rel).values() if isinstance(v, str))
    out = []
    for piece in _code_text(text):
        for m in _CMD.finditer(piece):
            cmd, rest = m.group(1), m.group(2)
            if cmd not in CLI:
                out.append('%s: leks %s is no command' % (rel, cmd))
                continue
            for f in _FLAG.findall(rest):
                if f not in CLI[cmd] and f not in GLOBAL_FLAGS:
                    out.append('%s: leks %s has no flag %s' % (rel, cmd, f))
    for rx in _TOOL_REFS:
        for m in rx.finditer(text):
            name = m.group(1)
            if name not in LEKS_TOOLS and name not in HOST_TOOLS:
                out.append('%s: no leks tool named %s' % (rel, name))
    return out


# the one-word code spans an agent reads that are no leks tool: status words, status fields, server names, the
# unity and blender servers' own tool names, and the parameter of neighbours. A new word goes here on purpose, so
# a tool named in any wording ("use `lookup`") has to be a real one
CODE_WORDS = frozenset({
    'current', 'superseded', 'living', 'freshness', 'stale', 'root', 'by_root', 'built_utc', 'lanes', 'was',
    'leks', 'blender',
    'execute_code', 'manage_asset', 'manage_gameobject', 'manage_material', 'manage_scene',
    'execute_blender_code_for_cli',
})
_WORD = re.compile(r'[a-z][a-z0-9_]*')


def agent_files():
    """the files an agent reads as instructions: the template's markdown, the skills and the team, no READMEs"""
    out = [p for p in kit_files(include_docs=False)
           if p.endswith(('.md', '.toml')) and os.path.basename(p) != 'README.md']
    return out


def code_word_problems(path, top=REPO):
    """every one-word code span is a leks tool, a host tool, mcp__leks__<tool> or a word of CODE_WORDS"""
    text = read(path)
    rel = os.path.relpath(path, top)
    if path.endswith('.toml'):
        text = '\n'.join(v for v in toml_subset(text, rel).values() if isinstance(v, str))
    out = []
    for line, inside, _ in _fenced(text):
        if inside:
            continue
        for m in _INLINE.finditer(line):
            w = m.group(2)
            if not _WORD.fullmatch(w):
                continue
            tool = w[len('mcp__leks__'):] if w.startswith('mcp__leks__') else w
            if tool not in LEKS_TOOLS and tool not in HOST_TOOLS and w not in CODE_WORDS:
                out.append('%s: `%s` is no leks tool and not in CODE_WORDS' % (rel, w))
    return out


# ---- the edit contracts a file names -------------------------------------------------------------------------

# words a line about edit contracts may put in a code span: the types and entry keys of leks/config.py, the fields
# an entry may keep, the config names around them, and the leks commands and tools
CONTRACT_WORDS = (frozenset(config.CONTRACT_TYPES) | LEKS_TOOLS | frozenset(CLI)
                  | {'glob', 'type', 'keep', 'successor', 'check', 'contracts', 'status', 'date', 'revisit'})
# words that read like a contract type and are none. In a line about contracts, "a <word> note" with one of them,
# or with a hyphenated word that is no type, names a type leks does not have
NEAR_TYPES = frozenset({'source', 'record', 'locked', 'immutable', 'read-only', 'append-only', 'fixed', 'static'})
_NOTE_OF = re.compile(r'\b(?:a|an|the|every|each|any)\s+([a-z][a-z-]*)\s+notes?\b')
_BARE = re.compile(r'[a-z][a-z-]*')
# the docs outside the kit that describe the contracts
CONTRACT_DOCS = ('README.md', 'docs/how-it-works.md', 'docs/configuration.md', 'docs/cli.md')


def contract_problems(path, top=REPO):
    """a line about edit contracts names only the contract types of leks/config.py, in a code span and as the
    word before note"""
    rel = os.path.relpath(path, top)
    out = []
    for n, line in enumerate(read(path).splitlines(), 1):
        if 'contract' not in line.lower():
            continue
        for m in _INLINE.finditer(line):
            w = m.group(2)
            if _BARE.fullmatch(w) and w not in CONTRACT_WORDS:
                out.append('%s:%d: `%s` is no contract type or key' % (rel, n, w))
        for w in _NOTE_OF.findall(line):
            if w in NEAR_TYPES or ('-' in w and w not in config.CONTRACT_TYPES):
                out.append('%s:%d: "%s note" names no contract type' % (rel, n, w))
    return out


# ---- the kit as it is -----------------------------------------------------------------------------------------

def skill_dirs():
    return sorted(os.path.join(SKILLS, d) for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d)))


class KitTests(unittest.TestCase):
    def test_skills(self):
        dirs = skill_dirs()
        self.assertGreaterEqual(len(dirs), 20)
        self.assertEqual([p for d in dirs for p in skill_problems(d)], [])

    def test_copilot_agents(self):
        self.assertEqual(copilot_problems(os.path.join(TEAM, '.github', 'agents')), [])

    def test_claude_agents(self):
        self.assertEqual(claude_problems(os.path.join(TEAM, '.claude', 'agents')), [])

    def test_codex_agents(self):
        self.assertEqual(codex_problems(os.path.join(TEAM, '.codex', 'agents')), [])

    def test_the_hosts_agree(self):
        self.assertEqual(team_problems(TEAM), [])

    def test_configs_start_one_server(self):
        self.assertEqual(config_problems(TEMPLATE), [])

    def test_the_hosts_run_the_same_hooks(self):
        self.assertEqual(hook_problems(TEMPLATE), [])

    def test_hosts_doc_names_the_hooks(self):
        self.assertEqual(hosts_doc_problems(os.path.join(REPO, 'docs', 'hosts.md'), TEMPLATE), [])

    def test_code_words(self):
        files = agent_files()
        self.assertGreater(len(files), 40)
        self.assertEqual([x for p in files for x in code_word_problems(p)], [])

    def test_code_blocks_parse(self):
        self.assertEqual(block_problems(kit_files()), [])

    def test_links_resolve(self):
        md = [p for p in kit_files() if p.endswith('.md')]
        self.assertGreater(len(md), 40)
        self.assertEqual([x for p in md for x in link_problems(p)], [])

    def test_words(self):
        self.assertEqual([x for p in kit_files() for x in word_problems(p)], [])

    def test_docs_list_every_skill_once(self):
        text = read(os.path.join(REPO, 'docs', 'skills.md'))
        rows = re.findall(r'^\| `([a-z0-9-]+)` \|', section(text, 'The skills'), re.M)
        self.assertEqual(sorted(rows), [os.path.basename(d) for d in skill_dirs()])
        self.assertIn('https://github.com/klemensgc/modular-context-obsidian-plugin', text)

    def test_docs_name_every_agent(self):
        text = read(os.path.join(REPO, 'docs', 'agents.md'))
        names = sorted(f[:-3] for f in os.listdir(os.path.join(TEAM, '.claude', 'agents')) if f.endswith('.md'))
        rows = re.findall(r'^\| ([a-z][a-z0-9-]*) \|', section(text, 'The team'), re.M)
        self.assertEqual(sorted(rows), names)
        # the copilot tools column is what each file grants
        cells = re.findall(r'^\| ([a-z][a-z0-9-]*) \| [^|\n]+ \| ([^|\n]+) \|', section(text, 'The team'), re.M)
        self.assertEqual(len(cells), len(names))
        for name, cell in cells:
            fm, _ = front_of(os.path.join(TEAM, '.github', 'agents', name + '.agent.md'))
            self.assertEqual(re.findall(r'`([^`]+)`', cell), fm['tools'], name)

    def test_leks_names_exist(self):
        files = [p for p in kit_files() if p.endswith(('.md', '.toml'))]
        self.assertEqual([x for p in files for x in leks_name_problems(p)], [])
        # the scan sees what it should: the reweave skill uses the neighbours tool, the template the check command
        text = read(os.path.join(SKILLS, 'leks-reweave', 'SKILL.md'))
        self.assertTrue(any(m.group(1) == 'neighbours' for rx in _TOOL_REFS for m in rx.finditer(text)))
        pieces = _code_text(read(os.path.join(TEMPLATE, 'AGENTS.md')))
        self.assertIn('check', [m.group(1) for s in pieces for m in _CMD.finditer(s)])

    def test_contract_types_exist(self):
        files = [p for p in kit_files() if p.endswith(('.md', '.toml'))]
        files += [os.path.join(REPO, p) for p in CONTRACT_DOCS]
        self.assertEqual([x for p in files for x in contract_problems(p)], [])
        # the docs that define the contracts name exactly the types of leks/config.py
        types = set(config.CONTRACT_TYPES)
        rows = [r for r in read(os.path.join(REPO, 'docs', 'configuration.md')).splitlines()
                if r.startswith('| `type` |')]
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(re.findall(r'"([a-z-]+)"', rows[0].split('|')[2])), types)
        how = read(os.path.join(REPO, 'docs', 'how-it-works.md'))
        self.assertEqual(set(re.findall(r'^\| `"type": "([a-z-]+)"` \|', how, re.M)), types)


class DecisionLog(unittest.TestCase):
    """the note template in decisions/DECISIONS.md builds into the status, date and successor LEKS reads"""

    def template(self):
        blocks = [c for info, c in md_blocks(read(os.path.join(TEMPLATE, 'decisions', 'DECISIONS.md')))
                  if info == 'markdown']
        self.assertEqual(len(blocks), 1)
        return blocks[0]

    def fill(self, tpl, **kw):
        t = tpl
        for old, new in (('# D-NN: the decision in a few words', '# D-%(num)s: %(title)s'),
                         ('Status: current', 'Status: %(status)s'), ('Date: YYYY-MM-DD', 'Date: %(date)s'),
                         ('Revisit: YYYY-MM-DD, or the event that should reopen it', 'Revisit: %(revisit)s'),
                         ('Supersedes: none', 'Supersedes: %(supersedes)s'),
                         ('The recommendation in one or two sentences, with the reason.', '%(body)s')):
            self.assertIn(old, t)
            t = t.replace(old, new % kw, 1)
        return t

    def test_head_lines_fit_the_zone(self):
        head = []
        for line in self.template().split('\n')[1:]:
            if line.startswith('## '):
                break
            if line.strip():
                head.append(line)
        self.assertLessEqual(len(head), 8)
        self.assertEqual([h.split(':')[0] for h in head[:4]], ['Status', 'Date', 'Revisit', 'Supersedes'])

    def test_front_matter_status_wins_so_the_skill_sets_it(self):
        # a note written for 3.0 keeps status: in front matter, and leks reads that before the line under the title
        note = ('---\nstatus: %s\ndate: 2026-01-10\n---\n# D-01: tile cache\n\nStatus: superseded by D-02\n'
                'Date: 2026-01-10\nRevisit: 2026-04-01\nSupersedes: none\n')
        for fm_status, want in (('current', 'current'), ('superseded', 'superseded')):
            fm, took = chunking.meta_of((note % fm_status).split('\n'))
            self.assertEqual(chunking.status_of('note', 'decisions/D-01.md', fm, {}, took)[0], want)
        # so superseding such a note has to change its front matter too, and the texts that say how do say so
        for rel in ('.claude/skills/supersede-decision/SKILL.md', '.claude/skills/leks-reweave/SKILL.md',
                    'decisions/DECISIONS.md'):
            text = read(os.path.join(TEMPLATE, *rel.split('/')))
            self.assertRegex(text, r'front matter[^\n]*`status:`[^\n]*`?superseded', rel)

    def test_head_lines_below_a_heading_are_lost(self):
        moved = self.template().replace('Status: current\n', '', 1)
        moved = moved.replace('## Decision\n', '## Decision\n\nStatus: current\n', 1)
        self.assertNotIn('status', chunking.head_fields(moved.split('\n')))

    def test_a_superseded_decision_ranks_below_its_successor(self):
        tpl = self.template()
        old = self.fill(tpl, num='01', title='tile cache evicts the least recently used tile',
                        status='superseded by D-02', date='2026-01-10', revisit='2026-04-01', supersedes='none',
                        body='The tile cache evicts the least recently used tile, it had the best hit rate.')
        new = self.fill(tpl, num='02', title='tile cache evicts the oldest tile', status='current',
                        date='2026-03-02', revisit='2026-06-01', supersedes='D-01',
                        body='The tile cache evicts the oldest tile, it keeps the frame time flat.')
        hf = chunking.head_fields(new.split('\n'))
        self.assertEqual((hf.get('status'), hf.get('date'), hf.get('revisit'), hf.get('supersedes')),
                         ('current', '2026-03-02', '2026-06-01', 'D-01'))
        home, cfg = helpers.make_home(profiles={'architect': {'roots': ['notes']}})
        try:
            dst = [r for r in cfg.roots if r.name == 'notes'][0].path
            shutil.rmtree(dst)
            write(os.path.join(dst, 'decisions', 'D-01-tile-eviction.md'), old)
            write(os.path.join(dst, 'decisions', 'D-02-tile-eviction.md'), new)
            shutil.copy(os.path.join(TEMPLATE, 'decisions', 'DECISIONS.md'), os.path.join(dst, 'decisions'))
            build.update_all(cfg, log=lambda *a: None)
            res = query.Engine(cfg, 'architect').search('tile cache eviction', k=5)['results']
            paths = [r['path'] for r in res]
            i_new = [i for i, p in enumerate(paths) if p.endswith('D-02-tile-eviction.md')]
            i_old = [i for i, p in enumerate(paths) if p.endswith('D-01-tile-eviction.md')]
            self.assertTrue(i_new and i_old, paths)
            self.assertLess(i_new[0], i_old[0], paths)
            self.assertEqual((res[i_new[0]]['status'], res[i_new[0]]['date']), ('current', '2026-03-02'))
            self.assertEqual(res[i_old[0]]['status'], 'superseded')
            self.assertTrue((res[i_old[0]]['newer'] or '').endswith('D-02-tile-eviction.md'))
        finally:
            helpers.cleanup(home)


class NotesCheck(unittest.TestCase):
    """a fresh copy of the template passes the notes check, and a dead link makes it exit 2 as AGENTS.md says"""

    def test_fresh_copy_then_a_dead_link(self):
        home, _ = helpers.make_home(profiles={'architect': {'roots': ['notes']}})
        try:
            root = os.path.join(home, 'proj')
            notes = []
            for rel in ('AGENTS.md', 'CLAUDE.md', 'decisions/DECISIONS.md'):
                p = os.path.join(root, *rel.split('/'))
                write(p, read(os.path.join(TEMPLATE, *rel.split('/'))))
                notes.append(p)
            err = io.StringIO()
            self.assertEqual(check.run([root], notes, stdin=io.BytesIO(b''), err=err), 0, err.getvalue())
            self.assertEqual(err.getvalue(), '')
            bad = os.path.join(root, 'decisions', 'D-01-x.md')
            write(bad, '# D-01: x\n\nStatus: current\n\nSee [[D-09-missing]].\n')
            err = io.StringIO()
            self.assertEqual(check.run([root], [bad], stdin=io.BytesIO(b''), err=err), 2)
            self.assertIn('D-09-missing', err.getvalue())
        finally:
            helpers.cleanup(home)

    def test_a_run_by_hand_names_the_notes(self):
        # a run without --changed lists a note once and is quiet on the next run, the problem still there. so the
        # texts that tell an agent to run the check by hand give it --changed, which looks at the named notes
        # every time
        home, _ = helpers.make_home(profiles={'architect': {'roots': ['notes']}})
        try:
            root = os.path.join(home, 'proj')
            write(os.path.join(root, 'AGENTS.md'), read(os.path.join(TEMPLATE, 'AGENTS.md')))
            def run(changed=None):
                return check.run([root], changed, stdin=io.BytesIO(b''), err=io.StringIO())
            self.assertEqual(run(), 0)   # the first run in a folder records
            bad = os.path.join(root, 'decisions', 'D-01-x.md')
            write(bad, '# D-01: x\n\nStatus: current\n\nSee [[D-09-missing]].\n')
            self.assertEqual([run(), run()], [2, 0])
            self.assertEqual([run([bad]), run([bad])], [2, 2])
        finally:
            helpers.cleanup(home)
        for rel in ('AGENTS.md', 'README.md', '.claude/skills/leks-index-health/SKILL.md',
                    '.claude/skills/leks-reweave/SKILL.md', '.claude/skills/docs-update/SKILL.md',
                    '.claude/skills/leks-close-session/SKILL.md'):
            text = read(os.path.join(TEMPLATE, *rel.split('/')))
            for line in text.splitlines():
                if line.startswith('5. Once the files are in place'):
                    continue   # the template README's one run that sets the record
                for m in re.finditer(r'`python3 -m leks check([^`]*)`', line):
                    self.assertRegex(m.group(1), r'--changed|--accept', '%s: %s' % (rel, line))
            self.assertIn('leks check --changed', text, rel)


# ---- the checks fail on broken files --------------------------------------------------------------------------

GOOD_SKILL = """---
name: %s
description: Does one thing. Use it when that thing comes up.
---

# One thing

## Steps

1. `search` for it.
2. `read` the lines.
3. Write it down.

## Done when

- It is written down.
"""


class CheckerSelfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = helpers.short_tmpdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, rel, text):
        p = os.path.join(self.tmp, *rel.split('/'))
        write(p, text)
        return p

    def test_the_parser_holds_its_subset(self):
        ok = parse_front(['# a comment', 'name: x', "tools: ['a', 'b/*']", 'handoffs:', '  - label: L',
                          '    send: false'], 't')
        self.assertEqual(ok, {'name': 'x', 'tools': ['a', 'b/*'], 'handoffs': [{'label': 'L', 'send': False}]})
        for bad in (['name: a', 'name: b'], ['description: a: b'], ['description: x #y'], ['meta:', '  k: v'],
                    ['\tname: x'], ['name: *x'], ['name:'], ["tools: ['a', 'b]"], ['name: "a\\n"']):
            with self.assertRaises(FrontMatterError, msg=bad):
                parse_front(bad, 't')
        with self.assertRaises(FrontMatterError):
            split_front('---\nname: x\n')

    def test_toml_subset(self):
        text = 'a = "x"  # c\n[t.u]\nb = ["p", "q"]\nn = 120\nc = """\nline\n"""\n'
        want = {'a': 'x', 't': {'u': {'b': ['p', 'q'], 'n': 120, 'c': 'line\n'}}}
        self.assertEqual(toml_subset(text, 't'), want)
        if tomllib is not None:
            self.assertEqual(tomllib.loads(text), want)
        for bad in ('a = 1.5\n', 'a = 012\n', 'a = true\n', 'a = "x"\na = "y"\n', 'c = """\nback\\slash\n"""\n',
                    'c = """\nnever\n', 'a = [1]\n', 'a = \'x\'\n'):
            with self.assertRaises(TomlError, msg=bad):
                toml_subset(bad, 't')

    def test_skill_check(self):
        base = os.path.join(self.tmp, 'skills')
        self.put('skills/good-one/SKILL.md', GOOD_SKILL % 'good-one')
        self.assertEqual(skill_problems(os.path.join(base, 'good-one')), [])
        cases = {
            'Bad-Name': GOOD_SKILL % 'Bad-Name',
            'other-folder': GOOD_SKILL % 'not-the-folder',
            'long-text': GOOD_SKILL.replace('Does one thing.', 'x' * 1025) % 'long-text',
            'extra-key': (GOOD_SKILL % 'extra-key').replace('---\n\n', 'when_to_use: later\n---\n\n', 1),
            'no-done': (GOOD_SKILL % 'no-done').replace('## Done when', '## Finally'),
            'no-tool': (GOOD_SKILL % 'no-tool').replace('`search`', 'search').replace('`read`', 'read'),
            'dead-ref': (GOOD_SKILL % 'dead-ref').replace('Write it down.', 'Use the missing-one skill.'),
            'colon': (GOOD_SKILL % 'colon').replace('Does one thing.', 'Does: one thing.'),
        }
        for name, text in cases.items():
            self.put('skills/%s/SKILL.md' % name, text)
            self.assertTrue(skill_problems(os.path.join(base, name)), name)

    def copilot(self, files):
        d = os.path.join(self.tmp, 'gh')
        shutil.rmtree(d, ignore_errors=True)
        for n, text in files.items():
            self.put('gh/%s.agent.md' % n, text)
        return copilot_problems(d)

    def test_copilot_check(self):
        orch = "---\nname: orchestrator\ndescription: Plans.\ntools: ['agent', 'leks/*']\n---\n\n# O\n"
        role = ("---\nname: app-dev\ndescription: Codes.\ntools: ['read', 'leks/search']\nhandoffs:\n"
                "  - label: Back\n    agent: orchestrator\n    prompt: Check it.\n    send: false\n---\n\n# A\n")
        self.assertEqual(self.copilot({'orchestrator': orch, 'app-dev': role}), [])
        for bad in (role.replace('agent: orchestrator', 'agent: nobody'),
                    role.replace("'read'", "'fetch'"),
                    role.replace("'read'", "'agent'"),
                    role.replace("'leks/search'", "'leks/neighbors'"),
                    role.replace('send: false', 'send: no'),
                    role.replace('    prompt: Check it.\n', ''),
                    role.replace('description: Codes.\n', ''),
                    role.replace('name: app-dev', 'name: coder'),
                    role.replace('handoffs:', 'infer: true\nhandoffs:')):
            self.assertTrue(self.copilot({'orchestrator': orch, 'app-dev': bad}), bad)
        self.assertTrue(self.copilot({'orchestrator': orch.replace("'agent', ", ''), 'app-dev': role}))

    def test_claude_check(self):
        d = os.path.join(self.tmp, 'cl')
        orch = '---\nname: orchestrator\ndescription: Plans.\n---\n'
        role = '---\nname: review\ndescription: Reviews.\ndisallowedTools: Agent, Edit, Write, NotebookEdit\n---\n'
        self.put('cl/orchestrator.md', orch)
        self.put('cl/review.md', role)
        self.assertEqual(claude_problems(d), [])
        for bad in (role.replace('name: review\n', ''), role.replace('Agent, ', ''), role.replace('Edit', 'Shell'),
                    role.replace(', Write', ''), role.replace('---\nname', '---\ncolour: red\nname'),
                    role.replace('Reviews.', 'Reviews: code.')):
            self.put('cl/review.md', bad)
            self.assertTrue(claude_problems(d), bad)
        self.put('cl/review.md', role)
        self.put('cl/orchestrator.md', orch.replace('Plans.\n', 'Plans.\ndisallowedTools: Agent\n'))
        self.assertTrue(claude_problems(d))

    def test_codex_check(self):
        d = os.path.join(self.tmp, 'cx')
        good = 'name = "app_dev"\ndescription = "Codes."\ndeveloper_instructions = """\nDo it.\n"""\n'
        self.put('cx/app-dev.toml', good)
        self.assertEqual(codex_problems(d), [])
        for bad in (good.replace('"app_dev"', '"app-dev"'), good.replace('description = "Codes."\n', ''),
                    good.split('developer_instructions')[0], good + 'sandbox_mode = "x"\n',
                    good.replace('Do it.', 'Do \\ it.')):
            self.put('cx/app-dev.toml', bad)
            self.assertTrue(codex_problems(d), bad)

    def test_team_check(self):
        team = os.path.join(self.tmp, 'team')
        shutil.copytree(TEAM, team)
        self.assertEqual(team_problems(team), [])
        os.remove(os.path.join(team, '.codex', 'agents', 'docs.toml'))
        self.assertTrue(team_problems(team))
        shutil.rmtree(team)
        shutil.copytree(TEAM, team)
        p = os.path.join(team, '.claude', 'agents', 'review.md')
        write(p, read(p).replace('description: Reviews', 'description: Looks at'))
        self.assertTrue(team_problems(team))
        shutil.rmtree(team)
        shutil.copytree(TEAM, team)
        p = os.path.join(team, 'AGENTS.team.md')
        write(p, read(p).replace('the task-brief skill', 'the brief-writer skill'))
        self.assertTrue(team_problems(team))
        # one host's copy of a role loses a rule
        for rel, old, new in (('.codex/agents/review.toml', '- Edit nothing.', '- Fix each finding in place.'),
                              ('.github/agents/knowledge.agent.md', 'Never rewrite', 'Rewrite')):
            shutil.rmtree(team)
            shutil.copytree(TEAM, team)
            p = os.path.join(team, *rel.split('/'))
            self.assertIn(old, read(p))
            write(p, read(p).replace(old, new, 1))
            self.assertTrue(team_problems(team), rel)

    def test_hook_check(self):
        tpl = os.path.join(self.tmp, 'tpl')
        shutil.copytree(TEMPLATE, tpl)
        self.assertEqual(hook_problems(tpl), [])
        brief = '{"type": "command", "bash": "python3 -m leks hook brief", "env": {"LEKS_PROFILE": "architect"}, ' \
                '"timeoutSec": 10},\n'
        for rel, old, new in (('.github/hooks/leks.json', brief, ''),
                              ('.github/hooks/leks.json', 'leks hook brief', 'leks hook warm'),
                              ('.github/hooks/leks.json', '"cwd": ".", ', ''),
                              ('.github/hooks/leks.json', '"timeoutSec": 20', '"timeoutSec": 10'),
                              ('.github/hooks/leks.json', '"sessionStart"', '"sessionEnd"'),
                              ('.vscode/settings.json', '"chat.useClaudeHooks": false', '"chat.useClaudeHooks": true'),
                              ('.vscode/settings.json', '"chat.useClaudeHooks": false', '"x": false'),
                              ('.vscode/mcp.json', '"LEKS_PROFILE": "architect"',
                               '"LEKS_PROFILE": "architect", "LEKS_CONFIG": "/p/config.json"'),
                              ('.mcp.json', '{"LEKS_PROFILE": "architect"}', '{}'),
                              ('.claude/settings.json', '"timeout": 20', '"timeout": 30'),
                              ('.codex/hooks.json', 'hook update --profile', 'hook warm --profile'),
                              ('.codex/hooks.json', '"matcher": "mcp__leks__search|mcp__leks__tried"',
                               '"matcher": "mcp__leks__search"')):
            shutil.rmtree(tpl)
            shutil.copytree(TEMPLATE, tpl)
            p = os.path.join(tpl, *rel.split('/'))
            self.assertIn(old, read(p), (rel, old))
            write(p, read(p).replace(old, new, 1))
            self.assertTrue(hook_problems(tpl), (rel, old, new))
        # a config per project: the same LEKS_CONFIG in every env and --config on every codex hook passes
        shutil.rmtree(tpl)
        shutil.copytree(TEMPLATE, tpl)
        for rel in ('.mcp.json', '.claude/settings.json', '.github/hooks/leks.json'):
            p = os.path.join(tpl, *rel.split('/'))
            write(p, read(p).replace('{"LEKS_PROFILE": "architect"}',
                                     '{"LEKS_PROFILE": "architect", "LEKS_CONFIG": "/p/config.json"}'))
        p = os.path.join(tpl, '.vscode', 'mcp.json')
        write(p, read(p).replace('{"LEKS_PROFILE": "architect"}',
                                 '{"LEKS_PROFILE": "architect", "LEKS_CONFIG": "${workspaceFolder}/config.json"}'))
        p = os.path.join(tpl, '.codex', 'config.toml')
        write(p, read(p) + 'LEKS_CONFIG = "/p/config.json"\n')
        p = os.path.join(tpl, '.codex', 'hooks.json')
        self.assertTrue(hook_problems(tpl))   # the codex hooks lack --config yet
        write(p, read(p).replace('--profile architect', '--profile architect --config /p/config.json'))
        self.assertEqual(hook_problems(tpl), [])

    def test_hosts_doc_check(self):
        doc = os.path.join(REPO, 'docs', 'hosts.md')
        self.assertEqual(hosts_doc_problems(doc, TEMPLATE), [])
        for old, new in (('| `agentStop` |', '| `stop` |'), ('| none |', '| `preToolUse` |'),
                         ('| `leks hook update` |', '| `leks hook refresh` |')):
            self.assertIn(old, read(doc))
            bad = self.put('docs/hosts.md', read(doc).replace(old, new, 1))
            self.assertTrue(hosts_doc_problems(bad, TEMPLATE), old)
        # and the template side: a hook the table does not name
        tpl = os.path.join(self.tmp, 'tpl')
        shutil.copytree(TEMPLATE, tpl)
        p = os.path.join(tpl, '.github', 'hooks', 'leks.json')
        write(p, read(p).replace('"agentStop"', '"subagentStop"'))
        self.assertTrue(hosts_doc_problems(doc, tpl))

    def test_code_word_check(self):
        good = self.put('ok.md', 'Call `search`, then `read` and `mcp__leks__tried`. Its status is `current`.\n'
                                 '`python3 -m leks check` and `D-07` are no single word.\n\n```\nlookup\n```\n')
        self.assertEqual(code_word_problems(good, self.tmp), [])
        for text in ('Use `lookup` to list the lines.\n', 'Then `neighbors` it.\n', 'See `mcp__leks__find`.\n'):
            self.assertTrue(code_word_problems(self.put('bad.md', text), self.tmp), text)
        toml = 'name = "x"\ndescription = "y"\ndeveloper_instructions = """\nUse `lookup`.\n"""\n'
        self.assertTrue(code_word_problems(self.put('a.toml', toml), self.tmp))

    def test_link_check(self):
        self.put('docs/there.md', '# There\n\n## A part\n')
        good = self.put('docs/here.md', '[x](there.md#a-part) [y](#here) [z](https://example.org/x) `[q](nope.md)`\n'
                                        '\n```\n[w](nope.md)\n```\n<!-- [v](nope.md) -->\n\n# Here\n')
        self.assertEqual(link_problems(good, self.tmp), [])
        for text in ('[x](nope.md)\n', '[x](there.md#nope)\n', 'see [[nowhere]]\n', '[x](../../escape.md)\n'):
            self.assertTrue(link_problems(self.put('docs/bad.md', text), self.tmp), text)

    def test_word_check(self):
        self.assertEqual(word_problems(self.put('ok.md', 'A plain line about a cache.\n'), self.tmp), [])
        for text in ('About my ' + _w('the', 'sis') + '.\n', 'Runs on ' + _w('vision', 'OS') + '.\n',
                     'The ' + _w('nov', 'el') + ' part.\n', 'See E' + '3.\n', 'dash ' + chr(0x2014) + '\n',
                     _w('Co-', 'Authored-By') + ': x\n'):
            self.assertTrue(word_problems(self.put('bad.md', text), self.tmp), text)

    def test_config_check(self):
        tpl = os.path.join(self.tmp, 'tpl')
        shutil.copytree(TEMPLATE, tpl)
        self.assertEqual(config_problems(tpl), [])
        for rel, old, new in (('.vscode/mcp.json', '"serve"', '"server"'),
                              ('.mcp.json', '"python3"', '"python"'),
                              ('.codex/config.toml', '"architect"', '"other"'),
                              ('.codex/config.toml', '[mcp_servers.leks]', '[mcp_servers.leks'),
                              ('.codex/config.toml', 'tool_timeout_sec = 120', 'tool_timeout_sec = 60'),
                              ('.codex/config.toml', 'tool_timeout_sec = 120', ''),
                              ('.claude/settings.json', 'python3 -m leks check', 'leks check'),
                              ('.github/hooks/leks.json', '"architect"', '"other"'),
                              ('.codex/hooks.json', 'hook brief --profile architect', 'hook brief --profile other'),
                              ('.codex/hooks.json', 'check --profile architect', 'check'),
                              ('.codex/hooks.json', 'python3 -m leks hook update', 'leks hook update'),
                              ('.vscode/settings.json', '{', '{,')):
            shutil.rmtree(tpl)
            shutil.copytree(TEMPLATE, tpl)
            p = os.path.join(tpl, *rel.split('/'))
            self.assertIn(old, read(p))
            write(p, read(p).replace(old, new, 1))
            self.assertTrue(config_problems(tpl), (rel, old, new))

    def test_leks_name_check(self):
        good = self.put('ok.md', 'Call `search`, then `read`. The `agent` tool starts it.\n\n'
                                 '```bash\npython3 -m leks check --root notes\nleks --profile p search x\n```\n'
                                 'With 3.1, call `neighbours` or run `leks neighbours a.md --was b.md`.\n')
        self.assertEqual(leks_name_problems(good, self.tmp), [])
        for text in ('Call `lookup` first.\n', 'The `fetch_notes` tool.\n', 'Use mcp__leks__find.\n',
                     '`python3 -m leks frobnicate`\n', '```\nleks check --bogus\n```\n',
                     'Call `neighbors` after an edit.\n', '`leks check --approve a.md`\n',
                     '`leks search --root x`\n'):
            self.assertTrue(leks_name_problems(self.put('bad.md', text), self.tmp), text)
        toml = 'name = "x"\ndescription = "y"\ndeveloper_instructions = """\nCall `lookup`.\n"""\n'
        self.assertTrue(leks_name_problems(self.put('a.toml', toml), self.tmp))
        # a toml comment is prose, the scan leaves it alone
        prose = self.put('b.toml', '# run `leks frobnicate` here\nname = "x"\n')
        self.assertEqual(leks_name_problems(prose, self.tmp), [])

    def test_contract_check(self):
        good = self.put('ok.md', 'With a `write-once` edit contract on `decisions/`, a changed note is listed.\n'
                                 'Edit contracts (`check.contracts`) take `glob`, `type`, `keep` and `successor`, '
                                 'and a frozen note under a contract keeps its text.\n'
                                 'A `source` line without the word is prose.\n')
        self.assertEqual(contract_problems(good, self.tmp), [])
        for text in ('Edit contracts take the types `write-once`, `living` and `source`.\n',
                     'With edit contracts the check lists any change to a source note.\n',
                     'An edit contract keeps a read-only note as it is.\n',
                     'Under a contract a write-twice note may change once.\n'):
            self.assertTrue(contract_problems(self.put('bad.md', text), self.tmp), text)

    def test_block_check(self):
        ok = self.put('a.md', '```json\n{"a": 1}\n```\n\n```toml\na = "b"\n```\n')
        self.assertEqual(block_problems([ok], self.tmp), [])
        for text in ('```json\n{"a": 1,}\n```\n', '```toml\na = b\n```\n'):
            self.assertTrue(block_problems([self.put('b.md', text)], self.tmp), text)


if __name__ == '__main__':
    unittest.main()
