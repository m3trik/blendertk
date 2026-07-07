# !/usr/bin/python
# coding=utf-8
"""Texture Path Editor tool panel — Switchboard slot wiring for the co-located
``texture_path_editor.ui``.

Blender counterpart of mayatk's Texture Path Editor, mirroring its structure 1:1 (same
objectNames, same layout, same header-menu sections — General / Path Management / Selection —
over a three-column table whose path cells are editable, with a per-row right-click menu, the
same selection-aware "scope" semantics, and the same in-session "Previous path" tooltip
bookkeeping). Maya file-node concepts map onto Blender image datablocks — a row is one FILE
image (``img.filepath`` is the path); the Material column lists the material(s) whose shader
graph references it. Path commands operate on the selected rows if any, otherwise on every image
(the same selection-aware scope as Maya).

Maya-only concepts that don't map are either adapted or dropped with a documented reason:
Maya's *sourceimages* project folder has no Blender analogue (no project workspace) — the
Blender analogue used throughout is ``<blenddir>/textures`` (falling back to the .blend's own
folder), and paths are made ``//``-relative to it the same way mayatk's commands make paths
relative to sourceimages; the Hypershade graph maps onto Blender's Shader Editor
(``row_show_in_hypershade``); *Select File Node* is kept as a structural placeholder (disabled,
``# TODO(blender-parity)``) because Blender images have no node-name handle distinct from the
datablock the way a Maya ``file`` node does.

The engine lives in ``blendertk.MatUtils`` (``get_image_records`` / ``repath_image`` /
``to_project_relative`` / ``resolve_missing_textures`` / ``normalize_texture_paths`` /
``set_texture_directory`` / ``find_and_copy_textures`` / ``reload_textures`` /
``select_by_material`` / ``graph_materials``); this is the thin Qt driver. Self-contained
(``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on tentacle; ``import bpy``
and the Qt-only ``uitk`` helpers are deferred into the call bodies (headless Blender ships no Qt
binding).
"""
import os

import pythontk as ptk

import blendertk as btk


class TexturePathEditorSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Texture Path Editor panel."""

    # Table columns whose values a selection/context action reads (mirror of the Maya slot's
    # ``_ROW_SELECTION_COLUMNS`` — Maya's shader/path/file_node → Blender material/path/image).
    _ROW_SELECTION_COLUMNS = {"material": 0, "path": 1, "image": 2}

    # Set-Directory / Find-&-Copy relocate combobox items (label, mode-key). Order is the
    # contract — the combobox is populated in this order and the index maps back to the key.
    _RELOCATE_MODE_ITEMS = (
        ("Leave textures in place (path only)", "rewrite"),
        ("Copy textures to new directory", "copy"),
        ("Move textures to new directory", "move"),
    )
    _FIND_MODE_ITEMS = (("Copy", "copy"), ("Move", "move"))
    # Normalize-Paths external-texture handling.
    _NORMALIZE_MODE_ITEMS = (
        ("Leave external textures untouched", "relative"),
        ("Copy external textures into the project", "copy"),
        ("Move external textures into the project", "move"),
    )

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.texture_path_editor
        self._image_to_mats = {}  # image-name -> [material names] (rebuilt each refresh)
        self._previous_paths = {}  # image name -> path before last in-session repath
        self._refresh_pending = False
        self._browse_in_progress = False  # re-entry guard
        self._find_copy_in_progress = False  # re-entry guard
        self._footer_controller = self._create_footer_controller()
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[texture_path_editor] ")

    # ------------------------------------------------------------------ header menu
    def header_init(self, widget):
        """Build the header menu (General / Path Management / Selection) + help text.

        Plain action items are QPushButtons wired via ``clicked.connect``. The four items with
        per-button option-box flyouts (Set Directory, Find & Copy, Normalize Paths, Resolve
        Missing Textures) are uitk ``PushButton`` (``tb_*``) auto-wired by name; their flyout
        contents are populated by the matching ``tb_*_init`` methods below.
        """
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self.refresh_texture_table)

        widget.menu.add("Separator", setTitle="General")
        btn_open = widget.menu.add(
            "QPushButton",
            setText="Open Textures Folder",
            setObjectName="btn_open_source_images",
            setToolTip="Open the project's textures directory in the file explorer.",
        )
        btn_open.clicked.connect(self.open_source_images)

        btn_reload = widget.menu.add(
            "QPushButton",
            setText="Reload Scene Textures",
            setObjectName="btn_reload_scene_textures",
            setToolTip=(
                "Force Blender to re-read every image datablock from disk. Useful after "
                "editing textures externally or after Find & Copy / Normalize Paths relocates "
                "them."
            ),
        )
        btn_reload.clicked.connect(self.reload_scene_textures)

        widget.menu.add("Separator", setTitle="Path Management")
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Set Directory…",
            setObjectName="tb_set_texture_directory",
            setToolTip=(
                "Repath every (selected, or all) image so its file lives under the chosen "
                "directory. Paths become // relative when the chosen directory is inside the "
                ".blend's own folder. Option box (▸) chooses leave / copy / move."
            ),
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Find && Copy Textures…",
            setObjectName="tb_find_and_copy_textures",
            setToolTip=(
                "Search recursively from a source directory for textures used by (selected, "
                "or all) images, relocate them to a destination, and repath. Paths become // "
                "relative when the destination is inside the .blend's own folder. Option box "
                "(▸) toggles Copy / Move."
            ),
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Normalize Paths",
            setObjectName="tb_normalize_paths",
            setToolTip=(
                "Rewrite (selected, or all) paths relative to the saved .blend (// paths). "
                "Option box (▸) controls external textures: leave / copy / move into the "
                "project."
            ),
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Resolve Missing Textures",
            setObjectName="tb_resolve_missing_textures",
            setToolTip=(
                "Search a folder (recursively) for replacement files for missing (selected, or "
                "all) textures. Enabled strategies run in order: Stem → Texture → Fuzzy "
                "(safest first); stops at first hit. Option box (▸) enables/disables "
                "individual strategies."
            ),
        )

        widget.menu.add("Separator", setTitle="Selection")
        btn_sel_obj = widget.menu.add(
            "QPushButton",
            setText="Select Textures for Selected Objects",
            setObjectName="btn_select_textures_for_objects",
            setToolTip=(
                "Highlight the texture-path cells for textures used by the currently selected "
                "scene objects."
            ),
        )
        btn_sel_obj.clicked.connect(self.select_textures_for_objects)

        btn_sel_broken = widget.menu.add(
            "QPushButton",
            setText="Select Broken Paths",
            setObjectName="btn_select_broken_paths",
            setToolTip="Highlight rows whose texture file is missing.",
        )
        btn_sel_broken.clicked.connect(self.select_broken_paths)

        btn_sel_abs = widget.menu.add(
            "QPushButton",
            setText="Select Absolute Paths",
            setObjectName="btn_select_absolute_paths",
            setToolTip=(
                "Highlight rows whose path is absolute (regardless of validity). These are "
                "candidates for Normalize Paths."
            ),
        )
        btn_sel_abs.clicked.connect(self.select_absolute_paths)

        widget.set_help_text(
            fmt(
                title="Texture Path Editor",
                body="Inspect and fix image texture paths. Path commands operate on selected "
                "rows if any, otherwise on all images in the file.",
                sections=[
                    ("Path management (header menu)", [
                        "<b>Set Directory…</b> — repath to a chosen folder. Option box (▸) "
                        "chooses leave / copy / move.",
                        "<b>Find &amp; Copy Textures…</b> — search an external folder for "
                        "matching textures, copy or move them into a destination. Option box "
                        "(▸) toggles Copy / Move.",
                        "<b>Normalize Paths</b> — rewrite paths relative to the saved .blend. "
                        "Option box (▸) controls external textures: leave / copy / move into "
                        "the project.",
                        "<b>Resolve Missing Textures</b> — search a folder using strategy "
                        "cascade <i>Stem → Texture → Fuzzy</i> (safest first; stops at first "
                        "hit). Option box (▸) enables/disables individual strategies.",
                    ]),
                    ("General (header menu)", [
                        "<b>Open Textures Folder</b> — Explorer shortcut.",
                        "<b>Reload Scene Textures</b> — force Blender to re-read all images "
                        "from disk (useful after relocations).",
                    ]),
                    ("Selection helpers (header menu)", [
                        "<b>Select Textures for Selected Objects</b> — highlight rows for "
                        "textures used by the current scene selection.",
                        "<b>Select Broken Paths</b> — rows whose file is missing on disk.",
                        "<b>Select Absolute Paths</b> — rows with absolute paths (candidates "
                        "for Normalize Paths).",
                    ]),
                ],
                notes=[
                    "<b>Right-click</b> any row for per-texture actions: Browse for File, "
                    "scene selection, Shader Editor graph, delete. <i>Select File Node</i> is "
                    "disabled — Blender images have no node-name handle distinct from the "
                    "datablock itself.",
                    "Collision policy on Copy / Move: same-name + same-size files rebind "
                    "without overwriting; different-size hits skip with a warning (never "
                    "silently rebinds to a wrong texture, never destroys the external).",
                    "Normalize Paths / Copy or Move into the project need the .blend to be "
                    "saved.",
                ],
            )
        )

    def tb_set_texture_directory_init(self, widget):
        """Populate the Set Directory option-box with the relocate-mode combobox."""
        widget.option_box.menu.setTitle("Set Directory")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_relocate_mode",
            setToolTip=(
                "Behavior for texture files when the directory changes:\n\n"
                "• Leave in place — only rewrite the path.\n"
                "• Copy — duplicate each texture into the chosen directory.\n"
                "• Move — relocate each texture into the chosen directory.\n\n"
                "Collision policy: same-name + same-size at destination is a safe rebind (no "
                "overwrite). Different size is skipped + warned — never silently rebind to a "
                "wrong texture."
            ),
            addItems=[label for label, _key in self._RELOCATE_MODE_ITEMS],
        )

    def tb_find_and_copy_textures_init(self, widget):
        """Populate the Find & Copy option-box with the copy/move combobox.

        Also wires the combobox to swap the button text between ``Find & Copy Textures…`` and
        ``Find & Move Textures…`` so the active mode is visible on the menu item itself.
        """
        widget.option_box.menu.setTitle("Find & Copy Textures")
        cmb = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_relocate_mode",
            setToolTip=(
                "How to relocate matched textures into the destination:\n\n"
                "• Copy — duplicate each match into the destination.\n"
                "• Move — relocate each match into the destination (removes the source file "
                "after a successful copy)."
            ),
            addItems=[label for label, _key in self._FIND_MODE_ITEMS],
        )

        def _sync_text(idx):
            label = self._FIND_MODE_ITEMS[idx][0] if 0 <= idx < len(self._FIND_MODE_ITEMS) else "Copy"
            widget.setText(f"Find && {label} Textures…")

        cmb.currentIndexChanged.connect(_sync_text)
        _sync_text(cmb.currentIndex())  # initial sync

    def tb_normalize_paths_init(self, widget):
        """Populate the Normalize Paths option-box with the external-mode combobox."""
        widget.option_box.menu.setTitle("Normalize Paths")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_external_mode",
            setToolTip=(
                "Behavior for external textures (paths outside the .blend's own folder) whose "
                "file exists on disk:\n\n"
                "• Leave untouched — only rewrite paths already inside the project folder.\n"
                "• Copy into the project — duplicate the file in, then rebind.\n"
                "• Move into the project — relocate the file in, then rebind.\n\n"
                "Collision policy: same-name + same-size in the project folder is a safe "
                "rebind (no overwrite). Different size is skipped + warned — never silently "
                "rebind to a wrong texture."
            ),
            addItems=[label for label, _key in self._NORMALIZE_MODE_ITEMS],
        )

    def tb_resolve_missing_textures_init(self, widget):
        """Populate the Resolve Missing option-box with the strategy checkboxes."""
        widget.option_box.menu.setTitle("Resolve Missing Textures")
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Stem  — exact name, any extension",
            setObjectName="chk_stem",
            setChecked=True,
            setToolTip=(
                "Match a file whose name (any extension) equals the missing texture's name."
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Texture  — same map type + base name (safest fuzzy)",
            setObjectName="chk_texture",
            setChecked=True,
            setToolTip=(
                "Restrict candidates to files of the same map type (AO / Normal / Roughness / "
                "…) and fuzzy-match on the map-stripped base name — an _AO file can never get "
                "repathed to a _Normal file."
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Fuzzy  — similar name (loose; may mismatch)",
            setObjectName="chk_fuzzy",
            setChecked=True,
            setToolTip=(
                "Loose name matching across all candidates. May mismatch on map-type "
                "boundaries."
            ),
        )

    # ------------------------------------------------------------------ table
    def tbl000_init(self, widget):
        """Build the row context menu once, then (re)populate the table."""
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self.handle_cell_edit)
            if self._footer_controller:
                widget.itemSelectionChanged.connect(self._footer_controller.update)

            widget.menu.add("Separator", setTitle="Path Management")
            widget.menu.add(
                "QPushButton",
                setText="Browse for File...",
                setObjectName="row_browse_for_file",
                setToolTip=(
                    "Open a file browser and pick a texture file to repath this row to. "
                    "Single selection only."
                ),
            )

            widget.menu.add("Separator", setTitle="Selection")
            widget.menu.add(
                "QPushButton",
                setText="Select In Scene",
                setObjectName="select_material",
                setToolTip=(
                    "Select all scene objects currently using this row's material(s)."
                ),
            )
            # TODO(blender-parity): mayatk's "Select File Node" selects the shading node itself
            # (distinct from selecting the objects that use it). Blender images have no separate
            # node-name handle — the closest concept would be selecting the ShaderNodeTexImage
            # node(s) referencing this image inside their material graphs, which needs its own
            # small helper in the engine. Kept disabled for structural parity with mayatk's menu
            # until that helper exists.
            widget.menu.add(
                "QPushButton",
                setText="Select File Node",
                setObjectName="select_file_node",
                setEnabled=False,
                setToolTip=(
                    "No Blender equivalent: images have no node-name handle distinct from the "
                    "datablock itself. Use Select In Scene or Show in Shader Editor instead."
                ),
            )
            widget.menu.add(
                "QPushButton",
                setText="Show in Shader Editor",
                setObjectName="row_show_in_hypershade",
                setToolTip="Graph the selected row's material in Blender's Shader Editor.",
            )

            widget.menu.add("Separator", setTitle="Edit")
            widget.menu.add(
                "QPushButton",
                setText="Delete Image",
                setObjectName="delete_file_node",
                setToolTip="Delete the selected image datablock(s).",
            )

            def _bind(action_name, method):
                widget.register_menu_action(
                    action_name,
                    lambda selection, fn=method: fn(selection),
                    columns=self._ROW_SELECTION_COLUMNS,
                )

            _bind("row_browse_for_file", self.row_browse_for_file)
            _bind("select_material", self.select_material)
            _bind("select_file_node", self.select_file_node)
            _bind("row_show_in_hypershade", self.row_show_in_hypershade)
            _bind("delete_file_node", self.delete_file_node)

            self._setup_scene_change_callback(widget)

        self._refresh_table_content(widget)

    def _refresh_table_content(self, widget):
        """Repopulate the table from the scene's FILE images (Material · Path · Image)."""
        self._image_to_mats = btk.get_image_material_map()
        records = btk.get_image_records()

        # Block signals across the rebuild — cellChanged is wired to handle_cell_edit, and
        # populating cells would otherwise fire it (spurious repath/rename on every refresh).
        widget.setUpdatesEnabled(False)
        widget.blockSignals(True)
        widget.clear()
        try:
            rows = []
            if not records:
                rows = [["", "", ("No file textures found", "")]]
            else:
                for r in records:
                    mats = self._image_to_mats.get(r["name"], [])
                    mat_label = ", ".join(mats) if mats else "(unused)"
                    rows.append([
                        (mat_label, mats[0] if mats else ""),
                        r["filepath"],
                        (r["name"], r["name"]),
                    ])
            widget.add(rows, headers=["Material", "Texture Path", "Image"])

            from qtpy import QtWidgets

            # Material (col 0) is a derived display — read-only (Path/Image cells stay editable
            # for repath / rename, handled by handle_cell_edit).
            for row in range(widget.rowCount()):
                item = widget.item(row, 0)
                if item:
                    item.setFlags(item.flags() & ~self.sb.QtCore.Qt.ItemIsEditable)

            header = widget.horizontalHeader()
            header.setSectionsMovable(False)
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
            widget.setColumnWidth(0, 200)
            widget.setColumnWidth(2, 200)

            self.setup_formatting(widget, records)
            widget.apply_formatting()
        finally:
            widget.blockSignals(False)
            widget.setUpdatesEnabled(True)

        if self._footer_controller:
            self._footer_controller.update()

    def setup_formatting(self, widget, records):
        """Mark the path cell invalid (red) when its file is missing; tooltip the abs path (plus
        the prior path, when this session repathed the row — mirrors mayatk's "Previous:" line)."""
        exists_by_path = {}
        abspath_by_path = {}
        for r in records:
            exists_by_path[r["filepath"]] = r["exists"]
            abspath_by_path[r["filepath"]] = r["abspath"]

        def format_if_invalid(item, value, row, col, *_):
            path = str(value).strip()
            exists = exists_by_path.get(path)
            if exists is None:  # cell was edited to a new path — resolve live
                ap = self._resolve_path(path)
                exists = bool(ap and os.path.exists(ap))
                abspath_by_path[path] = ap
            widget.format_item(item, key="reset" if exists else "invalid")
            ap = abspath_by_path.get(path, path)
            tooltip_lines = [ap if exists else f"Missing file:\n{ap}"]
            img_item = widget.item(row, 2)
            img_name = str(img_item.text()).strip() if img_item else ""
            previous = self._previous_paths.get(img_name) if img_name else None
            if previous and previous != path:
                tooltip_lines.append(f"Previous: {previous}")
            item.setToolTip("\n\n".join(tooltip_lines))

        widget.set_column_formatter(1, format_if_invalid)

    # ------------------------------------------------------------------ scope
    def _get_scope_images(self):
        """(images, label) — selected rows' images if any, else every FILE image."""
        selected = self._images_from_selection(None)
        if selected:
            return selected, f"{len(selected)} selected row(s)"
        images = [r["image"] for r in btk.get_image_records()]
        return images, f"all {len(images)} texture(s)"

    def _images_from_selection(self, selection):
        """Image datablocks behind ``selection`` (or the live row selection)."""
        import bpy

        table = self.ui.tbl000
        if selection is None:
            selection = table.get_selection(
                columns=self._ROW_SELECTION_COLUMNS, include_current=True
            )
        images = []
        for entry in selection or []:
            name = self._selection_value(entry, "image")
            img = bpy.data.images.get(name) if name else None
            if img is not None and img not in images:
                images.append(img)
        return images

    def _selection_value(self, entry, key):
        if hasattr(entry, "values"):
            return entry.values.get(key)
        if isinstance(entry, dict):
            if key in entry:
                return entry.get(key)
            col = self._ROW_SELECTION_COLUMNS.get(key)
            return entry.get(col) if col is not None else None
        return None

    # ------------------------------------------------------------------ header slots — General
    def open_source_images(self):
        """Open the project's textures directory in the file explorer."""
        path = self._resolve_source_images_path()
        if not path:
            self.sb.message_box("Save the .blend first — there is no project folder yet.")
            return
        try:
            os.startfile(path)  # noqa: S606 — Windows-only convenience (matches the Maya slot)
        except (AttributeError, OSError):
            self.sb.message_box(f"Textures folder:<br><hl>{path}</hl>")

    def reload_scene_textures(self):
        """Force Blender to re-read every image from disk."""
        btk.reload_textures()
        self.sb.message_box("Reloaded scene textures from disk.")
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------ header slots — Path Management
    def tb_set_texture_directory(self, widget=None):
        """Repath images (selection or all) so their files live under a chosen directory.

        The option-box combobox selects whether files are also relocated to the new directory
        (copy / move) or only the path attribute changes (rewrite, default).
        """
        images, scope_label = self._get_scope_images()
        if not images:
            self.sb.message_box("No textures to process.")
            return
        mode = self._read_combo_mode(widget, "cmb_relocate_mode", self._RELOCATE_MODE_ITEMS)

        # Surface the active mode in the dialog title — last interaction before any file ops
        # fire. Matches the dynamic-text intent in Find & Copy.
        mode_hint = {"rewrite": "path only", "copy": "copy files", "move": "move files"}.get(
            mode, mode
        )
        target_dir = self.sb.dir_dialog(
            title=f"Set Texture Directory — {mode_hint} — {scope_label}"
        )
        if not target_dir:
            return
        record = self._snapshot_for_tracking(images)
        count = btk.set_texture_directory(images, target_dir, mode=mode)
        record()
        self.sb.message_box(f"Updated <hl>{count}</hl>/{len(images)} texture path(s).")
        self.ui.tbl000.init_slot()

    def tb_find_and_copy_textures(self, widget=None):
        """Search a folder for the images' textures, copy/move to a destination, repath."""
        images, _scope = self._get_scope_images()
        if not images:
            self.sb.message_box("No textures to process.")
            return
        mode = self._read_combo_mode(widget, "cmb_relocate_mode", self._FIND_MODE_ITEMS)
        self._find_and_copy_workflow(images, relocate_mode=mode)

    def _read_combo_mode(self, button, combo_name, mode_items):
        """Read a relocate/external combobox by index → mode key (safe default = first)."""
        try:
            idx = getattr(button.option_box.menu, combo_name).currentIndex()
        except AttributeError:
            return mode_items[0][1]
        return mode_items[idx][1] if 0 <= idx < len(mode_items) else mode_items[0][1]

    def tb_normalize_paths(self, widget=None):
        """Rewrite (selected, or all) paths relative to the saved .blend; option box handles
        external textures."""
        images, scope_label = self._get_scope_images()
        if not images:
            self.sb.message_box("No textures to process.")
            return

        external_mode = self._read_combo_mode(
            widget, "cmb_external_mode", self._NORMALIZE_MODE_ITEMS
        )
        record = self._snapshot_for_tracking(images)
        if external_mode in ("copy", "move"):
            btk.normalize_texture_paths(external_mode, images=images)
        n = btk.normalize_texture_paths("relative", images=images)
        record()
        self.sb.message_box(
            f"Made <hl>{n}</hl> path(s) relative ({scope_label})."
            if n
            else "Nothing changed — paths are already relative (or the .blend isn't saved)."
        )
        self.ui.tbl000.init_slot()

    def tb_resolve_missing_textures(self, widget=None):
        """Search a folder for replacements for missing (selected, or all) textures.

        Strategy selection is read from this button's own option_box checkboxes.
        """
        images, _scope = self._get_scope_images()
        if not images:
            self.sb.message_box("No textures to process.")
            return

        use_stem, use_texture, use_fuzzy = self._read_resolve_modes(widget)
        if not (use_stem or use_texture or use_fuzzy):
            self.sb.message_box("No Resolve Missing strategies enabled in the option-box.")
            return
        search_dir = self.sb.dir_dialog(title="Resolve Missing Textures — pick a search folder")
        if not search_dir:
            return
        record = self._snapshot_for_tracking(images)
        n = btk.resolve_missing_textures(
            search_dir, stem=use_stem, texture=use_texture, fuzzy=use_fuzzy, images=images
        )
        record()
        self.sb.message_box(f"Resolved <hl>{n}</hl> missing texture(s).")
        self.ui.tbl000.init_slot()

    def _read_resolve_modes(self, button):
        """Read the Resolve Missing strategy checkboxes → ``(use_stem, use_texture, use_fuzzy)``."""
        menu = getattr(button, "option_box", None)
        if menu is None:
            return True, True, True
        return (
            bool(menu.menu.chk_stem.isChecked()),
            bool(menu.menu.chk_texture.isChecked()),
            bool(menu.menu.chk_fuzzy.isChecked()),
        )

    # ------------------------------------------------------------------ header slots — Selection
    def select_textures_for_objects(self):
        """Select rows whose image is used by a material on the scene selection."""
        objects = btk.selected_objects()
        if not objects:
            self.sb.message_box("Select object(s) first.")
            return
        mat_names = {
            s.material.name
            for o in objects
            for s in getattr(o, "material_slots", [])
            if s.material
        }
        if not mat_names:
            self.sb.message_box("No materials found on selected objects.")
            return
        target_images = {
            img_name
            for img_name, mats in self._image_to_mats.items()
            if mat_names.intersection(mats)
        }
        self._select_rows_by_predicate(
            lambda img_name, path: img_name in target_images, "matching textures"
        )

    def select_broken_paths(self):
        """Select rows whose texture file is missing."""
        missing = {r["name"] for r in btk.get_image_records() if not r["exists"]}
        self._select_rows_by_predicate(lambda img_name, path: img_name in missing, "broken paths")

    def select_absolute_paths(self):
        """Select rows whose path is absolute (not a // project-relative path)."""
        self._select_rows_by_predicate(
            lambda img_name, path: bool(path) and not path.startswith("//") and os.path.isabs(path),
            "absolute paths",
        )

    def _select_rows_by_predicate(self, predicate, label):
        """Select image-column cells where ``predicate(image_name, path)`` holds."""
        from qtpy import QtWidgets

        table = self.ui.tbl000
        table.clearSelection()
        prior = table.selectionMode()
        table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        selected = 0
        try:
            for row in range(table.rowCount()):
                img_item = table.item(row, 2)
                path_item = table.item(row, 1)
                if not img_item:
                    continue
                name = img_item.data(self.sb.QtCore.Qt.UserRole) or img_item.text()
                path = path_item.text() if path_item else ""
                if predicate(str(name), str(path)):
                    img_item.setSelected(True)
                    if selected == 0:
                        table.scrollToItem(img_item)
                    selected += 1
        finally:
            table.setSelectionMode(prior)
        self.sb.message_box(
            f"Selected <hl>{selected}</hl> {label}." if selected else f"No {label} found."
        )

    # ------------------------------------------------------------------ row-only context slots
    def row_browse_for_file(self, selection=None):
        """Open a file dialog and repath the selected row's image (single selection only)."""
        if getattr(self, "_browse_in_progress", False):
            return
        self._browse_in_progress = True
        try:
            self._do_browse_for_file(selection)
        finally:
            from qtpy.QtCore import QTimer

            QTimer.singleShot(250, lambda: setattr(self, "_browse_in_progress", False))

    def _do_browse_for_file(self, selection):
        images = self._images_from_selection(selection)
        if not images:
            return
        if len(images) > 1:
            self.sb.message_box("Browse for File: select a single row.")
            return
        img = images[0]
        chosen = self.sb.file_dialog(
            file_types=["*.png", "*.jpg", "*.jpeg", "*.tga", "*.tif", "*.tiff", "*.exr", "*.hdr", "*.bmp", "*.*"],
            title=f"Select texture file for {img.name}",
            filter_description="Texture Files",
            allow_multiple=False,
        )
        if not chosen:
            return
        new_path = btk.to_project_relative(chosen)
        old_path = img.filepath
        btk.repath_image(img, new_path)
        if old_path and old_path != img.filepath:
            self._previous_paths[img.name] = old_path
        self.sb.message_box(f"Repathed <hl>{img.name}</hl>.")
        self.ui.tbl000.init_slot()

    def select_material(self, selection=None):
        """Select scene objects using the materials of the selected rows."""
        import bpy

        images = self._images_from_selection(selection)
        if not images:
            return
        mat_names = {m for img in images for m in self._image_to_mats.get(img.name, [])}
        users = []
        for name in mat_names:
            mat = bpy.data.materials.get(name)
            if mat:
                users.extend(btk.select_by_material(mat, add=bool(users)))
        if users:
            self.sb.message_box(f"Selected objects for <hl>{len(mat_names)}</hl> material(s).")
        else:
            self.sb.message_box("No scene objects use the selected row's material(s).")

    def select_file_node(self, selection=None):
        """Disabled (see the row-menu tooltip) — retained for structural parity with mayatk's
        row menu; a future pass could select the ShaderNodeTexImage node(s) for this image in
        their material graphs. Unreachable while the menu item stays disabled."""
        # TODO(blender-parity): implement node selection in the material's shader graph once a
        # small "find image nodes for an image" helper exists in blendertk.mat_utils.
        return

    def row_show_in_hypershade(self, selection=None):
        """Graph the selected row's material(s) in the Shader Editor (Hypershade analogue)."""
        import bpy

        images = self._images_from_selection(selection)
        if not images:
            return
        mat_names = {m for img in images for m in self._image_to_mats.get(img.name, [])}
        mats = [m for m in (bpy.data.materials.get(n) for n in mat_names) if m]
        if mats:
            btk.graph_materials(mats)
        else:
            btk.open_editor("Shader Editor")

    def delete_file_node(self, selection=None):
        """Remove the selected image datablock(s)."""
        import bpy

        images = self._images_from_selection(selection)
        if not images:
            return
        names = [i.name for i in images]
        msg = (
            f"Delete the image datablock '{names[0]}'?"
            if len(names) == 1
            else f"Delete {len(names)} image datablock(s)?"
        )
        if self.sb.message_box(msg, "Yes", "No") != "Yes":
            return
        for img in images:
            try:
                bpy.data.images.remove(img)
            except (RuntimeError, ReferenceError) as e:
                self.logger.warning(f"Failed to remove image: {e}")
        self.sb.message_box(f"Deleted <hl>{len(names)}</hl> image(s).")
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------ cell editing
    def handle_cell_edit(self, row, col):
        """Editing a path cell repaths that row's image; the Image column renames the datablock."""
        import bpy

        table = self.ui.tbl000
        item = table.item(row, col)
        if not item:
            return
        new_value = item.text()
        img_item = table.item(row, 2)
        img_name = (img_item.data(self.sb.QtCore.Qt.UserRole) or img_item.text()) if img_item else None
        img = bpy.data.images.get(str(img_name)) if img_name else None
        if img is None:
            return

        if col == 1:  # path → repath
            old_path = img.filepath
            btk.repath_image(img, new_value)
            if old_path and old_path != new_value:
                self._previous_paths[img.name] = old_path
            table.apply_formatting()
            if self._footer_controller:
                self._footer_controller.update()
        elif col == 2 and new_value and new_value != img.name:  # rename datablock
            try:
                img.name = new_value
                item.setData(self.sb.QtCore.Qt.UserRole, img.name)
                if img.name != new_value:  # Blender de-duplicated the name
                    table.blockSignals(True)
                    item.setText(img.name)
                    table.blockSignals(False)
            except (RuntimeError, ReferenceError) as e:
                self.logger.warning(f"Failed to rename image: {e}")

    # ------------------------------------------------------------------ workflows
    def _find_and_copy_workflow(self, images, relocate_mode="copy"):
        """Run find/copy-or-move/repath with a re-entry guard.

        Modal dir dialogs occasionally deliver trailing release events that retrigger the slot,
        popping a second source-dir prompt. The guard protects against this (same pattern used by
        row_browse_for_file).
        """
        if getattr(self, "_find_copy_in_progress", False):
            return
        self._find_copy_in_progress = True
        try:
            self._do_find_and_copy_workflow(images, relocate_mode=relocate_mode)
        finally:
            from qtpy.QtCore import QTimer

            QTimer.singleShot(250, lambda: setattr(self, "_find_copy_in_progress", False))

    def _do_find_and_copy_workflow(self, images, relocate_mode="copy"):
        source_dir = self.sb.dir_dialog(title="Find & Copy — pick a folder to search recursively")
        if not source_dir:
            return
        dest_dir = self.sb.dir_dialog(title="Find & Copy — pick the destination folder")
        if not dest_dir:
            return
        record = self._snapshot_for_tracking(images)
        count = btk.find_and_copy_textures(images, source_dir, dest_dir, mode=relocate_mode)
        record()
        self.sb.message_box(
            f"{relocate_mode.title()}d + repathed <hl>{count}</hl> texture(s)."
            if count
            else "No matching textures found in the search folder."
        )
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------ scene refresh / misc
    def refresh_texture_table(self):
        """Manual refresh trigger from the header refresh button."""
        table = getattr(self.ui, "tbl000", None)
        if table:
            table.init_slot()

    def _setup_scene_change_callback(self, widget):
        """Subscribe to scene-change events via ScriptJobManager (mirrors mayatk's
        ScriptJobManager wiring so the table auto-refreshes after a file load)."""
        mgr = btk.ScriptJobManager.instance()
        # TODO(blender-parity): mayatk also listens for "SceneImported" and "workspaceChanged" —
        # blendertk's ScriptJobManager has no Blender-native backing for either (Blender doesn't
        # distinguish opening a file from appending/importing into one; there is no per-project
        # "workspace" concept the way Maya has), so only the two supported events are subscribed.
        for event in ("SceneOpened", "NewSceneOpened"):
            mgr.subscribe(event, lambda w=widget: self._on_scene_change(w), owner=self)
        mgr.connect_cleanup(widget, owner=self)

    def _on_scene_change(self, widget):
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def do_refresh():
            self._refresh_pending = False
            self._previous_paths.clear()
            try:
                try:
                    if not widget.isVisible():
                        pass
                except RuntimeError:
                    # Widget has been deleted (C++ object gone).
                    self.cleanup_scene_callbacks()
                    return
                self.logger.info("Scene changed, refreshing texture path table…")
                self._refresh_table_content(widget)
            except Exception as e:
                self.logger.warning(f"Error refreshing table on scene change: {e}")

        # Blender's handlers fire synchronously mid-load; defer the Qt repaint to the next event
        # loop tick — the Blender-idiomatic equivalent of Maya's cmds.evalDeferred.
        from qtpy.QtCore import QTimer

        QTimer.singleShot(0, do_refresh)

    def cleanup_scene_callbacks(self):
        """Clean up scene-change subscriptions via ScriptJobManager."""
        btk.ScriptJobManager.instance().unsubscribe_all(self)

    # ------------------------------------------------------------------ path-tracking helpers
    def _snapshot_for_tracking(self, images):
        """Snapshot ``images``' current paths; returns a callback that records any path that
        changed into ``self._previous_paths`` once the caller's batch operation finishes.

        Batch engine calls (``set_texture_directory`` / ``find_and_copy_textures`` /
        ``normalize_texture_paths`` / ``resolve_missing_textures``) mutate ``img.filepath``
        internally and only return a count, so the before/after diff has to be taken by the
        caller — this is the batched counterpart of mayatk's per-node inline
        ``self._previous_paths[node_name] = old_path`` bookkeeping.
        """
        before = {img.name: img.filepath for img in images}

        def record():
            import bpy

            for name, old_path in before.items():
                img = bpy.data.images.get(name)
                if img is not None and old_path and old_path != img.filepath:
                    self._previous_paths[name] = old_path

        return record

    @staticmethod
    def _resolve_path(path):
        """Resolve a raw path string (// project-relative included) to an absolute path."""
        import bpy

        try:
            return os.path.normpath(bpy.path.abspath(path)) if path else ""
        except Exception:
            return os.path.normpath(path) if path else ""

    # ------------------------------------------------------------------ footer
    def _create_footer_controller(self):
        """Wrap the footer in a status controller showing the resolved textures folder — mirrors
        mayatk's ``FooterStatusController`` wired to its sourceimages resolver."""
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return None
        try:
            from uitk.widgets.footer import FooterStatusController

            return FooterStatusController(
                footer=footer,
                resolver=self._resolve_source_images_path,
                default_text="",
                truncate_kwargs={"length": 96, "mode": "middle"},
            )
        except Exception:
            return None

    def _resolve_source_images_path(self) -> str:
        """The project's texture folder — Blender analogue of mayatk's
        ``EnvUtils.get_env_info("sourceimages")``: ``<blenddir>/textures`` when that folder
        exists, else the .blend's own folder. Empty until the file has been saved."""
        import bpy

        blenddir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""
        if not blenddir:
            return ""
        target = os.path.join(blenddir, "textures")
        return target if os.path.isdir(target) else blenddir


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("texture_path_editor", reload=True)
    ui.show(pos="screen", app_exec=True)
