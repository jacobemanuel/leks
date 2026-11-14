"""Tests for leks.docs: pdf, docx, OCR through a fake helper, limits. Offline."""
import importlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

from leks import docs

try:
    from tests.pdfgen import write_pdf
except ImportError:  # run from inside the tests folder
    from pdfgen import write_pdf

try:
    importlib.import_module('pypdf')
    HAVE_PYPDF = True
except ImportError:
    HAVE_PYPDF = False

# fake helper: reads paths on stdin, prints the contract json, one object per line
FAKE_HELPER = r'''#!%(py)s
import json, os, sys, time
calls = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calls.log')
paths = [l.strip() for l in sys.stdin if l.strip()]
with open(calls, 'a') as f:
    f.write(json.dumps({'paths': paths, 'max_pages': os.environ.get('LEKS_OCR_MAX_PAGES')}) + '\n')
MODE = %(mode)r
for p in paths:
    base = os.path.basename(p)
    o = {'path': p, 'kind': 'image-ocr', 'pages': 1, 'chars': 0, 'lines': 2, 'mean_confidence': 0.87,
         'low_confidence_lines': 1, 'seconds': 0.01, 'text': '', 'upscaled': False, 'error': None}
    if MODE == 'hang':
        time.sleep(60)
    if base.startswith('leak'):
        o['error'] = 'Traceback in /private/place: secret detail'
    elif base.startswith('bad'):
        o['error'] = 'unreadable image'
    elif base.startswith('blank'):
        o['text'] = ''
        o['lines'] = 0
    elif base.endswith('.pdf'):
        o['kind'] = 'pdf-ocr'
        o['pages'] = 2
        o['text'] = ('Scanned report about tile caching and frame pacing in the renderer.\n'
                     'The cache keeps the newest tiles and drops the oldest ones first.'
                     '\f'
                     'Second sheet explains how the build system bundles shader assets '
                     'before every release.')
    else:
        o['text'] = 'Build cache report\nrenderer frame time 12 ms'
    o['chars'] = len(o['text'])
    print(json.dumps(o))
    sys.stdout.flush()
'''

DOC_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Frame graph guide</w:t></w:r></w:p>'
    '<w:p><w:r><w:t>The frame graph orders passes</w:t></w:r><w:r><w:tab/><w:t>by dependency.</w:t></w:r></w:p>'
    '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Caching</w:t></w:r></w:p>'
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell one</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    '<w:p></w:p>'
    '</w:body></w:document>')


# the helper runs only on posix systems: docs.helper_available says no on windows
posix_helper = unittest.skipUnless(os.name == 'posix', 'the OCR helper runs only on posix systems')


def make_docx(path, xml=DOC_XML, extra=None):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', xml)
        for name, data in (extra or {}).items():
            z.writestr(name, data)


def make_cfg(home, mode='ok', ocr=None, limits=None, helper=True):
    """A config-like object with just what docs.py reads. The helper is a script in home/bin."""
    bindir = os.path.join(home, 'bin')
    os.makedirs(bindir, exist_ok=True)
    hp = os.path.join(bindir, 'extract_text')
    if not helper and os.path.exists(hp):
        os.remove(hp)
    if helper:
        with open(hp, 'w') as f:
            f.write(FAKE_HELPER % {'py': sys.executable, 'mode': mode})
        os.chmod(hp, os.stat(hp).st_mode | stat.S_IXUSR)
    o = {'enabled': 'auto', 'images': True, 'helper': None, 'max_pages': 40}
    o.update(ocr or {})
    lim = {'pdf_max_bytes': 209715200, 'office_max_bytes': 419430400}
    lim.update(limits or {})
    return SimpleNamespace(home=home, ocr=o, limits=lim, helper_path=lambda: hp)


def calls(home):
    p = os.path.join(home, 'bin', 'calls.log')
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='lkd')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = make_cfg(self.tmp)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def touch(self, name, data=b'x'):
        p = self.path(name)
        with open(p, 'wb') as f:
            f.write(data)
        return p


class CleaningTests(unittest.TestCase):
    def test_ligatures_and_control_chars(self):
        out = docs.clean_pages(['o\ufb03ce \ufb01le\x07 se\u202ecret\x00 end'])
        self.assertEqual(out, ['office file secret end'])

    def test_hyphen_join(self):
        pages = ['The exam-\nple shows a join.', 'Another line here.']
        out = docs.clean_pages(pages)[0]
        self.assertIn('example', out)
        self.assertNotIn('exam-', out)

    def test_hyphen_kept_when_text_uses_it(self):
        pages = ['The data-\nflow graph and the data-flow model.', 'More text about data-flow.']
        out = docs.clean_pages(pages)
        self.assertIn('data-flow', out[0])
        self.assertNotIn('dataflow', out[0])

    def test_hyphen_join_across_pages(self):
        out = docs.clean_pages(['The shader compi-', 'ler runs first.'])
        self.assertIn('compiler', out[0] + out[1])

    def test_running_header_and_footer_dropped(self):
        pages = []
        for i in range(1, 7):
            body = '\n'.join('Body line %d of sheet %d has enough words in it.' % (k, i) for k in range(6))
            pages.append('Frame Graph Guide\n' + body + '\nPage %d of 6' % i)
        out = docs.clean_pages(pages)
        self.assertEqual(out[0].split('\n')[0], 'Frame Graph Guide')  # page one keeps its top
        for p in out[1:]:
            self.assertNotIn('Frame Graph Guide', p)
        for p in out:
            self.assertNotIn('Page ', p)

    def test_short_file_keeps_header_but_loses_page_number(self):
        out = docs.clean_pages(['Same Title\ntext one here\n3', 'Same Title\ntext two here\n4'])
        self.assertIn('Same Title', out[1])
        self.assertNotIn('\n4', out[1])

    def test_usable(self):
        self.assertTrue(docs.usable('The cache drops the oldest tiles first.'))
        self.assertFalse(docs.usable(''))
        self.assertFalse(docs.usable('   \n '))
        self.assertFalse(docs.usable('#$%^&*()_+ {}|<> 123 456 ###'))

    def test_find_refs_and_chunks(self):
        pages = ['Intro text about tile caches.\n\nMore intro.',
                 '2. Method\n\nWe measure frame time per pass.',
                 'Results are in the table.',
                 'Discussion of the results.',
                 'References\n[1] A. Author, Tile caches, 2020.\n[2] B. Author, Frame graphs, 2021.']
        refs = docs.find_refs(pages)
        self.assertEqual(refs, (4, 0))
        ch = docs.chunks_of_pages(pages, refs)
        for first, last, _h, _t in ch:
            self.assertEqual(first, last)  # one page per passage
        self.assertEqual(ch[-1][2], 'References')
        self.assertTrue(any(h == '2. Method' for _, _, h, _ in ch))
        self.assertEqual([c[3] for c in ch if c[0] == 1][0].startswith('Intro'), True)

    def test_no_refs(self):
        self.assertIsNone(docs.find_refs(['plain text', 'more plain text']))

    def test_long_paragraph_is_cut(self):
        ch = docs.chunks_of_pages(['word ' * 600], None, 1000)
        self.assertGreater(len(ch), 2)
        self.assertTrue(all(len(c[3]) <= 1000 for c in ch))

    def test_appendix_ends_reference_list(self):
        pages = ['a', 'b', 'c', 'References\n[1] Some entry here.\n\nAppendix\n\nTable of settings.']
        refs = docs.find_refs(pages)
        ch = docs.chunks_of_pages(pages, refs)
        self.assertEqual(ch[-1][2], 'Appendix')

    def test_chunks_of_extracted_paragraphs(self):
        ex = {'unit': 'paragraph', 'title': 't', 'extractor': 'docx', 'version': 1, 'warning': None,
              'units': [(1, 'A', 'one'), (2, 'A', 'two'), (3, 'B', 'three'), (4, 'B', 'x' * 1500)]}
        ch = docs.chunks_of_extracted(ex, 1000)
        self.assertEqual(ch[0], (1, 2, 'A', 'one\ntwo'))
        self.assertEqual(ch[1][:3], (3, 3, 'B'))
        self.assertTrue(all(c[0] == c[1] or c[2] for c in ch))
        self.assertTrue(all(len(c[3]) <= 1000 for c in ch))


@unittest.skipUnless(HAVE_PYPDF, 'pypdf not installed')
class PdfTests(Base):
    def make_pdf(self, name='report.pdf', n=6):
        nouns = ('cache tile shader mesh buffer texture queue scheduler profiler allocator compiler linker '
                 'bundle manifest sampler uploader decoder encoder lighting culling batcher atlas mipmap '
                 'stencil raster fence barrier heap arena pool ring').split()
        pages = []
        for i in range(1, n + 1):
            lines = ['The %s stage owns its own %s and reports to the frame graph.' % (
                nouns[(5 * i + k) % len(nouns)], nouns[(5 * i + k + 7) % len(nouns)]) for k in range(5)]
            body = 'Renderer Notes\n' + '\n'.join(lines) + '\nPage %d' % i
            if i == n:
                body = ('Renderer Notes\nReferences\n[1] A. Author, Tile caches, 2020.\n'
                        '[2] B. Author, Frame graphs, 2021.\nPage %d' % i)
            pages.append(body)
        p = self.path(name)
        write_pdf(p, pages)
        return p

    def test_pdfgen_is_valid(self):
        p = self.make_pdf()
        with open(p, 'rb') as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b'%PDF-1.4'))
        self.assertTrue(raw.rstrip().endswith(b'%%EOF'))
        # startxref points at the xref keyword, every entry at its object
        start = int(raw.rsplit(b'startxref\n', 1)[1].split(b'\n')[0])
        self.assertEqual(raw[start:start + 4], b'xref')
        lines = raw[start:].split(b'\n')
        count = int(lines[1].split()[1])
        for num in range(1, count):
            off = int(lines[2 + num][:10])
            self.assertTrue(raw[off:].startswith(b'%d 0 obj' % num))

    def test_pdf_pages(self):
        p = self.make_pdf()
        pages = docs.pdf_pages(p)
        self.assertEqual(len(pages), 6)
        self.assertIn('frame graph', pages[0])

    def test_pdf_pages_bad_file(self):
        self.assertIsNone(docs.pdf_pages(self.touch('broken.pdf', b'not a pdf at all')))

    def test_extract_text_layer(self):
        ex = docs.extract(self.cfg, self.make_pdf(), '.pdf')
        self.assertEqual(ex['extractor'], 'pypdf')
        self.assertEqual(ex['unit'], 'page')
        self.assertEqual([u[0] for u in ex['units']], [1, 2, 3, 4, 5, 6])
        self.assertIsNone(ex['warning'])
        self.assertEqual(ex['version'], docs.EXTRACTOR_VERSION)
        # running header and page numbers are gone from the later pages
        self.assertNotIn('Renderer Notes', ex['units'][2][2])
        self.assertNotIn('Page 3', ex['units'][2][2])
        self.assertEqual(ex['units'][5][1], 'References')
        self.assertEqual(calls(self.tmp), [])  # text layer never needs the helper

    def test_passages_cite_one_page(self):
        ex = docs.extract(self.cfg, self.make_pdf(), '.pdf')
        ch = docs.chunks_of_extracted(ex)
        self.assertTrue(ch)
        self.assertTrue(all(a == b for a, b, _h, _t in ch))
        self.assertEqual(ch[-1][2], 'References')

    @posix_helper
    def test_scanned_pdf_goes_to_helper(self):
        p = self.path('scan.pdf')
        write_pdf(p, ['', ''])
        ex = docs.extract(self.cfg, p, '.pdf')
        self.assertEqual(ex['extractor'], 'ocr')
        self.assertEqual(ex['unit'], 'page')
        self.assertEqual([u[0] for u in ex['units']], [1, 2])
        self.assertIn('tile caching', ex['units'][0][2])
        self.assertIn('shader assets', ex['units'][1][2])
        self.assertTrue(ex['warning'].startswith('text from OCR, mean confidence 0.87'))
        self.assertEqual(len(calls(self.tmp)), 1)
        self.assertEqual(calls(self.tmp)[0]['max_pages'], '40')

    def test_scanned_pdf_without_helper_is_none(self):
        cfg = make_cfg(self.tmp, helper=False)
        p = self.path('scan.pdf')
        write_pdf(p, ['', ''])
        self.assertIsNone(docs.extract(cfg, p, '.pdf'))
        self.assertEqual(docs.skip_reason(cfg, p, '.pdf'), 'no usable text')

    @posix_helper
    def test_needs_ocr(self):
        scan = self.path('scan.pdf')
        write_pdf(scan, [''])
        self.assertTrue(docs.needs_ocr(self.cfg, scan, '.pdf'))
        self.assertFalse(docs.needs_ocr(self.cfg, self.make_pdf(), '.pdf'))

    def test_pdf_size_bound(self):
        cfg = make_cfg(self.tmp, limits={'pdf_max_bytes': 100})
        self.assertIsNone(docs.extract(cfg, self.make_pdf(), '.pdf'))
        self.assertEqual(docs.skip_reason(cfg, self.make_pdf(), '.pdf'), 'over size limit')

    @posix_helper
    def test_batch_result_is_used(self):
        scan = self.path('scan.pdf')
        write_pdf(scan, [''])
        pre = {scan: {'path': scan, 'kind': 'pdf-ocr', 'pages': 1, 'text': 'Prepared text about shader bundles.',
                      'mean_confidence': 0.5, 'low_confidence_lines': 0, 'error': None}}
        ex = docs.extract(self.cfg, scan, '.pdf', ocr=pre)
        self.assertIn('shader bundles', ex['units'][0][2])
        self.assertEqual(calls(self.tmp), [])  # the caller's batch is used, no second run


class NoPypdfTests(Base):
    def test_no_pypdf_no_ocr_is_name_only(self):
        cfg = make_cfg(self.tmp, ocr={'enabled': 'off'})
        p = self.touch('a.pdf', b'%PDF-1.4 stub')
        with mock.patch.dict(sys.modules, {'pypdf': None}):
            self.assertIsNone(docs.pdf_pages(p))
            self.assertIsNone(docs.extract(cfg, p, '.pdf'))
            with mock.patch.object(docs, '_pypdf_installed', return_value=False):
                self.assertEqual(docs.skip_reason(cfg, p, '.pdf'), 'no pdf text extractor')

    @posix_helper
    def test_no_pypdf_uses_helper(self):
        p = self.touch('b.pdf', b'%PDF-1.4 stub')
        with mock.patch.dict(sys.modules, {'pypdf': None}):
            ex = docs.extract(self.cfg, p, '.pdf')
        self.assertEqual(ex['extractor'], 'ocr')
        self.assertEqual(len(ex['units']), 2)


class DocxTests(Base):
    def test_paragraphs(self):
        p = self.path('guide.docx')
        make_docx(p)
        paras = docs.docx_paragraphs(p, {})
        self.assertEqual(paras[0], 'Frame graph guide')
        self.assertEqual(paras[1], 'The frame graph orders passes by dependency.')
        self.assertIn('cell one', paras)
        self.assertEqual(len(paras), 4)  # the empty paragraph is dropped

    def test_extract(self):
        p = self.path('guide.docx')
        make_docx(p)
        ex = docs.extract(self.cfg, p, '.docx')
        self.assertEqual(ex['unit'], 'paragraph')
        self.assertEqual(ex['extractor'], 'docx')
        self.assertEqual(ex['title'], 'Frame graph guide')
        self.assertEqual([u[0] for u in ex['units']], [1, 2, 3, 4])
        self.assertEqual(ex['units'][1][1], 'Frame graph guide')
        self.assertEqual(ex['units'][3][1], 'Caching')
        self.assertIsNone(ex['warning'])

    def test_docx_does_not_need_anything_else(self):
        cfg = make_cfg(self.tmp, helper=False, ocr={'enabled': 'off'})
        p = self.path('guide.docx')
        make_docx(p)
        self.assertIsNotNone(docs.extract(cfg, p, '.docx'))
        self.assertTrue(docs.capabilities(cfg)['docx'])

    def test_not_a_zip(self):
        p = self.touch('broken.docx', b'plain bytes, no archive')
        with self.assertRaises(ValueError):
            docs.docx_paragraphs(p, {})
        self.assertIsNone(docs.extract(self.cfg, p, '.docx'))

    def test_dtd_refused(self):
        xml = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "boom">]>'
               '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>')
        p = self.path('dtd.docx')
        make_docx(p, xml)
        with self.assertRaises(ValueError):
            docs.docx_paragraphs(p, {})
        self.assertIsNone(docs.extract(self.cfg, p, '.docx'))

    def test_other_encoding_refused(self):
        xml = DOC_XML.replace('UTF-8', 'UTF-16')
        p = self.path('enc.docx')
        make_docx(p, xml)
        with self.assertRaises(ValueError):
            docs.docx_paragraphs(p, {})

    def test_broken_xml(self):
        p = self.path('xml.docx')
        make_docx(p, '<w:document><w:p>')
        self.assertIsNone(docs.extract(self.cfg, p, '.docx'))

    def test_zip_member_count_bound(self):
        p = self.path('many.docx')
        make_docx(p, extra={'junk/%d.txt' % i: 'x' for i in range(30)})
        with self.assertRaises(ValueError):
            docs.docx_paragraphs(p, {'zip_max_members': 10})
        self.assertTrue(docs.docx_paragraphs(p, {'zip_max_members': 100}))

    def test_zip_member_size_bound(self):
        p = self.path('big.docx')
        make_docx(p, DOC_XML.replace('cell one', 'x' * 5000))
        with self.assertRaises(ValueError):
            docs.docx_paragraphs(p, {'zip_max_member': 1000})

    def test_zip_total_bound(self):
        p = self.path('tot.docx')
        make_docx(p)
        with self.assertRaises(ValueError):
            docs.docx_paragraphs(p, {'zip_max_total': 50})

    def test_size_bound(self):
        cfg = make_cfg(self.tmp, limits={'office_max_bytes': 100})
        p = self.path('guide.docx')
        make_docx(p)
        self.assertIsNone(docs.extract(cfg, p, '.docx'))
        self.assertEqual(docs.skip_reason(cfg, p, '.docx'), 'over size limit')

    def test_encrypted_member(self):
        p = self.path('enc.docx')
        make_docx(p)
        # flip the encrypted flag in the central directory entry of word/document.xml
        with open(p, 'rb') as f:
            raw = bytearray(f.read())
        i = raw.rfind(b'PK\x01\x02')
        while i != -1:
            if raw[i + 46:i + 46 + 17] == b'word/document.xml':
                raw[i + 8] |= 1
            i = raw.rfind(b'PK\x01\x02', 0, i)
        with open(p, 'wb') as f:
            f.write(bytes(raw))
        with self.assertRaises(ValueError):
            docs.docx_paragraphs(p, {})


@posix_helper
class OcrTests(Base):
    def test_image_through_helper(self):
        p = self.touch('shot.png')
        ex = docs.extract(self.cfg, p, '.png')
        self.assertEqual(ex['unit'], 'image')
        self.assertEqual(ex['extractor'], 'ocr')
        self.assertEqual(ex['units'], [(1, 'OCR text', 'Build cache report\nrenderer frame time 12 ms')])
        self.assertEqual(ex['warning'], 'text from OCR, mean confidence 0.87, 1 low confidence lines')
        self.assertEqual(ex['title'], 'shot')

    def test_every_image_extension(self):
        for ext in docs.IMAGE_EXT:
            p = self.touch('shot' + ext)
            self.assertIsNotNone(docs.extract(self.cfg, p, ext.upper()), ext)

    def test_images_skipped_when_ocr_off(self):
        cfg = make_cfg(self.tmp, ocr={'enabled': 'off'})
        p = self.touch('shot.png')
        self.assertIsNone(docs.extract(cfg, p, '.png'))
        self.assertFalse(docs.capabilities(cfg)['ocr'])
        self.assertFalse(docs.capabilities(cfg)['images'])
        self.assertEqual(docs.skip_reason(cfg, p, '.png'), 'ocr off or helper not built')
        self.assertEqual(calls(self.tmp), [])  # the helper never ran

    def test_images_skipped_when_images_flag_off(self):
        cfg = make_cfg(self.tmp, ocr={'images': False})
        p = self.touch('shot.png')
        self.assertIsNone(docs.extract(cfg, p, '.png'))
        self.assertFalse(docs.capabilities(cfg)['images'])
        self.assertTrue(docs.capabilities(cfg)['ocr'])

    def test_images_skipped_without_helper(self):
        cfg = make_cfg(self.tmp, helper=False)
        self.assertFalse(docs.helper_available(cfg))
        self.assertIsNone(docs.extract(cfg, self.touch('shot.png'), '.png'))
        self.assertEqual(docs.ocr_batch(cfg, ['/x/a.png'])['/x/a.png']['error'], 'ocr unavailable')

    def test_blank_image_has_no_text(self):
        self.assertIsNone(docs.extract(self.cfg, self.touch('blank.png'), '.png'))

    def test_unreadable_image(self):
        self.assertIsNone(docs.extract(self.cfg, self.touch('bad.png'), '.png'))

    def test_error_text_is_not_kept(self):
        p = self.touch('leak.png')
        r = docs.ocr_batch(self.cfg, [p])[p]
        self.assertEqual(r['error'], 'helper error')
        self.assertNotIn('secret', json.dumps(r))

    def test_one_helper_run_for_a_batch(self):
        ps = [self.touch('shot%d.png' % i) for i in range(4)]
        res = docs.ocr_batch(self.cfg, ps)
        self.assertEqual(set(res), set(ps))
        self.assertEqual(len(calls(self.tmp)), 1)
        self.assertEqual(calls(self.tmp)[0]['paths'], ps)
        self.assertEqual(res[ps[0]]['kind'], 'image-ocr')
        self.assertAlmostEqual(res[ps[0]]['mean_confidence'], 0.87)

    def test_batch_result_feeds_extract(self):
        p = self.touch('shot.png')
        res = docs.ocr_batch(self.cfg, [p])
        self.assertIsNotNone(docs.extract(self.cfg, p, '.png', ocr=res))
        self.assertEqual(len(calls(self.tmp)), 1)

    def test_max_pages_passed_to_helper(self):
        cfg = make_cfg(self.tmp, ocr={'max_pages': 7})
        docs.ocr_batch(cfg, [self.touch('shot.png')])
        self.assertEqual(calls(self.tmp)[0]['max_pages'], '7')

    def test_bad_path_not_sent(self):
        res = docs.ocr_batch(self.cfg, ['/tmp/a\nb.png', '  '])
        self.assertEqual(res['/tmp/a\nb.png']['error'], 'bad path')
        self.assertEqual(res['  ']['error'], 'bad path')
        self.assertEqual(calls(self.tmp), [])

    def test_timeout_kills_the_helper(self):
        cfg = make_cfg(self.tmp, mode='hang')
        p = self.touch('shot.png')
        t0 = time.time()
        res = docs.ocr_batch(cfg, [p], timeout=1.0)
        self.assertLess(time.time() - t0, 20)
        self.assertEqual(res[p]['error'], 'timeout')

    def test_garbage_lines_are_ignored(self):
        hp = self.cfg.helper_path()
        with open(hp, 'w') as f:
            f.write('#!%s\nimport sys\nsys.stdin.read()\nprint("not json")\nprint("[1,2]")\n'
                    'print(\'{"path": "/other/file.png", "text": "x"}\')\n' % sys.executable)
        p = self.touch('shot.png')
        res = docs.ocr_batch(self.cfg, [p])
        self.assertEqual(res[p]['error'], 'no result')
        self.assertNotIn('/other/file.png', res)

    def test_helper_with_no_form_feed(self):
        scan = self.path('scan.pdf')
        write_pdf(scan, ['', ''])
        pre = {scan: {'path': scan, 'kind': 'pdf-ocr', 'pages': 3,
                      'text': 'All the text of the scan comes as one block of words.',
                      'mean_confidence': 0.9, 'low_confidence_lines': 0, 'error': None}}
        ex = docs.extract(self.cfg, scan, '.pdf', ocr=pre)
        self.assertEqual(len(ex['units']), 1)
        self.assertIn('page breaks unknown', ex['warning'])

    def test_page_limit_noted(self):
        cfg = make_cfg(self.tmp, ocr={'max_pages': 2})
        scan = self.path('scan.pdf')
        write_pdf(scan, ['', ''])
        pre = {scan: {'path': scan, 'kind': 'pdf-ocr', 'pages': 9,
                      'text': 'First sheet has words in it.\fSecond sheet has words too.',
                      'mean_confidence': 0.9, 'low_confidence_lines': 0, 'error': None}}
        ex = docs.extract(cfg, scan, '.pdf', ocr=pre)
        self.assertIn('first 2 of 9 pages', ex['warning'])

    def test_capabilities_shape(self):
        caps = docs.capabilities(self.cfg)
        self.assertEqual(set(caps), {'pdf_text', 'ocr', 'docx', 'images'})
        self.assertTrue(caps['ocr'] and caps['images'] and caps['docx'])
        self.assertEqual(caps['pdf_text'], HAVE_PYPDF)

    def test_unknown_type_is_none(self):
        self.assertIsNone(docs.extract(self.cfg, self.touch('a.xyz'), '.xyz'))
        self.assertIsNone(docs.extract(self.cfg, self.path('missing.pdf'), '.pdf'))


class BuildHelperTests(Base):
    def fake_swiftc(self, code):
        p = self.path('fake_swiftc')
        with open(p, 'w') as f:
            f.write('#!%s\nimport sys\nargs = sys.argv[1:]\n'
                    'assert args[0] == "-O" and args[2] == "-o", args\n'
                    'assert args[1].endswith("extract_text.swift"), args\n'
                    'out = args[3]\n%s\n' % (sys.executable, code))
        os.chmod(p, 0o755)
        return p

    def test_not_macos(self):
        with mock.patch.object(docs.sys, 'platform', 'linux'):
            ok, msg = docs.build_helper(self.cfg)
        self.assertFalse(ok)
        self.assertIn('macOS', msg)

    def test_no_swiftc(self):
        with mock.patch.object(docs.sys, 'platform', 'darwin'), \
                mock.patch.object(docs.shutil, 'which', return_value=None):
            ok, msg = docs.build_helper(self.cfg)
        self.assertFalse(ok)
        self.assertIn('swiftc', msg)

    @posix_helper
    def test_build_ok_and_failure(self):
        cfg = make_cfg(self.tmp, helper=False)
        good = self.fake_swiftc('open(out, "w").write("#!/bin/sh\\n")')
        with mock.patch.object(docs.sys, 'platform', 'darwin'), \
                mock.patch.object(docs.shutil, 'which', return_value=good):
            ok, msg = docs.build_helper(cfg)
        self.assertTrue(ok, msg)
        self.assertTrue(docs.helper_available(cfg))
        self.assertEqual([n for n in os.listdir(os.path.join(self.tmp, 'bin')) if '.tmp' in n], [])
        bad = self.fake_swiftc('open(out, "w").write("partial")\nsys.exit(2)')
        cfg2 = make_cfg(os.path.join(self.tmp, 'other'), helper=False)
        with mock.patch.object(docs.sys, 'platform', 'darwin'), \
                mock.patch.object(docs.shutil, 'which', return_value=bad):
            ok, msg = docs.build_helper(cfg2)
        self.assertFalse(ok)
        self.assertEqual(msg, 'swiftc failed with exit code 2')
        self.assertFalse(docs.helper_available(cfg2))
        self.assertEqual(os.listdir(os.path.join(self.tmp, 'other', 'bin')), [])

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'needs macOS with swiftc')
    def test_real_build(self):
        cfg = make_cfg(self.tmp, helper=False)
        ok, msg = docs.build_helper(cfg)
        self.assertTrue(ok, msg)
        self.assertTrue(docs.helper_available(cfg))


class SourceTests(unittest.TestCase):
    def test_swift_header_and_build_line(self):
        src = os.path.join(os.path.dirname(docs.__file__), 'ocr', 'extract_text.swift')
        with open(src, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('swiftc -O leks/ocr/extract_text.swift -o <LEKS_HOME>/bin/extract_text', text)
        self.assertIn('form feed', text)
        for key in ('path', 'kind', 'pages', 'chars', 'lines', 'mean_confidence', 'low_confidence_lines',
                    'seconds', 'text', 'upscaled', 'error'):
            self.assertIn('var %s' % key, text)


if __name__ == '__main__':
    unittest.main()
