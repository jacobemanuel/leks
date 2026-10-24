# Version history

## 2.0 (current)

LEKS 2.0 keeps the search results of 1.0 and rebuilds what sits underneath. It runs on the real knowledge base.

### How it was built

The work followed an evaluation protocol that was frozen before the first change.
1. **Baseline.** An audit of 1.0, a full backup, the frozen protocol and a threat model.
2. **Integrity and performance:**
   - atomic writes under a lock;
   - the outside evidence store with restore;
   - the hash-chained journal and persistent incidents;
   - a stop check that catches in-place edits of source files;
   - a streaming full integrity check (peak memory 1.6 GB → 82 MB);
   - the persistent index R1, with rankings identical to R0.
3. **Retrieval experiments:**
   - the local embedding model on its own (R2) and the hybrid R4;
   - the cross-encoder reranker R5;
   - a local question-answering model for unanswerable questions.

   All of them run in the sandboxed model helper, were measured on the development set, and stayed opt-in.
4. **Evidence reliability:**
   - hashed excerpt anchors for every active claim;
   - typed facts with valid and record time;
   - contradiction detection and freshness rules.
5. **Frozen benchmark.** 320 questions, evaluated once under the pre-registered analysis: R1's results are identical to R0's; R4 is better within the top 5 but is not deployed.
6. **Validation:**
   - real-protocol MCP tests;
   - a reproduction from a fresh checkout;
   - a scaling run to 10,027 pages;
   - a test with 10,000 distractor pages;
   - an independent review that recomputed the statistics and re-ran tests.
7. **Hardening:**
   - the pre-tool check hardened against the forms reported by the review and by two later probes;
   - `doctor` and an idempotent `setup`;
   - safe handling of damaged metadata;
   - tests for the modules that had none;
   - a fault rehearsal on a copy of the real data.

### Deployment

- **Rehearsals:** the integration into the real knowledge base was rehearsed on full copies of it. Each rehearsal validated the result and rolled back to a byte-identical state.
- **The real run:** it kept a verified backup and the same rollback path. The developer's uncommitted work in the knowledge base stayed out of the 2.0 commits.
- **Evidence protection:** on by default, re-applied and reported at every agent session start.

### Known limitations

- retrieval quality is the same as in 1.0;
- unanswerable questions are not recognised;
- typed facts cover only part of the knowledge.

See [known limitations](limitations.md).

## 1.0

Published here first, under the title "Engineering knowledge base":
- source registry with hashes and revisions;
- compiled pages and claims with confidence;
- R0 search (BM25, domain thesaurus, archive rules);
- a one-call answer pack with the passages of the four best pages;
- an MCP server, three hooks, and Unity and Blender references;
- on-device text recognition for images and PDFs;
- nine agent roles and fourteen skills.

Its README is [archived](history/README-1.0.md) as published, apart from the small edits listed at its top.

## How the 1.0 README's statements carry over to 2.0

| 1.0 statement | In 2.0 | Note |
|---|---|---|
| Runs locally; standard-library Python; retrieval and OCR make no network calls | current, now checked | A static check finds no networking imports in the Python tools; it checks code, not run-time behaviour. The text-recognition helper uses on-device Apple frameworks (code review). The model helper for the optional engines runs in a network-denied sandbox, confirmed by a probe, and its installer downloads pinned files once. Obsidian, the code editor and AI coding agents are separate applications (see the README). |
| ≈ 70 % of held-out questions with every answer key in one pack, up from 57 % | historical | A 1.0 result, not re-measured for 2.0. On the same 177 questions, R1's rankings match R0's. Not comparable with the frozen benchmark. |
| 2.31 agent steps per question (modelled), down from 3.40 | historical, modelled | A model of the 1.0 answer pack; it was not re-run for 2.0 ([efficiency](efficiency.md#modelled-10-only)). |
| About a third less model cost per question (modelled) | historical, modelled | From the 1.0 cost model; not re-run for 2.0, and no cost figure is given for 2.0. |
| 0.41 s p99 latency of an answer pack | superseded | A different harness. In the shared performance harness, p99: 409 ms (1.0) against 40 ms (2.0). |
| 2,800+ evidence files, SHA-256 hashed; renames keep IDs | current | 2,850 registered source files, now also copied into the evidence store |
| 114 claims | updated | 116 claims; every active claim anchored to a hashed excerpt |
| 105 automated tests | updated | 250 in 2.0 |
| "How it works" diagram | superseded | Replaced by the 2.0 architecture diagram |
| Evidence protected, knowledge compiled; translations are renditions | current, extended | Plus anchors, the evidence store, the journal, incidents and file-system flags |
| Facts carry confidence, scope, evidence date and sources | current, extended | Plus typed facts with valid and record time, and contradiction checks |
| Ranking gain over the earlier engine not significant; paraphrases weak | historical, confirmed | The frozen benchmark confirms paraphrases as the default engine's weak spot. |
| On-device sentence embeddings never beat BM25; neither embeddings nor stemming shipped | historical, partly superseded | True for the models tested then. In 2.0 a different local model fused with BM25 (R4) improved top-5 results on the frozen benchmark; it is still not deployed. |
| One MCP call returns the passages of the four best pages within 1,800 tokens | current, clarified | 1,800 estimated tokens is the budget for the page passages. A whole pack, with claims, next pages and status fields, is about 2,800 estimated tokens in version 1.0 and about 2,860 in version 2.0, which adds a field naming the engine and a fixed note that the passages are not a confirmed answer. |
| Context loaded at session start about 4,800 tokens | different scope | That figure counted everything the knowledge base adds to each request, including tool descriptions. The session-start files alone are about 2,500 tokens in both versions. |
| Key rules in code: session-start hook, pre-tool check, stop check | current, extended | The pre-tool check now fails closed; the stop check catches in-place edits of source files. |
| Roslyn, scene, prefab and Blender references; Unity Editor bridge; OCR (99.42 % character accuracy) | current | Unchanged and not re-measured |
| Nine agent roles, fourteen skills, one writer, attribution by model name | current | Unchanged |
| Graph view and orbit video | current | Unchanged; shown in the archived README of 1.0 |
