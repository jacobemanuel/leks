---
name: leks-decide
description: Make and record a decision. Recommend one option first, then give the strongest counterargument, then record the decision in the project's decision log with its owner, evidence, date and a Revisit line. Use it when a choice between options is made or asked for.
---

# Making and recording a decision

Each decision is its own note in `decisions/`, and `decisions/DECISIONS.md` lists them and holds the note template. LEKS 3.1 reads the Status, Date, Supersedes and Revisit lines right under a note's title, and its session brief names the decisions whose Revisit date has come. LEKS 3.0 reads status and date from YAML front matter only (`status:` and `date:`), so on 3.0 put them there too.

## Steps

1. Call `tried` with the question in one line and `search` for the topic. `read` the current decision on it, if one exists. When a current decision already answers the question, cite it and stop. When the new call changes it, use the supersede-decision skill instead.
2. Recommend one option, first and alone: one or two sentences with the reason and the evidence as `path:line` citations or links. Do not open with a list of options.
3. Give the strongest counterargument, the one a careful reviewer would raise against that option. Say why the recommendation still holds, or what result would overturn it.
4. Under Options weighed, list each option that lost, with one line on why.
5. Name the owner, the person or role who makes the call and answers for it. When the user owns the call, show steps 2 to 4 and get the user's yes before you write anything.
6. Set the Revisit line to a date (`YYYY-MM-DD`) or to the event that should reopen the question. Prefer a date: the LEKS 3.1 session brief reads a date, and an event needs someone to remember it.
7. Copy the note template from `decisions/DECISIONS.md` into `decisions/D-NN-short-name.md` with the next free number. Fill the head lines (Status `current`, Date today, Revisit, Supersedes, Owner, Source) and the sections under them.
8. Add the decision's row at the top of the table in `decisions/DECISIONS.md`, with a link to the note. Fix what the notes check lists when the turn ends (see AGENTS.md).

## Done when

- The note exists with its head lines right under the title, its Decision section names one option, and its Counterargument section is filled.
- The log table links it, and after the next index update a `search` for the topic returns the note with status `current`.
