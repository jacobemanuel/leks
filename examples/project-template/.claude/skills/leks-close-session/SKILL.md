---
name: leks-close-session
description: Close a working session. Replace the state note with where the project stands now (no appended log) and record the session's decisions, then check the notes. Use it at the end of a session.
---

# Closing a session

## Steps

1. Call `state` and `read` the current state note in full.
2. Collect what changed in this session: decisions with their notes, results, changed files, new open items. Cite each one.
3. Record each decision made in this session that has no note yet with the leks-decide skill.
4. Write the state note again from the top: where the project stands, what is open with its next step, what is blocked and on whom, links to the current decisions. Replace the old text and add no session log. When the project has no state note yet, create `STATE.md` at its root; LEKS treats `STATE.md`, `NOW.md` and `*_STATE.md` as state notes by default.
5. Set its date line (`updated:` in front matter, or `Date:` under the title) to today. When older text must be kept, move it to a note under `history/` and link it.
6. Tell the user in a few lines what changed in the state note. Fix what the notes check lists when the turn ends (see AGENTS.md). With a LEKS 3.1 `living` edit contract on the state note (`check.contracts` in the config), the check also names the first line that starts with a date outside a log, changelog, history or journal section, and how many such lines the note has: that is a session log creeping back in.

## Done when

- The state note describes the present only and carries today's date.
- It links every decision made in this session, and `python3 -m leks check --changed` with the state note reports nothing.
