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

    # ---- interactive view-nav math (pure RegionView3D delta -> new view; headless-testable) ----
    from mathutils import Quaternion, Vector
    from blendertk.cam_utils._cam_utils import (
        _orbit_rotation, _roll_rotation, _dolly_distance, _track_location,
        _ensure_view_nav_operator,
    )
    I = Quaternion()  # identity view: looks down -Z, up +Y, right +X

    # orbit: horizontal drag orbits about world-Z (azimuth) by -dx*sens; from identity that
    # carries the right vector (1,0,0) to (cos(-0.5), sin(-0.5), 0) for dx=100 (sens 0.005).
    v = _orbit_rotation(I, 100, 0) @ Vector((1.0, 0.0, 0.0))
    check("orbit dx rotates about world-Z by -dx*sens",
          approx(v.x, math.cos(-0.5)) and approx(v.y, math.sin(-0.5)) and approx(v.z, 0.0),
          f"v={tuple(round(c, 3) for c in v)}")
    # orbit: vertical drag orbits about the view's right axis (X here) by +dy*sens (drag up tilts
    # the view up); (0,1,0)->(0,cos(0.5),sin(0.5)) for dy=100.
    v = _orbit_rotation(I, 0, 100) @ Vector((0.0, 1.0, 0.0))
    check("orbit dy rotates about view-right by +dy*sens (drag up tilts view up)",
          approx(v.x, 0.0) and approx(v.y, math.cos(0.5)) and approx(v.z, math.sin(0.5)),
          f"v={tuple(round(c, 3) for c in v)}")

    # roll: rotates about the forward axis — forward (0,0,-1) preserved, right vector rotates.
    rl = _roll_rotation(I, 100)
    fwd, right = rl @ Vector((0.0, 0.0, -1.0)), rl @ Vector((1.0, 0.0, 0.0))
    check("roll preserves the forward axis",
          approx(fwd.x, 0.0) and approx(fwd.y, 0.0) and approx(fwd.z, -1.0),
          f"fwd={tuple(round(c, 3) for c in fwd)}")
    check("roll rotates the right vector about forward by dx*sens",
          approx(right.x, math.cos(-0.5)) and approx(right.y, math.sin(-0.5)),
          f"right={tuple(round(c, 3) for c in right)}")

    # dolly: multiplicative; drag up (dy>0) shrinks distance; floored so the eye never crosses.
    check("dolly dy=50 halves distance (1 - 50*0.01)", approx(_dolly_distance(10.0, 50), 5.0),
          f"d={_dolly_distance(10.0, 50)}")
    check("dolly floors at min_dist", approx(_dolly_distance(10.0, 1000), 1e-4, tol=1e-5),
          f"d={_dolly_distance(10.0, 1000)}")

    # track: grab-and-drag — pivot slides OPPOSITE the drag on both axes (scale = 0.001*dist), so
    # the scene follows the cursor. dx=100 -> pivot -x; dy=100 -> pivot -y.
    loc = _track_location(Vector((0.0, 0.0, 0.0)), I, 10.0, 100, 0)
    check("track dx pans pivot along -right*dx*sens*dist (drag right: scene follows cursor)",
          approx(loc.x, -1.0) and approx(loc.y, 0.0) and approx(loc.z, 0.0),
          f"loc={tuple(round(c, 3) for c in loc)}")
    loc = _track_location(Vector((0.0, 0.0, 0.0)), I, 10.0, 0, 100)
    check("track dy pans pivot along -up*dy*sens*dist (drag up: scene follows cursor)",
          approx(loc.x, 0.0) and approx(loc.y, -1.0),
          f"loc={tuple(round(c, 3) for c in loc)}")

    # operator registers, helper is exposed, and the launch refuses deterministically headless.
    _ensure_view_nav_operator()
    check("BTK_OT_view_nav registered", hasattr(bpy.types, "BTK_OT_view_nav"))
    check("btk.navigate_view exposed", callable(getattr(btk, "navigate_view", None)))
    try:
        btk.navigate_view("ORBIT")
        check("navigate_view refuses in --background", False, "no RuntimeError raised")
    except RuntimeError:
        check("navigate_view refuses in --background", True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===CAM-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
