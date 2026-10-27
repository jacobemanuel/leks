"""Mutation suite for the validator: each class corrupts a fresh, valid fixture vault in one specific way and
the full check must report it with the expected message. Run: python3 -m unittest tools/test_mutations.py"""
import json
import os
from pathlib import Path
import unittest

import brain
import evidence_store as es
import vault_fixture as vf


def edit_json(path, change):
    data = json.loads(path.read_text(encoding='utf-8'))
    change(data)
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')


def reg(root, change):
    edit_json(root / 'system/source-registry.json', change)


def claims(root, change):
    edit_json(root / 'system/claims.json', change)


def first(data, path='raw/docs/build-log.md'):
    return next(r for r in data['files'] if r['path'] == path)


def store_object(root, path='raw/docs/build-log.md'):
    obj = es.object_path(es.settings(root)['store'], first(es.registry(root), path)['sha256'])
    es.set_immutable(obj, False)
    os.chmod(str(obj), 0o644)
    return obj


def page_text(root, rel, old, new):
    p = root / rel
    p.write_text(p.read_text(encoding='utf-8').replace(old, new), encoding='utf-8')


def append(path, text):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(text)


def forge_journal(root):
    es.checkpoint(root)
    entries = es.journal_lines(root)
    entries[0]['path'] = 'raw/docs/forged.md'
    prev, out = '0' * 64, []
    for e in entries:
        body = {k: v for k, v in e.items() if k not in ('prev', 'hash')}
        e = dict(body, prev=prev, hash=es._chain(prev, body))
        prev = e['hash']
        out.append(json.dumps(e, sort_keys=True))
    (root / es.JOURNAL).write_text('\n'.join(out) + '\n')


MUTATIONS = {
    'raw content changed, same size': (lambda r: (r / 'raw/docs/build-log.md').write_text('Build 261019120001 installed on the headset.\n'), 'raw changed'),
    'raw truncated': (lambda r: (r / 'raw/docs/build-log.md').write_bytes(b''), 'raw changed'),
    'raw deleted': (lambda r: (r / 'raw/docs/build-log.md').unlink(), 'missing raw source'),
    'raw replaced by a symbolic link': (lambda r: ((r / 'raw/docs/build-log.md').unlink(), os.symlink('mirror-note.md', str(r / 'raw/docs/build-log.md'))), 'symbolic link'),
    'unregistered raw file': (lambda r: (r / 'raw/docs/stray.md').write_text('x'), 'unregistered raw source'),
    'renamed outside the move command': (lambda r: os.rename(str(r / 'raw/docs/old-plan.md'), str(r / 'raw/docs/plan.md')), 'missing raw source'),
    'duplicate source ID': (lambda r: reg(r, lambda d: d['files'][1].update(id=d['files'][0]['id'])), 'duplicate source ID/path'),
    'source outside raw': (lambda r: reg(r, lambda d: first(d).update(path='wiki/delivery.md')), 'source outside raw'),
    'invalid former path': (lambda r: reg(r, lambda d: first(d).update(former_paths=['wiki/x.md'])), 'invalid former_paths'),
    'former path reused by a current source': (lambda r: reg(r, lambda d: first(d).update(former_paths=['raw/docs/mirror-note.md'])), 'former path reused'),
    'incomplete revision record': (lambda r: reg(r, lambda d: first(d).update(revisions=[{'on': '2026-10-20'}])), 'incomplete revision record'),
    'invalid rendition type': (lambda r: reg(r, lambda d: first(d).update(rendition={'type': 'summary'})), 'invalid rendition type'),
    'rendition without translator': (lambda r: reg(r, lambda d: first(d).update(rendition={'type': 'english-translation', 'translated_on': '2026-10-20'})), 'rendition without translator'),
    'source attributed to a client tool': (lambda r: reg(r, lambda d: first(d).update(origin='GitHub Copilot agent session')), 'client tool'),
    'registry unreadable': (lambda r: (r / 'system/source-registry.json').write_text('{ "files": ['), 'registry unreadable'),
    'page missing a metadata field': (lambda r: page_text(r, 'wiki/mirrors.md', 'owner: "XR-KNOWLEDGE"\n', ''), 'missing metadata owner'),
    'page with an invalid date': (lambda r: page_text(r, 'wiki/mirrors.md', 'as_of: "2026-10-08"', 'as_of: "October 2026"'), 'invalid date as_of'),
    'archived page outside the archive': (lambda r: page_text(r, 'wiki/mirrors.md', 'status: "active"', 'status: "archived"'), 'archived page outside'),
    'archive page not marked archived': (lambda r: page_text(r, 'wiki/archive/old-plan.md', 'status: "archived"', 'status: "active"'), 'archived pages need status'),
    'page missing from the index': (lambda r: vf.page(r, 'wiki/new-topic.md', 'New topic', 'Not indexed', ['new'], []), 'page missing from index'),
    'page cites an unregistered source': (lambda r: page_text(r, 'wiki/mirrors.md', '"raw/docs/mirror-note.md"', '"raw/docs/unknown.md"'), 'source not registered'),
    'broken wikilink': (lambda r: page_text(r, 'wiki/now.md', '# Now\n', '# Now\n[[wiki/nowhere]]\n'), 'broken wikilink'),
    'broken markdown link': (lambda r: page_text(r, 'wiki/now.md', '# Now\n', '# Now\n[x](missing.md)\n'), 'broken local reference'),
    'unescaped alias in a table': (lambda r: page_text(r, 'wiki/now.md', '# Now\n', '# Now\n| a | [[wiki/mirrors|Mirrors]] |\n'), 'unescaped wikilink alias'),
    'claim missing a field': (lambda r: claims(r, lambda d: d['claims'][0].pop('scope')), 'missing scope'),
    'claim with an invalid confidence': (lambda r: claims(r, lambda d: d['claims'][0].update(confidence='certain')), 'invalid confidence'),
    'claim citing an unregistered source': (lambda r: claims(r, lambda d: d['claims'][0].update(sources=['raw/docs/unknown.md'])), 'source not registered'),
    'claim on a missing page': (lambda r: claims(r, lambda d: d['claims'][0].update(page='wiki/gone.md')), 'missing page'),
    'duplicate claim IDs': (lambda r: claims(r, lambda d: d['claims'][1].update(id='C-1')), 'duplicate claim IDs'),
    'broken supersession chain': (lambda r: claims(r, lambda d: d['claims'][0].update(supersedes=['C-2'])), 'asymmetric supersession'),
    'superseded claim without replacement': (lambda r: claims(r, lambda d: d['claims'][1].update(lifecycle='superseded', superseded_by='C-9')), 'missing replacement relation'),
    'asymmetric conflict': (lambda r: claims(r, lambda d: d['claims'][0].update(conflicts=['C-2'])), 'asymmetric conflict'),
    'missing store object': (lambda r: store_object(r).unlink(), 'evidence store: missing object'),
    'corrupted store object': (lambda r: store_object(r).write_text('short'), 'evidence store: corrupt object'),
    'tampered journal line': (lambda r: page_text(r, es.JOURNAL, 'raw/docs/build-log.md', 'raw/docs/forged.md'), 'chain broken'),
    'consistently rewritten journal': (forge_journal, 'independent checkpoint'),
    'interrupted journal append': (lambda r: append(r / es.JOURNAL, '{"event": "reg'), 'unreadable line'),
    'derived index stale': (lambda r: page_text(r, 'wiki/now.md', '# Now\n', '# Now\nEdited after the build.\n'), 'derived indexes stale'),
    'Polish letters in a page': (lambda r: page_text(r, 'wiki/now.md', '# Now\n', '# Now\n\u0142\u00f3d\u017a\n'), 'Polish letters'),
}


class ValidatorMutations(unittest.TestCase):
    def test_every_mutation_class_is_detected(self):
        missed = []
        for name, (mutate, expected) in MUTATIONS.items():
            base, root = vf.make_vault()
            try:
                self.assertTrue(brain.check(root, False)['ok'], name)
                mutate(root)
                errors = ' | '.join(brain.check(root, False)['errors'])
                if expected not in errors:
                    missed.append((name, expected, errors[:300]))
            finally:
                vf.cleanup(base)
        self.assertEqual(missed, [])

    def test_suite_has_at_least_thirty_classes(self):
        self.assertGreaterEqual(len(MUTATIONS), 30)


if __name__ == '__main__':
    unittest.main()
