# !/usr/bin/python
# coding=utf-8
"""Transform diagnostics — the Blender counterpart of mayatk's
``core_utils.diagnostics.transform_diag`` (``TransformDiagnostics``).

Houses :func:`fix_non_orthogonal_axes` — non-orthogonal (sheared) object axes break FBX export.

**Blender divergence (documented).** A Maya transform carries its own ``shear`` attribute, so
mayatk fixes shear by freezing the object's transforms. A Blender object's local transform is
**always** Loc·Rot·Scale (orthogonal axes — even non-uniform scale keeps them orthogonal), so it
*cannot* hold shear on its own; shear only appears in ``matrix_world`` when an object is parented
under a non-uniformly-scaled **and** rotated parent. The fix therefore bakes the sheared visual
transform into an orthogonal one via "clear parent & keep transform" (Blender decomposes the world
matrix back to Loc·Rot·Scale, dropping the shear) — the object is un-parented in the process, the
analogue of Maya's freeze. ``import bpy`` is deferred into the call body.
"""

import pythontk as ptk

from blendertk.core_utils._core_utils import _object_mode


def _has_shear(matrix_3x3, tolerance=1e-5):
    """True if the 3×3's column axes are not mutually orthogonal (shear). Non-uniform scale keeps
    axes orthogonal, so it does NOT count; degenerate (zero-length) axes are ignored."""
    cols = [matrix_3x3.col[i] for i in range(3)]
    lengths = [c.length for c in cols]
    if any(length < 1e-9 for length in lengths):
        return False
    for i, j in ((0, 1), (0, 2), (1, 2)):
        if abs(cols[i].dot(cols[j]) / (lengths[i] * lengths[j])) > tolerance:
            return True
    return False


@_object_mode
def fix_non_orthogonal_axes(objects=None, dry_run=False, tolerance=1e-5):
    """Bake out non-orthogonal (sheared) world axes — shear breaks FBX export (mirror of
    ``mtk.TransformDiagnostics.fix_non_orthogonal_axes``). ``@_object_mode``-guarded: the
    ``parent_clear`` / ``visual_transform_apply`` ops it uses require OBJECT mode.

    Args:
        objects: objects (datablocks or names) to check; ``None`` uses the current selection.
        dry_run: when True, only report which objects would be fixed (no changes).
        tolerance: max axis-pair cosine treated as orthogonal.

    Returns:
        list: the objects fixed (or, on ``dry_run``, the objects that *would* be fixed). The fix
        is "clear parent & keep transform", which un-parents the object and decomposes its world
        matrix to an orthogonal Loc·Rot·Scale (see the module note).
    """
    import bpy
    from blendertk.core_utils._core_utils import selected_objects

    if objects is None:
        objects = list(selected_objects())
    pool = []
    for o in ptk.make_iterable(objects):
        obj = bpy.data.objects.get(o) if isinstance(o, str) else o
        if obj is not None:
            pool.append(obj)

    flagged = [o for o in pool if _has_shear(o.matrix_world.to_3x3(), tolerance)]
    if dry_run:
        for o in flagged:
            print(f"Dry run: would fix non-orthogonal axes on {o.name}")
        return flagged
    if not flagged:
        return []

    prior = list(selected_objects())
    prior_active = bpy.context.view_layer.objects.active
    fixed = []
    try:
        for obj in flagged:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            if obj.parent is not None:
                # decompose world -> Loc·Rot·Scale (drops shear), keep visual transform
                bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
            else:  # unparented objects can't carry shear; defensive re-bake of the visual xform
                bpy.ops.object.visual_transform_apply()
            fixed.append(obj)
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for o in prior:
            try:
                o.select_set(True)
            except ReferenceError:
                pass
        if prior_active is not None:
            try:
                bpy.context.view_layer.objects.active = prior_active
            except ReferenceError:
                pass
    return fixed


class TransformDiagnostics:
    """Transform/shear diagnostics (mirror of mayatk's ``TransformDiagnostics``)."""

    fix_non_orthogonal_axes = staticmethod(fix_non_orthogonal_axes)
