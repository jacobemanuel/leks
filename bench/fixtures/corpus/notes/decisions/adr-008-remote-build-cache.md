---
title: ADR-008 Remote build cache
status: accepted
date: 2026-02-09
---
# ADR-008 Remote build cache

## Decision
The build cache is keyed by the content hash of all inputs of a step. It is shared by every CI job and stored in object storage with a 30 day expiry. The local cache stays as the first level.

## Rollout
Staged by job group. See [[STATE]] for how far it got. Replaces [[adr-007-build-cache-keys]].
