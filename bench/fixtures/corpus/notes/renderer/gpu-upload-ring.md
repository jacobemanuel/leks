---
title: GPU upload ring buffer
status: active
date: 2025-10-14
---
# GPU upload ring buffer

Decoded tiles reach the GPU through a ring buffer of 32 MiB. The setting `max_inflight_uploads` defaults to 6.

When no slot is free the main thread does not wait. The upload is tried again on the next frame. A resize of the ring is waiting for a driver update, see [[STATE]].
