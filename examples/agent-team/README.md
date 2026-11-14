# Agent team

An optional layer on top of LEKS: a small team of agents for a project whose notes and code LEKS indexes. LEKS itself needs none of it. You talk to one agent, the orchestrator, which is also the architect: it reads the record, splits a request into tasks, briefs one role agent per task, checks each result against evidence and closes the loop. Nine role agents do the work, each from one brief. The same team is here in the formats of GitHub Copilot, Claude Code and Codex.

Every agent works from the LEKS tools (`search`, `read`, `locate`, `tried`, `neighbours`, `state`, `status`) and from the skills in [the project template](../project-template), so set up the template first.

| Path | Host | What it holds |
|---|---|---|
| `.github/agents/*.agent.md` | GitHub Copilot in VS Code, the Copilot CLI and GitHub.com | the orchestrator and the nine role agents |
| `.claude/agents/*.md` | Claude Code | the same ten agents |
| `.codex/agents/*.toml` | Codex | the nine role agents |
| `AGENTS.team.md` | Codex | the orchestrator, as a section to append to the project's `AGENTS.md` |

## The team

| Agent | Works on | Skills it follows |
|---|---|---|
| orchestrator | the plan, the briefs and the check of every result | task-brief, evidence-check, leks-decide |
| app-dev | application code and its tests | bug-triage, dependency-update |
| pipeline | data jobs, build jobs, batch scripts and CI | pipeline-run |
| unity | Unity scenes and assets through the Unity MCP server | unity-scene-change, blender-to-unity, unity-build-check |
| blender | Blender files through the Blender MCP server | blender-asset-change, blender-to-unity |
| review | reviews a change and edits nothing | code-review, evidence-check |
| repository | git state, versions, the changelog and release checks | release-check, dependency-update |
| knowledge | the project record in LEKS: decisions, state notes, links, freshness | leks-brief, leks-decide, supersede-decision, leks-reweave, leks-handoff, leks-close-session, leks-index-health |
| docs | READMEs and user docs | docs-update |
| research | outside sources, each with a link | source-note |

## How a request flows

1. The user asks the orchestrator.
2. The orchestrator reads the record: `state` for the newest state note, then `search` and `tried` on the topic. When the record already answers, it answers with citations and stops.
3. It writes one brief per task with the task-brief skill: the goal, the files the agent may open, the done criteria, the tests, the LEKS citations to read and what to report.
4. A role agent works on the brief and reports the paths it changed, the commands it ran with their exact results, its citations and what it could not verify.
5. The orchestrator checks the report with the evidence-check skill, and code changes go to the review agent. A task that fails its check goes back once. After a second failure the orchestrator stops and tells the user.
6. The knowledge agent records new decisions and replaces the state note, and the orchestrator answers the user with the evidence for each part.

## Install

The commands assume the LEKS clone is in `~/leks`. Run them in the root of your project.

### GitHub Copilot

```bash
A=~/leks/examples/agent-team    # wherever your clone is
mkdir -p .github/agents
cp -n "$A"/.github/agents/*.agent.md .github/agents/
```

- **VS Code:** pick orchestrator in the agents list of the Chat view and ask it. It starts the role agents as subagents. Its `tools` list holds the `agent` tool set, which VS Code asks a coordinating agent to have; in the Copilot harness the delegation runs through the harness's own `task` tool.
- **Copilot CLI:** run `copilot --agent=orchestrator`, or pick it with `/agent` in a session.
- **GitHub.com:** the cloud agent reads the same files. It ignores `handoffs`.

Handoffs: the orchestrator has one button per role agent, and every role agent has one back to the orchestrator. A handoff switches the same conversation to the other agent, so you can watch and steer each step instead of letting the orchestrator run subagents. VS Code shows these buttons only in its Local harness, which its docs mark as deprecated and due for removal in February 2027. The Copilot schema does not accept `handoffs`, so the loop never depends on them.

No file sets `agents`, the list of subagents a coordinator may call. Left out, VS Code lets the orchestrator call every agent, and the Copilot schema does not accept the field. No role agent has the `agent` tool, so only the orchestrator delegates.

The tool lists name LEKS as `leks/*`, the Unity server as `unityMCP/*` and the Blender server as `blender/*`. Those are server names: the template's `.vscode/mcp.json` registers `leks`, and [docs/integrations.md](../../docs/integrations.md) shows entries for the other two. Copilot ignores tool names it does not know, so an agent whose server is missing still loads.

### Claude Code

```bash
A=~/leks/examples/agent-team    # wherever your clone is
mkdir -p .claude/agents
cp -n "$A"/.claude/agents/*.md .claude/agents/
claude --agent orchestrator
```

`claude --agent orchestrator` runs the main session as the orchestrator. To make that the default in a project, add `"agent": "orchestrator"` to its `.claude/settings.json`. The orchestrator hands each brief to a role agent with the Agent tool. In Claude Code a subagent may start subagents of its own, so every role agent has `Agent` in `disallowedTools`, and the review agent also has the editing tools removed (`Edit`, `Write`, `NotebookEdit`). Its shell can still write files, so its promise to edit nothing rests on its instructions.

### Codex

```bash
A=~/leks/examples/agent-team    # wherever your clone is
mkdir -p .codex/agents
cp -n "$A"/.codex/agents/*.toml .codex/agents/
cat "$A/AGENTS.team.md" >> AGENTS.md
```

Codex starts custom agents as subagents of a session, so the orchestrator goes into `AGENTS.md`, which the main session reads. That section asks the main session to start the role agents by name, and Codex delegates when project instructions ask it to. Codex names use an underscore where the other hosts use a hyphen, so app-dev is app_dev in Codex. Subagents inherit the MCP servers, the skills, the sandbox and the approval settings of the parent session, and an agent file cannot override the MCP servers, so every role sees the Unity and Blender servers when the session has them. The Codex docs do not say whether a subagent can start subagents of its own, so in Codex only the role instructions keep the team to one level. An agent file has no tool list either, so in Codex the review agent's rule to edit nothing rests on its instructions alone.

The template's `.codex/config.toml` registers the LEKS server (see [the template README](../project-template/README.md#codex)). The Unity and Blender servers get tables of their own next to it:

```toml
[mcp_servers.blender]
command = "the command from the server's docs"
args = ["its", "arguments"]
```

Codex reads skills from `.agents/skills` and follows symlinked skill folders. The template keeps them in `.claude/skills`, so link each folder:

```bash
mkdir -p .agents/skills
for d in .claude/skills/*/; do n=$(basename "$d"); ln -s "../../.claude/skills/$n" ".agents/skills/$n"; done
```

The notes check runs at the end of a turn through the hook files of all three hosts. Codex runs the template's `.codex/hooks.json` once you have reviewed and trusted it with `/hooks`.

## Models

No agent file picks a model, so each agent runs on the model the session uses. Every file has a commented model line to switch on, for example a smaller model for the review or research role. Copilot takes a model name from its model picker, Claude Code an alias, a full model id or `inherit`, and Codex a model name.

## Which folders to copy

Copy the folders for the hosts you use. VS Code also reads `.claude/agents`, so with both folders in one project it may list each agent twice. I have not checked which copy it runs then.

## What is checked, and what is not

`tests/test_kit.py` in the LEKS repository parses every file here and checks each field against the host's documentation below: names match file names, every handoff and every named skill exists, only the orchestrator can delegate, the review agent has no editing tool in Copilot and Claude Code, all three hosts define the same roles with the same descriptions and the same instructions, and every LEKS tool or command a file names exists. [docs/agents.md](../../docs/agents.md) has the details. I have not run the team in a live session of any host.

## Sources

The formats above follow these pages, as read on 13 November 2026:

- VS Code, custom agents: <https://code.visualstudio.com/docs/copilot/customization/custom-agents>
- VS Code, custom agent file reference (fields, harnesses, tool aliases): <https://code.visualstudio.com/docs/agents/reference/custom-agent-file-reference>
- VS Code, subagents (the `agent` tool set, the `agents` field, Local and Copilot harnesses): <https://code.visualstudio.com/docs/copilot/agents/subagents>
- VS Code, agent skills (skill folders per harness): <https://code.visualstudio.com/docs/copilot/customization/agent-skills>
- GitHub, custom agents configuration (properties, ignored `handoffs`, tool aliases, 30,000 character limit): <https://docs.github.com/en/copilot/reference/custom-agents-configuration>
- GitHub, invoking custom agents in the Copilot CLI: <https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/invoke-custom-agents>
- GitHub, agent skills (`.github/skills`, `.claude/skills` or `.agents/skills`): <https://docs.github.com/en/copilot/concepts/agents/about-agent-skills>
- Claude Code, subagents (fields, `--agent`, nesting): <https://code.claude.com/docs/en/sub-agents>
- Claude Code, skills: <https://code.claude.com/docs/en/skills>
- Codex, subagents and custom agent files: <https://learn.chatgpt.com/docs/agent-configuration/subagents>
- Codex, AGENTS.md: <https://learn.chatgpt.com/docs/agent-configuration/agents-md>
- Codex, skills (`.agents/skills`, symlinks): <https://learn.chatgpt.com/docs/build-skills>
