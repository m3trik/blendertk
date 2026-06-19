"""blendertk Matrices feature test — compose/decompose/inverse/mult/space-conversion over
``mathutils.Matrix`` plus object-matrix get/set (mirror of mayatk's ``xform_utils.matrices``).

Run: blender --background --factory-startup --python blendertk/test/test_matrices.py
"""
import sys
import os
import math
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


def approx(a, b, tol=1e-5):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


try:
    import bpy
    from mathutils import Matrix, Quaternion, Vector
    import blendertk as btk
    from blendertk.xform_utils.matrices import Matrices

    check("btk.Matrices resolves from the subpackage path", btk.Matrices is Matrices)

    # ---- identity / is_identity ---------------------------------------------
    I = Matrices.identity()
    check("identity() is the 4x4 identity", I == Matrix.Identity(4))
    check("is_identity(identity) True", Matrices.is_identity(I))
    check("is_identity(non-identity) False",
          not Matrices.is_identity(Matrix.Translation((1, 0, 0))))

    # ---- from_srt / decompose (degrees) -------------------------------------
    m = Matrices.from_srt(translate=(5, -2, 3), rotate_euler_deg=(0, 0, 90), scale=(2, 2, 2))
    t, r_deg, s = Matrices.decompose(m)
    check("from_srt->decompose translation", approx(t, (5, -2, 3)), f"{t}")
    check("from_srt->decompose rotation (deg, Z=90)", approx(r_deg, (0, 0, 90), tol=1e-3), f"{r_deg}")
    check("from_srt->decompose scale", approx(s, (2, 2, 2)), f"{s}")

    # rotation actually applied: +X axis maps to ~+Y after a +90° Z rotation
    rotated_x = (m.to_3x3() @ Vector((1, 0, 0))).normalized()
    check("from_srt rotation orients +X -> +Y", approx(rotated_x, (0, 1, 0), tol=1e-4),
          f"{tuple(rotated_x)}")

    # ---- compose (quaternion-native) ----------------------------------------
    q = Quaternion((0, 0, 1), math.radians(45))
    cm = Matrices.compose(translate=(1, 2, 3), rotation=q, scale=(1, 1, 1))
    ct, cq, cs = cm.decompose()
    check("compose translation", approx((ct.x, ct.y, ct.z), (1, 2, 3)), f"{ct}")
    check("compose rotation round-trips", abs(cq.rotation_difference(q).angle) < 1e-5)

    # ---- inverse / mult -----------------------------------------------------
    check("inverse @ m == identity", Matrices.is_identity(Matrices.inverse(m) @ m, tolerance=1e-5))
    A = Matrix.Translation((1, 0, 0))
    B = Matrix.Translation((0, 1, 0))
    check("mult(A, B) == A @ B", Matrices.mult(A, B) == A @ B)
    check("mult() with no args -> identity", Matrices.is_identity(Matrices.mult()))

    # ---- space conversion (Blender column-major order) ----------------------
    parent_world = Matrices.from_srt(translate=(10, 0, 0), rotate_euler_deg=(0, 90, 0))
    local = Matrices.from_srt(translate=(0, 5, 0))
    world = Matrices.local_to_world(local, parent_world)
    back = Matrices.world_to_local(world, parent_world)
    check("local_to_world == parent @ local", world == parent_world @ local)
    check("world_to_local round-trips to local", Matrices.is_identity(back.inverted() @ local, tolerance=1e-5))

    # ---- to_matrix coercions ------------------------------------------------
    flat = [1, 0, 0, 4, 0, 1, 0, 5, 0, 0, 1, 6, 0, 0, 0, 1]
    check("to_matrix(16-flat) reads row-major translation",
          approx(Matrices.to_matrix(flat).to_translation(), (4, 5, 6)), f"{Matrices.to_matrix(flat).to_translation()}")
    check("to_matrix(Matrix) copies (not the same object)",
          Matrices.to_matrix(I) == I and Matrices.to_matrix(I) is not I)

    # ---- object matrix get/set ----------------------------------------------
    bpy.ops.object.select_all(action="DESELECT")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(location=(7, 8, 9))
    cube = bpy.context.active_object

    wm = Matrices.get_matrix(cube, "world")
    check("get_matrix(world) reads the world translation", approx(wm.to_translation(), (7, 8, 9)),
          f"{wm.to_translation()}")
    check("get_matrix returns a copy (not the live matrix)", wm is not cube.matrix_world)
    check("to_matrix(object) == its world matrix", Matrices.to_matrix(cube) == cube.matrix_world)
    check("extract_translation(world)", approx(Matrices.extract_translation(wm), (7, 8, 9)))

    target = Matrices.from_srt(translate=(1, 2, 3), rotate_euler_deg=(0, 0, 90), scale=(1, 1, 1))
    Matrices.set_matrix(cube, target, "world")
    bpy.context.view_layer.update()
    check("set_matrix(world) applies the matrix",
          approx(cube.matrix_world.to_translation(), (1, 2, 3)), f"{cube.matrix_world.to_translation()}")
    check("set_matrix accepts a 16-flat sequence",
          (Matrices.set_matrix(cube, flat, "basis"), approx(cube.matrix_basis.to_translation(), (4, 5, 6)))[1])

    try:
        Matrices.get_matrix(cube, "nope")
        check("get_matrix bad space -> ValueError", False)
    except ValueError:
        check("get_matrix bad space -> ValueError", True)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
