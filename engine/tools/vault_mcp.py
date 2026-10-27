#!/usr/bin/env python3
"""Read-only MCP server for a LEKS vault (stdio, JSON-RPC 2.0, standard library only).

Registered for VS Code in .vscode/mcp.json as "xr-vault". It lets every agent profile, including
the read-only specialists, search pages, find and resolve sources and read claims without a shell.
No tool changes the vault's content; vault_search,
vault_answer_pack and vault_status may refresh derived caches in .brain/ (page index, claim history).
"""
import json
from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
import brain  # noqa: E402

SERVER = {'name':'xr-vault','version':'1.0.0'}
KNOWN_VERSIONS = ['2025-06-18','2025-03-26','2024-11-05']

def schema(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}

TEXT = {'type':'string'}
LIMIT = {'type':'integer','minimum':1,'maximum':50,'default':5}
TOOLS = [
    {'name':'vault_search','description':'Rank wiki pages, system pages and skills for a question. Returns path, title, '
     'summary, as_of, status. Archived pages rank lower unless the question asks for history.',
     'inputSchema':schema({'question':TEXT,'limit':LIMIT},['question'])},
    {'name':'vault_answer_pack','description':'Start here for a question: matching passages of the best pages (the best page '
     'first, others marked [path]), facts with confidence and further pages, in one call. Read full pages or sources only if '
     'the passages are not enough.',
     'inputSchema':schema({'question':TEXT,'budget':{'type':'integer','minimum':300,'maximum':6000,'default':1800}},['question'])},
    {'name':'vault_source','description':'Find raw sources by path words, or by full text with content=true (exact phrases first, '
     'identical copies collapsed). Returns provenance; read the file next.',
     'inputSchema':schema({'terms':TEXT,'content':{'type':'boolean','default':False},'limit':LIMIT},['terms'])},
    {'name':'vault_media','description':'Image or PDF in raw/: its text (PDF text layer exact; screenshots and scans by '
     'on-device OCR with confidence), copies and mentions. Give a path, file name or words.',
     'inputSchema':schema({'path_or_words':TEXT,'budget':{'type':'integer','minimum':200,'maximum':6000,'default':1500}},['path_or_words'])},
    {'name':'vault_resolve','description':'Find a source by current path, original path, former path (after renames or archiving) or ID.',
     'inputSchema':schema({'path_or_id':TEXT},['path_or_id'])},
    {'name':'vault_claims','description':'Look up facts (claims) by ID, page path or words: confidence, scope, as_of, sources, lifecycle.',
     'inputSchema':schema({'query':TEXT,'limit':{'type':'integer','minimum':1,'maximum':50,'default':10}},['query'])},
    {'name':'vault_status','description':'Run the fast integrity check without writing a report: errors, warnings and counts.',
     'inputSchema':schema({})},
]

TOOL_SCHEMAS = {t['name']:t['inputSchema'] for t in TOOLS}
TYPES = {'string':str, 'boolean':bool, 'integer':int}

def coerce(spec, value):
    """Integer strings ("5") and "true"/"false" as earlier versions accepted them; anything else unchanged."""
    if spec['type'] == 'integer' and isinstance(value, str) and value.strip().lstrip('-').isdigit():
        return int(value)
    if spec['type'] == 'boolean' and isinstance(value, str) and value.strip().lower() in ('true','false'):
        return value.strip().lower() == 'true'
    return value

def invalid_arguments(schema, args):
    """Why `args` does not match the tool's published input schema, or None; coerces values in place."""
    if not isinstance(args, dict):
        return 'arguments must be an object'
    props = schema['properties']
    missing = [k for k in schema['required'] if k not in args]
    if missing:
        return 'missing required argument(s): '+', '.join(missing)
    extra = [k for k in args if k not in props]
    if extra:
        return 'unknown argument(s): '+', '.join(extra)
    for key in list(args):
        spec = props[key]
        value = args[key] = coerce(spec, args[key])
        kind = TYPES[spec['type']]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            return '%s must be %s' % (key, 'an integer' if kind is int else 'a '+spec['type'])
        if 'enum' in spec and value not in spec['enum']:
            return '%s must be one of %s' % (key, ', '.join(spec['enum']))
        if 'minimum' in spec and value < spec['minimum'] or 'maximum' in spec and value > spec['maximum']:
            return '%s must be between %s and %s' % (key, spec.get('minimum','-inf'), spec.get('maximum','inf'))
    return None

def call(name, a):
    if name == 'vault_search':
        return brain.search(ROOT, a['question'], int(a.get('limit',5)))
    if name == 'vault_answer_pack':
        return brain.answer_pack(ROOT, a['question'], int(a.get('budget',1800)))
    if name == 'vault_source':
        return brain.source_search(ROOT, a['terms'], int(a.get('limit',5)), bool(a.get('content',False)))
    if name == 'vault_media':
        return brain.media_info(ROOT, a['path_or_words'], int(a.get('budget',1500)))
    if name == 'vault_resolve':
        return brain.resolve(ROOT, a['path_or_id'])
    if name == 'vault_claims':
        q = a['query'].strip()
        claims = brain.read_json(ROOT/'system/claims.json',{'claims':[]})['claims']
        words = [w for w in brain.tokens(q) if w not in brain.STOP]
        exact = [c for c in claims if c['id'].lower() == q.lower() or c.get('page') == q]
        if exact:
            return exact[:int(a.get('limit',10))]
        hits = [c for c in claims if words and all(w in brain.normalize(c.get('text','')+' '+c['id']) for w in words)]
        return hits[:int(a.get('limit',10))]
    if name == 'vault_status':
        result = brain.check(ROOT, write_report=False, fast=True)
        return {k:result.get(k) for k in ('ok','errors','warnings','raw_files','wiki_pages','archived_pages','claims','agents','skills','open_incidents')}
    raise KeyError(name)

def respond(message_id, result=None, error=None):
    reply = {'jsonrpc':'2.0','id':message_id}
    reply.update({'error':error} if error else {'result':result})
    sys.stdout.write(json.dumps(reply, ensure_ascii=False)+'\n')
    sys.stdout.flush()

def handle(message):
    if not isinstance(message, dict):
        respond(None, error={'code':-32600,'message':'invalid request: one JSON object per line (batches are not supported)'})
        return
    method, mid, params = message.get('method'), message.get('id'), message.get('params') or {}
    if not isinstance(params, dict):
        params = {}
    if mid is None:
        return                      # notification, e.g. notifications/initialized
    if method == 'initialize':
        version = params.get('protocolVersion')
        respond(mid, {'protocolVersion':version if version in KNOWN_VERSIONS else KNOWN_VERSIONS[0],
                      'capabilities':{'tools':{'listChanged':False}},'serverInfo':SERVER,
                      'instructions':'Read-only access to the vault. Cite paths and dates; '
                                     'archived pages are history, not current plans.'})
    elif method == 'ping':
        respond(mid, {})
    elif method == 'tools/list':
        respond(mid, {'tools':TOOLS})
    elif method == 'tools/call':
        name, args = params.get('name'), params.get('arguments') or {}
        if name not in TOOL_SCHEMAS:
            respond(mid, error={'code':-32602,'message':'unknown tool '+str(name)})
            return
        problem = invalid_arguments(TOOL_SCHEMAS[name], args)
        if problem:
            respond(mid, error={'code':-32602,'message':'invalid arguments for %s: %s' % (name, problem)})
            return
        try:
            result = call(name, args)
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1)
            respond(mid, {'content':[{'type':'text','text':text}],'isError':False})
        except Exception as exc:  # report tool failures to the agent instead of crashing the server
            respond(mid, {'content':[{'type':'text','text':type(exc).__name__+': '+str(exc)}],'isError':True})
    else:
        respond(mid, error={'code':-32601,'message':'method not found: '+str(method)})

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            respond(None, error={'code':-32700,'message':'parse error'})
            continue
        handle(message)
    import dense_client
    dense_client.close()            # end of input: stop a dense helper this server started (it would also stop on EOF)
    return 0

if __name__ == '__main__':
    sys.exit(main())
