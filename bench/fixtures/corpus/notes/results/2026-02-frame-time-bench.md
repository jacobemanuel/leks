---
title: Frame time measurements, February 2026
status: final
date: 2026-02-27
---
# Frame time measurements, February 2026

Method: 200 recorded camera paths replayed on the reference laptop, each path 60 seconds long.

| tile size | median frame time | 95th percentile |
|---|---|---|
| 256 | 11.9 ms | 16.2 ms |
| 512 | 9.8 ms | 13.4 ms |

The larger tiles win mostly because fewer requests compete with drawing. Worst frames came from the shader cold start, not from tiles.
