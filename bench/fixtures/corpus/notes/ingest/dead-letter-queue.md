---
title: Dead letter queue
status: active
date: 2025-06-18
---
# Dead letter queue

Records that fail validation go to the dead letter queue together with an error code. The queue is reviewed once a week.

A record stays in the queue for at most 30 days. Replaying a record needs a fix of the source or of the validator first.
