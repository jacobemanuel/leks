#!/usr/bin/env python3
"""Summarise tool-server test runs.

    python3 bench/integrations/summarise.py bench/integrations/runs
    python3 bench/integrations/summarise.py --json out.json --md out.md bench/integrations/runs
    python3 bench/integrations/summarise.py --tool blender runs/b1 runs/b2 --tool unity runs/u1

A run folder holds session.jsonl (Claude Code stream JSON) and verify.txt (the
VERIFY line of the check script). Folders named blender_<id> or unity_<id> are
found under each path given; --tool NAME DIR... names folders by hand.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys

TOOL_PREFIX = {'blender': 'mcp__blender__', 'unity': 'mcp__unityMCP__'}
LEKS = 'mcp__leks__'


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def read_session(path: str):
    """Tool calls, their inputs, the result event and the text the first LEKS search returned."""
    calls, inputs, result, first_search, search_id = [], [], None, None, None
    try:
        f = open(path, encoding='utf-8')
    except OSError:
        return calls, inputs, result, first_search
    with f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get('type') == 'assistant':
                for c in (o.get('message') or {}).get('content') or []:
                    if isinstance(c, dict) and c.get('type') == 'tool_use':
                        calls.append(c.get('name', ''))
                        inputs.append(c.get('input') or {})
                        if search_id is None and c.get('name') == LEKS + 'search':
                            search_id = c.get('id')
            elif o.get('type') == 'user' and search_id and first_search is None:
                for c in (o.get('message') or {}).get('content') or []:
                    if isinstance(c, dict) and c.get('type') == 'tool_result' and c.get('tool_use_id') == search_id:
                        t = c.get('content')
                        if isinstance(t, list):
                            t = '\n'.join(x.get('text', '') for x in t if isinstance(x, dict))
                        first_search = t or ''
            elif o.get('type') == 'result':
                result = o
    return calls, inputs, result, first_search


def rank_of(search_text, prefix: str):
    """Rank of the first hit whose file name starts with prefix, in leks search output; None if absent."""
    for m in re.finditer(r'(?m)^(\d+)\. (\S+?):\d', search_text or ''):
        if os.path.basename(m.group(2)).startswith(prefix):
            return int(m.group(1))
    return None


def read_verify(path: str):
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.startswith('VERIFY '):
                    return json.loads(line[len('VERIFY '):])
    except (OSError, ValueError):
        pass
    return None


def run_row(tool: str, d: str) -> dict:
    calls, inputs, res, first_search = read_session(os.path.join(d, 'session.jsonl'))
    ver = read_verify(os.path.join(d, 'verify.txt'))
    res = res or {}
    answer = res.get('result') or ''
    checks = (ver or {}).get('checks') or {}
    pre = TOOL_PREFIX[tool]
    return {
        'tool': tool,
        'run': os.path.basename(os.path.normpath(d)),
        'all_checks_passed': bool(ver and ver.get('all_ok')),
        'checks': len(checks),
        'failed_checks': [k for k, v in checks.items() if not v.get('ok')],
        'session_ok': res.get('subtype') == 'success' and not res.get('is_error'),
        'turns': res.get('num_turns'),
        'wall_s': round((res.get('duration_ms') or 0) / 1000.0, 1),
        'usd': round(res.get('total_cost_usd') or 0.0, 4),
        'leks_calls': [c[len(LEKS):] for c in calls if c.startswith(LEKS)],
        'leks_searches': [i.get('query') for c, i in zip(calls, inputs, strict=True) if c == LEKS + 'search'],
        'leks_reads': [i.get('path') for c, i in zip(calls, inputs, strict=True) if c == LEKS + 'read'],
        'tool_calls': [c[len(pre):] for c in calls if c.startswith(pre)],
        'other_calls': [c for c in calls if not c.startswith(LEKS) and not c.startswith(pre)],
        'permission_denials': len(res.get('permission_denials') or []),
        # where the current and the superseded decision stood in the first search the agent ran
        'first_search_rank_d07': rank_of(first_search, 'D-07'),
        'first_search_rank_d03': rank_of(first_search, 'D-03'),
        # plain string checks on the final answer
        'answer_names_d07': 'D-07' in answer,
        'answer_calls_d03_superseded': 'D-03' in answer and 'supersed' in answer.lower(),
    }


def totals(rows: list) -> dict:
    n = len(rows)
    k = sum(r['all_checks_passed'] for r in rows)
    lo, hi = wilson(k, n)
    def med(key):
        return statistics.median(r[key] for r in rows) if rows else None

    return {
        'sessions': n,
        'all_checks_passed': k,
        'wilson_95': [round(lo, 3), round(hi, 3)],
        'median_wall_s': med('wall_s'),
        'median_turns': med('turns'),
        'median_usd': med('usd'),
        'total_usd': round(sum(r['usd'] for r in rows), 4),
        'median_leks_calls': statistics.median(len(r['leks_calls']) for r in rows) if rows else None,
        'median_tool_calls': statistics.median(len(r['tool_calls']) for r in rows) if rows else None,
        'sessions_with_denials': sum(1 for r in rows if r['permission_denials']),
        'first_search_d03_above_d07': sum(1 for r in rows if r['first_search_rank_d03'] and r['first_search_rank_d07']
                                          and r['first_search_rank_d03'] < r['first_search_rank_d07']),
        'answers_naming_d07': sum(r['answer_names_d07'] for r in rows),
        'answers_calling_d03_superseded': sum(r['answer_calls_d03_superseded'] for r in rows),
    }


def find_runs(root: str):
    out = []
    for name in sorted(os.listdir(root)):
        m = re.match(r'(blender|unity)_(.+)$', name)
        p = os.path.join(root, name)
        if m and m.group(2) != 'base' and os.path.isdir(p):
            out.append((m.group(1), p))
    return out


def run_key(run: str):
    return (0, int(run), '') if run.isdigit() else (1, 0, run)


def markdown(rows: list, tot: dict) -> str:
    def rk(v):
        return '-' if v is None else str(v)

    lines = ['| tool | run | all checks | failed | turns | wall time, s | USD | LEKS calls | first search: rank of D-07, D-03 | tool calls | other calls | answer names D-07 |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        lines.append('| %s | %s | %s | %s | %s | %.1f | %.4f | %s | %s, %s | %s | %s | %s |' % (
            r['tool'], r['run'], 'yes' if r['all_checks_passed'] else 'no', ', '.join(r['failed_checks']) or '-',
            r['turns'], r['wall_s'], r['usd'], ', '.join(r['leks_calls']) or '-',
            rk(r['first_search_rank_d07']), rk(r['first_search_rank_d03']), ', '.join(r['tool_calls']) or '-',
            ', '.join(r['other_calls']) or '-', 'yes' if r['answer_names_d07'] else 'no'))
    lines += ['', '| tool | sessions | all checks passed | Wilson 95% CI | median wall time, s | median turns | median USD | total USD | median LEKS calls | median tool calls | first search put D-03 above D-07 | answers naming D-07 | answers calling D-03 superseded |',
              '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for tool, t in tot.items():
        lines.append('| %s | %d | %d | %.2f to %.2f | %.1f | %s | %.4f | %.4f | %s | %s | %d | %d | %d |' % (
            tool, t['sessions'], t['all_checks_passed'], t['wilson_95'][0], t['wilson_95'][1], t['median_wall_s'],
            t['median_turns'], t['median_usd'], t['total_usd'], t['median_leks_calls'], t['median_tool_calls'],
            t['first_search_d03_above_d07'], t['answers_naming_d07'], t['answers_calling_d03_superseded']))
    return '\n'.join(lines) + '\n'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('roots', nargs='*', help='folders that hold blender_<id> and unity_<id> run folders')
    ap.add_argument('--tool', nargs='+', action='append', default=[], metavar=('NAME', 'DIR'),
                    help='a tool name (blender or unity) followed by run folders')
    ap.add_argument('--json', help='write rows and totals as JSON here')
    ap.add_argument('--md', help='write the tables as Markdown here')
    a = ap.parse_args(argv)
    runs = []
    for root in a.roots:
        runs += find_runs(root)
    for group in a.tool:
        if group[0] not in TOOL_PREFIX:
            ap.error('unknown tool %r' % group[0])
        runs += [(group[0], d) for d in group[1:]]
    if not runs:
        ap.error('no run folders found')
    rows = [run_row(t, d) for t, d in runs]
    rows.sort(key=lambda r: (r['tool'], run_key(r['run'])))
    tot = {tool: totals([r for r in rows if r['tool'] == tool]) for tool in sorted({r['tool'] for r in rows})}
    md = markdown(rows, tot)
    sys.stdout.write(md)
    if a.md:
        with open(a.md, 'w', encoding='utf-8') as f:
            f.write(md)
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump({'runs': rows, 'totals': tot}, f, indent=2)
            f.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
