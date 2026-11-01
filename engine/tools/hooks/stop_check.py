#!/usr/bin/env python3
"""agentStop hook: before an agent finishes a turn, run the fast vault check once.

If knowledge changed and the vault is no longer consistent (index not rebuilt, broken link,
changed evidence, missing metadata, a non-English letter ...), the turn is blocked once with the
errors, so the agent fixes them. A second stop in the same turn is always allowed, so the hook
can never trap a session. Nothing changed since the last check AND that check passed: the hook returns at once."""
import json
import os
from pathlib import Path
import sys

VAULT = Path(os.environ.get('XR_VAULT_ROOT') or Path(__file__).resolve().parents[2])
WATCH = ['wiki','system','.github','.agents','tools','raw']
WATCH_FILES = ['00_HOME.md','01_CONTINUE.md','AGENTS.md','README.md','Vault.canvas']

def latest_change():
    """Newest modification or status change of the watched files. Raw evidence counts file by file (an in-place
    edit does not change its folder) and uses the status-change time too, which a program cannot set back."""
    newest = 0.0
    for name in WATCH_FILES:
        p = VAULT/name
        if p.exists():
            st = p.stat()
            newest = max(newest, st.st_mtime, st.st_ctime)
    for top in WATCH:
        for dirpath, dirnames, filenames in os.walk(VAULT/top):
            dirnames[:] = [d for d in dirnames if d not in ('__pycache__','node_modules')]
            newest = max(newest, os.stat(dirpath).st_mtime)
            for f in filenames:
                try:
                    st = os.lstat(os.path.join(dirpath, f))
                except OSError:
                    continue
                newest = max(newest, st.st_mtime, st.st_ctime if top == 'raw' else st.st_mtime)
    return newest

def open_incidents():
    try:
        items = json.loads((VAULT/'.brain'/'integrity-incidents.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return [i for i in items if i.get('status') == 'open']

def last_check():
    """Time and result of the newest check report; a failed report never lets the hook skip."""
    newest, passed = 0.0, False
    for name in ('validation.json','validation-fast.json'):
        p = VAULT/'.brain'/name
        if p.exists() and p.stat().st_mtime > newest:
            newest = p.stat().st_mtime
            try:
                passed = bool(json.loads(p.read_text(encoding='utf-8')).get('ok'))
            except (OSError, ValueError):
                passed = False
    return newest, passed

def main():
    try:
        payload = json.loads(sys.stdin.read() or '{}')
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get('stop_hook_active') or payload.get('stopHookActive'):
        print('{}')
        return 0
    try:
        return stop(payload)
    except Exception as exc:            # an error around the check must block once too, never pass silently
        print(json.dumps({'decision':'block','reason':'The stop hook could not finish ({}: {}). Run '
                          '`python3 tools/brain.py check` and fix what it reports; do not report success.'.format(
                              type(exc).__name__, str(exc)[:300])}))
        return 0

def stop(payload):
    incidents = open_incidents()
    checked_at, passed = last_check()
    if not incidents and passed and latest_change() <= checked_at:
        print('{}')
        return 0
    sys.path.insert(0, str(VAULT/'tools'))
    try:
        import brain
        result = brain.check(VAULT, write_report=True, fast=True)
    except Exception as exc:            # a check that cannot run must block, never pass silently
        print(json.dumps({'decision':'block','reason':'The vault check could not run ({}: {}). Fix the tools, run '
                          '`python3 tools/brain.py check`, and do not report success.'.format(type(exc).__name__, str(exc)[:300])}))
        return 0
    incidents = result.get('open_incidents', incidents)
    if result['ok'] and not incidents:
        print('{}')
        return 0
    errors = result['errors']
    reason = ('The vault check failed ({} errors). Run `python3 tools/brain.py build`, then '
              '`python3 tools/brain.py check`, and fix: '.format(len(errors)) + ' | '.join(errors[:8])) if errors else ''
    if incidents:
        reason = ('{} integrity incident(s) remain open ({}). Evidence may be damaged: restore it with '
                  '`python3 tools/brain.py restore <path> --replace`, re-run the check and resolve the incident with '
                  '`python3 tools/brain.py incident resolve <ID> --reason "..."`. Do not report success. '.format(
                      len(incidents), ', '.join(i['id'] for i in incidents[:5]))) + reason
    print(json.dumps({'decision':'block','reason':reason}))
    return 0

if __name__ == '__main__':
    sys.exit(main())
