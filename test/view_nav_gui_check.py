"""Viewport-nav GUI smoke check — verifies what headless tests cannot reach: the modal nav
operator invoking from ``btk.navigate_view``'s deferred launch (the Qt-pump pattern) actually
runs on the window, and each per-mode delta mutates a **live** RegionView3D (the headless
test only exercises the pure math on a constructed quaternion).

Requires a real windowed Blender (NOT ``--background`` — a modal invoke there CANCELs silently),
so it is excluded from ``Run-Tests.ps1`` (globs ``test_*.py`` / ``*_slot_check.py``). Launch a
*fresh* instance (never an existing session)::

    blender --factory-startup --python blendertk/test/view_nav_gui_check.py

It does NOT synthesize the mouse drag (event injection is fragile); it proves the two halves the
drag is built from — the modal is live, and ``_apply_view_nav`` moves a real view — then quits
itself under a window override (a bare ``wm.quit_blender`` from a timer crashes).
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

import bpy  # noqa: E402

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


def _rv3d():
    for area in bpy.context.window_manager.windows[0].screen.areas:
        if area.type == "VIEW_3D":
            return area.spaces.active.region_3d
    return None


def _finish():
    print("\n".join(lines))
    ok = bool(lines) and all(l.startswith("OK") for l in lines)
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
          f"({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
    sys.stdout.flush()
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win):
        bpy.ops.wm.quit_blender()
    return None


def _phase2():
    """Runs after navigate_view's deferred (0.01s) invoke has fired."""
    try:
        from blendertk.cam_utils._cam_utils import _apply_view_nav

        win = bpy.context.window_manager.windows[0]
        modal = getattr(win, "modal_operators", None)
        if modal is None:  # pre-4.2 API — invoke not raising is the best signal we have
            check("modal running (modal_operators API unavailable)", True)
        else:
            check("btk.view_nav modal is live on the window after navigate_view",
                  any(op.bl_idname == "BTK_OT_view_nav" for op in modal),
                  f"modal={[op.bl_idname for op in modal]}")

        rv = _rv3d()
        check("VIEW_3D region_data present", rv is not None)
        if rv is not None:
            r0 = rv.view_rotation.copy()
            _apply_view_nav(rv, "ORBIT", 100, 0)
            check("ORBIT mutates the live view_rotation",
                  rv.view_rotation.rotation_difference(r0).angle > 1e-3)
            r0 = rv.view_rotation.copy()
            _apply_view_nav(rv, "ROLL", 100, 0)
            check("ROLL mutates the live view_rotation",
                  rv.view_rotation.rotation_difference(r0).angle > 1e-3)
            d0 = rv.view_distance
            _apply_view_nav(rv, "DOLLY", 0, 50)
            check("DOLLY mutates the live view_distance", abs(rv.view_distance - d0) > 1e-3,
                  f"{d0:.3f}->{rv.view_distance:.3f}")
            l0 = rv.view_location.copy()
            _apply_view_nav(rv, "TRACK", 100, 0)
            check("TRACK mutates the live view_location", (rv.view_location - l0).length > 1e-3)
    except Exception:
        traceback.print_exc()
        lines.append("FAIL unhandled exception")
    return _finish()


def _phase1():
    try:
        import blendertk as btk

        if bpy.app.background:
            check("requires a windowed Blender (running --background)", False,
                  "relaunch WITHOUT --background")
            return _finish()
        btk.navigate_view("ORBIT")  # deferred INVOKE_DEFAULT under a VIEW_3D override
        check("navigate_view scheduled without raising", True)
    except Exception:
        traceback.print_exc()
        lines.append("FAIL unhandled exception")
        return _finish()
    bpy.app.timers.register(_phase2, first_interval=0.5)  # after the deferred invoke fires
    return None


bpy.app.timers.register(_phase1, first_interval=1.0)
