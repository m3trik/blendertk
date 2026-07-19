"""blendertk.core_utils.auto_instancer headless test — geometric duplicate discovery,
instancing (shared datablocks), assembly separate/reassemble, remainder combine.
Mirrors mayatk's synthetic auto_instancer test cases (leaf/rotated/scaled/material/
scene-match/canonicalize/production-safety).
Run: blender --background --factory-startup --python blendertk/test/test_auto_instancer.py
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)            # blendertk/
MONO = os.path.dirname(REPO)           # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

try:
    import bpy
    import numpy as np
    from collections import Counter
    from mathutils import Matrix, Vector
    import blendertk as btk
    from blendertk.core_utils.auto_instancer._auto_instancer import AutoInstancer
    from blendertk.core_utils.auto_instancer.assembly_reconstructor import (
        AssemblyReconstructor,
        ASSEMBLY_TAG_ATTR,
    )
    from blendertk.core_utils.auto_instancer.geometry_matcher import GeometryMatcher

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for me in list(bpy.data.meshes):
            if me.users == 0:
                bpy.data.meshes.remove(me)
        for m in list(bpy.data.materials):
            if m.users == 0:
                bpy.data.materials.remove(m)

    def wedge(name, loc, bake=None):
        """Asymmetric mesh (cube with one vertex pulled) — defeats symmetry
        shortcuts so rotated copies exercise the robust PCA path."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        o = bpy.context.active_object
        o.name = name
        o.data.name = name
        o.data.vertices[0].co = Vector((-2.5, -1.7, -3.1))
        if bake is not None:
            o.data.transform(bake)
        o.location = loc
        bpy.context.view_layer.update()
        return o

    def cube(name, loc, size=2.0, mat=None):
        bpy.ops.mesh.primitive_cube_add(location=loc, size=size)
        o = bpy.context.active_object
        o.name = name
        o.data.name = name
        if mat is not None:
            o.data.materials.append(mat)
        bpy.context.view_layer.update()
        return o

    def sphere(name, loc, mat=None):
        bpy.ops.mesh.primitive_uv_sphere_add(location=loc)
        o = bpy.context.active_object
        o.name = name
        o.data.name = name
        if mat is not None:
            o.data.materials.append(mat)
        bpy.context.view_layer.update()
        return o

    def cone(name, loc, mat=None):
        bpy.ops.mesh.primitive_cone_add(location=loc, vertices=8)
        o = bpy.context.active_object
        o.name = name
        o.data.name = name
        if mat is not None:
            o.data.materials.append(mat)
        bpy.context.view_layer.update()
        return o

    def world_verts(o):
        me = o.data
        n = len(me.vertices)
        buf = np.empty(n * 3, dtype=np.float32)
        me.vertices.foreach_get("co", buf)
        pts = buf.astype(np.float64).reshape(-1, 3)
        w = np.array(o.matrix_world, dtype=float)
        return pts @ w[:3, :3].T + w[:3, 3]

    def max_nn(a, b):
        d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
        return float(d.min(axis=1).max())

    # ------------------------------------------------------------- 1. leaf instancing (identical copies)
    reset()
    a = wedge("W1", (0, 0, 0))
    b = wedge("W2", (5, 0, 0))
    c = wedge("W3", (0, 7, 0))
    before = {o.name: world_verts(o) for o in (a, b, c)}
    created = btk.auto_instance([a, b, c], combine_non_instanced=False, verbose=False)
    check("leaf: run returned 3 (proto + 2)", len(created) == 3, f"n={len(created)}")
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    check("leaf: one shared datablock x3", sorted(users.values()) == [3], f"{dict(users)}")
    worst = max(max_nn(world_verts(o), before[o.name]) for o in (a, b, c))
    check("leaf: world geometry preserved", worst < 1e-4, f"worst={worst:.6f}")

    # ------------------------------------------------------------- 2. baked-rotation copy (rel transform)
    reset()
    rot = Matrix.Rotation(0.7, 4, Vector((0.3, 1.0, 0.2)).normalized())
    a = wedge("R1", (0, 0, 0))
    b = wedge("R2", (6, 2, 1), bake=rot)
    before = {o.name: world_verts(o) for o in (a, b)}
    created = btk.auto_instance([a, b], combine_non_instanced=False, verbose=False)
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    check("rotated: instanced", sorted(users.values()) == [2], f"{dict(users)}")
    worst = max(max_nn(world_verts(o), before[o.name]) for o in (a, b))
    check("rotated: rel transform places geometry correctly", worst < 0.01,
          f"worst={worst:.5f}")

    # ------------------------------------------------------------- 3. baked-scale copy (scale_tolerance)
    reset()
    a = wedge("S1", (0, 0, 0))
    b = wedge("S2", (8, 0, 0), bake=Matrix.Scale(0.6, 4))
    before = {o.name: world_verts(o) for o in (a, b)}
    # strict mode keeps them distinct...
    btk.auto_instance([a, b], combine_non_instanced=False, verbose=False)
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    check("scaled: strict mode keeps distinct", sorted(users.values()) == [1, 1],
          f"{dict(users)}")
    # ...scale mode instances with the scale on the transform
    created = btk.auto_instance(
        [a, b], scale_tolerance=1.0, combine_non_instanced=False, verbose=False
    )
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    check("scaled: scale_tolerance=1 instances", sorted(users.values()) == [2],
          f"{dict(users)}")
    worst = max(max_nn(world_verts(o), before[o.name]) for o in (a, b))
    check("scaled: world geometry preserved (scale on transform)", worst < 0.01,
          f"worst={worst:.5f}")

    # ------------------------------------------------------------- 4. material gate
    reset()
    mat_a = bpy.data.materials.new("MatA")
    mat_b = bpy.data.materials.new("MatB")
    a = cube("M1", (0, 0, 0), mat=mat_a)
    b = cube("M2", (5, 0, 0), mat=mat_b)
    btk.auto_instance([a, b], combine_non_instanced=False, verbose=False)
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    check("materials: require_same_material blocks", sorted(users.values()) == [1, 1],
          f"{dict(users)}")
    btk.auto_instance(
        [a, b], require_same_material=False, combine_non_instanced=False, verbose=False
    )
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    check("materials: require_same_material=False instances",
          sorted(users.values()) == [2], f"{dict(users)}")

    # ------------------------------------------------------------- 5. combined-mesh scene match
    # (mirror of mayatk's test_auto_instancer_scene: 3 cubes + 2 spheres +
    # 2 cones joined into one mesh; separate_combined must recover them and
    # instance each family; positions must survive.)
    reset()
    mat_a = bpy.data.materials.new("MatA")
    mat_b = bpy.data.materials.new("MatB")
    sources = [
        cube("c0", (0, 0, 0), mat=mat_a),
        cube("c1", (10, 0, 0), mat=mat_a),
        cube("c2", (20, 0, 0), mat=mat_a),
        sphere("s0", (0, 0, 10), mat=mat_b),
        sphere("s1", (10, 0, 10), mat=mat_b),
        cone("k0", (0, 0, 20), mat=mat_a),
        cone("k1", (10, 0, 20), mat=mat_a),
    ]
    expected_centers = [np.array(o.location) for o in sources]
    with bpy.context.temp_override(
        active_object=sources[0],
        selected_objects=sources,
        selected_editable_objects=sources,
    ):
        bpy.ops.object.join()
    combined = sources[0]
    combined.name = "original_combined_mesh"
    created = btk.auto_instance(
        [combined],
        separate_combined=True,
        combine_assemblies=False,
        require_same_material=False,
        tolerance=0.1,
        is_static=False,
        verbose=False,
    )
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    users = Counter(o.data.name for o in mesh_objs)
    check("scene: 7 mesh objects out", len(mesh_objs) == 7, f"n={len(mesh_objs)}")
    check("scene: family sharing [2,2,3]", sorted(users.values()) == [2, 2, 3],
          f"{dict(users)}")
    # every expected center is occupied by some produced object (bbox center)
    def bbox_center(o):
        w = world_verts(o)
        return (w.min(axis=0) + w.max(axis=0)) / 2.0
    centers = [bbox_center(o) for o in mesh_objs]
    worst_center = max(
        min(np.linalg.norm(c - e) for c in centers) for e in expected_centers
    )
    check("scene: placements preserved", worst_center < 0.5, f"worst={worst_center:.3f}")

    # ------------------------------------------------------------- 6. remainder combine (defaults)
    reset()
    mat_a = bpy.data.materials.new("MatA")
    mat_b = bpy.data.materials.new("MatB")
    s0 = sphere("sp0", (0, 0, 0), mat=mat_a)      # ~960 fan tris: above micro
    s1 = sphere("sp1", (6, 0, 0), mat=mat_a)
    u0 = cube("u0", (0, 10, 0), size=1.0, mat=mat_a)   # unique micro leftovers
    u1 = cube("u1", (5, 10, 0), size=1.5, mat=mat_a)
    u2 = cube("u2", (10, 10, 0), size=2.5, mat=mat_b)
    created = btk.auto_instance([s0, s1, u0, u1, u2], verbose=False)
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    sphere_shared = 2 in users.values()
    check("remainder: spheres instanced", sphere_shared, f"{dict(users)}")
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    # 2 spheres + one combined mesh per material (MatA leftovers joined, MatB alone)
    check("remainder: leftovers merged per material", len(mesh_objs) == 4,
          f"n={len(mesh_objs)} ({[o.name for o in mesh_objs]})")

    # ------------------------------------------------------------- 6b. combine_assemblies=True (slot default path)
    # Two copies of a 2-part unit joined into one mesh; separate_combined
    # with assembly combining ON must rebuild each copy as ONE combined
    # mesh and instance the two copies against each other.
    reset()
    parts = []
    for x in (0.0, 12.0):
        body = cube(f"body{int(x)}", (x, 0, 0), size=2.0)
        knob = cone(f"knob{int(x)}", (x, 0, 1.2))
        parts += [body, knob]
    with bpy.context.temp_override(
        active_object=parts[0],
        selected_objects=parts,
        selected_editable_objects=parts,
    ):
        bpy.ops.object.join()
    combined = parts[0]
    combined.name = "fused_units"
    created = btk.auto_instance(
        [combined],
        separate_combined=True,
        combine_assemblies=True,
        combine_non_instanced=False,
        require_same_material=False,
        verbose=False,
    )
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    users = Counter(o.data.name for o in mesh_objs)
    check("assemblies: two combined copies share one datablock",
          sorted(users.values()) == [2], f"{dict(users)}")
    check("assemblies: no leftover assembly empties",
          not [o for o in bpy.data.objects if o.get(ASSEMBLY_TAG_ATTR)],
          f"{[o.name for o in bpy.data.objects]}")

    # ------------------------------------------------------------- 6c. hierarchy mode (chk006 path)
    reset()
    def unit(tag, loc):
        bpy.ops.object.empty_add(location=loc)
        root = bpy.context.active_object
        root.name = f"grp_{tag}"
        a = cube(f"ca_{tag}", (loc[0], loc[1], loc[2] + 1.5))
        b = cone(f"cb_{tag}", (loc[0] + 1.5, loc[1], loc[2]))
        for child in (a, b):
            w = child.matrix_world.copy()
            child.parent = root
            child.matrix_world = w
        bpy.context.view_layer.update()
        return root, a, b

    r1, a1, b1 = unit("one", (0, 0, 0))
    r2, a2, b2 = unit("two", (15, 0, 0))
    created = btk.auto_instance(
        [r1, a1, b1, r2, a2, b2],
        check_hierarchy=True,
        combine_non_instanced=False,
        verbose=False,
    )
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    users = Counter(o.data.name for o in mesh_objs)
    check("hierarchy: child meshes instanced across copies",
          sorted(users.values()) == [2, 2], f"{dict(users)}")
    roots = [o.name for o in bpy.data.objects if o.type == "EMPTY"]
    check("hierarchy: both roots survive (one replaced by linked copy)",
          sorted(roots) == ["grp_one", "grp_two"], f"{roots}")

    # ------------------------------------------------------------- 6d. leaf rel-correction preserves member children
    reset()
    rot = Matrix.Rotation(0.7, 4, Vector((0.3, 1.0, 0.2)).normalized())
    a = wedge("C1", (0, 0, 0))
    b = wedge("C2", (6, 2, 1), bake=rot)  # baked copy -> rel != None
    bpy.ops.object.empty_add(location=(6, 2, 4))
    deco = bpy.context.active_object
    deco.name = "decoration"
    w = deco.matrix_world.copy()
    deco.parent = b
    deco.matrix_world = w
    bpy.context.view_layer.update()
    deco_world_before = np.array(deco.matrix_world)
    btk.auto_instance([a, b], combine_non_instanced=False, verbose=False)
    bpy.context.view_layer.update()
    check("children: member's child keeps world pose through rel fold",
          np.allclose(np.array(deco.matrix_world), deco_world_before, atol=1e-5),
          f"delta={np.abs(np.array(deco.matrix_world) - deco_world_before).max():.6f}")
    check("children: child still parented to the converted member",
          deco.parent is not None and deco.parent.name == "C2")

    # ------------------------------------------------------------- 6e. leaf mode replicates the prototype's children
    reset()
    a = wedge("P1", (0, 0, 0))
    tag = cube("proto_tag", (0, 0, 3), size=0.5)
    wp = tag.matrix_world.copy()
    tag.parent = a
    tag.matrix_world = wp
    b = wedge("P2", (8, 0, 0))
    bpy.context.view_layer.update()
    btk.auto_instance([a, b], combine_non_instanced=False, verbose=False)
    bpy.context.view_layer.update()  # matrix_world is stale after reparent
    b_children = list(b.children)
    check("proto children: member gains a linked-duplicate child",
          len(b_children) == 1 and b_children[0].data.name == tag.data.name,
          f"{[c.name for c in b_children]}")
    if b_children:
        rel_off = (np.array(b_children[0].matrix_world)[:3, 3]
                   - np.array(b.matrix_world)[:3, 3])
        check("proto children: replica sits at the proto-relative offset",
              np.allclose(rel_off, (0, 0, 3), atol=1e-4), f"{rel_off}")
    # A repeat run must be a no-op — the member is already an instance of
    # the prototype, so its replicated child must NOT accumulate.
    btk.auto_instance([a, b], combine_non_instanced=False, verbose=False)
    check("proto children: repeat run does not duplicate the replica",
          len(list(b.children)) == 1, f"n={len(list(b.children))}")

    # ------------------------------------------------------------- 6f. remainder combine spares converted members' children
    reset()
    mat_a = bpy.data.materials.new("MatA")
    s0 = sphere("rs0", (0, 0, 0), mat=mat_a)
    s1 = sphere("rs1", (6, 0, 0), mat=mat_a)
    child = cube("keep_me", (6, 0, 2), size=0.8, mat=mat_a)
    wc = child.matrix_world.copy()
    child.parent = s1
    child.matrix_world = wc
    loose = cube("loose", (0, 6, 0), size=1.2, mat=mat_a)
    loose2 = cube("loose2", (3, 6, 0), size=1.7, mat=mat_a)
    bpy.context.view_layer.update()
    btk.auto_instance([s0, s1, child, loose, loose2], verbose=False)
    still = bpy.data.objects.get("keep_me")
    check("remainder: converted member's child survives the combine",
          still is not None and still.parent is not None
          and still.parent.name == "rs1",
          f"alive={still is not None}")

    # ------------------------------------------------------------- 6g. combine preserves children of joined-away sources
    reset()
    mat_a = bpy.data.materials.new("MatA")
    l0 = cube("j0", (0, 0, 0), size=1.0, mat=mat_a)
    l1 = cube("j1", (4, 0, 0), size=1.6, mat=mat_a)
    bpy.ops.object.empty_add(location=(4, 0, 2))  # non-mesh: never a combine candidate
    orphanable = bpy.context.active_object
    orphanable.name = "orphanable"
    wo = orphanable.matrix_world.copy()
    orphanable.parent = l1
    orphanable.matrix_world = wo
    bpy.context.view_layer.update()
    world_before = np.array(orphanable.matrix_world)
    # No instancing possible (unique sizes) -> both cubes go to the combine;
    # l1 is joined away and its Empty child must keep its world pose.
    btk.auto_instance([l0, l1], verbose=False)
    bpy.context.view_layer.update()
    still = bpy.data.objects.get("orphanable")
    check("join: child of joined-away source survives with world pose",
          still is not None
          and np.allclose(np.array(still.matrix_world), world_before, atol=1e-5),
          f"alive={still is not None}")

    # ------------------------------------------------------------- 6h. degenerate (zero-scale) object doesn't abort the run
    reset()
    a = wedge("Z1", (0, 0, 0))
    b = wedge("Z2", (5, 0, 0))
    flat = cube("flat", (0, 8, 0))
    flat.scale = (1.0, 1.0, 0.0)  # singular matrix_world
    bpy.context.view_layer.update()
    created = btk.auto_instance(
        [a, b, flat],
        separate_combined=True,
        combine_non_instanced=False,
        verbose=False,
    )
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    check("degenerate: zero-scale object skipped, run completes",
          users.get("Z1", 0) == 2 or users.get("Z2", 0) == 2, f"{dict(users)}")

    # ------------------------------------------------------------- 6i. zero-vertex meshes in hierarchy mode (no crash)
    reset()
    def empty_mesh_unit(tag, loc):
        bpy.ops.object.empty_add(location=loc)
        root = bpy.context.active_object
        root.name = f"em_{tag}"
        placeholder = bpy.data.objects.new(f"ph_{tag}", bpy.data.meshes.new(f"ph_{tag}"))
        bpy.context.scene.collection.objects.link(placeholder)
        real = cube(f"real_{tag}", (loc[0], loc[1], loc[2] + 1.5))
        for child in (placeholder, real):
            w = child.matrix_world.copy()
            child.parent = root
            child.matrix_world = w
        bpy.context.view_layer.update()
        return root

    empty_mesh_unit("a", (0, 0, 0))
    empty_mesh_unit("b", (10, 0, 0))
    objs = list(bpy.data.objects)
    created = btk.auto_instance(
        objs, check_hierarchy=True, combine_non_instanced=False, verbose=False
    )
    users = Counter(
        o.data.name for o in bpy.data.objects
        if o.type == "MESH" and len(o.data.vertices)
    )
    check("empty-mesh: hierarchy run completes and instances real children",
          sorted(users.values()) == [2], f"{dict(users)}")

    # ------------------------------------------------------------- 6j. second leaf pass (separate + combine_assemblies=False)
    reset()
    parts = []
    for x in (0.0, 12.0):  # two copies of type A: body + knob
        parts.append(cube(f"Abody{int(x)}", (x, 0, 0), size=2.0))
        parts.append(cone(f"Aknob{int(x)}", (x, 0, 1.2)))
    for x in (24.0, 36.0):  # two copies of type B: body + knob + extra
        parts.append(cube(f"Bbody{int(x)}", (x, 0, 0), size=2.0))
        parts.append(cone(f"Bknob{int(x)}", (x, 0, 1.2)))
        parts.append(cube(f"Bextra{int(x)}", (x, 0, -1.4), size=0.8))
    with bpy.context.temp_override(
        active_object=parts[0],
        selected_objects=parts,
        selected_editable_objects=parts,
    ):
        bpy.ops.object.join()
    fused = parts[0]
    fused.name = "two_types"
    created = btk.auto_instance(
        [fused],
        separate_combined=True,
        combine_assemblies=False,
        combine_non_instanced=False,
        require_same_material=False,
        verbose=False,
    )
    empties = [o for o in bpy.data.objects if o.get(ASSEMBLY_TAG_ATTR)]
    check("second pass: 4 tagged assembly groups", len(empties) == 4,
          f"n={len(empties)}")
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    # The shared body geometry must be shared ACROSS the A and B types
    # (the second leaf pass pairs B's parts against A's datablocks).
    check("second pass: cross-type body sharing (some datablock >= 4 users)",
          any(v >= 4 for v in users.values()), f"{dict(users)}")

    # ------------------------------------------------------------- 7. canonicalize preserves custom normals
    reset()
    a = wedge("N1", (0, 0, 0), bake=Matrix.Rotation(0.9, 4, "Z"))
    me = a.data
    skew = []
    for _ in me.loops:
        skew.append(Vector((0.3, 0.4, 0.87)).normalized())
    me.normals_split_custom_set(skew)
    check("normals: custom normals set", me.has_custom_normals)

    def world_corner_normals(o):
        me = o.data
        n = len(me.loops)
        buf = np.empty(n * 3, dtype=np.float32)
        me.corner_normals.foreach_get("vector", buf)
        local = buf.astype(np.float64).reshape(-1, 3)
        nm = np.array(o.matrix_world, dtype=float)[:3, :3]
        nm = np.linalg.inv(nm).T
        w = local @ nm.T
        lens = np.linalg.norm(w, axis=1)
        return w / lens[:, None]

    before_n = world_corner_normals(a)
    matcher = GeometryMatcher(verbose=False)
    recon = AssemblyReconstructor(matcher=matcher, verbose=False)
    recon.canonicalize_transform(a)
    after_n = world_corner_normals(a)
    dots = np.sum(before_n * after_n, axis=1)
    check("normals: world shading preserved through canonicalize",
          float(dots.min()) > 0.999, f"min_dot={float(dots.min()):.5f}")
    rot_changed = a.matrix_world.to_quaternion().angle > 1e-3
    check("normals: canonicalize actually rotated the transform", rot_changed)

    # ------------------------------------------------------------- 8. production safety
    reset()
    a = cube("P1", (0, 0, 0))
    b = cube("P2", (5, 0, 0))
    bpy.ops.object.camera_add(location=(0, -10, 0))
    cam = bpy.context.active_object
    bpy.ops.object.empty_add(location=(3, 3, 3))
    empty = bpy.context.active_object
    bpy.ops.object.light_add(location=(0, 0, 10))
    light = bpy.context.active_object
    bpy.ops.object.select_all(action="DESELECT")  # empty selection -> scene-wide fallback
    inst = AutoInstancer(combine_non_instanced=False, verbose=False)
    flag_before = inst.check_hierarchy
    created = inst.run(None)  # scene-wide fallback
    check("safety: cubes instanced scene-wide",
          a.data.name == b.data.name, f"{a.data.name} vs {b.data.name}")
    survivors = {o.name for o in bpy.data.objects}
    check("safety: camera/empty/light untouched",
          {cam.name, empty.name, light.name} <= survivors, f"{sorted(survivors)}")
    check("safety: run() does not mutate config",
          inst.check_hierarchy == flag_before)
    # second run is a stable no-op (already-instanced passthrough)
    created2 = inst.run(None)
    users = Counter(o.data.name for o in bpy.data.objects if o.type == "MESH")
    check("safety: second run stable", sorted(users.values()) == [2], f"{dict(users)}")

    # ------------------------------------------------------------- 9. run summary (why matches did / didn't instance)
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(location=(0, 0, 0)); d0 = bpy.context.active_object; d0.name = "Dense0"
    bpy.ops.mesh.primitive_uv_sphere_add(location=(5, 0, 0)); d1 = bpy.context.active_object; d1.name = "Dense1"
    _, summ = btk.auto_instance([d0, d1], combine_non_instanced=False, verbose=False, return_summary=True)
    check("summary: dense duplicates instanced",
          summ["matched_groups"] == 1 and summ["instanced_groups"] == 1
          and summ["instances_created"] == 1 and summ["simple_groups"] == 0, f"{summ}")

    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); t0 = bpy.context.active_object; t0.name = "Tiny0"
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); t1 = bpy.context.active_object; t1.name = "Tiny1"
    created, summ = btk.auto_instance([t0, t1], combine_non_instanced=True, verbose=False, return_summary=True)
    check("summary: micro duplicates flagged too-simple (combined, not instanced)",
          summ["matched_groups"] == 1 and summ["simple_groups"] == 1
          and summ["instanced_groups"] == 0
          and any(x["reason"] == "too_simple" for x in summ["details"]), f"{summ}")
    check("summary: too-simple text surfaced",
          "too simple" in AutoInstancer.format_summary(summ, len(created)))

    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); u0 = bpy.context.active_object; u0.name = "Uq0"
    bpy.ops.mesh.primitive_uv_sphere_add(location=(3, 0, 0)); u1 = bpy.context.active_object; u1.name = "Uq1"
    _, summ = btk.auto_instance([u0, u1], combine_non_instanced=False, verbose=False, return_summary=True)
    check("summary: no matches reported as such",
          summ["matched_groups"] == 0
          and "no geometrically identical meshes were found"
          in AutoInstancer.format_summary(summ, 0), f"{summ}")

    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); solo = bpy.context.active_object
    check("summary: return_summary defaults off (bare list, backward compatible)",
          isinstance(btk.auto_instance([solo], verbose=False), list))

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("===AUTO-INSTANCER===")
print("\n".join(lines))
print("===RESULT: PASS===" if ok else "===RESULT: FAIL===")
