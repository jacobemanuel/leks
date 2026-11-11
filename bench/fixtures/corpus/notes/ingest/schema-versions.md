---
title: Record schema versions
status: active
date: 2025-09-08
---
# Record schema versions

Schema version 2 adds a required field `source_id`. Records in version 1 are migrated on read by `migrate_record_v1_to_v2`.

Version 1 stays accepted until the end of 2026. After that the migration code is removed.
