"""blendertk.rig_utils.telescope_rig headless test — constraint + driver telescope rig.
Run: blender --background --factory-startup --python blendertk/test/test_telescope_rig.py

Verifies the rig BUILDS (constraints + scale driver wired) and EVALUATES (segments positioned by
the base->end lerp; the middle segment's driven scale.y == 1 at rest and collapses with distance).
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)            # blendertk/
MONO = os.path.dirname(REPO)           # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol

try:
    import bpy
    from blendertk.rig_utils.telescope_rig import TelescopeRig

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def empty(name, loc):
        e = bpy.data.objects.new(name, None)
        e.location = loc
        bpy.context.collection.objects.link(e)
        return e

    def eval_obj(o):
        dg = bpy.context.evaluated_depsgraph_get()
        return o.evaluated_get(dg)

    # ---- build: base@origin, end@(0,10,0), 3 segments (positions irrelevant; constrained) ----
    reset()
    base = empty("base", (0, 0, 0))
    end = empty("end", (0, 10, 0))
    segs = [empty(f"seg{i}", (5, 5, 5)) for i in range(3)]   # start off-line on purpose
    rigged = TelescopeRig().setup_telescope_rig(base, end, segs)
    check("setup returns the 3 segments", len(rigged) == 3, f"n={len(rigged)}")

    # ---- constraints wired ----
    check("base has DAMPED_TRACK", any(c.type == "DAMPED_TRACK" for c in base.constraints))
    check("end has DAMPED_TRACK", any(c.type == "DAMPED_TRACK" for c in end.constraints))
    cl0 = [c for c in segs[0].constraints if c.type == "COPY_LOCATION"]
    cl1 = [c for c in segs[1].constraints if c.type == "COPY_LOCATION"]
    check("seg0 has 1 copy-location (frac 0)", len(cl0) == 1, f"n={len(cl0)}")
    check("seg1 has 2 copy-location (lerp)", len(cl1) == 2, f"n={len(cl1)}")
    check("each seg has a damped-track", all(any(c.type == "DAMPED_TRACK" for c in s.constraints) for s in segs))

    # ---- middle segment has a scale.y driver; endpoints do not ----
    def has_scale_driver(o):
        ad = o.animation_data
        return bool(ad and any(d.data_path == "scale" and d.array_index == 1 for d in ad.drivers))
    check("seg1 (middle) has scale.y driver", has_scale_driver(segs[1]))
    check("seg0 (endpoint) has NO driver", not has_scale_driver(segs[0]))
    check("seg2 (endpoint) has NO driver", not has_scale_driver(segs[2]))

    # ---- evaluated positions: base->end lerp (seg0@base, seg2@end, seg1@midpoint) ----
    bpy.context.view_layer.update()
    p0 = eval_obj(segs[0]).matrix_world.translation
    p1 = eval_obj(segs[1]).matrix_world.translation
    p2 = eval_obj(segs[2]).matrix_world.translation
    check("seg0 at base (0,0,0)", approx(p0.x, 0) and approx(p0.y, 0) and approx(p0.z, 0), f"{tuple(round(v,2) for v in p0)}")
    check("seg1 at midpoint (0,5,0)", approx(p1.x, 0) and approx(p1.y, 5) and approx(p1.z, 0), f"{tuple(round(v,2) for v in p1)}")
    check("seg2 at end (0,10,0)", approx(p2.x, 0) and approx(p2.y, 10) and approx(p2.z, 0), f"{tuple(round(v,2) for v in p2)}")

    # ---- driven scale: at rest distance(=10) the middle scale.y == 1 ----
    s_rest = eval_obj(segs[1]).scale.y
    check("middle scale.y == 1.0 at rest", approx(s_rest, 1.0), f"scale.y={s_rest:.4f}")

    # ---- collapse: move end to (0,5,0) -> distance halves -> middle scale.y == 0.5 ----
    end.location = (0, 5, 0)
    bpy.context.view_layer.update()
    s_collapsed = eval_obj(segs[1]).scale.y
    check("middle scale.y == 0.5 when collapsed to half", approx(s_collapsed, 0.5, 2e-3), f"scale.y={s_collapsed:.4f}")

    # ---- collapsed-distance recorded on base ----
    check("collapsed_distance recorded on base", base.get("telescope_collapsed_distance") == 1.0,
          f"val={base.get('telescope_collapsed_distance')}")

    # ---- guards ----
    reset()
    try:
        TelescopeRig().setup_telescope_rig(empty("b", (0, 0, 0)), empty("e", (0, 1, 0)), [empty("only", (0, 0, 0))])
        check("rejects <2 segments", False)
    except ValueError:
        check("rejects <2 segments", True)

    # ---- Slots.build_rig path (selection ordering + logger config) via lightweight stubs ----
    # Exercises the slot path the functional test skips — would catch a logger reassignment bug.
    from blendertk.rig_utils.telescope_rig import TelescopeRigSlots

    class _Sig:
        def connect(self, *a, **k):
            pass

    class _UI:
        def __init__(self):
            self.btn_build = type("B", (), {"clicked": _Sig()})()
            self.spin_collapsed = type("S", (), {"value": staticmethod(lambda: 1.0)})()
            self.txt003 = type("T", (), {"append": staticmethod(lambda *a, **k: None)})()

    class _SB:
        def __init__(self, ui):
            self.loaded_ui = type("L", (), {"telescope_rig": ui})()
            self.registered_widgets = type("R", (), {})()   # no TextEditLogHandler -> guarded skip
            self.messages = []

        def message_box(self, msg, *a, **k):
            self.messages.append(msg)

    reset()
    objs = [empty("base", (0, 0, 0)), empty("s0", (1, 1, 1)), empty("s1", (2, 2, 2)), empty("end", (0, 10, 0))]
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    sb = _SB(_UI())
    slot = TelescopeRigSlots(sb)
    slot.build_rig()
    check("build_rig: no error message_box", not sb.messages, f"msgs={sb.messages}")
    check("build_rig: rig actually wired (base damped-track)",
          any(c.type == "DAMPED_TRACK" for c in objs[0].constraints))

    # build_rig rejects an under-sized / no-active selection
    reset()
    sb2 = _SB(_UI())
    TelescopeRigSlots(sb2).build_rig()   # nothing selected
    check("build_rig: guards empty selection", len(sb2.messages) == 1, f"msgs={sb2.messages}")

except Exception:
    lines.append("FAIL harness | " + traceback.format_exc().replace("\n", " | "))

failed = sum(1 for ln in lines if ln.startswith("FAIL"))
print("\n".join(lines))
result = "PASS" if not failed and lines else "FAIL"
print(f"===RESULT: {result}=== ({len(lines) - failed}/{len(lines)})")
sys.exit(1 if failed else 0)
