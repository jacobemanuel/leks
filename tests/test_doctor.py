"""leks doctor: what this install can do, as one json object."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import leks
from leks import __main__ as cli
from leks import transport
from tests import helpers


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(args)
    return code, out.getvalue(), err.getvalue()


class DoctorTests(unittest.TestCase):
    def test_with_a_config(self):
        home, cfg = helpers.make_home(models=False)
        self.addCleanup(helpers.cleanup, home)
        code, out, err = run(['doctor'])
        self.assertEqual((code, err), (0, ''))
        d = json.loads(out)
        self.assertEqual(d['leks'], leks.__version__)
        self.assertTrue(d['config']['ok'])
        self.assertEqual(d['config']['roots'], ['notes', 'code', 'docs'])
        self.assertEqual(d['index'], {'built': False,
                                      'profiles': {'architect': False, 'default': False, 'orchestrator': False}})
        self.assertEqual(d['models'], 'none configured, search runs on words')
        self.assertIn(d['model_transport'], ('unix', 'tcp'))
        for k in ('python', 'system', 'pdf', 'numpy', 'model_backend', 'sandbox'):
            self.assertIn(k, d)

    def test_index_and_weights_show_up(self):
        home, cfg = helpers.make_home(models=True)
        self.addCleanup(helpers.cleanup, home)
        self.assertEqual(run(['build'])[0], 0)
        d = json.loads(run(['doctor'])[1])
        self.assertTrue(d['index']['built'])
        self.assertTrue(d['index']['profiles']['architect'])
        self.assertEqual(set(d['models']), {cfg.embedder.key, cfg.reranker.key})

    def test_forced_tcp_is_reported(self):
        home, cfg = helpers.make_home(models=False)
        self.addCleanup(helpers.cleanup, home)
        with mock.patch.dict(os.environ, {transport.ENV: 'tcp'}):
            d = json.loads(run(['doctor'])[1])
        self.assertEqual(d['model_transport'], 'tcp')

    def test_without_a_config(self):
        empty = tempfile.mkdtemp(prefix='lkd')
        self.addCleanup(shutil.rmtree, empty, True)
        env = {'LEKS_HOME': empty}
        with mock.patch.dict(os.environ, env):
            os.environ.pop('LEKS_CONFIG', None)
            code, out, _ = run(['doctor'])
        self.assertEqual(code, 1)
        d = json.loads(out)
        self.assertFalse(d['config']['ok'])
        self.assertTrue(d['config']['why'])


if __name__ == '__main__':
    unittest.main()
