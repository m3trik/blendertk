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

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===XFORM-SMOKE===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
