# one-time model fetch for the knowledge-rag adapter
# uses the tool's own loaders (FastEmbedEmbeddings, CrossEncoderReranker) with its defaults,
# only models_cache_dir points into this tool folder so the adapter runs can read them offline
import json
import os
import shutil
import sys
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parent
MODELS = TOOL / "models_cache"
BASE = TOOL / "fetch_base"


def main():
    if BASE.exists():
        shutil.rmtree(BASE)
    BASE.mkdir(parents=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    (BASE / "config.yaml").write_text("paths:\n  models_cache_dir: %s\n" % json.dumps(str(MODELS)))
    os.environ["KNOWLEDGE_RAG_DIR"] = str(BASE)
    os.environ["KNOWLEDGE_RAG_WATCHER_DISABLED"] = "1"
    # onnxruntime 1.32 telemetry client off (found after the first fetch, see adapter.py)
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"

    from mcp_server import server as srv

    t = time.perf_counter()
    emb = srv.FastEmbedEmbeddings()
    emb._load_model()
    vec = emb.embed_query("hello world")[0]
    print("embedding", emb.model_name, "dim", len(vec), "provider", emb._active_provider,
          "secs %.1f" % (time.perf_counter() - t))

    t = time.perf_counter()
    rr = srv.CrossEncoderReranker()
    ok = rr._ensure_model()
    print("reranker", rr.model_name, "loaded", ok, "secs %.1f" % (time.perf_counter() - t))
    if not ok:
        sys.exit(1)
    scored = rr.rerank("python packaging", [{"document": "how to package python code"},
                                            {"document": "a recipe for bread"}], top_k=2)
    print("rerank check", [(d["document"], round(d["reranker_score"], 3)) for d in scored])
    shutil.rmtree(BASE)


if __name__ == "__main__":
    main()
