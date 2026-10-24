# Security and recovery

## What is protected

- **Source files:** a registered source file is never edited in place. Corrections are new source files plus edits of the compiled pages.
- **Pages and claims:** curated knowledge. It is versioned in git, and generated files are rebuilt, never edited.
- **Folders outside the knowledge base** that agents must not touch.

## What is out of scope

- A process of the same user that deliberately removes every layer.
- Loss of the whole disk; an off-disk backup is still needed.
- What an AI coding agent does with text it has read, including where its model runs. LEKS controls which files an agent may change, not how the agent handles what it reads.
- The network behaviour of other applications in the workflow (Obsidian, Unity, Blender, the code editor).

## Layers

| Layer | How it works | Verified by |
|---|---|---|
| File-system protection | Every registered source file carries the macOS user-immutable flag, on by default. Every agent session start re-applies a missing flag and reports the state. A missing flag fails the integrity check and `doctor`. The tools lift the flag only for their own authorised operation (register, move, restore). | On a disposable copy of the real data, each of the six writes tried was refused by the file system: append, truncate, delete, rename, hard link and replace. A deployment rehearsal then ran normal work, a new source file, a move and a controlled restore under the flags. |
| Integrity check and stop check | The full integrity check hashes every source file; the fast one trusts unchanged size and time stamps. Both validate links, anchors, metadata and indexes. When an agent stops, the stop check runs the fast one and blocks the turn once if the knowledge base is inconsistent. | A mutation suite of 39 corruption classes; an in-place edit of a source file caught on real data |
| Evidence store and restore | Exact, hash-addressed copies of every source file and of its earlier revisions, outside the knowledge base, with registry checkpoints. A restore keeps the damaged bytes for inspection. | 13 restore scenarios in tests. On real data, deleted and modified source files were restored byte for byte; a silently corrupted copy is found by a deep verify and never served. |
| Journal and incidents | Evidence events form a hash chain whose head is copied outside on every append. Damage opens an incident that stays visible at every session start until a person resolves it. | An edited entry and a consistently rewritten history are both detected. |
| Atomic writes | The registry, claims and other shared files are written through a temporary file, synced and renamed, under a lock | Interrupted-write tests |
| Pre-tool check | A hook reads each agent tool call before it runs and denies changes to source files and generated files, and any access to protected folders. It denies when it cannot decide, and when it fails or times out. It is a heuristic filter, not a security boundary. | Regression tests for every form reported by an independent review and two later probes |
| Model helper sandbox | R4 and R5 run in the model helper, a separate process that a macOS sandbox denies network access. Their packages and model files are pinned by SHA-256 and installed without dependency resolution. | A probe inside the sandbox: TCP connections, even to the local machine, and writes outside its folder are refused; the same probe outside the sandbox succeeds. |

The pre-tool check's rules and test cases are not published.

## Recovery rehearsals

**A fault rehearsal on a disposable copy of the real knowledge base** (10 of 10 scenarios passed). Each scenario below was detected and refused, restored, or reported as designed:
- a same-size in-place edit of a source file;
- a deleted source file;
- an edited journal entry, and a consistently rewritten journal;
- a damaged registry, a damaged claims file and a corrupted store object;
- an interrupted write;
- writes against flagged source files.

**The deployment of 2.0 into the real knowledge base**, rehearsed on full copies of it before it ran for real:
- Each rehearsal integrated 2.0 and validated the result.
- Each then rolled back and verified that the files and the developer's uncommitted work were byte-identical to the backup.
- The real deployment kept a verified backup and the same rollback path.
