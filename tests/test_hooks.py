"""Hook tests: spawn is mocked, run folders are temp dirs, nothing touches a real index."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from leks import hooks
from leks.hooks import session_brief, update_index, warm_models


def tmpdir():
    return tempfile.mkdtemp(prefix='lk', dir='/tmp')


def fake_cfg(base, embedder=True, reranker=True, backend='fake'):
    run = os.path.join(base, 'run')
    os.makedirs(run, exist_ok=True)
    return SimpleNamespace(
        home=base, backend=backend, source_path=None,
        embedder=SimpleNamespace(key='mdense') if embedder else None,
        reranker=SimpleNamespace(key='mlate') if reranker else None,
        update={'min_interval': 1800}, profiles={'default': [], 'architect': []},
        run_dir=lambda: run, socket_path=lambda: os.path.join(run, 'm.sock'))


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def age(path, secs):
    t = time.time() - secs
    os.utime(path, (t, t))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class HelperTests(Base):
    def test_lock_take_hold_and_stale(self):
        p = os.path.join(self.tmp, 'a', 'x.lock')
        self.assertTrue(hooks.take_lock(p, 60))
        self.assertFalse(hooks.take_lock(p, 60))
        age(p, 120)
        self.assertTrue(hooks.take_lock(p, 60))     # stale one removed, new one taken
        hooks.drop_lock(p)
        self.assertFalse(os.path.exists(p))
        hooks.drop_lock(p)                          # a second drop is fine

    def test_boot_argv(self):
        a = hooks.boot_argv('print(1)', isolated=True)
        self.assertEqual(a[0], sys.executable)
        self.assertEqual(a[1:3], ['-I', '-c'])
        self.assertIn('sys.path.insert(0, ', a[3])
        self.assertTrue(a[3].endswith('print(1)'))
        self.assertNotIn('-I', hooks.boot_argv('print(1)')[1:2])
        self.assertTrue(os.path.isdir(os.path.join(hooks.pkg_parent(), 'leks')))

    def test_child_env(self):
        cfg = fake_cfg(self.tmp)
        cfg.source_path = os.path.join(self.tmp, 'config.json')
        env = hooks.child_env(cfg)
        self.assertEqual(env['LEKS_HOME'], self.tmp)
        self.assertEqual(env['LEKS_CONFIG'], cfg.source_path)

    def test_spawn(self):
        with mock.patch('subprocess.Popen') as popen:
            self.assertTrue(hooks.spawn(['prog', 'arg'], {'A': 'b'}))
        kw = popen.call_args[1]
        self.assertTrue(kw['start_new_session'])
        self.assertEqual((kw['cwd'], kw['env']), ('/', {'A': 'b'}))
        self.assertIs(kw['stdout'], subprocess.DEVNULL)
        self.assertFalse(hooks.spawn([os.path.join(self.tmp, 'no-such-program')]))


class BriefTests(Base):
    def setUp(self):
        super().setUp()
        self.files = os.path.join(self.tmp, 'files')
        self.db = os.path.join(self.tmp, 'search.sqlite')
        now = time.time()
        c = sqlite3.connect(self.db)
        c.execute('CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT, name TEXT, kind TEXT, status TEXT, '
                  'date TEXT, mtime REAL)')
        rows = [
            ('notes/STATE.md', 'STATE.md', 'state', 'current', '2026-03-01', now - 5),
            ('notes/NOW.md', 'NOW.md', 'state', 'current', '2026-03-02', now - 6),
            ('notes/old/STATE_HISTORY.md', 'STATE_HISTORY.md', 'state', 'historical-record', '2026-01-01', now - 20 * 86400),
            ('notes/bad;name.md', 'bad;name.md', 'state', 'current', '2026-03-03', now - 8),
            ('notes/../x.md', 'x.md', 'note', 'unknown', '2026-03-03', now - 9),
            ('notes/new\nline.md', 'line.md', 'note', 'unknown', '2026-03-03', now - 10),
            ('notes/a.md', 'a.md', 'note', 'unknown', '2026-03-01', now - 100),
            ('code/tile.py', 'tile.py', 'code', 'unknown', '2026-03-01', now - 11),
            ('notes/old-note.md', 'old-note.md', 'note', 'unknown', '2026-01-01', now - 10 * 86400),
            ('notes/gone.md', 'gone.md', 'note', 'unknown', '2026-03-01', now - 12),
        ]
        c.executemany('INSERT INTO docs(path,name,kind,status,date,mtime) VALUES (?,?,?,?,?,?)', rows)
        c.commit()
        c.close()
        for rel in ('notes/STATE.md', 'notes/NOW.md', 'notes/old/STATE_HISTORY.md', 'notes/a.md',
                    'code/tile.py', 'notes/old-note.md'):
            p = os.path.join(self.files, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w').close()
        self.cfg = SimpleNamespace(profile_db=lambda p: self.db,
                                   abs_path=lambda d: os.path.join(self.files, d))

    def test_shape(self):
        lines = session_brief.brief(self.cfg, 'architect').split('\n')
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], session_brief.FIRST)
        self.assertTrue(lines[0].startswith('Project search is on (leks): search for this'))
        self.assertEqual(lines[1], 'State notes: notes/STATE.md, notes/NOW.md')
        self.assertEqual(lines[2], 'Changed in the last 3 days: notes/STATE.md; notes/NOW.md; notes/a.md')

    def test_unplain_and_missing_paths_dropped(self):
        text = session_brief.brief(self.cfg, 'architect')
        for bad in ('bad;name', '..', 'gone.md', 'tile.py', 'STATE_HISTORY', 'old-note', 'line.md'):
            self.assertNotIn(bad, text)

    def test_plain_filter(self):
        ok = lambda p: session_brief.PLAIN.fullmatch(p) is not None   # noqa: E731
        self.assertTrue(ok("notes/a (b).md"))
        self.assertFalse(ok('notes/a\n.md'))
        self.assertFalse(ok('notes/a;b.md'))
        self.assertFalse(ok('x' * 301))

    def test_no_index_gives_nothing(self):
        os.remove(self.db)
        self.assertEqual(session_brief.brief(self.cfg, 'architect'), '')

    def test_db_without_docs_table(self):
        os.remove(self.db)
        sqlite3.connect(self.db).execute('CREATE TABLE other(x)').connection.close()
        self.assertEqual(session_brief.brief(self.cfg, 'architect'), '')

    def test_only_first_line_when_nothing_else(self):
        c = sqlite3.connect(self.db)
        c.execute('DELETE FROM docs')
        c.commit()
        c.close()
        self.assertEqual(session_brief.brief(self.cfg, 'architect'), session_brief.FIRST)

    def test_main_prints_nothing_on_error_and_returns_zero(self):
        buf = io.StringIO()
        with mock.patch('leks.config.load', side_effect=RuntimeError('boom')), contextlib.redirect_stdout(buf):
            self.assertEqual(session_brief.main(), 0)
        self.assertEqual(buf.getvalue(), '')

    def run_main(self):
        buf = io.StringIO()
        with mock.patch('leks.config.load', return_value=self.cfg), \
                mock.patch('leks.config.active_profile', return_value='architect'), \
                contextlib.redirect_stdout(buf):
            self.assertEqual(session_brief.main(), 0)
        return buf.getvalue()

    def test_main_prints_brief_as_one_json_object(self):
        # copilot reads the context only from json stdout, claude code takes the same shape
        out = self.run_main()
        self.assertTrue(out.endswith('\n'))
        self.assertEqual(out.count('\n'), 1)
        d = json.loads(out)
        want = session_brief.brief(self.cfg, 'architect')
        self.assertEqual(d, {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': want}})
        self.assertTrue(want.startswith(session_brief.FIRST + '\n'))

    def test_main_prints_nothing_when_the_brief_is_empty(self):
        os.remove(self.db)
        self.assertEqual(self.run_main(), '')


def serve_once(path, reply, ready):
    """A one-shot unix socket server that answers one line."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    ready.set()
    conn, _ = srv.accept()
    buf = b''
    while b'\n' not in buf:
        buf += conn.recv(4096)
    conn.sendall((json.dumps(reply) + '\n').encode())
    conn.close()
    srv.close()


class WarmTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg = fake_cfg(self.tmp)
        self.lock = os.path.join(self.cfg.run_dir(), 'start.lock')

    def patched(self, **over):
        base = dict(_ping=False, _backend_ok=True, _weights_ok=True)
        base.update(over)
        stack = contextlib.ExitStack()
        for k, v in base.items():
            stack.enter_context(mock.patch.object(warm_models, k, return_value=v))
        stack.enter_context(mock.patch.object(warm_models, '_daemon_argv', return_value=(['daemon'], {'K': 'v'})))
        self.addCleanup(stack.close)
        return stack

    def test_up(self):
        self.patched(_ping=True)
        with mock.patch.object(hooks, 'spawn') as sp:
            self.assertEqual(warm_models.start(self.cfg), 'up')
        sp.assert_not_called()

    def test_nothing_configured(self):
        self.patched()
        cfg = fake_cfg(self.tmp, embedder=False, reranker=False)
        with mock.patch.object(hooks, 'spawn') as sp:
            self.assertEqual(warm_models.start(cfg), 'no models')
        sp.assert_not_called()

    def test_no_backend_and_no_weights(self):
        with mock.patch.object(hooks, 'spawn') as sp:
            self.patched(_backend_ok=False)
            self.assertEqual(warm_models.start(self.cfg), 'no backend')
        with mock.patch.object(hooks, 'spawn') as sp:
            self.patched(_weights_ok=False)
            self.assertEqual(warm_models.start(self.cfg), 'no weights')
            sp.assert_not_called()
        self.assertFalse(os.path.exists(self.lock))

    def test_started_then_locked_then_stale(self):
        self.patched()
        with mock.patch.object(hooks, 'spawn', return_value=True) as sp:
            self.assertEqual(warm_models.start(self.cfg), 'started')
            self.assertEqual(sp.call_count, 2)              # daemon and the follow-up child
            self.assertEqual(sp.call_args_list[0][0][0], ['daemon'])
            self.assertTrue(os.path.isdir(self.lock))       # the follow-up frees it
            self.assertEqual(warm_models.start(self.cfg), 'locked')
            self.assertEqual(sp.call_count, 2)
            age(self.lock, 200)
            self.assertEqual(warm_models.start(self.cfg), 'started')

    def test_spawn_failure_frees_the_lock(self):
        self.patched()
        with mock.patch.object(hooks, 'spawn', return_value=False):
            self.assertEqual(warm_models.start(self.cfg), 'refused')
        self.assertFalse(os.path.exists(self.lock))

    def test_follow_up_failure_frees_the_lock(self):
        self.patched()
        with mock.patch.object(hooks, 'spawn', side_effect=[True, False]):
            self.assertEqual(warm_models.start(self.cfg), 'started')
        self.assertFalse(os.path.exists(self.lock))

    def test_sandbox_refusal(self):
        self.patched()
        with mock.patch.object(warm_models, '_daemon_argv', side_effect=RuntimeError('no sandbox')):
            self.assertEqual(warm_models.start(self.cfg), 'refused')
        self.assertFalse(os.path.exists(self.lock))

    def test_wait_warms_reranker_then_embedder(self):
        self.patched()
        asked = []
        with mock.patch.object(hooks, 'spawn', return_value=True), \
                mock.patch.object(warm_models, '_wait_up', return_value=True), \
                mock.patch.object(warm_models, '_ask', side_effect=lambda s, r, t: asked.append(r)):
            self.assertEqual(warm_models.start(self.cfg, wait=True), 'started')
        self.assertEqual([r['op'] for r in asked], ['rerank', 'embed'])
        self.assertEqual(asked[0]['model'], 'mlate')
        self.assertEqual(asked[1]['model'], 'mdense')
        self.assertFalse(os.path.exists(self.lock))

    def test_wait_without_daemon_warms_nothing(self):
        self.patched()
        with mock.patch.object(hooks, 'spawn', return_value=True), \
                mock.patch.object(warm_models, '_wait_up', return_value=False), \
                mock.patch.object(warm_models, '_ask') as ask:
            warm_models.start(self.cfg, wait=True)
        ask.assert_not_called()
        self.assertFalse(os.path.exists(self.lock))

    def test_never_raises_never_prints(self):
        buf = io.StringIO()
        with mock.patch.object(warm_models, '_ping', side_effect=RuntimeError('x')), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            self.assertEqual(warm_models.start(self.cfg), 'refused')
            with mock.patch('leks.config.load', side_effect=RuntimeError('x')):
                self.assertEqual(warm_models.main(), 0)
        self.assertEqual(buf.getvalue(), '')

    def test_listening_frees_the_start_lock(self):
        self.patched()
        with mock.patch.object(hooks, 'spawn', return_value=True):
            self.assertEqual(warm_models.start(self.cfg), 'started')
            self.assertEqual(warm_models.start(self.cfg), 'locked')
            # the daemon listens: a start right after it may go again
            warm_models.listening(self.cfg)
            self.assertFalse(os.path.exists(self.lock))
            self.assertEqual(warm_models.start(self.cfg), 'started')
        warm_models.listening(self.cfg)
        warm_models.listening(self.cfg)      # nothing to free is fine

    def test_warm_requests(self):
        reqs = warm_models.warm_requests(self.cfg)
        self.assertEqual(reqs[0], {'op': 'rerank', 'model': 'mlate', 'q': 'warm', 'docs': ['warm']})
        self.assertEqual(reqs[1], {'op': 'embed', 'model': 'mdense', 'texts': ['warm'], 'kind': 'q'})
        self.assertEqual(warm_models.warm_requests(fake_cfg(self.tmp, reranker=False)), reqs[1:])

    def test_ping_against_a_real_socket(self):
        self.assertFalse(warm_models._ping(self.cfg))       # nothing listens: fails at once
        ready = threading.Event()
        t = threading.Thread(target=serve_once,
                             args=(self.cfg.socket_path(), {'shape': [0, 0], 'b64': ''}, ready), daemon=True)
        t.start()
        self.assertTrue(ready.wait(5))
        self.assertTrue(warm_models._ping(self.cfg))
        t.join(5)

    def test_error_reply_is_not_up(self):
        ready = threading.Event()
        t = threading.Thread(target=serve_once,
                             args=(self.cfg.socket_path(), {'error': 'busy'}, ready), daemon=True)
        t.start()
        self.assertTrue(ready.wait(5))
        self.assertFalse(warm_models._ping(self.cfg))
        t.join(5)

    def test_daemon_argv_with_a_real_config(self):
        try:
            from tests import helpers
            home, cfg = helpers.make_home(models=True)
        except Exception as e:      # config module not usable here
            self.skipTest('no test home: %s' % type(e).__name__)
        self.addCleanup(helpers.cleanup, home)
        argv, env = warm_models._daemon_argv(cfg)
        self.assertEqual(argv[-2:], ['modeld', 'serve'])
        self.assertIn('-I', argv)
        self.assertIn('runpy', ' '.join(argv))
        self.assertEqual(env['LEKS_HOME'], home)
        self.assertNotIn('PYTHONPATH', env)


class UpdateTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg = fake_cfg(self.tmp)
        run = self.cfg.run_dir()
        self.stamp = os.path.join(run, 'update.stamp')
        self.lock = os.path.join(run, 'update_job.lock')
        self.logf = os.path.join(run, 'update.log')

    def test_due(self):
        self.assertTrue(update_index.due(self.cfg))         # no stamp yet
        open(self.stamp, 'w').close()
        self.assertFalse(update_index.due(self.cfg))
        age(self.stamp, 1801)
        self.assertTrue(update_index.due(self.cfg))
        self.cfg.update = {'min_interval': 5}
        age(self.stamp, 6)
        self.assertTrue(update_index.due(self.cfg))
        age(self.stamp, 2)
        self.assertFalse(update_index.due(self.cfg))

    def test_not_due(self):
        open(self.stamp, 'w').close()
        with mock.patch.object(hooks, 'spawn') as sp:
            self.assertEqual(update_index.run(self.cfg), 'not due')
        sp.assert_not_called()
        self.assertFalse(os.path.exists(self.lock))

    def test_started_then_locked_then_stale_lock(self):
        with mock.patch.object(hooks, 'spawn', return_value=True) as sp:
            self.assertEqual(update_index.run(self.cfg), 'started')
            self.assertTrue(os.path.isdir(self.lock))
            self.assertEqual(update_index.run(self.cfg), 'locked')
            self.assertEqual(sp.call_count, 1)
            age(self.lock, 61 * 60)
            self.assertEqual(update_index.run(self.cfg), 'started')
            self.assertEqual(sp.call_count, 2)
        argv = sp.call_args[0][0]
        self.assertIn('update_index', argv[-1])

    def test_force_beats_the_stamp(self):
        open(self.stamp, 'w').close()
        with mock.patch.object(hooks, 'spawn', return_value=True):
            self.assertEqual(update_index.run(self.cfg, force=True), 'started')

    def test_spawn_failure_frees_the_lock(self):
        with mock.patch.object(hooks, 'spawn', return_value=False):
            self.assertEqual(update_index.run(self.cfg), 'refused')
        self.assertFalse(os.path.exists(self.lock))

    def test_inline_job_builds_embeds_stamps_and_logs(self):
        calls = []
        build = mock.Mock(side_effect=lambda cfg, say: calls.append('build') or {'indexed': 3})
        embed = mock.Mock(side_effect=lambda cfg, say: calls.append('embed'))
        buf = io.StringIO()
        with mock.patch.object(update_index, '_build_all', build), \
                mock.patch.object(update_index, '_embed_profiles', embed), \
                mock.patch.object(warm_models, 'start', return_value='up') as st, \
                contextlib.redirect_stdout(buf):
            self.assertEqual(update_index.run(self.cfg, background=False), 'done')
        self.assertEqual(calls, ['build', 'embed'])
        self.assertEqual(st.call_count, 2)
        self.assertEqual(buf.getvalue(), '')
        self.assertTrue(os.path.exists(self.stamp))
        self.assertFalse(os.path.exists(self.lock))
        self.assertFalse(update_index.due(self.cfg))
        text = read(self.logf)
        self.assertIn('update start', text)
        self.assertIn('update end', text)
        for line in text.splitlines():
            self.assertRegex(line, r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ')

    def test_no_embed_without_an_embedder_or_a_daemon(self):
        build = mock.Mock(return_value={})
        embed = mock.Mock()
        with mock.patch.object(update_index, '_build_all', build), \
                mock.patch.object(update_index, '_embed_profiles', embed), \
                mock.patch.object(warm_models, 'start', return_value='no weights'):
            update_index.run(self.cfg, background=False)
            embed.assert_not_called()
        self.cfg.embedder = None
        with mock.patch.object(update_index, '_build_all', build), \
                mock.patch.object(update_index, '_embed_profiles', embed), \
                mock.patch.object(warm_models, 'start', return_value='up'):
            update_index.run(self.cfg, force=True, background=False)
            embed.assert_not_called()

    def test_error_in_the_job_still_stamps_and_unlocks(self):
        with mock.patch.object(update_index, '_build_all', side_effect=ValueError('boom')), \
                mock.patch.object(warm_models, 'start', return_value='up'):
            self.assertEqual(update_index.run(self.cfg, background=False), 'done')
        self.assertTrue(os.path.exists(self.stamp))
        self.assertFalse(os.path.exists(self.lock))
        self.assertIn('error: ValueError', read(self.logf))

    def test_log_rotates_at_five_megabytes(self):
        with open(self.logf, 'wb') as f:
            f.truncate(update_index.LOG_MAX + 10)
        update_index.log(self.cfg, 'fresh line')
        self.assertTrue(os.path.exists(self.logf + '.1'))
        self.assertEqual(read(self.logf).count('fresh line'), 1)
        self.assertLess(os.path.getsize(self.logf), 1000)

    def test_keep_open_wrapper_swallows_bye(self):
        inner = mock.Mock()
        w = update_index._KeepOpen(inner)
        w.bye()
        inner.bye.assert_not_called()
        w.ping()
        inner.ping.assert_called_once_with()

    def test_main_never_raises_never_prints(self):
        buf = io.StringIO()
        with mock.patch('leks.config.load', side_effect=RuntimeError('x')), contextlib.redirect_stdout(buf):
            self.assertEqual(update_index.main(), 0)
        self.assertEqual(buf.getvalue(), '')

    def test_embed_profiles_one_daemon_for_all(self):
        seen = []
        client = mock.Mock()
        fake_embed = SimpleNamespace(run=lambda cfg, p, client=None: seen.append((p, client)) or 0)
        with mock.patch.dict(sys.modules, {'leks.embed_all': fake_embed}), \
                mock.patch('leks.modeld_client.Client.from_config', return_value=client):
            import leks
            with mock.patch.object(leks, 'embed_all', fake_embed, create=True):
                update_index._embed_profiles(self.cfg, lambda *a: None)
        self.assertEqual([p for p, _ in seen], ['default', 'architect'])
        self.assertIsInstance(seen[0][1], update_index._KeepOpen)
        self.assertIs(seen[1][1], client)
        client.bye.assert_called_once_with()


class FilesTests(unittest.TestCase):
    def test_template_parses_and_stays_minimal(self):
        # the one template both hosts read, see docs/hosts.md
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples', 'project-template')
        want = {
            '.mcp.json': {'mcpServers'},
            '.claude/settings.json': {'env', 'hooks'},
            '.vscode/mcp.json': {'servers'},
            '.github/hooks/leks.json': {'version', 'hooks'},
        }
        for rel, keys in want.items():
            with open(os.path.join(base, rel), encoding='utf-8') as f:
                d = json.load(f)
            self.assertEqual(set(d), keys, rel)
        with open(os.path.join(base, '.claude/settings.json'), encoding='utf-8') as f:
            c = json.load(f)
        h = c['hooks']
        self.assertEqual(c['env']['LEKS_PROFILE'], 'architect')
        cmds = [x['command'] for e in h['SessionStart'] for x in e['hooks']]
        self.assertEqual(cmds, ['python3 -m leks hook brief', 'python3 -m leks hook update'])
        self.assertEqual(h['PreToolUse'][0]['matcher'], 'mcp__leks__search|mcp__leks__tried')
        self.assertEqual([x['command'] for e in h['Stop'] for x in e['hooks']], ['python3 -m leks check'])
        with open(os.path.join(base, '.github/hooks/leks.json'), encoding='utf-8') as f:
            g = json.load(f)['hooks']
        self.assertEqual([x['bash'] for x in g['sessionStart']], ['python3 -m leks hook brief', 'python3 -m leks hook update'])
        self.assertEqual([x['bash'] for x in g['agentStop']], ['python3 -m leks check'])
        # copilot documents cwd as relative to the repository root; the check walks that folder
        self.assertEqual([x.get('cwd') for x in g['agentStop']], ['.'])
        for x in g['sessionStart'] + g['agentStop']:
            self.assertEqual(x['env']['LEKS_PROFILE'], 'architect')
        for rel, key in (('.mcp.json', 'mcpServers'), ('.vscode/mcp.json', 'servers')):
            with open(os.path.join(base, rel), encoding='utf-8') as f:
                srv = json.load(f)[key]['leks']
            self.assertEqual(srv['args'], ['-m', 'leks', 'serve'], rel)
            self.assertEqual(srv['env']['LEKS_PROFILE'], 'architect', rel)

if __name__ == '__main__':
    unittest.main()
