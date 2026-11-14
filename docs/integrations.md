# Integrations: LEKS beside tool servers

LEKS only searches. It runs next to MCP servers that act in a tool, so an agent can take a decision from the project's notes and carry it out in that tool within one session. Any MCP server can sit next to it. I tested two pairings end to end, LEKS with the Blender MCP server and LEKS with MCP for Unity, in ten sessions each. Everything the test used apart from the tools themselves is in [bench/integrations](../bench/integrations): the notes, the check scripts, the runners and a summary of every session.

## The test

The project holds five notes about a display pedestal for a product scene.

| note | what it says |
|---|---|
| `STATE.md` | the pedestal follows D-07 and D-03 is superseded; lighting follows D-05 |
| `decisions/D-07_pedestal.md` | status current, supersedes D-03: a matte blue cylinder 0.60 m across and 0.90 m high, its base on the floor at the origin |
| `decisions/D-03_pedestal.md` | status superseded by D-07: a glossy red box 0.50 by 0.50 by 1.10 m |
| `decisions/D-05_lighting.md` | one light 2 m above the top of the pedestal |
| `history/2026-07_pedestal_ideas.md` | an early sketch that was dropped |

Each tool has its own wording of the notes with the same numbers. Blender's D-07 asks for 64 vertices around, z up, a base colour and a roughness of 0.8. Unity's asks for the built-in cylinder, y up, the Standard shader, an albedo and a smoothness of 0.2.

Each run makes a fresh project from [examples/project-template](../examples/project-template), adds the tool's server and starts one Claude Code session: CLI 2.1.319, the model alias `sonnet` at medium effort, a budget of 3 USD. The prompt asks the agent to build the display pedestal "exactly as our current spec says", to take the spec from the project notes, to save the result and to say which note it followed. It names no note. The session may call the LEKS tools and the tool server's tools. The shell, the file and web tools, subagents and notebooks are switched off, and the permission mode denies any other tool without asking. No session tried one; each used the CLI's own tool loader (ToolSearch) once or twice to load the tool definitions. LEKS ran on words only, with no models, because the test is about the hand-over between the two servers.

After the session a separate run of the tool opens the saved file and checks it against D-07, plus one check that nothing from D-03 slipped in. A size may be off by 2 mm and a colour or roughness value by 0.01. The check script enters the project only after the session, so the agent never sees it.

## Results

**Ten sessions per tool**, run on 10 November 2026 on one Mac. The rows per session are in [bench/integrations/results](../bench/integrations/results).

| | Blender | Unity |
|---|---|---|
| sessions that passed every check | 10 of 10 | 10 of 10 |
| Wilson 95% interval | 0.72 to 1.00 | 0.72 to 1.00 |
| checks per session | 10 | 12 |
| median wall time | 17.6 s | 24.4 s |
| median turns | 6 | 13 |
| median cost | 0.069 USD | 0.115 USD |
| cost of all ten sessions | 0.77 USD | 1.13 USD |
| median LEKS calls | 3 | 4 |
| median tool-server calls | 1 | 7.5 |
| first search ranked D-03 above D-07 | 10 of 10 | 10 of 10 |
| final answer names D-07 and calls D-03 superseded | 10 of 10 | 10 of 10 |

Costs are the CLI's own figure at API prices. The last row is a plain text match on the final answer. The first session per tool cost about twice what later sessions of the same kind cost (Blender 0.157 against a median of 0.069 USD, Unity 0.176 against 0.080 to 0.083 USD), because it wrote about three times as many tokens to the prompt cache.

**What the agents did.** Every session listed the state notes (LEKS `state`), searched once and read D-07. Some also read the state note (3 Blender and 5 Unity sessions) or the lighting note D-05 (3 Unity sessions). None opened D-03 or the history note. All twenty answers mention the state note and 18 mention D-05. No session added the light from D-05: every Blender file holds the pedestal alone, and the Unity scenes hold the pedestal plus, where the agent kept the open default scene, its camera and light.

**Blender.** All ten sessions sent the same search, "display pedestal spec dimensions", and built the pedestal with one call of `execute_blender_code_for_cli`, which runs Python in a background Blender on a given file. Each agent's script built the pedestal and saved the file.

**Unity.** The sessions searched for "pedestal spec dimensions" (8) or "display pedestal spec dimensions" (2) and took two routes. Four sessions sent one `execute_code` call, C# that the package runs inside the editor, and kept the open default scene. Six made a new scene and built it with the package's separate tools, `manage_scene`, `manage_gameobject`, `manage_material` and `manage_asset`, in seven or eight calls. In each of those six the first attempt to create the material failed because the folder `Assets/Materials` did not exist yet; the agent created the folder, or saved the material next to the scene, and tried again. In the same six sessions the editor reloaded its scripts (a domain reload) and the package's bridge restarted on port 6400 or the next free one; the calls after it went through. The one-call route took a median of 15.6 s and 0.082 USD, the other 30.2 s and 0.118 USD.

**The ranking.** LEKS takes a note's status from its front matter (`status:`), and it links an older note to its newer version through a `superseded_by:` line or a phrase such as "superseded by" followed by a `[[link]]` ([Status](how-it-works.md#status), [Newer versions](how-it-works.md#newer-versions)). These notes give their status and date in plain lines such as "Status: superseded by D-07.", with no link. So LEKS listed both decisions with status `unknown`, linked neither to the other, dated all five notes by the file time of the copy, and ordered the two decisions on words alone. All five notes came back in every first search, and D-03 came above D-07 in all twenty: first and second for "display pedestal spec dimensions", second and third behind the history note for "pedestal spec dimensions". The agents still built D-07, going by the status lines in the snippets and by the state note, which LEKS marks current and which every first search returned. When I added front matter to the two decisions (`status: current` on D-07; `status: superseded` and `superseded_by: "[[D-07_pedestal]]"` on D-03), the same two searches ranked D-07 above D-03 and marked D-03 superseded; for the shorter query the history note stayed first. I ran no agent session on that version.

**In 3.1.** LEKS 3.1 reads status and date lines under the title and ids such as D-07 in them ([Lines under the title](how-it-works.md#front-matter), [Newer versions](how-it-works.md#newer-versions)), so the notes above need no front matter. `tests/test_status_lines.py` builds an index of each tool's five notes as they are and runs both session queries and four other ways to ask: D-07 ranks above D-03 in every one, with status current and the date of its `Date:` line, and D-03 is marked superseded with D-07 as its newer version. The same test fails on 3.0, where D-03 comes first. I ran no agent session on 3.1.

## Blender

**Server.** Blender MCP 1.0.0 from Blender Lab, GPL-3.0-or-later, documented at <https://www.blender.org/lab/mcp-server/>. Its tools whose names end in `_for_cli` start Blender in the background on a `.blend` file, so they need no open Blender window and no add-on. I used them with Blender 5.2.2 on macOS. The tools that work on a running Blender through its add-on were not part of the test.

**Set-up.** Add the server to the project's `.mcp.json` next to `leks`. I ran it from a local copy of the server with `uv`:

```json
{
  "mcpServers": {
    "leks": {
      "command": "python3",
      "args": ["-m", "leks", "serve"],
      "env": {"LEKS_PROFILE": "architect"}
    },
    "blender": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/blender_mcp", "blender-mcp"],
      "env": {"BLENDER_PATH": "/Applications/Blender.app/Contents/MacOS/Blender"}
    }
  }
}
```

**Checks, 10.** The object `Pedestal`, its diameter, height, base at z = 0 and centre on the origin, 64 vertices around, the material name, base colour and roughness, and no material from D-03. The runner starts each session from an empty `.blend` file it makes itself.

## Unity

**Server.** MCP for Unity 10.3.0 by Coplay, MIT, <https://github.com/CoplayDev/unity-mcp>. It has two parts: a Unity package, `com.coplaydev.unity-mcp`, which runs inside the editor and opens a local bridge, and a Python server, `mcp-for-unity`, which the agent's host starts and which talks to the bridge. I used it with Unity 6000.3.19f1 and the built-in render pipeline on macOS.

**Set-up.**

- Add the package to the project's `Packages/manifest.json`. I used a local checkout of the tag `v10.3.0`: `"com.coplaydev.unity-mcp": "file:/path/to/unity-mcp/MCPForUnity"`.
- Run `uv sync --frozen` once in the checkout's `Server` folder and add the server to `.mcp.json` next to `leks`:

  ```json
  "unityMCP": {
    "command": "uv",
    "args": ["run", "--frozen", "--directory", "/path/to/unity-mcp/Server", "mcp-for-unity", "--transport", "stdio"],
    "env": {"UNITY_MCP_DISABLE_TELEMETRY": "1", "UNITY_MCP_DISABLE_FOCUS_NUDGE": "1"}
  }
  ```

  The first variable turns the server's telemetry off. The second stops it from bringing the editor window to the front, which it does on macOS so that a background editor is not throttled; an editor in batch mode has no window.
- Keep the editor running with the project open. I ran it in batch mode without a window (`-batchmode -nographics`), which the package allows only with `UNITY_MCP_ALLOW_BATCH=1` in the editor's environment. The bridge listens on 127.0.0.1, port 6400 or the next free one, and the server finds it through the status files the package writes under `~/.unity-mcp`.
- Set three of the package's editor preferences: stdio transport instead of HTTP, telemetry off, and no rewriting of MCP client configs when the editor starts. They are stored per user, outside the project. The runner sets them with a small editor script, and `run.sh clear` removes them.

With the editor window open, the package's own window offers the same settings.

**Checks, 12.** The scene saved at `Assets/Scenes/Pedestal.unity`, exactly one object named `Pedestal`, Unity's built-in cylinder mesh, its diameter, height, base at y = 0 and centre on the origin, the material name, the Standard shader, albedo and smoothness, and no material from D-03.

## What the two tests show

- In 20 of 20 sessions an agent took the current decision from the notes through LEKS and built it in the tool, and a script it never saw confirmed the result. With 10 of 10 per tool the Wilson 95% interval runs from 0.72 to 1.00.
- The sessions differ only by the model's sampling: the prompt, the notes and the tools were the same each time. It is one small project with one task per tool.
- The test checks the hand-over between the servers; retrieval quality is measured in the [report](report-3.0.md). With five notes every note came back in the first search, and the agents picked D-07 by the status lines and the state note.
- Set-up: Claude Code as the host (alias sonnet), Blender started in the background on a file, and the Unity editor in batch mode.

## Run it yourself

The runners, what they need and how to summarise your own runs are in [bench/integrations/README.md](../bench/integrations/README.md).
