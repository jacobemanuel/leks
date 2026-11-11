---
title: Frame graph overview
status: active
date: 2025-08-20
---
# Frame graph overview

The frame graph describes one frame as passes and resources. A pass reads some resources and writes others.

The default graph runs five passes: depth prepass, tile pass, label pass, overlay pass and present. A pass whose outputs nobody reads is removed before recording. The remaining passes are ordered with a topological sort, so each pass runs after every pass it reads from.

Adding a pass means declaring its inputs and outputs, nothing else.
