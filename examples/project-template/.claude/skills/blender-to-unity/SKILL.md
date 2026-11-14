---
name: blender-to-unity
description: Move an asset from Blender into a Unity project so it arrives at the right scale, axis, pivot and name, with its materials and level-of-detail meshes, and check it on the Unity side. Use it whenever a Blender asset is exported for Unity or re-exported after a change.
---

# Blender to Unity

Blender works in metres with z up and Unity in metres with y up, and most import problems come from that turn, from unapplied transforms and from names that change on the way. This skill checks both ends against the current decision instead of trusting the exporter's defaults.

## Steps

1. `search` for the asset's spec and the export settings the project uses, and `read` the current decision. Write down the size with its unit, the pivot, the axis, the names the Unity side expects and any polygon or texture budget. Follow a `NEWER VERSION` line instead of a superseded note.
2. In Blender, apply rotation and scale on the objects you export, put the pivot where the spec says, and name the objects, meshes and materials as the Unity side expects them. Name level-of-detail meshes with the suffix your import settings use, such as `_LOD0` and `_LOD1`.
3. Export to the format the project uses (FBX or glTF) with the settings from step 1, into the folder the Unity project imports from. Keep the exact export settings in the asset's note, so the next export repeats them.
4. In Unity, check the imported asset with a script or the Unity MCP server: its bounds in metres, the forward and up axes, the object and material names, the vertex and triangle counts against the budget, and the LOD group if there is one. Run the check once before the export when an older version is in the project; it should fail on the old asset when the spec changed.
5. When the import settings changed, record them with the asset (the `.meta` file is part of the change) and say so in the report.
6. Record what was exported, from which `.blend` file and commit, and the decision it follows: a line in the state note, or a new decision with the leks-decide skill when the export made a new choice.

## Done when

- The Unity-side check passes on the imported asset, and its output is in the report.
- The export settings and the source file are written down next to the asset, and the record names the decision it follows.
