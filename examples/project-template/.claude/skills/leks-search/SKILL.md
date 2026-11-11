---
name: leks-search
description: Search this project's own notes, code and documents with the LEKS tools before answering a question about the project, its decisions, its current state or what was tried before. Use it whenever the answer should come from the project's record.
---

# Searching the project record

1. Call `search` with the question in plain words, in the language the notes are written in. If the user asked in another language, put the question into that language first. Each result names a file, a line range and the kind of source (state note, decision, result, code, history).
2. Open the lines you will rely on with `read`. Prefer a current state note or a decision over a history note when they disagree, and say that they disagree.
3. If the results miss, search again with the names of things: a file, a function, a decision number, a date. `locate` finds files and definitions by name.
4. Answer with `path:line` citations, and keep what the record says apart from your own inference.

Before you propose a change, call `tried` with a one-line summary of the proposal and report what the record says about earlier attempts.

If `status` shows the meaning lane as off, search runs on words alone. Say so when an answer seems to depend on matching meaning rather than words.
