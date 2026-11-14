---
name: supersede-decision
description: Replace a current decision with a new one and keep the old one readable. The new note names the old one in a Supersedes line, and the old note's status line names the new one. Use it when a decision changes.
---

# Superseding a decision

## Steps

1. `search` for the topic and `read` the current decision note. Confirm with its owner, or with the user, that it is being replaced and why.
2. Write the new decision with the leks-decide skill. Its head lines include `Supersedes: D-OLD`, with the old note's id.
3. In the old note change only the status line, to `Status: superseded by D-NEW`, and put `Superseded by [[D-NEW-short-name]]` on the line under it. When the old note also has YAML front matter with a `status:` field, as notes written for LEKS 3.0 do, set it to `status: superseded` as well: LEKS reads the front matter before the lines under the title, so a front matter that still says current keeps the note current. Leave the old body as it was. With a LEKS 3.1 `write-once` edit contract on `decisions/` (`check.contracts` in the config), the notes check lets the status and superseded lines, the front matter and link targets change, and lists any other edit to the body.
4. In `decisions/DECISIONS.md`, add the new row at the top and set the old row's status to superseded.
5. Run the leks-reweave skill for the old decision, so the notes that cite it point to the new one or say why they still cite the old one.
6. When the index has been updated (the session-start hook does it in the background, `python3 -m leks build` does it at once), `search` the topic. The new note should rank above the old one, and the old one should show `NEWER VERSION:` with the new note's path.

## Done when

- The new note says `Supersedes: D-OLD` and the old note says `Status: superseded by D-NEW`, in its front matter too when it has one.
- The log table shows both rows, and after an index update a search marks the old note superseded with a newer version line.
