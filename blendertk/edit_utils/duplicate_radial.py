# !/usr/bin/python
# coding=utf-8
"""Radial array duplication — mirror of mayatk's ``edit_utils.duplicate_radial``.

``duplicate_radial`` orbits copies about a pivot via ``mathutils`` matrices (no temp groups).
Its panel is tentacle's (``ui/panels/duplicate_radial``), shared with Maya.

``import bpy`` / ``mathutils`` are deferred into the call bodies (no import side effects).
"""

import math

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.edit_utils._edit_utils import EditUtils, _AXIS_INDEX
from blendertk.edit_utils.naming._naming import Naming


class _DuplicateRadialInternal(object):
    """Internal helpers for DuplicateRadial."""

    @staticmethod
    def _radial_factor(x, weight_bias, weight_curve):
        """Maya's radial weighting: lerp between linear and a bias-curved progression."""
        curve = min(max(weight_curve, 0.0), 0.999)  # 1.0 would divide by zero
        weight_factor = 2 * abs(weight_bias - 0.5)
        curve_value = (
            x ** (1 / (1 - curve))
            if weight_bias >= 0.5
            else 1 - (1 - x) ** (1 / (1 - curve))
        )
        return (1 - weight_factor) * x + weight_factor * curve_value


class DuplicateRadial(_DuplicateRadialInternal):
    """Radial-array duplication engine — mirror of mayatk's ``DuplicateRadial``.

    Reached as ``btk.DuplicateRadial.duplicate_radial`` (class-only: the module is not
    wildcard-scanned onto the flat ``btk.*`` namespace)."""

    @staticmethod
    @CoreUtils._object_mode
    def duplicate_radial(
        objects,
        num_copies,
        start_angle=0.0,
        end_angle=360.0,
        weight_bias=0.5,
        weight_curve=0.5,
        rotate_axis="y",
        offset=(0, 0, 0),
        translate=(0, 0, 0),
        rotate=(0, 0, 0),
        scale=(1, 1, 1),
        pivot="object",
        keep_original=False,
        instance=False,
        combine=False,
        suffix=True,
    ):
        """Duplicate object(s) in a radial pattern — mirror of mayatk's
        ``DuplicateRadial.duplicate_radial``.

        Copies orbit the pivot point (``"object"`` = the source's origin, ``"world"``, or a
        world ``(x, y, z)`` point; ``offset`` shifts it) about the **world** ``rotate_axis``.
        A full 360° sweep drops the shared endpoint so the last copy doesn't stack on the
        first. The per-copy ``rotate`` / ``scale`` are applied in the source's local frame and
        ``translate`` ramps in world space, like Maya. ``combine`` joins the copies into one
        mesh; otherwise they're grouped under an Empty (``<name>_array``). ``keep_original``
        ``False`` removes the source once the pattern is built. ``suffix`` renames the finished
        copy/copies with a location-based suffix (``Naming.append_location_based_suffix``,
        alphabetical, ordered by distance from the source's origin), mirroring Maya. Returns
        ``{name: [copies]}``.
        """
        import bpy
        from mathutils import Euler, Matrix, Vector

        if rotate_axis not in ("x", "y", "z"):
            raise ValueError("Invalid rotation axis, expected 'x', 'y', or 'z'")
        axis_vec = Vector((0.0, 0.0, 0.0))
        axis_vec[_AXIS_INDEX[rotate_axis]] = 1.0

        total_rotation = end_angle - start_angle
        remainder = abs(total_rotation) % 360.0
        is_full_revolution = (
            abs(total_rotation) > 1e-6 and min(remainder, 360.0 - remainder) < 1e-6
        )
        span_divisor = num_copies if is_full_revolution else max(num_copies - 1, 1)

        out = {}
        for src in (o for o in ptk.make_iterable(objects) if o):
            # the "driven" base transform: the uniform per-copy rotate/scale in the local frame
            # (Maya's initial-transformations step; translate ramps per copy instead — Maya also
            # pre-shifts every copy by the full translate, a double-application quirk not mirrored)
            rot = Euler([math.radians(r) for r in rotate], "XYZ").to_matrix().to_4x4()
            scl = Matrix.Diagonal((*scale, 1.0))
            base = src.matrix_world @ rot @ scl

            if pivot == "world":
                pivot_pos = Vector((0.0, 0.0, 0.0))
            elif isinstance(pivot, (tuple, list)) and len(pivot) == 3:
                pivot_pos = Vector(pivot)
            else:  # "object"
                pivot_pos = src.matrix_world.translation.copy()
            pivot_pos = pivot_pos + Vector(offset)

            copies = []
            for i in range(num_copies):
                x = i / span_divisor if num_copies > 1 else 0.0
                f = _DuplicateRadialInternal._radial_factor(
                    x, weight_bias, weight_curve
                )
                angle = math.radians(start_angle + total_rotation * f)
                orbit = (
                    Matrix.Translation(pivot_pos)
                    @ Matrix.Rotation(angle, 4, axis_vec)
                    @ Matrix.Translation(-pivot_pos)
                )
                dup = EditUtils._copy_object(src, instance=instance)
                t = Vector([v * f for v in translate])
                dup.matrix_world = Matrix.Translation(t) @ orbit @ base
                copies.append(dup)

            name = src.name
            if not copies:  # num_copies <= 0: no-op, don't destroy the source
                out[name] = []
                continue
            if not keep_original:
                data = src.data
                bpy.data.objects.remove(src, do_unlink=True)
                if (
                    data is not None and data.users == 0
                ):  # don't leak an orphaned datablock
                    bpy.data.batch_remove([data])
            if combine:
                copies = [EditUtils._join_copies(copies, f"{name}_array")]
            else:
                EditUtils._group_under_empty(copies, f"{name}_array")
            if suffix:
                Naming.append_location_based_suffix(
                    copies, first_obj_as_ref=True, alphabetical=True
                )
            out[name] = copies
        bpy.context.view_layer.update()
        return out

    @staticmethod
    def regroup_copies(copies):
        """Mirror of mayatk's ``DuplicateRadial.regroup_copies``, the finalize step after a
        committed :meth:`duplicate_radial`. Nothing to do here: that already groups each
        source's copies under one ``<name>_array`` Empty (or joins them, with ``combine``).
        """
