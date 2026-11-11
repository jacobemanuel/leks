---
title: Asset bundling
status: active
date: 2025-07-02
---
# Asset bundling

Assets are packed into bundles of 64 MiB by the function `bundle_assets` in the build script. A manifest lists every file with its content hash.

A bundle is rebuilt only when the hash of one of its members changes. Old bundles are pruned from the cache after 30 days.
