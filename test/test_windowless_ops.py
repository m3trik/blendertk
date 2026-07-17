"""blendertk headless regression test — ops-backed helpers must work in the WINDOW-LESS state.

tentacle drives the Blender slots from a Qt event-pump timer where ``bpy.context.window`` is
None; ``bpy.ops`` whose poll/exec read *screen-context* members (``selected_editable_objects``,
``edit_object``) silently act on nothing or poll-fail there. The 2026-07 slot audit found the
engine's ops-backed helpers (``center_pivot``, ``separate_objects``, modifier-apply paths) called
those ops bare; the fix routes them through ``window_context_override()`` via the ``_object_mode``
guard. ``bpy.context.temp_override(window=None)`` reproduces the failing state headlessly (same
technique as test_core_utils.py). Also covers the audit's companion fixes: ``apply_subdivision``
(new), ``set_shading``/``flip_normals`` ``selected_only`` (Maya component-scope parity),
``select_by_material`` (view-layer-safe), ``Selection.loop_multi_select`` (4.x/5.x portable),
and Edit-Mode entry safety for ``@_object_mode`` helpers.

Run: blender --background --factory-startup --python blendertk/test/test_windowless_ops.py
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

    def reset():
        if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for c in list(bpy.data.collections):
            bpy.data.collections.remove(c)

    def cube(loc=(0, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        return bpy.context.view_layer.objects.active

    # --- 0. precondition: the trap is real (documents WHY the wrap exists) ------------------
    reset()
    c = cube((2.0, 0.0, 0.0))
    origin_before = tuple(c.location)
    with bpy.context.temp_override(window=None):
        try:  # bare op in the pump state: exec iterates the (empty) screen-context selection
            bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY")
        except RuntimeError:
            pass
    check("precondition: bare origin_set under window=None is a no-op/poll-fail",
          tuple(c.location) == origin_before, f"location={tuple(c.location)}")

    # --- 1. center_pivot works in the window-less state -------------------------------------
    reset()
    c = cube((0.0, 0.0, 0.0))
    c.location = (2.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    with bpy.context.temp_override(window=None):
        btk.center_pivot(c)
    check("center_pivot under window=None moves the origin onto the geometry",
          abs(c.location.x - 2.0) < 1e-5 and c.location == c.matrix_world.translation,
          f"location={tuple(c.location)}")

    # --- 2. center_pivot entered from Edit Mode restores the caller's mode ------------------
    reset()
    c = cube()
    bpy.ops.object.mode_set(mode="EDIT")
    with bpy.context.temp_override(window=None):
        btk.center_pivot(c)
    check("center_pivot from Edit Mode (window=None): no raise, mode restored",
          c.mode == "EDIT", f"mode={c.mode}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # --- 3. separate_objects from Edit Mode + window=None -----------------------------------
    reset()
    a, b = cube((0, 0, 0)), cube((5, 0, 0))
    a.select_set(True); b.select_set(True)
    bpy.context.view_layer.objects.active = a
    bpy.ops.object.join()          # one mesh, two loose parts
    joined = bpy.context.view_layer.objects.active
    bpy.ops.object.mode_set(mode="EDIT")  # the audit crash: separate clicked mid-edit
    with bpy.context.temp_override(window=None):
        parts = btk.separate_objects(joined)
    check("separate_objects from Edit Mode (window=None) yields the loose part",
          len(parts) == 1 and len([o for o in bpy.data.objects if o.type == 'MESH']) == 2,
          f"parts={len(parts)} meshes={len([o for o in bpy.data.objects if o.type == 'MESH'])}")

    # --- 4. apply_subdivision bakes the live subsurf ----------------------------------------
    reset()
    c = cube()
    btk.set_subdivision(c, viewport_levels=2)
    with bpy.context.temp_override(window=None):
        applied = btk.apply_subdivision(c)
    check("apply_subdivision applies the modifier (window=None)",
          applied == [c] and not any(m.type == "SUBSURF" for m in c.modifiers),
          f"mods={[m.type for m in c.modifiers]}")
    check("apply_subdivision bakes real geometry (faces > 6)",
          len(c.data.polygons) > 6, f"faces={len(c.data.polygons)}")
    check("apply_subdivision on an un-subdivided mesh returns []",
          btk.apply_subdivision(c) == [])

    # --- 5. set_shading selected_only (Maya component-scope parity) -------------------------
    reset()
    c = cube()
    btk.set_shading(c, smooth=False)                    # all flat baseline
    # primitive_add leaves every domain fully selected in the mesh data — clear verts/edges
    # so this exercises the FACE path alone (the edge path is asserted separately below)
    for v in c.data.vertices:
        v.select = False
    for e in c.data.edges:
        e.select = False
    c.data.polygons[0].select = True
    for i in range(1, len(c.data.polygons)):
        c.data.polygons[i].select = False
    btk.set_shading(c, smooth=True, selected_only=True)
    smooth_flags = [p.use_smooth for p in c.data.polygons]
    check("set_shading selected_only smooths ONLY the selected face",
          smooth_flags[0] is True and not any(smooth_flags[1:]), f"flags={smooth_flags}")
    # edge scope: hardening a selected edge sets its sharp flag, others untouched
    for p in c.data.polygons:
        p.select = False
    for e in c.data.edges:
        e.select = False
    c.data.edges[0].select = True
    btk.set_shading(c, smooth=False, selected_only=True)
    check("set_shading selected_only hardens ONLY the selected edge",
          c.data.edges[0].use_edge_sharp and not any(e.use_edge_sharp for e in c.data.edges[1:]),
          f"sharp={[e.use_edge_sharp for e in c.data.edges]}")
    # fallback: nothing component-selected -> whole object (Maya acts on the selection)
    for e in c.data.edges:
        e.select = False
    btk.set_shading(c, smooth=True, selected_only=True)
    check("set_shading selected_only with no component selection falls back to whole object",
          all(p.use_smooth for p in c.data.polygons))

    # --- 6. flip_normals selected_only ------------------------------------------------------
    reset()
    c = cube()
    before = [tuple(round(v, 4) for v in p.normal) for p in c.data.polygons]
    c.data.polygons[0].select = True
    for i in range(1, len(c.data.polygons)):
        c.data.polygons[i].select = False
    btk.flip_normals(c, selected_only=True)
    after = [tuple(round(v, 4) for v in p.normal) for p in c.data.polygons]
    flipped = [i for i, (b_, a_) in enumerate(zip(before, after)) if b_ != a_]
    check("flip_normals selected_only flips ONLY the selected face", flipped == [0],
          f"flipped={flipped}")

    # --- 7. select_by_material survives view-layer-excluded users ---------------------------
    reset()
    mat = bpy.data.materials.new("wl_mat")
    c = cube()
    c.data.materials.append(mat)
    excl = bpy.data.collections.new("excluded")
    bpy.context.scene.collection.children.link(excl)
    hidden_mesh = c.data.copy()
    hidden = bpy.data.objects.new("hidden_user", hidden_mesh)
    excl.objects.link(hidden)      # in the excluded collection ONLY
    bpy.context.view_layer.layer_collection.children["excluded"].exclude = True
    users = btk.select_by_material(mat)
    check("select_by_material reports every user without raising on the excluded one",
          set(users) == {c, hidden}, f"users={sorted(o.name for o in users)}")
    check("select_by_material selected the selectable user",
          c.select_get() and bpy.context.view_layer.objects.active == c)

    # --- 8. Selection.loop_multi_select (version-portable edge loop) ------------------------
    reset()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6)
    g = bpy.context.view_layer.objects.active
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="EDGE")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    g.data.edges[0].select = True
    n_before = sum(e.select for e in g.data.edges)
    bpy.ops.object.mode_set(mode="EDIT")
    btk.Selection.loop_multi_select()
    bpy.ops.object.mode_set(mode="OBJECT")
    n_after = sum(e.select for e in g.data.edges)
    check("Selection.loop_multi_select extends the edge selection to the loop",
          n_after > n_before, f"{n_before} -> {n_after}")

    # --- 9. @_object_mode helper entered from Edit Mode: no raise, mode restored ------------
    reset()
    c = cube()
    bpy.ops.object.mode_set(mode="EDIT")
    with bpy.context.temp_override(window=None):
        btk.triangulate(c)         # decorated: bare mode_set here used to poll-fail
    check("@_object_mode helper from Edit Mode (window=None): triangulated + mode restored",
          c.mode == "EDIT" and len(c.data.polygons) == 12,
          f"mode={c.mode} faces={len(c.data.polygons)}")
    bpy.ops.object.mode_set(mode="OBJECT")

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===WINDOWLESS-OPS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
