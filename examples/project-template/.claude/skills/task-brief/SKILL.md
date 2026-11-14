---
name: task-brief
description: Write a brief that one agent or person can carry out without the conversation, with the goal, the files they may open, the done criteria, the tests to run, the context with citations and what to report. Use it before you delegate any task.
---

# Task brief

## Steps

1. `search` for the task and call `tried` with it in one line. `read` the current decision and state note that bear on it.
2. Write the goal in one sentence, as an outcome someone can check.
3. List the files and folders the agent may open, and the ones it must not touch.
4. Write the done criteria as checks someone else can run: a test, a command with its expected output, a file that must exist, a value that must match.
5. Name the tests to run, as exact commands.
6. Add the context: the `path:line` citations to read before starting, and the limits on versions, time, cost or scope.
7. Say what to report back: changed paths, commands with their exact results, citations, and what could not be verified.

The brief, ready to paste:

```text
Goal:
Files you may open:
Must not touch:
Done when:
Tests to run:
Context:
Report back:
```

## Done when

- A reader without the conversation can do the task, and you can check the result from the report alone.
