// qmd_worker.mjs: one warm node process that answers questions with qmd's own library code.
// adapter.py starts it after indexing. requests come as json lines on stdin, replies go to stdout
// behind a marker so a stray native log line on stdout can't break the protocol.
//
// the store is opened like getStore() in dist/cli/qmd.js (default db path, yaml config synced into
// sqlite, one LlamaCpp with the configured models, also set as the process default), then each mode
// calls the same function with the same options as the cli command of that name:
//   search  -> searchFTS(db, q, max(50, 2n), "pep"), keep score >= 0, first n       (qmd search)
//   vsearch -> vectorSearchQuery(store, q, {limit n, minScore 0.3}) in withLLMSession (qmd vsearch)
//   query   -> hybridQuery(store, q, {limit n, minScore 0}) in withLLMSession       (qmd query, also the MCP query tool)
import { createInterface } from "node:readline";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const MARK = "@@QMDW@@ ";
const PKG = process.env.QMDW_PKG;
if (!PKG || !existsSync(join(PKG, "dist", "index.js"))) {
  process.stderr.write("qmd_worker: QMDW_PKG must point at node_modules/@tobilu/qmd\n");
  process.exit(2);
}
const imp = (rel) => import(pathToFileURL(join(PKG, rel)).href);
const S = await imp("dist/store.js");
const L = await imp("dist/llm.js");
const C = await imp("dist/collections.js");
const version = JSON.parse(readFileSync(join(PKG, "package.json"), "utf-8")).version;

function send(obj) {
  process.stdout.write(MARK + JSON.stringify(obj) + "\n");
}

let store = null;
let mode = null;
let n = 20;
let coll = null;
let diag = null; // per call: what qmd did on the way (strong bm25 skip, expansion variants, fallbacks)
const bootFails = {};

// qmd falls back silently when a model can't run: expansion -> the original text, embeddings -> null
// (that vector list is skipped), reranker -> flat 0.5 scores. it only says so on stderr. count those
// messages so a broken model run can't pass as a real one. pass-through, nothing changes.
const FAIL_MARKS = [
  ["expand_failed", "Structured query expansion failed"],
  ["embed_failed", "Embedding error"],
  ["embed_failed", "Batch embedding error"],
  ["rerank_unavailable", "Reranker unavailable"],
];
function bump(flag) {
  const tgt = diag ? diag.fail : bootFails;
  tgt[flag] = (tgt[flag] || 0) + 1;
}
for (const name of ["error", "warn"]) {
  const orig = console[name].bind(console);
  console[name] = (...args) => {
    const first = typeof args[0] === "string" ? args[0] : "";
    for (const [flag, prefix] of FAIL_MARKS) if (first.startsWith(prefix)) bump(flag);
    orig(...args);
  };
}

function openLikeCli() {
  // same steps as getStore() in the cli, minus the yaml write-back of model names
  // (collection add / embed already wrote them, and they are the defaults)
  S.enableProductionMode();
  const st = S.createStore();
  const config = C.loadConfig();
  S.syncConfigToDb(st.db, config);
  const models = L.resolveModels(config.models);
  const llm = new L.LlamaCpp({ embedModel: models.embed, generateModel: models.generate, rerankModel: models.rerank });
  L.setDefaultLlamaCpp(llm);
  st.llm = llm;
  // watch the reranker's answer: model "fallback" means no ranking context could be made
  const origRerank = llm.rerank.bind(llm);
  llm.rerank = async (...args) => {
    const r = await origRerank(...args);
    if (diag) diag.rerank_llm_calls += 1;
    if (r && r.model === "fallback") bump("rerank_fallback");
    return r;
  };
  return st;
}

const SESSION = { maxDuration: 10 * 60 * 1000 };

async function runOne(text) {
  diag = { strong: null, expanded: null, embedded: null, reranked: null, rerank_llm_calls: 0, fail: {} };
  if (mode === "search") {
    const rows = await S.searchFTS(store.db, text, Math.max(50, n * 2), coll);
    return rows
      .filter((r) => r.score >= 0)
      .slice(0, n)
      .map((r) => ({ file: r.filepath, display: r.displayPath, title: r.title, score: r.score }));
  }
  if (mode === "vsearch") {
    const rows = await L.withLLMSession(
      async () =>
        S.vectorSearchQuery(store, text, {
          collection: coll,
          limit: n,
          minScore: 0.3,
          intent: undefined,
          hooks: { onExpand: (_q, ex) => { diag.expanded = ex.map((e) => e.type); } },
        }),
      { ...SESSION, name: "vectorSearch" },
    );
    return rows.filter((r) => r.score >= 0.3).map((r) => ({ file: r.file, display: r.displayPath, title: r.title, score: r.score }));
  }
  if (mode === "query") {
    const rows = await L.withLLMSession(
      async () =>
        S.hybridQuery(store, text, {
          collection: coll,
          limit: n,
          minScore: 0,
          candidateLimit: undefined,
          skipRerank: false,
          explain: false,
          intent: undefined,
          chunkStrategy: undefined,
          hooks: {
            onStrongSignal: (score) => { diag.strong = score; },
            onExpand: (_q, ex) => { diag.expanded = ex.map((e) => e.type); },
            onEmbedStart: (count) => { diag.embedded = count; },
            onRerankStart: (count) => { diag.reranked = count; },
          },
        }),
      { ...SESSION, name: "querySearch" },
    );
    return rows.filter((r) => r.score >= 0).map((r) => ({ file: r.file, display: r.displayPath, title: r.title, score: r.score }));
  }
  throw new Error("unknown mode " + mode);
}

async function init(req) {
  mode = req.mode;
  n = req.n;
  coll = req.collection;
  store = openLikeCli();
  const status = store.getStatus();
  const configPath = C.getConfigPath();
  const docsPerColl = store.db
    .prepare("SELECT collection, COUNT(*) AS c FROM documents WHERE active = 1 GROUP BY collection")
    .all();
  return {
    version,
    dbPath: store.dbPath,
    configPath,
    configExists: existsSync(configPath),
    localConfig: C.findLocalConfigPath() ?? null,
    status: {
      totalDocuments: status.totalDocuments,
      needsEmbedding: status.needsEmbedding,
      hasVectorIndex: status.hasVectorIndex,
      collections: status.collections,
    },
    docsPerColl,
    models: {
      embed: store.llm.embedModelName,
      generate: store.llm.generateModelName,
      rerank: store.llm.rerankModelName,
      cacheDir: store.llm.modelCacheDir,
    },
    constants: {
      rerankCandidateLimit: S.RERANK_CANDIDATE_LIMIT,
      strongSignalMinScore: S.STRONG_SIGNAL_MIN_SCORE,
      strongSignalMinGap: S.STRONG_SIGNAL_MIN_GAP,
      chunkSizeTokens: S.CHUNK_SIZE_TOKENS,
      chunkOverlapTokens: S.CHUNK_OVERLAP_TOKENS,
    },
    metalNoResidency: process.env.GGML_METAL_NO_RESIDENCY ?? null,
    bootFails,
    node: process.version,
  };
}

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  let req;
  try {
    req = JSON.parse(line);
  } catch (e) {
    send({ ok: false, error: "bad json: " + String(e) });
    continue;
  }
  try {
    if (req.op === "init") {
      send({ ok: true, op: "init", info: await init(req) });
    } else if (req.op === "q") {
      const t0 = process.hrtime.bigint();
      const results = await runOne(req.text);
      const ms = Number(process.hrtime.bigint() - t0) / 1e6;
      send({ ok: true, op: "q", id: req.id, ms, results, diag });
    } else if (req.op === "device") {
      // same call `qmd status` makes; only after the warm-up so it doesn't change model load timing
      let info = null;
      if (mode !== "search") info = await store.llm.getDeviceInfo({ allowBuild: false });
      send({ ok: true, op: "device", info });
    } else if (req.op === "bye") {
      const maxrss_kb = process.resourceUsage().maxRSS;
      try {
        await store.llm.dispose();
      } catch {}
      store.close();
      send({ ok: true, op: "bye", maxrss_kb });
      break;
    } else {
      send({ ok: false, error: "unknown op " + req.op });
    }
  } catch (e) {
    send({ ok: false, op: req.op, id: req.id, error: String(e && e.stack ? e.stack : e) });
  }
}
rl.close();
process.exit(0);
