# Tool-server test

The question: does an agent take the current decision from the project notes through LEKS and carry it out in another tool through that tool's MCP server? This folder holds everything the test used apart from the tools themselves: the five notes per tool, the check scripts, the runners and a summary of every session I ran. The write-up is in [docs/integrations.md](../../docs/integrations.md) and in section 4.15 of the [report](../../docs/report-3.0.md#415-leks-beside-a-tool-server).

## What one run does

1. Makes a fresh project from [examples/project-template](../../examples/project-template), puts the tool's five notes into `notes/` and builds a LEKS index on words only, with no models.
2. Starts one Claude Code session with the prompt in the runner. The session may use the LEKS tools and the tool server's tools and nothing else: the shell, the file and web tools, subagents and notebooks are off, the budget is 3 USD, and the model is the CLI alias `sonnet` at medium effort. The prompt names no note.
3. Opens the saved file in a separate run of the tool and checks it against D-07, plus one check that nothing from the superseded D-03 slipped in. The agent never sees the check script.

| | Blender | Unity |
|---|---|---|
| tool server | Blender Lab MCP 1.0.0 | MCP for Unity 10.3.0 |
| tool | Blender 5.2.2 | Unity 6000.3.19f1, built-in render pipeline, editor in batch mode |
| the agent saves | `pedestal.blend`, starting from an empty `start.blend` | `Assets/Scenes/Pedestal.unity` |
| check script | [blender/verify_pedestal.py](blender/verify_pedestal.py), 10 checks | [unity/Editor/VerifyPedestal.cs](unity/Editor/VerifyPedestal.cs), 12 checks |
| notes | [blender/notes](blender/notes) | [unity/notes](unity/notes) |

The two sets of notes say the same thing in each tool's terms: Blender's D-07 asks for 64 vertices around, z up, a base colour and a roughness; Unity's asks for the built-in cylinder, y up, the Standard shader, an albedo and a smoothness.

## Run it

```sh
BLENDER_MCP_DIR=/path/to/blender_mcp sh bench/integrations/blender/run.sh 1
UNITY=/path/to/Unity.app/Contents/MacOS/Unity UNITY_MCP_DIR=/path/to/unity-mcp sh bench/integrations/unity/run.sh 1
```

Both need `claude` (the Claude Code CLI), `uv`, and a `python3` of version 3.10 or later first on `PATH`; the runners put this repo on `PYTHONPATH`, and LEKS on words only needs nothing beyond the standard library. The Blender runner defaults `BLENDER` to the macOS app. For Unity, check out `CoplayDev/unity-mcp` at tag `v10.3.0` and run `uv sync --frozen` in its `Server` folder once. Each run writes to `bench/integrations/runs/<tool>_<id>/`: the session as stream JSON, the check result in `verify.txt` and the tool's logs. Set `OUT` to write somewhere else.

The Unity runner makes an empty project with the package once (`runs/unity_base`) and copies it for every run. Before the first run it sets three MCP for Unity editor preferences, which are stored per user, outside the project: stdio transport, telemetry off, and no rewriting of MCP client configs at start-up. `sh bench/integrations/unity/run.sh clear` removes them again.

```sh
python3 bench/integrations/summarise.py bench/integrations/runs
```

prints one row per run and the totals per tool, with a Wilson 95% interval for the share of sessions that passed every check.

## My runs

[results/](results) holds the summary of the twenty sessions I ran on 10 November 2026, ten per tool, with Claude Code 2.1.319 on macOS: [results_20261110.md](results/results_20261110.md) as tables and [results_20261110.json](results/results_20261110.json) with every field. One row per session gives its LEKS calls, searches, reads, tool calls, turns, time, cost, check result and where D-07 and D-03 stood in the first search. The raw session logs stay with me because they hold local paths.

After the twenty sessions I ran each runner once more, as it stands in this folder, to check it end to end. Both runs passed every check; they are not in the summary.
