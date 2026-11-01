# Benchmarks and methodology

This page describes how every number in the README was measured. Three kinds of measurement are kept apart:
- the [1.0 benchmark](#benchmark-of-version-10), the retrieval benchmark of version 1.0;
- the [frozen benchmark](#frozen-benchmark), built for 2.0;
- the [performance measurements](#performance-measurements).

The benchmark questions and answers are project content and are not published. Only aggregate results are.

## Terms

| Term | Meaning |
|---|---|
| 1.0, 2.0 | Versions of LEKS. 1.0 was published first, under the title "Engineering knowledge base", and 2.0 is the current version. |
| R0, R1, R4, R5 | Search engines (see the [README](../README.md#search-engines-r0-r1-r4-r5)). R0 is the engine of 1.0, R1 the deployed engine of 2.0, R4 and R5 experiments. R2 is the embedding model on its own, one half of R4. |
| Top 1, top 5, top 10 | Share of answerable questions whose relevant page (the gold page or an accepted alternative) is ranked first, or appears within the first 5 or 10 results. In the literature this is called Recall@k. |
| Complete answer pack | Every answer key of the question appears in the pack's page passages, in the best page's title or summary, or in the claims the pack includes. This definition is used for the frozen benchmark; the 1.0 benchmark used the harness of 1.0. |
| Estimated tokens | The characters of everything returned, divided by 4. The answer pack's 1,800-token budget (7,200 characters) applies to page passages only. |
| Unanswerable recognised | The engine indicates that the knowledge base has no answer. |
| MRR, nDCG@10 | Standard ranking measures, reported as secondary results |
| p95 | 95th percentile of the latency of individual calls |

## Benchmark of version 1.0

- **Questions:** 177, written from the pages by six independent agents without access to the search code. Answer keys were copied verbatim from their pages and checked mechanically.
- **Freezing:** the set was frozen by SHA-256 before the first run. A separate 176-question set was used for tuning.
- **Engines compared:** a word-count baseline, the earlier engine that R0 replaced, and R0. Paired exact McNemar tests were used.

| Engine | Top 1 | Top 5 |
|---|---:|---:|
| Word-count baseline | 20.3 % | 62.7 % |
| Earlier engine, replaced by R0 | 49.2 % | 70.1 % |
| R0 (1.0) | 52.0 % | 75.7 % |

| Answer pack of R0 | Complete answer pack |
|---|---:|
| Best page only (the earlier pack) | 57.1 % |
| Four best pages (the 1.0 pack) | **70.6 %** (Wilson 95 % interval 63.5 to 76.8 %) |

- **Pack:** the four-page pack gained 24 questions and lost none (p < 0.0001). A re-run after later documentation changes gave 69.5 %.
- **Ranking:** the gain over the earlier engine was not significant (top 1: p = 0.40; top 5: p = 0.064).
- **Paraphrases:** paraphrased questions were the weak spot, with 47.7 % complete packs against 83.9 % for naturally phrased ones.

**These 177 questions with 2.0:**
- **Rankings:** R1 returns R0's rankings on all 177 questions. On the knowledge base as it stood for 2.0, R1 ranks the relevant page first for 51.4 % and within the top 5 for 75.7 %, the same figures as a later re-run of 1.0.
- **Pack coverage:** not re-measured for 2.0, so the 70.6 % remains a 1.0 result.
- **"1,800-token pack":** The 1.0 documents use this phrase for the passage budget. A whole pack was about 2,800 estimated tokens.

The chart in the [archived README of 1.0](history/README-1.0.md) labels R0 as "Current engine".

## Frozen benchmark

### Protocol

- **Before any implementation:** the evaluation protocol was frozen by SHA-256. Later changes were recorded as numbered amendments, each before the result it could affect.
- **Questions:**
  - 440 candidates were written by eight separate agent sessions.
  - They were validated mechanically: answer keys occur verbatim in their pages, and every unanswerable question states why.
  - Near-duplicates were removed.
  - A fixed seed split them into a development set of 119, used for every design choice, and the frozen benchmark of 320.
- **The frozen benchmark's mix:** 150 direct, 80 paraphrased and 50 multi-source questions, plus 40 with no answer in the knowledge base.
- **Before the freeze:** the candidates were only used for the check that R0 and R1 rank identically, which compares the two engines with each other and uses no answers, keys or gold pages.
- **One run:** the frozen benchmark was evaluated once, at a recorded commit. The analysis script had been frozen and rehearsed on the development set beforehand. It is not used for further tuning.
- **Statistics:**
  - Wilson intervals for rates.
  - Paired exact McNemar tests with Holm correction within each family of comparisons.
  - Cluster-bootstrap 95 % intervals with 10,000 resamples, clustered by gold page.
  - Wording fixed in advance: "significant" only at Holm p < 0.05; otherwise "not established", never "equal".
- **Independent recomputation:** an independent reviewer recomputed every statistic from the saved per-question outcomes.

### Results (280 answerable questions, 40 unanswerable)

| Measure | R0 | R1 (deployed) | R4 (experimental) | R4 minus R1 |
|---|---:|---:|---:|---|
| Top 1 | 59.3 % | 59.3 % | 62.1 % | +2.9 [-1.7, 7.5]; Holm p = 0.70; not established |
| **Top 5** | 79.3 % | 79.3 % | 86.4 % | **+7.1 [4.0, 10.5]; 24 better, 4 worse; Holm p = 0.0007; significant** |
| Complete answer pack | 62.1 % | 62.1 % | 65.7 % | +3.6 [-0.3, 7.7]; Holm p = 0.30; not established |
| Unanswerable recognised (of 40) | 0 | 0 | 0 | none |
| Answer pack size, median (estimated tokens) | 2,857 | 2,872 | 2,868 | size only; see below |
| Top 10 | 86.1 % | 86.1 % | 91.4 % | secondary |
| MRR | 0.683 | 0.683 | 0.726 | +0.043 [0.014, 0.074], secondary |
| nDCG@10 | 0.654 | 0.654 | 0.719 | secondary |
| Archived page ranked first where the current one was asked for (of 80 eligible) | 4 | 4 | 3 | secondary |

- **R0 and R1 are identical on every item.**
  - No ranking outcome differs on the 280 answerable questions.
  - No answer-pack outcome (complete, partial, abstention) differs on any of the 320 items.
  - R1's packs were 15.5 estimated tokens longer on every item: a fixed-size field naming the engine and the passage mode.
- **The 2.0 pack.** After this run, version 2.0 added a fixed note that the passages are the closest matches, not a confirmed answer: about 45 estimated tokens more (median). Re-running the development evaluation with it changed no ranking, coverage or verdict, only pack sizes.
- **By question type,** top 5 for R4 against R1:
  - paraphrased: 63.8 % against 43.8 % (17 better, 1 worse);
  - direct: 94.7 % against 93.3 % (5 better, 3 worse);
  - multi-source: 2 better, 0 worse.
- **Sensitivity:** without the 3 near-duplicates of older regression questions (planned in advance), R4's top-5 gain is +7.2 [4.1, 10.6]. The reviewer's stricter check, also without 4 near-restatements of development questions, gives +7.3 [4.1, 10.6].
- **Why R1 stays the default:**
  - **Regression test:** the routing regression test has 108 hand-written cases, which R1's ranking rules were tuned on. R1 passes all of them; R4 passes 104.
  - **Memory:** with the MCP server, R4 needs 316 to 319 MB, against about 51 MB for R1.
  - **Footprint:** R4 adds dependencies, a model and the model helper process.

### Development-set results (not in the frozen benchmark)

These results come from the development set of 119 questions (104 answerable), which was used to make design choices. They are therefore optimistic and are not comparable with the frozen benchmark.

| Engine | Top 1 | Top 5 | Complete answer pack | Search p95 |
|---|---:|---:|---:|---:|
| R1 | 60.6 % | 77.9 % | 57.7 % | 2.0 ms |
| R2 (embedding model on its own) | 57.7 % | 84.6 % | not measured | not measured |
| R4 | 64.4 % | 85.6 % | 60.6 % | 14.4 ms |
| R5 | 68.3 % | 90.4 % | 61.5 % | 518 to 989 ms |

- **R5:** never run on the frozen benchmark, and not deployed.
- **Unanswerable questions:** a local question-answering model was tested as a signal for them. At no more than 5 % wrongly refused answerable questions, it caught 13.3 % of the unanswerable ones. It is off.

## Why the two benchmarks cannot be compared

| | 1.0 benchmark | Frozen benchmark |
|---|---|---|
| Questions | 177, six authors | 320, eight authors (280 answerable) |
| Mix | answerable; natural and paraphrased | 150 direct, 80 paraphrased, 50 multi-source, 40 unanswerable |
| Answer keys | fragments chosen by that set's authors | fragments chosen by this set's authors; a multi-source question needs all of its keys |
| Knowledge base | as it stood for 1.0 | as it stood for 2.0 |

R0 and R1 rank identically on both. On the frozen benchmark, the pack code of 1.0 also reaches 62.1 % complete packs, so the lower figure (against 70.6 % on the 1.0 benchmark) comes from the different questions, not from the engine. Neither benchmark measures answer correctness or user satisfaction end to end.

## Performance measurements

All performance figures were measured on one Mac, with Python 3.9.6 and SQLite 3.51.0.

Unless stated otherwise, latency is warm (after one discarded run) and memory is the peak or steady resident set size.

### The performance harness

The README's comparison of 1.0 and 2.0 comes from one harness, run on the same 217 benchmark questions against the same snapshot of the knowledge base (91 pages, 2,850 source files). Each side is the code of one commit:
- the code of 1.0, with R0;
- the release code of 2.0, with R1.

| Measure | 1.0 | 2.0 |
|---|---:|---:|
| Page search p50 / p95 / p99 | 181 / 351 / 362 ms | 1.8 / 2.0 / 2.1 ms |
| Answer pack p50 / p95 / p99 | 211 / 379 / 409 ms | 31.5 / 37.0 / 40.2 ms |
| Answer pack through MCP (120 calls) p95 | 371 ms | 36.7 ms |
| Answer pack in a new process (10 runs) p50 | 297 ms | 102 ms |
| Full integrity check | 8.18 s, 1,602 MB | 7.68 s, 82 MB |
| Fast integrity check | 1.12 s, 50 MB | 1.45 s, 57 MB |
| Build | 2.13 s, 619 MB | 1.66 s, 617 MB |
| MCP server, steady | 42.2 MB | 50.7 MB |
| Answer pack size, median | 2,802 tokens | 2,863 tokens |
| Session-start context | 2,509 tokens | 2,509 tokens |

Token counts are estimates (characters divided by 4). The pack grew by about 60 tokens: a field naming the engine (about 15) and the fixed note that the passages are not a confirmed answer (about 45).

### Other measurements, and why the numbers differ

| Figure | Run | Conditions | Use |
|---|---|---|---|
| R1 search p95 2.16 ms | the performance harness at the first R1 commit | 217 questions | superseded by the run on the final release code (2.0 ms); the cause of the difference was not isolated |
| R0 173 ms, R1 2.33 ms (p95) | independent reviewer | both engines in one process, 20 routing questions | cross-check in the README; a different question set, so a different factor (about 74) |
| R1 2.32 ms, R0 333 ms (p95) at 91 pages | scaling run | one process; R1 on 100 questions, R0 on the first 20 of the same 100 | [efficiency](efficiency.md#scaling) |
| R1 67.6 ms, R0 21.9 s (p95) at 10,027 pages | scaling run | synthetic pages built from real sentences; R1 on 100 questions, R0 on the first 3 of the same 100; one run | README and [efficiency](efficiency.md#measurement-limits); the R0 figure is indicative only |
| R1 81.7 ms at 10,027 pages | the embedding-model run | the same synthetic pages, measured while the machine was busy with the embedding build | not used for R1; reported with R4 |
| R0 about 24 s, R1 234 to 256 ms at 10,027 pages | two earlier scaling runs | before two optimizations of R1 (term-at-a-time scoring and a cheaper freshness check); R0's own time varied between runs (21.9 to 24.3 s) | superseded |
| Answer pack 0.22 s median, 0.41 s p99 | the original 1.0 measurement | 1,000 calls in one process, different questions | historical; the performance harness replaces it |

**Rule used in the README:** a comparison of the two versions uses only the performance harness, because only there do both sides share the code paths, questions, data and machine. The scaling run is shown separately, with its sample sizes.

## Reproducibility

- **Hashes:** the protocol, the frozen question sets and the analysis script are hashed.
- **Fresh checkout:** the measurement commands were re-run there, and every deterministic result was regenerated identically.
- **Expected variation:** latency and memory usually vary by a few percent between runs, and the slowest measurements by up to about 10 % (R0 at 10,027 pages: 21.9 to 24.3 s across three runs).
