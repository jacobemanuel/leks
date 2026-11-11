"""Tests for the benchmark: stats, helpers, fixtures and a small harness run."""
import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "bench")
FIX = os.path.join(BENCH, "fixtures")
CORPUS = os.path.join(FIX, "corpus")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


stats = _load("leks_bench_stats_t", os.path.join(BENCH, "stats.py"))
rb = _load("leks_run_bench_t", os.path.join(BENCH, "run_bench.py"))


def have(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class StatsTests(unittest.TestCase):
    def test_topk_and_mrr(self):
        ranks = [1, 2, None, 5]
        self.assertEqual(stats.topk(ranks, 1), 0.25)
        self.assertEqual(stats.topk(ranks, 3), 0.5)
        self.assertEqual(stats.topk(ranks, 5), 0.75)
        self.assertAlmostEqual(stats.mrr(ranks, 10), (1 + 0.5 + 0 + 0.2) / 4)
        self.assertEqual(stats.mrr([12, 1], 10), 0.5)  # rank 12 is below the cutoff
        self.assertEqual(stats.topk([], 5), 0.0)
        self.assertEqual(stats.mrr([], 10), 0.0)

    def test_mcnemar_hand_values(self):
        self.assertAlmostEqual(stats.mcnemar_exact(0, 5), 0.0625)
        self.assertAlmostEqual(stats.mcnemar_exact(5, 0), 0.0625)
        self.assertEqual(stats.mcnemar_exact(3, 3), 1.0)
        self.assertEqual(stats.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(stats.mcnemar_exact(1, 9), 22 / 1024)
        self.assertEqual(stats.mcnemar_exact(1, 1), 1.0)

    def test_bootstrap_constant_has_zero_width(self):
        mean, lo, hi = stats.bootstrap_ci([0.25] * 12, B=500)
        self.assertEqual(mean, 0.25)
        self.assertEqual(lo, hi)
        self.assertEqual(lo, 0.25)

    def test_bootstrap_interval_and_seed(self):
        d = [1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 0.0]
        a = stats.bootstrap_ci(d, B=2000, seed=3)
        b = stats.bootstrap_ci(d, B=2000, seed=3)
        self.assertEqual(a, b)  # same seed, same answer
        mean, lo, hi = a
        self.assertEqual(mean, 0.5)
        self.assertTrue(0.0 <= lo <= mean <= hi <= 1.0)
        self.assertLess(lo, hi)
        self.assertEqual(stats.bootstrap_ci([], B=10), (0.0, 0.0, 0.0))

    def test_paired(self):
        a = [1, None, 3, 7, None]
        b = [None, 2, 3, 1, None]
        p = stats.paired(a, b, 3)  # a only: q1, b only: q2 and q4
        self.assertEqual((p["b"], p["c"]), (1, 2))
        self.assertEqual(p["p"], 1.0)
        p = stats.paired([1] * 5, [None] * 5, 1)
        self.assertEqual((p["b"], p["c"]), (5, 0))
        self.assertAlmostEqual(p["p"], 0.0625)
        with self.assertRaises(ValueError):
            stats.paired([1], [1, 2], 1)


class HelperTests(unittest.TestCase):
    def test_rank_of_with_copies(self):
        ranked = [{"a"}, {"b", "c"}, {"d"}]
        self.assertEqual(rb.rank_of(["c"], ranked), 2)
        self.assertEqual(rb.rank_of(["x", "d"], ranked), 3)
        self.assertIsNone(rb.rank_of(["x"], ranked))
        self.assertIsNone(rb.rank_of([], ranked))
        self.assertEqual(rb.hit_paths({"path": "a", "_copies": ["b"]}), {"a", "b"})

    def test_percentile(self):
        self.assertEqual(rb.percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(rb.percentile(list(range(1, 21)), 95), 19)
        self.assertEqual(rb.percentile([], 95), 0.0)

    def test_summarize(self):
        rows = [
            {"id": "a", "cat": "fact", "una": False, "rank": 1, "stale_rank": None, "ms": 1.0, "low": False},
            {"id": "b", "cat": "fact", "una": False, "rank": 4, "stale_rank": None, "ms": 3.0, "low": True},
            {"id": "c", "cat": "current", "una": False, "rank": 2, "stale_rank": 1, "ms": 2.0, "low": False},
            {"id": "d", "cat": "current", "una": False, "rank": 1, "stale_rank": 3, "ms": 2.0, "low": False},
            {"id": "e", "cat": "unanswerable", "una": True, "rank": None, "stale_rank": None, "ms": 2.0,
             "low": True},
            {"id": "f", "cat": "fact", "una": False, "rank": None, "stale_rank": None, "ms": 2.0, "low": False},
        ]
        s = rb.summarize(rows)
        self.assertEqual(s["n"], 5)
        self.assertEqual(s["n_unanswerable"], 1)
        self.assertEqual(s["top1"], 0.4)
        self.assertEqual(s["top3"], 0.6)
        self.assertEqual(s["top5"], 0.8)
        self.assertEqual(s["miss10"], 1)
        self.assertEqual(s["abstain_unanswerable"], 1.0)
        self.assertEqual(s["false_abstain"], 0.2)
        self.assertEqual(s["current_above_stale"], 0.5)  # c has the stale note above it
        self.assertEqual(s["by_category"]["fact"]["n"], 3)
        self.assertEqual(s["by_category"]["fact"]["top5"], 0.6667)
        self.assertEqual(s["p50_ms"], 2.0)

    def test_summarize_without_confidence(self):
        rows = [{"id": "a", "cat": "fact", "una": False, "rank": 1, "stale_rank": None, "ms": 1.0, "low": None}]
        s = rb.summarize(rows)
        self.assertIsNone(s["abstain_unanswerable"])
        self.assertIsNone(s["false_abstain"])
        self.assertIsNone(s["current_above_stale"])

    def test_grep_scan(self):
        texts = {
            "n/a.md": "the cache evicts the oldest tile. tile tile",
            "n/b.md": "nothing relevant here",
            "n/c.md": "one tile only",
        }
        self.assertEqual(rb.grep_scan(texts, "Which tile is evicted?"), ["n/a.md", "n/c.md"])
        self.assertEqual(rb.grep_scan(texts, "the of and"), [])  # only stop words

    def test_needs_and_split(self):
        caps = {"pdf_text": False, "ocr": False, "docx": True, "images": False}
        qs = [
            {"id": "a", "question": "x", "gold": ["notes/a.md"], "category": "fact", "needs": []},
            {"id": "b", "question": "x", "gold": ["docs/p.pdf"], "category": "document", "needs": ["pdf"]},
            {"id": "c", "question": "x", "gold": ["code/c.py"], "category": "code", "needs": []},
            {"id": "d", "question": "x", "gold": [], "category": "unanswerable", "needs": []},
            {"id": "e", "question": "x", "gold": ["notes/gone.md"], "category": "fact", "needs": []},
            {"id": "f", "question": "x", "gold": ["notes/a.md"], "category": "fact", "needs": ["weird"]},
        ]
        self.assertEqual(rb.unmet_needs(qs[1], caps), ["pdf"])
        self.assertEqual(rb.unmet_needs(qs[1], dict(caps, pdf_text=True)), [])
        kept, skipped = rb.split_questions(qs, caps, {"notes", "docs"}, lambda g: g != "notes/gone.md")
        self.assertEqual([q["id"] for q in kept], ["a", "d"])
        why = {s["id"]: s["reason"] for s in skipped}
        self.assertEqual(why["b"], "needs pdf")
        self.assertEqual(why["c"], "gold outside the profile")
        self.assertEqual(why["e"], "gold file missing")
        self.assertEqual(why["f"], "needs weird")
        kept, skipped = rb.split_questions(qs[1:2], dict(caps, pdf_text=True), {"docs"}, lambda g: True)
        self.assertEqual(len(kept), 1)

    def test_parse_compare(self):
        self.assertEqual(rb.parse_compare("words:fused, fused:full"), [("words", "fused"), ("fused", "full")])
        self.assertEqual(rb.parse_compare(""), [])
        with self.assertRaises(ValueError):
            rb.parse_compare("words")

    def test_compare_systems(self):
        ok_a = {"status": "ok", "ranks": {"q1": 1, "q2": None, "q3": 2, "q4": None}}
        ok_b = {"status": "ok", "ranks": {"q1": 1, "q2": 1, "q3": 1, "q4": 3}}
        ids = ["q1", "q2", "q3", "q4"]
        c = rb.compare_systems("a", "b", ok_a, ok_b, ids, 200, 7)
        self.assertEqual(c["n"], 4)
        self.assertEqual((c["top1"]["a_only"], c["top1"]["b_only"]), (0, 2))
        self.assertEqual((c["top5"]["a_only"], c["top5"]["b_only"]), (0, 2))
        self.assertAlmostEqual(c["top5"]["p"], 0.5)
        self.assertAlmostEqual(c["top5_diff"]["mean"], 0.5)
        self.assertGreater(c["rr10_diff"]["mean"], 0)
        c = rb.compare_systems("a", "b", ok_a, {"status": "skipped", "reason": "no models configured"}, ids, 10, 7)
        self.assertIn("skipped", c)
        self.assertIn("no models configured", c["skipped"])

    def test_render_md_names_the_fixture_set(self):
        report = {
            "meta": {"utc": "2026-01-01 00:00:00", "models": "none", "profile": "p", "n_questions": 1,
                     "n_scored": 1, "seed": 7, "boot": 10, "systems": ["words", "full"]},
            "systems": {
                "words": dict(rb.summarize([{"id": "a", "cat": "fact", "una": False, "rank": 1,
                                             "stale_rank": None, "ms": 1.0, "low": False}]), lanes="x"),
                "full": {"status": "skipped", "reason": "no models configured"},
            },
            "comparisons": [{"a": "words", "b": "full", "skipped": "full was skipped"}],
            "skipped_questions": [{"id": "q9", "category": "document", "reason": "needs pdf"}],
        }
        md = rb.render_md(report)
        self.assertIn("small synthetic fixture set", md)
        self.assertIn("skipped: no models configured", md)
        self.assertIn("q9", md)
        for line in md.splitlines():
            if line.startswith("|"):
                self.assertTrue(line.endswith("|"))


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, "questions.json"), encoding="utf-8") as f:
            cls.raw = json.load(f)
        cls.qs = cls.raw["questions"]

    def files(self, sub):
        out = []
        for dp, _, fns in os.walk(os.path.join(CORPUS, sub)):
            out += [os.path.join(dp, f) for f in fns]
        return sorted(out)

    def test_question_mix(self):
        counts = {}
        for q in self.qs:
            counts[q["category"]] = counts.get(q["category"], 0) + 1
        want = {"fact": 14, "current": 6, "history": 4, "code": 6, "document": 4, "name": 3, "unanswerable": 3}
        self.assertEqual(counts, want)
        self.assertEqual(len(self.qs), 40)
        self.assertEqual(len({q["id"] for q in self.qs}), 40)
        self.assertIn("never tuned", self.raw["_note"])

    def test_question_shape(self):
        for q in self.qs:
            self.assertEqual(set(q), {"id", "question", "gold", "category", "stale", "needs"})
            if q["category"] == "unanswerable":
                self.assertEqual(q["gold"], [])
            else:
                self.assertTrue(q["gold"], q["id"])
            if q["category"] == "current":
                self.assertTrue(q["stale"], q["id"])
        pdf_q = [q["id"] for q in self.qs if q["needs"] == ["pdf"]]
        self.assertEqual(len(pdf_q), 2)

    def test_gold_files_exist(self):
        for q in self.qs:
            for g in q["gold"] + q["stale"]:
                if g.endswith(".pdf"):
                    continue  # made by the pdf step, checked below
                root, rel = g.split("/", 1)
                self.assertTrue(os.path.isfile(os.path.join(CORPUS, root, rel)), g)

    def test_corpus_shape(self):
        notes = [f for f in self.files("notes") if f.endswith(".md")]
        self.assertGreaterEqual(len(notes), 38)
        self.assertLessEqual(len(notes), 44)
        self.assertEqual(len([f for f in notes if os.sep + "decisions" + os.sep in f]), 8)
        self.assertEqual(len([f for f in notes if os.sep + "archive" + os.sep in f]), 4)
        self.assertEqual(len([f for f in notes if os.sep + "results" + os.sep in f]), 3)
        self.assertTrue(os.path.isfile(os.path.join(CORPUS, "notes", "STATE.md")))
        self.assertTrue(os.path.isfile(os.path.join(CORPUS, "notes", "renderer", "frame-budget-v1.md")))
        self.assertTrue(os.path.isfile(os.path.join(CORPUS, "notes", "renderer", "frame-budget-v2.md")))
        for p in notes:
            with open(p, encoding="utf-8") as f:
                head = f.read(400)
            self.assertTrue(head.startswith("---\n"), p)
            self.assertRegex(head, r"(?m)^status: \S")
            self.assertRegex(head, r"(?m)^date: \d{4}-\d{2}-\d{2}$")
        with open(os.path.join(CORPUS, "notes", "decisions", "adr-001-tile-size-256.md"), encoding="utf-8") as f:
            self.assertIn("superseded_by: adr-006-tile-size-512", f.read())

    def test_no_note_folder_hits_the_exclude_list(self):
        # a folder called build or dist would be skipped by the engine
        banned = {"build", "dist", "target", "node_modules", "venv"}
        for dp, dns, _ in os.walk(CORPUS):
            self.assertFalse(banned & set(dns), dp)

    def test_code_files(self):
        code = self.files("code")
        ext = sorted(os.path.splitext(c)[1] for c in code)
        self.assertEqual(ext, [".c", ".py", ".py", ".py", ".py", ".py", ".py", ".sh", ".sh", ".swift"])
        for p in code:
            with open(p, encoding="utf-8") as f:
                text = f.read()
            if p.endswith(".py"):
                ast.parse(text)  # must be valid python
                names = re.findall(r"(?m)^\s*(?:def|class) (\w+)", text)
            elif p.endswith(".sh"):
                names = re.findall(r"(?m)^(\w+)\(\) \{", text)
            elif p.endswith(".swift"):
                names = re.findall(r"(?m)^\s*(?:final class|struct|func) (\w+)", text)
            else:
                names = re.findall(r"(?m)^\w[\w ]*\s\**(\w+)\(", text)
            self.assertGreaterEqual(len(names), 3, p)

    def test_docs_folder(self):
        names = {os.path.basename(f) for f in self.files("docs")}
        self.assertTrue({"design-overview.txt", "ops-runbook.txt"} <= names)
        self.assertLessEqual(names - {"design-overview.txt", "ops-runbook.txt", "cache-study.pdf"}, set())

    def test_fixture_config(self):
        with open(os.path.join(FIX, "config.json"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(d["version"], 3)
        self.assertEqual([r["name"] for r in d["roots"]], ["notes", "code", "docs"])
        self.assertEqual(set(d["profiles"]), {"architect", "orchestrator"})
        self.assertEqual(d["profiles"]["architect"]["roots"], ["notes", "docs"])
        self.assertIsNone(d["models"]["embedder"])
        self.assertIsNone(d["models"]["reranker"])

    def test_no_dash_characters(self):
        bad = (chr(0x2014), chr(0x2013))
        for base in (FIX, BENCH):
            for dp, _, fns in os.walk(base):
                for fn in fns:
                    if fn.endswith((".pdf", ".pyc")) or dp.endswith("results"):
                        continue
                    p = os.path.join(dp, fn)
                    with open(p, encoding="utf-8") as f:
                        text = f.read()
                    for ch in bad:
                        self.assertNotIn(ch, text, p)


class PdfTests(unittest.TestCase):
    def test_make_pdf(self):
        d = tempfile.mkdtemp(prefix="btest")
        try:
            path = rb.make_pdf(os.path.join(d, "cache-study.pdf"))
            with open(path, "rb") as f:
                self.assertTrue(f.read(5).startswith(b"%PDF"))
            if have("pypdf"):
                import pypdf
                pages = pypdf.PdfReader(path).pages
                self.assertEqual(len(pages), 2)
                self.assertIn("512 tiles 95 percent", pages[0].extract_text())
                self.assertIn("First in, first out 77 percent", pages[1].extract_text())
        finally:
            shutil.rmtree(d, ignore_errors=True)


class HarnessTests(unittest.TestCase):
    def run_harness(self, *extra):
        out = tempfile.mkdtemp(prefix="btest")
        self.addCleanup(shutil.rmtree, out, True)
        argv = ["--out", out, "--boot", "200"] + list(extra)
        old = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = open(os.devnull, "w")
        try:
            rc = rb.main(argv)
        finally:
            sys.stdout.close()
            sys.stdout, sys.stderr = old
        self.assertEqual(rc, 0)
        names = sorted(os.listdir(out))
        self.assertEqual(len(names), 2)
        js = [n for n in names if n.endswith(".json")][0]
        md = [n for n in names if n.endswith(".md")][0]
        with open(os.path.join(out, js), encoding="utf-8") as f:
            report = json.load(f)
        with open(os.path.join(out, md), encoding="utf-8") as f:
            text = f.read()
        return report, text

    def setUp(self):
        if not os.path.exists(os.path.join(CORPUS, "docs", "cache-study.pdf")):
            try:
                rb.make_pdf()
            except Exception:
                pass

    def test_models_none(self):
        before = {k: os.environ.get(k) for k in ("LEKS_HOME", "LEKS_CONFIG", "LEKS_PROFILE")}
        report, md = self.run_harness("--models", "none")
        self.assertEqual({k: os.environ.get(k) for k in before}, before)  # env restored
        meta = report["meta"]
        self.assertEqual(meta["models"], "none")
        self.assertEqual(meta["n_questions"], 40)
        self.assertEqual(meta["seed"], 7)
        self.assertEqual(meta["systems"], ["grep", "words", "fused", "hybrid", "full"])
        sysm = report["systems"]
        for name in ("grep", "words", "fused"):
            self.assertEqual(sysm[name]["status"], "ok", name)
            self.assertGreater(sysm[name]["n"], 30)
            self.assertGreater(sysm[name]["mrr10"], 0.0)
            self.assertEqual(sysm[name]["errors"], 0)
        for name in ("hybrid", "full"):
            self.assertEqual(sysm[name]["status"], "skipped")
            self.assertEqual(sysm[name]["reason"], "no models configured")
        self.assertTrue(sysm["words"]["lanes"].startswith("words only"))
        self.assertIn("word lanes", sysm["words"]["lanes"])
        self.assertEqual(sysm["words"]["n"], sysm["grep"]["n"])
        pairs = {(c["a"], c["b"]): c for c in report["comparisons"]}
        self.assertIn("p", pairs[("words", "fused")]["top1"])
        self.assertIn("skipped", pairs[("fused", "full")])
        self.assertIn("small synthetic fixture set", md)
        self.assertIn("| words |", md)

    def test_pdf_needs(self):
        report, _ = self.run_harness("--models", "none", "--systems", "words", "--compare", "")
        skipped = {q["id"]: q["reason"] for q in report["skipped_questions"]}
        pdf_file = os.path.exists(os.path.join(CORPUS, "docs", "cache-study.pdf"))
        if not have("pypdf"):
            self.assertEqual(skipped.get("q33"), "needs pdf")
            self.assertEqual(skipped.get("q34"), "needs pdf")
            self.assertEqual(report["meta"]["n_scored"], 38)
        elif pdf_file:
            self.assertNotIn("q33", skipped)
            self.assertEqual(report["meta"]["n_scored"], 40)
        else:
            self.assertEqual(skipped.get("q33"), "gold file missing")
        self.assertEqual(report["comparisons"], [])

    def test_unknown_system_is_refused(self):
        old = sys.stderr
        sys.stderr = open(os.devnull, "w")
        try:
            rc = rb.main(["--systems", "nope", "--out", tempfile.gettempdir()])
        finally:
            sys.stderr.close()
            sys.stderr = old
        self.assertEqual(rc, 2)

    @unittest.skipUnless(have("numpy"), "numpy is not installed")
    def test_models_fake(self):
        report, _ = self.run_harness("--models", "fake", "--systems", "words,hybrid,full",
                                     "--compare", "words:hybrid")
        sysm = report["systems"]
        self.assertEqual(sysm["words"]["status"], "ok")
        self.assertEqual(sysm["hybrid"]["status"], "ok", sysm["hybrid"])
        self.assertEqual(sysm["full"]["status"], "ok", sysm["full"])
        self.assertIn("meaning", sysm["hybrid"]["lanes"])
        self.assertIn("reranker", sysm["full"]["lanes"])
        self.assertEqual(report["comparisons"][0]["n"], sysm["words"]["n"])


class RealModelPathTests(unittest.TestCase):
    """--models config: the message path and the argument handling. Nothing is downloaded or loaded."""

    def quiet_main(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = rb.main(argv)
        return rc, err.getvalue()

    def out_dir(self):
        out = tempfile.mkdtemp(prefix="btest")
        self.addCleanup(shutil.rmtree, out, True)
        return out

    def test_missing_packages_are_named(self):
        self.assertEqual(rb.MODEL_PACKAGES, ("numpy", "torch", "transformers", "safetensors"))
        with mock.patch.object(rb, "MODEL_PACKAGES", ("json", "leks_no_such_pkg_q", "os")):
            self.assertEqual(rb.missing_model_packages(), ["leks_no_such_pkg_q"])

    def test_without_the_extra_one_line_and_nothing_runs(self):
        out = self.out_dir()
        with mock.patch.object(rb, "MODEL_PACKAGES", ("json", "leks_no_such_pkg_q"), create=True), \
                mock.patch.object(rb, "ensure_fixture_pdf") as pdf, \
                mock.patch.object(rb, "effective_config") as eff:
            rc, err = self.quiet_main(["--models", "config", "--out", out])
        self.assertEqual(rc, 2)
        self.assertEqual(err.strip().split("\n"),
                         ['--models config needs the models extra: pip install -e ".[models]" in the LEKS clone '
                          '(not found: leks_no_such_pkg_q)'])
        pdf.assert_not_called()
        eff.assert_not_called()
        self.assertEqual(os.listdir(out), [])

    def write_config(self, folder, home=None, weights=None):
        d = rb.read_json(rb.FIXTURE_CONFIG)
        if home is not None:
            d["home"] = home
        d["models"]["weights_dir"] = weights
        path = os.path.join(folder, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
        return path

    def test_paths_resolve_from_the_config_folder(self):
        from leks import config as config_mod
        tmp = tempfile.mkdtemp(prefix="btest")
        self.addCleanup(shutil.rmtree, tmp, True)

        def weights(path, *extra):
            args = rb.parse_args(["--config", path, "--models", "config"] + list(extra))
            d = rb.effective_config(args, config_mod, "/srv/run-home", "config")
            self.assertEqual(d["home"], "/srv/run-home")
            self.assertEqual(d["roots"][0]["path"], os.path.join(tmp, "corpus", "notes"))
            return d["models"]["weights_dir"]

        with mock.patch.dict(os.environ):
            os.environ.pop("LEKS_HOME", None)
            # a relative weights_dir means the config's folder, not the temp copy's
            self.assertEqual(weights(self.write_config(tmp, weights="w")), os.path.join(tmp, "w"))
            # no weights_dir: the models folder of the user's home, a relative home field included
            self.assertEqual(weights(self.write_config(tmp, home="h")), os.path.join(tmp, "h", "models"))
            os.environ["LEKS_HOME"] = "/srv/user-home"
            self.assertEqual(weights(self.write_config(tmp)), os.path.join("/srv/user-home", "models"))
            # --weights is a path from where the command runs
            self.assertEqual(weights(self.write_config(tmp, weights="w"), "--weights", "rel/w"),
                             os.path.abspath("rel/w"))

    def test_the_daemon_gets_the_users_weights_and_this_runs_config(self):
        seen = {}

        def fake_start(cfg, models_mode, wait=40.0):
            seen.update(weights=cfg.weights_dir, source=cfg.source_path, env=os.environ.get("LEKS_CONFIG"),
                        there=bool(cfg.source_path) and os.path.isfile(cfg.source_path))
            return None, False, False

        out = self.out_dir()
        with mock.patch.object(rb, "MODEL_PACKAGES", ("json",), create=True), \
                mock.patch.object(rb, "start_daemon", fake_start), \
                mock.patch.dict(os.environ, {"LEKS_HOME": "/srv/user-home"}):
            rc, _ = self.quiet_main(["--models", "config", "--systems", "words", "--compare", "",
                                     "--boot", "50", "--out", out])
            self.assertEqual(os.environ["LEKS_HOME"], "/srv/user-home")
        self.assertEqual(rc, 0)
        # before the fix the weights pointed into the temp home, where nothing is
        self.assertEqual(seen["weights"], os.path.join("/srv/user-home", "models"))
        self.assertTrue(seen["there"])
        self.assertEqual(seen["source"], seen["env"])

    def test_no_wait_when_no_daemon_is_coming(self):
        from leks.hooks import warm_models
        tmp = tempfile.mkdtemp(prefix="bs")
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = SimpleNamespace(socket_path=lambda: os.path.join(tmp, "none.sock"))
        for word, fix in (("no weights", "leks models fetch"),
                          ("no backend", 'pip install -e ".[models]" in the LEKS clone'),
                          ("refused", "leks modeld serve")):
            err = io.StringIO()
            with mock.patch.object(warm_models, "start", return_value=word) as st, \
                    contextlib.redirect_stderr(err):
                t0 = time.monotonic()
                got = rb.start_daemon(cfg, "config", wait=3.0)
            self.assertEqual(got, (None, False, False), word)
            self.assertLess(time.monotonic() - t0, 2.0, word)
            self.assertIn(fix, err.getvalue())
            st.assert_called_once()

    def test_lanes_line_says_why_documents_are_off(self):
        from leks import query
        r = {"lanes": "words only: no models configured", "word_lanes": ["notes"],
             "docs_off": "no document indexed"}
        self.assertEqual(rb.lanes_line(r), "words only: no models configured; word lanes: notes; "
                                           "documents off: no document indexed")
        self.assertEqual(rb.lanes_line(r), query.lane_line(r))
        self.assertEqual(rb.lanes_line({"lanes": "x"}), "x")


if __name__ == "__main__":
    unittest.main()
