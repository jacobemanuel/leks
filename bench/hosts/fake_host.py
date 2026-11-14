#!/usr/bin/env python3
"""A stand-in host for testing bench/hosts/run.py with no account: prints one session's event stream.

    python3 bench/hosts/fake_host.py --format claude --arm leks --questions QUESTIONS.json -- PROMPT

It finds the question in the prompt, answers it and prints the events the
named host prints in its headless JSON mode (claude stream-json, codex exec
--json, copilot -p --output-format json), with token counts and costs that are
made up and fixed per arm. Nothing here is a measurement. In the plain arm it
names the stale file of a current-state question instead of the gold one, so
the task check can fail. FAKE_HOST_SLEEP=SECONDS makes it wait first and
FAKE_HOST_FAIL=1 makes it exit 3 with no result, for the timeout and error paths.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# made-up session shapes per arm: (input tokens, output tokens, turns) of the main model, and of the small
# model in the subagent arm. picked so the arms differ, not to look like any real host
SHAPES = {
    'plain': {'main': (52000, 860), 'small': None, 'turns': 4},
    'leks': {'main': (37000, 430), 'small': None, 'turns': 3},
    'team': {'main': (88000, 1300), 'small': None, 'turns': 6},
    'subagent': {'main': (24000, 380), 'small': (41000, 620), 'turns': 3},
}
# made-up prices in USD per 1M input and output tokens
PRICES = {'main': (3.0, 15.0), 'small': (1.0, 5.0)}
CACHED_SHARE = 0.75


def find_question(prompt: str, path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        qs = json.load(f)['questions']
    for q in qs:
        if q['question'] in prompt:
            return q
    return {'id': None, 'question': prompt, 'gold': [], 'stale': [], 'category': None}


def answer_for(q: dict, arm: str) -> str:
    if not q.get('gold'):
        return 'The files do not answer this question.'
    files = q['gold']
    if arm == 'plain' and q.get('stale'):
        files = q['stale']
    return 'Answer to %s. Source: %s' % (q['id'], ', '.join(files))


def usd(tokens, kind: str) -> float:
    rin, rout = PRICES[kind]
    return (tokens[0] * rin + tokens[1] * rout) / 1e6


def calls(arm: str) -> list:
    """(tool name kind, count) per arm: file reads, LEKS searches, subagent starts"""
    if arm == 'plain':
        return [('file', 3)]
    if arm == 'leks':
        return [('leks', 1), ('file', 1)]
    if arm == 'team':
        return [('agent', 2), ('leks', 2), ('file', 2)]
    return [('agent', 1), ('leks', 2), ('file', 1)]


def models_of(arm: str, main: str, small: str) -> list:
    shape = SHAPES[arm]
    out = [(main, shape['main'], 'main')]
    if shape['small']:
        out.append((small, shape['small'], 'small'))
    return out


def claude_events(arm: str, answer: str, main: str, small: str) -> list:
    names = {'file': 'Read', 'leks': 'mcp__leks__search', 'agent': 'Agent'}
    ev = [{'type': 'system', 'subtype': 'init', 'model': main, 'tools': ['Read', 'Grep', 'Glob']}]
    n = 0
    for kind, k in calls(arm):
        for _ in range(k):
            n += 1
            ev.append({'type': 'assistant', 'message': {'id': 'msg_%d' % n, 'content': [
                {'type': 'tool_use', 'id': 'tu_%d' % n, 'name': names[kind], 'input': {'query': 'fake'}}]}})
    ev.append({'type': 'assistant', 'message': {'id': 'msg_last', 'content': [{'type': 'text', 'text': answer}]}})
    usage, total = {}, 0.0
    for model, tok, kind in models_of(arm, main, small):
        cached = int(tok[0] * CACHED_SHARE)
        usage[model] = {'inputTokens': tok[0] - cached, 'cacheReadInputTokens': cached,
                        'cacheCreationInputTokens': 0, 'outputTokens': tok[1]}
        total += usd(tok, kind)
    ev.append({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': answer,
               'total_cost_usd': round(total, 6), 'num_turns': SHAPES[arm]['turns'], 'modelUsage': usage})
    return ev


def codex_events(arm: str, answer: str, main: str, small: str) -> list:
    ev = [{'type': 'thread.started', 'thread_id': 'fake'}, {'type': 'turn.started'}]
    n = 0
    for kind, k in calls(arm):
        for _ in range(k):
            n += 1
            if kind == 'file':
                item = {'id': 'item_%d' % n, 'type': 'command_execution', 'command': 'cat notes/x.md'}
            elif kind == 'leks':
                item = {'id': 'item_%d' % n, 'type': 'mcp_tool_call', 'server': 'leks', 'tool': 'search',
                        'arguments': {'query': 'fake'}}
            else:
                item = {'id': 'item_%d' % n, 'type': 'collab_tool_call', 'tool': 'spawn_agent'}
            ev.append({'type': 'item.completed', 'item': item})
    ev.append({'type': 'item.completed', 'item': {'id': 'item_last', 'type': 'agent_message', 'text': answer}})
    inp = sum(t[0] for _, t, _ in models_of(arm, main, small))
    out = sum(t[1] for _, t, _ in models_of(arm, main, small))
    ev.append({'type': 'turn.completed', 'usage': {'input_tokens': inp, 'cached_input_tokens': int(inp * CACHED_SHARE),
                                                   'output_tokens': out, 'reasoning_output_tokens': out // 2}})
    return ev


def copilot_events(arm: str, answer: str, main: str, small: str) -> list:
    names = {'file': ('view', None), 'leks': ('leks-search', 'leks'), 'agent': ('task', None)}
    ev = [{'id': 'e0', 'type': 'session.start', 'data': {'sessionId': 'fake-session', 'selectedModel': main}}]
    nano_total, n = 0, 0
    for model, tok, kind in models_of(arm, main, small):
        nano = int(round(usd(tok, kind) * 100 * 1e9))  # 1 credit = 0.01 USD, nano units
        nano_total += nano
        n += 1
        ev.append({'id': 'e%d' % n, 'type': 'assistant.usage', 'data': {
            'model': model, 'inputTokens': tok[0], 'outputTokens': tok[1],
            'cacheReadTokens': int(tok[0] * CACHED_SHARE), 'cacheWriteTokens': 0, 'initiator': 'user' if
            kind == 'main' else 'agent', 'copilotUsage': {'totalNanoAiu': nano}}})
    for kind, k in calls(arm):
        for _ in range(k):
            n += 1
            name, server = names[kind]
            d = {'toolName': name, 'arguments': {'query': 'fake'}}
            if server:
                d['mcpServerName'] = server
            ev.append({'id': 'e%d' % n, 'type': 'tool.execution_start', 'data': d})
    ev.append({'id': 'e%d' % (n + 1), 'type': 'assistant.message', 'data': {'model': main, 'content': answer}})
    ev.append({'id': 'e%d' % (n + 2), 'type': 'session.shutdown', 'data': {'totalNanoAiu': nano_total,
                                                                          'totalPremiumRequestCost': 1}})
    return ev


EVENTS = {'claude': claude_events, 'codex': codex_events, 'copilot': copilot_events}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--format', choices=sorted(EVENTS), required=True)
    ap.add_argument('--arm', choices=sorted(SHAPES), required=True)
    ap.add_argument('--questions', required=True)
    ap.add_argument('--model', default='fake-main')
    ap.add_argument('--small-model', default='fake-small')
    ap.add_argument('prompt')
    a = ap.parse_args(argv)
    wait = float(os.environ.get('FAKE_HOST_SLEEP') or 0)
    if wait > 0:
        time.sleep(wait)
    if os.environ.get('FAKE_HOST_FAIL') == '1':
        print('fake host: failing on purpose', file=sys.stderr)
        return 3
    answer = answer_for(find_question(a.prompt, a.questions), a.arm)
    for e in EVENTS[a.format](a.arm, answer, a.model, a.small_model):
        sys.stdout.write(json.dumps(e) + '\n')
    sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
