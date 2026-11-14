# Comparison data

Everything behind [the comparison page](../../docs/comparison.md): the two question sets written for it, the
rank at which every system listed the right note for every question, the cost of each run, the analysis
plan of the held-out set, and the scripts that ran the systems.

## Recount the page

```sh
python bench/compare/score.py            # every table of the page
python bench/compare/score.py --set q3   # one question set
```

Python 3.10 or newer, nothing else. It reads three tables:

| file | what it holds |
|---|---|
| `ranks_known.csv` | one row per question of q1, q1p and q3: the rank (1 to 10) at which each system listed the gold note, empty when it was not in the top 10 |
| `ranks_pairs.csv` | one row per pair of q2 and q2c: for each system the rank of the newer and of the replaced note, and whether a listed replaced note pointed to its successor (`y`), pointed elsewhere (`w`) or carried no pointer (`n`) |
| `cost.csv` | per run: questions, median and 95th percentile milliseconds per question, seconds to build the index, peak memory in MB, version and mode |

A copy of the gold note counts as the gold note, the same rule as the benchmark.

## Question sets

| file | sets | questions | sha256 |
|---|---|---|---|
| [`../public/questions.json`](../public/questions.json) | q1, q2, q2c | 777 | the benchmark's file |
| `questions_q1p.json` | q1p | 695 | `0dd0697a22f8cab2ed71902111116d355fdba3216670536f667458aa800df8e8` |
| `questions_q3.json` | q3 | 695 | `9cfcbea044d7c0c8fc7f728d267e3442066e6996f476cfbf6cb3a8fb7d37c692` |

Both new sets were written with a model, one question at a time, under the rule stored in each file, then
checked by a script and frozen before any system ran on them: q1p at 2026-11-14 00:23 UTC, q3 at
2026-11-14 01:23 UTC. "By hand" in the note of q1p means one at a time, without a template or a script.
The q3 writers saw the abstracts only: no system output and no q1p question.

## Held-out analysis plan

`ANALYSIS_q3.md` was written before any system ran on q3, and `ANALYSIS_q3.sha256` holds its hash and the
time it was taken (2026-11-14 03:15 UTC). It fixes the primary measure (hit@5), the 16 paired comparisons
with their correction and the wording rule of the page. `score.py` runs exactly those 16 tests. The plan
names the run folders and release commits of the working copy it was written in.

## Miss audit

`miss_audit_q3.json` is the check the plan fixed for its one-right-note rule: 20 q3 misses of LEKS with
models and 20 of qmd query, the best other configuration, drawn with the plan's seed and judged on one
sheet with no system names, before the key that maps the items to the systems was opened. Each item gives
the question, the gold PEP, the top hit and whether the top hit also answers the question by the plan's
rule. 2 of the 20 do on each side.

## Rerun the systems

```sh
python bench/compare/make_notes.py --out NOTES        # the 714 PEPs as notes, checked against the benchmark
python -B bench/compare/adapters/run_leks.py --system leks --notes NOTES \
    --questions bench/compare/questions_q3.json --out leks.json --work WORK
```

`run_leks.py` runs `bm25`, `leks` (this repository), `leks30` (a checkout of the `v3.0.0` tag, given with
`--repo30`) and `leks+models` (needs the models extra; `--fetch` downloads the pinned model revisions)
through the benchmark's own functions, so they run exactly as in [the benchmark](../../docs/benchmark.md).
The other tools ran through the adapters in `adapters/qmd`, `adapters/mempalace` and
`adapters/knowledge_rag`. Each one keeps HOME and every cache inside a fresh work folder, indexes the notes
with the tool's own command or library call, sends only the question text, and writes the same run file
shape as `run_leks.py`. The header of each adapter names the calls it makes and what it expects in its
folder: the tool installed there and its models already downloaded, since a run never downloads anything.

Peak memory in `cost.csv` is the largest resident size a run reached. For `leks+models` the worker starts
the model helper as a separate, detached process, so `run_leks.py` samples the whole process tree every
0.1 s with `adapters/tree_peak.py` and keeps the largest sum. That needs macOS; elsewhere the field holds the
worker alone. The other runs take the operating system's peak for their own process or their largest child.
Memory a run keeps on the GPU may not show in a resident size, so the peaks of the three GPU runs (qmd
vsearch, qmd query and `leks+models`) can read low. `leks+models` ran twice on the 1,472 known questions: the ranks and times in the tables come from the first
run, the peak memory from the second, which ran with the sampler and listed the same hits for every
question. Its times are left out because other heavy work ran on the same Mac during it.

| tool | version | installed with |
|---|---|---|
| qmd | 2.8.3 | `npm install --prefix adapters/qmd/pkg @tobilu/qmd@2.8.3` |
| MemPalace | 3.12.0 | `pip install mempalace==3.12.0` in `adapters/mempalace/venv` |
| knowledge-rag | 5.0.1 | `pip install knowledge-rag==5.0.1` in `adapters/knowledge_rag/venv` |

Models, once before any run. qmd and MemPalace download their default models on first use into HOME, so
run each once with HOME set to `adapters/qmd/home` or `adapters/mempalace/home`: qmd keeps its models in
`.cache/qmd/models`, MemPalace (through ChromaDB) keeps all-MiniLM-L6-v2 in `.cache/chroma/onnx_models`,
and its adapter checks that model's hash. For knowledge-rag,
`adapters/knowledge_rag/venv/bin/python adapters/knowledge_rag/fetch_models.py` fetches its two models with
the tool's own loaders.

All runs used Python 3.13 on one Mac with Apple silicon. The adapters are the files that ran, with
lint-only edits for this repository's rules.
