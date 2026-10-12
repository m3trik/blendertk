# !/usr/bin/python
# coding=utf-8
"""Blender scene handlers for the shot sequencer.

Provides :class:`SceneCallbacksMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. A redraw after
the shot store followed Blender's undo/redo, and ``bpy.app.handlers``
frame-change and depsgraph handlers: new keys join the active shot (and grow
it, when that option is on) through a debounced refresh, and the playhead
follows the scene frame.
"""

from blendertk.anim_utils.shots._shots import BlenderShotStore


class SceneCallbacksMixin:
    """Scene event hooks: undo/redo, key edits and time changes refresh the widget."""

    # ---- Blender scene callbacks (bpy.app.handlers) ----------------------

    def _register_scene_callbacks(self) -> None:
        """(Re-)register the undo/redo redraw, frame-change, and depsgraph handlers.

        Replaces mayatk's DG-time + anim-keyframe callbacks.  Undo and redo are
        the STORE's to follow (``BlenderShotStore.follow_native_step``, with
        this panel open or not); the panel only redraws after it
        (:meth:`_on_native_step`), as mayatk's does.
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
        BlenderShotStore.add_native_step_listener(self._on_native_step)

        def _add(handler_list, fn):
            handler_list.append(fn)
            self._handlers.append((handler_list, fn))

        h = bpy.app.handlers
        _add(h.frame_change_post, self._on_frame_change)
        _add(h.depsgraph_update_post, self._on_depsgraph_update)

        # An object rename (no app handler reports one): a rebuild re-points
        # the shots naming it (``reconcile_all_shots``) -- mayatk's DAG-path
        # reconcile, reached the same way.  msgbus takes plain functions only,
        # so the hook goes through this closure; file load drops the
        # subscription and the invalidation re-runs this method.
        def _on_rename(*_args):
            self._on_object_renamed()

        try:
            bpy.msgbus.subscribe_rna(
                key=(bpy.types.Object, "name"),
                owner=self,
                args=(),
                notify=_on_rename,
            )
        except Exception:
            self.logger.debug("object-rename watch unavailable", exc_info=True)

    def _unregister_scene_callbacks(self) -> None:
        """Detach the tracked bpy.app handlers (tolerates ones Blender already cleared)."""
        BlenderShotStore.remove_native_step_listener(self._on_native_step)
        try:
            import bpy

            bpy.msgbus.clear_by_owner(self)
        except Exception:
            pass
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
        self._edited_objects.clear()
        for attr in ("_keyframe_debounce", "_rebuild_debounce"):
            timer = getattr(self, attr, None)
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    pass
                setattr(self, attr, None)

    def _on_object_renamed(self) -> None:
        """An object was renamed: re-point the shots on a plain (debounced)
        rebuild (:meth:`_arm_rebuild_debounce`).

        Not the keyframe debounce: its epilogue adds keyed objects to the
        active shot, falling back to the SELECTION when nothing was keyed --
        and a rename keys nothing, so riding it put whatever was selected
        into the shot (and let Extend to Keys grow it), outside any undo step.
        """
        self._reconcile_needed = True
        try:
            self._arm_rebuild_debounce()
        except Exception:
            self.logger.debug("rename refresh not scheduled", exc_info=True)

    #: The single-shot plain rebuild (:meth:`_arm_rebuild_debounce`).
    _rebuild_debounce = None

    def _arm_rebuild_debounce(self) -> None:
        """(Re)start a 200 ms single-shot rebuild with NO keying epilogue, built
        on first use: for scene changes that key nothing (a rename, a sound
        strip edited in Blender's Sequencer)."""
        from qtpy import QtCore

        if self._rebuild_debounce is None:
            self._rebuild_debounce = QtCore.QTimer()
            self._rebuild_debounce.setSingleShot(True)
            self._rebuild_debounce.setInterval(200)
            self._rebuild_debounce.timeout.connect(self._on_rebuild_debounce_fire)
        self._rebuild_debounce.start()

    def _on_rebuild_debounce_fire(self) -> None:
        """Rebuild the widget from the scene -- membership is left as it is."""
        if self._syncing:
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def _arm_keyframe_debounce(self) -> None:
        """(Re)start the 200 ms single-shot refresh, built on first use."""
        from qtpy import QtCore

        if self._keyframe_debounce is None:
            self._keyframe_debounce = QtCore.QTimer()
            self._keyframe_debounce.setSingleShot(True)
            self._keyframe_debounce.setInterval(200)
            self._keyframe_debounce.timeout.connect(self._on_keyframe_debounce_fire)
        self._keyframe_debounce.start()

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
                widget.set_playhead(scene.frame_current_final)
        except Exception:
            self.logger.debug("frame-change handler failed", exc_info=True)

    def _on_native_step(self, event) -> None:
        """The store followed a Blender undo or redo (putting back the restore
        points of every edit of ours it crossed): redraw once.

        Whether or not it applied any (mayatk redraws only when it did): the
        refresh carries a rename reconcile (an undo can take a rename back),
        and Blender has no per-key callback to report what an unrelated undo
        did to the keys on screen, where mayatk's keyframe callback does.  The
        store's own events during the restore are skipped
        (``_on_store_event``), so a Ctrl+Z is one rebuild -- including the
        dropdown, which lists the shots in order and paints their bounds: an
        undone reorder left it showing the order that had just been undone.
        Only for the store this panel shows (a panel left behind by a scene
        swap or a reload must not redraw for it).
        """
        if self._syncing:
            return
        if event.store is not getattr(self._sequencer, "store", None):
            return
        self._after_shot_change()

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

        Deaf inside a transient edit (``ShotStore.transient_edit``: an export
        or a bridge send, which puts the scene back): its bake banked every
        animated object, and the keying epilogue added a non-member keyed in
        the active shot's frames to its members (2026-10-05, a Unity send).
        """
        if self._syncing or BlenderShotStore.in_transient_edit():
            return
        try:
            import bpy

            screen = getattr(bpy.context, "screen", None)
            if screen is not None and screen.is_animation_playing:
                return
            depsgraph = args[1] if len(args) > 1 else None
            if depsgraph is not None and not self._is_animation_update(depsgraph):
                # A sound strip moved, trimmed, added or removed in Blender's
                # own Sequencer tags no Action -- mayatk hears the same edit
                # through its keyframe callback, its audio being keyed on a
                # carrier -- so compare the strips themselves.  Only when the
                # SCENE is among the updates (a strip edit tags it; a viewport
                # drag, every tick of it, does not).  A plain rebuild redraws
                # the audio track: the keying epilogue would add the SELECTION
                # to the active shot, since a strip edit keys no object.
                scene_tagged = any(
                    isinstance(u.id, bpy.types.Scene) for u in depsgraph.updates
                )
                if scene_tagged and self._sound_strips_changed():
                    self._arm_rebuild_debounce()
                return
            if (
                depsgraph is not None
                and len(self._edited_objects) <= self._EDITED_OBJECT_CAP
            ):
                # Bank NOW — the depsgraph is invalid by the time the 200ms
                # debounce fires.  Object IDs arrive in the same updates
                # batch as the Action (probed pairing: key insert →
                # ['Object', 'Action']); updates carry EVALUATED ids, so
                # take .original.  Past the cap the burst is a bake or an
                # import and the refresh scans the selection instead.
                for u in depsgraph.updates:
                    if isinstance(u.id, bpy.types.Object):
                        self._edited_objects.add(u.id.original.name)
            self._arm_keyframe_debounce()
        except Exception:
            self.logger.debug("depsgraph handler failed", exc_info=True)

    #: The sound strips as :meth:`_sound_strips_changed` last saw them.
    _strip_signature = None

    def _note_sound_strips(self) -> None:
        """Take the strip baseline (the audio track was just built from it)."""
        self._strip_signature = None
        self._sound_strips_changed()

    def _sound_strips_changed(self) -> bool:
        """True when the scene's sound strips differ from the last look.

        Name, placement, trim, channel and mute of every sound strip: what the
        audio track draws.  Cheap -- a scene holds a handful of strips -- which
        matters, since it runs on every depsgraph update that is not a key edit.
        """
        scene = self._scene()
        ed = getattr(scene, "sequence_editor", None) if scene is not None else None
        sig = (
            tuple(
                sorted(
                    (
                        st.name,
                        st.frame_start,
                        st.frame_final_start,
                        st.frame_final_end,
                        st.channel,
                        st.mute,
                    )
                    for st in ed.strips_all
                    if st.type == "SOUND"
                )
            )
            if ed is not None
            else ()
        )
        if sig == self._strip_signature:
            return False
        first = self._strip_signature is None
        self._strip_signature = sig
        if first:
            return False  # the baseline, not an edit
        self._audio_segments_cache = None
        return True

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
            # Nothing to attribute the edits to -- drop them rather than let
            # them join whichever shot is active next (mirror of mayatk).
            self._edited_objects.clear()
            added = False
        if not added:
            self._sync_to_widget()

    #: Above this many banked objects the burst is a bake / import, not a
    #: keying gesture (mayatk's ``_EDITED_CURVE_CAP``): the refresh falls back
    #: to the selection scan instead of probing every object.
    _EDITED_OBJECT_CAP = 400

    def _auto_add_keyed_objects(self, shot_id: int) -> bool:
        """Merge newly-keyed objects into the active shot's objects.

        Candidates come from the objects whose Actions Blender reported as
        updated (banked by :meth:`_on_depsgraph_update`), falling back to
        the current selection when the handler banked nothing (or a bulk
        edit overran the cap) — so scripted or channel-pinned keying on
        UNSELECTED objects still joins the shot (mirror of mayatk's
        banked-curve path).

        A candidate qualifies on "has a key in the shot's range on a content
        channel" (``_is_transform_path``: transforms, visibility, render
        effects) and deliberately NOT on its values varying, as mayatk's
        does: an object keyed on a hold inside the shot -- or keyed for the
        first time -- is still the shot's content, and leaving it out of
        ``shot.objects`` both hid it from the panel and stranded its keys
        when the shot moved.
        """
        if self.sequencer is None:
            return False
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return False
        try:
            import bpy
        except ImportError:
            return False
        candidates = (
            set(self._edited_objects)
            if len(self._edited_objects) <= self._EDITED_OBJECT_CAP
            else set()
        )
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
        new_objects = {
            name
            for name in candidates
            if (obj := bpy.data.objects.get(name)) is not None
            and self.sequencer._has_keys(obj, shot.start, shot.end)
        }
        if not new_objects:
            return False
        # The system's own addition, not a hand edit: the manifest's
        # edited_by_hand reads past it (ShotStore.SYSTEM_MEMBERS; mayatk).
        self.sequencer.store.add_system_members(shot_id, new_objects)
        return True
