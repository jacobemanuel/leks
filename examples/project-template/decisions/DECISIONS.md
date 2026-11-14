# Decision log

Status: current

Each decision is a note of its own in this folder, named `D-NN-short-name.md`, with a row in the table below, newest at the top. LEKS 3.1 reads a note's status, date, revisit date and the note it replaces from the plain lines right under the note's title, so every decision keeps them there. LEKS 3.0 reads status and date from YAML front matter only; on 3.0, repeat them there as `status:` and `date:`. LEKS 3.1 reads the front matter before these lines, so keep both in step when a status changes.

- To add a decision, copy the note template below into a new note with the next free number, and add its row at the top of the table. The row's link is also what keeps the notes check from listing the new note as an orphan.
- A decision is never rewritten. When it changes, a new note says `Supersedes: D-NN`, the old note's status line becomes `Status: superseded by D-MM` (a line `Superseded by [[D-MM-short-name]]` under it is optional), its front matter `status:` becomes `superseded` when it has one, and the old row's status changes with it.
- The note is the source and the table is the index. When they disagree, fix the table.

| ID | Decision | Status | Date | Revisit |
|---|---|---|---|---|

<!-- a row looks like this, newest first:
| [[D-02-tile-eviction]] | evict the oldest tile first | current | 2026-03-02 | 2026-06-01 |
-->

## Note template

```markdown
# D-NN: the decision in a few words

Status: current
Date: YYYY-MM-DD
Revisit: YYYY-MM-DD, or the event that should reopen it
Supersedes: none
Owner: who made the call and answers for it
Source: path:line of the evidence, or a link

## Decision

The recommendation in one or two sentences, with the reason.

## Context

What forced the choice, with citations.

## Options weighed

- Each option that lost, and why it lost.

## Counterargument

The strongest case against the decision, and why the decision still holds.
```

Keep the head lines right under the title and above every `##` heading: LEKS reads at most eight non-blank lines there, and the template uses six. `Status` takes a status word LEKS knows, such as current, accepted or superseded. `Supersedes` names the id of the decision this one replaces, or says none. The skills leks-decide and supersede-decision in `.claude/skills` walk through both cases.
