"""blendertk DynamicPipe (btk.DynamicPipe engine + DynamicPipeSlots) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_dynamic_pipe.py

Covers the engine — curve build (one control point per handle, beveled into a round tube), the Hook
modifiers (one per handle, bound so the geometry doesn't jump at bind time), the **live follow**
(move a handle → its hooked control point + the evaluated pipe geometry move with it), in-between
Empty insertion, and the under-selection guard. Also exercises DynamicPipeSlots.b000 routing
(name-ordered selection → engine; the no-Qt header_init is covered by the handler test under .venv).
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import bpy
    import blendertk as btk
    from blendertk.edit_utils.dynamic_pipe import DynamicPipe, DynamicPipeSlots

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def handle(name, x=0.0, y=0.0, z=0.0):
        o = bpy.data.objects.new(name, None)
        o.location = (x, y, z)
        bpy.context.collection.objects.link(o)
        return o

    def eval_bbox(obj):
        """World-space bbox (min, max) of the curve's EVALUATED (post-modifier, beveled) mesh."""
        deps = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(deps)
        me = ev.to_mesh()
        try:
            cos = [obj.matrix_world @ v.co for v in me.vertices]
        finally:
            ev.to_mesh_clear()
        mn = [min(c[i] for c in cos) for i in range(3)]
        mx = [max(c[i] for c in cos) for i in range(3)]
        return mn, mx

    # ---- build: curve + control points + bevel ------------------------------------------------
    reset()
    a = handle("h_a", x=0.0)
    b = handle("h_b", x=4.0)
    pipe = DynamicPipe([a, b], radius=0.5)
    check("curve object created", pipe.curve is not None and pipe.curve.type == "CURVE",
          getattr(pipe.curve, "type", None))
    spline = pipe.curve.data.splines[0]
    check("one control point per handle", len(spline.points) == 2, str(len(spline.points)))
    check("bevel gives the pipe its radius", abs(pipe.curve.data.bevel_depth - 0.5) < 1e-6,
          str(pipe.curve.data.bevel_depth))
    check("control points placed at handle world positions",
          abs(spline.points[0].co.x - 0.0) < 1e-4 and abs(spline.points[1].co.x - 4.0) < 1e-4,
          f"{spline.points[0].co.x},{spline.points[1].co.x}")

    # ---- hooks: one per handle, bound to the matching control point ---------------------------
    hooks = [m for m in pipe.curve.modifiers if m.type == "HOOK"]
    check("one hook modifier per handle", len(hooks) == 2, str(len(hooks)))
    check("each hook targets its handle", {m.object for m in hooks} == {a, b},
          str([m.object.name for m in hooks]))

    # ---- bind is clean: evaluated pipe runs A..B along X, bevel extends in Y/Z (no jump) ------
    # (the tube runs along X, so its round profile extends Y/Z — NOT X — by ~the radius.)
    mn, mx = eval_bbox(pipe.curve)
    check("evaluated pipe spans handle A..B along the path (no jump)",
          abs(mn[0] - 0.0) < 0.1 and abs(mx[0] - 4.0) < 0.1,
          f"x∈[{round(mn[0],3)},{round(mx[0],3)}]")
    check("bevel gives the tube a ~2*radius cross-section (Y extent)",
          abs((mx[1] - mn[1]) - 1.0) < 0.2, f"Y extent {round(mx[1]-mn[1],3)}")

    # ---- LIVE FOLLOW: move a handle → the evaluated pipe follows -------------------------------
    b.location = (8.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    mn2, mx2 = eval_bbox(pipe.curve)
    check("moving handle B extends the pipe with it (live hook follow)",
          mx2[0] > mx[0] + 3.0, f"max x {round(mx[0],3)} -> {round(mx2[0],3)}")

    # move along Z too — confirms the hook is a full 3D follow, not axis-locked
    a.location = (0.0, 0.0, 3.0)
    bpy.context.view_layer.update()
    mn3, mx3 = eval_bbox(pipe.curve)
    check("moving handle A in Z lifts the pipe (3D follow)", mx3[2] > 2.0,
          f"max z {round(mx2[2],3)} -> {round(mx3[2],3)}")

    # ---- in-between Empties: inserted by linear interpolation ---------------------------------
    reset()
    a = handle("h_a", x=0.0)
    b = handle("h_b", x=4.0)
    pipe = DynamicPipe([a, b], num_inbetween=1)
    check("one in-between handle inserted", len(pipe.handles) == 3, str(len(pipe.handles)))
    check("in-between sits at the midpoint",
          abs(pipe.handles[1].location.x - 2.0) < 1e-4, str(pipe.handles[1].location.x))
    check("in-between drives its own control point",
          len([m for m in pipe.curve.modifiers if m.type == "HOOK"]) == 3,
          str(len(pipe.curve.modifiers)))

    # ---- guard: fewer than two handles raises -------------------------------------------------
    reset()
    a = handle("solo")
    raised = False
    try:
        DynamicPipe([a])
    except ValueError:
        raised = True
    check("under two handles raises ValueError", raised)

    # ---- DynamicPipeSlots.b000 routing --------------------------------------------------------
    class _SB:
        def __init__(self, ui):
            self.loaded_ui = type("L", (), {"dynamic_pipe": ui})()
            self.messages = []
        def message_box(self, msg):
            self.messages.append(msg)

    def make_slots():
        ui = type("U", (), {})()
        sb = _SB(ui)
        return DynamicPipeSlots(sb), sb

    # b000: name-ordered selection builds a pipe through the selected handles
    reset()
    h2 = handle("h_02", x=4.0)
    h1 = handle("h_01", x=0.0)  # created out of name order
    for h in (h1, h2):
        h.select_set(True)
    bpy.context.view_layer.objects.active = h2
    slots, sb = make_slots()
    slots.b000()
    check("slot b000 builds a pipe from the selection",
          slots.pipe is not None and any("Built" in m for m in sb.messages), str(sb.messages))
    check("slot b000 orders handles by name (h_01 before h_02)",
          slots.pipe is not None and [h.name for h in slots.pipe.handles] == ["h_01", "h_02"],
          str([h.name for h in slots.pipe.handles]) if slots.pipe else "no pipe")

    # b000 guard: a single selected object warns
    reset()
    h = handle("solo")
    h.select_set(True)
    bpy.context.view_layer.objects.active = h
    slots, sb = make_slots()
    slots.b000()
    check("slot b000 with one object warns", any("at least two" in m for m in sb.messages),
          str(sb.messages))

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
