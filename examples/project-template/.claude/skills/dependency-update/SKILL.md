---
name: dependency-update
description: Update one dependency, package or tool version on purpose. Read its changelog, check the record for earlier attempts, change one version at a time, run the tests and record the new pin. Use it for any version bump, including Unity packages, Blender add-ons, build tools and libraries.
---

# Dependency update

## Steps

1. Call `tried` with the update in one line, and `search` for the dependency's name and the decision that pinned its current version. `read` what you find, including failed updates.
2. Read the dependency's changelog between the pinned version and the target version. List the breaking changes, the deprecations and the fixes the project needs.
3. Change one version at a time, in the lock file as well as the manifest. Do not mix an update with other changes.
4. Run the full test suite and the build. Record the counts and compare them with the run before the update; a new failure belongs to this update until shown otherwise.
5. When the dependency affects frame time, memory or build size, measure with the perf-budget skill.
6. Record the new pin and the reason with the leks-decide skill, or with supersede-decision when a decision fixed the old version.

## Done when

- Only the one dependency changed, and the test and build results before and after are in the report.
- The record names the new version, the reason, and the decision it replaces if there was one.
