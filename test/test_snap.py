"""blendertk snap (btk.snap_to_grid / snap_to_surface / snap_closest_verts + SnapSlots) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_snap.py

Covers the two new engine functions (grid snap in object + edit mode with axis filtering; surface
projection with offset / threshold / invert push-out semantics) and the SnapSlots button routing
(source-first/target-last via the active object, under-selection guards). snap_closest_verts itself
is covered by test_wedge_snap_explode.
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
    import bmesh
    import blendertk as btk
    from blendertk.edit_utils.snap import SnapSlots

    def reset():
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def plane(z=0.0, size=2.0, name=None):
        bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, z))
        o = bpy.context.active_object
        if name:
            o.name = name
        return o

    # ---- snap_to_grid (object mode): rounds the object's origin to the grid -----------------
    reset()
    o = plane(name="P")
    o.location = (1.3, 2.6, -0.4)
    n = btk.snap_to_grid([o], grid_size=1.0)
    check("grid snap object mode moves the object", n == 1)
    check("grid snap rounds origin to nearest grid point",
          tuple(round(c, 4) for c in o.location) == (1.0, 3.0, 0.0), str(tuple(o.location)))

    # ---- axes filter: only the named axes snap ----------------------------------------------
    reset()
    o = plane(name="P")
    o.location = (1.3, 2.6, -0.4)
    btk.snap_to_grid([o], grid_size=1.0, axes="xy")
    check("axes='xy' leaves z untouched",
          tuple(round(c, 4) for c in o.location) == (1.0, 3.0, -0.4), str(tuple(o.location)))

    # ---- snap_to_grid (edit mode): snaps selected verts in world space ----------------------
    reset()
    o = plane(name="P", size=2.0)  # verts at (±1, ±1, 0)
    o.location = (0.3, 0.0, 0.0)   # world verts now x = 1.3 / -0.7
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(o.data)
    for v in bm.verts:
        v.select = True
    bm.select_flush(True)
    bmesh.update_edit_mesh(o.data)
    moved = btk.snap_to_grid(grid_size=1.0)
    check("grid snap edit mode snaps all selected verts", moved == 4, f"moved={moved}")
    world_x = sorted({round((o.matrix_world @ v.co).x, 4) for v in bm.verts})
    check("edit-mode verts land on integer grid in world space",
          all(abs(x - round(x)) < 1e-4 for x in world_x), str(world_x))
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- snap_to_surface: verts poking THROUGH a target snap out to the surface (offset 0) --
    reset()
    target = plane(z=0.0, size=4.0, name="T")   # +Z normal, spans [-2,2]
    src = plane(z=-1.0, size=2.0, name="S")      # 4 verts below the target (poking through)
    n = btk.snap_to_surface([src], target, offset=0.0)
    check("surface snap moves the 4 below verts", n == 4, f"moved={n}")
    zs = [round((src.matrix_world @ v.co).z, 4) for v in src.data.vertices]
    check("offset=0 places verts on the surface (z~0)", all(abs(z) < 1e-3 for z in zs), str(zs))

    # ---- offset pushes the verts out to exactly the offset distance -------------------------
    reset()
    target = plane(z=0.0, size=4.0, name="T")
    src = plane(z=-1.0, size=2.0, name="S")
    btk.snap_to_surface([src], target, offset=0.5)
    zs = [round((src.matrix_world @ v.co).z, 4) for v in src.data.vertices]
    check("offset=0.5 pushes verts to z~0.5", all(abs(z - 0.5) < 1e-3 for z in zs), str(zs))

    # ---- verts already outside (beyond offset) are NOT moved --------------------------------
    reset()
    target = plane(z=0.0, size=4.0, name="T")
    src = plane(z=2.0, size=2.0, name="S")  # well outside along +Z
    n = btk.snap_to_surface([src], target, offset=0.0)
    check("verts already past the offset are left alone", n == 0, f"moved={n}")

    # ---- threshold skips far verts ----------------------------------------------------------
    reset()
    target = plane(z=0.0, size=4.0, name="T")
    src = plane(z=-5.0, size=2.0, name="S")
    n_far = btk.snap_to_surface([src], target, offset=0.0, threshold=1.0)
    check("threshold skips verts beyond it", n_far == 0, f"moved={n_far}")

    # ---- invert flips the inside/outside sense ----------------------------------------------
    reset()
    target = plane(z=0.0, size=4.0, name="T")
    src = plane(z=-1.0, size=2.0, name="S")
    n_inv = btk.snap_to_surface([src], target, offset=0.0, invert=True)
    check("invert flips the sense (below verts now count as outside, not moved)",
          n_inv == 0, f"moved={n_inv}")

    # ---- non-mesh / empty inputs -> 0 -------------------------------------------------------
    reset()
    target = plane(name="T")
    check("surface snap with no sources -> 0", btk.snap_to_surface([], target) == 0)
    check("surface snap with no target -> 0", btk.snap_to_surface([target], None) == 0)

    # ---- SnapSlots routing: stub the option boxes, drive real bpy selection -----------------
    class _Field:
        def __init__(self, v):
            self.v = v
        def value(self):
            return self.v
        def isChecked(self):
            return self.v
        def text(self):
            return self.v

    def _btn(**fields):
        b = type("B", (), {})()
        b.menu = type("M", (), {})()
        for k, val in fields.items():
            setattr(b.menu, k, _Field(val))
        return b

    class _SB:
        def __init__(self, ui):
            self.loaded_ui = type("L", (), {"snap": ui})()
            self.messages = []
        def message_box(self, msg):
            self.messages.append(msg)

    def make_slots():
        ui = type("U", (), {})()
        ui.b000 = _btn(s000=0.0, s001=0.0, chk000=False)
        ui.b001 = _btn(s002=10.0)
        ui.b002 = _btn(s003=1.0, txt000="xyz")
        sb = _SB(ui)
        return SnapSlots(sb), sb

    # b002 Grid: snaps the selected objects' origins
    reset()
    o = plane(name="P")
    o.location = (1.4, 0.6, 0.0)
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    slots, sb = make_slots()
    slots.b002()
    check("slot b002 grid-snaps the selection", tuple(round(c, 4) for c in o.location)[:2] == (1.0, 1.0),
          str(tuple(o.location)))
    check("slot b002 reports", any("grid" in m.lower() for m in sb.messages), str(sb.messages))

    # b001 Closest Vertex: source first, target last (active)
    reset()
    a = plane(name="A")
    a.location = (0.05, 0, 0)
    b = plane(name="B")
    for x in (a, b):
        x.select_set(True)
    bpy.context.view_layer.objects.active = b  # target = active
    slots, sb = make_slots()
    slots.b001()
    check("slot b001 snaps source verts to target (active=target)",
          any("Snapped" in m for m in sb.messages), str(sb.messages))

    # b001 guard: a single selected mesh warns
    reset()
    a = plane(name="A")
    a.select_set(True)
    bpy.context.view_layer.objects.active = a
    slots, sb = make_slots()
    slots.b001()
    check("slot b001 with one mesh warns", any("exactly two" in m for m in sb.messages), str(sb.messages))

    # b000 Surface: sources + target(active)
    reset()
    target = plane(z=0.0, size=4.0, name="T")
    src = plane(z=-1.0, size=2.0, name="S")
    for x in (src, target):
        x.select_set(True)
    bpy.context.view_layer.objects.active = target
    slots, sb = make_slots()
    slots.b000()
    zs = [round((src.matrix_world @ v.co).z, 4) for v in src.data.vertices]
    check("slot b000 snaps sources onto the target surface", all(abs(z) < 1e-3 for z in zs), str(zs))

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
