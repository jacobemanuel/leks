---
title: Ingest backpressure
status: active
date: 2026-04-10
---
# Ingest backpressure

The worker stops fetching when the queue holds more than 10000 records and resumes below 4000.

Ticket INGEST-517 tracks a bug: after a worker restart the pause was never lifted, so the queue drained and stayed idle. The fix clears the pause flag on start.
