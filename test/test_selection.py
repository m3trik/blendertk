"""blendertk.edit_utils.selection headless test — category-driven select-by-type (no viewport).
Run: blender --background --factory-startup --python blendertk/test/test_selection.py
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # blendertk/
MONO = os.path.dirname(REPO)  # _scripts/
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
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def mesh_obj(name, loc=(0, 0, 0)):
        m = bpy.data.meshes.new(name + "Mesh")
        m.from_pydata(
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [],
            [(0, 1, 2, 3)],
        )
        m.update()
        o = bpy.data.objects.new(name, m)
        o.location = loc
        bpy.context.collection.objects.link(o)
        return o

    def empty_obj(name, display_type="PLAIN_AXES", loc=(0, 0, 0)):
        o = bpy.data.objects.new(name, None)
        o.empty_display_type = display_type
        o.location = loc
        bpy.context.collection.objects.link(o)
        return o

    # ---- get_selection_categories mirrors mayatk's shape (category -> leaf list) ----
    reset()
    cats = btk.Selection.get_selection_categories()
    check(
        "categories cover Animation/Dynamics/Geometry/Hierarchy/Scene/UV",
        set(cats.keys())
        >= {"Animation", "Dynamics", "Geometry", "Hierarchy", "Scene", "UV"},
        f"{sorted(cats.keys())}",
    )
    check("Geometry has Polygon Meshes leaf", "Polygon Meshes" in cats["Geometry"])
    check("Scene has Locators/Transforms leaves", {"Locators", "Transforms"} <= set(cats["Scene"]))

    # ---- Polygon Meshes / NURBS Curves / NURBS Surfaces ----
    reset()
    mA = mesh_obj("MeshA")
    curveData = bpy.data.curves.new("C", type="CURVE")
    curveData.splines.new("BEZIER")
    curveObj = bpy.data.objects.new("CurveObj", curveData)
    bpy.context.collection.objects.link(curveObj)
    surfData = bpy.data.curves.new("S", type="SURFACE")
    surfData.splines.new("NURBS")
    surfObj = bpy.data.objects.new("SurfObj", surfData)
    bpy.context.collection.objects.link(surfObj)

    objs = list(bpy.data.objects)
    res = btk.Selection.select_by_type("Polygon Meshes", objs, mode="replace")
    check("Polygon Meshes -> [MeshA]", res == [mA], f"{res}")
    res = btk.Selection.select_by_type("NURBS Curves", objs, mode="replace")
    check("NURBS Curves -> [CurveObj]", res == [curveObj], f"{res}")
    res = btk.Selection.select_by_type("NURBS Surfaces", objs, mode="replace")
    check("NURBS Surfaces -> [SurfObj]", res == [surfObj], f"{res}")
    check(
        "select_by_type applies live selection (mode=replace)",
        set(bpy.context.selected_objects) == {surfObj},
    )
    res = btk.Selection.select_by_type("Geometry", objs, mode="replace")
    check(
        "Geometry category union -> all 3 geo objects",
        set(res) == {mA, curveObj, surfObj},
        f"{res}",
    )

    # ---- Hidden / Non-Selectable / Single-Instance Geometry ----
    reset()
    hidden = mesh_obj("Hidden")
    hidden.hide_set(True)
    unselectable = mesh_obj("Unselectable")
    unselectable.hide_select = True
    normal = mesh_obj("Normal")
    objs = list(bpy.data.objects)
    res = btk.Selection.select_by_type("Hidden Geometry", objs, mode="replace")
    check("Hidden Geometry -> [Hidden]", res == [hidden], f"{res}")
    res = btk.Selection.select_by_type("Non-Selectable Geometry", objs, mode="replace")
    check("Non-Selectable Geometry -> [Unselectable]", res == [unselectable], f"{res}")

    reset()
    src = mesh_obj("Src")
    dup1 = bpy.data.objects.new("Dup1", src.data)
    bpy.context.collection.objects.link(dup1)
    dup2 = bpy.data.objects.new("Dup2", src.data)
    bpy.context.collection.objects.link(dup2)
    lone = mesh_obj("Lone")
    objs = list(bpy.data.objects)
    res = btk.Selection.select_by_type("Single-Instance Geometry", objs, mode="replace")
    shared_reps = [o for o in res if o in (src, dup1, dup2)]
    check(
        "Single-Instance Geometry -> ONE rep of the shared group + the lone mesh",
        len(res) == 2 and len(shared_reps) == 1 and lone in res,
        f"{res}",
    )

    # ---- Hierarchy: Ancestors / Children / Descendants / Groups ----
    reset()
    grp = empty_obj("Grp")
    child = mesh_obj("Child")
    child.parent = grp
    grandchild = mesh_obj("Grandchild")
    grandchild.parent = child
    bpy.context.view_layer.update()
    objs = list(bpy.data.objects)
    res = btk.Selection.select_by_type("Children", [grp], mode="replace")
    check("Children([Grp]) -> {Child}", set(res) == {child}, f"{res}")
    res = btk.Selection.select_by_type("Descendants", [grp], mode="replace")
    check("Descendants([Grp]) -> {Child, Grandchild}", set(res) == {child, grandchild}, f"{res}")
    res = btk.Selection.select_by_type("Ancestors", [grandchild], mode="replace")
    check("Ancestors([Grandchild]) -> {Child, Grp}", set(res) == {child, grp}, f"{res}")
    res = btk.Selection.select_by_type("Groups", objs, mode="replace")
    check("Groups -> [Grp] (Empty with children)", res == [grp], f"{res}")

    # ---- Scene: Locators / Image Planes / Cameras / Lights / Transforms ----
    reset()
    loc1 = empty_obj("Loc1")
    img = empty_obj("ImgPlane", display_type="IMAGE")
    cam_data = bpy.data.cameras.new("Cam")
    cam = bpy.data.objects.new("CamObj", cam_data)
    bpy.context.collection.objects.link(cam)
    light_data = bpy.data.lights.new("Light", type="POINT")
    light = bpy.data.objects.new("LightObj", light_data)
    bpy.context.collection.objects.link(light)
    mesh_extra = mesh_obj("MeshExtra")
    objs = list(bpy.data.objects)
    res = btk.Selection.select_by_type("Locators", objs, mode="replace")
    check("Locators -> [Loc1] (excludes image-plane empty)", res == [loc1], f"{res}")
    res = btk.Selection.select_by_type("Image Planes", objs, mode="replace")
    check("Image Planes -> [ImgPlane]", res == [img], f"{res}")
    res = btk.Selection.select_by_type("Cameras", objs, mode="replace")
    check("Cameras -> [CamObj]", res == [cam], f"{res}")
    res = btk.Selection.select_by_type("Lights", objs, mode="replace")
    check("Lights -> [LightObj]", res == [light], f"{res}")
    res = btk.Selection.select_by_type("Transforms", objs, mode="replace")
    check("Transforms -> every object", set(res) == set(objs), f"n={len(res)}")

    # ---- Assets (untested leaf -- verify the asset_data API assumption live too) ----
    reset()
    asset_obj = mesh_obj("AssetObj")
    asset_obj.asset_mark()
    plain_obj = mesh_obj("PlainObj")
    objs = list(bpy.data.objects)
    res = btk.Selection.select_by_type("Assets", objs, mode="replace")
    check("Assets -> [AssetObj] only (asset_mark'd)", res == [asset_obj], f"{res}")

    # ---- Keyed Locators ----
    reset()
    loc_keyed = empty_obj("LocKeyed")
    loc_keyed.keyframe_insert(data_path="location", frame=1)
    loc_plain = empty_obj("LocPlain")
    objs = list(bpy.data.objects)
    res = btk.Selection.select_by_type("Keyed Locators", objs, mode="replace")
    check("Keyed Locators -> [LocKeyed]", res == [loc_keyed], f"{res}")

    # ---- Animation: Animated Objects / Constraints ----
    reset()
    animated = mesh_obj("Animated")
    animated.keyframe_insert(data_path="location", frame=1)
    plain = mesh_obj("Plain")
    constrained = mesh_obj("Constrained")
    constrained.constraints.new(type="COPY_LOCATION")
    objs = list(bpy.data.objects)
    res = btk.Selection.select_by_type("Animated Objects", objs, mode="replace")
    check("Animated Objects -> [Animated]", res == [animated], f"{res}")
    res = btk.Selection.select_by_type("Constraints", objs, mode="replace")
    check("Constraints -> [Constrained]", res == [constrained], f"{res}")

    # ---- Dynamics: Fluids / nCloths / Lattices / Particles / Rigid Bodies / Rigid Constraints ----
    reset()
    fluid_obj = mesh_obj("FluidObj")
    fluid_obj.modifiers.new(name="Fluid", type="FLUID")
    cloth_obj = mesh_obj("ClothObj")
    cloth_obj.modifiers.new(name="Cloth", type="CLOTH")
    lattice_data = bpy.data.lattices.new("Lat")
    lattice_obj = bpy.data.objects.new("LatticeObj", lattice_data)
    bpy.context.collection.objects.link(lattice_obj)
    particle_obj = mesh_obj("ParticleObj")
    particle_obj.modifiers.new(name="PSys", type="PARTICLE_SYSTEM")
    particle_obj.particle_systems[-1].settings.type = "EMITTER"
    hair_obj = mesh_obj("HairObj")
    hair_obj.modifiers.new(name="PSys", type="PARTICLE_SYSTEM")
    hair_obj.particle_systems[-1].settings.type = "HAIR"
    rigid_active = mesh_obj("RigidActive")
    bpy.context.view_layer.objects.active = rigid_active
    bpy.ops.rigidbody.object_add()
    rigid_passive = mesh_obj("RigidPassive")
    bpy.context.view_layer.objects.active = rigid_passive
    bpy.ops.rigidbody.object_add(type="PASSIVE")
    rb_constraint_empty = empty_obj("RBConstraint")
    bpy.context.view_layer.objects.active = rb_constraint_empty
    bpy.ops.rigidbody.constraint_add()

    objs = list(bpy.data.objects)
    check("Fluids -> [FluidObj]", btk.Selection.select_by_type("Fluids", objs, mode="replace") == [fluid_obj])
    check("nCloths -> [ClothObj]", btk.Selection.select_by_type("nCloths", objs, mode="replace") == [cloth_obj])
    check("Lattices -> [LatticeObj]", btk.Selection.select_by_type("Lattices", objs, mode="replace") == [lattice_obj])
    check("Particles -> [ParticleObj]", btk.Selection.select_by_type("Particles", objs, mode="replace") == [particle_obj])
    check("Follicles -> [HairObj]", btk.Selection.select_by_type("Follicles", objs, mode="replace") == [hair_obj])
    check("Rigid Bodies -> [RigidActive]", btk.Selection.select_by_type("Rigid Bodies", objs, mode="replace") == [rigid_active])
    check("nRigids -> [RigidPassive]", btk.Selection.select_by_type("nRigids", objs, mode="replace") == [rigid_passive])
    check(
        "Rigid Constraints -> [RBConstraint]",
        btk.Selection.select_by_type("Rigid Constraints", objs, mode="replace") == [rb_constraint_empty],
    )

    # ---- Clusters (Hook modifier) / Wires (Curve modifier) -- added 2026-07-11 ----
    # Maya's cluster + wire deformers have no standalone Object in Blender; the direct
    # analogues are the Hook and Curve modifiers, so the leaves select the meshes carrying
    # one (same modifier-carrier idiom as Fluids/nCloths above). Category placement mirrors
    # Maya: Clusters under Animation, Wires under Dynamics.
    reset()
    hook_obj = mesh_obj("HookObj")
    hook_obj.modifiers.new(name="Hook", type="HOOK")
    curve_mod_obj = mesh_obj("CurveModObj")
    curve_mod_obj.modifiers.new(name="Curve", type="CURVE")
    plain_mod_obj = mesh_obj("PlainMod")  # no modifier -> neither leaf should pick it
    objs = list(bpy.data.objects)
    res_cl = btk.Selection.select_by_type("Clusters", objs, mode="replace")
    check("Clusters -> [HookObj] (Hook-modifier carrier)", res_cl == [hook_obj], f"{res_cl}")
    res_wi = btk.Selection.select_by_type("Wires", objs, mode="replace")
    check("Wires -> [CurveModObj] (Curve-modifier carrier)", res_wi == [curve_mod_obj], f"{res_wi}")
    check(
        "plain mesh (no modifier) selected by neither Clusters nor Wires",
        plain_mod_obj not in res_cl and plain_mod_obj not in res_wi,
    )
    cats2 = btk.Selection.get_selection_categories()
    check("Clusters leaf lives in the Animation category (matches Maya)",
          "Clusters" in cats2.get("Animation", []), f"{cats2.get('Animation')}")
    check("Wires leaf lives in the Dynamics category (matches Maya)",
          "Wires" in cats2.get("Dynamics", []), f"{cats2.get('Dynamics')}")

    # ---- UV: Overlapping / Non-Overlapping / Texture Borders / Unmapped ----
    reset()

    def two_face_uv_mesh(name, uv_coords):
        """A 2-quad mesh (separate verts per face, no shared edges) so its own two UV
        islands can be placed at identical (overlap) or distinct (clean) UV rects."""
        verts = [
            (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
            (2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0),
        ]
        faces = [(0, 1, 2, 3), (4, 5, 6, 7)]
        m = bpy.data.meshes.new(name + "Mesh")
        m.from_pydata(verts, [], faces)
        m.update()
        uv = m.uv_layers.new(name="UVMap")
        for i, co in enumerate(uv_coords):
            uv.data[i].uv = co
        o = bpy.data.objects.new(name, m)
        bpy.context.collection.objects.link(o)
        return o

    unit_square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    overlap_obj = two_face_uv_mesh("Overlap", unit_square * 2)  # both faces on the same rect
    clean_obj = two_face_uv_mesh(
        "Clean", unit_square + [(2, 0), (3, 0), (3, 1), (2, 1)]
    )  # distinct rects
    unmapped_obj = mesh_obj("Unmapped")
    bordered_obj = two_face_uv_mesh(
        "Bordered", unit_square + [(2, 0), (3, 0), (3, 1), (2, 1)]
    )
    bordered_obj.data.edges[0].use_seam = True

    objs = list(bpy.data.objects)
    res = btk.Selection.select_by_type("Overlapping", objs, mode="replace")
    check("Overlapping -> [Overlap]", res == [overlap_obj], f"{res}")
    res = btk.Selection.select_by_type("Non-Overlapping", objs, mode="replace")
    check(
        "Non-Overlapping -> the clean, non-overlapping meshes (excludes Overlap + the UV-less mesh)",
        set(res) >= {clean_obj, bordered_obj}
        and overlap_obj not in res
        and unmapped_obj not in res,
        f"{res}",
    )
    res = btk.Selection.select_by_type("Texture Borders", objs, mode="replace")
    check("Texture Borders -> [Bordered] (seam edge)", res == [bordered_obj], f"{res}")
    res = btk.Selection.select_by_type("Unmapped", objs, mode="replace")
    check("Unmapped -> [Unmapped] (no UV layer)", res == [unmapped_obj], f"{res}")

    # ---- select_by_type modes: add / remove ----
    reset()
    m1 = mesh_obj("M1")
    m2 = mesh_obj("M2")
    m1.select_set(True)
    btk.Selection._apply_selection_mode([m2], "add")
    check("mode=add keeps prior + adds new", set(bpy.context.selected_objects) == {m1, m2})
    btk.Selection._apply_selection_mode([m1], "remove")
    check("mode=remove deselects only that object", set(bpy.context.selected_objects) == {m2})

    # ---- Regression: objects outside the active view layer must not crash select_by_type.
    # ``select_by_type`` (and the tentacle list000 slot) commonly runs over
    # ``bpy.data.objects`` -- the WHOLE file, not scoped to the active view layer -- so an
    # object sitting in a collection excluded from the view layer is a realistic input.
    # ``Object.select_set()``/``hide_set()`` raise RuntimeError for such objects (verified
    # live, Blender 5.1.2); a bare (unguarded) call used to abort the entire selection sweep.
    def excluded_collection():
        name = "ExcludedColl"
        existing = bpy.data.collections.get(name)
        if existing:
            bpy.context.scene.collection.children.unlink(existing)
            bpy.data.collections.remove(existing)
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
        bpy.context.view_layer.layer_collection.children[name].exclude = True
        return coll

    reset()
    coll = excluded_collection()
    excl_mesh_data = bpy.data.meshes.new("ExclMesh")
    excl_mesh_data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    excl_mesh_data.update()
    excl_obj = bpy.data.objects.new("ExclObj", excl_mesh_data)
    excl_obj.hide_viewport = True
    coll.objects.link(excl_obj)
    in_view = mesh_obj("InView")
    objs = list(bpy.data.objects)

    try:
        res = btk.Selection.select_by_type("Transforms", objs, mode="replace")
        check(
            "select_by_type('Transforms') doesn't crash with an excluded-collection object",
            set(res) == {excl_obj, in_view},
            f"{res}",
        )
        check(
            "select_by_type('Transforms') selects the in-view-layer match despite the "
            "un-selectable one",
            set(bpy.context.selected_objects) == {in_view},
            f"{bpy.context.selected_objects}",
        )
    except RuntimeError as e:
        check("select_by_type('Transforms') doesn't crash with an excluded-collection object", False, repr(e))

    res = btk.Selection.select_by_type("Hidden Geometry", objs, mode="replace")
    check(
        "Hidden Geometry catches hide_viewport=True even outside the active view layer",
        excl_obj in res,
        f"{res}",
    )

    # UV Overlapping/Non-Overlapping must not crash either -- ``_select_uv_overlap`` calls
    # ``select_set``/sets ``.active`` per-candidate directly (bypassing ``_apply_selection_mode``).
    reset()
    coll = excluded_collection()
    uv_mesh = bpy.data.meshes.new("ExclUvMesh")
    uv_mesh.from_pydata(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)]
    )
    uv_mesh.update()
    uv_mesh.uv_layers.new(name="UVMap")
    excl_uv_obj = bpy.data.objects.new("ExclUvObj", uv_mesh)
    coll.objects.link(excl_uv_obj)
    objs = list(bpy.data.objects)
    try:
        res = btk.Selection.select_by_type("Overlapping", objs, mode="replace")
        check("UV Overlapping doesn't crash on an excluded-collection mesh", True, f"{res}")
        res = btk.Selection.select_by_type("Non-Overlapping", objs, mode="replace")
        check("UV Non-Overlapping doesn't crash on an excluded-collection mesh", True, f"{res}")
    except RuntimeError as e:
        check("UV overlap handlers don't crash on an excluded-collection mesh", False, repr(e))

    # ---- unknown leaf raises ValueError ----
    try:
        btk.Selection.select_by_type("NotARealType", [m1, m2])
        check("unknown type raises ValueError", False)
    except ValueError:
        check("unknown type raises ValueError", True)

    # ======================================================================================
    # Convert-To (cmb003 mirror): convert_to / select_face_path / select_vertex_perimeter /
    # select_edge_perimeter / select_face_perimeter / select_border_edges /
    # select_shell_border / select_uv_shell -- added 2026-07-06 for the shared selection.py
    # cmb003 "Convert To" combo (mayatk parity: 7->15 of Maya's 20 items). Real bmesh
    # verification, not just "doesn't crash" -- these ops are wrong in subtle, silent ways
    # if the touching/contained axis or the graph-walk direction is backwards.
    # ======================================================================================
    import bmesh

    def edit_mesh(mesh_obj):
        """Enter Edit Mode on mesh_obj (deselecting/reselecting as needed) and return its bm."""
        if bpy.context.active_object and bpy.context.active_object.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = mesh_obj
        for o in bpy.data.objects:
            o.select_set(o is mesh_obj)
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(mesh_obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        return bm

    def grid_obj(cuts=6):
        m = bpy.data.meshes.new("GridMesh")
        obj = bpy.data.objects.new("Grid", m)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bmesh.ops.create_grid(bmesh.from_edit_mesh(m), x_segments=cuts, y_segments=cuts, size=1.0)
        bmesh.update_edit_mesh(m)
        bpy.ops.object.mode_set(mode="OBJECT")
        return obj

    def contiguous_face_block(bm, n=9):
        """BFS-grow a genuinely contiguous n-face block from a central face."""
        start = bm.faces[len(bm.faces) // 2]
        block = {start}
        frontier = [start]
        while len(block) < n and frontier:
            f = frontier.pop(0)
            for e in f.edges:
                for nf in e.link_faces:
                    if nf not in block and len(block) < n:
                        block.add(nf)
                        frontier.append(nf)
        return block

    reset()
    g = grid_obj(6)

    # -- convert_to: touching (default) vs. contained, from an identical 1-vertex seed --
    bm = edit_mesh(g)
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type="VERT")
    bm = bmesh.from_edit_mesh(g.data)
    interior_v = [v for v in bm.verts if len(v.link_faces) == 4][0]
    interior_v.select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(g.data)
    btk.Selection.convert_to(g, "FACE", contained=True)
    bm = bmesh.from_edit_mesh(g.data)
    n_contained = len([f for f in bm.faces if f.select])
    check("convert_to FACE contained=True from 1 interior vert -> 0 faces", n_contained == 0, f"got {n_contained}")

    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type="VERT")
    bm = bmesh.from_edit_mesh(g.data)
    interior_v = [v for v in bm.verts if len(v.link_faces) == 4][0]
    interior_v.select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(g.data)
    btk.Selection.convert_to(g, "FACE", contained=False)
    bm = bmesh.from_edit_mesh(g.data)
    n_touching = len([f for f in bm.faces if f.select])
    check("convert_to FACE contained=False (touching) from 1 interior vert -> 4 faces", n_touching == 4, f"got {n_touching}")

    # -- select_face_path: shortest face-adjacency path between two far-apart faces --
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type="FACE")
    bm = bmesh.from_edit_mesh(g.data)
    bm.faces[0].select = True
    bm.faces[-1].select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(g.data)
    btk.Selection.select_face_path(g)
    bm = bmesh.from_edit_mesh(g.data)
    n_path = len([f for f in bm.faces if f.select])
    check("select_face_path connects 2 far faces with a real path", n_path >= 2, f"got {n_path} faces")

    # -- select_vertex_perimeter / select_edge_perimeter on a real contiguous 3x3 block --
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type="FACE")
    bm = bmesh.from_edit_mesh(g.data)
    for f in contiguous_face_block(bm, 9):
        f.select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(g.data)
    btk.Selection.select_vertex_perimeter(g)
    bm = bmesh.from_edit_mesh(g.data)
    n_perim_v = len([v for v in bm.verts if v.select])
    check("select_vertex_perimeter: 3x3 block -> 16 boundary verts (4x4 outer ring)", n_perim_v == 16, f"got {n_perim_v}")

    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type="FACE")
    bm = bmesh.from_edit_mesh(g.data)
    for f in contiguous_face_block(bm, 9):
        f.select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(g.data)
    btk.Selection.select_edge_perimeter(g)
    bm = bmesh.from_edit_mesh(g.data)
    n_perim_e = len([e for e in bm.edges if e.select])
    check("select_edge_perimeter: 3x3 block -> 16 boundary edges", n_perim_e == 16, f"got {n_perim_e}")

    # -- select_face_perimeter: the ring of faces one step outward, incl. the downward flush --
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type="FACE")
    bm = bmesh.from_edit_mesh(g.data)
    for f in contiguous_face_block(bm, 9):
        f.select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(g.data)
    n_ring = btk.Selection.select_face_perimeter(g)
    bm = bmesh.from_edit_mesh(g.data)
    n_faces_after = len([f for f in bm.faces if f.select])
    n_edges_after = len([e for e in bm.edges if e.select])
    check("select_face_perimeter finds a real surrounding ring", n_ring > 0, f"ring={n_ring}")
    check("select_face_perimeter return value matches actual face selection", n_faces_after == n_ring, f"faces={n_faces_after} ring={n_ring}")
    check("select_face_perimeter's FACE selection flushes down to edges too", n_edges_after > 0, f"edges={n_edges_after}")

    # -- select_border_edges: real naked edges on an open grid; zero on a closed cube;
    #    falls back to the whole mesh when nothing is selected --
    bpy.ops.mesh.select_all(action="SELECT")
    btk.Selection.select_border_edges(g)
    bm = bmesh.from_edit_mesh(g.data)
    n_border = len([e for e in bm.edges if e.select])
    check("select_border_edges on a 6x6 open grid, all selected -> 24-edge perimeter only", n_border == 24, f"got {n_border}")

    bpy.ops.mesh.select_all(action="DESELECT")
    btk.Selection.select_border_edges(g)
    bm = bmesh.from_edit_mesh(g.data)
    n_border_fallback = len([e for e in bm.edges if e.select])
    check("select_border_edges with nothing selected falls back to the whole mesh's border", n_border_fallback == 24, f"got {n_border_fallback}")

    bpy.ops.object.mode_set(mode="OBJECT")
    reset()
    cube_mesh = bpy.data.meshes.new("CubeMesh")
    cube_obj = bpy.data.objects.new("Cube", cube_mesh)
    bpy.context.collection.objects.link(cube_obj)
    bpy.context.view_layer.objects.active = cube_obj
    cube_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bmesh.ops.create_cube(bmesh.from_edit_mesh(cube_mesh), size=2.0)
    bmesh.update_edit_mesh(cube_mesh)
    bpy.ops.mesh.select_all(action="SELECT")
    btk.Selection.select_border_edges(cube_obj)
    bm = bmesh.from_edit_mesh(cube_mesh)
    n_cube_border = len([e for e in bm.edges if e.select])
    check("select_border_edges on a closed cube -> 0 (fully manifold, no open edges)", n_cube_border == 0, f"got {n_cube_border}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # -- select_shell_border: grows to the connected shell first, then finds its perimeter --
    reset()
    g2 = grid_obj(4)
    bm = edit_mesh(g2)
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type="FACE")
    bm = bmesh.from_edit_mesh(g2.data)
    bm.faces[0].select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(g2.data)
    btk.Selection.select_shell_border(g2)
    bm = bmesh.from_edit_mesh(g2.data)
    n_shell_border = len([e for e in bm.edges if e.select])
    check("select_shell_border grows to the shell then finds its perimeter", n_shell_border == 16, f"got {n_shell_border}")

    # -- select_uv_shell: grows to the whole UV island (a smart-projected flat grid is one) --
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.uv.smart_project()
    bpy.ops.mesh.select_mode(type="FACE")
    bm = bmesh.from_edit_mesh(g2.data)
    bm.faces[0].select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(g2.data)
    btk.Selection.select_uv_shell(g2)
    bm = bmesh.from_edit_mesh(g2.data)
    n_uv_shell = len([f for f in bm.faces if f.select])
    check("select_uv_shell grows to the whole UV island", n_uv_shell == len(bm.faces), f"got {n_uv_shell}/{len(bm.faces)}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # ======================================================================================
    # UV-domain Convert-To (cmb003): select_uv_shell_border / select_uv_perimeter /
    # select_uv_edge_loop -- ported 2026-07-13. A UV-island boundary = a mesh-open edge OR a UV
    # seam (a manifold edge whose two faces assign different UVs to a shared vert). Verified on
    # the two extremes that expose a wrong boundary test: a smart-projected cube (6 one-face
    # islands => EVERY edge is a seam) and a seamless flat grid (one continuous island => NO
    # internal seams). Plus list000 Back-/Front-Facing (signed UV area, object-level).
    def select_faces(obj, idxs):
        if bpy.context.active_object and bpy.context.active_object.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = obj
        for o in bpy.data.objects:
            o.select_set(o is obj)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_mode(type="FACE")
        bpy.ops.mesh.select_all(action="DESELECT")
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        for i in idxs:
            bm.faces[i].select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        return bm

    def n_selected_edges(obj):
        bm = bmesh.from_edit_mesh(obj.data)
        return len([e for e in bm.edges if e.select])

    # -- smart-projected cube: every face is its own UV island --
    reset()
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    cube = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project()
    bpy.ops.object.mode_set(mode="OBJECT")

    # UV Shell Border of one cube face = its 4 edges (all seams). Contrast: 3D Shell Border of
    # the same closed manifold cube = 0 open edges -> proves UV-domain includes seams.
    select_faces(cube, [0])
    n_uvsb = btk.Selection.select_uv_shell_border(cube)
    check("UV Shell Border of a cube face (own UV island) -> its 4 edges", n_uvsb == 4, f"got {n_uvsb}")
    select_faces(cube, [0])
    n_3dsb = btk.Selection.select_shell_border(cube)
    check("3D Shell Border of the closed cube -> 0 (no seam awareness)", n_3dsb == 0, f"got {n_3dsb}")

    # UV Perimeter: one face -> 4; two adjacent faces (separate UV islands) -> 7 (6 region-
    # boundary + the shared interior edge, a seam), where plain Edge Perimeter would give 6.
    select_faces(cube, [0])
    n_uvp1 = btk.Selection.select_uv_perimeter(cube)
    check("UV Perimeter of one cube face -> 4", n_uvp1 == 4, f"got {n_uvp1}")
    bm = select_faces(cube, [0])
    f0 = bm.faces[0]
    neighbor = next(nf.index for e in f0.edges for nf in e.link_faces if nf is not f0)
    select_faces(cube, [0, neighbor])
    n_uvp2 = btk.Selection.select_uv_perimeter(cube)
    check("UV Perimeter of 2 adjacent cube faces -> 7 (incl. the interior seam)", n_uvp2 == 7, f"got {n_uvp2}")
    select_faces(cube, [0, neighbor])
    btk.Selection.select_edge_perimeter(cube)  # region_to_loop, no return value
    n_ep2 = n_selected_edges(cube)
    check("plain Edge Perimeter of the same 2 faces -> 6 (interior edge excluded)", n_ep2 == 6, f"got {n_ep2}")

    def loop_count(obj, edge_idx, uv_op):
        """Select edge `edge_idx` fresh and run either the native loop op or the UV loop
        helper (`uv_op`), returning how many edges end up selected."""
        bpy.ops.mesh.select_mode(type="EDGE")
        bpy.ops.mesh.select_all(action="DESELECT")
        bm = bmesh.from_edit_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        bm.edges[edge_idx].select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        if uv_op:
            btk.Selection.select_uv_edge_loop(obj)
        else:
            bpy.ops.mesh.select_edge_loop_multi()
        return n_selected_edges(obj)

    # -- seamless flat grid: one continuous UV island -> UV Edge Loop equals the full native
    #    topological loop (nothing to truncate). --
    reset()
    g3 = grid_obj(6)
    edit_mesh(g3)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project()
    bm = bmesh.from_edit_mesh(g3.data)
    bm.edges.ensure_lookup_table()
    # an interior horizontal edge (constant y, spans x) -> its loop runs the full row
    interior_edge = next(
        e.index for e in bm.edges
        if not e.is_boundary
        and abs(e.verts[0].co.y - e.verts[1].co.y) < 1e-4
        and abs(e.verts[0].co.x - e.verts[1].co.x) > 1e-4
    )
    native_grid_loop = loop_count(g3, interior_edge, uv_op=False)
    n_uvloop_grid = loop_count(g3, interior_edge, uv_op=True)
    check(
        "UV Edge Loop on a seamless island == full native loop",
        n_uvloop_grid == native_grid_loop and native_grid_loop > 1,
        f"uv={n_uvloop_grid} native={native_grid_loop}",
    )

    # -- same grid, now with a real UV seam: mark the centre vertical column of edges as a seam
    #    and re-unwrap so the UVs actually split there; the horizontal loop must now STOP at the
    #    seam it crosses -> strictly fewer edges than the full native loop. --
    bpy.ops.mesh.select_mode(type="EDGE")
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(g3.data)
    bm.edges.ensure_lookup_table()
    vcols = sorted({round(e.verts[0].co.x, 5) for e in bm.edges
                    if abs(e.verts[0].co.x - e.verts[1].co.x) < 1e-4})
    midx = min(vcols, key=abs)  # the vertical column nearest x=0
    for e in bm.edges:
        if abs(e.verts[0].co.x - e.verts[1].co.x) < 1e-4 and abs(e.verts[0].co.x - midx) < 1e-4:
            e.seam = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(g3.data)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.unwrap()  # respects the seam -> two UV islands, real UV discontinuity at the column
    n_uvloop_seam = loop_count(g3, interior_edge, uv_op=True)
    check(
        "UV Edge Loop stops at a perpendicular UV seam (< full native loop)",
        1 <= n_uvloop_seam < native_grid_loop,
        f"uv={n_uvloop_seam} native={native_grid_loop}",
    )
    bpy.ops.object.mode_set(mode="OBJECT")

    # -- list000 Back-/Front-Facing (object-level, signed UV area) --
    def quad_with_uv(name, uv_per_loop):
        m = bpy.data.meshes.new(name + "Mesh")
        m.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
        m.update()
        uv = m.uv_layers.new(name="UVMap")
        for i, co in enumerate(uv_per_loop):
            uv.data[i].uv = co
        o = bpy.data.objects.new(name, m)
        bpy.context.collection.objects.link(o)
        return o

    reset()
    front = quad_with_uv("Front", [(0, 0), (1, 0), (1, 1), (0, 1)])   # CCW -> +area
    back = quad_with_uv("Back", [(0, 0), (0, 1), (1, 1), (1, 0)])     # CW  -> -area
    objs = [front, back]
    res = btk.Selection.select_by_type("Back-Facing", objs, mode="replace")
    check("Back-Facing (negative signed UV area) -> [Back]", res == [back], f"{res}")
    res = btk.Selection.select_by_type("Front-Facing", objs, mode="replace")
    check("Front-Facing (positive signed UV area) -> [Front]", res == [front], f"{res}")

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===SELECTION===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
