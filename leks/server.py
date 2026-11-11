"""MCP server over stdio (JSON-RPC 2.0, one message per line).

Six read-only tools: search, read, locate, tried, state, status. The profile
comes from the environment (LEKS_PROFILE), never from a tool argument. No model
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
DEFAULT_PROTOCOL = '2024-11-05'

INSTRUCTIONS = (
    "Local search over this project's own record: the notes, documents and code under the folders this index was "
    'built from. Use search when the answer depends on what this project wrote down, decided or built. General '
    'knowledge needs no search. Search returns ranked passages cited by line for a note or code file and by page '
    'for a PDF; read the cited lines before relying on them. A line NEWER VERSION names a note that replaces the one '
    'above it. The last line of a search says which lanes ran: words only means the meaning model was not available, '
    'so a miss proves less. Split a many-part question into one search per part. Call tried before a design proposal '
    'or an implementation plan. Use locate for a file or a code definition by name, and grep for an exact string in code.'
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
    """Run one tool. Returns text for search and tried, a dict for the rest."""
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
    eng = _engine()
    if name == 'search':
        return eng.render(eng.search(q, k=k, root=_opt_str(a, 'root'), status=_opt_str(a, 'status'), rerank=True))
    if name == 'read':
        return eng.read(path, start, end)
    if name == 'locate':
        return eng.locate(q)
    if name == 'tried':
        return eng.tried(q)
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


def handle(m, tools=None):
    """One JSON-RPC message in, the reply dict out (None for notifications and junk)."""
    if not isinstance(m, dict) or m.get('id') is None:
        return None
    i, me = m['id'], m.get('method')
    params = m.get('params')
    if not isinstance(params, dict):
        params = {}
    if me == 'initialize':
        ver = params.get('protocolVersion')
        r = {
            'protocolVersion': ver if isinstance(ver, str) and ver else DEFAULT_PROTOCOL,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': SERVER_NAME, 'version': getattr(leks, '__version__', '3.0.0')},
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
        return {'jsonrpc': '2.0', 'id': i, 'error': {'code': -32601, 'message': 'no such method'}}
    return {'jsonrpc': '2.0', 'id': i, 'result': r}


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
        try:
            m = json.loads(line)
        except ValueError:
            continue
        reply = handle(m)
        if reply is None:
            continue
        sys.stdout.write(json.dumps(reply) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
