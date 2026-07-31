"""blendertk.cam_utils headless test — clip-plane adjustment (camera .data, no viewport).
Run: blender --background --factory-startup --python blendertk/test/test_cam_utils.py
"""

import sys
import os
import traceback
import math

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
    bpy.ops.object.camera_add(location=(0, 0, 0))
    cam = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))  # corners +/-1
    max_dist = math.sqrt(3)
    expected_far = max_dist * 1.2

    btk.adjust_camera_clipping(camera=cam, near_clip="auto", far_clip="auto")
    check(
        "auto far == max_dist*1.2",
        approx(cam.data.clip_end, expected_far),
        f"end={cam.data.clip_end:.3f} exp={expected_far:.3f}",
    )
    check(
        "auto near floored to 0.1",
        approx(cam.data.clip_start, 0.1),
        f"start={cam.data.clip_start:.3f}",
    )

    # reset -> Blender defaults 0.1 / 1000
    btk.adjust_camera_clipping(camera=cam, near_clip="reset", far_clip="reset")
    check(
        "reset near == 0.1",
        approx(cam.data.clip_start, 0.1),
        f"start={cam.data.clip_start}",
    )
    check(
        "reset far == 1000",
        approx(cam.data.clip_end, 1000.0, tol=1e-3),
        f"end={cam.data.clip_end}",
    )

    # explicit floats
    btk.adjust_camera_clipping(camera=cam, near_clip=0.5, far_clip=250.0)
    check(
        "explicit near",
        approx(cam.data.clip_start, 0.5),
        f"start={cam.data.clip_start}",
    )
    check("explicit far", approx(cam.data.clip_end, 250.0), f"end={cam.data.clip_end}")

    # None leaves values unchanged
    btk.adjust_camera_clipping(camera=cam, near_clip=None, far_clip=None)
    check(
        "None leaves near unchanged",
        approx(cam.data.clip_start, 0.5),
        f"start={cam.data.clip_start}",
    )

    # camera=None resolves the scene's active camera
    bpy.context.scene.camera = cam
    btk.adjust_camera_clipping(near_clip="reset")
    check(
        "camera=None -> scene.camera near reset",
        approx(cam.data.clip_start, 0.1),
        f"start={cam.data.clip_start}",
    )

    # no camera -> no-op (no crash)
    reset()
    btk.adjust_camera_clipping(near_clip="auto", far_clip="auto")
    check("no camera -> no-op", True)

    # ---- interactive view-nav math (pure RegionView3D delta -> new view; headless-testable) ----
    from mathutils import Quaternion, Vector
    from blendertk.cam_utils._cam_utils import CamUtils

    I = Quaternion()  # identity view: looks down -Z, up +Y, right +X

    # orbit: horizontal drag orbits about world-Z (azimuth) by -dx*sens; from identity that
    # carries the right vector (1,0,0) to (cos(-0.5), sin(-0.5), 0) for dx=100 (sens 0.005).
    v = CamUtils._orbit_rotation(I, 100, 0) @ Vector((1.0, 0.0, 0.0))
    check(
        "orbit dx rotates about world-Z by -dx*sens",
        approx(v.x, math.cos(-0.5))
        and approx(v.y, math.sin(-0.5))
        and approx(v.z, 0.0),
        f"v={tuple(round(c, 3) for c in v)}",
    )
    # orbit: vertical drag orbits about the view's right axis (X here) by +dy*sens (drag up tilts
    # the view up); (0,1,0)->(0,cos(0.5),sin(0.5)) for dy=100.
    v = CamUtils._orbit_rotation(I, 0, 100) @ Vector((0.0, 1.0, 0.0))
    check(
        "orbit dy rotates about view-right by +dy*sens (drag up tilts view up)",
        approx(v.x, 0.0) and approx(v.y, math.cos(0.5)) and approx(v.z, math.sin(0.5)),
        f"v={tuple(round(c, 3) for c in v)}",
    )

    # roll: rotates about the forward axis — forward (0,0,-1) preserved, right vector rotates.
    rl = CamUtils._roll_rotation(I, 100)
    fwd, right = rl @ Vector((0.0, 0.0, -1.0)), rl @ Vector((1.0, 0.0, 0.0))
    check(
        "roll preserves the forward axis",
        approx(fwd.x, 0.0) and approx(fwd.y, 0.0) and approx(fwd.z, -1.0),
        f"fwd={tuple(round(c, 3) for c in fwd)}",
    )
    check(
        "roll rotates the right vector about forward by dx*sens",
        approx(right.x, math.cos(-0.5)) and approx(right.y, math.sin(-0.5)),
        f"right={tuple(round(c, 3) for c in right)}",
    )

    # dolly: multiplicative; drag up (dy>0) shrinks distance; floored so the eye never crosses.
    check(
        "dolly dy=50 halves distance (1 - 50*0.01)",
        approx(CamUtils._dolly_distance(10.0, 50), 5.0),
        f"d={CamUtils._dolly_distance(10.0, 50)}",
    )
    check(
        "dolly floors at min_dist",
        approx(CamUtils._dolly_distance(10.0, 1000), 1e-4, tol=1e-5),
        f"d={CamUtils._dolly_distance(10.0, 1000)}",
    )

    # track: grab-and-drag — pivot slides OPPOSITE the drag on both axes (scale = 0.001*dist), so
    # the scene follows the cursor. dx=100 -> pivot -x; dy=100 -> pivot -y.
    loc = CamUtils._track_location(Vector((0.0, 0.0, 0.0)), I, 10.0, 100, 0)
    check(
        "track dx pans pivot along -right*dx*sens*dist (drag right: scene follows cursor)",
        approx(loc.x, -1.0) and approx(loc.y, 0.0) and approx(loc.z, 0.0),
        f"loc={tuple(round(c, 3) for c in loc)}",
    )
    loc = CamUtils._track_location(Vector((0.0, 0.0, 0.0)), I, 10.0, 0, 100)
    check(
        "track dy pans pivot along -up*dy*sens*dist (drag up: scene follows cursor)",
        approx(loc.x, 0.0) and approx(loc.y, -1.0),
        f"loc={tuple(round(c, 3) for c in loc)}",
    )

    # operator registers, helper is exposed, and the launch refuses deterministically headless.
    CamUtils._ensure_view_nav_operator()
    check("BTK_OT_view_nav registered", hasattr(bpy.types, "BTK_OT_view_nav"))
    check("btk.navigate_view exposed", callable(getattr(btk, "navigate_view", None)))
    try:
        btk.navigate_view("ORBIT")
        check("navigate_view refuses in --background", False, "no RuntimeError raised")
    except RuntimeError:
        check("navigate_view refuses in --background", True)

    # ---- viewport view state + selection-fitted clipping (backs the m_frame step cycle) ----
    # --background keeps one window with the default screen, so a VIEW_3D resolves here too.
    reset()
    space, rv3d = CamUtils._active_view3d()
    if rv3d is None:
        check("VIEW_3D available headless", False, "no 3D viewport in --background")
    else:
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))  # corners +/-1
        cube = bpy.context.active_object

        # view state round-trip: snapshot, move the view, restore.
        rv3d.view_location = Vector((1.0, 2.0, 3.0))
        rv3d.view_distance = 12.0
        space.clip_start, space.clip_end = 0.5, 500.0
        state = btk.get_view_state()
        rv3d.view_location = Vector((50.0, 50.0, 50.0))
        rv3d.view_distance = 99.0
        space.clip_start, space.clip_end = 10.0, 20.0
        check("set_view_state restores the view", btk.set_view_state(state))
        check(
            "view location/distance restored",
            approx(rv3d.view_location.x, 1.0) and approx(rv3d.view_distance, 12.0),
            f"loc={tuple(round(c,2) for c in rv3d.view_location)} dist={rv3d.view_distance}",
        )
        check(
            "clip planes restored",
            approx(space.clip_start, 0.5) and approx(space.clip_end, 500.0),
            f"{space.clip_start}/{space.clip_end}",
        )
        check("set_view_state(None) is a no-op", btk.set_view_state(None) is False)

        # clip fitting: only ever widens, and only when something would clip.
        rv3d.view_location = Vector((0.0, 0.0, 0.0))
        rv3d.view_rotation = Quaternion()  # looking down -Z from +Z
        rv3d.view_distance = 50.0  # eye 50 units out -> cube spans depth 49..51
        space.clip_start, space.clip_end = 0.1, 10.0
        fitted = btk.fit_camera_clipping([cube])
        check(
            "fit widens the far plane past the framed object",
            fitted is not None and space.clip_end > 51.0,
            f"fitted={fitted} end={space.clip_end}",
        )
        check("fit leaves an already-wide near plane alone", approx(space.clip_start, 0.1))
        check(
            "fit is a no-op once nothing clips",
            btk.fit_camera_clipping([cube]) is None,
            f"{space.clip_start}/{space.clip_end}",
        )
        space.clip_start = 50.0  # near plane now cuts into the cube (depth 49..51)
        fitted = btk.fit_camera_clipping([cube])
        check(
            "fit pulls the near plane in front of the object",
            fitted is not None and space.clip_start < 49.0,
            f"start={space.clip_start}",
        )
        check("fit without geometry is a no-op", btk.fit_camera_clipping([]) is None)
        space.clip_start, space.clip_end = 0.1, 1000.0

        # Locked to a camera, the *lens* does the clipping — the fit has to widen that too, and
        # the snapshot has to carry it or the widening outlives the view it was made for.
        fit_cam = bpy.data.objects.new("FitCam", bpy.data.cameras.new("FitCam"))
        bpy.context.collection.objects.link(fit_cam)
        bpy.context.scene.camera = fit_cam
        fit_cam.location = (0.0, 0.0, 50.0)  # default rotation looks down -Z
        bpy.context.view_layer.update()  # matrix_world is stale until the depsgraph re-evaluates
        fit_cam.data.clip_start, fit_cam.data.clip_end = 0.1, 10.0
        rv3d.view_perspective = "CAMERA"
        cam_state = btk.get_view_state()
        btk.fit_camera_clipping([cube])
        check(
            "camera view: the fit widens the camera's lens clipping to the object's depth",
            51.0 < fit_cam.data.clip_end < 60.0,  # cube spans depth 49..51, plus the buffer
            f"end={fit_cam.data.clip_end}",
        )
        check(
            "camera view: the viewport's own planes are left alone",
            approx(space.clip_end, 1000.0),
            f"space end={space.clip_end}",
        )
        btk.set_view_state(cam_state)
        check(
            "camera view: the restore returns the lens clipping",
            approx(fit_cam.data.clip_end, 10.0),
            f"end={fit_cam.data.clip_end}",
        )
        rv3d.view_perspective = "PERSP"

    # ---- CameraVisibility: per-camera exclusive/hidden sets (2026-07-28 rolled engine) ----
    reset()
    cam_data = bpy.data.cameras.new("VisCam")
    cam = bpy.data.objects.new("VisCam", cam_data)
    bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    cubes = []
    for i in range(3):
        bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
        cubes.append(bpy.context.active_object)
        cubes[-1].name = f"VisCube{i}"
    light = bpy.data.objects.new("VisLight", bpy.data.lights.new("VisLight", "POINT"))
    bpy.context.collection.objects.link(light)

    CV = btk.CameraVisibility
    CV.set_exclusive(cam, [cubes[0]])
    check("exclusive: only the set member stays visible",
          not cubes[0].hide_viewport and cubes[1].hide_viewport and cubes[2].hide_viewport,
          f"hides={[c.hide_viewport for c in cubes]}")
    check("exclusive: helper objects (light) not implicitly hidden", not light.hide_viewport)
    CV.remove_all(cam)
    check("remove_all restores visibility", not any(c.hide_viewport for c in cubes),
          f"hides={[c.hide_viewport for c in cubes]}")

    CV.set_hidden(cam, [cubes[1]])
    check("hidden set hides its member only",
          cubes[1].hide_viewport and not cubes[0].hide_viewport and not cubes[2].hide_viewport)
    CV.remove_from_hidden(cam, [cubes[1]])
    check("remove_from_hidden restores", not cubes[1].hide_viewport)

    # a user-hidden object is not clobbered by apply/restore
    cubes[2].hide_viewport = True
    CV.set_hidden(cam, [cubes[0]])
    CV.remove_all(cam)
    check("user-hidden object untouched by the stash round-trip", cubes[2].hide_viewport)
    cubes[2].hide_viewport = False

    # camera switch: the second camera's (empty) sets take over on apply
    cam2 = bpy.data.objects.new("VisCam2", bpy.data.cameras.new("VisCam2"))
    bpy.context.collection.objects.link(cam2)
    CV.set_exclusive(cam, [cubes[0]])
    bpy.context.scene.camera = cam2
    CV.apply()  # what enable_auto's msgbus notify does on switch
    check("camera switch releases the old camera's isolation",
          not any(c.hide_viewport for c in cubes), f"hides={[c.hide_viewport for c in cubes]}")
    bpy.context.scene.camera = cam
    CV.apply()
    check("switching back re-applies its sets",
          not cubes[0].hide_viewport and cubes[1].hide_viewport)
    exc, hid = CV.get_sets(cam)
    check("get_sets reads the stored names", exc == ["VisCube0"] and hid == [], f"{exc}/{hid}")
    CV.remove_all_for_all()
    check("remove_all_for_all clears every camera",
          CV.get_sets(cam) == ([], []) and not any(c.hide_viewport for c in cubes))

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===CAM-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
