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
        ``BlenderShotStore._native_step_crossed`` (mayatk's pre-pairing behaviour), so a
        caller that pushes one gives up the pairing.  Prefer ``scene_edit``.
        """
        if self.sequencer is not None:
            self.sequencer.store.push_boundary_snapshot()

    # No ``_discard_shot_state`` (mayatk's): a Blender ``scene_edit`` pushes its
    # step even when the body raised, so the point it tagged stays paired with
    # that step; an edit that changed nothing calls the handle's ``cancel()``,
    # which drops the point and pushes no step.

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
        """Widget undo_requested -- Blender's undo; the restore point follows
        only when the step it undid was ours.

        mayatk decides up front from the queue's step names (``_undo_plan``);
        with no names to read, Blender decides from what the undo did: the
        store follows it (``BlenderShotStore.follow_native_step``, its
        ``undo_post`` handler -- the same path as a Ctrl+Z in a viewport, with
        this panel open or not) and applies the restore point for our own step
        alone.  An unrelated step on top is undone by itself, as mayatk's plan
        passes it straight through.
        """
        self._native_undo("undo")

    def on_redo(self) -> None:
        """Widget redo_requested -- Blender's redo; the store re-applies the
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
