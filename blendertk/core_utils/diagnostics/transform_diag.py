# !/usr/bin/python
# coding=utf-8
"""Transform diagnostics — the Blender counterpart of mayatk's
``core_utils.diagnostics.transform_diag`` (``TransformDiagnostics``).

Houses :meth:`TransformDiagnostics.get_non_orthogonal` / :meth:`~TransformDiagnostics.
fix_non_orthogonal_axes` — non-orthogonal (sheared) object axes break FBX export, which reports
them as *"Non-orthogonal matrix support"*.

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

from blendertk.core_utils._core_utils import CoreUtils


class _TransformDiagnosticsInternal(object):
    """Internal helpers for TransformDiagnostics."""

    @staticmethod
    def _matrix_skew(matrix_3x3):
        """Max abs cosine between the 3×3's column axes — 0.0 when they are mutually orthogonal.
        Measurement shared with mayatk via :meth:`ptk.MathUtils.max_axis_skew` (non-uniform scale
        does NOT register; degenerate zero-length axes read 0.0)."""
        return ptk.MathUtils.max_axis_skew(matrix_3x3.col[i] for i in range(3))

    @staticmethod
    def _has_shear(matrix_3x3, tolerance=1e-5):
        """True if the 3×3's column axes are not mutually orthogonal (shear)."""
        return (
            _TransformDiagnosticsInternal._matrix_skew(matrix_3x3) > tolerance
        )

    @staticmethod
    def _resolve(objects):
        """Coerce names/datablocks (or None -> selection) to a list of objects."""
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        if objects is None:
            objects = list(CoreUtils.selected_objects())
        pool = []
        for o in ptk.make_iterable(objects):
            obj = bpy.data.objects.get(o) if isinstance(o, str) else o
            if obj is not None:
                pool.append(obj)
        return pool

    # Transform data paths the fix rewrites (parent_clear / visual_transform_apply
    # write the whole local transform, unlike Maya's rotate/scale-only freeze).
    _TRANSFORM_PATHS = (
        "location",
        "rotation_euler",
        "rotation_quaternion",
        "rotation_axis_angle",
        "scale",
    )

    @staticmethod
    def _driving_connections(obj):
        """Tags for everything re-driving *obj*'s transform after a fix would
        write it: transform drivers (``driver:<path>``), action fcurves on
        transform channels (``anim:<path>``), and unmuted constraints
        (``constraint:<name>``). Any of these makes the bake inaccurate — the
        driver re-writes (or re-composes over) the channels the fix just set."""
        paths = _TransformDiagnosticsInternal._TRANSFORM_PATHS
        found = {}
        anim = obj.animation_data
        if anim:
            for drv in anim.drivers or []:
                if drv.data_path in paths:
                    found[f"driver:{drv.data_path}"] = None
            if anim.action:
                for fcurve in anim.action.fcurves:
                    if fcurve.data_path in paths:
                        found[f"anim:{fcurve.data_path}"] = None
        for con in obj.constraints:
            if not con.mute:
                found[f"constraint:{con.name}"] = None
        return list(found)

    @staticmethod
    def _break_driving_connections(obj):
        """Remove the transform drivers / fcurves / constraints found by
        :meth:`_driving_connections` (the Blender analogue of Maya's
        ``connection_strategy='disconnect'``)."""
        for tag in _TransformDiagnosticsInternal._driving_connections(obj):
            kind, _, name = tag.partition(":")
            if kind == "driver":
                obj.driver_remove(name)
            elif kind == "anim":
                action = obj.animation_data.action
                for fcurve in [f for f in action.fcurves if f.data_path == name]:
                    action.fcurves.remove(fcurve)
            elif kind == "constraint":
                constraint = obj.constraints.get(name)
                if constraint is not None:
                    obj.constraints.remove(constraint)


class TransformDiagnostics(_TransformDiagnosticsInternal):
    """Transform/shear diagnostics (mirror of mayatk's ``TransformDiagnostics``)."""

    # Static (not classmethod) throughout, like the rest of this module: the
    # ``btk.Diagnostics`` aggregate multi-inherits these, and a classmethod
    # would bind to the aggregate, so ``Diagnostics.x is TransformDiagnostics.x``
    # would no longer hold (test_diagnostics asserts it).
    @staticmethod
    def get_non_orthogonal(objects=None, tolerance=1e-5, detailed=False):
        """Return the objects whose evaluated (world) axes are not perpendicular — the
        condition FBX reports as "Non-orthogonal matrix support" (mirror of
        ``mtk.TransformDiagnostics.get_non_orthogonal``).

        Args:
            objects: objects (datablocks or names) to check; ``None`` uses the current selection.
            tolerance: max axis-pair cosine treated as orthogonal.
            detailed: return a per-object diagnosis dict instead of a flat list.

        Returns:
            list: the offending objects, or with ``detailed=True`` a
            ``{object: {"skew": float, "cause": "inherited", "driven": [tag, ...]}}`` mapping.
            ``cause`` is always ``inherited`` in Blender — a Blender object's own transform is
            Loc·Rot·Scale and cannot hold shear, so the skew always comes from a
            non-uniformly-scaled, rotated ancestor (see the module note); Maya reports ``shear``
            there as well. ``driven`` lists what re-drives the object's transform after a fix
            would write it (``driver:``/``anim:``/``constraint:`` tags) — non-empty means
            :meth:`fix_non_orthogonal_axes` skips the object unless ``break_connections`` is set.
        """
        found = {}
        for obj in _TransformDiagnosticsInternal._resolve(objects):
            skew = _TransformDiagnosticsInternal._matrix_skew(obj.matrix_world.to_3x3())
            if skew > tolerance:
                found[obj] = {
                    "skew": skew,
                    "cause": "inherited",
                    "driven": _TransformDiagnosticsInternal._driving_connections(obj),
                }
        return found if detailed else list(found)

    @staticmethod
    @CoreUtils._object_mode
    def fix_non_orthogonal_axes(
        objects=None, dry_run=False, tolerance=1e-5, break_connections=False
    ):
        """Bake out non-orthogonal (sheared) world axes — shear breaks FBX export (mirror of
        ``mtk.TransformDiagnostics.fix_non_orthogonal_axes``). ``@_object_mode``-guarded: the
        ``parent_clear`` / ``visual_transform_apply`` ops it uses require OBJECT mode.

        **Driven transforms** (drivers or action fcurves on transform channels, unmuted
        constraints) are skipped by default with a warning: the fix writes the local transform,
        and a driver re-writes (or a constraint re-composes over) it on the next evaluation —
        the bake cannot hold. Bake/remove the animation and re-run, or pass
        ``break_connections=True`` to remove those drivers/fcurves/constraints and fix anyway
        (the analogue of Maya's opt-in disconnect).

        **Instanced (linked-data) objects need no special handling here** — unlike Maya's
        twin, which bakes shear into geometry and therefore takes an ``instance_strategy``,
        this fix writes only the object's transform channels and never touches shared data,
        so linked duplicates are fixed in place with their instancing intact.

        Args:
            objects: objects (datablocks or names) to check; ``None`` uses the current selection.
            dry_run: when True, only report which objects would be fixed (no changes).
            tolerance: max axis-pair cosine treated as orthogonal.
            break_connections: remove transform drivers/fcurves/constraints on driven objects
                instead of skipping them.

        Returns:
            list: the objects fixed (or, on ``dry_run``, the objects that *would* be fixed —
            driven objects included only when ``break_connections`` is set). The fix is
            "clear parent & keep transform", which un-parents the object and decomposes its world
            matrix to an orthogonal Loc·Rot·Scale (see the module note).
        """
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        diagnosis = TransformDiagnostics.get_non_orthogonal(
            objects, tolerance, detailed=True
        )
        flagged, skipped = [], []
        for obj, info in diagnosis.items():
            if info["driven"] and not break_connections:
                skipped.append((obj, info["driven"]))
            else:
                flagged.append(obj)
        for obj, driven in skipped:
            print(
                f"Skipping {obj.name} — transform driven by {', '.join(driven)}. "
                "Bake or remove it, or run with break_connections=True."
            )
        if dry_run:
            for o in flagged:
                print(f"Dry run: would fix non-orthogonal axes on {o.name}")
            return flagged
        if not flagged:
            return []

        prior = list(CoreUtils.selected_objects())
        prior_active = bpy.context.view_layer.objects.active
        fixed = []
        try:
            for obj in flagged:
                if break_connections:
                    _TransformDiagnosticsInternal._break_driving_connections(obj)
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
