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

    # non-mesh objects are ignored (empty)
    reset()
    bpy.ops.object.empty_add(); e = bpy.context.active_object
    btk.triangulate(e); btk.decimate(e); btk.set_subdivision(e)
    check("non-mesh ignored (no crash)", True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===EDIT-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
