"""Real-protocol integration tests of the MCP server: the stdio server is started as a separate
process and spoken to in JSON-RPC lines, as VS Code does. Covered: handshake, every tool, invalid input, large
results, concurrent requests (pipelined and parallel servers), the error path, and that no tool writes anything.
Read-only tests use a fixture vault; the error path uses a fixture vault with corrupted data."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import unittest

import brain
import vault_fixture as vf

TOOLS = Path(__file__).resolve().parent
TRACKED = ('system/claims.json', 'system/source-registry.json', 'wiki/index.md', 'system/vault-config.json')


class Server:
    """One stdio MCP server process; requests may be sent before earlier replies are read."""

    def __init__(self, script=TOOLS / 'vault_mcp.py'):
        self.proc = subprocess.Popen([sys.executable, '-B', str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1, cwd=str(Path(script).parent.parent))

    def send(self, message):
        self.proc.stdin.write((message if isinstance(message, str) else json.dumps(message)) + '\n')
        self.proc.stdin.flush()

    def read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError('server closed its output: ' + self.proc.stderr.read()[-2000:])
        return json.loads(line)

    def call(self, mid, name, arguments=None):
        self.send({'jsonrpc': '2.0', 'id': mid, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments or {}}})
        return self.read()

    def close(self):
        self.proc.stdin.close()
        code = self.proc.wait(timeout=60)
        self.proc.stdout.close()
        self.proc.stderr.close()
        return code


def text(reply):
    return reply['result']['content'][0]['text']


VALID = {   # one valid call per tool, on the fixture vault
    'vault_search': {'question': 'Which build is on the headset?', 'limit': 3},
    'vault_answer_pack': {'question': 'Which build is on the headset?'},
    'vault_source': {'terms': 'mirror note'},
    'vault_media': {'path_or_words': 'mirror'},
    'vault_resolve': {'path_or_id': 'wiki/now.md'},
    'vault_claims': {'query': 'C-1'},
    'vault_status': {},
}


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.root = vf.make_vault(copy_tools=True)
        shutil.copyfile(str(TOOLS / 'vault_mcp.py'), str(cls.root / 'tools/vault_mcp.py'))
        for i in range(60):                                       # enough results for answers larger than 10 kB
            rel = 'raw/docs/build-note-%02d-installed-on-the-headset-after-the-delivery-check-in-october.md' % i
            (cls.root / rel).write_text(''.join('Build note %d.%d: build installed on the headset after delivery check %d.\n'
                                                % (i, j, j) for j in range(12)), encoding='utf-8')
            brain.register(cls.root, rel, 'fixture')
        claims = brain.read_json(cls.root / 'system/claims.json', {'claims': []})
        claims['claims'] += [{'id': 'N-%02d' % i, 'text': 'Build note %d: build installed on the headset' % i, 'page': 'wiki/delivery.md',
                              'confidence': 'reported', 'scope': 'fixture', 'as_of': '2026-10-19',
                              'sources': ['raw/docs/build-note-%02d-installed-on-the-headset-after-the-delivery-check-in-october.md' % i]}
                             for i in range(60)]
        brain.save_json(cls.root / 'system/claims.json', claims)
        brain.build(cls.root)
        (cls.root / 'wiki/index.md').touch()
        cls.before = {p: hashlib.sha256((cls.root / p).read_bytes()).hexdigest() for p in TRACKED}

    @classmethod
    def tearDownClass(cls):
        after = {p: hashlib.sha256((cls.root / p).read_bytes()).hexdigest() for p in TRACKED}
        vf.cleanup(cls.base)
        assert after == cls.before, 'an MCP tool changed a tracked file'      # the server is read-only

    def setUp(self):
        self.server = Server(self.root / 'tools/vault_mcp.py')

    def tearDown(self):
        self.assertEqual(self.server.close(), 0)                              # exits cleanly when stdin closes

    def test_handshake_listing_and_notifications(self):
        s = self.server
        s.send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}}})
        init = s.read()
        self.assertEqual(init['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(init['result']['serverInfo']['name'], 'xr-vault')
        s.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})          # no reply to a notification
        s.send({'jsonrpc': '2.0', 'id': 2, 'method': 'initialize', 'params': {'protocolVersion': '1999-01-01'}})
        self.assertEqual(s.read()['result']['protocolVersion'], '2025-06-18')       # unknown version: the server's latest
        s.send({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'})
        tools = s.read()['result']['tools']
        self.assertEqual(sorted(t['name'] for t in tools), sorted(VALID))
        for t in tools:
            self.assertEqual(t['inputSchema']['type'], 'object')
            self.assertFalse(t['inputSchema']['additionalProperties'])
        s.send({'jsonrpc': '2.0', 'id': 4, 'method': 'ping'})
        self.assertEqual(s.read(), {'jsonrpc': '2.0', 'id': 4, 'result': {}})

    def test_every_tool_answers(self):
        for i, (name, args) in enumerate(sorted(VALID.items()), 10):
            reply = self.server.call(i, name, args)
            self.assertEqual(reply['id'], i)
            self.assertFalse(reply['result']['isError'], (name, text(reply)[:300]))
            self.assertTrue(text(reply).strip(), name)
        status = json.loads(text(self.server.call(99, 'vault_status')))
        self.assertIsInstance(status['ok'], bool)                                # the vault's state is not this test's subject
        self.assertIsInstance(status['errors'], list)
        claim = json.loads(text(self.server.call(100, 'vault_claims', {'query': 'C-1'})))
        self.assertEqual(claim[0]['id'], 'C-1')

    def test_invalid_input(self):
        s = self.server
        bad = [('vault_search', {}, 'missing required argument(s): question'),
               ('vault_search', {'question': 'x', 'colour': 'red'}, 'unknown argument(s): colour'),
               ('vault_search', {'question': 7}, 'question must be a string'),
               ('vault_search', {'question': 'x', 'limit': 'many'}, 'limit must be an integer'),
               ('vault_search', {'question': 'x', 'limit': True}, 'limit must be an integer'),
               ('vault_search', {'question': 'x', 'limit': 51}, 'limit must be between 1 and 50'),
               ('vault_answer_pack', {'question': 'x', 'budget': 10}, 'budget must be between 300 and 6000'),
               ('vault_source', {'terms': 'x', 'content': 'maybe'}, 'content must be a boolean'),
               ('vault_status', {'force': True}, 'unknown argument(s): force')]
        for i, (name, args, message) in enumerate(bad, 1):
            reply = s.call(i, name, args)
            self.assertEqual(reply['error']['code'], -32602, (name, args))
            self.assertIn(message, reply['error']['message'])
        s.send({'jsonrpc': '2.0', 'id': 50, 'method': 'tools/call', 'params': {'name': 'vault_search', 'arguments': ['x']}})
        self.assertIn('arguments must be an object', s.read()['error']['message'])
        self.assertEqual(s.call(51, 'vault_delete_everything')['error']['code'], -32602)
        s.send({'jsonrpc': '2.0', 'id': 52, 'method': 'resources/list'})
        self.assertEqual(s.read()['error']['code'], -32601)
        s.send('{"jsonrpc": "2.0", "id": 53, "method": ')                          # not JSON
        self.assertEqual(s.read(), {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}})
        for message in ('[{"jsonrpc": "2.0", "id": 54, "method": "ping"}]', '42', '"ping"'):   # batch, number, string
            s.send(message)
            reply = s.read()
            self.assertEqual((reply['id'], reply['error']['code']), (None, -32600))
        s.send({'jsonrpc': '2.0', 'id': 55, 'method': 'tools/call', 'params': 'vault_status'})   # params not an object
        self.assertEqual(s.read()['error']['code'], -32602)
        self.assertEqual(s.call(56, 'vault_search', {'question': 'build', 'limit': '2'})['result']['isError'], False)  # "2" as before
        s.send({'jsonrpc': '2.0', 'id': 57, 'method': 'ping'})
        self.assertEqual(s.read()['id'], 57)                                        # still serving

    def test_large_results(self):
        s = self.server
        cases = [('vault_source', {'terms': 'build', 'content': True, 'limit': 50}),
                 ('vault_claims', {'query': 'build', 'limit': 50}),
                 ('vault_answer_pack', {'question': 'Which build is installed on the headset and how do the mirrors render?', 'budget': 6000}),
                 ('vault_search', {'question': 'build installed headset mirrors', 'limit': 50})]
        sizes = {}
        for i, (name, args) in enumerate(cases, 1):
            start = time.perf_counter()
            reply = s.call(i, name, args)
            self.assertLess(time.perf_counter() - start, 60, name)
            self.assertFalse(reply['result']['isError'], (name, text(reply)[:300]))
            sizes[name] = len(text(reply))
            body = text(reply)
            if name in ('vault_source', 'vault_claims', 'vault_search') and body.startswith('['):
                self.assertLessEqual(len(json.loads(body)), args['limit'])          # the limit is respected
        self.assertGreater(sizes['vault_source'], 10000)                           # large answers arrive intact in one line
        self.assertGreater(sizes['vault_claims'], 10000)

    def test_pipelined_requests_keep_their_ids(self):
        s = self.server
        names = sorted(VALID)
        requests = [(i, names[i % len(names)]) for i in range(1, 28)]
        for i, name in requests:                                                    # send everything before reading
            s.send({'jsonrpc': '2.0', 'id': i, 'method': 'tools/call', 'params': {'name': name, 'arguments': VALID[name]}})
        replies = [s.read() for _ in requests]
        self.assertEqual([r['id'] for r in replies], [i for i, _ in requests])     # answered in order, one each
        self.assertFalse(any(r['result']['isError'] for r in replies))

    def test_parallel_servers(self):
        results, errors = {}, []

        def session(n):
            try:
                srv = Server(self.root / 'tools/vault_mcp.py')
                got = [srv.call(k, name, VALID[name]) for k, name in enumerate(('vault_status', 'vault_answer_pack', 'vault_search', 'vault_claims'), 1)]
                results[n] = [g['result']['isError'] for g in got]
                errors.extend([] if srv.close() == 0 else ['exit code'])
            except Exception as exc:                                                  # surfaced by the assertion below
                errors.append(repr(exc))
        threads = [threading.Thread(target=session, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=300)
        self.assertEqual(errors, [])
        self.assertEqual(results, {n: [False] * 4 for n in range(4)})


class ErrorPathTests(unittest.TestCase):
    """A fixture vault with corrupted data: tools report errors and the server keeps serving."""

    def setUp(self):
        self.base, self.root = vf.make_vault(copy_tools=True)
        shutil.copyfile(str(TOOLS / 'vault_mcp.py'), str(self.root / 'tools/vault_mcp.py'))
        self.server = Server(self.root / 'tools/vault_mcp.py')

    def tearDown(self):
        self.server.close()
        vf.cleanup(self.base)

    def test_start_stop_and_restart(self):
        """Release: the server answers at once, stops at the end of its input and can be started again."""
        for attempt in range(3):
            started = time.monotonic()
            server = Server(self.root / 'tools/vault_mcp.py')
            server.send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}}})
            self.assertEqual(server.read()['result']['serverInfo']['name'], 'xr-vault')
            self.assertFalse(server.call(2, 'vault_status')['result']['isError'])
            self.assertEqual(server.close(), 0, attempt)
            self.assertLess(time.monotonic() - started, 30, attempt)

    def test_tool_failures_are_errors_and_the_server_survives(self):
        s = self.server
        self.assertFalse(s.call(1, 'vault_search', {'question': 'build'})['result']['isError'])
        (self.root / 'system/claims.json').write_text('{ broken', encoding='utf-8')
        (self.root / 'system/source-registry.json').write_text('{ broken', encoding='utf-8')
        claims = s.call(2, 'vault_claims', {'query': 'build'})
        self.assertTrue(claims['result']['isError'])
        self.assertIn('JSONDecodeError', text(claims))
        status = s.call(3, 'vault_status')                                          # the check's own report, not a crash
        self.assertFalse(status['result']['isError'])
        report = json.loads(text(status))
        self.assertFalse(report['ok'])
        self.assertIn('registry unreadable', report['errors'][0])
        s.send({'jsonrpc': '2.0', 'id': 5, 'method': 'ping'})
        self.assertEqual(s.read()['result'], {})


if __name__ == '__main__':
    unittest.main()
