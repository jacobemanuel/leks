---
title: Ingest throughput, March 2026
status: final
date: 2026-03-19
---
# Ingest throughput, March 2026

Eight workers processed a replayed day of provider data.

- Throughput: 18,400 records per second.
- Batch latency, 99th percentile: 2.2 seconds.
- Backpressure kicked in once, when the queue depth passed 10,000 records, and cleared after 40 seconds.

No record was lost. 212 records went to the dead letter queue, all with a missing field.
