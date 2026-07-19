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

Presentation-data helpers live in :mod:`segment_collector`; the interaction
handlers are inherited from the four mixins (gap / clip-motion / nav / marker).

Divergences (ledgered): **audio tracks** (VSE sound-strip display in the sequencer)
and **move-to-shot sequence grouping** are deferred — the object-animation timeline
is fully wired; audio time-shifting matches the engine's "no audio shifting" note.
Per-node icons degrade to uitk's named-icon set (no Maya ``:/`` resources).
"""
from collections import defaultdict
from typing import Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import undo_chunk
from blendertk.anim_utils.shots._shots import BlenderShotStore
from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer
from blendertk.anim_utils.shots.shot_sequencer.gap_manager import GapManagerMixin
from blendertk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin
from blendertk.anim_utils.shots.shot_sequencer.shot_nav import ShotNavMixin
from blendertk.anim_utils.shots.shot_sequencer.marker_manager import MarkerManagerMixin
from blendertk.anim_utils.shots.shot_sequencer.segment_collector import (
    collect_segments,
    active_object_set,
    extract_attributes,
    build_curve_preview,
)
from blendertk.anim_utils.shots.shot_sequencer.clip_motion import curves_for_attr
from pythontk import StoreEvent

_KB_LEFT = "←"
_KB_RIGHT = "→"


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
):
    """Business logic controller bridging SequencerWidget ↔ ShotSequencer."""

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
        self._shot_undo_stack: list = []
        self._shifted_out_keys: dict = {}
        self._prev_action = None
        self._next_action = None
        self._view_mode_action = None
        self._cmb_mode_widget = None
        self._playback_range_mode = "follows_view"
        self._track_order_scope = "visible"
        self._cmb_mode = "shots"
        # NOTE: no _show_internal_holds / _holds_action — Blender's segment
        # collector has no hold sub-splitting (ledgered divergence), so
        # mayatk's Show Internal Holds toggle has nothing to reveal here.

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
            label.setStyleSheet(f"background: transparent; border: none; color: {color};")
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
        idx = next((i for i, s in enumerate(self.sequencer.sorted_shots())
                    if s.shot_id == shot_id), 0)
        parts = [f"[{idx + 1}/{n_shots}]", f"{dur}f",
                 f"{n_obj} object{'s' if n_obj != 1 else ''}"]
        self._set_footer(" · ".join(parts))

    # ---- sequencer property (lazy from store) ----------------------------

    @property
    def sequencer(self) -> Optional[ShotSequencer]:
        if self._sequencer is None:
            store = BlenderShotStore.active()
            self._sequencer = ShotSequencer(store=store)
            self.logger.debug("Lazy-initialized ShotSequencer from BlenderShotStore.active().")
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
        self._shot_undo_stack.clear()
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
            scene = _scene()
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
        if self._syncing:
            return
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
            self._sub_row_cache = {k: v for k, v in self._sub_row_cache.items()
                                   if k[0] != active_id}
            added = self._auto_add_keyed_objects(active_id)
        else:
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            added = False
        if not added:
            self._sync_to_widget()

    def _auto_add_keyed_objects(self, shot_id: int) -> bool:
        """Merge newly-keyed *selected* transforms into the active shot's objects."""
        if self.sequencer is None:
            return False
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return False
        try:
            import bpy
        except ImportError:
            return False
        selected = [o.name for o in bpy.context.selected_objects]
        if not selected:
            return False
        existing = set(shot.objects)
        candidates = [s for s in selected if s not in existing]
        if not candidates:
            return False
        keyed = set(self.sequencer._find_keyed_transforms(shot.start, shot.end))
        new_objects = [c for c in candidates if c in keyed]
        if not new_objects:
            return False
        merged = sorted(existing | set(new_objects))
        self.sequencer.store.update_shot(shot_id, objects=merged)
        return True

    # ---- name resolution (flat in Blender) -------------------------------

    @staticmethod
    def _resolve_full_name(name: str) -> str:
        """Identity — Blender object names are flat and unique."""
        return name.rsplit("|", 1)[-1] if "|" in name else name

    @classmethod
    def _try_load_blender_icons(cls):
        """No Maya ``:/`` node-type icons in Blender — uitk named icons are the fallback."""
        return None

    def _select_and_show(self, obj_names) -> None:
        """Select the given objects (Blender Outliner/Graph Editor follow selection)."""
        try:
            import bpy
        except ImportError:
            return
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        active = None
        for name in obj_names:
            o = bpy.data.objects.get(name)
            if o is not None:
                o.select_set(True)
                active = o
        try:
            bpy.context.view_layer.objects.active = active
        except Exception:
            pass

    def _reveal_in_outliner(self, obj_names) -> None:
        self._select_and_show(obj_names)

    def _open_spreadsheet(self, track_names) -> None:
        """Maya's Attribute Spreadsheet has no direct Blender analogue — no-op."""
        self._set_footer("Attribute spreadsheet is Maya-only.")

    # -- zone context menus ------------------------------------------------

    def on_zone_context_menu(self, zone: str, time: float, global_pos) -> None:
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
        act_select = act_edit = act_trim = None
        if clicked_shot is not None:
            act_select = menu.addAction(f'Select "{clicked_shot.name}"')
            act_edit = menu.addAction(f'Edit "{clicked_shot.name}"…')
            menu.addSeparator()
            act_trim = menu.addAction("Trim Empty Space")
            menu.addSeparator()
        act_new = menu.addAction("New Shot")
        menu.addSeparator()
        act_refresh = menu.addAction("Refresh")
        chosen = menu.exec_(global_pos)
        if chosen is None:
            return
        if chosen == act_select and clicked_shot is not None:
            self.on_shot_block_clicked(clicked_shot.name)
        elif chosen == act_edit and clicked_shot is not None:
            self._edit_shot_dialog(clicked_shot)
        elif chosen == act_trim and clicked_shot is not None:
            self._trim_shot(clicked_shot.shot_id)
        elif chosen == act_new:
            self._create_shot_one_click()
        elif chosen == act_refresh:
            self.refresh()

    def _trim_shot(self, shot_id: int) -> None:
        if self.sequencer is None:
            return
        self._save_shot_state()
        with undo_chunk():
            self.sequencer.trim_shot_to_content(shot_id)
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._sync_combobox()

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
        from pythontk.core_utils.engines.shots.manifest.behaviors import compute_duration

        duration = compute_duration([], fallback=100.0)
        shot = store.append_shot(name=name, duration=duration, gap=gap)
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

    def _save_shot_state(self) -> None:
        if self.sequencer is None:
            return
        state = [(s.shot_id, s.start, s.end, list(s.objects)) for s in self.sequencer.shots]
        self._shot_undo_stack.append(state)
        if len(self._shot_undo_stack) > 50:
            self._shot_undo_stack.pop(0)

    def _restore_shot_state(self) -> None:
        if not self._shot_undo_stack or self.sequencer is None:
            return
        state = self._shot_undo_stack.pop()
        store = self.sequencer.store
        with store.batch_update():
            for shot_id, start, end, objects in state:
                store.update_shot(shot_id, start=start, end=end, objects=objects)

    def on_undo(self) -> None:
        """Widget undo_requested — restore the shot-boundary snapshot + refresh.

        The shot-state snapshot is the DCC-agnostic undo for boundary moves;
        native Blender key undo is handled by the ``undo_post`` handler.
        """
        self._syncing = True
        try:
            self._restore_shot_state()
        except Exception:
            self.logger.debug("on_undo: _restore_shot_state failed", exc_info=True)
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def on_redo(self) -> None:
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def _visible_shots(self, active_shot):
        if self._shot_display_mode == "current":
            return [active_shot]
        sorted_shots = self.sequencer.sorted_shots()
        if self._shot_display_mode == "all":
            return sorted_shots
        idx = next((i for i, s in enumerate(sorted_shots) if s.shot_id == active_shot.shot_id), None)
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

    def _sync_to_widget(self, shot_id: Optional[int] = None, *, frame: bool = False) -> None:
        widget, shot = self._resolve_sync_target(shot_id)
        if widget is None or shot is None:
            widget = self._get_sequencer_widget()
            if widget is not None and self.sequencer is not None and not self.sequencer.shots:
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

        scene = _scene()
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
        scene_shot = ShotBlock(shot_id=-1, name="Scene", start=start, end=end,
                               objects=sorted(set(discovered)))
        # The synthetic scene_shot (id -1) isn't in the store, so collect its
        # per-object spans directly from the keyed transforms.
        segments = self._scene_segments(scene_shot)
        segments_by_shot = {scene_shot.shot_id: segments}
        all_objects = set(scene_shot.objects) | {seg["obj"] for seg in segments}
        track_ids = self._build_tracks(widget, all_objects, all_objects, active_shot=scene_shot)
        self._build_clips(widget, scene_shot, [scene_shot], segments_by_shot, track_ids)
        self._ensure_scene_attr_colors(widget)
        widget.set_playhead(scene.frame_current)
        widget.set_active_range(start, end)
        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
        n = len(scene_shot.objects)
        self._set_footer(f"Scene  {start:.0f}–{end:.0f}  ·  {n} object{'s' if n != 1 else ''}")

    def _scene_segments(self, shot):
        """Per-object keyed-span segments for a synthetic (unstored) shot.

        Delegates to the engine's ``_span_segments`` — the one home for the
        segment-dict shape — so the shotless view can't drift from
        ``collect_object_segments``.
        """
        scene = _scene()
        if scene is None or self.sequencer is None:
            return []
        return self.sequencer._span_segments(scene, shot.objects, shot.start, shot.end)

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
            segments_by_shot, all_objects = collect_segments(
                self.sequencer, shot, visible_shots, self._segment_cache,
                self._shifted_out_keys, self.logger,
            )
            if self._track_order_scope == "global":
                for s in self.sequencer.sorted_shots():
                    all_objects.update(s.objects)
            active_objects = active_object_set(shot, segments_by_shot)
            track_ids = self._build_tracks(widget, all_objects, active_objects, active_shot=shot)
            self._build_clips(widget, shot, visible_shots, segments_by_shot, track_ids)
            self._ensure_scene_attr_colors(widget)
            # Audio-track display deferred (see module docstring).
        finally:
            self._syncing = False

    def _rebuild_decoration(self, widget, shot, visible_shots) -> None:
        scene = _scene()
        current_time = scene.frame_current if scene is not None else shot.start
        widget.set_playhead(current_time)
        widget.set_hidden_tracks(sorted(self.sequencer.hidden_objects))
        widget.set_active_range(shot.start, shot.end)
        widget.set_range_highlight(shot.start, shot.end)
        all_sorted = self.sequencer.sorted_shots()
        store = self.sequencer.store
        shot_blocks = [
            {"name": s.name, "start": s.start, "end": s.end,
             "active": s.shot_id == shot.shot_id}
            for s in all_sorted
        ]
        widget.set_shot_blocks(shot_blocks)
        for m in self.sequencer.markers:
            widget.add_marker(time=m["time"], note=m.get("note", ""), color=m.get("color"),
                              draggable=m.get("draggable", True), style=m.get("style", "triangle"),
                              line_style=m.get("line_style", "dashed"), opacity=m.get("opacity", 1.0))
        for i in range(len(all_sorted) - 1):
            left, right = all_sorted[i], all_sorted[i + 1]
            if right.start - left.end > -0.5:
                locked = store.is_gap_locked(left.shot_id, right.shot_id)
                widget.add_gap_overlay(left.end, right.start, locked=locked)
        for s in all_sorted:
            if s.shot_id != shot.shot_id:
                widget.add_range_overlay(s.start, s.end, color="#000000", alpha=40)

    def _restore_viewport(self, widget, frame, h_scroll, zoom, expanded_names) -> None:
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
        spn_gap = getattr(self.ui, "spn_gap", None)
        if spn_gap is not None:
            stored_gap = self.sequencer.store.gap if self.sequencer else 0
            spn_gap.blockSignals(True)
            spn_gap.setValue(int(stored_gap))
            spn_gap.blockSignals(False)
        if self._color_map_cache is None:
            from uitk.widgets.sequencer._sequencer import (
                AttributeColorDialog, _DEFAULT_ATTRIBUTE_COLORS,
            )
            from uitk.managers.settings_manager import SettingsManager

            color_settings = SettingsManager(namespace=AttributeColorDialog._SETTINGS_NS)
            color_map = dict(_DEFAULT_ATTRIBUTE_COLORS)
            for key in color_settings.keys():
                val = color_settings.value(key)
                if val:
                    color_map[key] = val
            self._color_map_cache = color_map
        widget.attribute_colors = self._color_map_cache

    _AUTO_PALETTE = ["#5B8BD4", "#6EBF6E", "#D4A65B", "#C45C5C",
                     "#8E6FBF", "#5BBFB4", "#BF6E8E", "#8EB05B"]

    def _ensure_scene_attr_colors(self, widget) -> None:
        if widget is None:
            return
        from hashlib import md5

        color_map = widget.attribute_colors
        changed = False
        for clip in widget._clips.values():
            for attr in clip.data.get("attributes", []):
                if attr not in color_map:
                    idx = int(md5(attr.encode()).hexdigest(), 16) % len(self._AUTO_PALETTE)
                    color_map[attr] = self._AUTO_PALETTE[idx]
                    changed = True
        if changed:
            widget.attribute_colors = color_map

    def _build_tracks(self, widget, all_objects, active_objects, active_shot=None) -> dict:
        from pythontk import SHOT_PALETTE

        obj_classes = active_shot.classify_objects() if active_shot and hasattr(active_shot, "classify_objects") else {}
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

        for obj_name in ordered:
            if self.sequencer.is_object_hidden(obj_name):
                continue
            exists = obj_name in existing_set
            if not exists and not self.sequencer.store.is_object_pinned(obj_name):
                continue
            in_active = obj_name in active_objects
            icon = None
            if not exists:
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
            tid = widget.add_track(obj_name.split("|")[-1], icon=icon,
                                   dimmed=not in_active or not exists,
                                   italic=not in_active and exists, **color_kw)
            track_ids[obj_name] = tid
        return track_ids

    def _build_clips(self, widget, shot, visible_shots, segments_by_shot, track_ids):
        from pythontk import SHOT_PALETTE

        for vs in visible_shots:
            is_active = vs.shot_id == shot.shot_id
            segs = segments_by_shot.get(vs.shot_id, [])
            obj_classes = vs.classify_objects() if hasattr(vs, "classify_objects") else {}
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
                span_segs = sorted((sg for sg in obj_segs if not sg.get("is_stepped")),
                                   key=lambda sg: sg["start"])
                merged: list = []
                for seg in span_segs:
                    if merged and seg["start"] <= merged[-1]["end"] + gap:
                        merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
                        merged[-1]["segs"].append(seg)
                    else:
                        merged.append({"start": seg["start"], "end": seg["end"], "segs": [seg]})

                for m in merged:
                    s, e = m["start"], m["end"]
                    attrs = extract_attributes(m["segs"])
                    clip_extra = dict(extra)
                    clip_extra.update({
                        "obj": obj_name, "shot_id": vs.shot_id,
                        "orig_start": s, "orig_end": e, "attributes": attrs,
                    })
                    try:
                        widget.add_clip(track_id=tid, start=s, duration=max(e - s, 0.0),
                                        label=obj_name.split("|")[-1], **clip_extra)
                    except Exception:
                        self.logger.debug("add_clip failed for %s", obj_name, exc_info=True)

    def _provide_sub_rows(self, track_id, track_name):
        """Per-attribute sub-rows: ``[(attr, [(start, dur, label, color, extra)...])...]``.

        Blender builds these from the object's transform fcurves grouped by channel
        (via :func:`curves_for_attr`), with a bezier ``build_curve_preview`` per attr.
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
        from blendertk.anim_utils.shots._shots import iter_action_fcurves, _is_transform_path
        from blendertk.anim_utils.shots.shot_sequencer.segment_collector import attr_label

        widget = self._get_sequencer_widget()
        color_map = widget.attribute_colors if widget else {}
        store = self.sequencer.store if self.sequencer else None
        is_obj_locked = bool(store and obj_name in store.locked_objects)

        # Group fcurves by channel label, gather their keyed spans in the shot.
        by_attr: dict = {}
        for fc in iter_action_fcurves(obj):
            if not _is_transform_path(fc.data_path):
                continue
            label = attr_label(fc)
            times = [kp.co[0] for kp in fc.keyframe_points
                     if shot.start - 1e-6 <= kp.co[0] <= shot.end + 1e-6]
            if not times:
                continue
            entry = by_attr.setdefault(label, {"lo": min(times), "hi": max(times), "fc": fc})
            entry["lo"] = min(entry["lo"], min(times))
            entry["hi"] = max(entry["hi"], max(times))

        result = []
        for label in sorted(by_attr):
            info = by_attr[label]
            s, e = info["lo"], info["hi"]
            color = color_map.get(label)
            extra = {"obj": obj_name, "shot_id": shot_id, "attr_name": label,
                     "orig_start": s, "orig_end": e,
                     "is_stepped": abs(e - s) < 1e-6, "attributes": [label]}
            if is_obj_locked:
                extra["locked"] = True
            preview = build_curve_preview(info["fc"], shot.start, shot.end)
            if preview is not None:
                extra["curve_preview"] = preview
            result.append((label, [(s, max(e - s, 0.0), label, color, extra)]))

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
        if not clip_ids:
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
            attrs = clip.data.get("attributes") or ([clip.data.get("attr_name")] if clip.data.get("attr_name") else [])
            start, end = clip.data.get("orig_start"), clip.data.get("orig_end")
            parts = [obj]
            if attrs:
                parts.append(", ".join(a for a in attrs[:3] if a))
            if start is not None and end is not None:
                parts.append(f"{start:.0f}–{end:.0f} ({int(end - start)}f)")
            labels.append(" · ".join(parts))
        self._select_and_show(resolved)
        if labels:
            self._set_footer("  |  ".join(labels[:3]) +
                             (f"  (+{len(labels) - 3} more)" if len(labels) > 3 else ""))

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
        menu.addSeparator()
        resolved = [self._resolve_full_name(n) for n in track_names]
        menu.addAction("Select in Viewport",
                       lambda objs=list(resolved): self._select_and_show(objs))

    def on_header_menu(self, menu) -> None:
        """Header background context menu — no domain actions this phase."""

    def on_clip_renamed(self, clip_id: int, new_label: str) -> None:
        """Renaming a clip is display-only in Blender (object names own identity)."""

    def on_playhead_moved(self, frame: float) -> None:
        """Widget playhead drag → set the scene frame."""
        scene = _scene()
        if scene is None:
            return
        self._syncing_playhead = True
        try:
            scene.frame_set(int(round(frame)))
        finally:
            self._syncing_playhead = False

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
        act_delete = menu.addAction(f"Delete Keys ({len(selected_ids)})" if multi else "Delete Key")
        act_delete.triggered.connect(lambda: self._delete_clip_keys(selected_ids))
        if obj_name and self.sequencer:
            menu.addSeparator()
            menu.addAction("Lock Others", lambda: self._lock_others(widget, obj_name))
            menu.addAction("Unlock All", lambda: self._unlock_all(widget))

    def on_gap_menu(self, menu, gap_start: float, gap_end: float) -> None:
        """Gap overlay context menu — no domain actions this phase."""

    def on_key_selection_changed(self, key_groups: list) -> None:
        """Per-key selection changed — footer feedback only."""
        n = sum(len(g["times"]) for g in key_groups) if key_groups else 0
        if n:
            self._set_footer(f"{n} key{'s' if n != 1 else ''} selected")

    def _lock_others(self, widget, keep_obj: str) -> None:
        store = self.sequencer.store if self.sequencer else None
        if store is None:
            return
        obj_names = {cd.data.get("obj") for cd in widget._clips.values()
                     if cd.data.get("obj") and not getattr(cd, "sub_row", False)
                     and not cd.data.get("read_only")}
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
        from blendertk.anim_utils.shots._shots import iter_action_fcurves, _is_transform_path

        self._save_shot_state()
        deleted = 0
        with undo_chunk():
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
                    curves_for_attr(obj.name, attr)
                    if attr
                    else [fc for fc in iter_action_fcurves(obj)
                          if _is_transform_path(fc.data_path)]
                )
                for fc in fcurves:
                    victims = [kp for kp in fc.keyframe_points if s - 1e-3 <= kp.co[0] <= e + 1e-3]
                    for kp in reversed(victims):
                        fc.keyframe_points.remove(kp)
                        deleted += 1
                    if victims:
                        fc.update()
        if deleted:
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            self._sync_to_widget()
            self._set_footer(f"Deleted {deleted} key{'s' if deleted != 1 else ''}")

    def _delete_selected_clip_keys(self) -> None:
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        selected = widget.selected_clips() or []
        if selected:
            self._delete_clip_keys(selected)

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
                                self.controller._delete_selected_clip_keys)
                    else:
                        mgr.add_shortcut("Delete", self.controller._delete_selected_clip_keys,
                                         "Delete keys for selected clips", _ctx)
            except Exception:
                self.logger.debug("Delete shortcut wiring failed", exc_info=True)

        self._setup_shot_nav()
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

    def _setup_shot_nav(self) -> None:
        """Prev/next/view-mode/add/refresh option-box actions on cmb_shot.

        Mirror of mayatk's ``_setup_shot_nav`` minus the *Show Internal Holds*
        toggle — Blender's segment collector returns one span per object (no
        ``SegmentKeys`` hold sub-splitting; ledgered divergence), so there are
        no hold spans for the toggle to reveal.  Port it together with the
        sub-splitting follow-up.
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
            ctl._cmb_mode_widget = getattr(self.ui, "cmb_mode", None)
            return
        try:
            from uitk.widgets.optionBox.options.action import ActionOption

            prev_opt = ActionOption(wrapped_widget=cmb,
                                    callback=lambda: cmb._nav_controller._navigate_shot(-1),
                                    icon="chevron_left", tooltip="Previous Shot", order=0)
            next_opt = ActionOption(wrapped_widget=cmb,
                                    callback=lambda: cmb._nav_controller._navigate_shot(1),
                                    icon="chevron_right", tooltip="Next Shot", order=1)

            # "+" button — one-click shot creation (mirror of mayatk).
            add_opt = ActionOption(
                wrapped_widget=cmb,
                callback=lambda: cmb._nav_controller._create_shot_one_click(),
                icon="add", tooltip="New Shot", order=2,
            )

            # View mode cycle: Current → Adjacent → All (mirror of mayatk).
            _VIEW_STATES = [
                {"icon": "target",
                 "tooltip": "View: Current Shot (click for adjacent)",
                 "callback": lambda: cmb._nav_controller._set_view_mode("adjacent")},
                {"icon": "columns",
                 "tooltip": "View: Adjacent Shots (click for all)",
                 "callback": lambda: cmb._nav_controller._set_view_mode("all")},
                {"icon": "grid",
                 "tooltip": "View: All Shots (click for current)",
                 "callback": lambda: cmb._nav_controller._set_view_mode("current")},
            ]
            view_opt = ActionOption(wrapped_widget=cmb, states=_VIEW_STATES, order=4)

            # Refresh button — re-collect animation data and rebuild the widget.
            refresh_opt = ActionOption(
                wrapped_widget=cmb,
                callback=lambda: cmb._nav_controller.refresh(),
                icon="refresh", tooltip="Refresh Sequencer", order=6,
            )

            cmb.option_box.set_order(["action"])
            for opt in (prev_opt, next_opt, add_opt, view_opt, refresh_opt):
                cmb.option_box.add_option(opt)

            self.controller._prev_action = prev_opt
            self.controller._next_action = next_opt
            self.controller._view_mode_action = view_opt
            # Sync controller view mode from persisted button state.
            self.controller._shot_display_mode = _VIEW_MODE_MAP.get(
                view_opt.current_state, "current"
            )
            cmb._shot_nav_options = {"prev": prev_opt, "next": next_opt,
                                     "add": add_opt, "view": view_opt,
                                     "refresh": refresh_opt}
        except Exception:
            self.logger.debug("shot nav option-box setup failed", exc_info=True)
        self.controller._cmb_mode_widget = getattr(self.ui, "cmb_mode", None)

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

    # ---- header menu (built here; auto-called by Switchboard) -------------

    def header_init(self, widget):
        """Build the header menu controls (mirror of mayatk's sequencer header)."""
        from uitk.widgets.mixins.tooltip_mixin import fmt, kbd
        from uitk.widgets.widgetComboBox import WidgetComboBox

        widget.menu.add(
            "QSpinBox", setMinimum=0, setMaximum=1000, setValue=1,
            setObjectName="spn_snap", setPrefix="Snap: ",
            setToolTip="Snap interval for clip edges when dragging or resizing (0 = free movement).",
        )
        cmb_pb = widget.menu.add(
            WidgetComboBox, setObjectName="cmb_playback_range",
            setToolTip="Control how the scene frame range tracks the visible shots.",
        )
        cmb_pb.addItem("Playback Range: Off", "off")
        cmb_pb.addItem("Playback Range: Follows View", "follows_view")
        cmb_pb.addItem("Playback Range: Locked to Shot", "locked")
        cmb_pb.setCurrentIndex(1)
        cmb_pb.currentIndexChanged.connect(self._on_playback_range_changed)

        cmb_scope = widget.menu.add(
            WidgetComboBox, setObjectName="cmb_track_order",
            setToolTip=fmt(
                title="Track Order",
                bullets=[
                    "<b>Visible:</b> Show objects from visible shots only.",
                    "<b>Global:</b> Show all objects from every shot so tracks never reorder when switching shots.",
                ],
            ),
        )
        cmb_scope.addItem("Track Order: Visible", "visible")
        cmb_scope.addItem("Track Order: Global", "global")
        cmb_scope.setCurrentIndex(0 if self.controller._track_order_scope == "visible" else 1)
        cmb_scope.currentIndexChanged.connect(self._on_track_order_changed)

        chk_select = widget.menu.add(
            "QCheckBox", setText="Select Members on Load",
            setObjectName="chk_select_on_load",
            setToolTip=("Select all objects belonging to the shot\n"
                        "when navigating to it in the sequencer."),
        )
        chk_select.restore_state = False  # store owns this setting
        seq = getattr(self.controller, "sequencer", None)
        if seq is not None and hasattr(seq, "store"):
            chk_select.setChecked(seq.store.select_on_load)
        chk_select.toggled.connect(self.controller._on_select_on_load_toggled)

        chk_frame = widget.menu.add(
            "QCheckBox", setText="Frame on Shot Change",
            setObjectName="chk_frame_on_shot_change",
            setToolTip=("Automatically frame the view on the shot's objects\n"
                        "when navigating to a different shot."),
        )
        chk_frame.restore_state = False  # store owns this setting
        if seq is not None and hasattr(seq, "store"):
            chk_frame.setChecked(seq.store.frame_on_shot_change)
        chk_frame.toggled.connect(self.controller._on_frame_on_shot_change_toggled)

        widget.menu.add("Separator", setTitle="Actions")
        widget.menu.add(
            "QPushButton", setText="Attribute Colors", setObjectName="btn_colors",
            setToolTip="Customize the colors used to display each animated attribute in the sequencer.",
        )
        widget.menu.add(
            "QPushButton", setText="Shortcuts…", setObjectName="btn_shortcuts",
            setToolTip="View and customise sequencer keyboard shortcuts.",
        )
        widget.menu.add(
            "QPushButton", setText="Shots…", setObjectName="btn_shot_settings",
            setToolTip="Open shared shot generation, gap, and editing settings.",
        )
        widget.set_help_text(
            fmt(
                title="Shot Sequencer",
                body="Visual timeline editor for per-shot animation with ripple editing, gap management, and markers.",
                sections=[
                    ("Quick Start", [
                        "Create a shot (or use the Manifest).",
                        "Select a shot from the dropdown to load its clips.",
                        "Drag clips to adjust timing; drag edges to resize.",
                        "Use <b>View Mode</b> to see adjacent or all shots.",
                    ]),
                    ("Clips", [
                        "<b>Drag body</b> — Move in time (ripple editing).",
                        "<b>Drag edge</b> — Resize (scales keyframes).",
                        "<b>Shift+drag</b> — Move boundaries only; keyframes stay in place.",
                        "<b>Right-click</b> — Lock/Unlock, Delete Key. All edits undoable (Ctrl+Z).",
                    ]),
                    ("Ruler / Tracks / Gaps / Markers", [
                        "<b>Ruler:</b> Click/drag to move playhead, double-click to add a marker, scroll to zoom.",
                        "<b>Tracks:</b> Double-click header to expand per-attribute sub-rows.",
                        "<b>Gaps:</b> Drag body to slide adjacent shots, drag edge to resize. Right-click to lock.",
                        "<b>Markers:</b> M or double-click ruler to add. Drag to move.",
                        "<b>Audio:</b> VSE sound strips (sequencer display is a documented follow-up).",
                    ]),
                    ("Keyboard", [
                        (kbd(_KB_LEFT) + " / " + kbd(_KB_RIGHT)
                         + " — prev / next key &nbsp;·&nbsp; "
                         + kbd("Shift", _KB_LEFT) + " / " + kbd("Shift", _KB_RIGHT)
                         + " — step ±1 frame"),
                        (kbd("Ctrl", "Z") + " — undo &nbsp;·&nbsp; "
                         + kbd("Del") + " — delete keys"),
                    ]),
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
            AttributeColorDialog, _COMMON_ATTRIBUTES, _DEFAULT_ATTRIBUTE_COLORS,
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
            settings=color_settings, parent=widget or self.ui,
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
