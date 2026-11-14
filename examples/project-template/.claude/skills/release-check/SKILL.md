---
name: release-check
description: Check a release before it is tagged. Version strings agree, the changelog and docs match the code, the package builds and passes its tests from a clean checkout, and no private data is in the tree. Use it before any tag or publish.
---

# Release check

## Steps

1. `search` for the release decision and the state note, and `read` the project's release checklist if there is one.
2. Check that every version string agrees: package metadata, the version in the code, the changelog and the docs.
3. Run the full test suite on a clean checkout. Record the counts and compare the failures with the last release.
4. Build the package, install it into a fresh environment and run its command line or import it.
5. Scan the tree for private data: absolute home paths, secrets and tokens, internal host names, files that should not ship.
6. Read the changelog against the commits since the last tag, so every change users can see is listed.
7. Report go or no-go with the evidence for each check. The tag and the push are the owner's call.

## Done when

- Each check has a result with its evidence, and the report ends with go or no-go.
