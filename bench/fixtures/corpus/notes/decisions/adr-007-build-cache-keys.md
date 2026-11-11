---
title: ADR-007 Build cache keys
status: superseded
date: 2025-07-14
---
# ADR-007 Build cache keys

## Decision
The local build cache is keyed by file path plus modification time.

## Problem found later
A fresh checkout on a CI runner gives every file a new modification time, so the hit rate on CI was zero.

Superseded by [[adr-008-remote-build-cache]].
