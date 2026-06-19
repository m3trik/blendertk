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
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

def approx(a, b, tol=2e-3):
    return abs(a - b) <= tol

try:
    import bpy
    from blendertk.rig_utils.wheel_rig import WheelRig, WheelRigSlots

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
    rigged = rig.rig_rotation(movement_axis="LOC_Y", rotation_index=0, wheel_height=2.0)  # radius=1
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
    rig.rig_rotation(movement_axis="LOC_Y", rotation_index=0, wheel_height=2.0)
    drivers_on_axis = [d for d in (w1.animation_data.drivers if w1.animation_data else [])
                       if d.data_path == "rotation_euler" and d.array_index == 0]
    check("rebuild does not stack drivers", len(drivers_on_axis) == 1, f"n={len(drivers_on_axis)}")

    # ---- guards ----
    reset()
    try:
        WheelRig(None, [])
        check("rejects invalid inputs", False)
    except ValueError:
        check("rejects invalid inputs", True)

    # ---- Slots.wheel_rig path via stubs (selection ordering: active=control, rest=wheels) ----
    class _Sig:
        def connect(self, *a, **k): pass

    class _LE:
        def __init__(self, t): self._t = t
        def text(self): return self._t

    class _Cmb:
        def currentIndex(self): return 1  # Move Y -> Rotate Y

    class _UI:
        def __init__(self):
            self.b000 = type("B", (), {"clicked": _Sig()})()
            self.s000 = _LE("2.0")
            self.cmb000 = _Cmb()
            self.txt000 = _LE("")

    class _SB:
        def __init__(self, ui):
            self.loaded_ui = type("L", (), {"wheel_rig": ui})()
            self.messages = []
        def message_box(self, msg, *a, **k): self.messages.append(msg)

    reset()
    ctrl = empty("ctrl", (0, 0, 0))
    wa = empty("wa", (1, 0, 0)); wb = empty("wb", (-1, 0, 0))
    for o in (ctrl, wa, wb):
        o.select_set(True)
    bpy.context.view_layer.objects.active = ctrl   # control = active (last)
    sb = _SB(_UI())
    WheelRigSlots(sb).wheel_rig()
    check("Slots.wheel_rig: no error", not sb.messages, f"msgs={sb.messages}")
    check("Slots.wheel_rig: wheels driven (rotation_euler[1])",
          rot_driver(wa, 1) is not None and rot_driver(wb, 1) is not None)

except Exception:
    lines.append("FAIL harness | " + traceback.format_exc().replace("\n", " | "))

failed = sum(1 for ln in lines if ln.startswith("FAIL"))
print("\n".join(lines))
print(f"===RESULT=== {len(lines) - failed}/{len(lines)} passed")
sys.exit(1 if failed else 0)
