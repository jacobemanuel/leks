---
name: unity-build-check
description: Build a Unity player from a clean state and check it against the project's budgets for build time, size and frame time, with a smoke test on the target device or a stand-in. Use it before a release build, after a change of packages or render settings, and when a build fails.
---

# Unity build check

## Steps

1. `search` for the build settings, the target platforms and the budgets, and `read` the decisions that set them. Call `tried` with the change you are about to test, so a known build problem is not hit twice.
2. Write down the Unity version, the package versions from `Packages/packages-lock.json`, the target, the scenes in the build and the build options. A build only compares with another one when these match.
3. Build from a clean state: close the editor, remove the `Library` folder or use a fresh clone, then build in batch mode with `-batchmode -quit -logFile` and a build method you keep in the project. Keep the log and the exit code.
4. Read the end of the log for errors and warnings. Record the build time and the size of the build and its largest assets from the build report.
5. Run a smoke test on the build: it starts, loads each scene in the build, and reaches its first frame without errors in the player log. Run it on the target device when you have it, and say so when you used a stand-in.
6. Compare build size and frame time with the budgets using the perf-budget skill, and write the result as a dated note under `results/`, linked from the state note.

## Done when

- The build ran from a clean state, and the report has its versions, exit code, build time, size and smoke test result.
- Each budget line has a verdict with its numbers, and the result note is linked.
