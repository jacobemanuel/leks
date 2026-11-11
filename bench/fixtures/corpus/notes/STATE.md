---
title: Project state
status: current
date: 2026-04-28
---
# Project state

Updated 2026-04-28. This is the entry note: read it first, then follow the links.

## Focus now
- The renderer is moving scene by scene to the larger tile size, see [[adr-006-tile-size-512]]. About 70 percent of scenes are done.
- The remote build cache covers 60 percent of CI jobs, see [[adr-008-remote-build-cache]].
- The ingest backpressure fix INGEST-517 is in review.
- The docs site moves to the third site template. Target date is 2026-05-12.

## Blocked
- The upload ring buffer resize waits for a driver update on the two test machines.

## Next milestone
Milestone M5 ships on 2026-06-15: the larger tiles everywhere, the remote cache on every job, nightly docs from the new template.
