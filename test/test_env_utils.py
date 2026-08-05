"""blendertk EnvUtils scene-export feature test (headless Blender — bpy present, NO Qt).

Run: blender --background --factory-startup --python blendertk/test/test_env_utils.py

Mirror of the ``TestExportSceneAsObj`` block in mayatk's ``test_env_utils.py``: the two
``export_scene_as_obj`` twins share parameter names and meanings so the Scene panel's
Export Scene format combo dispatches to either without a per-DCC branch — which only
holds if both are actually exercised the same way. The workspace half of ``EnvUtils``
is covered by ``test_workspace.py``.
"""

import sys
import os
import shutil
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


def _count(path, prefix):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for line in fh if line.startswith(prefix))


try:
    import bpy
    import blendertk as btk

    tmp = tempfile.mkdtemp(prefix="btk_env_obj_")

    # ---- scene: a shaded cube + a sphere -------------------------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "ObjCube"
    cube.data.materials.append(bpy.data.materials.new("ObjMat"))
    bpy.ops.mesh.primitive_uv_sphere_add()
    bpy.context.active_object.name = "ObjSphere"

    # ---- whole scene ---------------------------------------------------------
    out = os.path.join(tmp, "scene.obj")
    returned = btk.export_scene_as_obj(file_path=out)
    check("export_scene_as_obj returns the written path", returned == out, f"{returned}")
    check(
        "whole-scene OBJ is written",
        os.path.isfile(out) and os.path.getsize(out) > 0,
    )
    body = open(out, encoding="utf-8", errors="replace").read()
    check(
        "both objects carry group records",
        "ObjCube" in body and "ObjSphere" in body,
    )
    check("normals + UVs are written", "\nvn " in body and "\nvt " in body)
    check("the .mtl sidecar rides along", os.path.isfile(out[:-4] + ".mtl"))

    # ---- selection scope -----------------------------------------------------
    bpy.ops.object.select_all(action="DESELECT")
    bpy.data.objects["ObjCube"].select_set(True)
    sel_out = os.path.join(tmp, "sel.obj")
    btk.export_scene_as_obj(file_path=sel_out, selection_only=True, materials=False)
    check(
        "selection_only exports just the selection",
        _count(sel_out, "v ") == 8,
        f"{_count(sel_out, 'v ')} verts (cube alone is 8)",
    )
    check(
        "materials=False writes no .mtl",
        not os.path.isfile(sel_out[:-4] + ".mtl"),
    )

    # ---- unsaved scene, no path ---------------------------------------------
    # bpy.data.filepath is reliably "" for a never-saved .blend (unlike Maya's
    # phantom "<project>/untitled" -- see mayatk's saved_scene_path).
    try:
        btk.export_scene_as_obj()
        check("unsaved scene with no path -> ValueError", False)
    except ValueError:
        check("unsaved scene with no path -> ValueError", True)

    shutil.rmtree(tmp, ignore_errors=True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
