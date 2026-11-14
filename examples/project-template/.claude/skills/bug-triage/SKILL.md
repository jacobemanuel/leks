---
name: bug-triage
description: Triage a bug report before anyone fixes it. Reproduce it, find where it starts, check the record for earlier reports and write the next step as a brief with a severity. Use it when a bug is reported.
---

# Bug triage

## Steps

1. Restate the report: expected behaviour, actual behaviour, environment and version.
2. `search` and call `tried` for the symptom and the exact error message, to find earlier reports and fixes.
3. Reproduce it with the smallest input you can find. Record the exact command and its output. When it does not reproduce, say what you tried and what you need.
4. Narrow the cause to the commit and the function where the behaviour changes, with evidence such as a `git bisect` result or a log line.
5. Rate the severity (data loss, wrong result, crash, cosmetic) and say who is affected.
6. Write the next step with the task-brief skill: a failing test first, then the fix. Link the bug note from the state note while the bug is open.

## Done when

- The bug is reproduced, or the report says what is missing to reproduce it.
- The suspected cause has evidence, and the next step is a brief that starts with a failing test.
