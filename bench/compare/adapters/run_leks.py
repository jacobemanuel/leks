"""bm25, LEKS 3.1, LEKS 3.0 and LEKS 3.1 with models on one question file, in the comparison's run shape.

It calls the benchmark's own functions (bm25_run, run_worker, pep_config from bench/public/pep_bench.py), so
the systems run exactly as in the public benchmark and only the question file differs. The model system
needs a Python with the models extra; on macOS run it from a normal terminal, since the model daemon uses
a local socket that some sandboxes block.

    python -B bench/compare/adapters/run_leks.py --system leks --notes NOTES --questions Q.json \
        --out leks.json --work WORK --python PYTHON [--repo30 PATH_TO_A_V3.0.0_CHECKOUT]
"""
import argparse
import importlib.util
import json
import os
import resource
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ap = argparse.ArgumentParser()
ap.add_argument('--system', required=True, choices=['bm25', 'leks', 'leks30', 'leks+models'])
ap.add_argument('--notes', required=True, help='the folder that holds peps/, as bench/public/peps.py writes it')
ap.add_argument('--questions', required=True)
ap.add_argument('--out', required=True)
ap.add_argument('--work', required=True, help='fresh folder, must not exist yet')
ap.add_argument('--python', default=sys.executable, help='python for the leks worker')
ap.add_argument('--repo30', help='a checkout of the v3.0.0 tag, for --system leks30')
ap.add_argument('--weights', help='folder for the model weights (leks+models)')
ap.add_argument('--fetch', action='store_true', help='download the pinned model revisions first')
a = ap.parse_args()
# the worker runs with cwd=work, so every path it gets must be absolute
for _k in ('notes', 'questions', 'out', 'work', 'python', 'weights', 'repo30'):
    if getattr(a, _k):
        setattr(a, _k, os.path.abspath(getattr(a, _k)))
if a.system == 'leks30' and not a.repo30:
    ap.error('--system leks30 needs --repo30')
tree = a.repo30 if a.system == 'leks30' else REPO

spec = importlib.util.spec_from_file_location('pb', os.path.join(REPO, 'bench', 'public', 'pep_bench.py'))
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)
spec = importlib.util.spec_from_file_location('tree_peak', os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                        'tree_peak.py'))
tree_peak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tree_peak)

with open(a.questions, encoding='utf-8') as f:
    qs = json.load(f)['questions']
os.makedirs(a.work)
root = os.path.join(a.notes, 'peps')
cfg = pb.pep_config()
cfg['roots'] = [{'name': 'peps', 'path': root, 'kind': 'notes'}]
scrub = pb.scrubber([(a.work, '<work>')])
t0 = time.time()
tp = None
if a.system == 'bm25':
    o = pb.bm25_run(root, qs, k=pb.K)
    o['version'] = 'sqlite ' + __import__('sqlite3').sqlite_version
elif a.system == 'leks+models':
    weights = a.weights or os.path.join(a.work, 'weights')
    # the worker starts the model daemon detached, so getrusage below never sees it: sample the whole tree
    with tree_peak.TreePeak() as tp:
        o = pb.run_worker('leks-models', tree, dict(cfg, models=None), 'peps', qs, a.work, a.python, scrub,
                          k=pb.K, models={'weights_dir': weights, 'fetch': bool(a.fetch)}, timeout=pb.MODEL_TIMEOUT)
else:
    o = pb.run_worker(a.system, tree, cfg, 'peps', qs, a.work, a.python, scrub, k=pb.K)
wall = round(time.time() - t0, 1)
# largest single finished child (the worker); bm25 runs in this process
peak = resource.getrusage(resource.RUSAGE_SELF if a.system == 'bm25' else resource.RUSAGE_CHILDREN).ru_maxrss
peak_mb = round(peak / (1024 * 1024 if sys.platform == 'darwin' else 1024), 1)
peak_how = 'getrusage, this process' if a.system == 'bm25' else 'getrusage, largest child'
if tp is not None and tp.mb() is not None:
    peak_mb = max(peak_mb, tp.mb())
    peak_how = 'process tree (worker and model daemon) sampled every %s s, largest sum' % tp.every
res = o.get('results') or {}
out = {'status': o.get('status', 'ok'), 'tool': 'bm25' if a.system == 'bm25' else 'leks',
       'version': o.get('version'), 'mode': {'bm25': 'fts5', 'leks': 'words', 'leks30': 'words-3.0',
                                              'leks+models': 'models'}[a.system],
       'settings': {'harness': 'bench/compare/adapters/run_leks.py with pep_bench functions', 'k': pb.K,
                    'lanes': o.get('lanes'), 'package_digest': o.get('package_digest'),
                    'embed': o.get('embed'), 'python': o.get('python'),
                    'questions_file': os.path.basename(a.questions)},
       'index_secs': (o.get('build_secs') or 0) + ((o.get('embed') or {}).get('secs') or 0) if a.system != 'bm25' else None,
       'wall_secs': wall, 'query_ms': [r.get('ms') for r in res.values() if r.get('ms') is not None],
       'peak_rss_mb': peak_mb, 'peak_how': peak_how, 'peak_tree_procs': tp.most if tp is not None else None,
       'raw_asked': pb.K, 'reason': o.get('reason'),
       'results': {qid: {'hits': [{'path': h['path'], 'copies': h.get('copies') or [], 'newer': h.get('newer')}
                                  for h in r.get('hits') or []]} for qid, r in res.items()}}
with open(a.out, 'w', encoding='utf-8') as f:
    json.dump(out, f)
print('%s %s: %d questions, index %s s, wall %s s, peak %s MB' % (
    a.system, out['status'], len(res), out['index_secs'], wall, peak_mb))
