"""A small, valid vault for tests: three registered sources, current and archived pages, claims, an evidence
store and journal in a temporary folder. Nothing outside the temporary folder is touched."""
import json
from pathlib import Path
import shutil
import tempfile

import brain

TOOLS = Path(__file__).resolve().parent


def page(root, path, title, summary, keywords, sources, status='active', category='Domains', body=''):
    meta = {'title': title, 'summary': summary, 'category': category, 'status': status, 'as_of': '2026-10-08',
            'compiled_on': '2026-10-08', 'owner': 'XR-KNOWLEDGE', 'keywords': keywords, 'sources': sources}
    fm = '\n'.join(k + ': ' + json.dumps(v) for k, v in meta.items())
    p = Path(root) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('---\n' + fm + '\n---\n# ' + title + '\n' + body, encoding='utf-8')


def make_vault(protect=False, store=True, copy_tools=False):
    base = Path(tempfile.mkdtemp())
    root = base / 'vault'
    for d in ('raw/docs', 'wiki/archive', 'system', '.brain'):
        (root / d).mkdir(parents=True, exist_ok=True)
    cfg = {'protect_raw': protect}
    if store:
        cfg['evidence_store'] = str(base / 'store')
    (root / 'system/vault-config.json').write_text(json.dumps(cfg), encoding='utf-8')
    (base / 'store/objects').mkdir(parents=True) if store else None
    texts = {'raw/docs/build-log.md': 'Build 261019120000 installed on the headset.\n',
             'raw/docs/mirror-note.md': 'Mirrors render per eye through the outline.\n',
             'raw/docs/old-plan.md': 'Proposal 2: one shared app with content packages.\n'}
    for rel, text in texts.items():
        (root / rel).write_text(text, encoding='utf-8')
        brain.register(root, rel, 'fixture')
    page(root, 'wiki/delivery.md', 'Latest delivery', 'Build on the headset', ['build'], ['raw/docs/build-log.md'],
         body='[[raw/docs/build-log]]\n')
    page(root, 'wiki/mirrors.md', 'Mirrors', 'Stereo mirrors', ['mirrors'], ['raw/docs/mirror-note.md'])
    page(root, 'wiki/now.md', 'Now', 'Current state', ['current'], [])
    page(root, 'wiki/archive/old-plan.md', 'Old plan', 'Archived proposal', ['plan'], ['raw/docs/old-plan.md'],
         status='archived', category='Archive')
    claims = {'claims': [
        {'id': 'C-1', 'text': 'Build installed', 'page': 'wiki/delivery.md', 'confidence': 'verified', 'scope': 'fixture',
         'as_of': '2026-10-19', 'sources': ['raw/docs/build-log.md']},
        {'id': 'C-2', 'text': 'Mirrors per eye', 'page': 'wiki/mirrors.md', 'confidence': 'reported', 'scope': 'fixture',
         'as_of': '2026-10-19', 'sources': ['raw/docs/mirror-note.md']}]}
    brain.save_json(root / 'system/claims.json', claims)
    if copy_tools:
        (root / 'tools').mkdir(exist_ok=True)
        for module in ('brain.py', 'storage.py', 'evidence_store.py', 'retrieval.py', 'page_index.py', 'dense_client.py', 'anchors.py', 'facts.py', 'knowledge_time.py', 'freshness.py'):
            shutil.copyfile(str(TOOLS / module), str(root / 'tools' / module))
    brain.build(root)
    if store:
        brain.evidence_store.backup(root)
    return base, root


def cleanup(base):
    import evidence_store
    for p in Path(base).rglob('*'):
        if p.is_file() and not p.is_symlink() and evidence_store.is_immutable(p):
            evidence_store.set_immutable(p, False)
    shutil.rmtree(str(base), ignore_errors=True)
