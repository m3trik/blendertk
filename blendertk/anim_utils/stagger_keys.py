# !/usr/bin/python
# coding=utf-8
"""Dedicated stagger-keys module to keep AnimUtils lean and testable (mirror of mayatk's
``anim_utils.stagger_keys`` / ``StaggerKeys``).

The shared fcurve helpers live in ``_anim_utils``; they are imported lazily inside the call body
to avoid an import cycle (``_anim_utils`` re-imports ``stagger_keys`` so ``AnimUtils.stagger_keys``
/ ``btk.stagger_keys`` keep resolving).
"""


def _group_units(units, merge_touching):
    """Group units whose key ranges overlap (or merely touch, when ``merge_touching``) into
    blocks. Units are swept in start-frame order; chained overlaps fold into one block."""
    blocks = []
    for u in sorted(units, key=lambda u: u["start"]):
        if blocks:
            last_end = max(x["end"] for x in blocks[-1])
            joins = u["start"] <= last_end if merge_touching else u["start"] < last_end
            if joins:
                blocks[-1].append(u)
                continue
        blocks.append([u])
    return blocks


def stagger_keys(
    objects,
    start_frame=None,
    spacing=5,
    use_intervals=False,
    invert=False,
    group_overlapping=False,
    merge_touching=False,
    smooth_tangents=False,
):
    """Re-time selected objects so their animations play one after another (mirror of ``mtk``
    stagger-keys).

    * ``spacing`` — frames between one block's end and the next block's start (sequential mode),
      or the fixed frame interval between block starts when ``use_intervals``.
    * ``start_frame`` — where the first block's start lands (default: it stays put).
    * ``invert`` — reverse the block order.
    * ``group_overlapping`` — objects with overlapping key ranges re-time together as one block
      (relative timing within the block is preserved); ``merge_touching`` also joins blocks whose
      ranges merely touch (end == start).
    * ``smooth_tangents`` — set auto-clamped bezier handles on the re-timed keys.

    Returns the number of objects (actions) staggered."""
    from blendertk.anim_utils._anim_utils import _actions, _slot_fcurves, _key_range, _shift_fcurves

    units = []
    for action, slot in _actions(objects):
        fcurves = _slot_fcurves(action, slot)
        rng = _key_range(fcurves)
        if rng:
            units.append({"fcurves": fcurves, "start": rng[0], "end": rng[1]})
    if not units:
        return 0

    blocks = _group_units(units, merge_touching) if group_overlapping else [[u] for u in units]
    if invert:
        blocks.reverse()

    def block_bounds(block):
        return min(u["start"] for u in block), max(u["end"] for u in block)

    origin = start_frame if start_frame is not None else block_bounds(blocks[0])[0]
    cursor = origin
    for i, block in enumerate(blocks):
        b_start, b_end = block_bounds(block)
        target_start = origin + i * spacing if use_intervals else cursor
        offset = target_start - b_start
        if offset:
            for u in block:
                _shift_fcurves(u["fcurves"], offset)
        cursor = target_start + (b_end - b_start) + spacing

    if smooth_tangents:
        for u in units:
            for fc in u["fcurves"]:
                for k in fc.keyframe_points:
                    k.handle_left_type = k.handle_right_type = "AUTO_CLAMPED"
                fc.update()

    return len(units)


class StaggerKeys:
    """Namespace mirror of mayatk's ``StaggerKeys`` (``stagger_keys`` also exposed module-level)."""

    stagger_keys = staticmethod(stagger_keys)
