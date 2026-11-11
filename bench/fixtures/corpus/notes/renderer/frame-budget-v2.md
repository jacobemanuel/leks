---
title: Frame budget v2
status: current
date: 2026-03-03
---
# Frame budget v2

Target is 90 Hz, so the budget per frame is 11.1 ms.

- Tile upload: 3 ms, spread over several frames
- Drawing: 6 ms
- Slack: 2.1 ms

The old split is in the v1 note. Uploads moved to a ring buffer, see [[gpu-upload-ring]].
