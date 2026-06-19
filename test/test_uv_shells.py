"""blendertk UV-shell helpers headless test — islands, stack/restore, distribute, straighten.
Run: blender --background --factory-startup --python blendertk/test/test_uv_shells.py
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

    def quads_object(uv_rects, name="UVQuads"):
        """A mesh of disconnected unit quads, one per ``uv_rects`` entry
        ``(u0, v0, u1, v1)`` — each quad is its own UV island."""
        bm = bmesh.new()
        uvl = bm.loops.layers.uv.new("UVMap")
        for n, (u0, v0, u1, v1) in enumerate(uv_rects):
            x = n * 3.0
            verts = [bm.verts.new((x + dx, dy, 0.0)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
            face = bm.faces.new(verts)
            for loop, (lu, lv) in zip(face.loops, ((u0, v0), (u1, v0), (u1, v1), (u0, v1))):
                loop[uvl].uv = (lu, lv)
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        return o

    def island_centers(o):
        """Sorted (cu, cv) bbox centers of the object's UV islands."""
        bm = bmesh.new()
        bm.from_mesh(o.data)
        uvl = bm.loops.layers.uv.active
        from blendertk.uv_utils._uv_utils import _uv_islands, _island_bbox_center
        centers = sorted(
            tuple(round(c, 4) for c in _island_bbox_center(isl, uvl))
            for isl in _uv_islands(bm, uvl)
        )
        bm.free()
        return centers

    # ---- stack: both islands end on the first island's center; snapshot restores
    reset()
    o = quads_object([(0.0, 0.0, 0.2, 0.2), (0.6, 0.6, 0.8, 0.8)])
    before = island_centers(o)
    check("two islands detected", len(before) == 2, f"{before}")
    snapshot = btk.get_uv_coords([o])
    moved = btk.stack_uv_shells([o])
    centers = island_centers(o)
    check("stack moves one island", moved == 1, f"moved={moved}")
    check("stacked centers coincide", centers[0] == centers[1], f"{centers}")
    btk.set_uv_coords([o], snapshot)
    check("snapshot restore returns the originals", island_centers(o) == before)

    # ---- stack across objects shares one target center
    reset()
    o1 = quads_object([(0.0, 0.0, 0.2, 0.2)], name="A")
    o2 = quads_object([(0.5, 0.5, 0.9, 0.9)], name="B")
    btk.stack_uv_shells([o1, o2])
    check("cross-object stack lands on the first center",
          island_centers(o1) == island_centers(o2) == [(0.1, 0.1)],
          f"{island_centers(o1)} vs {island_centers(o2)}")

    # ---- distribute: middle island spaces evenly between the endpoints
    reset()
    o = quads_object([
        (0.0, 0.0, 0.2, 0.2),   # center u=0.1
        (0.2, 0.0, 0.4, 0.2),   # center u=0.3 -> should move to 0.5
        (0.8, 0.0, 1.0, 0.2),   # center u=0.9
    ])
    moved = btk.distribute_uv_shells(o, axis="u")
    centers = [c[0] for c in island_centers(o)]
    check("distribute repositions the middle island", moved == 1, f"moved={moved}")
    check("distribute spaces centers evenly", centers == [0.1, 0.5, 0.9], f"{centers}")
    check("distribute with <3 islands is a no-op",
          btk.distribute_uv_shells(quads_object([(0, 0, 0.1, 0.1)], name="Solo")) == 0)

    # ---- straighten: a skewed near-horizontal UV edge flattens in V
    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(o.data)
    uvl = bm.loops.layers.uv.verify()
    # skew the (1, 0) corner up to (1, 0.1) — the bottom edge becomes ~5.7 deg off horizontal
    for f in bm.faces:
        for loop in f.loops:
            if loop[uvl].uv.x > 0.5 and loop[uvl].uv.y < 0.5:
                loop[uvl].uv.y = 0.1
    for e in bm.edges:
        e.select = True
    bmesh.update_edit_mesh(o.data)
    snapped = btk.straighten_uvs(o, u=True, v=False, angle=30)
    bm = bmesh.from_edit_mesh(o.data)
    uvl = bm.loops.layers.uv.active
    bottom_vs = sorted(
        round(loop[uvl].uv.y, 4)
        for f in bm.faces
        for loop in f.loops
        if loop[uvl].uv.y < 0.5
    )
    check("straighten snaps a near-horizontal edge", snapped >= 1, f"snapped={snapped}")
    check("straighten flattens V to the average", bottom_vs == [0.05, 0.05], f"{bottom_vs}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- straighten leaves steep edges alone
    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(o.data)
    for e in bm.edges:
        e.select = True
    bmesh.update_edit_mesh(o.data)
    # default plane UVs are exactly square: nothing within a 30-deg threshold needs moving
    check("straighten on already-straight UVs snaps none",
          btk.straighten_uvs(o, u=True, v=True, angle=30) == 0)
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- edit-mode stack targets only selection-touched islands
    reset()
    o = quads_object([(0.0, 0.0, 0.2, 0.2), (0.4, 0.4, 0.6, 0.6), (0.7, 0.7, 0.9, 0.9)])
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(o.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select = False
    bm.faces[0].select = True
    bm.faces[1].select = True  # islands 0+1 targeted; island 2 must not move
    bmesh.update_edit_mesh(o.data)
    btk.stack_uv_shells(o)
    bpy.ops.object.mode_set(mode="OBJECT")
    centers = island_centers(o)
    check("edit-mode stack leaves unselected islands alone", (0.8, 0.8) in centers, f"{centers}")
    check("edit-mode stack stacks the selected ones",
          centers.count((0.1, 0.1)) == 2, f"{centers}")

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
