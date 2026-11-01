"""blendertk.RenderUtils (mirror of mayatk's) -- headless test.
Run: blender --background --factory-startup --python blendertk/test/test_render_utils.py

The render-control half the shared Rendering panel (tentacle's ``RenderingSlots``)
calls in both hosts: the engines, the scene's resolution as render settings,
the cameras a render looks through, and the IPR answer.
"""

import os
import sys
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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    import blendertk as btk
    from blendertk.render_utils._render_utils import RenderUtils

    scene = bpy.context.scene

    # ---- engines
    renderers = RenderUtils.get_available_renderers()
    names = [r["name"] for r in renderers]
    check("get_available_renderers: EEVEE and Workbench first",
          names[:2] == [RenderUtils._eevee_id(), "BLENDER_WORKBENCH"], str(names))
    check("...Cycles registered as an engine", "CYCLES" in names, str(names))
    check("...every entry is mayatk's shape",
          all(set(r) == {"name", "label", "loaded"} for r in renderers))
    check("...no duplicates", len(names) == len(set(names)))
    RenderUtils.set_renderer("CYCLES")
    check("set_renderer / current_renderer", RenderUtils.current_renderer() == "CYCLES")
    try:
        RenderUtils.set_renderer("NO_SUCH_ENGINE")
        check("set_renderer refuses an unknown engine", False)
    except ValueError:
        check("set_renderer refuses an unknown engine", True)

    # ---- render settings
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    check("render_settings reads the resolution",
          RenderUtils.render_settings() == {"width": 1280, "height": 720})
    written = RenderUtils.set_render_settings(
        {"width": 640, "height": 360, "camera_samples": 8, "_meta": {}}
    )
    check("set_render_settings writes its keys, skips Maya's",
          written == 2 and (scene.render.resolution_x, scene.render.resolution_y) == (640, 360))
    check("render_preset_store is the render collection",
          RenderUtils.render_preset_store().name == "render")

    # ---- cameras
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    check("render_cameras: none in an empty scene", RenderUtils.render_cameras() == [])
    cams = []
    for name in ("CamA", "CamB"):
        obj = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        scene.collection.objects.link(obj)
        cams.append(obj)
    scene.camera = cams[1]
    check("render_cameras: the scene camera first",
          RenderUtils.render_cameras() == ["CamB", "CamA"], str(RenderUtils.render_cameras()))
    try:
        RenderUtils.render_camera("NoSuchCam")
        check("render_camera refuses a missing camera", False)
    except ValueError:
        check("render_camera refuses a missing camera", True)

    # ---- IPR: none in Blender
    check("supports_ipr / start_ipr answer False",
          RenderUtils.supports_ipr("CYCLES") is False
          and RenderUtils.start_ipr("CamA", "CYCLES") is False)
    check("btk.RenderUtils is the root name", getattr(btk, "RenderUtils", None) is RenderUtils)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(ln.startswith("OK") for ln in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for ln in lines if ln.startswith('OK'))}/{len(lines)})"
)
