"""blendertk.rig_utils.wheel_rig headless test — auto-rolling wheel rig (driver-based).
Run: blender --background --factory-startup --python blendertk/test/test_wheel_rig.py

Verifies the rig BUILDS (rotation driver + control custom props) and EVALUATES (rolling:
rotation == 2*travel/height radians; mirrored wheel auto-flips; enableRotation gates it).
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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}")

def approx(a, b, tol=2e-3):
    return abs(a - b) <= tol

try:
    import bpy
    from blendertk.rig_utils.wheel_rig import WheelRig

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def empty(name, loc, rot=(0, 0, 0)):
        e = bpy.data.objects.new(name, None)
        e.location = loc
        e.rotation_euler = rot
        bpy.context.collection.objects.link(e)
        return e

    def eval_rot(o, idx):
        dg = bpy.context.evaluated_depsgraph_get()
        return o.evaluated_get(dg).rotation_euler[idx]

    # ---- build: control@origin, wheel1 (aligned), wheel2 (180deg about Z -> mirrored X axis) ----
    reset()
    control = empty("control", (0, 0, 0))
    w1 = empty("wheel_L", (2, 0, 0))
    w2 = empty("wheel_R", (-2, 0, 0), rot=(0, 0, math.pi))   # mirrored
    bpy.context.view_layer.update()
    rig = WheelRig(control, [w1, w2])
    rigged = rig.rig_rotation(movement_axis="translateY", rotation_axis="rotateX", wheel_height=2.0)  # radius=1
    check("rig_rotation returns 2 wheels", len(rigged) == 2, f"n={len(rigged)}")

    # ---- driver wired with 4 vars (travel + height/enable/direction) ----
    def rot_driver(o, idx):
        ad = o.animation_data
        return next((d for d in (ad.drivers if ad else []) if d.data_path == "rotation_euler" and d.array_index == idx), None)
    d1 = rot_driver(w1, 0)
    check("wheel_L has rotation_euler[0] driver", d1 is not None)
    check("driver has 4 variables (travel+height+enable+direction)",
          d1 is not None and len(d1.driver.variables) == 4, f"n={len(d1.driver.variables) if d1 else 0}")

    # ---- control keyable custom props (Maya parity) ----
    check("control wheelHeight == 2.0", control.get("wheelHeight") == 2.0, f"{control.get('wheelHeight')}")
    check("control enableRotation == 1.0", control.get("enableRotation") == 1.0)
    check("control spinDirection == 1.0", control.get("spinDirection") == 1.0)

    # ---- re-entrancy id stamped ----
    check("control wheel_rig_id stamped", bool(control.get("wheel_rig_id")), f"{control.get('wheel_rig_id')}")

    # ---- roll: move control +1 along Y (= radius) -> rotation == 2*1/2 == 1.0 rad ----
    control.location = (0, 1, 0)
    bpy.context.view_layer.update()
    r1 = eval_rot(w1, 0)
    r2 = eval_rot(w2, 0)
    check("wheel_L rolls +1.0 rad at travel=radius", approx(r1, 1.0), f"rot={r1:.4f}")
    check("wheel_R (mirrored) auto-flips to -1.0 rad", approx(r2, -1.0), f"rot={r2:.4f}")

    # ---- enableRotation gates the roll (disable, then move so the driver re-evaluates) ----
    control["enableRotation"] = 0.0
    control.location = (0, 3, 0)
    bpy.context.view_layer.update()
    check("enableRotation=0 -> no roll even while moving", approx(eval_rot(w1, 0), 0.0), f"rot={eval_rot(w1,0):.4f}")
    control["enableRotation"] = 1.0
    control.location = (0, 1, 0)
    bpy.context.view_layer.update()

    # ---- re-entrant rebuild keeps one driver per channel (no stacking) ----
    # Blender's own spellings (a LOC_ channel, a rotation index) name the same channel.
    rig.rig_rotation(movement_axis="LOC_Y", rotation_axis=0, wheel_height=2.0)
    drivers_on_axis = [d for d in (w1.animation_data.drivers if w1.animation_data else [])
                       if d.data_path == "rotation_euler" and d.array_index == 0]
    check("rebuild does not stack drivers", len(drivers_on_axis) == 1, f"n={len(drivers_on_axis)}")

    # ---- one Undo takes the rig off the existing wheels; Redo puts it back, driving ----
    # The build ends in a real undo step (pushed AFTER the body, then the drivers are
    # recompiled so the recompile stays their last op): Undo restores the wheels'
    # driver-free state, and the redone drivers still roll the wheel.
    reset()
    control = empty("control", (0, 0, 0))
    w1 = empty("wheel_L", (2, 0, 0))
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="baseline")
    WheelRig(control, [w1]).rig_rotation(
        movement_axis="translateY", rotation_axis="rotateX", wheel_height=2.0
    )
    bpy.ops.ed.undo()
    w1 = bpy.data.objects["wheel_L"]
    check(
        "undo: one press leaves the wheel driver-free",
        rot_driver(w1, 0) is None and "wheel_rig_id" not in bpy.data.objects["control"],
    )
    bpy.ops.ed.redo()
    control, w1 = bpy.data.objects["control"], bpy.data.objects["wheel_L"]
    control.location = (0, 1, 0)
    bpy.context.view_layer.update()
    check(
        "undo: redo restores the driver, and it rolls the wheel",
        rot_driver(w1, 0) is not None and approx(eval_rot(w1, 0), 1.0),
        f"rot={eval_rot(w1, 0):.4f}",
    )

    # ---- guards ----
    reset()
    try:
        WheelRig(None, [])
        check("rejects invalid inputs", False)
    except ValueError:
        check("rejects invalid inputs", True)

    reset()
    probe = WheelRig(empty("c", (0, 0, 0)), [empty("w", (1, 0, 0))], freeze_transforms=False)
    for bad in ({"movement_axis": "translateW"}, {"rotation_axis": "rotateW"}):
        try:
            probe.rig_rotation(**bad)
            check(f"rejects an unknown axis {bad}", False)
        except ValueError:
            check(f"rejects an unknown axis {bad}", True)

    # ---- from_selection: what tentacle's Wheel Rig panel runs (active = driver) ----
    reset()
    ctrl = empty("ctrl", (0, 0, 0))
    wa = empty("wa", (1, 0, 0))
    wb = empty("wb", (-1, 0, 0))
    for o in (ctrl, wa, wb):
        o.select_set(True)
    bpy.context.view_layer.objects.active = ctrl   # the driver: active (selected last)
    check("default_rig_name: <driver>_wheel_rig before any rig",
          WheelRig.default_rig_name() == "ctrl_wheel_rig", f"{WheelRig.default_rig_name()}")
    built = WheelRig.from_selection(
        freeze_transforms=False, movement_axis="translateY", rotation_axis="rotateY", wheel_height=2.0
    )
    check("from_selection: the active object drives, the rest are wheels",
          built.control == ctrl and sorted(w.name for w in built.wheels) == ["wa", "wb"],
          f"{built.control.name} {[w.name for w in built.wheels]}")
    check("from_selection: wheels driven (rotation_euler[1])",
          rot_driver(wa, 1) is not None and rot_driver(wb, 1) is not None)
    check("from_selection: named after the driver", built.rig_name == "ctrl_wheel_rig", built.rig_name)
    again = WheelRig.from_selection(
        rig_name="other", freeze_transforms=False, movement_axis="translateY", rotation_axis="rotateY"
    )
    check("from_selection: a re-run keeps the stored rig ID", again.rig_name == "ctrl_wheel_rig",
          again.rig_name)
    check("default_rig_name: the stored ID once rigged",
          WheelRig.default_rig_name(ctrl) == "ctrl_wheel_rig")

    # one Undo takes the whole click back (stamp included)
    reset()
    ctrl = empty("ctrl", (0, 0, 0))
    wa = empty("wa", (1, 0, 0))
    for o in (ctrl, wa):
        o.select_set(True)
    bpy.context.view_layer.objects.active = ctrl
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="baseline")
    WheelRig.from_selection(freeze_transforms=False, movement_axis="translateY", wheel_height=2.0)
    check("from_selection: Move Y infers Rotate Y", rot_driver(wa, 1) is not None)
    bpy.ops.ed.undo()
    check("from_selection: one undo leaves the wheel driver-free and the driver unstamped",
          rot_driver(bpy.data.objects["wa"], 1) is None
          and "wheel_rig_id" not in bpy.data.objects["ctrl"])

    # a refused selection raises before anything is touched
    reset()
    lone = empty("lone", (0, 0, 0))
    lone.select_set(True)
    bpy.context.view_layer.objects.active = lone
    try:
        WheelRig.from_selection()
        refused = ""
    except ValueError as exc:
        refused = str(exc)
    check("from_selection: one selected object is refused", "driver" in refused, refused)
    check("from_selection: a refusal stamps nothing", "wheel_rig_id" not in lone)

    # wheel_size: the larger extent across the axes the wheel turns in
    reset()
    bpy.ops.mesh.primitive_cylinder_add(radius=1.0, depth=0.5)   # 2 x 2 x 0.5
    wheel = bpy.context.view_layer.objects.active
    bpy.context.view_layer.update()
    size = WheelRig.wheel_size(wheel, movement_axis="translateZ")
    check("wheel_size: Move Z reads the Y/Z extents (the diameter)", approx(size, 2.0), f"{size}")
    check("wheel_size: the selection's wheel by default",
          approx(WheelRig.wheel_size(movement_axis="translateX") or 0.0, 2.0))
    bpy.ops.object.select_all(action="DESELECT")
    check("wheel_size: nothing selected measures nothing", WheelRig.wheel_size() is None)

except Exception:
    lines.append("FAIL harness | " + traceback.format_exc().replace("\n", " | "))

failed = sum(1 for ln in lines if ln.startswith("FAIL"))
print("\n".join(lines))
result = "PASS" if not failed and lines else "FAIL"
print(f"===RESULT: {result}=== ({len(lines) - failed}/{len(lines)})")
sys.exit(1 if failed else 0)
