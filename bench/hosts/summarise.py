#!/usr/bin/env python3
"""Paired numbers for host runs: what a question cost in each arm, with bootstrap intervals.

    python3 bench/hosts/summarise.py RUN_DIR
    python3 bench/hosts/summarise.py RUN_DIR --md table.md --json all.json
    python3 bench/hosts/summarise.py RUN_DIR --sheet grading
    python3 bench/hosts/summarise.py RUN_DIR --grades grading/grading_sheet.csv --key grading/grading_key.json
    python3 bench/hosts/summarise.py RUN_DIR --rates 1.25,0.125,10

RUN_DIR is a folder that bench/hosts/run.py made; several folders of one host
are pooled per question. Each session gives its cost in the host's own unit
(USD for Claude Code, AI credits for the Copilot CLI, tokens for Codex unless
--rates prices them in USD per million input, cached input and output tokens),
its tokens, turns, tool calls, LEKS calls and subagent calls, and whether its
answer names a gold file (the automatic task check). Every comparison pairs two
arms per question: the mean over a question's repeats, then the paired
difference and the ratio of sums, each with a percentile bootstrap over
questions. The pairs are each arm against `plain`, and the two agent arms
against `leks`. --sheet writes a blind grading sheet; --grades reads it back
and adds the right share and the cost per right answer. README.md next to this
file has the design.
"""
from __future__ import annotations

import argparse
import collections
import csv
import importlib.util
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ARMS = ('plain', 'leks', 'team', 'subagent')
# (arm, baseline): every arm against no LEKS, and the agent set-ups against one agent with LEKS
PAIRS = (('leks', 'plain'), ('team', 'plain'), ('subagent', 'plain'), ('team', 'leks'), ('subagent', 'leks'))
SERVER = 'leks'
CLAUDE_PREFIX = 'mcp__' + SERVER + '__'
# strings that only turn up when an agent goes after the answer key, the grading key or the index
PEEK_MARKERS = ('questions.json', 'grading_key', 'leks-home', 'leks-config.json')
UNITS = {'claude': 'USD, the CLI\'s own estimate', 'codex': 'USD at the --rates prices',
         'copilot': 'AI credits (nano-AIU / 1e9)'}
# 1 AI credit = 0.01 USD on GitHub's usage-based billing page (docs/costs.md has the link and the date)
USD_PER_CREDIT = 0.01
# measure key, label, whether a ratio of sums makes sense
MEASURES = (('cost', 'cost', True), ('input_tokens', 'input tokens', True), ('cached_tokens', 'cached input tokens', True),
            ('output_tokens', 'output tokens', True), ('reasoning_tokens', 'reasoning tokens', True),
            ('turns', 'turns', True), ('model_calls', 'model calls', True), ('tool_calls', 'tool calls', True),
            ('leks_calls', 'LEKS calls', True), ('subagent_calls', 'subagent calls', True),
            ('wall_s', 'wall time, s', True), ('cites_gold', 'answer names a gold file', False))


def unit_of(fmt: str, priced: bool = False):
    """the unit of the cost field for one host's event stream; codex tokens have none until priced"""
    if fmt == 'codex' and not priced:
        return None
    return UNITS[fmt]


def _load_stats():
    spec = importlib.util.spec_from_file_location('leks_bench_stats', os.path.join(REPO, 'bench', 'stats.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STATS = _load_stats()


def read_json(path: str):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_jsonl(path: str) -> list:
    out = []
    try:
        f = open(path, encoding='utf-8', errors='replace')
    except OSError:
        return out
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                out.append(o)
    return out


def read_text(path: str) -> str:
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _add(rec: dict, key: str, v) -> None:
    v = _num(v)
    if v is not None:
        rec[key] = (rec.get(key) or 0) + v


def peeked(obj) -> int:
    try:
        text = json.dumps(obj) if not isinstance(obj, str) else obj
    except (TypeError, ValueError):
        text = str(obj)
    return int(any(m in text for m in PEEK_MARKERS))


def blank() -> dict:
    return {'ok': False, 'answer': '', 'cost': None, 'usd': None, 'credits': None, 'premium_requests': None,
            'input_tokens': None, 'cached_tokens': None, 'cache_write_tokens': None, 'output_tokens': None,
            'reasoning_tokens': None, 'turns': None, 'model_calls': None, 'tool_calls': 0, 'leks_calls': 0,
            'subagent_calls': 0, 'shell_calls': 0, 'models': [], 'errors': [], 'peeks': 0}


def parse_claude(sdir: str) -> dict:
    """claude -p --output-format stream-json: tool calls from assistant events, totals from the result event"""
    rec = blank()
    result, texts, msg_ids = None, [], set()
    for o in read_jsonl(os.path.join(sdir, 'stdout.jsonl')):
        t = o.get('type')
        if t == 'assistant':
            m = o.get('message') or {}
            if m.get('id'):
                msg_ids.add(m['id'])
            for c in m.get('content') or []:
                if not isinstance(c, dict):
                    continue
                if c.get('type') == 'tool_use':
                    name = c.get('name') or ''
                    rec['tool_calls'] += 1
                    rec['leks_calls'] += name.startswith(CLAUDE_PREFIX)
                    rec['subagent_calls'] += name in ('Agent', 'Task')
                    rec['shell_calls'] += name == 'Bash'
                    rec['peeks'] += peeked(c.get('input'))
                elif c.get('type') == 'text' and c.get('text'):
                    texts.append(c['text'])
        elif t == 'result':
            result = o
    rec['model_calls'] = len(msg_ids) or None
    if result is None:
        rec['errors'].append('no result event')
        rec['answer'] = texts[-1] if texts else ''
        return rec
    rec['answer'] = result.get('result') if isinstance(result.get('result'), str) else (texts[-1] if texts else '')
    rec['ok'] = result.get('subtype') == 'success' and not result.get('is_error')
    if not rec['ok']:
        rec['errors'].append('result %s' % result.get('subtype'))
    rec['usd'] = _num(result.get('total_cost_usd'))
    rec['cost'] = rec['usd']
    rec['turns'] = _num(result.get('num_turns'))
    mu = result.get('modelUsage') if isinstance(result.get('modelUsage'), dict) else {}
    rec['models'] = sorted(mu)
    if mu:
        # per-model totals cover every model the session used
        for u in mu.values():
            if isinstance(u, dict):
                _add(rec, 'input_tokens', u.get('inputTokens'))
                _add(rec, 'input_tokens', u.get('cacheReadInputTokens'))
                _add(rec, 'input_tokens', u.get('cacheCreationInputTokens'))
                _add(rec, 'cached_tokens', u.get('cacheReadInputTokens'))
                _add(rec, 'cache_write_tokens', u.get('cacheCreationInputTokens'))
                _add(rec, 'output_tokens', u.get('outputTokens'))
    else:
        u = result.get('usage') if isinstance(result.get('usage'), dict) else {}
        for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'):
            _add(rec, 'input_tokens', u.get(k))
        _add(rec, 'cached_tokens', u.get('cache_read_input_tokens'))
        _add(rec, 'cache_write_tokens', u.get('cache_creation_input_tokens'))
        _add(rec, 'output_tokens', u.get('output_tokens'))
    return rec


def parse_codex(sdir: str, rates=None) -> dict:
    """codex exec --json: token usage from turn.completed, tool calls from completed items"""
    rec = blank()
    answer, turns, failed = None, 0, False
    for o in read_jsonl(os.path.join(sdir, 'stdout.jsonl')):
        t = o.get('type')
        if t == 'turn.completed':
            turns += 1
            u = o.get('usage') if isinstance(o.get('usage'), dict) else {}
            _add(rec, 'input_tokens', u.get('input_tokens'))
            _add(rec, 'cached_tokens', u.get('cached_input_tokens'))
            _add(rec, 'cache_write_tokens', u.get('cache_write_input_tokens'))
            _add(rec, 'output_tokens', u.get('output_tokens'))
            _add(rec, 'reasoning_tokens', u.get('reasoning_output_tokens'))
        elif t == 'turn.failed':
            failed = True
            rec['errors'].append('turn.failed: %s' % ((o.get('error') or {}).get('message') or ''))
        elif t == 'error':
            rec['errors'].append('error: %s' % (o.get('message') or ''))
        elif t == 'item.completed':
            it = o.get('item') if isinstance(o.get('item'), dict) else {}
            kind = it.get('type')
            if kind == 'agent_message' and it.get('text'):
                answer = it['text']
            elif kind in ('command_execution', 'mcp_tool_call', 'web_search', 'file_change', 'collab_tool_call'):
                rec['tool_calls'] += 1
                rec['shell_calls'] += kind == 'command_execution'
                rec['subagent_calls'] += kind == 'collab_tool_call'
                rec['leks_calls'] += kind == 'mcp_tool_call' and it.get('server') == SERVER
                rec['peeks'] += peeked(it.get('command') if kind == 'command_execution' else it.get('arguments'))
    rec['turns'] = turns or None
    rec['answer'] = answer if answer is not None else read_text(os.path.join(sdir, 'last.txt')).strip()
    rec['ok'] = turns > 0 and not failed and bool(rec['answer'])
    if rates and rec['input_tokens'] is not None:
        # input counts cached tokens too, as in OpenAI's usage fields; output is priced as given
        cached = rec['cached_tokens'] or 0
        out = rec['output_tokens'] or 0
        rin, rcached, rout = rates
        rec['usd'] = (max(rec['input_tokens'] - cached, 0) * rin + cached * rcached + out * rout) / 1e6
        rec['cost'] = rec['usd']
    return rec


def copilot_events(sdir: str) -> list:
    """the -p json stream plus the session's events.jsonl, each event once"""
    events, seen = [], set()
    out = read_jsonl(os.path.join(sdir, 'stdout.jsonl'))
    for o in out:
        if o.get('id') is not None:
            if o['id'] in seen:
                continue
            seen.add(o['id'])
        events.append(o)
    for o in read_jsonl(os.path.join(sdir, 'events.jsonl')):
        if o.get('id') is None:
            if not out:
                events.append(o)
            continue
        if o['id'] not in seen:
            seen.add(o['id'])
            events.append(o)
    return events


def parse_copilot(sdir: str) -> dict:
    """copilot -p --output-format json, read as session events: usage, tool starts, messages, shutdown totals"""
    rec = blank()
    answer, nano_sum, shutdown, models, initiators = None, None, None, set(), collections.Counter()
    calls = 0
    for o in copilot_events(sdir):
        t = o.get('type')
        d = o.get('data') if isinstance(o.get('data'), dict) else {}
        if t == 'session.start':
            if d.get('selectedModel'):
                models.add(str(d['selectedModel']))
        elif t == 'assistant.usage':
            calls += 1
            _add(rec, 'input_tokens', d.get('inputTokens'))
            _add(rec, 'output_tokens', d.get('outputTokens'))
            _add(rec, 'cached_tokens', d.get('cacheReadTokens'))
            _add(rec, 'cache_write_tokens', d.get('cacheWriteTokens'))
            if d.get('model'):
                models.add(str(d['model']))
            initiators[str(d.get('initiator') or 'unknown')] += 1
            cu = d.get('copilotUsage') if isinstance(d.get('copilotUsage'), dict) else {}
            n = _num(cu.get('totalNanoAiu'))
            if n is not None:
                nano_sum = (nano_sum or 0) + n
        elif t == 'tool.execution_start':
            name = str(d.get('toolName') or '')
            rec['tool_calls'] += 1
            rec['leks_calls'] += d.get('mcpServerName') == SERVER or name.startswith(SERVER + '-')
            rec['shell_calls'] += name in ('bash', 'powershell')
            rec['subagent_calls'] += name == 'task'
            rec['peeks'] += peeked(d.get('arguments'))
        elif t == 'assistant.message':
            if d.get('model'):
                models.add(str(d['model']))
            if not d.get('parentToolCallId') and isinstance(d.get('content'), str) and d['content'].strip():
                answer = d['content']
        elif t == 'session.shutdown':
            shutdown = d
        elif t == 'session.error':
            rec['errors'].append('session.error: %s' % (d.get('message') or ''))
    rec['model_calls'] = calls or None
    rec['models'] = sorted(models)
    rec['initiators'] = dict(initiators)
    nano = _num((shutdown or {}).get('totalNanoAiu'))
    if nano is None:
        nano = nano_sum
    rec['credits'] = nano / 1e9 if nano is not None else None
    rec['cost'] = rec['credits']
    rec['usd'] = rec['credits'] * USD_PER_CREDIT if rec['credits'] is not None else None
    # legacy request billing only; the sdk docs name the field totalPremiumRequestCost, older builds
    # wrote totalPremiumRequests
    sd = shutdown or {}
    pr = _num(sd.get('totalPremiumRequestCost'))
    rec['premium_requests'] = pr if pr is not None else _num(sd.get('totalPremiumRequests'))
    rec['answer'] = answer or ''
    rec['ok'] = bool(rec['answer'])
    if shutdown is None:
        rec['errors'].append('no session.shutdown event')
    return rec


def parse_session(fmt: str, sdir: str, meta: dict, rates=None) -> dict:
    """one session's record; fmt is the host whose event stream the session printed (the fake host prints one
    of the three)"""
    if fmt == 'claude':
        rec = parse_claude(sdir)
    elif fmt == 'codex':
        rec = parse_codex(sdir, rates)
    elif fmt == 'copilot':
        rec = parse_copilot(sdir)
    else:
        raise ValueError('unknown event format %r' % fmt)
    rec['wall_s'] = _num(meta.get('wall_s'))
    rec['exit_code'] = meta.get('exit_code')
    rec['timed_out'] = bool(meta.get('timed_out'))
    if rec['timed_out'] or meta.get('error') or meta.get('exit_code') not in (0, None):
        rec['ok'] = False
        rec['errors'].append('timeout' if rec['timed_out'] else 'exit %s %s' % (meta.get('exit_code'),
                                                                               meta.get('error') or ''))
    return rec


def names_file(answer: str, path: str) -> bool:
    """the answer names this file: its path from the project folder, or, for a nested file, the path below
    its first folder"""
    a = (answer or '').replace('\\', '/')
    if path in a:
        return True
    rest = path.partition('/')[2]
    return '/' in rest and rest in a


def cites(answer: str, paths: list):
    if not paths:
        return None
    return any(names_file(answer, p) for p in paths)


def load_questions(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return {q['id']: q for q in json.load(f)['questions']}


def questions_file(run: dict) -> str:
    """the questions file a run recorded, or the bench fixtures when the run folder came from another machine"""
    p = (run or {}).get('questions_file')
    if isinstance(p, str) and os.path.isfile(p):
        return p
    return os.path.join(REPO, 'bench', 'fixtures', 'questions.json')


def load_runs(dirs: list, rates=None):
    """(host, format, run records, session records); every folder must come from the same host"""
    runs, sessions, host, fmt, names = [], [], None, None, set()
    for d in dirs:
        run = read_json(os.path.join(d, 'run.json'))
        plan = read_json(os.path.join(d, 'plan.json'))
        if not isinstance(run, dict) or not isinstance(plan, list):
            raise SystemExit('%s has no run.json and plan.json from bench/hosts/run.py' % d)
        h = run['settings']['host']
        f = run['settings'].get('format') or h
        if host not in (None, h) or fmt not in (None, f):
            raise SystemExit('runs of different hosts: %s and %s; summarise one host at a time' % (host, h))
        host, fmt = h, f
        name = os.path.basename(os.path.normpath(d))
        if name in names:
            raise SystemExit('two run folders are both named %s' % name)
        names.add(name)
        if rates and f != 'codex':
            raise SystemExit('--rates prices codex tokens; %s reports its own cost' % h)
        questions = load_questions(questions_file(run))
        runs.append({'name': name, 'settings': run['settings'], 'host_version': run.get('host_version'),
                     'leks_commit': run.get('leks_commit'), 'dry_run': run.get('dry_run')})
        for e in plan:
            sdir = os.path.join(d, 'sessions', e['session'])
            meta = read_json(os.path.join(sdir, 'meta.json'))
            if not isinstance(meta, dict):
                continue
            rec = parse_session(f, sdir, meta, rates)
            q = questions.get(e['question']) or {}
            rec.update({'run': name, 'session': e['session'], 'question': e['question'], 'repeat': e['repeat'],
                        'arm': e.get('arm') or e.get('condition'), 'category': q.get('category'),
                        'cites_gold': cites(rec['answer'], q.get('gold') or []),
                        'cites_stale': cites(rec['answer'], q.get('stale') or [])})
            sessions.append(rec)
    return host, fmt, runs, sessions


def arms_of(sessions: list) -> list:
    seen = {s['arm'] for s in sessions}
    return [a for a in ARMS if a in seen]


def pairs_of(sessions: list) -> list:
    seen = set(arms_of(sessions))
    return [(a, b) for a, b in PAIRS if a in seen and b in seen]


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def per_question(sessions: list, key: str) -> dict:
    """{question: {arm: mean over that question's sessions that have a value}}"""
    acc = {}
    for s in sessions:
        v = s.get(key)
        if v is None:
            continue
        acc.setdefault(s['question'], {}).setdefault(s['arm'], []).append(float(v))
    return {q: {c: math.fsum(v) / len(v) for c, v in d.items()} for q, d in acc.items()}


def paired(pq: dict, a: str = 'leks', b: str = 'plain'):
    qs = sorted(q for q, d in pq.items() if a in d and b in d)
    return qs, [pq[q][a] for q in qs], [pq[q][b] for q in qs]


def _percentile(sorted_vals: list, alpha: float):
    n = len(sorted_vals)
    lo = sorted_vals[max(0, int(math.floor(alpha / 2 * n)))]
    hi = sorted_vals[min(n - 1, int(math.ceil((1 - alpha / 2) * n)) - 1)]
    return lo, hi


def ratio_ci(num: list, den: list, B: int = 10000, seed: int = 7, alpha: float = 0.05):
    """sum(num) / sum(den) over paired questions, percentile bootstrap over questions;
    resamples whose denominator is zero are skipped and counted"""
    n = len(num)
    if n == 0 or n != len(den) or math.fsum(den) == 0:
        return None
    point = math.fsum(num) / math.fsum(den)
    rng = random.Random(seed)
    idx = range(n)
    vals = []
    for _ in range(B):
        pick = rng.choices(idx, k=n)
        d = math.fsum(den[i] for i in pick)
        if d:
            vals.append(math.fsum(num[i] for i in pick) / d)
    if not vals:
        return {'ratio': point, 'lo': None, 'hi': None, 'skipped': B}
    vals.sort()
    lo, hi = _percentile(vals, alpha)
    return {'ratio': point, 'lo': lo, 'hi': hi, 'skipped': B - len(vals)}


def per_right_ci(cost_l: list, right_l: list, cost_p: list, right_p: list, B: int = 10000, seed: int = 7,
                 alpha: float = 0.05):
    """(cost per right answer in one arm) / (in its baseline), per-question sums resampled together;
    a resample with no right answer in an arm is skipped and counted"""
    n = len(cost_l)
    if n == 0 or not math.fsum(right_l) or not math.fsum(right_p) or not math.fsum(cost_p):
        return None

    def ratio(pick):
        rl, rp, cp = (math.fsum(x[i] for i in pick) for x in (right_l, right_p, cost_p))
        if not rl or not rp or not cp:
            return None
        return (math.fsum(cost_l[i] for i in pick) / rl) / (cp / rp)

    point = ratio(range(n))
    rng = random.Random(seed)
    vals = [v for v in (ratio(rng.choices(range(n), k=n)) for _ in range(B)) if v is not None]
    if not vals:
        return {'ratio': point, 'lo': None, 'hi': None, 'skipped': B}
    vals.sort()
    lo, hi = _percentile(vals, alpha)
    return {'ratio': point, 'lo': lo, 'hi': hi, 'skipped': B - len(vals)}


def compare(sessions: list, key: str, ratio: bool, B: int, seed: int, a: str = 'leks', b: str = 'plain') -> dict:
    """arm a against arm b on one measure, paired by question"""
    qs, xa, xb = paired(per_question(sessions, key), a, b)
    out = {'arm': a, 'baseline': b, 'questions': len(qs), 'mean_arm': None, 'mean_baseline': None, 'diff': None,
           'ratio': None}
    if not qs:
        return out
    out['mean_arm'] = math.fsum(xa) / len(xa)
    out['mean_baseline'] = math.fsum(xb) / len(xb)
    m, lo, hi = STATS.bootstrap_ci([x - y for x, y in zip(xa, xb, strict=True)], B=B, seed=seed)
    out['diff'] = {'mean': m, 'lo': lo, 'hi': hi}
    if ratio:
        out['ratio'] = ratio_ci(xa, xb, B=B, seed=seed)
    return out


def _mean(vals: list):
    vals = [v for v in vals if v is not None]
    return math.fsum(vals) / len(vals) if vals else None


def counts(sessions: list) -> dict:
    out = {}
    for c in arms_of(sessions):
        ss = [s for s in sessions if s['arm'] == c]
        answerable = [s for s in ss if s['cites_gold'] is not None]
        k = sum(1 for s in answerable if s['cites_gold'])
        lo, hi = wilson(k, len(answerable))
        stale = [s for s in ss if s['cites_stale'] is not None]
        out[c] = {'sessions': len(ss), 'ok': sum(1 for s in ss if s['ok']),
                  'timeouts': sum(1 for s in ss if s['timed_out']),
                  'cost_unknown': sum(1 for s in ss if s['cost'] is None),
                  'cost_total': math.fsum(s['cost'] for s in ss if s['cost'] is not None),
                  'cost_mean': _mean([s['cost'] for s in ss]),
                  'usd_mean': _mean([s['usd'] for s in ss]),
                  'input_tokens_mean': _mean([s['input_tokens'] for s in ss]),
                  'output_tokens_mean': _mean([s['output_tokens'] for s in ss]),
                  'wall_s_mean': _mean([s['wall_s'] for s in ss]),
                  'cites_gold': k, 'answerable': len(answerable), 'cites_gold_wilson': [lo, hi],
                  'cites_stale': sum(1 for s in stale if s['cites_stale']), 'stale_questions': len(stale),
                  'sessions_with_leks_calls': sum(1 for s in ss if s['leks_calls']),
                  'sessions_with_subagent_calls': sum(1 for s in ss if s['subagent_calls']),
                  'sessions_with_shell_calls': sum(1 for s in ss if s['shell_calls']),
                  'peeks': sum(s['peeks'] for s in ss),
                  'models': sorted({m for s in ss for m in s['models']}),
                  'premium_requests': (math.fsum(s['premium_requests'] for s in ss if s['premium_requests'] is not None)
                                       if any(s['premium_requests'] is not None for s in ss) else None)}
    return out


def write_sheet(sessions: list, questions: dict, out_dir: str, seed: int = 7) -> tuple:
    """grading_sheet.csv for the grader (no arm, shuffled, opaque codes) and grading_key.json kept apart"""
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    order = list(range(len(sessions)))
    rng.shuffle(order)
    key, used = {}, set()
    sheet = os.path.join(out_dir, 'grading_sheet.csv')
    with open(sheet, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['code', 'question', 'gold_files', 'answer', 'grade'])
        for i in order:
            s = sessions[i]
            code = '%06x' % rng.randrange(16 ** 6)
            while code in used:
                code = '%06x' % rng.randrange(16 ** 6)
            used.add(code)
            q = questions.get(s['question']) or {}
            key[code] = {'run': s['run'], 'session': s['session'], 'question': s['question'],
                         'arm': s['arm'], 'repeat': s['repeat']}
            gold = ' '.join(q.get('gold') or []) or '(none: the files do not answer it)'
            w.writerow([code, q.get('question', s['question']), gold, s['answer'], ''])
    keyfile = os.path.join(out_dir, 'grading_key.json')
    with open(keyfile, 'w', encoding='utf-8') as f:
        json.dump(key, f, indent=2)
        f.write('\n')
    return sheet, keyfile


def read_grades(sheet: str, keyfile: str) -> dict:
    """{(run, session): grade}; a grade is 1 (right), 0.5 (partly) or 0 (wrong), an empty cell is ungraded"""
    key = read_json(keyfile)
    if not isinstance(key, dict):
        raise SystemExit('cannot read the grading key %s' % keyfile)
    grades = {}
    with open(sheet, encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            g = (row.get('grade') or '').strip()
            if not g:
                continue
            try:
                v = float(g)
            except ValueError:
                v = None
            if v not in (0.0, 0.5, 1.0):
                raise SystemExit('grade %r for code %s: use 1, 0.5 or 0' % (g, row.get('code')))
            k = key.get((row.get('code') or '').strip())
            if k is None:
                raise SystemExit('code %r is not in the grading key' % row.get('code'))
            grades[(k['run'], k['session'])] = v
    return grades


def graded(sessions: list, B: int, seed: int) -> dict:
    g = [s for s in sessions if s.get('grade') is not None]
    out = {'graded': len(g), 'arms': {}, 'pairs': []}
    for c in arms_of(g):
        ss = [s for s in g if s['arm'] == c]
        right = math.fsum(s['grade'] for s in ss)
        costed = [s for s in ss if s['cost'] is not None]
        rc = math.fsum(s['grade'] for s in costed)
        out['arms'][c] = {'graded': len(ss), 'right': right, 'right_share': right / len(ss) if ss else None,
                          'cost_per_right': math.fsum(s['cost'] for s in costed) / rc if rc else None}
    both = [s for s in g if s['cost'] is not None]
    pc, pg = per_question(both, 'cost'), per_question(both, 'grade')
    for a, b in pairs_of(g):
        qs = sorted(q for q in pc if a in pc[q] and b in pc[q] and q in pg and a in pg[q] and b in pg[q])
        out['pairs'].append({
            'arm': a, 'baseline': b,
            'right_share_diff': compare(g, 'grade', False, B, seed, a, b)['diff'],
            'cost_per_right_ratio': per_right_ci([pc[q][a] for q in qs], [pg[q][a] for q in qs],
                                                 [pc[q][b] for q in qs], [pg[q][b] for q in qs], B=B, seed=seed),
            'cost_per_right_questions': len(qs)})
    return out


def summarise(host: str, runs: list, sessions: list, B: int = 10000, seed: int = 7, priced: bool = False,
              fmt: str = None) -> dict:
    fmt = fmt or host
    pairs = []
    for a, b in pairs_of(sessions):
        pairs.append({'arm': a, 'baseline': b,
                      'measures': {k: compare(sessions, k, r, B, seed, a, b) for k, _, r in MEASURES}})
    out = {'host': host, 'format': fmt, 'unit': unit_of(fmt, priced), 'runs': runs, 'arms': arms_of(sessions),
           'counts': counts(sessions), 'pairs': pairs, 'bootstrap': {'resamples': B, 'seed': seed, 'level': 0.95}}
    if any(s.get('grade') is not None for s in sessions):
        out['grades'] = graded(sessions, B, seed)
    return out


def _f(v, nd=4):
    if v is None:
        return '-'
    if isinstance(v, float):
        if v != 0 and abs(v) < 10 ** -nd:
            return '%.2g' % v
        return ('%.' + str(nd) + 'f') % v
    return str(v)


def _ci(d, nd=4):
    if not d:
        return '-'
    m = d.get('mean', d.get('ratio'))
    if d.get('lo') is None:
        return _f(m, nd)
    return '%s (%s to %s)' % (_f(m, nd), _f(d['lo'], nd), _f(d['hi'], nd))


def markdown(s: dict) -> str:
    c = s['counts']
    unit = s['unit'] or 'none, pass --rates to price the tokens'
    lines = ['# Host run: %s' % s['host'], '']
    for r in s['runs']:
        st = r['settings']
        lines.append('- run %s: model %s, small model %s, effort %s, %d questions x %d repeats, arms %s; host %s; '
                     'LEKS %s%s' % (
                         r['name'], st.get('model') or '-', st.get('small_model') or '-',
                         st.get('effort') or 'host default', len(st.get('ids') or []), st.get('repeats') or 0,
                         ', '.join(st.get('arms') or ('leks', 'plain')), r.get('host_version') or 'version unknown',
                         (r.get('leks_commit') or '-')[:12], ' (dry run, no sessions)' if r.get('dry_run') else ''))
    lines += ['- cost unit: %s' % unit, '',
              '| arm | sessions | finished ok | timeouts | task check passed (Wilson 95% CI) | mean cost | total cost '
              '| mean input tokens | mean output tokens | mean wall time, s | with LEKS calls | with subagent calls '
              '| models |',
              '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for arm in s['arms']:
        a = c[arm]
        lo, hi = a['cites_gold_wilson']
        check = '%d of %d' % (a['cites_gold'], a['answerable'])
        if lo is not None:
            check += ' (%s to %s)' % (_f(lo, 2), _f(hi, 2))
        lines.append('| %s | %d | %d | %d | %s | %s | %s | %s | %s | %s | %d | %d | %s |' % (
            arm, a['sessions'], a['ok'], a['timeouts'], check, _f(a['cost_mean']), _f(a['cost_total']),
            _f(a['input_tokens_mean'], 0), _f(a['output_tokens_mean'], 0), _f(a['wall_s_mean'], 1),
            a['sessions_with_leks_calls'], a['sessions_with_subagent_calls'], ', '.join(a['models']) or '-'))
    lines.append('')
    for arm in s['arms']:
        a = c[arm]
        notes = []
        if a['cost_unknown']:
            notes.append('%d sessions with no cost figure' % a['cost_unknown'])
        if a['stale_questions']:
            notes.append('answers naming a stale file: %d of %d' % (a['cites_stale'], a['stale_questions']))
        if a['sessions_with_shell_calls']:
            notes.append('sessions with a shell call: %d' % a['sessions_with_shell_calls'])
        if a['premium_requests'] is not None:
            notes.append('premium requests (legacy billing field): %s' % _f(a['premium_requests'], 2))
        if a['usd_mean'] is not None and s['format'] == 'copilot':
            notes.append('mean USD at 0.01 per credit: %s' % _f(a['usd_mean']))
        if a['peeks']:
            notes.append('WARNING: %d tool calls named the answer key, the grading key or the index; read those '
                         'sessions before using the numbers' % a['peeks'])
        if notes:
            lines.append('- %s: %s' % (arm, '; '.join(notes)))
    b = s['bootstrap']
    lines += ['', 'Means per question over repeats, then over the questions both arms have. Intervals: '
              '%d%% percentile bootstrap over questions, %d resamples, seed %d.' % (round(100 * b['level']),
                                                                                    b['resamples'], b['seed'])]
    for p in s['pairs']:
        lines += ['', '## %s against %s' % (p['arm'], p['baseline']), '',
                  '| measure | questions | %s | %s | difference (95%% CI) | ratio of sums (95%% CI) |' % (
                      p['arm'], p['baseline']),
                  '|---|---|---|---|---|---|']
        for k, label, _ in MEASURES:
            m = p['measures'][k]
            if not m['questions']:
                continue
            if k == 'cost' and s['unit']:
                label = 'cost, ' + s['unit']
            lines.append('| %s | %d | %s | %s | %s | %s |' % (label, m['questions'], _f(m['mean_arm']),
                                                               _f(m['mean_baseline']), _ci(m['diff']),
                                                               _ci(m['ratio'], 3)))
    g = s.get('grades')
    if g:
        lines += ['', '## Graded sessions', '', 'Graded sessions: %d. Right counts 1, partly 0.5.' % g['graded'], '',
                  '| arm | graded | right | right share | cost per right answer |', '|---|---|---|---|---|']
        for arm, v in g['arms'].items():
            lines.append('| %s | %d | %s | %s | %s |' % (arm, v['graded'], _f(v['right'], 1), _f(v['right_share'], 3),
                                                          _f(v['cost_per_right'])))
        lines.append('')
        for p in g['pairs']:
            r = p['cost_per_right_ratio']
            lines.append('- %s against %s: right share difference per question %s; cost per right answer, ratio %s '
                         'over %d questions%s' % (
                             p['arm'], p['baseline'], _ci(p['right_share_diff'], 3), _ci(r, 3),
                             p['cost_per_right_questions'],
                             '' if not r or not r.get('skipped') else ' (%d resamples with no right answer skipped)'
                             % r['skipped']))
    return '\n'.join(lines) + '\n'


def parse_rates(text: str):
    try:
        v = tuple(float(x) for x in text.split(','))
    except ValueError:
        v = ()
    if len(v) != 3 or any(x < 0 for x in v):
        raise SystemExit('--rates takes three prices: input,cached input,output in USD per 1M tokens')
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('runs', nargs='+', help='run folders from bench/hosts/run.py, all of one host')
    ap.add_argument('--md', help='write the Markdown summary here as well')
    ap.add_argument('--json', help='write the summary and every session record as JSON here')
    ap.add_argument('--sheet', help='write a blind grading sheet and its key into this folder')
    ap.add_argument('--grades', help='a filled grading_sheet.csv')
    ap.add_argument('--key', help='the grading_key.json that belongs to --grades')
    ap.add_argument('--rates', help='codex: USD per 1M input, cached input and output tokens, as IN,CACHED,OUT')
    ap.add_argument('--boot', type=int, default=10000, help='bootstrap resamples (default 10000)')
    ap.add_argument('--seed', type=int, default=7, help='seed for the bootstrap and the sheet order (default 7)')
    a = ap.parse_args(argv)
    if bool(a.grades) != bool(a.key):
        ap.error('--grades and --key go together')
    rates = parse_rates(a.rates) if a.rates else None
    if rates is None:
        # a codex run started with --rates keeps them in its settings
        first = read_json(os.path.join(os.path.abspath(a.runs[0]), 'run.json')) or {}
        kept = (first.get('settings') or {}).get('rates')
        rates = tuple(kept) if kept else None
    host, fmt, runs, sessions = load_runs([os.path.abspath(d) for d in a.runs], rates)
    if not sessions:
        print('no finished sessions in %s' % ', '.join(a.runs), file=sys.stderr)
        return 1
    if a.sheet:
        qfile = questions_file(read_json(os.path.join(os.path.abspath(a.runs[0]), 'run.json')))
        sheet, keyfile = write_sheet(sessions, load_questions(qfile), a.sheet, a.seed)
        print('grading sheet: %s\nkey, keep it from the grader: %s' % (sheet, keyfile))
    if a.grades:
        grades = read_grades(a.grades, a.key)
        for s in sessions:
            s['grade'] = grades.get((s['run'], s['session']))
    s = summarise(host, runs, sessions, B=a.boot, seed=a.seed, priced=bool(rates), fmt=fmt)
    md = markdown(s)
    sys.stdout.write(md)
    if a.md:
        with open(a.md, 'w', encoding='utf-8') as f:
            f.write(md)
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(dict(s, sessions=sessions), f, indent=2)
            f.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
