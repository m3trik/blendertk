# !/usr/bin/python
# coding=utf-8
"""Slots for the Scene Exporter panel -- Blender port of mayatk's ``SceneExporterSlots``.

Co-located with its engine (``_scene_exporter.SceneExporter``) and panel (``scene_exporter.ui``,
copied verbatim from mayatk). Discovered by ``BlenderUiHandler``
(``marking_menu.show("scene_exporter")``).

The FBX-preset combo (``cmb000``) is a real, populated combo backed by
``SceneExporter``'s ``pythontk.PresetStore``-based preset engine (named JSON dicts of
``export_scene.fbx`` kwargs -- see ``_scene_exporter.py``'s module docstring for the full
design rationale, including why Blender's native operator-preset system was considered and
rejected). It is a row of the Settings combo (``cmb008``); its own option box carries
mayatk's b007/b008 1:1:

* ``b007`` "Open FBX Preset Directory" -- ``os.startfile`` the writable preset directory. Adding
  and deleting presets happens there: a preset is a plain JSON file, so the file browser
  already copies, renames, and deletes them better than a pair of one-shot buttons could
  (the programmatic equivalents, :meth:`SceneExporter.save_fbx_preset` /
  :meth:`~SceneExporter.delete_fbx_preset`, remain on the engine).
* ``b008`` "Edit FBX Preset" -- ``os.startfile`` the selected preset's JSON file so the user can
  hand-edit + re-save it (Blender has no per-field editor for an arbitrary FBX-kwargs dict the
  way Maya's native FBX exporter dialog does). A built-in preset is shadowed into the user
  tier first ("duplicate to edit") so this never edits the shipped, read-only file in place.

``import bpy`` is deferred into the methods that need it (headless Blender ships no Qt binding
either, so the Qt-only ``fmt`` import is deferred alongside it).
"""

import os
from typing import Dict, Any, Optional

import pythontk as ptk

from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter


class SceneExporterSlots(SceneExporter):
    _log_level_options: Dict[str, Any] = {
        "Log Level: DEBUG": 10,
        "Log Level: INFO": 20,
        "Log Level: WARNING": 30,
        "Log Level: ERROR": 40,
    }

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__(log_level=log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.scene_exporter

        self.ui.txt001.setText("")  # Output Name
        self.ui.txt003.setText("")  # Log Output

        self._wire_dependencies()

        self.ui.b009.setEnabled(True)
        self.ui.b009.setChecked(False)
        self.ui.b009.setStyleSheet("QPushButton:checked {background-color: #FF9999;}")

        self.logger.setLevel(log_level)
        self.logger.hide_logger_name(True)
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)

        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

    def _wire_dependencies(self) -> None:
        """Grey out a setting while a lower-level choice makes it irrelevant.

        One ``sb.enable_when`` rule per dependency — declared once here, order-
        independent (the rows register later; the rule picks them up), and
        re-applied by the trigger's own change signal, so there is no per-
        trigger slot and no ``_sync_*`` helper to keep in step. A preset load
        applies with signals unblocked (``cmb007_init``), so these follow it too.
        """
        sb, ui = self.sb, self.ui
        # Texture File Type is the container dial for every texture the export
        # ships, so it is NOT gated on Optimize Textures: a GLB deliverable is
        # re-encoded to it whether or not the scene pass runs. The pass's size
        # ceiling needs no rule at all any more — it rides the Optimize
        # Textures combo itself ("Optimize + Max …"), so a ceiling with
        # nothing to apply it is unrepresentable rather than greyed out.
        # Texture Output only matters once a texture-processing task runs —
        # Optimize Textures, or the conversion a Texture Template arms.
        sb.enable_when(
            ui,
            "texture_write_back",
            ["texture_optimize", "cmb005"],
            lambda optimize, template: bool(optimize) or bool(template),
        )
        # Exclude HDR: the visible-geometry scope never contains a skydome
        # (surface shapes only); All / Selected can.
        sb.enable_when(
            ui, "exclude_hdr", "export_visible_objects", lambda scope: scope != "visible"
        )

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from blendertk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    @property
    def workspace(self):
        from blendertk.core_utils._core_utils import CoreUtils

        workspace_path = CoreUtils.get_env_info("workspace")
        if not workspace_path:
            self.logger.error("No saved .blend directory found.")
        return workspace_path

    def header_init(self, widget):
        """Initialize the header widget (log options; the export preset lives
        in the panel as ``cmb007``)."""
        widget.menu.add(
            "QCheckBox",
            setText="Create Log File",
            setObjectName="b011",
            setChecked=False,
            setToolTip="Export a log file along with the fbx.",
        )
        widget.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb003",
            add=self._log_level_options,
            setCurrentIndex=1,
            setToolTip="Set the log level.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Scene Exporter",
                body="Batch-export scene objects to FBX (or GLB) using configurable "
                "task pipelines.",
                steps=[
                    "Pick an export <b>Preset</b> — the whole panel's "
                    "configuration under a name (Save / Rename / Delete from "
                    "its toolbar).",
                    "Adjust <b>Settings</b> (FBX preset, format, units, scope, "
                    "texture template), <b>Tasks</b> (scene prep) and "
                    "<b>Checks</b> (validation gates), and set the output path.",
                    "Press <b>Export</b> to run.",
                ],
                sections=[
                    (
                        "Header menu",
                        [
                            "<b>Create Log File</b> — write a sidecar log next to "
                            "each FBX.",
                            "<b>Log Level</b> — DEBUG / INFO / WARNING / ERROR "
                            "output verbosity.",
                        ],
                    ),
                ],
            )
        )

    @property
    def presets(self) -> Dict[str, Optional[str]]:
        """FBX export-option presets available for ``cmb000``, keyed by name (``"None"``
        clears any loaded preset -- exports fall back to the built-in defaults)."""
        return {"None": None, **{name: name for name in self.list_fbx_presets()}}

    def _refresh_presets(self) -> None:
        """Re-scan the FBX preset directory (the ``cmb000`` refresh button).

        Mirror of mayatk's ``_refresh_presets``, minus its cache-invalidation step:
        ``PresetStore.list()`` globs both tiers on every call, so re-running
        :meth:`cmb000_init` -- which re-reads :attr:`presets` and restores the current
        selection if it survived -- is the whole refresh.
        """
        self.ui.cmb000.init_slot()
        self.logger.debug("Refreshed the FBX preset list.")

    def cmb000_init(self, widget) -> None:
        """Init FBX export-option preset combo (mirror of mayatk's ``cmb000_init`` -- see
        ``_scene_exporter.py``'s module docstring for the PresetStore-backed design).

        A Settings row (``cmb008``), created by :meth:`cmb008_init` and registered by
        objectName; Open / Edit and a refresh button live in this row's own option box
        (mirror of mayatk's; both preset directories are fixed, so neither side carries
        directory-switch entries)."""
        if not widget.is_initialized:
            widget.restore_state = True  # Enable state restore
            widget.refresh_on_show = True  # Call this method on show
            # Persist the selection by preset NAME, not combo index: the item list is rebuilt
            # from the preset store each show (mirrors mayatk's cmb000_init).
            widget.restore_by = "text"

            widget.option_box.menu.setTitle("FBX Preset:")
            widget.option_box.menu.add_defaults_button = False
            widget.option_box.menu.add(
                "QPushButton",
                setText="Open FBX Preset Directory",
                setObjectName="b007",
                setToolTip=(
                    "Open the writable FBX preset directory in the file browser. "
                    "A preset is a plain JSON file, so adding, renaming, and "
                    "deleting happens there."
                ),
            )
            widget.option_box.menu.add(
                "QPushButton",
                setText="Edit FBX Preset",
                setObjectName="b008",
                setToolTip=(
                    "Open the selected preset's JSON file for hand-editing. "
                    "A built-in preset is copied to the user tier first."
                ),
            )

            # Sorts ahead of the option-box menu button (DEFAULT_OPTION_ORDER:
            # "action" before "menu"). ``refresh_on_show`` already re-scans when
            # the panel opens; this is for a preset added, renamed or deleted
            # while it is sitting open -- a JSON file dropped into (or removed
            # from) the directory b007 opens is the common case, and the panel
            # has no way to hear about it. An in-place content edit needs no
            # refresh: the list is keyed on names, which such an edit leaves be.
            widget.option_box.add_action(
                callback=self._refresh_presets,
                icon="refresh",
                tooltip="Re-scan the FBX preset directory for presets added, renamed or removed since the panel opened.",
            )

        # Store current selection before refresh
        current_data = widget.currentData() if widget.count() > 0 else None
        current_text = widget.currentText() if widget.count() > 0 else ""

        presets = self.presets
        widget.add(presets, clear=True)

        # Restore previous selection if it still exists
        if current_data and current_data in presets.values():
            for text, name in presets.items():
                if name == current_data:
                    widget.setCurrentText(text)
                    self.logger.debug(f"Restored preset selection: {text}")
                    break
        elif current_text and current_text in presets:
            widget.setCurrentText(current_text)
            self.logger.debug(f"Restored preset selection by text: {current_text}")

    def txt000_init(self, widget) -> None:
        """Init Output Directory"""
        widget.option_box.menu.setTitle("Output Directory:")
        widget.option_box.menu.add_defaults_button = False
        widget.option_box.menu.add(
            "QPushButton",
            setToolTip="Set the output directory.",
            setText="Set Output Directory",
            setObjectName="b010",
        )
        widget.option_box.menu.add(
            "QPushButton",
            setToolTip="Open the output directory.",
            setText="Open Output Directory",
            setObjectName="b006",
        )

        from uitk.widgets.optionBox.options.recent_values import RecentValuesOption

        self._recent_dirs_option = RecentValuesOption(
            wrapped_widget=widget,
            settings_key="scene_exporter_output_dirs",
            max_recent=10,
            display_format=lambda p: (
                "…/" + "/".join(ptk.format_path(p).split("/")[-3:])
                if len(ptk.format_path(p).split("/")) > 3
                else str(p)
            ),
            text_align="left",
        )
        widget.option_box.add_option(self._recent_dirs_option)

    def txt001_init(self, widget) -> None:
        """Init Output Name"""
        widget.option_box.menu.setTitle("Output Name:")
        widget.option_box.menu.add_defaults_button = False
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QPushButton",
            setToolTip=(
                "Name the export after an existing file.\n\n"
                "Opens a file browser at the current output directory. The chosen "
                "file's name (without extension) becomes the output name, and the "
                "output directory follows the file if it lives elsewhere."
            ),
            setText="Browse for File",
            setObjectName="b012",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setToolTip="Add a timestamp suffix to the output filename.",
            setText="Timestamp",
            setObjectName="chk004",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setToolTip=(
                "Regex pattern for formatting the output name.\n\n"
                "Format:  PATTERN->REPLACEMENT\n"
                "Examples:\n"
                "  _bar.*->       Remove '_bar' and everything after\n"
                "  (foo|bar)->baz    Replace 'foo' or 'bar' with 'baz'\n"
                "Use standard Python regular expressions. If no '->', everything matching PATTERN is removed."
            ),
            setPlaceholderText="RegEx",
            setObjectName="txt002",
        )

        from uitk.widgets.optionBox.options.recent_values import RecentValuesOption

        self._recent_names_option = RecentValuesOption(
            wrapped_widget=widget,
            settings_key="scene_exporter_output_filenames",
            max_recent=10,
            display_format="basename",
            text_align="left",
        )
        widget.option_box.add_option(self._recent_names_option)

    # Rows of the Settings combo (cmb008), by group. Names resolve to a UI-only
    # widget spec (``_SETTINGS_WIDGETS``) or to a ``task_definitions`` entry
    # tagged ``"panel": "settings"`` — a task the engine dispatches (or a flag
    # ``perform_export`` pops) that the USER experiences as a write/scope
    # setting rather than scene prep. Order here is display order; a name a
    # DCC's definitions lack (blendertk has no set_workspace) is skipped, so
    # the layout is shared verbatim between the two panels.
    _SETTINGS_LAYOUT = (
        (
            "Output",
            (
                "cmb000",
                "cmb004",
                "set_linear_unit",
                "set_workspace",
                "version",
            ),
        ),
        (
            "Scope",
            (
                "export_visible_objects",
                "ignore_groups",
                "exclude_hdr",
                "export_data_node",
            ),
        ),
        # No Textures section: every texture dial — Texture Output included —
        # lives in the Tasks combo's Textures group, the gate row directly
        # above the three rows it governs (see task_definitions).
    )

    #: Settings rows with no task/check definition behind them. Each keeps the
    #: objectName it had as a main-layout / option-box widget so its ``_init``
    #: slot, ``b000``'s reads and every saved export preset stay valid.
    _SETTINGS_WIDGETS = {
        "cmb000": {
            "widget_type": "ComboBox",
            "set_row_label": "FBX Preset",
            "setToolTip": (
                "FBX export-option preset applied to the write (units, axis, "
                "geometry, animation).\n"
                "It governs a GLB output too: the GLB is converted from this "
                "FBX write, so the preset's geometry/animation choices carry "
                "through.\n"
                "'default' is Blender's own File > Export > FBX defaults; "
                "'game_asset' is the game-ready baseline (smoothing groups, "
                "tangents, textures shipped with the file) and is what 'None' "
                "writes with.\n"
                "The option box beside this row opens the preset folder "
                "or the current preset's JSON."
            ),
        },
        "cmb004": {
            "widget_type": "ComboBox",
            "set_row_label": "Format",
            "setToolTip": "Output file format: FBX, GLB, or both.",
        },
    }

    #: Definition keys that describe the row, not the widget — stripped before
    #: the remainder is applied as widget attributes.
    _DEFINITION_META_KEYS = (
        "widget_type",
        "panel",
        "group",
        "object_name",
        "value_method",
    )

    def _make_definition_widget(self, name, params, object_name=None):
        """Instantiate the widget a task/check/settings definition describes."""
        params = dict(params)
        widget_type = params.get("widget_type", "QCheckBox")
        object_name = object_name or params.get(
            "object_name", self.sb.convert_to_legal_name(name)
        )
        widget_class = getattr(self.sb.QtWidgets, widget_type, None)
        if widget_class is None:
            widget_class = getattr(self.sb.registered_widgets, widget_type, None)
            if widget_class is None:
                raise ValueError(f"Unknown widget type: {widget_type}")
        for key in self._DEFINITION_META_KEYS:
            params.pop(key, None)
        widget = widget_class()
        self.ui.set_attributes(widget, setObjectName=object_name, **params)
        return widget

    def _definition_rows(self, definitions, panel=None):
        """``[(widget, label)]`` for a WidgetComboBox: one row per definition
        whose ``panel`` tag matches, with a titled Separator wherever the
        ``group`` tag changes — the group sequence IS the section order, so no
        hand-placed separator entries."""
        rows = []
        current_group = None
        for name, params in definitions.items():
            if params.get("panel") != panel:
                continue
            group = params.get("group")
            if group and group != current_group:
                rows.append(
                    (self.sb.registered_widgets.Separator(title=group), group)
                )
                current_group = group
            rows.append((self._make_definition_widget(name, params), name))
        return rows

    def cmb001_init(self, widget) -> None:
        """Tasks — scene-prep steps the engine dispatches (``TASK_ORDER``),
        grouped by their ``group`` tag; entries tagged ``panel: settings``
        render in ``cmb008`` instead."""
        widget.add(
            self._definition_rows(self.task_manager.task_definitions),
            header="Tasks",
            clear=True,
        )

    def cmb002_init(self, widget) -> None:
        """Validation Checks — the gates that abort the write, grouped by tag."""
        widget.add(
            self._definition_rows(self.task_manager.check_definitions),
            header="Validation Checks",
            clear=True,
        )

    def cmb007_init(self, widget) -> None:
        """Export Preset — the whole panel's run configuration under a name.

        The window's ``PresetManager`` wired onto this main-layout combo (the
        canonical Refresh / Save / ⋯ toolbar comes from ``wire_combo``), the
        same pattern curtain's ``cmb000`` uses (mirror of mayatk's ``cmb007_init``).
        ``scope="window"`` captures
        every registered value-bearing widget — the Settings / Tasks / Checks
        rows (they register by objectName like any main-layout widget), the
        header menu's log options — minus the machine/scene-specific fields:
        output dir (txt000), output filename (txt001), log output (txt003).
        The preset combo itself is always excluded internally.
        """
        mgr = self.ui.presets
        # Adopt this panel's logger (instance-scoped) so the manager's
        # user-facing lines -- notably the schema-drift "preset doesn't cover
        # N new panel settings" warning -- reach the txt003 log sink instead
        # of only the console. Must precede wire_combo: the active-preset
        # restore it triggers is exactly the load that warns.
        mgr.use_logger(self.logger)
        mgr.setup(preset_dir="blendertk/scene_exporter")
        mgr.scope = "window"
        mgr.exclude("txt000", "txt001", "txt003")
        # No on_loaded: a preset then applies with signals UNBLOCKED, so the
        # enable_when dependencies (see _wire_dependencies) follow the loaded
        # values on their own.
        mgr.wire_combo(widget, placeholder="Preset…")

    def cmb008_init(self, widget) -> None:
        """Settings — what is written and from what (the scene-prep steps are
        Tasks). Rows come from :attr:`_SETTINGS_LAYOUT`; the FBX-preset
        management lives on the ``cmb000`` row's own option box
        (``cmb000_init``)."""
        definitions = self.task_manager.task_definitions
        rows = []
        for group, names in self._SETTINGS_LAYOUT:
            rows.append((self.sb.registered_widgets.Separator(title=group), group))
            for name in names:
                spec = self._SETTINGS_WIDGETS.get(name)
                if spec is not None:
                    rows.append(
                        (self._make_definition_widget(name, spec, object_name=name), name)
                    )
                elif name in definitions:
                    rows.append(
                        (self._make_definition_widget(name, definitions[name]), name)
                    )
        widget.add(rows, header="Settings", clear=True)

    def cmb004_init(self, widget) -> None:
        """Init Output Format — FBX (default), GLB, FBX + GLB, or USD.

        A Settings row (``cmb008``) (mirror of mayatk's ``cmb004_init``). The
        container its embedded textures are written in is the general
        ``texture_file_type`` row (a GLB carries what glTF accepts — see
        ``TaskManager._glb_texture_params``).
        """
        if not widget.is_initialized:
            widget.restore_state = True
        widget.add(
            {"FBX": "fbx", "GLB": "glb", "FBX + GLB": "fbx_glb", "USD": "usd"},
            clear=True,
        )

    def cmb005_init(self, widget) -> None:
        """Init Texture Template (mirror of mayatk's ``cmb005_init``).

        The ``convert_textures`` row of the Tasks combo (``cmb001``, Materials
        group), which is where it acts: it arms a pipeline task rather than
        describing the write.

        The ONE definition the conversion task and the compatibility check both
        key off — ``b000`` folds this combo's value into the tasks payload as
        ``convert_textures`` (task phase) and ``check_material_compatibility``
        (check phase), so there are no separate rows to keep in sync. "As
        Authored" (the default) sends textures exactly as the scene references
        them and arms neither.

        Populated from ``ptk.MapRegistry.get_workflow_presets()`` with each
        preset's description as its item tooltip.
        """
        from qtpy import QtCore

        if not widget.is_initialized:
            widget.restore_state = True
        # Names + tooltips from the OutputTemplates SSoT (shared with
        # game_shader / mat_updater / the converter / compositor).
        choices = ptk.OutputTemplates.profile_choices()
        widget.add(
            {"As Authored": None, **{name: name for name, _ in choices}},
            clear=True,
        )
        tooltips = dict(choices)
        for index in range(widget.count()):
            description = tooltips.get(widget.itemData(index))
            if description:
                widget.setItemData(index, description, QtCore.Qt.ToolTipRole)

    def b000(self) -> None:
        """Export: run the scene export with the configured tasks and settings."""
        self.ui.txt003.clear()
        task_params = {}
        check_params = {}

        for task_name, params in self.task_manager.task_definitions.items():
            widget_type = params.get("widget_type", "QCheckBox")
            object_name = params.get(
                "object_name", self.sb.convert_to_legal_name(task_name)
            )
            value_method = params.get("value_method")

            widget = getattr(self.ui, object_name, None)

            if not value_method:
                value_method = (
                    "isChecked" if widget_type == "QCheckBox" else "currentData"
                )

            if widget and hasattr(widget, value_method):
                value = getattr(widget, value_method)()
                task_params[task_name] = value

        for check_name, params in self.task_manager.check_definitions.items():
            widget_type = params.get("widget_type", "QCheckBox")
            object_name = params.get(
                "object_name", self.sb.convert_to_legal_name(check_name)
            )
            value_method = params.get("value_method")

            widget = getattr(self.ui, object_name, None)

            if not value_method:
                value_method = (
                    "isChecked" if widget_type == "QCheckBox" else "currentData"
                )

            if widget and hasattr(widget, value_method):
                value = getattr(widget, value_method)()
                check_params[check_name] = value

        # Texture template: the ``convert_textures`` Tasks row (``cmb005``),
        # already collected above by the definition loop. Mirror it onto the
        # check half here — the gate has no row of its own. The ONE
        # definition both pipeline hooks reference. Folded BEFORE the override
        # filter so "override checks" keeps the conversion but skips the gate.
        texture_template = task_params.get("convert_textures")
        if texture_template:
            check_params["check_material_compatibility"] = texture_template

        # Optimize Textures (one combo, mirror of mayatk): its value carries
        # the pass switch AND the size ceiling — decomposed here into the two
        # inputs the engine has always taken. The ceiling (an int, or the
        # template-budget sentinel) rides the tasks payload as
        # ``texture_max_size``, which perform_export pops into the per-run
        # mode, so headless callers' explicit key keeps working unchanged.
        # The pass then rides cmb005's template when one is selected — the
        # template's per-map-type output spec drives container/bit depth, its
        # budget stays advisory unless the ceiling half asks for it — else it
        # is the generic per-map-type pass (True). Folded BEFORE the override
        # filter for the same reason as the template: "override checks" keeps
        # the optimization, skips the gate. Where both land (export copies vs
        # the scene's files) is the Texture Output combo, collected above as
        # the ``texture_write_back`` flag perform_export pops.
        optimize_choice = task_params.get("optimize_textures")
        if optimize_choice:
            if optimize_choice is not True:
                task_params["texture_max_size"] = optimize_choice
            optimize_value = texture_template or True
            task_params["optimize_textures"] = optimize_value
            check_params["check_texture_optimization"] = optimize_value

        override = self.ui.b009.isChecked()

        if override:
            task_params = {k: v for k, v in task_params.items() if v}
            check_params = {}
        else:
            task_params = {k: v for k, v in task_params.items() if v}
            check_params = {k: v for k, v in check_params.items() if v}

        self.logger.debug(f"Task parameters: {task_params}")
        self.logger.debug(f"Check parameters: {check_params}")

        export_mode = task_params.pop("export_visible_objects", "visible")

        def objects_to_export():
            import bpy
            import blendertk as btk
            from blendertk.node_utils.data_nodes import DataNodes

            if export_mode == "selected":
                # data_internal is an ordinary, fully-selectable Empty (no hide_select) -- a
                # plain "Select All" before an export-selected workflow would otherwise sweep
                # its bake-session manifest into the export object set.
                return [
                    o for o in btk.selected_objects() if o.name != DataNodes.INTERNAL
                ]
            elif export_mode == "all":
                return [o for o in bpy.context.scene.objects if o.type == "MESH"]
            else:  # "visible" (also the fallback for any unknown mode)
                return btk.get_visible_geometry()

        export_tasks = {**task_params, **check_params}
        export_tasks["output_format"] = self.ui.cmb004.currentData()

        # Success/failure is already surfaced via the log panel (self.logger routes there);
        # nothing else here consumes perform_export's return value.
        self.perform_export(
            objects=objects_to_export,
            export_dir=self.ui.txt000.text(),
            preset_name=self.ui.cmb000.currentData(),
            export_visible=(export_mode != "selected"),
            output_name=self.ui.txt001.text(),
            name_regex=self.ui.txt002.text(),
            timestamp=self.ui.chk004.isChecked(),
            create_log_file=self.ui.b011.isChecked(),
            log_level=self.ui.cmb003.currentData(),
            tasks=export_tasks,
        )

        output_dir = self.ui.txt000.text()
        self.save_output_dir(output_dir)
        self.save_output_name(self.ui.txt001.text())

    def b010(self) -> None:
        """Set Output Directory"""
        output_dir = self.sb.dir_dialog(
            title="Select an output directory:", start_dir=self.workspace
        )
        if output_dir:
            self.ui.txt000.setText(output_dir)

    def b012(self) -> None:
        """Browse for Output File -- name the export after an existing file.

        Opens at the currently specified output directory (falling back to the
        workspace when it is unset or gone) and filters to the extensions the
        selected output format (``cmb004``) writes. The pick sets the output
        name to the file's basename and, when the file was chosen from another
        directory, retargets the output directory to match -- so the file the
        user pointed at is the file the next export overwrites.
        """
        start_dir = self.ui.txt000.text()
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = self.workspace or ""

        file_types = {
            "fbx": ["*.fbx"],
            "glb": ["*.glb"],
            "fbx_glb": ["*.fbx", "*.glb"],
            "usd": ["*.usd", "*.usda", "*.usdc"],
        }.get(self.ui.cmb004.currentData(), ["*.fbx", "*.glb", "*.usd"])

        file_path = self.sb.file_dialog(
            file_types=file_types,
            title="Select a file to name the export after:",
            start_dir=start_dir,
            filter_description="Export Files",
            allow_multiple=False,
        )
        if not file_path:
            return

        self.ui.txt001.setText(ptk.format_path(file_path, "name"))

        # Second pass restores the trailing slash on a drive root ("O:" -> "O:/",
        # which Windows resolves to that drive's CWD rather than its root).
        file_dir = ptk.format_path(ptk.format_path(file_path, "path"))
        if file_dir and file_dir != ptk.format_path(self.ui.txt000.text()):
            self.ui.txt000.setText(file_dir)
            self.logger.info(f"Output directory set to: {file_dir}")

    def b006(self) -> None:
        """Open Output Directory"""
        output_dir = self.ui.txt000.text()
        if os.path.exists(output_dir):
            os.startfile(output_dir)

    def b007(self) -> None:
        """Open Preset Directory."""
        preset_dir = self.fbx_preset_dir()
        os.makedirs(preset_dir, exist_ok=True)
        os.startfile(preset_dir)

    def b008(self) -> None:
        """Edit Preset -- open the selected preset's JSON file in the OS's default editor so
        the user can hand-edit and re-save the FBX kwargs. Blender has no per-field editor for
        an arbitrary ``export_scene.fbx`` kwargs dict the way Maya's native FBX exporter dialog
        does, so opening the on-disk JSON directly is the closest equivalent edit surface.

        A built-in preset is shadowed into the user tier first ("duplicate to edit" -- the
        same pattern ``PresetStore`` documents and ``delete_fbx_preset`` already enforces --
        so editing never touches the shipped, read-only file in place (which may not even be
        writable on a non-editable install, and would otherwise mutate a tracked default for
        every user)."""
        name = self.ui.cmb000.currentData()
        if not name:
            self.logger.error("No preset selected to edit.")
            return
        store = self._preset_store()
        if store.source(name) == "builtin":
            try:
                options = store.load(name)
            except (KeyError, ValueError, OSError) as e:
                self.logger.error(
                    f"Failed to read built-in preset {name!r} to shadow for editing: {e}"
                )
                return
            store.save(name, options)
            self.ui.cmb000.init_slot()
            self.ui.cmb000.setCurrentText(name)
            self.logger.info(
                f"Copied built-in preset {name!r} to the user tier for editing."
            )
        path = self.fbx_preset_path(name)
        if not path or not os.path.isfile(path):
            self.logger.error(f"Preset file does not exist: {name}")
            return
        os.startfile(path)

    def save_output_dir(self, output_dir: str) -> None:
        """Record the output directory into the recent values plugin."""
        if output_dir and hasattr(self, "_recent_dirs_option"):
            self._recent_dirs_option.record(ptk.format_path(output_dir))

    def save_output_name(self, output_name: str) -> None:
        """Record the output filename into the recent values plugin."""
        if output_name and hasattr(self, "_recent_names_option"):
            self._recent_names_option.record(output_name)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("scene_exporter", reload=True)
    ui.show(pos="screen", app_exec=True)
