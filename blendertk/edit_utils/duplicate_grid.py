# !/usr/bin/python
# coding=utf-8
"""Grid array duplication + its tool panel — mirror of mayatk's ``edit_utils.duplicate_grid``.

``duplicate_grid`` lays copies out in a 3D grid via ``mathutils`` translation matrices (the
source keeps the origin cell); ``DuplicateGridSlots`` is the Switchboard wiring for the
co-located ``duplicate_grid.ui`` panel, discovered + served by ``BlenderUiHandler``
(``marking_menu.show("duplicate_grid")``).

``import bpy`` / ``mathutils`` (and the Qt-only ``uitk`` helpers) are deferred into the call
bodies (no import side effects; headless Blender ships no Qt binding).
"""

import itertools

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.edit_utils._edit_utils import EditUtils


# Maya prompts before huge grids; headless we also hard-cap (a 50³ drag would hang) — the
# slots' BULK_THRESHOLD confirmation below sits well under this, so the cap is a last-resort
# backstop for a build the user already confirmed (or a scripted call with no UI at all).
GRID_MAX_COPIES = 10000


class DuplicateGrid:
    """Grid-array duplication engine — mirror of mayatk's ``DuplicateGrid``.

    Reached as ``btk.DuplicateGrid.duplicate_grid`` (class-only; the co-located
    ``DuplicateGridSlots`` panel is discovered by ``BlenderUiHandler``, so the module is not
    wildcard-scanned onto the flat ``btk.*`` namespace)."""

    @staticmethod
    def _normalize_spacing(spacing):
        """Coerce a scalar or per-axis spacing into an ``(sx, sy, sz)`` tuple —
        mirror of mayatk's ``DuplicateGrid._normalize_spacing``. A plain number
        applies uniformly to all three axes (the historical contract); any
        3-element sequence is used verbatim for per-axis gaps."""
        if isinstance(spacing, (int, float)):
            return float(spacing), float(spacing), float(spacing)
        sx, sy, sz = spacing
        return float(sx), float(sy), float(sz)

    @staticmethod
    @CoreUtils._object_mode
    def duplicate_grid(objects, dimensions=(2, 2, 1), spacing=0.0, mode="instance"):
        """Duplicate object(s) into a 3D grid — mirror of mayatk's ``DuplicateGrid.duplicate_grid``.

        ``dimensions`` = per-axis cell counts (negative counts lay the grid out in the opposite
        direction); the step per axis is the source's world-bbox size plus ``spacing``. The
        source object itself occupies the ``(0, 0, 0)`` cell and is never mutated — copies fill
        the remaining cells. ``spacing`` is a single float (uniform) or a ``(sx, sy, sz)`` tuple
        for a per-axis gap. ``mode``: ``"instance"`` (linked duplicates) / ``"copy"`` /
        ``"combine"`` (one joined mesh). Instance/copy results are grouped under an Empty
        (``<name>_grid``). Returns ``{original: [copies]}``.
        """
        import bpy
        from mathutils import Matrix

        from blendertk.xform_utils._xform_utils import XformUtils

        counts = [max(abs(int(d)), 1) for d in dimensions]
        total = counts[0] * counts[1] * counts[2] - 1
        if total > GRID_MAX_COPIES:
            raise ValueError(
                f"Grid of {total} copies exceeds the {GRID_MAX_COPIES} cap — reduce the counts."
            )
        signs = [-1.0 if d < 0 else 1.0 for d in dimensions]
        space = DuplicateGrid._normalize_spacing(spacing)

        out = {}
        for src in (o for o in ptk.make_iterable(objects) if o):
            mn, mx = XformUtils.get_world_bbox(src)
            steps = [(mx[i] - mn[i] + space[i]) * signs[i] for i in range(3)]
            m0 = src.matrix_world.copy()
            copies = []
            for ix, iy, iz in itertools.product(*(range(c) for c in counts)):
                if (ix, iy, iz) == (0, 0, 0):
                    continue  # the source occupies the origin cell
                dup = EditUtils._copy_object(src, instance=(mode == "instance"))
                dup.matrix_world = (
                    Matrix.Translation((ix * steps[0], iy * steps[1], iz * steps[2]))
                    @ m0
                )
                copies.append(dup)
            if not copies:
                out[src] = []
                continue
            if mode == "combine":
                copies = [EditUtils._join_copies(copies, f"{src.name}_grid")]
            else:
                EditUtils._group_under_empty(copies, f"{src.name}_grid")
            out[src] = copies
        bpy.context.view_layer.update()
        return out


# ----------------------------------------------------------------------------
# UI slots
# ----------------------------------------------------------------------------


# -----------------------------------------------------------------------------

