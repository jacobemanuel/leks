---
title: Mip level generation
status: active
date: 2025-12-02
---
# Mip level generation

Smaller levels are built at bundle time, not at run time. Each level averages blocks of 2 by 2 pixels from the level above. Normal data uses a variant that renormalises each vector after averaging.

A small C helper builds all levels of one tile in a single pass over the pixels.
