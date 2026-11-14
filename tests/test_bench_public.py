"""The public PEP benchmark: converter, question rule, link prior and scoring, on small inline samples.

No network: fetch() is only called offline or on a file:// archive the test builds itself, and the tiny
end to end run uses five made-up PEPs written below.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUB = os.path.join(REPO, 'bench', 'public')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


peps = _load('t_pub_peps', os.path.join(PUB, 'peps.py'))
questions = _load('t_pub_questions', os.path.join(PUB, 'questions.py'))
link_prior = _load('t_pub_link_prior', os.path.join(PUB, 'link_prior.py'))
pep_bench = _load('t_pub_pep_bench', os.path.join(PUB, 'pep_bench.py'))
worker = _load('t_pub_leks_worker', os.path.join(PUB, 'leks_worker.py'))

SAMPLE = '''PEP: 563
Title: Postponed Evaluation of Annotations
Author: Some One <one@example.org>,
        Other Person <other@example.org>
Status: Superseded
Type: Standards Track
Created: 08-Sep-2017
Superseded-By: 649, 749
Replaces: 3107

.. superseded:: 649

Abstract
========

:pep:`3107` added annotations and :pep:`the typing PEP <484>` gave them a meaning,
see also https://peps.python.org/pep-0526/ and `the old page <https://www.python.org/dev/peps/pep-0008/>`__.
``x: int`` stays text, :rfc:`2822` too, and :pep:`563` names itself.

.. note::
   :class: small

   a note the site renders in a box

Details
-------

Some text.

.. comment line that should vanish
   and its second line

::

    # a comment in code, never a heading
    def f(): ...

Copyright
=========

This document is placed in the public domain or under the
CC0-1.0-Universal license, whichever is more permissive.
'''


def sample(**kw):
    """SAMPLE with header lines swapped: Status='Rejected' and so on, None drops the line."""
    text = SAMPLE
    for k, v in kw.items():
        key = k.replace('_', '-')
        rx = re.compile(r'^%s:.*\n' % re.escape(key), re.M)
        text = rx.sub('' if v is None else '%s: %s\n' % (key, v), text, count=1)
    return text


class Header(unittest.TestCase):
    def test_fields_and_continuation_lines(self):
        f, body = peps.split_header(SAMPLE)
        self.assertEqual(f['PEP'], '563')
        self.assertEqual(f['Title'], 'Postponed Evaluation of Annotations')
        self.assertEqual(f['Author'], 'Some One <one@example.org>, Other Person <other@example.org>')
        self.assertEqual(f['Status'], 'Superseded')
        self.assertEqual(f['Superseded-By'], '649, 749')
        self.assertEqual(body[0], '')
        self.assertIn('Abstract', body)

    def test_header_stops_at_first_blank_line(self):
        f, body = peps.split_header('PEP: 1\nTitle: A\n\nStatus: Final\n')
        self.assertNotIn('Status', f)
        self.assertIn('Status: Final', body)

    def test_crlf_input(self):
        f, _ = peps.split_header(SAMPLE.replace('\n', '\r\n'))
        self.assertEqual(f['Type'], 'Standards Track')

    def test_numbers(self):
        self.assertEqual(peps.pep_numbers('649, 749'), [649, 749])
        self.assertEqual(peps.pep_numbers('3124'), [3124])
        self.assertEqual(peps.pep_numbers(''), [])
        self.assertEqual(peps.pep_numbers(None), [])

    def test_dates(self):
        self.assertEqual(peps.iso_date('08-Sep-2017'), '2017-09-08')
        self.assertEqual(peps.iso_date('5-Jul-2001'), '2001-07-05')
        self.assertEqual(peps.iso_date('2001-07-05'), '2001-07-05')
        self.assertIsNone(peps.iso_date('sometime in 2001'))
        self.assertIsNone(peps.iso_date('08-Foo-2017'))


class StatusMap(unittest.TestCase):
    def test_every_row_of_the_table(self):
        want = {'Active': 'current', 'Accepted': 'current', 'Provisional': 'current', 'Final': 'record',
                'Superseded': 'superseded', 'Rejected': 'archived', 'Withdrawn': 'archived',
                'Deferred': 'unknown', 'Draft': 'unknown', 'April Fool!': 'unknown', '': 'unknown'}
        for pep_status, word in want.items():
            self.assertEqual(peps.status_word(pep_status), word, pep_status)

    def test_case_and_padding(self):
        self.assertEqual(peps.status_word(' superseded '), 'superseded')
        self.assertEqual(peps.status_word('FINAL'), 'record')
        self.assertEqual(peps.status_word(None), 'unknown')

    def test_rejected_and_withdrawn_are_no_replacement(self):
        # leks itself reads "rejected" as superseded; the converter must never write that word for them
        for s in ('Rejected', 'Withdrawn'):
            self.assertNotIn(peps.status_word(s), ('superseded', 'rejected', 'withdrawn'))

    def test_written_words_read_by_this_leks(self):
        from leks import chunking
        want = {'current': 'current', 'record': 'record', 'superseded': 'superseded',
                'archived': 'historical-record'}
        for word, status in want.items():
            self.assertEqual(chunking.STATUS_WORDS[word], status)
        self.assertNotIn('unknown', chunking.STATUS_WORDS)


class License(unittest.TestCase):
    def test_classes(self):
        cases = {
            'Copyright\n=========\n\nThis document has been placed in the public domain.\n': 'public-domain',
            'Copyright\n=========\n\nThis document is placed in the public domain or under the\n'
            'CC0-1.0-Universal license, whichever is more permissive.\n': 'public-domain-or-cc0',
            'Copyright\n=========\n\nThis document is licensed under the `Open Publication License`_.\n': 'opl',
            'Copyright\n=========\n\nDo not even think about copying it.\n': 'none',
            'Abstract\n========\n\nNo copyright section at all.\n': 'none',
        }
        for text, want in cases.items():
            self.assertEqual(peps.license_of(text), want, text[:40])

    def test_only_the_last_copyright_section_counts(self):
        text = ('Copyright\n---------\n\nquoting a public domain idea\n\nCopyright\n=========\n\n'
                'This document is licensed under the Open Publication License.\n')
        self.assertEqual(peps.license_of(text), 'opl')


class Body(unittest.TestCase):
    def setUp(self):
        _f, body = peps.split_header(SAMPLE)
        self.md = peps.rst_body(body)

    def test_pep_roles_and_urls_become_wikilinks(self):
        self.assertIn('[[pep-3107]] added annotations', self.md)
        self.assertIn('[[pep-0484|the typing PEP]]', self.md)
        self.assertIn('[[pep-0526]]', self.md)
        self.assertIn('[[pep-0008|the old page]]', self.md)

    def test_other_inline_markup(self):
        self.assertIn('`x: int`', self.md)
        self.assertIn('RFC 2822', self.md)
        self.assertNotIn(':rfc:', self.md)
        self.assertNotIn(':pep:', self.md)

    def test_headings_by_adornment(self):
        self.assertIn('\n## Abstract\n', '\n' + self.md)
        self.assertIn('\n### Details\n', self.md)
        self.assertIn('\n## Copyright\n', self.md)

    def test_banner_comment_and_directive(self):
        self.assertTrue(self.md.startswith('This PEP has been superseded by [[pep-0649]].'))
        self.assertNotIn('comment line', self.md)
        self.assertNotIn('and its second line', self.md)
        self.assertNotIn(':class:', self.md)
        # the body of a note directive stays as text
        self.assertIn('a note the site renders in a box', self.md)

    def test_code_keeps_its_indent(self):
        self.assertIn('\n    # a comment in code, never a heading\n', self.md)
        self.assertNotIn('\n# a comment in code', self.md)

    def test_overline_title_and_targets(self):
        md = peps.rst_body(['=====', 'Title', '=====', '', 'see `docs`_', '', '.. _docs: https://example.org/x',
                            '.. [1] https://example.org/ref', '.. |sub| replace:: gone', '   still gone', 'end'])
        self.assertTrue(md.startswith('## Title'))
        self.assertIn('docs: https://example.org/x', md)
        self.assertIn('[1] https://example.org/ref', md)
        self.assertNotIn('gone', md)
        self.assertIn('see docs', md)

    def test_links_exclude_the_note_itself(self):
        self.assertEqual(peps.links_in(self.md, 563), [8, 484, 526, 649, 3107])
        self.assertIn('[[pep-0563]]', self.md)


class Notes(unittest.TestCase):
    def test_front_matter_exact(self):
        meta, note = peps.convert(SAMPLE, 'pep-0563.rst')
        fm = note.split('---\n')[1]
        self.assertEqual(fm, 'title: "Postponed Evaluation of Annotations"\npep: 563\nstatus: superseded\n'
                             'pep_status: Superseded\npep_type: Standards Track\ndate: 2017-09-08\n'
                             'superseded_by: ["[[pep-0649]]", "[[pep-0749]]"]\nsupersedes: "[[pep-3107]]"\n'
                             'license: public-domain-or-cc0\n')
        self.assertIn('\n# PEP 563: Postponed Evaluation of Annotations\n', note)
        self.assertEqual(meta['superseded_by'], [649, 749])
        self.assertEqual(meta['replaces'], [3107])
        self.assertEqual(meta['name'], 'pep-0563.md')

    def test_quotes_in_title_are_escaped(self):
        _m, note = peps.convert(sample(Title='The "with" Statement'), 'pep-0343.rst')
        self.assertIn('title: "The \\"with\\" Statement"\n', note)

    def test_missing_relations_and_number_from_name(self):
        meta, note = peps.convert(sample(PEP=None, Superseded_By=None, Replaces=None, Status='Rejected'),
                                  'pep-0563.rst')
        self.assertEqual(meta['pep'], 563)
        self.assertNotIn('superseded_by:', note)
        self.assertNotIn('supersedes:', note)
        self.assertIn('\nstatus: archived\n', note)

    def test_no_number_at_all_raises(self):
        with self.assertRaises(ValueError):
            peps.convert(sample(PEP=None), 'notes.rst')

    def test_leks_reads_status_and_single_relations(self):
        # what the engine of this checkout makes of converted notes: the status word, one superseded_by
        # in the old note, one supersedes in the new note. no banner in the body, so only the front matter counts
        from leks import chunking

        def tiny(n, status, sb=None, rp=None):
            head = ['PEP: %d' % n, 'Title: Thing %d' % n, 'Status: %s' % status, 'Created: 01-Jan-2020']
            head += ['Superseded-By: %s' % sb] if sb else []
            head += ['Replaces: %s' % rp] if rp else []
            return peps.convert('\n'.join(head) + '\n\nAbstract\n========\n\nWords.\n', 'pep-%04d.rst' % n)[1]

        def edges(old, new):
            rows = [(1, 'peps/pep-0563.md', 'pep-0563.md', '2020-01-01', 0.0, old),
                    (2, 'peps/pep-0649.md', 'pep-0649.md', '2020-01-01', 0.0, new)]
            return {k: v[0] for k, v in chunking.succession(rows).items()}

        old_says, old_quiet = tiny(563, 'Superseded', sb='649'), tiny(563, 'Superseded')
        new_says, new_quiet = tiny(649, 'Accepted', rp='563'), tiny(649, 'Accepted')
        fm = chunking.front(old_says.split('\n'))
        self.assertEqual(chunking._status_word(fm['status']), ('superseded', True))
        self.assertEqual(edges(old_says, new_quiet), {1: 2})
        self.assertEqual(edges(old_quiet, new_says), {1: 2})
        self.assertEqual(edges(old_quiet, new_quiet), {})

    def test_superseded_banner_names_the_first_successor(self):
        _f, body = peps.split_header(SAMPLE)
        first = [ln for ln in peps.rst_body(body).split('\n') if ln.strip()][0]
        self.assertEqual(first, 'This PEP has been superseded by [[pep-0649]].')

    def test_convert_tree_keeps_only_free_licenses(self):
        tmp = tempfile.mkdtemp()
        try:
            src, dest = os.path.join(tmp, 'src'), os.path.join(tmp, 'out')
            os.makedirs(src)
            opl = sample(PEP='1', Title='Open', Superseded_By=None, Replaces=None).split('Copyright\n=========')[0]
            opl += 'Copyright\n=========\n\nThis document is licensed under the Open Publication License.\n'
            texts = {'pep-0563.rst': SAMPLE, 'pep-0001.rst': opl,
                     'pep-0649.rst': sample(PEP='649', Title='Deferred', Superseded_By=None, Replaces='563'),
                     'README.rst': 'not a pep'}
            for n, t in texts.items():
                with open(os.path.join(src, n), 'w', encoding='utf-8') as f:
                    f.write(t)
            man = peps.convert_tree(src, dest)
            self.assertEqual(sorted(os.listdir(os.path.join(dest, 'peps'))), ['pep-0563.md', 'pep-0649.md'])
            self.assertEqual(man['left_out'], [{'pep': 1, 'license': 'opl'}])
            self.assertEqual([p['pep'] for p in man['peps']], [563, 649])
            self.assertIsNone(man['commit'])
            self.assertEqual(man['source_digest'], peps.source_digest(src))
            with open(os.path.join(dest, 'manifest.json'), encoding='utf-8') as f:
                self.assertEqual(json.load(f)['source_digest'], man['source_digest'])
            # the same bytes on every system, windows included
            for n in ('peps/pep-0563.md', 'peps/pep-0649.md', 'manifest.json'):
                with open(os.path.join(dest, n), 'rb') as f:
                    self.assertNotIn(b'\r', f.read(), n)
        finally:
            shutil.rmtree(tmp)


class Fetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_offline_without_cache_raises(self):
        with self.assertRaises(RuntimeError):
            peps.fetch(os.path.join(self.tmp, 'cache'), offline=True)

    def test_cached_sources_with_another_digest_are_refused(self):
        dest = os.path.join(self.tmp, 'peps-src-' + peps.PINNED_SHA[:12])
        os.makedirs(dest)
        with open(os.path.join(dest, 'pep-0001.rst'), 'w', encoding='utf-8') as f:
            f.write('PEP: 1\n')
        with self.assertRaises(RuntimeError):
            peps.fetch(self.tmp, offline=True)

    def test_other_commit_is_not_checked_against_the_pin(self):
        sha = 'f' * 40
        dest = os.path.join(self.tmp, 'peps-src-' + sha[:12])
        os.makedirs(dest)
        with open(os.path.join(dest, 'pep-0001.rst'), 'w', encoding='utf-8') as f:
            f.write('PEP: 1\n')
        self.assertEqual(peps.fetch(self.tmp, sha=sha, offline=True), dest)

    def test_extract_takes_only_pep_sources_by_name(self):
        tar_path = os.path.join(self.tmp, 'a.tar.gz')
        with tarfile.open(tar_path, 'w:gz') as tf:
            for name, data in (('peps-x/peps/pep-0001.rst', b'PEP: 1\n'), ('peps-x/peps/pep-0002.md', b'PEP: 2\n'),
                               ('peps-x/peps/../../evil.rst', b'bad'), ('peps-x/peps/pep-0003.txt', b'no'),
                               ('peps-x/other/pep-0004.rst', b'no'), ('peps-x/peps/sub/pep-0005.rst', b'no')):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        out = os.path.join(self.tmp, 'out')
        os.makedirs(out)
        self.assertEqual(peps._extract(tar_path, out), 2)
        self.assertEqual(sorted(os.listdir(out)), ['pep-0001.rst', 'pep-0002.md'])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'evil.rst')))

    def test_digest_depends_on_names_and_bytes(self):
        a = os.path.join(self.tmp, 'a')
        os.makedirs(a)
        with open(os.path.join(a, 'pep-0001.rst'), 'wb') as f:
            f.write(b'x')
        with open(os.path.join(a, 'notes.txt'), 'wb') as f:
            f.write(b'ignored')
        d1 = peps.source_digest(a)
        row = 'pep-0001.rst\t%s\n' % hashlib.sha256(b'x').hexdigest()
        self.assertEqual(d1, hashlib.sha256(row.encode('ascii')).hexdigest())
        with open(os.path.join(a, 'pep-0001.rst'), 'wb') as f:
            f.write(b'y')
        self.assertNotEqual(peps.source_digest(a), d1)


class Questions(unittest.TestCase):
    def test_numbers_removed(self):
        self.assertEqual(questions.strip_numbers('Python 3000', [3000]), 'Python')
        self.assertEqual(questions.strip_numbers('IEEE 754 Floating Point Special Values', [754]),
                         'IEEE Floating Point Special Values')
        self.assertEqual(questions.strip_numbers('Python 3.4 Release Schedule', [4, 429]),
                         'Python 3.4 Release Schedule')
        self.assertEqual(questions.strip_numbers('Backport ensurepip (PEP 453) to Python 2.7', [477]),
                         'Backport ensurepip (PEP 453) to Python 2.7')
        self.assertEqual(questions.strip_numbers('Backport ensurepip (PEP 453) to Python 2.7', [477, 453]),
                         'Backport ensurepip to Python 2.7')

    def test_ascii_fold(self):
        title = 'Marking ' + chr(0x201c) + 'externally managed' + chr(0x201d) + ' environments ' + chr(0x2014) + \
            ' caf' + chr(0xe9)
        q = questions.ascii_fold(title)
        self.assertTrue(all(ord(c) < 128 for c in q))
        self.assertEqual(re.sub(r'\s+', ' ', q).strip(), 'Marking externally managed environments caf' + 'e')

    def test_pairs_from_both_headers(self):
        man = [{'pep': 1, 'superseded_by': [2], 'replaces': []}, {'pep': 2, 'superseded_by': [], 'replaces': [1, 3]},
               {'pep': 3, 'superseded_by': [], 'replaces': []}, {'pep': 4, 'superseded_by': [99], 'replaces': [4]}]
        self.assertEqual(questions.relation_pairs(man), [(1, 2), (3, 2)])

    def tiny_manifest(self):
        def p(n, title, sb=(), rp=()):
            return {'pep': n, 'title': title, 'superseded_by': list(sb), 'replaces': list(rp)}
        return {'repo': 'r', 'commit': 'c', 'source_digest': 'd', 'left_out': [{'pep': 9, 'license': 'opl'}],
                'peps': [p(1, 'Old Thing Proposal', sb=[2]), p(2, 'New Thing Proposal', rp=[1]),
                         p(3, 'Python 3'), p(4, 'Same Name Here'), p(5, 'Same Name Here'), p(6, 'PEP 6: Six Rules')]}

    def test_build(self):
        d = questions.build(self.tiny_manifest())
        ids = [q['id'] for q in d['questions']]
        self.assertEqual(ids, ['q1-0001', 'q1-0002', 'q1-0006', 'q2-0001-0002', 'q2c-0001-0002'])
        self.assertEqual(d['dropped'], {'q1_short': [3], 'q1_duplicate': [4, 5], 'q2_short': []})
        by = {q['id']: q for q in d['questions']}
        self.assertEqual(by['q1-0006']['question'], 'Six Rules')
        self.assertTrue(by['q1-0001']['has_successor'])
        self.assertFalse(by['q1-0002']['has_successor'])
        q2 = by['q2-0001-0002']
        self.assertEqual((q2['question'], q2['gold'], q2['stale'], q2['category']),
                         ('Old Thing Proposal', ['peps/pep-0002.md'], ['peps/pep-0001.md'], 'current'))
        self.assertEqual(by['q2c-0001-0002']['question'], 'current Old Thing Proposal')
        self.assertEqual(d['counts']['pairs'], 1)
        self.assertEqual(d['counts']['left_out_by_license'], 1)
        # the same input gives the same bytes
        self.assertEqual(questions.dumps(d), questions.dumps(questions.build(self.tiny_manifest())))

    def test_chains(self):
        self.assertEqual(questions.chains([(1, 2), (2, 3), (4, 5)]), [[1, 2, 3]])


class FrozenFile(unittest.TestCase):
    def test_hash_in_docs_and_shape(self):
        path = os.path.join(PUB, 'questions.json')
        with open(path, 'rb') as f:
            raw = f.read()
        sha = hashlib.sha256(raw).hexdigest()
        with open(os.path.join(REPO, 'docs', 'benchmark.md'), encoding='utf-8') as f:
            self.assertIn(sha, f.read(), 'questions.json changed after it was frozen')
        raw.decode('ascii')
        d = json.loads(raw)
        self.assertEqual(raw.decode('ascii'), questions.dumps(d))
        ids = [q['id'] for q in d['questions']]
        self.assertEqual(len(ids), len(set(ids)))
        for q in d['questions']:
            self.assertTrue(q['question'] and q['gold'] and q['category'])
            self.assertNotRegex(q['question'], r'\b0*%d\b' % q.get('pep', q.get('old', -1)))
        self.assertEqual(d['source']['commit'], peps.PINNED_SHA)
        self.assertEqual(d['source']['source_digest'], peps.SOURCE_DIGEST)


class LinkPrior(unittest.TestCase):
    def test_wikilinks(self):
        self.assertEqual(link_prior.wikilinks('see [[STATE]], [[adr-1|the adr]] and [[notes/x.md#part]]'),
                         ['state', 'adr-1', 'x'])

    def test_graph(self):
        out, inn = link_prior.link_graph({'n/a.md': '[[B]] [[a]] [[missing]]', 'n/b.md': '', 'c/a.py': ''})
        self.assertEqual(out['n/a.md'], {'n/b.md', 'c/a.py'})
        self.assertEqual(inn['n/b.md'], {'n/a.md'})
        self.assertEqual(out['n/b.md'], set())

    def test_control_keeps_the_order(self):
        paths = ['p%d' % i for i in range(25)]
        self.assertEqual(link_prior.rerank(paths, {}, {}, set()), paths[:20])

    def test_out_link_from_the_top_hit(self):
        paths = ['a', 'b', 'c', 'd', 'e']
        out = {'a': {'e'}}
        # 1/15 + 0.25/11 beats 1/12 and 1/13: e moves to second place
        self.assertEqual(link_prior.rerank(paths, out, {}, set(), w_out=0.25), ['a', 'e', 'b', 'c', 'd'])
        # a link from a note outside the seeds counts for nothing
        self.assertEqual(link_prior.rerank(paths, {'d': {'e'}}, {}, set(), w_out=0.25), paths)
        # a seed that links to itself gets nothing for it
        self.assertEqual(link_prior.rerank(paths, {'b': {'b'}}, {}, set(), w_out=0.25, w_in=0.25), paths)

    def test_in_link_and_state(self):
        paths = ['a', 'b', 'c', 'd', 'e']
        # d at rank 4 links to the top hit: 1/14 + 0.25/11 beats 1/11, so it takes first place
        self.assertEqual(link_prior.rerank(paths, {'d': {'a'}}, {}, set(), w_in=0.25), ['d', 'a', 'b', 'c', 'e'])
        # from rank 5 on the same link is not enough to pass the top hit
        self.assertEqual(link_prior.rerank(paths, {'e': {'a'}}, {}, set(), w_in=0.25), ['a', 'e', 'b', 'c', 'd'])
        self.assertEqual(link_prior.rerank(paths, {}, {}, {'c'}, w_state=0.25), ['c', 'a', 'b', 'd', 'e'])
        self.assertEqual(link_prior.state_targets({'s': {'a', 's2'}, 's2': {'b'}}, ['s', 's2']), {'a', 'b'})

    def test_holm(self):
        adj = link_prior.holm([0.01, 0.04, 0.03])
        self.assertEqual([round(x, 6) for x in adj], [0.03, 0.06, 0.06])
        self.assertEqual(link_prior.holm([0.5, 0.9]), [1.0, 1.0])

    def test_wilson(self):
        self.assertEqual(link_prior.wilson(0, 0), (0.0, 0.0, 0.0))
        p, lo, hi = link_prior.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=4)
        self.assertAlmostEqual(hi, 0.7634, places=4)
        self.assertAlmostEqual(link_prior.wilson(10, 10)[2], 1.0)
        self.assertLessEqual(link_prior.wilson(10, 10)[2], 1.0)

    def test_variants_match_the_plan(self):
        self.assertEqual(link_prior.VARIANTS, {
            'control': (0.0, 0.0, 0.0), 'out': (0.25, 0.0, 0.0), 'in': (0.0, 0.25, 0.0),
            'both': (0.25, 0.25, 0.0), 'both-low': (0.10, 0.10, 0.0), 'state': (0.0, 0.0, 0.25),
            'all': (0.25, 0.25, 0.25)})
        self.assertEqual((link_prior.SEEDS, link_prior.POOL), (3, 20))


class Scoring(unittest.TestCase):
    def test_a_rate_shows_rounded_once_from_its_counts(self):
        # the stored rate keeps 4 places; showing that one rounded a second time gave 89.3% for 621 of 695
        for k, n, want in ((621, 695, '89.4% (86.8 to 91.4)'), (616, 662, '93.1% (90.9 to 94.8)'),
                           (27, 41, '65.9% (50.5 to 78.4)'), (2, 3, '66.7% (20.8 to 93.9)')):
            r = pep_bench.rate(k, n)
            self.assertEqual(pep_bench.pct(r), want)
            self.assertEqual(pep_bench.pct_n(r), '%d/%d, %s' % (k, n, want))

    def test_bm25_baseline(self):
        tmp = tempfile.mkdtemp()
        try:
            for name, text in (('pep-0001.md', 'tile cache eviction policy'), ('pep-0002.md', 'release schedule'),
                               ('pep-0003.md', 'cache sizes'), ('skip.txt', 'tile cache eviction')):
                with open(os.path.join(tmp, name), 'w', encoding='utf-8') as f:
                    f.write(text)
            r = pep_bench.bm25_run(tmp, [{'id': 'a', 'question': 'Tile cache eviction?'},
                                         {'id': 'b', 'question': '?!'}])
            paths = [h['path'] for h in r['results']['a']['hits']]
            self.assertEqual(paths, ['peps/pep-0001.md', 'peps/pep-0003.md'])
            self.assertEqual(r['results']['b']['hits'], [])
        finally:
            shutil.rmtree(tmp)

    def test_replacement_and_pointer(self):
        q = {'id': 'x', 'set': 'q2', 'old': 1, 'new': 2, 'successors': [2, 3], 'gold': ['peps/pep-0002.md'],
             'stale': ['peps/pep-0001.md']}

        def res(*hits):
            return {'x': {'hits': [{'path': p, 'newer': n} for p, n in hits]}}
        rows = pep_bench.score_q2(res(('peps/pep-0002.md', None), ('peps/pep-0001.md', 'peps/pep-0003.md')), [q])
        self.assertEqual(rows['x'], [1, 2, 'peps/pep-0003.md'])
        self.assertTrue(pep_bench.above(rows['x']))
        s = pep_bench.summary_q2(rows, [q])
        self.assertEqual((s['pointer']['k'], s['pointer']['n'], s['pointer_wrong']), (1, 1, 0))
        rows = pep_bench.score_q2(res(('peps/pep-0001.md', 'peps/pep-0009.md'), ('peps/pep-0002.md', None)), [q])
        self.assertFalse(pep_bench.above(rows['x']))
        s = pep_bench.summary_q2(rows, [q])
        self.assertEqual((s['pointer']['k'], s['pointer_wrong'], s['old_first']), (0, 1, 1))
        # new one listed, old one absent: counts as above
        self.assertTrue(pep_bench.above(pep_bench.score_q2(res(('peps/pep-0002.md', None)), [q])['x']))
        # neither listed: not above
        self.assertFalse(pep_bench.above(pep_bench.score_q2(res(), [q])['x']))

    def test_edge_check(self):
        qs = [{'set': 'q2', 'old': 1, 'new': 2, 'successors': [2, 3]},
              {'set': 'q2', 'old': 1, 'new': 3, 'successors': [2, 3]},
              {'set': 'q2', 'old': 4, 'new': 5, 'successors': [5]}]
        edges = [['peps/pep-0001.md', 'peps/pep-0003.md', 'the note says so'],
                 ['peps/pep-0007.md', 'peps/pep-0008.md', 'the newer note says so']]
        r = pep_bench.edge_check(edges, qs)
        # one edge per old note: an edge to either successor covers both pairs of that note
        self.assertEqual((r['pairs_covered'], r['pairs'], r['old_covered'], r['olds']), (2, 3, 1, 2))
        self.assertEqual((r['not_a_pair'], r['not_a_pair_list']), (1, [['peps/pep-0007.md', 'peps/pep-0008.md']]))
        self.assertEqual(r['pairs_without_edge'], [[4, 5]])
        self.assertEqual(r['how'], {'the note says so': 1, 'the newer note says so': 1})

    def test_paired(self):
        r = pep_bench.paired([True, True, False, False], [True, False, True, True])
        self.assertEqual((r['a_only'], r['b_only']), (1, 2))
        self.assertAlmostEqual(r['p'], 1.0)


class RelationGraph(unittest.TestCase):
    def test_front_fields(self):
        self.assertEqual(link_prior.front_fields('---\nTitle: A\nsuperseded_by: "[[b]]"\n---\nbody: no\n'),
                         {'title': 'A', 'superseded_by': '"[[b]]"'})
        self.assertEqual(link_prior.front_fields('no front matter\nsupersedes: x\n'), {})
        self.assertEqual(link_prior.front_fields('---\nsupersedes: x\nnever closed\n'), {})

    def test_names_in(self):
        self.assertEqual(link_prior.names_in('"[[pep-0649]]"'), ['pep-0649'])
        self.assertEqual(link_prior.names_in('["[[pep-0649]]", "[[pep-0749|the later one]]"]'), ['pep-0649', 'pep-0749'])
        self.assertEqual(link_prior.names_in('adr-006-tile-size-512'), ['adr-006-tile-size-512'])
        self.assertEqual(link_prior.names_in('notes/ADR-6.md'), ['adr-6'])
        self.assertEqual(link_prior.names_in(''), [])
        self.assertEqual(link_prior.names_in(None), [])

    def test_both_sides_of_the_relation(self):
        texts = {
            'p/old.md': '---\nsuperseded_by: "[[new]]"\n---\n',
            'p/new.md': '---\nsupersedes: ["[[older]]", "[[old]]"]\n---\n',
            'p/older.md': 'no front matter, [[new]] in the body is no relation\n',
            'p/self.md': '---\nreplaced_by: self\nreplaces: missing\n---\n',
            'p/bare.md': '---\nreplaced-by: new\n---\n',
        }
        newer = link_prior.relation_graph(texts)
        self.assertEqual(newer['p/old.md'], {'p/new.md'})
        self.assertEqual(newer['p/older.md'], {'p/new.md'})
        self.assertEqual(newer['p/bare.md'], {'p/new.md'})
        self.assertEqual(newer['p/new.md'], set())
        self.assertEqual(newer['p/self.md'], set())

    def test_rel_variant_lifts_the_newer_note_of_a_seed(self):
        paths = ['old', 'b', 'c', 'new', 'e']
        newer = {'old': {'new'}}
        # new at rank 4: 1/14 + 0.25/11 passes the seed it replaces
        self.assertEqual(link_prior.rerank(paths, newer, {}, set(), w_out=link_prior.REL_VARIANTS['rel']),
                         ['new', 'old', 'b', 'c', 'e'])
        # 0.10/11 only lifts it past rank 3 (1/14 + 0.10/11 sits between 1/13 and 1/12)
        self.assertEqual(link_prior.rerank(paths, newer, {}, set(), w_out=link_prior.REL_VARIANTS['rel-low']),
                         ['old', 'b', 'new', 'c', 'e'])
        # a relation of a note outside the seeds counts for nothing
        self.assertEqual(link_prior.rerank(paths, {'e': {'new'}}, {}, set(), w_out=0.25), paths)

    def test_relation_variants_match_the_plan(self):
        self.assertEqual(link_prior.REL_VARIANTS, {'rel': 0.25, 'rel-low': 0.10})


class Verdict(unittest.TestCase):
    @staticmethod
    def variant(k1, q2, kinds, a_only=0, b_only=0, holm_p=1.0, key='q2_above'):
        v = {key: {'k': q2}, 'kinds': {n: {'k': k} for n, k in kinds.items()}, 'q1': {'k': k1}}
        v['vs_control'] = {'a_only': a_only, 'b_only': b_only, 'holm_p': holm_p}
        return v

    def sets(self, pep_kinds, fix_kinds, gain=True, q2=5):
        pep = {'control': self.variant(0, 5, {'q1 has newer': 3, 'q2': 5}),
               'x': self.variant(0, q2, pep_kinds, 1, 9 if gain else 1, 0.01 if gain else 1.0)}
        fix = {'control': self.variant(0, 2, {'hit@1 fact': 4}, key='current_above_stale'),
               'x': self.variant(0, 2, fix_kinds, 0, 6 if gain else 0, 0.03 if gain else 1.0, key='current_above_stale')}
        return pep, fix

    def test_ships_only_with_a_gain_and_no_kind_down(self):
        pep, fix = self.sets({'q1 has newer': 3, 'q2': 5}, {'hit@1 fact': 4})
        self.assertTrue(pep_bench.verdicts(pep, fix, ['x'])['x']['ships'])

    def test_a_kind_down_stops_it(self):
        pep, fix = self.sets({'q1 has newer': 2, 'q2': 6}, {'hit@1 fact': 4})
        v = pep_bench.verdicts(pep, fix, ['x'])['x']
        self.assertTrue(v['gain_on_both'] and v['no_replacement_loss'])
        self.assertEqual((v['kinds_down'], v['ships']), (['peps: q1 has newer'], False))
        pep, fix = self.sets({'q1 has newer': 3, 'q2': 5}, {'hit@1 fact': 3})
        self.assertEqual(pep_bench.verdicts(pep, fix, ['x'])['x']['kinds_down'], ['fixtures: hit@1 fact'])

    def test_no_gain_or_a_replacement_loss_stops_it(self):
        pep, fix = self.sets({'q1 has newer': 3, 'q2': 5}, {'hit@1 fact': 4}, gain=False)
        self.assertFalse(pep_bench.verdicts(pep, fix, ['x'])['x']['ships'])
        pep, fix = self.sets({'q1 has newer': 3, 'q2': 5}, {'hit@1 fact': 4}, q2=4)
        v = pep_bench.verdicts(pep, fix, ['x'])['x']
        self.assertEqual((v['no_replacement_loss'], v['ships']), (False, False))


class ReplacementMetrics(unittest.TestCase):
    def test_new_pep_as_a_known_item(self):
        qs = [{'id': i, 'old': 1, 'new': 2, 'successors': [2], 'gold': ['peps/pep-0002.md'],
               'stale': ['peps/pep-0001.md']} for i in ('a', 'b', 'c', 'd')]
        rows = {'a': [1, 2, None], 'b': [2, 1, 'peps/pep-0002.md'], 'c': [7, None, None], 'd': [None, 1, None]}
        s = pep_bench.summary_q2(rows, qs)
        self.assertEqual((s['new']['hit1']['k'], s['new']['hit5']['k'], s['new']['n']), (1, 2, 4))
        self.assertAlmostEqual(s['new']['mrr10']['mean'], round((1 + 0.5 + 1 / 7 + 0) / 4, 4))
        self.assertEqual((s['above']['k'], s['old_first'], s['new_missing']), (2, 2, 1))
        self.assertEqual((s['pointer']['k'], s['pointer']['n']), (1, 3))

    def test_miss_below_the_cut(self):
        s = pep_bench.summary_q1([1, 11, None])
        self.assertEqual((s['hit1']['k'], s['hit5']['k']), (1, 1))
        self.assertAlmostEqual(s['mrr10']['mean'], round(1 / 3, 4))


class ModelGap(unittest.TestCase):
    def test_reasons(self):
        self.assertIsNone(worker.model_gap([], [], '/w'))
        self.assertIn('torch, tokenizers', worker.model_gap(['torch', 'tokenizers'], [], '/w'))
        self.assertIn('.[models]', worker.model_gap(['torch'], [], '/w'))
        r = worker.model_gap([], ['mdense'], '/w')
        self.assertIn('mdense', r)
        self.assertIn('--fetch-models', r)

    def test_missing_packages(self):
        self.assertEqual(worker.missing_packages(['json', 'surely_no_such_package_here']),
                         ['surely_no_such_package_here'])

    def test_default_weights_follow_leks_home(self):
        old = os.environ.get('LEKS_HOME')
        try:
            os.environ['LEKS_HOME'] = os.path.join(tempfile.gettempdir(), 'some-home')
            self.assertEqual(pep_bench.default_weights(), os.path.join(tempfile.gettempdir(), 'some-home', 'models'))
        finally:
            if old is None:
                os.environ.pop('LEKS_HOME', None)
            else:
                os.environ['LEKS_HOME'] = old


TINY = {
    101: ('Tile Cache Eviction Policy', 'Superseded', {'Superseded-By': '102'}, 'pd'),
    102: ('Adaptive Tile Cache Eviction', 'Final', {'Replaces': '101'}, 'both'),
    103: ('Release Schedule Rules', 'Active', {}, 'pd'),
    104: ('Open Format Notes', 'Final', {}, 'opl'),
    105: ('Deprecated Module Removal Plan', 'Rejected', {}, 'cc0'),
}
TINY_COPY = {
    'pd': 'This document has been placed in the public domain.',
    'both': 'This document is placed in the public domain or under the CC0-1.0-Universal license, '
            'whichever is more permissive.',
    'cc0': 'This document is licensed under CC0-1.0-Universal.',
    'opl': 'This document is licensed under the Open Publication License.',
}


def tiny_source(n):
    title, status, extra, lic = TINY[n]
    head = ['PEP: %d' % n, 'Title: %s' % title, 'Author: Nobody <nobody@example.org>', 'Status: %s' % status,
            'Type: Standards Track', 'Created: 0%d-Mar-2020' % (n % 10)]
    head += ['%s: %s' % kv for kv in extra.items()]
    body = ['', 'Abstract', '========', '', 'This proposal is about %s.' % title.lower(), '',
            'Copyright', '=========', '', TINY_COPY[lic], '']
    return '\n'.join(head + body)


class TinyEndToEnd(unittest.TestCase):
    """The whole script on five made-up PEPs: fetch from a file:// archive, digest, questions, run, files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        src = os.path.join(self.tmp, 'src')
        os.makedirs(src)
        for n in TINY:
            with open(os.path.join(src, 'pep-%04d.rst' % n), 'w', encoding='utf-8') as f:
                f.write(tiny_source(n))
        tar_path = os.path.join(self.tmp, '%s.tar.gz' % peps.PINNED_SHA)
        with tarfile.open(tar_path, 'w:gz') as tf:
            for fn in sorted(os.listdir(src)):
                tf.add(os.path.join(src, fn), arcname='peps-x/peps/' + fn)
            tf.add(os.path.join(src, 'pep-0101.rst'), arcname='peps-x/README.rst')
        self.saved = (pep_bench.peps.ARCHIVE_URL, pep_bench.peps.SOURCE_DIGEST, pep_bench.QFILE)
        # file:///C:/... on windows, file:///tmp/... elsewhere
        pep_bench.peps.ARCHIVE_URL = pathlib.Path(self.tmp).as_uri() + '/{sha}.tar.gz'
        pep_bench.peps.SOURCE_DIGEST = peps.source_digest(src)
        pep_bench.QFILE = os.path.join(self.tmp, 'questions.json')
        self.cache = os.path.join(self.tmp, 'cache')

    def tearDown(self):
        pep_bench.peps.ARCHIVE_URL, pep_bench.peps.SOURCE_DIGEST, pep_bench.QFILE = self.saved
        shutil.rmtree(self.tmp)

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pep_bench.main(['--cache', self.cache] + list(argv))
        return rc, err.getvalue()

    def test_fetch_questions_run_and_checks(self):
        rc, err = self.main('--fetch-only')
        self.assertEqual(rc, 0, err)
        self.assertIn('fetched', err)
        cached = os.path.join(self.cache, 'peps-src-' + peps.PINNED_SHA[:12])
        self.assertEqual(sorted(os.listdir(cached)), ['pep-%04d.rst' % n for n in sorted(TINY)])

        # the question file of a new pin, then the frozen check
        rc, err = self.main('--offline', '--write-questions')
        self.assertEqual(rc, 0, err)
        with open(pep_bench.QFILE, encoding='ascii') as f:
            qd = json.load(f)
        ids = [q['id'] for q in qd['questions']]
        self.assertEqual(ids, ['q1-0101', 'q1-0102', 'q1-0103', 'q1-0105', 'q2-0101-0102', 'q2c-0101-0102'])
        self.assertEqual(qd['counts']['left_out_by_license'], 1)
        self.assertEqual(qd['source']['commit'], peps.PINNED_SHA)

        out = os.path.join(self.tmp, 'out')
        rc, err = self.main('--offline', '--systems', 'bm25,leks', '--no-link-prior', '--out', out)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(out, 'pep_results.json'), encoding='ascii') as f:
            rep = json.load(f)
        with open(pep_bench.QFILE, 'rb') as f:
            self.assertEqual(rep['meta']['questions_sha256'], hashlib.sha256(f.read()).hexdigest())
        for n in ('pep_results.json', 'pep_results.md'):
            with open(os.path.join(out, n), 'rb') as f:
                self.assertNotIn(b'\r', f.read(), n)
        self.assertEqual(rep['meta']['notes'], 4)
        for name in ('bm25', 'leks'):
            s = rep['systems'][name]
            self.assertEqual(s['status'], 'ok', s)
            self.assertEqual(rep['per_question'][name]['q1']['q1-0103'], 1)
            self.assertEqual(s['q2']['n'], 1)
        leks = rep['systems']['leks']
        self.assertEqual(leks['indexed'], 4)
        # the old note names the new one, so the index holds that edge and the pair is covered
        self.assertEqual((leks['edges']['pairs_covered'], leks['edges']['pairs']), (1, 1))
        with open(os.path.join(out, 'pep_results.md'), encoding='ascii') as f:
            md = f.read()
        self.assertIn('## Replacement, the new PEP as the answer', md)
        self.assertNotIn(self.tmp, md)

        # a system that was asked for and skipped (no --leks30 folder) fails the run, the files are still written
        out2 = os.path.join(self.tmp, 'out2')
        rc, err = self.main('--offline', '--systems', 'bm25,leks30', '--no-link-prior', '--out', out2)
        self.assertEqual(rc, 1, err)
        self.assertIn('asked for but not run: leks30', err)
        with open(os.path.join(out2, 'pep_results.json'), encoding='ascii') as f:
            self.assertEqual(json.load(f)['systems']['leks30']['status'], 'skipped')
        # so does a link prior that cannot run at this --k
        rc, err = self.main('--offline', '--systems', 'bm25,leks', '--k', '5', '--out', out2)
        self.assertEqual(rc, 1, err)
        self.assertIn('asked for but not run: link prior', err)

        # a changed question file stops the run
        with open(pep_bench.QFILE, 'a', encoding='ascii') as f:
            f.write(' ')
        rc, err = self.main('--offline', '--systems', 'bm25', '--no-link-prior', '--out', out)
        self.assertEqual(rc, 2)
        self.assertIn('differ', err)

        # a changed source in the cache fails the digest
        with open(os.path.join(cached, 'pep-0103.rst'), 'a', encoding='utf-8') as f:
            f.write('one more line\n')
        rc, err = self.main('--offline', '--fetch-only')
        self.assertEqual(rc, 2)
        self.assertIn('pinned digest', err)

    def test_archive_with_other_files_fails_the_digest(self):
        pep_bench.peps.SOURCE_DIGEST = '0' * 64
        rc, err = self.main('--fetch-only')
        self.assertEqual(rc, 2)
        self.assertIn('do not match the pinned digest', err)
        self.assertFalse(os.path.isdir(os.path.join(self.cache, 'peps-src-' + peps.PINNED_SHA[:12])))


def no_unix_sockets():
    """True on windows python, which has no AF_UNIX."""
    return os.name != 'posix' or not hasattr(socket, 'AF_UNIX')


def short_tmp():
    """/tmp on posix, where a unix socket path has to stay short; the normal temp dir elsewhere."""
    return '/tmp' if os.name == 'posix' else None


def unix_sockets_blocked():
    """True when this machine has no unix sockets or refuses one in a short temp dir (some local sandboxes do)."""
    if no_unix_sockets():
        return True
    d = tempfile.mkdtemp(prefix='lk', dir=short_tmp())
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(os.path.join(d, 's'))
        return False
    except OSError:
        return True
    finally:
        s.close()
        shutil.rmtree(d, ignore_errors=True)


class ModelArmWithFakeBackend(unittest.TestCase):
    """The leks+models path of the worker: daemon up, every passage embedded, strict search. Fake models."""

    def setUp(self):
        if importlib.util.find_spec('numpy') is None:
            self.skipTest('numpy not installed, the fake backend needs it')
        if no_unix_sockets():
            self.skipTest('no unix sockets on this system; tests.test_transport covers the tcp daemon')
        if unix_sockets_blocked():
            self.skipTest('the local sandbox blocks unix sockets, the model daemon needs one; CI runs this test')

    def test_worker_runs_the_model_lanes(self):
        rb = _load('t_pub_run_bench', os.path.join(REPO, 'bench', 'run_bench.py'))
        tmp = tempfile.mkdtemp(prefix='lk', dir=short_tmp())
        try:
            notes = os.path.join(tmp, 'notes', 'peps')
            os.makedirs(notes)
            for n in (101, 102, 103):
                with open(os.path.join(notes, 'pep-%04d.md' % n), 'w', encoding='utf-8') as f:
                    f.write(peps.convert(tiny_source(n), 'pep-%04d.rst' % n)[1])
            home = os.path.join(tmp, 'home')
            cfg = dict(pep_bench.pep_config(), home=home,
                       roots=[{'name': 'peps', 'path': notes, 'kind': 'notes'}])
            cpath, jpath, opath = (os.path.join(tmp, x) for x in ('config.json', 'job.json', 'out.json'))
            with open(cpath, 'w', encoding='utf-8') as f:
                json.dump(cfg, f)
            fake = {'backend': 'fake', 'device': 'auto', 'embedder': dict(rb.FAKE_EMBEDDER),
                    'reranker': dict(rb.FAKE_RERANKER)}
            with open(jpath, 'w', encoding='utf-8') as f:
                json.dump({'leks_root': REPO, 'profile': 'peps', 'k': 5,
                           'models': {'weights_dir': os.path.join(tmp, 'w'), 'config': fake},
                           'questions': [{'id': 'a', 'question': 'release schedule rules'}]}, f)
            env = {a: b for a, b in os.environ.items() if not a.startswith(('LEKS_', 'PYTHON'))}
            env.update({'LEKS_HOME': home, 'LEKS_CONFIG': cpath, 'LEKS_ALLOW_FAKE': '1', 'PYTHONPATH': REPO})
            r = subprocess.run([sys.executable, '-B', os.path.join(PUB, 'leks_worker.py'), jpath, opath],
                               env=env, cwd=tmp, capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            with open(opath, encoding='utf-8') as f:
                out = json.load(f)
            self.assertNotEqual(out.get('status'), 'skipped', out)
            self.assertEqual(out['embed']['rc'], 0)
            self.assertNotIn('words only', out['lanes'])
            self.assertEqual(out['results']['a']['hits'][0]['path'], 'peps/pep-0103.md')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class WindowsGuards(unittest.TestCase):
    def test_model_arm_skips_without_af_unix(self):
        # windows python has no socket.AF_UNIX: the daemon test has to skip there, never error
        fake = types.ModuleType('socket')
        for k in dir(socket):
            if k != 'AF_UNIX':
                setattr(fake, k, getattr(socket, k))
        t = ModelArmWithFakeBackend('test_worker_runs_the_model_lanes')
        with mock.patch.object(sys.modules[__name__], 'socket', fake), \
                mock.patch.object(importlib.util, 'find_spec', return_value=object()):
            with self.assertRaises(unittest.SkipTest):
                t.setUp()


if __name__ == '__main__':
    unittest.main()
