# !/usr/bin/python
# coding=utf-8
"""UI slots for the Channels panel (``channels.ui``).

``ChannelsSlots`` — a single-table Switchboard interface for inspecting, editing, locking, and
keying a Blender object's channels (transform + custom properties). Mirror of mayatk's
``node_utils.attributes.channels.channels_slots``; delegates all non-UI logic to :class:`Channels`.
Discovered by ``BlenderUiHandler`` (``marking_menu.show("channels")``).

The table, filters, lock/key/mute action columns, value editing (direct + MMB-scrub +
wheel-scrub), create/delete/rename, freeze/unfreeze, breakdown, mute/unmute, select-connection,
compact view, auto-fit window, and the context-menu operations all mirror the Maya tool 1:1 via
native bpy equivalents (the scrub/wheel value editing and auto-fit window sizing ride the shared
``uitk.TableWidget``/Qt infra, so they are DCC-agnostic — not Maya niceties). The only Maya items
intentionally omitted are concepts with no Blender surface to act on:
  - **No channel box** → Toggle Keyable, Hide/Show Selected, Lock-and-Hide (every channel is
    keyable; there is no per-channel channel-box visibility flag).
  - **No DAG transform/shape split** → Select Shape Node / Select History Node (a Blender object
    owns its mesh data directly and modifiers are not selectable scene nodes).
  - **Channel-Box Qt-signal sync** (a Maya-editor-only nicety — Blender has no Channel Box).
  - **Enum attribute creation** (Maya's colon-separated enum labels) → Create Attribute offers
    float / int / bool / string; Blender custom props have no Maya-style enum type on arbitrary
    objects. The Channel Control / Connection editors map to Properties / Drivers / Graph editors.
``__init__`` is Qt-only (no ``bpy``) so the panel loads under the workspace ``.venv``; the live data
refresh + selection-change subscription are guarded so they no-op without a running Blender.
"""
from blendertk.node_utils.attributes.channels._channels import Channels


class ChannelsSlots:
    """Switchboard slots for the Channels panel.

    Layout
    ------
    - **Header menu**: Create Attribute, column-visibility toggles, native-editor shortcuts.
    - **Filter ComboBox** (``cmb000``) + invert: which channels are displayed.
    - **Name filter** (``txt000``): wildcard name filter.
    - **Target display** (``txt001``): active object; double-click to rename; single-object toggle.
    - **Table** (``tbl000``): one row per channel. Columns: Name | Lock | Key | Value | Type.
    """

    # Column indices — Name | Lock | Key/Connect | Value | Type
    COL_NAME = 0
    COL_LOCK = 1
    COL_CONN = 2
    COL_VALUE = 3
    COL_TYPE = 4

    _ROW_SELECTION_COLUMNS = {"name": 0, "value": 3, "type": 4}

    # Desaturated colour scheme mirrored from the Maya panel.
    ACTION_COLOR_MAP = {
        "off": "#555555",
        "active": "#6898b8",
        "locked": "#8a9bb0",
        "keyframe": "#c86464",
        "keyframe_active": "#a83838",
        "driven_key": "#6898b8",
        "constraint": "#5878b8",
        "muted": "#888850",  # olive — muted F-curve / driver
    }

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.channels
        self.controller = Channels()
        self._filter_invert = False
        self._compact_view = False
        self._base_row_height = None  # snapshotted on first _apply_row_height
        self._current_target = None
        # Descriptors aligned 1:1 with the current table rows (row index → channel descriptor).
        self._row_descriptors = []

        # Compact-mode footer plumbing (built lazily in _setup_compact_footer; all Qt-only).
        self._footer_warning = ""
        self._footer_compact_btn = None
        self._footer_lineedit = None
        self._footer_edit_page = -1
        self._footer_controller = self._create_footer_controller()
        self._setup_compact_footer()

        # MMB scrub-edit drag state — set by the scrub handlers; cleared on release.
        self._scrub_state = None

        # Name filter — debounced refresh (mirror of mayatk: bursts of keystrokes collapse
        # into a single rebuild 200ms after the user stops typing).
        QtCore = self.sb.QtCore
        self._filter_timer = QtCore.QTimer()
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(lambda: self._refresh_table(self.ui.tbl000))
        self.ui.txt000.textChanged.connect(lambda *_: self._filter_timer.start())
        self.ui.txt000.option_box.clear_option = True
        self.ui.destroyed.connect(self._filter_timer.stop)

        # Filter on/off toggle as an action button on the option_box (mirror of mayatk's
        # txt000 3rd add_action — see tentacle/docs/parity_map.py "add_action"). The action
        # cycles two states; each callback sets up the NEXT state before the cycle advances.
        clr = self.ACTION_COLOR_MAP
        self._filter_enabled = True
        self.ui.txt000.option_box.add_action(
            icon="filter",
            tooltip="Toggle name filter",
            states=[
                {
                    "icon": "filter",
                    "tooltip": "Filter ON — click to disable",
                    "color": clr["active"],
                    "callback": lambda: self._set_filter_enabled(False),
                },
                {
                    "icon": "filter",
                    "tooltip": "Filter OFF — click to enable",
                    "color": clr["off"],
                    "callback": lambda: self._set_filter_enabled(True),
                },
            ],
            settings_key=False,
        )

        # txt001 — target display + double-click-to-rename (read-only until double-clicked).
        self._setup_target_field()

    def apply_launch_config(self, targets=None, filter=None, search=None):
        """Configure the window from an external launch call (mirror of mayatk).

        Safe to call repeatedly — applies pin/filter/search to the already-constructed UI.
        Pass ``targets=None`` to clear a pin.
        """
        self.controller.pin_targets(targets)

        if filter:
            if filter in Channels.FILTER_MAP:
                cmb = getattr(self.ui, "cmb000", None)
                if cmb is not None:
                    cmb.setCurrentText(filter)
            else:
                import logging

                logging.getLogger(__name__).warning(
                    "launch(filter=%r) ignored — not a FILTER_MAP key. Valid: %s",
                    filter,
                    sorted(Channels.FILTER_MAP.keys()),
                )

        if search is not None:
            self.ui.txt000.setText(search)

        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Target field (txt001)
    # ------------------------------------------------------------------

    def _setup_target_field(self):
        txt1 = self.ui.txt001
        txt1.setPlaceholderText("No selection")
        txt1.setToolTip("Double-click to rename.")
        txt1.setReadOnly(True)
        txt1.editingFinished.connect(self._on_target_renamed)

        def _dbl_click(event, _orig=txt1.mouseDoubleClickEvent):
            if txt1.isReadOnly() and self._current_target is not None:
                txt1.setReadOnly(False)
                txt1.setToolTip(
                    f"{self._current_target.name}"
                    "\n\n(Type a new name and press Enter to rename.)"
                )
                txt1.selectAll()
                txt1.setFocus()
                return
            _orig(event)

        txt1.mouseDoubleClickEvent = _dbl_click

        def _key_press(event, _orig=txt1.keyPressEvent):
            Qt = self.sb.QtCore.Qt

            if event.key() == Qt.Key_Escape and not txt1.isReadOnly():
                if self._current_target is not None:
                    txt1.blockSignals(True)
                    txt1.setText(self._current_target.name)
                    txt1.blockSignals(False)
                txt1.setReadOnly(True)
                txt1.setToolTip("Double-click to rename.")
                return
            _orig(event)

        txt1.keyPressEvent = _key_press

        # Single-object-mode toggle on the field's option box (mirror of the
        # Maya "target" action).  Keep the ActionOption so _sync_target_toggles
        # can drive its visuals from the controller.
        self._target_action = txt1.option_box.add_action(
            icon="target",
            tooltip="Single-object mode",
            states=self._target_toggle_states(),
            settings_key=False,
        )

    def _target_toggle_states(self):
        """State dicts shared by both single/multi-object toggles.

        State index 0 = multi-object (off tint), 1 = single-object (active
        tint).  Each state's callback fires *before* the cycle advances, so
        it sets the value for the NEXT state; _set_single_object then syncs
        every toggle widget explicitly, which supersedes the auto-advance
        (see IconStates.activate).
        """
        clr = self.ACTION_COLOR_MAP
        return [
            {
                "icon": "target",
                "tooltip": "Multi-object mode — edits broadcast to every selected object.",
                "color": clr["off"],
                "callback": lambda: self._set_single_object(True),
            },
            {
                "icon": "target",
                "tooltip": "Single-object mode — only the active object is edited.",
                "color": clr["active"],
                "callback": lambda: self._set_single_object(False),
            },
        ]

    def _set_single_object(self, enabled):
        """Toggle single-object mode and sync every toggle widget to it."""
        self.controller.single_object_mode = bool(enabled)
        self._sync_target_toggles()
        self._refresh_table(self.ui.tbl000)

    def _sync_target_toggles(self):
        """Point both single/multi toggles (txt001 option_box action and the
        compact footer button) at the controller's current mode."""
        idx = 1 if self.controller.single_object_mode else 0
        action = getattr(self, "_target_action", None)
        if action is not None:
            action.current_state = idx
        btn = self._footer_compact_btn
        cycle = getattr(btn, "icon_states", None) if btn is not None else None
        if cycle is not None:
            cycle.current_state = idx

    def _update_target_display(self, objects):
        txt = self.ui.txt001
        txt.blockSignals(True)
        try:
            if not objects:
                self._current_target = None
                txt.setText("")
                txt.setToolTip("No selection")
            elif len(objects) == 1:
                self._current_target = objects[0]
                txt.setText(objects[0].name)
                txt.setToolTip(f"{objects[0].name}\n\n(Double-click to rename.)")
            else:
                self._current_target = None
                txt.setText(f"Multi-selection ({len(objects)})")
                names = "\n".join(f"  • {o.name}" for o in objects)
                txt.setToolTip(f"Selected objects:\n{names}")
        finally:
            txt.blockSignals(False)
            txt.setReadOnly(True)

    def _on_target_renamed(self):
        txt = self.ui.txt001
        was_editing = not txt.isReadOnly()
        txt.setReadOnly(True)
        if not was_editing or self._current_target is None:
            return
        new_name = txt.text().strip()
        if not new_name or new_name == self._current_target.name:
            return
        self.controller.rename_node(self._current_target, new_name)
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Filter ComboBox (cmb000)
    # ------------------------------------------------------------------

    def cmb000_init(self, widget):
        """Populate the filter combobox + wire its invert action (bpy-free)."""
        widget.addItems(list(Channels.FILTER_MAP.keys()))
        clr = self.ACTION_COLOR_MAP
        widget.option_box.add_action(
            icon="invert",
            tooltip="Invert filter",
            states=[
                {
                    "icon": "invert",
                    "tooltip": "Invert OFF — click to show the complement of the filter.",
                    "color": clr["off"],
                    "callback": lambda: self._set_filter_invert(True),
                },
                {
                    "icon": "invert",
                    "tooltip": "Invert ON — click to show the normal filter set.",
                    "color": clr["active"],
                    "callback": lambda: self._set_filter_invert(False),
                },
            ],
            settings_key=False,
        )

    def cmb000(self, index):
        """Filter changed — refresh table."""
        self._refresh_table(self.ui.tbl000)

    def _set_filter_invert(self, enabled):
        self._filter_invert = bool(enabled)
        self._refresh_table(self.ui.tbl000)

    def _set_filter_enabled(self, enabled):
        """Toggle whether the name filter (txt000) is applied (mirror of mayatk)."""
        self._filter_enabled = bool(enabled)
        self._refresh_table(self.ui.tbl000)

    def _filter_key(self):
        cmb = getattr(self.ui, "cmb000", None)
        label = cmb.currentText() if cmb else "Custom"
        return Channels.FILTER_MAP.get(label, "custom")

    # ------------------------------------------------------------------
    # Header menu
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Populate the header menu (Qt-only; editor shortcuts defer ``bpy`` to click time)."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add("Separator", setTitle="Create")
        widget.menu.add(
            "QPushButton",
            setText="Create Attribute …",
            setToolTip="Add a new custom property to the selected object(s).",
            setObjectName="show_create_menu",
        )

        widget.menu.add("Separator", setTitle="Visibility")
        chk_type = widget.menu.add(
            "QCheckBox",
            setText="Show Type",
            setChecked=False,
            setToolTip="Toggle the Type column in the table.",
            setObjectName="chk_show_type",
        )
        chk_type.toggled.connect(self._on_toggle_type_column)
        self._chk_show_type = chk_type

        chk_compact = widget.menu.add(
            "QCheckBox",
            setText="Compact View",
            setChecked=True,
            setToolTip="Reduce row height, hide the column header, and move the object name "
            "into the footer (with inline rename).",
            setObjectName="chk_compact_view",
        )
        chk_compact.toggled.connect(self._on_toggle_compact_view)
        self._chk_compact = chk_compact

        chk_auto_fit = widget.menu.add(
            "QCheckBox",
            setText="Auto-fit Window",
            setChecked=True,
            setToolTip="Resize columns to fit contents and grow/shrink the "
            "window to match on every refresh.",
            setObjectName="chk_auto_fit",
        )
        chk_auto_fit.toggled.connect(self._on_toggle_auto_fit)
        self._chk_auto_fit = chk_auto_fit

        # Compact view is push-only; reconcile it with the (possibly
        # restored) checkbox now so the checked-by-default state is
        # actually applied on first launch — persistent-state restore
        # only re-emits ``toggled`` when the stored value differs from
        # the construction default. Auto-fit needs no equivalent: it is
        # read on every refresh. Mirror of the Maya panel.
        self._sync_compact_default()

        # TODO(blender-parity): mayatk's "Selection" header section (Select Shape Node /
        # Select History Node) has no Blender analogue — a Blender object owns its mesh
        # data directly (no DAG transform/shape split) and modifiers aren't selectable
        # scene nodes. Intentionally omitted; ledgered in tentacle/docs/parity_map.py
        # (hdr_select_shape / hdr_select_history).
        widget.menu.add("Separator", setTitle="Blender Editors")
        widget.menu.add(
            "QPushButton", setText="Properties Editor …", setObjectName="hdr_properties",
            setToolTip="Open Blender's Properties editor (Object tab) in a new window.",
        )
        widget.menu.add(
            "QPushButton", setText="Drivers Editor …", setObjectName="hdr_drivers",
            setToolTip="Open Blender's Drivers editor in a new window.",
        )
        widget.menu.add(
            "QPushButton", setText="Graph Editor …", setObjectName="hdr_graph",
            setToolTip="Open Blender's Graph editor (F-curves) in a new window.",
        )
        widget.menu.hdr_properties.clicked.connect(lambda: self._open_editor("Properties", "OBJECT"))
        widget.menu.hdr_drivers.clicked.connect(lambda: self._open_editor("Drivers"))
        widget.menu.hdr_graph.clicked.connect(lambda: self._open_editor("Graph Editor"))

        widget.set_help_text(
            fmt(
                title="Channels",
                body="Inspect, edit, lock, and key a Blender object's channels — its transform "
                "(location / rotation / scale) and custom properties — in a spreadsheet-style table.",
                sections=[
                    ("Table", [
                        "Each row is one channel on the active selection.",
                        "Edit values directly in the Value column, or MMB-drag / mouse-wheel "
                        "over it to scrub numeric channels.",
                        "Click the lock icon to lock/unlock a transform channel.",
                        "Click the key icon to set/remove a keyframe at the current frame; "
                        "Ctrl+click to break the animation/driver.",
                        "A muted F-curve / driver shows the key icon in olive.",
                    ]),
                    ("Wheel-scrub modifiers", [
                        "plain &mdash; ×1",
                        "<b>Ctrl</b> &mdash; ×10 (coarse)",
                        "<b>Ctrl+Shift</b> &mdash; ×100 (very coarse)",
                        "<b>Alt</b> &mdash; ÷10 (fine)",
                        "<b>Ctrl+Alt</b> &mdash; smallest representable step",
                    ]),
                    ("Right-click (context menu)", [
                        "<b>Edit</b> — lock / unlock / reset to default.",
                        "<b>Values</b> — copy / paste channel values.",
                        "<b>Animation</b> — breakdown, mute / unmute, select connection "
                        "(driver / constraint target), break animation.",
                        "<b>Transform</b> — freeze / unfreeze transforms.",
                        "<b>Manage</b> — delete custom propert(ies).",
                    ]),
                    ("Filter (top-left)", [
                        "<b>Custom</b> — custom properties only.",
                        "<b>Keyable</b> — transform channels + custom properties.",
                        "<b>Locked</b> — locked transform channels.",
                        "<b>Animated</b> — channels driven by an F-curve or driver.",
                        "Invert (option box) shows the complement of the filter.",
                    ]),
                    ("Header", [
                        "<b>Create Attribute…</b> — add a custom property.",
                        "<b>Compact View</b> — collapse rows, hide the column header, "
                        "and move the object name into the footer.",
                        "<b>Auto-fit Window</b> — resize columns and grow/shrink the "
                        "window to match contents on every refresh.",
                        "<b>Properties / Drivers / Graph Editor…</b> — open the native editor.",
                    ]),
                ],
            )
        )

    def _open_editor(self, editor, properties_context=None):
        """Open a native Blender editor window (best-effort; reports failures to the user)."""
        try:
            import blendertk as btk

            if btk.open_editor(editor, properties_context) is None:
                self.sb.message_box(f"Could not open the {editor} editor.")
        except Exception as e:
            self.sb.message_box(f"Could not open the {editor} editor:\n{e}")

    def _on_toggle_type_column(self, visible):
        self.ui.tbl000.setColumnHidden(self.COL_TYPE, not visible)

    def _on_toggle_auto_fit(self, _enabled):
        """Re-apply column sizing when the auto-fit toggle changes."""
        self._refresh_table(self.ui.tbl000)

    def _sync_compact_default(self):
        """Reconcile ``self._compact_view`` with the compact-view checkbox.

        ``_on_toggle_compact_view`` is push-only (it fires from the
        checkbox's ``toggled`` signal), so a checked-but-unchanged box —
        the checked-by-default first launch, or a state restore that
        doesn't re-emit ``toggled`` — would leave the push effects
        unapplied. Mirror the Type-column pull model: whenever the
        checkbox disagrees with the applied state, apply it. Idempotent
        and cheap, so it's safe to call on every refresh. Mirror of the
        Maya panel.
        """
        chk = getattr(self, "_chk_compact", None)
        if chk is None:
            return
        checked = chk.isChecked()
        if checked != self._compact_view:
            self._on_toggle_compact_view(checked)

    # ------------------------------------------------------------------
    # Compact view (shorter rows + footer name display + inline rename)
    # ------------------------------------------------------------------

    def _on_toggle_compact_view(self, enabled):
        """Toggle compact view: shorter rows, hide the column header, swap txt001 ↔ footer name.

        Mirror of the Maya panel — pure Qt, no ``bpy``. When the table's column-label strip and
        txt001 are hidden, the object name (and its rename affordance) moves into the footer.
        """
        leaving_compact = self._compact_view and not enabled
        self._compact_view = bool(enabled)

        tbl = self.ui.tbl000
        # The *table's* horizontal header (column-label strip), NOT the window's top banner.
        if self._is_alive(tbl):
            tbl.horizontalHeader().setVisible(not self._compact_view)
            self._apply_row_height(tbl)

        # Only one of (txt001, footer-name) is visible at a time.
        txt1 = getattr(self.ui, "txt001", None)
        if txt1 is not None:
            txt1_widget = getattr(txt1, "container", None) or txt1
            try:
                txt1_widget.setVisible(not self._compact_view)
            except Exception:
                pass

        if self._footer_compact_btn is not None:
            try:
                self._footer_compact_btn.setVisible(self._compact_view)
            except Exception:
                pass

        # Single-object mode is a compact-footer affordance — drop it when
        # leaving compact so the mode matches what the normal view offers.
        # Routed through _set_single_object so every toggle widget re-syncs
        # (and the table refreshes to the restored mode).
        if leaving_compact and self.controller.single_object_mode:
            self._set_single_object(False)

        if self._footer_controller is not None:
            try:
                self._footer_controller.update()
            except Exception:
                pass

    def _apply_row_height(self, widget):
        """Apply the active row height (compact = 80 % of the natural default, floor 12 px).

        The natural default is snapshotted on first call so toggling always returns to the exact
        original height. ``Fixed`` resize mode is required for rows to shrink below their content's
        preferred height and for ``setDefaultSectionSize`` to govern every row.
        """
        if not self._is_alive(widget):
            return
        vh = widget.verticalHeader()
        QHV = self.sb.QtWidgets.QHeaderView
        vh.setSectionResizeMode(QHV.Fixed)

        if self._base_row_height is None:
            self._base_row_height = max(vh.defaultSectionSize(), 18)

        base = self._base_row_height
        height = max(int(base * 0.8), 12) if self._compact_view else base
        # Qt's style-dependent minimumSectionSize (~20 px) silently clamps setDefaultSectionSize,
        # so lower the floor first.
        vh.setMinimumSectionSize(min(height, vh.minimumSectionSize()))
        vh.setDefaultSectionSize(height)
        for row in range(widget.rowCount()):
            widget.setRowHeight(row, height)
        try:
            widget.actions.update_for_row_height()
        except AttributeError:
            pass

    def _configure_columns(self, widget):
        """Set horizontal column resize modes and widths (mirror of the Maya panel).

        Name always fits its content. With ``Auto-fit Window`` on, Value and Type also
        fit their content (so ``_autofit_window`` can size the window to the exact total);
        otherwise Value stretches and Type is a fixed 80 px. The Lock/Connect action
        columns are left to uitk's ``widget.actions`` (sized to row height).
        """
        if not self._is_alive(widget):
            return
        header = widget.horizontalHeader()
        header.setSectionsMovable(False)
        QHV = self.sb.QtWidgets.QHeaderView

        chk = getattr(self, "_chk_auto_fit", None)
        auto_fit = bool(chk and chk.isChecked())

        header.setSectionResizeMode(self.COL_NAME, QHV.ResizeToContents)
        if auto_fit:
            header.setSectionResizeMode(self.COL_VALUE, QHV.ResizeToContents)
            header.setSectionResizeMode(self.COL_TYPE, QHV.ResizeToContents)
        else:
            header.setSectionResizeMode(self.COL_VALUE, QHV.Stretch)
            header.setSectionResizeMode(self.COL_TYPE, QHV.Interactive)
            widget.setColumnWidth(self.COL_TYPE, 80)

    def _autofit_window(self, widget):
        """Resize the window so the table's content width/height fits exactly.

        Runs only when ``Auto-fit Window`` is checked. Deferred twice: once to let
        ``_refresh_table``'s signal-block unwind, then a second tick so the
        ``ResizeToContents`` columns have applied their final widths before we measure
        them. Pure Qt — mirror of the Maya panel, no ``bpy``.
        """
        chk = getattr(self, "_chk_auto_fit", None)
        if not (chk and chk.isChecked()):
            return

        def _do_fit():
            try:
                if not self._is_alive(widget):
                    return

                win = widget.window()
                if win is None:
                    return

                # Force only ResizeToContents columns to recompute — a blanket
                # resizeColumnsToContents() would override the Fixed-width action
                # columns (sized to row height) and inflate them.
                QHV = self.sb.QtWidgets.QHeaderView
                header = widget.horizontalHeader()
                for col in range(widget.columnCount()):
                    if (
                        not widget.isColumnHidden(col)
                        and header.sectionResizeMode(col) == QHV.ResizeToContents
                    ):
                        widget.resizeColumnToContents(col)

                lay = win.layout()
                if lay is not None:
                    lay.activate()
                cw = win.centralWidget() if hasattr(win, "centralWidget") else None
                if cw is not None and cw.layout() is not None:
                    cw.layout().activate()

                header_len = header.length()
                if header_len <= 0:
                    return

                # Width
                vbar = widget.verticalScrollBar()
                vsb_w = vbar.sizeHint().width() if vbar and vbar.isVisible() else 0
                fr_w = widget.frameWidth() * 2
                chrome_w = max(win.width() - widget.viewport().width(), 0)
                target_w = header_len + fr_w + vsb_w + chrome_w
                target_w = max(target_w, win.minimumWidth())

                # Height — sum of (visible) row heights + horizontal header height
                # (when shown) + horizontal scrollbar (when shown).
                rows_h = 0
                for row in range(widget.rowCount()):
                    if not widget.isRowHidden(row):
                        rows_h += widget.rowHeight(row)
                hhdr = widget.horizontalHeader()
                hhdr_h = hhdr.height() if hhdr.isVisible() else 0
                hbar = widget.horizontalScrollBar()
                hsb_h = hbar.sizeHint().height() if hbar and hbar.isVisible() else 0
                fr_h = widget.frameWidth() * 2
                chrome_h = max(win.height() - widget.viewport().height(), 0)
                target_h = rows_h + hhdr_h + fr_h + hsb_h + chrome_h
                target_h = max(target_h, win.minimumHeight())

                if target_w != win.width() or target_h != win.height():
                    win.resize(target_w, target_h)
            except RuntimeError:
                pass

        QtCore = self.sb.QtCore
        QtCore.QTimer.singleShot(0, lambda: QtCore.QTimer.singleShot(0, _do_fit))

    def _create_footer_controller(self):
        """Wrap the footer in a status controller (resolver shows the target name in compact mode)."""
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return None
        try:
            from uitk.widgets.footer import FooterStatusController

            return FooterStatusController(
                footer=footer,
                resolver=self._resolve_footer_text,
                default_text="",
                truncate_kwargs={"length": 96, "mode": "middle"},
            )
        except Exception:
            return None

    def _resolve_footer_text(self):
        """Compact mode shows the object name (mirrors txt001); otherwise any pending warning."""
        if self._footer_warning:
            return self._footer_warning
        if self._compact_view:
            txt1 = getattr(self.ui, "txt001", None)
            if txt1 is not None:
                try:
                    return txt1.text()
                except Exception:
                    pass
        return ""

    def _setup_compact_footer(self):
        """Wire the footer for compact-mode use (inline rename page + single-object toggle button).

        All Qt-only and defensive — touches the footer's private widgets exactly like the Maya
        panel, so any uitk shape change degrades to "no footer rename" rather than a slot crash.
        """
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return

        # -- Inline rename QLineEdit (an extra page in the footer's stacked widget) --
        try:
            QtWidgets = self.sb.QtWidgets
            Qt = self.sb.QtCore.Qt

            le = QtWidgets.QLineEdit()
            le.setFrame(False)
            le.setStyleSheet("QLineEdit { background: transparent; border: none; }")
            footer._stacked_widget.addWidget(le)
            self._footer_lineedit = le
            self._footer_edit_page = footer._stacked_widget.indexOf(le)
            le.editingFinished.connect(self._on_footer_edit_finished)

            def _footer_le_key_press(event, _orig=le.keyPressEvent):
                if event.key() == Qt.Key_Escape:
                    # Block signals so clearFocus() doesn't fire editingFinished (committing a
                    # rename — the opposite of cancel).
                    le.blockSignals(True)
                    try:
                        footer._stacked_widget.setCurrentIndex(0)
                    except Exception:
                        pass
                    le.clearFocus()
                    le.blockSignals(False)
                    return
                _orig(event)

            le.keyPressEvent = _footer_le_key_press
        except Exception:
            pass

        # -- Double-click on the footer label enters rename mode (compact only) --
        try:
            _orig_lbl_dbl = footer._status_label.mouseDoubleClickEvent

            def _footer_label_dbl_click(event, _orig=_orig_lbl_dbl):
                if self._compact_view and self._current_target is not None:
                    self._enter_footer_edit_mode()
                    return
                _orig(event)

            footer._status_label.mouseDoubleClickEvent = _footer_label_dbl_click
        except Exception:
            pass

        # -- Compact single-object toggle button (visible only in compact mode) --
        # Mirrors the txt001 option_box target action: same shared states,
        # so it renders color-coded (and in the correct mode) from creation;
        # _set_single_object keeps both widgets in sync thereafter.
        try:
            self._footer_compact_btn = footer.add_action_button(
                states=self._target_toggle_states(),
            )
            self._footer_compact_btn.setVisible(False)
        except Exception:
            self._footer_compact_btn = None

    def _enter_footer_edit_mode(self):
        """Switch the footer to its inline QLineEdit for object rename."""
        footer = getattr(self.ui, "footer", None)
        if not footer or self._footer_lineedit is None or self._footer_edit_page < 0:
            return
        name = getattr(self._current_target, "name", "") if self._current_target else ""
        self._footer_lineedit.blockSignals(True)
        self._footer_lineedit.setText(name)
        self._footer_lineedit.blockSignals(False)
        footer._stacked_widget.setCurrentIndex(self._footer_edit_page)
        self._footer_lineedit.selectAll()
        self._footer_lineedit.setFocus()

    def _on_footer_edit_finished(self):
        """Commit (or discard) a rename initiated via the footer QLineEdit."""
        footer = getattr(self.ui, "footer", None)
        if footer:
            try:
                footer._stacked_widget.setCurrentIndex(0)
            except Exception:
                pass

        if self._current_target is None or self._footer_lineedit is None:
            return
        new_name = self._footer_lineedit.text().strip()
        if not new_name or new_name == self._current_target.name:
            if self._footer_controller is not None:
                self._footer_controller.update()
            return
        self.controller.rename_node(self._current_target, new_name)
        self._refresh_table(self.ui.tbl000)

    def show_create_menu(self, *args):
        """Show the *Create Attribute* popup (a custom-property form)."""
        menu = self.sb.registered_widgets.Menu(
            parent=self.ui, position="cursor", add_defaults_button=False, fixed_item_height=20
        )
        menu.setTitle("Create Attribute")
        # Chrome is deferred to first show; build it now so the header exists
        # for the pin->hide swap below (this popup shows immediately).
        menu.ensure_chrome()
        if menu.header:
            menu.header.config_buttons("hide")

        menu.add("QLabel", setText="Name:", row=0, col=0)
        le_name = menu.add(
            "QLineEdit", setPlaceholderText="my_attribute", setObjectName="le_attr_name",
            row=0, col=1,
        )
        menu.add("QLabel", setText="Type:", row=1, col=0)
        cmb_type = menu.add(
            "QComboBox", setObjectName="cmb_attr_type",
            # 'vector' = Maya's double3 (a 3-float XYZ array custom prop); 'enum' is not offered
            # (no arbitrary-object Blender analogue — see parity_map channels_slots:cmb_attr_type).
            addItems=["float", "int", "bool", "string", "vector"], row=1, col=1,
        )
        _ranged = ("float", "int", "vector")  # types that carry a default + min/max

        sep_range = menu.add("Separator", setTitle="Range", row=2)
        lbl_default = menu.add("QLabel", setText="Default:", row=3, col=0)
        spn_default = menu.add(
            "QDoubleSpinBox", setObjectName="spn_default", setMinimum=-1e9, setMaximum=1e9,
            row=3, col=1,
        )
        lbl_min = menu.add("QLabel", setText="Min:", row=4, col=0)
        spn_min = menu.add(
            "QDoubleSpinBox", setObjectName="spn_min", setMinimum=-1e9, setMaximum=1e9, row=4, col=1,
        )
        lbl_max = menu.add("QLabel", setText="Max:", row=5, col=0)
        spn_max = menu.add(
            "QDoubleSpinBox", setObjectName="spn_max", setMinimum=-1e9, setMaximum=1e9,
            setValue=1.0, row=5, col=1,
        )
        btn = menu.add("QPushButton", setText="Create", setMinimumHeight=28, setMaximumHeight=28, row=6)

        _numeric = [sep_range, lbl_default, spn_default, lbl_min, spn_min, lbl_max, spn_max]

        def _on_type_changed(text):
            is_ranged = text in _ranged
            for w in _numeric:
                w.setVisible(is_ranged)

        cmb_type.currentTextChanged.connect(_on_type_changed)
        _on_type_changed(cmb_type.currentText())

        def _on_create():
            name = le_name.text().strip().replace(" ", "_")
            if not name:
                self.sb.message_box("Warning: Attribute name cannot be empty.")
                return
            objects = self.controller.get_selected_nodes()
            if not objects:
                self.sb.message_box("Warning: Nothing selected.")
                return
            attr_type = cmb_type.currentText()
            ranged = attr_type in _ranged
            self.controller.create_attribute(
                objects,
                name,
                attr_type,
                min_val=spn_min.value() if ranged else None,
                max_val=spn_max.value() if ranged else None,
                default_val=spn_default.value(),
            )
            menu.hide()
            self._refresh_table(self.ui.tbl000)

        btn.clicked.connect(_on_create)
        menu.show()

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def tbl000_init(self, widget):
        """One-time table setup (action columns + context menu + signals), then a guarded refresh."""
        if not widget.is_initialized:
            widget.refresh_on_show = True
            self._setup_action_columns(widget)
            self._setup_context_menu(widget)
            self._wire_context_menu_state(widget)
            self._setup_value_input(widget)
            widget.cellChanged.connect(self._handle_cell_edit)
            self._subscribe_scene_changes()

        widget.setColumnHidden(self.COL_TYPE, not self._chk_show_type.isChecked()
                               if getattr(self, "_chk_show_type", None) else True)
        self._refresh_table(widget)

    def _subscribe_scene_changes(self):
        """Refresh on selection / scene change (guarded — no-ops without a running Blender)."""
        try:
            from blendertk.core_utils.script_job_manager import ScriptJobManager

            mgr = ScriptJobManager.instance()
            mgr.subscribe("SelectionChanged", self._on_scene_change, owner=self, ephemeral=True)
            mgr.subscribe("SceneOpened", self._on_scene_change, owner=self)
            mgr.connect_cleanup(self.ui, owner=self)
        except Exception:
            pass

    def _on_scene_change(self, *args):
        self._refresh_table(self.ui.tbl000)

    def _setup_action_columns(self, widget):
        """Register Lock and Key/Connect as icon-toggle action columns."""
        clr = self.ACTION_COLOR_MAP
        widget.actions.add(
            self.COL_LOCK,
            states={
                "locked": {
                    "icon": "lock", "color": clr["locked"],
                    "tooltip": "Locked — click to unlock", "action": self._on_icon_cell_clicked,
                },
                "unlocked": {
                    "icon": "unlock", "color": clr["off"],
                    "tooltip": "Unlocked — click to lock", "action": self._on_icon_cell_clicked,
                },
            },
        )
        conn_states = {
            "none": {
                "icon": "disconnect", "color": clr["off"],
                "tooltip": "Not animated — click to key at the current frame.",
                "action": self._on_icon_cell_clicked,
            },
            "keyframe": {
                "icon": "connect", "color": clr["keyframe"],
                "tooltip": "Animated — click to key at the current frame.\nCtrl+click: break animation.",
                "action": self._on_icon_cell_clicked,
            },
            "keyframe_active": {
                "icon": "connect", "color": clr["keyframe_active"],
                "tooltip": "Key on current frame — click to remove it.\nCtrl+click: break animation.",
                "action": self._on_icon_cell_clicked,
            },
            "driven_key": {
                "icon": "connect", "color": clr["driven_key"],
                "tooltip": "Driven — Ctrl+click to remove the driver.",
                "action": self._on_icon_cell_clicked,
            },
            "constraint": {
                "icon": "connect", "color": clr["constraint"],
                "tooltip": "Constrained object — click to key at the current frame.\n"
                "(Manage constraints in the Properties editor.)",
                "action": self._on_icon_cell_clicked,
            },
            "muted": {
                "icon": "connect", "color": clr["muted"],
                "tooltip": "Muted F-curve / driver — click to key at the current frame.\n"
                "Right-click ▸ Unmute to re-enable.",
                "action": self._on_icon_cell_clicked,
            },
        }
        widget.actions.add(self.COL_CONN, states=conn_states)

    def _setup_context_menu(self, widget):
        """Build the table's right-click context menu and bind handlers.

        objectNames and handler method names mirror mayatk's ``_setup_context_menu`` verbatim
        for every item that has a Blender equivalent, so the two files diff side-by-side. Items
        with no Blender analogue are omitted, each flagged with a TODO(blender-parity) below —
        mayatk's own "Key" action lives only on the Connect-column icon click
        (``_on_icon_cell_clicked``), not the context menu, so there is no ``ctx_key`` item here
        either.
        """
        menu = widget.menu
        # Section order mirrors the Maya panel (Edit · Values · Channel Box→Animation ·
        # Transform · Manage). "Channel Box" is renamed "Animation" — Blender has no channel
        # box, and every surviving item in that section is animation-related.
        # fmt: off
        _items = [
            ("Edit",          None),
            ("Lock",          "ctx_lock",          "Lock the selected transform channel(s)."),
            ("Unlock",        "ctx_unlock",        "Unlock the selected transform channel(s)."),
            ("Reset to Default", "ctx_reset_default", "Reset to default (loc/rot 0, scale 1; custom-prop default)."),
            # TODO(blender-parity): mayatk's "Toggle Keyable" has no Blender equivalent —
            # every custom property is keyable; there is no per-channel keyable flag.
            ("Values",        None),
            ("Copy Values",   "ctx_copy_values",   "Copy selected channel values."),
            ("Paste Values",  "ctx_paste_values",  "Paste channel values onto the selection."),
            ("Animation",     None),
            ("Breakdown",     "ctx_breakdown",     "Insert a breakdown keyframe at the current frame."),
            ("Mute",          "ctx_mute",          "Mute the F-curve / driver on the selected channel(s)."),
            ("Unmute",        "ctx_unmute",        "Unmute the F-curve / driver on the selected channel(s)."),
            # TODO(blender-parity): mayatk's "Hide Selected" / "Show Selected" / "Lock and
            # Hide" have no Blender equivalent — there is no channel-box visibility flag;
            # every channel shown here is already visible by definition.
            ("Select Connection", "ctx_select_connection", "Select the object driving this channel (driver variable / constraint target)."),
            ("Break Connection", "ctx_break_connection", "Remove the F-curve / driver on the selected channel(s)."),
            ("Transform",     None),
            ("Freeze Transforms", "ctx_freeze_transforms", "Apply (freeze) transforms on the selected object(s)."),
            ("Unfreeze Transforms", "ctx_unfreeze_transforms", "Restore the pre-freeze transforms (requires a prior freeze)."),
            ("Manage",        None),
            ("Delete Attribute", "ctx_delete",     "Delete the selected custom propert(ies)."),
        ]
        # fmt: on
        handler_map = {
            "ctx_lock": self._ctx_lock,
            "ctx_unlock": self._ctx_unlock,
            "ctx_reset_default": self._ctx_reset_default,
            "ctx_copy_values": self._ctx_copy_values,
            "ctx_paste_values": self._ctx_paste_values,
            "ctx_breakdown": self._ctx_breakdown,
            "ctx_mute": self._ctx_mute,
            "ctx_unmute": self._ctx_unmute,
            "ctx_select_connection": self._ctx_select_connection,
            "ctx_break_connection": self._ctx_break_connection,
            "ctx_freeze_transforms": self._ctx_freeze_transforms,
            "ctx_unfreeze_transforms": self._ctx_unfreeze_transforms,
            "ctx_delete": self._ctx_delete,
        }
        # Node-level actions operate on the selection even with no row highlighted.
        _node_level = {"ctx_freeze_transforms", "ctx_unfreeze_transforms", "ctx_paste_values"}
        for label, obj_name, *rest in _items:
            if obj_name is None:
                menu.add("Separator", setTitle=label)
                continue
            menu.add("QPushButton", setText=label, setObjectName=obj_name,
                     setToolTip=rest[0] if rest else "")
            handler = handler_map.get(obj_name)
            if handler:
                widget.register_menu_action(
                    obj_name,
                    lambda sel, fn=handler: fn(sel),
                    columns=self._ROW_SELECTION_COLUMNS,
                    allow_empty=obj_name in _node_level,
                )

    def _wire_context_menu_state(self, widget):
        """Run our enable/disable pass before the table paints its context menu.

        Qt fires ``customContextMenuRequested`` slots in connection order, so we clear all
        bindings and re-add ours first, then the table's own popup (mirror of the Maya panel).
        """
        try:
            widget.customContextMenuRequested.disconnect()
        except (RuntimeError, TypeError):
            pass
        widget.customContextMenuRequested.connect(self._update_context_menu_state)
        show = getattr(widget, "_show_context_menu", None)
        if show is not None:
            widget.customContextMenuRequested.connect(show)

    def _update_context_menu_state(self, _pos=None):
        """Gate *Unfreeze Transforms* — enabled only when the selection has stored freeze data."""
        tbl = self.ui.tbl000
        if not self._is_alive(tbl):
            return
        menu = getattr(tbl, "menu", None)
        if menu is None:
            return
        btn = getattr(menu, "ctx_unfreeze_transforms", None)
        if btn is None:
            return
        try:
            objects = self.controller.get_selected_nodes()
        except Exception:
            objects = []
        has_stored = self.controller.has_unfreeze_info(objects)
        btn.setEnabled(has_stored)
        btn.setToolTip(
            "Restore the pre-freeze transforms on the selected object(s)."
            if has_stored
            else "Unfreeze unavailable — no stored freeze data. Use Freeze Transforms first."
        )

    # ------------------------------------------------------------------
    # Table refresh
    # ------------------------------------------------------------------

    def _refresh_table(self, widget):
        """Rebuild the table from the current selection + filter (defensive; no-bpy → empty)."""
        if not self._is_alive(widget):
            return

        # Apply the compact-view default/restored state (pull model — see
        # :meth:`_sync_compact_default`) before the rebuild, so row height
        # and the header/footer swap are correct even on the first show,
        # when header state-restore may run after this.
        self._sync_compact_default()

        try:
            objects = self.controller.get_selected_nodes()
        except Exception:
            objects = []

        widget.blockSignals(True)
        try:
            widget.clear()
            self._update_target_display(objects)

            filter_key = self._filter_key()
            rows, states = self.controller.build_table_data(objects, filter_key, self._filter_invert)
            # Keep descriptors aligned with rows for click/edit dispatch.
            self._row_descriptors = (
                self.controller.collect_channels(objects, filter_key, self._filter_invert)
                if objects else []
            )

            # Name filter (wildcard) — reuse pythontk's filter, like the Maya panel.
            text = self.ui.txt000.text().strip() if getattr(self.ui, "txt000", None) else ""
            if self._filter_enabled and text and self._row_descriptors:
                import pythontk as ptk

                keep = set(ptk.IterUtils.filter_list(
                    [d["name"] for d in self._row_descriptors], inc=text, ignore_case=True
                ))
                paired = [
                    (r, s, d) for r, s, d in zip(rows, states, self._row_descriptors)
                    if d["name"] in keep
                ]
                if paired:
                    rows, states, self._row_descriptors = (list(x) for x in zip(*paired))
                else:
                    rows, states, self._row_descriptors = [], [], []

            if not rows:
                rows, states = [["", "", "", "", "No channels"]], [(False, "none")]

            widget.add(rows, headers=["Name", "", "", "Value", "Type"])
            self._configure_columns(widget)
            for row_idx, (is_locked, conn_type) in enumerate(states):
                widget.actions.set(row_idx, self.COL_LOCK, "locked" if is_locked else "unlocked")
                widget.actions.set(row_idx, self.COL_CONN, conn_type)
            self._set_name_editability(widget)
        except Exception:
            import logging

            logging.getLogger(__name__).debug("channels refresh failed", exc_info=True)
        finally:
            widget.blockSignals(False)
            chk = getattr(self, "_chk_show_type", None)
            if chk is not None and self._is_alive(widget):
                widget.setColumnHidden(self.COL_TYPE, not chk.isChecked())
            if self._is_alive(widget):
                self._apply_row_height(widget)
            if self._footer_controller is not None:
                try:
                    self._footer_controller.update()
                except Exception:
                    pass
            # Match the Maya panel: only auto-fit when something is selected, so
            # clearing the selection doesn't collapse the window to the placeholder
            # row (mayatk returns before _autofit_window on the no-selection branch).
            if objects and self._is_alive(widget):
                self._autofit_window(widget)

    def _set_name_editability(self, widget):
        """Make custom-property name cells editable (for rename); transform names stay read-only."""
        Qt = self.sb.QtCore.Qt
        for row_idx, descriptor in enumerate(self._row_descriptors):
            item = widget.item(row_idx, self.COL_NAME)
            if item is None:
                continue
            if descriptor["kind"] == "custom":
                item.setFlags(item.flags() | Qt.ItemIsEditable)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _descriptor_at(self, row):
        if 0 <= row < len(self._row_descriptors):
            return self._row_descriptors[row]
        return None

    def _on_icon_cell_clicked(self, row, col):
        """Lock toggle (lock col) or key set/remove + Ctrl-break (key col)."""
        descriptor = self._descriptor_at(row)
        objects = self.controller.get_selected_nodes()
        if descriptor is None or not objects:
            return

        if col == self.COL_LOCK:
            self.controller.toggle_lock(objects, descriptor)
            self._refresh_table(self.ui.tbl000)
            return
        if col != self.COL_CONN:
            return

        Qt = self.sb.QtCore.Qt
        modifiers = self.sb.QtWidgets.QApplication.keyboardModifiers()
        if modifiers & Qt.ControlModifier:
            self.controller.break_connections(objects, descriptor)
        else:
            self.controller.toggle_key_at_current_time(objects, descriptor)
        self._refresh_table(self.ui.tbl000)

    def _handle_cell_edit(self, row, col):
        """Value edit (Value col) or custom-property rename (Name col)."""
        descriptor = self._descriptor_at(row)
        objects = self.controller.get_selected_nodes()
        if descriptor is None or not objects:
            return
        item = self.ui.tbl000.item(row, col)
        if item is None:
            return
        text = item.text().strip()

        if col == self.COL_VALUE:
            self.controller.set_channel_value(objects, descriptor, text)
            self._refresh_table(self.ui.tbl000)
        elif col == self.COL_NAME and descriptor["kind"] == "custom" and text != descriptor["name"]:
            self.controller.rename_attribute(objects, descriptor["name"], text)
            self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Value-cell scrub editing (MMB-drag + mouse-wheel) — uitk shared infra
    # ------------------------------------------------------------------

    # Channel descriptor types that accept numeric scrub / wheel input.
    _SCRUBBABLE_TYPES = frozenset({"float", "int"})

    # MMB-drag sensitivity. Ctrl=fine (×0.1), Shift=coarse (×10), read live so the user can
    # switch mid-drag (mirror of the Maya panel).
    _SCRUB_FLOAT_STEP = 0.01
    _SCRUB_INT_PIXELS_PER_UNIT = 4

    # Wheel-scroll per-notch step ladder, mirroring uitk's WheelStepMixin: Ctrl scales up,
    # Alt scales down. Float smallest matches Channels._fmt_float's 4-decimal display precision.
    _WHEEL_FLOAT_STEP = 0.1          # default (no modifier)
    _WHEEL_FLOAT_COARSE = 1.0        # Ctrl        (×10)
    _WHEEL_FLOAT_VERY_COARSE = 10.0  # Ctrl+Shift  (×100)
    _WHEEL_FLOAT_FINE = 0.01         # Alt         (÷10)
    _WHEEL_FLOAT_SMALLEST = 0.0001   # Ctrl+Alt    (smallest representable)
    _WHEEL_INT_COARSE = 10           # Ctrl on int (×10)
    _WHEEL_INT_VERY_COARSE = 100     # Ctrl+Shift on int (×100)
    _WHEEL_INT_SMALLEST = 1          # Ctrl+Alt on int — smallest int step
    _WHEEL_INT_FINE = 0              # Alt on int — no sub-1 step; notch silently consumed

    def _setup_value_input(self, widget):
        """Opt the Value column into MMB-scrub, wheel-scrub, and single-click edit (uitk infra)."""
        widget.set_scrub_columns([self.COL_VALUE])
        widget.set_wheel_scrub_columns([self.COL_VALUE])
        try:
            widget.set_single_click_edit_columns([self.COL_VALUE])
        except AttributeError:
            pass
        widget.cellScrubStarted.connect(self._on_scrub_started)
        widget.cellScrubMoved.connect(self._on_scrub_moved)
        widget.cellScrubFinished.connect(self._on_scrub_finished)
        widget.cellWheelScrolled.connect(self._on_wheel_scrolled)

    def _scrub_value(self, obj, descriptor):
        """*obj*'s current display value for *descriptor* if it is numeric **and** unlocked, else ``None``.

        Shared guard for MMB-scrub and wheel-scrub — both only adjust numeric, unlocked channels.
        Display-space (degrees for angle channels) so deltas round-trip through
        :meth:`Channels.set_channel_value`, which converts back to radians on write.
        """
        if self.controller.is_locked(obj, descriptor):
            return None
        val = self.controller.get_channel_value(obj, descriptor)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return None
        return float(val)

    def _scrub_targets(self, descriptor):
        """Snapshot ``{obj: start_display_value}`` for numeric, unlocked channels on the selection."""
        starts = {}
        for obj in self.controller.get_selected_nodes():
            val = self._scrub_value(obj, descriptor)
            if val is not None:
                starts[obj] = val
        return starts

    def _on_scrub_started(self, row, col):
        """Capture per-object start values for an MMB scrub-drag on a numeric channel."""
        if col != self.COL_VALUE:
            return
        descriptor = self._descriptor_at(row)
        if descriptor is None or descriptor["type"] not in self._SCRUBBABLE_TYPES:
            return
        starts = self._scrub_targets(descriptor)
        if not starts:
            return
        self._scrub_state = {
            "row": row,
            "descriptor": descriptor,
            "starts": starts,
            "is_int": descriptor["type"] == "int",
        }

    def _on_scrub_moved(self, row, col, dx, dy):
        """Apply ``start + delta`` to every snapshotted object for the active scrub."""
        state = self._scrub_state
        if not state:
            return
        Qt = self.sb.QtCore.Qt
        mods = self.sb.QtWidgets.QApplication.keyboardModifiers()
        scale = 1.0
        if mods & Qt.ControlModifier:
            scale *= 0.1
        if mods & Qt.ShiftModifier:
            scale *= 10.0

        if state["is_int"]:
            delta = int(dx / self._SCRUB_INT_PIXELS_PER_UNIT * scale)
        else:
            delta = dx * self._SCRUB_FLOAT_STEP * scale

        descriptor = state["descriptor"]
        primary_new = None
        for i, (obj, start) in enumerate(state["starts"].items()):
            new_val = start + delta
            if state["is_int"]:
                new_val = int(round(new_val))
            self.controller.set_channel_value([obj], descriptor, str(new_val))
            if i == 0:
                primary_new = new_val
        # Live cell feedback without a full rebuild (which would re-resolve descriptors mid-drag).
        if primary_new is not None:
            self._set_value_cell_text(state["row"], self.controller.format_value(primary_new))

    def _on_scrub_finished(self, row, col):
        """Clear scrub state and re-sync the table (value + connection icons)."""
        if self._scrub_state is not None:
            self._scrub_state = None
            self._refresh_table(self.ui.tbl000)

    def _set_value_cell_text(self, row, text):
        """Set the Value cell text in place with signals blocked (no edit-handler re-entry)."""
        widget = self.ui.tbl000
        if not self._is_alive(widget):
            return
        item = widget.item(row, self.COL_VALUE)
        if item is None:
            return
        widget.blockSignals(True)
        try:
            item.setText(text)
        finally:
            widget.blockSignals(False)

    def _wheel_step(self, mods, is_int):
        """Per-notch step amount for the active modifier state (symmetric ×10 / ÷10 ladder).

        Detection uses ``bool(mods & SPECIFIC)`` per modifier rather than ``(mods & MASK) == MASK``
        — the latter can return False under PySide6 when comparing the live ``KeyboardModifiers``
        flag set to a ``KeyboardModifier`` value (made Ctrl+Shift fall to the default).
        """
        Qt = self.sb.QtCore.Qt
        ctrl = bool(mods & Qt.ControlModifier)
        alt = bool(mods & Qt.AltModifier)
        shift = bool(mods & Qt.ShiftModifier)

        if ctrl and alt:
            return self._WHEEL_INT_SMALLEST if is_int else self._WHEEL_FLOAT_SMALLEST
        if ctrl and shift:
            return self._WHEEL_INT_VERY_COARSE if is_int else self._WHEEL_FLOAT_VERY_COARSE
        if ctrl:
            return self._WHEEL_INT_COARSE if is_int else self._WHEEL_FLOAT_COARSE
        if alt:
            return self._WHEEL_INT_FINE if is_int else self._WHEEL_FLOAT_FINE
        return 1 if is_int else self._WHEEL_FLOAT_STEP

    def _on_wheel_scrolled(self, row, col, steps, mods):
        """Adjust a numeric channel by *steps* notches (signed).

        Two paths mirror the Maya panel: when the cell has an open editor that text is the
        source of truth (so un-committed typing isn't lost); otherwise the channel's current
        value is read, stepped, and written, and the cell text refreshed in place. Locked or
        non-numeric channels are silently skipped.
        """
        if col != self.COL_VALUE or steps == 0:
            return
        descriptor = self._descriptor_at(row)
        if descriptor is None or descriptor["type"] not in self._SCRUBBABLE_TYPES:
            return
        objects = self.controller.get_selected_nodes()
        if not objects:
            return

        is_int = descriptor["type"] == "int"
        step = self._wheel_step(mods, is_int)
        if step == 0:  # Alt on an int — no sub-1 step exists; recognise the gesture, do nothing.
            return
        delta = step * steps

        def apply_delta(cur):
            new = cur + delta
            return int(round(new)) if is_int else new

        QtWidgets = self.sb.QtWidgets
        widget = self.ui.tbl000
        editor = None
        if hasattr(widget, "active_editor"):
            candidate = widget.active_editor()
            cur_idx = widget.currentIndex()
            if (
                isinstance(candidate, QtWidgets.QLineEdit)
                and cur_idx.isValid()
                and cur_idx.row() == row
                and cur_idx.column() == col
            ):
                editor = candidate

        if editor is not None:
            # Edit-mode: the editor text is primary's source of truth (may hold un-committed edits);
            # other objects step their own current value so multi-select divergence is preserved.
            try:
                primary_cur = float(editor.text())
            except (TypeError, ValueError):
                raw = self.controller.get_channel_value(objects[0], descriptor)
                primary_cur = float(raw) if isinstance(raw, (int, float)) else 0.0
            new_primary = apply_delta(primary_cur)
            new_text = self.controller.format_value(new_primary)

            cursor_from_end = len(editor.text()) - editor.cursorPosition()
            editor.blockSignals(True)
            try:
                editor.setText(new_text)
            finally:
                editor.blockSignals(False)
            editor.setCursorPosition(max(0, len(new_text) - cursor_from_end))

            for obj in objects:
                if obj is objects[0]:
                    if not self.controller.is_locked(obj, descriptor):
                        self.controller.set_channel_value([obj], descriptor, str(new_primary))
                    continue
                cur = self._scrub_value(obj, descriptor)
                if cur is not None:
                    self.controller.set_channel_value([obj], descriptor, str(apply_delta(cur)))
            return

        # Display-mode: step each object's current value, then refresh the cell text in place.
        changed = False
        for obj in objects:
            cur = self._scrub_value(obj, descriptor)
            if cur is None:
                continue
            self.controller.set_channel_value([obj], descriptor, str(apply_delta(cur)))
            changed = True
        if changed:
            primary_val = self.controller.get_channel_value(objects[0], descriptor)
            self._set_value_cell_text(row, self.controller.format_value(primary_val))

    # ------------------------------------------------------------------
    # Context-menu handlers (receive selection payloads from register_menu_action)
    # ------------------------------------------------------------------

    def _selected_descriptors(self, selection):
        """Resolve a context-menu selection payload → channel descriptors (by name)."""
        names = [s["name"] for s in (selection or []) if s.get("name")]
        by_name = {d["name"]: d for d in self._row_descriptors}
        return [by_name[n] for n in names if n in by_name]

    def _ctx_lock(self, selection):
        self.controller.set_lock(self.controller.get_selected_nodes(),
                                 self._selected_descriptors(selection), True)
        self._refresh_table(self.ui.tbl000)

    def _ctx_unlock(self, selection):
        self.controller.set_lock(self.controller.get_selected_nodes(),
                                 self._selected_descriptors(selection), False)
        self._refresh_table(self.ui.tbl000)

    def _ctx_reset_default(self, selection):
        self.controller.reset_to_default(self.controller.get_selected_nodes(),
                                         self._selected_descriptors(selection))
        self._refresh_table(self.ui.tbl000)

    def _ctx_break_connection(self, selection):
        objects = self.controller.get_selected_nodes()
        for d in self._selected_descriptors(selection):
            self.controller.break_connections(objects, d)
        self._refresh_table(self.ui.tbl000)

    def _ctx_copy_values(self, selection):
        self.controller.copy_values(self.controller.get_selected_nodes(),
                                    self._selected_descriptors(selection))

    def _ctx_paste_values(self, selection):
        self.controller.paste_values(self.controller.get_selected_nodes())
        self._refresh_table(self.ui.tbl000)

    def _ctx_freeze_transforms(self, selection):
        objects = self.controller.get_selected_nodes()
        if not objects:
            self.sb.message_box("Warning: No object(s) selected.")
            return
        descriptors = self._selected_descriptors(selection)
        if not self.controller.freeze_transforms(objects, descriptors or None):
            # Reached only when the selection names custom properties but no transform
            # channel — the UI normally leaves this reachable only via a valid target.
            self.sb.message_box(
                "Warning: Selection is not a valid freeze target — select a "
                "location / rotation / scale channel, or nothing."
            )
            return
        self._refresh_table(self.ui.tbl000)

    def _ctx_unfreeze_transforms(self, selection):
        objects = self.controller.get_selected_nodes()
        if not objects:
            return
        if not self.controller.unfreeze_transforms(objects):
            self.sb.message_box("Warning: No stored freeze data on the selected object(s).")
        self._refresh_table(self.ui.tbl000)

    def _ctx_breakdown(self, selection):
        self.controller.set_breakdown_key(self.controller.get_selected_nodes(),
                                          self._selected_descriptors(selection))
        self._refresh_table(self.ui.tbl000)

    def _ctx_mute(self, selection):
        self.controller.set_mute(self.controller.get_selected_nodes(),
                                 self._selected_descriptors(selection), True)
        self._refresh_table(self.ui.tbl000)

    def _ctx_unmute(self, selection):
        self.controller.set_mute(self.controller.get_selected_nodes(),
                                 self._selected_descriptors(selection), False)
        self._refresh_table(self.ui.tbl000)

    def _ctx_select_connection(self, selection):
        descriptors = self._selected_descriptors(selection)
        if not descriptors:
            return
        if not self.controller.select_connections(
            self.controller.get_selected_nodes(), descriptors[0]
        ):
            self.sb.message_box(
                f"Warning: No driver / constraint target on '{descriptors[0]['name']}'."
            )

    def _ctx_delete(self, selection):
        self.controller.delete_attributes(self.controller.get_selected_nodes(),
                                          self._selected_descriptors(selection))
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_alive(self, widget):
        """True if the underlying C++ widget still exists."""
        try:
            widget.objectName()
            return True
        except RuntimeError:
            return False


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("channels", reload=True)
    ui.show(pos="screen", app_exec=True)
