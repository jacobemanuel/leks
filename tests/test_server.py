"""MCP server tests: the loop in process with a stand-in engine, then a real subprocess over a built index."""
from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import leks
from leks import build, config, server
from tests import helpers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES = ['search', 'read', 'locate', 'tried', 'neighbours', 'state', 'status']


class FakeEngine:
    """Records what the server asks for and returns canned output."""

    def __init__(self):
        self.calls = []
        self.out = 'plain text\nlanes line'

    def search(self, q, **kw):
        self.calls.append(('search', q, kw))
        return {'q': q}

    def render(self, r):
        return self.out

    def read(self, path, start, end):
        self.calls.append(('read', path, start, end))
        return {'path': path, 'text': 'a\x1bb'}

    def locate(self, q):
        self.calls.append(('locate', q))
        return {'files': []}

    def tried(self, p):
        self.calls.append(('tried', p))
        return 'nothing certain'

    def state(self):
        return {'state_notes': []}

    def status(self):
        raise ValueError('secret detail /some/private/path')


class InProcessTests(unittest.TestCase):
    def setUp(self):
        self.eng = FakeEngine()
        p = mock.patch.object(server, '_engine', return_value=self.eng)
        p.start()
        self.addCleanup(p.stop)

    def rpc(self, method, params=None, mid=1):
        m = {'jsonrpc': '2.0', 'id': mid, 'method': method}
        if params is not None:
            m['params'] = params
        return server.handle(m)

    def test_initialize_echoes_version_and_names_the_server(self):
        r = self.rpc('initialize', {'protocolVersion': '2025-03-26'})['result']
        self.assertEqual(r['protocolVersion'], '2025-03-26')
        self.assertEqual(r['serverInfo']['name'], 'leks')
        self.assertEqual(r['capabilities'], {'tools': {}})
        self.assertEqual(r['instructions'], server.INSTRUCTIONS)
        # no version or one it does not speak: the newest it does speak
        self.assertEqual(self.rpc('initialize')['result']['protocolVersion'], server.LATEST_PROTOCOL)
        self.assertEqual(self.rpc('initialize', {'protocolVersion': '1999-01-01'})['result']['protocolVersion'],
                         '2025-11-25')
        for v in server.PROTOCOLS:
            self.assertEqual(self.rpc('initialize', {'protocolVersion': v})['result']['protocolVersion'], v)

    def test_tools_list(self):
        tools = self.rpc('tools/list')['result']['tools']
        self.assertEqual([t['name'] for t in tools], NAMES)
        blob = json.dumps(tools).lower() + server.INSTRUCTIONS.lower()
        self.assertNotIn('use it first', blob)
        self.assertNotIn('before grep', blob)
        for t in tools:
            self.assertEqual(t['inputSchema']['type'], 'object')
            self.assertEqual(t['_meta'], {'anthropic/alwaysLoad': True})
        self.assertEqual(tools[0]['inputSchema']['required'], ['query'])
        self.assertIn('General knowledge needs no search', tools[0]['description'])

    def test_always_load_can_be_switched_off(self):
        cfg = SimpleNamespace(server={'always_load': False})
        self.assertTrue(all('_meta' not in t for t in server.tools_for(cfg)))
        self.assertTrue(all('_meta' in t for t in server.tools_for(None)))

    def test_ping_unknown_method_and_notifications(self):
        self.assertEqual(self.rpc('ping')['result'], {})
        e = self.rpc('no/such')
        self.assertEqual(e['error']['code'], -32601)
        self.assertIsNone(server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        self.assertIsNone(server.handle({'jsonrpc': '2.0', 'method': 'no/such'}))
        self.assertIsNone(server.handle([1, 2]))

    def test_lines_batches_and_parse_errors(self):
        one = json.loads(server.reply_to_line('{"jsonrpc": "2.0", "id": 7, "method": "ping"}\n'))
        self.assertEqual(one, {'jsonrpc': '2.0', 'id': 7, 'result': {}})
        # a batch (2025-03-26) answers every request in it and skips notifications
        out = json.loads(server.reply_to_line(json.dumps([
            {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'no/such'}, 5])))
        self.assertEqual([r.get('id') for r in out], [1, 2, None])
        self.assertEqual([r.get('error', {}).get('code') for r in out], [None, -32601, -32600])
        self.assertIsNone(server.reply_to_line(json.dumps([{'jsonrpc': '2.0', 'method': 'notifications/x'}])))
        self.assertEqual(json.loads(server.reply_to_line('[]'))['error']['code'], -32600)
        bad = json.loads(server.reply_to_line('{not json'))
        self.assertEqual((bad['id'], bad['error']['code']), (None, -32700))
        self.assertEqual(json.loads(server.reply_to_line('"text"'))['error']['code'], -32600)
        self.assertIsNone(server.reply_to_line('\n'))
        # nested deeper than the decoder goes: a parse error, and the server answers the next line
        deep = json.loads(server.reply_to_line('[' * 200000 + ']' * 200000))
        self.assertEqual((deep['id'], deep['error']['code']), (None, -32700))
        self.assertEqual(json.loads(server.reply_to_line('{"jsonrpc": "2.0", "id": 8, "method": "ping"}'))['id'], 8)

    def call(self, name, args=None):
        r = self.rpc('tools/call', {'name': name, 'arguments': args or {}})['result']
        return r, r['content'][0]['text']

    def test_search_arguments(self):
        r, text = self.call('search', {'query': 'tile cache', 'k': 99, 'root': 'notes', 'status': 'current'})
        self.assertNotIn('isError', r)
        self.assertEqual(text, 'plain text\nlanes line')
        _, q, kw = self.eng.calls[0]
        self.assertEqual((q, kw['k'], kw['root'], kw['status'], kw['rerank']), ('tile cache', 12, 'notes', 'current', True))
        self.call('search', {'query': 'x'})
        self.assertEqual(self.eng.calls[1][2]['k'], 5)
        self.assertIsNone(self.eng.calls[1][2]['root'])
        self.call('search', {'query': 'x', 'k': 0})
        self.assertEqual(self.eng.calls[2][2]['k'], 1)

    def test_profile_is_never_a_tool_argument(self):
        self.call('search', {'query': 'x', 'profile': 'orchestrator'})
        self.assertNotIn('profile', self.eng.calls[0][2])

    def test_json_tools(self):
        _, text = self.call('read', {'path': 'notes/a.md', 'start': 3, 'end': 9})
        self.assertEqual(self.eng.calls[0], ('read', 'notes/a.md', 3, 9))
        self.assertEqual(json.loads(text)['path'], 'notes/a.md')
        self.call('read', {'path': 'notes/a.md'})
        self.assertEqual(self.eng.calls[1], ('read', 'notes/a.md', 1, None))
        _, text = self.call('locate', {'query': 'ext:py tile'})
        self.assertEqual(json.loads(text), {'files': []})
        _, text = self.call('state')
        self.assertEqual(json.loads(text), {'state_notes': []})
        _, text = self.call('tried', {'proposal': 'use a ring buffer'})
        self.assertEqual(text, 'nothing certain')

    def test_control_bytes_become_spaces(self):
        self.eng.out = 'a\x1b[31mred\x07\x00b\tc\nd\x7f\x9fe'
        _, text = self.call('search', {'query': 'x'})
        self.assertEqual(text, 'a [31mred  b\tc\nd  e')
        _, text = self.call('read', {'path': 'notes/a.md'})
        self.assertNotRegex(text, r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')

    def assert_plain(self, text):
        # one short line, no traceback, no path, no exception detail
        self.assertNotIn('\n', text)
        self.assertNotIn('Traceback', text)
        self.assertNotIn('/', text)
        self.assertNotIn('secret', text)
        self.assertLess(len(text), 200)

    def test_errors_say_what_failed_and_what_to_do(self):
        r, text = self.call('status')
        self.assertTrue(r['isError'])
        self.assertEqual(text, 'status failed inside leks (ValueError). Try again, and if it fails again run '
                               'leks status in a terminal')
        self.assert_plain(text)
        r, text = self.call('search', {})
        self.assertEqual((r['isError'], text), (True, 'search needs the argument query as text, it is missing'))
        r, text = self.call('search', {'query': 5})
        self.assertEqual(text, 'search needs the argument query as text, it got a number')
        r, text = self.call('search', {'query': 'x', 'k': 'many'})
        self.assertEqual(text, 'search needs the argument k as a whole number')
        r, text = self.call('read', {'path': 'notes/a.md', 'end': [1]})
        self.assertEqual(text, 'read needs the argument end as a whole number')
        r, text = self.call('tried', {'proposal': None})
        self.assertEqual(text, 'tried needs the argument proposal as text, it got null')
        # an unknown tool and arguments that are not an object are protocol errors, the name is never echoed
        e = self.rpc('tools/call', {'name': 'nope /etc/passwd'})['error']
        self.assertEqual(e, {'code': -32602, 'message': 'Unknown tool: the tools are ' + ', '.join(
            server._names()[:-1]) + ' and ' + server._names()[-1]})
        self.assertEqual(self.rpc('tools/call', {'name': 'state', 'arguments': 'junk'})['error']['code'], -32602)
        self.assertEqual(self.rpc('tools/call', {'arguments': {}})['error']['code'], -32602)
        self.assertEqual(self.rpc('tools/call', [1])['error']['code'], -32602)
        r = self.rpc('tools/call', {'name': 'state'})['result']
        self.assertNotIn('isError', r)
        # a null number takes the default
        self.call('search', {'query': 'x', 'k': None})
        self.assertEqual(self.eng.calls[-1][2]['k'], 5)

    def test_error_text_by_kind(self):
        from leks.config import ConfigError
        import sqlite3
        cases = {
            ConfigError('no config: run leks init (not found: /srv/someone/leks/config.json)'):
                'search failed: no config: run leks init',
            ConfigError('no index for profile notes yet: run leks build'):
                'search failed: no index for profile notes yet: run leks build',
            ConfigError('cannot read config at ~/x/config.json'):
                'search failed: cannot read config at a path. Check the config in a terminal with: leks config check',
            ConfigError('models.embedder.hf_id must look like owner/name'):
                'search failed: models.embedder.hf_id must look like owner/name. Check the config in a terminal '
                'with: leks config check',
            ConfigError('unknown profile: nope'):
                'search failed: unknown profile: nope. Set LEKS_PROFILE to a profile the config defines, '
                'leks config show lists them',
            sqlite3.DatabaseError('file is not a database'):
                'search failed: the index could not be read. Run leks build in a terminal, then try again',
            PermissionError(13, 'denied', '/srv/x/search.sqlite'):
                'search failed: a file leks needs could not be read. Run leks status in a terminal to see what is '
                'missing',
        }
        for e, want in cases.items():
            self.assertEqual(server.error_text('search', e), want)
        self.assertTrue(server.error_text(None, KeyError('x')).startswith('the tool failed inside leks (KeyError)'))

    def test_config_error_from_the_engine_names_the_fix(self):
        from leks.config import ConfigError
        with mock.patch.object(server, '_engine', side_effect=ConfigError(
                'no config: run leks init (not found: /srv/someone/config.json)')):
            r, text = self.call('state')
        self.assertTrue(r['isError'])
        self.assertEqual(text, 'state failed: no config: run leks init')

    def test_server_text_is_generic(self):
        blob = server.INSTRUCTIONS + json.dumps(server.TOOLS)
        self.assertIn('NEWER VERSION', blob)
        self.assertIn('words only', blob)
        self.assertNotRegex(blob, '[%s%s]' % (chr(0x2013), chr(0x2014)))


class EngineSlotTests(unittest.TestCase):
    """The engine is reopened when the stamp changes, and the profile comes from the environment."""

    def setUp(self):
        server._S.update(cfg=SimpleNamespace(source_path=None), own=False, engine=None, stamp=None)
        self.addCleanup(server._S.update, cfg=None, own=False, engine=None, stamp=None)

    def test_reopen_on_stamp_change(self):
        stamps = iter([[1], [1], [2]])
        made = []

        def fake_engine(cfg, profile):
            made.append(profile)
            return object()

        with mock.patch.dict(os.environ, {'LEKS_PROFILE': 'architect'}), \
                mock.patch('leks.config.active_profile', side_effect=lambda c: os.environ['LEKS_PROFILE']), \
                mock.patch('leks.vecstore.stamp', side_effect=lambda c, p: next(stamps)), \
                mock.patch('leks.query.Engine', side_effect=fake_engine):
            a = server._engine()
            b = server._engine()
            c = server._engine()
        self.assertIs(a, b)
        self.assertIsNot(b, c)
        self.assertEqual(made, ['architect', 'architect'])


class Client:
    """One stdio server process."""

    def __init__(self, env):
        e = dict(os.environ)
        e.update(env)
        e['PYTHONPATH'] = ROOT
        e['PYTHONDONTWRITEBYTECODE'] = '1'
        self.proc = subprocess.Popen([sys.executable, '-m', 'leks', 'serve'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     encoding='utf-8', cwd=ROOT, env=e)
        self.n = 0

    def rpc(self, method, params=None):
        self.n += 1
        m = {'jsonrpc': '2.0', 'id': self.n, 'method': method}
        if params is not None:
            m['params'] = params
        self.proc.stdin.write(json.dumps(m) + '\n')
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError('server closed: ' + self.proc.stderr.read()[-1500:])
        r = json.loads(line)
        self.last_id = r['id']
        return r

    def call(self, name, args=None):
        r = self.rpc('tools/call', {'name': name, 'arguments': args or {}})['result']
        return r.get('isError', False), r['content'][0]['text']

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=30)
        finally:
            self.proc.stdout.close()
            self.proc.stderr.close()


class SubprocessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home, cfg = helpers.make_home(models=True)
        # a note with terminal control bytes in it
        notes = cfg.root('notes').path
        with open(os.path.join(notes, 'terminal.md'), 'w', encoding='utf-8') as f:
            f.write('# Terminal note\n\nThe zebrafish marker has \x1b[31mred\x07 text.\n')
        # no daemon runs in this test, so keep the connect wait short
        with open(cfg.source_path, encoding='utf-8') as f:
            d = json.load(f)
        d['budgets'].update({'connect': 0.3, 'embed': 1.0, 'rerank': 1.0, 'cold_rerank': 1.0, 'ping': 0.5})
        with open(cfg.source_path, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        cls.cfg_path = cfg.source_path
        build.update_all(config.load(cls.cfg_path), log=lambda *a: None)
        cls.env = {'LEKS_HOME': cls.home, 'LEKS_CONFIG': cls.cfg_path, 'LEKS_ALLOW_FAKE': '1',
                   'LEKS_PROFILE': 'orchestrator'}
        cls.c = Client(cls.env)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        helpers.cleanup(cls.home)

    def test_a_handshake_and_tool_list(self):
        r = self.c.rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}})['result']
        self.assertEqual(r['serverInfo']['name'], 'leks')
        self.assertEqual(r['serverInfo']['version'], leks.__version__)
        tools = self.c.rpc('tools/list')['result']['tools']
        self.assertEqual([t['name'] for t in tools], NAMES)
        self.assertNotIn('use it first', json.dumps(tools).lower())
        self.assertEqual(self.c.rpc('ping')['result'], {})
        self.assertEqual(self.c.rpc('bogus')['error']['code'], -32601)

    def test_b_search_ends_with_the_lanes_line(self):
        err, text = self.c.call('search', {'query': 'tile cache eviction oldest', 'k': 3})
        self.assertFalse(err, text)
        last = text.strip().splitlines()[-1]
        self.assertTrue(last.startswith('words') or last.startswith('lanes'), last)
        self.assertIn('word lanes:', last)
        self.assertRegex(text, r'(?m)^1\. ')

    def test_c_read_what_search_cites(self):
        _, text = self.c.call('search', {'query': 'frame graph pass order'})
        m = re.search(r'(?m)^1\. (\S+?)(?::\d|\s)', text)
        self.assertTrue(m, text)
        err, out = self.c.call('read', {'path': m.group(1), 'start': 1, 'end': 5})
        self.assertFalse(err, out)
        self.assertEqual(json.loads(out)['path'], m.group(1))
        err, out = self.c.call('read', {'path': 'notes/not-there.md'})
        self.assertIn('error', json.loads(out))

    def test_d_locate(self):
        err, out = self.c.call('locate', {'query': 'tile_cache'})
        self.assertFalse(err, out)
        self.assertTrue(json.loads(out)['files'])
        err, out = self.c.call('locate', {'query': 'symbol:evict_oldest'})
        self.assertFalse(err, out)
        self.assertTrue(json.loads(out)['symbols'])

    def test_e_tried(self):
        err, out = self.c.call('tried', {'proposal': 'evict the oldest tile first when the cache budget is full'})
        self.assertFalse(err, out)
        self.assertIsInstance(out, str)
        self.assertIn('lanes', out)

    def test_f_state_and_status(self):
        err, out = self.c.call('state')
        self.assertFalse(err, out)
        paths = [n['path'] for n in json.loads(out)['state_notes']]
        self.assertIn('notes/STATE.md', paths)
        err, out = self.c.call('status')
        self.assertFalse(err, out)
        st = json.loads(out)
        self.assertEqual(st['profile'], 'orchestrator')
        self.assertGreater(st['docs'], 5)
        self.assertNotIn(self.home, out)

    def test_g_control_bytes_never_reach_the_client(self):
        err, text = self.c.call('search', {'query': 'zebrafish marker'})
        self.assertFalse(err, text)
        self.assertIn('zebrafish', text)
        self.assertNotRegex(text, r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')

    def test_h_profile_comes_from_the_environment(self):
        # a tool argument does not change it
        _, out = self.c.call('status', {'profile': 'architect'})
        self.assertEqual(json.loads(out)['profile'], 'orchestrator')
        narrow = Client(dict(self.env, LEKS_PROFILE='architect'))
        try:
            _, out = narrow.call('status')
            self.assertEqual(json.loads(out)['profile'], 'architect')
            # the architect profile has no code root, so a code definition is not found there
            _, out = narrow.call('locate', {'query': 'symbol:evict_oldest'})
            self.assertFalse(json.loads(out)['symbols'])
        finally:
            narrow.close()
        bad = Client(dict(self.env, LEKS_PROFILE='no_such_profile'))
        try:
            err, text = bad.call('status')
            self.assertTrue(err)
            self.assertEqual(text, 'status failed: unknown profile: no_such_profile. Set LEKS_PROFILE to a profile '
                                   'the config defines, leks config show lists them')
            self.assertEqual(bad.rpc('ping')['result'], {})
        finally:
            bad.close()


try:
    importlib.import_module('numpy')
    HAVE_NUMPY = True
except ImportError:      # pragma: no cover
    HAVE_NUMPY = False


@unittest.skipUnless(HAVE_NUMPY, 'needs numpy')
class NoWarmHookTests(unittest.TestCase):
    """The path of a host with no warm-up hook (Copilot).

    The background update starts the model helper for its embed run and ends it with bye.
    The next search through the MCP server has to start the helper itself.
    """

    def setUp(self):
        self.home, cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        # backstop: a helper this test leaves behind still goes after a minute
        with open(cfg.source_path, encoding='utf-8') as f:
            d = json.load(f)
        d['daemon']['idle_exit'] = 60
        with open(cfg.source_path, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        self.cfg = config.load(cfg.source_path)
        self.addCleanup(self._helper_down)

    def _gone(self, secs):
        from leks.hooks import warm_models
        end = time.time() + secs
        while time.time() < end and (os.path.exists(self.cfg.socket_path()) or warm_models._ping(self.cfg)):
            time.sleep(0.1)
        return not os.path.exists(self.cfg.socket_path())

    def _helper_down(self):
        from leks.hooks import warm_models
        warm_models._ask(self.cfg.socket_path(), {'op': 'bye'}, 5.0)
        self._gone(15)

    def test_search_starts_the_helper_the_update_ended(self):
        import contextlib
        import io
        import warnings
        from leks.hooks import update_index
        # the job spawns the helper detached and lets go of it, which python reports as a warning
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter('ignore', ResourceWarning)
            self.assertEqual(update_index.run(self.cfg, force=True, background=False), 'done')
        # the embed run said bye, so nothing serves the models now
        self.assertTrue(self._gone(15), 'the helper did not leave after the update')
        c = Client({'LEKS_HOME': self.home, 'LEKS_CONFIG': self.cfg.source_path, 'LEKS_ALLOW_FAKE': '1',
                    'LEKS_PROFILE': 'orchestrator'})
        try:
            err, text = c.call('search', {'query': 'tile cache eviction oldest'})
        finally:
            c.close()
        self.assertFalse(err, text)
        last = text.strip().splitlines()[-1]
        self.assertTrue(last.startswith('lanes: words, meaning, reranker'), last)


if __name__ == '__main__':
    unittest.main()
