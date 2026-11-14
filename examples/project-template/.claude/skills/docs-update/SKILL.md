---
name: docs-update
description: Bring the docs in line with a change. Find every page that describes the changed behaviour, update it, run the commands it shows and fix its links. Use it after any change users can see.
---

# Docs update

## Steps

1. `search` for the changed behaviour and the names of the commands and options involved, and `locate` the doc files that name them.
2. `read` each passage and decide whether it still holds or needs an edit.
3. Edit the pages. Keep each claim to what you ran or to a cited source.
4. Run every command you changed in the docs and check that the output shown is what you get.
5. Fix the links. Fix what the notes check lists when the turn ends (see AGENTS.md).
6. List the pages you changed in your report.

## Done when

- Every page that named the changed behaviour was read, and is unchanged for a stated reason or edited.
- The commands the docs show were run, and `python3 -m leks check --changed` with the pages you changed reports nothing.
