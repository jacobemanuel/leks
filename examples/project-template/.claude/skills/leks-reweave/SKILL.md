---
name: leks-reweave
description: After you edit or add a note, bring the notes that depend on it in line. List its neighbours, update what they say, fix links and mark replaced notes superseded. Use it after any change to a decision, a state note, a spec or a plan.
---

# Reweaving the notes around a change

The `neighbours` tool and the `leks neighbours` command come with LEKS 3.1. On 3.0, step 2 has a fallback that uses `search` and `locate`.

## Steps

1. Write down which note changed and what changed, in one line.
2. List the notes that depend on it.
   - LEKS 3.1: call `neighbours` with the path of the changed note as `search` cites it. It lists notes that still hold numbers or sentences the note no longer has, notes that link to it, files it links to and the files closest to it in meaning, each with path, line, status, date and the reason. When the index already holds the new text, pass the old text as `was`. From a shell, `python3 -m leks neighbours PATH --was OLD_FILE` does the same.
   - LEKS 3.0: `search` for the note's id (such as D-07) and its title, then for its main terms and numbers, and `locate` its file name to catch links by name.
3. `read` each listed note where it touches the changed one. Decide whether it still holds, needs an edit, or is replaced by the change.
4. Edit the notes that need it. Change the claim, link the changed note, and keep any history to one short line.
5. For a note the change replaces, set its status line to `Status: superseded by D-NN` when the newer note has an id, or to `Status: superseded by [[newer-note]]`. When the note has YAML front matter with a `status:` field, set that to `superseded` too, since LEKS reads the front matter before the lines under the title. Leave the rest of its text as it was.
6. Fix every link that points to a renamed or moved note. Fix what the notes check lists when the turn ends (see AGENTS.md). With LEKS 3.1 edit contracts in the config, the check also lists a changed body in a write-once note and any change to a frozen note; restore the text, or keep it on purpose with `python3 -m leks check --accept PATH`.

## Done when

- Each note `neighbours` listed (or the 3.0 searches found) was read, and every one that no longer held was edited or marked superseded.
- `python3 -m leks check --changed` with the notes you touched reports nothing.
