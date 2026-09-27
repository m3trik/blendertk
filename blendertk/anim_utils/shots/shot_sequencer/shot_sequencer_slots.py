# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shot Sequencer UI (Blender).

Blender mirror of mayatk's ``shot_sequencer.shot_sequencer_slots`` — bridges the
generic ``uitk`` :class:`SequencerWidget` to the Blender :class:`ShotSequencer`
engine, with the same public slot class (:class:`ShotSequencerSlots`) and widget
signal-wiring table so the tentacle nav stays branch-free.

The controller behind the slots (and the DCC swaps it makes versus the Maya
original) lives in :mod:`shot_sequencer_controller`, composed from the concept
mixins beside it.
"""

import pythontk as ptk

from blendertk.anim_utils.shots.shot_sequencer.shot_sequencer_controller import (
    ShotSequencerController,
)

_KB_LEFT = "←"
_KB_RIGHT = "→"


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
        validate=None,
    ):
        """Show a modal dialog and return the result tuple or ``None``.

        *validate* is ``(name) -> reason or None`` (a store's
        :meth:`~pythontk.ShotStore.name_error`): while it has a reason the
        dialog shows it and will not accept, so a name the export would
        respell never reaches the store.  The name comes back as typed.
        """
        from qtpy import QtWidgets

        dlg = QtWidgets.QDialog(parent)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(280)
        layout = QtWidgets.QFormLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)

        name_edit = QtWidgets.QLineEdit(name)
        name_edit.setPlaceholderText("Shot name")
        name_edit.setToolTip(
            f"Exported as the clip name: {ptk.ShotStore.NAME_RULE}, "
            "unique ignoring case."
        )
        layout.addRow("Name:", name_edit)
        name_error = QtWidgets.QLabel()
        name_error.setWordWrap(True)
        name_error.setStyleSheet(f"color: {ptk.SHOT_PALETTE['error'][0]};")
        layout.addRow(name_error)
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
        ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)

        def _check_name(text):
            error = validate(text) if validate is not None else None
            name_error.setText(error or "")
            name_error.setVisible(bool(error))
            ok_button.setEnabled(not error)

        name_edit.textChanged.connect(_check_name)
        _check_name(name_edit.text())
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return None
        return (
            name_edit.text(),
            start_spin.value(),
            end_spin.value(),
            desc_edit.text().strip(),
        )


class ShotSequencerSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    # (widget signal, controller slot) wiring table — mirror of mayatk's.
    _WIRING = [
        ("clip_resized", "on_clip_resized"),
        ("clips_batch_resized", "on_clips_batch_resized"),
        ("clip_moved", "on_clip_moved"),
        ("clips_batch_moved", "on_clips_batch_moved"),
        ("clip_renamed", "on_clip_renamed"),
        ("playhead_moved", "on_playhead_moved"),
        ("track_hidden", "hide_track"),
        ("track_shown", "show_track"),
        ("track_deleted", "delete_track"),
        ("selection_changed", "on_selection_changed"),
        ("track_selected", "on_track_selected"),
        ("sub_track_selected", "on_sub_track_selected"),
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
        ("key_menu_requested", "on_key_menu"),
        ("keys_tangent_dragged", "on_keys_tangent_dragged"),
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

            # The panel's own key bindings.  ``add_shortcut`` disposes and
            # replaces a same-sequence binding, so a slots re-init over the
            # same loaded UI re-points them at the new controller instead of
            # stacking a second one.  WindowShortcut context: Qt claims the key
            # at the window level and the host never sees it -- which is also
            # why the panel binds these itself, the host's own hotkeys never
            # reaching a focused Qt tool window.
            try:
                from qtpy import QtCore as _QtCore

                _mgr = getattr(sequencer, "_shortcut_mgr", None)
                for _key, _action, _desc in (
                    (
                        "Delete",
                        self.controller._delete_selected_clip_keys,
                        "Delete keys for selected clips",
                    ),
                    (
                        "Ctrl+C",
                        self.controller._copy_keys_shortcut,
                        "Copy the selected keys",
                    ),
                    (
                        "Ctrl+V",
                        self.controller._paste_keys_shortcut,
                        "Paste copied keys at the playhead",
                    ),
                ):
                    if _mgr is not None:
                        _mgr.add_shortcut(
                            _key, _action, _desc, _QtCore.Qt.WindowShortcut
                        )
            except Exception:
                self.logger.debug("shortcut wiring failed", exc_info=True)

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
        # Before the re-init early return: the cells are the controller's
        # (a fresh one on every re-init), the option boxes are the widget's.
        self.controller._configure_shot_combobox(cmb)
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

    def _on_shortcut_overlay_changed(self, index: int) -> None:
        """Off / On / On Modifier for the corner legend of gestures and keys."""
        cmb = getattr(self.ui, "cmb_shortcut_overlay", None)
        widget = self.controller._get_sequencer_widget()
        if cmb is None or widget is None:
            return
        mode = cmb.itemData(index)
        if mode in widget.SHORTCUT_OVERLAY_MODES:
            widget.shortcut_overlay_mode = mode

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

    def _detect_next_shot(self) -> None:
        """Generate a shot from the next unregistered animation cluster."""
        seq = self.controller.sequencer
        if seq is None:
            return
        store = seq.store  # never None: a sequencer builds one when not given
        cand = seq.detect_next_shot(gap_threshold=store.detection_threshold)
        if cand is None:
            self.controller._set_footer("No additional animation clusters found.")
            return
        result = ShotEditDialog.show(
            parent=self.ui,
            # Detection numbers its clusters from 1; the scene may hold those,
            # and then the next free Shot_<n> is proposed, not Shot_1_2.
            name=store.default_name(cand["name"]),
            start=cand["start"],
            end=cand["end"],
            title="Generated Shot",
            validate=store.name_error,
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
        if self.controller._cmb_mode != "shots":
            return
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return
        from uitk.widgets.context_menu import ContextMenu

        has_shot = self.controller.active_shot_id is not None
        menu = ContextMenu(parent=cmb)
        # Editing the shot you just picked is what this menu is reached
        # for most often, so it leads; creation and the structural edits
        # follow, each verb fanning out into its forms on hover.
        menu.add(
            "Edit Shot\u2026", callback=self._edit_shot_in_settings, setEnabled=has_shot
        )
        menu.add_separator()
        # New Shot (with Insert Before / After), Split at Playhead and Merge
        # all live on the shot body's own menu, over the shot they act on.
        # Repeating them here made this menu a worse copy of that one.
        # Generate Next Shot has no twin there, so it stays -- at the top
        # level, since the row it used to hang under is gone.
        menu.add("Generate Next Shot\u2026", callback=self._detect_next_shot)
        menu.add_separator()
        menu.add("Delete Shot\u2026", callback=self._delete_shot, setEnabled=has_shot)
        seq = self.controller.sequencer
        stale = seq.store.stale_shots() if seq is not None else []
        menu.add(
            "Delete Stale Shots\u2026",
            callback=self.controller.delete_stale_shots,
            setEnabled=bool(stale),
            setToolTip=(
                f"{len(stale)} shot(s) name only objects the file no longer "
                "holds and key nothing -- exports already leave them out.\n"
                "Deletes their records; no keyframe is touched, no shot moves."
                if stale
                else "No stale shots: every shot names an object the file "
                "holds, or keys something in its frames."
            ),
        )
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
        # Extend to Keys: one global switch, not a per-shot action.  Keys set
        # (or pasted) outside the current shot pull its bound out to cover
        # them, capped by the reach below -- which is the whole question the
        # option asks, so it sits right under it and greys out with it.
        chk_extend = widget.menu.add(
            "QCheckBox",
            setText="Extend to Keys",
            setObjectName="chk_extend_to_keys",
            setToolTip=(
                "Grow the current shot to cover keys created outside it.\n"
                "Keys inside a neighbouring shot are never claimed."
            ),
        )
        spn_reach = widget.menu.add(
            "QSpinBox",
            setObjectName="spn_extend_reach",
            setPrefix="Extend Distance: ",
            setSuffix=" frames",
            setMinimum=int(self.controller.ANY_REACH),
            setMaximum=100000,
            setSpecialValueText="Extend Distance: any",
            setValue=int(self.controller.EXTEND_REACH_FRAMES),
            setToolTip=(
                "How far outside a bound a new key may sit and still be "
                "reached for.\nAt the minimum (-1) the distance is not "
                "capped at all."
            ),
        )
        spn_reach.setEnabled(False)
        chk_extend.toggled.connect(self.controller._set_extend_to_keys)
        chk_extend.toggled.connect(spn_reach.setEnabled)
        spn_reach.valueChanged.connect(self.controller._set_extend_reach)
        cmb_overlay = widget.menu.add(
            WidgetComboBox,
            setObjectName="cmb_shortcut_overlay",
            setToolTip="Keep a legend of the drag grammar and keys in the timeline's corner;\nthe group under the pointer is lit.\n\nOn Modifier shows it only while Ctrl, Shift or Alt is held.",
        )
        cmb_overlay.addItem("Shortcut Overlay: Off", "off")
        cmb_overlay.addItem("Shortcut Overlay: On", "on")
        cmb_overlay.addItem("Shortcut Overlay: On Modifier", "modifier")
        cmb_overlay.setCurrentIndex(0)
        cmb_overlay.currentIndexChanged.connect(self._on_shortcut_overlay_changed)

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
                            "<b>Dropdown</b> — Select shot (sets playback range, selects objects, reframes the timeline). Right-click for Edit Shot, Generate Next Shot, Delete Shot, Delete Stale Shots — the shot body's own menu carries the rest.",
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
                            "<b>Ctrl</b> while dragging — Snap to whole frames.",
                            "A drag that lands on a frame already carrying keys is marked with a guide; <i>Snap to Keys</i> in the header menu also pulls the drag onto it.",
                            "<b>Right-click</b> — Lock/Unlock, Move to Shot (Next / Previous Shot lead the list), Store Keys (one entry per gesture, however many channels it covered), Retrieve Stored Keys (▸ Restore Keys… opens the Key Stash panel). On a key: handle types, interpolation, Break/Unify Tangents, Store Keys, the key edits under Edit, Move to Shot (keys); drag a selected key's handles to shape its tangents — the drag carries every selected key, <b>Ctrl</b> reshapes only the one grabbed, <b>Shift</b> gives them all that exact tangent, <b>Alt</b> breaks it. All edits undoable (Ctrl+Z).",
                        ],
                    ),
                    (
                        "Shot Edges",
                        [
                            "The shot's own edges (and the ruler band's edges) never move its keyframes:",
                            "<b>Drag</b> — Move the bound; the neighbouring shots move with their keys to keep the gaps.",
                            "<b>Ctrl+drag</b> — Move the bound and nothing else: the shot grows into the gap (taking the keys it covers) or shrinks and leaves them for the next shot.",
                            "<b>Shift+drag</b> — Retime: the shot's keyframes scale into the new range.",
                            "<b>Drag the ruler band</b> — Move the shot with its keys. A gap's edge belongs to the shot beyond it: drag to slide that shot, Ctrl moves that bound only, Shift retimes it. The <i>Shortcut Overlay</i> (header menu) keeps this legend in the timeline's corner.",
                        ],
                    ),
                    (
                        "Ruler / Tracks / Gaps / Markers",
                        [
                            "<b>Ruler:</b> Click/drag to move playhead, double-click to add a marker, scroll to zoom, middle-drag to pan.",
                            "<b>Shot Lane:</b> Right-click a shot block on the ruler for its menu: Edit, New Shot (insert before / after), Split Here (or at the current time), Merge (previous / next), Move To (re-slot it among the other shots; the one holding that slot moves downstream), Add Frames, Trim Empty Space (leading / trailing). Hover a row to open its finer forms. Right-click the ruler, or the tracks clear of every shot, for the timeline's own menu (markers and display toggles). Double-click the shot dropdown to edit name / start / end / description in place.",
                            "<b>Tracks:</b> Double-click header to expand per-attribute sub-rows. Right-click to hide, delete, or reveal in Outliner.",
                            "<b>Gaps:</b> Drag an edge to slide the shot beyond it (Ctrl moves the bound only, Shift retimes); drag the body to slide the gap. Right-click to lock. The caps before the first shot and after the last are those shots' own bounds: a plain drag moves the bound and nothing else.",
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
        from uitk import AttributeColorDialog

        widget = self.controller._get_sequencer_widget()
        dlg = AttributeColorDialog(
            defaults=ptk.Palette.channels(),
            common_attrs=list(ptk.TRANSFORM_CHANNELS),
            active_attrs=widget.clip_attributes() if widget else [],
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
