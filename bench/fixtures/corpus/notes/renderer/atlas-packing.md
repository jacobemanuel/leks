---
title: Atlas packing
status: active
date: 2025-11-04
---
# Atlas packing

Small images and glyphs share atlas pages of 2048 by 2048 pixels. Rectangles are packed in shelves, row by row.

Two pixels of padding separate neighbours so colours do not bleed under filtering. A page counts as full at 92 percent fill, then a new page opens.
