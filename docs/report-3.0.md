# LEKS 3.0: how the models were chosen and what the tests show

Report for release 3.0.0, November 2026. Every number below comes from a real and private record I have worked in every day since February 2026, measured on macOS with Apple silicon. Each table says how many questions stand behind it.

## Summary

LEKS 3.0 is a local search layer that finds the right project note for a coding agent before it answers. Its two models came out of a tournament fixed in advance: 30 stacks (6 embedding models by 5 reranker settings) on 82 frozen English development questions, under a latency gate and a memory budget. Inside the 6 GiB budget the fixed rule picked gemma2 with granite_rerank. Eight finalists, drawn by rule before any held-out question was loaded, then met 158 held-out English questions, scored once. mDenseOn with mLateOn (mdense__mlate) won the finals with the right note in the top five for 85.4% (95% CI 79.7 to 90.5), 10.8 points above the pick (20 questions against 3, Holm-adjusted p 0.0034). With the finals the memory budget moved to 8 GiB (Amendment G); the pair holds about 6.8 GiB under load, so it fits, and it ships. R1, the default engine of LEKS 2.0, read 61.4% (53.8 to 69.0) on the same questions, and the pick beat it as well: 74.7% (67.7 to 81.6), 29 questions against 8, exact McNemar p 0.0008, Holm p 0.0015. In my Polish wording R1 found 9.5% and the shipped pair 69.0%.

A confirmation test fixed in advance (Amendment L, section 4.4) then put 120 fresh questions, written after the pair went live from documents drawn at random, 40 of them with a PDF page as the answer, to the shipped pair, the pick and R1, each part scored once. The shipped pair reached 83.3% against 46.7% for R1 (46 questions found only by the pair, 2 only by R1, Holm p < 0.0001) and 80.0% for the pick. It reads PDF pages at 95.0%, and it leads R1 on English notes as well (77.5 against 70.0%). Its widest margins over the pick are PDF pages (95.0 against 82.5%) and questions asked in German (84.2 against 58.3%) and Polish (70.0 against 40.8%). Put into English first, German and Polish questions reach the English level (84.2 and 82.5% against 83.3%).

End to end, the same coding agent (Claude Code, one model at medium effort) answered 35 questions once with LEKS and once with its own file tools only, graded by a blind judge: 32 against 28 correct, at 0.72 of the cost per correct answer (95% CI 0.56 to 0.88). Over three scripted working days, with a cold prompt cache every morning, it answered 89 of 100 question turns right with LEKS against 75 without (Holm p 0.0043), at 0.85 of the bill. With a small model (alias haiku) as the only model, search lifted the agent from 17 to 23 of 35 right and halved its cost per correct answer (0.49, 95% CI 0.27 to 0.81).

Next to a tool server, the hand-over held: in ten sessions with the Blender MCP server and ten with MCP for Unity, the agent took the current decision from five notes through LEKS and built it in the tool, and a script it never saw passed all twenty (Wilson 95% CI 0.72 to 1.00 per tool; section 4.15).

## Terms

| term | meaning here |
|---|---|
| stack | one embedding model plus one reranker setting (a reranker or none), written `embedder__reranker`, for example `mdense__mlate` |
| arm | one system run over one question set; comparison arms can never be picked |
| dev, held-out | the development questions the choice used; the questions frozen and loaded by no run before the finalists were drawn, each part scored once |
| top-5 | share of counted questions whose gold note is among the top five results; top-1 likewise |
| MRR | mean reciprocal rank of the gold note, scored to rank 10 |
| cost proxy | mean characters an agent reads to reach the gold passage (section 3.4) |
| R1 | the default engine of LEKS 2.0 |
| Holm p | a p value adjusted for the number of comparisons in its family |
| sonnet, opus, haiku | model aliases of the Claude Code CLI, which the end-to-end harness passes as `--model`; each names a model family, and the CLI picks the model behind it |

**Models by alias.**

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

## 1 The question

I keep my project notes in seven libraries, one per working role: the architect, the orchestrator and five further roles. A coding agent works in each. Before it answers a question such as "why was this library chosen?", it has to read the note that holds the answer, and it may read only what its role is allowed to see. The notes are almost all English (a crude letter count marks 0.0 to 0.8% of passages as Polish), while my own messages often are not: 42 to 67% of the session history is Polish.

The question for 3.0:

> Which local embedding model and reranker, added to the word search, put the right note in the top five most often on these notes, within the time and memory a laptop can spare beside its other work, without letting one role's search reach another role's notes? And does the answer hold on questions that no choice has seen?

What I fixed before the work:

- Local: the search models run on the machine, and no cloud model takes part in the search step.
- Per role: each role has its own library.
- Measured choice: frozen questions, a rule hashed before any data, paired tests and a held-out test scored once.
- Real cost: the agent's token bill is measured end to end, apart from the retrieval scores.

## 2 From 2.0 to 3.0

### 2.1 What 2.0 was

LEKS 2.0 uses R1 as its default engine. R1 ranks whole Markdown pages by words (BM25 with title weighting, a domain thesaurus and hand-written rules) from an index held in memory, and it uses no model. 2.0 also ships two optional engines: R4 adds an embedding model to the word search, and R5 puts a reranker on top of R4. R1 stayed the default because R4 passed four fewer of 2.0's 108 routing regression cases and needs more memory, a model and a helper process. 2.0's own published test, on its own record of 91 pages and 2,850 source files, gave 79.3% top-5 for R1 and 86.4% for R4 on 320 questions, and showed R1 falling from 77.9% to 36.5% when 10,000 synthetic distractor pages were added.

### 2.2 What 3.0 changes

- It takes 2.0's R5 shape (words plus meaning, then a reranker) and picks both models by the tournament in section 3.
- It searches passages instead of whole pages, and each result cites its file with line, page, slide or symbol.
- It indexes code symbols, file names, PDFs and `.docx` files next to Markdown, with OCR for scanned pages and images on macOS.
- When a question asks what holds now, a note that a newer note replaces moves down.
- The models run in a separate helper process, under a sandbox profile on macOS, and every search says which lanes ran.
- It handles questions in Polish, measured below, and in German (section 4.4).

### 2.3 Side by side

R1 ran untouched through my harness on the same notes. The scoring counts only questions whose answer the asking role may see and maps R1's page hits back to notes, so these numbers do not match 2.0's published ones (section 2.4).

| measure | 2.0 (R1) | 3.0 pick, gemma2__granite_rerank | 3.0 shipped, mdense__mlate |
|---|---|---|---|
| dev, English top-5 (82 questions) | 68.3 | 81.7 | 86.6 |
| dev, Polish translated back to English top-5 (82) | 64.6 | 82.9 | 85.4 |
| dev, Polish as I wrote it, top-5 (82) | 9.8 | 58.5 | 72.0 |
| held-out English top-5 (158) | 61.4 (53.8 to 69.0) | 74.7 (67.7 to 81.6) | 85.4 (79.7 to 90.5) |
| held-out English top-1 (158) | 29.7 | 50.6 | 53.2 |
| held-out English MRR (158) | 0.420 | 0.614 | 0.656 |
| held-out Polish top-5 (158) | 9.5 (5.1 to 14.6) | 55.1 (46.8 to 62.7) | 69.0 (61.4 to 75.9) |
| fresh English top-5, Amendment L (120: 80 notes, 40 PDF pages) | 46.7 (38.0 to 55.6) | 80.0 (72.0 to 86.2) | 83.3 (75.7 to 88.9) |
| fresh English, note questions only (80) | 70.0 | 78.8 | 77.5 |
| fresh English, PDF questions only (40) | 0.0, reads no PDFs | 82.5, PDFs by words only | 95.0 |
| fresh, German as asked (120) | 32.5 | 58.3 | 84.2 |
| fresh, Polish as asked (120) | 7.5 | 40.8 | 70.0 |
| fresh, German put into English (120) | 46.7 | 76.7 | 84.2 |
| fresh, Polish put into English (120) | 47.5 | 76.7 | 82.5 |
| characters read to the gold passage, held-out English | 1,949 | 1,522 | 1,272 |
| median search time, held-out English | 437 ms | 1,220 ms | 1,436 ms |
| memory of the search | 2.0 publishes about 51 MB on its 91 pages | 4,180 MiB under load | 6,921 MiB under load, peaks up to 8,325 MiB |

Two reconstructions of 2.0's optional engines ran on dev as comparison arms (section 3.4): R4-like read 73.2% English top-5 and R5-like 75.6%, with 40.2% and 32.9% on Polish as written.

### 2.4 Why 2.0 scores lower here than in its own release

2.0's README gives R1 79.3% top-5 (222 of 280 answerable questions, Wilson 95% interval 74.2 to 83.6). On 3.0's held-out English set the same engine reads 61.4% (97 of 158, 53.8 to 69.0). The intervals do not overlap, so the gap is larger than sampling noise. R1's code is the same in both: my harness copies each library's notes into a folder and runs 2.0's own index and search on it. The test is what moved.

Every LEKS version has had its own test: 177 questions for 1.0, 320 for 2.0, the sets in section 3.3 for 3.0. 2.0 already printed that its two tests cannot be compared (R1 read 75.7% top-5 on 1.0's set and 79.3% on its own). A score compares only inside one test, so every 2.0-against-3.0 comparison in this report runs R1 beside 3.0 on the same questions, record and scoring.

| | 2.0's frozen test | 3.0's held-out test |
|---|---|---|
| what R1 searched | one wiki of 91 compiled pages | the asking role's library, 35 to 4,499 notes; the median question searched 1,308 |
| chance that a random top 5 holds the gold | 5.5% | 0.1 to 0.4% for the 112 questions in libraries of 1,194 notes or more |
| counted as found when | the relevant page is in the top 5 | the gold note, or a listed copy of it, is in the top 5 |
| question kinds, counted | 150 direct, 80 paraphrased, 50 multi-source (280) | 39 paraphrase, 37 history, 24 location, 23 evidence, 20 current state, 15 conflicting notes (158) |
| R1's hand-written ranking rules | tuned on 2.0's own routing cases | never saw this record |
| R1 top-5 | 79.3% | 61.4% |

**Size.** 2.0 measured it on itself: 10,000 synthetic pages built from real sentences took R1 from 77.9% to 36.5% top-5 on its development set. The held-out set shows the same pattern.

| libraries, by notes R1 indexed | questions | R1 | 3.0 pick | 3.0 shipped |
|---|---|---|---|---|
| 35 and 602 | 46 | 73.9 | 80.4 | 89.1 |
| 1,194 to 1,394 | 64 | 64.1 | 81.3 | 90.6 |
| 2,052 and 4,499 | 48 | 45.8 | 60.4 | 75.0 |

Every arm loses ground in the two largest libraries, and R1 loses the most: 28.1 points from the smallest group to the largest, against 20.0 for the pick and 14.1 for the shipped pair. The libraries also differ in their notes and their questions, and size is the difference that 2.0's own distractor test points to.

**Kinds of question.** R1 is strong when a question names its subject (location, 21 of 24) and weak on paraphrase (16 of 39, 41.0%) and on conflicting notes (6 of 15). Its paraphrase score sits near the 43.8% that 2.0 published for paraphrase on its own test, so on the kind both tests share R1 does about what it did before. 3.0's set has no plain direct kind and adds current state and conflicting notes, which 2.0's set did not have.

**Set size.** A smaller set widens the interval and leaves the expected score where it is: about 4.7 points either side at 280 questions, 7.6 at 158. The gap of 17.9 points exceeds both together.

**Why a new test.** 2.0's answer keys name pages of its 91-page wiki. 3.0 searches the record itself by passage, with PDFs, `.docx` files, code, file names and questions in Polish and German, and the old keys score none of that. A choice among 30 stacks also needs questions that no arm was tuned on, so the held-out set was loaded only after the finalists were drawn. Public benchmarks for AI models are versioned the same way: a harder new version scores the same model lower than the old one did, and results compare only inside one version.

## 3 Set-up and method

### 3.1 The record and the machine

- About 7,300 documents in seven libraries; the architect's, the widest, sees 7,194. At tournament time the libraries held 380,564 passages in all, and the embedders saw 205,997 distinct non-PDF passage inputs. The word index takes 2.7 GB, each embedder's vectors 1.89 to 2.35 GB.
- A Mac with Apple silicon and 18 GB of memory.
- In my set-up each library is also built and searched inside its own OS sandbox behind a launcher and a policy hook, which are specific to my machine and stay outside this repository.
- This repository is the engine that was measured, prepared for release, and `bench/` runs the same kind of measurement on a small synthetic corpus that ships with it. Since the runs I extended two behaviours. For hosts without a warm-up hook, a search through the MCP server now starts the model helper when it finds it down, and the helper frees its start lock as soon as it listens; the measured sessions ran in Claude Code, where a warm-up hook starts the helper before each search. And in a Claude Code hook run the notes check now walks the folder the session started in, so a `cd` in the agent's shell no longer moves it. Smaller fixes touched config parsing and error messages. None of these changes touches the ranking code.

### 3.2 Fixed in advance

I wrote the plan and hashed it on my machine on 6 November, before any tournament data. Every later change is a dated amendment (section 3.7). The tournament code refuses to start when plan and hash differ, and every results note prints the hash. One corpus snapshot served every arm, and any change to it would have ended the run; the index update was held for the whole run. The tournament's scoring job matched the stand-alone scorers row by row on the arms with no model: 98 rows each, 0 rows differ.

### 3.3 Question sets

The questions were written from the notes, checked mechanically and frozen by hash before any run. I wrote the Polish wording.

| set | size | counted | forms | use |
|---|---|---|---|---|
| dev | 98 (89 answerable, 9 unanswerable) | 82 | English; Polish as I wrote it; that Polish translated back to English by a separate pass that never saw the English original | the choice |
| held-out | 182 | 158 | English; Polish as I wrote it | scored once, after the finalists were drawn |
| supplementary | 70 dev, 100 held-out | all | English; Polish | gold in PDFs, code or file names; outside the choice |
| prior work | 24 dev, 49 held-out | all | as frozen | the `tried` tool (section 4.7) |
| source hierarchy | 29 cases | all | as frozen | section 4.6 |
| Polish gold | 28 | 28 | English; Polish | the gold is a Polish passage (section 4.5) |
| end to end (F5) | 35 | 35 | English | section 4.9 |
| language test (Amendment L) | 120 (80 with a note as gold, 40 with a PDF page) | 120 | English; Polish and German from one translating pass; both put back into English by a second pass that saw only the translations | fresh questions on the live corpus, scored once (section 4.4) |

A question counts when it is answerable and its gold note is visible to the asking role. The 82 counted dev questions fall into six kinds: conflicting notes 13, current state 8, evidence 17, history 15, location 14, paraphrase 15. Earlier runs that tuned the search code used the dev questions, for every arm alike, so the dev set served for tuning and the held-out and fresh sets decide.

### 3.4 The tournament

**Systems.** Thirty eligible stacks: six embedders (bgem3, qwen3e, arctic, granite311, gemma2, mdense) by five reranker settings (q3r, mlate, granite_rerank, msmarco, none), each inside the same hybrid search a live search makes. Comparison arms can never be picked: each embedder alone with no word lane (`__dense`), bm25, grep, `words_hybrid` (the words-only search I ran before 3.0), R1 and its plain variant R1p run untouched, and two labelled reconstructions of 2.0's optional engines. R4-like fuses R1's page ranking with an embedder's page ranking by reciprocal rank fusion (k = 60); R5-like lets msmarco re-score R4-like's top 20 pages. 2.0's R4 and R5 helpers are not public and the small embedder 2.0 names (bge-small-en-v1.5) was not usable here, so both reconstructions use granite311 in its place and compare shape only.

**Metrics.** The primary metric is English dev top-5: answerable questions with gold visible to the role, scored to rank 10, one passage per note. Secondary: top-1, top-10, MRR, both Polish forms, latency, memory, cold start, embedding rate, store size and the cost proxy. The cost proxy is the mean of 700 + 260 x (r - 1) characters for a gold passage at rank r from 1 to 5, and 3,480 characters for a miss, which stands for the whole output read plus one more search. It counts passage characters only; section 4.9 measures the real bill.

**Gates under load.** Latency gate: p95 search time of at most 8 s in the live path, with 20 s for the embedder and 8 s for the reranker; a search that lost a model lane counts as over. Memory guard: 6,144 MiB (6 GiB) for the helper holding both models; a stack above it is flagged and wins only if no other stack passes. Each stack ran in its own helper while four CPU busy loops and one other search ran beside it, over 42 timed searches; p95 is the third largest of the 42. Memory is the larger of `top` and `ps`, sampled every 2 s, in MiB as both report it (1 MiB = 1,048,576 bytes, 1 GiB = 1,024 MiB).

**Choice rule.** The leader is the stack with the highest English dev top-5 among those that pass the gate and the guard. A stack within 2.0 points of the leader and not separated from it at Holm p below 0.05 is tied. Among tied stacks, in this order: lower cost proxy (to 100 characters), higher English MRR (to 0.01), lower p95 under load (to 100 ms), higher translated Polish top-5, higher Polish top-5 as written, lower memory (to half a gigabyte), a model-provenance preference of mine, name. The same rule run only on the stacks that preference does not flag names a second pick.

### 3.5 Finalists, held-out test and switch rule

Finalists came from the final dev table by rule, before any held-out question was loaded: the best stack of each embedder inside the latency gate, the dev pick, the best stack with no reranker, and the best stack the provenance preference does not flag. Baselines: words_hybrid, bm25, grep and R1. The draw on 9 November gave eight finalists: gemma2__granite_rerank, the six `__mlate` stacks and arctic__none.

Each held-out part was scored once: English (158), Polish as written (158), and the supplementary parts (100 each). No blind translation of the held-out questions was made beforehand, so the held-out test has no translated Polish.

Switch rule: the dev pick stays live unless a finalist beats it on English held-out top-5 by more than 2.0 points at Holm p below 0.05 and passes every gate the pick passes. The held-out test ran on the dev snapshot with the search code that went live, which adds to the dev code a patch set for result marks, tool texts, the prior-work tool, a file-name lookup, a low-confidence flag and update speed. Because a switch uses the held-out questions, the fresh set of section 4.4 tests the live system again on questions no choice has seen.

### 3.6 Statistics

- Comparisons are paired per question: exact two-sided McNemar tests on the top-5 discordant questions, with Holm correction inside each family.
- Dev families: the leader against each other stack that may win (17), and the pick against each of 7 baselines. Held-out families: each finalist against the pick (7), and the pick against each baseline (4).
- Intervals: bootstrap 95% over questions, 10,000 resamples, seed 20261106, percentile method.
- Resolution: one question is 1.22 points on dev, 0.63 on held-out and 1.00 on the supplementary parts. An exact McNemar test at p below 0.05 needs at least 6 one-sided wins against 0, 8 against 1 or 10 against 2. On dev that is a gap of at least 7.3 points, which is why stacks within the 2-point band count as tied and why the top of the dev table goes on to a larger held-out final.

### 3.7 Amendments

Each amendment is dated; section 6 lists the hashes of the plan and the main amendments.

- **A**, 6 Nov, before the real start: the latency gate, a memory gate, the load measure, the translated Polish arm, a tie-break order.
- **B**, 6 Nov, after the start and before any score was read: the memory gate became a guard, and the tie-break order changed.
- **C**, 7 Nov, while the queue ran: the final tie-break order and the cost proxy. The primary metric and the tie band stayed.
- **D**, 7 Nov, a real error fixed end to end: the steps after the merge (pushing down a replaced note, lifting the newer one) re-sorted by merged scores and discarded the reranker's order in the libraries that have replaced notes. A test with a stand-in reranker failed 6 checks on the old code and passes on the new one; 40 result files ran again. Only counts of changed ranks and timings were read to find it.
- **E**, 7 Nov: an independent read-only audit, blind to top-k, MRR and cost, found no error that moves a number in the choice and added six checks for after the run (section 4.1).
- **F**, 8 Nov, before any held-out question was loaded: finalists by rule, held-out scoring, the switch rule and the end-to-end test. It names no stack.
- **G**, 9 Nov, with the finals: the memory guard went from 6,144 to 8,192 MiB (6 to 8 GiB), for live use and for the switch rule (section 4.2).
- **H**, 10 Nov, before any end-to-end run, with an addendum after a review of the harness: arms, settings, questions, measures, hygiene and budget of F5.
- **I**, 10 Nov, before any run on the live stack: a long mixed session and ten development tasks, search on and off.
- **J**, 10 Nov, before any analysed run: three working days of mixed work.
- **K**, 10 Nov, before any analysed run: does the agent leave search alone when a question does not need it.
- **L**, 10 Nov, before any L question existed: 120 fresh English questions, 40 of them with a PDF page as gold, in five forms; the shipped pair against R1 as the primary test and against the dev pick, Holm over the two; languages and PDFs secondary.
- **M**, 10 Nov, before any of its sessions: the small model (alias haiku) as the only model in the F5 harness, arms `live` and `plain` on the 35 F5 questions, one blind judge pass over its answers and those of the F5 main run. Asked because a host may hand the reading to a small model ([hosts.md](hosts.md)).

### 3.8 The end-to-end test (F5)

F5 asks what the agent pays, in tokens and time, per correct answer, with each kind of search. Its rules were written before any F5 answer existed.

| arm | search tool | what it searches |
|---|---|---|
| `live` | yes | the shipped pair, mdense with mlate |
| `noreranker` | yes | mdense with no reranker |
| `words` | yes | the word lanes only |
| `plain` | no | nothing; the agent's own file tools and the session-start brief |

- Questions: the 35 questions of an earlier words-only end-to-end run (5 per library, from the frozen sets), round-robin over the libraries, with the order of arms rotated per question.
- Settings: the main run puts every arm on one model (alias sonnet) at medium effort. A sweep runs `live` and `plain` on a fixed subset of 8 questions, chosen by id before any run, across two models and effort levels from low to max.
- Measures per run: wall time, turns, tool calls, characters returned by tools, input, output and cache tokens, and the CLI's own cost figure, also re-priced as a cold start. Quality: whether the answer cites the gold note (automatic), and a blind grade (correct, partly, wrong) by a judge that sees no arm, model or effort label, in shuffled order. The headline is cost per correct answer, with bootstrap intervals over questions.
- Hygiene: the index is held for the whole run; every arm gets Read, Grep and Glob, and shell, writing tools and tool search are denied; a test hook refuses reads of the index folder and the answer keys for every arm alike; a per-session tag keeps sessions from reading each other's cached question; each session's transcript leaves the agent's history after the run.
- Deviations fixed before the run: the `noreranker` arm uses mdense without mlate, because the plan's arctic__none store lacked about 190,000 newer passages; R1 is no session arm, because its index is not split by library.

## 4 Results

### 4.1 Development tournament

Of the 30 stacks, 18 pass both the latency gate and the memory guard. The six mlate stacks pass the latency gate (p95 4,401.5 to 4,809.8 ms) and are flagged by the guard (6,921 to 8,345 MiB). The six q3r stacks miss both: 41 or 42 of their 42 timed searches lost a model lane, at 13,312 to 16,384 MiB.

- Leader among the 18: arctic__none, 82.9 (95% CI 74.4 to 90.2).
- Tied with it (within 2.0 points, Holm p 1.0000 over 17 comparisons): arctic__granite_rerank, bgem3__granite_rerank, gemma2__granite_rerank and qwen3e__granite_rerank, all 81.7.
- Tie-breaks: all five cost proxies round to 1,300 characters; English MRR rounds to 0.70 for gemma2__granite_rerank (0.703) and bgem3__granite_rerank (0.698), lower for the rest; p95 under load then decides, 4,018.0 against 4,171.1 ms.
- Pick: **gemma2__granite_rerank**. It is also the best stack the provenance preference does not flag, so that preference changed nothing.

Six flagged stacks score above the pick, and the 6 GiB guard held them back from the pick; all six went on to the finals: mdense__mlate 86.6, gemma2__mlate 85.4, arctic__mlate, granite311__mlate and qwen3e__mlate 84.1, bgem3__mlate 82.9.

**Table 1. All 30 stacks on dev, sorted by English top-5.** n = 82 counted questions per form. Status: pass (both limits), flag (passes latency, above the 6,144 MiB guard), miss (over 8 s at p95 and over the guard).

| stack | EN top-5 (95% CI) | EN top-1 | EN MRR | cost proxy, chars | PL translated top-5 | PL as written top-5 | p95 under load, ms | memory under load, MiB | status |
|---|---|---|---|---|---|---|---|---|---|
| mdense__mlate | 86.6 (79.3 to 93.9) | 62.2 | 0.718 | 1,200 | 85.4 | 72.0 | 4,593.8 | 6,921 | flag |
| arctic__q3r | 85.4 (76.8 to 92.7) | 48.8 | 0.627 | 1,294 | 81.7 | 70.7 | 28,338.1, 42 of 42 lost a lane | 16,384 | miss |
| gemma2__mlate | 85.4 (76.8 to 92.7) | 62.2 | 0.717 | 1,221 | 84.1 | 78.0 | 4,513.1 | 7,083 | flag |
| arctic__mlate | 84.1 (75.6 to 91.5) | 63.4 | 0.725 | 1,239 | 84.1 | 72.0 | 4,401.5 | 8,237 | flag |
| granite311__mlate | 84.1 (75.6 to 91.5) | 63.4 | 0.729 | 1,217 | 84.1 | 65.9 | 4,809.8 | 7,603 | flag |
| qwen3e__mlate | 84.1 (75.6 to 91.5) | 58.5 | 0.701 | 1,242 | 82.9 | 73.2 | 4,783.9 | 8,132 | flag |
| arctic__none | 82.9 (74.4 to 90.2) | 51.2 | 0.639 | 1,327 | 80.5 | 63.4 | 661.2 | 3,730 | pass, leader |
| bgem3__mlate | 82.9 (74.4 to 90.2) | 61.0 | 0.711 | 1,260 | 82.9 | 70.7 | 4,432.3 | 8,345 | flag |
| bgem3__q3r | 82.9 (74.4 to 90.2) | 51.2 | 0.633 | 1,349 | 82.9 | 70.7 | 24,963.8, 41 of 42 lost a lane | 13,312 | miss |
| granite311__q3r | 82.9 (74.4 to 90.2) | 47.6 | 0.619 | 1,343 | 85.4 | 65.9 | 28,486.4, 42 of 42 lost a lane | 14,336 | miss |
| mdense__q3r | 82.9 (74.4 to 90.2) | 50.0 | 0.625 | 1,362 | 81.7 | 63.4 | 28,488.6, 42 of 42 lost a lane | 14,336 | miss |
| qwen3e__q3r | 82.9 (74.4 to 90.2) | 47.6 | 0.612 | 1,352 | 80.5 | 69.5 | 28,261.0, 42 of 42 lost a lane | 16,384 | miss |
| arctic__granite_rerank | 81.7 (73.2 to 90.2) | 59.8 | 0.690 | 1,320 | 82.9 | 57.3 | 3,402.5 | 4,800 | pass, tied |
| bgem3__granite_rerank | 81.7 (73.2 to 89.0) | 61.0 | 0.698 | 1,313 | 80.5 | 54.9 | 4,171.1 | 4,897 | pass, tied |
| **gemma2__granite_rerank** | 81.7 (73.2 to 90.2) | 61.0 | 0.703 | 1,294 | 82.9 | 58.5 | 4,018.0 | 4,180 | pass, tied, **pick** |
| gemma2__q3r | 81.7 (73.2 to 90.2) | 47.6 | 0.609 | 1,396 | 82.9 | 72.0 | 28,437.5, 42 of 42 lost a lane | 15,360 | miss |
| qwen3e__granite_rerank | 81.7 (73.2 to 89.0) | 56.1 | 0.681 | 1,307 | 82.9 | 58.5 | 4,047.9 | 4,410 | pass, tied |
| granite311__granite_rerank | 80.5 (72.0 to 89.0) | 57.3 | 0.681 | 1,331 | 80.5 | 61.0 | 4,363.3 | 4,050 | pass |
| mdense__granite_rerank | 80.5 (72.0 to 89.0) | 58.5 | 0.684 | 1,344 | 80.5 | 52.4 | 3,876.2 | 3,776 | pass |
| arctic__msmarco | 79.3 (69.5 to 87.8) | 51.2 | 0.639 | 1,394 | 76.8 | 46.3 | 586.6 | 3,771 | pass |
| bgem3__msmarco | 79.3 (69.5 to 87.8) | 50.0 | 0.623 | 1,406 | 74.4 | 39.0 | 577.5 | 3,861 | pass |
| bgem3__none | 79.3 (70.7 to 87.8) | 52.4 | 0.635 | 1,413 | 75.6 | 68.3 | 787.6 | 2,799 | pass |
| gemma2__none | 79.3 (70.7 to 87.8) | 54.9 | 0.658 | 1,378 | 78.0 | 67.1 | 559.5 | 3,109 | pass |
| granite311__none | 79.3 (70.7 to 87.8) | 56.1 | 0.651 | 1,400 | 74.4 | 53.7 | 556.7 | 1,964 | pass |
| mdense__none | 79.3 (70.7 to 87.8) | 56.1 | 0.670 | 1,368 | 84.1 | 53.7 | 506.4 | 1,709 | pass |
| qwen3e__none | 79.3 (70.7 to 87.8) | 53.7 | 0.642 | 1,397 | 78.0 | 64.6 | 644.4 | 3,723 | pass |
| gemma2__msmarco | 78.0 (68.3 to 86.6) | 53.7 | 0.654 | 1,402 | 75.6 | 39.0 | 548.6 | 3,149 | pass |
| granite311__msmarco | 78.0 (68.3 to 86.6) | 52.4 | 0.644 | 1,402 | 78.0 | 47.6 | 576.9 | 3,028 | pass |
| mdense__msmarco | 78.0 (68.3 to 86.6) | 52.4 | 0.645 | 1,415 | 78.0 | 37.8 | 501.0 | 2,774 | pass |
| qwen3e__msmarco | 76.8 (67.1 to 85.4) | 51.2 | 0.631 | 1,442 | 76.8 | 42.7 | 547.7 | 4,786 | pass |

The tie band holds a difference of one question, and an exact McNemar test at 0.05 needs a gap of at least 6 questions, so among the five tied stacks the fixed tie-breaks decide. Many stacks share a top-5 value; for granite311 against mdense I checked that their ranks differ in 17 to 32 of the 82 rows where the top-5 is equal, and the gains and losses cancel.

**Table 2. Comparison arms on dev, never eligible.** Latency with no load.

| arm | EN top-5 | EN top-1 | EN top-10 | EN MRR | PL translated top-5 | PL as written top-5 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|
| mdense__dense | 76.8 | 46.3 | 80.5 | 0.585 | 74.4 | 59.8 | 23 | 443 |
| R5-like | 75.6 | 51.2 | 84.1 | 0.609 | 68.3 | 32.9 | 545 | 1,749 |
| words_hybrid | 75.6 | 43.9 | 82.9 | 0.558 | 67.1 | 13.4 | 25 | 77 |
| bm25 | 74.4 | 42.7 | 80.5 | 0.560 | 69.5 | 15.9 | 9 | 52 |
| gemma2__dense | 73.2 | 45.1 | 80.5 | 0.576 | 74.4 | 68.3 | 28 | 404 |
| R4-like | 73.2 | 51.2 | 84.1 | 0.610 | 72.0 | 40.2 | 468 | 1,633 |
| arctic__dense | 72.0 | 40.2 | 78.0 | 0.540 | 73.2 | 61.0 | 31 | 529 |
| qwen3e__dense | 72.0 | 43.9 | 74.4 | 0.550 | 70.7 | 62.2 | 43 | 545 |
| R1 | 68.3 | 45.1 | 76.8 | 0.552 | 64.6 | 9.8 | 455 | 1,675 |
| R1p | 68.3 | 46.3 | 76.8 | 0.559 | 64.6 | 8.5 | 457 | 1,671 |
| bgem3__dense | 67.1 | 37.8 | 74.4 | 0.505 | 69.5 | 62.2 | 39 | 604 |
| granite311__dense | 64.6 | 37.8 | 73.2 | 0.486 | 62.2 | 51.2 | 22 | 493 |
| grep | 9.8 | 0.0 | 15.9 | 0.048 | 12.2 | 7.3 | 1,380 | 17,630 |

**The pick against each baseline on English dev**, Holm over 7: grep 59 against 0 (Holm p 0.0000); R1 and R1p 17 against 6 (0.2081); bm25 10 against 4, R4-like 12 against 5, R5-like 10 against 5 and words_hybrid 9 against 4 (each 0.5739). On 82 dev questions the gain over R1 reads 17 against 6; the held-out test, with almost twice the questions, settles it at Holm p 0.0015.

**By question kind**, top-5 of the pick (n per kind):

| form | conflict (13) | current (8) | evidence (17) | history (15) | location (14) | paraphrase (15) |
|---|---|---|---|---|---|---|
| English | 61.5 | 75.0 | 82.4 | 100.0 | 100.0 | 66.7 |
| Polish translated to English | 61.5 | 100.0 | 82.4 | 100.0 | 92.9 | 66.7 |
| Polish as written | 46.2 | 75.0 | 47.1 | 73.3 | 78.6 | 40.0 |

**Supplementary dev questions** (70, gold in PDFs, code or file names; PDF passages had no vectors yet). English top-5: reranked stacks 57.1 to 68.6 (the pick 68.6, mdense__mlate 65.7), stacks with no reranker 32.9 to 40.0, words_hybrid 62.9, bm25 24.3, grep 0.0. Polish: reranked stacks 14.3 to 31.4, words_hybrid 15.7.

**Checks after the run (Amendment E).** At live settings (5 results, 2 passages per note) the full rule gives the same pick. Replays of two stacks: 98 rows each, 0 rank differences. One coarser vector grid step (1/256 instead of 1/512) moved the pick's top-5 by one question. All six mlate stacks stayed above 6,144 MiB at batch 8 as well, so every flag stood. 18 direct tests of the rule on the real code passed.

### 4.2 Held-out test

The finalists were drawn on 9 November before any held-out question was loaded; each part was scored once.

**Table 3. Held-out English and Polish as written**, n = 158 counted questions each, one question 0.63 points.

| arm | EN top-5 (95% CI) | EN top-1 | EN MRR | EN cost proxy, chars | EN p50 ms | PL top-5 (95% CI) | PL top-1 | PL MRR |
|---|---|---|---|---|---|---|---|---|
| gemma2__granite_rerank (dev pick) | 74.7 (67.7 to 81.6) | 50.6 | 0.614 | 1,522 | 1,220 | 55.1 (46.8 to 62.7) | 27.8 | 0.380 |
| mdense__mlate (ships) | 85.4 (79.7 to 90.5) | 53.2 | 0.656 | 1,272 | 1,436 | 69.0 (61.4 to 75.9) | 43.7 | 0.545 |
| arctic__mlate | 81.6 (75.3 to 87.3) | 53.2 | 0.643 | 1,364 | 1,417 | 75.9 (69.0 to 82.3) | 51.3 | 0.622 |
| granite311__mlate | 79.7 (73.4 to 86.1) | 52.5 | 0.640 | 1,389 | 1,432 | 66.5 (58.9 to 73.4) | 42.4 | 0.530 |
| bgem3__mlate | 79.7 (73.4 to 86.1) | 53.2 | 0.639 | 1,397 | 1,474 | 69.6 (62.0 to 76.6) | 44.3 | 0.557 |
| qwen3e__mlate | 79.7 (72.8 to 85.4) | 51.9 | 0.640 | 1,401 | 1,434 | 67.1 (59.5 to 74.7) | 42.4 | 0.534 |
| gemma2__mlate | 79.1 (72.8 to 85.4) | 49.4 | 0.617 | 1,428 | 1,426 | 68.4 (60.8 to 75.3) | 44.3 | 0.543 |
| arctic__none | 73.4 (66.5 to 80.4) | 50.6 | 0.607 | 1,552 | 43 | 67.1 (59.5 to 74.7) | 41.8 | 0.515 |
| words_hybrid | 65.2 (57.6 to 72.8) | 41.1 | 0.515 | 1,774 | 14 | 17.7 (12.0 to 24.1) | 7.0 | 0.106 |
| bm25 | 62.0 (54.4 to 69.6) | 37.3 | 0.480 | 1,877 | 7 | 16.5 (10.8 to 22.2) | 7.0 | 0.108 |
| R1 | 61.4 (53.8 to 69.0) | 29.7 | 0.420 | 1,949 | 437 | 9.5 (5.1 to 14.6) | 3.8 | 0.062 |
| grep | 8.9 (4.4 to 13.3) | 1.3 | 0.058 | 3,278 | 1,275 | 10.8 (6.3 to 15.8) | 0.6 | 0.064 |

**Table 4. Supplementary held-out parts**, gold in PDFs, code or file names, n = 100 each.

| arm | EN top-5 (95% CI) | EN top-1 | EN MRR | PL top-5 (95% CI) | PL top-1 | PL MRR |
|---|---|---|---|---|---|---|
| gemma2__granite_rerank (dev pick) | 72.0 (63.0 to 80.0) | 47.0 | 0.581 | 30.0 (21.0 to 39.0) | 18.0 | 0.226 |
| mdense__mlate | 72.0 (63.0 to 80.0) | 39.0 | 0.535 | 26.0 (18.0 to 35.0) | 20.0 | 0.226 |
| arctic__mlate | 72.0 (63.0 to 80.0) | 38.0 | 0.526 | 29.0 (20.0 to 38.0) | 21.0 | 0.246 |
| gemma2__mlate | 72.0 (63.0 to 80.0) | 42.0 | 0.551 | 29.0 (20.0 to 38.0) | 24.0 | 0.264 |
| bgem3__mlate | 72.0 (63.0 to 80.0) | 41.0 | 0.545 | 29.0 (20.0 to 38.0) | 22.0 | 0.247 |
| granite311__mlate | 71.0 (62.0 to 80.0) | 39.0 | 0.532 | 26.0 (18.0 to 35.0) | 18.0 | 0.217 |
| qwen3e__mlate | 71.0 (62.0 to 80.0) | 42.0 | 0.547 | 26.0 (18.0 to 35.0) | 16.0 | 0.208 |
| arctic__none | 34.0 (25.0 to 44.0) | 23.0 | 0.292 | 27.0 (19.0 to 36.0) | 21.0 | 0.232 |
| words_hybrid | 67.0 (58.0 to 76.0) | 45.0 | 0.539 | 14.0 (8.0 to 21.0) | 7.0 | 0.104 |
| bm25 | 29.0 (20.0 to 38.0) | 23.0 | 0.255 | 9.0 (4.0 to 15.0) | 4.0 | 0.056 |
| grep | 0.0 | 0.0 | 0.001 | 0.0 | 0.0 | 0.001 |

R1 was not run on the supplementary parts.

**Paired tests on held-out English.** Exact McNemar, Holm within each family. Gap: the other arm minus the pick.

| family | compared with the pick | gap, points | pick only / other only | p | Holm p | passes every gate the pick passes, guard 8,192 MiB |
|---|---|---|---|---|---|---|
| finalists | mdense__mlate | +10.8 | 3 / 20 | 0.0005 | 0.0034 | yes |
| finalists | arctic__mlate | +7.0 | 3 / 14 | 0.0127 | 0.0764 | no |
| finalists | bgem3__mlate | +5.1 | 2 / 10 | 0.0386 | 0.1929 | no |
| finalists | granite311__mlate | +5.1 | 6 / 14 | 0.1153 | 0.3850 | yes |
| finalists | qwen3e__mlate | +5.1 | 5 / 13 | 0.0963 | 0.3850 | yes |
| finalists | gemma2__mlate | +4.4 | 4 / 11 | 0.1185 | 0.3850 | yes |
| finalists | arctic__none | -1.3 | 9 / 7 | 0.8036 | 0.8036 | yes |
| baselines | R1 | -13.3 | 29 / 8 | 0.0008 | 0.0015 | |
| baselines | bm25 | -12.7 | 23 / 3 | 0.0001 | 0.0003 | |
| baselines | words_hybrid | -9.5 | 18 / 3 | 0.0015 | 0.0015 | |
| baselines | grep | -65.8 | 106 / 2 | 0.0000 | 0.0000 | |

**The pick against 2.0.** The tournament pick against R1 on held-out English: 74.7% against 61.4%, +13.3 points, 29 questions only the pick finds and 8 only R1 finds, exact McNemar p 0.0008, Holm p 0.0015. The pick also beats bm25, words_hybrid and grep. On dev the same pair read 17 against 6 with Holm p 0.2081, so it took the held-out test to separate it.

**What went live.** One finalist beats the pick by more than 2.0 points at Holm p below 0.05: mdense__mlate, +10.8 points (20 questions only it finds against 3 only the pick finds, Holm p 0.0034). With the finals the memory guard went to 8,192 MiB (Amendment G). mdense__mlate holds 6,921 MiB under load, so it passes every gate the pick passes, and it went live at 85.4%, the best of the eight finalists. With an 8,192 MiB guard from the start the dev rule would also have picked a stack with the mlate reranker (gemma2__mlate). The fresh set of section 4.4 then tested the shipped pair on questions no choice had touched.

**Polish.** On held-out Polish as written the order changes: arctic__mlate leads with 75.9, mdense__mlate reads 69.0, the pick 55.1 and R1 9.5. Unadjusted paired counts: mdense__mlate against arctic__mlate, 5 against 16 discordant questions in Polish (p 0.027) and 8 against 2 in English (p 0.109). Read the same way, the 6,144 MiB guard would have cost about 11 points of English held-out top-5 and about 14 points of Polish.

**From dev to held-out.** The pick fell from 81.7 to 74.7 and R1 from 68.3 to 61.4, almost the same drop; mdense__mlate went from 86.6 to 85.4. The dev set served for tuning and the held-out questions are new, so a drop is expected, and the shipped pair held its level best.

**Supplementary parts.** On the test with gold in PDFs, code or file names every reranked finalist reads 71 to 72% in English, words_hybrid 67%, arctic__none 34%. PDF and figure passages had no vectors for any stack during this run, so these numbers measure the word lanes plus reranking.

### 4.3 Speed and memory

**Table 5. Latency and memory by reranker setting**, ranges over the six embedders. Under load: 42 searches per stack, with four busy loops and one parallel search.

| reranker setting | p50 under load, ms | p95 under load, ms | searches that lost a model lane, of 42 | memory under load, MiB | p50 no load, ms | peak memory no load, MiB |
|---|---|---|---|---|---|---|
| none | 59.2 to 84.2 | 506.4 to 787.6 | 0 | 1,709 to 3,730 | 37 to 58 | 1,708 to 3,731 |
| msmarco | 318.1 to 354.8 | 501.0 to 586.6 | 0 | 2,774 to 4,786 | 185 to 200 | 2,776 to 4,781 |
| granite_rerank | 2,486.0 to 2,729.6 | 3,402.5 to 4,363.3 | 0 | 3,776 to 4,897 | 1,281 to 1,310 | 3,786 to 5,005 |
| mlate | 2,890.0 to 2,997.9 | 4,401.5 to 4,809.8 | 0 | 6,921 to 8,345 | 1,473 to 1,533 | 7,104 to 8,791 |
| q3r | 18,895.9 to 28,167.3 | 24,963.8 to 28,488.6 | 41 to 42 | 13,312 to 16,384 | 11,035 to 29,901 | 14,336 to 16,384 |

**The shipped pair**, mdense__mlate: about 1.5 s per search with nothing else running (p50 1,484 ms, p95 1,877 ms), 4.6 s at p95 under load, about 6.8 GiB under load and 6.9 GiB with nothing else running. In the held-out run its helper peaked at up to 8,325 MiB. The guard is a selection rule; the helper itself runs with no memory cap. At batch 8 instead of 32 the six mlate stacks kept the same top-5 and used 600 to 1,250 MiB less.

**Cold start**, the opening request after a helper start, median of three: arctic 9.1 s, bgem3 9.6, gemma2 9.5, granite311 7.7, granite_rerank 7.4, mdense 21.5, mlate 8.1, msmarco 7.3, q3r 6.9, qwen3e 7.3. A whole cold search with the shipped pair took 20.2 s after the fixes in section 4.13, and the next two 2.6 and 2.8 s.

**Table 6. Embedding cost per embedder**, non-PDF passages of the dev corpus.

| embedder | inputs embedded | hours | inputs per s | peak MiB while embedding | vector folder, GB |
|---|---|---|---|---|---|
| granite311 | 205,997 | 1.87 | 30.6 | 3,098 | 1.89 |
| mdense | 205,997 | 1.99 | 28.8 | 2,910 | 1.89 |
| arctic | 189,341 | 3.47 | 15.1 | 4,777 | 2.35 |
| gemma2 | 205,997 | 3.98 | 14.4 | 6,008 | 1.89 |
| bgem3 | 205,997 | 5.25 | 10.9 | 15,360 | 2.35 |
| qwen3e | 205,997 | 7.37 | 7.8 | 7,242 | 2.35 |

bgem3 ran with 4 inputs per request to keep the machine usable, the others with 16. arctic embedded 189,341 inputs, the others 205,997.

### 4.4 Languages (Amendment L)

Plan: Amendment L, fixed before any of its questions existed (hashes in section 6). It asks two things the earlier sets could not answer: does the shipped pair hold up on questions written after it went live, and do German and Polish questions reach the English level.

**Questions.** 120 fresh English questions on the live corpus. Documents were drawn per library in proportion to its document count with seed 20261110: 80 notes of at least 800 bytes and 40 PDF pages of at least 700 characters. One question per document was written from that library's packet of drawn documents, around a key phrase from the document and without naming the file; a programmatic check kept a question only when its gold sits in that library's index and contains the key. Every question was translated into Polish and German with one instruction, and a second pass that saw only the translations put both back into English. The set was frozen with a hash before the first search. Each part was scored once.

**Arms.** The shipped pair (mdense__mlate, with vectors for notes and PDF pages); the dev pick (gemma2__granite_rerank, run as in the held-out test, with vectors for notes only, so PDF pages reach it through its word lane); words_hybrid (the 3.0 word, code and file-name lanes with no model); and R1, which reads notes only, so its PDF questions are misses by construction. On PDF questions the comparison is between the systems as they run, as the run record fixed before any data. Before the run the pick's vectors came back from the archive and were topped up for the 1,977 note passages added since (about five minutes); index updates were held for the whole run.

**Table 7. Top-5 by question form**, 120 questions per form, Wilson 95% intervals.

| arm | English | German as asked | Polish as asked | German put into English | Polish put into English |
|---|---|---|---|---|---|
| mdense__mlate, shipped | 83.3 (75.7 to 88.9) | 84.2 (76.6 to 89.6) | 70.0 (61.3 to 77.5) | 84.2 (76.6 to 89.6) | 82.5 (74.7 to 88.3) |
| gemma2__granite_rerank, dev pick | 80.0 (72.0 to 86.2) | 58.3 (49.4 to 66.8) | 40.8 (32.5 to 49.8) | 76.7 (68.3 to 83.3) | 76.7 (68.3 to 83.3) |
| words_hybrid, no model | 77.5 (69.2 to 84.1) | 45.0 (36.4 to 53.9) | 19.2 (13.1 to 27.1) | 75.8 (67.4 to 82.6) | 74.2 (65.7 to 81.2) |
| R1, LEKS 2.0 | 46.7 (38.0 to 55.6) | 32.5 (24.8 to 41.3) | 7.5 (4.0 to 13.6) | 46.7 (38.0 to 55.6) | 47.5 (38.8 to 56.4) |

**Table 8. English, by kind of gold**, with top-1, MRR and latency over all 120.

| arm | notes, top-5 (80) | PDF pages, top-5 (40) | top-1 | MRR | p50 ms | p95 ms |
|---|---|---|---|---|---|---|
| mdense__mlate | 77.5 | 95.0 | 60.0 | 0.698 | 1,729 | 2,333 |
| gemma2__granite_rerank | 78.8 | 82.5 | 45.0 | 0.597 | 1,427 | 1,925 |
| words_hybrid | 80.0 | 72.5 | 47.5 | 0.591 | 40 | 86 |
| R1 | 70.0 | 0.0 | 30.8 | 0.371 | 683 | 1,647 |

R1's timings come from my harness, which reaches it on its own path, so they stand apart from the 2.0 release figures.

**Primary comparisons**, English top-5, exact McNemar, Holm over the two:

- Shipped pair against R1: +36.7 points, 46 questions found only by the pair and 2 only by R1, Holm p < 0.0001. The plan expected more than 15 points.
- Shipped pair against the dev pick: +3.3 points, 9 against 5 (Holm p 0.42), inside the 5-point band the plan expected. On the note questions alone, a descriptive view fixed before the data, the two sit 1.2 points apart (3 against 4).

**Where the gap comes from.** On the 80 note questions the shipped pair leads R1 by 7.5 points (62 against 56 found, 8 against 2 discordant), and on the 40 PDF questions by 95.0 points (38 against 0), because R1 reads no PDFs. On English notes alone the larger held-out set (section 4.2, 158 questions) carries the comparison with 2.0: +24.0 points for the shipped pair and +13.3 for the pick.

**Languages.** Put into English first, German and Polish questions reach the English level with the shipped pair: 84.2% and 82.5% against 83.3%, within the plan's 5 points and 1.7 points apart. Asked as written, German stays at 84.2% and Polish reaches 70.0%. The plan expected both raw forms 10 to 20 points behind English; German beat that expectation and Polish met it. Against R1 every form gains a lot: Polish as asked 70.0 against 7.5% (77 questions against 2), German as asked 84.2 against 32.5% (66 against 4). My notes are in English, so for this record putting a Polish question into English before the search is worth 12.5 points; the template's skill tells the agent to search in the language of the notes.

**PDF pages**, secondary: the shipped pair against the 3.0 word lanes on the 40 PDF questions, 95.0 against 72.5% (10 against 1, exact p 0.0117). The plan expected at least 10 points.

**Reading.** In English the shipped pair leads the pick by 3.3 points on these fresh questions, inside the band the plan expected. Its clearest gains over the pick are PDF pages (95.0 against 82.5%, where only it has vectors) and German and Polish asked as written (84.2 against 58.3% and 70.0 against 40.8%).

### 4.5 Polish passages as gold

The notes are over 99% English, so a Polish note is hard to reach from an English word query. 28 questions, each asked in English and in Polish, each with a Polish passage as gold. "Two tries" is the English query and then the Polish one on a miss, as the tool text tells the agent to do.

| query | passage in top 5, words only | passage in top 5, shipped pair | document in top 5, words only | document in top 5, shipped pair |
|---|---|---|---|---|
| English alone | 5 | 9 | 12 | 21 |
| Polish alone | 15 | 20 | 23 | 26 |
| two tries | 15 | 22 | 24 | 27 |

The index grew between the two runs, so the gain combines the meaning lane and the new passages.

### 4.6 Newer and older sources

A source-hierarchy case pairs a stronger and a weaker source on the same point: 29 frozen cases, 24 for search and 5 about remembered decisions.

| measure | words only | shipped pair |
|---|---|---|
| stronger source ranked above the weaker, of 24 | 13 | 13 |
| stronger source not in the top 40, of 24 | 5 | 3 |
| weaker source marked history or absent, of 24 | 24 | 24 |
| remembered decisions: `tried` shows the stronger document, of 5 | 0 | 0 |
| remembered decisions: `search` has it in the top 10, of 5 | 1 | 2 |

The meaning lane brings the stronger note into reach more often. The order between versions stays where the source-class priors put it, and every weaker source is marked as history or absent.

### 4.7 Was this tried before

`tried` takes a proposal and says whether the idea was tried or rejected before, with the deciding note. A false alarm reports a match for an idea never tried.

| run | split | deciding note found | some row returned | false alarms |
|---|---|---|---|---|
| words only, thresholds tuned on dev | dev | 13 of 15 | not recorded | 0 of 9 |
| shipped pair, gate 0.65 | dev | 11 of 15 | 14 of 15 | 0 of 9 |
| shipped pair, gate 0.55, tuned on dev | dev | 13 of 15 | 15 of 15 | 0 of 9 |
| shipped pair, gate 0.55, scored once | held-out | 21 of 33 | 31 of 33 | 6 of 16 |

On the held-out proposals the 0.55 gate raised a false alarm for 6 of 16 ideas never tried. A false "already tried" hurts more than a miss, so the shipped gate is 0.65, with no false alarm on dev. When nothing passes the gate, `tried` shows up to three closest rows labelled "closest, not a match"; in a replay of scripted sessions this found the deciding note for 6 of 7 rejected ideas, against 1 of 7 before.

### 4.8 Isolation between libraries, in my set-up

A canary is a rare word from a document that only library B holds, searched from library A; the same word searched in B is the control. On the shipped pair's stores: 640 canary searches and 474 canaries in past-session search, 0 hits, with 206 of 208 and 79 of 79 controls found. Every sandbox read of another library's store, cache, transcript or memory was refused (818 of 818). At store level 34 rows crossed, none served by search: 21 folder names in the file-name lane and 13 stale vectors. Both were fixed, and a rerun after a full rebuild read 0 crossed. The canaries ran on the word lanes by design, the meaning lane was checked at the store, and the reranker only reorders the passages a library's own lanes return. One model helper serves every library, and its replies are numbers only.

### 4.9 End to end (F5)

The main run put all four arms on one model at medium effort, 140 sessions, and the sweep added 128. Every session passed the validity checks. The CLI's per-model usage lists a single model in each of the 268 sessions, so no second model, such as a smaller helper for reading or search, took part.

**Table 9. F5 main run**, 35 questions per arm, means per question; one question is 2.9 points of the correct rate. USD: the CLI's own cost figure at API prices; cold: the same sessions priced as if no earlier session had warmed the prompt cache. Prompt tokens: the whole context sent.

| arm | correct | USD per question | USD per question, cold | prompt tokens | output tokens | wall time, s | turns | USD per correct answer (95% CI) | cold USD per correct answer (95% CI) |
|---|---|---|---|---|---|---|---|---|---|
| `live` | 0.91 (32 of 35) | 0.0538 | 0.0772 | 36,998 | 427 | 9.7 | 2.6 | 0.0589 (0.0516 to 0.0688) | 0.0844 (0.0755 to 0.0972) |
| `noreranker` | 0.83 (29 of 35) | 0.0555 | 0.0789 | 38,790 | 435 | 8.0 | 2.7 | 0.0670 (0.0567 to 0.0820) | 0.0952 (0.0820 to 0.1153) |
| `words` | 0.83 (29 of 35) | 0.0554 | 0.0787 | 39,613 | 448 | 8.2 | 2.8 | 0.0668 (0.0557 to 0.0836) | 0.0950 (0.0808 to 0.1172) |
| `plain` | 0.80 (28 of 35) | 0.0650 | 0.0827 | 51,711 | 857 | 11.7 | 3.7 | 0.0813 (0.0659 to 0.1056) | 0.1034 (0.0855 to 0.1312) |

Whether the answer cites the gold note, which needs no judge: 0.79, 0.76, 0.76 and 0.72 in the same order. Of the net 4 questions between `live` and `plain`, 3 come from the 4 current-state questions: `live` answered all 4 right, `plain` 1.

**Table 10. Paired against `plain`**, mean per-question difference (arm minus `plain`) with its 95% bootstrap interval. Ratio: USD per correct answer of the arm over that of `plain`.

| arm | USD per question | USD per question, cold | prompt tokens | wall time, s | turns | correct | ratio of USD per correct answer |
|---|---|---|---|---|---|---|---|
| `live` | -0.0112 (-0.0187 to -0.0055) | -0.0056 (-0.0130 to 0.0002) | -14,713 (-24,721 to -6,810) | -2.0 (-4.0 to -0.4) | -1.1 (-1.8 to -0.6) | +0.11 (0.00 to 0.26) | 0.72 (0.56 to 0.88) |
| `noreranker` | -0.0095 (-0.0172 to -0.0032) | -0.0038 (-0.0115 to 0.0024) | -12,922 (-23,068 to -4,893) | -3.7 (-5.7 to -2.0) | -1.0 (-1.7 to -0.5) | +0.03 (-0.11 to 0.17) | 0.82 (0.63 to 1.03) |
| `words` | -0.0097 (-0.0168 to -0.0038) | -0.0040 (-0.0111 to 0.0019) | -12,099 (-21,776 to -3,839) | -3.5 (-5.7 to -1.8) | -0.9 (-1.5 to -0.4) | +0.03 (-0.11 to 0.17) | 0.82 (0.64 to 1.05) |

Every search arm cut the bill per question and the prompt, with every interval below zero. Re-priced cold, the saving per question is smaller and its intervals reach zero. Per correct answer, `live` is the only arm whose ratio interval lies wholly below 1. `live` and `noreranker` share the embedder and the vectors and differ only in the reranker. In this pass `live` answered a net 3 more questions right than either other search arm, and a second blind pass over the same answers (section 4.14) graded `live` and `noreranker` 31 each. Any search arm brings the token saving, and the reranker's gain shows in the retrieval scores (section 4.1).

**Table 11. Sweep, `live` against `plain`**, 8 questions per setting, one question 12.5 points. Ratio: USD per correct answer of `live` over `plain`, paired over the 8 questions.

| model | effort | correct of 8, `live` / `plain` | USD per question, `live` / `plain` | ratio (95% CI) | output tokens, `live` / `plain` | wall time, s, `live` / `plain` |
|---|---|---|---|---|---|---|
| sonnet | low | 8 / 6 | 0.0525 / 0.0674 | 0.58 (0.28 to 0.89) | 372 / 918 | 9.5 / 13.2 |
| sonnet | medium | 8 / 5 | 0.0529 / 0.0737 | 0.45 (0.16 to 0.80) | 406 / 1,076 | 9.6 / 13.1 |
| sonnet | high | 7 / 7 | 0.0597 / 0.0801 | 0.75 (0.59 to 0.90) | 490 / 1,270 | 10.4 / 12.9 |
| sonnet | xhigh | 8 / 6 | 0.0664 / 0.1004 | 0.50 (0.27 to 0.73) | 616 / 1,843 | 11.4 / 16.9 |
| sonnet | max | 7 / 7 | 0.1704 / 0.1995 | 0.85 (0.62 to 1.15) | 8,145 / 9,156 | 65.9 / 67.5 |
| opus | low | 8 / 6 | 0.1173 / 0.1335 | 0.66 (0.33 to 0.94) | 350 / 740 | 7.7 / 10.1 |
| opus | medium | 8 / 8 | 0.1159 / 0.1514 | 0.77 (0.60 to 0.92) | 519 / 1,039 | 9.4 / 13.0 |
| opus | high | 8 / 7 | 0.1223 / 0.1398 | 0.77 (0.49 to 1.05) | 562 / 869 | 10.2 / 11.2 |
| opus | max | 8 / 8 | 0.2953 / 0.3463 | 0.85 (0.70 to 1.03) | 5,744 / 7,295 | 56.3 / 66.6 |

`live` paid less per correct answer than `plain` in every setting, with the interval wholly below 1 in six. Max effort cost 2.3 to 3.2 times as much per question as medium and gave `live` no more correct answers. Five of the eight questions have gold that every arm can reach through its start routine, so the sweep shows differences in cost and only coarse differences in correctness.

### 4.10 Long sessions and development tasks (Amendment I)

**Long mixed session.** Two frozen scripts, one per library, 26 turns each (facts, locations, code, plans, a review, proposals tried or not tried before, my own earlier words, turns that need no tool) with one forced compaction. Search on and off, one session per arm, graded blind.

| | search on | search off |
|---|---|---|
| right / partly / wrong, of 52 | 49 / 2 / 1 | 39 / 8 / 5 |
| USD | 2.93 | 3.38 |
| wall time, minutes | 18.4 | 20.8 |
| "was this tried before", 6 turns, right | 5 | 1 |

By the rule fixed before the data, the 10 extra right answers are beyond noise, while cost (13% lower) and time (12% lower) count as about equal.

**Development tasks.** Ten tasks in scratch clones (fix, test, trace, plan, and two that write a new module), each check shown to fail on the base and pass on the reference before any run. Search on passed 9 of 10 and search off 7 of 10. The two tasks that differ are plans for a proposal an earlier decision had rejected: with search the agent found the decision. With search on, the ten tasks took 1.89 USD and 543 s, against 1.59 USD and 446 s without it.

### 4.11 Three working days (Amendment J)

Two scenarios of three working days each, questions and development tasks mixed in one conversation, each day resumed from the day before after at least 65 minutes, so every morning starts with a cold prompt cache. Ten sessions: search on and off twice per library, and one session per library with a one-line prompt nudge.

| | search on | search off | nudge (2 sessions) |
|---|---|---|---|
| question turns right, blind judge | 89 of 100 | 75 of 100 | 43 of 50 |
| partly right | 7 | 15 | 4 |
| development tasks passed | 14 of 14 | 12 of 14 | 7 of 7 |
| USD, three days | 10.49 | 12.33 | 5.89 |
| wall time, minutes | 37.3 | 43.6 | 20.8 |

Both primaries hold by the rules fixed before the data. Correctness: on 100 paired question turns, search on alone was right 18 times and search off alone 4 times (exact McNemar, Holm p 0.0043). Cost: 0.851 of the search-off sum over 114 paired turns (95% CI 0.803 to 0.896); wall time 0.858 (0.794 to 0.926). Counting partly right as right, it is 8 against 2 (p 0.11): much of the gain is a full answer where the other arm gave a partial one. The search-off sessions read more files, so their context grew faster and each later turn and each cold morning cost more. 54 of 55 search calls ran both models; replaying them through words only changed the top result in 27.

### 4.12 When the agent leaves search alone (Amendment K)

Nothing searches by itself: the models run only inside the search and prior-work tools, and only the agent decides to call them. 44 frozen items, one fresh session per item and arm.

| | search on | search off |
|---|---|---|
| a search on general knowledge and generic code | 0 of 16, second run 0 of 16 | |
| cost on those items, ratio | 0.999 (0.93 to 1.07) | |
| a search on field questions with no project cue | 2 of 6 | |
| "no search" from me obeyed | 4 of 4 | 4 of 4 |
| project questions searched | 5 of 6 | 0 of 6 |
| project questions right, full answer | 6 of 6 | 3 of 6 |
| right on general and field questions | 26 of 26 | 25 of 26 |

### 4.13 The first search after a pause

After 35 to 41 idle minutes, a search-sized rerank took 13 to 21 s and right after it 0.66 s: the operating system pages out the idle helper's model memory. With an 8 s budget such a search went on without the reranker. Since 3.0.0 the first rerank of a session, and the first after 10 quiet minutes, may wait up to 30 s; after it the 8 s budget holds again. A warm-up of about 20 s is acceptable to me, a search without the reranker is not.

### 4.14 A small model as the agent (Amendment M)

Some hosts hand the reading and searching to a smaller model than the one picked in the chat; GitHub Copilot's Explore subagent does so by default ([hosts.md](hosts.md)). I ran the nearest case I can measure: the F5 harness with the small model (alias haiku) as the only model at medium effort, arms `live` and `plain`, the 35 questions of the main run, 70 sessions, all valid. One blind judge call per question graded the two new arms together with the four arms of the main run, so every comparison below comes from one pass.

**Table 12. Small and main model, one judge pass**, 35 questions per arm, means per question. Partly right counts as half in the third column; gold cited needs no judge. LEKS calls: calls to any LEKS tool per session.

| model | arm | correct | partly as half | gold cited | prompt tokens | wall time, s | LEKS calls | USD per correct answer (95% CI) | cold USD per correct answer (95% CI) |
|---|---|---|---|---|---|---|---|---|---|
| haiku | `live` | 0.66 (23 of 35) | 0.77 | 0.62 | 79,411 | 16.8 | 2.5 | 0.0531 (0.0402 to 0.0722) | 0.0847 (0.0677 to 0.1121) |
| haiku | `plain` | 0.49 (17 of 35) | 0.60 | 0.38 | 132,834 | 23.1 | 0 | 0.1093 (0.0700 to 0.1838) | 0.1483 (0.1012 to 0.2391) |
| sonnet | `live` | 0.89 (31 of 35) | 0.91 | 0.79 | 36,998 | 9.7 | 1.3 | 0.0608 (0.0526 to 0.0723) | 0.0871 (0.0767 to 0.1024) |
| sonnet | `plain` | 0.80 (28 of 35) | 0.86 | 0.72 | 51,711 | 11.7 | 0 | 0.0813 (0.0663 to 0.1050) | 0.1034 (0.0857 to 0.1315) |

**Primary reading**, fixed before the data: haiku `live` against haiku `plain`, 23 against 17 right, a gap of 17 points (95% CI 0 to 34). Nine questions only `live` got right and three only `plain` did (exact McNemar p 0.15). The gap is above the 10-point line the plan set for a clear difference. With search the small model paid 0.49 of its own cost per correct answer without search (95% CI 0.27 to 0.81), sent 53,423 fewer prompt tokens per question (95% CI 669 to 118,570) and finished 6.2 s sooner (0.9 to 11.8).

**Secondary readings.** The small model stayed below the main model in both arms: 23 against 31 right with search (8 questions only the main model got right, none the other way, p 0.008) and 17 against 28 without (11 against 0, p 0.001). Search won back 6 of the 14 questions between the small model without search and the main model with it. The small model lost most on current-state questions (1 of 4 with search, the main model 4 of 4) and gained most from search on history questions (4 of 6 against 1 of 6). It called LEKS 2.5 times per session against the main model's 1.3 and sent about twice the prompt, so per correct answer it cost about what the main model cost with search: 0.0531 against 0.0608 USD, and 0.0847 against 0.0871 cold.

**My expectations**, written before the data: a gain of 15 points or more from search, met (17 points); a correct rate of 0.75 to 0.90 with search, which came to 0.66; about as many LEKS calls as the main model and a third of its cost per correct answer, which came to 2.5 calls and 0.87 of its cost.

**The judge.** The joint pass graded the 140 answers of the main run a second time, blind. It agreed with the first pass on correct or not for 131 of 140 and on the three-level grade for 130. It moved `live` from 32 to 31 and `noreranker` from 29 to 31; `words` and `plain` stayed at 29 and 28.

**Scope of this test.** It is Claude Code with the small model as the only model. Copilot's own pattern, where a main model hands a search to Explore and reads its summary, is a separate set-up ([hosts.md](hosts.md)).

### 4.15 LEKS beside a tool server

LEKS only searches, so in use it sits next to MCP servers that act in a tool. This test asks whether an agent takes the current decision from the notes through LEKS and carries it out in another tool through that tool's server. Five short notes describe a display pedestal: the current decision D-07, the superseded D-03, a lighting decision, a state note and a dropped sketch. Each session was Claude Code (alias sonnet, medium effort) with a prompt that names no note and with two servers only: LEKS on words only, and either the Blender MCP server 1.0.0 with Blender 5.2.2 or MCP for Unity 10.3.0 with Unity 6000.3.19f1. After the session a script the agent never saw opened the saved file in a separate run of the tool and checked it against D-07, with 10 checks in Blender and 12 in Unity. I ran ten sessions per tool. The set-up, the checks and what the agents did are in [Integrations](integrations.md).

**Table 13. Tool-server sessions**, ten per tool. USD: the CLI's own cost figure at API prices.

| tool | passed every check (Wilson 95% CI) | median wall time, s | median turns | median USD | LEKS calls, median | tool-server calls, median | first search ranked D-03 above D-07 |
|---|---|---|---|---|---|---|---|
| Blender | 10 of 10 (0.72 to 1.00) | 17.6 | 6 | 0.069 | 3 | 1 | 10 of 10 |
| Unity | 10 of 10 (0.72 to 1.00) | 24.4 | 13 | 0.115 | 4 | 7.5 | 10 of 10 |

Every session searched once and read D-07, and every final answer named D-07 and called D-03 superseded. These notes give their status in a plain line outside the front matter LEKS reads, so both decisions showed status `unknown`, and the agents went by the status lines in the snippets and by the state note, which every first search returned. With the status in front matter, the same searches put D-07 above D-03. The Unity sessions took two routes: one call that ran C# in the editor (4 sessions, median 15.6 s) or seven to eight calls of the package's separate tools (6 sessions, 30.2 s), each of which recovered from a failed first attempt to create the material. The test shows the hand-over between two servers on one small project.

## 5 Scope

- Measured on macOS with Apple silicon and 18 GB of memory, on a private record. The test suite runs on macOS and Linux in CI, on Python 3.10 and 3.13.
- R1's numbers here stand apart from 2.0's published ones (section 2.4): the questions, the record and the scoring differ, so every comparison in this report runs both engines side by side.
- PDF retrieval by meaning is scored in the L test (40 questions, 95.0% top-5). In the tournament and the held-out run PDF passages went through the word lanes and the reranker.
- The memory budget is a selection rule set for the measured machine; the helper wants about 6.8 GiB under load.
- LEKS returns its best passages, and deciding that the notes hold no answer stays with the agent.
- Costs come from the CLI's own report at API list prices, with prompt caching as the host applies it; the cold column prices the same sessions with no cache reuse between them. Each question ran once per arm, graded by a blind judge, and a second blind pass agreed on 131 of 140 grades (section 4.14).
- The isolation results describe my set-up with a sandbox per library. This repository keeps one library per profile and sandboxes the model helper.
- The tool-server test (section 4.15) is one small project with one task per tool, ten sessions per tool, and checks the hand-over and the saved file.

## 6 Reproducibility

The record, its questions and the per-question rows hold private text and stay private. Anyone can check the code, run `bench/` on its synthetic corpus (54 files, 40 questions in seven kinds) or on their own notes, and compare any file I share later against the hashes below. Before the tournament a larger synthetic corpus of fictional text (656 files, 342 questions in English and Polish) served as a pipeline check; its scores chose nothing.

| item | sha256 |
|---|---|
| plan as the arms ran (after Amendment A) | `8be5fb24d998bd46b2388bac54be052cefe0cd3207c4b7ec6aa41f4dfe7b2926` |
| final plan with Amendments A to E, which the choice follows | `f28df2bfa05c886bf3337b6f7d1ae9d72f2df9c8d6d1c84aff29c38328cbbd57` |
| Amendment F | `11f067504e23950e1decc4ec0eb4f0eb4263a15cba8650d51a0910c5519006ff` |
| inputs of the finalist draw: load rows, arm rows | `22bec06fae4926b661c083a861bbd198a8fdaf06d598a4d65f18e490ac783bd1`, `a00236529f4e9b4c871e4f3e1c984b6555fa0a392ea2add6cb98858cb970ea20` |
| dev results note | `3e8f706bb31cdb82c2dee7795dccdd0ab5610682d579c2d521ff63a95eeb0e71` |
| translated Polish, dev | `af435a1673e0c6594e24baa26232bffcdb01211b0df4315e0b7beb7ae55eb300` |
| Amendment L | `8fa611bea6aca911753744f2636487efae276c41a29520b31de7608b9a8aa3cd` |
| Amendment L run record, written before any L question existed | `8d9c016ba4e3094a314613f170a98b888c46929babb19096b2060418598e7a9f` |
| Amendment L questions, frozen before the first search | `f3b5dea670cf4f856b2a6fde7a4d252955fe37d344f314eab9ab0a18fc0df684` |
| Amendment M | `c82a97bde1f0117f0aac04f78d24710e91889ab74c9c2da250eda3fed3ae727e` |

Bootstrap seed 20261106 for the retrieval tests; 5,000 resamples with seed 7 for F5.

The tool-server test of section 4.15 can be rerun as a whole. [bench/integrations](../bench/integrations) holds its notes, check scripts and runners and a summary of all twenty sessions; it needs the Claude Code CLI and Blender or Unity with its MCP server.

## 7 Conclusion

A tournament fixed in advance on my own private record chose the models of LEKS 3.0. Inside the 6 GiB budget the fixed rule picked gemma2 with granite_rerank, and on 158 held-out English questions it beat R1, the default of LEKS 2.0, by 13.3 points of top-5 (74.7 against 61.4%, Holm p 0.0015). In the finals mDenseOn with mLateOn reached 85.4%, 10.8 points above the pick (Holm p 0.0034); with the budget moved to 8 GiB it fits at about 6.8 GiB under load, and it ships. On 120 fresh questions written after it went live it beat R1 by 36.7 points of top-5 (83.3 against 46.7%, Holm p < 0.0001), read PDF pages at 95.0%, and answered German and Polish questions at 84.2 and 70.0% as asked and at the English level once put into English. Polish questions gain the most from a reranker: on the held-out set R1 finds 9.5%, the finalists 55.1 to 75.9%. End to end, the live search cut the agent's cost per correct answer to 0.72 of the bill without search tools (95% CI 0.56 to 0.88), and over three working days the agent with search answered 89 of 100 question turns right against 75 without it, at 0.85 of the cost. With a small model as the only agent, search lifted it from 17 to 23 of 35 right at half its cost per correct answer. Next to the Blender and Unity MCP servers, an agent took the current decision from the notes through LEKS and built it in the tool in all 20 sessions.
