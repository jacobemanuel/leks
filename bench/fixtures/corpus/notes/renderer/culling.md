---
title: Viewport culling
status: active
date: 2025-11-18
---
# Viewport culling

Tiles outside the viewport are not drawn. A margin of one tile on each side stays, so a quick pan never shows holes. The margin is the setting `cull_margin_tiles`.

Culling runs before the frame graph is recorded, which keeps the pass list short.
