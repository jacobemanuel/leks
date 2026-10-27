# LEKS engine

The code behind [LEKS](../README.md), without any project data. It keeps a project's Markdown pages, the source files behind them and the facts drawn from them in one folder, and answers questions about them for people and for AI coding agents.

The tests build a small synthetic vault in a temporary folder, so everything here runs on its own.

## What's here

| File | What it does |
|---|---|
| `tools/brain.py` | The engine: build, search, answer packs, provenance, integrity checks and safe moves |
| `tools/retrieval.py`, `tools/page_index.py` | Search: a persistent BM25 index that updates page by page and ranks exactly like the original scan |
| `tools/storage.py` | Atomic writes that are read back and validated, and a lock between processes |
| `tools/evidence_store.py` | Content-addressed copies of every source version, locked with the immutable flag, plus an audit journal |
| `tools/facts.py`, `tools/anchors.py`, `tools/knowledge_time.py` | Claims tied to the exact source lines they rely on, and what was true at which date |
| `tools/freshness.py` | Rules that flag pages whose evidence has moved on |
| `tools/doctor.py` | Diagnosis and setup |
| `tools/vault_mcp.py` | A read-only MCP server (stdio, JSON-RPC 2.0) for AI coding agents |
| `tools/dense_client.py` | Client for the optional on-device embedding search; without it, search falls back to BM25 |
| `tools/hooks/` | Session hooks: the start-up context and an end-of-turn integrity check |
| `tools/vault_fixture.py` | The synthetic test vault |
| `tools/test_*.py` | 124 tests |

Standard library only, no network calls. Tested on macOS, where the evidence store uses the file system's immutable flag.

## Run the tests

```bash
cd engine
python3 -m unittest discover -s tools -p 'test_*.py'
```

## Left out

The project's pages, sources and claims, the retrieval benchmarks (their questions come from the project), the dense helper and its model, the integrations with Unity, Blender and Obsidian, and the pre-tool guard hook, which is tied to the folders of one machine.

© 2026 Jakub Majewski. All rights reserved.
