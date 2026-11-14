# Hosts: Claude Code, GitHub Copilot and Codex

LEKS serves three hosts from one set of files, [examples/project-template](../examples/project-template). This page says what each host reads, how the hooks behave in each, and which model does the reading. I checked the Claude Code and Copilot behaviour on this page in the installed builds of Claude Code 2.1.319 and of VS Code 1.144.1 with its bundled GitHub Copilot Chat 0.72.0. For Codex I followed its documentation as I read it on 13 November 2026 ([MCP](https://learn.chatgpt.com/docs/extend/mcp), [hooks](https://learn.chatgpt.com/docs/hooks)); the tests check the Codex files against those pages, and I have not run a Codex session with them. Later versions may differ.

## What each host reads

| | Claude Code | GitHub Copilot in VS Code | Codex |
|---|---|---|---|
| MCP server | `.mcp.json` | `.vscode/mcp.json` | `.codex/config.toml` in a trusted project, else `~/.codex/config.toml` |
| instructions | `CLAUDE.md`, which imports `AGENTS.md` | `AGENTS.md`, and `CLAUDE.md` while `chat.useClaudeMdFile` is on (the default) | `AGENTS.md` |
| skills | `.claude/skills` | the same folder | `.agents/skills`, where you link the template's skills ([skills.md](skills.md)) |
| hooks | `.claude/settings.json` | `.github/hooks/leks.json` | `.codex/hooks.json` in a trusted project, once you have trusted the hooks |
| profile | `LEKS_PROFILE` in the `env` of `.mcp.json` and of `.claude/settings.json` | `LEKS_PROFILE` in the `env` of `.vscode/mcp.json` and of each hook | `LEKS_PROFILE` in `[mcp_servers.leks.env]`, and `--profile` on each hook command |

VS Code looks for skills in `.agents/skills`, `.github/skills` and `.claude/skills` in the workspace, and in `~/.agents/skills`, `~/.copilot/skills` and `~/.claude/skills`. It also runs the hooks in `.claude/settings.json` when `chat.useClaudeHooks` is on. That setting is off by default, and the template's `.vscode/settings.json` keeps it off, so each host runs its own hook file once.

## Codex

Codex starts the servers in a project's `.codex/config.toml` and loads its `.codex/hooks.json` only when the project is trusted; elsewhere the two `leks` tables of the config go into `~/.codex/config.toml`. A hook that an administrator does not manage runs only after you have reviewed and trusted it with `/hooks`, and Codex keeps that trust against the hook's hash, so a changed hook needs a new review. Hooks are on by default, and `hooks = false` under `[features]` in `config.toml` turns them off.

Codex starts an MCP server with a short list of variables from your shell plus the server's `env` table ([agents.md](agents.md#codex) lists them), so a `LEKS_CONFIG` or `LEKS_HOME` from your shell has to go into `[mcp_servers.leks.env]`. The hook docs name no `env` field for a hook handler, so the template's hook commands carry the profile on their command line (`--profile architect`), and a config per project goes there too, as `--config` with an absolute path. Codex stops a tool call after `tool_timeout_sec`, 60 s by default; the template sets 120, because a search soon after the server starts may wait for the query vector and a first reranker call ([configuration.md](configuration.md#budgets)).

## Hooks

| moment | Claude Code | Copilot | Codex | command | what it does |
|---|---|---|---|---|---|
| session start | `SessionStart` | `sessionStart` | `SessionStart` | `leks hook brief` | at most five lines: which tools to use, up to four current state notes, up to six files changed in the last three days, up to six notes past their freshness budget, and up to six revisit dates that have come |
| session start | `SessionStart` | `sessionStart` | `SessionStart` | `leks hook update` | updates the index in a background process when the last update is older than `update.min_interval` (1,800 s by default) |
| before a search | `PreToolUse` on `mcp__leks__search` and `mcp__leks__tried` | none | `PreToolUse` on the same two tools | `leks hook warm` | wakes the model helper so the first search does not wait for it |
| end of a turn | `Stop` | `agentStop` | `Stop` | `leks check` | checks the notes (below) |

Codex reads a matcher as a regular expression on the tool name and names an MCP tool `mcp__<server>__<tool>`, as Claude Code does, so the two share the warm-up hook. Copilot hooks have no tool matcher, so the template gives Copilot no warm-up hook. A search that finds the model helper down starts it itself and waits for the models within the search's time budgets ([configuration.md](configuration.md#budgets)): 45 s for the query vector and 30 s for a first reranker call (see [the 3.0 report, section 4.13](report-3.0.md#413-the-first-search-after-a-pause)). A model that is not ready in time sits out that one search, the lanes line says why, and the next search finds it loaded.

**The brief.** Copilot takes session-start context only from JSON on stdout, in `hookSpecificOutput.additionalContext`, and drops plain text. `leks hook brief` prints that one JSON line, which Claude Code and Codex read as well. It prints nothing when no index exists yet, and it always exits 0, so a missing index never blocks a session.

**The notes check.** At the end of each turn `leks check` looks at the notes (`.md`, `.markdown`) changed since its last run, so its first run in a folder records them and reports nothing. It reports an empty note, a wikilink or relative Markdown link that leads nowhere, a new note that no other note links to (entry notes such as README, index or AGENTS are exempt), a link to a note that was removed or renamed, edits that break an edit contract of the config, and em dashes, which `"check": {"em_dash": false}` in the config or `--no-em-dash` turns off. It needs no index and no model. It walks the project folder: in Claude Code the folder the session started in (`CLAUDE_PROJECT_DIR`), even after the agent changed folders in its shell, which I tested in a real session; in Copilot the folder the hook runs in, which the template sets to the repository root with `"cwd": "."`, following GitHub's hook reference; in Codex the session's working folder, in which Codex runs every hook command. In Claude Code and Codex the findings go to stderr, at most 20 lines, with exit 2. Copilot treats exit 2 from a stop hook as a warning only, so for its payload `leks check` prints the findings as `{"decision": "block", "reason": ...}` on stdout and exits 0. Each host hands them back to the agent before it may stop. Exit 1 means the check could not run, for example on a folder with more than 100,000 files. All three hosts pass `stop_hook_active` in the hook input when this turn's stop has already been blocked once, and `leks check` then lets the stop through, so a turn is blocked at most once.

## Which model does the reading

A host can hand reading and searching to a subagent, and that subagent can run on a different model from the one picked in the chat. This matters for LEKS, because the subagent is then the one that reads the search results.

- **GitHub Copilot.** The Explore subagent uses the model named in `github.copilot.chat.exploreAgent.model`. The setting is empty by default, and Explore then takes the first available model from a fallback list that starts with Claude Haiku 4.5, followed by Gemini 3 Flash (Preview) and Auto. With a large model picked in the chat, the files and the LEKS results that Explore reads can therefore be read by a small model. The template's `.vscode/settings.json` has the line that sets the model, commented out.
- **Claude Code.** The built-in Explore subagent inherits the session's model. When the session runs a model ranked above the Opus family, Explore is capped at Opus.
- **Codex.** A subagent whose agent file sets no `model` inherits the parent session's model and reasoning effort. The Codex files of the agent team leave `model` commented out, so the role agents read with the session's model unless you set one ([agents.md](agents.md#codex)).
- **My measurements.** In every end-to-end session in [the 3.0 report](report-3.0.md) the agent had no tool to start a subagent, and the CLI's per-model usage listed a single model in each session. Every number in the report therefore comes from one model that both read the results and wrote the answer.

**Measured with a small reader.** I ran the end-to-end test of the report again with the small model (alias haiku) as the only model in Claude Code. It answered 23 of 35 questions right with LEKS and 17 without, where the main model answered 31 and 28 in the same grading pass, and with LEKS it cost about what the main model cost per correct answer ([the 3.0 report, section 4.14](report-3.0.md#414-a-small-model-as-the-agent-amendment-m)). Copilot's own pattern, where the chat model reads a summary from Explore, is a separate set-up. To keep a small model away from the reading in Copilot, set `github.copilot.chat.exploreAgent.model` to a model from the picker; the template's `.vscode/settings.json` has the line.

## Config and interpreter

LEKS finds its config in `$LEKS_CONFIG`, then `$LEKS_HOME/config.json`, then `~/.leks/config.json`. It never looks in the working folder. The simplest set-up is one config for all projects and a `LEKS_PROFILE` per project. For a config per project, set `LEKS_CONFIG` to an absolute path in every `env` block of the template and in the `[mcp_servers.leks.env]` table of `.codex/config.toml`, and add `--config` with the same path to each command in `.codex/hooks.json`; in `.vscode/mcp.json` the path may start with `${workspaceFolder}`.

Every file in the template calls `python3`. When that is not the interpreter that has LEKS installed, write the full path to the right interpreter in its place, in every file.
