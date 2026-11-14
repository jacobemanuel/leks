---
name: leks-search
description: Search the project's own record (notes, code, documents, PDFs) with the LEKS tools before answering a question about the project, its decisions, its current state or what was tried before. Use it whenever the answer should come from the project's record.
---

# Searching the project record

## Steps

1. Call `search` with the question in plain words, in the language the notes are written in. If the user asked in another language, put the question into that language first. Split a question with several parts into one search per part.
2. Read the head line of each result: citation, source class, status and date. A line `NEWER VERSION: <path>` under a result names the note that replaced it, so read that note. A line `file changed since indexed` means the passage may be out of date, so read the file itself.
3. Open the lines you will rely on with `read`. A passage can stop short of the part that matters.
4. Prefer a current state note or a current decision over an older note on the same topic. When two current sources disagree, say so and cite both.
5. If the results miss, search again with the names of things: a file, a function, a decision id, a date. `locate` finds files by name and code definitions with `symbol:name`. The `root` and `status` arguments of `search` narrow the results.
6. Check the last line of the output. When it says search runs on words only, a miss proves less, so say that in the answer.
7. Answer with a `path:line` citation for each claim, and keep what the record says apart from your own inference.

Before you propose a change, call `tried` with a one-line summary of the proposal and report what the record says about earlier attempts.

## Done when

- Every claim about the project in the answer carries a citation you opened with `read`.
- Or the answer says plainly that the record holds nothing on the question, and names the searches you ran.
