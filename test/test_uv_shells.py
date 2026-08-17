"""blendertk UV-shell helpers headless test — islands, stack/restore, distribute, straighten.
Run: blender --background --factory-startup --python blendertk/test/test_uv_shells.py
"""

import sys
import os
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


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
            verts = [
                bm.verts.new((x + dx, dy, 0.0))
                for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))
            ]
            face = bm.faces.new(verts)
            for loop, (lu, lv) in zip(
                face.loops, ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
            ):
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
        from blendertk.uv_utils._uv_utils import UvUtils

        centers = sorted(
            tuple(round(c, 4) for c in UvUtils._island_bbox_center(isl, uvl))
            for isl in UvUtils._uv_islands(bm, uvl)
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
    check(
        "cross-object stack lands on the first center",
        island_centers(o1) == island_centers(o2) == [(0.1, 0.1)],
        f"{island_centers(o1)} vs {island_centers(o2)}",
    )

    # ---- distribute: middle island spaces evenly between the endpoints
    reset()
    o = quads_object(
        [
            (0.0, 0.0, 0.2, 0.2),  # center u=0.1
            (0.2, 0.0, 0.4, 0.2),  # center u=0.3 -> should move to 0.5
            (0.8, 0.0, 1.0, 0.2),  # center u=0.9
        ]
    )
    moved = btk.distribute_uv_shells(o, axis="u")
    centers = [c[0] for c in island_centers(o)]
    check("distribute repositions the middle island", moved == 1, f"moved={moved}")
    check("distribute spaces centers evenly", centers == [0.1, 0.5, 0.9], f"{centers}")
    check(
        "distribute with <3 islands is a no-op",
        btk.distribute_uv_shells(quads_object([(0, 0, 0.1, 0.1)], name="Solo")) == 0,
    )

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
    check(
        "straighten flattens V to the average",
        bottom_vs == [0.05, 0.05],
        f"{bottom_vs}",
    )
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
    check(
        "straighten on already-straight UVs snaps none",
        btk.straighten_uvs(o, u=True, v=True, angle=30) == 0,
    )
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- stack_uv_shells(tolerance=...): only same-shape islands group together (shape, not
    #      size -- Maya's polyUVStackSimilarShells stacks a half-size copy too, probed)
    reset()
    o = quads_object(
        [
            (0.0, 0.0, 0.2, 0.2),  # A: 0.2x0.2 square
            (0.5, 0.5, 0.7, 0.7),  # B: 0.2x0.2 square -- same shape, stacks onto A
            (0.0, 0.5, 0.6, 0.8),  # C: 0.6x0.3 rectangle -- different shape, stays put
        ]
    )
    moved = btk.stack_uv_shells([o], tolerance=1.0)
    centers = island_centers(o)
    check("stack_similar moves only the matching island", moved == 1, f"moved={moved}")
    check(
        "stack_similar leaves the dissimilar island in place",
        (0.3, 0.65) in centers,
        f"{centers}",
    )
    check(
        "stack_similar groups same-shape islands together",
        centers.count((0.1, 0.1)) == 2,
        f"{centers}",
    )

    # ---- a same-shape island of a different SIZE is similar (scaled onto the reference)
    reset()
    o = quads_object([(0.0, 0.0, 0.2, 0.2), (0.5, 0.5, 1.1, 1.1)])  # 0.2 vs 0.6 squares
    moved = btk.stack_uv_shells([o], tolerance=0.0)
    centers = island_centers(o)
    check(
        "stack_similar treats a scaled copy as similar and fits it onto the reference",
        moved == 1 and centers.count((0.1, 0.1)) == 2,
        f"moved={moved} {centers}",
    )

    # ---- stack_uv_shells(tolerance=0): near-exact SHAPE required -- a 5% aspect gap no longer groups
    reset()
    o = quads_object(
        [(0.0, 0.0, 0.2, 0.2), (0.5, 0.5, 0.7, 0.71)]
    )  # square vs 0.2x0.21 -- 5% off
    moved = btk.stack_uv_shells([o], tolerance=0.0)
    check(
        "stack_similar tolerance=0 rejects a near-but-not-exact shape",
        moved == 0,
        f"moved={moved}",
    )
    moved = btk.stack_uv_shells([o], tolerance=1.0)
    check(
        "stack_similar tolerance=1 accepts that 5% shape gap",
        moved == 1,
        f"moved={moved}",
    )

    # ---- straighten_uv_shells: a sheared quad-grid rectangularizes via Follow Active Quads
    reset()
    bpy.ops.mesh.primitive_plane_add(size=2)
    o = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide(number_cuts=3)  # 4x4 grid of quads, one connected island

    def is_axis_aligned(bm, uvl):
        for f in bm.faces:
            us = {round(loop[uvl].uv.x, 4) for loop in f.loops}
            vs = {round(loop[uvl].uv.y, 4) for loop in f.loops}
            if len(us) != 2 or len(vs) != 2:
                return False
        return True

    bm = bmesh.from_edit_mesh(o.data)
    uvl = bm.loops.layers.uv.verify()
    # shear the grid's UVs from vertex position (u = x + 0.3y, v = y) -- a genuinely
    # unstraightened (non-axis-aligned per face) shell, continuous across the whole island.
    for f in bm.faces:
        for loop in f.loops:
            x, y = loop.vert.co.x, loop.vert.co.y
            loop[uvl].uv = (x + 0.3 * y, y)
    bpy.ops.mesh.select_all(action="SELECT")
    bmesh.update_edit_mesh(o.data)
    check("sheared grid starts non-axis-aligned", not is_axis_aligned(bm, uvl))

    straightened = btk.straighten_uv_shells(o)
    bm2 = bmesh.from_edit_mesh(o.data)
    uvl2 = bm2.loops.layers.uv.active
    check(
        "straighten_uv_shells processes the one island",
        straightened == 1,
        f"n={straightened}",
    )
    check("straighten_uv_shells rectangularizes every face", is_axis_aligned(bm2, uvl2))
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- straighten_uv_shells skips objects not in Edit Mode (object-mode is a no-op)
    reset()
    o = quads_object([(0.0, 0.0, 0.2, 0.2)])
    check("straighten_uv_shells object-mode no-op", btk.straighten_uv_shells([o]) == 0)

    # ---- derive_auto_seams: a temp Smart-Project pass marks real seams, leaves the UV
    # layer count/active layer untouched
    reset()
    bpy.ops.mesh.primitive_cube_add()
    o = bpy.context.active_object
    n_layers_before = len(o.data.uv_layers)
    original_name = o.data.uv_layers.active.name
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bm = bmesh.from_edit_mesh(o.data)
    seams_before = sum(1 for e in bm.edges if e.seam)
    n = btk.derive_auto_seams([o])
    bm2 = bmesh.from_edit_mesh(o.data)
    seams_after = sum(1 for e in bm2.edges if e.seam)
    check("derive_auto_seams processes one mesh", n == 1, f"n={n}")
    check(
        "derive_auto_seams marks new seams",
        seams_after > seams_before,
        f"{seams_before}->{seams_after}",
    )
    check(
        "derive_auto_seams leaves the UV-layer count unchanged",
        len(o.data.uv_layers) == n_layers_before,
        f"layers={len(o.data.uv_layers)}",
    )
    check(
        "derive_auto_seams restores the active layer",
        o.data.uv_layers.active.name == original_name,
        f"active={o.data.uv_layers.active.name}",
    )
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
    check(
        "edit-mode stack leaves unselected islands alone",
        (0.8, 0.8) in centers,
        f"{centers}",
    )
    check(
        "edit-mode stack stacks the selected ones",
        centers.count((0.1, 0.1)) == 2,
        f"{centers}",
    )

    # ---- stack_uv_shells(tolerance): a rotated / scaled twin is fitted onto the reference
    #      vertex-for-vertex (Maya's polyUVStackSimilarShells), not just center-aligned
    import math

    def island_loops(o):
        """Per-island loop UV lists (face-index order), sorted by island bbox center."""
        bm = bmesh.new()
        bm.from_mesh(o.data)
        uvl = bm.loops.layers.uv.active
        from blendertk.uv_utils._uv_utils import UvUtils

        out = sorted(
            (
                UvUtils._island_bbox_center(isl, uvl),
                [(l[uvl].uv.x, l[uvl].uv.y) for f in isl for l in f.loops],
            )
            for isl in UvUtils._uv_islands(bm, uvl)
        )
        bm.free()
        return [pts for _, pts in out]

    def transform_island_uvs(o, face_index, angle_deg, scale=1.0, du=0.0, dv=0.0):
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bm.faces.ensure_lookup_table()
        uvl = bm.loops.layers.uv.active
        f = bm.faces[face_index]
        cu = sum(l[uvl].uv.x for l in f.loops) / len(f.loops)
        cv = sum(l[uvl].uv.y for l in f.loops) / len(f.loops)
        a = math.radians(angle_deg)
        for l in f.loops:
            x, y = l[uvl].uv.x - cu, l[uvl].uv.y - cv
            l[uvl].uv = (
                cu + du + scale * (x * math.cos(a) - y * math.sin(a)),
                cv + dv + scale * (x * math.sin(a) + y * math.cos(a)),
            )
        bm.to_mesh(o.data)
        bm.free()

    def max_pair_dist(pa, pb):
        return max(math.dist(x, y) for x, y in zip(pa, pb))

    reset()
    o = quads_object([(0.0, 0.0, 0.2, 0.1), (0.5, 0.5, 0.7, 0.6)])  # two 0.2x0.1 rects
    transform_island_uvs(o, 1, 37.0)  # rotate the twin (bbox no longer matches naively)
    moved = btk.stack_uv_shells([o], tolerance=1.0)
    ref, twin = island_loops(o)
    check("similar stack fits a rotated twin", moved == 1, f"moved={moved}")
    check(
        "rotated twin lands vertex-for-vertex on the reference",
        max_pair_dist(ref, twin) < 1e-6,
        f"max dist {max_pair_dist(ref, twin):.6f}",
    )

    reset()
    o = quads_object([(0.0, 0.0, 0.2, 0.1), (0.5, 0.5, 0.7, 0.6)])
    transform_island_uvs(o, 1, 90.0, scale=0.5)  # rotated AND half-size
    moved = btk.stack_uv_shells([o], tolerance=1.0)
    ref, twin = island_loops(o)
    check(
        "similar stack fits a rotated + scaled twin exactly",
        moved == 1 and max_pair_dist(ref, twin) < 1e-6,
        f"moved={moved} max dist {max_pair_dist(ref, twin):.6f}",
    )

    # a mirrored copy has no rigid correspondence -> falls back to the center stack
    reset()
    o = quads_object([(0.0, 0.0, 0.2, 0.1), (0.7, 0.5, 0.5, 0.6)])  # second: u0 > u1 = mirrored
    moved = btk.stack_uv_shells([o], tolerance=1.0)
    centers = island_centers(o)
    check(
        "mirrored look-alike falls back to the center stack",
        moved == 1 and centers.count((0.1, 0.05)) == 2,
        f"moved={moved} {centers}",
    )

    # ---- get_uv_coords(pins=True) round-trips pin state; pin_uvs(whole_shells) pins islands
    reset()
    o = quads_object([(0.0, 0.0, 0.2, 0.2), (0.6, 0.6, 0.8, 0.8)])

    def pins(o):
        bm = bmesh.new()
        bm.from_mesh(o.data)
        uvl = bm.loops.layers.uv.active
        out = [bool(l[uvl].pin_uv) for f in sorted(bm.faces, key=lambda f: f.index) for l in f.loops]
        bm.free()
        return out

    snap = btk.get_uv_coords([o], pins=True)
    check(
        "pins=True snapshot carries a pin flag per loop",
        all(len(e) == 3 for e in snap[o.name]) and not any(e[2] for e in snap[o.name]),
    )
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(o.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select = False
    bm.faces[0].select = True  # island 0 touched (the stack's own _target_islands scope)
    bmesh.update_edit_mesh(o.data)
    btk.pin_uvs([o], pin=True, selected_only=True, whole_shells=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    check(
        "whole_shells pins every loop of the touched island only",
        pins(o) == [True] * 4 + [False] * 4,
        f"{pins(o)}",
    )
    btk.set_uv_coords([o], snap)
    check("restoring a pins=True snapshot unpins again", pins(o) == [False] * 8, f"{pins(o)}")

    # ---- get_similar_uv_shells: the Stack (Similar) oracle as a query; select=True applies it
    reset()
    o = quads_object([(0.0, 0.0, 0.2, 0.1), (0.5, 0.5, 0.7, 0.6), (0.0, 0.5, 0.6, 0.65), (0.7, 0.0, 0.9, 0.1)])
    transform_island_uvs(o, 1, 37.0)          # island 1: rotated twin of 0
    # island 2: 0.6x0.15 (4:1) -> different shape (a 3x scaled 2:1 rect WOULD be similar --
    # shape, not size); island 3: another twin of 0
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(o.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select = False
    bm.faces[0].select = True                  # reference = island 0
    bmesh.update_edit_mesh(o.data)
    before = btk.get_uv_coords([o])
    found = btk.get_similar_uv_shells([o], tolerance=1.0)
    check("get_similar_uv_shells finds the twins of the selected island",
          found.get(o.name) == [1, 3], f"{found}")
    check("get_similar_uv_shells moves nothing", btk.get_uv_coords([o]) == before)
    bm = bmesh.from_edit_mesh(o.data)
    bm.faces.ensure_lookup_table()
    check("get_similar_uv_shells without select leaves the selection alone",
          [f.index for f in bm.faces if f.select] == [0])
    found = btk.get_similar_uv_shells([o], tolerance=1.0, include_reference=True, select=True)
    bm = bmesh.from_edit_mesh(o.data)
    bm.faces.ensure_lookup_table()
    sel_faces = [f.index for f in bm.faces if f.select]
    check("select=True selects the similar islands, reference included",
          found.get(o.name) == [0, 1, 3] and sel_faces == [0, 1, 3], f"faces={sel_faces} found={found}")
    for f in bm.faces:                         # back to reference = island 0
        f.select = f.index == 0
    bmesh.update_edit_mesh(o.data)
    found = btk.get_similar_uv_shells([o], tolerance=1.0, select=True)  # replace: twins only
    bm = bmesh.from_edit_mesh(o.data)
    bm.faces.ensure_lookup_table()
    check("select=True without include_reference replaces the selection with the twins",
          [f.index for f in bm.faces if f.select] == [1, 3])
    bpy.ops.object.mode_set(mode="OBJECT")
    check("get_similar_uv_shells is edit-mode only", btk.get_similar_uv_shells([o]) == {})

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(
    f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})"
)
