# Command line

Every command runs as `leks <command>`, or as `python3 -m leks <command>` with the interpreter that has LEKS installed. Most commands print JSON on stdout. `search` and `tried` print text meant for an agent, and `search --json` prints data. `check` reports on stderr and leaves stdout empty, except for a Copilot stop payload, where it prints one JSON line ([below](#leks-check)). Progress and errors go to stderr, so stdout stays clean for a script.

Setup, step by step, is in [install.md](install.md). The config keys are in [configuration.md](configuration.md), and what happens inside each step is in [how-it-works.md](how-it-works.md).

## Global flags

Both flags may stand anywhere on the line, before or after the command, as `--profile P` or `--profile=P`.

| Flag | Effect |
|---|---|
| `--profile P` | use profile `P` for this command. It sets `LEKS_PROFILE`, so processes the command starts get it too. |
| `--config PATH` | read this config file. It sets `LEKS_CONFIG`. A path that does not exist is an error; there is no fall back to the other places. |

Without `--config`, the config is looked up in `LEKS_CONFIG`, then `$LEKS_HOME/config.json`, then `~/.leks/config.json` ([configuration.md](configuration.md#where-the-config-is-found)).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | the command ran. A search that finds nothing still exits 0. |
| `1` | an error. stderr has `leks: <message>`. |
| `2` | no command, or a flag or argument the command does not take. The usage text goes to stderr. |
| `130` | stopped with Ctrl-C |

Some commands give 1 or 2 a meaning of their own; their sections say so. Every `leks hook` command exits 0.

Config and model problems print their own message, such as `leks: no config: run leks init`, `leks: unknown profile: notes` or `leks: huggingface_hub is not installed: pip install huggingface_hub`. Any other error prints only its type and the command, such as `leks: OSError (build)`.

## Commands

| Command | What it does |
|---|---|
| [`init`](#leks-init) | writes a starter config |
| [`config`](#leks-config) | checks or shows the config |
| [`build`](#leks-build) | brings the index up to date |
| [`search`](#leks-search) | searches the active profile |
| [`read`](#leks-read) | returns cited lines, pages or paragraphs |
| [`locate`](#leks-locate) | finds files and code definitions by name |
| [`tried`](#leks-tried) | checks whether a proposal was tried or decided before |
| [`neighbours`](#leks-neighbours) | lists the notes to bring in line after an edit |
| [`revisit`](#leks-revisit) | lists the revisit dates and conditions of decisions and notes |
| [`state`](#leks-state) | lists the current state notes |
| [`status`](#leks-status) | reports index size, models and what search runs on |
| [`serve`](#leks-serve) | runs the MCP server on stdio |
| [`embed`](#leks-embed) | fills the vector store |
| [`modeld`](#leks-modeld) | starts, stops or checks the model daemon |
| [`models`](#leks-models) | downloads or checks the model weights |
| [`ocr`](#leks-ocr) | compiles the OCR helper |
| [`check`](#leks-check) | checks the notes that changed, for the end-of-turn hook |
| [`hook`](#leks-hook) | entry points for host hooks |
| [`doctor`](#leks-doctor) | reports what this install can do |

## leks init

```bash
leks init
leks init --home ~/work/leks-home
```

Writes a starter config and creates its folder. The file goes to `<DIR>/config.json` with `--home DIR`, else to `$LEKS_HOME/config.json`, else to `~/.leks/config.json`. With `--home`, the `home` field of the new config is set to that folder.

Prints `{"config": "<path>", "created": true}`. When the file already exists, it is left untouched and the answer is `"created": false`, still with exit 0.

The starter config has example roots under `~/projects/example`. Point them at your own folders before the next step ([configuration.md](configuration.md#create-a-config-leks-init)).

## leks config

```bash
leks config check
leks config show
```

`check` loads and validates the config and checks that the active profile exists. It prints:

```json
{"ok": true, "config": "~/.leks/config.json", "roots": 4, "profiles": ["architect", "default", "orchestrator"], "warnings": ["misc: folder not found"]}
```

A root whose folder is missing only adds a warning. Any validation problem exits 1 with its message ([configuration.md](configuration.md#where-the-config-is-found) lists them).

`show` prints the config as LEKS reads it: every default filled in and every path absolute. The built-in `default` profile is left out.

## leks build

```bash
leks build
leks build --force
```

Walks every root, indexes what changed into `<home>/index/master.sqlite`, then rebuilds `<home>/index/<profile>/search.sqlite` for each profile whose inputs changed, `default` included. `--force` reads every file again and rebuilds every profile.

Progress goes to stderr: one `update: seen N, indexed N, unchanged N, removed N, skipped N, errors N, ocr N, S.Ss` line and one `profile P: docs N, chunks N, distinct inputs N, symbols N, names N, newer N, S.Ss` line per rebuilt profile. stdout gets the same counts as JSON, with `{"profile": P, "unchanged": true}` for a profile that was left as it was.

When no configured root exists or can be read, the build stops before it touches the index:

```text
leks: nothing to build: none of the configured roots exists or is readable (notes, code); fix the paths in the config, leks config check shows which
```

Exit 1. When another build or a background update holds `<home>/run/update.lock`, the command returns at once with `{"update": {"skipped": "locked"}, "profiles": {}}` and exit 0.

## leks search

```bash
leks search what did we decide about the cache
leks search "tile budget" --k 8
leks search "tile budget" --json
leks search "tile budget" --words-only
```

| Argument | Default | Effect |
|---|---|---|
| `QUERY...` | required | the words to search for. They are joined with spaces, so quotes are optional. |
| `--k N` | `5` | number of results, clamped to 1 to 12 |
| `--json` | off | print data instead of text ([how-it-works.md](how-it-works.md#json)) |
| `--words-only` | off | skip both models; the daemon is not started |

Before it searches, the command starts the model daemon when a model is configured and waits until both models answer, so the search below meets them loaded. When another start is under way it waits up to 20 seconds for it. When the daemon cannot be started, the search runs on words, and the lanes line says `model daemon not started (<why>)` with one of these:

- `model backend not installed, run: pip install -e ".[models]" in the LEKS clone`
- `model weights not found, run: leks models fetch`
- `another start is under way, search again in a minute`
- `it did not come up, run leks modeld serve to see why`

The text output is one block per result and the lanes line last ([how-it-works.md](how-it-works.md#what-a-search-returns)).

## leks read

```bash
leks read notes/plan.md 12 30
leks read docs/spec.pdf 3
```

Takes a path as search cites it, a start (default 1) and an optional end, and prints JSON.

- For a note or code file the numbers are lines. It reads the live file and returns up to 120 lines, each cut at 1500 characters and prefixed with its number and a tab. Keys: `path`, `status`, `date`, `lines_total`, `from`, `to`, `text`.
- For a PDF, `.docx` or image the numbers are pages, paragraphs or images, as the citation gives them. It returns the indexed text, up to 5 pages or 120 paragraphs, and stops before 16000 characters. Keys: `path`, `status`, `date`, `unit`, `units_total`, `from`, `to`, `text`.

`warning` appears when the file changed since the last build. A document may also carry `caution` with its OCR label, and a note whose live file cannot be reached gets `note`, which says the indexed text came back instead. A path the profile does not have gives `{"error": "not visible in this profile, or not indexed"}`, still with exit 0.

## leks locate

```bash
leks locate tile cost
leks locate budget ext:pdf,docx in:specs
leks locate symbol:computeTileCost
leks locate class:image size:>10m n:50
```

Finds files by name and folder words. It reads no file content. Filters go inside the query; [how-it-works.md](how-it-works.md#the-other-tools) lists them. It returns 15 rows unless `n:` asks for another count.

The JSON has `files` (each with `path`, `size`, `root`, `class`, `sha`, `kind`, `status`, `date`), `total`, `matched_by`, `by_folder` (file count and bytes per folder), `symbols` and `rule`.

## leks tried

```bash
leks tried switch the reranker off for short queries
```

Searches decisions, results, state notes and notes for an earlier answer to the proposal and prints text: up to 5 rows, or a line that starts with `nothing certain` followed by the closest rows. It starts the daemon the way `search` does. The rules are in [how-it-works.md](how-it-works.md#the-other-tools).

## leks neighbours

```bash
leks neighbours notes/plan.md
leks neighbours notes/plan.md --was plan-before.md --n 20
leks neighbours src/cache.py --json
```

Lists the notes to bring in line after an edit of a note or code file: notes that still hold numbers or sentences the edit removed, the newer and older versions in its supersession chain, notes that link to it, files it links to, and the files closest to it in meaning (in words without vectors). The rules are in [how-it-works.md](how-it-works.md#neighbours). It reads the index and the files and writes nothing. It starts no model: the meaning group uses the stored vectors.

| Argument | Default | Effect |
|---|---|---|
| `PATH` | required | the edited file, as search cites it or as a path on disk under a root |
| `--was FILE` | off | a file with the text the note had before the edit; up to 2 MiB of it is read. Without it the old text is the indexed one, when the file changed since the last build. |
| `--n N` | `10` | how many notes, clamped to 1 to 30 |
| `--json` | off | print data instead of text |

The text starts with `neighbours of <path>`, plus `, changed since indexed` when the file differs from the index, and a `removed here:` line with the facts it follows. Then one numbered row per note, `path:line | status | date | why`, and a closing line on what the answer compared. Here a note went from 512 MiB to 256 MiB after the last build, in a small folder with no vectors:

```text
neighbours of notes/cache.md, changed since indexed
removed here: 512 MiB
1. notes/decisions/log.md:7 | current | 2026-11-13 | still holds what this note removed: 512 MiB; close in words (line 10)
2. notes/plan.md:6 | current | 2026-11-05 | still holds what this note removed: 512 MiB; links to this note; close in words (line 1)
3. notes/cache-old.md:3 | superseded | 2026-09-14 | older version, this note replaces it; links to this note; close in words (line 1)
4. notes/index.md:4 | unknown | 2026-11-13 | links to this note; close in words (line 1)
Removed text: compared with the indexed text. Close notes by words: this note has no vectors. A note marked record, superseded or historical-record keeps what held at its time: leave its text as it is.
```

The JSON has `path`, `changed`, `removed`, `neighbours` (each with `path`, `line`, `status`, `date` and `why`) and `note`. A path the profile does not have, a document or an image gives `error` instead, still with exit 0.

## leks revisit

```bash
leks revisit
leks revisit --due
leks revisit --by 2027-01-31 --json
```

Lists every `Revisit:` line in the notes of the active profile ([how-it-works.md](how-it-works.md#revisit-lines)): the lines whose date has come oldest first, then the dates ahead, then the conditions. Notes marked superseded or in a history folder are left out.

| Argument | Default | Effect |
|---|---|---|
| `--due` | off | only the lines whose date has come |
| `--by YYYY-MM-DD` | today | count a date as due when it is on or before this day |
| `--json` | off | print data instead of text |

Each row reads `state: path:line | date or condition | heading above it | kind, status`, where the state is `due`, `ahead` or `when` (a condition):

```text
revisit lines in profile default, today 2026-11-13: 1 due, 1 ahead, 1 on a condition
due: notes/decisions/log.md:8 | 2026-11-01 | Cache size | decision, current
ahead: notes/decisions/log.md:13 | 2027-02-01 | Reranker off for short queries | decision, current
when: notes/decisions/log.md:18 | when a second machine joins | Second machine | decision, current
```

The JSON has `profile`, `today` and `revisit`, a list with `path`, `line`, `state`, `date` (or `null`), `text`, `title`, `kind` and `status`. With no index for the profile yet it exits 1 with `leks: no index for profile <name> yet, run leks build`, and a `--by` that is not a date gives `leks: --by takes a date as YYYY-MM-DD`.

## leks state

```bash
leks state
```

Prints `{"state_notes": [{"path": ..., "date": ...}]}`: up to 12 state notes of the active profile that are not history, newest date at the top.

## leks status

```bash
leks status
```

Prints the health of the active profile as JSON. It never talks to the model daemon, so a daemon that is down because nothing asked for it is not reported as a problem.

| Key | Meaning |
|---|---|
| `version`, `profile` | the LEKS version and the active profile |
| `search` | one sentence: what search runs on now, and the command that fixes a missing lane |
| `lanes` | the lanes line a search would print now |
| `docs`, `chunks`, `documents`, `images`, `symbols`, `names` | counts in the profile database |
| `embedder`, `reranker` | the configured model keys, or `null` |
| `backend` | the model backend from the config |
| `vectors`, `vector_mode` | how many vectors are loaded, and `meaning` or `words` |
| `coverage` | the share of passages that have a vector |
| `why_no_meaning`, `why_no_reranker` | the fixed reason a lane is off, or `null` |
| `pdf_text`, `ocr` | whether `pypdf` and the OCR helper can run |
| `sandbox` | `on`, `off`, `off (<why>)` or `unknown` |
| `built_utc` | when the profile database was built |
| `by_root` | files per root |

`search` reads like `search runs on words and meaning with the reranker`, or `search runs on words only: model weights not found. Fix: leks models fetch`.

## leks serve

```bash
LEKS_PROFILE=architect leks serve
```

Runs the MCP server on stdin and stdout. The host starts it; by hand it just waits for JSON-RPC lines. The profile comes from `LEKS_PROFILE`, or from `--profile`, which sets it. The tools are in [how-it-works.md](how-it-works.md#the-mcp-server) and the host files in [hosts.md](hosts.md).

## leks embed

```bash
leks embed
leks embed --profile architect
```

Embeds every distinct passage that has no vector yet and writes the vector files of each profile. With `--profile P` on the command line it covers only that profile; `LEKS_PROFILE` does not limit it. It starts the model daemon and waits for it, and when it finishes it tells the daemon to exit. A run that stops can be started again and skips what is stored.

Output lines, on stdout unless noted:

| Line | Meaning |
|---|---|
| `model daemon not started: <word>` | on stderr: the daemon could not be started ([`leks modeld start`](#leks-modeld) lists the words); the run goes on and stops when the daemon does not answer |
| `refused: no embedder configured` | `models.embedder` is `null`. Exit 2. |
| `refused: numpy not installed` | install the `models` extra. Exit 2. |
| `refused: unknown profile` | `--profile` names no profile of the config. Exit 2. |
| `refused: no index yet, run a build first` | run `leks build`. Exit 2. |
| `refused: store was made by another revision of this model` | the vector store does not fit the configured embedder. Exit 2. |
| `pruned N` | stored vectors that no passage uses any more, removed |
| `to embed N of M` | passages without a vector, of all passages |
| `embedded N of M per sec X` | progress |
| `STOPPED at I of N the daemon does not answer, run again to resume` | the daemon went away |
| `profile P covered C of N` | the vector file of profile `P` was written |
| `profile P vectors not written` | it could not be written |
| `DONE <key> <count>` | everything has a vector. Exit 0. |
| `INCOMPLETE <key> <have> of <total>` | some passages still lack one. Exit 1. |

## leks modeld

```bash
leks modeld start
leks modeld ping
leks modeld stop
leks modeld serve
```

| Action | Effect |
|---|---|
| `start` | starts the daemon in the background, under the sandbox when `daemon.sandbox` allows it, and returns at once. Prints `{"modeld": "<word>"}`. |
| `ping` | prints `{"modeld": "up"}` with exit 0, or `{"modeld": "down"}` with exit 1 |
| `stop` | asks a running daemon to exit and prints `{"modeld": "stop sent"}`, or `{"modeld": "not running"}` |
| `serve` | runs the daemon in this terminal, in the foreground and without the sandbox, so its errors show. It ends on Ctrl-C, on `stop` or after `daemon.idle_exit` quiet seconds. When a daemon already answers on the socket, it returns at once. |

The words `start` can print:

| Word | Meaning |
|---|---|
| `up` | a daemon already answers |
| `started` | a new daemon was launched; a follow-up loads both models |
| `no models` | neither model is configured |
| `no backend` | the `models` extra is not installed |
| `no weights` | no manifest for a configured model: run `leks models fetch` |
| `locked` | another start is under way |
| `refused` | it could not be launched, for example `daemon.sandbox` is `on` and `sandbox-exec` is missing |

The daemon and its clients talk over a Unix socket at the socket path, which only the user can open, wherever Python has Unix sockets with descriptor passing: macOS and Linux. On Windows they talk over TCP on 127.0.0.1, on a port the system picks. The daemon then writes the port and a random token into `modeld.port` next to the socket path, a file only the user may read (mode 0600 on macOS and Linux; on Windows the access rules of the LEKS home folder do that job, which in a user profile admit the user, the system and the administrators), and answers only requests that carry the token. `LEKS_MODELD_TRANSPORT=tcp` takes the TCP path on any system; `leks doctor` shows which one is in use.

## leks models

```bash
leks models fetch
leks models check
```

`fetch` downloads the pinned revision of the configured embedder and reranker from Hugging Face into `<models.weights_dir>/<key>`, `<home>/models/<key>` by default. It skips the file formats LEKS never loads (`*.bin`, `*.onnx`, `*.h5`, `*.msgpack`, `*.ot`, `*.gguf` and the `onnx`, `openvino`, `coreml`, `flax`, `tf` and `.mlpackage` folders), checks that the folder has the layout the config expects, and writes `manifest.json` with the size and SHA-256 of every file. It needs the `models` extra and is the only command that uses the network. A model set to `null` is skipped. It prints `{"<key>": {"role": "embedder", "files": N, "bytes": N}, ...}`.

When `huggingface_hub` is missing or the download has another layout than the config expects, the error says so, for example `leks: huggingface_hub is not installed: pip install huggingface_hub`.

`check` compares each model folder with its manifest: the same files, the same sizes, and the same hash for every file up to 64 MiB. It prints `{"<key>": {"role": ..., "ok": true, "why": ""}, ...}`, and exits 0 even when a check fails. Typical `why` texts are `no manifest: run leks models fetch`, `file set differs from the manifest: ...`, `size differs from the manifest: <file>` and `hash differs from the manifest: <file>`.

## leks doctor

```bash
leks doctor
```

Reports what this install can do, as one JSON object. It reads files and starts nothing. Exit 0 when the config loads, 1 when it does not.

| Key | Value |
|---|---|
| `leks`, `python`, `system` | the LEKS version, the Python version and `sys.platform` |
| `model_transport` | `unix` or `tcp`, how the model daemon and its clients talk ([leks modeld](#leks-modeld)) |
| `pdf` | `pypdf found`, or that PDFs are skipped |
| `numpy` | whether numpy is installed |
| `model_backend` | whether torch, transformers and safetensors are all installed |
| `sandbox` | `sandbox-exec`, or why the model daemon runs without the sandbox |
| `config` | `{"ok": true, "path": ..., "roots": [...], "profiles": [...]}`, or `{"ok": false, "why": ...}` |
| `index` | whether the master index and the database of each profile exist |
| `models` | for each configured model, `weights found` or `no weights, run leks models fetch`; `none configured, search runs on words` without models |

When the config does not load, the keys after `config` are left out.

## leks ocr

```bash
leks ocr build
```

Compiles the Swift helper that reads scanned PDFs and images through macOS Vision, with `swiftc -O`, into `ocr.helper` or `<home>/bin/extract_text`. The compiler gets 600 seconds. It prints one line and exits 0 when the helper was built, 1 otherwise.

| Line | Meaning |
|---|---|
| `built the OCR helper` | done; run `leks build` so scanned files are read |
| `the OCR helper needs macOS` | this system is not macOS |
| `swiftc not found: install the Xcode command line tools` | no Swift compiler on the `PATH` |
| `helper source not found` | `leks/ocr/extract_text.swift` is missing from the install |
| `swiftc timed out` | the compiler ran past 600 seconds |
| `swiftc could not run` | the compiler could not be started |
| `swiftc failed with exit code N` | the compiler reported an error |

## leks check

```bash
leks check
leks check --root notes
leks check --changed notes/plan.md notes/log.md
leks check --no-em-dash
leks check --accept decisions/DEC-4.md
```

The notes check that the template runs when a turn ends (`Stop` in Claude Code and Codex, `agentStop` in Copilot, see [hosts.md](hosts.md#hooks)). It walks the Markdown notes (`.md`, `.markdown`) under its base folder, or under each `--root`, and checks only the notes whose modification time or size changed since its last run. In a hook run, one that gets the host's hook input on stdin, the base folder is `CLAUDE_PROJECT_DIR` when that is set and exists: Claude Code sets it to the folder the session started in, so a `cd` in the agent's shell does not move the check. Otherwise, and in a run from a terminal, the base folder is the current folder. Copilot runs the hook in the folder its `cwd` field names, and the template sets that to the repository root. The opening run in a set of folders records the notes and reports nothing. It needs no index and no model, and a missing or broken config does not stop it.

| Argument | Default | Effect |
|---|---|---|
| `--root DIR...` | the base folder | the folders to walk. A relative path is read from the base folder. Hidden files and folders and the build folders listed under [Roots](configuration.md#roots) are skipped. |
| `--changed FILE...` | off | check these notes, changed or not, instead of the ones changed since the last run. A file outside the roots is ignored. |
| `--no-em-dash` | off | do not flag em dashes. `"check": {"em_dash": false}` in the config does the same ([configuration.md](configuration.md#check)). |
| `--accept FILE...` | off | keep the current text of these write-once or frozen notes as their new baseline and check nothing. Pass the same `--root` as the check. |
| `--config PATH` | the usual lookup | the global flag: this config sets the home folder that holds the record, the `check.em_dash` switch and the edit contracts |

What it reports:

| Message | When |
|---|---|
| `empty note, write it or delete it` | a changed note is blank once its YAML front matter is gone |
| `dead link [[X]], nothing by that name` | a wikilink in a changed note matches no file under the roots |
| `dead link (X), nothing by that name` | the same for a relative Markdown link |
| `em dash (N in this note), use a comma, colon or brackets` | a changed note holds em dashes; the count shows when there is more than one |
| `new note that no other note links to, link it from a related note or an index` | a note added since the last run gets no link from any other note |
| `dead link [[X]], its note was removed or renamed` | an unchanged note still links to a note that is gone since the last run |
| `write-once note changed, it is a record: ...` | a changed note under a `write-once` contract has a new body, named by its first changed line |
| `frozen note changed: ...` | a changed note under a `frozen` contract has any new text |
| `dated line outside a log section, ...` | a changed note under a `living` contract has a line that starts with a date outside a log section |
| `missing field X, the contract for G keeps ...` | a changed note lacks a field its contract keeps |
| `marked superseded but names no successor, ...` | a changed note under a contract with `successor` is marked superseded and names no note after it |

The contract rows come only from `check.contracts` in the config; the rules and the full messages are in [how-it-works.md](how-it-works.md#edit-contracts). The two contract findings for a changed note end with the command that keeps the change, `leks check --accept <path>`, with the same `--root` flags. In a hook run whose shell has moved away from the project folder, the command names the note and the checked folders in full, so it runs as printed from there. The command uses POSIX shell quoting.

A link matches by name anywhere under the roots, with or without `.md`, or by its path from the note's folder, and case does not count. Links inside code blocks, code spans, HTML comments and `%%` comments are left alone, and so are anchors, URLs, template placeholders such as `{{name}}` and links that lead out of the root. Entry notes need no inbound link: `readme`, `index`, `home`, `agents`, `claude`, `changelog`, `license`, `contributing`, `security` and `code_of_conduct`, in any case. The search for inbound links stops after 8 seconds, and a search cut short names no unlinked note.

Problems go to stderr, one per line, under a header, and stdout stays empty:

```text
leks check: 2 problems in the notes, fix them before you stop
notes/plan.md:14: dead link [[tile-cost]], nothing by that name
notes/idea.md: new note that no other note links to, link it from a related note or an index
```

A row reads `path:line: message`, or `path: message` when no line applies. Paths under the base folder are shown relative to it. The output stops at 20 lines, header included, and then ends with `... and N more`. With problems the exit code is 2, and the host hands them to the agent, which fixes them before it stops. With nothing to report it is 0. Copilot treats exit 2 from a stop hook as a warning only, so for its stop payload (hook input with `stopReason` or `stop_reason`) the same text goes to stdout as one line `{"decision": "block", "reason": "..."}` and the exit code is 0 ([hosts.md](hosts.md#hooks)).

`leks check --accept` exits 0 and prints `leks check: kept the current text of N notes` to stderr. It exits 1 when the config has no write-once or frozen contract, when the check has no record for those folders yet, or when a file is not a note under them or has no such contract.

When the check itself cannot run it exits 1 with one of these on stderr (the last one only when there is nothing else to report):

- `leks check: no such folder: <path>`
- `leks check: the current folder is gone, pass --root`
- `leks check: more than 100,000 files under <path>, nothing checked. Point --root at the notes folder`
- `leks check: cannot keep its record in <folder> (<reason>)`

A flag it does not know also gives exit 1, so a typo in the hook command never holds every turn.

The host passes its hook input on stdin. From a pipe, `leks check` reads it for at most a second; from a terminal it reads nothing. When that input has `stop_hook_active` or `stopHookActive` set to true, this turn's stop was already held once, so the check exits 0 at once and leaves its record as it was. A turn is held at most once, and what the agent changed while fixing is looked at by the next check.

The record is `<home>/run/check-<hash>.json`, one per set of roots, with the modification time and size of every note, and for write-once and frozen notes the hashes of up to five kept texts. It is updated after every run in which a note changed, also when problems were found, so each problem is reported once, for the change that brought it. A second run with nothing changed stays quiet. To look at a note again, name it with `--changed`; that works on the opening run as well, while the checks for new and removed notes need a record.

## leks hook

```bash
leks hook brief
leks hook update
leks hook warm
```

These are for host hooks ([hosts.md](hosts.md#hooks)). Each one swallows its own errors and exits 0, so a hook never breaks the host.

`brief` prints the session brief as one line of hook JSON, or nothing when there is no index yet. The brief names the state notes, recent changes, notes past their freshness budget and revisit dates that have come. `update` starts a background build and embed when one is due, and `warm` starts the model daemon when it is down and loads both models in the background. Both of those return at once and print nothing. What each does in detail is in [how-it-works.md](how-it-works.md#hooks).
