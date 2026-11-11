---
title: Tile request order
status: active
date: 2025-10-01
---
# Tile request order

Tiles are requested in a spiral that starts at the viewport centre, so the middle of the screen fills first.

At most 8 requests are in flight at once. The limit is the setting `max_inflight_requests`. Requests for tiles that already left the viewport are cancelled before they start.
