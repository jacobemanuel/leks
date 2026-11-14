---
name: code-review
description: Review a code change for a test that fails on the old code and passes on the new one, for security, for edge cases and error paths, and against the project's decisions. Use it before a change is merged or reported done.
---

# Code review

## Steps

1. Read the brief or the change description, then the whole diff. `search` for the decisions and notes on the area and call `tried` with the approach.
2. Run the test suite on the change and record the counts: tests run, failures, errors and skips.
3. Find the test that guards the change. Show that it fails on the old code (in a scratch copy at the parent commit, or with the change reverted) and passes on the new code. When there is no such test, write one or report it as missing.
4. Check security: outside input (files, network replies, tool results, user text) handled as data, no secrets in code or logs, paths kept inside their root, no shell commands built from raw input.
5. Check edge cases and error paths: empty, huge, malformed or truncated input, missing files, timeouts, concurrent runs, text outside ASCII, and errors that leave state half written.
6. Check the change against the project's decisions and conventions, with citations.
7. Report the findings ranked by severity, each with `file:line`, the input that breaks it, what goes wrong and a fix. Say what you did not check.

## Done when

- The report names the test that fails on the old code and passes on the new one, or says it is missing.
- Each finding has a failure scenario, and the test counts are stated.
