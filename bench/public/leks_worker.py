"""Run one LEKS version over a question list in a process of its own, write what it returned as JSON.

usage: python -B leks_worker.py JOB.json OUT.json

The parent sets LEKS_HOME, LEKS_CONFIG and PYTHONPATH (that LEKS folder first) for this process. The job
names the folder leks has to come from, so an installed copy can never answer for the version under test.

With job['models'] set the worker runs the model lanes: it checks the packages and the weights first and
writes {"status": "skipped", "reason": ...} when either is missing, then builds, starts the model daemon
the way bench/run_bench.py does, embeds every passage and searches in strict mode, so a missing lane fails
the run instead of giving word lane numbers under the model name.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_BENCH = os.path.join(os.path.dirname(HERE), 'run_bench.py')
# what the model lanes import, the daemon needs all of them; fetching also needs huggingface_hub
MODEL_PACKAGES = ('numpy', 'torch', 'transformers', 'safetensors', 'tokenizers')
FETCH_PACKAGES = ('huggingface_hub',)


def package_digest(pkg_dir):
    """sha256 over the .py files of a package, by relative name, so a result names the exact code."""
    rows = []
    for dp, dns, fns in os.walk(pkg_dir):
        dns[:] = sorted(d for d in dns if d != '__pycache__')
        for fn in sorted(fns):
            if fn.endswith('.py'):
                p = os.path.join(dp, fn)
                with open(p, 'rb') as f:
                    rows.append('%s\t%s\n' % (os.path.relpath(p, pkg_dir).replace(os.sep, '/'),
                                              hashlib.sha256(f.read()).hexdigest()))
    return hashlib.sha256(''.join(rows).encode('utf-8')).hexdigest()


def missing_packages(names):
    out = []
    for name in names:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            out.append(name)
    return out


def model_gap(missing, weights_missing, weights_dir):
    """Why the model lanes cannot run, or None."""
    if missing:
        return ('python packages for the model lanes are not installed: %s (pip install -e ".[models]")'
                % ', '.join(missing))
    if weights_missing:
        return ('model weights for %s not found in %s (run with --fetch-models, or leks models fetch)'
                % (', '.join(weights_missing), weights_dir))
    return None


def _skip(out_path, reason):
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'status': 'skipped', 'reason': reason}, f)
    return 0


def main(argv):
    job_path, out_path = argv[1], argv[2]
    with open(job_path, encoding='utf-8') as f:
        job = json.load(f)
    want = os.path.realpath(job['leks_root'])
    if not sys.path or os.path.realpath(sys.path[0] or '.') != want:
        sys.path.insert(0, want)
    import leks
    got = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(leks.__file__))))
    if got != want:
        print('leks was imported from another folder than the one under test', file=sys.stderr)
        return 3
    from leks import build, config as config_mod, query, text

    cfg_path = os.environ['LEKS_CONFIG']
    with open(cfg_path, encoding='utf-8') as f:
        d = json.load(f)
    models = job.get('models')
    fake = False
    if models:
        # the default embedder and reranker of this leks, weights from the folder the job names.
        # a test hands in the fake backend as models['config'] instead
        m = dict(models.get('config') or config_mod.default_dict()['models'])
        m['weights_dir'] = models['weights_dir']
        d['models'] = m
        fake = m.get('backend') == 'fake'
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1)
        need = ('numpy',) if fake else MODEL_PACKAGES + (FETCH_PACKAGES if models.get('fetch') else ())
        missing = missing_packages(need)
        if missing:
            return _skip(out_path, model_gap(missing, [], models['weights_dir']))
    cfg = config_mod.from_dict(d, base_dir=os.path.dirname(cfg_path))
    cfg.source_path = cfg_path
    profile = job['profile']
    fetched = None
    if models and not fake:
        from leks import models as models_mod
        if models.get('fetch'):
            # the only network use of this worker: the pinned revisions of the two models
            fetched = models_mod.fetch(cfg)
        absent = [s.key for s in (cfg.embedder, cfg.reranker) if not models_mod.weights_present(cfg, s.key)]
        if absent:
            return _skip(out_path, model_gap([], absent, models['weights_dir']))
    t0 = time.time()
    built = build.update_all(cfg, force=True, log=lambda *a, **kw: None)
    build_secs = time.time() - t0
    try:
        from leks import docs
        caps = dict(docs.capabilities(cfg))
    except Exception:
        caps = {}
    client, stop = None, (lambda: None)
    embed = None
    if models:
        client, stop, embed = start_models(cfg, profile, 'fake' if fake else 'config')
        if client is None:
            stop()
            return _skip(out_path, embed)
        if embed.get('rc') != 0:
            stop()
            print('embedding did not finish: %s' % embed, file=sys.stderr)
            return 4
    eng = query.Engine(cfg, profile=profile, immutable=True, client=client)
    out = {'version': getattr(leks, '__version__', 'unknown'),
           'package_digest': package_digest(os.path.dirname(os.path.abspath(leks.__file__))),
           'python': sys.version.split()[0], 'sqlite': sqlite3.sqlite_version, 'build_secs': round(build_secs, 2),
           'caps': caps, 'build': json.loads(json.dumps(built, default=str)), 'embed': embed, 'fetched': fetched}
    try:
        rows = eng.db.execute('SELECT id, path, kind, status, status_src FROM docs ORDER BY path').fetchall()
        by_id = {r[0]: r[1] for r in rows}
        out['docs'] = {r[1]: {'kind': r[2], 'status': r[3], 'status_from': r[4]} for r in rows}
        out['edges'] = sorted([by_id.get(a), p, how] for a, (p, how, _t) in eng._newer().items())
        res, lanes = {}, None
        for q in job['questions']:
            t = time.perf_counter()
            r = eng.search(q['question'], k=job['k'], per_doc=1, rerank=True, strict=bool(models))
            ms = (time.perf_counter() - t) * 1000.0
            hits = [{'path': h.get('path'), 'copies': list(h.get('_copies') or []), 'newer': h.get('newer'),
                     'status': h.get('status'), 'source': h.get('source')} for h in (r.get('results') or [])]
            res[q['id']] = {'hits': hits, 'ms': round(ms, 2),
                            'intent': bool(text.CURRENT.search(q['question'])) and not text.dates_in(q['question'])}
            if lanes is None:
                lanes = r.get('lanes')
        out['lanes'] = lanes
        out['results'] = res
    finally:
        eng.close()
        stop()
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f)
    return 0


def start_models(cfg, profile, mode='config'):
    """(client, stop, embed info) with the daemon up and every passage embedded, or (None, stop, reason).

    mode 'config' starts the daemon the normal way (sandboxed where there is a sandbox), 'fake' runs
    leks modeld serve directly, as bench/run_bench.py does for its test backend."""
    spec = importlib.util.spec_from_file_location('leks_pub_run_bench', RUN_BENCH)
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    from leks import embed_all, modeld_client
    proc, started, up = rb.start_daemon(cfg, mode, wait=120.0)
    client = modeld_client.Client.from_config(cfg)
    real_bye = client.bye

    def stop():
        if started:
            rb.stop_daemon(real_bye, proc)
    if not up:
        return None, stop, 'the model daemon did not come up (leks modeld serve shows why)'
    try:
        client.bye = lambda: None  # embed_all says bye at its end, the daemon must stay up for the search
    except AttributeError:
        pass
    t0 = time.time()
    rc = embed_all.run(cfg, profile, client=client)
    return client, stop, {'rc': rc, 'secs': round(time.time() - t0, 1)}


if __name__ == '__main__':
    sys.exit(main(sys.argv))
