# !/usr/bin/python
# coding=utf-8
"""Linear array duplication — mirror of mayatk's ``edit_utils.duplicate_linear``.

``duplicate_linear`` is pure ``mathutils`` matrix math over the shared
``ptk.ProgressionCurves`` (no temp nodes, fully headless-testable). The panel is tentacle's
shared Duplicate Linear tool (``slots/tools/duplicate_linear.py``).

``import bpy`` / ``mathutils`` are deferred into the call bodies (no import side effects).
"""

import math

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.edit_utils._edit_utils import EditUtils
from blendertk.xform_utils._xform_utils import XformUtils


class DuplicateLinear:
    """Linear-array duplication engine — mirror of mayatk's ``DuplicateLinear``.

    Reached as ``btk.DuplicateLinear.duplicate_linear`` (class-only: the module is not
    wildcard-scanned onto the flat ``btk.*`` namespace)."""

    @staticmethod
    @CoreUtils._object_mode
    def duplicate_linear(
        objects,
        num_copies,
        translate=(0, 0, 0),
        rotate=(0, 0, 0),
        scale=(1, 1, 1),
        weight_bias=0.5,
        weight_curve=4,
        pivot="object",
        calculation_mode="weighted",
        instance=True,
    ):
        """Duplicate object(s) along a linear path — mirror of mayatk's
        ``DuplicateLinear.duplicate_linear``.

        Each copy ``i`` interpolates toward the end-state ``translate`` / ``rotate`` / ``scale``
        by ``ptk.ProgressionCurves.calculate_progression_factor(i, num_copies, …)`` (the same
        shared math as Maya — the last copy gets the full offset). ``pivot`` is resolved via
        :func:`blendertk.xform_utils.get_operation_axis_matrix` (``"object"`` / ``"world"`` /
        ``"manip"`` / bbox locations / an explicit point — mirror of mayatk's
        ``XformUtils.get_operation_axis_matrix``). Returns ``{original: [copies]}``.
        """
        import bpy
        from mathutils import Euler, Matrix, Vector

        out = {}
        for src in (o for o in ptk.make_iterable(objects) if o):
            copies = []

            # Get the pivot matrix (Orientation + Position) using the centralized utility
            pivot_mat = XformUtils.get_operation_axis_matrix(src, pivot)
            pivot_inv = pivot_mat.inverted()
            m0 = src.matrix_world.copy()

            for i in range(num_copies):
                dup = EditUtils._copy_object(src, instance=instance)

                # Calculate the transformation factor using the selected method
                f = ptk.ProgressionCurves.calculate_progression_factor(
                    i, num_copies, weight_bias, weight_curve, calculation_mode
                )

                # Calculate transformations
                factors = [(abs(s) ** f) * (-1.0 if s < 0 else 1.0) for s in scale]

                # 1. Local scale
                scaled = m0 @ Matrix.Diagonal((*factors, 1.0))

                # 2. Rotate around Pivot — orbits the object around the pivot frame (Pos + Ori)
                rot = (
                    Euler([math.radians(r * f) for r in rotate], "XYZ")
                    .to_matrix()
                    .to_4x4()
                )
                orbit = pivot_mat @ rot @ pivot_inv

                # 3. Apply Translation (World Space, but respecting Pivot Orientation) — rotate
                # the translation vector so it follows the pivot's axes (e.g. Manip axis).
                t = pivot_mat.to_3x3() @ Vector([v * f for v in translate])

                dup.matrix_world = Matrix.Translation(t) @ orbit @ scaled
                copies.append(dup)

            out[src] = copies
        bpy.context.view_layer.update()
        return out


# ----------------------------------------------------------------------------
# UI slots
# ----------------------------------------------------------------------------


# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
