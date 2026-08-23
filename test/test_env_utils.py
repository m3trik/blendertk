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

    # ---- scene settings: the bridges' ``scene`` record ------------------------
    # Twin of mayatk's TestSceneSettings: same keys, same playback/anim mapping.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = 24, 1.0
    applied = btk.apply_scene_settings(
        {
            "fps": 29.97,
            "frame_start": 10,
            "frame_end": 90,
            "anim_start": 5,
            "anim_end": 100,
            "frame_current": 42,
        }
    )
    check(
        "apply_scene_settings: fractional fps -> integer fps + fps_base",
        scene.render.fps == 30 and abs(scene.render.fps_base - 30 / 29.97) < 1e-9,
        f"{scene.render.fps}/{scene.render.fps_base}",
    )
    check(
        "apply_scene_settings: anim range is the scene range, playback the preview range",
        (scene.frame_start, scene.frame_end) == (5, 100)
        and scene.use_preview_range
        and (scene.frame_preview_start, scene.frame_preview_end) == (10, 90),
    )
    check("apply_scene_settings: current frame", scene.frame_current == 42)
    check(
        "apply_scene_settings reports every key it applied",
        set(applied) == set(btk.EnvUtils.SCENE_SETTINGS_KEYS),
        f"{applied}",
    )
    rec = btk.scene_settings()
    check(
        "scene_settings reads back the record it applied",
        abs(rec["fps"] - 29.97) < 1e-6
        and (rec["frame_start"], rec["frame_end"]) == (10, 90)
        and (rec["anim_start"], rec["anim_end"]) == (5, 100)
        and rec["frame_current"] == 42,
        f"{rec}",
    )
    # Identical ranges -> no preview range (a single-range scene stays single).
    btk.apply_scene_settings({"frame_start": 1, "frame_end": 48})
    check(
        "apply_scene_settings: equal ranges leave the preview range off",
        not scene.use_preview_range and (scene.frame_start, scene.frame_end) == (1, 48),
    )
    rec = btk.scene_settings()
    check(
        "scene_settings: no preview range -> playback == anim",
        (rec["frame_start"], rec["frame_end"]) == (rec["anim_start"], rec["anim_end"]) == (1, 48),
    )
    check(
        "apply_scene_settings: a partial / empty record applies nothing and never raises",
        btk.apply_scene_settings({}) == [] and btk.apply_scene_settings({"fps": 0}) == [],
    )

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
