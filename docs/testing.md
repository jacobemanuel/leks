# Testing

This page lists every job in `.github/workflows` and every group of tests, with what each one proves, where it runs and what it leaves out. Where a number appears, the text says where it comes from. The [last section](#run-the-checks-on-your-computer) shows how to run the same checks on your own computer. Every command runs from the root of the clone, best inside a virtual environment.

## Overview

| Job or test group | Where it runs | What it proves | What it does not cover |
|---|---|---|---|
| [unit](#unit) | tests.yml: Ubuntu, macOS and Windows, each with Python 3.10, 3.11, 3.12 and 3.13 (12 jobs) | every file compiles and every module imports; the whole suite passes and runs at least `TESTS_FLOOR` tests; the words-only bench holds `bench/baseline_words.json` | the real models, since the suite uses a stand-in backend; the tests that [skip on one system](#tests-that-skip-themselves) |
| [model daemon over tcp](#model-daemon-over-tcp) (`tests.test_transport`, `tests.test_doctor`) | tests.yml: Ubuntu and macOS, Python 3.13, with `LEKS_MODELD_TRANSPORT=tcp`; both modules also run in every unit job | the TCP transport that Windows uses: endpoint file, token check, a real daemon on a local port, the client, the warm hook and `leks doctor`; no test may skip | Windows itself, which the Windows unit jobs cover |
| [mcp conformance](#mcp-conformance) (`tests.test_mcp_conformance`) | tests.yml: Ubuntu and Windows, Python 3.10 and 3.13 | with the official MCP Python SDK as the client: `initialize` with version negotiation, `tools/list` with valid JSON Schema, `tools/call` for every tool, protocol errors for an unknown tool and for malformed arguments; stdout carries JSON-RPC and nothing else; no test may skip | how a given host behaves on top of the protocol |
| [host formats](#host-formats) (`tests.test_host_payloads`) | every unit job, and the mcp jobs | the hook commands take the payloads Claude Code, GitHub Copilot, VS Code and Codex send, and answer in the form each one reads; the MCP and hook config files of the project template have the documented shapes | a real host session: Claude Code, Copilot and Codex are never started in CI |
| [packaging](#packaging) (`tests.test_packaging`) | tests.yml: Ubuntu, macOS and Windows, Python 3.13 | the sdist and the wheel build, pass `twine check --strict` and hold what they should; the wheel installs into a fresh virtual environment, where `leks`, `python -m leks`, `leks doctor`, the CLI and an MCP session work; no test may skip | installing the extras from PyPI; an upload to PyPI |
| [coverage](#coverage) | tests.yml: Ubuntu, Python 3.13 | the share of the package's lines that run inside the test process stays at or above `fail_under` | lines that run in the processes the tests start; whether a covered line is checked by an assertion |
| [lint](#lint) (with `tests.test_ci_scripts`) | tests.yml: Ubuntu, Python 3.13; the CI script tests also run in every unit job, where the PyYAML half skips | ruff finds nothing; actionlint and shellcheck accept the workflows; the workflow rules of this repository hold; the CI scripts pass their tests; no test may skip | what the code does at run time |
| [bench gate](#the-bench-gate) (`tests.test_gate`) | every unit job | `bench/gate.py` passes the baseline's own numbers and a drop within the tolerance, fails a drop past it for each metric, and exits 2 on input it cannot judge | the bench scores themselves, which the unit jobs gate on a real run |
| [models](#models) (`tests.test_real_models`) | models.yml: Ubuntu, CPU, Python 3.12 | the pinned weights download and match their manifest; a search with both models finds a known answer in its top 5 that words alone do not rank first; the bench runs all five systems with their lanes; no test may skip | a score floor for the model systems; speed; Apple silicon, GPUs and the macOS sandbox around the model daemon |
| [public benchmark](#public-benchmark) | public-bench.yml: Ubuntu, Python 3.13, started by hand | the PEP sources at the pinned commit match their hash; bm25, LEKS and LEKS 3.0 run on the same questions, and with the `models` input on, LEKS with the pinned models on CPU; the result files are kept | it gates nothing: no score is held to a floor, and it never runs on a push |
| [codeql](#codeql) | security.yml: Ubuntu | CodeQL's default Python queries ran and their results reached code scanning | that the code has no security bugs |
| [secret scan](#secret-scan) | security.yml: Ubuntu, gitleaks 8.30.1 | no gitleaks rule matches in any commit of any branch or tag | secrets in a form its rules do not know |
| [bandit](#bandit) | security.yml: Ubuntu, Python 3.13 | no bandit finding in `leks/` and `bench/` outside the skips documented in pyproject.toml | what a check over the syntax of one file cannot see |
| [pip-audit](#pip-audit) | security.yml: Ubuntu, Python 3.10, 3.11 and 3.13 | on the day of the run, no published advisory names a version that pip resolves for `.[pdf,test]` on 3.10 and for `.[models,pdf,test]` on 3.11 and 3.13 | advisories that are not published yet; what other platforms resolve |
| [Dependabot](#dependabot) | GitHub, monthly | pull requests with new versions of the actions, the CI tools and the extras | that an update is safe; its pull request runs the jobs above like any other |
| [real host sessions](#real-host-sessions) | by hand: `bench/hosts/smoke.sh` | which host CLIs are installed and signed in, and that a short run with LEKS completes on each | never runs in CI, since it needs an account on each host and spends its usage |
| [a new user's install](#a-new-users-install) | by hand on macOS, 14 November 2026 | the Quick start and [install](install.md) work as written from a fresh clone of main with Homebrew's Python 3.13 and 3.14, up to the three hosts' config files and hooks starting LEKS with no venv on PATH | Linux and Windows; the model download and embed, which the models job and the benchmark runs cover |

## What the workflows share

| Workflow | File | When |
|---|---|---|
| tests | `.github/workflows/tests.yml` | every push to main and to `release-*` branches, every tag `v*`, every pull request, by hand |
| models | `.github/workflows/models.yml` | every push to main and to `release-*` branches, the 3rd of each month at 04:17 UTC, by hand |
| security | `.github/workflows/security.yml` | every push to main and to `release-*` branches, every pull request, the 5th of each month at 05:23 UTC, by hand |
| public benchmark | `.github/workflows/public-bench.yml` | by hand only |
| Dependabot | `.github/dependabot.yml` | once a month |

The models workflow leaves pull requests out: it downloads 2.54 GB of weights on a cache miss ([install](install.md#what-you-need)), which is too slow for every push to a pull request.

- The token can read the repository and nothing else (`permissions: contents: read`). The CodeQL job also gets `security-events: write`, which its upload needs ([codeql-action](https://github.com/github/codeql-action)).
- Checkout keeps no credentials (`persist-credentials: false`).
- Every action is pinned to a full commit SHA, with its version in a comment.
- Every tool a job installs (test clients, build tools, linters, scanners) is pinned with `==` in `.github/ci/requirements-*.txt`, and gitleaks by version and checksum. The requirement files pin versions only, with no `--hash`, so a file replaced on PyPI under the same version would go unnoticed, and the build backend comes at the newest setuptools that pyproject.toml allows. The package's own extras install at the newest versions they allow, so a new release of a dependency shows up in CI; torch comes from the PyTorch CPU index.
- Every job has a timeout, and the jobs of a matrix all run to the end when one of them fails (`fail-fast: false`).
- No job uses `continue-on-error`, so a red job is a failed check.
- A new push to a pull request cancels the older run of the same workflow there. On main, release branches and tags a running workflow is never cancelled: a newer run waits for it, and of several waiting runs only the newest goes ahead ([concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)).

`.github/scripts/check_workflows.py` checks these rules in the [lint](#lint) job.

### The test count floor

Every job that runs tests runs them through `.github/scripts/run_suite.py`. It fails the job when unittest fails, when the output has no `Ran N tests` line, or when fewer tests ran than the floor the job gives with `--floor`. With `--no-skips` it also fails the job when any test skipped. Each failure shows up as an error annotation and in the job summary.

Tests run verbose, one line per test with the traceback of a failure right below it, so a log always names the test a run stopped in. A test that runs longer than `--hang-after` seconds (300 by default, 0 turns it off) makes `faulthandler` print the stack of every thread and end the run, and an import that hangs during discovery is caught the same way. A hang then fails its job within minutes and the log shows where it hung, instead of running into the job timeout with no output.

unittest counts a skipped test as run and a module that does not import as one failed test, so a module that goes missing either fails the job or brings the count under the floor. The floor of the whole suite is `TESTS_FLOOR` at the top of tests.yml, 1030, the `Ran N tests` line of `python -m unittest discover -s tests -t .` on the commit that set it. A pull request that adds tests may raise it, and `tests.test_ci_scripts` fails when it is higher than the number of tests on disk. The smaller jobs name their own floors: 39 for the transport and doctor tests, 11 for MCP conformance, 18 for the host formats, 8 for packaging, 16 for the CI scripts and 5 for the real models, each the count of its modules when it was set, and `tests.test_ci_scripts` fails when one of them is higher than the tests its modules have. No check holds a smaller floor up to the count: a floor lowered to 1 still passes.

## tests

### unit

Each of the 12 jobs installs LEKS with the `pdf` and `test` extras, byte-compiles every Python file, imports every module of the package, runs the whole suite with the `TESTS_FLOOR` floor, then runs the bench on words only and holds its scores to `bench/baseline_words.json` with the [bench gate](#the-bench-gate). The import step (`.github/scripts/import_all.py`) lets a module fail only on a missing optional package, in practice the torch backend, since these jobs install no torch. When a job fails, it keeps the bench results as an artifact for 14 days, with the rank of every question.

No test needs the model weights: the model tests use a stand-in backend that hashes words into vectors (`leks/backends/fake_backend.py`). It checks the plumbing around the models: the daemon, its protocol on both transports, the vector store and the lanes. The [models](#models) workflow runs the real ones.

What differs on Windows:

- The model daemon and its clients talk over TCP (see the next section). The tests that need Unix sockets skip there, and the TCP tests run in their place.
- The jobs set `PYTHONUTF8=1`, since many test helpers read files and pipes with the locale encoding, cp1252 on Windows. The package itself names UTF-8 at every open and on stdio, and the packaging job runs the installed package without that setting.
- git keeps the line endings as committed, since the fixtures and the hashes expect LF: `.gitattributes` asks for LF in every checkout, a local Windows clone included, and the jobs also set `core.autocrlf false`.
- The server writes its replies through Python's text-mode stdout, which turns `\n` into `\r\n` on Windows ([CPython](https://github.com/python/cpython/blob/3.13/Python/pylifecycle.c#L2710-L2719)). The conformance test accepts the `\r`, which JSON reads as whitespace ([RFC 8259](https://www.rfc-editor.org/rfc/rfc8259#section-2)). Whether every MCP host does the same is untested.

I wrote the Windows cells without a Windows computer at hand. The release script moves `main` only after this workflow has passed on the release commit, Windows cells included, so a release on `main` has passed them.

### Tests that skip themselves

A skipped test prints its reason. Outside the jobs that pass `--no-skips`, these skip by design:

| Tests | Skip when | Where they run |
|---|---|---|
| Unix socket tests of the daemon, the client and the hooks, and the length limit of a socket path | the system has no Unix sockets with descriptor passing (Windows) | the macOS and Ubuntu unit jobs |
| real TCP daemon tests in `tests.test_transport` | the system refuses to bind a local port, as some sandboxes do | every unit job and the tcp job |
| file mode, symlink and FIFO tests | the system is not POSIX | the macOS and Ubuntu unit jobs |
| OCR helper tests | the system is not POSIX; the compile test also needs macOS with `swiftc` | the macOS and Ubuntu unit jobs, the compile test on macOS only |
| macOS sandbox tests | the system is not macOS | the macOS unit jobs |
| two tests of the macOS sandbox profile text that compare POSIX paths | the system is Windows | the macOS and Ubuntu unit jobs |
| the SDK and jsonschema half of `tests.test_mcp_conformance` | `mcp` or `jsonschema` is not installed | the mcp jobs, with `--no-skips` |
| `tests.test_packaging` | `LEKS_TEST_PACKAGING=1` is not set | the packaging jobs, with `--no-skips` |
| `tests.test_real_models` | `LEKS_TEST_MODELS=1` and `LEKS_TEST_WEIGHTS` are not set | the models job, with `--no-skips` |
| the PyYAML half of `tests.test_ci_scripts` | PyYAML is not installed | the lint job, with `--no-skips` |
| the Codex config test in `tests.test_host_payloads`, and the profile comparison in its Codex hooks test | no TOML reader: Python 3.10 without `tomli`, which the `test` extra installs | every unit job, since each installs the `test` extra |

### model daemon over tcp

The model daemon and its clients talk over a Unix socket wherever Python's socket module has `recvmsg` with descriptor passing, which the Python docs list as available on Unix only ([socket](https://docs.python.org/3/library/socket.html#socket.socket.recvmsg)). Everywhere else, which means Windows, they talk over TCP on 127.0.0.1, on a port the system picks. The daemon writes the port and a random 64-character token into `modeld.port` next to the socket path, a file only the user may read (mode 0600 on POSIX; on Windows the LEKS home folder's access rules do that job), and answers only requests that carry the token. `LEKS_MODELD_TRANSPORT=tcp` takes the TCP path on any system, and `leks doctor` shows which transport is in use ([cli](cli.md#leks-doctor)).

This job sets that variable on Ubuntu and macOS and runs `tests.test_transport` and `tests.test_doctor` with `--no-skips`: the endpoint file (its place, its mode, the refusal of a symlink or bad contents, and that only the daemon holding its token removes it), the token check in the daemon, the client over TCP, the warm hook starting the daemon without Unix sockets, the start options Windows uses to detach the daemon, the macOS sandbox profile for the TCP case, and a real daemon on a real port that answers with the token and refuses without it. A change that breaks the Windows path fails here too, on systems where it is quick to reproduce.

### mcp conformance

Ubuntu and Windows, with Python 3.10 and 3.13. The official MCP Python SDK ([mcp 2.3.0](https://pypi.org/project/mcp/2.3.0/)) is the client: it starts `python -m leks serve` the way the project template does, initializes, lists the tools and calls each one with good arguments and with bad ones. jsonschema 4.26.0 checks every tool's input schema against JSON Schema 2020-12. A second group writes raw lines to the server and reads every byte it prints. The tool list and the arguments come from the server's own schemas, so a new tool is covered without editing the test.

What the tests check, against the [MCP specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25) and [JSON-RPC 2.0](https://www.jsonrpc.org/specification#error_object):

- [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle): a client that asks for a protocol version the server speaks (2025-11-25, 2025-06-18, 2025-03-26 or 2024-11-05, `PROTOCOLS` in `leks/server.py`) gets that version back; any other value, or none, gets 2025-11-25. The SDK's own handshake version comes back unchanged.
- [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools): every input schema is a valid JSON Schema object; every good call returns one text block; every bad argument value returns a tool result with `isError: true`, with no traceback and no local path in its text; an unknown tool and arguments that break the shape of the request (a string where an object belongs) return the protocol error `-32602`, and the message does not echo the unknown name.
- JSON-RPC: stdout carries one JSON-RPC 2.0 message per line and nothing else; one reply per request, in order, and none for a notification; `-32700` for a line that is not JSON; `-32601` for an unknown method; a batch array for clients on 2025-03-26, and `-32600` for an empty batch; a clean exit when stdin closes. With the SDK installed, its own message model parses every line.

The job fails when any of these tests skip. It does not cover how a particular host behaves on top of the protocol.

### host formats

`tests.test_host_payloads` feeds the hook commands of the project template the JSON payloads each host sends on stdin, and checks the template's config files against the formats each host documents:

| Host | Pages the tests follow |
|---|---|
| Claude Code | [hooks](https://code.claude.com/docs/en/hooks) (payloads of `SessionStart`, `PreToolUse` and `Stop`, exit 2 to block a stop), [MCP](https://code.claude.com/docs/en/mcp) (`.mcp.json`, key `mcpServers`) |
| GitHub Copilot | [hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference) (camelCase `sessionStart`, `preToolUse` and `agentStop`, and the PascalCase forms VS Code shares) |
| VS Code | [hooks](https://code.visualstudio.com/docs/copilot/customization/hooks), [MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration) (`.vscode/mcp.json`, key `servers`) |
| Codex | [MCP](https://learn.chatgpt.com/docs/extend/mcp) (`[mcp_servers.<name>]` in `.codex/config.toml`), [hooks](https://learn.chatgpt.com/docs/hooks) (`.codex/hooks.json`, payloads of `SessionStart`, `PreToolUse` and `Stop`, exit 2 to continue a turn) |

Each hook command must exit 0 (the stop check 0 or 2), finish inside the timeout the template gives it and print nothing or exactly one JSON object on stdout. A stop with problems exits 2 for Claude Code and Codex. For the Copilot forms, where exit 2 from a stop hook is only a warning, it prints `{"decision": "block", "reason": ...}` and exits 0. The config checks read `.mcp.json`, `.vscode/mcp.json`, `.vscode/settings.json` (as JSON with comments), the Claude Code hooks, the Copilot hooks in `.github/hooks/leks.json`, the Codex hooks in `.codex/hooks.json` (each command carries the profile of `.codex/config.toml`, since a Codex hook has no env of its own), `AGENTS.md` as the file `CLAUDE.md` pulls in, and `.codex/config.toml`. A last test feeds the checkers broken shapes, so they can fail.

These tests run in every unit job and in the mcp jobs, Windows included.

### real host sessions

Real Claude Code, Copilot and Codex sessions do not run in CI. Each needs a signed in account and spends that account's usage, and a hosted runner has neither. `sh bench/hosts/smoke.sh` is the check by hand: it finds which host CLIs are installed and signed in, runs a short set of sessions with LEKS on each and writes one summary file. `bench/hosts/README.md` has its settings and what it costs. So a change in how a host calls hooks or starts MCP servers shows up only in such a run, or when someone uses the host.

### a new user's install

By hand, on 14 November 2026, on a Mac with Apple silicon, in a throwaway home folder. From a fresh clone of main I followed the README's Quick start and [install](install.md) to the letter with the Python 3.13 and 3.14 that Homebrew installs: the venv, `pip install -e ".[models,pdf]"` with torch importing afterwards, the demo, `leks init`, a words-only config, `build`, `search`, `status`, the project template copied with the commands from install, and the loop that puts the venv's Python into the template. Then the MCP server and the hooks were started straight from `.mcp.json`, `.vscode/mcp.json`, `.codex/config.toml` and the three hook files, with a PATH that holds no venv: each server listed the seven tools and answered a search, and every hook exited 0. The same walk-through is why install starts with a version check and a venv: outside one, pip stops with `externally-managed-environment` on Homebrew's Python, and Apple's own Python 3.9 is too old for LEKS. It did not fetch the models or embed, and it did not run on Linux or Windows.

### packaging

Ubuntu, macOS and Windows, with Python 3.13, build 1.6.1 and twine 7.0.0. `tests.test_packaging` copies the clone to a temporary folder and builds there: the sdist, then the wheel from that sdist, which is what `python -m build` does by default ([build](https://build.pypa.io/en/stable/reference/cli.html)), so a file missing from the sdist also goes missing from the wheel. Then it:

- runs `twine check --strict` on both archives,
- checks that pyproject.toml, the archive names, `leks.__version__` and the installed metadata give the same version,
- checks the wheel: every module under `leks/` and the Swift sources of the OCR helper are in it, nothing else sits at its top level, and its entry point is `leks = leks.__main__:main`,
- checks the sdist: the same modules plus the project template under `examples/` (which [install](install.md) tells people to copy), pyproject.toml, the README and the license; `MANIFEST.in` puts the template there,
- installs the wheel into a new virtual environment with `pip install --no-index --no-deps`, since LEKS needs nothing beyond the standard library,
- runs `leks --help` and `python -m leks --help`, and `leks doctor` without a config, which must exit 1,
- uses the installed `leks` from outside the clone: `init`, `config check`, `doctor`, `build`, `search`, `status`, `doctor` again, `check`, and an MCP session over `serve` whose tool list matches the server's.

The job fails when any of these tests skip. It does not cover an install of the extras from PyPI (the wheel goes in without dependencies) or an upload to PyPI.

### coverage

Ubuntu with Python 3.13 and coverage 7.17.3, with the same extras as the unit jobs. The suite runs under `coverage run`. The XML and HTML reports go up as the `coverage` artifact, kept for 14 days, and then `coverage report` fails the job when the total is under `fail_under` in pyproject.toml, which is 87.

On 3.1 the job measured 88.3% of the statements in `leks/` (7,691 statements, 902 missed): 1,030 tests ran, 33 were skipped and none failed. The macOS-only tests are among the skipped ones, so a run on macOS gives a somewhat different total. The floor is 87, a point under that total.

It does not show that a covered line is checked by an assertion. Code in the processes the tests start (the CLI, the MCP server, the hooks, the model daemon) does not count: coverage measures a subprocess only when coverage is started in it as well ([coverage](https://coverage.readthedocs.io/en/latest/subprocess.html)), and this job leaves that off.

### lint

Ubuntu with Python 3.13:

- `ruff check .` with ruff 0.17.4 and the rules in `[tool.ruff.lint]`: pyflakes, the pycodestyle groups E4, E7 and E9, pycodestyle warnings and bugbear.
- actionlint 1.7.12 over the workflow files. It runs shellcheck over every `run:` block when it finds the command ([actionlint](https://github.com/rhysd/actionlint/blob/main/docs/checks.md#check-shellcheck-integ)), so the job installs shellcheck 0.11.0 from PyPI (shellcheck-py 0.11.0.1) and the result does not depend on the runner image. actionlint-py is a source package that downloads the actionlint binary from its GitHub release during the install.
- `.github/scripts/check_workflows.py` with PyYAML 6.0.3, for the rules of this repository that actionlint does not know: the [shared rules](#what-the-workflows-share) above, the triggers of each workflow, the full unit matrix with no cell left out, a positive whole `--floor` on every `run_suite.py` step, each test step of the [overview table](#overview) with its modules and with `--no-skips` where the table says no test may skip, the model tests and the lanes gate in models.yml, CodeQL, the secret scan over the whole history, bandit and pip-audit in security.yml, Dependabot for the actions and both pip folders, and an `==` pin on every line of `.github/ci/requirements-*.txt`.
- `tests.test_ci_scripts` with `--no-skips`: `run_suite.py` reads the `Ran` and `skipped` counts right, tells a hung run from one that just stopped, passes and fails a real run on its floor and fails a run that skipped under `--no-skips`; `check_workflows.py` passes the repository and catches a moving tag, `continue-on-error`, a missing matrix cell, an unpinned tool, the models workflow on pull requests, a model test step without `--no-skips`, a test step that loses its floor, its modules or its `--no-skips`, and a shallow checkout for the secret scan; and neither `TESTS_FLOOR` nor a floor that a step names for its own modules is higher than the tests on disk.

None of this shows what the code does at run time.

## models

Ubuntu with Python 3.12, on CPU. torch comes from the CPU wheel index, `https://download.pytorch.org/whl/cpu`, and goes in before the `models` extra, so pip keeps it and does not fetch the CUDA build from PyPI ([install](install.md#what-you-need) has the sizes). Then:

1. `leks init` writes a starter config with the pinned models into a fresh home.
2. The weights come from the Actions cache. `.github/scripts/models_ci.py key` builds the key from the pinned repository ids and revisions and the file patterns the fetch leaves out, so a new pin downloads afresh.
3. `models_ci.py check` runs the checks of `leks models check` (every file in the manifest written at fetch time is there with its size, and files up to 64 MiB also with their SHA-256) and fails unless both pinned models pass; `leks models check` itself only reports. On a miss or a bad copy, the job runs `leks models fetch` and checks again.
4. The weights go into the cache right after that check, so a failure later in the job keeps the download. A cached copy that fails the check is fetched again in that run, but the save step runs only on a cache miss and the key stays the same, so the bad entry stays and later runs fetch again until the pins change or someone deletes it (`gh cache delete`).
5. `tests.test_real_models` runs with `--no-skips` and a floor of 5 (below).
6. `bench/run_bench.py --models config` runs all five systems with the real models. It runs `hybrid` and `full` in strict mode: a question where a model lane is missing marks the whole system as skipped.
7. `bench/gate.py --require grep,words,fused,hybrid,full --lanes` fails the job unless all five systems ran, `hybrid` had the meaning lane with vectors for every passage, and `full` also ran the reranker.

The result files and the output of `pip freeze` go up as the `bench-models` artifact, kept for 30 days, also when a step failed.

| Key | Repository | Pinned revision |
|---|---|---|
| mdense (embedder) | lightonai/mDenseOn | a5fdb000f7a21da96c3bddde3a782ef777316df3 |
| mlate (reranker) | lightonai/mLateOn | bbd883ae1fadb563056ff73b1061ae8172a3fcda |

The two come to 2.54 GB ([install](install.md#what-you-need)). The Actions cache holds 10 GB per repository by default and drops an entry that nobody read for 7 days, and a pull request can restore what a run on main saved ([dependency caching](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)). The hub's own folder (`HF_HOME`) sits outside the cached folder, so only the fetched weights are saved.

### The model tests

`tests.test_real_models` builds an index over 12 short notes in a fresh home with the starter config, embeds it and searches it through the `leks` command, with the fetched weights:

- `leks models check` reports both pinned models as matching their manifest,
- the question "what happens to the computers if the cooling breaks" finds `notes/server-room.md` in its top 5, and the search reports the lanes `words, meaning, reranker`, so both models ran on every passage. The question shares no content word with that note, and the same search with `--words-only` does not rank it first, so the result comes from the models,
- the embedder returns unit-length vectors of 768 dimensions and puts the answer closer to the question than an unrelated note,
- the reranker scores the answer above an unrelated note.

These tests skip everywhere the weights are missing, which is why this job passes `--no-skips`: here a skip fails the job.

### No score floor for the model systems

The gate in this job checks that the model systems ran with their lanes and holds no score floor for them, so every run prints a `NO BASELINE` warning together with the baseline it would write. I have not measured the model systems on the Linux CPU runner, and PyTorch does not promise identical results across releases or platforms, or between CPU and GPU ([reproducibility](https://docs.pytorch.org/docs/stable/notes/randomness.html)), while every run installs the newest torch and transformers the extras allow. To add a floor once a few runs agree:

1. Download a run's `bench-models` artifact, for example with the GitHub CLI: `gh run download <run-id> -n bench-models -D bench-models` ([gh run download](https://cli.github.com/manual/gh_run_download)).
2. Write a baseline from the `bench` folder inside it:

   ```bash
   python3 bench/gate.py bench-models/bench --write-baseline bench/baseline_models.json \
     --require grep,words,fused,hybrid,full --tolerance-questions 1
   ```

3. Check that the numbers in the file match the ones the gate printed in the log, add `--baseline bench/baseline_models.json` to the gate step in models.yml, and commit both.

What this workflow does not cover:

- speed on anyone's computer, since a shared CPU runner says little about a laptop or a GPU,
- the scores on Apple silicon or a GPU, for the PyTorch reason above,
- the macOS sandbox around the model daemon, since on Linux the daemon runs without it (`leks/sandbox.py`),
- quality on anyone's own files, since the bench has 40 questions over a small fixture corpus.

I wrote this workflow without the weights at hand. The release script moves `main` only after it has passed on the release commit, so a release on `main` has passed it.

## The bench gate

`bench/gate.py` reads the newest `results_*.json` in the folder it is given, or the file itself, and checks:

1. every required system ran and no question raised an exception;
2. with `--lanes`, the model systems used their lanes (`hybrid` words and meaning, `full` the reranker as well) with vectors for every passage, and the embedding run over the corpus ended with code 0;
3. the run scored as many questions as the baseline; a question whose needs the build cannot meet is skipped, so a run without pypdf scores fewer;
4. every metric of every system in the baseline (top 1, top 5, MRR@10) is at least the baseline value less `tolerance_questions` divided by that system's number of answerable questions.

It exits 0 when the gate holds and 1 when it does not, with every reason printed and, inside Actions, also shown as an error annotation and in the job summary. Exit 2 means input it cannot judge: a missing or broken result file, a `--baseline` file that is missing or broken, a baseline made for another `--models` mode, or a broken tolerance in the baseline. A run with no `--baseline` at all is no failure: then only points 1 and 2 apply, and the gate says so with a warning. `tests.test_gate` checks each of these outcomes.

### The words baseline

`bench/baseline_words.json` comes from `python3 bench/run_bench.py --models none` (seed 7, 10 results per question) over the fixture corpus and its 40 questions. 37 of them have an answer in the corpus. The other 3 have none and only count toward the abstain numbers. The file records how it was made: on macOS (arm64) with Python 3.10.20, 3.11.15, 3.12.13 and 3.13.12 and pypdf 7.0.1, where all four runs gave every question the same rank.

| System | What runs | Top 1 | Top 5 | MRR@10 |
|---|---|---|---|---|
| grep | a scan of the files for the question's words, no index | 18 of 37 | 35 of 37 | 0.6707 |
| words | BM25 over the word index, no priors | 28 of 37 | 35 of 37 | 0.8387 |
| fused | every word lane, fused, with the priors | 26 of 37 | 35 of 37 | 0.8183 |

The tolerance is one question: each metric of each system may fall by 1 of 37 answerable questions (2.7 points), and a drop of two fails the gate. The unit jobs also run on Linux and Windows, where I have not measured the bench. The bench and the index walk the files in sorted order, but Python there may come with another SQLite version, and a tie between two equally scored passages can then fall the other way; one question of room absorbs that. When a run on GitHub drops further, the gate names the system and the metric, and the job keeps the result file with the rank of every question. A change that moves these numbers on purpose updates the file in the same commit.

## security

### codeql

CodeQL with its default queries for Python and build mode `none`, which the `build-mode` input of [init/action.yml](https://github.com/github/codeql-action/blob/33d7b85da1a04171e211befe2c739303b9a4c066/init/action.yml) lists as available for all interpreted languages. The results go to the repository's code scanning alerts. Whether an alert blocks a merge is a ruleset in the repository settings, outside the workflow ([merge protection](https://docs.github.com/en/code-security/code-scanning/managing-your-code-scanning-configuration/set-code-scanning-merge-protection)).

### secret scan

gitleaks 8.30.1 over the whole history: the checkout fetches every commit (`fetch-depth: 0`) and `gitleaks git --log-opts="--all"` scans every branch and tag, so a secret that was committed and later deleted is still found. The job downloads the release archive from GitHub and checks it against the SHA-256 that the gitleaks release publishes in `gitleaks_8.30.1_checksums.txt` (`551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb` for linux_x64), so a changed download fails the job. Any finding fails it too. `--redact` keeps the secret out of the log, and the SARIF report is kept for 14 days when the scan fails.

It does not find a secret in a form its rules do not know, or one that lives outside git.

### bandit

bandit 1.9.4 over `leks/` and `bench/` with the settings in `[tool.bandit]` in pyproject.toml. Bandit builds a syntax tree of each file and runs its plugins over the nodes ([bandit](https://pypi.org/project/bandit/)). The tests stay out, since they use `assert` and temporary files on purpose. Every skipped check has a comment there with its reason. It cannot see anything a check over the syntax of one file misses.

### pip-audit

Ubuntu with Python 3.10, 3.11 and 3.13 and pip-audit 2.10.1. The `models` extra needs Python 3.11 ([install](install.md)), so on 3.10 the job checks `.[pdf,test]` and on 3.11 and 3.13 `.[models,pdf,test]`. `pip install --dry-run --ignore-installed --report` resolves everything those extras would install, without installing it ([pip installation report](https://pip.pypa.io/en/stable/reference/installation-report/)). The job writes those exact versions to a requirements file, without LEKS itself (it is installed from the clone and is not on PyPI), and runs `pip-audit --requirement <file> --no-deps --strict --desc`. pip-audit looks each version up in the Python Packaging Advisory Database through the PyPI JSON API ([pip-audit](https://pypi.org/project/pip-audit/)), and `--strict` fails the job when it cannot collect a package.

It says nothing about advisories that are not published yet, or about the versions another platform or Python resolves to. The models workflow installs torch from the PyTorch CPU index, while the audit looks up torch as PyPI resolves it. The workflow also runs every Monday, since advisories appear without any change here.

### Dependabot

`.github/dependabot.yml` asks once a month for grouped pull requests with updates to the actions the workflows use, to the CI tools pinned in `.github/ci/requirements-*.txt` and to the extras in pyproject.toml ([Dependabot options](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference)). Every action is pinned to a full commit SHA with its version in a comment, so when such a pull request comes, check that each new SHA belongs to the release its comment names before merging it.

## public benchmark

`public-bench.yml` runs [the public benchmark](benchmark.md) as section 1 of that page describes: it fetches the PEPs at the pinned commit, checks them by hash, adds LEKS 3.0 as a worktree of commit `aa1e842` and runs bm25, LEKS and LEKS 3.0 on the same questions. With the `models` input on, the default, it also installs torch for CPU and runs LEKS with the pinned embedder and reranker, which downloads their weights. The result files are uploaded as the artifact `public-bench-results`. The workflow runs only when started by hand and holds no score to a floor. It fails when a step fails, and the run step fails when a system it asked for, or the link prior, did not run: `pep_bench.py` then exits 1 after it has written the result files. It has not run on GitHub yet.

## Run the checks on your computer

The commands of each job work outside GitHub too. On macOS, inside the activated venv from [Get LEKS](install.md#get-leks), so that `python3` is the venv's Python:

```bash
# the whole suite with its floor, as in the unit jobs
python3 -m pip install -e ".[pdf,test]"
python3 .github/scripts/run_suite.py --floor 1024

# the bench on words only, and its gate (results go to bench/results, which git ignores)
python3 bench/run_bench.py --models none
python3 bench/gate.py bench/results --baseline bench/baseline_words.json

# the tcp transport that windows uses, and the install report
LEKS_MODELD_TRANSPORT=tcp python3 -m unittest -v tests.test_transport tests.test_doctor
python3 -m leks doctor

# MCP conformance and the host formats
python3 -m pip install -r .github/ci/requirements-mcp.txt
python3 .github/scripts/run_suite.py --floor 11 --no-skips tests.test_mcp_conformance
python3 -m unittest -v tests.test_host_payloads

# packaging; python -m build fetches setuptools from PyPI
python3 -m pip install -r .github/ci/requirements-build.txt
LEKS_TEST_PACKAGING=1 python3 -m unittest -v tests.test_packaging

# coverage, with the floor from pyproject.toml
python3 -m pip install -r .github/ci/requirements-coverage.txt
python3 -m coverage run -m unittest discover -s tests -t .
python3 -m coverage report

# lint and the workflow rules
python3 -m pip install -r .github/ci/requirements-lint.txt
ruff check .
actionlint
python3 .github/scripts/check_workflows.py
python3 -m unittest -v tests.test_ci_scripts

# bandit
python3 -m pip install -r .github/ci/requirements-bandit.txt
bandit -c pyproject.toml -r leks bench

# pip-audit over what the extras resolve to on this computer
python3 -m pip install -r .github/ci/requirements-audit.txt
tmp=$(mktemp -d)
python3 -m pip install --dry-run --ignore-installed --quiet --report "$tmp/report.json" ".[models,pdf,test]"
python3 -c "import json, sys; r = json.load(open(sys.argv[1])); print('\n'.join(sorted('%s==%s' % (i['metadata']['name'], i['metadata']['version']) for i in r['install'] if i['metadata']['name'].lower() != 'leks')))" "$tmp/report.json" > "$tmp/requirements.txt"
pip-audit --requirement "$tmp/requirements.txt" --no-deps --strict --desc
```

The dry run leaves a `leks.egg-info` folder in the clone, which git ignores. pip resolves for your platform and Python, so the list can differ from the one on GitHub. In a sandbox that refuses local ports, the real TCP daemon tests skip with a reason that says so.

The secret scan needs [gitleaks](https://github.com/gitleaks/gitleaks) 8.30.1 on the path:

```bash
gitleaks git --log-opts="--all" --redact --no-banner --exit-code 1 .
```

The models job, on a computer with room for the weights:

```bash
python3 -m pip install -e ".[models,pdf,test]"
python3 -m leks init
python3 -m leks models fetch
LEKS_TEST_MODELS=1 LEKS_TEST_WEIGHTS="$HOME/.leks/models" python3 .github/scripts/run_suite.py --floor 5 --no-skips tests.test_real_models
python3 bench/run_bench.py --models config
python3 bench/gate.py bench/results --require grep,words,fused,hybrid,full --lanes
```

`leks init` and `leks models fetch` work in your LEKS home (`~/.leks` unless `LEKS_HOME` says otherwise), and the bench reads the weights from there. On a Mac the scores can differ from a Linux CPU run, for the PyTorch reason above.
