---
name: results-report
description: Report the results of a run or an experiment against its plan, with each number traced to its source, what the numbers show and what they do not, the verdict in the plan's words and every deviation from the plan. Use it after a run and before anyone acts on the numbers.
---

# Results report

## Steps

1. `search` for the plan of the run and `read` it. Note the prediction, the measure and the stopping rule.
2. Collect the raw outputs with their paths, commands, versions and dates. Check that the counts match the plan.
3. Compute the numbers the plan named. Give each with its unit and the count it rests on. Add a spread or an interval where one applies.
4. Compare with the prediction in the words the plan fixed: held, failed, mixed or not testable.
5. List each deviation from the plan and what it may change in the reading.
6. Write the report as a dated note under `results/`, and link it from the plan and the state note. Record any decision it triggers with the leks-decide skill.

## Done when

- Every number traces to a file or a command, and the verdict uses the plan's words.
- The deviations are listed, or the report says there were none.
