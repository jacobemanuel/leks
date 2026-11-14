# Project template

Copy the files in this folder, apart from this README, into the root of a project to connect LEKS to Claude Code, to GitHub Copilot in VS Code and to Codex. All three get the same tools, the same brief at session start and the same notes check at the end of a turn; Codex runs its hooks once you have trusted them (see [Codex](#codex)).

| File | Read by | What it does |
|---|---|---|
| `AGENTS.md` | Copilot, Codex, and Claude Code through `CLAUDE.md` | Search before answering, cite `path:line`, call `tried` before proposing a change, one note per decision |
| `CLAUDE.md` | Claude Code | Imports `AGENTS.md` and says how to call the skills and the agent team |
| `.mcp.json` | Claude Code | Starts the LEKS MCP server |
| `.claude/settings.json` | Claude Code | Brief and index update at session start, model warm-up before a search, notes check when a turn ends |
| `.claude/skills/` | Claude Code, Copilot, Codex through links | 26 skills, one folder each, listed in [docs/skills.md](../../docs/skills.md) |
| `.vscode/mcp.json` | Copilot | Starts the LEKS MCP server |
| `.github/hooks/leks.json` | Copilot | Brief and index update at session start, notes check when the agent stops |
| `.vscode/settings.json` | Copilot | Keeps VS Code from also running the Claude Code hooks, and a note on the Explore subagent's model |
| `.codex/config.toml` | Codex | Starts the LEKS MCP server in a trusted project, with a longer tool timeout |
| `.codex/hooks.json` | Codex | Brief and index update at session start, model warm-up before a search, notes check when a turn ends, once you trust the hooks |
| `decisions/DECISIONS.md` | every host, through LEKS | The decision log: the index table and the template for one note per decision |
| `STATE.md` | every host, through LEKS | The state note, which the skills link new notes from and leks-close-session writes again; leave it out when the project already has one |

## Before you copy

1. Install LEKS (see the main README) and run `leks init`. The config is written to `~/.leks/config.json`. List your folders under `roots`.
2. Build the index once with `leks build`.
3. Replace the profile name `architect` in these files with a profile from your config, or delete the `LEKS_PROFILE` lines and the `--profile architect` of each command in `.codex/hooks.json` to use the config's default profile.
4. If `python3` on your PATH is not the interpreter that has LEKS installed, write the full path to that interpreter in place of `python3` in every file here. For the venv of the install guide, one loop does it ([install](../../docs/install.md#connect-claude-code-copilot-and-codex)).
5. Once the files are in place, run `python3 -m leks check` once in the project root, with that same interpreter in place of `python3`. Its first run in a folder only records the notes, so after this run the check at the end of a turn reports what that turn changed.

LEKS looks for its config in `$LEKS_CONFIG`, then in `$LEKS_HOME/config.json`, then in `~/.leks/config.json`. To keep one config per project, add `LEKS_CONFIG` with an absolute path to the `env` blocks of `.mcp.json`, `.claude/settings.json`, `.vscode/mcp.json` and `.github/hooks/leks.json`, and to the `[mcp_servers.leks.env]` table of `.codex/config.toml`. A Codex hook has no `env` of its own, so add `--config` with the same path to each command in `.codex/hooks.json`. In `.vscode/mcp.json` the path may start with `${workspaceFolder}`.

## Files both hosts read

VS Code reads `AGENTS.md`, `CLAUDE.md` and the skills in `.claude/skills` by default, so one copy of each serves both hosts. It reads the hooks in `.claude/settings.json` only when `chat.useClaudeHooks` is on. The template keeps that setting off, so each host runs its own hook file once.

The notes check blocks the end of a turn at most once: on the second attempt the host marks the stop as already blocked and the check lets it through.

## Codex

Codex reads `AGENTS.md`, and it starts the servers in a project's `.codex/config.toml` only when you have marked the project as trusted. Otherwise put the two `leks` tables into `~/.codex/config.toml`. Codex hands the server a short list of environment variables, `HOME` and `PATH` among them, so LEKS finds `~/.leks/config.json`, but a `LEKS_CONFIG` or `LEKS_HOME` set in your shell only arrives through the `[mcp_servers.leks.env]` table. The file also sets `tool_timeout_sec` to 120, since Codex stops a tool call after 60 s by default and a search that waits for the models can take longer ([docs/agents.md](../../docs/agents.md#codex) has the sources).

Codex reads skills from `.agents/skills`, so link the template's skills there:

```bash
mkdir -p .agents/skills
for d in .claude/skills/*/; do n=$(basename "$d"); ln -s "../../.claude/skills/$n" ".agents/skills/$n"; done
```

`.codex/hooks.json` runs the brief and the index update when a session starts, wakes the model helper before a search and runs the notes check when a turn ends, as the Claude Code hooks do. Codex loads a project's hooks only in a trusted project and runs them after you have reviewed and trusted them with `/hooks`; a changed hook needs a new review. The commands carry the profile on their command line (`--profile architect`), so change it there together with `LEKS_PROFILE` in `.codex/config.toml`. Until the hooks run, `AGENTS.md` asks the agent to run `python3 -m leks check --changed` on the notes it changed before it finishes.

## Decisions

[decisions/DECISIONS.md](decisions/DECISIONS.md) explains the decision log and holds the note template: one note per decision with Status, Date, Revisit and Supersedes lines under its title, the options weighed and the strongest counterargument. The skills leks-decide and supersede-decision walk through it, and [docs/skills.md](../../docs/skills.md) lists every skill.

## The agent team

The team in [examples/agent-team](../agent-team) is optional: one orchestrator you talk to, which plans, briefs nine role agents and checks their results. With the template in place, per host (from the LEKS clone, here `~/leks`):

- **GitHub Copilot:** `cp ~/leks/examples/agent-team/.github/agents/*.agent.md .github/agents/`, then pick orchestrator in the Chat view's agent list, or run `copilot --agent=orchestrator`.
- **Claude Code:** `cp ~/leks/examples/agent-team/.claude/agents/*.md .claude/agents/`, then run `claude --agent orchestrator`.
- **Codex:** `cp ~/leks/examples/agent-team/.codex/agents/*.toml .codex/agents/` and `cat ~/leks/examples/agent-team/AGENTS.team.md >> AGENTS.md`, link the skills as shown under [Codex](#codex), then start Codex in the project as usual: the main session is the orchestrator.

Create the target folder first (`mkdir -p`). The team's [README](../agent-team/README.md) has the details, the Unity and Blender servers, and the documentation each format follows.
