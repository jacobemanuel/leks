---
title: ADR-003 Texture compression
status: accepted
date: 2025-04-15
---
# ADR-003 Texture compression

## Decision
Colour tiles use block compression at 4 bits per pixel. Data tiles that carry normals use 8 bits per pixel. The encoder runs in the asset build, never on the device.

## Reason
Encoding on the device took about 40 ms per tile, which broke the frame budget. Offline encoding costs build time only.

## Rejected
Raw RGBA (four times the memory) and the palette format (bad on smooth gradients, see the early notes in the archive).
