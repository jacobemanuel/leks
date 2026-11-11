---
title: Flaky tests
status: active
date: 2025-10-22
---
# Flaky tests

A test that fails and then passes on the same commit goes to the quarantine list. Retries are limited to 2 per test.

The list is reviewed every Friday. A test may stay quarantined for at most 14 days, then it is fixed or deleted.
