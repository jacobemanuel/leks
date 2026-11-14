---
name: evidence-check
description: Check a claim or an agent's report against evidence before you accept it. Read what it cites and rerun what it measured. Use it before you call work done or repeat a claim to the user.
---

# Evidence check

## Steps

1. Split the text into single claims. Label each one: fact about the project, measured result, change to a file, or inference.
2. For each fact, `read` the cited lines. When there is no citation, `search` for it. Mark it held, partly held, contradicted or not found.
3. For each measured result, rerun the command when that is cheap, or open its saved output, and compare the numbers exactly.
4. For each changed file the report names, open it and check that the change is there and matches the description.
5. Check counts and words like all, every, none and always. "All tests pass" needs the test count and zero failures; "in every run" needs each run.
6. Report a table with the claim, its kind, the evidence (`path:line` or the command) and the verdict. Inferences stay labelled as inferences.

## Done when

- Every claim has a verdict, and nothing marked held rests on the reporting agent's word alone.
