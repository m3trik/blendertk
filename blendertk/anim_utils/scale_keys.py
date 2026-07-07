# !/usr/bin/python
# coding=utf-8
"""Dedicated scale-keys module to keep AnimUtils lean and testable (mirror of mayatk's
``anim_utils.scale_keys`` / ``ScaleKeys``).

Mirrors mayatk's ``scale_keys`` at the *name + behavior* level (uniform vs. speed-normalized
retiming, single/per-object/overlap-group pivots, absolute vs. relative factor, split-static
segmentation, post-scale snapping) — not at the signature level, and not at the
implementation-complexity level. mayatk's version leans on a dedicated ``SegmentKeys`` grouper,
per-item tangent flattening and an overlap-prevention re-stagger pass because Maya's ``cmds``
API has no batch fcurve primitives; Blender's ``keyframe_points`` are plain, directly-addressable
arrays, so the same *result* (independent segments/objects/overlap-groups each scaled around
their own pivot, speed mode retiming to a target units/frame) is reachable with far less
machinery. The shared fcurve helpers live in ``_anim_utils``; imported lazily inside the call
body to avoid an import cycle (``_anim_utils`` re-imports ``scale_keys`` so
``AnimUtils.scale_keys`` / ``btk.scale_keys`` keep resolving).

Divergence from mayatk (by design):

* No tangent-flattening pass and no post-scale overlap-prevention re-stagger — mayatk applies
  both unconditionally (not user-facing toggles in its own option box either), Blender's bezier
  handles are scaled in lock-step with their key so they don't need flattening, and overlap
  prevention is exactly what ``chk_merge_touching`` / ``group_mode="overlap_groups"`` already
  give the caller control over.
* Speed mode's per-group scale factor is a single combined value (total sampled motion distance
  and total duration summed across every block in the group) rather than mayatk's per-item
  ratio-then-take-the-max reconciliation — same intent (retime the group to the target speed),
  simpler math.
"""
import pythontk as ptk


def _static_segments(fcurves, tolerance=1e-6):
    """Split a unit's overall key range into sub-ranges separated by a static hold — a gap
    between two adjacent key times where every fcurve's evaluated value doesn't change (nothing
    moves). The Blender equivalent of mayatk's ``split_static`` segmentation: an object with
    several animated "clips" separated by flat holds scales each clip independently instead of
    as one block."""
    times = sorted({k.co.x for fc in fcurves for k in fc.keyframe_points})
    if len(times) < 2:
        return [(times[0], times[0])] if times else []
    segments = []
    seg_start = times[0]
    for t1, t2 in zip(times, times[1:]):
        if all(abs(fc.evaluate(t1) - fc.evaluate(t2)) <= tolerance for fc in fcurves):
            if t1 > seg_start:
                segments.append((seg_start, t1))
            seg_start = t2
    segments.append((seg_start, times[-1]))
    return [(s, e) for s, e in segments if e > s] or [(times[0], times[-1])]


def _sample_motion_distance(obj, start, end, samples=64, include_rotation=False):
    """Sample ``obj``'s world-space motion across ``[start, end]`` and return the total distance
    traveled — the Blender analogue of mayatk's ``_compute_motion_progress`` (used only for its
    ``total_distance``; see the module docstring). ``include_rotation`` mirrors mayatk's tri-state:
    ``False`` = translation only, ``True`` = translation + rotation (per-step max of the two,
    matching mayatk), ``"only"`` = rotation only. Rotation is measured in degrees (mayatk's
    "1 degree ~= 1 translation unit" heuristic)."""
    import math

    import bpy

    if end <= start:
        return 0.0
    samples = max(3, int(samples))
    span = end - start
    times = [start + span * i / (samples - 1) for i in range(samples)]

    scene = bpy.context.scene
    orig_frame = scene.frame_current
    positions = []
    rotations = []
    try:
        for t in times:
            scene.frame_set(int(t), subframe=t - int(t))
            positions.append(obj.matrix_world.translation.copy())
            if include_rotation:
                e = obj.matrix_world.to_euler()
                rotations.append((math.degrees(e.x), math.degrees(e.y), math.degrees(e.z)))
    finally:
        scene.frame_set(orig_frame)

    total = 0.0
    for i in range(1, len(positions)):
        dist_trans = 0.0 if include_rotation == "only" else (positions[i] - positions[i - 1]).length
        dist_rot = 0.0
        if include_rotation and rotations:
            r1, r2 = rotations[i - 1], rotations[i]
            dist_rot = math.sqrt(sum((b - a) ** 2 for a, b in zip(r1, r2)))
        total += max(dist_trans, dist_rot)
    return total


def scale_keys(
    objects,
    factor,
    pivot=None,
    mode="uniform",
    absolute=False,
    group_mode="single_group",
    snap_mode="none",
    samples=64,
    include_rotation=False,
    split_static=True,
    merge_touching=False,
):
    """Scale (retime) keyframes uniformly or via motion-aware speed normalization — mirror of
    ``mtk.scale_keys``/``ScaleKeys``.

    * ``mode`` — ``"uniform"`` (default): ``factor`` is a plain time multiplier (or, with
      ``absolute``, a target duration in frames). ``"speed"``: ``factor`` is a speed multiplier
      (or, with ``absolute``, a target speed in units/frame); the actual scale factor is derived
      from each block's sampled world-space motion distance (``samples``, ``include_rotation`` —
      ``False``/``True``/``"only"``, mirroring mayatk's translation/both/rotation-only modes).
    * ``absolute`` — uniform: ``factor`` is the target duration in frames instead of a multiplier.
      speed: ``factor`` is the target speed (units/frame) instead of a multiplier of the block's
      current speed.
    * ``group_mode`` — which keys share one pivot/factor: ``"single_group"`` (default, the whole
      selection), ``"per_object"`` (each object — or, with ``split_static``, each segment — gets
      its own pivot/range), ``"overlap_groups"`` (objects/segments with overlapping key ranges
      share a group pivot; reuses :func:`blendertk.anim_utils.stagger_keys._group_units`).
    * ``split_static`` — (default True) break each object's key range into independent segments at
      static holds (see :func:`_static_segments`) before grouping/scaling.
    * ``merge_touching`` — with ``group_mode="overlap_groups"``, also merge blocks whose ranges
      merely touch (end == start); reuses ``stagger_keys``'s same option.
    * ``pivot`` — explicit pivot frame; overrides the per-group/per-object auto pivot (each
      group's/object's own range start) for every block.
    * ``snap_mode`` — post-scale key rounding, composed via :func:`snap_keys`: ``"nearest"``,
      ``"floor"``, ``"ceil"``, ``"half_up"``, ``"preferred"``, ``"aggressive_preferred"``, or
      ``"none"`` (default — no snapping).

    Returns the number of keys scaled (0 if nothing matched or ``factor``/computed motion was
    invalid)."""
    from blendertk.anim_utils._anim_utils import _slot_fcurves, snap_keys
    from blendertk.anim_utils.stagger_keys import _group_units

    if factor is None or factor <= 0:
        return 0

    # One unit per unique action across the given objects (dedupes objects that share an
    # action), each carrying a representative object for speed-mode motion sampling.
    units = []
    seen_actions = []
    for o in ptk.make_iterable(objects):
        ad = getattr(o, "animation_data", None)
        action = ad.action if ad else None
        if action is None or any(action is a for a, _o in seen_actions):
            continue
        seen_actions.append((action, o))
        fcurves = _slot_fcurves(action, getattr(ad, "action_slot", None))
        times = [k.co.x for fc in fcurves for k in fc.keyframe_points]
        if times:
            units.append({"object": o, "fcurves": fcurves, "start": min(times), "end": max(times)})
    if not units:
        return 0

    blocks = []
    for u in units:
        segs = _static_segments(u["fcurves"]) if split_static else [(u["start"], u["end"])]
        for s, e in segs:
            blocks.append({"fcurves": u["fcurves"], "object": u["object"], "start": s, "end": e})

    if group_mode == "single_group":
        groups = [blocks]
    elif group_mode == "overlap_groups":
        groups = _group_units(blocks, merge_touching)
    else:  # "per_object" (also the fallback for an unrecognized group_mode)
        groups = [[b] for b in blocks]

    keys_scaled = 0
    for group in groups:
        g_start = min(b["start"] for b in group)
        g_end = max(b["end"] for b in group)
        duration = g_end - g_start

        if mode == "speed":
            total_distance = sum(
                _sample_motion_distance(b["object"], b["start"], b["end"], samples, include_rotation)
                for b in group
            )
            if total_distance <= 1e-6 or duration <= 1e-6:
                continue
            current_speed = total_distance / duration
            target_speed = factor if absolute else current_speed * factor
            if target_speed <= 0:
                continue
            block_factor = (total_distance / target_speed) / duration
        elif absolute:
            if duration <= 1e-6:
                continue
            block_factor = factor / duration
        else:
            block_factor = factor

        if block_factor <= 0 or abs(block_factor - 1.0) < 1e-9:
            continue

        p = g_start if pivot is None else pivot
        touched = set()
        for b in group:
            for fc in b["fcurves"]:
                for k in fc.keyframe_points:
                    if b["start"] - 1e-6 <= k.co.x <= b["end"] + 1e-6:
                        k.co.x = p + (k.co.x - p) * block_factor
                        k.handle_left.x = p + (k.handle_left.x - p) * block_factor
                        k.handle_right.x = p + (k.handle_right.x - p) * block_factor
                        keys_scaled += 1
                        touched.add(fc)
        for fc in touched:
            fc.update()

    if keys_scaled and snap_mode and snap_mode != "none":
        snap_keys([u["object"] for u in units], method=snap_mode)

    return keys_scaled


class ScaleKeys:
    """Namespace mirror of mayatk's ``ScaleKeys`` (``scale_keys`` also exposed module-level)."""

    scale_keys = staticmethod(scale_keys)
