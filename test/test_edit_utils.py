"""blendertk.edit_utils headless test — decimate/dissolve (modifier) + triangulate/quads/subdivide
(bmesh) + subsurf levels. Run: blender --background --factory-startup --python blendertk/test/test_edit_utils.py
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
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
    def faces(o):
        return len(o.data.polygons)

    # decimate: ico sphere -> ~half faces, modifier applied (no modifier left)
    reset()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3); o = bpy.context.active_object
    n0 = faces(o)
    btk.decimate(o, percentage=50.0)
    check("decimate ~50% reduces faces", faces(o) < n0, f"{n0}->{faces(o)}")
    check("decimate applied (no modifier left)", len(o.modifiers) == 0, f"mods={len(o.modifiers)}")

    # decimate apply=False keeps the modifier live
    reset()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2); o = bpy.context.active_object
    btk.decimate(o, percentage=30.0, apply=False)
    check("decimate apply=False keeps modifier", any(m.type == "DECIMATE" for m in o.modifiers))

    # dissolve_coplanar: a flat subdivided grid collapses coplanar faces -> fewer faces
    reset()
    bpy.ops.mesh.primitive_plane_add(); o = bpy.context.active_object
    btk.subdivide_mesh(o, cuts=3)  # 16 coplanar faces
    n1 = faces(o)
    btk.dissolve_coplanar(o, angle_tolerance=1.0)
    check("dissolve_coplanar merges coplanar faces", faces(o) < n1, f"{n1}->{faces(o)}")

    # triangulate: cube 6 quads -> 12 tris
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.triangulate(o)
    check("triangulate cube -> 12 tris", faces(o) == 12, f"n={faces(o)}")
    check("triangulate -> all tris", all(len(p.vertices) == 3 for p in o.data.polygons))

    # tris_to_quads: re-merge -> back to 6 quads
    btk.tris_to_quads(o, angle=40.0)
    check("tris_to_quads -> back to 6 quads", faces(o) == 6, f"n={faces(o)}")

    # subdivide_mesh: cube 6 -> 24 faces (1 cut)
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.subdivide_mesh(o, cuts=1)
    check("subdivide_mesh cube 6 -> 24", faces(o) == 24, f"n={faces(o)}")

    # set_subdivision: ensures subsurf + sets viewport/render levels (live, not applied)
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.set_subdivision(o, viewport_levels=2, render_levels=3)
    sm = next((m for m in o.modifiers if m.type == "SUBSURF"), None)
    check("set_subdivision adds subsurf", sm is not None)
    check("set_subdivision viewport/render levels", sm and sm.levels == 2 and sm.render_levels == 3,
          f"v={sm.levels if sm else '?'} r={sm.render_levels if sm else '?'}")
    check("set_subdivision keeps base mesh (live, 6 faces)", faces(o) == 6, f"n={faces(o)}")
    # second call updates the SAME modifier (no duplicate)
    btk.set_subdivision(o, viewport_levels=1)
    subsurfs = [m for m in o.modifiers if m.type == "SUBSURF"]
    check("set_subdivision reuses modifier", len(subsurfs) == 1 and subsurfs[0].levels == 1,
          f"n_subsurf={len(subsurfs)}")

    # ensure=False on a mesh with no subsurf -> no-op
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.set_subdivision(o, viewport_levels=2, ensure=False)
    check("set_subdivision ensure=False -> no modifier added", len(o.modifiers) == 0)

    # set_shading: flat -> all faces use_smooth False; smooth -> True
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.set_shading(o, smooth=False)
    check("set_shading flat -> faces not smooth", all(not p.use_smooth for p in o.data.polygons))
    btk.set_shading(o, smooth=True)
    check("set_shading smooth -> faces smooth", all(p.use_smooth for p in o.data.polygons))

    # average_normals: softens all faces + edges (smooth, not sharp)
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    for e in o.data.edges:
        e.use_edge_sharp = True  # start all-hard so averaging must clear them
    btk.average_normals(o)
    check("average_normals -> all faces smooth", all(p.use_smooth for p in o.data.polygons))
    check("average_normals -> all edges soft (not sharp)",
          all(not e.use_edge_sharp for e in o.data.edges))

    # average_normals by_uv_shell: a cylinder smart-unwraps to a multi-face side island (interior
    # edges UV-continuous -> stay soft) plus seams at the cut + cap boundaries (-> sharp). Assert
    # SOME edges are sharp (UV-island boundaries) and SOME stay soft (interior of the side island).
    reset()
    bpy.ops.mesh.primitive_cylinder_add(); o = bpy.context.active_object
    o.select_set(True); bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project()
    bpy.ops.object.mode_set(mode="OBJECT")
    btk.average_normals(o, by_uv_shell=True)
    sharp = sum(1 for e in o.data.edges if e.use_edge_sharp)
    check("average_normals by_uv_shell -> some UV-seam edges sharp", sharp > 0, f"sharp={sharp}")
    check("average_normals by_uv_shell -> not ALL edges sharp (interior stays soft)",
          sharp < len(o.data.edges), f"sharp={sharp}/{len(o.data.edges)}")
    check("average_normals by_uv_shell -> faces still smooth", all(p.use_smooth for p in o.data.polygons))

    # set_edge_hardness: cube 90-degree edges -> angle 30 marks all sharp; angle 120 marks none
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.set_edge_hardness(o, angle=30.0)
    check("set_edge_hardness 30 -> all 12 edges sharp",
          sum(1 for e in o.data.edges if e.use_edge_sharp) == 12,
          f"sharp={sum(1 for e in o.data.edges if e.use_edge_sharp)}")
    check("set_edge_hardness -> faces smooth-shaded", all(p.use_smooth for p in o.data.polygons))
    btk.set_edge_hardness(o, angle=120.0)
    check("set_edge_hardness 120 -> no edges sharp",
          sum(1 for e in o.data.edges if e.use_edge_sharp) == 0,
          f"sharp={sum(1 for e in o.data.edges if e.use_edge_sharp)}")

    # set_edge_hardness inverted (upper=soft, lower=hard): cube 90-degree edges all >= 30 -> soft
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.set_edge_hardness(o, angle=30.0, upper_hardness=180, lower_hardness=0)
    check("set_edge_hardness upper=180 -> 0 sharp (above-threshold softened)",
          sum(1 for e in o.data.edges if e.use_edge_sharp) == 0,
          f"sharp={sum(1 for e in o.data.edges if e.use_edge_sharp)}")

    # set_edge_hardness upper=None (-1 disable): the above-threshold bucket is left as-is
    btk.set_edge_hardness(o, angle=30.0)  # mark all 12 sharp first
    btk.set_edge_hardness(o, angle=30.0, upper_hardness=None, lower_hardness=180)
    check("set_edge_hardness upper=None leaves above-threshold edges as-is (still 12 sharp)",
          sum(1 for e in o.data.edges if e.use_edge_sharp) == 12,
          f"sharp={sum(1 for e in o.data.edges if e.use_edge_sharp)}")

    # select_edges_by_angle: cube edges are all 90 degrees -> in [70,160] all 12; outside -> none
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="EDGE")
    n_in = btk.select_edges_by_angle(o, low_angle=70, high_angle=160)
    check("select_edges_by_angle range covering 90 -> all 12 edges", n_in == 12, f"n={n_in}")
    n_low = btk.select_edges_by_angle(o, low_angle=0, high_angle=45)
    check("select_edges_by_angle range below 90 -> none", n_low == 0, f"n={n_low}")
    n_high = btk.select_edges_by_angle(o, low_angle=95, high_angle=160)
    check("select_edges_by_angle range above 90 -> none", n_high == 0, f"n={n_high}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # flip_normals: a face normal is reversed
    reset()
    bpy.ops.mesh.primitive_plane_add(); o = bpy.context.active_object
    o.data.calc_loop_triangles()
    n_before = o.data.polygons[0].normal.copy()
    btk.flip_normals(o)
    n_after = o.data.polygons[0].normal
    check("flip_normals reverses normal", (n_before + n_after).length < 1e-4,
          f"{tuple(round(v,2) for v in n_before)}->{tuple(round(v,2) for v in n_after)}")

    # recalculate_normals inside: cube normals point toward center (dot with outward < 0)
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.recalculate_normals(o, inside=True)
    p = o.data.polygons[0]
    check("recalculate inside -> normal points inward", p.normal.dot(p.center) < 0,
          f"dot={p.normal.dot(p.center):.2f}")
    btk.recalculate_normals(o, inside=False)
    p = o.data.polygons[0]
    check("recalculate outside -> normal points outward", p.normal.dot(p.center) > 0,
          f"dot={p.normal.dot(p.center):.2f}")

    # clean_geometry: merge doubles + remove loose vert
    reset()
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.verts.ensure_lookup_table()
    # add a duplicate (doubled) vertex at an existing corner + a loose vertex
    corner = bm.verts[0].co.copy()
    bm.verts.new(corner)            # exact double -> should merge
    bm.verts.new((10.0, 10.0, 10.0))  # loose vertex -> should be deleted
    me = bpy.data.meshes.new("M"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("Clean", me); bpy.context.collection.objects.link(o)
    v0 = len(o.data.vertices)
    btk.clean_geometry(o, merge_distance=0.001)
    v1 = len(o.data.vertices)
    check("clean_geometry removes double + loose vert", v1 == v0 - 2, f"{v0}->{v1}")

    # clean_geometry recalculate makes normals outward
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.flip_normals(o)  # make them inward first
    btk.clean_geometry(o, merge=False, recalculate=True)
    p = o.data.polygons[0]
    check("clean_geometry recalc -> outward normals", p.normal.dot(p.center) > 0,
          f"dot={p.normal.dot(p.center):.2f}")

    # clean_geometry: degenerate dissolve must work with merge OFF (regression — the degenerate
    # threshold was coupled to the merge distance, so merge-off silently disabled it).
    reset()
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.verts.ensure_lookup_table()
    v1 = bm.verts[0]
    bm.edges.new((bm.verts.new(v1.co.copy()), v1))  # zero-length edge -> degenerate
    me = bpy.data.meshes.new("Degen"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("Degen", me); bpy.context.collection.objects.link(o)
    e0 = len(o.data.edges)
    btk.clean_geometry(o, merge=False, delete_loose=False, degenerate=True)
    check("clean_geometry degenerate works with merge off", len(o.data.edges) < e0,
          f"{e0}->{len(o.data.edges)}")

    # clean_geometry: merge=False leaves exact-double verts in place (merge really is off)
    reset()
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.verts.ensure_lookup_table()
    bm.verts.new(bm.verts[0].co.copy())  # exact double (loose)
    me = bpy.data.meshes.new("NoMerge"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("NoMerge", me); bpy.context.collection.objects.link(o)
    v0 = len(o.data.vertices)
    btk.clean_geometry(o, merge=False, delete_loose=False, degenerate=False)
    check("clean_geometry merge=False keeps doubles", len(o.data.vertices) == v0,
          f"{v0}->{len(o.data.vertices)}")

    # _object_mode guard restores the CALLER's active object + mode (regression — the helper
    # re-activates its target, and mode restore acts on the active object).
    reset()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object; a.name = "EditA"
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0)); b = bpy.context.active_object; b.name = "TargetB"
    bpy.ops.object.select_all(action="DESELECT")
    a.select_set(True); bpy.context.view_layer.objects.active = a
    bpy.ops.object.mode_set(mode="EDIT")     # caller edits A...
    btk.decimate(b, percentage=50.0)         # ...helper activates/selects B internally
    check("_object_mode restores caller's active", bpy.context.view_layer.objects.active is a,
          f"active={getattr(bpy.context.view_layer.objects.active, 'name', None)}")
    check("_object_mode restores caller's EDIT mode on A", a.mode == "EDIT", f"mode={a.mode}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # crease_edges OBJECT mode: creases ALL edges (amount 10 -> 1.0)
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.crease_edges(o, amount=10.0)
    cl = o.data.attributes.get("crease_edge")
    check("crease_edges object-mode -> all edges 1.0",
          cl is not None and all(abs(d.value - 1.0) < 1e-4 for d in cl.data), "via crease_edge attr")

    # crease_edges EDIT mode: creases only SELECTED edges (amount 5 -> 0.5)
    reset()
    import bmesh
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    o.select_set(True); bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(o.data)
    for i, e in enumerate(bm.edges):
        e.select = i < 3            # select first 3 edges
    bmesh.update_edit_mesh(o.data)
    btk.crease_edges(o, amount=5.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    cl = o.data.attributes.get("crease_edge")
    vals = sorted(round(d.value, 3) for d in cl.data) if cl else []
    n_half = sum(1 for d in (cl.data if cl else []) if abs(d.value - 0.5) < 1e-4)
    check("crease_edges edit-mode -> only 3 selected edges 0.5", n_half == 3, f"n_0.5={n_half}")

    # edit-mode guard: bmesh helper invoked from EDIT mode still applies + restores mode
    # (to_mesh while in edit mode would otherwise be clobbered on mode exit).
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    o.select_set(True); bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    btk.triangulate(o)
    restored = o.mode
    bpy.ops.object.mode_set(mode="OBJECT")
    check("triangulate from EDIT -> 12 tris applied", faces(o) == 12, f"n={faces(o)}")
    check("triangulate from EDIT restores EDIT mode", restored == "EDIT", f"mode={restored}")

    # boolean_op: cube minus overlapping cube — modifier applied, geometry changed
    reset()
    bpy.ops.mesh.primitive_cube_add(); base = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(1.0, 1.0, 1.0)); cutter = bpy.context.active_object
    out = btk.boolean_op([base, cutter], operation="DIFFERENCE")
    check("boolean_op returns base, modifier applied", out is base and len(base.modifiers) == 0)
    check("boolean_op changed geometry", len(base.data.vertices) != 8,
          f"v={len(base.data.vertices)}")

    # crease_edges angle=0: hardens every edge (Maya polySoftEdge angle=0) AND still creases
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.crease_edges(o, amount=10.0, angle=0)
    check("crease_edges angle=0 -> all edges hard",
          all(e.use_edge_sharp for e in o.data.edges),
          f"sharp={sum(1 for e in o.data.edges if e.use_edge_sharp)}")
    cl = o.data.attributes.get("crease_edge")
    check("crease_edges angle=0 still creases", cl is not None and all(abs(d.value - 1.0) < 1e-4 for d in cl.data))

    # crease_edges angle threshold vs the cube's real 90-deg dihedral: 45 -> hard, 135 -> soft
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    btk.crease_edges(o, amount=10.0, angle=45)
    check("crease_edges angle=45 (< 90deg) -> edges hard", all(e.use_edge_sharp for e in o.data.edges))
    btk.crease_edges(o, amount=10.0, angle=135)
    check("crease_edges angle=135 (> 90deg) -> edges soft", not any(e.use_edge_sharp for e in o.data.edges))

    # crease_edges angle=None: leaves edge softness untouched (only creases)
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    for e in o.data.edges:
        e.use_edge_sharp = True
    btk.crease_edges(o, amount=10.0)  # angle defaults to None
    check("crease_edges angle=None leaves softness untouched", all(e.use_edge_sharp for e in o.data.edges))

    # dissolve_coplanar delimit: SHARP preserves a marked-sharp ridge across a flat grid
    reset()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4); o = bpy.context.active_object
    # mark a middle edge loop sharp so a delimited planar dissolve must keep it
    for e in o.data.edges:
        v0, v1 = (o.data.vertices[i].co for i in e.vertices)
        if abs(v0.x) < 1e-5 and abs(v1.x) < 1e-5:
            e.use_edge_sharp = True
    n_before = len(o.data.polygons)
    btk.dissolve_coplanar(o, angle_tolerance=1.0, delimit={"SHARP"})
    check("dissolve_coplanar delimit=SHARP keeps a split (more than 1 face)",
          len(o.data.polygons) > 1, f"faces={len(o.data.polygons)} (was {n_before})")

    # dissolve_coplanar preserve_borders: a flat grid's outer boundary verts survive (more verts
    # kept) than when boundaries are dissolved too. Capture each count BEFORE the next reset() —
    # reset() deletes all objects, so the earlier object's RNA would be stale.
    reset()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=5, y_subdivisions=5); keepb = bpy.context.active_object
    btk.dissolve_coplanar(keepb, angle_tolerance=1.0, preserve_borders=True)
    keep_count = len(keepb.data.vertices)
    reset()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=5, y_subdivisions=5); dropb = bpy.context.active_object
    btk.dissolve_coplanar(dropb, angle_tolerance=1.0, preserve_borders=False)
    drop_count = len(dropb.data.vertices)
    check("dissolve_coplanar preserve_borders keeps more boundary verts",
          keep_count > drop_count, f"keep={keep_count} drop={drop_count}")

    # custom split normals = Blender's normal lock. add/clear/has back the Un/Lock Normals toggle,
    # so the COUNTS must be honest: the add/clear operators return {'CANCELLED'} (they do NOT raise)
    # for a mesh already in the target state, so a mesh-count loop reports untouched meshes as changed.
    reset()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(); b = bpy.context.active_object

    check("has_custom_split_normals: no meshes -> False", btk.has_custom_split_normals([]) is False)
    check("has_custom_split_normals: fresh meshes -> False", btk.has_custom_split_normals([a, b]) is False)

    check("add_custom_split_normals locks both", btk.add_custom_split_normals([a, b]) == 2)
    check("add_custom_split_normals -> custom_split data", a.data.has_custom_normals and b.data.has_custom_normals)
    check("has_custom_split_normals: both locked -> True", btk.has_custom_split_normals([a, b]) is True)
    check("add_custom_split_normals: already locked -> 0", btk.add_custom_split_normals([a, b]) == 0)

    check("clear_custom_split_normals unlocks both", btk.clear_custom_split_normals([a, b]) == 2)
    check("clear_custom_split_normals -> no custom_split data",
          not a.data.has_custom_normals and not b.data.has_custom_normals)
    check("clear_custom_split_normals: nothing locked -> 0", btk.clear_custom_split_normals([a, b]) == 0)

    # Mixed selection reads unlocked (Maya's all() lock-state semantics), so a toggle locks it whole.
    btk.add_custom_split_normals([a])
    check("has_custom_split_normals: mixed -> False", btk.has_custom_split_normals([a, b]) is False)
    check("add_custom_split_normals: mixed -> only the unlocked one counts",
          btk.add_custom_split_normals([a, b]) == 1)

    # The lock state is read/written from Edit Mode too (the toggle's _object_mode guard). The
    # helpers re-activate each mesh they touch, so pin the active object first: _object_mode
    # restores the mode of the object that was active on entry, not of whichever it processed last.
    bpy.context.view_layer.objects.active = a
    bpy.ops.object.mode_set(mode="EDIT")
    check("has_custom_split_normals from Edit Mode", btk.has_custom_split_normals([a, b]) is True)
    check("clear_custom_split_normals from Edit Mode", btk.clear_custom_split_normals([a, b]) == 2)
    check("Edit Mode restored for the caller", a.mode == "EDIT", a.mode)
    bpy.ops.object.mode_set(mode="OBJECT")

    # Non-mesh objects are skipped, not crashed on.
    reset()
    bpy.ops.object.empty_add(); e = bpy.context.active_object
    check("has_custom_split_normals: non-mesh -> False", btk.has_custom_split_normals([e]) is False)
    check("add_custom_split_normals: non-mesh -> 0", btk.add_custom_split_normals([e]) == 0)

    # find_problem_geometry: clean cube reports no problems for any criterion
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    c = btk.find_problem_geometry(
        o, ngons=True, concave=True, nonplanar=True, interior=True,
        nonmanifold=True, loose=True, select=False,
    )
    check("find_problem clean cube -> all zero",
          all(c[k] == 0 for k in ("ngons", "concave", "nonplanar", "interior", "nonmanifold", "loose")),
          f"{ {k: c[k] for k in c if not k.startswith('_')} }")

    # ngons: an n-gon circle fill (one >4-sided face)
    reset()
    bpy.ops.mesh.primitive_circle_add(vertices=8, fill_type="NGON"); o = bpy.context.active_object
    c = btk.find_problem_geometry(o, ngons=True, select=True)
    check("find_problem detects 1 ngon", c["ngons"] == 1, f"ngons={c['ngons']}")
    check("find_problem ngon mode=FACE", c["_mode"] == "FACE", c["_mode"])
    check("find_problem ngon flagged selected", any(p.select for p in o.data.polygons))

    # find_problem from EDIT mode: select flags must survive the round-trip (regression for the
    # missing @_object_mode guard — a live edit bmesh would otherwise clobber bm.to_mesh writes).
    reset()
    bpy.ops.mesh.primitive_circle_add(vertices=8, fill_type="NGON"); o = bpy.context.active_object
    o.select_set(True); bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT")
    btk.find_problem_geometry(o, ngons=True, select=True)
    check("find_problem from EDIT restores EDIT mode", o.mode == "EDIT", f"mode={o.mode}")
    bpy.ops.object.mode_set(mode="OBJECT")  # flush -> select flags must still be there
    check("find_problem from EDIT keeps the ngon selected", any(p.select for p in o.data.polygons))

    # nonplanar: a quad with one vertex lifted off the plane is non-planar; a flat plane is planar
    reset()
    bm = bmesh.new()
    vs = [bm.verts.new(co) for co in ((0, 0, 0), (1, 0, 0), (1, 1, 0.5), (0, 1, 0))]
    bm.faces.new(vs)
    me = bpy.data.meshes.new("NP"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("NP", me); bpy.context.collection.objects.link(o)
    c = btk.find_problem_geometry(o, nonplanar=True, select=False)
    check("find_problem detects non-planar quad", c["nonplanar"] == 1, f"nonplanar={c['nonplanar']}")
    reset()
    bpy.ops.mesh.primitive_plane_add(); o = bpy.context.active_object
    c = btk.find_problem_geometry(o, nonplanar=True, select=False)
    check("find_problem flat plane -> planar", c["nonplanar"] == 0, f"nonplanar={c['nonplanar']}")

    # nonmanifold: a cube with one face deleted -> 4 boundary (1-face) edges
    reset()
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2.0)
    bm.faces.ensure_lookup_table(); bm.faces.remove(bm.faces[0])
    me = bpy.data.meshes.new("Open"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("Open", me); bpy.context.collection.objects.link(o)
    c = btk.find_problem_geometry(o, nonmanifold=True, select=False)
    check("find_problem detects 4 nonmanifold (boundary) edges", c["nonmanifold"] == 4,
          f"nonmanifold={c['nonmanifold']}")
    check("find_problem nonmanifold mode=EDGE", c["_mode"] == "EDGE", c["_mode"])

    # concave: a dart-shaped quad (one reflex vertex) is concave; a square plane is convex
    reset()
    bm = bmesh.new()
    vs = [bm.verts.new(co) for co in ((0, 0, 0), (2, 0, 0), (0.5, 0.5, 0), (0, 2, 0))]
    bm.faces.new(vs)
    me = bpy.data.meshes.new("Dart"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("Dart", me); bpy.context.collection.objects.link(o)
    c = btk.find_problem_geometry(o, concave=True, select=False)
    check("find_problem detects concave dart quad", c["concave"] == 1, f"concave={c['concave']}")
    reset()
    bpy.ops.mesh.primitive_plane_add(); o = bpy.context.active_object
    c = btk.find_problem_geometry(o, concave=True, select=False)
    check("find_problem square plane -> not concave", c["concave"] == 0, f"concave={c['concave']}")

    # interior: a face whose every edge is shared by >2 faces (a partition face inside a box).
    # Build two cubes sharing the middle wall, then add a duplicate of that shared wall.
    reset()
    bm = bmesh.new()
    # 6 verts of one quad wall shared by faces on both sides -> make the wall edges 3-face
    # Simplest reliable construction: a "fin" — a quad whose 4 edges each also border two
    # other faces. Use bmesh create_grid won't do it; instead fan three faces around one edge.
    v = [bm.verts.new(co) for co in (
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),   # base quad 0-3
        (1, 0, 1), (1, 1, 1),                          # +Z wall pts 4-5
        (1, 0, -1), (1, 1, -1),                        # -Z wall pts 6-7
    )]
    bm.faces.new((v[0], v[1], v[2], v[3]))   # base
    bm.faces.new((v[1], v[4], v[5], v[2]))   # +Z, shares edge v1-v2
    bm.faces.new((v[1], v[6], v[7], v[2]))   # -Z, shares edge v1-v2  -> edge v1-v2 has 3 faces
    me = bpy.data.meshes.new("Fin"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("Fin", me); bpy.context.collection.objects.link(o)
    c = btk.find_problem_geometry(o, nonmanifold=True, select=False)
    check("find_problem detects nonmanifold shared edge", c["nonmanifold"] >= 1,
          f"nonmanifold={c['nonmanifold']}")

    # loose: a cube plus an unconnected vertex + a wire edge
    reset()
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2.0)
    a = bm.verts.new((5, 5, 5)); b = bm.verts.new((6, 5, 5)); bm.edges.new((a, b))  # wire edge
    bm.verts.new((9, 9, 9))  # loose vert
    me = bpy.data.meshes.new("Loose"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("Loose", me); bpy.context.collection.objects.link(o)
    c = btk.find_problem_geometry(o, loose=True, select=False)
    check("find_problem detects loose (1 wire edge + 1 vert)", c["loose"] == 2, f"loose={c['loose']}")

    # quads: a default cube is 6 quad faces (Maya "Quads")
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    c = btk.find_problem_geometry(o, quads=True, select=True)
    check("find_problem detects 6 quads on a cube", c["quads"] == 6, f"quads={c['quads']}")
    check("find_problem quads mode=FACE", c["_mode"] == "FACE", c["_mode"])
    check("find_problem quads flagged selected", all(p.select for p in o.data.polygons))

    # zero_area_faces: a collinear triangle has ~0 area; a clean plane does not (Maya "Zero Face Area")
    reset()
    bm = bmesh.new()
    vs = [bm.verts.new(co) for co in ((0, 0, 0), (1, 0, 0), (2, 0, 0))]
    bm.faces.new(vs)
    me = bpy.data.meshes.new("ZA"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("ZA", me); bpy.context.collection.objects.link(o)
    c = btk.find_problem_geometry(o, zero_area_faces=True, select=False)
    check("find_problem detects 1 zero-area face", c["zero_area_faces"] == 1, f"za={c['zero_area_faces']}")
    reset()
    bpy.ops.mesh.primitive_plane_add(); o = bpy.context.active_object
    c = btk.find_problem_geometry(o, zero_area_faces=True, select=False)
    check("find_problem flat plane -> no zero-area face", c["zero_area_faces"] == 0, f"za={c['zero_area_faces']}")

    # zero_length_edges: an edge between two coincident verts (Maya "Zero Length Edges")
    reset()
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2.0)
    a = bm.verts.new((3, 3, 3)); b = bm.verts.new((3, 3, 3)); bm.edges.new((a, b))  # coincident
    me = bpy.data.meshes.new("ZL"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("ZL", me); bpy.context.collection.objects.link(o)
    c = btk.find_problem_geometry(o, zero_length_edges=True, select=False)
    check("find_problem detects 1 zero-length edge", c["zero_length_edges"] == 1, f"zl={c['zero_length_edges']}")
    check("find_problem zero-length mode=EDGE", c["_mode"] == "EDGE", c["_mode"])

    # zero_uv_area: a cube UV-mapped with one face collapsed to a point (Maya "Zero UV Face Area")
    reset()
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2.0)
    uv = bm.loops.layers.uv.new("UVMap")
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for fi, face in enumerate(bm.faces):
        for li, loop in enumerate(face.loops):
            loop[uv].uv = (0.0, 0.0) if fi == 0 else square[li]
    me = bpy.data.meshes.new("ZUV"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("ZUV", me); bpy.context.collection.objects.link(o)
    c = btk.find_problem_geometry(o, zero_uv_area=True, select=False)
    check("find_problem detects 1 zero-UV-area face", c["zero_uv_area"] == 1, f"zuv={c['zero_uv_area']}")
    # no UV layer -> contributes nothing
    reset()
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2.0)
    me = bpy.data.meshes.new("NoUV"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("NoUV", me); bpy.context.collection.objects.link(o)
    c = btk.find_problem_geometry(o, zero_uv_area=True, select=False)
    check("find_problem zero-UV with no UV layer -> 0", c["zero_uv_area"] == 0, f"zuv={c['zero_uv_area']}")

    # get_overlapping_faces: two coincident faces on distinct vert sets -> 1 overlap
    reset()
    bm = bmesh.new()
    quad = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))
    bm.faces.new([bm.verts.new(co) for co in quad])
    bm.faces.new([bm.verts.new(co) for co in quad])  # coincident, separate verts
    me = bpy.data.meshes.new("OV"); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new("OV", me); bpy.context.collection.objects.link(o)
    check("get_overlapping_faces detects 1 coincident face", btk.get_overlapping_faces(o, select=True) == 1)
    n2 = btk.get_overlapping_faces(o, delete=True)
    check("get_overlapping_faces delete removes the dupe", n2 == 1 and len(o.data.polygons) == 1,
          f"n={n2} faces={len(o.data.polygons)}")
    reset()
    bpy.ops.mesh.primitive_plane_add(); o = bpy.context.active_object
    check("get_overlapping_faces clean plane -> 0", btk.get_overlapping_faces(o) == 0)

    # get_overlapping_duplicates: two cubes at the same spot -> 1 duplicate (a sphere is not)
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); a = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); b = bpy.context.active_object
    bpy.ops.mesh.primitive_uv_sphere_add(location=(0, 0, 0))
    dupes = btk.get_overlapping_duplicates()
    check("get_overlapping_duplicates finds 1 coincident cube (not the sphere)",
          len(dupes) == 1 and dupes[0] in (a, b), f"{[d.name for d in dupes]}")
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    check("get_overlapping_duplicates spaced cubes -> 0", btk.get_overlapping_duplicates() == [])
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); keep = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); twin = bpy.context.active_object
    check("get_overlapping_duplicates retain keeps the given, reports the twin",
          btk.get_overlapping_duplicates(retain=[keep]) == [twin])

    # get_similar_mesh: two identical cubes match by face count; a sphere does not
    reset()
    bpy.ops.mesh.primitive_cube_add(); c1 = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); c2 = bpy.context.active_object
    bpy.ops.mesh.primitive_uv_sphere_add(location=(6, 0, 0)); sp = bpy.context.active_object
    res = btk.get_similar_mesh([c1], face=True, select=True)
    check("get_similar_mesh matches the twin cube, not the sphere", c2 in res and sp not in res,
          f"{[o.name for o in res]}")
    check("get_similar_mesh selected the match only", c2.select_get() and not sp.select_get())
    check("get_similar_mesh excludes the reference by default", c1 not in res)
    check("get_similar_mesh inc_orig includes the reference",
          c1 in btk.get_similar_mesh([c1], face=True, inc_orig=True))
    check("get_similar_mesh with no criteria -> []", btk.get_similar_mesh([c1]) == [])

    # bounding_box: same-size cubes match; a 3x cube does not (tight tolerance)
    reset()
    bpy.ops.mesh.primitive_cube_add(size=2); a = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=2, location=(5, 0, 0)); b = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=6, location=(12, 0, 0)); big = bpy.context.active_object
    res = btk.get_similar_mesh([a], bounding_box=True, tolerance=0.01)
    check("get_similar_mesh bbox matches same-size, excludes 3x", b in res and big not in res,
          f"{[o.name for o in res]}")

    # shell: two 2-shell objects match each other; a 1-shell cube does not
    reset()
    bpy.ops.mesh.primitive_cube_add(); one = bpy.context.active_object  # 1 shell
    def _two_shell(loc):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        p = bpy.context.active_object
        bpy.ops.mesh.primitive_cube_add(location=(loc[0], loc[1], loc[2] + 5))
        q = bpy.context.active_object
        bpy.ops.object.select_all(action="DESELECT")
        p.select_set(True); q.select_set(True); bpy.context.view_layer.objects.active = p
        bpy.ops.object.join()
        return p
    ta = _two_shell((4, 0, 0)); tb = _two_shell((8, 0, 0))
    res = btk.get_similar_mesh([ta], shell=True, tolerance=0)
    check("get_similar_mesh shell: 2-shell matches 2-shell, not 1-shell",
          tb in res and one not in res, f"{[o.name for o in res]}")

    # regression: an object in another scene is in bpy.data.objects but NOT the active view layer —
    # it must be ignored (matching it + select_set would otherwise raise).
    reset()
    bpy.ops.mesh.primitive_cube_add(); main_cube = bpy.context.active_object
    other = bpy.data.scenes.new("Other")
    ob = bpy.data.meshes.new("OtherCube"); _b = bmesh.new()
    bmesh.ops.create_cube(_b, size=2.0); _b.to_mesh(ob); _b.free()
    oc = bpy.data.objects.new("OtherCube", ob); other.collection.objects.link(oc)
    res = btk.get_similar_mesh([main_cube], face=True, select=True)
    check("get_similar_mesh ignores other-scene objects (no crash, not matched)", oc not in res,
          f"{[o.name for o in res]}")
    bpy.data.scenes.remove(other)

    # separate_objects by_material: a 2-material cube splits into 2; rename names the parts
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    o.name = "Split"
    o.data.materials.append(bpy.data.materials.new("M1"))
    o.data.materials.append(bpy.data.materials.new("M2"))
    for i, p in enumerate(o.data.polygons):
        p.material_index = i % 2
    new = btk.separate_objects([o], by_material=True, rename=True)
    check("separate_objects by_material -> 1 new part", len(new) == 1, f"new={len(new)}")
    check("separate_objects rename -> base + _part01",
          sorted(x.name for x in [o] + new) == ["Split", "Split_part01"],
          f"{sorted(x.name for x in [o] + new)}")

    # separate_objects loose: a mesh with two disjoint shells -> 1 new object
    reset()
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    r = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.translate(bm, verts=r["verts"], vec=(5, 0, 0))
    me = bpy.data.meshes.new("Two"); bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new("Two", me); bpy.context.collection.objects.link(ob)
    new = btk.separate_objects([ob], by_material=False)
    check("separate_objects loose -> 1 new part from 2 shells", len(new) == 1, f"new={len(new)}")

    # combine_objects plain: two cubes -> one mesh (12 faces), named after the first
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); a = bpy.context.active_object; a.name = "CombA"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object; b.name = "CombB"
    res = btk.combine_objects([a, b])
    check("combine_objects plain -> single object", res is not None and res.name == "CombA",
          f"res={getattr(res, 'name', res)}")
    check("combine_objects plain -> 12 faces (6+6)", res is not None and faces(res) == 12,
          f"n={faces(res) if res else '?'}")
    check("combine_objects plain -> only one object left", len(bpy.data.objects) == 1,
          f"objs={len(bpy.data.objects)}")

    # combine_objects fewer than 2 meshes -> None (no-op)
    reset()
    bpy.ops.mesh.primitive_cube_add(); only = bpy.context.active_object
    check("combine_objects <2 meshes -> None", btk.combine_objects([only]) is None)

    # combine_objects group_by_material: 4 cubes, 2 share matX, 2 share matY -> 2 combined meshes
    reset()
    matX = bpy.data.materials.new("MatX"); matY = bpy.data.materials.new("MatY")
    cubes = []
    for i, mat in enumerate((matX, matX, matY, matY)):
        bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
        c = bpy.context.active_object
        c.data.materials.append(mat)
        cubes.append(c)
    res = btk.combine_objects(cubes, group_by_material=True)
    check("combine_objects group_by_material -> 2 meshes", isinstance(res, list) and len(res) == 2,
          f"res={[o.name for o in res] if isinstance(res, list) else res}")
    check("combine_objects group_by_material -> each 12 faces",
          isinstance(res, list) and all(faces(o) == 12 for o in res),
          f"faces={[faces(o) for o in res] if isinstance(res, list) else '?'}")

    # combine_objects cluster_by_distance: 4 same-material cubes in 2 far-apart pairs -> 2 meshes
    reset()
    matZ = bpy.data.materials.new("MatZ")
    cubes = []
    for loc in ((0, 0, 0), (2, 0, 0), (100, 0, 0), (102, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        c = bpy.context.active_object
        c.data.materials.append(matZ)
        cubes.append(c)
    res = btk.combine_objects(cubes, group_by_material=True, cluster_by_distance=True, threshold=10.0)
    check("combine_objects cluster_by_distance -> 2 clusters", isinstance(res, list) and len(res) == 2,
          f"res={[o.name for o in res] if isinstance(res, list) else res}")
    # Without clustering the same set collapses to one mesh (shared material).
    reset()
    cubes = []
    for loc in ((0, 0, 0), (2, 0, 0), (100, 0, 0), (102, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        c = bpy.context.active_object
        c.data.materials.append(matZ)
        cubes.append(c)
    res = btk.combine_objects(cubes, group_by_material=True, cluster_by_distance=False)
    check("combine_objects no-cluster -> 1 mesh for shared material", isinstance(res, list) and len(res) == 1,
          f"res={[o.name for o in res] if isinstance(res, list) else res}")

    # loft: bridge 3 stacked profile curves -> a mesh with (rings-1)*(n-1) quads
    reset()
    def _poly_curve(name, pts):
        cu = bpy.data.curves.new(name, "CURVE"); cu.dimensions = "3D"
        sp = cu.splines.new("POLY"); sp.points.add(len(pts) - 1)
        for i, p in enumerate(pts):
            sp.points[i].co = (p[0], p[1], p[2], 1.0)
        ob = bpy.data.objects.new(name, cu); bpy.context.collection.objects.link(ob)
        return ob
    profs = [
        _poly_curve(f"P{z}", [(0, 0, z), (1, 0, z), (2, 0, z), (3, 0, z)])
        for z in (0, 1, 2)
    ]
    lofted = btk.loft(profs, close=False, section_spans=1)
    check("loft -> a new mesh object", lofted is not None and lofted.type == "MESH",
          f"{getattr(lofted, 'type', lofted)}")
    check("loft -> (3 rings-1)*(4 pts-1) = 6 quads", lofted is not None and len(lofted.data.polygons) == 6,
          f"faces={len(lofted.data.polygons) if lofted else '?'}")

    # loft section_spans=2 doubles the ring gaps: (2*2 rings-1)*(4-1) -> 5 ring-gaps * 3 = ... ->
    # 3 profiles + 1 interp each gap = 5 rings -> 4*3 = 12 quads
    reset()
    profs = [
        _poly_curve(f"Q{z}", [(0, 0, z), (1, 0, z), (2, 0, z), (3, 0, z)])
        for z in (0, 1, 2)
    ]
    lofted = btk.loft(profs, section_spans=2)
    check("loft section_spans=2 -> 12 quads", lofted is not None and len(lofted.data.polygons) == 12,
          f"faces={len(lofted.data.polygons) if lofted else '?'}")

    # loft close=True bridges last->first: 3 profiles -> 4 ring-gaps -> 3*3 = 9 quads
    reset()
    profs = [
        _poly_curve(f"R{z}", [(0, 0, z), (1, 0, z), (2, 0, z), (3, 0, z)])
        for z in (0, 1, 2)
    ]
    lofted = btk.loft(profs, close=True)
    check("loft close=True -> 9 quads (periodic)", lofted is not None and len(lofted.data.polygons) == 9,
          f"faces={len(lofted.data.polygons) if lofted else '?'}")

    # loft with <2 profiles -> None
    reset()
    one = _poly_curve("Solo", [(0, 0, 0), (1, 0, 0)])
    check("loft <2 profiles -> None", btk.loft([one]) is None)

    # detach_components: select 2 faces of a cube in edit mode, then detach
    def select_faces(o, idxs):
        bpy.ops.object.select_all(action="DESELECT")
        o.select_set(True); bpy.context.view_layer.objects.active = o
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_mode(type="FACE")
        bm = bmesh.from_edit_mesh(o.data); bm.faces.ensure_lookup_table()
        for f in bm.faces:
            f.select = False
        for i in idxs:
            bm.faces[i].select = True
        bmesh.update_edit_mesh(o.data)

    # separate only: 2 faces -> 1 new object (2 faces); original loses them (4 faces)
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    select_faces(o, [0, 1])
    new = btk.detach_components(duplicate=False, separate=True, separate_each=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    check("detach_components separate -> 1 new object", len(new) == 1, f"new={len(new)}")
    check("detach_components separate -> new has 2 faces", new and faces(new[0]) == 2,
          f"n={faces(new[0]) if new else '?'}")
    check("detach_components separate -> original has 4 faces", faces(o) == 4, f"n={faces(o)}")

    # duplicate + separate: original keeps all 6 faces, new object gets the 2-face copy
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    select_faces(o, [0, 1])
    new = btk.detach_components(duplicate=True, separate=True, separate_each=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    check("detach_components duplicate keeps original intact (6 faces)", faces(o) == 6, f"n={faces(o)}")
    check("detach_components duplicate -> new copy has 2 faces", new and faces(new[0]) == 2,
          f"n={faces(new[0]) if new else '?'}")

    # separate_each: 2 selected faces -> 2 separate single-face objects
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    select_faces(o, [0, 1])
    new = btk.detach_components(duplicate=False, separate=True, separate_each=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    check("detach_components separate_each -> 2 objects", len(new) == 2, f"new={len(new)}")
    check("detach_components separate_each -> each has 1 face",
          all(faces(n) == 1 for n in new), f"faces={[faces(n) for n in new]}")

    # separate=False: split in place -> no new object, original stays one object (still 6 faces)
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    select_faces(o, [0, 1])
    before_n = len(bpy.data.objects)
    new = btk.detach_components(duplicate=False, separate=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    check("detach_components separate=False -> no new object", new == [] and len(bpy.data.objects) == before_n,
          f"new={len(new)} objs={len(bpy.data.objects)}")
    check("detach_components separate=False -> original keeps all 6 faces", faces(o) == 6, f"n={faces(o)}")

    # detach_components requires edit mode (object mode -> no-op, returns [])
    reset()
    bpy.ops.mesh.primitive_cube_add(); o = bpy.context.active_object
    check("detach_components in object mode -> [] (no crash)",
          btk.detach_components(separate=True) == [])

    # non-mesh objects are ignored (empty)
    reset()
    bpy.ops.object.empty_add(); e = bpy.context.active_object
    btk.triangulate(e); btk.decimate(e); btk.set_subdivision(e)
    btk.find_problem_geometry(e, ngons=True)
    check("non-mesh ignored (no crash)", True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===EDIT-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
