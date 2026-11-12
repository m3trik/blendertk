"""blendertk.LodUtils headless test -- LOD groups in Blender, mirror of mayatk's
test_lods. Run: blender --background --factory-startup --python blendertk/test/test_lods.py
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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def tris(o):
        return sum(len(p.vertices) - 2 for p in o.data.polygons)

    # generate: a sphere becomes a group Empty with falling levels
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=40, location=(1, 2, 3))
    ball = bpy.context.active_object
    ball.name = "ball"
    (group,) = btk.LodUtils.generate([ball])
    levels = sorted(group.children, key=lambda o: o.name)
    check(
        "generate makes ball with ball_LOD0..2",
        group.name == "ball" and group.type == "EMPTY"
        and [o.name for o in levels] == ["ball_LOD0", "ball_LOD1", "ball_LOD2"],
        f"{group.name} {[o.name for o in levels]}",
    )
    counts = [tris(o) for o in levels]
    check("each level has fewer triangles", counts == sorted(counts, reverse=True) and counts[1] < counts[0] * 0.6, counts)
    check("LOD0 keeps its world place", abs(levels[0].matrix_world.translation.z - 3.0) < 1e-4,
          tuple(levels[0].matrix_world.translation))
    check(
        "the group states its FBX type and its heights",
        group.get("fbx_type") == "LodGroup" and list(group.get("lod_heights")) == [0.5, 0.25, 0.0],
        dict(group.items()),
    )
    (read,) = btk.LodUtils.groups([group])
    check("the exporter reads one valid group", read["issues"] == [], read)

    # a light mesh and an existing level are skipped
    reset()
    bpy.ops.mesh.primitive_cube_add()
    box = bpy.context.active_object
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=40)
    old = bpy.context.active_object
    old.name = "old_LOD0"
    check("a light mesh and a level are skipped", btk.LodUtils.generate([box, old]) == [])

    # create_group: meshes in order become the levels
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=30, ring_count=30)
    hi = bpy.context.active_object
    bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=8)
    lo = bpy.context.active_object
    seat = btk.LodUtils.create_group([hi, lo], name="seat")
    check(
        "create_group names and groups the levels",
        sorted(o.name for o in seat.children) == ["seat_LOD0", "seat_LOD1"]
        and list(seat.get("lod_heights")) == [0.5, 0.0],
        sorted(o.name for o in seat.children),
    )

    # create_group: the group takes the base its first level held (2026-11-10 review)
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=30, ring_count=30)
    hi = bpy.context.active_object
    hi.name = "chair"
    bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=8)
    lo = bpy.context.active_object
    made = btk.LodUtils.create_group([hi, lo])
    check("create_group names the group its first level's base", made.name == "chair", made.name)
    (read,) = btk.LodUtils.groups([made])
    check("levels that agree note nothing", read["notes"] == [], read["notes"])
    lo.location.x += 5.0  # evaluated matrices: no view-layer update between
    (read,) = btk.LodUtils.groups([made])
    check("a level away from LOD0 is noted, not blocked",
          read["issues"] == [] and "LOD1" in " ".join(read["notes"]), read)

    # a parented mesh keeps its world placement and its own transform (2026-11-10
    # review: the group's matrix_world was read before any evaluation)
    from mathutils import Matrix

    def world(o):
        bpy.context.view_layer.update()
        return o.matrix_world.copy()

    reset()
    holder = bpy.data.objects.new("holder", None)
    bpy.context.scene.collection.objects.link(holder)
    holder.location = (5, 0, 0)
    holder.rotation_euler = (0, 0, 0.7)
    holder.scale = (2, 2, 2)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=40)
    ball = bpy.context.active_object
    ball.name = "ball"
    ball.parent = holder
    ball.matrix_parent_inverse = Matrix.Translation((0.5, 0, 0))
    ball.location = (1, 2, 3)
    before, basis = world(ball), ball.matrix_basis.copy()
    (group,) = btk.LodUtils.generate([ball])
    after = world(ball)
    check("a parented mesh keeps its world placement",
          all(abs(a - b) < 1e-5 for ra, rb in zip(after, before) for a, b in zip(ra, rb)), (after, before))
    check("and its own transform", ball.matrix_basis == basis and group.parent is holder, (ball.matrix_basis, group.parent))
    copy = next(o for o in group.children if o.name == "ball_LOD1")
    check("each copy stands where LOD0 does",
          all(abs(a - b) < 1e-5 for ra, rb in zip(world(copy), before) for a, b in zip(ra, rb)), world(copy))

    # a modifier stack: a level is what the FBX writes (2026-11-10 review)
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    smooth = bpy.context.active_object
    smooth.name = "smooth"
    smooth.modifiers.new("Subdivision", "SUBSURF").levels = 2
    lod0_tris = btk.LodUtils.triangles(smooth)
    check("triangles count the evaluated mesh", lod0_tris > btk.CoreUtils._mesh_face_counts(smooth.data)[0] * 10, lod0_tris)
    (group,) = btk.LodUtils.generate([smooth])
    lod1 = next(o for o in group.children if o.name == "smooth_LOD1")
    check("a generated level carries no modifier", len(lod1.modifiers) == 0, [m.type for m in lod1.modifiers])
    check("and halves what LOD0 writes", 0.3 * lod0_tris < btk.LodUtils.triangles(lod1) < 0.7 * lod0_tris,
          (btk.LodUtils.triangles(lod1), lod0_tris))
    (read,) = btk.LodUtils.groups([group])
    check("the group reads valid on its written triangles", read["issues"] == [], read["issues"])

    # a mesh holding other objects is skipped
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=40)
    plate = bpy.context.active_object
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=40)
    bolt = bpy.context.active_object
    bolt.parent = plate
    check("a mesh holding another is skipped", btk.LodUtils.generate([plate]) == [])

    # a mesh under a moving parent is generated and its levels follow; only its
    # own animation needs authored LODs (production rigs key the _LOC above each
    # mesh: refusing an animated ancestor left 330 of 331 ungenerated, 2026-11-11)
    reset()
    rig = bpy.data.objects.new("arm_LOC", None)
    bpy.context.scene.collection.objects.link(rig)
    rig.rotation_euler = (0, 0, 0)
    rig.keyframe_insert("rotation_euler", index=2, frame=1)
    rig.rotation_euler = (0, 0, 1.5)
    rig.keyframe_insert("rotation_euler", index=2, frame=10)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=40)
    ball = bpy.context.active_object
    ball.name = "ball"
    ball.parent = rig
    ball.location = (2, 0, 0)
    check("a mesh under a moving parent is eligible", btk.LodUtils.ineligible(ball) is None, btk.LodUtils.ineligible(ball))
    made = btk.LodUtils.generate([ball])
    check("and generated", len(made) == 1, made)
    bpy.context.scene.frame_set(10)
    lod1 = next(o for o in made[0].children if o.name == "ball_LOD1")
    check("its level follows the parent",
          all(abs(a - b) < 1e-4 for ra, rb in zip(world(lod1), world(ball)) for a, b in zip(ra, rb)), (world(lod1), world(ball)))
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=40)
    spinner = bpy.context.active_object
    spinner.keyframe_insert("rotation_euler", index=2, frame=1)
    check("a mesh keyed itself stays animated", btk.LodUtils.ineligible(spinner) == "animated", btk.LodUtils.ineligible(spinner))

    # a shape-keyed mesh is left alone: a level is a static copy (2026-11-10 review)
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=40)
    morph = bpy.context.active_object
    morph.shape_key_add(name="Basis")
    check("a shape-keyed mesh is skipped, said", btk.LodUtils.generate([morph]) == []
          and btk.LodUtils.ineligible(morph) == "skinned or with blend shapes", btk.LodUtils.ineligible(morph))

    # create_group on the selection: most triangles first (Blender's selection holds no order)
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=8)
    low = bpy.context.active_object
    low.name = "pot"
    bpy.ops.mesh.primitive_uv_sphere_add(segments=30, ring_count=30)
    high = bpy.context.active_object
    for o in (low, high):
        o.select_set(True)
    pot = btk.LodUtils.create_group()
    check("the selection's most detailed mesh becomes LOD0",
          pot is not None and high.name.endswith("_LOD0") and low.name.endswith("_LOD1"), (high.name, low.name))

    # USD: a group nested in another's level is stated too, inner first (the
    # twin is pure pxr; 2026-11-10 review: the outer edit left the inner's
    # prims stale and the call raised)
    import tempfile as _tf
    from pxr import Usd as _Usd, UsdGeom as _UsdGeom

    _nested = os.path.join(_tf.mkdtemp(), "nested.usda")
    _stage = _Usd.Stage.CreateNew(_nested)
    for _p in ("/car", "/car/car_LOD0", "/car/car_LOD1", "/car/car_LOD0/wheel"):
        _UsdGeom.Xform.Define(_stage, _p)
    for _p in ("/car/car_LOD0/body", "/car/car_LOD1/body", "/car/car_LOD0/wheel/wheel_LOD0", "/car/car_LOD0/wheel/wheel_LOD1"):
        _UsdGeom.Mesh.Define(_stage, _p)
    _stage.GetRootLayer().Save()
    del _stage
    _stated = btk.UsdUtils.author_lod_variants(_nested, [
        {"group": "car", "levels": ["car_LOD0", "car_LOD1"], "variants": ["LOD0", "LOD1"]},
        {"group": "wheel", "levels": ["wheel_LOD0", "wheel_LOD1"], "variants": ["LOD0", "LOD1"]},
    ])
    check("nested LOD groups are both stated as variant sets", sorted(_stated) == ["/car", "/car/car_LOD0/wheel"], _stated)

    # conform: LOD_<n> containers (Houdini, an FBX from Maya's generator)
    reset()
    holder = bpy.data.objects.new("LOD_Group_1", None)
    bpy.context.scene.collection.objects.link(holder)
    for index, segments in enumerate((24, 8)):
        container = bpy.data.objects.new(f"LOD_{index}", None)
        bpy.context.scene.collection.objects.link(container)
        container.parent = holder
        bpy.ops.mesh.primitive_torus_add(major_segments=segments, minor_segments=segments)
        wheel = bpy.context.active_object
        wheel.name = "wheel.L"  # a dot of its own: kept (2026-11-10 review)
        wheel.parent = container
    btk.LodUtils.conform([holder])
    (after,) = btk.LodUtils.groups([holder])
    check(
        "conform turns containers into named levels",
        sorted(o.name for o in holder.children) == ["wheel.L_LOD0", "wheel.L_LOD1"]
        and after["form"] == "names" and after["issues"] == [],
        sorted(o.name for o in holder.children),
    )

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(ln.startswith("OK") for ln in lines)
print("\n===LODS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
