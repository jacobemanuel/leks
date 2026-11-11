# Configuration

LEKS reads one JSON file. This page says where that file is found, how `leks init` creates it, and what every key does, with its type and default. The mechanisms behind the keys are in [how-it-works.md](how-it-works.md), the commands in [cli.md](cli.md), and the host setup in [hosts.md](hosts.md).

The defaults on this page are those of the starter template in `leks/config.py`. `leks config show` prints the config as LEKS resolved it.

## Where the config is found

`_find` in `leks/config.py` uses the earliest of these places that applies:

1. `--config PATH` on the command line. The flag may sit anywhere on the line; it sets `LEKS_CONFIG` for that run.
2. `LEKS_CONFIG` in the environment.
3. `$LEKS_HOME/config.json`, when `LEKS_HOME` is set.
4. `~/.leks/config.json`.

A file named by `--config` or `LEKS_CONFIG` must exist. When it does not, LEKS stops with `no config: run leks init (not found: <path>)` and looks nowhere else. Places 3 and 4 are tried in turn, so a missing `$LEKS_HOME/config.json` falls through to `~/.leks/config.json`. When nothing is found the message is `no config: run leks init`.

`~` is expanded in every path. Relative paths inside the file (`home`, a root's `path`, `models.weights_dir`, `ocr.helper`) resolve against the folder that holds the config file, so a config can sit next to the folders it indexes.

The file is checked strictly. Each of these stops the command with exit code 1 and `leks: <message>`:

| Message | Cause |
|---|---|
| `config file is too big` | the file is over 1 MiB |
| `config is not utf-8` | the bytes are not UTF-8 |
| `cannot read config` | the file was found but could not be opened or read |
| `config must be a JSON object` | the top level is a list, a string or a number |
| `config is not valid json (line L, column C)` | a JSON syntax error |
| `config is nested too deep` | runaway nesting |
| `duplicate key: K` | a key appears twice in one object |
| `bad number in config: NaN` | `NaN`, `Infinity` or `-Infinity` anywhere |
| `unknown key in S: K` | a key the section does not accept, typos included |
| `version must be 3` | `version` missing or different |
| `unknown profile: P` | `--profile` or `LEKS_PROFILE` names a profile the config lacks |

Every other value error names the key, for example `roots[0].kind must be one of: notes, code, docs, mixed`.

## Create a config: `leks init`

```sh
leks init                                   # writes ~/.leks/config.json, or $LEKS_HOME/config.json
leks init --home ~/projects/example/.leks   # writes that folder's config.json, with "home" set to it
```

- It writes the starter template (the same content as `examples/config.example.json`) and prints `{"config": "<path>", "created": true}`.
- `--home DIR` puts the file at `DIR/config.json` and sets its `home` key to the absolute form of `DIR`. Without `--home` the file goes to `$LEKS_HOME` or `~/.leks`, and `home` stays `"~/.leks"` (`LEKS_HOME` overrides it whenever it is set).
- An existing file is left alone: the command prints `"created": false` and exits 0.
- `init` ignores `--config` and `LEKS_CONFIG`.

The template points its roots at folders under `~/projects/example`. Edit `roots` and the two example profiles before you run `leks build`. `leks config check` warns about every root whose folder is missing.

## The home folder

`home` holds everything LEKS writes. `LEKS_HOME` beats the `home` key, which beats `~/.leks`.

| Path under home | Contents |
|---|---|
| `index/master.sqlite` | the files read so far with their passages and symbols, the name of every file seen, skip reasons, and extracted document text keyed by content hash. Updated in place. |
| `index/<profile>/search.sqlite` | the search database of one profile. Rebuilt whole into a temporary file, then swapped in. |
| `index/vectors/<key>/` | vectors of one embedder: `store.sqlite`, shared by all profiles, plus `<profile>.f32` and `<profile>.json` per profile |
| `models/<key>/` | the weights of one model and their `manifest.json` (see `models.weights_dir`) |
| `models/hf-home/` | the Hugging Face client's own files, unless `HF_HOME` is set ([environment variables](#environment-variables)) |
| `bin/extract_text` | the OCR helper that `leks ocr build` compiles (see `ocr.helper`) |
| `run/` | the model daemon's socket and sandbox profile, locks, the update stamp and log, and the daemon's own home, temp and cache folders |

## Top-level keys

| Key | Type | Default | Effect |
|---|---|---|---|
| `version` | integer | required | must be `3` |
| `home` | string | `"~/.leks"` | where LEKS keeps its indexes, model weights, OCR helper and run files. `LEKS_HOME` overrides it. |
| `roots` | list | required | the folders to index, at least one ([Roots](#roots)) |
| `profiles` | object | `{}` | named sets of roots ([Profiles](#profiles)) |
| `default_profile` | string | `"default"` | the profile used when none is chosen |
| `classify` | object | see [Classify](#classify) | globs that mark state, rule, decision, result and history files |
| `models` | object | see [Models](#models) | the embedder and the reranker |
| `budgets` | object | see [Budgets](#budgets) | time limits for model calls |
| `daemon` | object | see [Daemon](#daemon) | idle exit and sandbox of the model daemon |
| `update` | object | see [Update](#update) | how often the hook updates the index |
| `ocr` | object | see [OCR](#ocr) | text from scanned PDFs and images |
| `limits` | object | see [Limits](#limits) | size limits and passage length |
| `server` | object | see [Server](#server) | MCP server options |
| `check` | object | see [Check](#check) | options of the notes check. `leks init` does not write it. |

A section you leave out takes its defaults. A section you give is merged over its defaults key by key, so `"ocr": {"enabled": "off"}` keeps the other OCR defaults. This holds for `budgets`, `daemon`, `update`, `ocr`, `limits`, `server` and `check`. `classify` and `models` follow their own rules, given below.

## Roots

A root is one folder to index.

| Key | Type | Default | Effect |
|---|---|---|---|
| `name` | string | required | id of the root: `^[a-z0-9][a-z0-9_-]{0,39}$`, unique. Every citation starts with it, as in `notes/a.md:12-30`. |
| `path` | string | required | the folder. `~` is expanded; a relative path resolves against the config file's folder. |
| `kind` | string | required | `notes`, `code`, `docs` or `mixed`. Picks the default `include` list. |
| `include` | list of globs, or `null` | `null` | files whose content is indexed. `null` means the kind's list; `[]` indexes names only. |
| `exclude` | list of globs, or `null` | `null` | files kept out completely, names included. `null` means none. |
| `follow_links` | boolean | `false` | follow symbolic links to files and folders. When off, links are skipped, and `read` returns the indexed text instead of reading a live file whose real location leaves the root. |

Default `include` per kind, one `**/*<ext>` glob per extension:

| Kind | Extensions |
|---|---|
| `notes` | `.md .markdown .txt .rst .tex .html .htm` |
| `code` | `.py .swift .metal .cs .sh .bash .zsh .c .h .cpp .cc .hpp .m .mm .js .ts .go .rs .java .kt .toml .yml .yaml .json` |
| `docs` | `.pdf .docx .txt .md .png .jpg .jpeg .heic .tif .tiff .gif .bmp .webp` |
| `mixed` | the union of the other kinds |

Every file the walk reaches gets a name row, so `locate` can find it by its name or folder. A file that also matches `include` has its content indexed. A file that `exclude` matches stays out of the index entirely, name row included, and a pattern that ends in `/**` (such as `drafts/**`) keeps the walk out of that folder altogether.

These are skipped in every root, whatever `include` says (`skipped` in `leks/config.py`):

- Folders named `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `.build`, `build`, `DerivedData`, `.swiftpm`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.obsidian`, `.Trash`, `.ipynb_checkpoints`, `dist` and `target`.
- Junk files: `.DS_Store`, `Thumbs.db`, `.localized`, names that start with `._` or `~$`, and names that end in `.pyc`, `.swp`, `.tmp`, `.sqlite-wal` or `.sqlite-shm`.
- Secret names. The test runs on the whole relative path, folder names included, and ignores case: `.env` and `.env.*`, names ending in `.pem`, `.key`, `.p12` or `.pfx`, `id_rsa`, `id_ed25519` and `id_ecdsa` keys, and any path that contains `credential`, `secret`, `password`, `apikey` or `api_key`. These are plain substrings, so `secretary.md` and a folder named `passwords` are skipped too, with everything below it.
- Symbolic links when `follow_links` is off, folders more than 64 levels deep, and file names that are not valid UTF-8.

File content is checked as well (`SECRET_TEXT` in `leks/build.py`). A file whose text holds a private key block, an `AKIA` access key id, a `gh*_` token, an `xoxb-` or `xoxp-` token, an `sk-` key, or `password:` or `password=` followed by eight or more characters stays out of the content index whole. Its name row remains.

Glob rules (`glob_match` in `leks/config.py`):

- Paths are relative to the root, use `/`, and match case-sensitively.
- `*` and `?` match inside one path segment. `[abc]` and `[!abc]` match one character.
- A `**` segment matches zero or more folders, so `**/*.md` also matches `a.md` at the top of the root. At the end of a pattern `**` needs at least one segment: `drafts/**` means everything below `drafts`.
- A glob is a non-empty string of at most 300 characters. It may not start with `/`, hold a `..` segment or contain a backslash.

A root whose folder is missing at build time keeps its rows from the last build, and the build logs `root not reachable, kept as is: <name>`. A root you remove from the config loses its rows at the next build.

## Profiles

A profile is a named set of roots with its own search database. A search sees only the roots of the active profile.

| Key | Type | Default | Effect |
|---|---|---|---|
| `profiles` | object | `{}` | `{"<name>": {"roots": ["<root>", ...]}}`. A name follows the root name pattern. `roots` is a non-empty list of root names that exist; a repeated name is dropped. A profile takes no other key. |
| `default_profile` | string | `"default"` | the profile used when neither `--profile` nor `LEKS_PROFILE` is set. It must exist. |

The profile `default` is built in. It holds every root in config order and cannot be defined in the file. The template defines two example profiles: `architect` with `notes` and `docs`, and `orchestrator` with `notes`, `code` and `docs`.

The active profile is `--profile P` from the command line, else `LEKS_PROFILE`, else `default_profile`. The MCP server reads `LEKS_PROFILE` only; a tool call cannot change the profile. `leks build` always builds every profile, `default` included.

## Classify

`classify` maps five classes to glob lists. The four besides `history` set a file's kind and are tried in the order `state`, `rule`, `decision`, `result`. A file that matches none of them takes its kind from its extension: `code`, `document` (`.pdf`, `.docx`), `image` or `note`. `history` leaves the kind alone and sets the status.

| Class | Default globs | Effect |
|---|---|---|
| `state` | `**/STATE.md`, `**/NOW.md`, `**/*_STATE.md` | kind `state`, status `current`. Listed by `state` and by the session brief, and lifted for questions about the present. |
| `rule` | `**/CLAUDE.md`, `**/AGENTS.md`, `.claude/**`, `.github/copilot-instructions.md` | kind `rule` |
| `decision` | `**/decisions/**`, `**/adr/**`, `**/DEC-*`, `**/ADR-*` | kind `decision`. Counts for `tried`. |
| `result` | `**/results/**` | kind `result`. Counts for `tried`. |
| `history` | `**/archive/**`, `**/history/**`, `**/*_HISTORY*` | status `historical-record` and source class `history` |

A class you give replaces that class's list. Classes you leave out keep their defaults. `[]` switches a class off. An unknown class name is an error. A change to `classify` makes the next build index every file again.

## Models

`models` names two optional models that run in the model daemon: an embedder for the meaning lane and a reranker that reorders the best passages. Without them search runs on words and says so in its last line. See [how-it-works.md](how-it-works.md#the-model-daemon).

| Key | Type | Default | Effect |
|---|---|---|---|
| `backend` | string | `"torch"` | `torch`, or `fake` for tests. `fake` loads only with `LEKS_ALLOW_FAKE=1`. |
| `device` | string | `"auto"` | `auto` takes `mps`, then `cuda`, then `cpu`. `cpu`, `mps` and `cuda` ask for one device; a device that is not there makes the model load fail. |
| `weights_dir` | string or `null` | `null` | where weights live, one folder per model key. `null` means `<home>/models`. |
| `embedder` | model spec or `null` | the `mdense` spec below | `null` turns the meaning lane off |
| `reranker` | model spec or `null` | the `mlate` spec below | `null` turns the reranker off |

A missing `embedder` or `reranker` key keeps the default model; only an explicit `null` turns one off. In the same way a missing `models` section, or `"models": null`, keeps both default models. The two models need different keys. For a words-only setup write:

```json
"models": {"embedder": null, "reranker": null}
```

Model spec keys (`_spec` in `leks/config.py`):

| Key | Type | Default | Rule and effect |
|---|---|---|---|
| `key` | string | required | `^[a-z0-9_]{1,32}$`. Names the weights folder and the vector store. |
| `hf_id` | string | required | the Hugging Face repository, `owner/name` |
| `revision` | string | required | the pinned commit, 40 lowercase hex characters |
| `op` | string | `embed` or `maxsim` by role | must be `embed` for the embedder and `maxsim` for the reranker |
| `pooling` | string | required | embedder: `cls`, `mean` or `last`. Reranker: `maxsim`. |
| `normalize` | boolean | `true` | scale vectors to unit length |
| `prefixes` | object | `{"q": "", "d": ""}` | text put before a query (`q`) and before a passage (`d`). Exactly these two keys. |
| `dim` | integer | required | output size, 1 to 8192 |
| `max_length` | integer | required | token limit per text, 8 to 32768 |
| `dtype` | string | `"float32"` | only `float32` is accepted |
| `projection` | list | none | reranker only: dense layers applied after the encoder |

A projection item has `src` (a folder of the model repository, `^[A-Za-z0-9_.-]{1,64}$`, never `.` or `..`), `in` and `out` (integers from 1 to 65536) and `residual` (boolean). The last item's `out` must equal `dim`.

The default specs:

| Key | `embedder` | `reranker` |
|---|---|---|
| `key` | `mdense` | `mlate` |
| `hf_id` | `lightonai/mDenseOn` | `lightonai/mLateOn` |
| `revision` | `a5fdb000f7a21da96c3bddde3a782ef777316df3` | `bbd883ae1fadb563056ff73b1061ae8172a3fcda` |
| `op`, `pooling` | `embed`, `cls` | `maxsim`, `maxsim` |
| `normalize`, `dtype` | `true`, `float32` | `true`, `float32` |
| `prefixes` | `"query: "`, `"document: "` | `"[Q] "`, `"[D] "` |
| `dim`, `max_length` | 768, 512 | 128, 512 |
| `projection` | none | `1_Dense` 768 to 1536 with residual, `2_Dense` 1536 to 768 with residual, `3_Dense` 768 to 128 |

`leks models fetch` downloads the pinned revisions, and every model load checks the files against their manifest. [report-3.0.md](report-3.0.md) explains how these two models were chosen.

## Budgets

Time limits in seconds for a search's calls to the model daemon (`leks/modeld_client.py`). Each must be a number above 0. A call that runs over its budget is dropped, and the search goes on without that lane, with the reason `model over its time budget`.

| Key | Default | Effect |
|---|---|---|
| `embed` | `45.0` | embedding one query |
| `rerank` | `8.0` | one rerank call when the reranker is warm |
| `cold_rerank` | `30.0` | the rerank budget when the calling process has not reranked yet, or not for `cold_after` seconds. A CLI search is a new process each time, so it always gets this budget. |
| `cold_after` | `600.0` | quiet time after which the next rerank counts as cold |
| `connect` | `10.0` | how long a call keeps trying to reach the daemon socket |
| `ping` | `2.0` | the liveness check |

`leks embed` uses its own per-batch limit and ignores these.

## Daemon

| Key | Type | Default | Effect |
|---|---|---|---|
| `idle_exit` | integer >= 1 | `3600` | seconds without a request after which the model daemon exits |
| `sandbox` | string | `"auto"` | `auto` runs the daemon under macOS `sandbox-exec` when it exists and plainly otherwise; `on` refuses to start it without the sandbox; `off` never wraps it |

## Update

| Key | Type | Default | Effect |
|---|---|---|---|
| `min_interval` | number >= 0 | `1800` | seconds between two background updates started by `leks hook update`. `0` lets every session start one. `leks build` ignores it. |

## OCR

OCR reads text from scanned PDFs and images through a small macOS helper compiled from `leks/ocr/extract_text.swift` by `leks ocr build`.

| Key | Type | Default | Effect |
|---|---|---|---|
| `enabled` | string or boolean | `"auto"` | `auto` uses the helper when it exists and is executable. `off` never uses it. `on` is accepted and acts like `auto`. `true` reads as `on`, `false` as `off`. |
| `images` | boolean | `true` | also read text from images (`.png .jpg .jpeg .heic .tif .tiff .gif .bmp .webp`) |
| `helper` | string or `null` | `null` | path of the helper. `null` means `<home>/bin/extract_text`. `~` is expanded; a relative path resolves against the config file's folder. |
| `max_pages` | integer, 1 to 2000 | `40` | how many pages of a scanned PDF the helper renders and reads |
| `label_results` | boolean | `true` | a result whose text came from OCR carries a `caution:` line with the mean OCR confidence and the number of low-confidence lines. `false` hides that line in search results and in `read`. |

A PDF with a text layer is read with `pypdf` (the `pdf` extra) and needs no OCR. See [how-it-works.md](how-it-works.md#ocr).

## Limits

All limits are integers of at least 1.

| Key | Default | Effect |
|---|---|---|
| `max_file_bytes` | `1500000` | largest note or code file read. `.json` files are capped at 200000 bytes in every root. |
| `chunk_chars` | `1000` | target passage length in characters. A change makes the next build index every file again. |
| `pdf_max_bytes` | `209715200` | largest PDF or image read |
| `office_max_bytes` | `419430400` | largest `.docx` read |
| `max_names` | `500000` | most name rows kept for `locate` |

A file over its limit keeps its name row and is skipped with the reason `over size limit`.

## Server

| Key | Type | Default | Effect |
|---|---|---|---|
| `always_load` | boolean | `true` | puts the always-load hint `_meta: {"anthropic/alwaysLoad": true}` on each MCP tool, so a host that defers tool loading keeps these tools loaded. `false` leaves the hint out. |

## Check

Options of `leks check`, the notes check that the template's end-of-turn hook runs. The section is optional and `leks init` does not write it.

| Key | Type | Default | Effect |
|---|---|---|---|
| `em_dash` | boolean | `true` | `false` stops `leks check` from flagging em dashes in changed notes |

## Environment variables

| Variable | Effect |
|---|---|
| `LEKS_CONFIG` | path of the config file ([lookup order](#where-the-config-is-found)) |
| `LEKS_HOME` | the home folder. Beats the `home` key, and `$LEKS_HOME/config.json` is a lookup place. |
| `LEKS_PROFILE` | the active profile. The MCP server takes its profile only from here. |
| `LEKS_MODELD_SOCK` | path of the daemon socket, default `<home>/run/modeld.sock`. A path of 100 bytes or more is replaced by `/tmp/leks-<uid>-<hash>.sock`. |
| `LEKS_ALLOW_FAKE` | `1` allows `"backend": "fake"` (tests) |
| `LEKS_LOCK_HELD` | `1` tells a build that its caller already holds `run/update.lock`, so the build does not take it again |
| `HF_HOME` | where the Hugging Face client keeps its own files during `leks models fetch` and inside the model daemon. Default `<home>/models/hf-home`; a value you set yourself wins. |

LEKS also sets `LEKS_OCR_MAX_PAGES` for the OCR helper from `ocr.max_pages`. The MCP server sees the `env` block of the host's server entry, while hooks run with the host's own environment, so a hook that needs `LEKS_CONFIG` or `LEKS_PROFILE` has to get it there. [hosts.md](hosts.md#config-and-interpreter) shows both.

## Example: a words-only config

```json
{
  "version": 3,
  "home": "~/projects/example/.leks",
  "roots": [
    {"name": "notes", "path": "~/projects/example/notes", "kind": "notes", "exclude": ["drafts/**"]},
    {"name": "code", "path": "~/projects/example/src", "kind": "code"},
    {"name": "docs", "path": "~/projects/example/docs", "kind": "docs"}
  ],
  "profiles": {
    "architect": {"roots": ["notes", "docs"]},
    "orchestrator": {"roots": ["notes", "code", "docs"]}
  },
  "default_profile": "orchestrator",
  "models": {"embedder": null, "reranker": null},
  "ocr": {"enabled": "off"}
}
```

```sh
export LEKS_CONFIG=~/projects/example/.leks/config.json
leks config check
leks build
leks search "what is the current plan"
```

To add the meaning lane and the reranker later, delete the `models` line, install the `models` extra (`pip install -e ".[models]"` in the LEKS clone), run `leks models fetch`, and then `leks embed`.

## When the next build reads everything again

A change to `classify` or `limits.chunk_chars` makes the next build index every file again, and so does a LEKS upgrade that changes the index format. `leks build --force` does the same on demand. Extracted document text, OCR text included, comes back from the cache by content hash, so documents are not extracted twice. When what the machine can read changes, for example after `pypdf` is installed or the OCR helper is built, the build retries the files it skipped for lack of an extractor.
