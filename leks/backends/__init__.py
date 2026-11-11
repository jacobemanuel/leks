"""Model backends. Nothing heavy is imported here: torch lives in torch_backend only."""
from __future__ import annotations

import importlib
import os

NAMES = ('torch', 'fake')
DEVICES = ('auto', 'cpu', 'mps', 'cuda')


def load_backend(name):
    """Return the backend module for a config name. 'fake' needs LEKS_ALLOW_FAKE=1."""
    if name == 'torch':
        return importlib.import_module('leks.backends.torch_backend')
    if name == 'fake':
        if os.environ.get('LEKS_ALLOW_FAKE') != '1':
            raise PermissionError('the fake backend only loads with LEKS_ALLOW_FAKE=1')
        return importlib.import_module('leks.backends.fake_backend')
    raise ValueError('unknown backend: %r' % (name,))


def pick_device(name, have_mps, have_cuda):
    """Resolve the device name. auto prefers mps, then cuda, then cpu."""
    if name not in DEVICES:
        raise ValueError('unknown device: %r' % (name,))
    if name == 'auto':
        return 'mps' if have_mps else ('cuda' if have_cuda else 'cpu')
    if name == 'mps' and not have_mps:
        raise RuntimeError('mps asked for but not available')
    if name == 'cuda' and not have_cuda:
        raise RuntimeError('cuda asked for but not available')
    return name


def batch_plan(lengths, max_tokens=16384, max_texts=32):
    """Group text indexes into batches, longest first.

    The padded size of a batch (longest * count) stays under max_tokens, so an
    out of memory shows up on the first batch and padding stays low.
    """
    order = sorted(range(len(lengths)), key=lambda i: -lengths[i])
    cur, longest = [], 0
    for i in order:
        longest = max(longest, lengths[i])
        if cur and (len(cur) >= max_texts or longest * (len(cur) + 1) > max_tokens):
            yield cur
            cur, longest = [], lengths[i]
        cur.append(i)
    if cur:
        yield cur
