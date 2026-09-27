# !/usr/bin/python
# coding=utf-8
"""The shot sequencer's undo ledger.

Provides :class:`UndoLedgerMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. Every structural
edit records the shot boundaries before it; the widget's undo/redo requests
restore / re-apply those snapshots alongside Blender's own undo queue.
"""

from blendertk.core_utils._core_utils import CoreUtils


class UndoLedgerMixin:
    """Shot-boundary restore points, and how one undo/redo splits between them and the DCC."""

    # Boundary snapshots delegate to the STORE's ledger (pythontk
    # ShotStore.push/restore/redo_boundary_snapshot) — one stack per scene,
    # shared with the Shots settings panel; see mayatk's twin for the why.

    def _save_shot_state(self) -> None:
        """Record the current shot boundaries as an undo restore point."""
        if self.sequencer is not None:
            self.sequencer.store.push_boundary_snapshot()

    def _discard_shot_state(self) -> None:
        """Drop the most recent restore point (the edit was a no-op)."""
        if self.sequencer is not None:
            self.sequencer.store.discard_boundary_snapshot()

    def _restore_shot_state(self) -> None:
        """Apply the most recent restore point (the undo direction).

        Membership is restored symmetrically: a shot ABSENT from the
        snapshot is removed (no phantom after undoing an insert), and a
        shot the store lost is re-created from its record (undoing a
        delete — the keys were never deleted with it).
        """
        if self.sequencer is not None:
            self.sequencer.store.restore_boundary_snapshot()

    def _redo_shot_state(self) -> None:
        """Re-apply the state undo stepped back from (the redo direction)."""
        if self.sequencer is not None:
            self.sequencer.store.redo_boundary_snapshot()

    def on_undo(self) -> None:
        """Widget undo_requested — restore the shot snapshot, then Blender undo.

        Mirror of mayatk's ``cmds.undo()`` path: the boundary snapshot is popped
        here under the ``_syncing`` guard (so the ``undo_post`` handler doesn't
        pop a second one), then ``bpy.ops.ed.undo`` reverts the key edits.
        """
        self._syncing = True
        try:
            try:
                self._restore_shot_state()
            except Exception:
                self.logger.debug("on_undo: _restore_shot_state failed", exc_info=True)
            self._native_undo("undo")
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def on_redo(self) -> None:
        """Widget redo_requested — re-apply the redo-side bounds, then Blender redo."""
        self._syncing = True
        try:
            try:
                self._redo_shot_state()
            except Exception:
                self.logger.debug("on_redo: _redo_shot_state failed", exc_info=True)
            self._native_undo("redo")
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def _native_undo(self, which: str) -> None:
        """Run ``bpy.ops.ed.undo`` / ``redo`` under a window context (Qt-timer safe)."""
        try:
            import bpy
        except ImportError:
            return
        try:
            with CoreUtils.window_context_override():
                if which == "undo":
                    bpy.ops.ed.undo()
                else:
                    bpy.ops.ed.redo()
        except Exception:
            self.logger.debug("native %s failed", which, exc_info=True)
