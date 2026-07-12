# !/usr/bin/python
# coding=utf-8
"""Segment collection and attribute extraction for the shot sequencer (Blender).

Blender mirror of mayatk's ``shot_sequencer.segment_collector`` — pure functions
the controller calls directly.  ``collect_segments`` / ``active_object_set`` are
1:1 (they delegate to the engine's ``collect_object_segments``); ``extract_attributes``
and ``build_curve_preview`` read Blender fcurves instead of Maya animCurve nodes.

``build_curve_preview`` is actually *simpler* here than in Maya: a Blender fcurve
keyframe already stores its bezier handles in (frame, value) space, so the control
points are the handles directly — no weighted/1-third-span reconstruction from
tangent angles is needed.
"""
from __future__ import annotations

from blendertk.anim_utils.shots._shots import iter_action_fcurves, _is_transform_path

# Tolerance for matching shift-moved-out key times.
KEY_PROXIMITY_EPS = 0.5

_EPS = 1e-3

__all__ = [
    "collect_segments",
    "active_object_set",
    "extract_attributes",
    "build_curve_preview",
    "attr_label",
]

# Readable per-channel labels, mirroring mayatk's ``translateX`` style so the
# shared widget's sub-rows read the same across DCCs.  Quaternions get their
# own base label ("quatRotate") — mapping them onto "rotate" collided with the
# euler labels, so a sub-row edit through ``curves_for_attr`` would move BOTH
# rotation families at once.
_PATH_LABELS = {
    "location": "translate",
    "rotation_euler": "rotate",
    "rotation_quaternion": "quatRotate",
    "scale": "scale",
}
_AXES = ("X", "Y", "Z")
_QUAT_AXES = ("W", "X", "Y", "Z")  # Blender stores quaternions W-first


def attr_label(fcurve) -> str:
    """``location[0]`` → ``translateX`` (mayatk-style channel label).

    ``rotation_quaternion`` indexes W-first (``[0]`` is W, not X), so it maps
    through :data:`_QUAT_AXES` — the shared X-first table mislabeled every
    quaternion channel by one axis.
    """
    path = fcurve.data_path
    base = _PATH_LABELS.get(path, path)
    idx = getattr(fcurve, "array_index", -1)
    axes = _QUAT_AXES if path.endswith("rotation_quaternion") else _AXES
    if 0 <= idx < len(axes):
        return f"{base}{axes[idx]}"
    return base


def collect_segments(sequencer, shot, visible_shots, segment_cache, shifted_out_keys, logger):
    """Collect per-object animation segments for visible shots.

    Returns ``(segments_by_shot, all_objects)``.  Delegates to the engine's
    :meth:`ShotSequencer.collect_object_segments` (which owns the Blender fcurve
    walk); object-level tracks always ignore holds.  The active shot is always
    re-collected (its keys may have just moved); others are cached.
    """
    segments_by_shot: dict = {}
    all_objects: set = set()
    pinned = sequencer.store.pinned_objects if sequencer and sequencer.store else set()
    for vs in visible_shots:
        is_active_shot = vs.shot_id == shot.shot_id
        if is_active_shot or vs.shot_id not in segment_cache:
            segs = sequencer.collect_object_segments(vs.shot_id, ignore_holds=True)
            segment_cache[vs.shot_id] = segs
        else:
            segs = segment_cache[vs.shot_id]
        segments_by_shot[vs.shot_id] = segs
        all_objects.update(seg["obj"] for seg in segs)
        all_objects.update(o for o in vs.objects if o in pinned)

    active_segs = segments_by_shot.get(shot.shot_id, [])

    # Drop segments for keys that were shift-moved out of this shot.
    if shifted_out_keys:
        filtered = []
        for seg in active_segs:
            obj = seg.get("obj")
            t = seg.get("start")
            if (
                obj in shifted_out_keys
                and t is not None
                and any(abs(t - ex) < KEY_PROXIMITY_EPS for ex in shifted_out_keys[obj])
            ):
                logger.debug("[SYNC] excluding shift-moved-out segment: obj=%s time=%s", obj, t)
                continue
            filtered.append(seg)
        active_segs = filtered
        segments_by_shot[shot.shot_id] = active_segs

    logger.debug(
        "[SYNC] shot=%s range=(%s,%s) total_segments=%s objects=%s",
        shot.shot_id, shot.start, shot.end, len(active_segs), sorted(all_objects),
    )
    return segments_by_shot, all_objects


def active_object_set(shot, segments_by_shot) -> set:
    """Objects that have actual animation segments in the active shot."""
    return {seg["obj"] for seg in segments_by_shot.get(shot.shot_id, [])}


def extract_attributes(segments) -> list:
    """Transform-channel labels (``translateX``…) keyed within the segments.

    Blender reads the object's transform fcurves directly (mayatk read the
    animCurve→plug connections); a channel counts only if it carries a key
    inside a segment's time range.
    """
    try:
        import bpy
    except ImportError:
        return []
    attrs: set = set()
    for seg in segments:
        # bpy.data.objects (not scene.objects) — consistent with the rest of
        # the sequencer stack; an object not linked to the active scene must
        # still contribute its attribute chips.
        obj = bpy.data.objects.get(seg.get("obj"))
        if obj is None:
            continue
        s, e = seg.get("start"), seg.get("end")
        if s is None or e is None:
            continue
        for fc in iter_action_fcurves(obj):
            if not _is_transform_path(fc.data_path):
                continue
            if any(s - _EPS <= kp.co[0] <= e + _EPS for kp in fc.keyframe_points):
                attrs.add(attr_label(fc))
    return sorted(attrs)


def build_curve_preview(fcurve, t_start, t_end):
    """Bézier shape data for one Blender fcurve, clipped to ``[t_start, t_end]``.

    Returns the DCC-agnostic ``{keys, segments, val_min, val_max}`` dict the shared
    widget painter renders with ``QPainterPath.cubicTo`` / ``lineTo`` — identical
    shape to mayatk's, so the painter is unchanged.  A Blender keyframe's
    ``handle_right`` / ``handle_left`` are the bezier control points directly (in
    frame/value space), so no angle+weight reconstruction is needed.  *None* when
    the curve has no usable data in range.
    """
    if fcurve is None:
        return None
    kps = list(getattr(fcurve, "keyframe_points", []) or [])
    if not kps:
        return None
    times = [kp.co[0] for kp in kps]
    n = len(kps)

    # Visible key indices, plus one bounding key each side for edge segments.
    first_vis = last_vis = None
    for i, t in enumerate(times):
        if t_start - 0.001 <= t <= t_end + 0.001:
            if first_vis is None:
                first_vis = i
            last_vis = i
    if first_vis is None:
        before = [i for i, t in enumerate(times) if t < t_start]
        after = [i for i, t in enumerate(times) if t > t_end]
        if not before or not after:
            return None
        first_vis, last_vis = before[-1], after[0]
    else:
        if first_vis > 0:
            first_vis -= 1
        if last_vis < n - 1:
            last_vis += 1

    vis_keys = []
    vis_segs = []
    all_vals = []
    for i in range(first_vis, last_vis + 1):
        kp = kps[i]
        vis_keys.append((kp.co[0], kp.co[1]))
        all_vals.append(kp.co[1])

    for i in range(first_vis, last_vis):
        k0, k1 = kps[i], kps[i + 1]
        t0, v0 = k0.co[0], k0.co[1]
        t1, v1 = k1.co[0], k1.co[1]
        interp = getattr(k0, "interpolation", "BEZIER")
        # Only true BEZIER keys have meaningful handles; the easing modes
        # (SINE/QUAD/BOUNCE/…) don't evaluate through them, so drawing their
        # handles as control points renders a wrong curve — degrade those to a
        # straight preview segment instead.
        out_type = {"CONSTANT": "step", "LINEAR": "linear", "BEZIER": "bezier"}.get(
            interp, "linear"
        )
        cp1 = cp2 = None
        if out_type == "bezier":
            cp1 = (k0.handle_right[0], k0.handle_right[1])
            cp2 = (k1.handle_left[0], k1.handle_left[1])
            all_vals.extend([cp1[1], cp2[1]])
        vis_segs.append({
            "t0": t0, "v0": v0, "t1": t1, "v1": v1,
            "out_type": out_type, "cp1": cp1, "cp2": cp2,
        })

    if not vis_keys:
        return None
    return {
        "keys": vis_keys,
        "segments": vis_segs,
        "val_min": min(all_vals),
        "val_max": max(all_vals),
    }
