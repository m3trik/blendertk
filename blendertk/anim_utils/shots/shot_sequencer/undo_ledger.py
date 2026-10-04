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
        """Record the current shot boundaries as an UNTAGGED restore point.

        No production path calls this: every edit brackets through
        ``BlenderShotStore.scene_edit``, which tags the point with the edit
        serial its step carries.  An untagged point reads as "always ours" to
        :meth:`_native_event_is_ours` (mayatk's pre-pairing behaviour), so a
        caller that pushes one gives up the pairing.  Prefer ``scene_edit``.
        """
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

    #: The scene's edit serial as the undo/redo handler found it BEFORE the
    #: native step ran (``_on_undo_pre`` / ``_on_redo_pre``).
    _serial_before_native = None

    def _native_event_is_ours(self, redo: bool = False) -> bool:
        """True when the undo/redo Blender JUST performed was our newest edit.

        Mirror of mayatk's: Blender fires its undo/redo handlers for every step
        in the session, and consuming a restore point for someone else's step
        reverted shot bounds whose keys Blender had left where they were.
        mayatk reads the step's NAME off the queue; Blender's undo exposes no
        names, so each edit stamps :attr:`BlenderShotStore.EDIT_SERIAL_PROP`
        inside its own step (``scene_edit``) and the restore point records it.
        Memfile undo winds that property with the steps, so:

        * an undo was ours when the serial moved AND it stood at our marker
          before the step was taken;
        * a redo was ours when the serial moved and now stands at the marker.

        Any other step leaves the serial where it was.  An untagged restore
        point (a legacy push) keeps the pre-pairing "always ours" behaviour.
        """
        store = self.sequencer.store if self.sequencer is not None else None
        if store is None or not store.has_boundary_snapshot(redo=redo):
            return False
        tag = store.peek_boundary_tag(redo=redo)
        if not isinstance(tag, tuple):
            return True
        paired, marker = tag
        before, after = self._serial_before_native, store.edit_serial()
        if not paired or before is None or before == after:
            return False
        return (after if redo else before) == marker

    def on_undo(self) -> None:
        """Widget undo_requested -- Blender's undo; the restore point follows
        only when the step it undid was ours.

        mayatk decides up front from the queue's step names (``_undo_plan``);
        with no names to read, Blender decides from what the undo did: the
        ``undo_post`` handler checks :meth:`_native_event_is_ours` and applies
        the restore point for our own step alone.  An unrelated step on top is
        undone by itself, as mayatk's plan passes it straight through.
        """
        self._native_undo("undo")

    def on_redo(self) -> None:
        """Widget redo_requested -- Blender's redo; ``redo_post`` re-applies the
        redo-side bounds only when the step it redid was ours (see :meth:`on_undo`)."""
        self._native_undo("redo")

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
