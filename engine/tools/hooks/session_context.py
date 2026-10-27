#!/usr/bin/env python3
"""sessionStart hook: give every agent session the core vault rules and the compact current state.

This replaces reading several long pages before the first answer: the agent starts with the rules
and wiki/now.md, and opens the vault map, schema or index only when a task needs them. With protect_raw on it also
re-locks any registered raw file that lost its immutable flag; protection that is off, incomplete or unverifiable is
announced at the top."""
import json
import os
import re
from pathlib import Path
import sys

VAULT = Path(os.environ.get('XR_VAULT_ROOT') or Path(__file__).resolve().parents[2])
LIMIT = 9000

RULES = """Knowledge vault. The full contract is AGENTS.md (loaded with the instructions). Essentials: \
English only; raw/ is immutable (new files + `python3 tools/brain.py register`, moves with `brain.py move`); \
wiki/archive/ and raw/archive/ are history, never current plans; record AI work by model name only. \
For a question call the MCP tool `vault_answer_pack` first; keep steps few and answers short."""

KEEP = re.compile(r'app|delivery|architecture|next steps|open', re.I)

def current_state():
    """wiki/now.md without its front matter, link syntax and navigation-only sections (about half the size)."""
    path = VAULT/'wiki'/'now.md'
    if not path.is_file():
        return ''
    text = path.read_text(encoding='utf-8')
    if text.startswith('---\n'):
        end = text.find('\n---', 4)
        text = text[end+4:].lstrip('\n') if end > 0 else text
    parts = re.split(r'(?m)^(?=## )', text)
    kept = [parts[0]] + [part for part in parts[1:] if KEEP.search(part.splitlines()[0])]
    text = ''.join(kept)
    text = re.sub(r'\[\[[^\]|]+\|([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    text = '\n'.join(line for line in text.splitlines() if not line.startswith(('Background:', 'Earlier states')))
    return re.sub(r'\n{3,}', '\n\n', text).strip()

def open_incidents():
    """Integrity incidents stay visible at the start of every session until they are resolved."""
    path = VAULT/'.brain'/'integrity-incidents.json'
    try:
        items = [i for i in json.loads(path.read_text(encoding='utf-8')) if i.get('status') == 'open']
    except (OSError, ValueError):
        return ''
    if not items:
        return ''
    lines = ['- %s (since %s): %s' % (i['id'], i.get('first_seen', '?'), i['error']) for i in items[:10]]
    return ('INTEGRITY INCIDENTS OPEN (%d). Evidence may be damaged. Do not report success. Restore with '
            '`python3 tools/brain.py restore <path> --replace`, re-run the check, then '
            '`python3 tools/brain.py incident resolve <ID> --reason "..."`.\n' % len(items)) + '\n'.join(lines)

def evidence_protection():
    """(warning for the top of the context, or '', short status line)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import evidence_store
        if not evidence_store.settings(VAULT)['protect_raw']:
            return ('EVIDENCE PROTECTION OFF: "protect_raw" is false in system/vault-config.json, so raw/ files are writable '
                    'at the file-system level. Do not change raw/; tell the owner.'), ''
        if not evidence_store.flags_supported():
            return 'EVIDENCE PROTECTION UNAVAILABLE: this file system has no immutable flags.', ''
        state = evidence_store.protect(VAULT, 'apply')
    except Exception as exc:            # protection that cannot be verified is announced, never assumed
        return ('EVIDENCE PROTECTION NOT VERIFIED ({}: {}): run `python3 tools/brain.py doctor`.'.format(
            type(exc).__name__, str(exc)[:200])), ''
    if state['protected'] < state['files']:
        return ('EVIDENCE PROTECTION INCOMPLETE: %d of %d registered raw files are not immutable. Run `python3 '
                'tools/brain.py protect apply` and `python3 tools/brain.py doctor`.' % (state['files'] - state['protected'],
                                                                                       state['files'])), ''
    relocked = '; %d re-locked at session start' % state['changed'] if state['changed'] else ''
    return '', 'Evidence protection: on; %d registered raw files immutable%s.' % (state['files'], relocked)

def main():
    try:
        json.loads(sys.stdin.read() or '{}')
    except json.JSONDecodeError:
        pass
    state = current_state()
    incidents = open_incidents()
    warning, status = evidence_protection()
    top = '\n\n'.join(x for x in (warning, incidents) if x)
    context = (top + '\n\n' if top else '') + RULES + ('\n' + status if status else '') + (
        '\n\nCurrent state (wiki/now.md):\n' + state if state else '')
    if len(context) > LIMIT:
        context = context[:LIMIT] + '\n[truncated; open wiki/now.md]'
    print(json.dumps({'additionalContext':context}))
    return 0

if __name__ == '__main__':
    sys.exit(main())
