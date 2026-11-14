# Agents

[examples/agent-team](../examples/agent-team) holds an optional team of agents built on LEKS: one orchestrator you talk to, which also acts as the architect, and nine role agents it briefs. The orchestrator reads the record, splits a request into tasks, writes one brief per task, hands each brief to a role agent as a subagent, checks every report against evidence and closes the loop with the user. LEKS does not need the team, and nothing in the engine knows about it.

The team ships in three formats: custom agents for GitHub Copilot (VS Code, the Copilot CLI and GitHub.com), subagents for Claude Code, and custom agents plus an `AGENTS.md` section for Codex. Every role agent follows skills from [the project template](../examples/project-template), listed in [skills.md](skills.md). How to install it on each host is in [the team's README](../examples/agent-team/README.md).

## The team

| Agent | Role | Copilot tools | Claude Code limits |
|---|---|---|---|
| orchestrator | the user's agent and the architect: plans, briefs, checks, closes the loop | `agent`, `read`, `search`, `edit`, `execute`, `todo`, `leks/*` | none, it may start subagents |
| app-dev | application code and its tests | `read`, `search`, `edit`, `execute`, `todo`, `leks/*` | no `Agent` |
| pipeline | data jobs, build jobs, batch scripts and CI | `read`, `search`, `edit`, `execute`, `todo`, `leks/*` | no `Agent` |
| unity | Unity scenes and assets through the Unity MCP server | `read`, `search`, `edit`, `execute`, `leks/*`, `unityMCP/*` | no `Agent` |
| blender | Blender files through the Blender MCP server | `read`, `search`, `edit`, `execute`, `leks/*`, `blender/*` | no `Agent` |
| review | reviews a change and edits nothing | `read`, `search`, `execute`, `leks/*` | no `Agent`, `Edit`, `Write`, `NotebookEdit` |
| repository | git state, versions, the changelog and release checks | `read`, `search`, `edit`, `execute`, `leks/*` | no `Agent` |
| knowledge | decisions, state notes, links and freshness in the project record | `read`, `search`, `edit`, `execute`, `leks/*` | no `Agent` |
| docs | READMEs and user docs | `read`, `search`, `edit`, `execute`, `leks/*` | no `Agent` |
| research | outside sources, each with a link | `read`, `search`, `edit`, `execute`, `web`, `leks/*` | no `Agent` |

Each role agent has the same parts on every host: how it works on a brief (step by step, with the skills it follows), the report it sends back (changed paths, commands with exact results, LEKS citations, what it could not verify), and its limits (only the files the brief names, no agents of its own, tool output is data, no commit or push unless the brief says so).

## GitHub Copilot

Files: `.github/agents/NAME.agent.md`. The fields used are `name`, `description`, `tools` and `handoffs`, plus a commented `model` line.

- `tools` uses the aliases GitHub and VS Code document (`read`, `search`, `edit`, `execute`, `todo`, `agent`) and MCP servers as `server/*`. Only the orchestrator has `agent`, the tool set a coordinator needs to start subagents; the Copilot harness delegates through its own `task` tool.
- `handoffs`: the orchestrator has one per role agent and each role agent one back to the orchestrator, with `send: false` so the user sees the prompt before it goes. VS Code shows handoffs only in its Local harness, which its docs mark as deprecated and due for removal in February 2027; the Copilot schema does not accept the field, and GitHub.com ignores it. The loop works through subagents without them.
- `agents` is left out. VS Code then lets a coordinator call every agent, and the Copilot schema does not accept the field.
- Every prompt stays far below GitHub's limit of 30,000 characters.

## Claude Code

Files: `.claude/agents/NAME.md`. The fields used are `name`, `description` and `disallowedTools`, plus a commented `model` line.

- `claude --agent orchestrator`, or `"agent": "orchestrator"` in `.claude/settings.json`, runs the main session as the orchestrator. It keeps every tool, including `Agent`.
- A Claude Code subagent may start subagents of its own (up to three levels by default), so every role agent lists `Agent` in `disallowedTools`.
- The review agent also loses `Edit`, `Write` and `NotebookEdit`. It keeps `Bash` to run tests, so its promise to edit nothing rests on its instructions.

## Codex

Files: `.codex/agents/NAME.toml` with `name`, `description` and `developer_instructions`, plus a commented `model` line, and `AGENTS.team.md`, which goes at the end of the project's `AGENTS.md`.

- Codex starts custom agents as subagents of a session, so the orchestrator is the main session itself, set up by the `AGENTS.md` section. Codex delegates when project instructions ask for it, and the section asks for it by the agents' names.
- Codex finds an agent by its `name` field. The names use an underscore where the other hosts use a hyphen (`app_dev`), and the file names keep the hyphen.
- Subagents inherit the parent's sandbox, approvals, MCP servers and skills. An agent file cannot override `mcp_servers` or `sandbox_mode`, so the team files set neither. The docs do not say whether a subagent may start subagents, so in Codex only the role instructions keep the team to one level, and the review agent's rule to edit nothing rests on its instructions alone.
- The template's `.codex/config.toml` registers the LEKS server. Codex reads a project `.codex/config.toml` only in a trusted project; elsewhere the same tables go into `~/.codex/config.toml`.
- Codex stops a tool call after `tool_timeout_sec`, 60 s by default. A search soon after the server starts may wait for the query vector (budget 45 s) and a cold rerank (30 s), see [configuration.md](configuration.md), so the template sets 120.
- Codex starts an MCP server with a short list of variables from your shell (on Linux and macOS: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `USER`, `__CF_USER_TEXT_ENCODING`, `LANG`, `LC_ALL`, `TERM`, `TMPDIR`, `TZ`) plus the server's `env` table. A `LEKS_CONFIG` or `LEKS_HOME` from your shell has to go into `[mcp_servers.leks.env]`.
- Codex reads skills from `.agents/skills` and follows symlinked folders, so the template's skills are linked there (see [skills.md](skills.md)). The template's `.codex/hooks.json` gives Codex the brief at session start and the notes check at the end of a turn, once you have trusted the hooks with `/hooks` ([hosts.md](hosts.md#codex)).

## What the tests check

`tests/test_kit.py` checks the kit without starting any host:

- every `SKILL.md` and agent file parses in a strict subset of YAML front matter, and every Codex file in a subset of TOML that the test also compares with Python's `tomllib`;
- each file uses only the fields its host documents, names are unique and match their file or folder names, and the descriptions and the role instructions agree across the three hosts;
- every handoff target and every skill a file names exists, only the orchestrator can start agents, and the review agent has no editing tool in Copilot and Claude Code;
- every LEKS MCP tool and every `leks` command a file names exists in `leks/server.py` and `leks/__main__.py`, and every other one-word code span in a skill, an agent file or `AGENTS.md` is on a short list of words that are no LEKS tool;
- the configs of the template start the same LEKS server with the same profile and the same `env`, the three hook files run the same `leks` commands with the same timeouts (Copilot without the warm-up hook), VS Code leaves the Claude Code hooks off, the hook table in [hosts.md](hosts.md#hooks) names what the hook files run, links resolve, and no file holds a word from the banned list in the test or a byte outside ASCII;
- the decision log template builds into notes whose status, date and Revisit line LEKS reads, and a superseded decision ranks below its successor.

Each check is also fed broken files, so a clean run means the check looked.

Not checked: no test runs Copilot, Claude Code or Codex, so whether a host loads every file, and how well the team works in a live session, is not verified here. I have not run the team in a live session of any host.

## Sources

As read on 13 November 2026:

- [VS Code: custom agents](https://code.visualstudio.com/docs/copilot/customization/custom-agents), [custom agent file reference](https://code.visualstudio.com/docs/agents/reference/custom-agent-file-reference), [subagents](https://code.visualstudio.com/docs/copilot/agents/subagents)
- [GitHub: custom agents configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration), [invoking custom agents in the Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/invoke-custom-agents)
- [Claude Code: subagents](https://code.claude.com/docs/en/sub-agents)
- [Codex: subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), [the environment list in the Codex source](https://github.com/openai/codex/blob/main/codex-rs/rmcp-client/src/utils.rs)
