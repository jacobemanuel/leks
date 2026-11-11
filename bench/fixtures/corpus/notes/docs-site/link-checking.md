---
title: Link checking
status: active
date: 2025-05-14
---
# Link checking

The link checker runs on every build, using `find_broken_links`. A broken internal link fails the build. Broken external links only warn and are retried 3 times.
