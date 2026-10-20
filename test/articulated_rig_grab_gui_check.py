"""Articulated Rig Grab GUI check -- what the headless suite cannot reach: the modal
operator starting from an app timer through the 3D-view override, running on the
window, and a real drag through it. The drag goes through Blender's own event queue
(``window.event_simulate``, which ``--enable-event-simulate`` allows -- and which
blocks the OS's input to this Blender meanwhile): press on the end link, move, Ctrl+Z,
let go. The live operator must consume the Ctrl+Z mid-drag, carry the end control, key
it with Auto Keying on, land as one undo step, and Esc must end the tool.

Requires a real windowed Blender (NOT ``--background``), so ``run_tests.py`` and
``Run-Tests.ps1`` do not glob it. Launch a *fresh* instance (never an existing
session)::

    blender --factory-startup --enable-event-simulate \\
        --python blendertk/test/articulated_rig_grab_gui_check.py

The check runs from ``bpy.app.timers`` steps (each simulated event needs the event loop
to turn), prints the ``===RESULT===`` sentinel and quits Blender itself (under a window
override -- a bare ``wm.quit_blender`` from a timer has no context window).
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

import bpy  # noqa: E402

lines = []
state = {}


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


def view3d():
    """``(window, area, region, region_3d)`` of the first 3D view."""
    window = bpy.context.window_manager.windows[0]
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            region = next(r for r in area.regions if r.type == "WINDOW")
            return window, area, region, area.spaces.active.region_3d
    raise RuntimeError("no 3D view")


def grab_running():
    window = bpy.context.window_manager.windows[0]
    return any(
        op.bl_idname == "BTK_OT_articulated_rig_grab"
        for op in getattr(window, "modal_operators", ())
    )


def simulate(kind, value, x, y, **modifiers):
    window = bpy.context.window_manager.windows[0]
    window.event_simulate(kind, value, x=int(x), y=int(y), **modifiers)


def setup():
    from mathutils import Quaternion, Vector

    from blendertk.rig_utils.articulated_rig import ArticulatedRig, ArticulatedRigGrab

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    names = []
    for i, x in enumerate((0.0, 4.0, 8.0, 12.0)):
        bpy.ops.mesh.primitive_cube_add(size=1.5, location=(x, 0.0, 0.0))
        bpy.context.active_object.name = f"P{i}"
        names.append(f"P{i}")
    joints = [
        {
            "link": i,
            "parent": i - 1,
            "type": "hinge",
            "position": [4.0 * i - 2.0, 0.0, 0.0],
            "aim": [1.0, 0.0, 0.0],
            "normal": [0.0, 0.0, 1.0],
            "limits": {},
            "size": 1.0,
        }
        for i in range(1, 4)
    ]
    rig = ArticulatedRig.create([[n] for n in names], joints, name="chain")
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
    _window, _area, region, view = view3d()
    view.view_perspective = "ORTHO"
    view.view_rotation = Quaternion()  # looking down -Z
    view.view_location = Vector((6.0, 0.0, 0.0))
    view.view_distance = 30.0
    ArticulatedRigGrab.activate()
    state["rig"] = rig
    check("the grab tool runs on the window", grab_running())


def press():
    from bpy_extras import view3d_utils

    window, _area, region, view = view3d()
    head = bpy.data.objects["P3"].matrix_world.translation
    at = view3d_utils.location_3d_to_region_2d(region, view, head)
    state["at"] = (region.x + at.x, region.y + at.y)
    state["before"] = head.copy()
    simulate("MOUSEMOVE", "NOTHING", *state["at"])
    simulate("LEFTMOUSE", "PRESS", *state["at"])


def move():
    x, y = state["at"]
    for step in range(1, 5):
        simulate("MOUSEMOVE", "NOTHING", x - 10 * step, y + 10 * step)


def undo_mid_drag():
    """Ctrl+Z with the mouse still held: the tool must hold it back -- an undo would
    free the bones the drag holds and take its unkeyed pose. A simulated key reaches
    the window's modal handlers but never its keymaps (measured: no simulated Ctrl+Z
    undoes, with or without a modal), so what is checked is the live operator's
    answer: a press it consumes never reaches the keymap behind it."""
    from blendertk.rig_utils.articulated_rig import grab_tool

    internal = grab_tool._ArticulatedRigGrabInternal
    modal = internal._op_modal
    answers = state["answers"] = []

    def spy(op, context, event):
        answer = modal(op, context, event)
        if event.type == "Z":
            answers.append((event.value, bool(op._tool.hold), sorted(answer)))
        return answer

    internal._op_modal = staticmethod(spy)
    x, y = state["at"]
    state["held"] = tuple(state["rig"].end_control.location)
    simulate("Z", "PRESS", x - 40, y + 40, ctrl=True)
    simulate("Z", "RELEASE", x - 40, y + 40, ctrl=True)


def verify_held():
    held = tuple(state["rig"].end_control.location)
    check(
        "a Ctrl+Z mid-drag is held back: the drag keeps its pose and goes on",
        ("PRESS", True, ["RUNNING_MODAL"]) in state["answers"]
        and sum(c * c for c in held) > 1.0
        and held == state["held"]
        and grab_running(),
        state["answers"],
    )


def release():
    x, y = state["at"]
    simulate("LEFTMOUSE", "RELEASE", x - 40, y + 40)


def verify_drag():
    rig = state["rig"]
    end = rig.end_control
    head = bpy.data.objects["P3"].matrix_world.translation
    moved = (head - state["before"]).length
    check("the drag carried the end link", moved > 1.0, moved)
    check(
        "... by the end control, keyed on release (Auto Keying)",
        end.location.length > 1.0
        and rig._keyed(end)
        and rig.fk_state() == [0.0] * len(rig.fk_state()),
        tuple(end.location),
    )
    window = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=window):
        bpy.ops.ed.undo()
    end = rig.end_control
    check(
        "... one undo takes the whole drag back, keys included",
        end.location.length < 1e-6 and not rig._keyed(end),
        tuple(end.location),
    )
    check("the tool is still on after the drag", grab_running())
    simulate("ESC", "PRESS", *state["at"])


def verify_exit():
    check("Esc ends the tool", not grab_running())


STEPS = [setup, press, move, undo_mid_drag, verify_held, release, verify_drag, verify_exit]


def _run():
    try:
        step = STEPS.pop(0)
        step()
        if STEPS:
            return 0.5  # let the event loop deliver what the step queued
    except Exception:
        traceback.print_exc()
        lines.append("FAIL unhandled exception")
    print("\n".join(lines))
    ok = bool(lines) and all(line.startswith("OK") for line in lines)
    print(
        f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
        f"({sum(1 for line in lines if line.startswith('OK'))}/{len(lines)})"
    )
    sys.stdout.flush()
    window = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_run, first_interval=1.0)
