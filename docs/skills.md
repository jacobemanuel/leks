# Skills

The project template carries 26 skills in [examples/project-template/.claude/skills](../examples/project-template/.claude/skills), one folder each with a `SKILL.md`. A skill is a short routine an agent follows when a task matches its description: concrete steps, the LEKS tools to call at each step, and a "Done when" section that says how to tell the work is finished. They are optional. LEKS works the same without them.

Each `SKILL.md` uses only the fields of the [Agent Skills specification](https://agentskills.io/specification), `name` and `description`, so the same file works in every host below. The name matches the folder name and the description says when to use the skill.

## Where each host reads them

| Host | Project folder | How to call one |
|---|---|---|
| Claude Code | `.claude/skills` | it picks one by its description, or you type `/name` |
| GitHub Copilot | `.github/skills`, `.claude/skills` or `.agents/skills` (GitHub's docs); VS Code's table names `.github/skills` for its Copilot harness | it picks one by its description, or you type `/name` in VS Code |
| Codex | `.agents/skills`, symlinked folders followed | it picks one by its description, or you mention it with `$name` or pick it with `/skills` |

The template keeps the skills in `.claude/skills`. For Codex, link them into `.agents/skills`:

```bash
mkdir -p .agents/skills
for d in .claude/skills/*/; do n=$(basename "$d"); ln -s "../../.claude/skills/$n" ".agents/skills/$n"; done
```

When a Copilot session in VS Code does not list them, link them into `.github/skills` the same way. A host that reads more than one of these folders may then list a skill twice; the links point at one file, so both entries run the same text.

Sources, as read on 13 November 2026: [Claude Code skills](https://code.claude.com/docs/en/skills), [VS Code agent skills](https://code.visualstudio.com/docs/copilot/customization/agent-skills), [GitHub agent skills](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills), [Codex skills](https://learn.chatgpt.com/docs/build-skills).

## The skills

| Skill | Use it when | LEKS tools it calls |
|---|---|---|
| `leks-search` | a question should be answered from the project's own record | `search`, `read`, `locate`, `tried` |
| `leks-brief` | a session starts, or before planning work | `state`, `read`, `search`, `status` |
| `leks-decide` | a choice between options is made or asked for: one recommendation first, then the strongest counterargument, then the decision note with a Revisit line | `tried`, `search`, `read` |
| `supersede-decision` | a decision changes | `search`, `read`, `leks build` |
| `leks-reweave` | a note was edited or added and the notes around it may no longer hold | `neighbours` (3.1), `search`, `locate`, `read`, `leks check --accept` (3.1) |
| `leks-handoff` | work stops before it is finished | `state`, `read` |
| `leks-close-session` | a session ends | `state`, `read` |
| `leks-index-health` | search misses a note you know exists, or after a config change | `status`, `search`, `locate`, `state`, `leks config`, `leks build`, `leks check` |
| `task-brief` | before any task is delegated | `search`, `tried`, `read` |
| `evidence-check` | before a report or a claim is accepted | `read`, `search` |
| `code-review` | before a change is merged or reported done | `search`, `tried` |
| `bug-triage` | a bug is reported | `search`, `tried` |
| `dependency-update` | a package, tool or add-on version changes | `tried`, `search`, `read` |
| `release-check` | before a tag or a publish | `search`, `read` |
| `docs-update` | after a change users can see | `search`, `locate`, `read` |
| `pipeline-run` | a data or build job whose output others use | `search`, `tried` |
| `perf-budget` | a change may cost frame time, latency, memory or size | `search`, `read` |
| `unity-scene-change` | a Unity scene or asset changes through the Unity MCP server | `search`, `read` |
| `unity-build-check` | a Unity player build, before a release or after a change of packages or render settings | `search`, `read`, `tried` |
| `blender-asset-change` | a Blender file changes through the Blender MCP server | `search`, `read` |
| `blender-to-unity` | an asset goes from Blender into a Unity project | `search`, `read` |
| `experiment-plan` | before a benchmark, an A/B comparison, a load test or a tuning run | `tried`, `search`, `read` |
| `results-report` | after a run, before anyone acts on its numbers | `search`, `read` |
| `source-note` | an outside source informs a decision | `search` |
| `meeting-notes` | during or right after a meeting | `search` |
| `weekly-review` | once a week, or before planning | `search` |

`leks build`, `leks check` and `leks config` are commands of the LEKS command line, which the skills run as `python3 -m leks ...` (see [cli.md](cli.md)). The others are tools of the LEKS MCP server.

The Unity and Blender skills need those tools' MCP servers next to LEKS; [integrations.md](integrations.md) shows the set-up of both. They are written as general engineering practice: take every number from the current decision, write the check before the change, see it fail, make the change, see the check pass, and record which decision the result follows.

## Parts that are new in LEKS 3.1

Some skills use parts of LEKS that are new in release 3.1, and say so where they use them, so a project still on 3.0 can use them too. Each has a way to work on 3.0:

- `neighbours`, as an MCP tool and as `leks neighbours PATH`: the notes to bring in line after a note was edited. On 3.0, leks-reweave falls back to `search` for the note's id, title and terms, and `locate` for its file name.
- Freshness budgets and Revisit dates in the session brief: the brief names the notes older than their budget and the decisions whose Revisit date has come. On 3.0, leks-brief reads the state notes' dates and the decision log by hand.
- Edit contracts in `leks check` (`check.contracts` in the config, with the types `write-once`, `frozen` and `living`), and `leks check --accept PATH` to keep a changed note on purpose. On 3.0, the check has no contracts.
- Status, date, Revisit and Supersedes as plain lines under a note's title, which is how [the decision log](../examples/project-template/decisions/DECISIONS.md) writes them. 3.0 reads status and date from YAML front matter only.

## Credit

The idea behind leks-reweave, going back over the notes around a changed one so the record stays consistent, and its name follow the reweave step of [Modular Context](https://github.com/klemensgc/modular-context-obsidian-plugin), an MIT-licensed Obsidian plugin. The skill text and the `neighbours` tool are written for LEKS.
