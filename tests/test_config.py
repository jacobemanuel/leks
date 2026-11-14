"""Tests for leks.config and the command line dispatch."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import unittest
from unittest import mock

from leks import __main__ as cli
from leks import config
from leks.config import ConfigError, Root

from tests import helpers

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# a posix path as the config reports it: unchanged on posix, with the drive and backslashes on windows
N = os.path.abspath


def clean_env(**extra):
    """Patch os.environ with every LEKS_* variable removed, plus extras."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('LEKS_')}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


def base() -> dict:
    d = config.default_dict()
    d['home'] = '/tmp/leks-test-home'
    return d


class Defaults(unittest.TestCase):
    def setUp(self):
        p = clean_env()
        p.start()
        self.addCleanup(p.stop)

    def test_template_loads(self):
        cfg = config.from_dict(base())
        self.assertEqual([r.name for r in cfg.roots], ['notes', 'code', 'docs', 'misc'])
        self.assertEqual(cfg.profiles['default'], ['notes', 'code', 'docs', 'misc'])
        self.assertEqual(cfg.profiles['architect'], ['notes', 'docs'])
        self.assertEqual(cfg.default_profile, 'default')
        self.assertEqual(cfg.backend, 'torch')
        self.assertEqual(cfg.device, 'auto')

    def test_example_file_matches_template(self):
        with open(os.path.join(REPO, 'examples', 'config.example.json'), encoding='utf-8') as f:
            ex = config.parse_text(f.read())
        self.assertEqual(ex, config.default_dict())
        config.from_dict(ex)

    def test_minimal_config(self):
        cfg = config.from_dict({'version': 3, 'roots': [{'name': 'a', 'path': '/tmp/x', 'kind': 'notes'}]})
        self.assertEqual(list(cfg.profiles), ['default'])
        self.assertEqual(cfg.budgets['embed'], 45.0)
        self.assertEqual(cfg.limits['chunk_chars'], 1000)
        self.assertTrue(cfg.server['always_load'])
        self.assertEqual(cfg.embedder.key, 'mdense')
        self.assertEqual(cfg.reranker.key, 'mlate')

    def test_include_defaults_by_kind(self):
        inc = config.DEFAULT_INCLUDE
        self.assertIn('**/*.md', inc['notes'])
        self.assertIn('**/*.html', inc['notes'])
        self.assertNotIn('**/*.py', inc['notes'])
        self.assertIn('**/*.py', inc['code'])
        self.assertIn('**/*.json', inc['code'])
        self.assertIn('**/*.yaml', inc['code'])
        self.assertIn('**/*.pdf', inc['docs'])
        self.assertIn('**/*.png', inc['docs'])
        self.assertEqual(set(inc['mixed']), set(inc['notes']) | set(inc['code']) | set(inc['docs']))
        self.assertEqual(len(inc['mixed']), len(set(inc['mixed'])))
        cfg = config.from_dict({'version': 3, 'roots': [
            {'name': 'c', 'path': '/tmp/c', 'kind': 'code'},
            {'name': 'n', 'path': '/tmp/n', 'kind': 'notes', 'include': ['**/*.md']}]})
        self.assertEqual(cfg.root('c').include, inc['code'])
        self.assertEqual(cfg.root('n').include, ('**/*.md',))

    def test_model_defaults(self):
        cfg = config.from_dict(base())
        e, r = cfg.embedder, cfg.reranker
        self.assertEqual((e.key, e.hf_id, e.op, e.pooling, e.dim), ('mdense', 'lightonai/mDenseOn', 'embed', 'cls', 768))
        self.assertEqual(e.revision, 'a5fdb000f7a21da96c3bddde3a782ef777316df3')
        self.assertEqual(e.prefixes, {'q': 'query: ', 'd': 'document: '})
        self.assertEqual((r.key, r.hf_id, r.op, r.dim), ('mlate', 'lightonai/mLateOn', 'maxsim', 128))
        self.assertEqual(r.revision, 'bbd883ae1fadb563056ff73b1061ae8172a3fcda')
        self.assertEqual(r.prefixes, {'q': '[Q] ', 'd': '[D] '})
        self.assertEqual([p['src'] for p in r.projection], ['1_Dense', '2_Dense', '3_Dense'])
        self.assertEqual([p['residual'] for p in r.projection], [True, True, False])
        self.assertEqual(cfg.model('mlate'), r)
        self.assertEqual(cfg.model('mdense'), e)
        self.assertIsNone(cfg.model('other'))
        self.assertEqual(cfg.weights_dir, os.path.join(cfg.home, 'models'))

    def test_models_null(self):
        d = base()
        d['models']['embedder'] = None
        d['models']['reranker'] = None
        cfg = config.from_dict(d)
        self.assertIsNone(cfg.embedder)
        self.assertIsNone(cfg.reranker)
        self.assertIsNone(cfg.model('mdense'))

    def test_to_dict_round_trip(self):
        cfg = config.from_dict(base())
        again = config.from_dict(cfg.to_dict())
        self.assertEqual(cfg.to_dict(), again.to_dict())
        self.assertEqual(cfg.roots, again.roots)
        self.assertEqual(cfg.reranker, again.reranker)
        json.dumps(cfg.to_dict())

    def test_derived_paths(self):
        cfg = config.from_dict(base())
        self.assertEqual(cfg.index_dir(), N('/tmp/leks-test-home/index'))
        self.assertEqual(cfg.run_dir(), N('/tmp/leks-test-home/run'))
        self.assertEqual(cfg.master_path(), N('/tmp/leks-test-home/index/master.sqlite'))
        self.assertEqual(cfg.profile_db('architect'), N('/tmp/leks-test-home/index/architect/search.sqlite'))
        self.assertEqual(cfg.vectors_dir(), N('/tmp/leks-test-home/index/vectors'))
        self.assertEqual(cfg.helper_path(), N('/tmp/leks-test-home/bin/extract_text'))
        with self.assertRaises(ConfigError):
            cfg.profile_db('../x')

    def test_helper_override(self):
        d = base()
        d['ocr']['helper'] = '~/tools/ocr'
        cfg = config.from_dict(d)
        self.assertEqual(cfg.helper_path(), N(os.path.expanduser('~/tools/ocr')))

    def test_relative_helper_resolves_against_the_config_folder(self):
        # hook jobs run in /, so a helper path that leaned on the working folder broke there
        d = base()
        d['ocr']['helper'] = 'tools/ocr'
        cfg = config.from_dict(d, base_dir='/tmp/leks-test-conf')
        self.assertEqual(cfg.helper_path(), N('/tmp/leks-test-conf/tools/ocr'))

    def test_lookups(self):
        cfg = config.from_dict(base())
        self.assertEqual(cfg.root('code').kind, 'code')
        with self.assertRaises(KeyError):
            cfg.root('nope')
        self.assertEqual([r.name for r in cfg.roots_for('architect')], ['notes', 'docs'])
        self.assertEqual(len(cfg.roots_for('default')), 4)
        with self.assertRaises(KeyError):
            cfg.roots_for('nope')


class Errors(unittest.TestCase):
    def setUp(self):
        p = clean_env()
        p.start()
        self.addCleanup(p.stop)

    def bad(self, mutate, pattern=None):
        d = base()
        mutate(d)
        with self.assertRaises(ConfigError) as cm:
            config.from_dict(d)
        if pattern:
            self.assertRegex(str(cm.exception), pattern)

    def test_ocr_page_limit_has_the_helper_bound(self):
        # the helper reads at most 2000 pages and went back to 40 above that without a word
        self.bad(lambda d: d['ocr'].update(max_pages=2001), r'ocr\.max_pages must be an integer from 1 to 2000')
        d = base()
        d['ocr']['max_pages'] = 2000
        self.assertEqual(config.from_dict(d).ocr['max_pages'], 2000)

    def test_not_an_object(self):
        with self.assertRaises(ConfigError):
            config.from_dict([])

    def test_unknown_top_key(self):
        self.bad(lambda d: d.update(rootz=[]), 'unknown key')

    def test_unknown_nested_keys(self):
        self.bad(lambda d: d['budgets'].update(embedd=1.0), 'unknown key in budgets')
        self.bad(lambda d: d['roots'][0].update(paht='x'), 'unknown key in roots')
        self.bad(lambda d: d['models'].update(devise='cpu'), 'unknown key in models')
        self.bad(lambda d: d['models']['embedder'].update(dimm=3), 'unknown key')
        self.bad(lambda d: d['models']['reranker']['projection'][0].update(extra=1), 'unknown key')
        self.bad(lambda d: d['classify'].update(statee=[]), 'unknown key in classify')
        self.bad(lambda d: d['profiles']['architect'].update(rootz=[]), 'unknown key')

    def test_version(self):
        self.bad(lambda d: d.update(version=2), 'version')
        self.bad(lambda d: d.update(version=True), 'version')
        self.bad(lambda d: d.pop('version'), 'version')

    def test_roots(self):
        self.bad(lambda d: d.update(roots=[]), 'non-empty')
        self.bad(lambda d: d.update(roots='x'), 'non-empty')
        self.bad(lambda d: d.update(roots=['x']), 'object')
        self.bad(lambda d: d['roots'][0].update(name='Bad Name'), 'root name')
        self.bad(lambda d: d['roots'][0].update(name='-x'), 'root name')
        self.bad(lambda d: d['roots'][0].update(name='x' * 41), 'root name')
        self.bad(lambda d: d['roots'][1].update(name='notes'), 'duplicate root')
        self.bad(lambda d: d['roots'][0].update(kind='video'), 'kind')
        self.bad(lambda d: d['roots'][0].update(path=''), 'path')
        self.bad(lambda d: d['roots'][0].pop('path'), 'path')
        self.bad(lambda d: d['roots'][0].update(follow_links='yes'), 'true or false')

    def test_globs(self):
        self.bad(lambda d: d['roots'][0].update(include='*.md'), 'list')
        self.bad(lambda d: d['roots'][0].update(exclude=['/abs/**']), 'relative')
        self.bad(lambda d: d['roots'][0].update(exclude=['../up/**']), 'relative')
        self.bad(lambda d: d['roots'][0].update(exclude=[3]), 'bad glob')
        self.bad(lambda d: d['roots'][0].update(exclude=['a\\b']), 'bad glob')
        self.bad(lambda d: d['classify'].update(state='x'), 'list')

    def test_profiles(self):
        self.bad(lambda d: d['profiles'].update(default={'roots': ['notes']}), 'default')
        self.bad(lambda d: d['profiles'].update(x={'roots': ['ghost']}), 'unknown root')
        self.bad(lambda d: d['profiles'].update(x={'roots': []}), 'roots list')
        self.bad(lambda d: d['profiles'].update(x='notes'), 'object')
        self.bad(lambda d: d['profiles'].update({'Bad': {'roots': ['notes']}}), 'profile name')
        self.bad(lambda d: d.update(profiles=[]), 'object')
        self.bad(lambda d: d.update(default_profile='ghost'), 'default_profile')

    def test_models(self):
        self.bad(lambda d: d['models'].update(backend='tf'), 'backend')
        self.bad(lambda d: d['models'].update(device='tpu'), 'device')
        self.bad(lambda d: d['models'].update(weights_dir=''), 'weights_dir')
        self.bad(lambda d: d['models']['embedder'].update(key='Bad Key'), 'key')
        self.bad(lambda d: d['models']['embedder'].update(hf_id='noslash'), 'hf_id')
        self.bad(lambda d: d['models']['embedder'].update(revision='main'), 'revision')
        self.bad(lambda d: d['models']['embedder'].update(revision='A' * 40), 'revision')
        self.bad(lambda d: d['models']['embedder'].update(op='maxsim'), 'op')
        self.bad(lambda d: d['models']['reranker'].update(op='embed'), 'op')
        self.bad(lambda d: d['models']['embedder'].update(pooling='maxsim'), 'pooling')
        self.bad(lambda d: d['models']['reranker'].update(pooling='cls'), 'pooling')
        self.bad(lambda d: d['models']['embedder'].update(pooling='median'), 'pooling')
        self.bad(lambda d: d['models']['embedder'].update(dim=0), 'dim')
        self.bad(lambda d: d['models']['embedder'].update(dim=True), 'dim')
        self.bad(lambda d: d['models']['embedder'].update(max_length=2), 'max_length')
        self.bad(lambda d: d['models']['embedder'].update(dtype='float16'), 'dtype')
        self.bad(lambda d: d['models']['embedder'].update(normalize='yes'), 'normalize')
        self.bad(lambda d: d['models']['embedder'].update(prefixes={'q': 'x'}), 'prefixes')
        self.bad(lambda d: d['models']['embedder'].pop('dim'), 'needs dim')
        self.bad(lambda d: d['models']['embedder'].update(projection=[]) or d['models']['embedder'].update(
            projection=[{'src': 'a', 'in': 1, 'out': 768, 'residual': False}]), 'projection')
        self.bad(lambda d: d['models']['reranker']['projection'][0].update(src='../x'), 'src')
        self.bad(lambda d: d['models']['reranker']['projection'][2].update(out=64), 'end at dim')
        self.bad(lambda d: d['models']['reranker']['projection'][0].pop('residual'), 'needs')
        self.bad(lambda d: d['models']['reranker'].update(key='mdense'), 'different keys')

    def test_fake_backend_needs_env(self):
        d = base()
        d['models']['backend'] = 'fake'
        with self.assertRaises(ConfigError):
            config.from_dict(d)
        with mock.patch.dict(os.environ, {'LEKS_ALLOW_FAKE': '1'}):
            self.assertEqual(config.from_dict(d).backend, 'fake')
        with mock.patch.dict(os.environ, {'LEKS_ALLOW_FAKE': 'true'}):
            with self.assertRaises(ConfigError):
                config.from_dict(d)

    def test_plain_sections(self):
        self.bad(lambda d: d['budgets'].update(embed=0), 'budgets.embed')
        self.bad(lambda d: d['budgets'].update(ping='fast'), 'budgets.ping')
        self.bad(lambda d: d['budgets'].update(ping=True), 'budgets.ping')
        self.bad(lambda d: d['daemon'].update(sandbox='maybe'), 'sandbox')
        self.bad(lambda d: d['daemon'].update(idle_exit=0), 'idle_exit')
        self.bad(lambda d: d['update'].update(min_interval=-1), 'min_interval')
        self.bad(lambda d: d['ocr'].update(enabled='sometimes'), 'ocr.enabled')
        self.bad(lambda d: d['ocr'].update(images='yes'), 'ocr.images')
        self.bad(lambda d: d['ocr'].update(max_pages=0), 'max_pages')
        self.bad(lambda d: d['ocr'].update(helper=''), 'helper')
        self.bad(lambda d: d['limits'].update(chunk_chars=0), 'chunk_chars')
        self.bad(lambda d: d['limits'].update(max_names=1.5), 'max_names')
        self.bad(lambda d: d['server'].update(always_load=1), 'always_load')
        self.bad(lambda d: d.update(ocr=[]), 'object')

    def test_ocr_enabled_booleans(self):
        d = base()
        d['ocr']['enabled'] = False
        self.assertEqual(config.from_dict(d).ocr['enabled'], 'off')
        d['ocr']['enabled'] = True
        self.assertEqual(config.from_dict(d).ocr['enabled'], 'on')

    def test_zero_min_interval_is_fine(self):
        d = base()
        d['update']['min_interval'] = 0
        self.assertEqual(config.from_dict(d).update['min_interval'], 0.0)

    def test_default_profile_must_exist_when_set(self):
        d = base()
        d['default_profile'] = 'orchestrator'
        self.assertEqual(config.from_dict(d).default_profile, 'orchestrator')


class Parsing(unittest.TestCase):
    def setUp(self):
        p = clean_env()
        p.start()
        self.addCleanup(p.stop)
        self.tmp = helpers.short_tmpdir()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text):
        p = os.path.join(self.tmp, *name.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        return p

    def test_duplicate_keys(self):
        with self.assertRaisesRegex(ConfigError, 'duplicate key'):
            config.parse_text('{"version": 3, "version": 3}')
        with self.assertRaisesRegex(ConfigError, 'duplicate key'):
            config.parse_text('{"a": {"b": 1, "b": 2}}')

    def test_bad_json(self):
        with self.assertRaisesRegex(ConfigError, 'not valid json'):
            config.parse_text('{"version": 3,}')
        with self.assertRaises(ConfigError):
            config.parse_text('{"version": NaN}')

    def test_load_duplicate_key_file(self):
        p = self.write('c.json', '{"version": 3, "roots": [], "roots": []}')
        with self.assertRaisesRegex(ConfigError, 'duplicate'):
            config.load(p)

    def test_load_non_utf8(self):
        p = os.path.join(self.tmp, 'bin.json')
        with open(p, 'wb') as f:
            f.write(b'\xff\xfe\x00')
        with self.assertRaises(ConfigError):
            config.load(p)

    def test_no_config(self):
        home = os.path.join(self.tmp, 'empty')
        os.makedirs(home)
        with mock.patch.dict(os.environ, {'LEKS_HOME': home, 'HOME': self.tmp}):
            with self.assertRaisesRegex(ConfigError, 'no config: run leks init'):
                config.load()
        with self.assertRaisesRegex(ConfigError, 'no config'):
            config.load(os.path.join(self.tmp, 'missing.json'))

    def test_order_path_beats_env_beats_home(self):
        def cfg_text(name):
            return json.dumps({'version': 3, 'roots': [{'name': name, 'path': 'r', 'kind': 'notes'}]})
        a = self.write('a.json', cfg_text('froma'))
        b = self.write('b.json', cfg_text('fromb'))
        home = os.path.join(self.tmp, 'h')
        self.write('h/config.json', cfg_text('fromhome'))
        with mock.patch.dict(os.environ, {'LEKS_HOME': home, 'LEKS_CONFIG': b}):
            self.assertEqual(config.load(a).roots[0].name, 'froma')
            self.assertEqual(config.load().roots[0].name, 'fromb')
        with mock.patch.dict(os.environ, {'LEKS_HOME': home}):
            self.assertEqual(config.load().roots[0].name, 'fromhome')

    def test_default_location_under_user_home(self):
        fake_home = os.path.join(self.tmp, 'uh')
        self.write('uh/.leks/config.json',
                   json.dumps({'version': 3, 'roots': [{'name': 'z', 'path': 'r', 'kind': 'notes'}]}))
        with mock.patch.dict(os.environ, {'HOME': fake_home, 'USERPROFILE': fake_home}):
            cfg = config.load()
        self.assertEqual(cfg.roots[0].name, 'z')
        self.assertEqual(cfg.home, os.path.join(fake_home, '.leks'))

    def test_tilde_in_env_config(self):
        fake_home = os.path.join(self.tmp, 'uh2')
        self.write('uh2/c.json', json.dumps({'version': 3, 'roots': [{'name': 'z', 'path': 'r', 'kind': 'notes'}]}))
        with mock.patch.dict(os.environ, {'HOME': fake_home, 'USERPROFILE': fake_home, 'LEKS_CONFIG': '~/c.json'}):
            self.assertEqual(config.load().roots[0].name, 'z')

    def test_relative_root_resolves_against_config_folder(self):
        p = self.write('proj/cfg/config.json', json.dumps(
            {'version': 3, 'home': 'state', 'roots': [{'name': 'n', 'path': '../notes', 'kind': 'notes'}]}))
        cfg = config.load(p)
        self.assertEqual(cfg.roots[0].path, os.path.join(self.tmp, 'proj', 'notes'))
        self.assertEqual(cfg.home, os.path.join(self.tmp, 'proj', 'cfg', 'state'))
        self.assertEqual(cfg.source_path, p)

    def test_root_path_tilde_and_trailing_slash(self):
        fake_home = os.path.join(self.tmp, 'uh3')
        with mock.patch.dict(os.environ, {'HOME': fake_home, 'USERPROFILE': fake_home}):
            cfg = config.from_dict({'version': 3, 'roots': [{'name': 'n', 'path': '~/p/', 'kind': 'notes'}]})
        self.assertEqual(cfg.roots[0].path, os.path.join(fake_home, 'p'))

    def test_home_precedence(self):
        d = {'version': 3, 'home': '/tmp/from-field', 'roots': [{'name': 'n', 'path': '/tmp/n', 'kind': 'notes'}]}
        self.assertEqual(config.from_dict(d).home, N('/tmp/from-field'))
        with mock.patch.dict(os.environ, {'LEKS_HOME': '/tmp/from-env'}):
            self.assertEqual(config.from_dict(d).home, N('/tmp/from-env'))
            self.assertEqual(config.home_dir(), N('/tmp/from-env'))
        del d['home']
        with mock.patch.dict(os.environ, {'HOME': '/tmp/uh4', 'USERPROFILE': '/tmp/uh4'}):
            self.assertEqual(config.from_dict(d).home, N('/tmp/uh4/.leks'))
            self.assertEqual(config.home_dir(None), N('/tmp/uh4/.leks'))

    def test_active_profile(self):
        cfg = config.from_dict(base())
        self.assertEqual(config.active_profile(cfg), 'default')
        with mock.patch.dict(os.environ, {'LEKS_PROFILE': 'architect'}):
            self.assertEqual(config.active_profile(cfg), 'architect')
        with mock.patch.dict(os.environ, {'LEKS_PROFILE': 'ghost'}):
            with self.assertRaises(ConfigError):
                config.active_profile(cfg)
        with mock.patch.dict(os.environ, {'LEKS_PROFILE': ''}):
            self.assertEqual(config.active_profile(cfg), 'default')


class Globs(unittest.TestCase):
    def test_star_stays_in_segment(self):
        self.assertTrue(config.glob_match('a.md', '*.md'))
        self.assertFalse(config.glob_match('x/a.md', '*.md'))
        self.assertTrue(config.glob_match('x/a.md', 'x/*.md'))
        self.assertFalse(config.glob_match('x/y/a.md', 'x/*.md'))

    def test_double_star_crosses_folders(self):
        self.assertTrue(config.glob_match('a.md', '**/*.md'))
        self.assertTrue(config.glob_match('x/a.md', '**/*.md'))
        self.assertTrue(config.glob_match('x/y/z/a.md', '**/*.md'))
        self.assertFalse(config.glob_match('x/y/a.txt', '**/*.md'))
        self.assertTrue(config.glob_match('x/build/y/a.o', '**/build/**'))
        self.assertTrue(config.glob_match('build/a.o', '**/build/**'))
        self.assertFalse(config.glob_match('build', '**/build/**'))
        self.assertTrue(config.glob_match('a/b/c.md', 'a/**/c.md'))
        self.assertTrue(config.glob_match('a/c.md', 'a/**/c.md'))
        self.assertFalse(config.glob_match('b/c.md', 'a/**/c.md'))

    def test_trailing_double_star_needs_something(self):
        self.assertTrue(config.glob_match('drafts/a.md', 'drafts/**'))
        self.assertTrue(config.glob_match('drafts/x/a.md', 'drafts/**'))
        self.assertFalse(config.glob_match('drafts', 'drafts/**'))
        self.assertFalse(config.glob_match('other/a.md', 'drafts/**'))
        self.assertTrue(config.glob_match('a/b', '**'))

    def test_run_of_double_stars(self):
        self.assertTrue(config.glob_match('a/b/c.md', '**/**/c.md'))

    def test_question_and_classes(self):
        self.assertTrue(config.glob_match('v1.md', 'v?.md'))
        self.assertFalse(config.glob_match('v10.md', 'v?.md'))
        self.assertTrue(config.glob_match('a1.md', 'a[0-9].md'))
        self.assertFalse(config.glob_match('ab.md', 'a[0-9].md'))
        self.assertTrue(config.glob_match('ab.md', 'a[!0-9].md'))
        self.assertTrue(config.glob_match('a[.md', 'a[.md'))

    def test_case_sensitive(self):
        self.assertTrue(config.glob_match('STATE.md', '**/STATE.md'))
        self.assertFalse(config.glob_match('state.md', '**/STATE.md'))

    def test_special_names(self):
        self.assertTrue(config.glob_match('x/DEC-001-a.md', '**/DEC-*'))
        self.assertTrue(config.glob_match('.claude/rules/a.md', '.claude/**'))
        self.assertTrue(config.glob_match('.github/copilot-instructions.md', '.github/copilot-instructions.md'))
        self.assertTrue(config.glob_match('a/b_STATE.md', '**/*_STATE.md'))
        self.assertTrue(config.glob_match('a.b+c.md', '**/*.md'))
        self.assertFalse(config.glob_match('axmd', '*.md'))

    def test_empty_inputs(self):
        self.assertFalse(config.glob_match('', '**'))
        self.assertFalse(config.glob_match('a', ''))


class Skipping(unittest.TestCase):
    def setUp(self):
        self.root = Root('r', '/tmp/r', 'mixed', config.DEFAULT_INCLUDE['mixed'], ('drafts/**', '**/private/**'))

    def test_junk(self):
        for rel in ('.DS_Store', 'a/Thumbs.db', 'a/.localized', 'a/._x.md', 'a/~$doc.docx',
                    'a/x.pyc', 'a/.x.swp', 'a/x.tmp', 'a/x.sqlite-wal', 'a/x.sqlite-shm'):
            self.assertTrue(config.skipped(self.root, rel), rel)
        self.assertFalse(config.skipped(self.root, 'a/notes.md'))

    def test_secret_names(self):
        for rel in ('.env', 'a/.env', 'a/.env.local', 'k.pem', 'a/server.key', 'a/x.p12', 'a/x.pfx',
                    'a/id_rsa', 'id_ed25519', 'a/id_ecdsa.pub', 'a/Credentials.md', 'a/my-secret.txt',
                    'a/PASSWORDS.txt', 'a/apikey.json', 'a/api_key.py'):
            self.assertTrue(config.skipped(self.root, rel), rel)
            self.assertTrue(config.SECRET_NAME.search(rel), rel)
        for rel in ('a/environment.md', 'a/monkey.md', 'a/keyboard.md', 'a/idea_rsa_notes.md'):
            self.assertFalse(config.skipped(self.root, rel), rel)

    def test_always_excluded_dirs(self):
        for d in config.ALWAYS_EXCLUDE_DIRS:
            self.assertTrue(config.skipped(self.root, 'a/%s/x.md' % d), d)
            self.assertTrue(config.skipped(self.root, '%s/x.md' % d), d)
        self.assertFalse(config.skipped(self.root, 'a/build.sh'))
        self.assertFalse(config.skipped(self.root, 'a/dist_notes.md'))

    def test_root_excludes(self):
        self.assertTrue(config.skipped(self.root, 'drafts/a.md'))
        self.assertTrue(config.skipped(self.root, 'x/private/a.md'))
        self.assertFalse(config.skipped(self.root, 'x/a.md'))

    def test_wanted(self):
        notes = Root('n', '/tmp/n', 'notes', config.DEFAULT_INCLUDE['notes'], ('drafts/**',))
        self.assertTrue(config.wanted(notes, 'a/b.md'))
        self.assertTrue(config.wanted(notes, 'b.txt'))
        self.assertFalse(config.wanted(notes, 'a/b.py'))
        self.assertFalse(config.wanted(notes, 'drafts/b.md'))
        self.assertFalse(config.wanted(notes, 'a/secret.md'))
        self.assertFalse(config.wanted(notes, 'node_modules/x.md'))
        code = Root('c', '/tmp/c', 'code', config.DEFAULT_INCLUDE['code'], ())
        self.assertTrue(config.wanted(code, 'src/a.py'))
        self.assertTrue(config.wanted(code, 'cfg.toml'))
        self.assertFalse(config.wanted(code, 'a.md'))
        docs = Root('d', '/tmp/d', 'docs', config.DEFAULT_INCLUDE['docs'], ())
        self.assertTrue(config.wanted(docs, 'a/scan.png'))
        self.assertTrue(config.wanted(docs, 'a/paper.pdf'))
        self.assertFalse(config.wanted(docs, 'a/x.py'))

    def test_json_limit_constant(self):
        self.assertEqual(config.JSON_MAX_BYTES, 200000)


class Paths(unittest.TestCase):
    def setUp(self):
        p = clean_env()
        p.start()
        self.addCleanup(p.stop)
        self.cfg = config.from_dict({'version': 3, 'home': '/tmp/hh', 'roots': [
            {'name': 'notes', 'path': '/tmp/pr/notes', 'kind': 'notes'},
            {'name': 'inner', 'path': '/tmp/pr/notes/inner', 'kind': 'notes'},
            {'name': 'code', 'path': '/tmp/pr/code', 'kind': 'code'}]})

    def test_round_trip(self):
        for disp in ('notes/a.md', 'notes/sub/dir/b.md', 'code/x/y.py', 'inner/z.md'):
            ab = self.cfg.abs_path(disp)
            self.assertIsNotNone(ab, disp)
            self.assertEqual(self.cfg.display_path(ab), disp)
        self.assertEqual(self.cfg.abs_path('notes/a/b.md'), os.path.join(N('/tmp/pr/notes'), 'a', 'b.md'))

    def test_longest_root_wins(self):
        self.assertEqual(self.cfg.display_path('/tmp/pr/notes/inner/z.md'), 'inner/z.md')
        self.assertEqual(self.cfg.display_path('/tmp/pr/notes/other.md'), 'notes/other.md')

    def test_abs_path_refusals(self):
        for bad in ('notes/../x', 'notes/a/../../b', '../notes/a.md', 'ghost/a.md', 'notes', 'notes/',
                    '/tmp/pr/notes/a.md', 'notes//a.md', 'notes/./a.md', 'notes\\a.md', '', None,
                    'notes/a\x00b'):
            self.assertIsNone(self.cfg.abs_path(bad), repr(bad))

    def test_display_path_refusals(self):
        for bad in ('/tmp/pr/notes/../code/x.py', '/tmp/elsewhere/a.md', '/tmp/pr/notes', '/tmp/pr/notes/',
                    '/tmp/pr/notesx/a.md', '', None, '/tmp/pr/notes/a\x00'):
            self.assertIsNone(self.cfg.display_path(bad), repr(bad))

    def test_display_path_through_symlinked_root(self):
        tmp = helpers.short_tmpdir()
        self.addCleanup(shutil.rmtree, tmp, True)
        real = os.path.join(tmp, 'real')
        os.makedirs(os.path.join(real, 'sub'))
        link = os.path.join(tmp, 'link')
        os.symlink(real, link)
        cfg = config.from_dict({'version': 3, 'home': tmp + '/h',
                                'roots': [{'name': 'n', 'path': link, 'kind': 'notes'}]})
        self.assertEqual(cfg.display_path(os.path.join(real, 'sub', 'a.md')), 'n/sub/a.md')

    def test_socket_default(self):
        cfg = config.from_dict({'version': 3, 'home': '/tmp/hh', 'roots': [
            {'name': 'n', 'path': '/tmp/n', 'kind': 'notes'}]})
        self.assertEqual(cfg.socket_path(), os.path.join(N('/tmp/hh'), 'run', 'modeld.sock'))

    def test_socket_env(self):
        with mock.patch.dict(os.environ, {'LEKS_MODELD_SOCK': '/tmp/custom.sock'}):
            self.assertEqual(self.cfg.socket_path(), '/tmp/custom.sock')

    @unittest.skipUnless(os.name == 'posix', 'the short socket fallback is for unix sockets')
    def test_socket_fallback_when_long(self):
        home = '/tmp/' + 'long' * 30
        cfg = config.from_dict({'version': 3, 'home': home, 'roots': [
            {'name': 'n', 'path': '/tmp/n', 'kind': 'notes'}]})
        sha = hashlib.sha256(home.encode()).hexdigest()[:8]
        self.assertEqual(cfg.socket_path(), '/tmp/leks-%d-%s.sock' % (os.getuid(), sha))
        self.assertLess(len(cfg.socket_path().encode()), config.SOCK_MAX)

    @unittest.skipUnless(os.name == 'posix', 'the length limit is a unix socket one')
    def test_socket_boundary(self):
        # a home that makes <home>/run/modeld.sock exactly 99 bytes still fits
        tail = '/run/modeld.sock'
        home = '/tmp/' + 'h' * (99 - len(tail) - 5)
        cfg = config.from_dict({'version': 3, 'home': home, 'roots': [
            {'name': 'n', 'path': '/tmp/n', 'kind': 'notes'}]})
        self.assertEqual(len(cfg.socket_path()), 99)
        home2 = home + 'h'
        cfg2 = config.from_dict({'version': 3, 'home': home2, 'roots': [
            {'name': 'n', 'path': '/tmp/n', 'kind': 'notes'}]})
        self.assertTrue(cfg2.socket_path().startswith('/tmp/leks-'))


class Helpers(unittest.TestCase):
    def test_make_home_words_only(self):
        home, cfg = helpers.make_home()
        try:
            self.assertEqual(cfg.home, home)
            self.assertIsNone(cfg.embedder)
            self.assertIsNone(cfg.reranker)
            self.assertEqual(os.environ['LEKS_HOME'], home)
            self.assertEqual([r.name for r in cfg.roots], ['notes', 'code', 'docs'])
            self.assertTrue(os.path.isfile(os.path.join(cfg.root('notes').path, 'STATE.md')))
            self.assertTrue(os.path.isfile(os.path.join(cfg.root('code').path, 'tile_cache.py')))
            self.assertEqual(cfg.profiles['architect'], ['notes', 'docs'])
            self.assertLess(len(cfg.socket_path().encode()), config.SOCK_MAX)
        finally:
            helpers.cleanup(home)
        self.assertFalse(os.path.exists(home))

    def test_make_home_fake_models_and_env_restore(self):
        before = os.environ.get('LEKS_ALLOW_FAKE')
        home, cfg = helpers.make_home(models=True)
        try:
            self.assertEqual(cfg.backend, 'fake')
            self.assertEqual(cfg.embedder.dim, 64)
            self.assertEqual(cfg.reranker.dim, 16)
            self.assertEqual(os.environ['LEKS_ALLOW_FAKE'], '1')
        finally:
            helpers.cleanup(home)
        self.assertEqual(os.environ.get('LEKS_ALLOW_FAKE'), before)

    def test_corpus_shape(self):
        tmp = helpers.short_tmpdir()
        try:
            out = helpers.write_corpus(tmp)
            self.assertEqual(sorted(out), ['code', 'docs', 'notes'])
            notes = [os.path.join(dp, f) for dp, _, fs in os.walk(out['notes']) for f in fs]
            self.assertEqual(len(notes), 6)
            self.assertEqual(len(os.listdir(out['code'])), 2)
            self.assertEqual(len(os.listdir(out['docs'])), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_custom_profiles(self):
        home, cfg = helpers.make_home(profiles={'solo': {'roots': ['code']}})
        try:
            self.assertEqual(sorted(cfg.profiles), ['default', 'solo'])
        finally:
            helpers.cleanup(home)

    def test_corpus_is_clean_of_skipped_names(self):
        home, cfg = helpers.make_home()
        try:
            for r in cfg.roots:
                for dp, _, fs in os.walk(r.path):
                    for f in fs:
                        rel = os.path.relpath(os.path.join(dp, f), r.path)
                        self.assertFalse(config.skipped(r, rel), rel)
                        self.assertTrue(config.wanted(r, rel), rel)
        finally:
            helpers.cleanup(home)


@contextlib.contextmanager
def fake_module(name, obj):
    """Put obj where `from leks import <name>` finds it (None makes the import fail)."""
    import sys
    import leks
    full = 'leks.' + name
    old_mod = sys.modules.get(full, KeyError)
    old_attr = leks.__dict__.get(name, KeyError)
    sys.modules[full] = obj
    if obj is None:
        leks.__dict__.pop(name, None)
    else:
        setattr(leks, name, obj)
    try:
        yield
    finally:
        if old_mod is KeyError:
            sys.modules.pop(full, None)
        else:
            sys.modules[full] = old_mod
        if old_attr is KeyError:
            leks.__dict__.pop(name, None)
        else:
            setattr(leks, name, old_attr)


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Cli(unittest.TestCase):
    def setUp(self):
        p = clean_env()
        p.start()
        self.addCleanup(p.stop)
        self.tmp = helpers.short_tmpdir()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_init_writes_config_once(self):
        home = os.path.join(self.tmp, 'h')
        code, out, _ = run_cli('init', '--home', home)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)['created'])
        target = os.path.join(home, 'config.json')
        with open(target, encoding='utf-8') as f:
            d = json.load(f)
        self.assertEqual(d['home'], home)
        d2 = copy.deepcopy(d)
        d2['home'] = '~/.leks'
        self.assertEqual(d2, config.default_dict())
        code, out, _ = run_cli('init', '--home', home)
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)['created'])

    def test_model_check_says_what_went_wrong(self):
        # the models command printed only the error type, never the reason
        home = os.path.join(self.tmp, 'h')
        run_cli('init', '--home', home)
        from leks import models
        why = 'huggingface_hub is not installed: pip install huggingface_hub'
        with mock.patch.dict(os.environ, {'LEKS_CONFIG': os.path.join(home, 'config.json')}), \
                mock.patch.object(models, 'fetch', side_effect=models.ModelCheck(why)):
            code, _, err = run_cli('models', 'fetch')
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), 'leks: ' + why)

    def test_config_check_and_show(self):
        home = os.path.join(self.tmp, 'h')
        run_cli('init', '--home', home)
        cfgpath = os.path.join(home, 'config.json')
        code, out, _ = run_cli('config', 'check', '--config', cfgpath)
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertTrue(res['ok'])
        self.assertEqual(res['roots'], 4)
        self.assertEqual(len(res['warnings']), 4)    # the example folders do not exist
        code, out, _ = run_cli('--config', cfgpath, 'config', 'show')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['version'], 3)

    def test_config_check_reports_error(self):
        p = os.path.join(self.tmp, 'bad.json')
        with open(p, 'w') as f:
            f.write('{"version": 3, "nope": 1}')
        code, out, err = run_cli('config', 'check', '--config', p)
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn('unknown key', err)

    def test_build_with_no_reachable_root_stops_in_one_line(self):
        home = os.path.join(self.tmp, 'h')
        run_cli('init', '--home', home)    # the example folders do not exist
        code, out, err = run_cli('--config', os.path.join(home, 'config.json'), 'build')
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(len(err.strip().split('\n')), 1)
        self.assertIn('none of the configured roots exists or is readable (notes, code, docs, misc)', err)
        self.assertFalse(os.path.exists(os.path.join(home, 'index')))

    def test_modeld_stop_without_a_daemon(self):
        home, cfg = helpers.make_home()
        try:
            code, out, err = run_cli('modeld', 'stop')
            self.assertEqual((code, json.loads(out), err), (0, {'modeld': 'not running'}, ''))
            from leks.hooks import warm_models
            with mock.patch.object(warm_models, '_ping', return_value=True), \
                    mock.patch.object(warm_models, '_ask', return_value={}) as ask:
                code, out, _ = run_cli('modeld', 'stop')
            self.assertEqual((code, json.loads(out)), (0, {'modeld': 'stop sent'}))
            self.assertEqual(ask.call_args[0][1], {'op': 'bye'})
        finally:
            helpers.cleanup(home)

    def test_no_config_message(self):
        with mock.patch.dict(os.environ, {'LEKS_HOME': os.path.join(self.tmp, 'none'), 'HOME': self.tmp}):
            code, _, err = run_cli('config', 'check')
        self.assertEqual(code, 1)
        self.assertIn('no config: run leks init', err)

    def test_profile_flag_sets_env(self):
        home, cfg = helpers.make_home()
        try:
            code, out, _ = run_cli('config', 'check', '--profile', 'architect')
            self.assertEqual(code, 0)
            self.assertEqual(os.environ['LEKS_PROFILE'], 'architect')
            code, _, err = run_cli('config', 'check', '--profile=ghost')
            self.assertEqual(code, 1)
            self.assertIn('unknown profile', err)
        finally:
            helpers.cleanup(home)

    def test_usage(self):
        code, _, _ = run_cli()
        self.assertEqual(code, 2)
        code, _, _ = run_cli('nonsense')
        self.assertEqual(code, 2)
        code, _, _ = run_cli('search')
        self.assertEqual(code, 2)

    def test_hooks_never_fail(self):
        # no config at all, modules may be missing: still exit 0
        with mock.patch.dict(os.environ, {'LEKS_HOME': os.path.join(self.tmp, 'none'), 'HOME': self.tmp}):
            for a in ('brief', 'warm', 'update'):
                code, _, _ = run_cli('hook', a)
                self.assertEqual(code, 0, a)

    def test_hook_swallows_exceptions(self):
        boom = mock.Mock()
        boom.main.side_effect = RuntimeError('x')
        with mock.patch.dict('sys.modules', {'leks.hooks': mock.Mock(session_brief=boom),
                                             'leks.hooks.session_brief': boom}):
            code, out, err = run_cli('hook', 'brief')
        self.assertEqual(code, 0)
        self.assertEqual((out, err), ('', ''))

    def test_dispatch_imports_lazily(self):
        # a command whose module fails to import reports an error and does not touch others
        home, cfg = helpers.make_home()
        try:
            with fake_module('build', None):
                code, _, err = run_cli('build')
            self.assertEqual(code, 1)
            self.assertRegex(err, r'Error \(build\)')
            code, _, _ = run_cli('config', 'check')
            self.assertEqual(code, 0)
        finally:
            helpers.cleanup(home)

    def test_dispatch_calls_target(self):
        home, cfg = helpers.make_home()
        try:
            fake_build = mock.Mock()
            fake_build.update_all.return_value = {'seen': 0}
            with fake_module('build', fake_build):
                code, out, _ = run_cli('build', '--force')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), {'seen': 0})
            args = fake_build.update_all.call_args[0]
            self.assertTrue(args[1])
        finally:
            helpers.cleanup(home)



class CliMeaning(unittest.TestCase):
    """CLI search and tried start the model daemon, or fall back to words and say why."""

    def setUp(self):
        p = clean_env()
        p.start()
        self.addCleanup(p.stop)
        self.home, self.cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, self.home)
        os.environ['LEKS_PROFILE'] = 'orchestrator'

    def test_start_words(self):
        from leks.hooks import warm_models
        cases = [('no backend',
                  'model daemon not started (model backend not installed, '
                  'run: pip install -e ".[models]" in the LEKS clone)'),
                 ('no weights', 'model daemon not started (model weights not found, run: leks models fetch)'),
                 ('refused', 'model daemon not started (it did not come up, run leks modeld serve to see why)'),
                 ('up', None)]
        for word, want in cases:
            with mock.patch.object(warm_models, 'start', return_value=word) as st:
                self.assertEqual(cli._meaning_ready(self.cfg), want, word)
            st.assert_called_once_with(self.cfg, wait=True)
        with mock.patch.object(warm_models, 'start', return_value='started'), \
                mock.patch.object(warm_models, '_ping', side_effect=[True, False]):
            self.assertIsNone(cli._meaning_ready(self.cfg))
            self.assertIn('did not come up', cli._meaning_ready(self.cfg))
        with mock.patch.object(warm_models, 'start', return_value='locked'), \
                mock.patch.object(warm_models, '_wait_up', side_effect=[True, False]):
            self.assertIsNone(cli._meaning_ready(self.cfg))
            self.assertIn('another start is under way', cli._meaning_ready(self.cfg))

    def test_no_start_without_models_or_with_words_only(self):
        from leks.hooks import warm_models
        with mock.patch.object(warm_models, 'start') as st:
            self.assertIsNone(cli._meaning_ready(self.cfg, words_only=True))
            self.cfg.embedder = self.cfg.reranker = None
            self.assertIsNone(cli._meaning_ready(self.cfg))
        st.assert_not_called()

    def test_search_and_tried_fall_back_to_words_and_say_so(self):
        from leks import build
        from leks.hooks import warm_models
        build.update_all(self.cfg, log=lambda *a: None)
        with mock.patch.object(warm_models, 'start', return_value='no backend') as st:
            code, out, _ = run_cli('search', 'tile', 'cache', 'eviction')
            self.assertEqual(code, 0)
            self.assertTrue(out.strip().split('\n')[-1].startswith(
                'words only: model daemon not started (model backend not installed, '
                'run: pip install -e ".[models]" in the LEKS clone)'))
            code, out, _ = run_cli('tried', 'evict', 'the', 'oldest', 'tile')
            self.assertEqual(code, 0)
            self.assertIn('words only: model daemon not started (model backend not installed', out)
            self.assertEqual(st.call_count, 2)
            code, out, _ = run_cli('search', '--words-only', 'tile', 'cache')
            self.assertEqual(st.call_count, 2)
            self.assertIn('words only: not asked for', out)


if __name__ == '__main__':
    unittest.main()
