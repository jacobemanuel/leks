---
name: leks-brief
description: Start-of-session brief from the project record with the current state notes, the notes past their freshness budget, the decisions past their Revisit date and what search runs on. Use it at the start of a session or before planning work.
---

# Session brief

The session-start hook prints a short brief. This skill writes the full one. From LEKS 3.1 the hook's brief also names the notes past their freshness budget (the `freshness` section of the config) and the notes whose Revisit date has come; on 3.0, steps 2 and 3 find them by hand.

## Steps

1. Call `state`. It lists up to 12 state notes, newest date at the top. `read` the newest one in full.
2. Take the stale notes from the brief when it lists them. Otherwise mark each state note whose date is older than the freshness budget as possibly stale. The budget is 14 days unless the project's AGENTS.md names another one.
3. Take the due decisions from the brief when it lists them. Otherwise `read` the table in `decisions/DECISIONS.md` and list each decision whose Revisit date is today or earlier. When the table and a note disagree, the head lines of the note win.
4. Call `search` with the session's topic and `status` set to `stale` to find notes on that topic that call themselves stale or outdated.
5. Call `status` and note whether search runs on words only or with the meaning lane.
6. Write the brief in at most ten lines: where the project stands, what is stale, which decisions are due, what search runs on. Cite each line.

## Done when

- The brief cites the newest state note and names what search runs on.
- It lists the stale notes and the due decisions, or says there are none.
