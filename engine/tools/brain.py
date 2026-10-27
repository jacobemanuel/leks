#!/usr/bin/env python3
"""LEKS vault engine: routing, provenance, integrity checks and safe moves.

Standard library only; no LLM calls. Commands:
  build                      refresh the index, registry citations, graph and full-text index
  check [--fast]             integrity, links, metadata, claims, agents, skills, hooks, language
  evaluate                   retrieval test (top-5 pass and rank-1 accuracy)
  search "<question>"        route a question to wiki/system pages
  source "<terms>" [--content]   find raw sources by path, or by full text (SQLite FTS5)
  resolve "<path or ID>"     find a source by current, original or former path
  register raw/<file> --origin "<text>"   record a new source (hash, size, provenance)
  move <old> <new> --reason "<text>" | --plan moves.json   move files or folders safely
  media [--limit N]          extract the text of images and PDFs in raw/ on this Mac (then run build)
  media-info "<path or words>"   text, OCR confidence and mentions of one image or PDF
"""
import argparse
import collections
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_store  # noqa: E402
import storage  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = 'system/vault-config.json'
VALID_CONFIDENCE = {'verified', 'reported', 'inferred', 'planned', 'unknown'}
FINDER_METADATA = {'.DS_Store'}
# English-only guard. The Polish-specific letters
# are written as escapes so that this file itself stays free of them.
POLISH_LETTERS = re.compile('[\u0105\u0107\u0119\u0142\u0144\u00f3\u015b\u017a\u017c'
                            '\u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u0179\u017b]')
TEXT_SUFFIXES = {'.md','.txt','.log','.json','.jsonl','.ndjson','.yaml','.yml','.csv','.tsv',
                 '.html','.htm','.xml','.svg','.py','.sh','.swift','.cs','.js','.mjs','.css','.usda',
                 '.toml','.canvas','.mtlx','.patch'}
GUARDED_SUFFIXES = TEXT_SUFFIXES
RENDITION_TYPES = {'english-translation','english-normalisation','english-rendition-with-elision'}
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
WIKI = re.compile(r'!?\[\[([^\]]+)\]\]')
MD = re.compile(r'(?<!!)\[[^\]\n]+\]\(([^\)\n]+)\)')
# AI work is attributed by model name only, never by client tool.
TOOL_ATTRIBUTION = re.compile(r'\b(codex|claude code|chatgpt|copilot (?:agent|session|cli)|github copilot (?:agent|session)|vs ?code agent)\b', re.I)
ATTRIBUTION_FIELDS = ('author','authors','translator','origin','authored_by','generated_by','model_tool','owner')
# Names that say nothing when shown alone as a node in the Obsidian graph.
GENERIC_NAMES = {'readme','assessment','evidence','handoff','notes','report','summary','verification','request',
                 'provenance','index','overview','plan','status','output','results','untitled'}
ARCHIVE_INTENT = {'archive','archived','history','historical','proposal','proposals','draft','drafts','adr',
                  'native','onboarding','prototype','earlier','superseded','previous','old','gate','roadmap'}
READONLY_FORBIDDEN_TOOLS = {'edit','execute','new','runCommands','runTasks','terminal'}

def read_json(path, default=None):
    """JSON from path, or default if it is missing; a damaged derived file (.brain/) counts as missing and is rebuilt."""
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    except ValueError:
        if '.brain' not in Path(path).parts: raise
        return default

def save_json(path, data):
    storage.atomic_write_json(path, data)

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def config(root):
    cfg = read_json(root/CONFIG, {}) or {}
    cfg.setdefault('outside_archive', str(root.parent/(root.name+'_original_language_archive')))
    cfg.setdefault('former_roots', [str(root)+'/', str(root.parent/(root.name+'_migration'))+'/'])
    cfg['outside_archive'] = os.path.expanduser(cfg['outside_archive'])
    cfg['former_roots'] = [os.path.expanduser(r) for r in cfg['former_roots']]
    return cfg

def metadata(text):
    if not text.startswith('---\n'):
        return {}
    end = text.find('\n---', 4)
    if end < 0:
        return {}
    result = {}
    for line in text[4:end].splitlines():
        if not line or ':' not in line or line[0].isspace():
            continue
        key, value = line.split(':', 1)
        value = value.strip()
        try:
            result[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            result[key.strip()] = value.strip('"').strip("'")
    return result

def rel(root, p):
    return p.relative_to(root).as_posix()

def pages(root):
    result = []
    for p in sorted((root/'wiki').rglob('*.md')):
        if p.name == 'index.md' and p.parent == root/'wiki':
            continue
        text = p.read_text(encoding='utf-8')
        result.append((p, metadata(text), text))
    return result

def is_archived(path_rel, meta):
    return path_rel.startswith('wiki/archive/') or meta.get('status') == 'archived'

def curated_files(root):
    result = list((root/'wiki').rglob('*.md')) + list((root/'system').rglob('*.md'))
    result += [root/n for n in ['00_HOME.md','01_CONTINUE.md','AGENTS.md','README.md'] if (root/n).is_file()]
    result += list((root/'.github').rglob('*.md')) + list((root/'.agents/skills').rglob('*.md'))
    return sorted(set(result))

def active_files(root):
    result = curated_files(root) + list((root/'tools').glob('*.py'))
    result += [root/p for p in ['system/claims.json','system/source-registry.json','system/retrieval-cases.json',
                               'system/language-allowlist.json',CONFIG,'Vault.canvas'] if (root/p).is_file()]
    return sorted(set(result))

def wiki_target(root, target):
    target = target.replace('\\|','|').split('|',1)[0]
    target = unquote(target.split('#',1)[0].strip())
    if not target:
        return None
    p = root/target
    if p.is_file():
        return p
    if Path(str(p)+'.md').is_file():
        return Path(str(p)+'.md')
    return p

def normalize(text):
    # NFKD removes combining accents; U+0142 (l with stroke) has no decomposition.
    text = text.casefold().replace('\u0142','l')
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))

STEMS = {'marker':'marker','material':'material','build':'build','anchor':'anchor','texture':'texture',
         'launch':'launch','package':'package','mirror':'mirror','door':'door','gate':'gate'}

def tokens(text):
    words = re.findall(r'[a-z0-9]+', normalize(text))
    # A small, explicit domain stemmer folds plural and verb forms without a
    # dependency or a brittle exact-question router. It applies to both corpus
    # and queries so singular/plural forms share one vocabulary.
    return [next((canonical for prefix,canonical in STEMS.items() if w.startswith(prefix)),w) for w in words]

def stem(word):
    """Light English suffix folding (conflicts/conflict, signed/sign, entries/entry), used only to pick
    the passages of a page for answer_pack. Page ranking does not use it: in tests it lowered rank-1
    accuracy (regression 107 -> 104 of 108), so search() keeps exact tokens."""
    if len(word) <= 4 or word.isdigit():
        return word
    if word.endswith('ies') and len(word) > 5:
        return word[:-3] + 'y'
    if word.endswith('sses'):
        return word[:-2]
    if word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        word = word[:-1]
    for suffix in ('ing', 'ed'):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word

def terms(text):
    return [stem(w) for w in tokens(text)]

# English-only query vocabulary (the vault is English-only). Translate a question into English before searching.
STOP = set('a an the and or of in on for to is are was were be been does do did why what how can which who when where with from by at as it its this that our we i go goes went gone '
           'should would could will shall may might must each every any some own become becomes became exist exists there here '
           'then than so if about into also just very much many more most such have has had actually really use'.split())
# Domain vocabulary; never a table mapping benchmark questions to answers.
ALIASES = {
    'memory': ['footprint','jetsam'],
    'door':['grip','interactions'], 'steering':['wheel','interactions'],
    'headset':['avp','device'], 'device':['avp'],
    'current':['now'], 'latest':['now'], 'today':['now'],
    'engine':['architecture'],
    'format':['package','contract'], 'designer':['publisher'],
    'car':['vehicle'], 'cars':['vehicle'], 'marker':['placement'], 'anchor':['spatial'],
    'glass':['materials'], 'image':['texture','uv'], 'texture':['uv'], 'flipped':['uv','flip'],
    'rebuild':['architecture','adr'], 'airdrop':['intake','import'],
    'junk':['sources','archive','coverage'], 'conversations':['conversation','sources'],
    'agent':['copilot','role'], 'skills':['workflow'], 'launch':['startup'],
    'handover':['handoff'], 'delivery':['build','evidence'],
    'archived':['archive'], 'draft':['archive','proposal'],
    'valid':['active','lifecycle'],
    # Question vocabulary that the pages express with other words (domain thesaurus, not question routing).
    'contradict':['conflicts','pitfalls'], 'contradiction':['conflicts','pitfalls'], 'disagree':['conflicts'],
    'inconsistent':['conflicts'], 'missing':['gaps','open'], 'unanswered':['open','questions','gaps'],
    'issue':['problems'], 'bug':['problems'], 'broken':['problems'], 'failing':['problems','fails'],
    'successor':['handover','maintainer'], 'continuity':['handover'], 'maintain':['maintainer','handover'],
    'sign':['signing','signature'], 'signature':['signing'], 'certificate':['signing'], 'install':['installed','signing'],
    'rename':['move','renames'], 'chronological':['timeline'], 'chronologically':['timeline'],
    'colour':['palette','legend'], 'color':['palette','legend'], 'rule':['schema','rules'], 'policy':['policy','schema'],
    'definition':['glossary'], 'term':['glossary'],
}
# Page priors: hub pages mention every topic, so they must not outrank the topic page itself.
# Defaults here; system/vault-config.json ("search_hub_priors") may add or override pages.
HUB_PRIORS = {'wiki/log.md':(0.35,{'log','changed','change','history','chronological'}),
              '01_CONTINUE.md':(0.6,{'continue','resume','conversation','checkpoint','chat'}),
              'wiki/sources.md':(0.8,{'sources','source','raw','archive','registry'}),
              '00_HOME.md':(0.6,{'home','start','mission','control','overview','workspace','page'})}
CURRENT_INTENT = {'current','currently','now','latest','today','status','next','existing','newest','final'}
# Reviews are unapproved opinions: they rank below knowledge pages unless the question asks for one.
REVIEW_INTENT = {'review','reviews','opinion','opinions','recommendation','recommend','advice','assessment'}
# "How do I ..." questions are about procedures, so procedure pages get a small boost.
PROCEDURE_PAGES = {'system/WORKFLOWS.md','system/MAINTAINER_GUIDE.md'}
SKILL_INTENT = {'skill','skills','procedure','steps','step'}
PHRASE_STOP = {'own','every','each','any','some','many','more','most','become','became','becomes','exist','exists'}
PROCEDURE_QUESTION = re.compile(r'\bhow (?:do|can|should) (?:i|we)\b|\bhow to\b', re.I)
# Dated delivery records: a newer record of the same series supersedes an older one for status questions.
DATED_RECORD = re.compile(r'^(wiki/codebase/)(\d{4}-\d{2}-\d{2})-')

def hub_priors(root):
    priors = dict(HUB_PRIORS)
    for path, (weight, words) in (config(root).get('search_hub_priors') or {}).items():
        priors[path] = (float(weight), set(words))
    return priors

def head_text(m):
    return ' '.join([str(m.get('title','')),str(m.get('label','')),str(m.get('summary','')),' '.join(m.get('keywords',[]) or [])])

def _namespace():
    """This engine's functions and constants, whatever name the module was loaded under."""
    import types
    return types.SimpleNamespace(**globals())

def search(root, query, limit=5):
    """Page search with the configured engine (tools/retrieval.py); the default R1 is identical to search_scan."""
    import retrieval
    return retrieval.search(_namespace(), root, query, limit)[0]

def search_scan(root, query, limit=5):
    """Reference engine R0: reads and tokenises every page per query. Kept for regression comparison."""
    query_words = [w for w in tokens(query) if w not in STOP]
    qset = set(query_words)
    qall = set(tokens(query))    # intent words may be stop words ("when")
    expanded = list(query_words)
    for word in query_words:
        expanded += tokens(' '.join(ALIASES.get(word, [])))
    expanded = list(dict.fromkeys(expanded))
    # A quantifier or "own" next to a content word ("own app", "every product") carries meaning that
    # single-word scoring drops, so pages using the same pair get a small bonus.
    all_words = tokens(query)
    phrases = {a+' '+b for a, b in zip(all_words, all_words[1:])
               if (a in PHRASE_STOP and b not in STOP) or (b in PHRASE_STOP and a not in STOP)}
    corpus = []
    for p, m, text in pages(root):
        corpus.append((p,m,collections.Counter(tokens(head_text(m))*5 + tokens(text))))
    for p in sorted((root/'system').glob('*.md')):
        txt = p.read_text(encoding='utf-8')
        m = metadata(txt)
        if not m.get('title'):
            m = {'title':txt.splitlines()[0].lstrip('# '),'summary':'Knowledge workflows and agent configuration.'}
        corpus.append((p,m,collections.Counter(tokens(head_text(m))*5 + tokens(txt))))
    # Skills are the procedures themselves; their shared rules footer is not indexed.
    for p in sorted((root/'.agents/skills').glob('*/SKILL.md')):
        txt = p.read_text(encoding='utf-8').split('\n---\nRules:')[0]
        sm = metadata(txt)
        m = {'title':sm.get('name',''),'summary':sm.get('description',''),'category':'Skill'}
        corpus.append((p,m,collections.Counter(tokens(head_text(m))*5 + tokens(txt))))
    for entry in ('01_CONTINUE.md', '00_HOME.md'):
        page = root/entry
        if page.is_file():
            txt = page.read_text(encoding='utf-8')
            m = metadata(txt)
            corpus.append((page,m,collections.Counter(tokens(head_text(m))*5 + tokens(txt))))
    df = collections.Counter(w for _,_,c in corpus for w in c)
    n = len(corpus)
    avg = sum(sum(c.values()) for _,_,c in corpus)/max(1,n)
    archive_intent = bool(qset & ARCHIVE_INTENT)
    current_intent = bool(qset & CURRENT_INTENT) and not archive_intent
    priors = hub_priors(root)
    procedure = bool(PROCEDURE_QUESTION.search(query))
    record_dates = sorted(m.group(2) for m in (DATED_RECORD.match(rel(root,p)) for p,_,_ in corpus) if m)
    hits = []
    for p,m,c in corpus:
        length = sum(c.values())
        score = 0.0
        for w in expanded:
            tf = c[w]
            if tf:
                idf = math.log(1 + (n-df[w]+0.5)/(df[w]+0.5))
                score += idf * tf*2.2/(tf+1.2*(0.25+0.75*length/max(1,avg))) * (1 if w in query_words else 0.6)
        if not score:
            continue
        if phrases:
            seq = tokens(p.read_text(encoding='utf-8'))
            found = collections.Counter(a+' '+b for a, b in zip(seq, seq[1:]) if a+' '+b in phrases)
            score += sum(tf/(tf+1) for tf in found.values())
        path = rel(root,p)
        # A question whose words appear in the page title or label is about that page.
        title_words = set(tokens(str(m.get('title',''))+' '+str(m.get('label',''))))
        overlap = len(qset & title_words)/max(1,len(qset))
        score *= 1 + 0.6*overlap
        prior, intent = priors.get(path,(1.0,set()))
        if prior < 1 and not (qall & intent):
            score *= prior
        if is_archived(path, m) and not archive_intent:
            score *= 0.35 if current_intent else 0.85
        if path.startswith('wiki/reviews/') and not (qset & REVIEW_INTENT):
            score *= 0.8
        if procedure and path in PROCEDURE_PAGES:
            score *= 1.25
        if m.get('category') == 'Skill' and not (procedure or qall & SKILL_INTENT):
            score *= 0.7
        dated = DATED_RECORD.match(path)
        if dated:
            score *= 0.9 ** sum(1 for d in record_dates if d > dated.group(2))
        hits.append({'path':path,'title':m.get('title'),'summary':m.get('summary'),'as_of':m.get('as_of'),
                     'status':m.get('status'),'score':round(score,4)})
    return sorted(hits,key=lambda h:(-h['score'],h['path']))[:limit]

# Passages of the next-best pages count less: the best page usually answers, but when it does not, the
# answer is most often on the second or third page. Measured on 176 held-out questions:
# answer fully inside the pack 58 % with the best page only, 79 % with four pages, same token budget.
PACK_PAGE_WEIGHTS = (1.0, 0.6, 0.4, 0.3)

def page_blocks(root, path):
    """Paragraphs, list blocks and headed sections of a page body (front matter and skill footer removed)."""
    text = (root/path).read_text(encoding='utf-8')
    if text.startswith('---\n') and '\n---' in text[4:]:
        text = text[text.index('\n---', 4)+4:]
    text = text.split('\n---\nRules:')[0]
    return [b.strip() for b in re.split(r'\n(?=#{1,4} )|\n\s*\n', text) if b.strip()]

def answer_pack(root, question, budget=1800, engine=None, passages=None, abstain=None):
    """One call that answers most questions: the passages that match the question from the best pages
    (the best page first, the next pages with lower weight), the matching facts with their confidence,
    and the remaining next pages. It replaces the usual chain search -> read page -> look up claims ->
    read the next page, so an agent needs fewer steps (each step re-sends the whole conversation).
    budget is in tokens (about 4 characters each) for the passages. engine, passages and abstain override the
    configured retrieval engine, passage selection and no-answer abstention (tools/retrieval.py)."""
    import retrieval
    hits, engine_used, fallback = retrieval.search(_namespace(), root, question, len(PACK_PAGE_WEIGHTS)+2, engine)
    if not hits:
        return {'question':question,'status':'insufficient_evidence','page':None,'excerpt':'','sources':[],'facts':[],'also':[],
                'note':'No page matched the words of the question. That is not proof that the vault lacks the answer: try '
                       'vault_source with content=true for the raw evidence.'}
    top = hits[0]
    wanted = {w for w in terms(question) if w not in STOP}
    for raw in [w for w in tokens(question) if w not in STOP]:
        wanted |= set(terms(' '.join(ALIASES.get(raw, []))))
    candidates, selection = retrieval.passage_candidates(_namespace(), root, question, hits, wanted, passages)
    chosen, used = [], 0
    for score, rank, i, block in candidates:
        if score <= 0 and chosen:
            break
        if used + len(block) > budget*4 and chosen:
            continue
        chosen.append((rank, i, block)); used += len(block)
        if used >= budget*4:
            break
    ranks = sorted({rank for rank, _, _ in chosen})
    parts = []
    for rank in ranks:
        text = '\n\n'.join(b if len(b) <= budget*4 else b[:budget*4]+' …' for r, _, b in sorted(chosen) if r == rank)
        parts.append(text if len(ranks) == 1 else '[{}]\n{}'.format(hits[rank]['path'], text))
    excerpt = '\n\n'.join(parts)
    pages = {h['path'] for h in hits[:len(PACK_PAGE_WEIGHTS)]}
    claims = read_json(root/'system/claims.json',{'claims':[]})['claims']
    def claim_score(c):
        words = set(terms(c.get('text','')))
        return (c.get('page') == top['path'])*2 + (c.get('page') in pages) + len(wanted & words) - 2*(c.get('lifecycle') in ('archived','superseded'))
    facts = [c for c in sorted(claims, key=lambda c: -claim_score(c)) if claim_score(c) >= 2][:4]
    used_paths = [hits[r]['path'] for r in ranks]
    incidents = evidence_store.open_incidents(root)
    status, check = retrieval.abstention(_namespace(), root, question, hits, abstain)
    return {'question':question, **({'status':status} if status else {}),
            'page':{k:top.get(k) for k in ('path','title','as_of','status','summary')},
            'excerpt':excerpt,
            'sources':used_paths,
            'facts':[{k:c.get(k) for k in ('id','text','confidence','as_of','page','lifecycle') if c.get(k) is not None} for c in facts],
            'also':[{k:h.get(k) for k in ('path','title','as_of','status')} for h in hits[1:] if h['path'] not in used_paths][:3],
            **({'integrity':'%d integrity incident(s) open: evidence may be damaged; see python3 tools/brain.py incident list' % len(incidents)} if incidents else {}),
            'retrieval':dict({'engine':engine_used,'passages':selection}, **({'fallback':fallback} if fallback else {}), **(check or {})),
            'answer_check':'off' if check is None else 'unavailable' if check.get('abstain') else 'heuristic: '+('no answer found' if status else 'passed'),
            'note':('The no-answer check (an optional heuristic that misses many unanswerable questions) found no answer in the best passages: treat the question as not answered by the vault unless the passages below show otherwise. ' if status else
                    'No-answer detection is off: these are the closest passages, not a confirmed answer; if they do not state the answer, say that the vault does not contain it. ' if check is None else '')
                   +'Passages, not whole pages: the best page first, then [path]-marked passages of the next pages. Read the page or its sources before stating anything the passages do not cover; archived pages are history, not plans.'}

def register(root, path, origin):
    candidate = root/path
    if candidate.is_symlink():
        raise ValueError('register requires a regular raw file, not a symbolic link')
    p = candidate.resolve()
    raw = (root/'raw').resolve()
    try:
        relpath = p.relative_to(root.resolve()).as_posix()
        p.relative_to(raw)
    except ValueError:
        raise ValueError('register requires an existing file under raw/')
    if not p.is_file():
        raise ValueError('register requires a regular raw file')
    if p.name in FINDER_METADATA:
        raise ValueError('Finder metadata is not evidence and is never registered')
    if TOOL_ATTRIBUTION.search(origin):
        raise ValueError('origin names an AI client tool; record the model name instead')
    with storage.vault_lock(root):
        registry = read_json(root/'system/source-registry.json', {'schema':2,'files':[]})
        if any(r['path']==relpath for r in registry['files']):
            raise ValueError('already registered; existing hashes cannot be rebaselined')
        record = {'id':'S-'+hashlib.sha256(relpath.encode()).hexdigest()[:12],
                  'original_path':relpath,'path':relpath,'bytes':p.stat().st_size,'sha256':digest(p),
                  'kind':'new-source','review':'inventoried','origin':origin,'compiled_in':[]}
        registry['files'].append(record)
        registry['files'].sort(key=lambda r:r['path'])
        save_json(root/'system/source-registry.json',registry)
        evidence_store.after_register(root, p, record)
    return record

INDEX_GROUPS = ['Start','Foundations','Projects','Domains','Implementation reference','Quality',
                'Workspace and method','Reviews (unapproved opinions)','History','Archive']

def build_text_index(root, records):
    """Full-text index of every decodable source, outside the visible graph.

    Indexing is mechanical and does not raise semantic review status."""
    db = root/'.brain/raw-text.sqlite'
    tmp = root/'.brain/raw-text.sqlite.tmp'
    db.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(str(tmp))
    con.execute('create table meta(rowid integer primary key, id text, path text, kind text, original_path text, text_from text, confidence real)')
    con.execute("create virtual table ft using fts5(names, body, content='', tokenize='unicode61 remove_diacritics 2')")
    count = 0
    for record in records:
        p = root/record['path']
        if p.name in FINDER_METADATA or p.suffix.lower() not in TEXT_SUFFIXES or not p.is_file():
            continue
        try:
            text = p.read_text(encoding='utf-8')
        except UnicodeError:
            text = p.read_bytes().decode('utf-8', errors='replace')
        except OSError:
            continue
        count += 1
        con.execute('insert into meta values (?,?,?,?,?,?,?)',(count,record['id'],record['path'],record['kind'],record['original_path'],'text',None))
        # FTS5's unicode61 tokenizer folds case and diacritics in C; queries use word prefixes.
        names = re.sub(r'[/_.-]+', ' ', record['original_path']+' '+record['path'])
        con.execute('insert into ft(rowid,names,body) values (?,?,?)',(count,names,text))
    # Images and PDFs: the text extracted by `brain.py media` (OCR or the PDF text layer), when present.
    extracted = media_cache(root)
    for record in records:
        found = extracted.get(record.get('sha256'))
        if Path(record['path']).suffix.lower() not in MEDIA_SUFFIXES or not found or not found.get('text'):
            continue
        count += 1
        con.execute('insert into meta values (?,?,?,?,?,?,?)',(count,record['id'],record['path'],record['kind'],record['original_path'],
                                                             found['kind'],found.get('mean_confidence')))
        names = re.sub(r'[/_.-]+', ' ', record['original_path']+' '+record['path'])
        con.execute('insert into ft(rowid,names,body) values (?,?,?)',(count,names,found['text']))
    con.commit()
    con.close()
    os.replace(str(tmp), str(db))
    legacy = root/'.brain/raw-text-index.json'
    if legacy.exists():
        legacy.unlink()
    return count

def build(root):
    """Refresh derived navigation under the vault write lock. Never change source hashes or claims."""
    with storage.vault_lock(root):
        return _build(root)

def _build(root):
    """Refresh derived navigation. Never change source hashes or claims."""
    catalog = pages(root)
    grouped = collections.defaultdict(list)
    for p,m,_ in catalog:
        group = 'Archive' if is_archived(rel(root,p),m) else m.get('category','Other')
        grouped[group].append((p,m))
    intro = 'Start with [[wiki/now|Now]]. Choose a topic; each page explains its state and cites evidence. '
    if (root/'system/VAULT_MAP.md').is_file():
        intro += 'Folders, sources and where new material goes are in the [[system/VAULT_MAP|vault map]]. '
    lines = ['---','title: "Knowledge index"','status: "active"','---','','# Knowledge index','',
             intro + 'This index is generated from wiki metadata.','']
    for group in INDEX_GROUPS+sorted(set(grouped)-set(INDEX_GROUPS)):
        if group not in grouped:
            continue
        lines += ['## '+group,'']
        if group == 'Archive':
            lines += ['Archived material: drafts, dormant prototypes and outdated work. It is history, not a current plan.','']
        for p,m in grouped[group]:
            lines += ['- [['+rel(root,p).removesuffix('.md')+'|'+m.get('title',p.stem)+']]: '+m.get('summary','No description')]
        lines += ['']
    storage.atomic_write_text(root/'wiki/index.md', '\n'.join(lines))   # never a half-written index
    registry = read_json(root/'system/source-registry.json',{'schema':2,'files':[]})
    compiled = collections.defaultdict(list)
    for p,m,_ in catalog:
        for source in m.get('sources',[]):
            compiled[source].append(rel(root,p))
    for record in registry['files']:
        record['compiled_in'] = compiled.get(record['path'],[])
        if record['compiled_in']:
            record['review'] = 'reviewed-selected-sections'
        elif record.get('review') == 'reviewed-selected-sections':
            record['review'] = 'inventoried'
    save_json(root/'system/source-registry.json',registry)
    nodes = [rel(root,p) for p,_,_ in catalog] + ['wiki/index.md']
    nodes += [rel(root,p) for p in (root/'system').rglob('*.md')]+[n for n in ['00_HOME.md','01_CONTINUE.md'] if (root/n).exists()]
    edges = []
    for node in nodes:
        p = root/node
        if p.is_file():
            for target in WIKI.findall(p.read_text(encoding='utf-8')):
                t = wiki_target(root,target)
                if t and t.is_file():
                    trel = rel(root,t)
                    if trel in nodes:
                        edges.append({'from':node,'to':trel})
    save_json(root/'.brain/graph.json',{'nodes':nodes,'edges':edges,'scope':'curated wiki/system/home, not all raw artifacts'})
    counts = collections.Counter(r['kind'] for r in registry['files'])
    hashes = collections.defaultdict(list)
    for r in registry['files']:
        hashes[r['sha256']].append(r['path'])
    duplicates = [v for v in hashes.values() if len(v)>1]
    save_json(root/'.brain/inventory-summary.json',{'raw_files':len(registry['files']),'wiki_pages':len(catalog),
              'archived_pages':len(grouped.get('Archive',[])),'kinds':dict(counts),'sources_cited':len(compiled),
              'duplicate_groups':len(duplicates),'compiled_pages_are_synthesis':True,'all_binary_visuals_reviewed':False})
    save_json(root/'.brain/duplicate-groups.json',duplicates)
    indexed = build_text_index(root, registry['files'])
    save_json(root/'.brain/build-manifest.json',{rel(root,p):digest(p) for p in active_files(root) if p.is_file()})
    import page_index
    page_index.get(_namespace(), root, save=True)
    return {'wiki_pages':len(catalog),'archived_pages':len(grouped.get('Archive',[])),'curated_nodes':len(nodes),
            'curated_edges':len(edges),'raw_files':len(registry['files']),'sources_cited':len(compiled),
            'text_sources_indexed':indexed}

def source_search(root,query,limit=5,content=False):
    words = [w for w in tokens(query) if w not in STOP]
    if not words:
        return []
    registry = read_json(root/'system/source-registry.json',{'files':[]})['files']
    if not content:
        return [{'id':r['id'],'path':r['path'],'review':r['review'],'compiled_in':r['compiled_in']}
                for r in registry if all(w in normalize(r['original_path']+' '+r['path']) for w in words)][:limit]
    db = root/'.brain/raw-text.sqlite'
    if not db.exists():
        raise ValueError('full-text index missing; run python3 tools/brain.py build')
    regmap = {r['id']:r for r in registry}
    # Every word must occur (as a word or word prefix) in the path or the text.
    match = ' AND '.join('"'+w+'"' + ('*' if len(w) > 3 else '') for w in words)
    sql = ('select meta.id, meta.path, meta.kind, bm25(ft, 3.0, 1.0), meta.text_from, meta.confidence from ft join meta on meta.rowid = ft.rowid '
           'where ft match ? order by bm25(ft, 3.0, 1.0) limit ?')
    con = sqlite3.connect('file:'+str(db)+'?mode=ro', uri=True)
    rows = con.execute(sql, (match, max(limit*8, 40))).fetchall()
    # Sources that contain the words as one phrase come first.
    phrase_ids = set()
    sequence = tokens(query)    # the phrase keeps short words, exactly as the index stores them
    if len(sequence) >= 3:
        try:
            phrase_rows = con.execute(sql, ('"' + ' '.join(sequence) + '"', max(limit*8, 40))).fetchall()
        except sqlite3.OperationalError:
            phrase_rows = []
        phrase_ids = {r[0] for r in phrase_rows}
        rows = phrase_rows + [r for r in rows if r[0] not in phrase_ids]
    con.close()
    result = []
    for sid, path, kind, bm, text_from, confidence in rows:
        score = -bm
        if sid in phrase_ids:
            score = score*3 + 1000
        if kind in ['evidence-source','decision-source','handoff-source','dossier-source']:
            score *= 1.5
        if kind in ['generated-v1-navigation','experiment-log','agent-history','tool-history']:
            score *= 0.5
        r = regmap.get(sid,{})
        # Return provenance and paths. The agent reads the exact source next;
        # do not promote search snippets into unqualified facts.
        hit = {'id':sid,'path':path,'kind':kind,'review':r.get('review','inventoried'),
               'score':round(score,3),'compiled_in':r.get('compiled_in',[]),'sha256':r.get('sha256')}
        if text_from and text_from != 'text':
            # Text read from an image or PDF: say how, so the agent knows how far to trust a quote.
            hit['text_from'] = text_from
            if confidence is not None:
                hit['ocr_confidence'] = round(confidence, 2)
        result.append(hit)
    # Byte-identical copies (the archive keeps historical duplicates) collapse into one hit.
    seen, unique = {}, []
    for hit in sorted(result,key=lambda x:(-x['score'],x['path'])):
        key = hit.pop('sha256') or hit['id']
        if key in seen:
            first = seen[key]
            first['copies'] = first.get('copies', 1) + 1
            if len(first.setdefault('same_content', [])) < 3:
                first['same_content'].append(hit['path'])
            continue
        seen[key] = hit
        unique.append(hit)
    return unique[:limit]

# Images and PDFs ------------------------------------------------------------------------------------------
# Their text is extracted on this Mac only (PDFKit for a PDF's text layer, Vision OCR for scans and images,
# with a confidence between 0 and 1 for every recognised line). Nothing is uploaded and raw files are only
# read; the results live in .brain/ and feed the full-text index, so `source --content` finds screenshots
# and PDFs by the words they show.
MEDIA_SUFFIXES = {'.png','.jpg','.jpeg','.webp','.gif','.heic','.tif','.tiff','.bmp','.pdf'}
MEDIA_HELPER_SOURCE = 'tools/media/extract_text.swift'
MEDIA_HELPER = '.brain/bin/extract_text'
MEDIA_CACHE = '.brain/media-text.jsonl'
MEDIA_CONTEXT = '.brain/media-context.json'
MEDIA_MENTION = re.compile(r'[^\s/\\\'"()\[\]|<>`*]+\.(?:png|jpe?g|webp|gif|heic|tiff?|bmp|pdf)\b', re.I)
MENTION_SUFFIXES = {'.md','.txt','.json','.jsonl','.ndjson','.log','.canvas'}

def media_records(root):
    registry = read_json(root/'system/source-registry.json',{'files':[]})['files']
    return [r for r in registry if Path(r['path']).suffix.lower() in MEDIA_SUFFIXES]

def media_cache(root):
    """Extraction results keyed by content hash: identical copies are read once."""
    out = {}
    path = root/MEDIA_CACHE
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                found = json.loads(line)
            except ValueError:
                continue
            if found.get('sha256'):
                out[found['sha256']] = found
    return out

def media_helper(root):
    """Compile the extraction helper into .brain/bin when it is missing or older than its source."""
    source, exe = root/MEDIA_HELPER_SOURCE, root/MEDIA_HELPER
    if exe.exists() and exe.stat().st_mtime >= source.stat().st_mtime:
        return exe
    if not shutil.which('swiftc'):
        raise ValueError('swiftc not found: install the Xcode command line tools to read images and PDFs')
    exe.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['swiftc','-O',str(source),'-o',str(exe)], check=True, capture_output=True, timeout=900)
    return exe

def media_context(root, records):
    """Where each image or PDF is mentioned (reports, notes, logs, pages): the context that says what it shows.
    A file name used in several folders is matched only to the copy in the same source folder as the mention."""
    by_name = collections.defaultdict(list)
    for r in records:
        for p in {r['path'], r.get('original_path') or r['path']}:
            by_name[Path(p).name.lower()].append(r['path'])
    mentions = collections.defaultdict(list)
    def bundle(path):
        return '/'.join(Path(path).parts[:2])
    for top in ('raw','wiki','system'):
        for dirpath, dirnames, filenames in os.walk(root/top):
            dirnames[:] = sorted(d for d in dirnames if d not in ('__pycache__','node_modules'))
            for name in sorted(filenames):
                p = Path(dirpath)/name
                if p.suffix.lower() not in MENTION_SUFFIXES:
                    continue
                try:
                    text = p.read_text(encoding='utf-8', errors='replace')
                except OSError:
                    continue
                source = rel(root, p)
                for match in MEDIA_MENTION.finditer(text):
                    owners = sorted(set(by_name.get(match.group(0).lower(), [])))
                    if len(owners) > 1:
                        owners = [o for o in owners if bundle(o) == bundle(source)] or []
                    if not owners:
                        continue
                    start = text.rfind('\n', 0, match.start()) + 1
                    end = text.find('\n', match.end())
                    line = text[start:end if end >= 0 else len(text)].strip()
                    if len(line) > 240:
                        centre = match.start() - start
                        line = '…' + line[max(0, centre-110):centre+130].strip() + '…'
                    for owner in owners:
                        if not any(m['file'] == source for m in mentions[owner]):
                            mentions[owner].append({'file':source,'line':line})
    readable = ('.md','.txt')
    out = {}
    for path, found in mentions.items():
        here = str(Path(path).parent)
        found.sort(key=lambda m: (not m['file'].endswith(readable), not m['file'].startswith(here), m['file']))
        out[path] = {'count':len(found), 'mentions':found[:5]}
    return out

def media_extract(root, workers=None, limit=None):
    """Read the text of every image and PDF in raw/ that is not cached yet, then record where each is mentioned.
    Run `build` afterwards so the full-text index includes the new text."""
    exe = media_helper(root)
    records = media_records(root)
    cache = media_cache(root)
    todo = {}
    for r in sorted(records, key=lambda r: r['path']):
        if r.get('sha256') and r['sha256'] not in cache and (root/r['path']).is_file():
            todo.setdefault(r['sha256'], r['path'])
    items = list(todo.items())[:limit] if limit else list(todo.items())
    started = datetime.datetime.now()
    if items:
        workers = max(1, min(workers or max(2, (os.cpu_count() or 4)//2), len(items)))
        by_path = {str((root/p).resolve()): sha for sha, p in items}
        with tempfile.TemporaryDirectory() as temp:
            procs = []
            for i in range(workers):
                shard = [str((root/p).resolve()) for _, p in items[i::workers]]
                if not shard:
                    continue
                inp, outp = Path(temp)/'in-{}.txt'.format(i), Path(temp)/'out-{}.jsonl'.format(i)
                inp.write_text('\n'.join(shard)+'\n', encoding='utf-8')
                with open(inp, encoding='utf-8') as fin, open(outp, 'w', encoding='utf-8') as fout:
                    procs.append((subprocess.Popen([str(exe)], stdin=fin, stdout=fout, stderr=subprocess.DEVNULL), outp))
            for proc, _ in procs:
                proc.wait()
            today = datetime.date.today().isoformat()
            for _, outp in procs:
                for line in outp.read_text(encoding='utf-8').splitlines():
                    try:
                        found = json.loads(line)
                    except ValueError:
                        continue
                    sha = by_path.get(found.pop('path', ''))
                    if sha:
                        found.update(sha256=sha, path=todo[sha], extracted_on=today)
                        cache[sha] = found
        tmp = root/(MEDIA_CACHE+'.tmp')
        tmp.write_text(''.join(json.dumps(cache[k], ensure_ascii=False)+'\n' for k in sorted(cache)), encoding='utf-8')
        os.replace(str(tmp), str(root/MEDIA_CACHE))
    context = media_context(root, records)
    save_json(root/MEDIA_CONTEXT, context)
    distinct = {r['sha256'] for r in records if r.get('sha256')}
    done = [cache[s] for s in distinct if s in cache]
    confident = [d['mean_confidence'] for d in done if d.get('mean_confidence') is not None]
    return {'media_files':len(records), 'distinct_contents':len(distinct), 'extracted_now':len(items),
            'extracted_total':len(done), 'with_text':sum(1 for d in done if d.get('text')),
            'by_kind':dict(collections.Counter(d.get('kind') for d in done)),
            'errors':sorted({d['error'] for d in done if d.get('error')}),
            'ocr_mean_confidence':round(sum(confident)/len(confident), 3) if confident else None,
            'ocr_lines':sum(d.get('lines', 0) for d in done if d.get('kind') != 'pdf-text'),
            'ocr_low_confidence_lines':sum(d.get('low_confidence_lines', 0) for d in done),
            'files_with_context':sum(1 for r in records if r['path'] in context),
            'seconds':round((datetime.datetime.now()-started).total_seconds(), 1),
            'next':'python3 tools/brain.py build'}

def media_info(root, query, budget=1500):
    """One image or PDF by path (current, original or former) or file name, or the best matches of a text search
    among images and PDFs: extracted text, how it was read, OCR confidence, copies and mentions."""
    records = media_records(root)
    q = query.strip().strip('"\'')
    exact = [r for r in records if q in {r['path'], r.get('original_path')} or q in (r.get('former_paths') or [])]
    if not exact:
        exact = [r for r in records if Path(r['path']).name.lower() == Path(q).name.lower()]
    if not exact:
        found = [h for h in source_search(root, query, 40, content=True) if Path(h['path']).suffix.lower() in MEDIA_SUFFIXES][:5]
        return {'query':query, 'matches':[{k:h.get(k) for k in ('path','text_from','ocr_confidence','copies','same_content') if h.get(k) is not None}
                                          for h in found],
                'note':'Call media-info with one of these paths for its text and mentions.' if found else 'No image or PDF matched.'}
    cache = media_cache(root)
    context = read_json(root/MEDIA_CONTEXT, {})
    out = []
    for r in exact[:3]:
        found = cache.get(r.get('sha256'), {})
        copies = sorted(x['path'] for x in records if x.get('sha256') == r.get('sha256') and x['path'] != r['path'])
        text = found.get('text', '')
        item = {'path':r['path'], 'id':r['id'], 'kind':r['kind'], 'text_from':found.get('kind', 'not extracted yet (run brain.py media)'),
                'pages':found.get('pages'), 'chars':found.get('chars'), 'text':text[:budget*4] + (' …' if len(text) > budget*4 else '')}
        if found.get('mean_confidence') is not None:
            item.update(ocr_mean_confidence=round(found['mean_confidence'], 3), ocr_lines=found.get('lines'),
                        ocr_low_confidence_lines=found.get('low_confidence_lines'))
        if copies:
            item['same_content'] = copies[:5]
        item['mentioned_in'] = context.get(r['path'], {'count':0, 'mentions':[]})
        out.append(item)
    return {'query':query, 'files':out,
            'note':'Text read on this Mac. A PDF text layer is exact; OCR text can misread single characters, so check '
                   'low-confidence lines against the image, and attach the image only when layout or visuals matter.'}

def changed_since(p, stamp):
    try:
        return stamp is None or p.stat().st_mtime > stamp
    except OSError:
        return True

def first_match_line(path, pattern, chunk=1 << 20):
    """Line number of the first match, reading the file in chunks (constant memory for large evidence files).
    The pattern matches single characters, so no match can straddle two chunks."""
    import codecs
    decoder, line = codecs.getincrementaldecoder('utf-8')(errors='ignore'), 1
    try:
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(chunk), b''):
                text = decoder.decode(block)
                m = pattern.search(text)
                if m:
                    return line + text.count('\n', 0, m.start())
                line += text.count('\n')
    except OSError:
        return None
    return None

def language_guard(root, since=None):
    """Report literal Polish-specific letters in vault text files (maintained and raw).

    Returns (errors, warnings). inbox/ may hold non-English input until it is ingested,
    so findings there are warnings; everywhere else they are errors. With `since`, only
    files modified after that time are read (file names are always checked)."""
    allow = {e['path'] for e in read_json(root/'system/language-allowlist.json',{'files':[]}).get('files',[])}
    skip = {'.git','.venv','.brain','.maintenance','node_modules','__pycache__'}
    found = []
    for dirpath,dirnames,filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        rel_dir = Path(dirpath).relative_to(root)
        for name in filenames:
            p = Path(dirpath)/name
            relpath = (rel_dir/name).as_posix()
            if POLISH_LETTERS.search(relpath):
                found.append('Polish letters in file name: '+relpath)
            if p.suffix.lower() not in GUARDED_SUFFIXES or p.is_symlink() or relpath in allow:
                continue
            if since is not None and not changed_since(p, since):
                continue
            line = first_match_line(p, POLISH_LETTERS)
            if line:
                found.append('Polish letters in '+relpath+':'+str(line))
    errors = [f for f in found if not f.split(': ',1)[-1].startswith('inbox/') and ' in inbox/' not in f]
    warnings = [f+' (inbox awaiting ingest)' for f in found if f not in errors]
    return errors,warnings

def resolve(root, query):
    """Find registry records by current path, original path, former vault path or ID.

    Renamed or archived files keep their earlier vault paths in `former_paths`, so old
    references in historical documents and logs still resolve."""
    q = query.strip()
    for prefix in config(root)['former_roots']:
        q = q.removeprefix(prefix)
    recs = read_json(root/'system/source-registry.json',{'files':[]})['files']
    return [{'id':r['id'],'original_path':r['original_path'],'path':r['path'],'former_paths':r.get('former_paths',[])}
            for r in recs if q in (r['original_path'],r['path'],r['id']) or q in r.get('former_paths',[])]

def attribution_errors(label, value):
    texts = value if isinstance(value, list) else [value]
    return [label+': names an AI client tool; record the model name only'
            for t in texts if isinstance(t, str) and TOOL_ATTRIBUTION.search(t)]

def hook_errors(root):
    errors = []
    for p in sorted((root/'.github/hooks').glob('*.json')):
        try:
            data = read_json(p)
        except json.JSONDecodeError:
            errors.append(rel(root,p)+': invalid JSON'); continue
        if data.get('version') != 1 or not isinstance(data.get('hooks'),dict):
            errors.append(rel(root,p)+': hook file needs "version": 1 and a "hooks" map'); continue
        for event, entries in data['hooks'].items():
            for entry in entries:
                command = entry.get('command') or entry.get('bash') or ''
                for script in re.findall(r'(tools/[\w./-]+\.py)', command):
                    if not (root/script).is_file(): errors.append(rel(root,p)+': '+event+' script missing '+script)
    return errors

def raw_symlinks(root):
    found = []
    for dirpath,dirnames,filenames in os.walk(root/'raw'):
        for name in dirnames+filenames:
            p = Path(dirpath)/name
            if p.is_symlink(): found.append(rel(root,p))
    return found

def _check_sources(root, records, rawpaths, fast, cache):
    """Registry records against raw/: content hashes (fast mode trusts an unchanged size, modification and status-change
    time), attribution, former paths, revisions, renditions, unregistered files and links. Returns (errors, verified)."""
    errors, verified = [], {}
    for r in records:
        p = root/r['path']
        if not r['path'].startswith('raw/'):
            errors.append('source outside raw: '+r['path']); continue
        if p.name in FINDER_METADATA:
            errors.append('Finder metadata registered as a source: '+r['path']); continue
        if p.is_symlink(): errors.append('raw changed: '+r['path']+' (symbolic link)')
        elif not p.is_file(): errors.append('missing raw source: '+r['path'])
        else:
            st = p.stat()
            stamp = [st.st_size, st.st_mtime_ns, st.st_ctime_ns, r['sha256']]
            if fast and cache.get(r['path']) == stamp:
                verified[r['path']] = stamp
            elif digest(p)!=r['sha256'] or st.st_size!=r['bytes']:
                errors.append('raw changed: '+r['path'])
            else:
                verified[r['path']] = stamp
        attrs = [('origin',r.get('origin')),('translator',(r.get('rendition') or {}).get('translator'))]
        for field, value in attrs:
            if value: errors += attribution_errors(r['path']+' '+field, value)
        former = r.get('former_paths',[])
        if not isinstance(former,list) or any(not isinstance(f,str) or not f.startswith('raw/') or f==r['path'] for f in former):
            errors.append('invalid former_paths: '+r['path'])
        for rev in r.get('revisions',[]):
            if (not isinstance(rev,dict) or not DATE.match(str(rev.get('on',''))) or not rev.get('reason')
                    or not re.fullmatch(r'[0-9a-f]{64}',str(rev.get('previous_sha256','')))
                    or not isinstance(rev.get('previous_bytes'),int)):
                errors.append('incomplete revision record: '+r['path'])
        rendition = r.get('rendition')
        if rendition is not None:
            # An English rendition replaces non-English source bytes in the vault; the
            # original's hash and size stay on record and the original is kept outside it.
            if rendition.get('type') not in RENDITION_TYPES: errors.append('invalid rendition type: '+r['path'])
            if not DATE.match(str(rendition.get('translated_on',''))): errors.append('invalid rendition date: '+r['path'])
            if not rendition.get('translator'): errors.append('rendition without translator: '+r['path'])
            if (not re.fullmatch(r'[0-9a-f]{64}',str(rendition.get('original_sha256','')))
                    or not isinstance(rendition.get('original_bytes'),int)):
                errors.append('incomplete rendition provenance: '+r['path'])
            elif rendition['original_sha256']==r['sha256']:
                errors.append('rendition identical to its original: '+r['path'])
    discovered = {rel(root,p) for p in (root/'raw').rglob('*') if p.is_file() and not p.is_symlink() and p.name not in FINDER_METADATA}
    for p in sorted(discovered-rawpaths): errors.append('unregistered raw source: '+p)
    for p in raw_symlinks(root): errors.append('symbolic link inside raw/ (evidence must be regular files): '+p)
    formerpaths = [f for r in records for f in r.get('former_paths',[])]
    for f in sorted(set(formerpaths)&rawpaths): errors.append('former path reused by a current source: '+f)
    return errors, verified

def _check_pages(root, catalog, rawpaths):
    """Page metadata, archive placement, attribution, index entries and page sources; then the wikilinks and local
    Markdown links of the curated files."""
    errors = []
    index = (root/'wiki/index.md').read_text(encoding='utf-8') if (root/'wiki/index.md').exists() else ''
    for p,m,_ in catalog:
        relpath = rel(root,p)
        for field in ['title','summary','category','status','as_of','compiled_on','owner','keywords','sources']:
            if field not in m: errors.append(relpath+': missing metadata '+field)
        for field in ['as_of','compiled_on']:
            if not DATE.match(str(m.get(field,''))): errors.append(relpath+': invalid date '+field)
        if 'keywords' in m and not isinstance(m['keywords'],list): errors.append(relpath+': keywords must be a list')
        if relpath.startswith('wiki/archive/') and (m.get('status')!='archived' or m.get('category')!='Archive'):
            errors.append(relpath+': archived pages need status "archived" and category "Archive"')
        if m.get('status')=='archived' and not relpath.startswith('wiki/archive/'):
            errors.append(relpath+': archived page outside wiki/archive/')
        for field in ATTRIBUTION_FIELDS:
            if field in m: errors += attribution_errors(relpath+' '+field, m[field])
        if '[['+relpath.removesuffix('.md')+'|' not in index: errors.append('page missing from index: '+relpath)
        for src in m.get('sources',[]):
            if src not in rawpaths: errors.append(relpath+': source not registered '+src)
    for p in curated_files(root):
        text = p.read_text(encoding='utf-8')
        relpath = rel(root,p)
        for line in text.splitlines():
            if line.lstrip().startswith('|'):
                for target in WIKI.findall(line):
                    if re.search(r'(?<!\\)\|',target):
                        errors.append(relpath+': unescaped wikilink alias inside table')
        for target in WIKI.findall(text):
            t = wiki_target(root,target)
            if t and not t.is_file(): errors.append(relpath+': broken wikilink '+target)
        for target in MD.findall(text):
            target = target.strip('<> ')
            if re.match(r'^[a-z][a-z0-9+.-]*:',target) or target.startswith('#'):
                continue
            value = unquote(target.split('#',1)[0])
            local = Path(value) if value.startswith('/') else p.parent/value
            if not local.exists(): errors.append(relpath+': broken local reference '+target)
    return errors

def _check_claims(root, claims, rawpaths):
    """Claim fields, attribution, sources, and the symmetry of supersession and conflict relations."""
    errors = []
    claimmap = {c['id']:c for c in claims}
    if len(claimmap)!=len(claims): errors.append('duplicate claim IDs')
    for c in claims:
        cid = c['id']
        for field in ['text','page','confidence','scope','as_of','sources']:
            if not c.get(field): errors.append(cid+': missing '+field)
        if c.get('confidence') not in VALID_CONFIDENCE: errors.append(cid+': invalid confidence')
        if not DATE.match(str(c.get('as_of',''))): errors.append(cid+': invalid date')
        if not (root/c.get('page','__missing__')).is_file(): errors.append(cid+': missing page')
        for field in ATTRIBUTION_FIELDS:
            if field in c: errors += attribution_errors(cid+' '+field, c[field])
        for src in c.get('sources',[]):
            if src not in rawpaths: errors.append(cid+': source not registered '+src)
        for other in c.get('supersedes',[]):
            if other not in claimmap or claimmap[other].get('superseded_by')!=cid or claimmap[other].get('lifecycle')!='superseded':
                errors.append(cid+': asymmetric supersession '+other)
        if c.get('lifecycle')=='superseded':
            newer = claimmap.get(c.get('superseded_by'))
            if not newer or cid not in newer.get('supersedes',[]): errors.append(cid+': missing replacement relation')
        for other in c.get('conflicts',[]):
            if other not in claimmap or cid not in claimmap[other].get('conflicts',[]): errors.append(cid+': asymmetric conflict '+other)
    return errors

def _check_profiles(root):
    """Copilot agent profiles, skills, hooks and the Obsidian canvas. Returns (errors, agents, skills)."""
    errors = []
    agents = []
    for p in sorted((root/'.github/agents').glob('*.agent.md')):
        agents.append((p,metadata(p.read_text(encoding='utf-8'))))
    agentnames = {m.get('name') for _,m in agents}
    for p,m in agents:
        name = m.get('name',p.stem)
        # The model is chosen per task in the VS Code model picker.
        if 'model' in m: errors.append(name+': remove the model field; the model is chosen in VS Code')
        if not m.get('description') or not isinstance(m.get('tools'),list) or not isinstance(m.get('agents'),list):
            errors.append(name+': invalid Copilot metadata')
        for other in m.get('agents',[]):
            if other not in agentnames: errors.append(name+': unknown delegate '+other)
        if m.get('agents') and 'agent' not in m.get('tools',[]): errors.append(name+': delegates require agent tool')
        if name not in ['XR-ORCHESTRATOR','XR-KNOWLEDGE'] and any(t in READONLY_FORBIDDEN_TOOLS for t in m.get('tools',[])):
            errors.append(name+': read-only profile has write/execute tool')
    skillcount = 0
    for p in sorted((root/'.agents/skills').glob('*/SKILL.md')):
        skillcount += 1
        m = metadata(p.read_text(encoding='utf-8'))
        if m.get('name')!=p.parent.name or not re.fullmatch('[a-z0-9-]{1,64}',str(m.get('name',''))): errors.append(str(p)+': invalid skill name')
        if not m.get('description') or len(m['description'])>1024: errors.append(str(p)+': invalid skill description')
        if 'argument-hint' in m and not isinstance(m['argument-hint'],str): errors.append(str(p)+': argument-hint must be text')
    errors += hook_errors(root)
    canvas = root/'Vault.canvas'
    if canvas.exists():
        data = read_json(canvas)
        nodeids = {n['id'] for n in data.get('nodes',[])}
        for n in data.get('nodes',[]):
            if n.get('type')=='file' and not (root/n.get('file','__missing__')).is_file(): errors.append('canvas missing file: '+str(n.get('file')))
            if n.get('type')=='text':
                for target in WIKI.findall(n.get('text','')):
                    t = wiki_target(root,target)
                    if t and not t.is_file(): errors.append('canvas broken link: '+target)
        for e in data.get('edges',[]):
            if e.get('fromNode') not in nodeids or e.get('toNode') not in nodeids: errors.append('canvas dangling edge')
    return errors, len(agents), skillcount

def _maintenance_warnings(root, records, fast):
    """Unresolved wikilinks inside raw sources (full mode only), unclear file names, an evaluation older than the last
    build, the retrieval configuration and the graph palette."""
    warnings = []
    if not fast:
        _, resolve_link = link_index(root)
        unresolved = 0
        for r in records:
            if r['path'].endswith('.md') and (root/r['path']).is_file():
                try:
                    text = (root/r['path']).read_text(encoding='utf-8')
                except UnicodeError:
                    continue
                for target in WIKI.findall(text):
                    path = target.replace('\\|','|').split('|',1)[0].split('#',1)[0]
                    if path.strip() and not resolve_link(path, r['path']):
                        unresolved += 1
        if unresolved:
            warnings.append('unresolved wikilinks inside raw sources: '+str(unresolved)+' (historical links)')
    # Naming: a node label must say what the file is.
    names = collections.Counter(p.stem.lower() for p in curated_files(root) if p.name != 'SKILL.md')
    for p in curated_files(root):
        stem = p.stem.lower()
        if p.name == 'SKILL.md' or rel(root,p) in ('wiki/index.md','README.md'):
            continue
        if stem in GENERIC_NAMES or names[stem] > 1:
            warnings.append('unclear or duplicate file name: '+rel(root,p))
    evaluation = root/'.brain/retrieval-evaluation.json'
    manifest = root/'.brain/build-manifest.json'
    if not evaluation.exists() or (manifest.exists() and evaluation.stat().st_mtime < manifest.stat().st_mtime):
        warnings.append('retrieval evaluation older than the last build; run python3 tools/brain.py evaluate')
    elif not (read_json(evaluation,{}) or {}).get('pass') == (read_json(evaluation,{}) or {}).get('total'):
        warnings.append('retrieval evaluation has failing cases')
    import retrieval
    warnings += ['retrieval configuration: '+p for p in retrieval.config_problems(config(root).get('retrieval'))]
    return warnings

def check(root, write_report=True, fast=False):
    """Validate the vault. `fast` re-hashes only raw files whose size or modification time
    changed since the last full check and reads only recently changed text files."""
    errors = []
    warnings = []
    try:
        registry = read_json(root/'system/source-registry.json',{'files':[]})
    except ValueError as exc:
        return {'ok':False,'mode':'fast' if fast else 'full','errors':['registry unreadable: system/source-registry.json (%s); '
                'restore it from git or the evidence store checkpoint' % exc],'warnings':[],'open_incidents':evidence_store.open_incidents(root)}
    records = registry.get('files',[])
    rawpaths = {r['path'] for r in records}
    ids = [r['id'] for r in records]
    if len(ids)!=len(set(ids)) or len(records)!=len(rawpaths): errors.append('duplicate source ID/path')
    cache_path = root/'.brain/raw-verified.json'
    cache = read_json(cache_path,{}) if fast else {}
    source_errors, verified = _check_sources(root, records, rawpaths, fast, cache)
    catalog = pages(root)
    claims = read_json(root/'system/claims.json',{'claims':[]})['claims']
    errors += source_errors + _check_pages(root, catalog, rawpaths) + _check_claims(root, claims, rawpaths)
    import anchors
    anchor_errors, anchor_warnings, anchor_summary = anchors.check_findings(root, claims)
    import facts
    fact_errors, fact_warnings, fact_summary = facts.check_findings(root, claims, facts.knowledge_time.history(root, 'builds'))
    errors += anchor_errors + fact_errors; warnings += anchor_warnings + fact_warnings
    import freshness
    stale = [] if fast else freshness.findings(root, sys.modules[__name__])
    warnings += ['freshness %s %s: %s' % (s['rule'], s['page'], s['reason']) for s in stale if s['severity'] == 'warning']
    profile_errors, agent_count, skillcount = _check_profiles(root)
    errors += profile_errors
    last_full = (read_json(cache_path,{}) or {}).get('__checked_at__') if fast else None
    language_errors,language_warnings = language_guard(root, since=last_full)
    errors += language_errors
    warnings += language_warnings
    built = read_json(root/'.brain/build-manifest.json',{})
    for p in active_files(root):
        relpath = rel(root,p)
        if p.is_file() and built.get(relpath)!=digest(p): errors.append('derived indexes stale; build required: '+relpath)
    warnings += _maintenance_warnings(root, records, fast)
    store_errors, store_warnings = evidence_store.check_findings(root)
    errors += store_errors
    warnings += store_warnings
    open_incidents = evidence_store.record_incidents(root, errors) if write_report else evidence_store.open_incidents(root)
    result = {'ok':not errors,'mode':'fast' if fast else 'full','errors':errors,'warnings':warnings,'raw_files':len(records),
              'wiki_pages':len(catalog),'archived_pages':sum(1 for p,m,_ in catalog if is_archived(rel(root,p),m)),
              'claims':len(claims),'claim_anchors':anchor_summary,'claim_facts':fact_summary,'freshness':None if fast else len(stale),'agents':agent_count,'skills':skillcount,'open_incidents':open_incidents,
              'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'limitations':['Static checks cannot establish semantic truth or device acceptance.',
                             'The language guard detects Polish-specific letters only; Polish typed without diacritics needs the language review in system/WORKFLOWS.md.']
                            + (['Fast mode trusted unchanged size, modification and status-change time since the last full check.'] if fast else [])}
    if write_report:
        save_json(root/('.brain/validation-fast.json' if fast else '.brain/validation.json'),result)
        if not fast and not errors:
            verified['__checked_at__'] = datetime.datetime.now().timestamp()
            save_json(cache_path, verified)
    return result

def evaluate(root):
    cases = read_json(root/'system/retrieval-cases.json',[])
    results = []
    for case in cases:
        hits = search(root,case['question'],5)
        paths = [h['path'] for h in hits]
        results.append({'question':case['question'],'expected':case['expected'],'hits':paths,
                        'ok':any(p in paths for p in case['expected']),
                        'rank1':bool(paths) and paths[0] in case['expected']})
    report = {'pass':sum(r['ok'] for r in results),'rank1':sum(r['rank1'] for r in results),'total':len(results),
              'cases':results,'meaning':'Lexical routing evaluation: pass = an expected page in the top 5; '
              'rank1 = an expected page first. It measures the helper, not model reasoning.'}
    save_json(root/'.brain/retrieval-evaluation.json',report)
    return report

# ---------------------------------------------------------------------------------------
# Safe moves: archive or rename files and folders without losing provenance or links.

SCAN_GLOBS = ['wiki/**/*.md','system/**/*.md','system/*.json','.github/**/*','.agents/**/*','integrations/**/*',
              'tools/**/*.py','reference/**/*.md','.obsidian/bookmarks.json','.obsidian/graph.json',
              '.obsidian/app.json','.obsidian/plugins/*/data.json']
SCAN_ROOT_FILES = ['00_HOME.md','01_CONTINUE.md','AGENTS.md','README.md','Vault.canvas']
SCAN_EXCLUDE = {'system/source-registry.json'}   # updated structurally, never by text replacement
SCAN_SKIP_NAME = re.compile(r'^test_.*\.(py|cjs)$')    # test fixtures use example paths on purpose
BEFORE = r'(?:^|(?<=[\s\[\(\"\'`/=:,|>]))'
AFTER = r'(?=$|[\s\]\|#\)\"\'`,;:<>/]|\\\||\.md\b|\.(?:\s|$))'

def _plain_pattern(keys):
    keys = sorted(keys, key=len, reverse=True)
    return re.compile(BEFORE+'('+'|'.join(re.escape(k) for k in keys)+')'+AFTER, re.M) if keys else None

def _rewrite_md_links(text, old_rel, new_rel, mapped):
    """Rewrite Markdown link targets, relative ones included, for a file that may move."""
    import posixpath
    from urllib.parse import quote
    old_dir, new_dir = posixpath.dirname(old_rel), posixpath.dirname(new_rel)
    def fix(m):
        full, target = m.group(0), m.group(1)
        t = target.strip()
        if not t or re.match(r'^[a-z][a-z0-9+.-]*:', t) or t.startswith(('#','<','/')):
            return full
        path_part, sep, anchor = t.partition('#')
        decoded = unquote(path_part)
        absolute = posixpath.normpath(posixpath.join(old_dir, decoded))
        if absolute.startswith('..'):
            return full
        moved = mapped(absolute)
        if moved is None and old_dir == new_dir:
            return full
        new_target = posixpath.relpath(moved or absolute, new_dir or '.')
        if '%' in path_part:
            new_target = quote(new_target)
        rebuilt = new_target + (sep+anchor if sep else '')
        return full[:m.start(1)-m.start(0)] + rebuilt + full[m.end(1)-m.start(0):]
    return MD.sub(fix, text)

LINK_SKIP = {'.git','.brain','.venv','.maintenance','node_modules','__pycache__'}

def link_index(root):
    """Obsidian-like resolution of [[links]]: exact vault path, relative path, or a unique path suffix
    (a bare file name is the shortest suffix). Keys are lower case; Markdown files are keyed without .md."""
    files, index = set(), collections.defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in LINK_SKIP]
        for name in filenames:
            if name in FINDER_METADATA:
                continue
            path = rel(root, Path(dirpath)/name)
            files.add(path)
            key = (path[:-3] if path.endswith('.md') else path).lower().split('/')
            for i in range(len(key)):
                index['/'.join(key[i:])].append(path)
    def resolve_link(target, from_rel):
        import posixpath
        t = unquote(target.strip())
        if not t:
            return None
        if t.startswith(('./','../')):
            t = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), t))
        for candidate in (t, t+'.md'):
            if candidate in files:
                return candidate
        hits = index.get((t[:-3] if t.lower().endswith('.md') else t).lower(), [])
        return hits[0] if len(hits) == 1 else None
    return files, resolve_link

def _rewrite_wikilinks(text, resolve_link, final_of, from_rel):
    def fix(m):
        inner = m.group(1)
        alias_sep = '\\|' if '\\|' in inner else '|'
        target, s1, alias = inner.partition(alias_sep)
        path, s2, anchor = target.partition('#')
        hit = resolve_link(path, from_rel)
        new = final_of.get(hit) if hit else None
        if not new or new == hit:
            return m.group(0)
        if new.endswith('.md') and not path.strip().lower().endswith('.md'):
            new = new[:-3]
        start = m.start(1)-m.start(0)
        return m.group(0)[:start] + new + s2 + anchor + s1 + alias + m.group(0)[m.end(1)-m.start(0):]
    return WIKI.sub(fix, text)

def move_many(root, pairs, reason, dry_run=False, today=None, archive_root=None):
    """Safe moves under the vault write lock; flags, evidence store and journal are handled around the move."""
    if dry_run:
        return _move_many(root, pairs, reason, True, today, archive_root)
    with storage.vault_lock(root), evidence_store.guarded_change(root):
        return _move_many(root, pairs, reason, False, today, archive_root)

def _move_many(root, pairs, reason, dry_run=False, today=None, archive_root=None):
    """Move files or folders and keep every reference valid.

    - Raw sources keep their registry ID; the new path is recorded and the old one added to
      `former_paths`, so `resolve` finds them by any earlier path.
    - Curated files (wiki, system, root pages, agents, skills, integrations, tools, reference,
      Obsidian bookmarks, graph and plugin settings) are rewritten to the new paths.
    - Inside raw sources only link targets are rewritten. Each changed raw file gets a
      `revisions` entry and its previous bytes are preserved in the outside archive."""
    today = today or datetime.date.today().isoformat()
    archive_root = Path(archive_root or config(root)['outside_archive'])
    registry = read_json(root/'system/source-registry.json',{'schema':2,'files':[]})
    file_map, dir_map, ordered = {}, {}, []
    for old, new in pairs:
        old, new = old.strip('/'), new.strip('/')
        src, dst = root/old, root/new
        if not src.exists():
            raise ValueError('nothing to move: '+old)
        if dst.exists():
            raise ValueError('target exists: '+new)
        for path in (old, new):
            if path.split('/')[0] in ('.git','.brain','.venv') or '..' in path.split('/'):
                raise ValueError('refusing to move into or out of '+path)
        if old.startswith('raw/') != new.startswith('raw/'):
            raise ValueError('raw sources stay under raw/: '+old+' -> '+new)
        (dir_map if src.is_dir() else file_map)[old] = new
        ordered.append((old, new, src.is_dir()))
    for a in dir_map:
        for b in dir_map:
            if a != b and b.startswith(a+'/'):
                raise ValueError('nested folder moves in one plan: '+b+' inside '+a)
    dirs = sorted(dir_map, key=len, reverse=True)
    def apply_dirs(path):
        for old_dir in dirs:
            if path == old_dir:
                return dir_map[old_dir]
            if path.startswith(old_dir+'/'):
                return dir_map[old_dir]+path[len(old_dir):]
        return path
    def final(path):
        # File renames are given with paths before any folder move; folder moves apply afterwards.
        return apply_dirs(file_map.get(path, path))
    def mapped(path):
        moved = final(path)
        return moved if moved != path else None
    affected = set(file_map)
    for d in dir_map:
        affected.update(rel(root,p) for p in (root/d).rglob('*') if p.is_file())
    finals = collections.Counter(final(a) for a in affected)
    clashes = [f for f,n in finals.items() if n > 1 or ((root/f).exists() and f not in affected)]
    if clashes:
        raise ValueError('moves would overwrite or merge files: '+', '.join(sorted(clashes)[:10]))
    moved_new = {a:final(a) for a in affected}
    by_path = {r['path']:r for r in registry['files']}
    file_moves = [(a, moved_new[a]) for a in sorted(affected) if moved_new[a] != a]
    _, resolve_link = link_index(root)
    plain_keys = dict(dir_map)
    plain_keys.update({k+'/':v+'/' for k,v in dir_map.items()})
    for old_file in file_map:
        plain_keys[old_file] = final(old_file)
        if old_file.endswith('.md') and final(old_file).endswith('.md'):
            plain_keys[old_file[:-3]] = final(old_file)[:-3]
    plain = _plain_pattern(plain_keys)
    report = {'moves':len(pairs),'files':len(file_moves),
              'registry_updates':sum(1 for o,_ in file_moves if o in by_path),
              'curated_files_rewritten':[],'raw_files_revised':[],'dry_run':dry_run}
    curated = set()
    for g in SCAN_GLOBS:
        curated.update(p for p in root.glob(g) if p.is_file() and not p.is_symlink())
    curated.update(root/n for n in SCAN_ROOT_FILES if (root/n).is_file())
    rewrites = {}
    for p in sorted(curated):
        relpath = rel(root,p)
        if relpath in SCAN_EXCLUDE or SCAN_SKIP_NAME.match(p.name):
            continue
        try:
            text = p.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            continue
        new_rel = moved_new.get(relpath, relpath)
        new_text = text
        if p.suffix == '.md':
            new_text = _rewrite_md_links(new_text, relpath, new_rel, mapped)
        new_text = _rewrite_wikilinks(new_text, resolve_link, moved_new, relpath)
        if plain:
            new_text = plain.sub(lambda m: plain_keys[m.group(1)], new_text)
        if new_text != text:
            rewrites[new_rel] = new_text
    report['curated_files_rewritten'] = sorted(rewrites)
    raw_rewrites = {}
    for r in registry['files']:
        p = root/r['path']
        if p.suffix.lower() not in ('.md','.canvas') or not p.is_file():
            continue
        try:
            text = p.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            continue
        new_rel = moved_new.get(r['path'], r['path'])
        new_text = _rewrite_md_links(text, r['path'], new_rel, mapped) if p.suffix.lower()=='.md' else text
        new_text = _rewrite_wikilinks(new_text, resolve_link, moved_new, r['path'])
        if new_text != text:
            raw_rewrites[new_rel] = (r, new_text)
    report['raw_files_revised'] = sorted(raw_rewrites)
    if dry_run:
        return report
    for old, new, is_dir in sorted(ordered, key=lambda item: item[2]):
        (root/new).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root/old), str(root/new))
        parent = (root/old).parent
        while parent != root and parent.exists() and all(c.name in FINDER_METADATA for c in parent.iterdir()):
            for c in parent.iterdir():
                c.unlink()
            parent.rmdir()
            parent = parent.parent
    for old_file, new_file in file_moves:
        r = by_path.get(old_file)
        if r:
            r['path'] = new_file
            former = [f for f in r.get('former_paths',[]) if f != new_file]
            if old_file not in former:
                former.append(old_file)
            r['former_paths'] = former
    for relpath, text in rewrites.items():
        (root/relpath).write_text(text, encoding='utf-8')
    manifest_path = archive_root/'manifest.json'
    manifest = read_json(manifest_path, {'schema':1,'vault':str(root),'created_on':today,'entries':[]})
    stamp = datetime.datetime.now().astimezone().isoformat()
    for relpath, (r, text) in raw_rewrites.items():
        p = root/relpath
        previous = p.read_bytes()
        sha = hashlib.sha256(previous).hexdigest()
        keep = archive_root/('pre-reorganisation-'+today)/relpath
        if keep.exists() and digest(keep) != sha:
            keep = keep.with_name(keep.name+'.'+sha[:12])
        keep.parent.mkdir(parents=True, exist_ok=True)
        if not keep.exists():
            keep.write_bytes(previous)
        manifest['entries'].append({'vault_path':relpath,'archive_path':rel(archive_root,keep),'sha256':sha,
                                    'bytes':len(previous),'action':'preserved-before-edit',
                                    'reason':'link targets updated: '+reason,'recorded_at':stamp})
        p.write_text(text, encoding='utf-8')
        r.setdefault('revisions',[]).append({'on':today,'reason':'link targets updated: '+reason,
                                             'previous_sha256':sha,'previous_bytes':len(previous),
                                             'previous_preserved_in':rel(archive_root,keep)})
        r['sha256'] = digest(p)
        r['bytes'] = p.stat().st_size
    if raw_rewrites:
        save_json(manifest_path, manifest)
    registry['files'].sort(key=lambda r:r['path'])
    save_json(root/'system/source-registry.json', registry)
    return report

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command',required=True)
    sub.add_parser('build'); sub.add_parser('evaluate')
    chk = sub.add_parser('check'); chk.add_argument('--fast',action='store_true',help='trust unchanged raw files since the last full check')
    pk = sub.add_parser('pack'); pk.add_argument('query'); pk.add_argument('--budget',type=int,default=1800)
    for cmd in ['search','source','resolve']:
        q = sub.add_parser(cmd); q.add_argument('query'); q.add_argument('--limit',type=int,default=5)
        if cmd=='source': q.add_argument('--content',action='store_true',help='Search the full decodable text of raw sources.')
    reg = sub.add_parser('register'); reg.add_argument('path'); reg.add_argument('--origin',required=True)
    mv = sub.add_parser('move'); mv.add_argument('old',nargs='?'); mv.add_argument('new',nargs='?')
    mv.add_argument('--plan',help='JSON list of [old, new] pairs'); mv.add_argument('--reason',required=True)
    mv.add_argument('--dry-run',action='store_true')
    md = sub.add_parser('media'); md.add_argument('--limit',type=int); md.add_argument('--workers',type=int)
    mi = sub.add_parser('media-info'); mi.add_argument('query'); mi.add_argument('--budget',type=int,default=1500)
    bk = sub.add_parser('backup', help='copy every registered source into the evidence store (or verify it)')
    bk.add_argument('--verify',action='store_true'); bk.add_argument('--deep',action='store_true',help='re-hash every stored object')
    rs = sub.add_parser('restore', help='restore one source from the evidence store by ID, path, former path or hash')
    rs.add_argument('query'); rs.add_argument('--revision',type=int); rs.add_argument('--sha')
    rs.add_argument('--dry-run',action='store_true'); rs.add_argument('--replace',action='store_true')
    pr = sub.add_parser('protect', help='immutable flag on registered raw files'); pr.add_argument('mode',choices=['check','apply','release'])
    ic = sub.add_parser('incident', help='open integrity incidents'); ic.add_argument('action',choices=['list','resolve'])
    ic.add_argument('id',nargs='?'); ic.add_argument('--reason')
    jn = sub.add_parser('journal', help='verify the evidence journal or copy its head into the store'); jn.add_argument('action',choices=['verify','checkpoint'])
    sub.add_parser('doctor', help='diagnose the installation, with a fix for each problem (read-only)').add_argument('--json',action='store_true')
    sub.add_parser('setup', help='bring indexes, evaluation, store and flags up to date (idempotent)').add_argument('--dry-run',action='store_true')
    args = parser.parse_args()
    if args.command in ('doctor','setup'): import doctor; return doctor.run(ROOT, args)
    if args.command in ('backup','restore','protect','incident','journal'):
        result = evidence_store.cli(ROOT, args, check)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result.get('status') not in ('error','refused','conflict') and not result.get('errors') else 1
    if args.command=='build': result = build(ROOT)
    elif args.command=='check': result = check(ROOT, fast=args.fast)
    elif args.command=='evaluate': result = evaluate(ROOT)
    elif args.command=='search': result = search(ROOT,args.query,args.limit)
    elif args.command=='pack': result = answer_pack(ROOT,args.query,args.budget)
    elif args.command=='register': result = register(ROOT,args.path,args.origin)
    elif args.command=='source': result = source_search(ROOT,args.query,args.limit,args.content)
    elif args.command=='media': result = media_extract(ROOT,args.workers,args.limit)
    elif args.command=='media-info': result = media_info(ROOT,args.query,args.budget)
    elif args.command=='move':
        pairs = read_json(Path(args.plan)) if args.plan else [[args.old,args.new]]
        result = move_many(ROOT,pairs,args.reason,dry_run=args.dry_run)
    else: result = resolve(ROOT,args.query)
    if args.command=='evaluate':
        print(json.dumps({k:result[k] for k in ('pass','rank1','total')},indent=2))
    else:
        print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.command=='check' and not result['ok']: return 1
    if args.command=='evaluate' and result['pass']!=result['total']: return 1
    if args.command=='resolve' and not result: return 1
    return 1 if isinstance(result,dict) and result.get('status') in ('error','refused','conflict') else 0

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc: sys.exit(__import__('doctor').explain(' '.join(sys.argv[1:2]), exc))  # readable error and fix
