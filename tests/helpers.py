"""Shared test helpers: short temp dirs, a tiny synthetic corpus, a ready config."""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest

from leks import config, transport

POSIX = os.name == 'posix'
# windows python has no unix sockets with descriptor passing; its tcp path is tested in test_transport
needs_unix_sockets = unittest.skipUnless(
    transport.UNIX_OK, 'needs unix domain sockets with descriptor passing (not on windows, see test_transport)')

_ENV_KEYS = ('LEKS_HOME', 'LEKS_CONFIG', 'LEKS_PROFILE', 'LEKS_ALLOW_FAKE', 'LEKS_MODELD_SOCK', transport.ENV)
_SAVED = {}   # home -> env values to put back in cleanup()

DEFAULT_PROFILES = {
    'architect': {'roots': ['notes', 'docs']},
    'orchestrator': {'roots': ['notes', 'code', 'docs']},
}

NOTES = {
    'STATE.md': """---
title: Project state
status: current
updated: 2026-03-02
---
# Project state

The frame graph pass order is settled: depth prepass, tile binning, shading, resolve.
The tile cache evicts the oldest tile first, see the eviction decision.
Open item: the asset bundle step still copies shaders by hand.
""",
    'decisions/DEC-001-eviction-policy.md': """# DEC-001 eviction policy

Status: superseded by [[DEC-002-eviction-policy]]

The tile cache evicts the least recently used tile when the budget is full.
""",
    'decisions/DEC-002-eviction-policy.md': """# DEC-002 eviction policy

Status: accepted

The tile cache evicts the oldest tile first. Measured on the sample scene, this
kept the frame time flat while the camera moved.
""",
    'archive/old-frame-notes.md': """# Old frame notes

Early idea: render the scene in one pass without a depth prepass.
We dropped it because overdraw made the shading pass too slow.
""",
    'frame_graph.md': """# Frame graph

The frame graph lists the render passes and the resources each pass reads and writes.

## Pass order

Depth prepass first, then tile binning, then shading, then resolve to the swap chain.

## Transient resources

Transient attachments are aliased when two passes never overlap in time.
""",
    'build_cache.md': """# Build cache

The build cache stores compiled shader blobs keyed by the hash of the source and the flags.

## Invalidation

A changed include file changes the key of every shader that pulls it in.
Cache entries older than thirty days are removed by the bundle script.
""",
}

CODE = {
    'tile_cache.py': '''"""Small tile cache used by the sample renderer."""


class TileCache:
    """Keeps decoded tiles, oldest tile leaves first."""

    def __init__(self, budget):
        self.budget = budget
        self.tiles = {}

    def put(self, key, tile):
        self.tiles[key] = tile
        while len(self.tiles) > self.budget:
            self.evict_oldest()

    def evict_oldest(self):
        oldest = next(iter(self.tiles))
        del self.tiles[oldest]
        return oldest
''',
    'build.sh': '''#!/bin/sh
# bundle the shader blobs and the textures into one folder

bundle_assets() {
  out="$1"
  mkdir -p "$out"
  cp shaders/*.blob "$out"/
  cp textures/*.png "$out"/
}

bundle_assets "${1:-dist-assets}"
''',
}

DOCS = {
    'release_checklist.txt': """Release checklist

1. Run the render tests on the sample scene.
2. Bundle the assets with the build script.
3. Tag the build and archive the shader cache.
""",
}


def short_tmpdir() -> str:
    """A temp dir with a short path, so a unix socket inside it fits."""
    return tempfile.mkdtemp(prefix='lk', dir='/tmp' if POSIX else None)


def _put(base: str, files: dict) -> None:
    for rel, text in files.items():
        p = os.path.join(base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)


def write_corpus(dst: str) -> dict:
    """Write the synthetic corpus under dst. Returns {root name: folder}."""
    out = {}
    for name, files in (('notes', NOTES), ('code', CODE), ('docs', DOCS)):
        base = os.path.join(dst, name)
        os.makedirs(base, exist_ok=True)
        _put(base, files)
        out[name] = base
    return out


def make_home(models: bool = False, profiles: 'dict | None' = None):
    """A fresh LEKS_HOME with a config over the corpus. Returns (home, Config).

    models=False: no models at all (words lanes only).
    models=True: fake backend, embedder dim 64, reranker dim 16.
    Call cleanup(home) when done, it puts the environment back.
    """
    home = short_tmpdir()
    _SAVED[home] = {k: os.environ.get(k) for k in _ENV_KEYS}
    for k in ('LEKS_CONFIG', 'LEKS_PROFILE', 'LEKS_MODELD_SOCK', transport.ENV):
        os.environ.pop(k, None)
    os.environ['LEKS_HOME'] = home
    folders = write_corpus(os.path.join(home, 'corpus'))
    d = config.default_dict()
    d['home'] = home
    d['roots'] = [
        {'name': 'notes', 'path': folders['notes'], 'kind': 'notes'},
        {'name': 'code', 'path': folders['code'], 'kind': 'code'},
        {'name': 'docs', 'path': folders['docs'], 'kind': 'docs'},
    ]
    d['profiles'] = copy.deepcopy(DEFAULT_PROFILES if profiles is None else profiles)
    if models:
        os.environ['LEKS_ALLOW_FAKE'] = '1'
        d['models']['backend'] = 'fake'
        d['models']['embedder']['dim'] = 64
        d['models']['reranker']['dim'] = 16
        d['models']['reranker'].pop('projection', None)
    else:
        d['models']['embedder'] = None
        d['models']['reranker'] = None
    path = os.path.join(home, 'config.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=2)
    return home, config.load(path)


def cleanup(home: str) -> None:
    """Remove the temp home and restore the environment make_home changed."""
    saved = _SAVED.pop(home, {})
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(home, ignore_errors=True)
