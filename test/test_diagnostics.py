"""blendertk diagnostics feature test — Diagnostics aggregator + fix_non_orthogonal_axes
(mirror of mayatk's ``core_utils.diagnostics``). find_problem_geometry detection is covered by
``test_edit_utils.py``; this exercises the re-homed resolution + the transform (shear) diag.

Run: blender --background --factory-startup --python blendertk/test/test_diagnostics.py
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


try:
    import bpy
    from mathutils import Euler
    import blendertk as btk
    from blendertk.core_utils.diagnostics.mesh_diag import find_problem_geometry
    from blendertk.core_utils.diagnostics.transform_diag import (
        TransformDiagnostics,
        fix_non_orthogonal_axes,
        _has_shear,
    )

    # ---- re-home + aggregator resolution ------------------------------------
    check("btk.find_problem_geometry re-homed to mesh_diag", btk.find_problem_geometry is find_problem_geometry)
    check("btk.Diagnostics aggregates find_problem_geometry",
          btk.Diagnostics.find_problem_geometry is find_problem_geometry)
    check("btk.Diagnostics aggregates fix_non_orthogonal_axes",
          btk.Diagnostics.fix_non_orthogonal_axes is fix_non_orthogonal_axes)
    check("EditUtils no longer carries find_problem_geometry",
          not hasattr(btk.EditUtils, "find_problem_geometry"))

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    # ---- _has_shear: orthogonal (incl. non-uniform scale) is NOT shear ------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    clean = bpy.context.active_object
    clean.scale = (3.0, 1.0, 0.5)  # non-uniform scale keeps axes orthogonal
    bpy.context.view_layer.update()
    check("non-uniform scale is not flagged as shear",
          not _has_shear(clean.matrix_world.to_3x3()))

    # ---- build a genuinely sheared child via a non-uniform rotated parent ----
    reset()
    parent = bpy.data.objects.new("ShearParent", None)  # empty
    bpy.context.scene.collection.objects.link(parent)
    parent.scale = (2.0, 1.0, 0.5)
    parent.rotation_euler = Euler((0.0, 0.0, math.radians(45)), "XYZ")

    bpy.ops.mesh.primitive_cube_add()
    child = bpy.context.active_object
    child.name = "ShearChild"
    child.parent = parent
    child.rotation_euler = Euler((0.0, 0.0, math.radians(30)), "XYZ")  # rotated vs parent -> shear
    bpy.context.view_layer.update()

    check("constructed child world matrix is sheared (precondition)",
          _has_shear(child.matrix_world.to_3x3()), f"{tuple(child.matrix_world.to_3x3().col[0])}")

    # ---- dry_run reports without changing -----------------------------------
    would = fix_non_orthogonal_axes([child], dry_run=True)
    check("dry_run flags the sheared child", child in would)
    check("dry_run does not change parenting", child.parent is parent)
    check("dry_run leaves the shear in place", _has_shear(child.matrix_world.to_3x3()))

    # ---- real fix removes shear ---------------------------------------------
    fixed = fix_non_orthogonal_axes([child])
    bpy.context.view_layer.update()
    check("fix returns the child", child in fixed)
    check("fix clears the parent (keep-transform)", child.parent is None)
    check("fix removes the shear", not _has_shear(child.matrix_world.to_3x3()),
          f"{tuple(child.matrix_world.to_3x3().col[0])}")

    # ---- @_object_mode guard: callable from EDIT mode without raising -------
    reset()
    parent2 = bpy.data.objects.new("ShearParent2", None)
    bpy.context.scene.collection.objects.link(parent2)
    parent2.scale = (2.0, 1.0, 0.5)
    parent2.rotation_euler = Euler((0.0, 0.0, math.radians(45)), "XYZ")
    bpy.ops.mesh.primitive_cube_add()
    child2 = bpy.context.active_object
    child2.parent = parent2
    child2.rotation_euler = Euler((0.0, 0.0, math.radians(30)), "XYZ")
    bpy.ops.mesh.primitive_cube_add()  # a separate mesh to host EDIT mode
    editor = bpy.context.active_object
    bpy.context.view_layer.objects.active = editor
    bpy.ops.object.mode_set(mode="EDIT")
    edit_fixed = fix_non_orthogonal_axes([child2])  # would raise unguarded from EDIT mode
    check("fix_non_orthogonal_axes succeeds when called from EDIT mode", child2 in edit_fixed)
    check("guard restores the caller's EDIT mode", editor.mode == "EDIT")
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- no-shear input -> no-op --------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    ortho = bpy.context.active_object
    ortho.scale = (2.0, 0.5, 1.0)
    bpy.context.view_layer.update()
    check("fix on a clean (orthogonal) object is a no-op", fix_non_orthogonal_axes([ortho]) == [])

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
