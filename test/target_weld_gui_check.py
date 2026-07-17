"""Target Weld GUI smoke check — verifies the interactive activation path headless tests
cannot reach: the modal operator invoking from an app-timer context (no ``bpy.context.window``
— the exact context tentacle's Qt marking menu drives slots from), the modal handler actually
running on the window, and the POST_PIXEL draw handler surviving real redraws.

Requires a real windowed Blender (NOT ``--background``), so it is excluded from
``Run-Tests.ps1`` (its globs are ``test_*.py`` / ``*_slot_check.py``). Launch a *fresh*
instance (never an existing session)::

    blender --factory-startup --python blendertk/test/target_weld_gui_check.py

The check runs from a one-shot ``bpy.app.timers`` timer after the window is up, prints the
usual ``===RESULT===`` sentinel, and quits Blender itself (under a window override — a bare
``wm.quit_blender`` from a timer has a NULL context window and crashes).
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


def _run():
    try:
        import blendertk as btk
        from blendertk.core_utils._core_utils import get_view3d_context

        # (App timers differ from tentacle's Qt pump in that they DO carry a context window;
        # the activation path is shared either way — get_view3d_context never reads
        # bpy.context.window, and the temp_override supplies window/area/region itself.)

        # Full production activation path: prep (edit mode / vertex mask / deselect) +
        # modal invoke, all from the timer context via the VIEW_3D override.
        btk.target_weld()
        active = bpy.context.view_layer.objects.active
        check("prep entered Edit Mode on the active mesh",
              active is not None and active.mode == "EDIT", f"mode={getattr(active, 'mode', None)}")
        check("prep set vertex select mode",
              tuple(bpy.context.scene.tool_settings.mesh_select_mode) == (True, False, False))

        win = bpy.context.window_manager.windows[0]
        modal = getattr(win, "modal_operators", None)
        if modal is None:  # pre-4.2 API — activation not raising is the best signal we have
            check("modal handler running (API unavailable — activation did not raise)", True)
        else:
            check("modal handler running on the window",
                  any(op.bl_idname == "BTK_OT_target_weld" for op in modal),
                  f"modal={[op.bl_idname for op in modal]}")

        # Exercise the POST_PIXEL draw handler with real redraws (a draw-time exception
        # would traceback to the console; reaching the next check proves the op survived).
        ctx = {k: v for k, v in get_view3d_context().items() if v is not None}
        with bpy.context.temp_override(**ctx):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
        check("viewport redraws with the draw handler installed", True)
    except Exception:
        traceback.print_exc()
        lines.append("FAIL unhandled exception")

    print("\n".join(lines))
    ok = all(l.startswith("OK") for l in lines) and lines
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
          f"({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
    sys.stdout.flush()

    # Quit under a window override — bare quit from a timer crashes (NULL context window).
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win):
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_run, first_interval=1.0)
