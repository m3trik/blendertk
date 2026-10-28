# !/usr/bin/python
# coding=utf-8
"""Dedicated stagger-keys module to keep AnimUtils lean and testable (mirror of mayatk's
``anim_utils.stagger_keys`` / ``StaggerKeys``).

The shared fcurve helpers live in ``_anim_utils``; they are imported lazily inside the call body
to avoid an import cycle (``_anim_utils`` re-imports ``stagger_keys`` so ``AnimUtils.stagger_keys``
/ ``btk.stagger_keys`` keep resolving).
"""

import pythontk as ptk

# Eager: ``CoreUtils.undoable`` decorates ``stagger_keys`` while the class body runs.
from blendertk.core_utils._core_utils import CoreUtils


class _StaggerKeysInternal(object):
    """Internal helpers for StaggerKeys."""

    @staticmethod
    def _group_units(units, merge_touching):
        """Group units whose key ranges overlap (or merely touch, when ``merge_touching``) into
        blocks. Units are swept in start-frame order; chained overlaps fold into one block
        (``ptk.ShotDetection.cluster_spans``, the ecosystem's one key-timing grouping)."""
        return ptk.ShotDetection.cluster_spans(units, inclusive=bool(merge_touching))


class StaggerKeys(_StaggerKeysInternal):
    """Namespace mirror of mayatk's ``StaggerKeys`` (``stagger_keys`` also exposed module-level)."""

    @staticmethod
    @CoreUtils.undoable
    def stagger_keys(
        objects,
        start_frame=None,
        spacing=5,
        use_intervals=False,
        invert=False,
        group_overlapping=False,
        merge_touching=False,
        smooth_tangents=False,
        on_replace=None,
        on_move=None,
        # mayatk's, accepted for parity: a Blender block moves whole (so blocks never
        # overlap), there is no Channel Box, and nothing is printed.
        avoid_overlap=False,
        channel_box_attrs_only=False,
        verbose=False,
    ):
        """Re-time selected objects so their animations play one after another (mirror of ``mtk``
        stagger-keys).

        * ``spacing`` — frames between one block's end and the next block's start (sequential mode),
          or the fixed frame interval between block starts when ``use_intervals``. In sequential
          mode a value strictly between -1 and 1 is a FRACTION of each block's own duration
          (``0.5`` a half-length gap, ``-0.3`` a 30% overlap), as mayatk reads it.
        * ``start_frame`` — where the first block's start lands (default: the earliest
          key, so without ``invert`` the earliest block stays put).
        * ``invert`` — reverse the block order. Blocks are laid out in start-time
          order, never selection order; ``invert`` runs them latest first.
        * ``group_overlapping`` — objects with overlapping key ranges re-time together as one block
          (relative timing within the block is preserved); ``merge_touching`` also joins blocks whose
          ranges merely touch (end == start), and turns grouping on (mayatk's rule).
        * ``smooth_tangents`` — set auto-clamped bezier handles on the re-timed keys.
        * ``on_move(fcurve, pairs)`` — hears the ``(old, new)`` frames of every key re-timed,
          per fcurve (what the shot system carries its claims along on).
        * ``on_replace`` — accepted for parity with mayatk's (whose split curves' segments can
          land on one another); never called here: a unit moves whole, so no key is landed on.

        Returns the number of objects (actions) staggered."""
        from blendertk.anim_utils._anim_utils import AnimUtils

        if merge_touching:  # mayatk's rule: touching implies grouping
            group_overlapping = True
        units = []
        for action, slot in AnimUtils._actions(objects):
            fcurves = AnimUtils._slot_fcurves(action, slot)
            rng = AnimUtils._key_range(fcurves)
            if rng:
                units.append({"fcurves": fcurves, "start": rng[0], "end": rng[1]})
        if not units:
            return 0

        # Blocks run in TIME order whatever the selection order (stable, so equal
        # starts keep it), reversed by Invert, and the run starts at the EARLIEST
        # key -- not at the first-placed block's own start, which under Invert is
        # the latest one (mayatk's layout).
        blocks = (
            _StaggerKeysInternal._group_units(units, merge_touching)
            if group_overlapping
            else sorted(([u] for u in units), key=lambda b: b[0]["start"])
        )
        if invert:
            blocks.reverse()

        def block_bounds(block):
            return min(u["start"] for u in block), max(u["end"] for u in block)

        origin = (
            start_frame if start_frame is not None else min(u["start"] for u in units)
        )
        cursor = origin
        for i, block in enumerate(blocks):
            b_start, b_end = block_bounds(block)
            target_start = origin + i * spacing if use_intervals else cursor
            offset = target_start - b_start
            if offset:
                for u in block:
                    moved = (
                        {
                            fc: [(t, t + offset) for t in AnimUtils.key_times(fc)]
                            for fc in u["fcurves"]
                        }
                        if on_move is not None
                        else {}
                    )
                    AnimUtils._shift_fcurves(u["fcurves"], offset)
                    for fc, pairs in moved.items():
                        AnimUtils._report_edit(fc, [], pairs, None, on_move)
            gap = spacing * (b_end - b_start) if 0 < abs(spacing) < 1 else spacing
            cursor = target_start + (b_end - b_start) + gap

        if smooth_tangents:
            for u in units:
                for fc in u["fcurves"]:
                    for k in fc.keyframe_points:
                        k.handle_left_type = k.handle_right_type = "AUTO_CLAMPED"
                    fc.update()

        return len(units)
