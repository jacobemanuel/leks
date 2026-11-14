# LEKS

[![tests](https://github.com/jacobemanuel/leks/actions/workflows/tests.yml/badge.svg)](https://github.com/jacobemanuel/leks/actions/workflows/tests.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab)
![License: MIT](https://img.shields.io/badge/license-MIT-2f855a)

[![A knowledge graph in 3D, one note selected and another previewed](media/graph-3d.gif)](https://majewski.studio/assets/work/leks-graph.mp4)

Local search for coding agents. Your agent asks a question and LEKS hands back the passages that answer it, from your own notes, decisions, PDFs, `.docx` files and code, each with its file and line range so the agent can cite it. The index and the models run on your machine.

## What it is for

LEKS is for projects whose knowledge has outgrown what an agent can read in one go: a docs folder or an Obsidian-style notes folder next to the code, months of decisions and specs, meeting notes, PDFs and Word files. An agent without it greps file names or opens whole files, and it can miss the note that answers or quote a decision that a later note replaced. With LEKS the agent asks in plain words and gets the few passages that answer, each cited, with a replaced note marked and pointing to the note that replaced it.

- **Ask the record before changing code.** A question such as "what did we decide about the cache key?" returns the passages that answer it, from notes, decisions, specs and code, each cited by file and line.
- **Answer from PDFs and Word files.** Papers, specs, manuals and reports are searched like notes and cited by page.
- **Keep decisions straight over months.** When a newer note replaces an older one, the older ranks below it and points to it, whether the replacement is written in front matter, in a plain `Status:` line or as a link in the opening lines.
- **Keep a growing record true.** After an edit LEKS lists the notes to bring in line, and it names notes past a freshness budget or a revisit date and notes that break the edit rules you set.
- **Search across languages.** A question in one language finds notes written in another.
- **Spend less per answer.** Per right answer the agent cost 0.72 of what it cost without LEKS, and three scripted working days came to 0.85 of the bill ([Cost](#cost)).

It fits a solo builder or a small team working with Claude Code, GitHub Copilot or Codex on a project that runs for months, with the index and the models on their own machine. For a handful of short files, plain search in the editor is enough.

## Built on a real record

I built LEKS for a real and private record I have worked in every day since February 2026. By my own count it holds 7,294 documents and 172,775 files. The search, agent and cost numbers below come from that record and were measured on 3.0. Version 3.0 searches by words and by meaning, reranks with a small local model, reads PDFs and `.docx` files, and knows when a newer note replaces an older one. Its models won a tournament of 30 model stacks played under rules I fixed and hashed in advance, and the winner then beat 2.0 again on fresh questions written after it went live. The full report with every table is in [docs/report-3.0.md](docs/report-3.0.md).

Version 3.1 keeps the search lanes and models of 3.0 and adds the parts that hold a growing record true. It reads the `Status:` and `Date:` lines under a note's title, lists the notes to bring in line after an edit, names notes past a freshness budget or a revisit date, and holds notes to edit contracts. It also serves Codex next to Claude Code and GitHub Copilot, ships 26 skills and an optional agent team, and comes with a public benchmark on the Python PEPs that anyone can rerun. CI runs the tests on macOS, Linux and Windows with Python 3.10 to 3.13, and with the real models on every push to main and once a week. [What 3.1 adds](#what-31-adds) has the details and the numbers.

## At a glance

- **Finds the right note far more often.** 158 held-out questions that no tuning had seen, each answered by a note: 85.4% in the top 5 against 61.4% for 2.0. 42 of them only 3.0 finds, 4 only 2.0 finds
- **Holds on fresh questions.** 120 questions written after 3.0 went live, from documents drawn at random: 83.3% against 46.7%, 46 questions against 2, Holm p < 0.0001. Of the 44 extra right answers, 6 come from the 80 questions answered by a note (77.5% against 70.0%) and 38 from the 40 answered by a PDF page
- **Reads PDFs.** 95.0% in the top 5 when the answer sits on a PDF page. 2.0 reads none
- **Works across languages.** The meaning lane matches across languages, so a question in another language than the notes still finds them: on the fresh questions asked in two other languages, 70.0% to 84.2% against 7.5% to 32.5% for 2.0 ([Languages](#languages))
- **Makes the agent better and cheaper.** Three scripted working days of questions and dev tasks in one conversation: 89 of 100 answers right with LEKS against 75 of 100 without (Holm p 0.0043), 14 of 14 tasks passed against 12 of 14, at 0.85 of the bill
- **Scales to a real record.** 7,294 documents (4.6 GB of text, 380,564 passages) in full and 172,775 files (249 GB) by name, as counted on my private record, where 2.0's own test searched 91 wiki pages
- **Stays local.** The models run in a separate helper process, on macOS in a sandbox with no network

## What 3.1 adds

3.1 changes no model and no search lane, so the 3.0 numbers on this page still describe its search. What it adds keeps notes true while a record grows, and opens the template to a third host.

- **Status lines count.** Many decision logs keep `Status: superseded by D-07` and `Date: 2026-10-02` as plain lines under the title. 3.1 reads them, along with ids such as D-07 in them, and ranks the replaced note below its successor with a pointer to it. In the [tool server sessions](docs/integrations.md) on 3.0, the replaced decision came first in all 20 first searches. A test now runs those searches on the same notes: it fails on 3.0 and passes on 3.1. The status words `stable`, `needs update` and `archived` are new.
- **Neighbours.** After an edit, the `neighbours` tool and `leks neighbours PATH` list the notes to bring in line, each with its reason: notes that still hold a number or a sentence the edit removed, the chain of newer and older versions, the notes that link here, the files this note links to, and the notes closest in meaning.
- **Freshness budgets and revisit dates.** A few lines of config give notes a budget in days per source class or path, and the session brief names the notes past it. The brief also names notes whose `Revisit:` date has come, and `leks revisit` lists every revisit date and condition.
- **Edit contracts.** `leks check` holds notes to rules you set per path glob: a `write-once` record keeps its body, a `frozen` text keeps its whole text, a `living` note keeps dated lines inside its log section, a note keeps the fields you name, and a superseded note names its successor. `leks check --accept FILE` keeps a change that should stay.
- **A decision log.** The `leks-decide` skill writes a decision with the recommendation first, the strongest counterargument next, its owner, its evidence and a `Revisit:` line, and `supersede-decision` replaces a decision while the old one stays readable.
- **Codex.** The template serves Codex next to Claude Code and GitHub Copilot, through `.codex/config.toml`, `.codex/hooks.json`, `AGENTS.md` and the skills ([docs/hosts.md](docs/hosts.md)).
- **Skills and an agent team.** 26 skills for recurring work such as searching the record, reweaving notes after a change, decisions, task briefs, evidence checks and closing a session, and a team of an orchestrator and nine role agents for Claude Code, Copilot and Codex ([docs/skills.md](docs/skills.md), [docs/agents.md](docs/agents.md)).
- **Public benchmark and cost harness.** `bench/public` is a benchmark on the Python PEPs that anyone can rerun, below. `bench/hosts` sends the same questions to Claude Code, Copilot CLI and Codex with and without LEKS and keeps what each session cost ([docs/costs.md](docs/costs.md)).

### On the public benchmark

My record is private, so 3.1 comes with a test anyone can rerun: the Python PEPs at a pinned commit, 695 questions that ask for one PEP by its title, and 41 pairs where a newer PEP replaces an older one, each asked as it is and with "current" in the question. A bm25 baseline and LEKS 3.0 answer the same questions, and all three run on words.

| measure | bm25 | LEKS 3.0 | LEKS 3.1 |
|---|---|---|---|
| the asked PEP first, 695 questions | 80.6% | 90.1% | 89.4% |
| the asked PEP in the top 5 | 96.7% | 98.4% | 98.4% |
| newer PEP above the replaced one, 41 pairs | 5 | 25 | 30 |
| the same, with "current" in the question | 5 | 25 | 32 |
| replaced PEP points to the newer one | none | 25 of 40 | 33 of 40 |

3.1 loses no replacement pair that 3.0 wins, and the pointer gain holds after the Holm correction (p 0.047); the ranking gains alone do not (Holm p 0.19 and 0.0625). On the asked PEP, 3.0 has 5 questions at rank 1 that 3.1 has at rank 2 (p 0.0625). Four of them are PEPs that 3.1 now knows a newer version of: it lists the newer one right above the asked one, which follows with its pointer, as intended. Both versions are about 9 points ahead of bm25 on the asked PEP first. Every interval and test is in [docs/benchmark.md](docs/benchmark.md#8-results).

### How the parts fit

Modular Context describes a knowledge base for agents in three layers: sources as the raw input, a wiki of linked notes, and a schema that types and structures them. LEKS maps onto the same three. Your files are the sources, and a `frozen` contract keeps any of them as they are. Your notes are the wiki, and the brief, `neighbours`, the check and the contracts keep them in line. The config and the template are the schema: roots, profiles, contracts, budgets, `AGENTS.md` and the skills. The core is the MCP server and the command line, which work on their own; the skills and the agent team are optional and sit on top.

The note-keeping ideas follow [Modular Context](https://github.com/klemensgc/modular-context-obsidian-plugin), an MIT-licensed Obsidian plugin: reweaving the notes around a change, freshness budgets, edit contracts, its status words and a decision log with revisit dates. The code and texts here are written for LEKS.

## What it searches

| | LEKS 2.0 | LEKS 3.0 |
|---|---|---|
| full text | Markdown pages. Its own release test ran on one wiki of 91 pages built from 2,850 source files | Markdown, PDF, `.docx` and code. In my private record 7,294 documents, 4.6 GB, 380,564 passages at test time |
| PDFs | no | 446 PDFs (4.4 GB) in my record, scanned pages through OCR on macOS |
| code | no | symbols from Python, Swift and more, 1,357 Python and 524 Swift files in my record |
| by file name and path | no | 172,775 files, 249 GB in my private record |
| what an answer is | a whole page | a passage with its file and lines, or its page, slide or symbol |

The record and its questions stay private. The tables, the statistics and the harness are public here, and `bench/` runs the same kind of measurement on a synthetic corpus that ships with the repo and on the Python PEPs.

[![A knowledge graph orbiting in 3D](media/graph-orbit.gif)](https://majewski.studio/assets/work/leks-graph.mp4)

## How it works

```mermaid
flowchart LR
    subgraph build["leks build and leks embed, then updates on change"]
        direction TB
        SRC["your folders: notes, decisions, PDFs, .docx files, code"] --> RD["readers: Markdown, PDF, .docx, code symbols, OCR on macOS"]
        RD --> WI[("word, code and file-name index")]
        RD --> VI[("meaning vectors")]
    end
    subgraph search["one search"]
        direction TB
        Q["question from the agent"] --> LW["word lane"]
        Q --> LM["meaning lane"]
        Q --> LC["code and file-name lanes"]
        LW --> FU["fusion"]
        LM --> FU
        LC --> FU
        FU --> RR["reranker"]
        RR --> OUT["passages with file and lines, a replaced note moved down"]
    end
    WI -.-> LW
    WI -.-> LC
    VI -.-> LM
    MH["model helper: separate process, no network"] -.-> LM
    MH -.-> RR
    AG["Claude Code, GitHub Copilot or Codex"] -- "MCP tools" --> Q
    OUT --> AG
```

The agent gets seven tools over MCP:

- `search` finds the passages for a question
- `read` opens the cited lines or pages
- `locate` finds files and definitions by name
- `tried` says whether a proposal was tried before and what happened then
- `neighbours` lists the notes to bring in line after an edit, each with the reason
- `state` lists the notes that say where the project stands now
- `status` shows the index size, the models and which lanes are on

Every search answer says which lanes ran, so if the models are off you see it right there. The details are in [docs/how-it-works.md](docs/how-it-works.md).

Notes also get help staying true over time. The session brief names notes whose `Revisit:` date has come, and `leks revisit` lists every revisit date and condition. With a few lines of config the brief also names notes past a freshness budget, and `leks check` holds notes to edit contracts: write-once records, frozen texts, living notes without stale dated lines, fields a note keeps, and a named successor for a superseded note ([docs/how-it-works.md](docs/how-it-works.md#keeping-notes-in-line)).

## Why models at all

2.0 was pure word search. Its default engine R1 ranks whole pages by words (BM25 with title weighting, a domain thesaurus and hand-written rules). It's tiny (about 51 MB), fast (2.0 ms p95 in its own test) and works great as long as you use the same words as your notes. With other words its recall drops: 43.8% top 5 on paraphrased questions in its own test, 9.5% on Polish questions here.

2.0 already had two model engines on the side. R4 (words plus a small embedding model) won +7.1 points on 2.0's own test, but it needed six times the memory and failed 4 of the 108 routing cases R1 passes, so it stayed off. R5 (R4 plus a reranker) did even better on the dev set at 0.5 to 1 s per query. In the 3.0 tournament I rebuilt both shapes on the new dev set: R4-like got 73.2% and R5-like 75.6%, against 68.3% for R1 and 86.6% for the best stack. So 3.0 takes R5's shape, words plus meaning plus a reranker, and a tournament picks the models.

## How 3.0 picked its models

```mermaid
flowchart LR
    T["30 stacks<br/>6 embedders x 5 reranker settings"] --> D["82 dev questions<br/>latency and memory gates under load"]
    D --> F["8 finalists<br/>drawn by rule"]
    F --> H["158 held-out questions<br/>loaded after the draw, scored once"]
    H --> W["winner ships<br/>mDenseOn + mLateOn"]
    W --> C["120 fresh questions<br/>written after it went live"]
```

**Round 1, the tournament.** 6 embedding models times 5 reranker settings (4 rerankers and none) is 30 stacks, all small open models that run locally. Every stack ran inside the same hybrid search on the same 82 frozen English dev questions, the same snapshot of the record and the same scoring, with two gates under load (four CPU busy loops and a second search running beside it): 8 s p95 per search, and a memory budget for the helper that holds both models. At the 6 GiB budget of round 1, 18 stacks passed both gates. The fixed choice rule then picked EmbeddingGemma 2 with the Granite reranker (81.7%): it ties with the top of the passing stacks and wins the tie-breaks on MRR and speed. The mLateOn reranker took the top score and five of the six best places, all inside the latency gate, at 6.8 to 8.1 GiB of memory. The six stacks with the Qwen3 reranker took 25 to 28.5 s p95 under load and dropped out.

![30 model stacks: English dev top 5 against the memory of the search helper under load](media/tournament.svg)

<details open>
<summary>All 30 stacks on dev, sorted by English top 5</summary>

82 counted questions per form. Status: within both gates at 6 GiB, over the 6 GiB memory budget, or over the 8 s latency gate.

| stack | EN top 5 (95% CI) | EN top 1 | EN MRR | PL as written, top 5 | p95 under load | memory under load, MiB | status |
|---|---|---|---|---|---|---|---|
| **mdense__mlate**, ships | **86.6** (79.3 to 93.9) | 62.2 | 0.718 | 72.0 | 4.6 s | 6,921 | over 6 GiB |
| arctic__q3r | 85.4 (76.8 to 92.7) | 48.8 | 0.627 | 70.7 | 28.3 s | 16,384 | over 8 s |
| gemma2__mlate | 85.4 (76.8 to 92.7) | 62.2 | 0.717 | 78.0 | 4.5 s | 7,083 | over 6 GiB |
| arctic__mlate | 84.1 (75.6 to 91.5) | 63.4 | 0.725 | 72.0 | 4.4 s | 8,237 | over 6 GiB |
| granite311__mlate | 84.1 (75.6 to 91.5) | 63.4 | 0.729 | 65.9 | 4.8 s | 7,603 | over 6 GiB |
| qwen3e__mlate | 84.1 (75.6 to 91.5) | 58.5 | 0.701 | 73.2 | 4.8 s | 8,132 | over 6 GiB |
| arctic__none | 82.9 (74.4 to 90.2) | 51.2 | 0.639 | 63.4 | 0.7 s | 3,730 | within |
| bgem3__mlate | 82.9 (74.4 to 90.2) | 61.0 | 0.711 | 70.7 | 4.4 s | 8,345 | over 6 GiB |
| bgem3__q3r | 82.9 (74.4 to 90.2) | 51.2 | 0.633 | 70.7 | 25.0 s | 13,312 | over 8 s |
| granite311__q3r | 82.9 (74.4 to 90.2) | 47.6 | 0.619 | 65.9 | 28.5 s | 14,336 | over 8 s |
| mdense__q3r | 82.9 (74.4 to 90.2) | 50.0 | 0.625 | 63.4 | 28.5 s | 14,336 | over 8 s |
| qwen3e__q3r | 82.9 (74.4 to 90.2) | 47.6 | 0.612 | 69.5 | 28.3 s | 16,384 | over 8 s |
| arctic__granite_rerank | 81.7 (73.2 to 90.2) | 59.8 | 0.690 | 57.3 | 3.4 s | 4,800 | within |
| bgem3__granite_rerank | 81.7 (73.2 to 89.0) | 61.0 | 0.698 | 54.9 | 4.2 s | 4,897 | within |
| **gemma2__granite_rerank**, tournament pick | **81.7** (73.2 to 90.2) | 61.0 | 0.703 | 58.5 | 4.0 s | 4,180 | within |
| gemma2__q3r | 81.7 (73.2 to 90.2) | 47.6 | 0.609 | 72.0 | 28.4 s | 15,360 | over 8 s |
| qwen3e__granite_rerank | 81.7 (73.2 to 89.0) | 56.1 | 0.681 | 58.5 | 4.0 s | 4,410 | within |
| granite311__granite_rerank | 80.5 (72.0 to 89.0) | 57.3 | 0.681 | 61.0 | 4.4 s | 4,050 | within |
| mdense__granite_rerank | 80.5 (72.0 to 89.0) | 58.5 | 0.684 | 52.4 | 3.9 s | 3,776 | within |
| arctic__msmarco | 79.3 (69.5 to 87.8) | 51.2 | 0.639 | 46.3 | 0.6 s | 3,771 | within |
| bgem3__msmarco | 79.3 (69.5 to 87.8) | 50.0 | 0.623 | 39.0 | 0.6 s | 3,861 | within |
| bgem3__none | 79.3 (70.7 to 87.8) | 52.4 | 0.635 | 68.3 | 0.8 s | 2,799 | within |
| gemma2__none | 79.3 (70.7 to 87.8) | 54.9 | 0.658 | 67.1 | 0.6 s | 3,109 | within |
| granite311__none | 79.3 (70.7 to 87.8) | 56.1 | 0.651 | 53.7 | 0.6 s | 1,964 | within |
| mdense__none | 79.3 (70.7 to 87.8) | 56.1 | 0.670 | 53.7 | 0.5 s | 1,709 | within |
| qwen3e__none | 79.3 (70.7 to 87.8) | 53.7 | 0.642 | 64.6 | 0.6 s | 3,723 | within |
| gemma2__msmarco | 78.0 (68.3 to 86.6) | 53.7 | 0.654 | 39.0 | 0.5 s | 3,149 | within |
| granite311__msmarco | 78.0 (68.3 to 86.6) | 52.4 | 0.644 | 47.6 | 0.6 s | 3,028 | within |
| mdense__msmarco | 78.0 (68.3 to 86.6) | 52.4 | 0.645 | 37.8 | 0.5 s | 2,774 | within |
| qwen3e__msmarco | 76.8 (67.1 to 85.4) | 51.2 | 0.631 | 42.7 | 0.5 s | 4,786 | within |

The baselines on the same dev questions: R5-like 75.6, words only 75.6, BM25 74.4, R4-like 73.2, R1 68.3, grep 9.8. Rerankers help most on Polish as written, where R1 reads 9.8.
</details>

<details>
<summary>All models in the tournament</summary>

| alias | job | model | maker |
|---|---|---|---|
| mdense | embedder, ships in 3.0 | mDenseOn (`lightonai/mDenseOn`, Apache-2.0) | LightOn, France |
| mlate | reranker, ships in 3.0 | mLateOn (`lightonai/mLateOn`, Apache-2.0) | LightOn, France |
| gemma2 | embedder of the tournament pick | EmbeddingGemma 2 | Google DeepMind, USA |
| granite_rerank | reranker of the tournament pick | Granite embedding reranker, English r2 | IBM, USA |
| granite311 | embedder | Granite embedding 311M multilingual r2 | IBM, USA |
| arctic | embedder | Snowflake Arctic Embed L v2.0 | Snowflake, USA |
| bgem3 | embedder | BGE-M3 | BAAI, China |
| qwen3e | embedder | Qwen3 Embedding 0.6B | Alibaba Qwen team, China |
| q3r | reranker | Qwen3 Reranker 0.6B | Alibaba Qwen team, China |
| msmarco | reranker | MS MARCO MiniLM L6 cross-encoder | sentence-transformers project |
</details>

**Round 2, the finals.** On 82 questions two stacks need about 7 points between them before a paired test can separate them, so the top of the dev board goes to a final on a bigger, untouched set. Eight finalists were drawn by rule before any held-out question was loaded: the tournament pick, the best stack of each embedder inside the latency gate and the best stack with no reranker. Then 158 held-out questions, each part scored once.

mDenseOn with mLateOn won the finals: 85.4% (95% CI 79.7 to 90.5), 10.8 points above the tournament pick, 20 questions against 3, Holm p 0.0034. With the finals the memory budget moved to 8 GiB (Amendment G in the report). The winner holds about 6.8 GiB under load, so it fits, clears the switch rule and ships. The tournament pick beat 2.0 clearly in the finals too: 74.7% against 61.4%, 29 questions against 8, Holm p 0.0015.

![Held-out English top 5 of the eight finalists and the baselines](media/finals.svg)

| arm | EN top 5 (95% CI) | EN top 1 | EN MRR | chars to the answer | median search | PL top 5 | against the pick: gap, only this arm / only the pick, Holm p |
|---|---|---|---|---|---|---|---|
| **mdense__mlate**, ships | **85.4** (79.7 to 90.5) | 53.2 | 0.656 | 1,272 | 1.4 s | 69.0 | +10.8, 20 / 3, 0.0034 |
| arctic__mlate | 81.6 (75.3 to 87.3) | 53.2 | 0.643 | 1,364 | 1.4 s | 75.9 | +7.0, 14 / 3, 0.0764 |
| granite311__mlate | 79.7 (73.4 to 86.1) | 52.5 | 0.640 | 1,389 | 1.4 s | 66.5 | +5.1, 14 / 6, 0.3850 |
| bgem3__mlate | 79.7 (73.4 to 86.1) | 53.2 | 0.639 | 1,397 | 1.5 s | 69.6 | +5.1, 10 / 2, 0.1929 |
| qwen3e__mlate | 79.7 (72.8 to 85.4) | 51.9 | 0.640 | 1,401 | 1.4 s | 67.1 | +5.1, 13 / 5, 0.3850 |
| gemma2__mlate | 79.1 (72.8 to 85.4) | 49.4 | 0.617 | 1,428 | 1.4 s | 68.4 | +4.4, 11 / 4, 0.3850 |
| gemma2__granite_rerank, tournament pick | 74.7 (67.7 to 81.6) | 50.6 | 0.614 | 1,522 | 1.2 s | 55.1 | |
| arctic__none | 73.4 (66.5 to 80.4) | 50.6 | 0.607 | 1,552 | 43 ms | 67.1 | -1.3, 7 / 9, 0.8036 |
| words only | 65.2 (57.6 to 72.8) | 41.1 | 0.515 | 1,774 | 14 ms | 17.7 | -9.5, 3 / 18, 0.0015 |
| BM25 | 62.0 (54.4 to 69.6) | 37.3 | 0.480 | 1,877 | 7 ms | 16.5 | -12.7, 3 / 23, 0.0003 |
| R1, LEKS 2.0 | 61.4 (53.8 to 69.0) | 29.7 | 0.420 | 1,949 | 437 ms | 9.5 | -13.3, 8 / 29, 0.0015 |
| grep | 8.9 (4.4 to 13.3) | 1.3 | 0.058 | 3,278 | 1.3 s | 10.8 | -65.8, 2 / 106, < 0.0001 |

**Round 3, confirmation.** 120 fresh questions written after the winner went live, from documents drawn at random per library (80 notes and 40 PDF pages), frozen by hash before the first search and scored once in five forms. 3.0 as it ships: 83.3% against 46.7% for 2.0, 46 questions against 2, Holm p < 0.0001. Against the tournament pick its biggest gains are PDF pages (95.0% against 82.5%) and questions asked in German (84.2% against 58.3%) or Polish (70.0% against 40.8%). In English the two sit close (83.3% against 80.0%).

**The rules behind every number.**

- Every comparison is paired: both systems answer the same questions, an exact McNemar test runs on the questions only one of them gets, and Holm's correction holds inside each family of tests
- A hit means the gold note is among the first five results. The 95% intervals come from 10,000 bootstrap resamples over questions (Wilson intervals on the fresh set)
- The plan, the question sets and the run records were hashed before scoring, and the tournament code refuses to start when plan and hash differ. Every later change is a dated amendment, A to M, listed in the report
- The held-out set stayed unloaded until the finalists were drawn, and the fresh set was written after the winner went live

## Tested and left out

What I measured and did not ship, with the reason. The first six rows come from the 3.0 tournament, the last two from the public benchmark of 3.1:

| tested | result | why it stayed out |
|---|---|---|
| the six stacks with the Qwen3 reranker | 81.7% to 85.4% top 5 on dev, 25.0 to 28.5 s p95 under load, 13,312 to 16,384 MiB | over the 8 s latency gate and the memory budget |
| EmbeddingGemma 2 with the Granite reranker, the tournament pick at 6 GiB | 74.7% top 5 held-out | mDenseOn with mLateOn reached 85.4% once the budget moved to 8 GiB (Amendment G), 20 questions against 3, Holm p 0.0034 |
| the best stack with no reranker (Arctic Embed alone) | 73.4% top 5 held-out | 12.0 points below the shipped pair |
| 2.0's R4 shape rebuilt: words plus a small embedding model | 73.2% top 5 on dev | the best stack reached 86.6% |
| 2.0's R5 shape rebuilt: R4 plus a reranker | 75.6% top 5 on dev | the same |
| the `tried` gate at 0.55 | a false alarm for 6 of 16 held-out ideas never tried | ships at 0.65, with no false alarm on dev |
| a link prior: lift a note that links to or from the top hits, in five variants, or one linked from a state note | the asked PEP first fell from 89.4% to between 53.2% and 76.8% in the five (Holm p below 1e-17); the state-note variant leaves the PEPs, which have no state note, as they are, and on the 40 fixture questions puts 4 fewer and 1 more answer first | no variant put the right note first more often, on either set |
| relation fields (`supersedes`, `superseded_by`) as a ranking boost of their own, at two weights | the newer PEP above the replaced one rose from 30 to 35 of 41, while the asked PEP first fell to 87.8%, 12 questions lost for 1 gained (Holm p 0.0068); the lower weight lost 9 for 1 (Holm p 0.021) | the boost costs known-item answers; the fields keep their role as proof of a newer version |

The full tables are in [section 4 of the report](docs/report-3.0.md#4-results) and [section 8 of the benchmark](docs/benchmark.md#8-results).

## 2.0 against 3.0, same questions

![LEKS 2.0 against 3.0 on the held-out and fresh question sets](media/tests-2-vs-3.svg)

| question set | LEKS 2.0 | LEKS 3.0 |
|---|---|---|
| 158 held-out English, top 5 | 61.4% (53.8 to 69.0) | **85.4%** (79.7 to 90.5) |
| same, top 1 | 29.7% | **53.2%** |
| same, MRR | 0.420 | **0.656** |
| same 158 asked in Polish, top 5 | 9.5% | **69.0%** |
| 120 fresh English, top 5 | 46.7% (38.0 to 55.6) | **83.3%** (75.7 to 88.9) |
| fresh, the 80 with a note as the answer | 70.0% | **77.5%** |
| fresh, the 40 with a PDF page as the answer | 0.0% | **95.0%** |
| fresh, asked in German | 32.5% | **84.2%** |
| fresh, asked in Polish | 7.5% | **70.0%** |
| characters the agent reads to reach the answer, held-out | 1,949 | **1,272** |
| median search time, held-out | 437 ms | 1,436 ms |

On the held-out set 42 questions are found only by 3.0 and 4 only by 2.0. On the fresh set it is 46 against 2 (Holm p < 0.0001). 2.0 runs here exactly as it ships: my harness copies each library's notes into a folder and runs 2.0's own index and search on it, untouched.

## Hold on, 2.0's own README said 79.3%. Why 61.4% here?

Fair question, I asked it myself. Same engine, same code. What moved is the test.

| | 2.0's own test | 3.0's held-out test |
|---|---|---|
| what R1 searched | one wiki of 91 compiled pages | the library of the agent that asks, 35 to 4,499 notes, 1,308 for the median question |
| chance that 5 random pages hold the answer | 5.5% | 0.1 to 0.4% for 112 of the 158 questions |
| questions | 280 answerable: 150 direct, 80 paraphrased, 50 multi-source | 158 answerable: 39 paraphrase, 37 history, 24 location, 23 evidence, 20 current state, 15 conflicting notes |
| R1's hand-written rules | tuned on 2.0's own routing cases | never saw this record |
| R1 top 5 | 79.3% | 61.4% |

The 3.0 kinds in plain words: paraphrase asks in other words than the note uses, history asks what was decided or tried earlier, location asks where something lives, evidence asks which result backs a claim, current state asks what holds now, and conflicting notes needs the newer of two notes that disagree.

Size is the big one, and 2.0 had measured that on itself: 10,000 extra distractor pages took R1 from 77.9% to 36.5%. Inside the held-out set it shows again:

![Held-out English top 5 by library size, 2.0 against 3.0](media/library-size.svg)

R1 drops 28.1 points from the small libraries to the big ones, 3.0 drops 14.1, and 3.0 keeps a lead of 29.2 points in the biggest libraries.

On the one kind of question both tests share, paraphrase, R1 gets 41.0% here and got 43.8% in 2.0's test. Same engine, same kind of question, about the same score. R1 is strong when a question names its subject (location, 21 of 24) and weak on conflicting notes (6 of 15), and 3.0's set has more of the hard kinds.

And the smaller question set? A smaller set only widens the error bar, about 4.7 points either way at 280 questions and 7.6 at 158. The gap is 17.9 points, more than both bars together.

Why a new test at all: 2.0's answer keys point at pages of that 91-page wiki, so they can't score PDFs, code, file names or German and Polish questions, and choosing between 30 stacks needs questions nobody tuned on. Benchmarks for AI models work the same way: when Scale released [SWE-Bench Pro](https://scale.com/blog/swe-bench-pro) in 2025, models that scored above 70% on SWE-bench Verified got about 23% on it, and nobody read that as the models getting worse. You compare inside one version of a test. 1.0 and 2.0 had their own tests too (177 and 320 questions). So every 2.0 against 3.0 comparison in this README runs both engines on the same questions, notes and scoring, apart from the figures I quote from 2.0's own release, which say so. More in [section 2.4 of the report](docs/report-3.0.md#24-why-20-scores-lower-here-than-in-its-own-release).

## Languages

![Right answer in the top five by question language](media/languages.svg)

The same 120 fresh questions in five forms: English, German and Polish machine translations, and those two translated back into English by a second pass that saw only the translations. Back in English, all three land at the same level (84.2%, 82.5%, 83.3%). Asked as they are, German keeps up (84.2%) and Polish reaches 70.0%, from 7.5% in 2.0. My notes are in English, so putting a Polish question into English first is worth another 12.5 points here, and the agent instructions in the template tell the agent to search in the language the notes are written in.

## With an agent, end to end

Search scores are half the story, what counts is whether the agent does its job better. So I ran Claude Code on my record with LEKS and without it, a fresh session per question and arm, graded blind: the judge saw the answers in shuffled order and no arm labels, and a second blind pass agreed with it on 131 of 140 grades.

![Right answers with and without LEKS across the end-to-end tests](media/end-to-end.svg)

| | with LEKS 3.0 | without |
|---|---|---|
| 35 project questions, one session each | 32 of 35 right | 28 of 35 |
| two long sessions of 26 turns with a forced compaction | 49 of 52 right | 39 of 52 |
| "was this tried before" turns in those sessions | 5 of 6 | 1 of 6 |
| ten development tasks in scratch clones | 9 of 10 passed | 7 of 10 |
| three working days, two scenarios, a cold cache every morning | 89 of 100 right, 14 of 14 tasks | 75 of 100, 12 of 14 tasks |

The working days carry the formal test: 18 question turns only the LEKS sessions got right against 4 only the others did, Holm p 0.0043, at 0.85 of the bill and 0.86 of the time. In the long sessions the 10 extra right answers clear the bar fixed before the data. In the dev tasks the two extra passes are plans for a proposal an earlier decision had rejected: with LEKS the agent found that decision. Nothing searches by itself, the models run only when the agent calls a tool: on 16 general questions that needed no search the agent never called it, and the cost on them stayed the same (ratio 0.999).

**The 35 questions in detail**, means per question, Claude Code at medium effort:

| model | search | right | prompt tokens | wall time |
|---|---|---|---|---|
| sonnet | LEKS 3.0 | 32 of 35 | 36,998 | 9.7 s |
| sonnet | LEKS 3.0 without the reranker | 29 of 35 | 38,790 | 8.0 s |
| sonnet | LEKS 3.0, words only | 29 of 35 | 39,613 | 8.2 s |
| sonnet | none, its own file tools | 28 of 35 | 51,711 | 11.7 s |
| haiku | LEKS 3.0 | 23 of 35 | 79,411 | 16.8 s |
| haiku | none, its own file tools | 17 of 35 | 132,834 | 23.1 s |

The haiku rows come from the second blind pass, which graded them together with all the sonnet answers (sonnet 31 of 35 with LEKS and 28 without in that pass). Every search arm cuts the prompt by about a quarter and the wall time by 2 to 4 s. The reranker's gain shows in the search scores above, and any search arm brings the token saving.

**A small model as the agent.** Some hosts hand the reading to a small model, Copilot's Explore subagent does that by default (see [docs/hosts.md](docs/hosts.md)). So I ran the 35 questions again with haiku as the only model. With LEKS it got 23 right, without 17, and per right answer it paid half (0.49, 95% CI 0.27 to 0.81), with 40% less prompt and 6 s less per question. The large model with LEKS still leads (31 against 23), so for Copilot the template's VS Code settings carry the line that sets the Explore model.

## Cost

LEKS itself makes no paid calls. What changes is the agent's bill, and that drops. All of it was measured on a small, bounded test budget (the largest single arm, three scripted working days without LEKS, came to 12.33 USD), priced from the Claude Code CLI's own cost report at API list prices with the prompt caching the host applies by default. So the ratios carry more than the sums.

![Cost of the agent with LEKS as a share of the cost without it](media/cost.svg)

- **per right answer, 35 questions:** 0.72 of the cost without LEKS (95% CI 0.56 to 0.88)
- **three working days, every morning with a cold cache:** 0.85 of the bill (0.80 to 0.90) and 0.86 of the time (0.79 to 0.93)
- **two models, effort from low to max, nine settings:** cheaper per right answer in all nine, ratios 0.45 to 0.85
- **a small model as the agent:** 0.49 of its own cost per right answer (0.27 to 0.81)

**Resources.** The model helper holds about 6.8 GiB under load, with peaks up to 8.1 GiB, and a search with both models takes 1.4 to 1.7 s at the median. Words, code and file names alone answer in about 40 ms with no model memory.

## Scope

- **Platforms.** Built and measured on macOS with Apple silicon, where the model helper runs in a sandbox and scanned pages go through the Vision framework. CI runs the tests on macOS, Linux and Windows with Python 3.10 to 3.13, and on every push to main and once a week loads the real models on Linux, on CPU, and runs the bench with them. On Linux the models run without the macOS sandbox. On Windows the tests run with a stand-in model backend, and the model helper is reached over a local TCP port with a token ([docs/install.md](docs/install.md#what-you-need)).
- **Memory.** The model helper wants about 6.8 GiB, with peaks up to 8.1 GiB. On a smaller machine LEKS runs on words, code symbols and file names, and `leks status` says which lanes are on.
- **Hosts.** Claude Code, GitHub Copilot in VS Code and Codex, through the same template. I measured agent sessions on Claude Code. The Copilot and Codex files follow the documented formats of those hosts and tests check them, and I have not measured a Copilot or Codex session with LEKS.
- **Answers.** LEKS returns the best passages it has, and the agent decides whether they answer the question.
- **Evidence.** The search, agent and cost numbers come from my private record and were measured on 3.0. 3.1 changes no lane and no model; its own numbers come from the public benchmark and the tests.
- **Core and extras.** The core is the MCP server and the command line. The hooks, the skills and the agent team are optional, and the core works without them.
- **What is public.** The code, the tests, the harnesses, the result tables and the statistics. My record, its questions and the agent transcripts stay private, so the record numbers cannot be rerun from outside; the public benchmark can.
- **Security.** [SECURITY.md](SECURITY.md) says where your data goes and how to report a problem. CI scans for secrets and runs CodeQL, bandit and pip-audit. LEKS has had no external security audit.

## Install

The step-by-step guide, with what each command does and what lands where on your disk, is [docs/install.md](docs/install.md). The quick version:

```bash
git clone https://github.com/jacobemanuel/leks
cd leks
python3 -m pip install -e ".[models,pdf]"
leks init            # writes a starter config to ~/.leks/config.json
# put your folders into that config, docs/install.md shows how
leks models fetch    # downloads the two models once
leks build           # word, code and file-name index
leks embed           # meaning vectors
leks search "what did we decide about the cache"
```

Without the `models` extra LEKS searches by words, code symbols and file names, and `leks status` says which lanes are on and why. Requires Python 3.10 or newer, and 3.11 or newer for the `models` extra.

The test suite (a stand-in model backend, no downloads) runs with `python3 -m pip install -e ".[pdf,test]"` and `python3 -m unittest discover -s tests -t .`. CI runs it on macOS, Linux and Windows with Python 3.10, 3.11, 3.12 and 3.13 for every push to main and every pull request; [docs/testing.md](docs/testing.md) lists every job, what it proves and what it leaves out.

## Connect an agent

Copy [examples/project-template](examples/project-template) into your project, apart from its README.md. One set of files serves all three hosts:

- Claude Code reads `.mcp.json`, `.claude/settings.json`, `CLAUDE.md` and the skills in `.claude/skills`
- GitHub Copilot in VS Code reads `.vscode/mcp.json`, `.github/hooks/leks.json`, `AGENTS.md` and the same skills
- Codex reads `.codex/config.toml` and `.codex/hooks.json` in a trusted project, `AGENTS.md`, and the skills once you link them into `.agents/skills`. It runs the project's hooks after you have reviewed and trusted them with `/hooks`

The agent gets the seven tools. Through the hook files it also gets a short brief and an index update at session start, and a notes check after each turn (empty notes, dead `[[links]]`, new notes nothing links to, em dashes unless you switch that off, and the edit contracts you set). Host details, including which model Copilot uses when it hands reading to a subagent, are in [docs/hosts.md](docs/hosts.md). The optional agent team, an orchestrator and nine role agents, is in [examples/agent-team](examples/agent-team) and described in [docs/agents.md](docs/agents.md).

## Other MCP servers

LEKS sits next to tool servers. In ten sessions per tool, an agent took the spec of a display pedestal from the project notes through LEKS and built it in Blender through the Blender Lab MCP server, or in Unity through MCP for Unity. A check script the agent never saw passed all 20 sessions (Wilson 95% interval 0.72 to 1.00 per tool), at a median of 17.6 s in Blender and 24.4 s in Unity. Set-up and results: [docs/integrations.md](docs/integrations.md).

## Privacy

LEKS sends nothing anywhere. The index stays in its home folder. The models are downloaded once from Hugging Face and then run in a separate helper process. On macOS that process runs under a sandbox profile with no network, no writes outside its run, cache and temp folders, and no new programs. Scanned PDFs and images are read with the on-device macOS Vision framework. Your agent host still sends what the agent reads to its model provider, as it does with any file the agent opens.

## Documentation

- [Install](docs/install.md): every step, what it downloads and writes, how to undo it
- [Configuration](docs/configuration.md): config lookup, roots, profiles, source classes, models, OCR, limits, edit contracts, freshness budgets
- [How it works](docs/how-it-works.md): indexing, the search lanes, fusion, the model helper, updates
- [Command line](docs/cli.md)
- [Testing](docs/testing.md): every CI job and test group, what it proves and what it leaves out
- [Hosts](docs/hosts.md): Claude Code, GitHub Copilot and Codex
- [Integrations](docs/integrations.md): LEKS next to the Blender and Unity MCP servers, ten tested sessions each
- [Skills](docs/skills.md): the 26 skills of the project template, optional
- [Agents](docs/agents.md): an optional agent team for Copilot, Claude Code and Codex, an orchestrator and nine role agents
- [Report 3.0](docs/report-3.0.md): method and every result
- [Public benchmark](docs/benchmark.md): a test on the Python PEPs that anyone can rerun, with bm25 and LEKS 3.0 next to this version, and the ideas tested there that did not ship
- [Costs](docs/costs.md): how Claude Code, Copilot and Codex bill, what was measured and what the cost harness measures
- [History](docs/history.md): the versions from 1.0 to 3.1 and where each one lives in this repository
- [Security](SECURITY.md): where your data goes and how to report a problem

## License

MIT. Copyright 2026 Jakub Majewski.
