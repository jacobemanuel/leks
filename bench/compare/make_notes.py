"""Write the notes folder every system of the comparison indexed: the PEPs as Markdown at the pinned commit.

    python bench/compare/make_notes.py --out NOTES [--cache DIR] [--offline]

NOTES gets peps/pep-NNNN.md exactly as bench/public/pep_bench.py makes them. The script then builds the
benchmark's questions from these notes and stops if they differ from bench/public/questions.json.
"""
import argparse
import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location('pep_bench', os.path.join(REPO, 'bench', 'public', 'pep_bench.py'))
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

ap = argparse.ArgumentParser()
ap.add_argument('--out', required=True, help='a new folder for the notes')
ap.add_argument('--cache', default=os.path.join(os.path.expanduser('~'), '.cache', 'leks-public-bench'))
ap.add_argument('--offline', action='store_true', help='use the cached sources only')
a = ap.parse_args()
if os.path.exists(a.out):
    ap.error('%s exists already' % a.out)

src = pb.peps.fetch(a.cache, offline=a.offline, log=print)
manifest = pb.peps.convert_tree(src, a.out)
built = pb.questions.dumps(pb.questions.build(manifest))
with open(pb.QFILE, encoding='ascii') as f:
    if built != f.read():
        raise SystemExit('the questions built from these notes differ from bench/public/questions.json')
root = os.path.join(a.out, pb.questions.ROOT_NAME)
n = sum(1 for x in os.listdir(root) if x.endswith('.md'))
print('%d notes in %s, source digest %s; questions match bench/public/questions.json' % (
    n, root, manifest['source_digest']))
