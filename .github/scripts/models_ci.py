"""Helpers for the models workflow. Standard library plus the leks package.

    python .github/scripts/models_ci.py key     print key=<cache key> for $GITHUB_OUTPUT
    python .github/scripts/models_ci.py check   exit 0 when both pinned models pass leks models check

The cache key is built from what decides the downloaded files: model key, hub id and
pinned revision of the embedder and the reranker in the config template, and the file
patterns leks models fetch leaves out. A new pin gives a new key, so stale weights are
never restored for it.
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

ROLES = ('embedder', 'reranker')


def pins():
    from leks import config
    m = config.default_dict()['models']
    return [[m[r]['key'], m[r]['hf_id'], m[r]['revision']] for r in ROLES]


def key():
    from leks import models
    p = pins()
    blob = json.dumps({'pins': p, 'skip': models.SKIP_PATTERNS}, sort_keys=True)
    names = '-'.join('%s-%s' % (k, rev[:12]) for k, _, rev in p)
    return 'leks-weights-%s-%s' % (names, hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12])


def check():
    from leks import config, models
    cfg = config.load()
    got = models.check_all(cfg)
    print(json.dumps(got, indent=1, sort_keys=True))
    want = {k for k, _, _ in pins()}
    ok = set(got) == want and all(v.get('ok') for v in got.values())
    if not ok:
        print('weights are missing or differ from their manifest (wanted %s)' % ', '.join(sorted(want)))
    return 0 if ok else 1


def main(argv):
    if argv == ['key']:
        print('key=' + key())
        return 0
    if argv == ['check']:
        return check()
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
