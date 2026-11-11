---
title: ADR-004 Persistent shader cache
status: accepted
date: 2025-05-06
---
# ADR-004 Persistent shader cache

## Decision
Compiled shaders are cached on disk. The key is the sha256 of the shader source joined with the feature flag bitmask. The cache folder is capped at 200 MiB and the stalest entries go first.

## Numbers
A cold start without the cache compiles for 3.1 seconds. A warm start takes 0.2 seconds.

## Rejected
Shipping precompiled binaries. A driver update invalidates them, and the failure shows up as a black screen on some machines.
