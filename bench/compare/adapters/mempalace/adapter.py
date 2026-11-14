#!/usr/bin/env python3
# mempalace adapter for the leks public pep bench comparison
# run it with the tool's own venv python and -I:
#   venv/bin/python -I adapter.py --notes DIR --questions FILE --mode MODE --out FILE.json --work FRESH_DIR
#       [--limit-questions N] [--subset-notes N] [--show N] [--check mapped|all]
# modes
#   default  `mempalace search` as shipped: minilm vector pool (n*3), re-ranked 0.6 cosine + 0.4 bm25
#   raw      same cli path, documented weights set to vector 1.0 / bm25 0.0, so pure minilm cosine order
#   hybrid   mcp `mempalace_search` with candidate_strategy "union" (bm25 candidates merged before the re-rank)
# index = `mempalace --palace P init DIR --yes --no-llm` then `mempalace --palace P mine DIR`, both timed
# the minilm model must already sit in the tool folder, a run never downloads anything
import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import resource
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parent
VENV = TOOL / "venv"
CLI = VENV / "bin" / "mempalace"
MODEL_DIR = TOOL / "home" / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"
MODEL_FILES = ("config.json", "model.onnx", "special_tokens_map.json",
               "tokenizer_config.json", "tokenizer.json", "vocab.txt")
MODEL_SHA256 = "4f148ba8ae9c2c7fbee4af2b132db8d06c6a6545b47fc83bbb98c3d22b8393e6"
ARCHIVE_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
K = 20
RAW_ASKED = 100  # mcp caps limit at 100, the cli takes it as --results 100
WARMUP = "warm up"  # untimed, never a bench question
MODES = ("default", "raw", "hybrid")

HDR = re.compile(r"^  \[(\d+)\] (.*)$")
SRC = re.compile(r"^      Source: (.*)$")
SEP = "  " + "-" * 56


def log(*a):
    print("[mempalace-adapter]", *a, file=sys.stderr, flush=True)


def rss_mb(v):
    # macos reports bytes, linux kilobytes
    return round(v / 1048576.0 if sys.platform == "darwin" else v / 1024.0, 1)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def norm(s):
    return " ".join(s.split())


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def fail(out, mode, msg, **extra):
    d = {"status": "error", "tool": "mempalace", "mode": mode, "error": msg}
    d.update(extra)
    write_json(out, d)
    log("ERROR:", msg)
    sys.exit(1)


def list_notes(root):
    out = []
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in fn:
            if f.endswith(".md"):
                out.append((Path(dp) / f).relative_to(root).as_posix())
    return sorted(out)


def load_questions(path, subset, limit):
    d = json.load(open(path, encoding="utf-8"))
    qs = d["questions"] if isinstance(d, dict) else d
    if subset is not None:
        # harness side only: keep questions whose targets sit in the subset, the tool never sees gold
        keep = set(subset)
        qs = [q for q in qs if q.get("gold") and all(g in keep for g in q["gold"])]
    if limit:
        qs = qs[:limit]
    return [(q["id"], q["question"]) for q in qs]


def tool_env(work, mode):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("MEMPALACE_") and k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}
    home = work / "home"
    env.update({
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "MEMPALACE_PALACE_PATH": str(work / "palace"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "ANONYMIZED_TELEMETRY": "False",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        # onnxruntime 1.32 runs its own 1DS telemetry (device id + event queue under HOME), only this switch stops it
        "ORT_DISABLE_TELEMETRY": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if mode == "raw":
        env["MEMPALACE_HYBRID_VECTOR_WEIGHT"] = "1.0"
        env["MEMPALACE_HYBRID_BM25_WEIGHT"] = "0.0"
    return env


def run_logged(cmd, env, logfile, cwd):
    with open(logfile, "w", encoding="utf-8") as lf:
        lf.write("$ " + " ".join(cmd) + "\n")
        lf.flush()
        t = time.perf_counter()
        r = subprocess.run(cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT)
        dt = time.perf_counter() - t
    return r.returncode, dt


def palace_stats(palace):
    # read only peek into chroma's sqlite: rows and distinct source files per collection
    dbs = []
    for dp, _dn, fn in os.walk(palace):
        if "chroma.sqlite3" in fn:
            dbs.append(Path(dp) / "chroma.sqlite3")
    out = {}
    for db in dbs:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            rows = con.execute(
                "SELECT c.name, COUNT(DISTINCT e.id), "
                "COUNT(DISTINCT CASE WHEN m.key='source_file' THEN m.string_value END) "
                "FROM embeddings e JOIN segments s ON e.segment_id = s.id "
                "JOIN collections c ON s.collection = c.id "
                "LEFT JOIN embedding_metadata m ON m.id = e.id GROUP BY c.name").fetchall()
        finally:
            con.close()
        for name, n, src in rows:
            out[name] = {"rows": n, "source_files": src}
    return out


def parse_cli(text):
    # the cli prints "  [i] wing / room" then "      Source: <basename>", optional "Also in", "Match", a blank,
    # the chunk text indented by 6 spaces, then a dashed separator; chunk lines always start with 6 spaces
    lines = text.split("\n")
    hits = []
    i = 0
    while i < len(lines):
        m = HDR.match(lines[i])
        s = SRC.match(lines[i + 1]) if (m and i + 1 < len(lines)) else None
        if not (m and s and int(m.group(1)) == len(hits) + 1):
            i += 1
            continue
        j = i + 2
        while j < len(lines) and lines[j] != "" and lines[j] != SEP:
            j += 1  # Also in / Match lines
        j += 1  # the blank line before the text
        body = []
        while j < len(lines) and lines[j] != SEP:
            body.append(lines[j][6:] if lines[j].startswith("      ") else lines[j])
            j += 1
        while body and body[-1] == "":
            body.pop()
        hits.append((s.group(1), "\n".join(body)))
        i = j + 1
    return hits, ("(vector disabled)" in text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--mode", required=True, choices=MODES)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--limit-questions", type=int, default=0)
    ap.add_argument("--subset-notes", type=int, default=0)
    ap.add_argument("--show", type=int, default=0, help="print this many raw hits per question for the first 3 questions")
    ap.add_argument("--check", choices=("mapped", "all"), default="mapped",
                    help="mapped: chunk text must sit in the mapped note; all: also scan every note (slow, smoke only)")
    a = ap.parse_args()
    mode = a.mode
    notes = Path(a.notes).resolve()
    work = Path(a.work).resolve()
    out = Path(a.out).resolve()

    if work.exists() and any(work.iterdir()):
        fail(out, mode, "work dir is not fresh: %s" % work)
    work.mkdir(parents=True, exist_ok=True)

    # model check, never download in a run
    missing = [f for f in MODEL_FILES if not (MODEL_DIR / "onnx" / f).is_file()]
    if missing:
        fail(out, mode, "minilm model missing in tool folder: %s" % ",".join(missing))
    model_sha = sha256(MODEL_DIR / "onnx" / "model.onnx")
    if model_sha != MODEL_SHA256:
        fail(out, mode, "model.onnx sha256 mismatch: %s" % model_sha)

    all_notes = list_notes(notes)
    sel = all_notes[:a.subset_notes] if a.subset_notes else all_notes
    qs = load_questions(a.questions, sel if a.subset_notes else None, a.limit_questions)
    if not sel or not qs:
        fail(out, mode, "nothing to do: %d notes, %d questions" % (len(sel), len(qs)))

    corpus = work / "notes"  # a copy, because init writes mempalace.yaml, entities.json and .gitignore into it
    t = time.perf_counter()
    for rel in sel:
        dst = corpus / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(notes / rel, dst)
    copy_secs = time.perf_counter() - t
    by_name = {}
    for rel in sel:
        by_name.setdefault(Path(rel).name, []).append(rel)
    dup_names = sorted(n for n, v in by_name.items() if len(v) > 1)
    if dup_names and mode != "hybrid":
        fail(out, mode, "cli prints basenames only and these repeat: %s" % ",".join(dup_names[:5]))

    env = tool_env(work, mode)
    mdst = work / "home" / ".cache" / "chroma" / "onnx_models"
    mdst.mkdir(parents=True, exist_ok=True)
    os.symlink(MODEL_DIR, mdst / "all-MiniLM-L6-v2")
    (work / "logs").mkdir(exist_ok=True)
    palace = work / "palace"

    init_cmd = [str(CLI), "--palace", str(palace), "init", str(corpus), "--yes", "--no-llm"]
    mine_cmd = [str(CLI), "--palace", str(palace), "mine", str(corpus)]
    log("index: init + mine on %d notes" % len(sel))
    rc, init_secs = run_logged(init_cmd, env, work / "logs" / "init.log", work)
    if rc != 0:
        fail(out, mode, "init failed rc=%d, see %s" % (rc, work / "logs" / "init.log"))
    rc, mine_secs = run_logged(mine_cmd, env, work / "logs" / "mine.log", work)
    if rc != 0:
        fail(out, mode, "mine failed rc=%d, see %s" % (rc, work / "logs" / "mine.log"))
    index_secs = init_secs + mine_secs
    child_rss = rss_mb(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    log("index done: init %.1fs, mine %.1fs, child peak rss %.0f MB" % (init_secs, mine_secs, child_rss))
    try:
        stats = palace_stats(palace)
    except Exception as e:  # stats are a record, never a reason to stop
        stats = {"error": repr(e)}

    # query side: same env inside this process, set before anything imports chromadb
    for k in [k for k in os.environ if k.startswith("MEMPALACE_")]:
        del os.environ[k]
    os.environ.update(env)
    import chromadb
    import onnxruntime
    from mempalace.version import __version__ as mp_version

    fallbacks = 0
    sanitized = 0
    if mode in ("default", "raw"):
        from mempalace.query_sanitizer import sanitize_query, sanitizer_report
        from mempalace.searcher import SearchError, search

        def ask(q):
            nonlocal sanitized
            rep = sanitizer_report(sanitize_query(q))  # same as cmd_search
            if rep:
                sanitized += 1
                q = rep["clean_query"]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    search(query=q, palace_path=str(palace), n_results=RAW_ASKED)
                except SearchError as e:
                    raise RuntimeError("SearchError: %s" % e) from e
            return buf.getvalue()

        def raw_hits(res):
            nonlocal fallbacks
            hits, fb = parse_cli(res)
            fallbacks += int(fb)
            return [(src, (by_name.get(src) or [None])[0], txt) for src, txt in hits]
    else:
        import mempalace.mcp_server as ms  # note: this import points fd 1 at stderr, the mcp stdio guard

        croot = corpus.resolve()

        def ask(q):
            return ms.tool_search(query=q, limit=RAW_ASKED, candidate_strategy="union")

        def raw_hits(res):
            nonlocal fallbacks
            if not isinstance(res, dict) or res.get("error"):
                raise RuntimeError("tool_search error: %s" % (res.get("error") if isinstance(res, dict) else res))
            if res.get("vector_disabled") or res.get("fallback"):
                fallbacks += 1
            outl = []
            for h in res.get("results") or []:
                sp = h.get("source_path") or ""
                try:
                    rel = Path(sp).resolve().relative_to(croot).as_posix()
                except (ValueError, OSError):
                    rel = None
                outl.append((sp, rel, h.get("text") or ""))
            return outl

    ask(WARMUP)  # untimed warm-up
    log("queries: %d, mode %s" % (len(qs), mode))
    results = {}
    query_ms = []
    errors = []
    short20 = short10 = dropped = 0
    n_raw = []
    checks = {"checked": 0, "in_mapped": 0, "not_in_mapped": 0, "in_mapped_only": 0, "elsewhere_only": 0}
    bad_examples = []
    texts = {}

    def note_text(rel):
        if rel not in texts:
            texts[rel] = norm((corpus / rel).read_text(encoding="utf-8", errors="replace"))
        return texts[rel]

    for qi, (qid, qtext) in enumerate(qs):
        res = None
        for attempt in (1, 2):
            t = time.perf_counter()
            try:
                res = ask(qtext)
                dt = (time.perf_counter() - t) * 1000.0
                rh = raw_hits(res)
                break
            except Exception as e:
                dt = (time.perf_counter() - t) * 1000.0
                rh = None
                if attempt == 2:
                    errors.append({"id": qid, "error": repr(e)[:300]})
        query_ms.append(round(dt, 3))
        rh = rh or []
        n_raw.append(len(rh))
        seen = []
        for label, rel, txt in rh:
            if rel is None:
                dropped += 1
                continue
            if rel not in seen and len(seen) < K:
                seen.append(rel)
            # mapping check: the returned chunk text must sit in the note we map it to
            nt = norm(txt)
            if nt:
                checks["checked"] += 1
                ok = nt in note_text(rel)
                checks["in_mapped" if ok else "not_in_mapped"] += 1
                if not ok and len(bad_examples) < 5:
                    bad_examples.append({"id": qid, "label": label, "mapped": rel, "text": txt[:120]})
                if a.check == "all":
                    others = [r for r in sel if r != rel and nt in note_text(r)]
                    if ok and not others:
                        checks["in_mapped_only"] += 1
                    if not ok and others:
                        checks["elsewhere_only"] += 1
        if len(seen) < K:
            short20 += 1
        if len(seen) < 10:
            short10 += 1
        results[qid] = {"hits": [{"path": p, "copies": [], "newer": None} for p in seen]}
        if a.show and qi < 3:
            log("Q %s %r: %d raw hits, %d notes" % (qid, qtext, len(rh), len(seen)))
            for r, (label, rel, txt) in enumerate(rh[:a.show], 1):
                inm = (norm(txt) in note_text(rel)) if rel else False
                log("  raw %2d  %-28s -> %-22s in_file=%s  %r" % (r, Path(label).name, rel, inm, norm(txt)[:60]))

    self_rss = rss_mb(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    qsorted = sorted(query_ms)
    settings = {
        "interface": {"default": "cli search (searcher.search, as cmd_search)",
                      "raw": "cli search (searcher.search, as cmd_search)",
                      "hybrid": "mcp mempalace_search (tool_search)"}[mode],
        "equivalent_command": {"default": "mempalace --palace PALACE search QUERY --results 100",
                               "raw": "MEMPALACE_HYBRID_VECTOR_WEIGHT=1.0 MEMPALACE_HYBRID_BM25_WEIGHT=0.0 mempalace --palace PALACE search QUERY --results 100",
                               "hybrid": "mempalace_search {query, limit: 100, candidate_strategy: union}"}[mode],
        "index_commands": ["mempalace --palace PALACE init NOTES --yes --no-llm", "mempalace --palace PALACE mine NOTES"],
        "init_flags_why": "--yes auto-accepts detected entities (non-interactive); --no-llm skips the local ollama refinement, the documented fallback is the same heuristics",
        "embedding_model": "minilm (default): all-MiniLM-L6-v2 onnx via chromadb, 384 dim, max 256 tokens, cpu",
        "model_onnx_sha256": model_sha,
        "model_archive_sha256_pin": ARCHIVE_SHA256,
        "chunking": "800 chars, 100 overlap, min 50 (defaults)",
        "backend": "chroma (default)",
        "n_results": RAW_ASKED,
        "candidate_pool": {"default": "vector n*3 = 300, widened x4 up to 500 when folds leave it short",
                           "raw": "vector n*3 = 300, widened x4 up to 500 when folds leave it short",
                           "hybrid": "vector n*4 + bm25 n*3 (union), closet boost, then hybrid re-rank"}[mode],
        "hybrid_weights": {"default": [0.6, 0.4], "raw": [1.0, 0.0], "hybrid": [0.6, 0.4]}[mode],
        "mapping": "cli Source basename -> its path under the notes dir; mcp source_path -> relative path; first occurrence per note, top %d; also_in copies not mapped" % K,
        "notes": len(sel),
        "notes_subset": a.subset_notes or None,
        "questions": len(qs),
        "copy_secs": round(copy_secs, 3),
        "init_secs": round(init_secs, 3),
        "mine_secs": round(mine_secs, 3),
        "palace": stats,
        "versions": {"mempalace": mp_version, "chromadb": chromadb.__version__,
                     "onnxruntime": onnxruntime.__version__, "python": sys.version.split()[0]},
        "rss_mb": {"self_queries": self_rss, "children_index": child_rss},
        "raw_hits_per_query": {"min": min(n_raw), "max": max(n_raw)},
        "questions_under_20_notes": short20,
        "questions_under_10_notes": short10,
        "non_note_hits_dropped": dropped,
        "queries_sanitized": sanitized,
        "bm25_only_fallbacks": fallbacks,
        "query_errors": len(errors),
        "error_examples": errors[:5],
        "mapping_check": dict(checks, mode=a.check, bad_examples=bad_examples),
        "query_ms_p50_p95": [qsorted[len(qsorted) // 2], qsorted[min(len(qsorted) - 1, int(len(qsorted) * 0.95))]],
        "offline_env": {k: env[k] for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "ANONYMIZED_TELEMETRY",
                                            "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK", "ORT_DISABLE_TELEMETRY")},
        "warmup_query": WARMUP,
    }
    doc = {
        "status": "ok" if not errors else "error",
        "tool": "mempalace",
        "version": mp_version,
        "mode": mode,
        "settings": settings,
        "index_secs": round(index_secs, 3),
        "query_ms": query_ms,
        "peak_rss_mb": max(self_rss, child_rss),
        "raw_asked": RAW_ASKED,
        "results": results,
    }
    write_json(out, doc)
    log("done: index %.1fs, %d queries, p50 %.1f ms, peak rss %.0f MB, errors %d, short20 %d, short10 %d, check %s"
        % (index_secs, len(qs), settings["query_ms_p50_p95"][0], doc["peak_rss_mb"], len(errors), short20, short10,
           {k: checks[k] for k in ("checked", "in_mapped", "not_in_mapped")}))
    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
