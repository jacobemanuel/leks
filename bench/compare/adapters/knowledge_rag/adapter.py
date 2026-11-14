#!/usr/bin/env python3
# knowledge-rag adapter for the public PEP comparison
#
# runs the tool in-process: KnowledgeOrchestrator.index_all(force=False) is the job the MCP server
# starts on first run (and reindex_documents() with no flags), and every question goes through
# search_knowledge(), the same function the MCP server dispatches to. tool defaults everywhere,
# the only config key set is paths.models_cache_dir so the models come from this tool folder.
#
# usage: python -I adapter.py --notes DIR --questions FILE --mode hybrid|keyword|semantic
#        --out FILE --work FRESH_DIR [--limit-questions N] [--subset-notes N] [--debug-raw N]
import argparse
import json
import os
import platform
import resource
import shutil
import sys
import time
import traceback
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
MODELS_DIR = TOOL_DIR / "models_cache"
MODEL_DIRS = ("models--Qdrant--bge-small-en-v1.5-onnx-Q", "models--Xenova--ms-marco-MiniLM-L-6-v2")
RAW = 100  # asked per question; also the tool's default search.max_results cap
K = 20  # distinct notes kept per question
WARMUP = "warm-up query"
# hybrid_alpha as documented on search_knowledge: 0.3 default, 0.0 keyword-only, 1.0 semantic-only
MODES = {"hybrid": 0.3, "keyword": 0.0, "semantic": 1.0}
ENV_KEYS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "ANONYMIZED_TELEMETRY",
            "DO_NOT_TRACK", "ORT_DISABLE_TELEMETRY", "KNOWLEDGE_RAG_WATCHER_DISABLED", "KNOWLEDGE_RAG_DIR", "HOME",
            "XDG_CACHE_HOME", "HF_HOME")


def say(*a):
    print("[adapter]", *a, file=sys.stderr, flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="knowledge-rag adapter (PEP bench contract)")
    p.add_argument("--notes", required=True)
    p.add_argument("--questions", required=True)
    p.add_argument("--mode", required=True, choices=sorted(MODES))
    p.add_argument("--out", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--limit-questions", type=int, default=None)
    p.add_argument("--subset-notes", type=int, default=None)
    p.add_argument("--debug-raw", type=int, default=0, help="keep raw rows of the first N questions")
    return p.parse_args(argv)


def peak_rss_mb(who):
    ru = resource.getrusage(who).ru_maxrss
    return ru / (1024.0 * 1024.0) if sys.platform == "darwin" else ru / 1024.0


def pick_notes(notes_root, subset):
    rels = sorted(p.relative_to(notes_root).as_posix() for p in notes_root.rglob("*.md") if p.is_file())
    return rels[:subset] if subset else rels


def pick_questions(qfile, note_set, subset, limit):
    """harness side: gold/stale are read only to filter a subset run, the tool gets id and text only"""
    data = json.loads(Path(qfile).read_text(encoding="utf-8"))
    qs = data["questions"]
    if subset:
        qs = [q for q in qs if set(q.get("gold") or []) | set(q.get("stale") or []) <= note_set]
    if limit:
        qs = qs[:limit]
    return [(q["id"], q["question"]) for q in qs]


def snapshot_of(model_dir):
    ref = MODELS_DIR / model_dir / "refs" / "main"
    return ref.read_text().strip() if ref.exists() else None


def run(a):
    alpha = MODES[a.mode]
    notes_root = Path(a.notes).resolve()
    work = Path(a.work).resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work folder is not fresh: %s" % work)
    for d in MODEL_DIRS:
        if not (MODELS_DIR / d).is_dir():
            raise SystemExit("model missing in %s, run fetch_models.py once first" % MODELS_DIR)

    rels = pick_notes(notes_root, a.subset_notes)
    note_set = set(rels)
    asks = pick_questions(a.questions, note_set, a.subset_notes, a.limit_questions)
    if not rels or not asks:
        raise SystemExit("nothing to do: %d notes, %d questions" % (len(rels), len(asks)))

    # corpus goes into the tool's default documents/ folder under a fresh KNOWLEDGE_RAG_DIR
    base = work / "kb"
    docs = base / "documents"
    for rel in rels:
        dst = docs / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(notes_root / rel, dst)
    (base / "config.yaml").write_text("paths:\n  models_cache_dir: %s\n" % json.dumps(str(MODELS_DIR)),
                                      encoding="utf-8")
    os.environ["KNOWLEDGE_RAG_DIR"] = str(base)
    os.environ.setdefault("KNOWLEDGE_RAG_WATCHER_DISABLED", "1")
    # onnxruntime 1.32 has its own telemetry client (device id file plus a ProcessInfo event queued under HOME),
    # the other telemetry variables do not reach it, so its own switch goes on before anything loads it
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    say("notes %d, questions %d, mode %s (hybrid_alpha %s)" % (len(rels), len(asks), a.mode, alpha))

    # config is resolved at import time, so the tool is imported only now
    import chromadb
    import fastembed
    import onnxruntime
    ort_off = getattr(onnxruntime, "disable_telemetry_events", None)
    if ort_off is not None:
        ort_off()  # before any session exists
    from mcp_server import __version__ as krag_version
    from mcp_server import server as srv

    cfg = srv.config
    if Path(cfg.documents_dir).resolve() != docs.resolve() or Path(cfg.models_cache_dir) != MODELS_DIR:
        raise SystemExit("tool did not pick up the work folder: %s" % cfg.documents_dir)

    # ---- index, timed: orchestrator init, model load from disk, parse, chunk, embed, store, BM25 build
    t0 = time.perf_counter()
    orch = srv.get_orchestrator()
    stats = orch.index_all(force=False)
    if alpha < 1.0:
        orch._ensure_bm25_index()  # the first query would build it otherwise; counted here, not in a query
    index_secs = time.perf_counter() - t0
    chunks = orch.collection.count()
    say("indexed %s files, %d chunks, %.1f s" % (stats.get("indexed"), chunks, index_secs))
    if stats.get("errors") or stats.get("indexed") != len(rels) or stats.get("skipped_reason"):
        raise SystemExit("indexing incomplete: %s" % json.dumps(stats, default=str)[:600])
    if alpha < 1.0 and not orch._bm25_initialized:
        raise SystemExit("BM25 index was not built")

    # ---- warm-up, untimed: loads the cross-encoder
    warm = json.loads(srv.search_knowledge(query=WARMUP, max_results=RAW, hybrid_alpha=alpha))
    if warm.get("status") not in ("success", "no_results"):
        raise SystemExit("warm-up failed: %s" % json.dumps(warm)[:400])
    # the tool falls back to fused order without a word when a model fails, so check both here
    if cfg.reranker_enabled and (orch.reranker._model is None or orch.reranker._load_failed):
        raise SystemExit("cross-encoder did not load")
    if alpha > 0 and orch.embed_fn._model is None:
        raise SystemExit("embedding model did not load")

    docs_resolved = docs.resolve()

    def to_rel(source):
        p = Path(source)
        for root in (docs_resolved, docs):
            try:
                return p.relative_to(root).as_posix()
            except ValueError:
                pass
        try:
            return p.resolve().relative_to(docs_resolved).as_posix()
        except (ValueError, OSError):
            return None

    results, query_ms, ids = {}, [], []
    raw_rows, distinct, sem_rows, rerank_rows = [], [], 0, 0
    cache_hit_ids, unmapped, debug = [], [], {}
    for n, (qid, text) in enumerate(asks):
        h0 = orch.query_cache.stats()["hits"]
        t = time.perf_counter()
        out = srv.search_knowledge(query=text, max_results=RAW, hybrid_alpha=alpha)
        query_ms.append((time.perf_counter() - t) * 1000.0)
        if orch.query_cache.stats()["hits"] > h0:
            cache_hit_ids.append(qid)
        payload = json.loads(out)
        status = payload.get("status")
        if status == "success":
            rows = payload.get("results") or []
        elif status == "no_results":
            rows = []
        else:
            raise SystemExit("query %s failed: %s" % (qid, json.dumps(payload)[:400]))
        hits, seen = [], set()
        for row in rows:
            sem_rows += row.get("semantic_rank") is not None
            rerank_rows += row.get("reranker_score") is not None
            rel = to_rel(row.get("source", ""))
            if rel is None or rel not in note_set:
                unmapped.append([qid, row.get("source", "")])
                continue
            if rel in seen:
                continue
            seen.add(rel)
            if len(hits) < K:
                hits.append({"path": rel, "copies": [], "newer": None})
        raw_rows.append(len(rows))
        distinct.append(len(seen))
        results[qid] = {"hits": hits}
        ids.append(qid)
        if n < a.debug_raw:
            debug[qid] = {"question": text, "raw": [
                {"source": r.get("source"), "mapped": to_rel(r.get("source", "")), "chunk_index": r.get("chunk_index"),
                 "reranker_score": r.get("reranker_score"), "semantic_rank": r.get("semantic_rank"),
                 "bm25_rank": r.get("bm25_rank")} for r in rows[:5]]}
        if (n + 1) % 50 == 0:
            say("%d/%d questions" % (n + 1, len(asks)))
    if unmapped:
        raise SystemExit("%d result rows did not map to a note, first: %s" % (len(unmapped), unmapped[:3]))

    total_rows = sum(raw_rows)
    if total_rows and cfg.reranker_enabled and rerank_rows != total_rows:
        raise SystemExit("cross-encoder scored %d of %d rows" % (rerank_rows, total_rows))
    if total_rows and alpha > 0 and sem_rows == 0:
        raise SystemExit("no row came from the vector side")

    mult = cfg.reranker_top_k_multiplier
    settings = {
        "interface": "in-process; index_all(force=False) as on first server start, then the MCP tool "
                     "function search_knowledge(); stdio transport not used",
        "search_knowledge_args": {"max_results": RAW, "hybrid_alpha": alpha, "category": None, "min_score": 0.0,
                                  "snippet_mode": True, "search_method": "auto", "include_folders": None,
                                  "exclude_folders": None},
        "mode_note": {"hybrid": "tool default (hybrid_alpha 0.3)", "keyword": "documented keyword-only (0.0)",
                      "semantic": "documented semantic-only (1.0)"}[a.mode],
        "config_yaml": {"paths": {"models_cache_dir": str(MODELS_DIR)}},
        "config_note": "no other key set; all search, chunking and model settings are tool defaults",
        "embedding_model": cfg.embedding_model, "embedding_dim": cfg.embedding_dim,
        "embedding_profile": cfg.embedding_profile, "query_prefix": cfg.query_prefix,
        "passage_prefix": cfg.passage_prefix, "gpu_mode": cfg.gpu_mode,
        "embedding_provider": orch.embed_fn._active_provider,
        "reranker_model": cfg.reranker_model, "reranker_enabled": cfg.reranker_enabled,
        "reranker_top_k_multiplier": mult,
        "candidates_per_query": {"semantic": RAW * max(3, mult) if alpha > 0 else 0,
                                 "bm25": RAW * 3 if alpha < 1 else 0, "reranked": RAW * mult},
        "chunk_size": cfg.chunk_size, "chunk_overlap": cfg.chunk_overlap,
        "markdown_split": "## and ### sections, frontmatter stripped (tool default)",
        "max_results_cap": cfg.max_results, "fts5_fast_path": cfg.fts5_enabled,
        "keyword_routes": "tool default, informational only", "query_expansions": "tool default",
        "query_cache": "tool default (100 entries, 300 s)", "watcher": "off", "preflight": "not run, fresh data dir",
        "onnxruntime_telemetry": {"ORT_DISABLE_TELEMETRY": os.environ.get("ORT_DISABLE_TELEMETRY"),
                                  "disable_telemetry_events_called": ort_off is not None},
        "bm25_build": "inside index_secs" if alpha < 1 else "not used",
        "warmup_query": WARMUP, "k_kept": K,
        "model_snapshots": {d: snapshot_of(d) for d in MODEL_DIRS},
        "env": {k: os.environ.get(k) for k in ENV_KEYS},
        "libs": {"knowledge-rag": krag_version, "fastembed": getattr(fastembed, "__version__", None),
                 "chromadb": getattr(chromadb, "__version__", None), "onnxruntime": onnxruntime.__version__,
                 "python": platform.python_version()},
        "machine": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
        "notes_indexed": len(rels), "chunks": chunks,
        "index_stats": {k: stats.get(k) for k in ("total_files", "indexed", "errors", "chunks_added",
                                                  "dedup_skipped")},
        "subset_notes": a.subset_notes, "limit_questions": a.limit_questions, "questions_asked": len(asks),
    }
    checks = {
        "raw_rows_min": min(raw_rows), "raw_rows_mean": round(total_rows / len(raw_rows), 1),
        "distinct_notes_min": min(distinct), "distinct_notes_mean": round(sum(distinct) / len(distinct), 1),
        "questions_under_k_notes": sum(1 for d in distinct if d < K),
        "rows_from_vector_side": sem_rows, "rows_reranked": rerank_rows, "rows_total": total_rows,
        "cache_hits": len(cache_hit_ids), "cache_hit_ids": cache_hit_ids,
        "peak_rss_children_mb": round(peak_rss_mb(resource.RUSAGE_CHILDREN), 1),
    }
    doc = {"status": "ok", "tool": "knowledge-rag", "version": krag_version, "mode": a.mode, "settings": settings,
           "index_secs": round(index_secs, 3), "query_ms": [round(x, 3) for x in query_ms], "query_ids": ids,
           "peak_rss_mb": round(peak_rss_mb(resource.RUSAGE_SELF), 1), "raw_asked": RAW, "results": results,
           "checks": checks}
    if debug:
        doc["debug_raw"] = debug
    return doc


def write(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None):
    a = parse_args(argv)
    try:
        doc = run(a)
    except BaseException as exc:  # SystemExit included: the runner always gets a file
        msg = str(exc) if isinstance(exc, SystemExit) else "%s: %s" % (type(exc).__name__, exc)
        write(a.out, {"status": "error", "tool": "knowledge-rag", "mode": a.mode, "error": msg,
                      "trace": traceback.format_exc()[-2000:]})
        say("error:", msg)
        return 1
    write(a.out, doc)
    ms = sorted(doc["query_ms"])
    say("done: index %.1f s, query median %.1f ms, peak rss %.0f MB, out %s" % (
        doc["index_secs"], ms[len(ms) // 2], doc["peak_rss_mb"], a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
