#!/usr/bin/env python3
"""Benchmark harness for the leks search engine.

Builds an index of a corpus into a temp home, asks every question of a
question file against several systems and reports rank metrics plus paired
comparisons. The defaults use the small synthetic fixtures in this repo.

    python3 bench/run_bench.py --models none
    python3 bench/run_bench.py --models fake --compare words:fused,fused:full

Pass --config and --questions to benchmark your own corpus. The question
file format is the one in bench/fixtures/questions.json.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import importlib.util
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_stats():
    # load by path so this works as a script and as an imported module
    spec = importlib.util.spec_from_file_location("leks_bench_stats", os.path.join(HERE, "stats.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stats = _load_stats()

FIXTURE_CONFIG = os.path.join(HERE, "fixtures", "config.json")
FIXTURE_QUESTIONS = os.path.join(HERE, "fixtures", "questions.json")
FIXTURE_PDF = os.path.join(HERE, "fixtures", "corpus", "docs", "cache-study.pdf")

# page text of the fixture pdf, written with tests/pdfgen.write_pdf
PDF_PAGES = [
    "Tile cache replay study\n"
    "Method: 200 recorded camera paths were replayed against caches of 128, 256, 512 and 1024 tiles.\n"
    "Hit rate with least recently used eviction:\n"
    "128 tiles 71 percent\n"
    "256 tiles 84 percent\n"
    "512 tiles 95 percent\n"
    "1024 tiles 97 percent",
    "Eviction policies at 256 tiles\n"
    "Least recently used 84 percent\n"
    "First in, first out 77 percent\n"
    "Random replacement 73 percent\n"
    "Conclusion: keep least recently used with a pinned viewport set.\n"
    "Above 512 tiles extra capacity adds under 2 points.",
]

# search settings per system, None means the grep baseline
SYSTEMS = {
    "grep": None,
    "words": dict(mode="bm25", priors=False, words_only=True, rerank=False),
    "fused": dict(mode="full", words_only=True, rerank=False),
    "hybrid": dict(mode="full", rerank=False),
    "full": dict(mode="full", rerank=True),
}
MODEL_SYSTEMS = ("hybrid", "full")
# what --models config needs from the models extra, the daemon imports all of them
MODEL_PACKAGES = ("numpy", "torch", "transformers", "safetensors")
# start words after which no daemon is coming, so there is nothing to wait for
NOT_COMING = {
    "no models": "no models configured",
    "no backend": 'model backend not installed, run: pip install -e ".[models]" in the LEKS clone',
    "no weights": "model weights not found, run: leks models fetch (or pass --weights)",
    "refused": "the daemon could not be spawned, leks modeld serve shows why",
}

# what a question may need from the build, mapped to a capabilities key
NEEDS = {"pdf": "pdf_text", "ocr": "ocr", "images": "images", "docx": "docx"}

TEXT_EXT = frozenset(
    (".md .markdown .txt .rst .tex .html .htm .py .sh .bash .zsh .swift .metal .cs .c .h .cpp .cc .hpp "
     ".m .mm .js .ts .go .rs .java .kt .toml .yml .yaml .json").split())

STOP = frozenset(
    ("a an and are as at be been but by can could did do does for from had has have how i in into is it its "
     "me my of on or our should so than that the their them then there these they this to was we were what "
     "when where which who whom why will with would you your about after again all also any because before "
     "being between both each few more most other over same some such through under until very").split())

FAKE_EMBEDDER = {
    "key": "mdense", "hf_id": "fake/dense", "revision": "0" * 40, "op": "embed", "pooling": "cls",
    "normalize": True, "prefixes": {"q": "query: ", "d": "document: "}, "dim": 64, "max_length": 512,
    "dtype": "float32",
}
FAKE_RERANKER = {
    "key": "mlate", "hf_id": "fake/late", "revision": "1" * 40, "op": "maxsim", "pooling": "maxsim",
    "normalize": True, "prefixes": {"q": "[Q] ", "d": "[D] "}, "dim": 16, "max_length": 512,
    "dtype": "float32",
}

SMALL_NOTE = ("These numbers come from the small synthetic fixture set: they check the harness and show how "
              "the lanes compare on synthetic text. The measurements on a real record are in docs/report-3.0.md.")


# ---------------------------------------------------------------- pure helpers

def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_questions(path):
    """Read a question file, return (questions, note). Raises ValueError on a bad entry."""
    d = read_json(path)
    qs = d["questions"] if isinstance(d, dict) else d
    note = d.get("_note", "") if isinstance(d, dict) else ""
    seen = set()
    for q in qs:
        for key in ("id", "question", "gold", "category"):
            if key not in q:
                raise ValueError("question without %r: %r" % (key, q.get("id")))
        if q["id"] in seen:
            raise ValueError("duplicate question id " + q["id"])
        seen.add(q["id"])
        q.setdefault("stale", [])
        q.setdefault("needs", [])
    return qs, note


def hit_paths(hit):
    """Display paths that count as this hit: the path and its copies."""
    out = {hit.get("path")}
    out.update(hit.get("_copies") or [])
    out.discard(None)
    return out


def rank_of(gold, ranked):
    """1-based position of the first entry that shares a path with gold, else None."""
    g = set(gold or [])
    if not g:
        return None
    for i, paths in enumerate(ranked):
        if g & set(paths):
            return i + 1
    return None


def rr(rank, cutoff=10):
    return 1.0 / rank if rank is not None and rank <= cutoff else 0.0


def percentile(values, p):
    """Nearest-rank percentile of a list, p in [0, 100]."""
    if not values:
        return 0.0
    s = sorted(values)
    i = max(0, min(len(s) - 1, int(-(-p * len(s) // 100)) - 1))
    return s[i]


def summarize(rows):
    """Metrics of one system. Each row: id, cat, una, rank, stale_rank, ms, low."""
    ans = [r for r in rows if not r["una"]]
    una = [r for r in rows if r["una"]]
    ranks = [r["rank"] for r in ans]
    ms = [r["ms"] for r in rows]
    out = {
        "status": "ok",
        "n": len(ans),
        "n_unanswerable": len(una),
        "top1": round(stats.topk(ranks, 1), 4),
        "top3": round(stats.topk(ranks, 3), 4),
        "top5": round(stats.topk(ranks, 5), 4),
        "top10": round(stats.topk(ranks, 10), 4),
        "mrr10": round(stats.mrr(ranks, 10), 4),
        "miss10": sum(1 for r in ranks if r is None or r > 10),
        "p50_ms": round(statistics.median(ms), 2) if ms else 0.0,
        "p95_ms": round(percentile(ms, 95), 2),
        "abstain_unanswerable": None,
        "false_abstain": None,
        "current_above_stale": None,
    }
    if una and all(r["low"] is not None for r in una):
        out["abstain_unanswerable"] = round(sum(1 for r in una if r["low"]) / len(una), 4)
    if ans and all(r["low"] is not None for r in ans):
        out["false_abstain"] = round(sum(1 for r in ans if r["low"]) / len(ans), 4)
    cur = [r for r in ans if r["cat"] == "current"]
    if cur:
        ok = sum(1 for r in cur
                 if r["rank"] is not None and (r["stale_rank"] is None or r["rank"] < r["stale_rank"]))
        out["current_above_stale"] = round(ok / len(cur), 4)
    by = {}
    for cat in sorted({r["cat"] for r in ans}):
        rk = [r["rank"] for r in ans if r["cat"] == cat]
        by[cat] = {"n": len(rk), "top1": round(stats.topk(rk, 1), 4), "top5": round(stats.topk(rk, 5), 4)}
    out["by_category"] = by
    return out


def unmet_needs(q, caps):
    """Needs of a question that the build cannot meet."""
    miss = []
    for n in q.get("needs") or []:
        key = NEEDS.get(n)
        if key is None or not caps.get(key):
            miss.append(n)
    return miss


def split_questions(qs, caps, visible_roots, exists):
    """Return (kept, skipped). A skipped question is reported, never scored as a miss."""
    kept, skipped = [], []
    for q in qs:
        gold = q.get("gold") or []
        why = None
        miss = unmet_needs(q, caps)
        if miss:
            why = "needs " + ",".join(miss)
        elif gold and not any(g.split("/", 1)[0] in visible_roots for g in gold):
            why = "gold outside the profile"
        elif gold and not any(exists(g) for g in gold):
            why = "gold file missing"
        if why:
            skipped.append({"id": q["id"], "category": q["category"], "reason": why})
        else:
            kept.append(q)
    return kept, skipped


def grep_scan(texts, question, limit=50):
    """Rank files by how often any query term occurs, like a plain grep would.

    texts maps display path to lowercased text. Returns display paths, best first.
    """
    terms = sorted({t for t in re.findall(r"[a-z0-9_]+", question.lower()) if len(t) > 2 and t not in STOP})
    if not terms:
        return []
    rx = re.compile("|".join(re.escape(t) for t in terms))
    rows = []
    for path, text in texts.items():
        n = len(rx.findall(text))
        if n:
            rows.append((-n, path))
    rows.sort()
    return [p for _, p in rows[:limit]]


def lanes_line(r):
    s = r.get("lanes") or ""
    wl = r.get("word_lanes") or []
    s += ("; word lanes: " + ", ".join(wl)) if wl else ""
    # same words as the engine's lanes line: a documents lane that did not run says why
    if r.get("docs_off"):
        s += "; documents off: " + str(r["docs_off"])
    return s


def parse_compare(text):
    pairs = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        a, sep, b = part.partition(":")
        if not sep or not a or not b:
            raise ValueError("bad --compare pair: " + part)
        pairs.append((a, b))
    return pairs


def compare_systems(name_a, name_b, res_a, res_b, ids, B, seed):
    """Paired comparison on the answerable questions in ids (b minus a for the differences)."""
    out = {"a": name_a, "b": name_b}
    for nm, res in ((name_a, res_a), (name_b, res_b)):
        if res.get("status") != "ok":
            out["skipped"] = "%s was skipped: %s" % (nm, res.get("reason", ""))
            return out
    ra = [res_a["ranks"][i] for i in ids]
    rb = [res_b["ranks"][i] for i in ids]
    out["n"] = len(ids)
    for k in (1, 5):
        p = stats.paired(ra, rb, k)
        out["top%d" % k] = {"a_only": p["b"], "b_only": p["c"], "p": round(p["p"], 6)}
    for label, diffs in (
        ("rr10_diff", [rr(y) - rr(x) for x, y in zip(ra, rb, strict=True)]),
        ("top5_diff", [float(y is not None and y <= 5) - float(x is not None and x <= 5) for x, y in zip(ra, rb, strict=True)]),
    ):
        mean, lo, hi = stats.bootstrap_ci(diffs, B=B, seed=seed)
        out[label] = {"mean": round(mean, 4), "lo": round(lo, 4), "hi": round(hi, 4)}
    return out


# ------------------------------------------------------------------- rendering

def _pct(x):
    return "-" if x is None else "%.1f" % (100.0 * x)


def render_md(report):
    meta = report["meta"]
    lines = ["# Benchmark results", ""]
    lines.append("Run at %s, models: %s, profile: %s, %d questions in the file, %d scored, seed %d, "
                 "%d bootstrap draws." % (meta["utc"], meta["models"], meta["profile"], meta["n_questions"],
                                          meta["n_scored"], meta["seed"], meta["boot"]))
    lines += ["", "Shares are percent. Rank metrics use the first gold hit, copies count as hits.", ""]
    head = ["system", "n", "top1", "top3", "top5", "top10", "mrr10", "miss10", "p50 ms", "p95 ms",
            "abstain", "false abstain", "current above stale"]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "---|" * len(head))
    for name in meta["systems"]:
        s = report["systems"][name]
        if s["status"] != "ok":
            lines.append("| %s | skipped: %s |%s" % (name, s["reason"], " |" * (len(head) - 2)))
            continue
        row = [name, str(s["n"]), _pct(s["top1"]), _pct(s["top3"]), _pct(s["top5"]), _pct(s["top10"]),
               "%.3f" % s["mrr10"], str(s["miss10"]), "%.1f" % s["p50_ms"], "%.1f" % s["p95_ms"],
               _pct(s["abstain_unanswerable"]), _pct(s["false_abstain"]), _pct(s["current_above_stale"])]
        lines.append("| " + " | ".join(row) + " |")
    ok = [n for n in meta["systems"] if report["systems"][n]["status"] == "ok"]
    cats = sorted({c for n in ok for c in report["systems"][n]["by_category"]})
    if cats:
        lines += ["", "Top 1 / top 5 by category (percent):", ""]
        lines.append("| system | " + " | ".join(cats) + " |")
        lines.append("|" + "---|" * (len(cats) + 1))
        for n in ok:
            by = report["systems"][n]["by_category"]
            cells = []
            for c in cats:
                v = by.get(c)
                cells.append("-" if not v else "%s / %s (n=%d)" % (_pct(v["top1"]), _pct(v["top5"]), v["n"]))
            lines.append("| %s | %s |" % (n, " | ".join(cells)))
    if report["comparisons"]:
        lines += ["", "Paired comparisons, a against b. a only and b only count questions where just one "
                      "system hit. p is the exact McNemar test. The differences are b minus a with a "
                      "95 % bootstrap interval.", ""]
        head = ["a", "b", "n", "top1 a only / b only", "p top1", "top5 a only / b only", "p top5",
                "reciprocal rank diff", "top5 hit diff"]
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + "---|" * len(head))
        for c in report["comparisons"]:
            if "skipped" in c:
                lines.append("| %s | %s | %s |%s" % (c["a"], c["b"], c["skipped"], " |" * (len(head) - 3)))
                continue
            rrd, t5 = c["rr10_diff"], c["top5_diff"]
            lines.append("| %s | %s | %d | %d / %d | %.4f | %d / %d | %.4f | %+.3f [%+.3f, %+.3f] | "
                         "%+.3f [%+.3f, %+.3f] |" % (
                             c["a"], c["b"], c["n"], c["top1"]["a_only"], c["top1"]["b_only"], c["top1"]["p"],
                             c["top5"]["a_only"], c["top5"]["b_only"], c["top5"]["p"],
                             rrd["mean"], rrd["lo"], rrd["hi"], t5["mean"], t5["lo"], t5["hi"]))
    lines += ["", "Lanes line seen on the first question:", ""]
    for n in meta["systems"]:
        s = report["systems"][n]
        if s["status"] == "ok":
            lines.append("- %s: %s" % (n, s["lanes"]))
    if report["skipped_questions"]:
        lines += ["", "Skipped questions (not scored as misses):", ""]
        for q in report["skipped_questions"]:
            lines.append("- %s (%s): %s" % (q["id"], q["category"], q["reason"]))
    lines += ["", SMALL_NOTE, ""]
    return "\n".join(lines)


# --------------------------------------------------------------- engine plumbing

@contextlib.contextmanager
def scoped_env(updates):
    """Set env vars for a block, None removes one. Restores everything after."""
    old = {k: os.environ.get(k) for k in updates}
    try:
        for k, v in updates.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def make_pdf(path=FIXTURE_PDF):
    """Write the fixture pdf with the test pdf writer (imported lazily)."""
    from tests import pdfgen
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pdfgen.write_pdf(path, PDF_PAGES)
    return path


def ensure_fixture_pdf(config_path):
    """Create the fixture pdf when the bundled config is used and the file is missing."""
    if os.path.abspath(config_path) != os.path.abspath(FIXTURE_CONFIG) or os.path.exists(FIXTURE_PDF):
        return
    try:
        make_pdf()
    except Exception as e:
        print("note: fixture pdf not made (%s)" % type(e).__name__, file=sys.stderr)


def effective_config(args, config_mod, home, models_mode):
    """Config dict for this run: absolute root paths, temp home, models by flag.

    Call it before LEKS_HOME points at the temp home: the real weights default to the user's own home.
    """
    cfg_file = os.path.abspath(os.path.expanduser(args.config))
    base = os.path.dirname(cfg_file)
    d = read_json(cfg_file)
    real_home = config_mod.home_dir(d, base)

    def here(p):
        # relative means the config's own folder, the copy we write lives somewhere else
        p = os.path.expanduser(str(p))
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))

    for r in d.get("roots", []):
        r["path"] = here(r.get("path", ""))
    d["home"] = home
    models = dict(d.get("models") or {})
    if models_mode == "none":
        models["embedder"] = None
        models["reranker"] = None
    elif models_mode == "fake":
        models["backend"] = "fake"
        models["embedder"] = dict(FAKE_EMBEDDER)
        models["reranker"] = dict(FAKE_RERANKER)
    else:
        tmpl = config_mod.default_dict()["models"]
        for key in ("embedder", "reranker"):
            if not models.get(key):
                models[key] = tmpl[key]
        models.setdefault("backend", tmpl.get("backend", "torch"))
        wd = models.get("weights_dir")
        if args.weights:
            wd = os.path.abspath(os.path.expanduser(args.weights))
        elif isinstance(wd, str) and wd:
            wd = here(wd)
        elif not wd:
            wd = os.path.join(real_home, "models")
        models["weights_dir"] = wd
    d["models"] = models
    return d


def raw_ping(sock_path, timeout=1.0):
    """Ping the daemon on a plain socket. No client, so no cool-down is set."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall(b'{"op": "ping"}\n')
        data = b""
        while not data.endswith(b"\n") and len(data) < 65536:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        return "shape" in json.loads(data.decode("utf-8"))
    except (OSError, ValueError):
        return False
    finally:
        s.close()


def start_daemon(cfg, models_mode, wait=40.0):
    """Start the model daemon unless one answers. Returns (process or None, started, up)."""
    sock = cfg.socket_path()
    if raw_ping(sock):
        return None, False, True
    proc = None
    if models_mode == "config":
        # the normal way: sandboxed, detached
        try:
            from leks.hooks import warm_models
            word = warm_models.start(cfg, wait=True)
        except Exception as e:
            print("note: daemon start failed (%s)" % type(e).__name__, file=sys.stderr)
            return None, False, False
        if word in NOT_COMING:
            # say why once and skip the wait, the model systems then report themselves skipped
            print("note: model daemon not started: %s" % NOT_COMING[word], file=sys.stderr)
            return None, False, False
    else:
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.Popen([sys.executable, "-m", "leks", "modeld", "serve"], cwd=ROOT, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + wait
    while time.time() < end:
        if raw_ping(sock):
            return proc, True, True
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.2)
    return proc, True, raw_ping(sock)


def stop_daemon(bye, proc):
    try:
        bye()
    except Exception:
        pass
    if proc is not None:
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass


def read_texts(cfg, profile):
    """Lowercased text of every plain text file the profile sees, by display path."""
    from leks import config as config_mod
    limit = int((cfg.limits or {}).get("max_file_bytes", 1500000))
    texts = {}
    for root in cfg.roots_for(profile):
        for dp, dns, fns in os.walk(root.path):
            dns[:] = sorted(d for d in dns if d not in config_mod.ALWAYS_EXCLUDE_DIRS)
            for fn in sorted(fns):
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, root.path).replace(os.sep, "/")
                if os.path.splitext(fn)[1].lower() not in TEXT_EXT or config_mod.skipped(root, rel):
                    continue
                try:
                    if os.path.getsize(full) > limit:
                        continue
                    with open(full, encoding="utf-8", errors="replace") as f:
                        texts[root.name + "/" + rel] = f.read().lower()
                except OSError:
                    continue
    return texts


def get_caps(cfg):
    try:
        from leks import docs
        return dict(docs.capabilities(cfg))
    except Exception:
        return {"pdf_text": False, "ocr": False, "docx": True, "images": False}


def missing_model_packages():
    """Packages of the models extra this python cannot find. Nothing is imported."""
    out = []
    for name in MODEL_PACKAGES:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            out.append(name)
    return out


def model_gap(cfg, name):
    """Reason a model system cannot run in this config, else None."""
    if name not in MODEL_SYSTEMS:
        return None
    if cfg.embedder is None:
        return "no models configured"
    if name == "full" and cfg.reranker is None:
        return "no reranker configured"
    return None


def run_system(name, eng, qs, k, texts):
    """Run one system over the questions. Returns the summary dict or a skipped marker."""
    kw = SYSTEMS[name]
    rows, lanes, errors = [], None, 0
    for q in qs:
        gold, stale = q["gold"], q.get("stale") or []
        low = None
        t0 = time.perf_counter()
        if kw is None:
            ranked = [[p] for p in grep_scan(texts, q["question"])[:k]]
            ms = (time.perf_counter() - t0) * 1000.0
        else:
            try:
                r = eng.search(q["question"], k=k, per_doc=1, strict=name in MODEL_SYSTEMS, **kw)
            except RuntimeError as e:
                return {"status": "skipped", "reason": "lane missing: " + str(e)[:160]}
            except Exception as e:
                errors += 1
                print("warning: %s failed on %s (%s)" % (name, q["id"], type(e).__name__), file=sys.stderr)
                r = None
            ms = (time.perf_counter() - t0) * 1000.0
            if r is None:
                ranked = []
            else:
                ranked = [hit_paths(h) for h in (r.get("results") or [])[:k]]
                low = r.get("confidence") == "low"
                if lanes is None:
                    lanes = lanes_line(r)
        rows.append({"id": q["id"], "cat": q["category"], "una": not gold, "rank": rank_of(gold, ranked),
                     "stale_rank": rank_of(stale, ranked), "ms": ms, "low": low})
    out = summarize(rows)
    out["lanes"] = lanes if lanes is not None else "grep baseline, no index"
    out["errors"] = errors
    out["ranks"] = {r["id"]: r["rank"] for r in rows if not r["una"]}
    return out


# ------------------------------------------------------------------------ main

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Benchmark the leks search engine on a question set.")
    ap.add_argument("--config", default=FIXTURE_CONFIG, help="config file of the corpus to index")
    ap.add_argument("--questions", default=FIXTURE_QUESTIONS, help="question file")
    ap.add_argument("--systems", default="grep,words,fused,hybrid,full")
    ap.add_argument("--models", choices=("none", "fake", "config"), default="none",
                    help="none: words only, fake: test backend, config: the real models "
                         "(needs the models extra and weights from leks models fetch)")
    ap.add_argument("--k", type=int, default=10, help="results asked per question")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--boot", type=int, default=10000, help="bootstrap draws")
    ap.add_argument("--compare", default="words:fused,fused:full")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--home", default=None, help="use this index home instead of a temp one")
    ap.add_argument("--weights", default=None, help="model weights folder for --models config")
    ap.add_argument("--keep", action="store_true", help="keep the temp home")
    ap.add_argument("--make-pdf", action="store_true", help="write the fixture pdf and exit")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.make_pdf:
        print(make_pdf())
        return 0
    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    for s in systems:
        if s not in SYSTEMS:
            print("unknown system %r, known: %s" % (s, ", ".join(SYSTEMS)), file=sys.stderr)
            return 2
    try:
        pairs = parse_compare(args.compare)
        qs_all, qnote = load_questions(args.questions)
    except (ValueError, OSError, KeyError) as e:
        print("bad input: %s" % e, file=sys.stderr)
        return 2
    try:
        from leks import config as config_mod, build, query
    except ImportError as e:
        print("the leks package is not importable: %s" % e, file=sys.stderr)
        return 2
    if args.models == "config":
        miss = missing_model_packages()
        if miss:
            # one line and out, before any temp home or index exists
            print('--models config needs the models extra: pip install -e ".[models]" in the LEKS clone '
                  '(not found: %s)'
                  % ", ".join(miss), file=sys.stderr)
            return 2
    ensure_fixture_pdf(args.config)

    home = os.path.abspath(os.path.expanduser(args.home)) if args.home else tempfile.mkdtemp(prefix="leksb")
    cfgdir = tempfile.mkdtemp(prefix="leksbc")
    env = {"LEKS_HOME": home, "LEKS_CONFIG": os.path.join(cfgdir, "config.json"),
           "LEKS_MODELD_SOCK": None, "LEKS_PROFILE": None,
           "LEKS_ALLOW_FAKE": "1" if args.models == "fake" else os.environ.get("LEKS_ALLOW_FAKE")}
    proc, started, real_bye = None, False, (lambda: None)
    try:
        # read it while LEKS_HOME is still the user's, that is where the real weights live
        d = effective_config(args, config_mod, home, args.models)
        with scoped_env(env):
            try:
                with open(env["LEKS_CONFIG"], "w", encoding="utf-8") as f:
                    json.dump(d, f, indent=1)
                cfg = config_mod.from_dict(d, base_dir=cfgdir)
                # the sandboxed daemon gets a clean env and finds this run's config only through it
                cfg.source_path = env["LEKS_CONFIG"]
                profile = args.profile or ("orchestrator" if "orchestrator" in cfg.profiles
                                           else cfg.default_profile)
                if profile not in cfg.profiles:
                    print("unknown profile %r" % profile, file=sys.stderr)
                    return 2
                os.environ["LEKS_PROFILE"] = profile

                t0 = time.time()
                built = build.update_all(cfg, force=not args.home, log=lambda *a, **kw: None)
                meta_extra = {"build_secs": round(time.time() - t0, 2)}
                try:
                    meta_extra["build"] = json.loads(json.dumps(built, default=str))
                except (TypeError, ValueError):
                    meta_extra["build"] = str(built)

                client = None
                if args.models != "none":
                    from leks import modeld_client
                    proc, started, up = start_daemon(cfg, args.models)
                    client = modeld_client.Client.from_config(cfg)
                    real_bye = client.bye
                    try:
                        client.bye = lambda: None  # embed_all says bye at its end, the daemon must stay up
                    except AttributeError:
                        pass
                    if up and cfg.embedder is not None:
                        from leks import embed_all
                        try:
                            meta_extra["embed_rc"] = embed_all.run(cfg, profile, client=client)
                        except Exception as e:
                            meta_extra["embed_rc"] = "error: " + type(e).__name__
                    else:
                        meta_extra["embed_rc"] = "daemon not up"

                caps = get_caps(cfg)
                visible = {r.name for r in cfg.roots_for(profile)}

                def exists(g):
                    try:
                        p = cfg.abs_path(g)
                    except Exception:
                        return False
                    return bool(p) and os.path.isfile(p)

                qs, skipped_q = split_questions(qs_all, caps, visible, exists)
                texts = read_texts(cfg, profile) if "grep" in systems else {}
                eng = query.Engine(cfg, profile=profile, immutable=True, client=client)

                results = {}
                try:
                    for name in systems:
                        gap = model_gap(cfg, name)
                        if gap:
                            results[name] = {"status": "skipped", "reason": gap}
                        else:
                            results[name] = run_system(name, eng, qs, args.k, texts)
                        r = results[name]
                        print("system %-7s %s" % (name, "skipped: " + r["reason"] if r["status"] != "ok"
                                                  else "top1 %s top5 %s" % (_pct(r["top1"]), _pct(r["top5"]))),
                              file=sys.stderr)
                finally:
                    eng.close()

                ids = [q["id"] for q in qs if q["gold"]]
                comps = []
                for a, b in pairs:
                    if a not in results or b not in results:
                        comps.append({"a": a, "b": b, "skipped": "system not in this run"})
                    else:
                        comps.append(compare_systems(a, b, results[a], results[b], ids, args.boot, args.seed))
            finally:
                if started:
                    stop_daemon(real_bye, proc)
    finally:
        shutil.rmtree(cfgdir, ignore_errors=True)
        if not args.home and not args.keep:
            shutil.rmtree(home, ignore_errors=True)

    try:
        import leks
        version = leks.__version__
    except Exception:
        version = "unknown"
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    report = {
        "meta": dict({"version": version, "python": sys.version.split()[0], "n_questions": len(qs_all),
                      "n_scored": len(qs), "seed": args.seed, "boot": args.boot, "models": args.models,
                      "systems": systems, "utc": now.strftime("%Y-%m-%d %H:%M:%S"), "profile": profile,
                      "k": args.k, "questions_note": qnote}, **meta_extra),
        "systems": results,
        "comparisons": comps,
        "skipped_questions": skipped_q,
    }
    os.makedirs(args.out, exist_ok=True)
    jpath = os.path.join(args.out, "results_%s.json" % stamp)
    mpath = os.path.join(args.out, "results_%s.md" % stamp)
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    md = render_md(report)
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print("wrote %s and %s" % (jpath, mpath), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
