# History

LEKS has had three releases before this one. This page says what each one was, where it lives in this repository, and why the history looks the way it does.

## Versions

| version | when | where | what it was |
|---|---|---|---|
| 1.0 | before 24 Oct 2026 | its README, archived in [v2.0.0](https://github.com/jacobemanuel/leks/blob/v2.0.0/docs/history/README-1.0.md) | Published before this repository, under the title "Engineering knowledge base". Word search (BM25 with a domain thesaurus and archive rules) over compiled pages, claims with confidence, a one-call answer pack, an MCP server and three hooks. |
| 2.0 | 24 Oct to 1 Nov 2026 | tag [v2.0.0](https://github.com/jacobemanuel/leks/tree/v2.0.0), release LEKS 2.0 | The same search results, a new engine underneath: an evidence store with restore, a hash-chained journal, claims anchored to hashed excerpts, typed facts with valid and record time, and a persistent word index (R1). An embedding model and a reranker were measured on a frozen 320-question benchmark and stayed opt-in. The engine code is MIT, with 124 tests in CI. |
| 3.0 | 11 Nov 2026 | tag [v3.0.0](https://github.com/jacobemanuel/leks/tree/v3.0.0), release LEKS 3.0 | Search by words and by meaning, with two local models chosen in a tournament fixed in advance. Full text of Markdown, PDF, `.docx` and code, file names and paths, questions in several languages, one library per role, MCP for Claude Code and GitHub Copilot. The measurements are in the [3.0 report](report-3.0.md). |
| 3.1 | 14 Nov 2026 | tag [v3.1.0](https://github.com/jacobemanuel/leks/tree/v3.1.0), release LEKS 3.1 | The search lanes and models of 3.0, and the parts that keep a growing record true: `Status:` and `Date:` lines under the title and ids in them as proof of a newer version, the `neighbours` tool, freshness budgets and revisit dates, edit contracts in the notes check, a decision log, Codex as a third host, 26 skills, an optional agent team, a public benchmark on the Python PEPs and a cost harness for three hosts. CI on macOS, Linux and Windows with Python 3.10 to 3.13. |

## Why 3.0 was one commit

I built 3.0 in a separate working repository and published the finished tree as one commit with no parent. The public history then starts at the release I had reviewed as a whole, and nothing from the working repository comes along by accident.

That also cut the 2.0 commits off from `main`. They are back: `main` merges the 2.0 line with a merge that keeps the 3.x files exactly as they are, so `git log` on `main` reaches every 2.0 commit and each tag points at its release.

3.1 came the same way: I built it in the working repository and added the finished tree to `main` as one commit after that merge.

The 2.0 line as it stands now:

- `3fb83cd`, `cfa14a3`, `9fb8e6c`, `4f0f337` (24 to 27 Oct): the 2.0 release and three README edits, with the hashes they had in October.
- `edeb4fa` (tag v2.0.0, 1 Nov): the nine commits from 27 Oct to 1 Nov folded into one. They brought the engine code with its 124 tests and CI, the MIT licence for the engine, the archived 1.0 README and README updates. A few words in test fixtures, comments and rule codes are renamed to neutral ones; behaviour and tests are unchanged, and the 124 tests pass.

## Looking at an old version

```bash
git checkout v2.0.0      # the 2.0 tree, engine under engine/
git checkout v3.0.0      # the 3.0 tree
git checkout v3.1.0      # the 3.1 tree
git log --oneline --graph main
```

The 2.0 engine runs on its own: `cd engine && python3 -m unittest discover -s tools -p 'test_*.py'`.
