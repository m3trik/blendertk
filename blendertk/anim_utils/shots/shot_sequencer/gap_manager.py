# !/usr/bin/python
# coding=utf-8
"""Gap and range-highlight handlers for the shot sequencer controller (Blender).

Blender mirror of mayatk's ``shot_sequencer.gap_manager`` — :class:`GapManagerMixin`
handles gap resize/move/lock and range-highlight interactions.  The only DCC swap
is the undo bracket (``CoreUtils.undo_chunk`` → ``btk.undo_chunk``); every edit is
expressed through the DCC-agnostic ``ShotSequencer`` + ``BlenderShotStore`` surface.
"""

from __future__ import annotations

from blendertk.core_utils._core_utils import CoreUtils

# Threshold for detecting meaningful time deltas (frame-level tolerance).
TIME_SNAP_EPS = 1e-3

__all__ = ["GapManagerMixin"]


class GapManagerMixin:
    """Mixin supplying gap-overlay and range-highlight handlers.

    Expects the host controller to provide ``sequencer``, ``active_shot_id``,
    ``_save_shot_state()`` / ``_sync_to_widget()`` / ``_sync_combobox()``,
    ``_get_sequencer_widget()``, ``_syncing``, ``_segment_cache`` / ``_sub_row_cache``,
    ``_set_footer()`` (a refused drag is an answer, not a silent no-op),
    ``_neighbour_shots()``, and ``logger``.
    """

    # ---- range highlight -------------------------------------------------

    def on_range_highlight_changed(self, start: float, end: float) -> None:
        """Update the active shot boundaries when the range highlight is dragged.

        Both edges shifted by the same delta → *move* (keys shift + downstream
        ripples).  Otherwise an edge resize: the plain drag moves the boundary
        and leaves keyframes where they are, **Shift** retimes the shot's keys
        into the new range.  Both ripple neighbours by the edge deltas.
        """
        if self.sequencer is None or self.active_shot_id is None:
            return

        shot = self.sequencer.shot_by_id(self.active_shot_id)
        if shot is None:
            return

        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "shift_held_at_press", False)

        ds = start - shot.start
        de = end - shot.end

        self._save_shot_state()

        if abs(ds - de) < TIME_SNAP_EPS and abs(ds) > TIME_SNAP_EPS:
            self._syncing = True
            try:
                with CoreUtils.undo_chunk():
                    self.sequencer.move_shot(self.active_shot_id, start)
            finally:
                self._syncing = False
            self._gap_edit_epilogue()
            return

        # Edge resize — plain moves the boundary, Shift retimes the content.
        self._syncing = True
        try:
            with CoreUtils.undo_chunk():
                if shift_held:
                    self.sequencer.resize_shot(self.active_shot_id, start, end)
                else:
                    self.sequencer.resize_shot_bounds(self.active_shot_id, start, end)
        finally:
            self._syncing = False
        self._gap_edit_epilogue()

    # ---- helpers ---------------------------------------------------------

    def _find_shot_by_start(self, frame: float):
        """Return the shot whose start is closest to *frame*, or None."""
        for shot in self.sequencer.sorted_shots():
            if abs(shot.start - frame) < TIME_SNAP_EPS:
                return shot
        return None

    def _find_shot_by_end(self, frame: float):
        """Return the shot whose end is closest to *frame*, or None."""
        for shot in self.sequencer.sorted_shots():
            if abs(shot.end - frame) < TIME_SNAP_EPS:
                return shot
        return None

    def _refuse_if_gap_locked(self, shot, side: str) -> bool:
        """True (and the drag is refused) when that gap is locked.

        A lock is a statement about the gap's WIDTH, so it stops the two
        gestures that would change it — either edge handle — and nothing
        else: the body drag slides at constant width, a shot resize ripples
        its neighbour so the width survives, and a respace already skips
        locked gaps.  (Mirrors mayatk.)
        """
        # ``_neighbour_shots`` is the host's own prev/next lookup (the shot
        # menu's Merge entries read it); a second copy here would be one more
        # place for "which shot is next" to drift.  Either side is ``None``
        # where the timeline ends -- the span before the first shot and the
        # tail handle after the last one flank no gap, so neither can be
        # locked.
        neighbours = self._neighbour_shots(shot.shot_id)
        if side == "left":
            left, right = neighbours["merge_prev"], shot
        else:
            left, right = shot, neighbours["merge_next"]
        if left is None or right is None:
            return False
        if not self.sequencer.store.is_gap_locked(left.shot_id, right.shot_id):
            return False
        self.logger.debug(
            "Gap %s-%s is locked; resize refused", left.shot_id, right.shot_id
        )
        self._set_footer(
            f"Gap between “{left.name}” and “{right.name}” "
            "is locked — unlock it to resize."
        )
        return True

    def _gap_edit_epilogue(self):
        """Common cleanup after any gap edit."""
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        if self.sequencer is not None:
            self.sequencer.store.mark_dirty()
        self._sync_to_widget()
        self._sync_combobox()
        # Boundaries just moved — the playback range was set from the OLD
        # ones and is now stale (transport buttons land on the wrong frames).
        self._apply_view_playback_range()

    def _set_shot_edge(
        self, shot, new_start=None, new_end=None, scale: bool = False
    ) -> bool:
        """Move one of *shot*'s edges while the other stays fixed.

        Snaps the raw drag through the store and clamps against the opposite edge
        (zero-duration floor) so an over-dragged edge can't store inverted bounds.

        ``scale=False`` (the plain drag) moves the boundary only, leaving
        keyframes untouched; ``scale=True`` (Shift) retimes the shot's keys
        into the new range.  Neither ripples — the shot grows or shrinks into
        the adjacent gap, so no neighbour has to move, and the edge is
        clamped at the neighbour so a drag past a zero-width gap cannot
        overlap it.

        Returns True when the shot actually changed.
        """
        store = self.sequencer.store
        old_s, old_e = shot.start, shot.end
        ns = old_s if new_start is None else store.snap(new_start)
        ne = old_e if new_end is None else store.snap(new_end)
        if new_start is not None:
            ns = min(ns, old_e)
        if new_end is not None:
            ne = max(ne, old_s)
        # Clamp against the NEIGHBOURS too.  This edit deliberately does not
        # ripple - the shot only grows or shrinks into the adjacent GAP - so
        # at zero gap there is nothing to grow into and the edge must hold.
        # Without this an inner edge drag stores overlapping shots, and two
        # shots claiming one span makes key ownership (and every envelope
        # derived from it) ambiguous.
        sorted_s = self.sequencer.sorted_shots()
        idx = next(
            (i for i, s in enumerate(sorted_s) if s.shot_id == shot.shot_id), None
        )
        if idx is not None:
            if new_start is not None and idx > 0:
                ns = max(ns, sorted_s[idx - 1].end)
            if new_end is not None and idx + 1 < len(sorted_s):
                ne = min(ne, sorted_s[idx + 1].start)
        if abs(ns - old_s) < TIME_SNAP_EPS and abs(ne - old_e) < TIME_SNAP_EPS:
            return False
        if scale:
            for obj in shot.objects:
                self.sequencer.scale_object_keys(obj, old_s, old_e, ns, ne)
        shot.start = ns
        shot.end = ne
        return True

    # ---- gap resize / move -----------------------------------------------

    def on_gap_resized(self, original_next_start: float, new_next_start: float) -> None:
        """Handle right-edge gap drag (a shot's ``.start``).

        Inner (active shot) → the start moves, end fixed, keys left in place
        (no ripple); outer → slide the adjacent shot downstream intact;
        Shift → retime the touched shot's keys into the new range.
        """
        if self.sequencer is None:
            return
        delta = new_next_start - original_next_start
        if abs(delta) < TIME_SNAP_EPS:
            return
        target = self._find_shot_by_start(original_next_start)
        if target is None:
            return
        if self._refuse_if_gap_locked(target, "left"):
            return
        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "shift_held_at_press", False)

        self._save_shot_state()
        self._syncing = True
        try:
            with CoreUtils.undo_chunk():
                is_inner = (
                    self.active_shot_id is not None
                    and target.shot_id == self.active_shot_id
                )
                if shift_held or is_inner:
                    if self._set_shot_edge(
                        target, new_start=new_next_start, scale=shift_held
                    ):
                        self.sequencer._enforce_gap_holds()
                else:
                    self.sequencer.slide_shot(
                        target.shot_id, new_next_start, direction="downstream"
                    )
        finally:
            self._syncing = False
        self._gap_edit_epilogue()

    def on_gap_left_resized(
        self, original_prev_end: float, new_prev_end: float
    ) -> None:
        """Handle left-edge gap drag (a shot's ``.end``).

        The overlay placed after the LAST shot is a ``tail`` handle whose left
        edge is that shot's end, so the final shot resizes like every other.

        Inner (active shot) → the end moves, start fixed, keys left in place
        (no ripple); outer → slide the adjacent shot upstream intact;
        Shift → retime the touched shot's keys into the new range.
        """
        if self.sequencer is None:
            return
        delta = new_prev_end - original_prev_end
        if abs(delta) < TIME_SNAP_EPS:
            return
        target = self._find_shot_by_end(original_prev_end)
        if target is None:
            return
        if self._refuse_if_gap_locked(target, "right"):
            return
        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "shift_held_at_press", False)

        self._save_shot_state()
        self._syncing = True
        try:
            with CoreUtils.undo_chunk():
                sorted_shots = self.sequencer.sorted_shots()
                is_timeline_last = bool(
                    sorted_shots and target.shot_id == sorted_shots[-1].shot_id
                )
                is_inner = (
                    self.active_shot_id is not None
                    and target.shot_id == self.active_shot_id
                )
                if shift_held or is_inner or is_timeline_last:
                    # Timeline-last included: its tail handle promises
                    # "resize the last shot"; the outer branch would slide
                    # the whole timeline upstream instead.
                    if self._set_shot_edge(
                        target, new_end=new_prev_end, scale=shift_held
                    ):
                        self.sequencer._enforce_gap_holds()
                else:
                    new_start = target.start + delta
                    self.sequencer.slide_shot(
                        target.shot_id, new_start, direction="upstream"
                    )
        finally:
            self._syncing = False
        self._gap_edit_epilogue()

    def on_gap_moved(
        self, old_start: float, old_end: float, new_start: float, new_end: float
    ) -> None:
        """Handle body gap drag — slide the gap while preserving its width."""
        if self.sequencer is None:
            return
        delta = new_start - old_start
        if abs(delta) < TIME_SNAP_EPS:
            return
        left_shot = self._find_shot_by_end(old_start)
        right_shot = self._find_shot_by_start(old_end)
        if left_shot is None and right_shot is None:
            return
        widget = self._get_sequencer_widget()
        shift_held = getattr(widget, "shift_held_at_press", False)
        active_id = self.active_shot_id

        self._save_shot_state()
        self._syncing = True
        try:
            with CoreUtils.undo_chunk():
                left_is_active = (
                    left_shot is not None
                    and active_id is not None
                    and left_shot.shot_id == active_id
                )
                right_is_active = (
                    right_shot is not None
                    and active_id is not None
                    and right_shot.shot_id == active_id
                )

                if left_is_active:
                    if right_shot is not None:
                        self.sequencer.slide_shot(
                            right_shot.shot_id,
                            right_shot.start + delta,
                            direction="downstream",
                            _enforce=False,
                        )
                    self._set_shot_edge(
                        left_shot, new_end=left_shot.end + delta, scale=shift_held
                    )
                elif right_is_active:
                    if left_shot is not None:
                        self.sequencer.slide_shot(
                            left_shot.shot_id,
                            left_shot.start + delta,
                            direction="upstream",
                            _enforce=False,
                        )
                    self._set_shot_edge(
                        right_shot,
                        new_start=right_shot.start + delta,
                        scale=shift_held,
                    )
                else:
                    if right_shot is not None:
                        self.sequencer.slide_shot(
                            right_shot.shot_id,
                            right_shot.start + delta,
                            direction="downstream",
                            _enforce=False,
                        )
                    if left_shot is not None:
                        self.sequencer.slide_shot(
                            left_shot.shot_id,
                            left_shot.start + delta,
                            direction="upstream",
                            _enforce=False,
                        )
                self.sequencer._enforce_gap_holds()
        finally:
            self._syncing = False
        self._gap_edit_epilogue()

    # ---- gap lock --------------------------------------------------------

    def on_gap_lock_changed(
        self, gap_start: float, gap_end: float, locked: bool
    ) -> None:
        """Handle a single gap's lock state being toggled via context menu.

        The overlay reports the gap by its FRAMES and the store keys locks by
        the flanking shot ids.  Matching each edge independently against an
        exact-ish frame found nothing whenever an overlay's cached span had
        drifted by more than a rounding — and a lock that resolves to nothing
        is never written, so the overlay drew itself "[Locked]" while the
        store stayed empty.  Resolve the PAIR instead.  (Mirrors mayatk.)
        """
        if self.sequencer is None:
            return
        pair = self._gap_pair_at(gap_start, gap_end)
        if pair is None:
            self.logger.warning(
                "Gap lock ignored: no shot pair flanks [%s, %s]", gap_start, gap_end
            )
            self._set_footer("Could not resolve that gap — lock not saved.")
            return
        left_shot, right_shot = pair
        store = self.sequencer.store
        if locked:
            store.lock_gap(left_shot.shot_id, right_shot.shot_id)
        else:
            store.unlock_gap(left_shot.shot_id, right_shot.shot_id)

    def _gap_pair_at(self, gap_start: float, gap_end: float):
        """The adjacent shot pair whose gap best matches ``[gap_start, gap_end]``.

        Best match, not a tolerance test: the caller has a gap overlay in
        hand, so one of these pairs IS it, and refusing on a rounding is what
        lost the lock.
        """
        shots = self.sequencer.sorted_shots() if self.sequencer else []
        if len(shots) < 2:
            return None
        return min(
            zip(shots, shots[1:]),
            key=lambda p: abs(p[0].end - gap_start) + abs(p[1].start - gap_end),
        )

    def on_gap_lock_all(self) -> None:
        """Lock all gaps so they are preserved during respace."""
        if self.sequencer is None:
            return
        self.sequencer.store.lock_all_gaps()
        widget = self._get_sequencer_widget()
        if widget is not None:
            widget.set_all_gap_overlays_locked(True)

    def on_gap_unlock_all(self) -> None:
        """Unlock all gaps so they follow the global gap value."""
        if self.sequencer is None:
            return
        self.sequencer.store.unlock_all_gaps()
        widget = self._get_sequencer_widget()
        if widget is not None:
            widget.set_all_gap_overlays_locked(False)
