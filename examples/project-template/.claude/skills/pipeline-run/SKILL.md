---
name: pipeline-run
description: Run a data or build job so others can rely on its output. Write the run note and dry run on a small input before the real run, then check the outputs and link the result. Use it for any job whose output others use.
---

# Pipeline run

## Steps

1. `search` for the job's spec and its last runs, including failed ones, and call `tried` with any change to the job.
2. Write a run note before you start: the inputs and their versions, the parameters, the output folder and the exact command.
3. Dry run on a small input and check the shape and counts of the outputs.
4. Run the job. Keep the exact command, the log, the exit code and the wall time.
5. Check the outputs: counts match the inputs, no file is empty or cut short, sizes look right and checksums are recorded.
6. Write the result into the run note under `results/`, link it from the state note, and report failures with their log lines.

## Done when

- The run note holds the command, the versions, the exit code and the output checks.
- The run note is linked from the state note.
