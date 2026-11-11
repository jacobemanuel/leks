---
title: ADR-006 Tile size 512
status: accepted
date: 2026-01-20
---
# ADR-006 Tile size 512

## Context
Per-request overhead dominates on fast links. [[adr-001-tile-size-256]] picked the small size for memory reasons that no longer hold on the reference devices.

## Decision
Tiles are 512 by 512 pixels. The request count for one screen drops by a factor of four. Raw memory per tile rises to 1 MiB. The switch is rolled out scene by scene behind the flag `tile_size`.

## Effect on other decisions
The cache capacity stays in megabytes ([[adr-002-lru-tile-cache]]), so nothing changes there. This note supersedes ADR-001.
