"""MCP conformance: the real server process over stdio, the way docs/hosts.md starts it.

The host config runs `python3 -m leks serve` with LEKS_PROFILE in the environment
(examples/project-template/.mcp.json). These tests start exactly that against a small
words-only index and talk to it in two ways:

- SdkTests drive it with the official MCP Python SDK client (pip install mcp). They
  skip with a reason when the SDK is not installed; the CI job "mcp conformance"
  installs it.
- StdoutTests write raw JSON-RPC lines and read every byte the server prints, to
  check that stdout carries JSON-RPC messages and nothing else. They need only the
  standard library.

The tool list is read from the server, and the arguments for each tool are made
from its input schema, so a new tool is covered without editing this file.

Spec used for the checks (2025-11-25):
- lifecycle, version negotiation:
  https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- tools, input schemas and errors:
  https://modelcontextprotocol.io/specification/2025-11-25/server/tools
  (inputSchema MUST be a valid JSON Schema object, dialect 2020-12 unless $schema
  says otherwise; unknown tools and requests that break the CallToolRequest shape
  are protocol errors; input validation errors are tool results with isError true)
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from leks import server

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False

try:
    import jsonschema
except ImportError:
    jsonschema = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 90.0      # one whole session; a hang fails the test instead of blocking the job
PROFILE = 'main'
TOOLS = tuple(t['name'] for t in server.TOOLS)
SCHEMAS = {t['name']: t['inputSchema'] for t in server.TOOLS}

NOTES = {
    'design.md': '# Design\n\nThe tile cache keeps tiles of 256 pixels.\nBack to [[release]].\n',
    'release.md': '# Release checklist\n\nRun the render tests before tagging.\nSee [[design]].\n',
    'STATE.md': '# State\n\nThe tile cache is in review.\nLinks: [[design]], [[release]].\n',
}

# values for argument names the fixture can answer; anything else gets a type default
TEXT = {'query': 'tile cache size', 'proposal': 'make the tile cache bigger', 'path': 'notes/design.md'}
WHOLE = {'k': 3, 'start': 1, 'end': 3}


def _value(name, prop):
    if 'enum' in prop:
        return prop['enum'][0]
    kind = prop.get('type')
    if kind == 'string':
        return TEXT.get(name, 'tile cache')
    if kind == 'integer':
        return WHOLE.get(name, 1)
    if kind == 'number':
        return 1
    if kind == 'boolean':
        return False
    if kind == 'array':
        return []
    return {}


def valid_args(name):
    """Required arguments from the schema, plus the whole numbers the fixture knows how to use."""
    s = SCHEMAS[name]
    props = s.get('properties', {})
    keys = list(s.get('required', [])) + [k for k in props if k in WHOLE]
    return {k: _value(k, props[k]) for k in dict.fromkeys(keys)}


def bad_args(name):
    """Arguments the schema rejects: a required one missing, or of the wrong type."""
    s = SCHEMAS[name]
    props, req = s.get('properties', {}), s.get('required', [])
    out = []
    if req:
        out.append({})
        for k in req:
            wrong = 7 if props[k].get('type') == 'string' else 'seven'
            out.append(dict(valid_args(name), **{k: wrong}))
    for k, prop in props.items():
        if prop.get('type') == 'integer' and req:
            out.append(dict(valid_args(name), **{k: 'many'}))
    return out


VALID = {n: valid_args(n) for n in TOOLS}
BAD = {n: bad_args(n) for n in TOOLS if bad_args(n)}


class Fixture:
    """A temp home with three notes, a words-only config and a built index."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix='leksmcp')
        notes = os.path.join(self.dir, 'notes')
        os.makedirs(notes)
        for name, text in NOTES.items():
            with open(os.path.join(notes, name), 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
        self.config = os.path.join(self.dir, 'config.json')
        cfg = {
            'version': 3,
            'home': os.path.join(self.dir, 'home'),
            'roots': [{'name': 'notes', 'path': notes, 'kind': 'notes'}],
            'profiles': {PROFILE: {'roots': ['notes']}},
            'default_profile': PROFILE,
            'models': {'backend': 'torch', 'device': 'auto', 'weights_dir': None, 'embedder': None, 'reranker': None},
            'ocr': {'enabled': 'off', 'images': False, 'helper': None, 'max_pages': 40},
        }
        with open(self.config, 'w', encoding='utf-8') as f:
            json.dump(cfg, f)
        user = os.path.join(self.dir, 'user')
        os.makedirs(user)
        env = {k: v for k, v in os.environ.items() if not k.startswith('LEKS_')}
        # the source tree on the path, the user's own ~/.leks out of reach
        env.update({'LEKS_CONFIG': self.config, 'LEKS_PROFILE': PROFILE, 'PYTHONPATH': ROOT,
                    'HOME': user, 'USERPROFILE': user, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.env = env
        r = subprocess.run([sys.executable, '-m', 'leks', 'build'], cwd=self.dir, env=env,
                           capture_output=True, timeout=180)
        if r.returncode != 0:
            raise RuntimeError('leks build failed: %s' % r.stderr.decode('utf-8', 'replace')[-600:])

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def pick(obj, *names):
    """An attribute under its SDK 2.x name or its 1.x name."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError(names[0])


def schema_errors(schema, args):
    """Validation messages for args against a tool input schema (2020-12 unless $schema says otherwise)."""
    cls = jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)
    return [e.message for e in cls(schema).iter_errors(args)]


async def _session(fx, errlog):
    """One full session. Returns plain data, the asserts run outside the event loop."""
    params = StdioServerParameters(command=sys.executable, args=['-m', 'leks', 'serve'], env=fx.env, cwd=fx.dir)
    out = {'valid': {}, 'bad': {}}
    async with stdio_client(params, errlog=errlog) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            info = pick(init, 'server_info', 'serverInfo')
            caps = pick(init, 'capabilities')
            out['init'] = {'protocol': pick(init, 'protocol_version', 'protocolVersion'),
                           'name': info.name, 'version': info.version,
                           'tools_cap': getattr(caps, 'tools', None) is not None}
            listed = await s.list_tools()
            out['tools'] = [{'name': t.name, 'description': t.description,
                             'schema': pick(t, 'input_schema', 'inputSchema')} for t in listed.tools]
            for name, args in VALID.items():
                r = await s.call_tool(name, args)
                out['valid'][name] = (pick(r, 'is_error', 'isError'), [c.type for c in r.content],
                                      r.content[0].text if r.content else '')
            for name, cases in BAD.items():
                out['bad'][name] = []
                for args in cases:
                    r = await s.call_tool(name, args)
                    out['bad'][name].append((args, pick(r, 'is_error', 'isError'),
                                             r.content[0].text if r.content else ''))
            # an unknown tool is a protocol error (-32602), never a crash
            try:
                r = await s.call_tool('no_such_tool', {})
                out['unknown'] = ('result', pick(r, 'is_error', 'isError'))
            except Exception as e:
                err = getattr(e, 'error', None)
                out['unknown'] = ('protocol error', getattr(err, 'code', None))
            await s.send_ping()
            out['alive'] = True
    return out


@unittest.skipUnless(HAVE_SDK, 'the MCP Python SDK is not installed (pip install mcp); the CI job "mcp conformance" runs these')
class SdkTests(unittest.TestCase):
    """The server as an MCP client library sees it."""

    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()
        cls.errpath = os.path.join(cls.fx.dir, 'server-stderr.txt')
        try:
            with open(cls.errpath, 'w', encoding='utf-8') as errlog:
                cls.rec = asyncio.run(asyncio.wait_for(_session(cls.fx, errlog), TIMEOUT))
        except BaseException:
            cls.fx.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def test_initialize(self):
        init = self.rec['init']
        # the SDK asks for its newest handshake version; the server speaks it and echoes it
        from mcp.client import session as sdk_session
        want = getattr(sdk_session, 'LATEST_HANDSHAKE_VERSION', None)
        if want in server.PROTOCOLS:
            self.assertEqual(init['protocol'], want)
        self.assertIn(init['protocol'], server.PROTOCOLS)
        self.assertEqual(init['name'], 'leks')
        import leks
        self.assertEqual(init['version'], leks.__version__)
        self.assertTrue(init['tools_cap'], 'the tools capability must be declared')

    def test_tool_list(self):
        self.assertEqual(sorted(t['name'] for t in self.rec['tools']), sorted(TOOLS))
        for t in self.rec['tools']:
            self.assertTrue(t['description'], t['name'])
            s = t['schema']
            self.assertIsInstance(s, dict, t['name'])
            self.assertEqual(s.get('type'), 'object', t['name'])
            for req in s.get('required', []):
                self.assertIn(req, s.get('properties', {}), '%s: required %s is not a property' % (t['name'], req))

    @unittest.skipIf(jsonschema is None, 'jsonschema is not installed here; the CI job "mcp conformance" installs it')
    def test_schemas_are_json_schema(self):
        for t in self.rec['tools']:
            s = t['schema']
            self.assertIsInstance(s, dict, t['name'])
            cls = jsonschema.validators.validator_for(s, default=jsonschema.Draft202012Validator)
            cls.check_schema(s)
            self.assertEqual(schema_errors(s, VALID[t['name']]), [], t['name'])
            for args in BAD.get(t['name'], []):
                self.assertNotEqual(schema_errors(s, args), [], '%s: %r should break the schema' % (t['name'], args))

    def test_valid_calls(self):
        for name in TOOLS:
            err, kinds, text = self.rec['valid'][name]
            self.assertFalse(err, '%s: %s' % (name, text[:200]))
            self.assertEqual(kinds, ['text'], name)
            self.assertTrue(text.strip(), name)
        self.assertIn('notes/design.md', self.rec['valid']['search'][2])
        self.assertIn('256 pixels', self.rec['valid']['read'][2])

    def test_bad_calls_are_error_results(self):
        for name, rows in self.rec['bad'].items():
            for args, err, text in rows:
                self.assertTrue(err, '%s %r should be an error result, got: %s' % (name, args, text[:200]))
                self.assertTrue(text.strip(), '%s %r: empty error text' % (name, args))
                # the server promises no paths in its messages
                self.assertNotIn(self.fx.dir, text)
                self.assertNotIn('Traceback', text)

    def test_unknown_tool_and_still_alive(self):
        self.assertEqual(self.rec['unknown'], ('protocol error', -32602))
        self.assertTrue(self.rec.get('alive'))


def _raw_lines():
    """Requests with ids, a notification and junk, the way a host may send them."""
    msgs = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
         'params': {'protocolVersion': '2025-11-25', 'capabilities': {},
                    'clientInfo': {'name': 'conformance', 'version': '0'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
    ]
    i = 10
    for name, args in VALID.items():
        msgs.append({'jsonrpc': '2.0', 'id': i, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
        i += 1
    for name, cases in BAD.items():
        for args in cases:
            msgs.append({'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
                         'params': {'name': name, 'arguments': args}})
            i += 1
    msgs.append({'jsonrpc': '2.0', 'id': 'unknown', 'method': 'tools/call',
                 'params': {'name': 'no_such_tool', 'arguments': {}}})
    msgs.append({'jsonrpc': '2.0', 'id': 'args', 'method': 'tools/call',
                 'params': {'name': TOOLS[0], 'arguments': 'not an object'}})
    lines = [json.dumps(m) for m in msgs]
    lines.insert(3, 'this line is not json')
    lines.append(json.dumps({'jsonrpc': '2.0', 'id': 'str-id', 'method': 'no/such/method'}))
    lines.append(json.dumps({'jsonrpc': '2.0', 'id': 99, 'method': 'ping'}))
    ids = [m['id'] for m in msgs if 'id' in m] + ['str-id', 99]
    # the junk line gets a parse error with a null id, right after the reply to tools/list
    ids.insert(2, None)
    return ('\n'.join(lines) + '\n').encode('utf-8'), ids


def serve_lines(fx, lines):
    """Run the server on these input lines, return the parsed output lines."""
    data = ('\n'.join(lines) + '\n').encode('utf-8')
    r = subprocess.run([sys.executable, '-m', 'leks', 'serve'], input=data, cwd=fx.dir, env=fx.env,
                       capture_output=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise AssertionError(r.stderr.decode('utf-8', 'replace')[-400:])
    return [json.loads(x) for x in r.stdout.decode('utf-8').splitlines() if x.strip()]


def init_line(version, mid=1):
    params = {'capabilities': {}, 'clientInfo': {'name': 'conformance', 'version': '0'}}
    if version is not None:
        params['protocolVersion'] = version
    return json.dumps({'jsonrpc': '2.0', 'id': mid, 'method': 'initialize', 'params': params})


class StdoutTests(unittest.TestCase):
    """Every byte on stdout is a JSON-RPC 2.0 message, one per line, one per request."""

    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def test_stdout_is_jsonrpc_only(self):
        data, ids = _raw_lines()
        r = subprocess.run([sys.executable, '-m', 'leks', 'serve'], input=data, cwd=self.fx.dir, env=self.fx.env,
                           capture_output=True, timeout=TIMEOUT)
        self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8', 'replace')[-400:])
        self.assertTrue(r.stdout.endswith(b'\n'))
        lines = r.stdout.split(b'\n')[:-1]
        got = []
        for raw in lines:
            # a text mode stdout on windows ends lines with \r\n; json allows the \r as whitespace
            text = raw[:-1] if raw.endswith(b'\r') else raw
            m = json.loads(text.decode('utf-8'))
            self.assertIsInstance(m, dict)
            self.assertEqual(m.get('jsonrpc'), '2.0')
            self.assertIn('id', m)
            self.assertEqual(('result' in m) + ('error' in m), 1, m)
            got.append(m)
            if HAVE_SDK:
                self._sdk_parse(text)
        # one reply per request, in order; none for the notification, a parse error for the junk line
        self.assertEqual([m['id'] for m in got], ids)
        by_id = {m['id']: m for m in got}
        self.assertEqual(by_id[None]['error']['code'], -32700)
        self.assertEqual(by_id['str-id']['error']['code'], -32601)
        self.assertEqual(by_id['unknown']['error']['code'], -32602)
        self.assertNotIn('no_such_tool', by_id['unknown']['error']['message'])
        self.assertEqual(by_id['args']['error']['code'], -32602)
        self.assertEqual(by_id[99]['result'], {})
        self.assertEqual(by_id[1]['result']['protocolVersion'], '2025-11-25')
        self.assertEqual(sorted(t['name'] for t in by_id[2]['result']['tools']), sorted(TOOLS))
        for i in range(10, 10 + len(VALID)):
            self.assertFalse(by_id[i]['result'].get('isError', False), by_id[i])
        for i in range(10 + len(VALID), 99):
            if i in by_id:
                self.assertIs(by_id[i]['result'].get('isError'), True, by_id[i])

    def test_version_negotiation(self):
        # a version the server speaks is echoed, anything else gets the newest one it speaks
        for asked, want in [(v, v) for v in server.PROTOCOLS] + [('2099-01-01', '2025-11-25'),
                                                                   (None, '2025-11-25'), (7, '2025-11-25')]:
            got = serve_lines(self.fx, [init_line(asked)])
            self.assertEqual(len(got), 1, asked)
            self.assertEqual(got[0]['result']['protocolVersion'], want, asked)

    def test_batch_for_2025_03_26(self):
        got = serve_lines(self.fx, [json.dumps([
            json.loads(init_line('2025-03-26')),
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]), '[]'])
        self.assertEqual(len(got), 2)
        self.assertEqual([m['id'] for m in got[0]], [1, 2])
        self.assertEqual(got[0][0]['result']['protocolVersion'], '2025-03-26')
        self.assertEqual(got[1]['error']['code'], -32600)

    def test_every_tool_has_a_valid_input_schema(self):
        # the shape the spec asks for, checked without any extra package
        for name, s in SCHEMAS.items():
            self.assertIsInstance(s, dict, name)
            self.assertEqual(s.get('type'), 'object', name)
            props = s.get('properties', {})
            self.assertIsInstance(props, dict, name)
            for k in s.get('required', []):
                self.assertIn(k, props, name)
            for k, prop in props.items():
                self.assertIn(prop.get('type'), ('string', 'integer', 'number', 'boolean', 'array', 'object'),
                              '%s.%s' % (name, k))
        if jsonschema is not None:
            for name, s in SCHEMAS.items():
                jsonschema.Draft202012Validator.check_schema(s)
                self.assertEqual(list(jsonschema.Draft202012Validator(s).iter_errors(VALID[name])), [], name)

    def _sdk_parse(self, text):
        # the SDK's own message model must accept every line
        from mcp import types
        msg = types.JSONRPCMessage
        if hasattr(msg, 'model_validate_json'):
            msg.model_validate_json(text)
        else:
            import pydantic
            pydantic.TypeAdapter(msg).validate_json(text)

    def test_closed_stdin_ends_the_server(self):
        r = subprocess.run([sys.executable, '-m', 'leks', 'serve'], input=b'', cwd=self.fx.dir, env=self.fx.env,
                           capture_output=True, timeout=TIMEOUT)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, b'')


if __name__ == '__main__':
    unittest.main()
