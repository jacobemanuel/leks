# Install

This is the long version of the install. For each step I say what it does, what it writes and where, how long it takes, and how you can tell it worked. The short version is in the [README](../README.md#install).

Almost everything LEKS writes goes into one home folder, `~/.leks` unless you pick another one. So it's easy to look at and easy to throw away. You can also run it without the models. Then it finds things by their words and by their names, and you skip the big downloads.

## What you need

- **A Mac for the full set.** The model helper runs inside a macOS sandbox, and scanned PDFs and images are read with the Vision framework, which only macOS has. I built and measured everything on a Mac with Apple silicon and 18 GB of memory.
- **Linux and Windows.** The tests run on Linux, macOS and Windows for every push to main and every pull request, with the `pdf` and `test` extras and a stand-in model backend ([testing](testing.md)). Those jobs are the only test LEKS has had on Windows. On every push to main and once a week a job also loads the real models on Linux, on CPU, and runs the bench with them ([testing](testing.md#models)). The sandbox and the OCR helper are macOS parts: on Linux the models run without the sandbox, and scanned pages and images stay unread.
- **How the model helper is reached.** On macOS and Linux over a Unix domain socket. Python on Windows lacks the socket calls that needs, so there it is reached over TCP on 127.0.0.1, with a token in a file in the LEKS home folder, which other standard users of the machine cannot read ([cli](cli.md#leks-modeld)).
- **Python 3.10 or newer, 3.11 or newer for the `models` extra.** The tests run on 3.10, 3.11, 3.12 and 3.13. The `models` extra asks for filelock 4.2.1 or newer (torch, transformers and huggingface_hub use it), the first release with the fixes for the advisories against 4.1.0, and every release since needs Python 3.11; on 3.10 pip stops with `No matching distribution found for filelock>=4.2.1`. `python3 --version` tells you what you have.
- **Space.** The `models` extra pulls in torch. On my Mac (Apple silicon, Python 3.13) that was 38 packages, 181 MB to download and about 1 GB once installed, 139 MB of the download for torch alone. On x86-64 Linux the torch wheel alone is 539 MB and brings CUDA libraries on top. The two models come to 2.54 GB at the pinned commits (1.26 GB for the embedder, 1.28 GB for the reranker). The index grows with your files. For scale: my own files, about 7,300 documents and 380,564 passages, gave a 2.7 GB word index and 1.89 GB of vectors for the shipped embedder ([report, section 3.1](report-3.0.md#31-the-record-and-the-machine) and [4.3](report-3.0.md#43-speed-and-memory)).
- **Memory.** With both models loaded, LEKS used about 6.8 GiB under load on my 18 GB Mac, with peaks up to 8.1 GiB ([report, section 4.3](report-3.0.md#43-speed-and-memory)). Words only needs none of that.
- **Optional: the Xcode command line tools**, if you want the OCR helper for scans and images.

## Get LEKS

```bash
git clone https://github.com/jacobemanuel/leks
cd leks
python3 --version
python3 -m venv .venv
. .venv/bin/activate        # on Windows: .venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e ".[models,pdf]"
python -c "import sys; print(sys.executable)"
```

**Does:** makes a virtual environment in `.venv` inside the clone, installs LEKS into it with both extras, and puts a `leks` command into the venv. The `-e` makes the install point at the clone, so leave the folder where it is. The last line prints the venv's Python. Keep that path: the hosts start `python3` from whatever PATH they get, which is usually another Python, and you'll put this path into the host files later.

**Which Python:** `python3 --version` has to say 3.10 or newer, 3.11 or newer for the `models` extra. Apple's own `python3`, the one from Xcode or its command line tools, is 3.9. Install a newer one from python.org or with Homebrew (`brew install python@3.13`) and make the venv with it, as in `python3.13 -m venv .venv`.

**Why the venv:** Homebrew's Python and recent Linux distributions refuse `pip install` outside one, with `error: externally-managed-environment`, and the venv keeps torch and friends away from your other Python stuff. The pip upgrade matters on an older Python, whose own pip may stop with `editable mode currently requires a setuptools-based build`.

**Writes:** the venv in `.venv` inside the clone, with the packages and the `leks` script. Nothing in your home folder yet.

**Takes:** with the `models` extra, about 180 MB of downloads. On my Mac pip fetched them in 14 s and installed them in 71 s. Without the `models` extra there's little to fetch: LEKS itself has no dependencies, and `pdf` adds only pypdf.

**Worked if:** `leks --help` lists the commands. The `leks` command is there while the venv is active; in a new terminal run `. .venv/bin/activate` in the clone again, or call `.venv/bin/leks` directly. `python -m leks --help` is the same program, and it's the form the hooks use. setuptools may print a long warning that `leks.ocr` is absent from the `packages` configuration; with `-e` the install reads that folder straight from the clone, so `leks ocr build` still finds its source. With the `models` extra in, `python -c "import torch, transformers, numpy, huggingface_hub"` prints nothing.

Pick your extras, inside the venv:

| Install line | What you get |
|---|---|
| `python -m pip install -e ".[models,pdf]"` | everything |
| `python -m pip install -e ".[pdf]"` | no models. Notes, documents, code symbols and file names, PDF text included. |
| `python -m pip install -e ".[pdf,test]"` | the test suite, with numpy for the stand-in model backend. Run it with `python -m unittest discover -s tests -t .` |
| `python -m pip install -e ".[models]"` | the models. PDFs get read only through the OCR helper on a Mac. |
| `python -m pip install -e .` | no models, and no PDF text unless the OCR helper is built |

`models` brings torch, transformers, safetensors, tokenizers, numpy and huggingface_hub. `pdf` brings pypdf.

### Try the demo (optional)

The clone has a tiny made-up project of 54 files and a config that searches it on words only. From the clone:

```bash
leks --config examples/leks.example.json build
leks --config examples/leks.example.json search frame budget
```

It writes its index to `~/.leks-demo` (or to `$LEKS_HOME` if you have that set) and nothing else. The search output ends with a lanes line that starts with `words only: no models configured`. When you're done, `rm -rf ~/.leks-demo`.

## Make a config: leks init

```bash
leks init
```

**Does:** writes a starter config. It lists four example folders under `~/projects/example` (it doesn't create them), both models, the profiles `architect` and `orchestrator`, and the defaults for everything else.

**Writes:** `~/.leks/config.json`. With `LEKS_HOME` set it goes to `$LEKS_HOME/config.json`. With `leks init --home DIR` it goes to `DIR/config.json`, and the config's `home` points at `DIR`.

**Takes:** no time.

**Worked if:** it prints the path and `"created": true`. If a config is already there, it leaves it alone and says `"created": false`.

Every command looks for its config here, in this order:

1. `--config PATH` on the command line
2. `LEKS_CONFIG`
3. `$LEKS_HOME/config.json`
4. `~/.leks/config.json`

A path from `--config` or `LEKS_CONFIG` has to exist. If it doesn't, you get `no config: run leks init (not found: <path>)` and LEKS doesn't look any further. It never looks in the folder you're standing in. More in [configuration.md](configuration.md#where-the-config-is-found).

## Point it at your folders

Open the config and swap the example roots for your own folders. A root is a folder plus the kind of files it holds. A profile is the set of roots one agent gets to search. Here's the part to change for a project with notes and code; the rest of the file stays as it is:

```json
"roots": [
  {"name": "notes", "path": "~/projects/myapp/notes", "kind": "notes"},
  {"name": "code", "path": "~/projects/myapp/src", "kind": "code"}
],
"profiles": {
  "architect": {"roots": ["notes"]},
  "orchestrator": {"roots": ["notes", "code"]}
},
```

- `name`: lowercase letters, digits, `_` and `-`, up to 40 characters, starting with a letter or a digit.
- `path`: `~` works. A relative path counts from the folder the config sits in.
- `kind`: `notes`, `code`, `docs` or `mixed`. It decides which file types the root takes ([configuration.md](configuration.md#roots)).
- The starter `notes` root has `"include": ["**/*.md"]`, so it takes only `.md`. Drop that line and it takes `.md`, `.markdown`, `.txt`, `.rst`, `.tex`, `.html` and `.htm`.

A profile can only name roots that exist. If one still names a root you deleted, every command stops with something like `profile orchestrator names unknown root: code`. There's also a built-in profile called `default` that sees every root. A command uses the profile in `LEKS_PROFILE` (`--profile` sets it), else `default_profile` from the config, which the starter config sets to `default`. More in [configuration.md](configuration.md#profiles).

**Worked if:**

```bash
leks config check
```

prints `"ok": true` and an empty `warnings` list. `"warnings": ["misc: folder not found"]` means that root's folder isn't there, and the build skips it. A broken config prints its problem and exits 1.

### Words only

If you don't want the models, set both to `null`:

```json
"models": {"embedder": null, "reranker": null}
```

Then skip `leks models fetch` and `leks embed`. Search runs on words alone, and the lanes line says `words only: no models configured`. A whole words-only config is in [configuration.md](configuration.md#example-a-words-only-config).

## Download the models: leks models fetch

```bash
leks models fetch
```

| Role | Hugging Face repo | Pinned commit | Lands in |
|---|---|---|---|
| embedder | `lightonai/mDenseOn` | `a5fdb000f7a21da96c3bddde3a782ef777316df3` | `~/.leks/models/mdense/` |
| reranker | `lightonai/mLateOn` | `bbd883ae1fadb563056ff73b1061ae8172a3fcda` | `~/.leks/models/mlate/` |

**Does:** downloads exactly those commits, minus the formats LEKS never loads (ONNX, Core ML, GGUF, `.bin` and others). It checks the folder has the layout the config expects and writes a `manifest.json` with the size and SHA-256 of every file. This is the only LEKS command that goes online. Later the helper runs with the hub's offline switches on, and on a Mac inside a sandbox with no network.

**Writes:** the two model folders above, plus the hub client's cache in `~/.leks/models/hf-home`. If you have `HF_HOME` set, it uses yours. `models.weights_dir` in the config moves the model folders somewhere else.

**Takes:** 2.54 GB of downloads, so it mostly depends on your connection. Here it took about 75 s.

**Worked if:** it prints one entry per model with its file count and bytes, like `{"mdense": {"role": "embedder", "files": ..., "bytes": ...}, "mlate": {...}}`. After that, `leks models check` says `"ok": true` for both.

About the hashes: the manifest is made from your own download, so it shows the files haven't changed since the fetch. The pinned commit is what ties them to a known version. Each time the helper loads a model, it checks every file's size against the manifest, and the hash too for files up to 64 MiB. `leks models check` runs the same check.

Both model repositories are public under the Apache 2.0 license, so the fetch needs no Hugging Face login or token.

## Build the index: leks build

```bash
leks build
```

**Does:** walks every root, reads what changed and indexes it. Then it rebuilds the search database of each profile whose files changed, `default` included. `leks build --force` reads everything again.

**Writes:** `~/.leks/index/master.sqlite` with every file, passage, code symbol and file name, and one `~/.leks/index/<profile>/search.sqlite` per profile. It holds a lock in `~/.leks/run/` while it works.

**Takes:** on my Mac, words only and with two profiles: 1 to 2.5 s for the LEKS clone itself (about 150 files and 1,800 passages) and 33 s for the Python 3.13 standard library (1,829 files, 52,472 passages). In the second build reading the files took 19 s and each profile's search database 7 s; the index came to 59 MB plus 158 MB per profile. OCR adds about half a second per scanned page (next section). Later builds skip the files that haven't changed: run again, the same two builds took 0.1 s and 0.3 s.

**Worked if:** stderr shows an `update:` line and then a `profile` line for each profile it rebuilt:

```text
update: seen N, indexed N, unchanged N, removed N, skipped N, errors N, ocr N, S.Ss
profile architect: docs N, chunks N, distinct inputs N, symbols N, names N, newer N, S.Ss
```

`errors 0` is what you want. `leks status` then shows `docs` and `chunks` above zero and a fresh `built_utc`. If the JSON on stdout says `"skipped": "locked"`, another build or a background update is running. Try again in a bit.

What gets read:

- **Notes** are cut into passages that keep their line numbers. Front matter and dates get picked up too ([how-it-works.md](how-it-works.md#passages)).
- **Code** gets text passages and symbols. Python goes through its own parser. Swift, Metal, C#, shell, C, C++ and Objective-C go through line scanners. JavaScript, TypeScript, Go, Rust, Java and Kotlin are text only.
- **PDFs** are read from their text layer with pypdf. Without pypdf, or when a PDF has no usable text layer, it goes to the OCR helper on a Mac. With neither, a PDF is a file name only.
- **`.docx` files** are read with the standard library. Other office files get a file name only.
- **Images** go through the OCR helper, on a Mac.
- **Every file** the walk reaches gets a name row, up to 500,000 names (`limits.max_names`), so `locate` can find it.
- **Some files are left out**: build folders, secret names, files over the size limits, binaries and a few more. The lists are in [configuration.md](configuration.md#roots) and [how-it-works.md](how-it-works.md#skip-reasons).

## Scans and images on a Mac: leks ocr build

```bash
xcode-select --install    # only if the command line tools aren't there yet
leks ocr build
leks build
```

**Does:** compiles the small Swift helper that ships with LEKS, `leks/ocr/extract_text.swift`, with `swiftc -O`. The helper reads PDFs through PDFKit and runs Vision text recognition on scanned pages and images, all on the Mac itself. The build after it hands the helper the scans and images.

**Writes:** `~/.leks/bin/extract_text`, or the path in `ocr.helper`.

**Takes:** the compiler gets up to 600 s; on my Mac it needed 1 to 2 s. During a build, OCR adds about half a second per scanned page, for at most `ocr.max_pages` pages of each scan. I measured that on clean A4 pages scanned at 200 dpi: 0.48 to 0.59 s a page, and 10.5 s for a folder with 20 such pages and two small images. Noisy or crowded scans may take longer.

**Worked if:** it prints `built the OCR helper`, and `leks status` shows `"ocr": true`. In the next build the `update:` line counts what went through the helper in `ocr N`. Files that were skipped before for lack of a reader get another go by themselves.

The `ocr` block of the config ([configuration.md](configuration.md#ocr), and how it works in [how-it-works.md](how-it-works.md#ocr)):

| Key | Default | What it does |
|---|---|---|
| `enabled` | `"auto"` | `"off"` or `false` switches OCR off |
| `images` | `true` | read images, on top of scanned PDFs |
| `max_pages` | `40` | the most pages OCR reads from one scanned PDF |
| `label_results` | `true` | results with OCR text carry a caution line |

## Meaning vectors: leks embed

```bash
leks embed
```

**Does:** starts the model helper and turns every passage that has no vector yet into one. Then it writes a vector file for each profile. The order is state notes, rules, decisions, then everything else, so a run you stop early has the important bits. When it's done it tells the helper to exit. `leks embed --profile architect` covers one profile only.

**Writes:** `~/.leks/index/vectors/mdense/store.sqlite` with every vector once. Next to it, each profile gets `<profile>.f32` and `<profile>.json`. A vector is 768 numbers, 3,072 bytes.

**Takes:** on my 18 GB Mac the shipped embedder did about 29 passages a second ([report, section 4.3](report-3.0.md#43-speed-and-memory)). So 10,000 passages take about 6 minutes and 100,000 about an hour. The `to embed N of M` line near the top tells you your N. My run peaked at about 2.8 GiB of memory while embedding.

**Worked if:** the last line is `DONE mdense <count>` and the exit code is 0. `INCOMPLETE mdense <have> of <total>` (exit 1) means some passages still lack a vector; run it again and it picks up where it stopped. A line that starts with `refused:` (exit 2) says what's missing, such as `refused: no index yet, run a build first`. After a good run, `leks status` shows `"vector_mode": "meaning"` and `coverage` at 1.0.

## Try a search

```bash
leks search what did we decide about the cache
```

**Does:** with a model in the config, it starts the helper when it's down and waits for the models to warm up. Then it searches the active profile and prints up to 5 results. `--k` goes up to 12.

**Writes:** nothing, apart from the helper's working files in `~/.leks/run/`.

**Takes:** on my Mac a cold search with both models took about 20 s, and the next two took 2.6 and 2.8 s. A warm search took about 1.5 s with nothing else running ([report, section 4.3](report-3.0.md#43-speed-and-memory)).

**Worked if:** you get numbered results, each with a `path:line` citation, its source class, its status and a snippet. The last line is the lanes line. `lanes: words, meaning, reranker` means everything ran. Anything else names what's off, like `words only: model daemon not started (model weights not found, run: leks models fetch)`, and `leks status` has the fix. The variants are in [how-it-works.md](how-it-works.md#the-lanes-line). `--words-only` skips the models for one search.

After a search the helper stays up. It exits after an hour with no request (`daemon.idle_exit`) or when you run `leks modeld stop`. A background update and `leks embed` stop it too, when they finish.

## Connect Claude Code, Copilot and Codex

[examples/project-template](../examples/project-template) has what the three hosts need. Copy it into the root of your project:

```bash
cd ~/projects/myapp
T=~/leks/examples/project-template    # wherever your clone is
mkdir -p .claude/skills/leks-search .vscode .github/hooks .codex decisions
cp -n "$T/AGENTS.md" "$T/CLAUDE.md" "$T/STATE.md" "$T/.mcp.json" .
cp -n "$T/decisions/DECISIONS.md" decisions/
cp -n "$T/.claude/settings.json" .claude/
cp -n "$T/.claude/skills/leks-search/SKILL.md" .claude/skills/leks-search/
cp -n "$T/.vscode/mcp.json" "$T/.vscode/settings.json" .vscode/
cp -n "$T/.github/hooks/leks.json" .github/hooks/
cp -n "$T/.codex/config.toml" "$T/.codex/hooks.json" .codex/
P=~/leks/.venv/bin/python    # the path the venv step printed
for f in .mcp.json .claude/settings.json .vscode/mcp.json .github/hooks/leks.json .codex/config.toml .codex/hooks.json; do sed -i.bak "s#\"python3#\"$P#g" "$f" && rm "$f.bak"; done
```

The loop puts the venv's Python in place of `python3` in every command of those six files, so the hosts run LEKS whatever their PATH holds. `cp -n` won't overwrite a file you already have. Merge those by hand. For an existing `CLAUDE.md` that's one extra line, `@AGENTS.md`. The template's own `README.md` stays out on purpose: it explains the template, and your project has its own. The template holds more skills than `leks-search`, listed in [skills.md](skills.md); copy the folders you want the same way. Then run `"$P" -m leks check` once in the project root: its first run in a folder only records the notes, so from then on the check at the end of a turn reports what that turn changed.

| File | Read by | What it does |
|---|---|---|
| `AGENTS.md` | Copilot, Codex, and Claude Code through `CLAUDE.md` | the rules for the agent: search before answering, cite `path:line`, `read` the lines it relies on, `tried` before a proposal |
| `CLAUDE.md` | Claude Code | `@AGENTS.md`, which pulls in the file above, and a line on slash commands and the agent team |
| `STATE.md` | every host, through LEKS | the state note, which the skills link new notes from; leave it out when your project has one |
| `decisions/DECISIONS.md` | every host, through LEKS | the decision log, one row per decision note |
| `.mcp.json` | Claude Code | starts `python3 -m leks serve` as the MCP server `leks` |
| `.claude/settings.json` | Claude Code | the brief and the index update at session start, and the warm-up before `search` and `tried`. The notes check runs at `Stop`. |
| `.claude/skills/leks-search/SKILL.md` | Claude Code, Copilot, Codex through a link | the search routine as a skill |
| `.vscode/mcp.json` | Copilot | starts the same server in VS Code |
| `.github/hooks/leks.json` | Copilot | the same session-start hooks, and the notes check at `agentStop`. No warm-up. |
| `.vscode/settings.json` | Copilot | turns `chat.useClaudeHooks` off, so VS Code doesn't run the Claude Code hooks as well |
| `.codex/config.toml` | Codex | starts the same server in a trusted project, with `tool_timeout_sec` at 120 |
| `.codex/hooks.json` | Codex | the same session-start hooks, the warm-up before `search` and `tried`, and the notes check at `Stop`, once you trust the hooks |

**Codex.** Codex starts the servers in a project's `.codex/config.toml` only when you have marked the project as trusted; otherwise put the two `leks` tables into `~/.codex/config.toml`. It runs the hooks in `.codex/hooks.json` only after you have reviewed and trusted them with `/hooks`, and a changed hook needs a new review. It reads skills from `.agents/skills`, so link them there:

```bash
mkdir -p .agents/skills
for d in .claude/skills/*/; do n=$(basename "$d"); ln -s "../../.claude/skills/$n" ".agents/skills/$n"; done
```

The template's [README](../examples/project-template/README.md#codex) has the details.

**Profile.** The files that start LEKS set `LEKS_PROFILE` to `architect`: `.mcp.json`, `.claude/settings.json`, `.vscode/mcp.json`, `.github/hooks/leks.json` and `.codex/config.toml`. The Codex hooks carry it on their command line instead, as `--profile architect` in `.codex/hooks.json`. Put in a profile from your config, or delete those settings and LEKS takes `default_profile`.

**Interpreter.** Every command in the template starts with `python3`, and the loop above swaps in the venv's Python. Without that swap a host that starts another `python3` stops with `No module named leks`. On Windows put the path in by hand, with forward slashes, like `D:/code/leks/.venv/Scripts/python.exe`. A path with spaces needs quotes around it inside the hook commands.

**One config per project.** Add `LEKS_CONFIG` with an absolute path to the same `env` blocks, and to the `[mcp_servers.leks.env]` table of `.codex/config.toml`. A Codex hook has no `env` of its own, so add `--config` with the same path to each command in `.codex/hooks.json`. In `.vscode/mcp.json` the path may start with `${workspaceFolder}`. More in [hosts.md](hosts.md#config-and-interpreter).

Check it before you open the agent:

```bash
LEKS_PROFILE=architect "$P" -m leks hook brief
```

After a build this prints one line of JSON whose `additionalContext` starts with `Project search is on (leks)`. That's the brief the agent gets at session start, with up to four current state notes and up to six files changed in the last three days, and, when the config sets [freshness](configuration.md#freshness) budgets or a note has a revisit date, up to six notes past their budget and up to six revisit dates that have come. It prints nothing when that profile has no index yet, or when the config or the profile is wrong. `leks status` with the same profile tells you which.

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"status","arguments":{}}}' | LEKS_PROFILE=architect "$P" -m leks serve
```

That's the MCP server answering one call by hand. You get one line back, with the same report as `leks status` inside `result.content`. Then open the project in the host and ask the agent to call the `status` tool.

**The notes check.** When a turn ends, the three hosts run `python3 -m leks check` (`Stop` in Claude Code, `agentStop` in Copilot, `Stop` in Codex once its hooks are trusted). It looks at the Markdown notes that changed since its last run and lists empty notes, dead links, new notes that nothing links to, em dashes and, where `check.contracts` in the config sets rules, edits that break an [edit contract](how-it-works.md#edit-contracts). The agent gets the list and fixes it before it stops, and a turn is held at most once. The opening run in a folder only records the notes. The check walks the project folder. In Claude Code that's the folder the session started in (`CLAUDE_PROJECT_DIR`), even after the agent changed folders in its shell; I tested that in a real session. In Copilot it's the folder the hook runs in, which the template sets to the repository root with `"cwd": "."`, as GitHub's hook reference describes. In Codex it's the session's working folder. In a big repo point the check at your notes, as in `python3 -m leks check --root notes`. A relative `--root` is read from the project folder, and above 100,000 files the check refuses. `--no-em-dash` drops the em dash rule. The rest is in [cli.md](cli.md#leks-check).

**Copilot and the model helper.** Copilot hooks can't match a tool, so the template gives Copilot no warm-up hook. A search that finds the helper down starts it itself and waits for the models; background updates and `leks embed` stop it again when they finish. If a model takes longer to load than the search allows, that search runs without it and the lanes line says so, and the next one finds it loaded. If every search ends with `model unavailable`, the helper can't start: `leks modeld serve` in a terminal shows why. Host details are in [hosts.md](hosts.md).

## Health check: leks status

```bash
leks status
```

**Does:** reads the profile's database and the config, and reports what search runs on right now.

**Writes:** nothing.

**Takes:** no time to speak of.

**Worked if:** it prints JSON. The keys worth a look:

| Key | What to look for |
|---|---|
| `search` | one sentence on what search runs on, with the fix for a lane that's off |
| `lanes` | the lanes line a search would print now |
| `docs`, `chunks` | files and passages in this profile. Zero means no build yet, or empty roots. |
| `coverage` | the share of passages with a vector, 1.0 after a full embed |
| `pdf_text`, `ocr` | whether PDF text and scans can be read |
| `sandbox` | `on` on a Mac, `off (sandbox-exec not available on this system)` elsewhere |
| `built_utc` | when this profile was last built |

`leks status` never asks the helper, so a helper that's just not running doesn't show up as a problem there. `leks modeld ping` does ask: `{"modeld": "up"}`, or `{"modeld": "down"}` with exit 1. Every key is in [cli.md](cli.md#leks-status).

## Keeping it up to date

Once the template is in, you rarely run `build` or `embed` yourself. At session start the hook `leks hook update` looks at `~/.leks/run/update.stamp`. If the stamp is missing or older than `update.min_interval` (1,800 s), it starts a background job and returns at once. The job:

1. starts the model helper,
2. runs the build,
3. embeds what's new for every profile, then stops the helper,
4. touches the stamp.

It logs to `~/.leks/run/update.log`, which rolls over to `update.log.1` past 5 MiB. A good run has `update start`, `daemon: <word>`, the build's own lines, `build: {...}`, an `embed <profile>: exit 0` for each profile, and `update end`. A crash shows up as `error: <type>: <message>`. A running MCP server notices the new index on its next call, so nothing needs a restart.

By hand it's `leks build`, then `leks embed`. A build without the embed is fine for a while: new passages are found by their words, and the lanes line shows `(vectors for N% of passages)` until the next embed.

A change to `classify` or `limits.chunk_chars` makes the next build read every file again, and so does a LEKS upgrade that changes the index format. `leks build --force` does the same whenever you like ([configuration.md](configuration.md#when-the-next-build-reads-everything-again)).

To update LEKS itself, `git pull` in the clone. With the `-e` install the new code is live right away. Run the same `pip install -e` line again if the extras changed.

## Uninstall

```bash
leks modeld stop
rm -rf ~/.leks
```

The stop asks the helper to exit, if it's running. The `rm` deletes the config, the index, the vectors, the model weights, the OCR helper and the run folder. Then delete the clone: LEKS, torch and the rest live in its `.venv`. If you installed LEKS into another Python, `python -m pip uninstall leks` there removes LEKS and the `leks` command and leaves the extras, so remove torch and friends by name.

A few things live outside `~/.leks`:

- another home folder, if you set one with `LEKS_HOME` or in the config (`leks init --home` writes it there)
- `models.weights_dir`, if you moved the weights
- the hub cache in your own `HF_HOME`, if you had one set during the fetch
- `~/.leks-demo`, if you tried the demo

Take the template files out of your projects too, so the hosts stop calling LEKS, and delete the clone.

One thing stays behind on a Mac: running the models on the GPU leaves a Metal shader cache in `$(getconf DARWIN_USER_CACHE_DIR)org.python.python/com.apple.metal`, 14 MB on my Mac. Other Python programs that use the GPU write to the same folder, so leave it if you run any. Deleting it is safe, since it is only a cache and gets rebuilt when needed.

## Troubleshooting

| Message | What to do |
|---|---|
| `error: externally-managed-environment` | pip ran outside a venv, on a Python that refuses that (Homebrew, recent Linux). Make the venv as in [Get LEKS](#get-leks) and install inside it. |
| `File "setup.py" or "setup.cfg" not found` or `editable mode currently requires a setuptools-based build` | An old pip, often on an old Python such as Apple's own 3.9. Make the venv from Python 3.10 or newer and run `python -m pip install -U pip` in it. |
| `Package 'leks' requires a different Python: 3.9.6 not in '>=3.10'` | The venv was made from a Python older than 3.10. Delete `.venv` and make it again from 3.10 or newer, 3.11 or newer for the models. |
| `No module named leks` when the agent starts LEKS | The host started a `python3` without LEKS. Swap in the venv's Python as in [Connect](#connect-claude-code-copilot-and-codex). |
| `leks: no config: run leks init` | There's no config in `$LEKS_HOME` or `~/.leks`. Run `leks init`, or point `LEKS_CONFIG` at your file. |
| `leks: no config: run leks init (not found: <path>)` | `--config` or `LEKS_CONFIG` names a file that isn't there. LEKS doesn't fall back to the other places. |
| `leks: unknown profile: <name>` | The profile asked for (`LEKS_PROFILE`, which `--profile` sets, else `default_profile`) isn't in the config. `leks config show` lists them, apart from the built-in `default`. |
| `leks: profile <p> names unknown root: <r>` | The profile lists a root that's gone. Fix its `roots`. |
| `leks: nothing to build: none of the configured roots exists or is readable (...)` | The paths are wrong. `leks config check` shows which folders are missing. |
| `leks: no index for profile <p> yet: run leks build` | Run `leks build`. |
| `leks: huggingface_hub is not installed: pip install huggingface_hub` | From `leks models fetch`. The `models` extra brings it: `pip install -e ".[models]"` in the clone. |
| `leks models fetch` ends with `... the hub layout ... differs ...` | The download doesn't have what the config expects at its top level: a `config.json`, `.safetensors` weights and each `projection` folder. Check the model's `hf_id`, `revision` and `projection` in the config. |
| `model daemon not started (<why>)` in the lanes line | The part in brackets says what to run. For `it did not come up`, run `leks modeld serve` in a terminal: it runs in the foreground without the sandbox, so its errors show. Ctrl-C ends it. |
| `model unavailable` in the lanes line | The helper isn't running or doesn't answer. `leks modeld ping`, then `leks modeld start`. |
| `refused: store was made by another revision of this model` | The stored vectors came from another revision or vector size of the embedder, for example after you changed `revision` in the config. Delete `~/.leks/index/vectors/<key>/` and run `leks embed` again. |
| `swiftc not found: ...` or `swiftc failed with exit code N` | Install the command line tools with `xcode-select --install`. LEKS throws the compiler output away, so to see it, compile by hand in the clone: `swiftc -O leks/ocr/extract_text.swift -o /tmp/extract_text`. |
| `leks check: more than 100,000 files under <path>, nothing checked. ...` | Add `--root` with your notes folder to the check command in `.claude/settings.json` and `.github/hooks/leks.json`. |
| `search failed: ...` (or another tool) in the agent | The rest of the message says what to run in a terminal, such as `leks build` or `leks status`. |

When a fix says `pip install -e ".[models]"` or `pip install -e ".[pdf]"`, run it in the clone with the Python that runs LEKS, as in `.venv/bin/python -m pip install -e ".[models]"` for the venv from [Get LEKS](#get-leks). LEKS is not on PyPI, so a plain package name there would fetch someone else's package.
