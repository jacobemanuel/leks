---
title: CI pipeline
status: active
date: 2025-06-20
---
# CI pipeline

A run has four stages: lint, compile, bundle and test. Compile and test run in parallel on separate runners; there are 6 runners in total.

A full run takes about 14 minutes at the median. The slowest stage is the bundle step, see [[asset-bundling]].
