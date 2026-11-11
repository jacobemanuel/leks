"""Fake models for tests and CI. Hashing, not learning: same words give close vectors.

Only loaded when LEKS_ALLOW_FAKE=1 (see backends.load_backend). Needs numpy.
"""
from __future__ import annotations

import functools
import hashlib
import re

import numpy as np

TOKEN = re.compile(r'[a-z0-9]+')


def tokens(text):
    return TOKEN.findall((text or '').lower())


@functools.lru_cache(maxsize=65536)
def _slot(token, dim):
    """(index, sign) of a token: sign from bit 0 of the first hex digit, index from the next 8."""
    h = hashlib.sha1(token.encode('utf-8')).hexdigest()
    return int(h[1:9], 16) % dim, (1.0 if int(h[0], 16) & 1 else -1.0)


class Enc:
    """Bag of hashed tokens, L2 normalised. Empty text (or a perfect cancel) gives e0."""

    def __init__(self, path, spec, device='cpu'):
        self.dim = int(spec.dim)

    def __call__(self, texts, kind='d'):
        if kind not in ('q', 'd'):
            raise ValueError('kind must be q or d')
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in tokens(t):
                idx, sign = _slot(tok, self.dim)
                out[i, idx] += sign
            n = float(np.linalg.norm(out[i]))
            if n == 0.0:
                out[i, 0] = 1.0
            else:
                out[i] /= n
        return out


class MaxSim:
    """Each token is a signed unit vector, so the score is a plain count.

    score = sum over query tokens of the best dot product against any doc token.
    """

    def __init__(self, path, spec, device='cpu'):
        self.dim = int(spec.dim)

    def _slots(self, text):
        return [_slot(tok, self.dim) for tok in tokens(text)]

    def rerank(self, q, docs):
        qs = self._slots(q)
        out = []
        for d in docs:
            ds = self._slots(d)
            total = 0.0
            if ds:
                for qi, qsign in qs:
                    total += max((qsign * dsign if qi == di else 0.0) for di, dsign in ds)
            out.append(float(total))
        return out


def release():
    pass
