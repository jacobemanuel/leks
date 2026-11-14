"""Tests for the host cost harness in bench/hosts, with the fake host only: no host CLI runs and no account pays."""
import contextlib
import csv
import importlib.util
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTS_DIR = os.path.join(ROOT, 'bench', 'hosts')
QUESTIONS = os.path.join(ROOT, 'bench', 'fixtures', 'questions.json')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


run = _load('leks_hosts_run_t', os.path.join(HOSTS_DIR, 'run.py'))
summ = _load('leks_hosts_summarise_t', os.path.join(HOSTS_DIR, 'summarise.py'))
fake = _load('leks_hosts_fake_t', os.path.join(HOSTS_DIR, 'fake_host.py'))

with open(QUESTIONS, encoding='utf-8') as _f:
    QS = {q['id']: q for q in json.load(_f)['questions']}


def quiet(fn, *args, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        r = fn(*args, **kw)
    return r, out.getvalue(), err.getvalue()


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def read_text(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def read_jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def args(**kw):
    base = {'host': 'claude', 'model': 'sonnet', 'small_model': 'haiku', 'effort': 'medium', 'max_usd': 1.0,
            'max_credits': None, 'usage_file': False, 'format': 'claude'}
    base.update(kw)
    return SimpleNamespace(**base)


PREP = {'work': '/tmp/w', 'python': '/usr/bin/python3', 'profile': 'orchestrator', 'config': '/tmp/w/c.json',
        'claude_mcp': '/tmp/w/claude-mcp.json', 'copilot_mcp': '/tmp/w/copilot-mcp.json',
        'questions_file': QUESTIONS}


class ScheduleAndTasks(unittest.TestCase):
    def test_every_arm_once_per_question_and_repeat(self):
        qs = [QS['q01'], QS['q03'], QS['q05']]
        plan = run.schedule(qs, 2, run.ARMS)
        self.assertEqual(len(plan), 3 * 2 * 4)
        for r in (1, 2):
            for q in qs:
                arms = [a for rr, qq, a in plan if rr == r and qq is q]
                self.assertEqual(sorted(arms), sorted(run.ARMS))

    def test_first_arm_rotates(self):
        qs = [QS[i] for i in ('q01', 'q03', 'q05', 'q07')]
        plan = run.schedule(qs, 1, run.ARMS)
        firsts = [plan[i][2] for i in range(0, len(plan), 4)]
        self.assertEqual(sorted(firsts), sorted(run.ARMS))

    def test_two_arms_alternate(self):
        plan = run.schedule([QS['q01'], QS['q03']], 1, ['plain', 'leks'])
        self.assertEqual([a for _, _, a in plan], ['leks', 'plain', 'plain', 'leks'])

    def test_smoke_set(self):
        t = run.load_tasks(os.path.join(HOSTS_DIR, 'tasks.json'), smoke=True)
        self.assertEqual([q['id'] for q in t['questions']], ['q01', 'q15', 'q25', 'q33'])
        self.assertEqual(t['repeats'], 1)
        self.assertEqual(t['arms'], list(run.ARMS))
        # every smoke question has a gold file, so the automatic check applies to all of them
        self.assertTrue(all(q['gold'] for q in t['questions']))

    def test_task_set_default(self):
        t = run.load_tasks(os.path.join(HOSTS_DIR, 'tasks.json'))
        self.assertEqual(len(t['questions']), 20)
        self.assertEqual(t['repeats'], 2)
        t = run.load_tasks(os.path.join(HOSTS_DIR, 'tasks.json'), all_questions=True)
        self.assertEqual(len(t['questions']), 40)

    def test_bad_ids_and_arms(self):
        with self.assertRaises(SystemExit):
            run.load_tasks(os.path.join(HOSTS_DIR, 'tasks.json'), ids=['q99'])
        with self.assertRaises(SystemExit):
            run.load_tasks(os.path.join(HOSTS_DIR, 'tasks.json'), ids=['q01', 'q01'])
        with self.assertRaises(SystemExit):
            run.parse_arms('plain,solo')
        with self.assertRaises(SystemExit):
            run.parse_arms('leks,leks')
        self.assertEqual(run.parse_arms(' leks, plain '), ['leks', 'plain'])


class HostCommands(unittest.TestCase):
    def test_claude_plain_has_no_leks_and_no_subagents(self):
        argv = run.claude_argv(args(), 'Q', 'plain', PREP)
        self.assertNotIn('--mcp-config', argv)
        self.assertNotIn('--agents', argv)
        allowed = argv[argv.index('--allowedTools') + 1].split(',')
        denied = argv[argv.index('--disallowedTools') + 1].split(',')
        self.assertEqual(allowed, ['Read', 'Grep', 'Glob'])
        self.assertIn('Agent', denied)
        self.assertIn('Task', denied)
        self.assertIn('Bash', denied)

    def test_claude_leks(self):
        argv = run.claude_argv(args(), 'Q', 'leks', PREP)
        self.assertEqual(argv[argv.index('--mcp-config') + 1], PREP['claude_mcp'])
        self.assertIn('mcp__leks', argv[argv.index('--allowedTools') + 1].split(','))
        self.assertNotIn('--agents', argv)

    def test_claude_team(self):
        argv = run.claude_argv(args(), 'Q', 'team', PREP)
        agents = json.loads(argv[argv.index('--agents') + 1])
        self.assertEqual(sorted(agents), ['knowledge', 'review'])
        self.assertEqual({a['model'] for a in agents.values()}, {'inherit'})
        self.assertIn('mcp__leks__*', agents['knowledge']['tools'])
        self.assertNotIn('mcp__leks__*', agents['review']['tools'])
        allowed = argv[argv.index('--allowedTools') + 1].split(',')
        denied = argv[argv.index('--disallowedTools') + 1].split(',')
        self.assertIn('Agent', allowed)
        self.assertNotIn('Agent', denied)
        self.assertNotIn('Task', denied)
        self.assertIn('Bash', denied)
        self.assertIn('--mcp-config', argv)

    def test_claude_subagent_runs_the_small_model(self):
        argv = run.claude_argv(args(small_model='haiku'), 'Q', 'subagent', PREP)
        agents = json.loads(argv[argv.index('--agents') + 1])
        self.assertEqual(list(agents), ['searcher'])
        self.assertEqual(agents['searcher']['model'], 'haiku')
        self.assertIn('mcp__leks__*', agents['searcher']['tools'])
        self.assertEqual(argv[argv.index('--model') + 1], 'sonnet')

    def test_copilot_arms(self):
        plain = run.copilot_argv(args(host='copilot', model='auto'), 'Q', 'plain', PREP, '/tmp/s')
        self.assertIn('--disable-mcp-server=leks', plain)
        self.assertIn('--model=auto', plain)
        excl = [x for x in plain if x.startswith('--excluded-tools=')][0].split('=', 1)[1].split(',')
        self.assertIn('task', excl)
        team = run.copilot_argv(args(host='copilot', model='auto'), 'Q', 'team', PREP, '/tmp/s')
        excl = [x for x in team if x.startswith('--excluded-tools=')][0].split('=', 1)[1].split(',')
        self.assertNotIn('task', excl)
        self.assertIn('bash', excl)
        self.assertIn('--additional-mcp-config=@' + PREP['copilot_mcp'], team)
        self.assertIn('--allow-tool=leks', team)

    def test_codex_arms(self):
        plain = run.codex_argv(args(host='codex', model='m'), 'Q', 'plain', PREP, '/tmp/p', '/tmp/s')
        self.assertIn('agents.enabled=false', plain)
        self.assertFalse(any(x.startswith('mcp_servers.') for x in plain))
        sub = run.codex_argv(args(host='codex', model='m'), 'Q', 'subagent', PREP, '/tmp/p', '/tmp/s')
        self.assertIn('agents.enabled=true', sub)
        self.assertIn('mcp_servers.leks.required=true', sub)
        self.assertEqual(sub[-1], 'Q')

    def test_agent_files_parse(self):
        md = run.copilot_agent_md('searcher', 'small-1')
        head = md.split('---')[1]
        fields = dict(line.split(': ', 1) for line in head.strip().splitlines())
        self.assertEqual(fields['name'], 'searcher')
        self.assertEqual(json.loads(fields['model']), 'small-1')
        self.assertEqual(fields['model-policy'], 'required')
        self.assertNotIn('model', run.copilot_agent_md('knowledge', 'small-1').split('---')[1])
        toml = run.codex_agent_toml('searcher', 'small-1')
        try:
            import tomllib
        except ImportError:
            tomllib = None
        if tomllib is not None:
            d = tomllib.loads(toml)
            self.assertEqual(d['name'], 'searcher')
            self.assertEqual(d['model'], 'small-1')
            self.assertIn('path:line', d['developer_instructions'])
        self.assertNotIn('model =', run.codex_agent_toml('review', 'x'))


class FakeHostRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='leks-hosts-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('LEKS_', 'FAKE_HOST_'))}
        env.pop('PYTHONPATH', None)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def go(self, *extra, out='run', code=0):
        out = os.path.join(self.tmp, out)
        got, stdout, _ = quiet(run.main, ['fake', '--out', out, '--python', sys.executable] + list(extra))
        self.assertEqual(got, code)
        return out, stdout

    def test_dry_run_runs_no_session(self):
        out, stdout = self.go('--smoke', '--dry-run')
        self.assertIn('dry run: 16 sessions planned', stdout)
        self.assertEqual(os.listdir(os.path.join(out, 'sessions')), [])
        plan = read_json(os.path.join(out, 'plan.json'))
        self.assertEqual(sorted({e['arm'] for e in plan}), sorted(run.ARMS))
        r = read_json(os.path.join(out, 'run.json'))
        self.assertTrue(r['dry_run'])
        self.assertTrue(r['server_check']['ok'])
        work = os.path.join(out, 'work')
        self.assertFalse(os.path.exists(os.path.join(work, 'plain', 'AGENTS.md')))
        for arm in ('leks', 'team', 'subagent'):
            self.assertTrue(os.path.isfile(os.path.join(work, arm, 'AGENTS.md')))
        with open(os.path.join(work, 'team', 'AGENTS.md'), encoding='utf-8') as f:
            self.assertIn('## Agent team', f.read())
        with open(os.path.join(work, 'leks', 'AGENTS.md'), encoding='utf-8') as f:
            text = f.read()
        self.assertNotIn('## Agent team', text)
        self.assertNotIn('## Search helper', text)
        self.assertNotIn('## Skills', text)
        self.assertIn('## When you write notes', text)
        self.assertTrue(text.startswith('# Agent instructions'))

    def check_run(self, fmt, *extra):
        out, stdout = self.go('--format', fmt, '--smoke', *extra, out=fmt)
        res = read_jsonl(os.path.join(out, 'results.jsonl'))
        self.assertEqual(len(res), 16)
        by = {(r['question'], r['arm']): r for r in res}
        for r in res:
            self.assertTrue(r['ok'], r)
            self.assertIsNotNone(r['input_tokens'])
            self.assertIsNotNone(r['output_tokens'])
            self.assertIsInstance(r['wall_s'], float)
            self.assertTrue(os.path.isfile(os.path.join(out, 'sessions', r['session'], 'result.json')))
        # the fake host names the stale file on the current-state question in the plain arm only
        self.assertFalse(by[('q15', 'plain')]['check_passed'])
        self.assertTrue(by[('q15', 'plain')]['cites_stale'])
        for arm in ('leks', 'team', 'subagent'):
            self.assertTrue(by[('q15', arm)]['check_passed'])
        self.assertTrue(by[('q01', 'plain')]['check_passed'])
        self.assertEqual(by[('q01', 'plain')]['leks_calls'], 0)
        self.assertGreater(by[('q01', 'leks')]['leks_calls'], 0)
        self.assertEqual(by[('q01', 'leks')]['subagent_calls'], 0)
        self.assertGreater(by[('q01', 'team')]['subagent_calls'], 0)
        self.assertGreater(by[('q01', 'subagent')]['subagent_calls'], 0)
        return out, by, stdout

    def test_claude_format(self):
        out, by, stdout = self.check_run('claude')
        r = by[('q01', 'leks')]
        self.assertEqual(r['unit'], summ.UNITS['claude'])
        # 37000 in at 3 and 430 out at 15 USD per 1M, the fake host's made-up shape
        self.assertAlmostEqual(r['cost'], (37000 * 3 + 430 * 15) / 1e6, places=6)
        self.assertEqual(r['models'], ['fake-main'])
        self.assertEqual(by[('q01', 'subagent')]['models'], ['fake-main', 'fake-small'])
        self.assertIn('check passed', stdout)
        self.assertIn('check failed', stdout)

    def test_copilot_format(self):
        out, by, _ = self.check_run('copilot')
        r = by[('q01', 'leks')]
        usd = (37000 * 3 + 430 * 15) / 1e6
        self.assertAlmostEqual(r['credits'], usd * 100, places=4)
        self.assertAlmostEqual(r['usd'], usd, places=6)
        self.assertEqual(r['premium_requests'], 1)

    def test_codex_format_unpriced_and_priced(self):
        out, by, _ = self.check_run('codex')
        self.assertIsNone(by[('q01', 'leks')]['cost'])
        self.assertIsNone(by[('q01', 'leks')]['unit'])
        out, stdout = self.go('--format', 'codex', '--ids', 'q01', '--repeats', '1', '--arms', 'leks',
                              '--rates', '1,0.1,10', out='priced')
        r = read_jsonl(os.path.join(out, 'results.jsonl'))[0]
        inp, cached, outp = 37000, int(37000 * 0.75), 430
        self.assertAlmostEqual(r['cost'], ((inp - cached) * 1 + cached * 0.1 + outp * 10) / 1e6, places=9)
        # summarise picks the rates up from the run settings
        _, md, _ = quiet(summ.main, [out, '--boot', '50'])
        self.assertIn('USD at the --rates prices', md)

    def test_summary_pairs_and_table(self):
        out, by, _ = self.check_run('claude')
        host, fmt, runs, sessions = summ.load_runs([out])
        self.assertEqual((host, fmt), ('fake', 'claude'))
        s = summ.summarise(host, runs, sessions, B=200, seed=3, fmt=fmt)
        pairs = [(p['arm'], p['baseline']) for p in s['pairs']]
        self.assertEqual(pairs, list(summ.PAIRS))
        lp = s['pairs'][0]['measures']
        self.assertEqual(lp['cost']['questions'], 4)
        self.assertLess(lp['cost']['mean_arm'], lp['cost']['mean_baseline'])
        self.assertAlmostEqual(lp['cost']['ratio']['ratio'], by[('q01', 'leks')]['cost'] / by[('q01', 'plain')]['cost'])
        self.assertEqual(s['counts']['plain']['cites_gold'], 3)
        self.assertEqual(s['counts']['leks']['cites_gold'], 4)
        md = summ.markdown(s)
        for head in ('## leks against plain', '## team against plain', '## subagent against plain',
                     '## team against leks', '## subagent against leks', '| subagent | 4 | 4 | 0 | 4 of 4'):
            self.assertIn(head, md)

    def test_two_arms_only(self):
        out, _ = self.go('--ids', 'q01,q03', '--repeats', '2', '--arms', 'plain,leks')
        host, fmt, runs, sessions = summ.load_runs([out])
        self.assertEqual(len(sessions), 8)
        s = summ.summarise(host, runs, sessions, B=100, fmt=fmt)
        self.assertEqual([(p['arm'], p['baseline']) for p in s['pairs']], [('leks', 'plain')])

    def test_timeout_and_failure(self):
        with mock.patch.dict(os.environ, {'FAKE_HOST_SLEEP': '5'}):
            out, _ = self.go('--ids', 'q01', '--repeats', '1', '--arms', 'leks', '--timeout', '1', out='slow',
                             code=1)
        r = read_jsonl(os.path.join(out, 'results.jsonl'))[0]
        self.assertTrue(r['timed_out'])
        self.assertFalse(r['ok'])
        self.assertIn('timeout', r['errors'])
        with mock.patch.dict(os.environ, {'FAKE_HOST_FAIL': '1'}):
            out, _ = self.go('--ids', 'q01', '--repeats', '1', '--arms', 'plain', out='fail', code=1)
        r = read_jsonl(os.path.join(out, 'results.jsonl'))[0]
        self.assertEqual(r['exit_code'], 3)
        self.assertFalse(r['ok'])
        self.assertFalse(r['check_passed'])
        self.assertIsNone(r['cost'])

    def test_timeout_without_posix_signals(self):
        # windows has neither os.killpg nor signal.SIGKILL: the timeout path falls back to p.kill()
        class NoKillpg:
            def __getattr__(self, name):
                if name == 'killpg':
                    raise AttributeError(name)
                return getattr(os, name)
        sdir = os.path.join(self.tmp, 'nokill')
        os.makedirs(sdir)
        with mock.patch.object(run, 'os', NoKillpg()), \
                mock.patch.object(run, 'signal', SimpleNamespace(SIGTERM=signal.SIGTERM)):
            got = run.run_session([sys.executable, '-c', 'import time; time.sleep(60)'], self.tmp, dict(os.environ),
                                  sdir, 1)
        self.assertEqual(got, (None, True, None))

    def test_git_runs_by_its_full_path(self):
        seen = []
        with mock.patch.object(run.shutil, 'which', return_value='/opt/x/bin/git'), \
                mock.patch.object(run.subprocess, 'run',
                                  side_effect=lambda argv, **kw: seen.append(argv) or SimpleNamespace(stdout='abc\n')):
            self.assertEqual(run.leks_commit(), 'abc')
        self.assertEqual(seen[0][0], '/opt/x/bin/git')

    def test_resume_runs_only_what_is_missing(self):
        out, _ = self.go('--ids', 'q01,q03', '--repeats', '1', '--arms', 'plain,leks')
        os.remove(os.path.join(out, 'sessions', 'q03-r1-leks', 'meta.json'))
        code, stdout, _ = quiet(run.main, ['fake', '--out', out, '--python', sys.executable, '--ids', 'q01,q03',
                                           '--repeats', '1', '--arms', 'plain,leks', '--resume'])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.count('] q'), 1)
        self.assertIn('q03-r1-leks', stdout)
        with self.assertRaises(SystemExit):
            quiet(run.main, ['fake', '--out', out, '--python', sys.executable, '--ids', 'q01,q03',
                             '--repeats', '1', '--arms', 'plain', '--resume'])
        with self.assertRaises(SystemExit):
            quiet(run.main, ['fake', '--out', out, '--python', sys.executable, '--ids', 'q01'])

    def test_grading_sheet_hides_the_arm(self):
        out, _ = self.go('--ids', 'q01,q15', '--repeats', '1', '--arms', 'plain,leks')
        host, fmt, runs, sessions = summ.load_runs([out])
        sheet, keyfile = summ.write_sheet(sessions, QS, os.path.join(self.tmp, 'grading'))
        with open(sheet, encoding='utf-8', newline='') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 4)
        self.assertNotIn('arm', rows[0])
        text = read_text(sheet)
        self.assertNotIn('plain', text)
        key = read_json(keyfile)
        with open(sheet, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['code', 'question', 'gold_files', 'answer', 'grade'])
            w.writeheader()
            for row in rows:
                row['grade'] = '0' if key[row['code']]['arm'] == 'plain' and key[row['code']]['question'] == 'q15' \
                    else '1'
                w.writerow(row)
        grades = summ.read_grades(sheet, keyfile)
        for s in sessions:
            s['grade'] = grades.get((s['run'], s['session']))
        g = summ.graded(sessions, 200, 7)
        self.assertEqual(g['arms']['leks']['right'], 2)
        self.assertEqual(g['arms']['plain']['right'], 1)
        self.assertEqual(g['pairs'][0]['arm'], 'leks')
        self.assertIsNotNone(g['pairs'][0]['cost_per_right_ratio'])


class Parsers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='leks-hosts-p-')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, events, name='stdout.jsonl'):
        with open(os.path.join(self.tmp, name), 'w', encoding='utf-8') as f:
            for e in events:
                f.write(json.dumps(e) + '\n')

    def test_copilot_premium_field_names(self):
        base = [{'id': 1, 'type': 'assistant.message', 'data': {'content': 'see notes/a.md'}}]
        self.write(base + [{'id': 2, 'type': 'session.shutdown', 'data': {'totalNanoAiu': 2e9,
                                                                        'totalPremiumRequests': 3}}])
        r = summ.parse_copilot(self.tmp)
        self.assertEqual((r['credits'], r['premium_requests']), (2.0, 3))
        self.assertAlmostEqual(r['usd'], 0.02)
        self.write(base + [{'id': 2, 'type': 'session.shutdown', 'data': {'totalPremiumRequestCost': 0.9}}])
        r = summ.parse_copilot(self.tmp)
        self.assertEqual(r['premium_requests'], 0.9)
        self.assertIsNone(r['credits'])

    def test_copilot_events_file_counted_once(self):
        ev = [{'id': 'a', 'type': 'session.start', 'data': {'sessionId': 's1'}},
              {'id': 'b', 'type': 'assistant.usage', 'data': {'inputTokens': 10, 'outputTokens': 2,
                                                              'copilotUsage': {'totalNanoAiu': 1e9}}},
              {'id': 'c', 'type': 'tool.execution_start', 'data': {'toolName': 'task'}},
              {'id': 'd', 'type': 'assistant.message', 'data': {'content': 'x'}}]
        self.write(ev)
        self.write(ev, 'events.jsonl')
        r = summ.parse_copilot(self.tmp)
        self.assertEqual(r['input_tokens'], 10)
        self.assertEqual(r['subagent_calls'], 1)
        self.assertEqual(r['credits'], 1.0)
        self.assertIn('no session.shutdown event', r['errors'])

    def test_claude_without_result_is_not_ok(self):
        self.write([{'type': 'assistant', 'message': {'id': 'm', 'content': [{'type': 'text', 'text': 'hi'}]}}])
        r = summ.parse_claude(self.tmp)
        self.assertFalse(r['ok'])
        self.assertEqual(r['answer'], 'hi')
        self.assertIsNone(r['cost'])

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            summ.parse_session('gemini', self.tmp, {})

    def test_fake_host_answers(self):
        self.assertIn('notes/docs-site/overview.md', fake.answer_for(QS['q01'], 'plain'))
        self.assertIn(QS['q15']['stale'][0], fake.answer_for(QS['q15'], 'plain'))
        self.assertIn(QS['q15']['gold'][0], fake.answer_for(QS['q15'], 'leks'))
        self.assertIn('do not answer', fake.answer_for(QS['q39'], 'leks'))


def _shim(path, body):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('#!%s\n%s' % (sys.executable, body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# a stand-in `claude` on PATH for the smoke script: answers the sign-in prompt and prints the fake host's
# stream-json for each session, with the arm read from the project folder name
CLAUDE_SHIM = '''import os, sys
sys.path.insert(0, %(hosts)r)
import fake_host
a = sys.argv[1:]
if a and a[0] == '--version':
    print('0.0 (stand-in)')
    sys.exit(0)
prompt = a[a.index('-p') + 1]
if prompt.startswith('Reply with'):
    print('{"type": "result", "subtype": "success", "result": "ok"}')
    sys.exit(0)
arm = os.path.basename(os.getcwd())
sys.exit(fake_host.main(['--format', 'claude', '--arm', arm, '--questions', %(questions)r, '--', prompt]))
'''


@unittest.skipIf(os.name != 'posix' or not shutil.which('sh'), 'the smoke script is a POSIX shell script')
class SmokeScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='leks-hosts-s-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, 'bin')
        os.makedirs(self.bin)

    def smoke(self, out, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('LEKS_', 'FAKE_HOST_', 'SMOKE_', 'CODEX_',
                                                                         'COPILOT_', 'CLAUDE_'))}
        env.pop('PYTHONPATH', None)
        env.update({'PATH': os.pathsep.join([self.bin, '/usr/bin', '/bin']), 'PYTHON': sys.executable})
        env.update(extra)
        return subprocess.run(['sh', os.path.join(HOSTS_DIR, 'smoke.sh'), out], env=env, capture_output=True,
                              text=True, timeout=300, stdin=subprocess.DEVNULL)

    def test_no_host_installed(self):
        out = os.path.join(self.tmp, 'none')
        p = self.smoke(out)
        # nothing it was asked to run ran: the table is written and the exit says so
        self.assertEqual(p.returncode, 1, p.stderr)
        with open(os.path.join(out, 'summary.md'), encoding='utf-8') as f:
            text = f.read()
        for h in ('claude', 'copilot', 'codex'):
            self.assertIn('| %s | no | - | - | no | not on PATH |' % h, text)

    def test_refuses_a_full_folder(self):
        out = os.path.join(self.tmp, 'full')
        os.makedirs(out)
        open(os.path.join(out, 'x'), 'w').close()
        p = self.smoke(out)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('not empty', p.stderr)

    def test_stand_in_claude_end_to_end(self):
        _shim(os.path.join(self.bin, 'claude'), CLAUDE_SHIM % {'hosts': HOSTS_DIR, 'questions': QUESTIONS})
        out = os.path.join(self.tmp, 'claude')
        p = self.smoke(out, SMOKE_HOSTS='claude')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        res = read_jsonl(os.path.join(out, 'claude', 'results.jsonl'))
        self.assertEqual(len(res), 16)
        self.assertEqual(sum(1 for r in res if r['check_passed']), 15)
        with open(os.path.join(out, 'summary.md'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('| claude | yes | 0.0 (stand-in) | yes | yes | - |', text)
        self.assertIn('### leks against plain', text)
        self.assertIn('### subagent against leks', text)
        self.assertTrue(os.path.isfile(os.path.join(out, 'claude.json')))

    def test_failed_sessions_fail_the_script(self):
        _shim(os.path.join(self.bin, 'claude'), CLAUDE_SHIM % {'hosts': HOSTS_DIR, 'questions': QUESTIONS})
        out = os.path.join(self.tmp, 'claude-fail')
        p = self.smoke(out, SMOKE_HOSTS='claude', SMOKE_ARMS='plain', FAKE_HOST_FAIL='1')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        with open(os.path.join(out, 'summary.md'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('| claude | yes | 0.0 (stand-in) | yes | failed | some sessions did not end ok', text)


if __name__ == '__main__':
    unittest.main()
