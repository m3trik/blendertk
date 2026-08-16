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
rejected). Its option-box mirrors mayatk's b007/b008 1:1 by objectName:

* ``b007`` "Open Preset Directory" -- ``os.startfile`` the writable preset directory. Adding
  and deleting presets happens there: a preset is a plain JSON file, so the file browser
  already copies, renames, and deletes them better than a pair of one-shot buttons could
  (the programmatic equivalents, :meth:`SceneExporter.save_fbx_preset` /
  :meth:`~SceneExporter.delete_fbx_preset`, remain on the engine).
* ``b008`` "Edit Preset" -- ``os.startfile`` the selected preset's JSON file so the user can
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

        self.ui.b009.setEnabled(True)
        self.ui.b009.setChecked(False)
        self.ui.b009.setStyleSheet("QPushButton:checked {background-color: #FF9999;}")

        self.logger.setLevel(log_level)
        self.logger.hide_logger_name(True)
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)

        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

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
        """Initialize the header widget."""
        widget.menu.add_presets = True
        widget.menu.presets.preset_dir = "blendertk/scene_exporter"
        widget.menu.presets.scope = "window"
        widget.menu.presets.exclude("txt000", "txt001", "txt003")

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
                    "Pick a <b>Preset</b> (option box ▸ for preset management — "
                    "Open Folder / Edit; add and delete in the folder itself).",
                    "Configure the task list and output path in the panel.",
                    "Press the export action button to run.",
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

    def cmb000_init(self, widget) -> None:
        """Init FBX export-option preset combo (mirror of mayatk's ``cmb000_init`` -- see
        ``_scene_exporter.py``'s module docstring for the PresetStore-backed design)."""
        if not widget.is_initialized:
            widget.restore_state = True  # Enable state restore
            widget.refresh_on_show = True  # Call this method on show
            # Persist the selection by preset NAME, not combo index: the item list is rebuilt
            # from the preset store each show (mirrors mayatk's cmb000_init).
            widget.restore_by = "text"

            widget.option_box.menu.setTitle("Preset Options:")
            widget.option_box.menu.add_defaults_button = False
            # Adding and deleting presets is done in the preset directory itself
            # (b007) -- see this module's docstring.
            widget.option_box.menu.add(
                "QPushButton",
                setToolTip="Open the FBX export-option preset directory to add, rename, or delete presets.",
                setText="Open Preset Directory",
                setObjectName="b007",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setToolTip="Edit the current FBX export-option preset (opens its JSON file).",
                setText="Edit Preset",
                setObjectName="b008",
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

    def cmb001_init(self, widget) -> None:
        """Auto-generate Export Settings UI from task definitions using WidgetComboBox."""
        widget_items = []

        for task_name, params in self.task_manager.task_definitions.items():
            widget_type = params.pop("widget_type", "QCheckBox")
            object_name = self.sb.convert_to_legal_name(task_name)

            widget_class = getattr(self.sb.QtWidgets, widget_type, None)
            if widget_class is None:
                widget_class = getattr(self.sb.registered_widgets, widget_type, None)
                if widget_class is None:
                    raise ValueError(f"Unknown widget type: {widget_type}")

            created_widget = widget_class()
            self.ui.set_attributes(created_widget, setObjectName=object_name, **params)

            label = params.get("title", "") if widget_type == "Separator" else task_name
            widget_items.append((created_widget, label))

        widget.add(widget_items, header="Tasks", clear=True)

    def cmb002_init(self, widget) -> None:
        """Auto-generate Check Settings UI from check definitions using WidgetComboBox."""
        widget_items = []

        for check_name, params in self.task_manager.check_definitions.items():
            widget_type = params.get("widget_type", "QCheckBox")
            object_name = self.sb.convert_to_legal_name(check_name)

            widget_class = getattr(self.sb.QtWidgets, widget_type, None)
            if widget_class is None:
                widget_class = getattr(self.sb.registered_widgets, widget_type, None)
                if widget_class is None:
                    raise ValueError(f"Unknown widget type: {widget_type}")

            created_widget = widget_class()

            params_copy = {k: v for k, v in params.items() if k != "widget_type"}
            self.ui.set_attributes(
                created_widget, setObjectName=object_name, **params_copy
            )

            label = (
                params.get("title", "") if widget_type == "Separator" else check_name
            )
            widget_items.append((created_widget, label))

        widget.add(widget_items, header="Validation Checks", clear=True)

    def cmb004_init(self, widget) -> None:
        """Init Output Format — FBX (default), GLB, or FBX + GLB.

        The GLB texture carrier (``cmb006``) hangs off this combo's option box
        rather than the main layout: it only qualifies a GLB write, so it stays
        out of the way for the common FBX case. A menu item registers by
        objectName, so it keeps the same ``self.ui.cmb006`` / ``cmb006_init``
        wiring — and the same template coverage — a main-layout combo has.
        """
        if not widget.is_initialized:
            widget.restore_state = True
            # Built once: unlike the combo's own items (`add(clear=True)` is
            # idempotent), a menu add on a re-init would stack a SECOND cmb006
            # and rebind self.ui.cmb006 to the copy the user never touched.
            widget.option_box.menu.setTitle("GLB Options:")
            widget.option_box.menu.add_defaults_button = False
            widget.option_box.menu.add(
                self.sb.registered_widgets.ComboBox,
                setObjectName="cmb006",
                setToolTip=(
                    "How the GLB deliverable carries its textures: Original "
                    "(byte-stable), WebP (transport size, broad compatibility), "
                    "or KTX2 (GPU-compressed for web/XR runtimes; requires "
                    "toktx). Inert for FBX-only output."
                ),
            )
        widget.add(
            {"FBX": "fbx", "GLB": "glb", "FBX + GLB": "fbx_glb"},
            clear=True,
        )

    def cmb004(self, index, widget) -> None:
        """Output-format changed: the GLB Textures combo is inert without a GLB
        (mirror of mayatk's ``cmb004``)."""
        self._sync_glb_texture_combo()

    def cmb006_init(self, widget) -> None:
        """Init GLB Textures (mirror of mayatk's ``cmb006_init``).

        Lives in ``cmb004``'s option box (created there); this runs when the
        menu item is registered by objectName.

        The *carrier* axis, distinct from cmb005's *workflow* axis: cmb005
        decides what the maps ARE (channel packing / shading model, re-authored
        in the scene pre-export); this decides how the finished GLB embeds
        whatever they became. "Original" (default) keeps the conversion
        byte-stable. WebP / KTX2 run ``MeshConvert.optimize_glb_textures`` on
        the converted GLB — container only (``max_size=0``), resolution is
        never resampled. Inert unless the output format (cmb004) produces a
        GLB; ``b000`` folds the value into the tasks payload as
        ``glb_texture_format``.

        KTX2 output is a terminal delivery artifact: no fallback images ride
        along (``KHR_texture_basisu`` lands in ``extensionsRequired``), so it
        opens in web/XR runtimes (three.js / Babylon / model-viewer, the
        bundled WebXR preview) but will NOT re-import into Blender, Unreal, or
        stock Unity — pair it with FBX + GLB so the FBX stays the working
        artifact. Requires KTX-Software's ``toktx``; the export fails upfront
        with the install URL when it is missing.
        """
        from qtpy import QtCore

        if not widget.is_initialized:
            widget.restore_state = True
        # Index 0 = Original is a TEMPLATE contract (templates persist combos
        # by index) — never reorder or insert above it.
        widget.add(
            {
                "Original Textures": None,
                "WebP Textures": "WEBP",
                "KTX2 Textures": "KTX2",
            },
            clear=True,
        )
        tooltips = {
            "WEBP": (
                "Re-encode embedded textures as WebP (lossy q85; lightmaps "
                "stay lossless) — transport-size win with the broadest viewer "
                "compatibility, and the GLB still re-imports into Blender."
            ),
            "KTX2": (
                "GPU-compressed Basis (UASTC for normals/data, ETC1S for "
                "color; lightmaps stay lossless WebP) for web/XR runtimes. "
                "NOT re-importable into Blender/Unreal/stock Unity — a final "
                "delivery artifact. Requires KTX-Software's toktx."
            ),
        }
        for index in range(widget.count()):
            tip = tooltips.get(widget.itemData(index))
            if tip:
                widget.setItemData(index, tip, QtCore.Qt.ToolTipRole)
        self._sync_glb_texture_combo(widget)

    def _sync_glb_texture_combo(self, combo=None) -> None:
        """Enable cmb006 only when the output format (cmb004) produces a GLB."""
        combo = combo or getattr(self.ui, "cmb006", None)
        fmt_widget = getattr(self.ui, "cmb004", None)
        if combo is None or fmt_widget is None:
            return
        combo.setEnabled((fmt_widget.currentData() or "fbx") != "fbx")

    def cmb005_init(self, widget) -> None:
        """Init Texture Template (mirror of mayatk's ``cmb005_init``).

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

        # Texture template (cmb005, a main-layout combo like cmb004): the ONE
        # definition both pipeline hooks reference. Folded BEFORE the override
        # filter so "override checks" keeps the conversion but skips the gate.
        texture_template = self.ui.cmb005.currentData()
        if texture_template:
            task_params["convert_textures"] = texture_template
            check_params["check_material_compatibility"] = texture_template

        # Optimize Textures (checkbox, mirror of mayatk): the pass rides
        # cmb005's template when one is selected — the template's per-map-type
        # output spec drives container/bit depth, its budget stays advisory —
        # else it is the generic per-map-type pass (True). Folded BEFORE the
        # override filter for the same reason as the template: "override
        # checks" keeps the optimization, skips the gate.
        if task_params.get("optimize_textures"):
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
        export_tasks["glb_texture_format"] = self.ui.cmb006.currentData()

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
