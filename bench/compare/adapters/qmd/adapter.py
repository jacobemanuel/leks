#!/usr/bin/env python3
# qmd adapter for the notes-search comparison on the public PEP benchmark. stdlib only.
#
# what it does, in order:
#   1. copies the notes (or the first --subset-notes of them, sorted) into --work/notes
#   2. indexes from scratch with the real qmd cli, HOME and XDG dirs inside --work:
#        qmd collection add <work>/notes --name pep          (all modes, default mask **/*.md)
#        qmd embed                                          (vsearch and query only, defaults)
#      both timed together as index_secs. models are already on disk (tool folder, symlinked in),
#      so nothing is downloaded here.
#   3. starts one warm node worker (qmd_worker.mjs) that opens the index like the cli does and calls
#      the same library function with the same options as `qmd search|vsearch|query`, asks for 100
#      results, one untimed warm-up, then every question timed as a round trip from here.
#   4. maps qmd://pep/<rel> to <rel>, keeps the first hit per note, top 20, writes the contract json.
# the tool only ever sees the question text (the id stays here). gold is used only to pick the
# smoke subset, never sent anywhere.
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "pkg", "node_modules", "@tobilu", "qmd")
QMD_LAUNCHER = os.path.join(PKG, "bin", "qmd")
WORKER = os.path.join(HERE, "qmd_worker.mjs")
MODELS = os.path.join(HERE, "home", ".cache", "qmd", "models")
COLL = "pep"
N_ASK = 100  # raw results asked per question (qmd returns whole notes, so this is plenty for 20 distinct)
K = 20  # distinct notes kept
MARK = "@@QMDW@@ "
WARMUP = "which vegetables grow well in a shaded garden"  # untimed, not a benchmark question
MODES = ("search", "vsearch", "query")
DEAD_PROXY = "http://127.0.0.1:9"  # qmd has no offline switch; any env-proxied request just fails
CALL_TIMEOUT_S = 900


def die(msg, out=None, mode=None):
    sys.stderr.write("qmd adapter: " + msg + "\n")
    if out:
        try:
            write_json(out, {"status": "error", "tool": "qmd", "mode": mode, "error": msg})
        except Exception:
            pass
    sys.exit(1)


def write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def rss_mb(ru_maxrss):
    # macOS reports bytes, linux kilobytes
    return ru_maxrss / (1024 * 1024) if sys.platform == "darwin" else ru_maxrss / 1024


def find_node():
    p = shutil.which("node")
    if p:
        return p
    if os.path.exists("/opt/homebrew/bin/node"):
        return "/opt/homebrew/bin/node"
    return None


def child_env(work, worker=False):
    home = os.path.join(work, "home")
    env = {}
    for k in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "USER", "LOGNAME", "SHELL"):
        if k in os.environ:
            env[k] = os.environ[k]
    env.update(
        {
            "HOME": home,
            "XDG_CACHE_HOME": os.path.join(home, ".cache"),
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "ANONYMIZED_TELEMETRY": "False",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "NODE_USE_ENV_PROXY": "1",
            "HTTP_PROXY": DEAD_PROXY,
            "HTTPS_PROXY": DEAD_PROXY,
            "ALL_PROXY": DEAD_PROXY,
            "http_proxy": DEAD_PROXY,
            "https_proxy": DEAD_PROXY,
            "all_proxy": DEAD_PROXY,
            "NO_PROXY": "",
            "no_proxy": "",
            "NO_COLOR": "1",
        }
    )
    if worker:
        # what bin/qmd sets before loading the cli: quiet native logs (mcp) and no metal residency (darwin)
        env.update({"LLAMA_LOG_LEVEL": "error", "GGML_LOG_LEVEL": "error", "GGML_BACKEND_SILENT": "1"})
        if sys.platform == "darwin":
            env["GGML_METAL_NO_RESIDENCY"] = "1"
        env["QMDW_PKG"] = PKG
    return env


def run_timed(cmd, env, cwd, log_path):
    # wall time plus the child's rusage (wait4); bin/qmd reaps its node child before exiting,
    # and macOS folds reaped children into the exit rusage (max for maxrss)
    with open(log_path, "ab") as log:
        log.write(("$ " + " ".join(cmd) + "\n").encode())
        log.flush()
        t0 = time.perf_counter()
        p = subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        _, status, ru = os.wait4(p.pid, 0)
        secs = time.perf_counter() - t0
    p.returncode = os.waitstatus_to_exitcode(status)
    return secs, p.returncode, rss_mb(ru.ru_maxrss)


class Worker:
    def __init__(self, node, env, cwd, err_path):
        self.err = open(err_path, "wb")
        self.p = subprocess.Popen(
            [node, WORKER], env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err, bufsize=0
        )
        self.q = queue.Queue()
        self.noise = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in iter(self.p.stdout.readline, b""):
            self.q.put(line)
        self.q.put(None)

    def call(self, obj, timeout=CALL_TIMEOUT_S):
        self.p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RuntimeError("worker timeout on op " + str(obj.get("op")))
            try:
                line = self.q.get(timeout=left)
            except queue.Empty:
                raise RuntimeError("worker timeout on op " + str(obj.get("op"))) from None
            if line is None:
                raise RuntimeError("worker closed stdout (see worker.stderr.log)")
            s = line.decode("utf-8", "replace")
            i = s.find(MARK)
            if i < 0:
                self.noise.append(s.rstrip("\n"))
                continue
            if i > 0:
                self.noise.append(s[:i])
            rep = json.loads(s[i + len(MARK):])
            if not rep.get("ok"):
                raise RuntimeError("worker error: " + str(rep.get("error"))[:2000])
            return rep

    def finish(self):
        # bye, then reap with wait4 for the worker's own peak rss
        rep = self.call({"op": "bye"}, timeout=120)
        self.p.stdin.close()
        _, status, ru = os.wait4(self.p.pid, 0)
        self.p.returncode = os.waitstatus_to_exitcode(status)
        self.err.close()
        return rep, self.p.returncode, rss_mb(ru.ru_maxrss)

    def kill(self):
        try:
            self.p.kill()
        except Exception:
            pass


def to_note(raw_file, raw_display, notes_set):
    for raw in (raw_file, raw_display):
        if not raw:
            continue
        p = raw
        if p.startswith("qmd://"):
            p = p[len("qmd://"):]
        p = p.split("?", 1)[0]
        if p.startswith(COLL + "/"):
            p = p[len(COLL) + 1:]
        if p in notes_set:
            return p
    return None


def inside(path, root):
    path = os.path.realpath(path)
    root = os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--mode", required=True, choices=MODES)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--limit-questions", type=int, default=None)
    ap.add_argument("--subset-notes", type=int, default=None)
    a = ap.parse_args()
    mode = a.mode
    out = os.path.abspath(a.out)
    work = os.path.abspath(a.work)
    t_start = time.time()

    if os.path.exists(work) and os.listdir(work):
        die("--work must be a fresh folder: " + work, out, mode)
    if not os.path.isdir(MODELS):
        die("models folder missing: " + MODELS, out, mode)
    node = find_node()
    if not node:
        die("node not found", out, mode)
    version = json.load(open(os.path.join(PKG, "package.json"), encoding="utf-8"))["version"]

    # notes: sorted relative .md paths, optional subset = first N
    notes_root = os.path.abspath(a.notes)
    rels = []
    for d, _, files in os.walk(notes_root):
        for fn in files:
            if fn.endswith(".md"):
                rels.append(os.path.relpath(os.path.join(d, fn), notes_root).replace(os.sep, "/"))
    rels.sort()
    if a.subset_notes:
        rels = rels[: a.subset_notes]
    notes_set = set(rels)

    # questions: id + text only go to the tool; gold only filters the smoke subset
    qdoc = json.load(open(a.questions, encoding="utf-8"))
    qs = qdoc["questions"] if isinstance(qdoc, dict) else qdoc
    if a.subset_notes:
        qs = [q for q in qs if q.get("gold") and all(g in notes_set for g in q["gold"])]
    if a.limit_questions:
        qs = qs[: a.limit_questions]
    if not qs:
        die("no questions selected", out, mode)
    asked = [(q["id"], q["question"]) for q in qs]
    if any(t == WARMUP for _, t in asked):
        die("warm-up text collides with a question", out, mode)

    # fresh work layout; models symlinked in so nothing is fetched or copied
    os.makedirs(work, exist_ok=True)
    home = os.path.join(work, "home")
    logs = os.path.join(work, "logs")
    notes_dst = os.path.join(work, "notes")
    for d in (os.path.join(home, ".cache", "qmd"), os.path.join(home, ".config"), logs):
        os.makedirs(d, exist_ok=True)
    os.symlink(MODELS, os.path.join(home, ".cache", "qmd", "models"))
    for r in rels:
        dst = os.path.join(notes_dst, r)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(notes_root, r), dst)

    env = child_env(work)
    qmd = [node, QMD_LAUNCHER]
    index_cmds = [qmd + ["collection", "add", notes_dst, "--name", COLL]]
    if mode in ("vsearch", "query"):
        index_cmds.append(qmd + ["embed"])
    index_secs = 0.0
    index_rss = 0.0
    index_steps = []
    for cmd in index_cmds:
        secs, rc, rss = run_timed(cmd, env, work, os.path.join(logs, "index.log"))
        index_steps.append({"cmd": " ".join(cmd[2:]), "secs": round(secs, 3), "rc": rc, "peak_rss_mb": round(rss, 1)})
        if rc != 0:
            hint = ""
            try:
                tail = open(os.path.join(logs, "index.log"), "rb").read()[-20000:].decode("utf-8", "replace")
                if "failed to create command queue" in tail or "Failed to create any embedding context" in tail:
                    hint = "; llama.cpp got no Metal context (a sandboxed shell blocks the GPU; run from a normal terminal)"
            except OSError:
                pass
            die("index step failed rc=%s: %s%s (see %s)" % (rc, " ".join(cmd[2:]), hint, os.path.join(logs, "index.log")), out, mode)
        index_secs += secs
        index_rss = max(index_rss, rss)
    sys.stderr.write("qmd adapter: indexed %d notes in %.1fs (%s)\n" % (len(rels), index_secs, mode))

    db_path = os.path.join(home, ".cache", "qmd", "index.sqlite")
    cfg_path = os.path.join(home, ".config", "qmd", "index.yml")
    if not os.path.exists(db_path) or not os.path.exists(cfg_path):
        die("index db or config not where expected inside --work", out, mode)

    w = Worker(node, child_env(work, worker=True), work, os.path.join(logs, "worker.stderr.log"))
    try:
        info = w.call({"op": "init", "mode": mode, "n": N_ASK, "collection": COLL}, timeout=300)["info"]
        if not inside(info["dbPath"], work) or not inside(info["configPath"], work) or info.get("localConfig"):
            raise RuntimeError("qmd store or config outside --work: %s %s %s" % (info["dbPath"], info["configPath"], info.get("localConfig")))
        st = info["status"]
        if st["totalDocuments"] != len(rels):
            raise RuntimeError("qmd indexed %s notes, expected %d" % (st["totalDocuments"], len(rels)))
        if mode in ("vsearch", "query"):
            ne = st["needsEmbedding"]
            ne = len(ne) if isinstance(ne, list) else ne
            if ne or not st["hasVectorIndex"]:
                raise RuntimeError("embeddings incomplete: needsEmbedding=%s hasVectorIndex=%s" % (ne, st["hasVectorIndex"]))

        if info.get("bootFails"):
            raise RuntimeError("qmd model fallbacks before the first query: %s" % info["bootFails"])
        dump = open(os.path.join(work, "raw_results.jsonl"), "w", encoding="utf-8")
        t0 = time.perf_counter()
        wu = w.call({"op": "q", "id": "_warmup", "text": WARMUP})
        warm_ms = (time.perf_counter() - t0) * 1000
        dump.write(json.dumps({"id": "_warmup", "ms_adapter": warm_ms, "ms_worker": wu["ms"], "diag": wu["diag"], "raw": wu["results"]}) + "\n")
        # the warm-up must have run every model this mode uses, without qmd's silent fallbacks
        wd = wu["diag"]
        if wd["fail"]:
            raise RuntimeError("qmd models did not run in the warm-up, fallbacks %s (no usable Metal context?)" % wd["fail"])
        if mode == "query" and not (wd["reranked"] and wd["rerank_llm_calls"] >= 1 and wd["embedded"]):
            raise RuntimeError("warm-up did not embed and rerank: %s" % wd)
        if mode == "vsearch" and wd["expanded"] is None:
            raise RuntimeError("warm-up did not expand: %s" % wd)
        device = w.call({"op": "device"}, timeout=120).get("info")

        results = {}
        query_ms = []
        worker_ms = []
        unmapped = 0
        short = 0
        strong = 0
        degraded = []
        for i, (qid, text) in enumerate(asked):
            t0 = time.perf_counter()
            rep = w.call({"op": "q", "id": qid, "text": text})
            ms = (time.perf_counter() - t0) * 1000
            query_ms.append(ms)
            worker_ms.append(rep["ms"])
            if rep["diag"].get("strong") is not None:
                strong += 1
            if rep["diag"]["fail"]:
                degraded.append(qid)
            seen = set()
            hits = []
            for r in rep["results"]:
                p = to_note(r.get("file"), r.get("display"), notes_set)
                if p is None:
                    unmapped += 1
                    continue
                if p in seen:
                    continue
                seen.add(p)
                hits.append({"path": p, "copies": [], "newer": None})
                if len(hits) == K:
                    break
            if len(hits) < K:
                short += 1
            results[qid] = {"hits": hits}
            dump.write(json.dumps({"id": qid, "ms_adapter": ms, "ms_worker": rep["ms"], "diag": rep["diag"], "raw": rep["results"]}) + "\n")
            if (i + 1) % 50 == 0:
                sys.stderr.write("qmd adapter: %d/%d questions, %.0fs\n" % (i + 1, len(asked), time.time() - t_start))
        dump.close()
        bye, rc, peak = w.finish()
        if degraded:
            # numbers from a half-working model run would not be qmd's; raw_results.jsonl keeps the details
            raise RuntimeError("%d questions hit a qmd model fallback, first %s" % (len(degraded), degraded[:5]))
    except Exception as e:
        w.kill()
        die("%s: %s" % (type(e).__name__, e), out, mode)

    calls = {
        "search": "searchFTS(db, text, max(50, 2*100), 'pep'), keep score >= 0, first 100 (= qmd search -n 100 --json)",
        "vsearch": "vectorSearchQuery(store, text, {collection:'pep', limit:100, minScore:0.3}) in withLLMSession (= qmd vsearch -n 100 --json)",
        "query": "hybridQuery(store, text, {collection:'pep', limit:100, minScore:0}) in withLLMSession (= qmd query -n 100 --json, same as the MCP query tool)",
    }
    qsorted = sorted(query_ms)
    settings = {
        "transport": "one warm node worker running qmd's own library calls with the cli defaults; MCP stdio has no vsearch tool",
        "call": calls[mode],
        "index": [s["cmd"] for s in index_steps],
        "collection": COLL,
        "mask": "**/*.md (default)",
        "embed_defaults": "regex chunking, 900 token chunks, 135 overlap" if mode != "search" else None,
        "raw_asked": N_ASK,
        "keep_top": K,
        "min_score": {"search": 0, "vsearch": 0.3, "query": 0}[mode],
        "rerank": mode == "query",
        "query_expansion": mode in ("vsearch", "query"),
        "models": info["models"] if mode != "search" else None,
        "model_files_dir": MODELS,
        "constants": info["constants"],
        "device": device,
        "node": node + " " + info["node"],
        "env": {
            k: v
            for k, v in child_env(work, worker=True).items()
            if k not in ("PATH", "TMPDIR", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "QMDW_PKG")
        },
        "network": "no offline switch in qmd; proxy env points at a dead port, models load from local files",
        "warmup": WARMUP,
        "warmup_ms": round(warm_ms, 1),
        "query_ms_scope": "round trip adapter to warm worker per question; no process start; models loaded in warm-up",
        "llm_cache": "qmd default on: expansion and rerank outputs cached in the index db, repeated texts reuse them",
        "notes_indexed": len(rels),
        "questions": len(asked),
        "subset_notes": a.subset_notes,
        "limit_questions": a.limit_questions,
        "measure": {
            "index_steps": index_steps,
            "index_peak_rss_mb": round(index_rss, 1),
            "worker_maxrss_mb_self": round(bye["maxrss_kb"] / 1024, 1),
            "worker_rc": rc,
            "query_ms_median": round(qsorted[len(qsorted) // 2], 1),
            "query_ms_p95": round(qsorted[min(len(qsorted) - 1, int(0.95 * len(qsorted)))], 1),
            "worker_ms_total": round(sum(worker_ms), 1),
            "strong_bm25_skips": strong if mode == "query" else None,
            "model_fallback_questions": len(degraded),
            "unmapped_raw_hits": unmapped,
            "questions_under_20_hits": short,
            "stdout_noise_lines": len(w.noise),
            "wall_secs": round(time.time() - t_start, 1),
        },
    }
    write_json(
        out,
        {
            "status": "ok",
            "tool": "qmd",
            "version": version,
            "mode": mode,
            "settings": settings,
            "index_secs": round(index_secs, 3),
            "query_ms": [round(x, 3) for x in query_ms],
            "peak_rss_mb": round(peak, 1),
            "raw_asked": N_ASK,
            "results": results,
        },
    )
    sys.stderr.write(
        "qmd adapter: ok %s, %d questions, index %.1fs, median %.1f ms, peak rss %.0f MB -> %s\n"
        % (mode, len(asked), index_secs, settings["measure"]["query_ms_median"], peak, out)
    )


if __name__ == "__main__":
    main()
