"""MCP server over stdio (JSON-RPC 2.0, one message per line).

Seven read-only tools: search, read, locate, tried, neighbours, state, status.
The profile comes from the environment (LEKS_PROFILE), never from a tool argument. No model
runs in this process: the engine asks the model daemon for numbers or goes on
with the words lanes.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys

import leks

SERVER_NAME = 'leks'
# protocol versions this server speaks, newest first. initialize echoes a version from this list
# and answers the newest one for anything else (the lifecycle rules of the spec)
PROTOCOLS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
LATEST_PROTOCOL = PROTOCOLS[0]
# json-rpc error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

INSTRUCTIONS = (
    "Local search over this project's own record: the notes, documents and code under the folders this index was "
    'built from. Use search when the answer depends on what this project wrote down, decided or built. General '
    'knowledge needs no search. Search returns ranked passages cited by line for a note or code file and by page '
    'for a PDF; read the cited lines before relying on them. A line NEWER VERSION names a note that replaces the one '
    'above it. The last line of a search says which lanes ran: words only means the meaning model was not available, '
    'so a miss proves less. Split a many-part question into one search per part. Call tried before a design proposal '
    'or an implementation plan. Use locate for a file or a code definition by name, and grep for an exact string in code. '
    'After editing a note, call neighbours and bring the listed notes in line.'
)

_STATUSES = ['current', 'record', 'historical-record', 'superseded', 'stale', 'unknown']

TOOLS = [
    {
        'name': 'search',
        'description': (
            "Search this project's notes, documents and code for passages about the project itself: what was "
            'written, decided or built. Returns ranked passages with path, line or page, status and date. The last '
            'line says which lanes ran. General knowledge needs no search.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string',
                          'description': 'The question in plain words; code names and file names as they are'},
                'k': {'type': 'integer', 'description': 'How many passages, default 5, at most 12'},
                'root': {'type': 'string', 'description': 'Only this configured folder'},
                'status': {'type': 'string', 'enum': _STATUSES},
            },
            'required': ['query'],
        },
    },
    {
        'name': 'read',
        'description': (
            'Read what search cites: a line range of a note or code file, at most 120 lines per call, or pages of a '
            'PDF or the text of an image from the index, at most 5 pages per call. Start and end are the numbers the '
            'citation names. A file locate only lists by name cannot be read here.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'The path as search cites it'},
                'start': {'type': 'integer', 'description': 'First line or page'},
                'end': {'type': 'integer', 'description': 'Last line or page'},
            },
            'required': ['path'],
        },
    },
    {
        'name': 'locate',
        'description': (
            'Find a file by name or path words, or a code definition with symbol:name. Filters: ext:pdf folder:x '
            'root:notes class:data size:>1mb n:30. Files that are not indexed are listed as locations only: name, '
            'size, folder.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {'query': {'type': 'string'}},
            'required': ['query'],
        },
    },
    {
        'name': 'tried',
        'description': (
            'Before a design proposal or an implementation plan: was this already tried, done or rejected in this '
            'project? Searches decisions, results, state notes and notes that report an outcome. At most 5 rows: '
            'what, date, status, source class, citation. When no row holds enough of the proposal it says nothing '
            'certain and gives up to 3 nearest decision or result rows, each labelled closest, not a match. Evidence '
            'only, it decides nothing.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {'proposal': {'type': 'string',
                                        'description': 'The proposal in one or two sentences'}},
            'required': ['proposal'],
        },
    },
    {
        'name': 'neighbours',
        'description': (
            'After editing a note or code file: the notes to bring in line with it. Lists notes that still hold '
            'numbers or sentences this file no longer has, the newer and older versions in its supersession chain, '
            'notes that link to it, files it links to, and the files closest to it in meaning, each with path, '
            'line, status, date and why. The old text is the indexed one; pass was when the index already holds '
            'the new text.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'The edited file, as search cites it'},
                'was': {'type': 'string', 'description': 'The text the file had before the edit'},
                'n': {'type': 'integer', 'description': 'How many notes, default 10, at most 30'},
            },
            'required': ['path'],
        },
    },
    {
        'name': 'state',
        'description': 'List the current state notes.',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'status',
        'description': 'Index size, build time, models, vector coverage and which lanes are available.',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
]

# a note can hold terminal control bytes and the reader's terminal may obey them. tab and newline stay
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')

# one slot of server state: config, engine, and the stamp the engine was opened under
_S = {'cfg': None, 'own': False, 'engine': None, 'stamp': None}


def clean(text):
    """Control bytes become spaces."""
    return _CONTROL.sub(' ', text)


def tools_for(cfg=None):
    """The tool list; every tool carries the always-load hint unless the config switches it off."""
    always = True
    if cfg is not None:
        try:
            always = bool((getattr(cfg, 'server', None) or {}).get('always_load', True))
        except Exception:
            always = True
    out = []
    for t in TOOLS:
        t = dict(t)
        if always:
            t['_meta'] = {'anthropic/alwaysLoad': True}
        out.append(t)
    return out


def _engine():
    """The engine for the active profile. A new build, new vectors or an edited config show in the stamp."""
    from leks import config, vecstore
    from leks.query import Engine

    cfg = _S['cfg']
    if cfg is None:
        cfg = _S['cfg'] = config.load()
        _S['own'] = True
    profile = config.active_profile(cfg)
    try:
        stamp = [profile, vecstore.stamp(cfg, profile)]
    except Exception:
        stamp = None
    if _S['engine'] is None or stamp is None or stamp != _S['stamp']:
        if _S['engine'] is not None and _S['own'] and getattr(cfg, 'source_path', None):
            # config file may have changed too
            try:
                cfg = _S['cfg'] = config.load(cfg.source_path)
                profile = config.active_profile(cfg)
            except Exception:
                pass
        old = _S['engine']
        _S['engine'] = None
        if old is not None:
            closer = getattr(old, 'close', None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        _S['engine'] = Engine(cfg, profile)
        _S['stamp'] = stamp
    return _S['engine']


class ToolError(Exception):
    """A message meant for the user: what failed and what to do. Never a path or a traceback."""


# json type names for an argument of the wrong kind, the value itself is never echoed
_KINDS = {bool: 'true or false', int: 'a number', float: 'a number', list: 'a list', dict: 'an object',
          type(None): 'null'}


def _text_arg(a, key, tool='search'):
    if key not in a:
        raise ToolError('%s needs the argument %s as text, it is missing' % (tool, key))
    v = a[key]
    if not isinstance(v, str):
        got = _KINDS.get(type(v), 'something else')
        raise ToolError('%s needs the argument %s as text, it got %s' % (tool, key, got))
    return v


def _int_arg(a, key, default, tool):
    v = a.get(key)
    if v is None:
        return default
    try:
        if isinstance(v, (list, dict)):
            raise TypeError
        return int(v)
    except (TypeError, ValueError, OverflowError):
        raise ToolError('%s needs the argument %s as a whole number' % (tool, key)) from None


def _opt_str(a, key):
    v = a.get(key)
    return v if isinstance(v, str) and v else None


def _names():
    return [t['name'] for t in TOOLS]


def call(name, args):
    """Run one tool. Returns text for search, tried and neighbours, a dict for the rest."""
    a = args if isinstance(args, dict) else {}
    if not isinstance(name, str) or name not in _names():
        # the asked name is never echoed back
        raise ToolError('unknown tool: the tools are ' + ', '.join(_names()[:-1]) + ' and ' + _names()[-1])
    # arguments first, so a bad call never waits for the index
    if name == 'search':
        q = _text_arg(a, 'query', name)
        k = max(1, min(_int_arg(a, 'k', 5, name), 12))
    elif name == 'read':
        path = _text_arg(a, 'path', name)
        start = _int_arg(a, 'start', 1, name)
        end = _int_arg(a, 'end', None, name)
    elif name in ('locate', 'tried'):
        q = _text_arg(a, 'query' if name == 'locate' else 'proposal', name)
    elif name == 'neighbours':
        path = _text_arg(a, 'path', name)
        was = a.get('was')
        if was is not None and not isinstance(was, str):
            raise ToolError('neighbours needs the argument was as text, it got %s'
                            % _KINDS.get(type(was), 'something else'))
        n = max(1, min(_int_arg(a, 'n', 10, name), 30))
    eng = _engine()
    if name == 'search':
        return eng.render(eng.search(q, k=k, root=_opt_str(a, 'root'), status=_opt_str(a, 'status'), rerank=True))
    if name == 'read':
        return eng.read(path, start, end)
    if name == 'locate':
        return eng.locate(q)
    if name == 'tried':
        return eng.tried(q)
    if name == 'neighbours':
        return eng.render_neighbours(eng.neighbours(path, n=n, was=was))
    if name == 'state':
        return eng.state()
    return eng.status()


# a path in a message: a slash, a backslash, ~ or a drive letter up front, never inside a word like owner/name
_PATH = re.compile(r'(?<![\w.])(?:~|[A-Za-z]:)?[/\\][^\s)]+')
_PAREN_PATH = re.compile(r'\s*\([^()]*[/\\][^()]*\)')


def _scrub(msg):
    """A message with its paths taken out."""
    return _PATH.sub('a path', _PAREN_PATH.sub('', msg)).strip()


def error_text(name, e):
    """One short English line for a failed call: what failed and what to do. No path, no traceback."""
    if isinstance(e, ToolError):
        return str(e)
    tool = name if isinstance(name, str) and name in _names() else 'the tool'
    if type(e).__name__ == 'ConfigError':
        msg = _scrub(str(e).split('\n')[0])[:300]
        if msg.startswith('unknown profile'):
            msg += '. Set LEKS_PROFILE to a profile the config defines, leks config show lists them'
        elif 'run leks' not in msg:
            msg += '. Check the config in a terminal with: leks config check'
        return '%s failed: %s' % (tool, msg)
    if isinstance(e, sqlite3.Error):
        return '%s failed: the index could not be read. Run leks build in a terminal, then try again' % tool
    if isinstance(e, OSError):
        return ('%s failed: a file leks needs could not be read. Run leks status in a terminal to see what is '
                'missing' % tool)
    return ('%s failed inside leks (%s). Try again, and if it fails again run leks status in a terminal'
            % (tool, type(e).__name__))


def _result_text(out):
    s = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
    return clean(s)


def _error(i, code, message):
    return {'jsonrpc': '2.0', 'id': i, 'error': {'code': code, 'message': message}}


def _call_problem(params):
    """Why a tools/call request breaks the CallToolRequest shape, or None. Never echoes the asked name."""
    if not isinstance(params, dict):
        return 'Invalid params: tools/call needs params as an object'
    name = params.get('name')
    if not isinstance(name, str) or not name:
        return 'Invalid params: tools/call needs the tool name as text'
    if name not in _names():
        return 'Unknown tool: the tools are ' + ', '.join(_names()[:-1]) + ' and ' + _names()[-1]
    args = params.get('arguments')
    if args is not None and not isinstance(args, dict):
        return 'Invalid params: arguments must be an object'
    return None


def handle(m, tools=None):
    """One JSON-RPC message in, the reply dict out (None for notifications and junk)."""
    if not isinstance(m, dict) or m.get('id') is None:
        return None
    i, me = m['id'], m.get('method')
    params = m.get('params')
    if me == 'tools/call':
        # unknown tools and requests that break the CallToolRequest shape are protocol errors;
        # argument values the tool cannot use come back as a tool result with isError
        bad = _call_problem(params)
        if bad:
            return _error(i, INVALID_PARAMS, bad)
    if not isinstance(params, dict):
        params = {}
    if me == 'initialize':
        ver = params.get('protocolVersion')
        r = {
            'protocolVersion': ver if ver in PROTOCOLS else LATEST_PROTOCOL,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': SERVER_NAME, 'version': getattr(leks, '__version__', '3.1.0')},
            'instructions': INSTRUCTIONS,
        }
    elif me == 'tools/list':
        r = {'tools': tools if tools is not None else tools_for(_S['cfg'])}
    elif me == 'ping':
        r = {}
    elif me == 'tools/call':
        try:
            out = call(params.get('name'), params.get('arguments') or {})
            r = {'content': [{'type': 'text', 'text': _result_text(out)}]}
        except Exception as e:
            # a short line that says what to do, never a path, note text or a traceback
            r = {'content': [{'type': 'text', 'text': clean(error_text(params.get('name'), e))}], 'isError': True}
    else:
        return _error(i, METHOD_NOT_FOUND, 'no such method')
    return {'jsonrpc': '2.0', 'id': i, 'result': r}


def reply_to_line(line):
    """The text to write back for one input line, or None.

    A batch (a JSON array, which protocol version 2025-03-26 allows) gets an array
    of the replies; an empty array is an invalid request. A line that is not JSON
    gets a parse error with a null id, as JSON-RPC 2.0 asks. So does a line nested
    deeper than the decoder goes, which would else end the server.
    """
    try:
        m = json.loads(line)
    except (ValueError, RecursionError):
        if not line.strip():
            return None
        return json.dumps(_error(None, PARSE_ERROR, 'Parse error'))
    if isinstance(m, list):
        if not m:
            return json.dumps(_error(None, INVALID_REQUEST, 'Invalid Request: empty batch'))
        out = [r for r in (_one(x) for x in m) if r is not None]
        return json.dumps(out) if out else None
    reply = _one(m)
    return None if reply is None else json.dumps(reply)


def _one(m):
    if not isinstance(m, dict):
        return _error(None, INVALID_REQUEST, 'Invalid Request: a message must be an object')
    return handle(m)


def main(cfg=None):
    """Serve on stdin and stdout until stdin closes."""
    _S.update(cfg=cfg, own=False, engine=None, stamp=None)
    if cfg is None:
        try:
            from leks import config
            _S['cfg'] = config.load()
            _S['own'] = True
        except Exception:
            # tool calls retry the load and report the exception type
            _S['cfg'] = None
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    for line in sys.stdin:
        out = reply_to_line(line)
        if out is None:
            continue
        sys.stdout.write(out + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
