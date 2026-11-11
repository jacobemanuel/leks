---
title: ADR-005 Ingest checkpoints
status: accepted
date: 2025-06-02
---
# ADR-005 Ingest checkpoints

## Decision
The ingest worker writes a checkpoint after every 5000 records. After a crash it replays from the last checkpoint. Writes are idempotent and keyed by record id, so a replayed record does no harm.

## Rejected
Exactly-once transport between the stages. It needs a coordinator that we do not want to run.
