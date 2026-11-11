---
title: ADR-002 Least recently used tile cache
status: accepted
date: 2025-03-24
---
# ADR-002 Least recently used tile cache

## Decision
The tile cache drops the least recently used tile first. Capacity is set in megabytes, default 256 MiB, and not in a tile count, so the setting survives a change of tile size.

Tiles inside the current viewport are pinned and never dropped. When an insert would go over capacity, the cache removes old tiles until 10 percent headroom is free.

## Rejected
- Random replacement: lower hit rate in every replay.
- First in, first out: ignores that the user pans back to places just left.

See also [[adr-006-tile-size-512]] and the cache replay study in the docs folder.
