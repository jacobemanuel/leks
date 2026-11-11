---
title: ADR-001 Tile size 256
status: superseded
superseded_by: adr-006-tile-size-512
date: 2025-03-10
---
# ADR-001 Tile size 256

## Context
The first prototype needs one tile size for the whole pyramid. The low-end test devices have 2 GiB of memory.

## Decision
Tiles are 256 by 256 pixels with four bytes per pixel before compression. The pyramid has 12 zoom levels.

## Consequences
Memory per tile stays small, 256 KiB raw. The request count grows fast when the viewport is large.

Superseded by [[adr-006-tile-size-512]].
