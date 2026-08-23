# !/usr/bin/python
# coding=utf-8
"""Clip motion, resize, and key-scaling logic for the shot sequencer (Blender).

Blender mirror of mayatk's ``shot_sequencer.clip_motion`` — :class:`ClipMotionMixin`
plus two standalone helpers (:func:`curves_for_attr`, :func:`scale_attribute_keys`).

Blender's fcurve key edits need none of Maya's cut-and-recreate dance (Maya's
``keyframe(timeChange=)`` won't slide a key past an occupied frame, and it snaps
tangents): moving a key is ``keyframe_point.co[0] = new_t`` and its interpolation
travels with it.  Audio clips are VSE sound strips (``AudioUtils.shift_clips_in_range``
— a strip's position is its keyed state, so no compositor re-sync is needed); sub-row
runs shift through ``btk.SegmentKeys.shift_curves`` like the Maya original.
"""

from __future__ import annotations

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.anim_utils.shots._shots import BlenderShotStore
from blendertk.anim_utils._anim_utils import AnimUtils

# Near-zero guard for floating-point comparisons.
FLOAT_ZERO_EPS = 1e-6
_EPS = 1e-3

__all__ = ["ClipMotionMixin"]

# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class _ClipMotionMixinInternal(object):
    """Internal helpers for ClipMotionMixin."""

    @staticmethod
    def _object_exists(obj_name: str) -> bool:
        try:
            import bpy
        except ImportError:
            return False
        return obj_name in bpy.data.objects


class ClipMotionMixin(_ClipMotionMixinInternal):
    """Mixin supplying clip move, resize, and batch-move handlers.

    Expects the host controller to provide ``sequencer``, ``_get_sequencer_widget()``,
    ``_shifted_out_keys``, ``_segment_cache`` / ``_sub_row_cache``,
    ``_audio_segments_cache``, ``_syncing``, ``_save_shot_state()`` /
    ``_sync_to_widget()`` / ``_sync_combobox()``, ``_gap_edit_epilogue()``,
    ``_set_footer()``, and ``logger``.
    """

    def on_clip_resized(
        self, clip_id: int, new_start: float, new_duration: float
    ) -> None:
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
        with CoreUtils.undo_chunk():
            if attr_name:
                ClipMotionMixin.scale_attribute_keys(
                    obj_name, attr_name, orig_start, orig_end, new_start, new_end
                )
            else:
                self.sequencer.resize_object(
                    shot_id, obj_name, orig_start, orig_end, new_start, new_end
                )
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

        # Audio clip move — translate the VSE sound strip (a strip's position IS
        # its keyed state; no compositor re-sync needed, unlike mayatk).
        if clip.data.get("is_audio"):
            orig_start = clip.data.get("orig_start")
            orig_end = clip.data.get("orig_end")
            track_id = clip.data.get("audio_track_id")
            if orig_start is None or orig_end is None or not track_id:
                return False
            delta = new_start - orig_start
            if abs(delta) < FLOAT_ZERO_EPS:
                return False
            from blendertk.audio_utils._audio_utils import AudioUtils

            AudioUtils.shift_clips_in_range(
                orig_start, orig_end, delta, names=[track_id]
            )
            new_end = new_start + (orig_end - orig_start)
            clip.data["orig_start"] = new_start
            clip.data["orig_end"] = new_end
            # The audio segment cache still holds the pre-move span — the
            # immediate rebuild would snap the clip back until the next refresh.
            self._audio_segments_cache = None
            self._expand_shot_for_clip(clip, new_start, new_end)
            return True

        # Sub-row attribute clip move
        attr_name = clip.data.get("attr_name")
        if attr_name:
            obj_name = clip.data.get("obj")
            orig_start = clip.data.get("orig_start")
            orig_end = clip.data.get("orig_end")
            if not obj_name or orig_start is None or orig_end is None:
                return False
            if not _ClipMotionMixinInternal._object_exists(obj_name):
                return False
            delta = new_start - orig_start
            if abs(delta) < FLOAT_ZERO_EPS:
                return False
            if clip.data.get("is_stepped"):
                # Stepped sub-row clip: a point window at orig_start.
                if self.sequencer is not None:
                    self.sequencer.move_stepped_keys(
                        obj_name, orig_start, new_start, attr_name=attr_name
                    )
            else:
                curves = ClipMotionMixin.curves_for_attr(obj_name, attr_name)
                if curves:
                    from blendertk.anim_utils.segment_keys import SegmentKeys

                    SegmentKeys.shift_curves(
                        curves,
                        delta,
                        time_range=(orig_start, orig_end),
                        remove_flat_at_dest=False,
                    )
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
        if (
            shot_id is None
            or obj_name is None
            or orig_start is None
            or orig_end is None
        ):
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
            self.sequencer.move_object_in_shot(
                shot_id, obj_name, orig_start, orig_end, new_start
            )
            self._shifted_out_keys.pop(obj_name, None)

        shot_after = self.sequencer.shot_by_id(shot_id)
        if (
            pre_bounds is not None
            and shot_after is not None
            and (
                abs(shot_after.start - pre_bounds[0]) > FLOAT_ZERO_EPS
                or abs(shot_after.end - pre_bounds[1]) > FLOAT_ZERO_EPS
            )
        ):
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
                self.sequencer.store.update_shot(
                    shot_id, start=expanded_start, end=expanded_end
                )
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
        with CoreUtils.undo_chunk():
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
        with CoreUtils.undo_chunk():
            needs_sync = False
            for clip_id, new_start in moves:
                if self._apply_clip_move(clip_id, new_start):
                    needs_sync = True
            if needs_sync:
                self._sync_to_widget(shot_id=shot_id)
                self._sync_combobox()
                self._set_footer(
                    f"Moved {len(moves)} clip{'s' if len(moves) != 1 else ''}"
                )

    # -- per-key handlers ---------------------------------------------------

    def on_keys_moved(self, clip_id: int, changes: list) -> None:
        """Move individual keyframes on the fcurves, then refresh.

        *changes* is ``[(old_time, new_time), ...]``.  Routed through the engine's
        ``move_curve_keys`` / ``recreate_curve_keys`` exactly like mayatk; Blender
        moves each point in place (``co[0] = new_t``) — no tangent capture/replay,
        and the two-pass recreate path guarantees a later ``(old_t, new_t)`` pair
        can never grab a key an earlier pair just moved.
        """
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return
        obj_name = clip.data.get("obj")
        attr_name = clip.data.get("attr_name")
        if not obj_name or not attr_name:
            return
        curves = ClipMotionMixin.curves_for_attr(obj_name, attr_name)
        if not curves:
            return

        from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )

        # Same primitives as mayatk: one shared delta → move_curve_keys; mixed
        # deltas → recreate_curve_keys (two-pass, so a key can't be claimed twice).
        moved_any = False
        with CoreUtils.undo_chunk():
            for fc in curves:
                kt = AnimUtils.key_times(fc)

                def _has_key(t, _kt=kt):
                    i0, i1 = AnimUtils.window_indices(_kt, t - _EPS, t + _EPS)
                    return i1 > i0

                pairs = [
                    (old_t, new_t)
                    for old_t, new_t in changes
                    if abs(new_t - old_t) >= 1e-6 and _has_key(old_t)
                ]
                if not pairs:
                    continue
                deltas = {round(new_t - old_t, 6) for old_t, new_t in pairs}
                if len(deltas) == 1:
                    ShotSequencer.move_curve_keys(
                        fc, [old_t for old_t, _ in pairs], deltas.pop(), eps=_EPS
                    )
                else:
                    ShotSequencer.recreate_curve_keys(fc, pairs, eps=_EPS)
                moved_any = True
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
        self._set_footer(
            f"Moved {n} key{'s' if n != 1 else ''} on {obj_name}.{attr_name}"
        )

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
        curves = ClipMotionMixin.curves_for_attr(obj_name, attr_name)
        if not curves:
            return

        deleted = False
        with CoreUtils.undo_chunk():
            for t in times:
                for fc in curves:
                    i0, i1 = AnimUtils.window_indices(
                        AnimUtils.key_times(fc), t - _EPS, t + _EPS
                    )
                    for i in reversed(range(i0, i1)):
                        fc.keyframe_points.remove(fc.keyframe_points[i])
                        deleted = True
                    if i1 > i0:
                        fc.update()
        if not deleted:
            return

        self._save_shot_state()
        shot_id = clip.data.get("shot_id")
        self._sync_to_widget(shot_id=shot_id)
        n = len(times)
        self._set_footer(
            f"Deleted {n} key{'s' if n != 1 else ''} on {obj_name}.{attr_name}"
        )

    @staticmethod
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
        from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
            SegmentCollector,
        )

        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return []
        return [
            fc
            for fc in BlenderShotStore.iter_action_fcurves(obj)
            if SegmentCollector.attr_label(fc) == attr_name or attr_name in fc.data_path
        ]

    @staticmethod
    def scale_attribute_keys(
        obj_name: str,
        attr_name: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Scale only the fcurves driving *attr_name* on *obj_name* (sub-row clip resize)."""
        curves = ClipMotionMixin.curves_for_attr(obj_name, attr_name)
        if not curves or abs(old_end - old_start) < FLOAT_ZERO_EPS:
            return
        lo, hi = old_start - _EPS, old_end + _EPS
        for fc in curves:
            AnimUtils.remap_keys_in_window(
                fc, lo, hi, old_start, old_end, new_start, new_end
            )
