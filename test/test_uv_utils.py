"""blendertk.uv_utils headless test — UV translate + UV-set cleanup (mesh UV data, no editor).
Run: blender --background --factory-startup --python blendertk/test/test_uv_utils.py
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
    import pythontk as ptk
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def uv_bounds(o):
        bm = bmesh.new()
        bm.from_mesh(o.data)
        uvl = bm.loops.layers.uv.active
        us = [loop[uvl].uv.x for f in bm.faces for loop in f.loops]
        vs = [loop[uvl].uv.y for f in bm.faces for loop in f.loops]
        bm.free()
        return min(us), max(us), min(vs), max(vs)

    # move_uvs object mode: +1 in u
    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object  # default UV 0..1
    u0 = uv_bounds(o)[0]
    btk.move_uvs(o, du=1.0)
    check(
        "move_uvs du=+1 -> u shifted +1",
        abs(uv_bounds(o)[0] - (u0 + 1.0)) < 1e-4,
        f"minu {u0:.2f}->{uv_bounds(o)[0]:.2f}",
    )
    btk.move_uvs(o, dv=-2.0)
    check(
        "move_uvs dv=-2 -> v shifted -2",
        abs(uv_bounds(o)[2] - (-2.0)) < 1e-4,
        f"minv={uv_bounds(o)[2]:.2f}",
    )

    # get_uv_bounds: agrees with the local probe, in (u_min, v_min, u_max, v_max)
    # order (mirrors mayatk). The Shell Xform snap mode leans on this pairing —
    # the bounds must describe exactly what move_uvs then shifts.
    min_u, max_u, min_v, max_v = uv_bounds(o)
    bounds = btk.get_uv_bounds(o)
    check(
        "get_uv_bounds -> (u_min, v_min, u_max, v_max)",
        bounds is not None
        and max(abs(a - b) for a, b in zip(bounds, (min_u, min_v, max_u, max_v)))
        < 1e-4,
        f"{bounds} vs {(min_u, min_v, max_u, max_v)}",
    )
    before = btk.get_uv_bounds(o)
    btk.move_uvs(o, du=0.25, dv=-0.75)
    after = btk.get_uv_bounds(o)
    check(
        "get_uv_bounds tracks a move exactly",
        abs(after[0] - (before[0] + 0.25)) < 1e-4
        and abs(after[1] - (before[1] - 0.75)) < 1e-4,
        f"{before} -> {after}",
    )

    # No mesh / no UVs -> None, not a crash (the panel shows a message box).
    check("get_uv_bounds on an empty selection -> None", btk.get_uv_bounds([]) is None)

    # get_neighbor_shell_bounds: shells sharing the input's UV space, minus its own.
    # Backs the move pad's "snap to shell" mode.
    reset()
    bpy.ops.mesh.primitive_plane_add()
    near = bpy.context.active_object  # UVs 0..1
    bpy.ops.mesh.primitive_plane_add()
    far = bpy.context.active_object
    btk.move_uvs(far, du=3.0)  # UVs 3..4

    near_sees = btk.get_neighbor_shell_bounds(near)
    check(
        "get_neighbor_shell_bounds -> only the other mesh",
        len(near_sees) == 1 and abs(near_sees[0][0] - 3.0) < 1e-4,
        f"{near_sees}",
    )
    far_sees = btk.get_neighbor_shell_bounds(far)
    check(
        "and symmetrically from the other side",
        len(far_sees) == 1 and abs(far_sees[0][0]) < 1e-4,
        f"{far_sees}",
    )
    check(
        "a mesh alone in its UV layer has no neighbours",
        btk.get_neighbor_shell_bounds([near, far]) == [],
    )
    # UV-layer scoping: a different active layer name is a different UV space.
    far.data.uv_layers.active.name = "lightmap"
    check(
        "a differently-named active UV layer is not a neighbour",
        btk.get_neighbor_shell_bounds(near) == [],
        f"{btk.get_neighbor_shell_bounds(near)}",
    )
    far.data.uv_layers.active.name = "UVMap"
    check(
        "renamed back onto the shared layer, it counts again",
        len(btk.get_neighbor_shell_bounds(near)) == 1,
    )
    check("get_neighbor_shell_bounds on an empty selection -> []",
          btk.get_neighbor_shell_bounds([]) == [])

    # scale_uvs: half-U about the origin -> 0..1 map becomes 0..0.5 in u, v untouched
    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object
    btk.scale_uvs(o, su=0.5)
    bmin_u, bmax_u, bmin_v, bmax_v = uv_bounds(o)
    check(
        "scale_uvs su=0.5 about origin -> u 0..0.5, v 0..1",
        abs(bmax_u - 0.5) < 1e-4 and abs(bmax_v - 1.0) < 1e-4,
        f"u max={bmax_u:.3f} v max={bmax_v:.3f}",
    )
    # scale_uvs about a non-origin pivot: quarter coverage anchored at tile corner (2,3)
    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object
    btk.move_uvs(o, du=2.0, dv=3.0)  # map now 2..3 / 3..4 (tile 2,3)
    btk.scale_uvs(o, su=0.5, sv=0.5, pivot=(2.0, 3.0))
    bmin_u, bmax_u, bmin_v, bmax_v = uv_bounds(o)
    check(
        "scale_uvs quarter about tile corner -> anchored at (2,3), half extents",
        abs(bmin_u - 2.0) < 1e-4 and abs(bmax_u - 2.5) < 1e-4 and abs(bmax_v - 3.5) < 1e-4,
        f"u {bmin_u:.3f}..{bmax_u:.3f} v {bmin_v:.3f}..{bmax_v:.3f}",
    )

    # transfer_uvs_to_similar: fan the source's UVs to look-alike meshes; linked
    # duplicates of the source skipped, dissimilar meshes rejected by tolerance.
    reset()
    bpy.ops.mesh.primitive_plane_add()
    src = bpy.context.active_object
    src.name = "uvsrc"
    btk.move_uvs(src, du=3.0)  # distinctive source UVs (u 3..4)
    bpy.ops.mesh.primitive_plane_add(location=(3, 0, 0))
    twin = bpy.context.active_object  # similar (same plane) -> receives
    linked = bpy.data.objects.new("uvsrc_linked", src.data)  # shares datablock -> skipped
    bpy.context.collection.objects.link(linked)
    bpy.ops.mesh.primitive_ico_sphere_add(location=(9, 9, 9))  # dissimilar -> rejected
    ball = bpy.context.active_object
    targets = btk.transfer_uvs_to_similar(src, tolerance=0.9)
    check(
        "transfer_uvs_to_similar picks the twin only",
        targets == [twin],
        f"targets={[t.name for t in targets]}",
    )
    check(
        "transfer_uvs_to_similar copied the UVs",
        abs(uv_bounds(twin)[0] - 3.0) < 1e-4,
        f"twin min_u={uv_bounds(twin)[0]:.3f}",
    )
    check("transfer_uvs_to_similar left the dissimilar mesh alone", uv_bounds(ball)[0] < 1.0)
    # explicit candidate pool (Similar in Selection scope)
    targets2 = btk.transfer_uvs_to_similar(src, [ball], tolerance=0.9)
    check("explicit pool rejects dissimilar candidate", targets2 == [], str(targets2))

    # move_uvs edit mode: live bmesh update
    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    btk.move_uvs(o, du=1.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    check(
        "move_uvs in EDIT mode -> u shifted +1",
        abs(uv_bounds(o)[0] - 1.0) < 1e-4,
        f"minu={uv_bounds(o)[0]:.2f}",
    )

    # delete_extra_uv_sets: 3 -> 1 (keeps first)
    reset()
    bpy.ops.mesh.primitive_cube_add()
    o = bpy.context.active_object
    first = o.data.uv_layers[0].name
    o.data.uv_layers.new(name="extra1")
    o.data.uv_layers.new(name="extra2")
    btk.delete_extra_uv_sets(o)
    check(
        "delete_extra_uv_sets -> 1 layer left",
        len(o.data.uv_layers) == 1,
        f"n={len(o.data.uv_layers)}",
    )
    check(
        "delete_extra_uv_sets -> kept the first",
        o.data.uv_layers[0].name == first,
        f"kept={o.data.uv_layers[0].name}",
    )

    # no UV layer -> move_uvs verify() creates one, no crash
    reset()
    bpy.ops.mesh.primitive_cube_add()
    o = bpy.context.active_object
    while o.data.uv_layers:
        o.data.uv_layers.remove(o.data.uv_layers[0])
    btk.move_uvs(o, du=1.0)
    check(
        "move_uvs with no UV layer -> creates one (no crash)",
        len(o.data.uv_layers) >= 1,
    )

    # non-mesh ignored
    reset()
    bpy.ops.object.empty_add()
    e = bpy.context.active_object
    btk.move_uvs(e, du=1.0)
    btk.delete_extra_uv_sets(e)
    check("non-mesh ignored (no crash)", True)

    # transform_uvs: flip_u about the bbox center keeps the bounds, mirrors the coords
    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object
    btk.move_uvs(o, du=1.0)  # offset 1..2 so a flip about 0.5 would show
    before = uv_bounds(o)
    btk.transform_uvs(o, flip_u=True)
    check(
        "transform_uvs flip_u keeps bbox (mirror about center)",
        all(abs(a - b) < 1e-4 for a, b in zip(before, uv_bounds(o))),
        f"{before} -> {uv_bounds(o)}",
    )
    btk.transform_uvs(o, angle=90.0)
    check(
        "transform_uvs rotate 90 keeps square bbox",
        all(abs(a - b) < 1e-4 for a, b in zip(before, uv_bounds(o))),
        f"{uv_bounds(o)}",
    )

    # transform_uvs per_shell=True: each island flips about its OWN center, not one shared
    # pivot -- two disconnected squares must each keep their own center after flip_u.
    reset()

    def two_square_islands(name):
        bm = bmesh.new()
        uvl = bm.loops.layers.uv.new("UVMap")
        for n, (u0, v0, u1, v1) in enumerate(
            ((0.0, 0.0, 0.2, 0.2), (0.6, 0.6, 0.8, 0.8))
        ):
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
        obj = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        return obj

    def island_centers(obj):
        from blendertk.uv_utils._uv_utils import UvUtils

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        uvl = bm.loops.layers.uv.active
        centers = sorted(
            tuple(round(c, 4) for c in UvUtils._island_bbox_center(isl, uvl))
            for isl in UvUtils._uv_islands(bm, uvl)
        )
        bm.free()
        return centers

    o = two_square_islands("PerShellFlip")
    btk.transform_uvs(o, flip_u=True, per_shell=True)
    check(
        "transform_uvs per_shell keeps each island's own center",
        island_centers(o) == [(0.1, 0.1), (0.7, 0.7)],
        f"{island_centers(o)}",
    )

    # transform_uvs per_shell=False (default) uses ONE shared pivot -- the same flip on the
    # same layout must move at least one island's center (they swap toward the combined center).
    o2 = two_square_islands("SharedPivotFlip")
    btk.transform_uvs(o2, flip_u=True, per_shell=False)
    check(
        "transform_uvs per_shell=False moves islands off their own centers",
        island_centers(o2) != [(0.1, 0.1), (0.7, 0.7)],
        f"{island_centers(o2)}",
    )

    # mirror_uvs preserve_position=True: the exact UV point set survives a mirror on an
    # asymmetric (trapezoid) single-face island -- only the assignment permutes, not the set.
    reset()

    def uv_point_set(obj):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        uvl = bm.loops.layers.uv.active
        pts = sorted(
            (round(loop[uvl].uv.x, 4), round(loop[uvl].uv.y, 4))
            for f in bm.faces
            for loop in f.loops
        )
        bm.free()
        return pts

    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    verts = [bm.verts.new((dx, dy, 0.0)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
    face = bm.faces.new(verts)
    trapezoid = [(0.0, 0.0), (0.3, 0.0), (0.3, 0.2), (0.0, 0.1)]  # asymmetric quad
    for loop, uv in zip(face.loops, trapezoid):
        loop[uvl].uv = uv
    me = bpy.data.meshes.new("Trapezoid")
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new("Trapezoid", me)
    bpy.context.collection.objects.link(o)
    o.select_set(True)
    bpy.context.view_layer.objects.active = o

    before = uv_point_set(o)
    btk.mirror_uvs(o, axis="u", per_shell=True, preserve_position=True)
    after = uv_point_set(o)
    check(
        "mirror_uvs preserve_position keeps the exact UV point set",
        before == after,
        f"{before} vs {after}",
    )

    btk.mirror_uvs(o, axis="u", per_shell=True, preserve_position=False)
    after_geo = uv_point_set(o)
    check(
        "mirror_uvs preserve_position=False (geometric flip) changes the point set",
        after_geo != before,
        f"{after_geo}",
    )

    # mirror_uvs per_shell=False on a multi-island object: still preserves the combined
    # footprint across islands (no crash / no dropped points when grouping spans islands).
    reset()
    o = two_square_islands("MirrorTwoIslands")
    before = uv_point_set(o)
    btk.mirror_uvs(o, axis="u", per_shell=False, preserve_position=True)
    check(
        "mirror_uvs per_shell=False preserves the combined footprint across islands",
        uv_point_set(o) == before,
        f"{uv_point_set(o)} vs {before}",
    )

    # mirror_uvs axis="v" mirrors the other axis
    reset()
    o = two_square_islands("MirrorAxisV")
    before = uv_point_set(o)
    btk.mirror_uvs(o, axis="v", per_shell=True, preserve_position=True)
    check("mirror_uvs axis=v preserves the footprint too", uv_point_set(o) == before)

    # gather_to_udim: strays come back to the tile keeping their sub-tile position, inset
    # by the shared border margin; residents don't move. Mirror of mayatk's twin.
    margin = ptk.MathUtils.uv_tile_margin(4096)

    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object
    before = uv_bounds(o)
    btk.move_uvs(o, du=3.0, dv=2.0)
    moved = btk.gather_to_udim(o, udim=1001)
    check(
        "gather_to_udim pulls a stray island back, sub-tile position kept",
        moved == 1 and all(abs(a - b) < 1e-4 for a, b in zip(before, uv_bounds(o))),
        f"moved={moved} {before} -> {uv_bounds(o)}",
    )

    resident = uv_bounds(o)
    moved = btk.gather_to_udim(o, udim=1001)
    check(
        "gather_to_udim leaves a resident island alone",
        moved == 0 and all(abs(a - b) < 1e-4 for a, b in zip(resident, uv_bounds(o))),
        f"moved={moved} {uv_bounds(o)}",
    )

    btk.gather_to_udim(o, udim=1013)  # tile (2, 1)
    min_u, max_u, min_v, max_v = uv_bounds(o)
    check(
        "gather_to_udim targets an explicit UDIM",
        2.0 <= min_u and max_u <= 3.0 and 1.0 <= min_v and max_v <= 2.0,
        f"u {min_u:.3f}..{max_u:.3f} v {min_v:.3f}..{max_v:.3f}",
    )

    # An island overhanging the tile is pulled in to the PADDED border, not the border.
    # Half-scale first: an island spanning the tile exactly is oversize for the inset
    # tile and gets centered instead (see fit_into_tile), which is a different rule.
    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object
    btk.scale_uvs(o, su=0.5, sv=0.5)
    b = uv_bounds(o)
    btk.move_uvs(o, du=1.0 - b[1] + 0.05, dv=1.0 - b[3] + 0.05)  # overhang top-right
    btk.gather_to_udim(o, udim=1001)
    min_u, max_u, min_v, max_v = uv_bounds(o)
    check(
        "gather_to_udim clamps to the padded tile border",
        abs(max_u - (1.0 - margin)) < 1e-5 and abs(max_v - (1.0 - margin)) < 1e-5,
        f"max_u={max_u:.6f} max_v={max_v:.6f} margin={margin:.6f}",
    )

    # No explicit UDIM -> the tile most islands already occupy wins; the majority stays put.
    reset()
    residents = []
    for _ in range(2):
        bpy.ops.mesh.primitive_plane_add()
        r = bpy.context.active_object
        btk.move_uvs(r, du=1.0)  # tile (1, 0)
        residents.append(r)
    bpy.ops.mesh.primitive_plane_add()
    stray = bpy.context.active_object
    btk.move_uvs(stray, du=4.0, dv=3.0)
    resident_before = [uv_bounds(r) for r in residents]
    moved = btk.gather_to_udim(residents + [stray])
    min_u, max_u, min_v, max_v = uv_bounds(stray)
    check(
        "gather_to_udim votes for the majority tile",
        moved == 1
        and 1.0 <= min_u
        and max_u <= 2.0
        and max_v <= 1.0
        and all(
            abs(a - b) < 1e-4
            for before_r, r in zip(resident_before, residents)
            for a, b in zip(before_r, uv_bounds(r))
        ),
        f"moved={moved} stray u {min_u:.3f}..{max_u:.3f} v {min_v:.3f}..{max_v:.3f}",
    )

    check("gather_to_udim on an empty selection -> None", btk.gather_to_udim([]) is None)

    # A partial selection moves the WHOLE island, never a fragment of it (the Maya twin
    # had to opt into `whole_shells=True` for this; here `_target_islands` gives it).
    reset()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4)
    o = bpy.context.active_object
    btk.move_uvs(o, du=3.0, dv=2.0)
    before = uv_bounds(o)
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    o.data.polygons[0].select = True  # one face of a single-island grid
    bpy.ops.object.mode_set(mode="EDIT")
    btk.gather_to_udim(o, udim=1001)
    bpy.ops.object.mode_set(mode="OBJECT")
    after = uv_bounds(o)
    check(
        "gather_to_udim moves the whole island from a partial selection",
        abs((after[1] - after[0]) - (before[1] - before[0])) < 1e-4
        and abs((after[3] - after[2]) - (before[3] - before[2])) < 1e-4,
        f"extent {before[1]-before[0]:.3f} -> {after[1]-after[0]:.3f}",
    )

    # pin_uvs: object mode pins all; unpin clears
    reset()
    bpy.ops.mesh.primitive_plane_add()
    o = bpy.context.active_object

    def pin_count(obj):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        uvl = bm.loops.layers.uv.active
        n = sum(1 for f in bm.faces for loop in f.loops if loop[uvl].pin_uv)
        bm.free()
        return n

    btk.pin_uvs(o, pin=True, selected_only=False)
    check("pin_uvs pins all loops", pin_count(o) == 4, f"pinned={pin_count(o)}")
    btk.pin_uvs(o, pin=False, selected_only=False)
    check("pin_uvs unpins", pin_count(o) == 0, f"pinned={pin_count(o)}")

    # texel density: default plane = 2x2 world (area 4), UV 0..1 (area 1)
    # -> density = sqrt(1/4) * map = map/2; on a unit-scaled plane of size 1 it equals map.
    reset()
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    o = bpy.context.active_object
    d = btk.get_texel_density(o, 1024)
    check(
        "get_texel_density unit plane == map size", abs(d - 1024.0) < 1e-3, f"d={d:.3f}"
    )
    btk.set_texel_density(o, density=512.0, map_size=1024)
    d2 = btk.get_texel_density(o, 1024)
    check("set_texel_density rescales to target", abs(d2 - 512.0) < 1e-3, f"d={d2:.3f}")
    # bbox center preserved by the scale
    b = uv_bounds(o)
    center = ((b[0] + b[1]) / 2.0, (b[2] + b[3]) / 2.0)
    check(
        "set_texel_density scales about the UV bbox center",
        abs(center[0] - 0.5) < 1e-4 and abs(center[1] - 0.5) < 1e-4,
        f"center={center}",
    )
    check("get_texel_density empty -> 0", btk.get_texel_density([], 1024) == 0)
    # reads must not create a UV layer on a layerless mesh
    reset()
    bpy.ops.mesh.primitive_cube_add()
    o = bpy.context.active_object
    while o.data.uv_layers:
        o.data.uv_layers.remove(o.data.uv_layers[0])
    check(
        "get_texel_density layerless -> 0, creates no layer",
        btk.get_texel_density(o, 1024) == 0 and len(o.data.uv_layers) == 0,
        f"layers={len(o.data.uv_layers)}",
    )

    # ---- cmb009 Pre-Scale "Preserve 3D" mechanism (Pack UVs tool) -- added 2026-07-11 ----
    # The tentacle Blender "Pack UVs" tool's Pre-Scale=Preserve 3D runs
    # bpy.ops.uv.average_islands_scale() before pack_islands. Verify that op genuinely equalizes
    # texel density (grows an under-scaled island toward its equal-3D-area neighbor), i.e. it is
    # NOT a no-op — two disjoint unit quads (same 3D area) with islands seeded at different UV
    # scales must end at ~equal UV area.
    reset()
    m = bpy.data.meshes.new("TwoIsl")
    m.from_pydata(
        [
            (0, 0, 0),
            (1, 0, 0),
            (1, 1, 0),
            (0, 1, 0),
            (2, 0, 0),
            (3, 0, 0),
            (3, 1, 0),
            (2, 1, 0),
        ],
        [],
        [(0, 1, 2, 3), (4, 5, 6, 7)],
    )
    m.update()
    m.uv_layers.new(name="UVMap")
    o = bpy.data.objects.new("TwoIsl", m)
    bpy.context.collection.objects.link(o)
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(m)
    bm.faces.ensure_lookup_table()
    uvl = bm.loops.layers.uv.active
    for k, loop in enumerate(bm.faces[0].loops):  # island A: full unit square
        loop[uvl].uv = [(0, 0), (1, 0), (1, 1), (0, 1)][k]
    for k, loop in enumerate(bm.faces[1].loops):  # island B: half-scale (under-dense)
        loop[uvl].uv = [(0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)][k]
    bmesh.update_edit_mesh(m)

    def face_uv_area(fi):
        b = bmesh.from_edit_mesh(m)
        b.faces.ensure_lookup_table()
        u = b.loops.layers.uv.active
        us = [l[u].uv.x for l in b.faces[fi].loops]
        vs = [l[u].uv.y for l in b.faces[fi].loops]
        return (max(us) - min(us)) * (max(vs) - min(vs))

    b_before = face_uv_area(1)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.average_islands_scale()
    a_after, b_after = face_uv_area(0), face_uv_area(1)
    check(
        "average_islands_scale grows the under-scaled island (Preserve-3D is not a no-op)",
        b_after > b_before + 1e-6,
        f"islandB area {b_before:.3f}->{b_after:.3f}",
    )
    check(
        "average_islands_scale equalizes two equal-3D-area islands to ~equal UV area",
        abs(a_after - b_after) < 0.05,
        f"A={a_after:.3f} B={b_after:.3f}",
    )
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- shell_xform Align / Orient / Gather / Randomize ops (Phase 1c, 2026-07-11) --------
    # Every op was probed live in Blender 5.1 before building; these lock in the behavior so a
    # future regression is caught headlessly.
    import math as _math
    import mathutils as _mu

    def one_quad(uvs, name="Q"):
        """A single selected+active quad whose 4 loop UVs are ``uvs`` (object mode)."""
        b = bmesh.new()
        u = b.loops.layers.uv.new("UVMap")
        vv = [b.verts.new((dx, dy, 0.0)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
        fc = b.faces.new(vv)
        for loop, uv in zip(fc.loops, uvs):
            loop[u].uv = uv
        me = bpy.data.meshes.new(name)
        b.to_mesh(me)
        b.free()
        obj = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        return obj

    def all_us(obj):
        b = bmesh.new()
        b.from_mesh(obj.data)
        u = b.loops.layers.uv.active
        r = [l[u].uv.x for f in b.faces for l in f.loops]
        b.free()
        return r

    def all_vs(obj):
        b = bmesh.new()
        b.from_mesh(obj.data)
        u = b.loops.layers.uv.active
        r = [l[u].uv.y for f in b.faces for l in f.loops]
        b.free()
        return r

    def rotate_uv_map(obj, deg):
        """Rotate an object's whole UV map ``deg`` degrees about its centroid — synthesizes a
        mis-oriented shell for the Orient tests."""
        b = bmesh.new()
        b.from_mesh(obj.data)
        u = b.loops.layers.uv.active
        loops = [l for f in b.faces for l in f.loops]
        cu = sum(l[u].uv.x for l in loops) / len(loops)
        cv = sum(l[u].uv.y for l in loops) / len(loops)
        rot = _mu.Matrix.Rotation(_math.radians(deg), 2)
        for l in loops:
            d = rot @ (l[u].uv - _mu.Vector((cu, cv)))
            l[u].uv = (cu + d.x, cv + d.y)
        b.to_mesh(obj.data)
        b.free()

    # align_uvs min / max / avg (object mode = whole map). U loops of this quad = [.2,.6,.6,.2].
    reset()
    o = one_quad([(0.2, 0.5), (0.6, 0.5), (0.6, 0.9), (0.2, 0.9)])
    btk.align_uvs(o, axis="u", mode="min")
    check(
        "align_uvs u min -> all U == 0.2",
        all(abs(u - 0.2) < 1e-5 for u in all_us(o)),
        f"{set(round(u, 3) for u in all_us(o))}",
    )
    reset()
    o = one_quad([(0.2, 0.5), (0.6, 0.5), (0.6, 0.9), (0.2, 0.9)])
    btk.align_uvs(o, axis="u", mode="max")
    check(
        "align_uvs u max -> all U == 0.6", all(abs(u - 0.6) < 1e-5 for u in all_us(o))
    )
    reset()
    o = one_quad([(0.2, 0.5), (0.6, 0.5), (0.6, 0.9), (0.2, 0.9)])
    btk.align_uvs(o, axis="u", mode="avg")
    check(
        "align_uvs u avg -> all U == arithmetic mean 0.4 (Maya avgU, not bbox center)",
        all(abs(u - 0.4) < 1e-5 for u in all_us(o)),
        f"{set(round(u, 3) for u in all_us(o))}",
    )
    reset()
    o = one_quad([(0.5, 0.2), (0.5, 0.6), (0.9, 0.6), (0.9, 0.2)])
    btk.align_uvs(o, axis="v", mode="max")
    check(
        "align_uvs v max -> all V == 0.6", all(abs(v - 0.6) < 1e-5 for v in all_vs(o))
    )

    # align_uvs linear: a zig-zag row becomes collinear (max cross-product ~ 0)
    reset()
    o = one_quad([(0.0, 0.0), (0.4, 0.3), (0.8, 0.0), (1.2, -0.3)])
    btk.align_uvs(o, mode="linear")
    pts = list(zip(all_us(o), all_vs(o)))
    x0, y0 = pts[0]
    xn, yn = pts[-1]
    bx, by = xn - x0, yn - y0
    maxcross = max(abs((x - x0) * by - (y - y0) * bx) for x, y in pts)
    check(
        "align_uvs linear -> selected UVs collinear",
        maxcross < 1e-5,
        f"maxcross={maxcross:.2e}",
    )

    # align_uvs edit-mode selection scoping: ONLY the selected island collapses, the other is
    # untouched (Maya aligns the selected UVs, not the whole map).
    reset()
    o = two_square_islands(
        "AlignPartial"
    )  # island A center (0.1,0.1), island B center (0.7,0.7)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(o.data)
    bm.faces.ensure_lookup_table()
    for v in bm.faces[0].verts:
        v.select = True
    bm.select_flush(True)
    bmesh.update_edit_mesh(o.data)
    btk.align_uvs(o, axis="u", mode="min")
    bpy.ops.object.mode_set(mode="OBJECT")
    centers = island_centers(o)
    check(
        "align_uvs edit-mode aligns only the selected island (B untouched, A moved)",
        (0.7, 0.7) in centers and (0.1, 0.1) not in centers,
        f"{centers}",
    )

    # gather_uv_shells: two shells in tiles (0,0) and (1,1) both return to the 0-1 tile
    reset()
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    for n, (u0, v0, u1, v1) in enumerate(((0.1, 0.1, 0.4, 0.4), (1.2, 1.1, 1.5, 1.5))):
        x = n * 3.0
        vv = [
            bm.verts.new((x + dx, dy, 0.0))
            for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))
        ]
        fc = bm.faces.new(vv)
        for loop, (lu, lv) in zip(fc.loops, ((u0, v0), (u1, v0), (u1, v1), (u0, v1))):
            loop[uvl].uv = (lu, lv)
    me = bpy.data.meshes.new("Gather")
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new("Gather", me)
    bpy.context.collection.objects.link(o)
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    moved = btk.gather_uv_shells(o)
    cs = island_centers(o)
    check(
        "gather_uv_shells pulls both shells into the 0-1 tile",
        moved == 1 and all(0 <= c[0] < 1 and 0 <= c[1] < 1 for c in cs),
        f"moved={moved} centers={cs}",
    )

    # orient_uv_shells AUTO: a 30deg-rotated 0.4x0.2 shell is re-squared (bbox aspect -> ~2.0)
    reset()
    o = one_quad([(0.0, 0.0), (0.4, 0.0), (0.4, 0.2), (0.0, 0.2)])
    rotate_uv_map(o, 30)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.mesh.select_all(action="SELECT")
    ran = btk.orient_uv_shells(o)
    bpy.ops.object.mode_set(mode="OBJECT")
    us, vs = all_us(o), all_vs(o)
    aspect = (max(us) - min(us)) / (max(vs) - min(vs))
    check(
        "orient_uv_shells AUTO re-squares a rotated shell",
        ran == 1 and aspect > 1.7,
        f"ran={ran} aspect={aspect:.2f}",
    )

    # orient_uv_shells(to_edge=True): EDGE method orients the shell to a selected UV edge, so the
    # rotated shell snaps back axis-aligned — bbox aspect leaves the rotated ~1.2 for an extreme
    # (~2.0 or ~0.5 depending which edge is picked, so accept either).
    reset()
    o = one_quad([(0.0, 0.0), (0.4, 0.0), (0.4, 0.2), (0.0, 0.2)])
    rotate_uv_map(o, 30)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.mesh.select_mode(type="EDGE")
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(o.data)
    bm.edges.ensure_lookup_table()
    bm.edges[0].select = True
    bmesh.update_edit_mesh(o.data)
    ran_e = btk.orient_uv_shells(o, to_edge=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    us, vs = all_us(o), all_vs(o)
    aspect_e = (max(us) - min(us)) / (max(vs) - min(vs))
    check(
        "orient_uv_shells(to_edge) orients the shell to its selected edge (axis-aligned)",
        ran_e == 1 and (aspect_e > 1.7 or aspect_e < 0.6),
        f"ran={ran_e} aspect={aspect_e:.2f}",
    )

    # orient_uv_shells needs EDIT mode: object mode is a no-op (returns 0)
    reset()
    o = one_quad([(0.0, 0.0), (0.4, 0.0), (0.4, 0.2), (0.0, 0.2)])
    check("orient_uv_shells in object mode -> no-op (0)", btk.orient_uv_shells(o) == 0)

    # randomize_uv_shells: seeded => deterministic; a per-shell offset is actually applied
    def rand_center(seed):
        reset()
        oo = one_quad([(0.1, 0.1), (0.4, 0.1), (0.4, 0.4), (0.1, 0.4)])
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.scene.tool_settings.use_uv_select_sync = True
        bpy.ops.mesh.select_all(action="SELECT")
        btk.randomize_uv_shells(oo, seed=seed)
        bpy.ops.object.mode_set(mode="OBJECT")
        uu, vv = all_us(oo), all_vs(oo)
        return sum(uu) / len(uu), sum(vv) / len(vv)

    c1, c2, c3 = rand_center(7), rand_center(7), rand_center(99)
    check(
        "randomize_uv_shells seeded is deterministic (same seed twice)",
        abs(c1[0] - c2[0]) < 1e-6 and abs(c1[1] - c2[1]) < 1e-6,
        f"{c1} == {c2}",
    )
    check(
        "randomize_uv_shells applies an offset that varies by seed",
        (abs(c1[0] - 0.25) + abs(c1[1] - 0.25)) > 1e-4 and c1 != c3,
        f"seed7={tuple(round(x, 3) for x in c1)} seed99={tuple(round(x, 3) for x in c3)}",
    )

    # ---------------------------------------------------------------- transfer_uvs
    reset()
    bpy.ops.mesh.primitive_cube_add()
    src = bpy.context.active_object
    src.name = "xfer_src"
    btk.move_uvs(src, du=0.3)
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    dst = bpy.context.active_object
    dst.name = "xfer_dst"
    btk.UvUtils.transfer_uvs(src, dst, match_by_similarity=False)
    check(
        "transfer_uvs copies UVs exactly when topology matches",
        abs(uv_bounds(dst)[0] - uv_bounds(src)[0]) < 1e-5,
        f"src minu={uv_bounds(src)[0]:.3f} dst minu={uv_bounds(dst)[0]:.3f}",
    )

    # ---------------------------------------------------------- _fit_uvs_to_tile
    reset()
    bpy.ops.mesh.primitive_cube_add()
    big = bpy.context.active_object
    btk.scale_uvs(big, su=1.8, sv=1.8)
    check("fit precondition: UVs overrun the tile", uv_bounds(big)[1] > 1.0)
    btk.UvUtils._fit_uvs_to_tile(big)
    b = uv_bounds(big)
    check(
        "_fit_uvs_to_tile scales UVs back inside 0-1",
        b[0] >= -1e-4 and b[1] <= 1.0001 and b[2] >= -1e-4 and b[3] <= 1.0001,
        f"bounds={tuple(round(x, 3) for x in b)}",
    )

    # ------------------------------------------------------------------ auto_unwrap
    from blendertk.uv_utils import _auto_unwrap

    def stub_engine(obj_in, engine_key, **params):
        """Stand in for Ministry of Flat / BFF: shift every UV by a known amount."""
        obj_out = obj_in.replace(".obj", "_out.obj")
        with open(obj_in, encoding="utf-8") as f_in, open(
            obj_out, "w", encoding="utf-8"
        ) as f_out:
            for line in f_in:
                if line.startswith("vt "):
                    _, u, v = line.split()[:3]
                    line = f"vt {float(u) + 0.25:.6f} {float(v):.6f}" + chr(10)
                f_out.write(line)
        stub_engine.seen = dict(engine=engine_key, params=params)
        return obj_out

    _auto_unwrap._AutoUnwrapInternal._engine_unwrap = staticmethod(stub_engine)
    _auto_unwrap._AutoUnwrapInternal._check_engine = staticmethod(lambda key: "stub.exe")

    reset()
    bpy.ops.mesh.primitive_cube_add()
    target = bpy.context.active_object
    target.name = "auto_cube"
    before = uv_bounds(target)
    result = btk.UvUtils.auto_unwrap(target, method="hard", pack=False)
    check(
        "auto_unwrap applies the engine's UVs to the original",
        bool(result) and abs(uv_bounds(target)[0] - (before[0] + 0.25)) < 1e-4,
        f"{before[0]:.3f} -> {uv_bounds(target)[0]:.3f} failed={result.failed}",
    )
    check("auto_unwrap 'hard' selects Ministry of Flat", stub_engine.seen["engine"] == "mof")
    check(
        "auto_unwrap forwards map_size as the MoF resolution",
        stub_engine.seen["params"].get("resolution") == 4096,
        str(stub_engine.seen["params"]),
    )
    check(
        "auto_unwrap leaves no imported objects behind",
        len([o for o in bpy.data.objects]) == 1,
        f"objects={[o.name for o in bpy.data.objects]}",
    )
    check(
        "auto_unwrap preserves topology (no triangulation)",
        len(target.data.polygons) == 6,
        f"faces={len(target.data.polygons)}",
    )

    reset()
    bpy.ops.mesh.primitive_uv_sphere_add()
    organic = bpy.context.active_object
    btk.UvUtils.auto_unwrap(organic, method="organic", pack=False)
    check("auto_unwrap 'organic' selects BFF", stub_engine.seen["engine"] == "bff")

    # engine missing -> raises before the scene is touched
    def missing(_key):
        raise FileNotFoundError("Ministry of Flat not found: https://example/download")

    _auto_unwrap._AutoUnwrapInternal._check_engine = staticmethod(missing)
    reset()
    bpy.ops.mesh.primitive_cube_add()
    untouched = bpy.context.active_object
    pristine = uv_bounds(untouched)
    raised = ""
    try:
        btk.UvUtils.auto_unwrap(untouched, method="hard")
    except FileNotFoundError as err:
        raised = str(err)
    check(
        "auto_unwrap raises with a download URL when the engine is missing",
        "https://" in raised and uv_bounds(untouched) == pristine,
        raised[:60],
    )

    # per-object failure is isolated and rolled back
    def flaky(obj_in, engine_key, **params):
        verts = sum(1 for line in open(obj_in, encoding="utf-8") if line.startswith("v "))
        if verts > 8:
            raise RuntimeError("engine exploded")
        return stub_engine(obj_in, engine_key, **params)

    _auto_unwrap._AutoUnwrapInternal._engine_unwrap = staticmethod(flaky)
    _auto_unwrap._AutoUnwrapInternal._check_engine = staticmethod(lambda key: "stub.exe")
    reset()
    bpy.ops.mesh.primitive_cube_add()
    good = bpy.context.active_object
    good.name = "good_cube"
    bpy.ops.mesh.primitive_uv_sphere_add(location=(5, 0, 0))
    doomed = bpy.context.active_object
    doomed.name = "doomed_sphere"
    doomed_before = uv_bounds(doomed)
    res = btk.UvUtils.auto_unwrap([good, doomed], method="hard", pack=False)
    check(
        "auto_unwrap isolates a per-object failure",
        len(res.succeeded) == 1 and len(res.failed) == 1,
        f"ok={res.succeeded} failed={[f[0] for f in res.failed]}",
    )
    check(
        "auto_unwrap restores the failed mesh's UVs",
        all(abs(a - b) < 1e-4 for a, b in zip(uv_bounds(doomed), doomed_before)),
        f"{tuple(round(x,3) for x in doomed_before)} -> {tuple(round(x,3) for x in uv_bounds(doomed))}",
    )

    # ---- export_uv_layout: the wire format the Maya lightmap round trip rides on.
    # Blender owns the whole lightmap job and hands the finished layout back, so this
    # payload IS the guarantee that Maya's meshes sample the bake through the right UVs.
    import array
    import base64

    from blendertk.uv_utils._uv_utils import UvUtils as _UvUtils

    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    _UvUtils.create_lightmap_uvs([cube], quiet=True)
    lm = _UvUtils.find_lightmap_uv_set(cube)
    check("export: cube has a lightmap set", bool(lm), str(lm))

    payload = _UvUtils.export_uv_layout([cube])
    entry = payload.get(cube.name, {})
    check("export: keyed by object name", bool(entry), str(list(payload)))
    check(
        "export: carries a topology fingerprint",
        entry.get("poly_counts") == [len(p.vertices) for p in cube.data.polygons]
        and entry.get("num_verts") == len(cube.data.vertices),
        f"{entry.get('num_verts')} verts",
    )

    buf = array.array("f")
    buf.frombytes(base64.b64decode(entry["uvs"]))
    loops = len(cube.data.uv_layers[lm].data)
    check(
        "export: two float32 per LOOP, not per vertex",
        len(buf) == loops * 2,
        f"{len(buf)} floats vs {loops} loops",
    )

    src = [0.0] * (loops * 2)
    cube.data.uv_layers[lm].data.foreach_get("uv", src)
    check(
        "export: values survive the encode exactly",
        all(abs(a - b) < 1e-6 for a, b in zip(buf, src)),
        f"max {max((abs(a - b) for a, b in zip(buf, src)), default=0):.3g}",
    )

    check(
        "export: skips a mesh with no such set",
        _UvUtils.export_uv_layout([cube], uv_set="no_such_set") == {},
    )

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===UV-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
