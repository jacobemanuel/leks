# Host cost harness

`run.py` sends the same questions to one coding CLI in four set-ups and keeps what each session cost, as the host reports it. `summarise.py` turns the run folders into paired numbers. `smoke.sh` checks which CLIs are installed and signed in, runs a small set on each and writes one summary file. `fake_host.py` stands in for a host, so all of this can be tested with no account. What each host bills, and what the harness can and cannot tell yet, is in [docs/costs.md](../../docs/costs.md).

The hosts are Claude Code (`claude`), GitHub Copilot CLI (`copilot`) and Codex CLI (`codex`), each in its headless JSON mode.

## The arms

| arm | what the agent has |
|---|---|
| `plain` | the host's own file tools, no LEKS |
| `leks` | the file tools and the LEKS server, with the template's `AGENTS.md` without its Skills and Agent team sections, which point to files a bench project does not have |
| `team` | an orchestrator with two role agents, `knowledge` (searches with LEKS, reports passages with `path:line`) and `review` (checks a draft against the lines it cites); all on the main model |
| `subagent` | the main agent hands every search to a `searcher` subagent that runs on a small model and has LEKS |

Every arm gets its own copy of the bench corpus (`bench/fixtures/corpus`) as a separate project folder, so no arm sees another arm's instruction files. Shell, file writing, web access and skills are switched off in all arms; the tool that starts subagents is on only in `team` and `subagent`. The role agents and their prompts are fixed in `run.py` (`AGENTS`, `ARM_SECTIONS`) and are written the way each host expects them:

| host | role agents | small model for `searcher` |
|---|---|---|
| Claude Code | `--agents` JSON on the command line | `model` field, `--small-model` (default `haiku`) |
| Copilot CLI | `.github/agents/NAME.agent.md` in the project | `model` with `model-policy: required` |
| Codex CLI | `.codex/agents/NAME.toml` in the project, `agents.enabled` on | `model` in the TOML file |

The index is built once from a separate copy of the corpus, words only unless `--models-from` names a LEKS config with models, and the server is checked to list its seven tools before any session starts. Every question runs once per arm and repeat, each as one new non-interactive session. The order of the arms rotates by question and repeat, so no arm always runs first or last.

## The task set

`tasks.json` fixes the questions before any host run: the 20 odd-numbered questions of `bench/fixtures/questions.json`, two repeats, 600 s per session. `--all` takes all 40, `--ids` names your own. The smoke set is four questions (one fact, one current-state, one code and one PDF question, each with a gold file) and one repeat, so 16 sessions per host.

The automatic task check passes when the answer names a gold file of the question, and the record also says when it names a stale file. That check is coarse: an answer can name the right file and still be wrong. For right and wrong, `summarise.py --sheet` writes a blind grading sheet (the arm is hidden behind a code) and `--grades` reads the filled sheet back.

## What each session records

Each session folder under `sessions/` keeps the host's event stream (`stdout.jsonl`), its stderr, `meta.json` (argv, arm, question, times, exit code) and `result.json`. Each result is also appended to `results.jsonl` in the run folder.

| field | meaning |
|---|---|
| `cost`, `unit` | the cost in the host's own unit: USD from Claude Code's `total_cost_usd`, AI credits from Copilot's `totalNanoAiu` divided by 1e9, USD for Codex only when `--rates` gives prices |
| `usd`, `credits`, `premium_requests` | Copilot: credits at 0.01 USD each, and the premium request figure when the CLI prints one |
| `input_tokens`, `cached_tokens`, `output_tokens`, `reasoning_tokens` | as the host reports them; `null` where it does not |
| `turns`, `model_calls`, `tool_calls` | from the event stream |
| `leks_calls`, `subagent_calls`, `shell_calls` | calls to the LEKS tools, subagent starts, shell commands |
| `models` | every model the host names in the session's usage |
| `check_passed`, `cites_stale` | the automatic task check |
| `wall_s`, `exit_code`, `timed_out`, `errors` | how the session ended |
| `peeks` | strings that only turn up when an agent goes after the answer key, the grading key or the index |

## Running it

Python 3.10 or later with `leks` importable from this clone. Real runs spend money or plan usage on the account the CLI is signed in to; `--dry-run` and the fake host spend nothing.

```sh
# prepare everything, write plan.json, run no session
python3 bench/hosts/run.py claude --dry-run --out /tmp/hosts-dry

# the fake host, in any of the three event formats
python3 bench/hosts/run.py fake --format copilot --smoke --out /tmp/hosts-fake
python3 bench/hosts/summarise.py /tmp/hosts-fake

# real runs
python3 bench/hosts/run.py claude --model sonnet --effort medium --small-model haiku
python3 bench/hosts/run.py copilot --model auto --small-model MODEL --max-credits 50
python3 bench/hosts/run.py codex --model MODEL --small-model MODEL --rates IN,CACHED,OUT
```

`--resume` continues a run folder and skips the sessions that already have a `meta.json`; it refuses when the settings differ. `--arms plain,leks` runs a subset. `run.py` exits 1 when a session it ran did not end ok (a host error, a timeout or no answer); that session is still in `results.jsonl`, and `--resume` does not run it again. Claude Code sessions carry `--max-budget-usd` (1 by default), Copilot sessions `--max-ai-credits` when you pass `--max-credits`.

`summarise.py RUN_DIR` prints a table per arm (sessions, task checks with a Wilson interval, mean and total cost, tokens, wall time, LEKS and subagent use, models) and a table per pair of arms: each arm against `plain`, and `team` and `subagent` against `leks`. For each measure it gives the per-question mean of both arms, the paired difference and the ratio of sums, each with a percentile bootstrap interval over questions (10,000 resamples by default).

## The smoke script

```sh
sh bench/hosts/smoke.sh [OUT_DIR]
```

For each host on `PATH` it reads the version, sends one short prompt to see that the CLI is signed in, runs the smoke set and summarises it. `status.md` has one row per host (installed, version, signed in, ran, note) and `summary.md` has that table and the summary of each host that ran. A host that is missing, not signed in or not configured gets a row that says so, and the script goes on with the next one. The script exits 0 only when every host in `SMOKE_HOSTS` ran and every session ended ok. Otherwise it exits 1 after `summary.md` is written, so name only the hosts you have in `SMOKE_HOSTS`.

| setting | default | meaning |
|---|---|---|
| `SMOKE_HOSTS` | `claude copilot codex` | hosts to try |
| `SMOKE_ARMS` | `plain,leks,team,subagent` | arms to run |
| `CLAUDE_MODEL`, `CLAUDE_SMALL_MODEL` | `sonnet`, `haiku` | |
| `COPILOT_MODEL`, `COPILOT_SMALL_MODEL` | `auto`, empty | empty small model: the `subagent` arm is left out |
| `CODEX_MODEL`, `CODEX_SMALL_MODEL` | empty, empty | empty `CODEX_MODEL`: codex is checked but not run |
| `CODEX_RATES` | empty | `IN,CACHED,OUT` in USD per 1M tokens, so codex gets a cost figure |
| `PYTHON` | `python3` | interpreter with `leks` importable |

## Tests

`tests/test_hosts_bench.py` runs the whole harness against the fake host in all three event formats, and `smoke.sh` against a stand-in `claude` on `PATH`. The fake host's token counts and prices are made up and fixed per arm; they test the plumbing and say nothing about any host.
