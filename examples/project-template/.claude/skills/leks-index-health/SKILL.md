---
name: leks-index-health
description: Check that LEKS sees the project as it is. The config parses, the index covers the folders and is up to date, the meaning lane runs, and a known question finds its note. Use it when search misses something you know is there, after changing the config, or once a week.
---

# Index health

## Steps

1. Run `python3 -m leks config check`. It names the config file, the number of roots and the profiles, and warns about a root whose folder is missing. `python3 -m leks config show` prints the roots: each folder the project keeps notes, code or documents in should be one, and the profile in `LEKS_PROFILE` should be among the profiles.
2. Call `status`. Note the files per root (`by_root`), when the index was built (`built_utc`), and the `lanes` line, which says whether search runs with the meaning lane or on words only.
3. When a root is missing or the build is older than the newest note, run `python3 -m leks build` and call `status` again. The session-start hook also updates the index in the background, so a gap that stays points to a cause such as a skipped folder or an unreadable file.
4. Pick two or three questions whose answer you know, each with the note that holds it. `search` each one and check that the note comes back with the right status and date. When a note is missing, `locate` its file name: a location without passages means the file is listed but not indexed.
5. Call `state` and check that every state note the project relies on is listed. A missing one needs a name LEKS treats as a state note (`STATE.md`, `NOW.md` or `*_STATE.md` by default) or a pattern under `classify.state` in the config.
6. Run `python3 -m leks check --changed` in the notes folder with the notes you touched, and fix what it lists. A run without `--changed` looks only at notes changed since its last run (see AGENTS.md). Write what you found and fixed into the state note.

## Done when

- `status` counts files in every root after a recent build, and the report says which lanes ran.
- Each known question returned its note, or the report names the cause of the miss and the fix.
