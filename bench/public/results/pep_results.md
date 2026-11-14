# PEP benchmark results

Written by `bench/public/pep_bench.py`. Method, rule and limits: `docs/benchmark.md`.

- PEP commit: `2a71e92663616ff5b2c3085ac8ed4f29d38e83d4`, notes: 714, questions file sha256: `8c2924b24a6a8a079acdd6ffc6d88d1cfdedf86b64290e22c114e08c84062323`
- run (UTC): 2026-11-13 21:32:53, Python 3.13.12, SQLite 3.53.3, results asked: 20, a gold note below rank 10 is a miss

## Systems

| system | status | notes in the index | lanes |
|---|---|---:|---|
| bm25 | ok | 714 | sqlite fts5 bm25, whole notes, no priors |
| leks | ok, leks 3.1.0, code eb7ca7bed5e0 | 713 | words only: no models configured |
| leks30 | ok, leks 3.0.0, code d38bf56ed291 | 713 | words only: no models configured |
| leks+models | skipped: python packages for the model lanes are not installed: torch, transformers, safetensors, tokenizers (pip install -e ".[models]") | | |

## Known item (q1)

| system | n | hit@1 | hit@5 | MRR@10 |
|---|---:|---|---|---|
| bm25 | 695 | 80.6% (77.5 to 83.3) | 96.7% (95.1 to 97.8) | 0.875 (0.855 to 0.894) |
| leks | 695 | 89.4% (86.8 to 91.4) | 98.4% (97.2 to 99.1) | 0.934 (0.919 to 0.949) |
| leks30 | 695 | 90.1% (87.6 to 92.1) | 98.4% (97.2 to 99.1) | 0.938 (0.923 to 0.952) |

Split by whether the PEP has a newer one:

| system | has a newer one: hit@1 | has none: hit@1 |
|---|---|---|
| bm25 | 22/33, 66.7% (49.6 to 80.2) | 538/662, 81.3% (78.1 to 84.1) |
| leks | 5/33, 15.2% (6.7 to 30.9) | 616/662, 93.1% (90.9 to 94.8) |
| leks30 | 9/33, 27.3% (15.1 to 44.2) | 617/662, 93.2% (91.0 to 94.9) |

## Replacement, the new PEP as the answer

Gold is the newer PEP of the pair. Same metrics as q1.

| system | set | n | hit@1 | hit@5 | MRR@10 |
|---|---|---:|---|---|---|
| bm25 | q2 | 41 | 2.4% (0.4 to 12.6) | 63.4% (48.1 to 76.4) | 0.318 (0.254 to 0.385) |
| leks | q2 | 41 | 61.0% (45.7 to 74.3) | 87.8% (74.5 to 94.7) | 0.699 (0.575 to 0.814) |
| leks30 | q2 | 41 | 48.8% (34.3 to 63.5) | 87.8% (74.5 to 94.7) | 0.628 (0.506 to 0.744) |
| bm25 | q2c | 41 | 2.4% (0.4 to 12.6) | 63.4% (48.1 to 76.4) | 0.318 (0.254 to 0.385) |
| leks | q2c | 41 | 46.3% (32.1 to 61.3) | 80.5% (66.0 to 89.8) | 0.603 (0.482 to 0.721) |
| leks30 | q2c | 41 | 31.7% (19.6 to 47.0) | 80.5% (66.0 to 89.8) | 0.511 (0.401 to 0.623) |

## Replacement (q2)

| system | current above replaced | new missing | old first | old listed | pointer when listed | wrong pointer |
|---|---|---:|---:|---:|---|---:|
| bm25 | 5/41, 12.2% (5.3 to 25.5) | 6 | 36 | 39 | n/a | n/a |
| leks | 30/41, 73.2% (58.1 to 84.3) | 4 | 11 | 40 | 33/40, 82.5% (68.1 to 91.3) | 0 |
| leks30 | 25/41, 61.0% (45.7 to 74.3) | 4 | 16 | 40 | 25/40, 62.5% (47.0 to 75.8) | 0 |

## Replacement (q2c)

| system | current above replaced | new missing | old first | old listed | pointer when listed | wrong pointer |
|---|---|---:|---:|---:|---|---:|
| bm25 | 5/41, 12.2% (5.3 to 25.5) | 6 | 36 | 39 | n/a | n/a |
| leks | 32/41, 78.0% (63.3 to 88.0) | 5 | 8 | 35 | 28/35, 80.0% (64.1 to 90.0) | 0 |
| leks30 | 25/41, 61.0% (45.7 to 74.3) | 5 | 15 | 34 | 19/34, 55.9% (39.5 to 71.1) | 0 |

## Paired tests

Exact two-sided McNemar on the per-question outcomes.

| a | b | measure | n | only a | only b | p |
|---|---|---|---:|---:|---:|---:|
| leks30 | leks | q1 hit@1 | 695 | 5 | 0 | 0.0625 |
| leks30 | leks | q1 hit@5 | 695 | 0 | 0 | 1 |
| leks30 | leks | q2 current above replaced | 41 | 0 | 5 | 0.0625 |
| leks30 | leks | q2c current above replaced | 41 | 0 | 7 | 0.0156 |
| leks30 | leks | q2 pointer, pairs both list | 40 | 0 | 8 | 0.00781 |
| leks30 | leks | q2c pointer, pairs both list | 34 | 0 | 8 | 0.00781 |
| bm25 | leks | q1 hit@1 | 695 | 34 | 95 | 7.34e-08 |
| bm25 | leks | q1 hit@5 | 695 | 3 | 15 | 0.00754 |
| bm25 | leks | q2 current above replaced | 41 | 0 | 25 | 5.96e-08 |
| bm25 | leks | q2c current above replaced | 41 | 0 | 27 | 1.49e-08 |
| bm25 | leks30 | q1 hit@1 | 695 | 31 | 97 | 4.16e-09 |
| bm25 | leks30 | q1 hit@5 | 695 | 3 | 15 | 0.00754 |
| bm25 | leks30 | q2 current above replaced | 41 | 1 | 21 | 1.1e-05 |
| bm25 | leks30 | q2c current above replaced | 41 | 1 | 21 | 1.1e-05 |

## How each index reads the relations

| system | edges | pairs covered | old PEPs covered | edges that match no pair |
|---|---:|---|---|---:|
| leks | 36 | 33 of 41 | 30 of 36 | 6 |
| leks30 | 30 | 25 of 41 | 24 of 36 | 6 |

Pairs (old, new) with no edge from the old PEP to one of its successors:

- leks: 245 to 443, 245 to 3124, 246 to 443, 246 to 3124, 397 to 773, 486 to 773, 543 to 748, 3124 to 443
- leks30: 245 to 443, 245 to 3124, 246 to 443, 246 to 3124, 247 to 452, 381 to 449, 381 to 464, 382 to 402, 397 to 773, 422 to 487, 486 to 773, 543 to 748, 563 to 649, 563 to 749, 724 to 742, 3124 to 443
