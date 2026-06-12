"""blendertk.cam_utils headless test — clip-plane adjustment (camera .data, no viewport).
Run: blender --background --factory-startup --python blendertk/test/test_cam_utils.py
"""
import sys, os, traceback, math

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")
def approx(a, b, tol=1e-2):
    return abs(a - b) <= tol

try:
    import bpy
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    # camera at origin; a size-2 cube at origin -> farthest bbox corner dist = sqrt(3)
    reset()
    bpy.ops.object.camera_add(location=(0, 0, 0)); cam = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))  # corners +/-1
    max_dist = math.sqrt(3)
    expected_far = max_dist * 1.2

    btk.adjust_camera_clipping(camera=cam, near_clip="auto", far_clip="auto")
    check("auto far == max_dist*1.2", approx(cam.data.clip_end, expected_far),
          f"end={cam.data.clip_end:.3f} exp={expected_far:.3f}")
    check("auto near floored to 0.1", approx(cam.data.clip_start, 0.1),
          f"start={cam.data.clip_start:.3f}")

    # reset -> Blender defaults 0.1 / 1000
    btk.adjust_camera_clipping(camera=cam, near_clip="reset", far_clip="reset")
    check("reset near == 0.1", approx(cam.data.clip_start, 0.1), f"start={cam.data.clip_start}")
    check("reset far == 1000", approx(cam.data.clip_end, 1000.0, tol=1e-3), f"end={cam.data.clip_end}")

    # explicit floats
    btk.adjust_camera_clipping(camera=cam, near_clip=0.5, far_clip=250.0)
    check("explicit near", approx(cam.data.clip_start, 0.5), f"start={cam.data.clip_start}")
    check("explicit far", approx(cam.data.clip_end, 250.0), f"end={cam.data.clip_end}")

    # None leaves values unchanged
    btk.adjust_camera_clipping(camera=cam, near_clip=None, far_clip=None)
    check("None leaves near unchanged", approx(cam.data.clip_start, 0.5), f"start={cam.data.clip_start}")

    # camera=None resolves the scene's active camera
    bpy.context.scene.camera = cam
    btk.adjust_camera_clipping(near_clip="reset")
    check("camera=None -> scene.camera near reset", approx(cam.data.clip_start, 0.1),
          f"start={cam.data.clip_start}")

    # no camera -> no-op (no crash)
    reset()
    btk.adjust_camera_clipping(near_clip="auto", far_clip="auto")
    check("no camera -> no-op", True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===CAM-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
