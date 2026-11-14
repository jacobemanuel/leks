# Link prior results

Plan and bar: `docs/benchmark.md` sections 7 and 7a. Variants re-rank the LEKS top 20. The relation variants (rel, rel-low) are the addendum of section 7a.

## PEP set

notes 713, links 1622, relation edges 40, state notes 0

| variant | q1 hit@1 | lost | gained | p | Holm p | q2 current above replaced | q2c current above replaced |
|---|---|---:|---:|---:|---:|---|---|
| control | 621/695, 89.4% (86.8 to 91.4) |  |  |  |  | 30/41, 73.2% (58.1 to 84.3) | 32/41, 78.0% (63.3 to 88.0) |
| out | 445/695, 64.0% (60.4 to 67.5) | 194 | 18 | 1.86e-38 | 5.58e-38 | 22/41, 53.7% (38.7 to 67.9) | 22/41, 53.7% (38.7 to 67.9) |
| in | 370/695, 53.2% (49.5 to 56.9) | 255 | 4 | 4.02e-70 | 2.41e-69 | 35/41, 85.4% (71.6 to 93.1) | 32/41, 78.0% (63.3 to 88.0) |
| both | 397/695, 57.1% (53.4 to 60.8) | 234 | 10 | 1.26e-56 | 6.32e-56 | 26/41, 63.4% (48.1 to 76.4) | 20/41, 48.8% (34.3 to 63.5) |
| both-low | 534/695, 76.8% (73.6 to 79.8) | 98 | 11 | 1.33e-18 | 2.66e-18 | 27/41, 65.9% (50.5 to 78.4) | 30/41, 73.2% (58.1 to 84.3) |
| state | 621/695, 89.4% (86.8 to 91.4) | 0 | 0 | 1 | 1 | 30/41, 73.2% (58.1 to 84.3) | 32/41, 78.0% (63.3 to 88.0) |
| all | 397/695, 57.1% (53.4 to 60.8) | 234 | 10 | 1.26e-56 | 6.32e-56 | 26/41, 63.4% (48.1 to 76.4) | 20/41, 48.8% (34.3 to 63.5) |
| rel | 610/695, 87.8% (85.1 to 90.0) | 12 | 1 | 0.00342 | 0.00684 | 35/41, 85.4% (71.6 to 93.1) | 35/41, 85.4% (71.6 to 93.1) |
| rel-low | 613/695, 88.2% (85.6 to 90.4) | 9 | 1 | 0.0215 | 0.0215 | 31/41, 75.6% (60.7 to 86.2) | 34/41, 82.9% (68.7 to 91.5) |

Per kind (count of questions right):

| variant | q1 has newer | q1 has none | q2 | q2c |
|---|---:|---:|---:|---:|
| control | 5/33 | 616/662 | 30/41 | 32/41 |
| out | 9/33 | 436/662 | 22/41 | 22/41 |
| in | 1/33 | 369/662 | 35/41 | 32/41 |
| both | 6/33 | 391/662 | 26/41 | 20/41 |
| both-low | 6/33 | 528/662 | 27/41 | 30/41 |
| state | 5/33 | 616/662 | 30/41 | 32/41 |
| all | 6/33 | 391/662 | 26/41 | 20/41 |
| rel | 1/33 | 609/662 | 35/41 | 35/41 |
| rel-low | 4/33 | 609/662 | 31/41 | 34/41 |

## Fixture set

notes 43, links 15, relation edges 1, state notes 1

| variant | hit@1 | lost | gained | p | Holm p | current above stale |
|---|---|---:|---:|---:|---:|---|
| control | 27/37, 73.0% (57.0 to 84.6) |  |  |  |  | 6/6, 100.0% (61.0 to 100.0) |
| out | 25/37, 67.6% (51.5 to 80.4) | 2 | 0 | 0.5 | 1 | 6/6, 100.0% (61.0 to 100.0) |
| in | 22/37, 59.5% (43.5 to 73.7) | 5 | 0 | 0.0625 | 0.375 | 6/6, 100.0% (61.0 to 100.0) |
| both | 25/37, 67.6% (51.5 to 80.4) | 2 | 0 | 0.5 | 1 | 6/6, 100.0% (61.0 to 100.0) |
| both-low | 25/37, 67.6% (51.5 to 80.4) | 2 | 0 | 0.5 | 1 | 6/6, 100.0% (61.0 to 100.0) |
| state | 24/37, 64.9% (48.8 to 78.2) | 4 | 1 | 0.375 | 1 | 6/6, 100.0% (61.0 to 100.0) |
| all | 24/37, 64.9% (48.8 to 78.2) | 4 | 1 | 0.375 | 1 | 6/6, 100.0% (61.0 to 100.0) |
| rel | 27/37, 73.0% (57.0 to 84.6) | 0 | 0 | 1 | 1 | 6/6, 100.0% (61.0 to 100.0) |
| rel-low | 27/37, 73.0% (57.0 to 84.6) | 0 | 0 | 1 | 1 | 6/6, 100.0% (61.0 to 100.0) |

Per kind (count of questions right):

| variant | current above stale | hit@1 code | hit@1 current | hit@1 document | hit@1 fact | hit@1 history | hit@1 name |
|---|---:|---:|---:|---:|---:|---:|---:|
| control | 6/6 | 2/6 | 3/6 | 2/4 | 13/14 | 4/4 | 3/3 |
| out | 6/6 | 2/6 | 3/6 | 2/4 | 12/14 | 4/4 | 2/3 |
| in | 6/6 | 2/6 | 2/6 | 2/4 | 11/14 | 4/4 | 1/3 |
| both | 6/6 | 2/6 | 3/6 | 2/4 | 12/14 | 4/4 | 2/3 |
| both-low | 6/6 | 2/6 | 3/6 | 2/4 | 12/14 | 4/4 | 2/3 |
| state | 6/6 | 2/6 | 4/6 | 2/4 | 10/14 | 4/4 | 2/3 |
| all | 6/6 | 2/6 | 4/6 | 2/4 | 10/14 | 4/4 | 2/3 |
| rel | 6/6 | 2/6 | 3/6 | 2/4 | 13/14 | 4/4 | 3/3 |
| rel-low | 6/6 | 2/6 | 3/6 | 2/4 | 13/14 | 4/4 | 3/3 |

## Verdict

| variant | gain on both sets | no replacement loss | kinds down | ships |
|---|---|---|---|---|
| out | no | no | fixtures: hit@1 fact, fixtures: hit@1 name, peps: q1 has none, peps: q2, peps: q2c | no |
| in | no | yes | fixtures: hit@1 current, fixtures: hit@1 fact, fixtures: hit@1 name, peps: q1 has newer, peps: q1 has none | no |
| both | no | no | fixtures: hit@1 fact, fixtures: hit@1 name, peps: q1 has none, peps: q2, peps: q2c | no |
| both-low | no | no | fixtures: hit@1 fact, fixtures: hit@1 name, peps: q1 has none, peps: q2, peps: q2c | no |
| state | no | yes | fixtures: hit@1 fact, fixtures: hit@1 name | no |
| all | no | no | fixtures: hit@1 fact, fixtures: hit@1 name, peps: q1 has none, peps: q2, peps: q2c | no |
| rel | no | yes | peps: q1 has newer, peps: q1 has none | no |
| rel-low | no | yes | peps: q1 has newer, peps: q1 has none | no |

Ships: none
