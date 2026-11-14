---
name: unity-scene-change
description: Change a Unity scene or asset through the Unity MCP server, from a plan based on the current decision. A check script written before the change verifies the saved scene, and the result is recorded with the decision it follows. Use it for any scene change.
---

# Unity scene change

This skill needs MCP for Unity running under the server name `unityMCP` next to `leks`. The setup is in docs/integrations.md in the LEKS repository.

## Steps

1. `search` for the spec and `read` the current decision. Write down every number with its unit and tolerance. Never build from a note marked superseded; follow its `NEWER VERSION` line.
2. Plan the change: the objects and components, the materials, the save path and what must stay as it is.
3. Write or update a check script from the decision before you change anything. It opens the saved scene and checks names, sizes, positions and materials, and that nothing from a replaced decision is left. Run it once; it must fail on the scene as it is now.
4. Apply the change with the server's tools, such as `manage_scene`, `manage_gameobject`, `manage_material` and `manage_asset`, or `execute_code` for C# that runs in the editor. Create a folder before you save an asset into it.
5. Save the scene, run the check script on the saved file and keep its output.
6. Record what was built and the decision it follows: a line in the state note, or a new decision with the leks-decide skill when the change made a new choice.

## Done when

- The check script failed before the change and passes after it, and its output is in the report.
- The record names the decision the scene follows.
