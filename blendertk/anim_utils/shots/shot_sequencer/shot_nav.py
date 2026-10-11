# !/usr/bin/python
# coding=utf-8
"""Shot navigation and combobox synchronization (Blender).

Blender mirror of mayatk's ``shot_sequencer.shot_nav`` — :class:`ShotNavMixin`
handles shot selection, navigation, and combobox population.  Two DCC swaps vs.
the Maya original: object selection (``cmds.ls``/``cmds.select`` → Blender
``select_set`` + active object) and the view playback range (``cmds.playbackOptions``
→ the scene's PREVIEW range, ``frame_preview_start`` / ``frame_preview_end``: the
scene range is also the render range).
"""

from __future__ import annotations

__all__ = ["ShotNavMixin"]


class ShotNavMixin:
    """Mixin supplying shot selection and navigation.

    Expects the host controller to provide ``sequencer``, ``ui`` (with ``cmb_shot``),
    ``active_shot_id``, ``_playback_range_mode`` / ``_shot_display_mode``,
    ``_shifted_out_keys``, ``_cmb_mode`` / ``_cmb_mode_widget``,
    ``_prev_action`` / ``_next_action``, ``_syncing``, ``_sync_to_widget()`` /
    ``_update_shot_nav_state()``, ``_visible_shots()``, ``_get_sequencer_widget()``,
    ``_playhead_to_shot_start`` / ``on_playhead_moved()`` (the transport).
    """

    def select_shot(self, shot_id: int) -> None:
        """Set the view playback range to the shot and select its objects."""
        if self.sequencer is None:
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return
        # Self-originated: every caller syncs the widget afterwards, so suppress
        # this controller's own ActiveShotChanged full rebuild (other listeners —
        # e.g. the Shots settings panel — still receive the event).
        was_syncing = self._syncing
        self._syncing = True
        try:
            self.sequencer.store.set_active_shot(shot_id)
        finally:
            self._syncing = was_syncing
        self._apply_view_playback_range(shot)

        if not self.sequencer.store.select_on_load:
            return

        try:
            import bpy
        except ImportError:
            return
        from blendertk.core_utils._core_utils import CoreUtils

        # The window's view layer: windowless, bpy.context.selected_objects is absent
        # and context.view_layer / select_set address the scene's default layer.
        with CoreUtils.window_context_override():
            for o in list(bpy.context.selected_objects):
                o.select_set(False)
            active = None
            # Selection state is a view-layer concept: ``select_set`` raises on objects
            # outside the active view layer (excluded collection, another scene — the
            # ``bpy.data.objects`` lookup is scene-wide), so skip those instead of
            # letting one abort the loop mid-way.
            view_layer = bpy.context.view_layer
            for name in shot.objects:
                o = bpy.data.objects.get(name)
                if o is not None and o.name in view_layer.objects:
                    o.select_set(True)
                    active = o
            try:
                bpy.context.view_layer.objects.active = active
            except Exception:
                pass

    def _apply_view_playback_range(self, shot=None) -> None:
        """Set the scene frame range per the current playback-range mode.

        * ``"off"`` — no change.
        * ``"follows_view"`` — range covers all visible shots.
        * ``"locked"`` — range covers only the active shot.
        """
        if self._playback_range_mode == "off":
            return
        if self.sequencer is None:
            return
        if shot is None:
            sid = self.active_shot_id
            shot = self.sequencer.shot_by_id(sid) if sid is not None else None
        if shot is None:
            return

        if self._playback_range_mode == "follows_view":
            visible = self._visible_shots(shot)
            rng_start = min(s.start for s in visible)
            rng_end = max(s.end for s in visible)
        else:
            rng_start, rng_end = shot.start, shot.end

        try:
            import bpy
        except ImportError:
            return
        scene = bpy.context.scene
        if scene is not None:
            # The PREVIEW range: Blender's inner range is Maya's playback range
            # (``playbackOptions -min/-max``), and the scene range it used to
            # write is also the render range -- every shot switch rewrote the
            # file's output frames.
            scene.use_preview_range = True
            scene.frame_preview_start = int(round(rng_start))
            scene.frame_preview_end = int(round(rng_end))

    def _sync_combobox(self) -> None:
        """Populate the shot combobox and update prev/next action state."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return

        # The store's active shot is the model and the dropdown a view of it:
        # after an undo, or a pick in the Shots window, the row the dropdown
        # still shows can name a shot that is gone or no longer active.
        store_sid = self.sequencer.store.active_shot_id if self.sequencer else None
        if store_sid is not None and self.sequencer.shot_by_id(store_sid):
            old_sid = store_sid
        else:
            old_sid = self.active_shot_id

        cmb.blockSignals(True)
        cmb.clear()

        if self._cmb_mode == "markers":
            widget = self._get_sequencer_widget()
            markers = sorted(widget.markers(), key=lambda m: m.time) if widget else []
            if markers:
                for md in markers:
                    label = f"@ {md.time:.0f}"
                    if md.note:
                        label += f"  {md.note}"
                    cmb.addItem(label, md.time)
            else:
                cmb.addItem("No markers", None)
            cmb.blockSignals(False)
            self._update_shot_nav_state()
            return

        if self.sequencer is None:
            cmb.blockSignals(False)
            return
        cells = getattr(cmb, "cell_spec", None)
        for shot in self.sequencer.sorted_shots():
            if cells:
                # One cell per field: the popup reads as a table and a
                # double-click edits the fields in place (see
                # _on_shot_cells_edited).
                cmb.add_cells(
                    {
                        "name": shot.name,
                        "start": shot.start,
                        "end": shot.end,
                        "description": shot.description or "",
                    },
                    shot.shot_id,
                )
                continue
            label = f"{shot.name}  [{shot.start:.0f}-{shot.end:.0f}]"
            if shot.description:
                label += f"  {shot.description}"
            cmb.addItem(label, shot.shot_id)
        # Restore previous selection
        if old_sid is not None:
            for i in range(cmb.count()):
                if cmb.itemData(i) == old_sid:
                    cmb.setCurrentIndex(i)
                    break
        cmb.blockSignals(False)
        self._update_shot_nav_state()

    def _configure_shot_combobox(self, cmb) -> None:
        """Make *cmb* a multi-cell shot list whose rows edit in place
        (mirror of mayatk's; idempotent, late-bound through
        ``cmb._nav_controller``)."""
        set_cells = getattr(cmb, "set_cells", None)
        if not callable(set_cells):
            return  # a plain QComboBox (tests): the label form stands
        set_cells(self.SHOT_CELLS, cell_format=self.SHOT_CELL_FORMAT)
        cmb.rename_on_double_click = True
        cmb._nav_controller = self
        if not getattr(cmb, "_shot_cells_wired", False):
            cmb.on_cells_edited.connect(
                lambda index, cells, c=cmb: c._nav_controller._on_shot_cells_edited(
                    index, cells
                )
            )
            cmb._shot_cells_wired = True

    def _on_shot_cells_edited(self, index: int, cells: dict) -> None:
        """Apply an inline edit of the shot combobox's cells to that shot:
        name / description are plain fields, a new start MOVES the shot
        (keys ride), a new end moves that bound alone (keys stay); start
        before end so both can be typed together (mirror of mayatk's)."""
        from pythontk import ShotBoundaryConflict

        if self.sequencer is None or self._cmb_mode != "shots":
            return
        cmb = getattr(self.ui, "cmb_shot", None)
        sid = cmb.itemData(index) if cmb is not None else None
        shot = self.sequencer.shot_by_id(sid) if sid is not None else None
        if shot is None:
            return
        store = self.sequencer.store
        fields = {}
        name = str(cells["name"]) if "name" in cells else shot.name
        if name != shot.name:
            # The name is the exported clip name: refuse one the export would
            # respell, and say why, rather than store it.
            error = store.name_error(name, shot.shot_id)
            if error:
                self.logger.warning(f"Shot name not changed. {error}")
                self._set_footer(error)
            else:
                fields["name"] = name
        if "description" in cells:
            fields["description"] = str(cells["description"])
        was_syncing = self._syncing
        self._syncing = True
        try:
            with store.scene_edit("Edit Shot"):
                if fields:
                    store.update_shot(shot.shot_id, **fields)
                if "start" in cells and abs(float(cells["start"]) - shot.start) > 1e-6:
                    self.sequencer.move_shot(shot.shot_id, float(cells["start"]))
                if "end" in cells and abs(float(cells["end"]) - shot.end) > 1e-6:
                    self.sequencer.resize_shot_bounds(
                        shot.shot_id, shot.start, float(cells["end"])
                    )
        except ShotBoundaryConflict as exc:
            # The restore point stays with the step ``scene_edit`` pushed: the
            # rename and the move may already have run when the resize is
            # declined, and undoing the step has to take them back whole.
            self.logger.warning(str(exc))
            self._set_footer(str(exc))
        finally:
            self._syncing = was_syncing
        self._after_shot_change(shot_id=shot.shot_id)

    def _update_shot_nav_state(self) -> None:
        """Enable/disable prev/next option box actions based on combobox index."""
        cmb = getattr(self.ui, "cmb_shot", None)
        idx = cmb.currentIndex() if cmb is not None else 0
        count = cmb.count() if cmb is not None else 0
        if self._prev_action is not None:
            self._prev_action.widget.setEnabled(idx > 0)
        if self._next_action is not None:
            self._next_action.widget.setEnabled(idx < count - 1)

    def _navigate_shot(self, delta: int) -> None:
        """Move to the previous (-1) or next (+1) shot."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return
        new_idx = cmb.currentIndex() + delta
        if new_idx < 0 or new_idx >= cmb.count():
            return
        # Programmatic index change — the explicit select/sync below does the
        # work; letting the auto-wired cmb_shot slot fire too would rebuild twice.
        cmb.blockSignals(True)
        cmb.setCurrentIndex(new_idx)
        cmb.blockSignals(False)
        if self._cmb_mode == "markers":
            # Markers carry a TIME, not a shot id — jump the playhead (same path
            # as the cmb_shot slot); routing the float time into select_shot can
            # match an unrelated integer shot id.
            marker_time = cmb.itemData(new_idx)
            if marker_time is not None:
                widget = self._get_sequencer_widget()
                if widget:
                    widget.set_playhead(marker_time)
                    widget.playhead_moved.emit(marker_time)
            self._update_shot_nav_state()
            return
        self._go_to_shot(cmb.itemData(new_idx))

    def _go_to_shot(self, shot_id) -> None:
        """Change to *shot_id*: make it active, then draw it.

        The one path every shot change takes -- the dropdown, the prev / next
        buttons, a click on a shot's block -- so the header's options apply to
        each alike: Frame on Shot Change, and Playhead to Shot Start
        (:meth:`_land_on_shot`), which leaves the playhead alone when the shot
        was already the active one.
        """
        if self.sequencer is None:
            return
        store = self.sequencer.store
        changed = shot_id != store.active_shot_id
        self._shifted_out_keys.clear()
        self.select_shot(shot_id)
        shot = self.sequencer.shot_by_id(shot_id)
        if changed and shot is not None:
            self._land_on_shot(shot)
        self._sync_to_widget(frame=store.frame_on_shot_change)
        self._update_shot_nav_state()

    def _set_playhead_to_shot_start(self, enabled: bool) -> None:
        """Turn the global "a shot change moves the playhead to the shot's
        first frame" option on/off (header menu)."""
        self._playhead_to_shot_start = bool(enabled)

    def _land_on_shot(self, shot) -> None:
        """Put the playhead on *shot*'s first frame -- when the header's
        Playhead to Shot Start is on.

        For a shot changed to, or one just created: it is the one the user
        is about to work in, and the playhead left where the gesture began sat
        in another shot or in none (2026-10-06: "move the playhead to the
        start of a new shot when it is created"; 2026-10-07: "... when we
        change shots.  this should be a global option").  A view move, never
        an undo step (:meth:`on_playhead_moved`); the rebuild that follows
        draws it.
        """
        if self._playhead_to_shot_start:
            self.on_playhead_moved(shot.start)

    def on_shot_block_clicked(self, shot_name: str) -> None:
        """Select a shot by name when its block is clicked in the shot lane."""
        if self.sequencer is None:
            return
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return
        for shot in self.sequencer.sorted_shots():
            if shot.name == shot_name:
                for i in range(cmb.count()):
                    if cmb.itemData(i) == shot.shot_id:
                        cmb.blockSignals(True)
                        cmb.setCurrentIndex(i)
                        cmb.blockSignals(False)
                        break
                self._go_to_shot(shot.shot_id)
                return
