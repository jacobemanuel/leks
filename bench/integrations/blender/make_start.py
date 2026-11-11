# writes an empty .blend file to the path given after --
import sys
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
