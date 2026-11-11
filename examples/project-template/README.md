# Project template

Copy the files in this folder into the root of a project to connect LEKS to Claude Code and to GitHub Copilot in VS Code. Both hosts then get the same tools, the same brief at session start and the same notes check at the end of a turn.

| File | Read by | What it does |
|---|---|---|
| `AGENTS.md` | Copilot, and Claude Code through `CLAUDE.md` | Search before answering, cite `path:line`, call `tried` before proposing a change |
| `CLAUDE.md` | Claude Code | Imports `AGENTS.md` |
| `.mcp.json` | Claude Code | Starts the LEKS MCP server |
| `.claude/settings.json` | Claude Code | Brief and index update at session start, model warm-up before a search, notes check when a turn ends |
| `.claude/skills/leks-search/SKILL.md` | Claude Code, Copilot | The search routine as a skill |
| `.vscode/mcp.json` | Copilot | Starts the LEKS MCP server |
| `.github/hooks/leks.json` | Copilot | Brief and index update at session start, notes check when the agent stops |
| `.vscode/settings.json` | Copilot | Keeps VS Code from also running the Claude Code hooks, and a note on the Explore subagent's model |

## Before you copy

1. Install LEKS (see the main README) and run `leks init`. The config is written to `~/.leks/config.json`. List your folders under `roots`.
2. Build the index once with `leks build`.
3. Replace the profile name `architect` in these files with a profile from your config, or delete the `LEKS_PROFILE` lines to use the config's default profile.
4. If `python3` on your PATH is not the interpreter that has LEKS installed, write the full path to that interpreter in place of `python3` in every file here.

LEKS looks for its config in `$LEKS_CONFIG`, then in `$LEKS_HOME/config.json`, then in `~/.leks/config.json`. To keep one config per project, add `LEKS_CONFIG` with an absolute path to the `env` blocks of `.mcp.json`, `.claude/settings.json`, `.vscode/mcp.json` and `.github/hooks/leks.json`. In `.vscode/mcp.json` the path may start with `${workspaceFolder}`.

## Files both hosts read

VS Code reads `AGENTS.md`, `CLAUDE.md` and the skills in `.claude/skills` by default, so one copy of each serves both hosts. It reads the hooks in `.claude/settings.json` only when `chat.useClaudeHooks` is on. The template keeps that setting off, so each host runs its own hook file once.

The notes check blocks the end of a turn at most once: on the second attempt the host marks the stop as already blocked and the check lets it through.
