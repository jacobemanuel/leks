---
name: blender-asset-change
description: Change a Blender file through the Blender MCP server, from a plan based on the current decision. A check script written before the change verifies the saved file, and the result is recorded with the decision it follows. Use it for any asset change in Blender.
---

# Blender asset change

This skill needs the Blender MCP server running under the server name `blender` next to `leks`. The setup is in docs/integrations.md in the LEKS repository.

## Steps

1. `search` for the spec and `read` the current decision. Write down every number with its unit and tolerance, and the axis that points up. Never build from a note marked superseded; follow its `NEWER VERSION` line.
2. Plan the change: the objects and their mesh detail, the materials, the save path and what must stay as it is.
3. Write or update a check script from the decision before you change anything. It opens the saved `.blend` file in a background Blender and checks names, dimensions, positions and material values, and that nothing from a replaced decision is left. Run it once; it must fail on the file as it is now.
4. Apply the change with the server's tools. `execute_blender_code_for_cli` runs Python in a background Blender on a given file and needs no open window; the other tools work on a running Blender through its add-on.
5. Save the file, run the check script on it and keep its output.
6. Record what was built and the decision it follows: a line in the state note, or a new decision with the leks-decide skill when the change made a new choice.

## Done when

- The check script failed before the change and passes after it, and its output is in the report.
- The record names the decision the file follows.
