# Hosts: Claude Code and GitHub Copilot

LEKS serves both hosts from one set of files, [examples/project-template](../examples/project-template). This page says what each host reads, how the hooks behave in each, and which model does the reading. I checked the host behaviour on this page in the installed builds of Claude Code 2.1.319 and of VS Code 1.144.1 with its bundled GitHub Copilot Chat 0.72.0. Later versions may differ.

## What each host reads

| | Claude Code | GitHub Copilot in VS Code |
|---|---|---|
| MCP server | `.mcp.json` | `.vscode/mcp.json` |
| instructions | `CLAUDE.md`, which imports `AGENTS.md` | `AGENTS.md`, and `CLAUDE.md` while `chat.useClaudeMdFile` is on (the default) |
| skill | `.claude/skills/leks-search/SKILL.md` | the same file |
| hooks | `.claude/settings.json` | `.github/hooks/leks.json` |
| profile | `LEKS_PROFILE` in the `env` of `.mcp.json` and of `.claude/settings.json` | `LEKS_PROFILE` in the `env` of `.vscode/mcp.json` and of each hook |

VS Code looks for skills in `.agents/skills`, `.github/skills` and `.claude/skills` in the workspace, and in `~/.agents/skills`, `~/.copilot/skills` and `~/.claude/skills`. It also runs the hooks in `.claude/settings.json` when `chat.useClaudeHooks` is on. That setting is off by default, and the template's `.vscode/settings.json` keeps it off, so each host runs its own hook file once.

## Hooks

| moment | Claude Code | Copilot | command | what it does |
|---|---|---|---|---|
| session start | `SessionStart` | `sessionStart` | `leks hook brief` | at most three lines: which tools to use, up to four current state notes, and up to six files changed in the last three days |
| session start | `SessionStart` | `sessionStart` | `leks hook update` | updates the index in a background process when the last update is older than `update.min_interval` (1,800 s by default) |
| before a search | `PreToolUse` on `mcp__leks__search` and `mcp__leks__tried` | none | `leks hook warm` | wakes the model helper so the first search does not wait for it |
| end of a turn | `Stop` | `agentStop` | `leks check` | checks the notes (below) |

Copilot hooks have no tool matcher, so the template gives Copilot no warm-up hook. A search that finds the model helper down starts it itself and waits for the models within the search's time budgets ([configuration.md](configuration.md#budgets)): 45 s for the query vector and 30 s for a first reranker call (see the report, section 4.13). A model that is not ready in time sits out that one search, the lanes line says why, and the next search finds it loaded.

**The brief.** Copilot takes session-start context only from JSON on stdout, in `hookSpecificOutput.additionalContext`, and drops plain text. `leks hook brief` prints that one JSON line, which Claude Code reads as well. It prints nothing when no index exists yet, and it always exits 0, so a missing index never blocks a session.

**The notes check.** At the end of each turn `leks check` looks at the notes (`.md`, `.markdown`) changed since its last run, so its first run in a folder records them and reports nothing. It reports an empty note, a wikilink or relative Markdown link that leads nowhere, a new note that no other note links to (entry notes such as README, index or AGENTS are exempt), a link to a note that was removed or renamed, and em dashes, which `"check": {"em_dash": false}` in the config or `--no-em-dash` turns off. It needs no index and no model. It walks the project folder: in Claude Code the folder the session started in (`CLAUDE_PROJECT_DIR`), even after the agent changed folders in its shell, which I tested in a real session; in Copilot the folder the hook runs in, which the template sets to the repository root with `"cwd": "."`, following GitHub's hook reference. Findings go to stderr, at most 20 lines, with exit 2, and the host hands them back to the agent before it may stop. Exit 1 means the check could not run, for example on a folder with more than 100,000 files. Both hosts pass `stop_hook_active` in the hook input when this turn's stop has already been blocked once, and `leks check` then lets the stop through, so a turn is blocked at most once.

## Which model does the reading

A host can hand reading and searching to a subagent, and that subagent can run on a different model from the one picked in the chat. This matters for LEKS, because the subagent is then the one that reads the search results.

- **GitHub Copilot.** The Explore subagent uses the model named in `github.copilot.chat.exploreAgent.model`. The setting is empty by default, and Explore then takes the first available model from a fallback list that starts with Claude Haiku 4.5, followed by Gemini 3 Flash (Preview) and Auto. With a large model picked in the chat, the files and the LEKS results that Explore reads can therefore be read by a small model. The template's `.vscode/settings.json` has the line that sets the model, commented out.
- **Claude Code.** The built-in Explore subagent inherits the session's model. When the session runs a model ranked above the Opus family, Explore is capped at Opus.
- **My measurements.** In every end-to-end session in the report the agent had no tool to start a subagent, and the CLI's per-model usage listed a single model in each session. Every number in the report therefore comes from one model that both read the results and wrote the answer.

**Measured with a small reader.** I ran the end-to-end test of the report again with the small model (alias haiku) as the only model in Claude Code. It answered 23 of 35 questions right with LEKS and 17 without, where the main model answered 31 and 28 in the same grading pass, and with LEKS it cost about what the main model cost per correct answer (report, section 4.14). Copilot's own pattern, where the chat model reads a summary from Explore, is a separate set-up. To keep a small model away from the reading in Copilot, set `github.copilot.chat.exploreAgent.model` to a model from the picker; the template's `.vscode/settings.json` has the line.

## Config and interpreter

LEKS finds its config in `$LEKS_CONFIG`, then `$LEKS_HOME/config.json`, then `~/.leks/config.json`. It never looks in the working folder. The simplest set-up is one config for all projects and a `LEKS_PROFILE` per project. For a config per project, set `LEKS_CONFIG` to an absolute path in every `env` block of the template; in `.vscode/mcp.json` the path may start with `${workspaceFolder}`.

Every file in the template calls `python3`. When that is not the interpreter that has LEKS installed, write the full path to the right interpreter in its place, in every file.
