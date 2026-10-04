# !/usr/bin/python
# coding=utf-8
"""The Shot Sequencer panel's controller: :class:`ShotSequencerController` (Blender).

Blender mirror of mayatk's ``shot_sequencer.shot_sequencer_controller``:
bridges the generic ``uitk`` :class:`SequencerWidget` to the Blender
:class:`ShotSequencer` engine. This module holds the controller's own state --
construction, the footer, the active-store binding and the active shot -- and
composes the interaction handlers from the concept mixins beside it (same
module names as mayatk's):

- ``scene_callbacks`` -- ``bpy.app.handlers`` undo/redo, frame-change and
  depsgraph events.
- ``undo_ledger`` -- the shot-boundary snapshots undo/redo step through.
- ``shot_lane`` -- the shot lane's context menu and shot structure edits.
- ``clip_menu`` -- clip context menus, Move to Shot, clip-key delete / stash.
- ``key_menu`` -- key context menus, handle edits and drags, key edits.
- ``widget_sync`` -- rebuilding the widget: tracks, clips, sub-rows, colours.
- ``scene_selection`` -- tracks and the Blender selection / Graph Editor sync.
- ``transport`` -- playhead, audio scrub and the transport row.
- ``gap_manager`` / ``clip_motion`` / ``shot_nav`` / ``marker_manager`` --
  gaps, clip drags, shot navigation and markers.

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
- Transport plays through ``screen.animation_play``
  (:class:`~.transport._BlenderPlayController`).
- Per-object icons come from :class:`~blendertk.ui_utils.node_icons.NodeIcons`.

The panel's Switchboard slots (:class:`ShotSequencerSlots`) stay in
``shot_sequencer_slots``, which re-imports this class.
"""

from typing import Optional

import pythontk as ptk
from pythontk import StoreEvent

from blendertk.anim_utils.shots._shots import BlenderShotStore
from blendertk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer
from blendertk.anim_utils.shots.shot_sequencer.clip_menu import ClipMenuMixin
from blendertk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin
from blendertk.anim_utils.shots.shot_sequencer.gap_manager import GapManagerMixin
from blendertk.anim_utils.shots.shot_sequencer.key_menu import KeyMenuMixin
from blendertk.anim_utils.shots.shot_sequencer.marker_manager import MarkerManagerMixin
from blendertk.anim_utils.shots.shot_sequencer.scene_callbacks import (
    SceneCallbacksMixin,
)
from blendertk.anim_utils.shots.shot_sequencer.scene_selection import (
    SceneSelectionMixin,
)
from blendertk.anim_utils.shots.shot_sequencer.shot_lane import ShotLaneMixin
from blendertk.anim_utils.shots.shot_sequencer.shot_nav import ShotNavMixin
from blendertk.anim_utils.shots.shot_sequencer.transport import TransportMixin
from blendertk.anim_utils.shots.shot_sequencer.undo_ledger import UndoLedgerMixin
from blendertk.anim_utils.shots.shot_sequencer.widget_sync import WidgetSyncMixin


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
    SceneCallbacksMixin,
    UndoLedgerMixin,
    ShotLaneMixin,
    ClipMenuMixin,
    KeyMenuMixin,
    WidgetSyncMixin,
    SceneSelectionMixin,
    TransportMixin,
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

    #: How far past a bound the "Extend to Keys" option reaches, in frames.
    #: ``ANY_REACH`` (-1) means any distance.  Mirror of mayatk.
    EXTEND_REACH_FRAMES = 24.0

    ANY_REACH = -1.0

    #: The shot combobox's cells (``uitk.ComboBox.set_cells``): one row per
    #: shot reads name / start / end / description, and double-clicking it
    #: edits those in place -- the Shots window's fields without the window.
    SHOT_CELLS = (
        {"key": "name", "label": "Name"},
        {"key": "start", "label": "Start", "kind": "int", "format": "{:.0f}"},
        {"key": "end", "label": "End", "kind": "int", "format": "{:.0f}"},
        {"key": "description", "label": "Description"},
    )

    SHOT_CELL_FORMAT = "{name}  [{start:.0f}-{end:.0f}]  {description}"

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
        # Global "grow the current shot over keys set just outside it".  Off
        # by default: it moves a bound the user did not touch.
        self._extend_to_keys: bool = False
        self._extend_reach: float = self.EXTEND_REACH_FRAMES
        # Keys copied from the key menu, awaiting a paste.
        self._copied_keys = None
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
        self._copied_keys = None  # keyed by object NAME; the names are gone
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
            # Nothing selected yet (the panel just opened, or markers mode):
            # the shot under the playhead is the one being worked on, and the
            # one the first framing should show.  The first shot stands in
            # only when the playhead sits outside every shot.
            under_playhead = self._shot_at_current_time()
            if under_playhead is not None:
                return under_playhead.shot_id
            return self.sequencer.sorted_shots()[0].shot_id
        return None

    @staticmethod
    def _scene_playback_range(scene) -> tuple:
        """*scene*'s playback range: the preview range when it is on (Blender's
        twin of Maya's playback range), else the scene range."""
        if scene.use_preview_range:
            return float(scene.frame_preview_start), float(scene.frame_preview_end)
        return float(scene.frame_start), float(scene.frame_end)

    def _current_time(self):
        """The playhead's frame, or ``None`` when there is no scene to ask."""
        try:
            import bpy

            return float(bpy.context.scene.frame_current_final)
        except Exception:
            return None

    def _shot_at_current_time(self):
        """The shot the playhead is in, or ``None``."""
        now = self._current_time()
        if now is None or self.sequencer is None:
            return None
        return self._find_shot_at_time(now)

    def _get_sequencer_widget(self):
        """Return the promoted SequencerWidget, or None (placeholder QSplitter)."""
        w = getattr(self.ui, "sequencer_widget", None)
        if w is not None and hasattr(w, "add_track"):
            return w
        return None
