# !/usr/bin/python
# coding=utf-8
"""Blender scene handlers for the shot sequencer.

Provides :class:`SceneCallbacksMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`.
``bpy.app.handlers`` undo/redo, frame-change and depsgraph handlers: new keys
join the active shot (and grow it, when that option is on) through a debounced
refresh, and the playhead follows the scene frame.
"""

from blendertk.anim_utils.shots._shots import BlenderShotStore


class SceneCallbacksMixin:
    """Scene event hooks: undo/redo, key edits and time changes refresh the widget."""

    # ---- Blender scene callbacks (bpy.app.handlers) ----------------------

    def _register_scene_callbacks(self) -> None:
        """(Re-)register undo/redo, frame-change, and depsgraph handlers.

        Replaces mayatk's OpenMaya undo/redo + DG-time + anim-keyframe callbacks.
        Idempotent: detaches any it previously attached first, so it can be
        re-run after a scene swap — Blender clears non-``@persistent`` handlers on
        File ▸ New/Open, which would otherwise silently kill the sequencer's live
        playhead/keyframe refresh for the rest of the session.  Each handler is
        tracked in ``self._handlers`` so teardown detaches exactly what it attached.
        """
        try:
            import bpy
        except ImportError:
            return

        self._unregister_scene_callbacks()

        def _add(handler_list, fn):
            handler_list.append(fn)
            self._handlers.append((handler_list, fn))

        h = bpy.app.handlers
        _add(h.frame_change_post, self._on_frame_change)
        _add(h.undo_post, self._on_undo_post)
        _add(h.redo_post, self._on_redo_post)
        _add(h.depsgraph_update_post, self._on_depsgraph_update)

    def _unregister_scene_callbacks(self) -> None:
        """Detach the tracked bpy.app handlers (tolerates ones Blender already cleared)."""
        for handler_list, fn in self._handlers:
            try:
                handler_list.remove(fn)
            except (ValueError, ReferenceError):
                pass
        self._handlers.clear()

    def remove_callbacks(self) -> None:
        """Detach all scene handlers + listeners (call on teardown)."""
        self._unbind_store_listener()
        try:
            BlenderShotStore.remove_invalidation_listener(self._on_store_invalidated)
        except Exception:
            pass
        self._unregister_scene_callbacks()
        if self._keyframe_debounce is not None:
            try:
                self._keyframe_debounce.stop()
            except RuntimeError:
                pass
            self._keyframe_debounce = None

    def _on_frame_change(self, *args) -> None:
        """Update the widget playhead when the scene frame changes.

        Render guard: ``frame_change_post`` also fires per-frame during a
        render job — on the render thread, against the evaluated scene copy —
        and calling into Qt from there is unsafe.  Skip when a render job is
        running or when the handler's scene isn't the UI scene.

        Fully try-wrapped: this fires every frame during playback, so a raise
        here (e.g. a stale-widget access) would spam the console and break the
        refresh loop mid-playback — Blender does NOT auto-remove a raising
        handler, it just prints the traceback each time.
        """
        if self._syncing_playhead:
            return
        try:
            import bpy

            is_job_running = getattr(bpy.app, "is_job_running", None)
            if is_job_running is not None and is_job_running("RENDER"):
                return
            scene = self._scene()
            if args and scene is not None and args[0] is not scene:
                return  # evaluated copy from a render/bake job, not the UI scene
            widget = self._get_sequencer_widget()
            if widget is not None and scene is not None:
                widget.set_playhead(scene.frame_current)
        except Exception:
            self.logger.debug("frame-change handler failed", exc_info=True)

    def _on_undo_post(self, *_args) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._restore_shot_state()
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        # The dropdown lists the shots in order and paints their bounds, so
        # it goes as stale as the timeline does -- an undone reorder left it
        # showing the order that had just been undone.
        self._sync_combobox()
        self._sync_to_widget()

    def _on_redo_post(self, *_args) -> None:
        # Redo re-applies the scene keys; the ledger's redo direction
        # re-applies the bounds the matching undo stepped back from, so the
        # two stay paired through undo→redo cycles.
        if self._syncing:
            return
        self._syncing = True
        try:
            self._redo_shot_state()
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        # The dropdown lists the shots in order and paints their bounds, so
        # it goes as stale as the timeline does -- an undone reorder left it
        # showing the order that had just been undone.
        self._sync_combobox()
        self._sync_to_widget()

    def _on_depsgraph_update(self, *args) -> None:
        """Debounced refresh when the scene's ANIMATION DATA changes.

        Replaces mayatk's ``MAnimMessage`` keyframe-edited callback, so it must
        be scoped like one: ``depsgraph_update_post`` fires on nearly every
        scene interaction (selection clicks, transform drags, playback ticks),
        and the debounce epilogue mutates state (``_auto_add_keyed_objects``
        merges keyed selected objects into the active shot + marks the store
        dirty).  Two guards keep it a keyframe-edit proxy:

        - **playback guard** — skip while the animation is playing (every
          frame is a depsgraph tick; the playhead handler owns playback sync);
        - **Action filter** — only react when an ``Action`` datablock is among
          ``depsgraph.updates`` (keyframe insert/move/delete tags the action;
          a bare selection click or a transform drag without autokey does not).

        Try-wrapped: a raise here would spam the console on every scene edit
        and break the live refresh — Blender does NOT auto-remove a raising
        handler, it just prints the traceback each time.
        """
        if self._syncing:
            return
        try:
            import bpy

            screen = getattr(bpy.context, "screen", None)
            if screen is not None and screen.is_animation_playing:
                return
            depsgraph = args[1] if len(args) > 1 else None
            if depsgraph is not None and not self._is_animation_update(depsgraph):
                return
            if depsgraph is not None:
                # Bank NOW — the depsgraph is invalid by the time the 200ms
                # debounce fires.  Object IDs arrive in the same updates
                # batch as the Action (probed pairing: key insert →
                # ['Object', 'Action']); updates carry EVALUATED ids, so
                # take .original.
                for u in depsgraph.updates:
                    if isinstance(u.id, bpy.types.Object):
                        self._edited_objects.add(u.id.original.name)
            from qtpy import QtCore

            if self._keyframe_debounce is None:
                self._keyframe_debounce = QtCore.QTimer()
                self._keyframe_debounce.setSingleShot(True)
                self._keyframe_debounce.setInterval(200)
                self._keyframe_debounce.timeout.connect(self._on_keyframe_debounce_fire)
            self._keyframe_debounce.start()
        except Exception:
            self.logger.debug("depsgraph handler failed", exc_info=True)

    @staticmethod
    def _is_animation_update(depsgraph) -> bool:
        """True when an ``Action`` datablock is among the depsgraph updates.

        Keyframe insert/move/delete tags the owning Action (probed on Blender
        5.1: key insert → ``['Object', 'Action']``); a bare selection click
        (``['Scene']``) or a transform drag without autokey (``['Object']``)
        does not — the discriminator that scopes :meth:`_on_depsgraph_update`
        to keyframe edits, like mayatk's ``MAnimMessage`` callback.
        """
        import bpy

        return any(isinstance(u.id, bpy.types.Action) for u in depsgraph.updates)

    def _on_keyframe_debounce_fire(self) -> None:
        if self._syncing:
            return
        active_id = self.active_shot_id
        self._audio_segments_cache = None
        self._reconcile_needed = True
        if active_id is not None:
            self._segment_cache.pop(active_id, None)
            self._sub_row_cache = {
                k: v for k, v in self._sub_row_cache.items() if k[0] != active_id
            }
            added = self._auto_add_keyed_objects(active_id)
            # Grow the shot over anything that landed outside it, if the
            # option is on.  After the membership pass: a freshly keyed
            # object has to be IN the shot before its keys count as its own.
            if self._auto_extend_to_new_keys(active_id):
                return  # it rebuilt already
        else:
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            added = False
        if not added:
            self._sync_to_widget()

    def _auto_add_keyed_objects(self, shot_id: int) -> bool:
        """Merge newly-keyed transforms into the active shot's objects.

        Candidates come from the objects whose Actions Blender reported as
        updated (banked by :meth:`_on_depsgraph_update`), falling back to
        the current selection when the handler banked nothing — so scripted
        or channel-pinned keying on UNSELECTED objects still joins the shot
        (mirror of mayatk's banked-curve path).
        """
        if self.sequencer is None:
            return False
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return False
        try:
            import bpy  # noqa: F401 -- probe: no selection to read outside Blender
        except ImportError:
            return False
        candidates = set(self._edited_objects)
        self._edited_objects.clear()
        if not candidates:
            from blendertk.core_utils._core_utils import CoreUtils

            # not bpy.context.selected_objects: absent windowless (the Qt pump)
            candidates = {o.name for o in CoreUtils.selected_objects()}
        if not candidates:
            return False
        existing = set(shot.objects)
        candidates -= existing
        if not candidates:
            return False
        keyed = set(self.sequencer._find_keyed_transforms(shot.start, shot.end))
        new_objects = candidates & keyed
        if not new_objects:
            return False
        merged = sorted(existing | new_objects)
        self.sequencer.store.update_shot(shot_id, objects=merged)
        return True
