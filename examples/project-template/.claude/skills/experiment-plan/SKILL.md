---
name: experiment-plan
description: Plan an experiment before any data exists. Write the question, the prediction and the result that would refute it, the measure, the runs and the stopping rule, and record them. Use it before a benchmark, an A/B comparison, a load test or a tuning run.
---

# Experiment plan

## Steps

1. Call `tried` with the experiment in one line and `search` for earlier runs and decisions on the topic. `read` what you find.
2. Write the question and the prediction, and the result that would show the prediction wrong.
3. Fix the measure and how it is computed, the baseline, the inputs, the number of runs, the seeds or machines, and the stopping rule.
4. Fix what you will report whatever the outcome, and how you will read a tie or a mixed result.
5. List what else could produce the predicted result, and how the plan rules it out or reports it.
6. Write the plan as a dated note under `results/` with `Status: current`, and link it from the state note. Record the go decision with the leks-decide skill when the runs cost real time or money.

## Done when

- The plan states the prediction, the result that refutes it, the measure, the baseline, the run count and the stopping rule.
- Its date is earlier than any of the data.
