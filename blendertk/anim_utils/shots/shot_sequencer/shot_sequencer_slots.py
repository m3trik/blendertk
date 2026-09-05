# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shot Sequencer UI (Blender).

Blender mirror of mayatk's ``shot_sequencer.shot_sequencer_slots`` — bridges the
generic ``uitk`` :class:`SequencerWidget` to the Blender :class:`ShotSequencer`
engine, with the same public slot class (:class:`ShotSequencerSlots`) and widget
signal-wiring table so the tentacle nav stays branch-free.

DCC swaps versus the Maya original:

- **Callbacks → ``bpy.app.handlers``**: Maya's OpenMaya undo/redo, DG time-change,
  and anim-keyframe-edited callbacks become ``undo_post`` / ``redo_post`` /
  ``frame_change_post`` / ``depsgraph_update_post`` handlers (the last debounced),
  registered on panel open and removed on close.
- Scene queries: ``cmds.currentTime`` → ``scene.frame_current``;
  ``cmds.playbackOptions`` → ``scene.frame_start`` / ``frame_end``;
  ``cmds.ls`` / ``objExists`` / ``select`` → ``bpy.data.objects`` / ``select_set``.
- Undo bracket → ``btk.undo_chunk``; scene-change tracking → ``BlenderShotStore``'s
  invalidation registry.
- ``_resolve_full_name`` is identity (Blender names are flat, unique).
- Audio tracks are VSE sound strips (``blendertk.audio_utils.segments.AudioSegment``);
  scrub audio is Blender's own ``scene.use_audio_scrub`` (the widget's ScrubPlayer
  is not bound — two players on one scene would double the grains).
- Transport plays through ``screen.animation_play`` (:class:`_BlenderPlayController`).
- Per-object icons come from :class:`~blendertk.ui_utils.node_icons.NodeIcons`.

Presentation-data helpers live in :mod:`segment_collector`; the interaction
handlers are inherited from the four mixins (gap / clip-motion / nav / marker).
"""

from collections import defaultdict
from typing import Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.anim_utils.shots._shots import BlenderShotStore
from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer
from blendertk.anim_utils.shots.shot_sequencer.gap_manager import GapManagerMixin
from blendertk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin
from blendertk.anim_utils.shots.shot_sequencer.shot_nav import ShotNavMixin
from blendertk.anim_utils.shots.shot_sequencer.marker_manager import MarkerManagerMixin
from blendertk.anim_utils.shots.shot_sequencer.segment_collector import SegmentCollector
from pythontk import StoreEvent
from blendertk.anim_utils._anim_utils import AnimUtils

_KB_LEFT = "←"
_KB_RIGHT = "→"


class _ShotSequencerControllerInternal(object):
    """Internal helpers for ShotSequencerController."""

    @staticmethod
    def _scene():
        """Active Blender scene, or ``None`` headless."""
        try:
            import bpy
        except ImportError:
            return None
        return bpy.context.scene


class ShotSequencerController(
    GapManagerMixin,
    ClipMotionMixin,
    ShotNavMixin,
    MarkerManagerMixin,
    ptk.LoggingMixin,
    _ShotSequencerControllerInternal,
):
    """Business logic controller bridging SequencerWidget ↔ ShotSequencer."""

    #: Frames the context-menu padding prompt opens on.  A beat of room is
    #: what the gesture is usually for; the prompt then remembers whatever
    #: the user actually typed for the rest of the session.
    CONTEXT_SPACE_FRAMES = 15.0

    def __init__(self, slots_instance, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui
        self._sequencer: Optional[ShotSequencer] = None
        self._handlers: list = []  # (handler_list, fn) pairs for bpy.app.handlers
        self._keyframe_debounce = None
        self._syncing = False
        self._syncing_playhead = False
        self._store_listener_bound = False
        self._shot_display_mode = "current"  # "current" | "adjacent" | "all"
        self._segment_cache: dict = {}
        self._sub_row_cache: dict = {}
        self._color_map_cache: Optional[dict] = None
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        # Objects whose Actions Blender reported as updated since the last
        # refresh — banked at depsgraph-handler time (the depsgraph is
        # invalid by the time the debounce fires); consumed by
        # _auto_add_keyed_objects so scripted / channel-pinned keying on
        # UNSELECTED objects still joins the active shot (mirror of
        # mayatk's banked-curve path).
        self._edited_objects: set = set()
        self._shifted_out_keys: dict = {}
        # Last amount the padding prompt was answered with — padding a run of
        # shots by the same beat is the common case, so the field opens on it.
        self._context_space_frames: float = self.CONTEXT_SPACE_FRAMES
        self._prev_action = None
        self._next_action = None
        self._view_mode_action = None
        self._cmb_mode_widget = None
        self._playback_range_mode = "follows_view"
        self._track_order_scope = "visible"
        self._show_internal_holds = False  # flat-key spans in attribute sub-rows
        self._holds_action = None  # OptionBox action for the holds toggle
        self._cmb_mode = "shots"
        self._transport_controls = None

        self._register_scene_callbacks()
        self._bind_store_listener()
        self._bind_invalidation_listener()
        self.ui.destroyed.connect(lambda *_: self.remove_callbacks())
        self.logger.debug("ShotSequencerController initialized.")

    # ---- footer helpers --------------------------------------------------

    def _set_footer(self, text: str, *, color: str = "") -> None:
        footer = getattr(self.ui, "footer", None)
        if footer is None:
            return
        label = footer._status_label
        if color:
            label.setStyleSheet(
                f"background: transparent; border: none; color: {color};"
            )
        else:
            label.setStyleSheet("background: transparent; border: none;")
        footer.setText(text)

    def _update_footer_shot_summary(self) -> None:
        if self.sequencer is None:
            self._set_footer("No shots defined.")
            return
        shot_id = self.active_shot_id
        shot = self.sequencer.shot_by_id(shot_id) if shot_id is not None else None
        if shot is None:
            self._set_footer("No shot selected.")
            return
        dur = int(shot.end - shot.start)
        n_obj = len(shot.objects)
        n_shots = len(self.sequencer.shots)
        idx = next(
            (
                i
                for i, s in enumerate(self.sequencer.sorted_shots())
                if s.shot_id == shot_id
            ),
            0,
        )
        parts = [
            f"[{idx + 1}/{n_shots}]",
            f"{dur}f",
            f"{n_obj} object{'s' if n_obj != 1 else ''}",
        ]
        self._set_footer(" · ".join(parts))

    # ---- sequencer property (lazy from store) ----------------------------

    @property
    def sequencer(self) -> Optional[ShotSequencer]:
        if self._sequencer is None:
            store = BlenderShotStore.active()
            self._sequencer = ShotSequencer(store=store)
            self.logger.debug(
                "Lazy-initialized ShotSequencer from BlenderShotStore.active()."
            )
        return self._sequencer

    @sequencer.setter
    def sequencer(self, value):
        self._sequencer = value

    # ---- store observers -------------------------------------------------

    def _bind_store_listener(self) -> None:
        if self._store_listener_bound:
            return
        try:
            store = BlenderShotStore.active()
            store.add_listener(self._on_store_event)
            self._bound_store = store
            self._store_listener_bound = True
        except Exception:
            self.logger.warning("store listener bind failed", exc_info=True)

    def _unbind_store_listener(self) -> None:
        if not self._store_listener_bound:
            return
        try:
            store = getattr(self, "_bound_store", None)
            if store is not None:
                store.remove_listener(self._on_store_event)
                self._bound_store = None
        except Exception:
            self.logger.debug("store listener unbind failed", exc_info=True)
        self._store_listener_bound = False

    def _bind_invalidation_listener(self) -> None:
        BlenderShotStore.add_invalidation_listener(self._on_store_invalidated)

    def _on_store_invalidated(self, event=None) -> None:
        """Rebind to the new active store after a scene swap."""
        self._unbind_store_listener()
        self._sequencer = None
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        # The boundary ledger needs no clearing here — it lives on the
        # STORE, so the new scene's store starts with a fresh one.
        self._edited_objects.clear()
        self._shifted_out_keys.clear()
        self._bind_store_listener()
        # Blender clears non-persistent app-handlers on File ▸ New/Open, so
        # re-attach them here (idempotent) or the live playhead/keyframe refresh
        # would be dead for the rest of the session after a scene swap.
        self._register_scene_callbacks()
        self._sync_combobox()
        self._sync_to_widget()
        if self._cmb_mode == "markers":
            self._sync_combobox()

    def _on_store_event(self, event: StoreEvent) -> None:
        if self._syncing or self.sequencer is None:
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        self._sync_combobox()
        self._sync_to_widget()
        widget = self._get_sequencer_widget()
        if widget is not None and hasattr(widget, "shots_changed"):
            widget.shots_changed.emit()
            if hasattr(widget, "app_event"):
                widget.app_event.emit(event.name, event)

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
            scene = _ShotSequencerControllerInternal._scene()
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
            import bpy
        except ImportError:
            return False
        candidates = set(self._edited_objects)
        self._edited_objects.clear()
        if not candidates:
            candidates = {o.name for o in bpy.context.selected_objects}
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

    # ---- name resolution (flat in Blender) -------------------------------

    @staticmethod
    def _resolve_full_name(name: str) -> str:
        """Identity — Blender object names are flat and unique.

        Strips the audio-track prefix (``\u266b ``) so an audio track label maps
        back to its strip name, like the Maya original.
        """
        if name.startswith("\u266b "):
            name = name[2:]
        return name.rsplit("|", 1)[-1] if "|" in name else name

    _node_icons_cls_cache = ...  # sentinel — not yet resolved

    @classmethod
    def _try_load_blender_icons(cls):
        """Return :class:`NodeIcons` (``Object.type`` → uitk icon), memoised per process."""
        if cls._node_icons_cls_cache is not ...:
            return cls._node_icons_cls_cache
        try:
            from blendertk.ui_utils.node_icons import NodeIcons

            cls._node_icons_cls_cache = NodeIcons
        except ImportError:
            cls._node_icons_cls_cache = None
        return cls._node_icons_cls_cache

    def _select_and_show(self, obj_names) -> None:
        """Select the given objects (Blender Outliner/Graph Editor follow selection)."""
        try:
            import bpy
        except ImportError:
            return
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        active = None
        # Selection state is a view-layer concept: ``select_set`` raises on objects
        # outside the active view layer (excluded collection, another scene — the
        # ``bpy.data.objects`` lookup is scene-wide), so skip those instead of
        # letting one abort the loop mid-way.
        view_layer = bpy.context.view_layer
        for name in obj_names:
            o = bpy.data.objects.get(name)
            if o is not None and o.name in view_layer.objects:
                o.select_set(True)
                active = o
        try:
            bpy.context.view_layer.objects.active = active
        except Exception:
            pass

    def _reveal_in_outliner(self, obj_names) -> None:
        """Select and scroll the Outliner to the object(s)."""
        from blendertk.ui_utils._ui_utils import UiUtils

        UiUtils.reveal_in_outliner(obj_names)

    def _open_spreadsheet(self, track_names) -> None:
        """Maya's Attribute Spreadsheet has no direct Blender analogue — no-op."""
        self._set_footer("Attribute spreadsheet is Maya-only.")

    # -- zone context menus ------------------------------------------------

    def on_zone_context_menu(self, zone: str, time: float, global_pos) -> None:
        """``"shot_lane"`` is every click at a time some shot covers, at any
        height -- the widget resolves that.  Outside every shot the widget's
        own menu stands alone; inside one it is folded into the shot menu."""
        if zone == "shot_lane":
            self._show_shot_lane_context_menu(time, global_pos)
            return
        widget = self._get_sequencer_widget()
        if widget is not None:
            widget._timeline._show_default_context_menu(widget, time, global_pos)

    def _show_shot_lane_context_menu(self, time: float, global_pos) -> None:
        from qtpy import QtWidgets

        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        clicked_shot = self._find_shot_at_time(time)
        menu = QtWidgets.QMenu(widget)
        acts = {}
        if clicked_shot is not None:
            sid = clicked_shot.shot_id
            neighbours = self._neighbour_shots(sid)
            acts["select"] = menu.addAction(f'Select "{clicked_shot.name}"')
            acts["edit"] = menu.addAction(f'Edit "{clicked_shot.name}"\u2026')
            menu.addSeparator()
            acts["before"] = menu.addAction("Insert Shot Before")
            acts["after"] = menu.addAction("Insert Shot After")
            acts["split"] = menu.addAction(f"Split Here ({time:.0f})")
            # A split needs room on both sides; on a bound it divides nothing.
            acts["split"].setEnabled(
                clicked_shot.start + 1e-6 < time < clicked_shot.end - 1e-6
            )
            for key, label in (
                ("merge_prev", "Merge with Previous"),
                ("merge_next", "Merge with Next"),
            ):
                acts[key] = menu.addAction(label)
                acts[key].setEnabled(neighbours[key] is not None)
            menu.addSeparator()
            # Both prompt for the amount (the ellipsis says so) rather than
            # spending a fixed step: how much room a shot needs is the whole
            # question, and a fixed step meant re-opening the menu to get it.
            acts["add_lead"] = menu.addAction("Add Leading Frames\u2026")
            acts["add_tail"] = menu.addAction("Add Trailing Frames\u2026")
            acts["trim"] = menu.addAction("Trim Empty Space")
            acts["trim_lead"] = menu.addAction("Trim Leading Space")
            acts["trim_tail"] = menu.addAction("Trim Trailing Space")
            menu.addSeparator()
            acts["delete"] = menu.addAction(f'Delete "{clicked_shot.name}"\u2026')
            menu.addSeparator()
        acts["new"] = menu.addAction("New Shot")
        acts["refresh"] = menu.addAction("Refresh")
        # The timeline's own actions (add marker, display toggles) are folded
        # in rather than living in a rival menu the user has to find by
        # right-clicking somewhere a shot does NOT cover.
        handled = widget._timeline.add_default_context_actions(menu, time)

        chosen = menu.exec_(global_pos)
        if chosen is None:
            return
        if handled(chosen):
            return
        picked = next((k for k, a in acts.items() if a is chosen), None)
        if picked is None:
            return
        if picked == "new":
            self._create_shot_one_click()
        elif picked == "refresh":
            self.refresh()
        elif clicked_shot is None:
            return
        elif picked == "select":
            self.on_shot_block_clicked(clicked_shot.name)
        elif picked == "edit":
            self._edit_shot_dialog(clicked_shot)
        elif picked == "before":
            self._insert_shot(clicked_shot.shot_id, before=True)
        elif picked == "after":
            self._insert_shot(clicked_shot.shot_id, before=False)
        elif picked == "split":
            self.split_shot_at(clicked_shot.shot_id, time)
        elif picked in ("merge_prev", "merge_next"):
            other = neighbours[picked]  # resolved once, when the menu was built
            if other is not None:
                self.merge_shot_with(clicked_shot.shot_id, other.shot_id)
        elif picked in ("add_lead", "add_tail"):
            self._prompt_shot_space(
                clicked_shot,
                edge="leading" if picked == "add_lead" else "trailing",
            )
        elif picked == "trim":
            self._trim_shot(clicked_shot.shot_id)
        elif picked == "trim_lead":
            self._trim_shot(clicked_shot.shot_id, edge="leading")
        elif picked == "trim_tail":
            self._trim_shot(clicked_shot.shot_id, edge="trailing")
        elif picked == "delete":
            self.delete_shot(clicked_shot.shot_id)

    def _neighbour_shots(self, shot_id: int) -> dict:
        """``{"merge_prev": shot|None, "merge_next": shot|None}`` around *shot_id*."""
        shots = self.sequencer.sorted_shots() if self.sequencer else []
        idx = next((i for i, s in enumerate(shots) if s.shot_id == shot_id), None)
        if idx is None:
            return {"merge_prev": None, "merge_next": None}
        return {
            "merge_prev": shots[idx - 1] if idx > 0 else None,
            "merge_next": shots[idx + 1] if idx + 1 < len(shots) else None,
        }

    def _after_shot_change(self, shot_id=None) -> None:
        """Rebuild everything a shot add/remove/resize invalidates."""
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_combobox()
        self._sync_to_widget(shot_id=shot_id)
        self._apply_view_playback_range()

    def delete_shot(self, shot_id: int) -> None:
        """Delete *shot_id* with its contents, closing the timeline behind it.

        This is what "delete a shot" means from the timeline: the shot, the
        animation it owns, and the space it occupied all go, and the next shot
        lands where this one started.  The confirmation says so, because the
        keys are the animator's and a menu click should not eat them silently.
        """
        from qtpy import QtWidgets

        if self.sequencer is None:
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return
        reply = QtWidgets.QMessageBox.question(
            self._get_sequencer_widget() or self.ui,
            "Delete Shot",
            f'Delete "{shot.name}" [{shot.start:.0f}\u2013{shot.end:.0f}]\n'
            "\u2014 its keyframes, closing the gap behind it?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        store = self.sequencer.store
        self._save_shot_state()
        try:
            with CoreUtils.undo_chunk():
                result = self.sequencer.delete_shot(shot_id)
        except Exception:
            self._discard_shot_state()
            raise
        store.set_active_shot(None)
        self._after_shot_change()
        cut = result.get("curves_cut", 0)
        closed = result.get("closed", 0.0)
        parts = [f"Deleted {result.get('name', shot.name)}"]
        if cut:
            parts.append(f"{cut} curve(s) cleared")
        if closed:
            parts.append(f"closed {closed:.0f}f")
        self._set_footer(" \u00b7 ".join(parts))

    def merge_shot_with(self, shot_id: int, other_id: int) -> None:
        """Fuse two neighbouring shots into one spanning both."""
        if self.sequencer is None:
            return
        store = self.sequencer.store
        self._save_shot_state()
        try:
            with CoreUtils.undo_chunk():
                merged = self.sequencer.merge_shots([shot_id, other_id])
        except Exception:
            self._discard_shot_state()
            raise
        store.set_active_shot(merged.shot_id)
        self._after_shot_change(shot_id=merged.shot_id)
        self._set_footer(
            f"Merged into {merged.name} \u00b7 {merged.start:.0f}\u2013{merged.end:.0f}"
        )

    def split_shot_at(self, shot_id: int, time: float) -> None:
        """Cut *shot_id* in two at *time*, leaving its content where it is."""
        if self.sequencer is None:
            return
        store = self.sequencer.store
        self._save_shot_state()
        try:
            with CoreUtils.undo_chunk():
                tail = self.sequencer.split_shot(shot_id, time)
        except ValueError as exc:
            self._discard_shot_state()
            self._set_footer(str(exc))
            return
        except Exception:
            self._discard_shot_state()
            raise
        store.set_active_shot(tail.shot_id)
        self._after_shot_change(shot_id=tail.shot_id)
        self._set_footer(
            f"Split at {time:.0f} \u00b7 {tail.name} {tail.start:.0f}\u2013{tail.end:.0f}"
        )

    def _prompt_shot_space(self, shot, edge: str) -> None:
        """Ask how many frames to pad *shot* at *edge*, then pad it.

        The amount is the whole question a padding gesture asks, so it is
        typed rather than assumed: the field opens on the last answer (the
        class default on the first use) and a run of shots padded by the same
        beat costs one keystroke each.  A negative amount removes room —
        :meth:`ShotSequencer.add_shot_space` clamps it to what is actually
        empty — so the validator gates on "a number that is not zero", not on
        sign.
        """

        def _parse(text):
            try:
                return float(str(text).strip())
            except (TypeError, ValueError):
                return None

        answer = self.sb.input_dialog(
            title=f"Add {edge.capitalize()} Frames",
            label=f'Frames of {edge} space for "{shot.name}":',
            text=f"{self._context_space_frames:g}",
            parent=self._get_sequencer_widget() or self.ui,
            validate=lambda t: (_parse(t) or 0.0) != 0.0,
            error_text="Enter a non-zero number of frames.",
        )
        frames = _parse(answer)
        if not frames:
            return  # cancelled, or an amount that would move nothing
        self._context_space_frames = frames
        self._add_shot_space(shot.shot_id, frames, edge=edge)

    def _add_shot_space(self, shot_id: int, frames: float, edge: str) -> None:
        """Pad *shot_id* by *frames* at *edge*, undoable, then refresh.

        The context-menu twin of the Shots panel's Add Space field, routed
        through the same engine call so both mean exactly the same thing.
        """
        from pythontk.core_utils.engines.shots.shot_plan import ShotBoundaryConflict

        if self.sequencer is None:
            return
        self._save_shot_state()
        try:
            with CoreUtils.undo_chunk():
                head, tail = self.sequencer.add_shot_space(shot_id, frames, edge=edge)
        except ShotBoundaryConflict as exc:
            # Declined before writing anything, so the restore point goes too
            # (mirrors mayatk): a refusal is an answer, not a crash.
            self._discard_shot_state()
            self.logger.warning(str(exc))
            self._set_footer(str(exc))
            return
        if abs(head) < 1e-6 and abs(tail) < 1e-6:
            self._discard_shot_state()
            self._set_footer(f"Add {edge} space: nothing to do")
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._sync_combobox()
        self._apply_view_playback_range()
        self._set_footer(f"Added {tail - head:.0f}f of {edge} space")

    def _trim_shot(self, shot_id: int, edge: str = "both") -> None:
        """Trim empty space from *shot_id*, undoable, then refresh the widget.

        *edge* selects which end gives way: ``"both"`` (default), ``"leading"``
        or ``"trailing"``.
        """
        if self.sequencer is None:
            return
        self._save_shot_state()
        with CoreUtils.undo_chunk():
            head, tail = self.sequencer.trim_shot_to_content(shot_id, edge=edge)
        if abs(head) < 1e-6 and abs(tail) < 1e-6:
            # Nothing moved: drop the snapshot and skip the rebuild.  (The
            # chunk above still deposited one empty native undo step —
            # CoreUtils.undo_chunk pushes unconditionally; a dry-probe
            # would duplicate the engine's whole content-bounds scan here,
            # so the snapshot discard is the load-bearing half.)
            self._discard_shot_state()
            self._set_footer("Nothing to trim — the shot already fits its content.")
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._sync_combobox()
        self._apply_view_playback_range()
        self._set_footer(
            f"Trimmed {abs(head):.0f}f from the head, {abs(tail):.0f}f from the tail"
        )

    def _insert_shot(self, anchor_shot_id: int, before: bool) -> None:
        """Insert a new shot before or after *anchor_shot_id*.

        Downstream shots (and their keys and audio) ripple to open the space,
        so this never overwrites existing content.
        """
        if self.sequencer is None:
            return
        seq = self.sequencer
        store = seq.store
        anchor = seq.shot_by_id(anchor_shot_id)
        if anchor is None:
            return
        sorted_s = seq.sorted_shots()
        existing_names = {sh.name for sh in sorted_s}
        idx = next(
            (i for i, sh in enumerate(sorted_s) if sh.shot_id == anchor_shot_id), 0
        )
        n = len(sorted_s) + 1
        while f"Shot {n}" in existing_names:
            n += 1

        from pythontk.core_utils.engines.shots.manifest.behaviors import Behaviors

        duration = Behaviors.compute_duration([], fallback=100.0)
        self._save_shot_state()
        try:
            with CoreUtils.undo_chunk():
                shot = seq.insert_shot(
                    name=f"Shot {n}",
                    duration=duration,
                    at_position=(idx + 1) if before else (idx + 2),
                )
        except Exception:
            self._discard_shot_state()
            raise
        store.set_active_shot(shot.shot_id)
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_combobox()
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is not None:
            for i in range(cmb.count()):
                if cmb.itemData(i) == shot.shot_id:
                    cmb.blockSignals(True)
                    cmb.setCurrentIndex(i)
                    cmb.blockSignals(False)
                    break
        self.select_shot(shot.shot_id)
        self._sync_to_widget()
        self._set_footer(f"Inserted {shot.name} · {shot.start:.0f}–{shot.end:.0f}")

    def _create_shot_one_click(self) -> None:
        if self.sequencer is None:
            return
        store = self.sequencer.store
        gap = store.gap or 0
        existing = self.sequencer.sorted_shots()
        existing_names = {s.name for s in existing}
        idx = len(existing) + 1
        while f"Shot {idx}" in existing_names:
            idx += 1
        name = f"Shot {idx}"
        from pythontk.core_utils.engines.shots.manifest.behaviors import Behaviors

        duration = Behaviors.compute_duration([], fallback=100.0)
        # Sequencer-level append (insert_shot, no anchor): probes the last
        # shot's trailing envelope content so the new shot is never built
        # over fade tails / trailing strips; snapshot makes it undoable via
        # the ledger's membership diff.
        self._save_shot_state()
        try:
            shot = self.sequencer.insert_shot(name=name, duration=duration, gap=gap)
        except Exception:
            self._discard_shot_state()
            raise
        self._sync_combobox()
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is not None:
            for i in range(cmb.count()):
                if cmb.itemData(i) == shot.shot_id:
                    cmb.setCurrentIndex(i)
                    break
        self.select_shot(shot.shot_id)
        self._sync_to_widget()
        self._set_footer(f"Created {shot.name} · {shot.start:.0f}–{shot.end:.0f}")

    def _find_shot_at_time(self, time: float):
        if self.sequencer is None:
            return None
        for s in self.sequencer.sorted_shots():
            if s.start <= time <= s.end:
                return s
        return None

    def _on_shot_switch_requested(self, time: float) -> None:
        shot = self._find_shot_at_time(time)
        if shot is not None:
            self.on_shot_block_clicked(shot.name)

    def _edit_shot_dialog(self, shot) -> None:
        self.sequencer.store.set_active_shot(shot.shot_id)
        self.sb.handlers.marking_menu.show("shots")

    def _set_view_mode(self, mode: str) -> None:
        self._shot_display_mode = mode
        if self._playback_range_mode != "off":
            self._apply_view_playback_range()
        self._sync_to_widget()

    def _set_playback_range_mode(self, mode: str) -> None:
        self._playback_range_mode = mode
        if mode != "off":
            self._apply_view_playback_range()

    def _set_cmb_mode(self, mode: str) -> None:
        self._cmb_mode = mode
        cmb_mode = self._cmb_mode_widget
        if cmb_mode is not None:
            idx = 1 if mode == "markers" else 0
            if cmb_mode.currentIndex() != idx:
                cmb_mode.blockSignals(True)
                cmb_mode.setCurrentIndex(idx)
                cmb_mode.blockSignals(False)
        self._sync_combobox()

    # ---- widget ↔ engine sync -------------------------------------------

    @property
    def active_shot_id(self) -> Optional[int]:
        cmb = getattr(self.ui, "cmb_shot", None)
        if self._cmb_mode != "markers" and cmb is not None and cmb.currentIndex() >= 0:
            sid = cmb.itemData(cmb.currentIndex())
            if sid is not None:
                return sid
        if self.sequencer and self.sequencer.shots:
            store_active = self.sequencer.store.active_shot_id
            if store_active is not None and self.sequencer.shot_by_id(store_active):
                return store_active
            return self.sequencer.sorted_shots()[0].shot_id
        return None

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

    def _visible_shots(self, active_shot):
        if self._shot_display_mode == "current":
            return [active_shot]
        sorted_shots = self.sequencer.sorted_shots()
        if self._shot_display_mode == "all":
            return sorted_shots
        idx = next(
            (i for i, s in enumerate(sorted_shots) if s.shot_id == active_shot.shot_id),
            None,
        )
        if idx is None:
            return [active_shot]
        result = []
        if idx > 0:
            result.append(sorted_shots[idx - 1])
        result.append(active_shot)
        if idx < len(sorted_shots) - 1:
            result.append(sorted_shots[idx + 1])
        return result

    def _get_sequencer_widget(self):
        """Return the promoted SequencerWidget, or None (placeholder QSplitter)."""
        w = getattr(self.ui, "sequencer_widget", None)
        if w is not None and hasattr(w, "add_track"):
            return w
        return None

    def refresh(self) -> None:
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        self._sync_to_widget()

    def _sync_to_widget(
        self, shot_id: Optional[int] = None, *, frame: bool = False
    ) -> None:
        widget, shot = self._resolve_sync_target(shot_id)
        if widget is None or shot is None:
            widget = self._get_sequencer_widget()
            if (
                widget is not None
                and self.sequencer is not None
                and not self.sequencer.shots
            ):
                self._sync_shotless(widget, frame=frame)
            return
        h_scroll, zoom, expanded_names = self._save_viewport_state(widget)
        visible_shots = self._visible_shots(shot)
        bulk = getattr(widget, "bulk_updates", None)
        if callable(bulk):
            with bulk():
                self._rebuild_content(widget, shot, visible_shots)
                self._rebuild_decoration(widget, shot, visible_shots)
        else:
            self._rebuild_content(widget, shot, visible_shots)
            self._rebuild_decoration(widget, shot, visible_shots)
        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
        self._update_footer_shot_summary()

    def _sync_shotless(self, widget, *, frame: bool = False) -> None:
        """Scene-wide animation display when no shots exist."""
        from pythontk.core_utils.engines.shots.shot_model import ShotBlock

        scene = _ShotSequencerControllerInternal._scene()
        if scene is None:
            return
        start, end = float(scene.frame_start), float(scene.frame_end)
        h_scroll, zoom, expanded_names = self._save_viewport_state(widget)
        widget.clear()
        self._sync_header_settings(widget)
        if end <= start:
            self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
            self._set_footer("No valid playback range.")
            return
        discovered = self.sequencer._find_keyed_transforms(start, end)
        if not discovered:
            self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
            self._set_footer("No animated objects in scene.")
            return
        scene_shot = ShotBlock(
            shot_id=-1,
            name="Scene",
            start=start,
            end=end,
            objects=sorted(set(discovered)),
        )
        from blendertk.anim_utils.segment_keys import SegmentKeys

        # The synthetic scene_shot (id -1) isn't in the store — collect its
        # motion segments directly (same pipeline as collect_object_segments).
        segments = SegmentKeys.collect_segments(
            scene_shot.objects,
            split_static=True,
            time_range=(start, end),
            ignore_holds=True,
            ignore_visibility_holds=True,
            motion_only=True,
            motion_rate=1e-3,
        )
        segments_by_shot = {scene_shot.shot_id: segments}
        all_objects = set(scene_shot.objects) | {seg["obj"] for seg in segments}
        track_ids = self._build_tracks(
            widget, all_objects, all_objects, active_shot=scene_shot
        )
        self._build_clips(widget, scene_shot, [scene_shot], segments_by_shot, track_ids)
        self._ensure_scene_attr_colors(widget)
        self._build_audio_tracks(widget, scene_shot, [scene_shot])
        widget.set_playhead(scene.frame_current)
        widget.set_active_range(start, end)
        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
        n = len(scene_shot.objects)
        self._set_footer(
            f"Scene  {start:.0f}–{end:.0f}  ·  {n} object{'s' if n != 1 else ''}"
        )

    def _resolve_sync_target(self, shot_id=None):
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return None, None
        if shot_id is None:
            shot_id = self.active_shot_id
        if shot_id is None:
            return None, None
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return None, None
        return widget, shot

    def _save_viewport_state(self, widget):
        try:
            h_scroll = widget._timeline.horizontalScrollBar().value()
            zoom = widget._timeline.pixels_per_unit
            expanded_names = set()
            for tid in list(widget._expanded_tracks):
                td = widget.get_track(tid)
                if td is not None:
                    expanded_names.add(td.name)
            return h_scroll, zoom, expanded_names
        except Exception:
            return 0, None, set()

    def _rebuild_content(self, widget, shot, visible_shots) -> None:
        self._syncing = True
        try:
            widget.clear(keep_range_highlight=True)
            self._sub_row_cache.clear()
            self._sync_header_settings(widget)
            if self._reconcile_needed:
                if self.sequencer.reconcile_all_shots():
                    self._segment_cache.clear()
                self._reconcile_needed = False
            segments_by_shot, all_objects = SegmentCollector.collect_segments(
                self.sequencer,
                shot,
                visible_shots,
                self._segment_cache,
                self._shifted_out_keys,
                self.logger,
            )
            if self._track_order_scope == "global":
                for s in self.sequencer.sorted_shots():
                    all_objects.update(s.objects)
            active_objects = SegmentCollector.active_object_set(shot, segments_by_shot)
            track_ids = self._build_tracks(
                widget, all_objects, active_objects, active_shot=shot
            )
            self._build_clips(widget, shot, visible_shots, segments_by_shot, track_ids)
            self._ensure_scene_attr_colors(widget)
            self._build_audio_tracks(widget, shot, visible_shots)
        finally:
            self._syncing = False

    def _rebuild_decoration(self, widget, shot, visible_shots) -> None:
        scene = _ShotSequencerControllerInternal._scene()
        current_time = scene.frame_current if scene is not None else shot.start
        widget.set_playhead(current_time)
        widget.set_hidden_tracks(sorted(self.sequencer.hidden_objects))
        widget.set_active_range(shot.start, shot.end)
        widget.set_range_highlight(shot.start, shot.end)
        all_sorted = self.sequencer.sorted_shots()
        store = self.sequencer.store
        shot_blocks = [
            {
                "id": s.shot_id,
                "name": s.name,
                "start": s.start,
                "end": s.end,
                "active": s.shot_id == shot.shot_id,
            }
            for s in all_sorted
        ]
        widget.set_shot_blocks(shot_blocks)
        for m in self.sequencer.markers:
            widget.add_marker(
                time=m["time"],
                note=m.get("note", ""),
                color=m.get("color"),
                draggable=m.get("draggable", True),
                style=m.get("style", "triangle"),
                line_style=m.get("line_style", "dashed"),
                opacity=m.get("opacity", 1.0),
            )
        for i in range(len(all_sorted) - 1):
            left, right = all_sorted[i], all_sorted[i + 1]
            if right.start - left.end > -0.5:
                locked = store.is_gap_locked(left.shot_id, right.shot_id)
                widget.add_gap_overlay(left.end, right.start, locked=locked)
        # The last shot has no following shot, so the loop above leaves it with
        # no drag handle at its end — the one shot that could not be resized
        # like the others.  A zero-width tail overlay supplies that handle; its
        # left edge IS the shot's end, which on_gap_left_resized already
        # knows how to act on.
        if all_sorted:
            widget.add_gap_overlay(all_sorted[-1].end, all_sorted[-1].end, tail=True)
        for s in all_sorted:
            if s.shot_id != shot.shot_id:
                widget.add_range_overlay(s.start, s.end, color="#000000", alpha=40)

    #: Set by the first :meth:`_restore_viewport`; see its docstring.
    _viewport_framed = False

    def _restore_viewport(self, widget, frame, h_scroll, zoom, expanded_names) -> None:
        """Restore scroll/zoom/expansion and trigger geometry recalculation.

        The FIRST restore always frames: there is no prior view to preserve
        on the first build, and the panel opening on frame 0 of a
        several-thousand-frame scene starts every session by hunting for the
        shot being worked on.  (``SequencerWidget.frame_on_first_show`` does
        the same at show time; whichever runs last frames the same range, so
        the two agree however the panel is brought up.)
        """
        frame = frame or not self._viewport_framed
        self._viewport_framed = True
        try:
            if frame:
                widget._timeline._refresh_all()
                widget.frame_shot()
            else:
                if zoom is not None:
                    widget._timeline._pixels_per_unit = zoom
                widget._timeline._refresh_all()
                widget._timeline.horizontalScrollBar().setValue(h_scroll)
            widget.sub_row_provider = self._provide_sub_rows
            if expanded_names:
                for td in widget.tracks():
                    if td.name in expanded_names:
                        widget.expand_track(td.track_id)
        except Exception:
            self.logger.debug("restore_viewport failed", exc_info=True)

    def _sync_header_settings(self, widget) -> None:
        spn_snap = getattr(self.ui, "spn_snap", None)
        if spn_snap is not None:
            widget.snap_interval = float(spn_snap.value())
        # Read on every rebuild so the widget also picks up the value the
        # checkbox restored from settings on load.
        chk_snap_keys = getattr(self.ui, "chk_snap_to_keys", None)
        if chk_snap_keys is not None:
            widget.snap_to_keys = bool(chk_snap_keys.isChecked())
        spn_gap = getattr(self.ui, "spn_gap", None)
        if spn_gap is not None:
            stored_gap = self.sequencer.store.gap if self.sequencer else 0
            spn_gap.blockSignals(True)
            spn_gap.setValue(int(stored_gap))
            spn_gap.blockSignals(False)
        if self._color_map_cache is None:
            from uitk.widgets.sequencer._sequencer import (
                AttributeColorDialog,
                _DEFAULT_ATTRIBUTE_COLORS,
            )
            from uitk.managers.settings_manager import SettingsManager

            color_settings = SettingsManager(
                namespace=AttributeColorDialog._SETTINGS_NS
            )
            color_map = dict(_DEFAULT_ATTRIBUTE_COLORS)
            for key in color_settings.keys():
                val = color_settings.value(key)
                if val:
                    color_map[key] = val
            self._color_map_cache = color_map
        widget.attribute_colors = self._color_map_cache

    _AUTO_PALETTE = [
        "#5B8BD4",
        "#6EBF6E",
        "#D4A65B",
        "#C45C5C",
        "#8E6FBF",
        "#5BBFB4",
        "#BF6E8E",
        "#8EB05B",
    ]

    def _ensure_scene_attr_colors(self, widget) -> None:
        if widget is None:
            return
        from hashlib import md5

        color_map = widget.attribute_colors
        changed = False
        for clip in widget._clips.values():
            for attr in clip.data.get("attributes", []):
                if attr not in color_map:
                    idx = int(md5(attr.encode()).hexdigest(), 16) % len(
                        self._AUTO_PALETTE
                    )
                    color_map[attr] = self._AUTO_PALETTE[idx]
                    changed = True
        if changed:
            widget.attribute_colors = color_map

    def _build_tracks(
        self, widget, all_objects, active_objects, active_shot=None
    ) -> dict:
        from pythontk import SHOT_PALETTE

        obj_classes = (
            active_shot.classify_objects()
            if active_shot and hasattr(active_shot, "classify_objects")
            else {}
        )
        track_ids: dict = {}
        _NOT_FOUND_COLOR = "#E0A0A0"
        if self._track_order_scope == "global":
            ordered = sorted(all_objects)
        else:
            active = sorted(o for o in all_objects if o in active_objects)
            inactive = sorted(o for o in all_objects if o not in active_objects)
            ordered = active + inactive

        try:
            import bpy

            existing_set = {n for n in ordered if bpy.data.objects.get(n) is not None}
        except ImportError:
            existing_set = set(ordered)

        node_icons_cls = self._try_load_blender_icons()
        for obj_name in ordered:
            if self.sequencer.is_object_hidden(obj_name):
                continue
            exists = obj_name in existing_set
            if not exists and not self.sequencer.store.is_object_pinned(obj_name):
                continue
            in_active = obj_name in active_objects
            icon = (
                node_icons_cls.get_icon(obj_name)
                if (node_icons_cls and exists)
                else None
            )
            if not exists and icon is None:
                from uitk.managers.icon_manager import IconManager

                icon = IconManager.get("close", size=(16, 16), color=_NOT_FOUND_COLOR)
            color_kw: dict = {}
            status = obj_classes.get(obj_name, "valid")
            if status != "valid":
                pair = SHOT_PALETTE.get(status)
                if pair is not None:
                    fg, bg = pair[0], pair[1]
                    if bg:
                        color_kw["color"] = bg
                    if fg:
                        color_kw["text_color"] = fg
            tid = widget.add_track(
                obj_name.split("|")[-1],
                icon=icon,
                dimmed=not in_active or not exists,
                italic=not in_active and exists,
                **color_kw,
            )
            track_ids[obj_name] = tid
        return track_ids

    def _build_clips(self, widget, shot, visible_shots, segments_by_shot, track_ids):
        from pythontk import SHOT_PALETTE

        for vs in visible_shots:
            is_active = vs.shot_id == shot.shot_id
            segs = segments_by_shot.get(vs.shot_id, [])
            obj_classes = (
                vs.classify_objects() if hasattr(vs, "classify_objects") else {}
            )
            by_obj: dict = defaultdict(list)
            for seg in segs:
                by_obj[seg["obj"]].append(seg)
            store = self.sequencer.store if self.sequencer else None

            for obj_name in sorted(set(vs.objects) | set(by_obj)):
                if self.sequencer.is_object_hidden(obj_name):
                    continue
                tid = track_ids.get(obj_name)
                if tid is None:
                    continue
                obj_segs = by_obj.get(obj_name, [])
                if not obj_segs:
                    continue
                extra: dict = {}
                if not is_active:
                    extra = {"locked": True, "read_only": True, "dimmed": True}
                elif store and obj_name in store.locked_objects:
                    extra = {"locked": True}
                status = obj_classes.get(obj_name, "valid")
                if status != "valid":
                    pair = SHOT_PALETTE.get(status)
                    if pair is not None and pair[0]:
                        extra["status_color"] = pair[0]

                gap = store.detection_threshold if store else 10.0
                span_segs = sorted(
                    (sg for sg in obj_segs if not sg.get("is_stepped")),
                    key=lambda sg: sg["start"],
                )
                merged: list = []
                for seg in span_segs:
                    if merged and seg["start"] <= merged[-1]["end"] + gap:
                        merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
                        merged[-1]["segs"].append(seg)
                    else:
                        merged.append(
                            {"start": seg["start"], "end": seg["end"], "segs": [seg]}
                        )

                for m in merged:
                    s, e = m["start"], m["end"]
                    attrs = SegmentCollector.extract_attributes(m["segs"])
                    clip_extra = dict(extra)
                    if is_active and attrs:
                        clip_extra["label_center"] = SegmentCollector.abbreviate_attrs(
                            attrs
                        )
                    clip_extra.update(
                        {
                            "obj": obj_name,
                            "shot_id": vs.shot_id,
                            "orig_start": s,
                            "orig_end": e,
                            "attributes": attrs,
                        }
                    )
                    try:
                        widget.add_clip(
                            track_id=tid,
                            start=s,
                            duration=max(e - s, 0.0),
                            label="",
                            **clip_extra,
                        )
                    except Exception:
                        self.logger.debug(
                            "add_clip failed for %s", obj_name, exc_info=True
                        )

    def _build_audio_tracks(self, widget, shot, visible_shots) -> None:
        """Add one track per VSE sound strip overlapping the visible shots.

        Segments come from :class:`~blendertk.audio_utils.segments.AudioSegment`
        (strip name = ``track_id``); they only change on audio edits, not on
        shot switches, so they're cached by visible range like the Maya original.
        """
        scene_start = min(vs.start for vs in visible_shots)
        scene_end = max(vs.end for vs in visible_shots)
        cache_key = (scene_start, scene_end)
        cached = self._audio_segments_cache
        if cached is not None and cached[0] == cache_key:
            segs = cached[1]
        else:
            try:
                from blendertk.audio_utils.segments import AudioSegment

                segs = AudioSegment.collect_all_segments(
                    scene_start=scene_start, scene_end=scene_end, include_waveform=True
                )
            except Exception:
                self.logger.debug("audio segment collection failed", exc_info=True)
                segs = []
            self._audio_segments_cache = (cache_key, segs)

        by_track: dict = defaultdict(list)
        for seg in segs:
            by_track[seg.track_id].append(seg)
        if not by_track:
            return

        from uitk.managers.icon_manager import IconManager

        for track_id, track_segs in by_track.items():
            if self.sequencer.is_object_hidden(track_id):
                continue
            clip_descs: list = []
            for seg in track_segs:
                for vs in visible_shots:
                    vis_start = max(seg.start, vs.start)
                    vis_end = min(seg.end, vs.end)
                    if vis_end <= vis_start:
                        continue
                    clip_descs.append((seg, vs, vis_start, vis_end))
            if not clip_descs:
                continue

            icon = IconManager.get("activity", size=(16, 16), color="#888888")
            widget_track_id = widget.add_track(track_id, icon=icon)

            for seg, vs, vis_start, vis_end in clip_descs:
                is_active = vs.shot_id == shot.shot_id
                full_waveform = seg.waveform or []
                full_dur = seg.end - seg.start
                if full_waveform and full_dur > 0:
                    n = len(full_waveform)
                    i_lo = int((vis_start - seg.start) / full_dur * n)
                    i_hi = max(i_lo + 1, int((vis_end - seg.start) / full_dur * n))
                    vis_waveform = full_waveform[i_lo:i_hi]
                else:
                    vis_waveform = full_waveform
                extra: dict = {}
                if not is_active:
                    extra = {"locked": True, "read_only": True, "dimmed": True}
                widget.add_clip(
                    track_id=widget_track_id,
                    start=vis_start,
                    duration=vis_end - vis_start,
                    label=seg.label or track_id,
                    color="#3A7D44",
                    is_audio=True,
                    audio_track_id=seg.track_id,
                    file_path=seg.file_path,
                    waveform=vis_waveform,
                    orig_start=seg.start,
                    orig_end=seg.end,
                    shot_id=vs.shot_id,
                    **extra,
                )

    def _provide_sub_rows(self, track_id, track_name):
        """Per-attribute sub-rows: ``[(attr, [(start, dur, label, color, extra)...])...]``.

        Same ``SegmentKeys.collect_segments`` pipeline as the object row (hold
        absorption, hold-only synthesis and motion detection agree between the
        two views); with *Show Internal Holds* on, pure-hold spans are emitted
        with ``is_hold`` so the widget can style them.  Each sub-row also gets a
        full-range background curve preview.
        """
        if self.sequencer is None:
            return []
        shot_id = self.active_shot_id
        shot = self.sequencer.shot_by_id(shot_id) if shot_id is not None else None
        if shot is None:
            return []
        obj_name = self._resolve_full_name(track_name)
        cache_key = (shot_id, track_name)
        cached = self._sub_row_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            import bpy
        except ImportError:
            return []
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return []
        from blendertk.anim_utils.segment_keys import SegmentKeys

        all_curves = ShotSequencer._transform_fcurves(obj)
        if not all_curves:
            return []

        widget = self._get_sequencer_widget()
        color_map = widget.attribute_colors if widget else {}
        show_holds = self._show_internal_holds

        # Channel label → first fcurve (the full-range background preview source).
        attr_to_curve: dict = {}
        for fc in all_curves:
            attr_to_curve.setdefault(SegmentCollector.attr_label(fc), fc)

        store = self.sequencer.store if self.sequencer else None
        is_obj_locked = bool(store and obj_name in store.locked_objects)

        visible = self._visible_shots(shot)
        curve_range_start = min(sh.start for sh in visible)
        curve_range_end = max(sh.end for sh in visible)

        result = []
        for attr_name in sorted(attr_to_curve):
            segs = SegmentKeys.collect_segments(
                [obj_name],
                split_static=True,
                channel_box_attrs=[attr_name],
                ignore_holds=not show_holds,
                ignore_visibility_holds=True,
                motion_only=True,
                motion_rate=1e-3,
                time_range=(shot.start, shot.end),
            )
            if not segs:
                continue

            hold_ranges: set = set()
            if show_holds:
                active_segs = SegmentKeys.collect_segments(
                    [obj_name],
                    split_static=True,
                    channel_box_attrs=[attr_name],
                    ignore_holds=True,
                    ignore_visibility_holds=True,
                    motion_only=True,
                    motion_rate=1e-3,
                    time_range=(shot.start, shot.end),
                )
                active_spans = [(a["start"], a["end"]) for a in active_segs]
                for seg in segs:
                    ss, se = seg["start"], seg["end"]
                    if not any(a_s < se and a_e > ss for a_s, a_e in active_spans):
                        hold_ranges.add((ss, se))

            color = color_map.get(attr_name)
            segments = []
            for seg in segs:
                st, en = seg["start"], seg["end"]
                preview = None
                for crv in seg.get("curves", []):
                    preview = SegmentCollector.build_curve_preview(crv, st, en)
                    if preview:
                        break
                extra = {
                    "obj": obj_name,
                    "attr_name": attr_name,
                    "shot_id": shot_id,
                    "orig_start": st,
                    "orig_end": en,
                    "is_stepped": abs(en - st) < 1e-6,
                    "attributes": [attr_name],
                }
                if preview:
                    extra["curve_preview"] = preview
                if (st, en) in hold_ranges:
                    extra["is_hold"] = True
                if is_obj_locked:
                    extra["locked"] = True
                segments.append((st, max(en - st, 0.0), attr_name, color, extra))
            result.append((attr_name, segments))

        if widget is not None:
            for attr_name, _ in result:
                crv = attr_to_curve.get(attr_name)
                if crv is None:
                    continue
                bg_preview = SegmentCollector.build_curve_preview(
                    crv, curve_range_start, curve_range_end
                )
                hex_color = color_map.get(attr_name, "#CCCCCC")
                widget.set_bg_curve_preview(
                    track_id, attr_name, bg_preview, color=hex_color or "#CCCCCC"
                )

        self._sub_row_cache[cache_key] = result
        return result

    # ---- widget signal handlers (non-mixin) ------------------------------

    def hide_track(self, track_names) -> None:
        if self.sequencer is None:
            return
        if isinstance(track_names, str):
            track_names = [track_names]
        for name in track_names:
            self.sequencer.set_object_hidden(self._resolve_full_name(name), True)
        self._sync_to_widget()

    def show_track(self, track_name: str) -> None:
        if self.sequencer is None:
            return
        self.sequencer.set_object_hidden(track_name, False)
        self._sync_to_widget()

    def delete_track(self, track_names) -> None:
        if self.sequencer is None:
            return
        if isinstance(track_names, str):
            track_names = [track_names]
        for name in track_names:
            self.sequencer.store.remove_object_from_shots(self._resolve_full_name(name))
        self._sync_to_widget()

    def on_selection_changed(self, clip_ids: list) -> None:
        if not clip_ids or self._syncing:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        resolved, labels = [], []
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None:
                continue
            obj = clip.data.get("obj")
            if not obj:
                continue
            resolved.append(self._resolve_full_name(obj))
            attrs = clip.data.get("attributes") or (
                [clip.data.get("attr_name")] if clip.data.get("attr_name") else []
            )
            start, end = clip.data.get("orig_start"), clip.data.get("orig_end")
            parts = [obj]
            if attrs:
                parts.append(", ".join(a for a in attrs[:3] if a))
            if start is not None and end is not None:
                parts.append(f"{start:.0f}–{end:.0f} ({int(end - start)}f)")
            labels.append(" · ".join(parts))
        self._select_and_show(resolved)
        if labels:
            self._set_footer(
                "  |  ".join(labels[:3])
                + (f"  (+{len(labels) - 3} more)" if len(labels) > 3 else "")
            )

    def on_track_selected(self, track_names: list) -> None:
        if not track_names:
            return
        self._select_and_show([self._resolve_full_name(n) for n in track_names])

    def on_clip_locked(self, clip_id: int, locked: bool) -> None:
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        clip = widget._clips.get(clip_id)
        if clip is None:
            return
        obj_name = clip.data.get("obj")
        if not obj_name:
            return
        store = self.sequencer.store
        if locked:
            store.locked_objects.add(obj_name)
        else:
            store.locked_objects.discard(obj_name)
        for cid, cd in widget._clips.items():
            if cd.data.get("obj") == obj_name:
                widget.set_clip_locked(cid, locked)
        self._sub_row_cache.clear()

    def on_track_menu(self, menu, track_names) -> None:
        if not track_names:
            return
        try:
            import bpy
        except ImportError:
            return
        menu.addSeparator()
        resolved = [
            full
            for full in (self._resolve_full_name(n) for n in track_names)
            if bpy.data.objects.get(full) is not None
        ]
        if resolved:
            menu.addAction(
                "Reveal in Outliner",
                lambda objs=list(resolved): self._reveal_in_outliner(objs),
            )
        # Offered for every track, resolved or not -- pasting the name of an
        # object the scene no longer holds is exactly how it gets found again.
        shorts = [self._resolve_full_name(n) for n in track_names]
        copy_label = (
            f"Copy '{shorts[0]}' to Clipboard"
            if len(shorts) == 1
            else f"Copy {len(shorts)} Names to Clipboard"
        )
        menu.addAction(copy_label, lambda n=list(shorts): self._copy_names(n))

    @staticmethod
    def _copy_names(names) -> None:
        """Put the given object names on the clipboard, one per line."""
        from qtpy import QtWidgets

        QtWidgets.QApplication.clipboard().setText("\n".join(names))

    def on_header_menu(self, menu) -> None:
        """Header background context menu — no domain actions this phase."""

    def on_clip_renamed(self, clip_id: int, new_label: str) -> None:
        """Renaming a clip is display-only in Blender (object names own identity)."""

    def on_playhead_moved(self, frame: float) -> None:
        """Widget playhead drag → set the scene frame (scrub audio via Blender)."""
        scene = _ShotSequencerControllerInternal._scene()
        if scene is None:
            return
        self._syncing_playhead = True
        try:
            self._ensure_sound_on_timeline()
            scene.frame_set(int(round(frame)))
        finally:
            self._syncing_playhead = False

    def _ensure_sound_on_timeline(self) -> None:
        """Make scrubbing audible — Blender's own ``use_audio_scrub`` on the scene.

        Mirror of mayatk's "bind the composite to the Time Slider": Blender's
        sequencer already plays every strip, and ``scene.frame_set`` seeks the
        audio, so scrub grains only need the scene flag.  The widget's own
        ScrubPlayer is deliberately NOT bound (two players on one scene would
        double every grain).  Only flips the flag while sound strips exist.  The
        flag is read off the scene each time rather than cached: an undo swaps
        ``bpy.data`` and can revert it behind a cached "armed" marker.
        """
        scene = _ShotSequencerControllerInternal._scene()
        if scene is None or scene.use_audio_scrub:
            return
        try:
            from blendertk.audio_utils._audio_utils import AudioUtils

            if AudioUtils.list_clips(scene):
                scene.use_audio_scrub = True
        except Exception:
            self.logger.debug("audio scrub arming failed", exc_info=True)

    def on_clip_menu(self, menu, clip_id: int) -> None:
        """Add Delete-key + lock actions to a clip's context menu."""
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        clip = widget.get_clip(clip_id)
        if clip is None:
            return
        obj_name = clip.data.get("obj")
        selected_ids = widget.selected_clips() or [clip_id]
        if clip_id not in selected_ids:
            selected_ids = [clip_id]
        multi = len(selected_ids) > 1
        menu.addSeparator()
        act_delete = menu.addAction(
            f"Delete Keys ({len(selected_ids)})" if multi else "Delete Key"
        )
        act_delete.triggered.connect(lambda: self._delete_clip_keys(selected_ids))

        # Key stash: park the clips' keys out of the working animation (inert,
        # never exported, retrievable across sessions) — the non-destructive
        # sibling of Delete Keys — and bring stored clips back onto this lane.
        act_store = menu.addAction(
            f"Store Keys ({len(selected_ids)})" if multi else "Store Keys"
        )
        act_store.triggered.connect(lambda: self._stash_clip_keys(selected_ids))
        if obj_name:
            self._add_retrieve_menu(menu, obj_name)
        if obj_name and self.sequencer:
            menu.addSeparator()
            menu.addAction("Lock Others", lambda: self._lock_others(widget, obj_name))
            menu.addAction("Unlock All", lambda: self._unlock_all(widget))

        # "Move to Shot" submenu — anim/audio clips moved as sequences.
        if self.sequencer:
            seqs = self._clips_to_sequences(widget, selected_ids)
            shots = self.sequencer.sorted_shots()
            if seqs and len(shots) > 1:
                menu.addSeparator()
                move_label = f"Move to Shot ({len(seqs)})" if multi else "Move to Shot"
                # Parent the submenu explicitly: PySide 6.11's ``addMenu(str)`` hands
                # back a wrapper that goes stale once this frame drops it (the C++
                # menu survives, but a later ``action.menu()`` raises).
                from qtpy import QtWidgets

                move_menu = QtWidgets.QMenu(move_label, menu)
                menu.addMenu(move_menu)
                source_ids = {self.sequencer._source_shot_id_for(sq) for sq in seqs}
                for sh in shots:
                    if len(source_ids) == 1 and sh.shot_id in source_ids:
                        continue  # all sequences already live here
                    act = move_menu.addAction(
                        f"{sh.name}  [{sh.start:.0f}–{sh.end:.0f}]"
                    )
                    act.triggered.connect(
                        lambda _checked=False, sid=sh.shot_id: self._move_clips_to_shot(
                            seqs, sid
                        )
                    )

    def _clips_to_sequences(self, widget, clip_ids):
        """Convert widget clip ids to unified sequence dicts.

        Stepped (zero-duration) clips, read-only clips (non-active visible
        shots) and single-attribute sub-row clips are skipped — a whole-object
        sequence move would relocate EVERY attribute's keys in the span, not
        just the one the user grabbed.  Duplicates (one segment spanning several
        visible shots) are collapsed.
        """
        seqs = []
        seen: set = set()
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None or clip.data.get("read_only"):
                continue
            if clip.data.get("is_stepped") or clip.data.get("attr_name"):
                continue
            start = clip.data.get("orig_start")
            end = clip.data.get("orig_end")
            if start is None or end is None or end <= start:
                continue
            if clip.data.get("is_audio"):
                obj, kind = clip.data.get("audio_track_id"), "audio"
            else:
                obj, kind = clip.data.get("obj"), "anim"
            if not obj:
                continue
            key = (kind, obj, round(start, 6), round(end, 6))
            if key in seen:
                continue
            seen.add(key)
            seqs.append({"kind": kind, "obj": obj, "start": start, "end": end})
        return seqs

    def _move_clips_to_shot(self, sequences, dest_shot_id):
        """Run ``move_sequences_to_shot``, undoable, then refresh.

        Reports the outcome in the footer (mirror of mayatk): the move is a
        no-op whenever every selected sequence already lives in the
        destination — which used to look like the command silently failing.
        """
        if self.sequencer is None or not sequences:
            self._set_footer(
                "Move to Shot: nothing movable in the selection.", color="#E0A0A0"
            )
            return
        dest = self.sequencer.shot_by_id(dest_shot_id)
        movable = [
            sq
            for sq in sequences
            if self.sequencer._source_shot_id_for(sq) != dest_shot_id
        ]
        if not movable:
            self._set_footer(
                "Move to Shot: selection is already in "
                f"{dest.name if dest else 'that shot'}.",
                color="#E0A0A0",
            )
            return
        self._save_shot_state()
        with CoreUtils.undo_chunk("Move to Shot"):
            self.sequencer.move_sequences_to_shot(movable, dest_shot_id)
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._sync_to_widget()
        self._sync_combobox()
        self._apply_view_playback_range()
        n = len(movable)
        self._set_footer(
            f"Moved {n} clip{'s' if n != 1 else ''} to "
            f"{dest.name if dest else dest_shot_id}"
        )

    def on_gap_menu(self, menu, gap_start: float, gap_end: float) -> None:
        """Add domain-specific actions to a gap overlay's context menu (none by default)."""

    def on_key_selection_changed(self, key_groups: list) -> None:
        """Sync the Graph Editor's key selection to match the sequencer.

        *key_groups* is ``[{clip_id, times}, ...]`` — one entry per clip with
        selected keyframe items.  Every keyframe point on the object's
        transform fcurves is deselected first, then the named times are
        selected on the clip's attribute curves (mirror of ``cmds.selectKey``).
        """
        if self._syncing:
            # During a rebuild the scene selection empties as items are torn
            # down.  Mirroring that would clear the user's Graph Editor key
            # selection on every refresh.
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        try:
            import bpy  # noqa: F401
        except ImportError:
            return
        touched: set = set()
        for group in key_groups:
            clip = widget.get_clip(group["clip_id"])
            if clip is None:
                continue
            obj_name = clip.data.get("obj")
            if obj_name:
                touched.add(obj_name)
        for obj_name in touched:
            obj = bpy.data.objects.get(obj_name)
            if obj is None:
                continue
            for fc in BlenderShotStore.iter_action_fcurves(obj):
                n = len(fc.keyframe_points)
                if n:
                    off = [False] * n
                    for prop in (
                        "select_control_point",
                        "select_left_handle",
                        "select_right_handle",
                    ):
                        fc.keyframe_points.foreach_set(prop, off)
        n = 0
        for group in key_groups:
            clip = widget.get_clip(group["clip_id"])
            if clip is None:
                continue
            obj_name = clip.data.get("obj")
            attr_name = clip.data.get("attr_name")
            if not obj_name or not attr_name:
                continue
            for fc in ClipMotionMixin.curves_for_attr(obj_name, attr_name):
                kt = AnimUtils.key_times(fc)
                for t in group["times"]:
                    i0, i1 = AnimUtils.window_indices(kt, t - 1e-3, t + 1e-3)
                    for i in range(i0, i1):
                        fc.keyframe_points[i].select_control_point = True
                        n += 1
        if n:
            self._set_footer(f"{n} key{'s' if n != 1 else ''} selected")

    def _lock_others(self, widget, keep_obj: str) -> None:
        store = self.sequencer.store if self.sequencer else None
        if store is None:
            return
        obj_names = {
            cd.data.get("obj")
            for cd in widget._clips.values()
            if cd.data.get("obj")
            and not getattr(cd, "sub_row", False)
            and not cd.data.get("read_only")
        }
        for o in obj_names:
            if o == keep_obj:
                store.locked_objects.discard(o)
            else:
                store.locked_objects.add(o)
        for cid, cd in list(widget._clips.items()):
            o = cd.data.get("obj")
            if o and not cd.data.get("read_only"):
                widget.set_clip_locked(cid, o != keep_obj)
        self._sub_row_cache.clear()

    def _unlock_all(self, widget) -> None:
        store = self.sequencer.store if self.sequencer else None
        if store is not None:
            store.locked_objects.clear()
        for cid, cd in list(widget._clips.items()):
            if cd.locked and not cd.data.get("read_only"):
                widget.set_clip_locked(cid, False)
        self._sub_row_cache.clear()

    def _delete_clip_keys(self, clip_ids: list) -> None:
        """Delete the given clips' keys within their span.

        A whole-object clip is scoped to the object's TRANSFORM fcurves — the
        same ``_is_transform_path`` filter its span was collected from
        (``_span_segments``) — never every fcurve on the action: custom-property,
        constraint-influence, and modifier curves aren't part of the clip and
        must survive a "Delete Key".
        """
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        try:
            import bpy
        except ImportError:
            return

        self._save_shot_state()
        deleted = 0
        with CoreUtils.undo_chunk():
            for cid in clip_ids:
                clip = widget.get_clip(cid)
                if clip is None or clip.data.get("read_only"):
                    continue
                obj = bpy.data.objects.get(clip.data.get("obj", ""))
                if obj is None:
                    continue
                s, e = clip.data.get("orig_start"), clip.data.get("orig_end")
                if s is None or e is None:
                    continue
                attr = clip.data.get("attr_name")
                fcurves = (
                    ClipMotionMixin.curves_for_attr(obj.name, attr)
                    if attr
                    else ShotSequencer._transform_fcurves(obj)
                )
                for fc in fcurves:
                    i0, i1 = AnimUtils.window_indices(
                        AnimUtils.key_times(fc), s - 1e-3, e + 1e-3
                    )
                    for i in reversed(range(i0, i1)):
                        fc.keyframe_points.remove(fc.keyframe_points[i])
                        deleted += 1
                    if i1 > i0:
                        fc.update()
        if deleted:
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            self._sync_to_widget()
            self._set_footer(f"Deleted {deleted} key{'s' if deleted != 1 else ''}")

    def _stash_clip_keys(self, clip_ids: list) -> None:
        """Move the given clips' keys into the key stash (``KeyStash.stash``).

        Same scoping as :meth:`_delete_clip_keys` — a whole-object clip is the
        object's TRANSFORM fcurves, a sub-row clip its one attribute, over the
        clip's original span — but the keys are parked, not destroyed: the shot
        block stays as it is and the clip records the shot it came from.
        """
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        try:
            import bpy
        except ImportError:
            return
        from blendertk.anim_utils.key_stash._key_stash import KeyStash

        store = KeyStash.active()
        stored = 0
        self._save_shot_state()
        with CoreUtils.undo_chunk("Store Keys"):
            for cid in clip_ids:
                clip = widget.get_clip(cid)
                if clip is None or clip.data.get("read_only"):
                    continue
                obj = bpy.data.objects.get(clip.data.get("obj", ""))
                if obj is None:
                    continue
                s, e = clip.data.get("orig_start"), clip.data.get("orig_end")
                if s is None or e is None:
                    continue
                attr = clip.data.get("attr_name")
                fcurves = (
                    ClipMotionMixin.curves_for_attr(obj.name, attr)
                    if attr
                    else ShotSequencer._transform_fcurves(obj)
                )
                if not fcurves:
                    continue
                shot_id = clip.data.get("shot_id")
                clip_rec = store.stash(
                    objects=[obj],
                    time_range=(s, e),
                    fcurves=fcurves,
                    source_shot_id=None if shot_id == -1 else shot_id,
                )
                if clip_rec is not None:
                    stored += clip_rec.key_count
        if not stored:
            self._set_footer("No keys to store")
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._set_footer(
            f"Stored {stored} key{'s' if stored != 1 else ''} in the key stash"
        )

    def _add_retrieve_menu(self, menu, obj_name: str) -> None:
        """Append a "Retrieve Stored Keys" submenu listing *obj_name*'s clips."""
        from blendertk.anim_utils.key_stash._key_stash import KeyStash

        clips = KeyStash.active().clips_for_object(obj_name)
        if not clips:
            return
        # Parent the submenu explicitly (see the Move-to-Shot note above).
        from qtpy import QtWidgets

        sub = QtWidgets.QMenu("Retrieve Stored Keys", menu)
        menu.addMenu(sub)
        for clip in clips:
            act = sub.addAction(clip.label)
            act.triggered.connect(
                lambda _checked=False, cid=clip.clip_id: self._retrieve_stashed_clip(
                    cid
                )
            )

    def _retrieve_stashed_clip(self, clip_id: int) -> None:
        """Put a stored clip back on its original frames (``KeyStash.retrieve``)."""
        if self.sequencer is None:
            return
        from blendertk.anim_utils.key_stash._key_stash import KeyStash

        self._save_shot_state()
        with CoreUtils.undo_chunk("Retrieve Stored Keys"):
            restored = KeyStash.active().retrieve(clip_id)
        if not restored:
            self._set_footer("Nothing retrieved — see the console")
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._set_footer(f"Retrieved {restored} key{'s' if restored != 1 else ''}")

    def _delete_selected_clip_keys(self) -> None:
        """Delete selected keyframes or, if none, all keys on selected clips.

        Individual keyframe items are batched into a single undo step so Ctrl+Z
        restores every deleted key at once.
        """
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        from uitk.widgets.sequencer._keyframe import KeyframeItem

        try:
            items = widget._timeline._scene.selectedItems()
        except RuntimeError:
            items = []
        by_clip: dict = {}
        for item in items:
            if isinstance(item, KeyframeItem):
                cid = item._parent_clip._data.clip_id
                by_clip.setdefault(cid, []).append(item._time)

        if by_clip:
            deleted = 0
            with CoreUtils.undo_chunk("Delete Keys"):
                for clip_id, times in by_clip.items():
                    clip = widget.get_clip(clip_id)
                    if clip is None:
                        continue
                    obj_name = clip.data.get("obj")
                    attr_name = clip.data.get("attr_name")
                    if not obj_name or not attr_name:
                        continue
                    curves = ClipMotionMixin.curves_for_attr(obj_name, attr_name)
                    for t in times:
                        cut_ok = False
                        for fc in curves:
                            i0, i1 = AnimUtils.window_indices(
                                AnimUtils.key_times(fc), t - 1e-3, t + 1e-3
                            )
                            for i in reversed(range(i0, i1)):
                                fc.keyframe_points.remove(fc.keyframe_points[i])
                                cut_ok = True
                            if i1 > i0:
                                fc.update()
                        if cut_ok:
                            deleted += 1
            if deleted:
                self._save_shot_state()
                shot_id = self.active_shot_id
                self._segment_cache.clear()
                self._sub_row_cache.clear()
                self._sync_to_widget(shot_id=shot_id)
                self._set_footer(f"Deleted {deleted} key{'s' if deleted != 1 else ''}")
            return

        selected = widget.selected_clips() or []
        if selected:
            self._delete_clip_keys(selected)
            return

        # Nothing at all is selected inside the tracks, so Delete is about the
        # SHOT -- the only other thing the panel has selected.  It confirms
        # first, so the key cannot quietly take a shot and its animation.
        block = widget.selected_shot()
        if block is not None and block.get("id") is not None:
            self.delete_shot(block["id"])

    def _set_show_internal_holds(self, enabled: bool) -> None:
        """Toggle flat-key span visibility in attribute sub-rows."""
        self._show_internal_holds = enabled
        self._sub_row_cache.clear()
        self._sync_to_widget()

    # ---- header / transport / toggles ------------------------------------

    def _on_frame_on_shot_change_toggled(self, checked: bool) -> None:
        if self.sequencer is None:
            return
        self.sequencer.store.frame_on_shot_change = checked
        self.sequencer.store.mark_dirty()

    def _on_select_on_load_toggled(self, checked: bool) -> None:
        if self.sequencer is None:
            return
        self.sequencer.store.select_on_load = checked
        self.sequencer.store.mark_dirty()

    # ---- Transport controls (footer) -------------------------------------

    #: Button edge of the footer transport, in pixels (mirrors mayatk): sized
    #: so the glyphs land on uitk's 16px icon grid (icons are 0.7 of the
    #: button) instead of the 14px the old 20px height produced.
    TRANSPORT_BUTTON_HEIGHT = 23

    def _setup_transport_controls(self) -> None:
        """Install the reusable ``TransportControls`` row on the footer's RIGHT.

        Wired to :class:`_BlenderPlayController`; keyed off the persistent
        footer (not this controller) so a slots re-init adopts the existing row
        instead of stacking a duplicate.
        """
        footer = getattr(self.ui, "footer", None)
        if footer is None:
            return
        existing = getattr(footer, "_shot_transport_controls", None)
        if existing is not None:
            existing.set_play_controller(_BlenderPlayController(self))
            # range_fn is an instance method — the constructor binding would
            # otherwise keep reading the retired controller's stale state.
            existing.set_range_fn(self._playback_range)
            self._transport_controls = existing
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        from uitk.widgets.sequencer import TransportControls

        transport = TransportControls(
            sequencer=widget,
            play_controller=_BlenderPlayController(self),
            parent=footer,
            # The footer grows to fit a taller child, so this is a floor.
            button_height=max(footer.height(), self.TRANSPORT_BUTTON_HEIGHT),
            interrupt_mode=TransportControls.INTERRUPT_STOP,
            range_fn=self._playback_range,
            button_names=(
                "go_to_start",
                "prev_key",
                "play_back",
                "play_forward",
                "next_key",
                "go_to_end",
            ),
        )
        transport.attach_to_footer(footer, side="right")
        self._transport_controls = transport
        footer._shot_transport_controls = transport
        try:
            self._ensure_sound_on_timeline()
        except Exception:
            pass

    def _playback_range(self) -> tuple:
        """Range the transport's go-to-start / go-to-end buttons target.

        The ACTIVE SHOT wins over the scene range.  Reading the scene range
        made the two buttons skip the current shot's own boundaries whenever
        the range covered more than that shot — which it does in the
        "adjacent" and "all" view modes, and whenever the playback-range mode
        is "off".  An empty shot has no clips to fall back on, so there the
        skip was total.
        """
        if self.sequencer is not None:
            sid = self.active_shot_id
            shot = self.sequencer.shot_by_id(sid) if sid is not None else None
            if shot is not None and shot.end > shot.start:
                return float(shot.start), float(shot.end)
        scene = _ShotSequencerControllerInternal._scene()
        if scene is None:
            return 1.0, 120.0
        return float(scene.frame_start), float(scene.frame_end)


# ---------------------------------------------------------------------------
# Play controller (Blender)
# ---------------------------------------------------------------------------


class _BlenderPlayController:
    """``PlayController`` adapter driving Blender's timeline via ``screen.animation_play``.

    Tracks direction so ``TransportControls`` can resume the right way; every
    operator call runs under a window context so it works from the Qt pump.
    """

    def __init__(self, controller: "ShotSequencerController"):
        self._ctl = controller
        self._forward = True

    @staticmethod
    def _screen():
        try:
            import bpy
        except ImportError:
            return None
        screen = getattr(bpy.context, "screen", None)
        if screen is None:
            win = getattr(bpy.context, "window_manager", None)
            wins = getattr(win, "windows", None) or []
            screen = wins[0].screen if wins else None
        return screen

    def is_playing(self) -> bool:
        screen = self._screen()
        return bool(screen is not None and screen.is_animation_playing)

    def play(self, forward: bool) -> None:
        self._forward = bool(forward)
        try:
            import bpy
        except ImportError:
            return
        try:
            self._ctl._ensure_sound_on_timeline()
        except Exception:
            pass
        try:
            with CoreUtils.window_context_override():
                if self.is_playing():
                    bpy.ops.screen.animation_cancel(restore_frame=False)
                bpy.ops.screen.animation_play(reverse=not self._forward)
        except Exception:
            self._ctl.logger.debug("animation_play failed", exc_info=True)

    def stop(self) -> None:
        try:
            import bpy
        except ImportError:
            return
        try:
            if self.is_playing():
                with CoreUtils.window_context_override():
                    bpy.ops.screen.animation_cancel(restore_frame=False)
        except Exception:
            self._ctl.logger.debug("animation_cancel failed", exc_info=True)


# ---------------------------------------------------------------------------
# Shot Edit Dialog
# ---------------------------------------------------------------------------


class ShotEditDialog:
    """Lightweight dialog for creating or editing a shot (plain Qt widgets).

    Returns ``(name, start, end, description)`` on accept, ``None`` on cancel.
    """

    @staticmethod
    def show(
        parent=None,
        name: str = "",
        start: float = 1.0,
        end: float = 100.0,
        description: str = "",
        title: str = "Shot",
    ):
        """Show a modal dialog and return the result tuple or ``None``."""
        from qtpy import QtWidgets

        dlg = QtWidgets.QDialog(parent)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(280)
        layout = QtWidgets.QFormLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)

        name_edit = QtWidgets.QLineEdit(name)
        name_edit.setPlaceholderText("Shot name")
        layout.addRow("Name:", name_edit)
        start_spin = QtWidgets.QDoubleSpinBox()
        start_spin.setDecimals(1)
        start_spin.setRange(-1e6, 1e6)
        start_spin.setValue(start)
        layout.addRow("Start:", start_spin)
        end_spin = QtWidgets.QDoubleSpinBox()
        end_spin.setDecimals(1)
        end_spin.setRange(-1e6, 1e6)
        end_spin.setValue(end)
        layout.addRow("End:", end_spin)
        desc_edit = QtWidgets.QLineEdit(description)
        desc_edit.setPlaceholderText("Optional description")
        layout.addRow("Description:", desc_edit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return None
        return (
            name_edit.text().strip() or "Shot",
            start_spin.value(),
            end_spin.value(),
            desc_edit.text().strip(),
        )


class ShotSequencerSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    # (widget signal, controller slot) wiring table — mirror of mayatk's.
    _WIRING = [
        ("clip_resized", "on_clip_resized"),
        ("clip_moved", "on_clip_moved"),
        ("clips_batch_moved", "on_clips_batch_moved"),
        ("clip_renamed", "on_clip_renamed"),
        ("playhead_moved", "on_playhead_moved"),
        ("track_hidden", "hide_track"),
        ("track_shown", "show_track"),
        ("track_deleted", "delete_track"),
        ("selection_changed", "on_selection_changed"),
        ("track_selected", "on_track_selected"),
        ("track_menu_requested", "on_track_menu"),
        ("clip_locked", "on_clip_locked"),
        ("undo_requested", "on_undo"),
        ("redo_requested", "on_redo"),
        ("marker_added", "on_marker_added"),
        ("marker_moved", "on_marker_moved"),
        ("marker_changed", "on_marker_changed"),
        ("marker_removed", "on_marker_removed"),
        ("gap_resized", "on_gap_resized"),
        ("gap_left_resized", "on_gap_left_resized"),
        ("gap_moved", "on_gap_moved"),
        ("gap_lock_changed", "on_gap_lock_changed"),
        ("gap_lock_all_requested", "on_gap_lock_all"),
        ("gap_unlock_all_requested", "on_gap_unlock_all"),
        ("clip_menu_requested", "on_clip_menu"),
        ("gap_menu_requested", "on_gap_menu"),
        ("range_highlight_changed", "on_range_highlight_changed"),
        ("zone_context_menu_requested", "on_zone_context_menu"),
        ("shot_switch_requested", "_on_shot_switch_requested"),
        ("header_menu_requested", "on_header_menu"),
        ("keys_moved", "on_keys_moved"),
        ("keys_batch_moved", "on_keys_batch_moved"),
        ("keys_deleted", "on_keys_deleted"),
        ("key_selection_changed", "on_key_selection_changed"),
    ]

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shot_sequencer

        cmb_shot = getattr(self.ui, "cmb_shot", None)
        if cmb_shot is not None:
            cmb_shot.restore_state = False

        # Re-init safety: the widget-signal table below de-dupes itself, but a
        # prior controller's bpy.app handlers + store/invalidation listeners
        # only die on ui.destroyed — tear them down NOW or every scene edit
        # would fan out to two controllers (double rebuilds).
        prior = getattr(self.ui, "_sequencer_controller", None)
        if prior is not None:
            try:
                prior.remove_callbacks()
            except Exception:
                self.logger.debug("prior controller teardown failed", exc_info=True)

        self.controller = ShotSequencerController(self)
        self.ui._sequencer_controller = self.controller

        sequencer = self.controller._get_sequencer_widget()
        if sequencer is not None and hasattr(sequencer, "clip_resized"):
            sequencer.window_shortcuts = True
            # Disconnect any prior controller's connections (re-init safety).
            for sig_name, slot in getattr(sequencer, "_slots_connections", []):
                try:
                    getattr(sequencer, sig_name).disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            connections = []
            for sig_name, slot_name in self._WIRING:
                slot = getattr(self.controller, slot_name, None)
                sig = getattr(sequencer, sig_name, None)
                if slot is None or sig is None:
                    # Degrade the one connection, never silently — a missing
                    # signal means a uitk/blendertk version mismatch.
                    self.logger.warning(
                        "sequencer wiring skipped: %s -> %s (signal or slot "
                        "missing - uitk version mismatch?)",
                        sig_name,
                        slot_name,
                    )
                    continue
                sig.connect(slot)
                connections.append((sig_name, slot))
            sequencer._slots_connections = connections

            # Delete-key shortcut for selected clips.
            try:
                from qtpy import QtCore as _QtCore, QtGui as _QtGui

                _del_key = _QtGui.QKeySequence("Delete").toString()
                mgr = getattr(sequencer, "_shortcut_mgr", None)
                _ctx = _QtCore.Qt.WindowShortcut
                if mgr is not None:
                    if _del_key in mgr.shortcuts:
                        entry = mgr.shortcuts[_del_key]
                        entry["action"] = self.controller._delete_selected_clip_keys
                        if entry["shortcut"] is not None:
                            entry["shortcut"].setContext(_ctx)
                            entry["shortcut"].activated.disconnect()
                            entry["shortcut"].activated.connect(
                                self.controller._delete_selected_clip_keys
                            )
                    else:
                        mgr.add_shortcut(
                            "Delete",
                            self.controller._delete_selected_clip_keys,
                            "Delete keys for selected clips",
                            _ctx,
                        )
            except Exception:
                self.logger.debug("Delete shortcut wiring failed", exc_info=True)

        self._setup_shot_nav()
        self.controller._setup_transport_controls()
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

    def _setup_shot_nav(self) -> None:
        """Prev/next/add/view-mode/holds/refresh option-box actions on cmb_shot.

        Every callback is late-bound through ``cmb._nav_controller`` /
        ``cmb._nav_slots`` so a slots re-init over the same loaded UI only
        repoints those attributes — the option-box actions and menu connects are
        created exactly once and never duplicated.
        """
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None or not hasattr(cmb, "option_box"):
            return
        cmb._nav_controller = self.controller
        cmb._nav_slots = self
        _VIEW_MODE_MAP = {0: "current", 1: "adjacent", 2: "all"}
        existing = getattr(cmb, "_shot_nav_options", None)
        if existing is not None:
            # Re-init: adopt the already-built options for this controller.
            ctl = self.controller
            ctl._prev_action = existing.get("prev")
            ctl._next_action = existing.get("next")
            ctl._view_mode_action = existing.get("view")
            view_opt = existing.get("view")
            if view_opt is not None:
                ctl._shot_display_mode = _VIEW_MODE_MAP.get(
                    view_opt.current_state, "current"
                )
            holds_opt = existing.get("holds")
            if holds_opt is not None:
                ctl._holds_action = holds_opt
                ctl._show_internal_holds = holds_opt.current_state == 1
            ctl._cmb_mode_widget = getattr(self.ui, "cmb_mode", None)
            return
        try:
            from uitk.widgets.optionBox.options.action import ActionOption

            prev_opt = ActionOption(
                wrapped_widget=cmb,
                callback=lambda: cmb._nav_controller._navigate_shot(-1),
                icon="chevron_left",
                tooltip="Previous Shot",
                order=0,
            )
            next_opt = ActionOption(
                wrapped_widget=cmb,
                callback=lambda: cmb._nav_controller._navigate_shot(1),
                icon="chevron_right",
                tooltip="Next Shot",
                order=1,
            )

            # "+" button — one-click shot creation (mirror of mayatk).
            add_opt = ActionOption(
                wrapped_widget=cmb,
                callback=lambda: cmb._nav_controller._create_shot_one_click(),
                icon="add",
                tooltip="New Shot",
                order=2,
            )

            # View mode cycle: Current → Adjacent → All (mirror of mayatk).
            _VIEW_STATES = [
                {
                    "icon": "target",
                    "tooltip": "View: Current Shot (click for adjacent)",
                    "callback": lambda: cmb._nav_controller._set_view_mode("adjacent"),
                },
                {
                    "icon": "columns",
                    "tooltip": "View: Adjacent Shots (click for all)",
                    "callback": lambda: cmb._nav_controller._set_view_mode("all"),
                },
                {
                    "icon": "grid",
                    "tooltip": "View: All Shots (click for current)",
                    "callback": lambda: cmb._nav_controller._set_view_mode("current"),
                },
            ]
            view_opt = ActionOption(wrapped_widget=cmb, states=_VIEW_STATES, order=4)

            # Refresh button — re-collect animation data and rebuild the widget.
            refresh_opt = ActionOption(
                wrapped_widget=cmb,
                callback=lambda: cmb._nav_controller.refresh(),
                icon="refresh",
                tooltip="Refresh Sequencer",
                order=6,
            )

            # Show Internal Holds toggle (two-state: off / on)
            _HOLD_STATES = [
                {
                    "icon": "eye_off",
                    "tooltip": "Show Internal Holds (off)\nClick to reveal flat-key spans in sub-rows",
                    "callback": lambda: cmb._nav_controller._set_show_internal_holds(
                        True
                    ),
                },
                {
                    "icon": "eye",
                    "tooltip": "Show Internal Holds (on)\nClick to hide flat-key spans in sub-rows",
                    "callback": lambda: cmb._nav_controller._set_show_internal_holds(
                        False
                    ),
                },
            ]
            holds_opt = ActionOption(
                wrapped_widget=cmb,
                states=_HOLD_STATES,
                order=5,
                settings_key="shot_sequencer_show_holds",
            )

            cmb.option_box.set_order(["action"])
            for opt in (prev_opt, next_opt, add_opt, view_opt, holds_opt, refresh_opt):
                cmb.option_box.add_option(opt)

            self.controller._prev_action = prev_opt
            self.controller._next_action = next_opt
            self.controller._view_mode_action = view_opt
            self.controller._holds_action = holds_opt
            # Sync controller state from persisted button states.
            self.controller._shot_display_mode = _VIEW_MODE_MAP.get(
                view_opt.current_state, "current"
            )
            self.controller._show_internal_holds = holds_opt.current_state == 1
            cmb._shot_nav_options = {
                "prev": prev_opt,
                "next": next_opt,
                "add": add_opt,
                "view": view_opt,
                "holds": holds_opt,
                "refresh": refresh_opt,
            }

            # Right-click context menu on the combobox (New / Generate / Edit / Delete).
            from qtpy import QtCore

            cmb.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            cmb.customContextMenuRequested.connect(
                lambda pos: cmb._nav_slots._cmb_context_menu(pos)
            )
        except Exception:
            self.logger.debug("shot nav option-box setup failed", exc_info=True)
        self.controller._cmb_mode_widget = getattr(self.ui, "cmb_mode", None)

    def _on_snap_to_keys_toggled(self, checked: bool) -> None:
        """Turn the opt-in pull onto existing key frames on or off.

        The alignment guides are unconditional -- this only decides whether
        the drag is also captured by the frames they mark.
        """
        widget = self.controller._get_sequencer_widget()
        if widget is not None:
            widget.snap_to_keys = bool(checked)

    def _on_playback_range_changed(self, index: int) -> None:
        """Handle playback-range combobox selection."""
        cmb_pb = getattr(self.ui, "cmb_playback_range", None)
        if cmb_pb is None:
            return
        mode = cmb_pb.itemData(index)
        if mode:
            self.controller._set_playback_range_mode(mode)

    def _on_cmb_mode_changed(self, index: int) -> None:
        """Handle the Shots/Markers mode selector combobox."""
        cmb_mode = getattr(self.ui, "cmb_mode", None)
        if cmb_mode is None:
            return
        mode = cmb_mode.itemData(index)
        if mode:
            self.controller._set_cmb_mode(mode)

    def _on_track_order_changed(self, index: int) -> None:
        """Handle track-order scope combobox selection."""
        cmb = getattr(self.ui, "cmb_track_order", None)
        if cmb is None:
            return
        scope = cmb.itemData(index)
        if scope and scope != self.controller._track_order_scope:
            self.controller._track_order_scope = scope
            self.controller._sync_to_widget()

    # ---- shot CRUD helpers -----------------------------------------------

    def _edit_shot_in_settings(self) -> None:
        """Open Shot Settings with the active shot pre-selected."""
        if self.controller.sequencer is not None:
            sid = self.controller.active_shot_id
            if sid is not None:
                self.controller.sequencer.store.set_active_shot(sid)
        self.sb.handlers.marking_menu.show("shots")

    def _delete_shot(self) -> None:
        """Delete the selected shot (combobox menu / nav bar).

        One implementation for every entry point: the controller owns the
        confirmation and the engine call, so the combobox menu, the shot-lane
        menu and the Delete key cannot drift into three different ideas of
        what deleting a shot does.
        """
        sid = self.controller.active_shot_id
        if self.controller.sequencer is None or sid is None:
            return
        self.controller.delete_shot(sid)

    def _merge_shot(self, direction: str) -> None:
        """Merge the selected shot with its previous / next neighbour."""
        sid = self.controller.active_shot_id
        if self.controller.sequencer is None or sid is None:
            return
        other = self.controller._neighbour_shots(sid)[f"merge_{direction}"]
        if other is None:
            self.controller._set_footer(f"No {direction} shot to merge with")
            return
        self.controller.merge_shot_with(sid, other.shot_id)

    def _split_shot_at_playhead(self) -> None:
        """Split the selected shot at the current frame."""
        import bpy

        sid = self.controller.active_shot_id
        if self.controller.sequencer is None or sid is None:
            return
        self.controller.split_shot_at(sid, float(bpy.context.scene.frame_current))

    def _detect_next_shot(self) -> None:
        """Generate a shot from the next unregistered animation cluster."""
        seq = self.controller.sequencer
        if seq is None:
            return
        store = seq.store
        cand = seq.detect_next_shot(
            gap_threshold=(store.detection_threshold if store else 5.0)
        )
        if cand is None:
            self.controller._set_footer("No additional animation clusters found.")
            return
        result = ShotEditDialog.show(
            parent=self.ui,
            name=cand["name"],
            start=cand["start"],
            end=cand["end"],
            title="Generated Shot",
        )
        if result is None:
            return
        name, s, e, desc = result
        if e <= s:
            return
        seq.define_shot(
            name=name, start=s, end=e, objects=cand["objects"], description=desc
        )
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

    def _cmb_context_menu(self, pos) -> None:
        """Right-click context menu on the shot combobox."""
        from qtpy import QtWidgets

        if self.controller._cmb_mode != "shots":
            return
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return
        menu = QtWidgets.QMenu(cmb)
        has_shot = self.controller.active_shot_id is not None
        sid = self.controller.active_shot_id

        # Editing the shot you just picked is what this menu is reached
        # for most often, so it leads; creation and the structural edits
        # follow.
        edit_action = menu.addAction("Edit Shot…", self._edit_shot_in_settings)
        edit_action.setEnabled(has_shot)
        menu.addSeparator()

        menu.addAction("New Shot", self.controller._create_shot_one_click)
        menu.addAction("Generate Next Shot…", self._detect_next_shot)
        menu.addSeparator()
        before_action = menu.addAction(
            "Insert Shot Before",
            lambda: self.controller._insert_shot(sid, before=True),
        )
        after_action = menu.addAction(
            "Insert Shot After",
            lambda: self.controller._insert_shot(sid, before=False),
        )
        before_action.setEnabled(has_shot)
        after_action.setEnabled(has_shot)
        menu.addSeparator()
        split_action = menu.addAction("Split at Playhead", self._split_shot_at_playhead)
        merge_prev = menu.addAction(
            "Merge with Previous", lambda: self._merge_shot("prev")
        )
        merge_next = menu.addAction("Merge with Next", lambda: self._merge_shot("next"))
        neighbours = (
            self.controller._neighbour_shots(sid)
            if has_shot
            else {"merge_prev": None, "merge_next": None}
        )
        split_action.setEnabled(has_shot)
        merge_prev.setEnabled(neighbours["merge_prev"] is not None)
        merge_next.setEnabled(neighbours["merge_next"] is not None)
        menu.addSeparator()
        delete_action = menu.addAction("Delete Shot…", self._delete_shot)
        delete_action.setEnabled(has_shot)
        menu.exec_(cmb.mapToGlobal(pos))

    # ---- header menu (built here; auto-called by Switchboard) -------------

    def header_init(self, widget):
        """Build the header menu controls (mirror of mayatk's sequencer header)."""
        from uitk.widgets.widgetComboBox import WidgetComboBox

        widget.menu.add(
            "QSpinBox",
            setMinimum=0,
            setMaximum=1000,
            setValue=1,
            setObjectName="spn_snap",
            setPrefix="Snap: ",
            setToolTip="Snap interval for clip edges when dragging or resizing (0 = free movement).",
        )
        chk_snap_keys = widget.menu.add(
            "QCheckBox",
            setText="Snap to Keys",
            setObjectName="chk_snap_to_keys",
            setToolTip="Pull clip and key drags onto frames that already carry keys.\nAlignment guides are shown either way.",
        )
        chk_snap_keys.toggled.connect(self._on_snap_to_keys_toggled)
        cmb_pb = widget.menu.add(
            WidgetComboBox,
            setObjectName="cmb_playback_range",
            setToolTip="Control how the scene frame range tracks the visible shots.",
        )
        cmb_pb.addItem("Playback Range: Off", "off")
        cmb_pb.addItem("Playback Range: Follows View", "follows_view")
        cmb_pb.addItem("Playback Range: Locked to Shot", "locked")
        cmb_pb.setCurrentIndex(1)
        cmb_pb.currentIndexChanged.connect(self._on_playback_range_changed)

        cmb_scope = widget.menu.add(
            WidgetComboBox,
            setObjectName="cmb_track_order",
            setToolTip=self.sb.tooltip.fmt(
                title="Track Order",
                bullets=[
                    "<b>Visible:</b> Show objects from visible shots only.",
                    "<b>Global:</b> Show all objects from every shot so tracks never reorder when switching shots.",
                ],
            ),
        )
        cmb_scope.addItem("Track Order: Visible", "visible")
        cmb_scope.addItem("Track Order: Global", "global")
        cmb_scope.setCurrentIndex(
            0 if self.controller._track_order_scope == "visible" else 1
        )
        cmb_scope.currentIndexChanged.connect(self._on_track_order_changed)

        chk_select = widget.menu.add(
            "QCheckBox",
            setText="Select Members on Load",
            setObjectName="chk_select_on_load",
            setToolTip=(
                "Select all objects belonging to the shot\n"
                "when navigating to it in the sequencer."
            ),
        )
        chk_select.restore_state = False  # store owns this setting
        seq = getattr(self.controller, "sequencer", None)
        if seq is not None and hasattr(seq, "store"):
            chk_select.setChecked(seq.store.select_on_load)
        chk_select.toggled.connect(self.controller._on_select_on_load_toggled)

        chk_frame = widget.menu.add(
            "QCheckBox",
            setText="Frame on Shot Change",
            setObjectName="chk_frame_on_shot_change",
            setToolTip=(
                "Automatically frame the view on the shot's objects\n"
                "when navigating to a different shot."
            ),
        )
        chk_frame.restore_state = False  # store owns this setting
        if seq is not None and hasattr(seq, "store"):
            chk_frame.setChecked(seq.store.frame_on_shot_change)
        chk_frame.toggled.connect(self.controller._on_frame_on_shot_change_toggled)

        widget.menu.add("Separator", setTitle="Actions")
        widget.menu.add(
            "QPushButton",
            setText="Attribute Colors",
            setObjectName="btn_colors",
            setToolTip="Customize the colors used to display each animated attribute in the sequencer.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Shortcuts…",
            setObjectName="btn_shortcuts",
            setToolTip="View and customise sequencer keyboard shortcuts.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Shots…",
            setObjectName="btn_shot_settings",
            setToolTip="Open shared shot generation, gap, and editing settings.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Shot Sequencer",
                body="Visual timeline editor for per-shot animation with ripple editing, gap management, markers, and audio tracks.",
                sections=[
                    (
                        "Quick Start",
                        [
                            "Click <b>+</b> to create a shot (or use the Manifest).",
                            "Select a shot from the dropdown to load its clips.",
                            "Drag clips to adjust timing; drag edges to resize.",
                            "Use <b>View Mode</b> to see adjacent or all shots.",
                        ],
                    ),
                    (
                        "Shot Navigation",
                        [
                            "<b>Dropdown</b> — Select shot (sets playback range, selects objects, reframes the timeline). Right-click for Edit Shot, New Shot, Generate Next Shot, Delete Shot.",
                            "<b>◄ / ►</b> — Previous / next shot. &nbsp; <b>+</b> — Append new shot.",
                            "<b>View Mode</b> (cycles): Current → Adjacent → All.",
                            "<b>Refresh</b> — Rebuild from the scene.",
                        ],
                    ),
                    (
                        "Clips",
                        [
                            "<b>Drag body</b> — Move in time (ripple editing).",
                            "<b>Drag edge</b> — Resize the clip (scales its keyframes).",
                            "<b>Shift+drag</b> — Move across shot boundaries without changing them.",
                            "<b>Ctrl+drag</b> — Per-frame snap override.",
                            "A drag that lands on a frame already carrying keys is marked with a guide; <i>Snap to Keys</i> in the header menu also pulls the drag onto it.",
                            "<b>Right-click</b> — Lock/Unlock, Move to Shot, Delete Key. All edits undoable (Ctrl+Z).",
                        ],
                    ),
                    (
                        "Shot Edges",
                        [
                            "<b>Drag a shot edge</b> — The boundary moves; keyframes stay put.",
                            "<b>Shift+drag a shot edge</b> — Retime: the shot's keyframes scale into the new range.",
                            "The last shot has a handle at its end, same as every other shot.",
                        ],
                    ),
                    (
                        "Ruler / Tracks / Gaps / Markers",
                        [
                            "<b>Ruler:</b> Click/drag to move playhead, double-click to add a marker, scroll to zoom, middle-drag to pan.",
                            "<b>Shot Lane:</b> Right-click a shot block on the ruler to select, edit, insert before/after, or trim that shot.",
                            "<b>Tracks:</b> Double-click header to expand per-attribute sub-rows. Right-click to hide, delete, or reveal in Outliner.",
                            "<b>Gaps:</b> Drag body to slide adjacent shots, drag edge to resize (Shift retimes). Right-click to lock.",
                            "<b>Markers:</b> M or double-click ruler to add. Drag to move. Right-click to edit note, color, or style.",
                            "<b>Audio:</b> Auto-discovered from VSE sound strips. Drag to move; Move to Shot groups them with animation.",
                        ],
                    ),
                    (
                        "Keyboard",
                        [
                            (
                                self.sb.tooltip.kbd(_KB_LEFT)
                                + " / "
                                + self.sb.tooltip.kbd(_KB_RIGHT)
                                + " — prev / next key &nbsp;·&nbsp; "
                                + self.sb.tooltip.kbd("Shift", _KB_LEFT)
                                + " / "
                                + self.sb.tooltip.kbd("Shift", _KB_RIGHT)
                                + " — step ±1 frame"
                            ),
                            (
                                self.sb.tooltip.kbd("Home")
                                + " / "
                                + self.sb.tooltip.kbd("End")
                                + " — start / end &nbsp;·&nbsp; "
                                + self.sb.tooltip.kbd("F")
                                + " — frame shot &nbsp;·&nbsp; "
                                + self.sb.tooltip.kbd("M")
                                + " — add marker"
                            ),
                            (
                                self.sb.tooltip.kbd("Ctrl", "Z")
                                + " — undo &nbsp;·&nbsp; "
                                + self.sb.tooltip.kbd("Ctrl", "Shift", "Z")
                                + " — redo &nbsp;·&nbsp; "
                                + self.sb.tooltip.kbd("Del")
                                + " — delete keys"
                            ),
                        ],
                    ),
                ],
            )
        )

        # Wire the mode selector combobox (Shots / Markers).
        cmb_mode = getattr(self.ui, "cmb_mode", None)
        if cmb_mode is not None:
            cmb_mode.blockSignals(True)
            cmb_mode.clear()
            cmb_mode.addItem("Shots:", "shots")
            cmb_mode.addItem("Markers:", "markers")
            cmb_mode.setCurrentIndex(0)
            cmb_mode.blockSignals(False)
            cmb_mode.currentIndexChanged.connect(self._on_cmb_mode_changed)
            self.controller._cmb_mode_widget = cmb_mode

    # ---- auto-wired header slots -----------------------------------------

    def btn_colors(self):
        """Open the attribute color configuration dialog."""
        from uitk.managers.settings_manager import SettingsManager
        from uitk.widgets.sequencer._sequencer import (
            AttributeColorDialog,
            _COMMON_ATTRIBUTES,
            _DEFAULT_ATTRIBUTE_COLORS,
        )

        widget = self.controller._get_sequencer_widget()
        active_attrs = set()
        if widget:
            for clip in widget._clips.values():
                for attr in clip.data.get("attributes", []):
                    active_attrs.add(attr)
        color_settings = SettingsManager(namespace=AttributeColorDialog._SETTINGS_NS)
        dlg = AttributeColorDialog(
            defaults=dict(_DEFAULT_ATTRIBUTE_COLORS),
            common_attrs=list(_COMMON_ATTRIBUTES),
            active_attrs=sorted(active_attrs),
            settings=color_settings,
            parent=widget or self.ui,
        )

        def _apply(cmap):
            if widget:
                widget.attribute_colors = cmap
            self.controller._color_map_cache = None

        dlg.colors_changed.connect(_apply)
        dlg.exec_()

    def spn_snap(self, value):
        """Set the snap interval on the sequencer widget."""
        widget = self.controller._get_sequencer_widget()
        if widget is not None:
            widget.snap_interval = float(value)

    def btn_shortcuts(self):
        """Open the sequencer shortcut editor."""
        widget = self.controller._get_sequencer_widget()
        if widget is not None:
            widget._shortcut_mgr.show_editor(parent=widget, title="Sequencer Shortcuts")

    def btn_shot_settings(self):
        """Open the shared shots settings panel."""
        self.sb.handlers.marking_menu.show("shots")

    def cmb_shot(self, index):
        """Handle direct combobox selection of a shot or marker."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None or index < 0:
            return
        if self.controller._cmb_mode == "markers":
            marker_time = cmb.itemData(index)
            if marker_time is not None:
                widget = self.controller._get_sequencer_widget()
                if widget:
                    widget.set_playhead(marker_time)
                    widget.playhead_moved.emit(marker_time)
            return
        shot_id = cmb.itemData(index)
        if shot_id is None:
            return
        self.controller._shifted_out_keys.clear()
        self.controller.select_shot(shot_id)
        store = self.controller.sequencer.store if self.controller.sequencer else None
        do_frame = store.frame_on_shot_change if store else False
        self.controller._sync_to_widget(frame=do_frame)
        self.controller._update_shot_nav_state()
