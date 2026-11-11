---
title: Reproducible builds
status: active
date: 2025-09-30
---
# Reproducible builds

Toolchain versions are pinned in a lock file. File lists are sorted before packing and timestamps inside bundles are set to zero.

Two builds of the same commit must give the same bundle hashes. A nightly job compares them and opens a ticket on a difference.
