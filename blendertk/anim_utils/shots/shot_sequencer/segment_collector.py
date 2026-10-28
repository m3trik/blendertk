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

from blendertk.anim_utils._anim_utils import AnimUtils


# Tolerance for matching shift-moved-out key times.
KEY_PROXIMITY_EPS = 0.5

_EPS = 1e-3

__all__ = ["SegmentCollector"]

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
# Channels that carry an axis: the transform properties (bare, on a pose bone,
# or their ``delta_*`` twins) plus axis-angle rotation. Anything else is a
# scalar -- ``hide_render``, a custom property -- and keys at array_index 0
# without there being an "X" to name.
_VECTOR_PATHS = tuple(_PATH_LABELS) + ("rotation_axis_angle",)
# Scalar channels that stand in for a Maya attribute of another name:
# ``hide_render`` is the deliverable visibility (what a Maya ``visibility``
# replay keys -- ``BlenderShotStore._TRANSFER_LABEL_ALIASES``), so it reads as
# Maya's channel and takes its colour.  ``hide_viewport`` is a viewport aid
# with no Maya twin and keeps its own name.
_SCALAR_LABELS = {"hide_render": "visibility"}


class SegmentCollector:
    """SegmentCollector — module namespace."""

    @staticmethod
    def label_for(data_path: str, array_index: int = -1) -> str:
        """``("location", 0)`` → ``translateX`` (mayatk-style channel label).

        The one label table, for a channel named by path rather than by a live
        fcurve (a ledger key names its channel that way); :meth:`attr_label` is
        this over an fcurve.  ``rotation_quaternion`` indexes W-first (``[0]``
        is W, not X), so it maps through :data:`_QUAT_AXES` — the shared X-first
        table mislabeled every quaternion channel by one axis.
        """
        base = _PATH_LABELS.get(data_path) or _SCALAR_LABELS.get(data_path, data_path)
        # A custom property is labelled by its name, as Maya spells the same
        # attribute (``["opacity"]`` -> ``opacity``).
        if base.startswith('["') and base.endswith('"]'):
            base = base[2:-2]
        vector = data_path.endswith(_VECTOR_PATHS)
        axes = _QUAT_AXES if data_path.endswith("rotation_quaternion") else _AXES
        if vector and 0 <= array_index < len(axes):
            return f"{base}{axes[array_index]}"
        return base

    @staticmethod
    def attr_label(fcurve) -> str:
        """``location[0]`` → ``translateX`` (mayatk-style channel label)."""
        return SegmentCollector.label_for(
            fcurve.data_path, getattr(fcurve, "array_index", -1)
        )

    @staticmethod
    def abbreviate_attrs(attrs) -> str:
        """Compact, sorted summary of channel labels (``translateX`` -> ``tx``).

        Twin of mayatk's ``SegmentCollector.abbreviate_attrs`` (its
        ``Attributes.abbreviate_attrs``) for the clip centre label: first
        character + every following uppercase letter, lowercased.
        """
        out = set()
        for a in attrs:
            if not a:
                continue
            out.add((a[0] + "".join(c for c in a[1:] if c.isupper())).lower())
        return " ".join(sorted(out))

    @staticmethod
    def collect_segments(
        sequencer, shot, visible_shots, segment_cache, shifted_out_keys, logger
    ):
        """Collect per-object animation segments for visible shots.

        Returns ``(segments_by_shot, all_objects)``.  Delegates to the engine's
        :meth:`ShotSequencer.collect_object_segments` (which owns the Blender fcurve
        walk); object-level tracks always ignore holds.  The active shot is always
        re-collected (its keys may have just moved); others are cached.
        """
        segments_by_shot: dict = {}
        all_objects: set = set()
        pinned = (
            sequencer.store.pinned_objects if sequencer and sequencer.store else set()
        )
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
                    and any(
                        abs(t - ex) < KEY_PROXIMITY_EPS for ex in shifted_out_keys[obj]
                    )
                ):
                    logger.debug(
                        "[SYNC] excluding shift-moved-out segment: obj=%s time=%s",
                        obj,
                        t,
                    )
                    continue
                filtered.append(seg)
            active_segs = filtered
            segments_by_shot[shot.shot_id] = active_segs

        logger.debug(
            "[SYNC] shot=%s range=(%s,%s) total_segments=%s objects=%s",
            shot.shot_id,
            shot.start,
            shot.end,
            len(active_segs),
            sorted(all_objects),
        )
        return segments_by_shot, all_objects

    @staticmethod
    def active_object_set(shot, segments_by_shot) -> set:
        """Objects that have actual animation segments in the active shot."""
        return {seg["obj"] for seg in segments_by_shot.get(shot.shot_id, [])}

    @staticmethod
    def extract_attributes(segments) -> list:
        """Transform-channel labels (``translateX``…) keyed within the segments.

        Reads each segment's own ``curves`` (the fcurves ``SegmentKeys`` collected
        it from — mayatk reads the animCurve→plug connections); a segment
        without a curve list falls back to the object's transform fcurves.  A
        channel counts only if it carries a key inside the segment's time range.
        """
        attrs: set = set()
        for seg in segments:
            s, e = seg.get("start"), seg.get("end")
            if s is None or e is None:
                continue
            curves = seg.get("curves")
            if not curves:
                try:
                    import bpy
                except ImportError:
                    continue
                obj = bpy.data.objects.get(seg.get("obj"))
                if obj is None:
                    continue

                curves = list(AnimUtils.get_fcurves(obj))
            for fc in curves:
                try:
                    i0, i1 = AnimUtils.window_indices(
                        AnimUtils.key_times(fc), s - _EPS, e + _EPS
                    )
                    if i1 > i0:
                        attrs.add(SegmentCollector.attr_label(fc))
                except ReferenceError:
                    continue  # fcurve died with an undo / action swap
        return sorted(attrs)

    @staticmethod
    def summary_times(segments, claimed_times) -> list:
        """Every key time an object row ticks in the flat key style.

        The segments' own ``keyframes`` (already collected upstream), minus
        the shot system's claimed bound samples: a time drops only when every
        curve of its segment that is keyed there holds it as a claimed sample.
        A claim on one channel beside an animator key on another keeps the
        tick -- the object has a real key there.

        Parameters:
            segments: Segment dicts of one object (``curves``, ``keyframes``).
            claimed_times: ``curve -> [time, ...]``, the samples the shot
                system created on that curve (the ledger).

        Returns:
            Ascending, de-duplicated times.
        """
        out: set = set()
        for seg in segments:
            curves = list(seg.get("curves") or [])
            claims = {id(c): list(claimed_times(c) or ()) for c in curves}
            for t in seg.get("keyframes") or ():
                claimers = [
                    c for c in curves if any(abs(t - h) <= 1e-3 for h in claims[id(c)])
                ]
                if not claimers or any(
                    SegmentCollector._has_key_at(c, t)
                    for c in curves
                    if all(c is not k for k in claimers)
                ):
                    out.add(float(t))
        return sorted(out)

    @staticmethod
    def _has_key_at(curve, t: float) -> bool:
        """Whether *curve* carries a key at *t* (within 1e-3 frames)."""
        try:
            i0, i1 = AnimUtils.window_indices(
                AnimUtils.key_times(curve), t - _EPS, t + _EPS
            )
        except ReferenceError:  # the fcurve died with an undo / action swap
            return False
        return i1 > i0

    @staticmethod
    def build_curve_preview(fcurve, t_start, t_end, hidden_times=()):
        """Bezier shape data for one Blender fcurve, clipped to ``[t_start, t_end]``.

        The fcurve read as a host-free ``pythontk.KeyCurve``
        (:meth:`AnimUtils.get_key_curves`, mirror of mayatk) and windowed by
        ``KeyCurve.to_preview``: the DCC-agnostic ``{keys, segments, broken,
        hidden, weighted, val_min, val_max}`` dict the shared widget painter
        renders.  A keyframe's ``handle_left`` / ``handle_right`` are the
        control points directly; an eased span (SINE/QUAD/BOUNCE...) is drawn
        through ``samples`` of the curve itself and stays a ``"linear"``
        segment (no handles to grab).  *None* when the curve has no usable
        data in range.  Keys on *hidden_times* are flagged ``hidden`` (part of
        the curve, no dot): the shot system's claimed bound samples.
        """
        if fcurve is None:
            return None
        curves = AnimUtils.get_key_curves([fcurve])
        if not curves:
            return None
        return curves[0].to_preview(t_start, t_end, hidden_times)
