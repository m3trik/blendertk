"""blendertk.xform_utils headless test — object-transform helpers (no viewport needed).
Run: blender --background --factory-startup --python blendertk/test/test_xform_utils.py
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

def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol

try:
    import bpy
    import blendertk as btk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    # 1. drop_to_grid: a 2m cube centered at z=5 (bbox z [4,6]) -> Min sits at z=0
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 5))
    c = bpy.context.active_object
    btk.drop_to_grid(c, align="Min")
    mn, mx = btk.get_world_bbox(c)
    check("drop_to_grid Min -> bbox min.z == 0", approx(mn.z, 0.0), f"min.z={mn.z:.3f}")

    # 1b. drop_to_grid center_pivot=True routes through center_pivot() -> origin at geo bbox
    #     center (verifies the refactored branch + forward XformUtils ref resolves).
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(2, 0, 5)); c = bpy.context.active_object
    for v in c.data.vertices:
        v.co.x += 4.0  # geo center world x=6
    bpy.context.view_layer.update()
    btk.drop_to_grid(c, align="Min", center_pivot=True)
    mn, mx = btk.get_world_bbox(c)
    check("drop_to_grid center_pivot -> origin.x == geo center 6", approx(c.location.x, 6.0),
          f"x={c.location.x:.3f}")
    check("drop_to_grid center_pivot -> still grounded min.z == 0", approx(mn.z, 0.0),
          f"min.z={mn.z:.3f}")

    # 2. match_scale: A (size 2) rescaled to B (scale 2 -> size 4)
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); A = bpy.context.active_object; A.name = "A"
    bpy.ops.mesh.primitive_cube_add(location=(10, 0, 0)); B = bpy.context.active_object; B.name = "B"
    B.scale = (2, 2, 2); bpy.context.view_layer.update()
    btk.match_scale(A, B); bpy.context.view_layer.update()
    a_sz = (btk.get_world_bbox(A)[1] - btk.get_world_bbox(A)[0]).x
    b_sz = (btk.get_world_bbox(B)[1] - btk.get_world_bbox(B)[0]).x
    check("match_scale -> A size == B size", approx(a_sz, b_sz, 1e-2), f"A={a_sz:.2f} B={b_sz:.2f}")

    # 3. move_to center: A center -> B center (origin)
    A.scale = (1, 1, 1); A.location = (5, 0, 0); B.location = (0, 0, 0); bpy.context.view_layer.update()
    btk.move_to(A, B, "center"); bpy.context.view_layer.update()
    check("move_to center -> A center.x == 0", approx(A.matrix_world.translation.x, 0.0),
          f"x={A.matrix_world.translation.x:.3f}")

    # 4. freeze_transforms: bake location + scale -> reset to 0 / 1
    A.location = (3, 0, 0); A.scale = (2, 2, 2); bpy.context.view_layer.update()
    btk.freeze_transforms(A, location=True, rotation=False, scale=True)
    check("freeze -> location.x == 0", approx(A.location.x, 0.0), f"x={A.location.x:.3f}")
    check("freeze -> scale.x == 1", approx(A.scale.x, 1.0), f"s={A.scale.x:.3f}")

    # 5. center_pivot: origin at (5,0,0), mesh shifted +3 -> object-mode origin -> bbox center x=8
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0)); c = bpy.context.active_object
    for v in c.data.vertices:
        v.co.x += 3.0
    bpy.context.view_layer.update()
    btk.center_pivot(c, mode="object")
    check("center_pivot object -> origin.x == 8", approx(c.location.x, 8.0), f"x={c.location.x:.3f}")
    btk.center_pivot(c, mode="world")
    check("center_pivot world -> origin.x == 0", approx(c.location.x, 0.0), f"x={c.location.x:.3f}")

    # 6. object-mode guard: invoke from EDIT mode (Component pivot workflow) -> still works,
    #    and the caller's edit mode is restored (origin_set would otherwise raise in edit mode).
    bpy.ops.object.select_all(action="DESELECT")
    c.select_set(True); bpy.context.view_layer.objects.active = c
    bpy.ops.object.mode_set(mode="EDIT")
    btk.center_pivot(c, mode="object")  # geometry world bbox center is still x=8
    check("center_pivot from EDIT -> origin.x == 8 (no raise)", approx(c.location.x, 8.0),
          f"x={c.location.x:.3f}")
    check("center_pivot restores EDIT mode", c.mode == "EDIT", f"mode={c.mode}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # 7. freeze stamps bakes; restore_transforms un-freezes with world position preserved
    reset()
    import math
    from mathutils import Vector
    bpy.ops.mesh.primitive_cube_add(location=(3, 2, 1))
    c = bpy.context.active_object
    c.rotation_euler = (0.0, 0.0, math.radians(90))
    c.scale = (2.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    world_before = [(c.matrix_world @ v.co).copy() for v in c.data.vertices]
    btk.freeze_transforms(c, location=True, rotation=True, scale=True)
    check("freeze zeroes the transform",
          approx(c.location.length, 0) and approx(c.scale.x, 1.0))
    check("freeze stamps bakes", "btk_T_bake" in c and "btk_R_bake" in c and "btk_S_bake" in c)
    restored = btk.restore_transforms(c)
    check("restore returns the object", restored and restored[0] is c)
    check("restore brings the channels back",
          approx(c.location.x, 3.0) and approx(c.scale.x, 2.0)
          and approx(math.degrees(c.rotation_euler.z), 90.0),
          f"loc={tuple(round(v,3) for v in c.location)} s={c.scale.x:.3f}")
    world_after = [(c.matrix_world @ v.co) for v in c.data.vertices]
    drift = max((a - b).length for a, b in zip(world_after, world_before))
    check("restore preserves world position", drift < 1e-4, f"drift={drift:.6f}")
    check("restore consumes the bakes", "btk_T_bake" not in c)

    # 7b. cumulative contract: freeze, move, freeze again -> one restore returns the sum
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(1, 0, 0)); c = bpy.context.active_object
    btk.freeze_transforms(c, location=True, rotation=False, scale=False)
    c.location = (2, 0, 0)
    bpy.context.view_layer.update()
    btk.freeze_transforms(c, location=True, rotation=False, scale=False)
    btk.restore_transforms(c)
    check("cumulative freeze composes (1+2=3)", approx(c.location.x, 3.0), f"x={c.location.x:.3f}")

    # 7c. restore with nothing stored is a no-op
    reset()
    bpy.ops.mesh.primitive_cube_add(); c = bpy.context.active_object
    check("restore without bakes returns []", btk.restore_transforms(c) == [])

    # 7d. center_pivot drops the stale location bake (origin is the translate reference) so a
    # later un-freeze won't double-apply translation; the scale bake stays valid.
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0)); c = bpy.context.active_object
    c.scale = (2.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    btk.freeze_transforms(c, location=True, rotation=False, scale=True)  # stamps T + S bakes
    btk.center_pivot(c, mode="object")  # moves the origin -> the T bake is now stale
    check("center_pivot drops the stale location bake", "btk_T_bake" not in c)
    check("center_pivot keeps the scale bake", "btk_S_bake" in c)
    world_before = [(c.matrix_world @ v.co).copy() for v in c.data.vertices]
    btk.restore_transforms(c)
    world_after = [(c.matrix_world @ v.co) for v in c.data.vertices]
    drift = max((a - b).length for a, b in zip(world_after, world_before))
    check("un-freeze after center_pivot preserves world position (no double-translate)",
          drift < 1e-4, f"drift={drift:.6f}")

    # 8. scale_connected_edges: two separate selected edge loops each scale about their own center
    reset()
    bpy.ops.mesh.primitive_cube_add(size=2.0); c = bpy.context.active_object
    import bmesh
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(c.data)
    for e in bm.edges:
        e.select = False
    # select the top face's edge ring (z=+1) and the bottom's (z=-1) -> 2 connected sets
    for e in bm.edges:
        if all(approx(v.co.z, 1.0) for v in e.verts) or all(approx(v.co.z, -1.0) for v in e.verts):
            e.select = True
    bmesh.update_edit_mesh(c.data)
    n_sets = btk.scale_connected_edges(c, scale_factor=2.0)
    check("scale_connected_edges finds 2 sets", n_sets == 2, f"sets={n_sets}")
    bm = bmesh.from_edit_mesh(c.data)
    top = [v for v in bm.verts if approx(v.co.z, 1.0)]
    check("top loop scaled about its own center (x = +/-2)",
          all(approx(abs(v.co.x), 2.0) and approx(abs(v.co.y), 2.0) for v in top),
          f"{[round(v.co.x,2) for v in top]}")
    check("z untouched (uniform in-plane set keeps its plane)",
          all(approx(abs(v.co.z), 1.0) for v in bm.verts))
    bpy.ops.object.mode_set(mode="OBJECT")

    # 8b. object mode / no selection -> 0 sets
    check("scale_connected_edges object mode -> 0", btk.scale_connected_edges(c) == 0)

    # 9. transfer_pivot — origin moves onto the source's origin, geometry stays put
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_cube_add(location=(7, 0, 0)); src = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0)); tgt = bpy.context.active_object
    v_before = tuple(tgt.matrix_world @ tgt.data.vertices[0].co)
    btk.transfer_pivot([src, tgt], translate=True)
    drift = max(abs(p - q) for p, q in zip(v_before, tuple(tgt.matrix_world @ tgt.data.vertices[0].co)))
    check("transfer_pivot moves origin to source", approx(tgt.matrix_world.translation.x, 7.0),
          f"x={round(tgt.matrix_world.translation.x, 2)}")
    check("transfer_pivot preserves geometry", drift < 1e-5, f"drift={drift:.2e}")
    check("transfer_pivot needs 2+ objects (no-op)", btk.transfer_pivot([src]) == [])

    # 10. spatial queries: get_bounding_box / get_center_point / get_distance /
    #     order_by_distance / aim_object_at_point
    from mathutils import Vector

    reset()
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0)); c0 = bpy.context.active_object
    bb = btk.get_bounding_box(c0)
    check("get_bounding_box dict (2m cube)",
          approx(bb["sizex"], 2.0) and approx(bb["volume"], 8.0)
          and tuple(round(v, 3) for v in bb["center"]) == (0.0, 0.0, 0.0), f"{bb}")
    check("get_bounding_box single value key", approx(btk.get_bounding_box(c0, "sizez"), 2.0))
    check("get_center_point", tuple(round(v, 3) for v in btk.get_center_point(c0)) == (0, 0, 0))
    bpy.ops.mesh.primitive_cube_add(size=2, location=(4, 0, 0)); c1 = bpy.context.active_object
    combined = btk.get_bounding_box([c0, c1])
    check("get_bounding_box combined spans both",
          approx(combined["xmin"], -1.0) and approx(combined["xmax"], 5.0), f"{combined}")
    check("get_distance between objects", approx(btk.get_distance(c0, c1), 4.0))
    check("get_distance between vectors", approx(btk.get_distance((0, 0, 0), (0, 3, 4)), 5.0))
    check("order_by_distance (origin)", btk.order_by_distance([c1, c0]) == [c0, c1])
    check("order_by_distance reverse", btk.order_by_distance([c0, c1], reverse=True) == [c1, c0])
    aimed = btk.aim_object_at_point(c0, (0, 0, 10), aim_vect=(0, 0, 1), up_vect=(0, 1, 0))
    zdir = (c0.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()
    check("aim_object_at_point points the aim axis at the target",
          aimed == [c0] and approx(zdir.z, 1.0, 0.01),
          f"zdir={tuple(round(v, 2) for v in zdir)}")

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===XFORM-SMOKE===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
