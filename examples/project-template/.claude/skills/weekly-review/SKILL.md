---
name: weekly-review
description: Weekly review of the project record covering what changed, which decisions were made or are due, which notes went stale and what is blocked, ending with goals for next week. Use it once a week or before planning.
---

# Weekly review

## Steps

1. Run the leks-brief skill.
2. List the week's changes: `git log --since="1 week ago" --stat` for the code, and `find . -name '*.md' -mtime -7` for the notes.
3. `search` for the decisions of this week and check that each one has its note and its row in `decisions/DECISIONS.md`.
4. For each open item in the state note, mark it done, progressed, blocked (and on whom) or dropped.
5. Write the review as `reviews/YYYY-MM-DD.md` with a citation for each line, and link it from the state note. When the state note no longer matches the review, replace it with the leks-close-session skill.
6. End with at most four goals for next week, each with its done criteria.

## Done when

- The review cites its sources and every open item has a mark.
- The state note matches the review.
