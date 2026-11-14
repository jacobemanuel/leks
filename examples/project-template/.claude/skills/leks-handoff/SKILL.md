---
name: leks-handoff
description: Hand unfinished work to another agent or a later session with what is done, what is open, what the next agent reads first and must not touch, and the evidence for each. Use it when you stop before the work is finished.
---

# Handoff

## Steps

1. Call `state` and `read` the current state note, so the handoff starts from the record.
2. List what is done in this session, each item with its evidence: changed paths, commands with their exact results, `path:line` citations, links to notes.
3. List what is open, each item with its next action and its done criteria.
4. Name the files the next agent reads first, in order, and what it must not touch.
5. Name the open risks and what is still unverified.
6. Write the handoff as a dated note, for example `handoffs/YYYY-MM-DD-topic.md`, and link it from the state note. Fix what the notes check lists when the turn ends (see AGENTS.md).

## Done when

- A reader with no access to this conversation can start from the note alone.
- Every item marked done carries a path, a command result, a citation or a link.
