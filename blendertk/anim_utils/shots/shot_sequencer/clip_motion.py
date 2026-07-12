# !/usr/bin/python
# coding=utf-8
"""Clip motion, resize, and key-scaling logic for the shot sequencer (Blender).

Blender mirror of mayatk's ``shot_sequencer.clip_motion`` — :class:`ClipMotionMixin`
plus two standalone helpers (:func:`curves_for_attr`, :func:`scale_attribute_keys`).

Blender's fcurve key edits need none of Maya's cut-and-recreate dance (Maya's
``keyframe(timeChange=)`` won't slide a key past an occupied frame, and it snaps
tangents): moving a key is ``keyframe_point.co[0] = new_t`` and its interpolation
travels with it.  Divergences (ledgered): audio-clip *motion* is a no-op this phase
(VSE sound-strip time-shifting is a documented follow-up — matches the engine's
"no audio shifting"), and per-attribute sub-row segmentation uses the object-segment
fallback (no ``SegmentKeys`` port).
"""
from __future__ import annotations

from blendertk.core_utils._core_utils import undo_chunk
from blendertk.anim_utils.shots._shots import iter_action_fcurves

# Near-zero guard for floating-point comparisons.
FLOAT_ZERO_EPS = 1e-6
_EPS = 1e-3

__all__ = ["ClipMotionMixin", "curves_for_attr", "scale_attribute_keys"]

# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------


def curves_for_attr(obj_name: str, attr_name: str) -> list:
    """Return the fcurves driving *attr_name* (a ``translateX``-style label) on *obj_name*.

    Matches by :func:`segment_collector.attr_label` — the *same* forward function
    ``_provide_sub_rows`` labels sub-rows with — so resolution is always consistent
    with the label the user sees, works for ``rotation_quaternion`` (a hand-kept
    reverse ``translateX→(location,0)`` map missed it and silently returned ``[]``),
    and can't drift from ``attr_label``.  Falls back to a raw ``data_path`` substring
    for non-standard/custom-property channels.
    """
    try:
        import bpy
    except ImportError:
        return []
    from blendertk.anim_utils.shots.shot_sequencer.segment_collector import attr_label

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return []
    return [fc for fc in iter_action_fcurves(obj)
            if attr_label(fc) == attr_name or attr_name in fc.data_path]


def _shift_fcurve_keys(fcurves, delta: float, time_range) -> None:
    """Shift every key of *fcurves* within *time_range* by *delta* (Blender direct move)."""
    if abs(delta) < FLOAT_ZERO_EPS:
        return
    lo, hi = time_range[0] - _EPS, time_range[1] + _EPS
    for fc in fcurves:
        moved = False
        for kp in fc.keyframe_points:
            if lo <= kp.co[0] <= hi:
                kp.co[0] += delta
                kp.handle_left[0] += delta
                kp.handle_right[0] += delta
                moved = True
        if moved:
            fc.update()


def scale_attribute_keys(obj_name: str, attr_name: str, old_start: float,
                         old_end: float, new_start: float, new_end: float) -> None:
    """Scale only the fcurves driving *attr_name* on *obj_name* (sub-row clip resize)."""
    curves = curves_for_attr(obj_name, attr_name)
    if not curves or abs(old_end - old_start) < FLOAT_ZERO_EPS:
        return
    scale = (new_end - new_start) / (old_end - old_start)
    lo, hi = old_start - _EPS, old_end + _EPS

    def _remap(x):
        return new_start + (x - old_start) * scale

    for fc in curves:
        moved = False
        for kp in fc.keyframe_points:
            if lo <= kp.co[0] <= hi:
                kp.handle_left[0] = _remap(kp.handle_left[0])
                kp.handle_right[0] = _remap(kp.handle_right[0])
                kp.co[0] = _remap(kp.co[0])
                moved = True
        if moved:
            fc.update()


def _object_exists(obj_name: str) -> bool:
    try:
        import bpy
    except ImportError:
        return False
    return obj_name in bpy.data.objects


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class ClipMotionMixin:
    """Mixin supplying clip move, resize, and batch-move handlers.

    Expects the host controller to provide ``sequencer``, ``_get_sequencer_widget()``,
    ``_shifted_out_keys``, ``_segment_cache`` / ``_sub_row_cache``,
    ``_audio_segments_cache``, ``_syncing``, ``_save_shot_state()`` /
    ``_sync_to_widget()`` / ``_sync_combobox()``, ``_gap_edit_epilogue()``,
    ``_set_footer()``, and ``logger``.
    """

    def on_clip_resized(self, clip_id: int, new_start: float, new_duration: float) -> None:
        """Resize a clip — attribute sub-row (scale one channel) or main track (``resize_object``)."""
        if self.sequencer is None:
            return
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return
        if clip.data.get("is_audio"):
            return
        shot_id = clip.data.get("shot_id")
        obj_name = clip.data.get("obj")
        if shot_id is None or obj_name is None:
            return
        orig_start = clip.data.get("orig_start")
        orig_end = clip.data.get("orig_end")
        if orig_start is None or orig_end is None:
            return

        self._save_shot_state()
        new_end = new_start + new_duration
        attr_name = clip.data.get("attr_name")
        with undo_chunk():
            if attr_name:
                scale_attribute_keys(obj_name, attr_name, orig_start, orig_end, new_start, new_end)
            else:
                self.sequencer.resize_object(shot_id, obj_name, orig_start, orig_end, new_start, new_end)
        self._gap_edit_epilogue()
        label = f"{obj_name}.{attr_name}" if attr_name else obj_name
        dur = int(new_end - new_start)
        self._set_footer(f"Resized {label} · {new_start:.0f}–{new_end:.0f} ({dur}f)")

    def _apply_clip_move(self, clip_id: int, new_start: float) -> bool:
        """Move a single clip's keys without rebuilding the widget. Returns whether a sync is needed."""
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return False

        # Audio clip move — deferred (VSE sound-strip time-shifting is a follow-up,
        # matching the engine's "no audio shifting" divergence).
        if clip.data.get("is_audio"):
            self.logger.debug("[CLIP MOVE] audio clip move is a no-op this phase")
            return False

        # Sub-row attribute clip move
        attr_name = clip.data.get("attr_name")
        if attr_name:
            obj_name = clip.data.get("obj")
            orig_start = clip.data.get("orig_start")
            orig_end = clip.data.get("orig_end")
            if not obj_name or orig_start is None or orig_end is None:
                return False
            if not _object_exists(obj_name):
                return False
            delta = new_start - orig_start
            if abs(delta) < FLOAT_ZERO_EPS:
                return False
            # Both stepped and span sub-row moves go through curves_for_attr
            # (label-scoped, quaternion-safe).  The engine's move_stepped_keys
            # takes a data_path filter, not a display label, so it can't be used
            # here — a stepped key is just a point window (orig_start, orig_start).
            curves = curves_for_attr(obj_name, attr_name)
            if curves:
                window = (orig_start, orig_start) if clip.data.get("is_stepped") \
                    else (orig_start, orig_end)
                _shift_fcurve_keys(curves, delta, window)
            new_end = new_start + (orig_end - orig_start)
            self._expand_shot_for_clip(clip, new_start, new_end)
            return True

        # Animation clip move — per-object within a shot
        if self.sequencer is None:
            return False
        shot_id = clip.data.get("shot_id")
        obj_name = clip.data.get("obj")
        orig_start = clip.data.get("orig_start")
        orig_end = clip.data.get("orig_end")
        if shot_id is None or obj_name is None or orig_start is None or orig_end is None:
            return False
        delta = new_start - orig_start
        if abs(delta) < FLOAT_ZERO_EPS:
            return False

        # Stepped (zero-duration) clips
        if clip.data.get("is_stepped"):
            self.sequencer.move_stepped_keys(obj_name, orig_start, new_start)
            shift_held = getattr(widget, "shift_held_at_press", False)
            if shift_held:
                shot = self.sequencer.shot_by_id(shot_id)
                if shot and (new_start < shot.start or new_start > shot.end):
                    self._shifted_out_keys.setdefault(obj_name, set()).add(new_start)
            else:
                self._shifted_out_keys.pop(obj_name, None)
            self._expand_shot_for_clip(clip, new_start, new_start)
            return True

        shot = self.sequencer.shot_by_id(shot_id)
        pre_bounds = (shot.start, shot.end) if shot else None
        shift_held = getattr(widget, "shift_held_at_press", False)
        if shift_held:
            self.sequencer.move_object_keys(obj_name, orig_start, orig_end, new_start)
        else:
            self.sequencer.move_object_in_shot(shot_id, obj_name, orig_start, orig_end, new_start)
            self._shifted_out_keys.pop(obj_name, None)

        shot_after = self.sequencer.shot_by_id(shot_id)
        if (pre_bounds is not None and shot_after is not None
                and (abs(shot_after.start - pre_bounds[0]) > FLOAT_ZERO_EPS
                     or abs(shot_after.end - pre_bounds[1]) > FLOAT_ZERO_EPS)):
            self._segment_cache.clear()
            self._sub_row_cache.clear()
        return True

    def _expand_shot_for_clip(self, clip, new_start: float, new_end: float) -> None:
        """Grow the shot if the clip's new range exceeds bounds (skipped when Shift is held)."""
        widget = self._get_sequencer_widget()
        if getattr(widget, "shift_held_at_press", False):
            return
        if self.sequencer is None:
            return
        shot_id = clip.data.get("shot_id")
        if shot_id is None:
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return
        prior_start, prior_end = shot.start, shot.end
        expanded_start = min(shot.start, new_start)
        expanded_end = max(shot.end, new_end)
        start_delta = expanded_start - prior_start
        end_delta = expanded_end - prior_end
        if abs(start_delta) > 1e-6 or abs(end_delta) > 1e-6:
            was_syncing = self._syncing
            self._syncing = True
            try:
                self.sequencer.store.update_shot(shot_id, start=expanded_start, end=expanded_end)
                if abs(start_delta) > 1e-6:
                    self.sequencer.ripple_upstream(shot_id, prior_start, start_delta)
                if abs(end_delta) > 1e-6:
                    self.sequencer.ripple_downstream(shot_id, prior_end, end_delta)
            finally:
                self._syncing = was_syncing
            self._segment_cache.clear()

    def on_clip_moved(self, clip_id: int, new_start: float) -> None:
        """Handle clip move — routes to audio (deferred) or shot-level logic."""
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        shot_id = clip.data.get("shot_id") if clip else None
        obj_name = clip.data.get("obj", "") if clip else ""

        self._save_shot_state()
        with undo_chunk():
            if self._apply_clip_move(clip_id, new_start):
                self._sync_to_widget(shot_id=shot_id)
                self._sync_combobox()
                if obj_name:
                    self._set_footer(f"Moved {obj_name} → {new_start:.0f}")

    def on_clips_batch_moved(self, moves) -> None:
        """Handle a batch of clip moves (group drag), syncing once at the end."""
        shot_id = None
        if moves:
            widget = self._get_sequencer_widget()
            if widget:
                clip = widget.get_clip(moves[0][0])
                if clip:
                    shot_id = clip.data.get("shot_id")
        self._save_shot_state()
        with undo_chunk():
            needs_sync = False
            for clip_id, new_start in moves:
                if self._apply_clip_move(clip_id, new_start):
                    needs_sync = True
            if needs_sync:
                self._sync_to_widget(shot_id=shot_id)
                self._sync_combobox()
                self._set_footer(f"Moved {len(moves)} clip{'s' if len(moves) != 1 else ''}")

    # -- per-key handlers ---------------------------------------------------

    def on_keys_moved(self, clip_id: int, changes: list) -> None:
        """Move individual keyframes on the fcurves, then refresh.

        *changes* is ``[(old_time, new_time), ...]``.  Blender moves the point in
        place (``co[0] = new_t``) — no value/tangent capture-and-replay.

        Two-pass per fcurve (the Blender analogue of mayatk's batch-collision
        handling): every ``(key, new_time)`` target is matched against the
        PRE-move key positions first, then written.  A single in-place pass
        would let a later ``(old_t, new_t)`` pair match a key an earlier pair
        just moved — e.g. ``[(10, 12), (12, 14)]`` stacking both keys on 14.
        """
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return
        obj_name = clip.data.get("obj")
        attr_name = clip.data.get("attr_name")
        if not obj_name or not attr_name:
            return
        curves = curves_for_attr(obj_name, attr_name)
        if not curves:
            return

        moved_any = False
        with undo_chunk():
            for fc in curves:
                # Pass 1 — match against pre-move positions (each key claimed once).
                targets = []
                claimed = set()
                for old_t, new_t in changes:
                    if abs(new_t - old_t) < 1e-6:
                        continue
                    for kp in fc.keyframe_points:
                        ptr = kp.as_pointer()
                        if ptr in claimed:
                            continue
                        if abs(kp.co[0] - old_t) < _EPS:
                            claimed.add(ptr)
                            targets.append((kp, new_t))
                # Pass 2 — write (no add/remove, so the kp references stay valid).
                for kp, new_t in targets:
                    d = new_t - kp.co[0]
                    kp.co[0] = new_t
                    kp.handle_left[0] += d
                    kp.handle_right[0] += d
                    moved_any = True
                if targets:
                    fc.update()
        if not moved_any:
            return

        self._save_shot_state()
        shot_id = clip.data.get("shot_id")
        shot = self.sequencer.shot_by_id(shot_id) if self.sequencer else None
        if shot is not None:
            new_times = [new_t for _, new_t in changes]
            if any(t < shot.start or t > shot.end for t in new_times):
                for sid in list(self._segment_cache):
                    if sid != shot_id:
                        self._segment_cache.pop(sid, None)
        self._sync_to_widget(shot_id=shot_id)
        n = len(changes)
        self._set_footer(f"Moved {n} key{'s' if n != 1 else ''} on {obj_name}.{attr_name}")

    def on_keys_deleted(self, clip_id: int, times: list) -> None:
        """Delete individual keyframes from the fcurves, then refresh."""
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return
        obj_name = clip.data.get("obj")
        attr_name = clip.data.get("attr_name")
        if not obj_name or not attr_name:
            return
        curves = curves_for_attr(obj_name, attr_name)
        if not curves:
            return

        deleted = False
        with undo_chunk():
            for t in times:
                for fc in curves:
                    victims = [kp for kp in fc.keyframe_points if abs(kp.co[0] - t) < _EPS]
                    for kp in reversed(victims):
                        fc.keyframe_points.remove(kp)
                        deleted = True
                    if victims:
                        fc.update()
        if not deleted:
            return

        self._save_shot_state()
        shot_id = clip.data.get("shot_id")
        self._sync_to_widget(shot_id=shot_id)
        n = len(times)
        self._set_footer(f"Deleted {n} key{'s' if n != 1 else ''} on {obj_name}.{attr_name}")
