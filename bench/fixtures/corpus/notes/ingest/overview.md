---
title: Ingest overview
status: active
date: 2025-05-20
---
# Ingest overview

Data arrives as daily batches from external providers. The pipeline has four stages: fetch, validate, normalize and write.

The output is written as partitions by day. Each stage can be restarted alone. Failures after validation never touch the raw batch.
