"""blendertk wedge / snap_closest_verts / explode_view headless test.
Run: blender --background --factory-startup --python blendertk/test/test_wedge_snap_explode.py
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

    def reset():
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def cube(x=0.0, size=2.0, name=None):
        bpy.ops.mesh.primitive_cube_add(size=size, location=(x, 0, 0))
        o = bpy.context.active_object
        if name:
            o.name = name
        return o

    # ---- wedge: top face about one of its edges -> faces sweep outward in steps
    reset()
    o = cube()
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(o.data)
    for f in bm.faces:
        f.select = False
    for e in bm.edges:
        e.select = False
    top = next(f for f in bm.faces if all(abs(v.co.z - 1.0) < 1e-4 for v in f.verts))
    top.select = True
    hinge = next(e for e in top.edges if all(abs(v.co.y - 1.0) < 1e-4 for v in e.verts))
    hinge.select = True
    bm.select_history.add(hinge)
    bmesh.update_edit_mesh(o.data)
    f0 = len(bm.faces)
    n = btk.wedge(o, angle=90.0, divisions=4)
    bm = bmesh.from_edit_mesh(o.data)
    check("wedge ran on 1 mesh", n == 1)
    check("wedge adds the swept segments", len(bm.faces) > f0, f"{f0}->{len(bm.faces)}")
    # 90-deg sweep about the y=1/z=1 edge arcs the face up over the hinge: the swept
    # geometry rises above the original z=1 top (ending vertical at z≈2)
    max_z = max(v.co.z for v in bm.verts)
    check("wedge sweeps up over the hinge", max_z > 1.5, f"max_z={max_z:.2f}")
    check("wedge welds the hinge (no degenerate quads)",
          all(f.calc_area() > 1e-9 for f in bm.faces))
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- wedge without a valid selection -> 0
    reset()
    o = cube()
    check("wedge object mode -> 0", btk.wedge(o) == 0)

    # ---- an unrelated ACTIVE edge can't hijack the hinge (Maya contract: "edges from
    #      the selected faces") — the fallback picks one of the face's own edges instead.
    #      A top-edge hinge arcs the face up to z≈2; the bottom-edge hinge would cap
    #      below z≈1.24 (radius 2.24 from the bottom line).
    reset()
    o = cube()
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(o.data)
    for f in bm.faces:
        f.select = False
    for e in bm.edges:
        e.select = False
    top = next(f for f in bm.faces if all(abs(v.co.z - 1.0) < 1e-4 for v in f.verts))
    top.select = True  # bmesh face-select flags its edges too — a valid hinge exists
    bottom_edge = next(
        e for e in bm.edges if all(abs(v.co.z + 1.0) < 1e-4 for v in e.verts)
    )
    bottom_edge.select = True  # selected + ACTIVE, but bounds no selected face
    bm.select_history.add(bottom_edge)
    bmesh.update_edit_mesh(o.data)
    n = btk.wedge(o, angle=90.0, divisions=4)
    bm = bmesh.from_edit_mesh(o.data)
    max_z = max(v.co.z for v in bm.verts)
    check("unrelated active edge doesn't hijack the hinge", n == 1 and max_z > 1.5,
          f"n={n} max_z={max_z:.2f}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- snap_closest_verts: offset twin snaps exactly onto the target
    reset()
    a = cube(x=0.05, name="A")
    b = cube(x=0.0, name="B")
    moved = btk.snap_closest_verts(a, b, tolerance=0.2)
    check("snap moves all 8 verts", moved == 8, f"moved={moved}")
    pos_a = sorted(tuple(round(c, 4) for c in (a.matrix_world @ v.co)) for v in a.data.vertices)
    pos_b = sorted(tuple(round(c, 4) for c in (b.matrix_world @ v.co)) for v in b.data.vertices)
    check("snapped verts coincide with the target", pos_a == pos_b)

    # ---- tolerance excludes far verts
    reset()
    a = cube(x=0.5, name="A")
    b = cube(x=0.0, name="B")
    check("snap outside tolerance moves none",
          btk.snap_closest_verts(a, b, tolerance=0.1) == 0)

    # ---- explode_view: overlapping cubes separate; unexplode restores exactly
    reset()
    a = cube(x=0.5, name="A")
    b = cube(x=-0.5, name="B")
    locs = {o.name: tuple(o.location) for o in (a, b)}
    moved = btk.explode_view([a, b])
    check("explode moves both", len(moved) == 2)
    check("explode stamps the origin", btk.is_exploded([a, b]))
    (amn, amx), (bmn, bmx) = btk.get_world_bbox(a), btk.get_world_bbox(b)
    separated = amx.x < bmn.x or bmx.x < amn.x or amx.y < bmn.y or bmx.y < amn.y \
        or amx.z < bmn.z or bmx.z < amn.z
    check("explode separates the bboxes", separated,
          f"a={amn.x:.2f}..{amx.x:.2f} b={bmn.x:.2f}..{bmx.x:.2f}")
    btk.unexplode_view([a, b])
    check("unexplode restores exact locations",
          all(tuple(o.location) == locs[o.name] for o in (a, b)))
    check("unexplode drops the stamp", not btk.is_exploded([a, b]))

    # ---- exactly-centered geometry (both bbox centers identical) still separates
    reset()
    a = cube(x=0.0, name="A")
    b = cube(x=0.0, name="B")
    btk.explode_view([a, b])
    (amn, amx), (bmn, bmx) = btk.get_world_bbox(a), btk.get_world_bbox(b)
    check("co-located geometry nudges apart", amx.x < bmn.x or bmx.x < amn.x,
          f"a={amn.x:.2f}..{amx.x:.2f} b={bmn.x:.2f}..{bmx.x:.2f}")

    # ---- single object / empty -> no-op
    reset()
    a = cube(name="A")
    check("explode needs 2+ objects", btk.explode_view(a) == [])

    # ---- unexplode_all: restores every exploded object scene-wide, regardless of selection
    reset()
    a = cube(x=0.5, name="A")
    b = cube(x=-0.5, name="B")
    locs = {o.name: tuple(o.location) for o in (a, b)}
    btk.explode_view([a, b])
    bpy.ops.object.select_all(action="DESELECT")  # nothing selected
    restored = btk.unexplode_all()
    check("unexplode_all restores both with nothing selected", len(restored) == 2)
    check("unexplode_all returns to exact locations",
          all(tuple(o.location) == locs[o.name] for o in (a, b)))
    check("unexplode_all clears every stamp", not btk.is_exploded([a, b]))

    # ---- ExplodedViewSlots: panel buttons drive the engine over the live selection ----------
    from blendertk.display_utils.exploded_view import ExplodedViewSlots

    class _UI:
        pass

    class _SB:
        def __init__(self):
            self.loaded_ui = type("L", (), {"exploded_view": _UI()})()
            self.messages = []
        def message_box(self, msg):
            self.messages.append(msg)

    reset()
    a = cube(x=0.5, name="A")
    b = cube(x=-0.5, name="B")
    for o in (a, b):
        o.select_set(True)
    bpy.context.view_layer.objects.active = a
    slots = ExplodedViewSlots(_SB())
    slots.b000()  # Explode
    check("slot b000 explodes the selection", btk.is_exploded([a, b]))
    slots.b001()  # Un-Explode (selected)
    check("slot b001 un-explodes the selection", not btk.is_exploded([a, b]))
    slots.b003()  # Toggle -> explode
    check("slot b003 toggles to exploded", btk.is_exploded([a, b]))
    slots.b003()  # Toggle -> restore
    check("slot b003 toggles back to original", not btk.is_exploded([a, b]))
    slots.b000()  # explode again, then clear selection and Un-Explode All
    bpy.ops.object.select_all(action="DESELECT")
    slots.b002()  # Un-Explode All
    check("slot b002 un-explodes all with nothing selected", not btk.is_exploded([a, b]))

    # ---- slot guards: explode with <2 selected meshes warns instead of acting --------------
    reset()
    a = cube(name="A")
    a.select_set(True)
    bpy.context.view_layer.objects.active = a
    sb = _SB()
    slots = ExplodedViewSlots(sb)
    slots.b000()
    check("slot b000 with <2 selected shows a message", len(sb.messages) == 1, str(sb.messages))

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
