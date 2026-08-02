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
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import bpy
    from mathutils import Euler
    import blendertk as btk
    from blendertk.core_utils.diagnostics.mesh_diag import MeshDiagnostics
    from blendertk.core_utils.diagnostics.transform_diag import TransformDiagnostics

    # ---- re-home + aggregator resolution ------------------------------------
    check(
        "btk.MeshDiagnostics exposed class-only (submodule, not flat)",
        btk.MeshDiagnostics is MeshDiagnostics
        and not hasattr(btk, "find_problem_geometry"),
    )
    check(
        "btk.Diagnostics aggregates find_problem_geometry",
        btk.Diagnostics.find_problem_geometry is MeshDiagnostics.find_problem_geometry,
    )
    check(
        "btk.Diagnostics aggregates fix_non_orthogonal_axes",
        btk.Diagnostics.fix_non_orthogonal_axes
        is TransformDiagnostics.fix_non_orthogonal_axes,
    )
    check(
        "EditUtils no longer carries find_problem_geometry",
        not hasattr(btk.EditUtils, "find_problem_geometry"),
    )

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
    check(
        "non-uniform scale is not flagged as shear",
        not TransformDiagnostics._has_shear(clean.matrix_world.to_3x3()),
    )

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
    child.rotation_euler = Euler(
        (0.0, 0.0, math.radians(30)), "XYZ"
    )  # rotated vs parent -> shear
    bpy.context.view_layer.update()

    check(
        "constructed child world matrix is sheared (precondition)",
        TransformDiagnostics._has_shear(child.matrix_world.to_3x3()),
        f"{tuple(child.matrix_world.to_3x3().col[0])}",
    )

    # ---- get_non_orthogonal: the public detection primitive (mtk mirror) -----
    check(
        "get_non_orthogonal flags the sheared child",
        TransformDiagnostics.get_non_orthogonal([child, parent]) == [child],
    )
    detail = TransformDiagnostics.get_non_orthogonal([child], detailed=True)
    check(
        "detailed reports skew + inherited cause",
        detail[child]["cause"] == "inherited" and detail[child]["skew"] > 0.0,
        f"{detail.get(child)}",
    )
    check(
        "get_non_orthogonal accepts object names",
        TransformDiagnostics.get_non_orthogonal([child.name]) == [child],
    )

    # ---- dry_run reports without changing -----------------------------------
    would = TransformDiagnostics.fix_non_orthogonal_axes([child], dry_run=True)
    check("dry_run flags the sheared child", child in would)
    check("dry_run does not change parenting", child.parent is parent)
    check(
        "dry_run leaves the shear in place",
        TransformDiagnostics._has_shear(child.matrix_world.to_3x3()),
    )

    # ---- real fix removes shear ---------------------------------------------
    fixed = TransformDiagnostics.fix_non_orthogonal_axes([child])
    bpy.context.view_layer.update()
    check("fix returns the child", child in fixed)
    check("fix clears the parent (keep-transform)", child.parent is None)
    check(
        "fix removes the shear",
        not TransformDiagnostics._has_shear(child.matrix_world.to_3x3()),
        f"{tuple(child.matrix_world.to_3x3().col[0])}",
    )

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
    edit_fixed = TransformDiagnostics.fix_non_orthogonal_axes(
        [child2]
    )  # would raise unguarded from EDIT mode
    check(
        "fix_non_orthogonal_axes succeeds when called from EDIT mode",
        child2 in edit_fixed,
    )
    check("guard restores the caller's EDIT mode", editor.mode == "EDIT")
    bpy.ops.object.mode_set(mode="OBJECT")

    # ---- no-shear input -> no-op --------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    ortho = bpy.context.active_object
    ortho.scale = (2.0, 0.5, 1.0)
    bpy.context.view_layer.update()
    check(
        "fix on a clean (orthogonal) object is a no-op",
        TransformDiagnostics.fix_non_orthogonal_axes([ortho]) == [],
    )

    # ---- driven transforms: skipped by default, fixed with break_connections
    def sheared_driven(name):
        p = bpy.data.objects.new(f"{name}Parent", None)
        bpy.context.scene.collection.objects.link(p)
        p.scale = (2.0, 1.0, 0.5)
        p.rotation_euler = Euler((0.0, 0.0, math.radians(45)), "XYZ")
        bpy.ops.mesh.primitive_cube_add()
        c = bpy.context.active_object
        c.name = name
        c.parent = p
        c.rotation_euler = Euler((0.0, 0.0, math.radians(30)), "XYZ")
        drv = c.driver_add("rotation_euler", 2).driver  # driver on rot z
        drv.expression = "0.5236"  # 30 degrees, static
        bpy.context.view_layer.update()
        return c

    reset()
    driven = sheared_driven("DrivenChild")
    detail = TransformDiagnostics.get_non_orthogonal([driven], detailed=True)
    check(
        "detailed reports the transform driver",
        detail[driven]["driven"] == ["driver:rotation_euler"],
        f"{detail[driven].get('driven')}",
    )
    check(
        "driven object skipped by default",
        TransformDiagnostics.fix_non_orthogonal_axes([driven]) == [],
    )
    check("driver survives the skip", bool(driven.animation_data.drivers))
    check(
        "dry_run excludes driven too",
        TransformDiagnostics.fix_non_orthogonal_axes([driven], dry_run=True) == [],
    )
    broke = TransformDiagnostics.fix_non_orthogonal_axes(
        [driven], break_connections=True
    )
    bpy.context.view_layer.update()
    check("break_connections fixes the driven object", driven in broke)
    check(
        "break_connections removed the driver",
        not (driven.animation_data and list(driven.animation_data.drivers)),
    )
    check(
        "skew gone after break-fix",
        TransformDiagnostics.get_non_orthogonal([driven]) == [],
    )

    # constraints count as driven too (they re-compose over the baked values)
    reset()
    bpy.ops.mesh.primitive_cube_add()
    tgt = bpy.context.active_object
    con_parent = bpy.data.objects.new("ConParent", None)
    bpy.context.scene.collection.objects.link(con_parent)
    con_parent.scale = (2.0, 1.0, 0.5)
    con_parent.rotation_euler = Euler((0.0, 0.0, math.radians(45)), "XYZ")
    bpy.ops.mesh.primitive_cube_add()
    constrained = bpy.context.active_object
    constrained.parent = con_parent
    constrained.rotation_euler = Euler((0.0, 0.0, math.radians(30)), "XYZ")
    con = constrained.constraints.new("COPY_LOCATION")
    con.target = tgt
    bpy.context.view_layer.update()
    d2 = TransformDiagnostics.get_non_orthogonal([constrained], detailed=True)
    check(
        "constraint reported as driven",
        d2[constrained]["driven"] == [f"constraint:{con.name}"],
        f"{d2[constrained].get('driven')}",
    )
    check(
        "constrained object skipped by default",
        TransformDiagnostics.fix_non_orthogonal_axes([constrained]) == [],
    )
    broke2 = TransformDiagnostics.fix_non_orthogonal_axes(
        [constrained], break_connections=True
    )
    check("break_connections fixes constrained object", constrained in broke2)
    check("constraint removed", len(constrained.constraints) == 0)

    # ---- the fix must stamp bake history (mayatk parity: its twin stores too).
    # parent_clear / visual_transform_apply rewrite the local transform without
    # going through freeze_transforms, so without an explicit stamp an axis fix
    # is irreversible.
    import blendertk as btk

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.empty_add(location=(0, 0, 0))
    parent = bpy.context.active_object
    parent.scale = (2.0, 1.0, 1.0)
    parent.rotation_euler = (0.0, 0.0, 0.7)
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    child = bpy.context.active_object
    child.parent = parent
    child.rotation_euler = (0.0, 0.0, 0.4)
    bpy.context.view_layer.update()

    check(
        "sheared child detected",
        child in TransformDiagnostics.get_non_orthogonal([child]),
    )
    TransformDiagnostics.fix_non_orthogonal_axes([child])
    check(
        "axis fix stamps bake history",
        btk.XformUtils.get_stored_transforms(child) is not None,
        "un-freeze cannot reverse an unstamped axis fix",
    )

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
