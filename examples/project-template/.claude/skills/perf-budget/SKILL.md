---
name: perf-budget
description: Check a change against the project's performance budget. Find the budget in the record, measure before and after on the same machine and input, compare against run-to-run noise and record the result. Use it for any change that may cost frame time, latency, memory or size.
---

# Performance budget

## Steps

1. `search` for the budget and `read` the decision that sets it: frame time, latency, memory or build size. When there is no budget, propose one with the leks-decide skill before you measure.
2. Fix the measurement: the input, the machine and build, the warm-up and the number of runs. Use the same setup before and after.
3. Measure the old code, then the new code. Keep the raw numbers and the exact commands.
4. Compare the medians and the spread. A difference inside the run-to-run noise counts as no measurable change.
5. Give each budget line a verdict, pass or fail, with the numbers.
6. Write the result as a dated note under `results/` and link it from the decision or the state note.

## Done when

- The numbers before and after come from the same setup, and each budget line has a verdict with its numbers.
- The result note is linked from the decision or the state note.
