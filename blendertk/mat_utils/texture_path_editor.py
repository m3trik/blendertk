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
    # Colour markers leading the two Find & Copy directory dialogs. The native OS picker draws
    # its caption through the shell — plain text, no rich text — but it renders emoji in colour,
    # so the marker is the one channel that carries colour without giving up the shell browser.
    # Blue/amber rather than green/red: it survives the common colour-vision deficiencies, and
    # the words carry the meaning anyway — the glyph is redundancy, never the only signal.
    _DIALOG_MARK_SOURCE = "🔵"
    _DIALOG_MARK_DEST = "🟠"

    _FIND_MODE_ITEMS = (("Copy", "copy"), ("Move", "move"))
    # Displayed length of a texture path while the header's "Truncate Texture Paths" toggle is
    # on. Cut with ``mode="path"``, which drops whole middle components: the drive/root stays
    # readable at the front, the filename and as many of its parents as fit at the back.
    # ``_PATH_TRUNCATE_HEAD`` caps the front to that root — what identifies a texture is the end
    # of its path, so the whole budget goes there (mirror of the Maya slot's constants).
    _PATH_TRUNCATE_LENGTH = 67
    _PATH_TRUNCATE_HEAD = 1
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
                "Force Blender to re-read every texture from disk. Useful after "
                "editing textures externally or after Find & Copy / Normalize Paths relocates "
                "them."
            ),
        )
        btn_reload.clicked.connect(self.reload_scene_textures)

        chk_truncate = widget.menu.add(
            "QCheckBox",
            setText="Truncate Texture Paths",
            setObjectName="chk_truncate_paths",
            setChecked=False,
            setToolTip=(
                "Shorten long paths in the Texture Path column by dropping whole middle "
                "folders — the drive and its first directories stay readable at the front, "
                "the filename at the back.\n"
                "Display only — the cell still holds the full path, so edits, path commands "
                "and the tooltip are unaffected."
            ),
        )
        chk_truncate.toggled.connect(lambda *_: self._apply_path_truncation())

        chk_warn_len = widget.menu.add(
            "QCheckBox",
            setText="Warn On Over-Long Paths",
            setObjectName="chk_warn_path_length",
            setChecked=True,
            setToolTip=(
                "Flag rows whose resolved path is longer than this OS accepts "
                f"({ptk.FileUtils.path_length_limit()} characters).\n"
                "Over-long paths fail late and opaquely — a texture the FBX exporter "
                "silently cannot embed, a copy that reports success and produced nothing — "
                "and a path that fits here still breaks on a machine without long paths "
                "enabled (260 characters)."
            ),
        )
        chk_warn_len.toggled.connect(lambda *_: self.refresh_texture_table())

        widget.menu.add("Separator", setTitle="Path Management")
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Set Directory…",
            setObjectName="tb_set_texture_directory",
            setToolTip=(
                "Repath every (selected, or all) texture so its file lives under the chosen "
                "directory. Paths become // relative when the chosen directory is inside the "
                ".blend's own folder. Option box (▸) chooses leave / copy / move."
            ),
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Find && Copy Textures…",
            setObjectName="tb_find_and_copy_textures",
            setToolTip=(
                "Gather the files behind the (selected, or all) textures, relocate them into one "
                "destination, and repath. Paths become // relative when the destination is "
                "inside the .blend's own folder.\n\n"
                "By default a path that already resolves is its own source, so the search "
                "dialog only opens for what is unresolved — with every path valid the "
                "destination picker is the only prompt. Option box (▸) has Copy / Move plus "
                "both dialog-skipping toggles."
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
        btn_make_abs = widget.menu.add(
            "QPushButton",
            setText="Make Paths Absolute",
            setObjectName="btn_make_paths_absolute",
            setToolTip=(
                "Rewrite (selected, or all) // relative paths to absolute, resolved from "
                "the saved .blend. Inverse of Normalize Paths."
            ),
        )
        btn_make_abs.clicked.connect(self.make_paths_absolute)
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
            self.sb.tooltip.fmt(
                title="Texture Path Editor",
                body="Inspect and fix texture paths. Path commands operate on selected "
                "rows if any, otherwise on every texture in the .blend.",
                sections=[
                    (
                        "Path management (header menu)",
                        [
                            "<b>Set Directory…</b> — repath to a chosen folder. Option box (▸) "
                            "chooses leave / copy / move.",
                            "<b>Find &amp; Copy Textures…</b> — gather every texture the "
                            "images use and relocate them into one destination. Option box (▸): "
                            "Copy / Move, <i>Use Valid Paths As Source</i> (on — a path that "
                            "already resolves is its own source, so the search dialog opens only "
                            "for what is unresolved, and cancelling it skips just those), "
                            "<i>Always Relocate To The Textures Folder</i> (off — on, the "
                            "destination dialog is skipped).",
                            "<b>Normalize Paths</b> — rewrite paths relative to the saved .blend. "
                            "Option box (▸) controls external textures: leave / copy / move into "
                            "the project.",
                            "<b>Make Paths Absolute</b> — rewrite // relative paths to absolute "
                            "(resolved from the saved .blend). Inverse of Normalize Paths.",
                            "<b>Resolve Missing Textures</b> — search a folder using strategy "
                            "cascade <i>Stem → Texture → Fuzzy</i> (safest first; stops at first "
                            "hit). Option box (▸) enables/disables individual strategies.",
                        ],
                    ),
                    (
                        "General (header menu)",
                        [
                            "<b>Open Textures Folder</b> — Explorer shortcut.",
                            "<b>Reload Scene Textures</b> — force Blender to re-read all images "
                            "from disk (useful after relocations).",
                            "<b>Truncate Texture Paths</b> — shorten the path column's display "
                            "by dropping whole middle folders (drive and filename stay "
                            "readable). The cell keeps the full path (edits, commands and the "
                            "tooltip always use it).",
                        ],
                    ),
                    (
                        "Selection helpers (header menu)",
                        [
                            "<b>Select Textures for Selected Objects</b> — highlight rows for "
                            "textures used by the current scene selection.",
                            "<b>Select Broken Paths</b> — rows whose file is missing on disk.",
                            "<b>Select Absolute Paths</b> — rows with absolute paths (candidates "
                            "for Normalize Paths).",
                        ],
                    ),
                ],
                notes=[
                    "Find &amp; Copy opens at most two directory dialogs and either one can be "
                    "the only one shown — read the title bar: <b>SOURCE</b> is the folder "
                    "searched, <b>DESTINATION</b> is where files are written.",
                    "<b>Right-click</b> any row for per-texture actions: Browse for File, "
                    "scene selection, Shader Editor graph, delete. <i>Select File Node</i> is "
                    "disabled — Blender images have no node-name handle distinct from the "
                    "datablock itself.",
                    "Collision policy on Copy / Move: same-name + same-size files rebind "
                    "without overwriting; different-size hits skip with a warning (never "
                    "silently rebinds to a wrong texture, never destroys the external).",
                    "Normalize Paths / Make Paths Absolute / Copy or Move into the project "
                    "need the .blend to be saved.",
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
        widget.option_box.menu.add(
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

        # Self-labelling: the mode lives in the option box, so the entry says which
        # one it will run. The combo is populated from _FIND_MODE_ITEMS' labels, so
        # its own text IS the label — no index lookup, and no out-of-range branch to
        # get wrong (the ``or`` keeps the first item's wording for an empty combo).
        self.sb.text_from(
            widget.option_box.menu,
            widget,
            "cmb_relocate_mode",
            lambda label: f"Find && {label or self._FIND_MODE_ITEMS[0][0]} Textures…",
            value=lambda w: w.currentText(),
        )

        widget.option_box.menu.add(
            "QCheckBox",
            setText="Use Valid Paths As Source",
            setObjectName="chk_use_valid_paths",
            setChecked=True,
            setToolTip=(
                "Treat a texture whose path already resolves on disk as its own source, instead "
                "of hunting for that file under the search folder.\n\n"
                "The search dialog then only appears when something is actually unresolved, and "
                "counts only those textures; cancelling it skips them and relocates the rest. "
                "With every path valid, the only dialog left is the destination.\n\n"
                "A valid path always outranks a search hit of the same name — that file is "
                "the one the scene is rendering with."
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Always Relocate To The Textures Folder",
            setObjectName="chk_dest_sourceimages",
            setChecked=False,
            setToolTip=(
                "Send every match to the project's textures folder without asking, skipping the "
                "destination dialog (the folder is created if missing).\n\n"
                "Paths land // relative, since that folder is inside the project. Off: pick the "
                "destination each run."
            ),
        )

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
            widget.menu.add(
                "QPushButton",
                setText="Select Texture Node",
                setObjectName="select_file_node",
                setToolTip=(
                    "Select the Image Texture node(s) bound to this row's texture, in every "
                    "material that uses it (nodes inside node groups included).\n\n"
                    "Blender's analogue of Maya's file node: the datablock owns the path, the "
                    "node references it — so one row can map to no nodes at all (an unused "
                    "texture) or to several."
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
                setText="Remove Texture",
                setObjectName="delete_file_node",
                setToolTip=(
                    "Remove the selected texture(s) from the .blend. Every Image Texture node "
                    "using one is left with an empty texture slot; the file on disk is not "
                    "deleted."
                ),
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
        """Repopulate the table from the scene's FILE images (Material · Path · Texture)."""
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
                    rows.append(
                        [
                            (mat_label, mats[0] if mats else ""),
                            r["filepath"],
                            (r["name"], r["name"]),
                        ]
                    )
            widget.add(rows, headers=["Material", "Texture Path", "Texture"])

            from qtpy import QtWidgets

            # Material (col 0) is a derived display — read-only (Path/Texture cells stay editable
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
            self._apply_path_truncation(widget)
        finally:
            widget.blockSignals(False)
            widget.setUpdatesEnabled(True)

        # After apply_formatting — that pass is what fills the set.
        over_long = getattr(self, "_over_long_paths", None)
        if over_long:
            self.logger.warning(
                f"Texture Path Editor: {len(over_long)} path(s) exceed this OS's "
                f"{ptk.FileUtils.path_length_limit()}-character path limit."
            )

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

        warn_long = self._warn_path_length_enabled()
        length_limit = ptk.FileUtils.path_length_limit()
        # Reset per rebuild; the formatter fills it as it paints, so the caller reads it only
        # after apply_formatting.
        self._over_long_paths = set()

        def format_if_invalid(item, value, row, col, *_):
            path = str(value).strip()
            exists = exists_by_path.get(path)
            if exists is None:  # cell was edited to a new path — resolve live
                ap = self._resolve_path(path)
                exists = bool(ap and os.path.exists(ap))
                abspath_by_path[path] = ap
            ap = abspath_by_path.get(path, path)
            # A missing file outranks an over-long one: it's the harder failure, and an
            # over-long path is usually WHY it went missing.
            over_long = warn_long and len(ap or "") > length_limit
            if over_long:
                self._over_long_paths.add(path)
            widget.format_item(
                item,
                key="invalid" if not exists else ("warning" if over_long else "reset"),
            )
            tooltip_lines = [ap if exists else f"Missing file:\n{ap}"]
            if over_long:
                tooltip_lines.append(
                    f"Path is {len(ap)} characters — over this OS's "
                    f"{length_limit}-character limit."
                )
            img_item = widget.item(row, 2)
            img_name = str(img_item.text()).strip() if img_item else ""
            previous = self._previous_paths.get(img_name) if img_name else None
            if previous and previous != path:
                tooltip_lines.append(f"Previous: {previous}")
            item.setToolTip("\n\n".join(tooltip_lines))

        widget.set_column_formatter(1, format_if_invalid)

    @staticmethod
    def _menu_flag(menu, name, default):
        """State of a checkbox on ``menu``; ``default`` when it isn't there.

        Every toggle in this panel is read from a menu that may not exist yet — a refresh can
        fire before ``header_init`` builds it, and a workflow can be driven without an option
        box at all (programmatic calls, tests). One lookup for all of them, so the per-toggle
        readers carry only the thing that actually differs: the default.
        """
        chk = getattr(menu, name, None) if menu is not None else None
        try:
            return bool(chk.isChecked())
        except AttributeError:
            return default

    def _header_menu(self):
        """The header's menu, or None while it is still unbuilt."""
        return getattr(getattr(self.ui, "header", None), "menu", None)

    def _warn_path_length_enabled(self):
        """State of the header's "Warn On Over-Long Paths" toggle.

        True when the header menu hasn't been built yet — an early refresh should warn, not
        silently skip the check (opposite default to the truncation toggle, which is off).
        """
        return self._menu_flag(self._header_menu(), "chk_warn_path_length", True)

    def _truncate_paths_enabled(self):
        """State of the header's "Truncate Texture Paths" toggle.

        False when the header menu hasn't been built yet, so an early refresh is safe.
        """
        return self._menu_flag(self._header_menu(), "chk_truncate_paths", False)

    def _apply_path_truncation(self, widget=None):
        """Push the Truncate Texture Paths toggle onto the path column.

        Display-only: ``set_column_truncation`` shortens what the delegate paints, never the
        item's data — the cell still holds (and edits back) the full path, and
        ``setup_formatting``'s tooltip still resolves it. Re-applied on every table rebuild so a
        refresh can't drop it.
        """
        widget = widget if widget is not None else getattr(self.ui, "tbl000", None)
        if widget is None:  # toggled before the table exists
            return
        widget.set_column_truncation(
            1,
            length=(
                self._PATH_TRUNCATE_LENGTH if self._truncate_paths_enabled() else None
            ),
            mode="path",
            # An ellipsis, not the primitive's default "..", which in a path column reads as a
            # parent-directory segment.
            insert="…",
            head=self._PATH_TRUNCATE_HEAD,
        )

    # ------------------------------------------------------------------ scope
    def _get_scope_images(self):
        """(images, label) — selected rows' images if any, else every FILE image.

        A row selection that resolves to NO live image (rows gone stale after a rename/delete)
        returns empty rather than falling through to every texture in the file — mirrors mayatk's
        ``_get_scope_nodes``, which distinguishes "nothing selected" from "selection with no valid
        nodes" so a broken selection is never silently escalated to scene-wide scope.
        """
        table = getattr(self.ui, "tbl000", None)
        selection = (
            table.get_selection(
                columns=self._ROW_SELECTION_COLUMNS, include_current=True
            )
            if table is not None
            else None
        )
        if selection:
            selected = self._images_from_selection(selection)
            if selected:
                return selected, f"{len(selected)} selected row(s)"
            self.logger.warning(
                "Selected row(s) contain no valid images; nothing to do."
            )
            return [], "selected (no valid images)"
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
            self.sb.message_box(
                "Save the .blend first — there is no project folder yet."
            )
            return
        if not os.path.isdir(path):
            # resolve_dir falls back to a `textures` name that need not exist yet.
            self.sb.message_box(f"Textures directory not found:<br><hl>{path}</hl>")
            return
        # ptk's cross-platform opener — the same helper the Maya slot uses (os.startfile is
        # Windows-only, and blendertk already depends on pythontk).
        ptk.FileUtils.open_explorer(path)

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
        mode = self._read_combo_mode(
            widget, "cmb_relocate_mode", self._RELOCATE_MODE_ITEMS
        )

        # Surface the active mode in the dialog title — last interaction before any file ops
        # fire. Matches the dynamic-text intent in Find & Copy.
        mode_hint = {
            "rewrite": "path only",
            "copy": "copy files",
            "move": "move files",
        }.get(mode, mode)
        # Same marker as Find & Copy's destination picker: another TARGET, so it
        # must not read as "pick a folder to look in".
        target_dir = self.sb.dir_dialog(
            title=(
                f"{self._DIALOG_MARK_DEST} DESTINATION — Set Texture Directory "
                f"({mode_hint}) for {scope_label}"
            ),
            start_dir=self._resolve_source_images_path(),
        )
        if not target_dir:
            return
        record = self._snapshot_for_tracking(images)
        count = btk.set_texture_directory(images, target_dir, mode=mode)
        record()
        self.sb.message_box(f"Updated <hl>{count}</hl>/{len(images)} texture path(s).")
        self.ui.tbl000.init_slot()

    def tb_find_and_copy_textures(self, widget=None):
        """Gather the images' textures, copy/move them into one destination, repath.

        Both option-box checkboxes exist to remove a dialog: Use Valid Paths drops the search
        prompt when nothing needs finding, Always Relocate To The Textures Folder drops the
        destination prompt.
        """
        images, _scope = self._get_scope_images()
        if not images:
            self.sb.message_box("No textures to process.")
            return
        mode = self._read_combo_mode(widget, "cmb_relocate_mode", self._FIND_MODE_ITEMS)
        self._find_and_copy_workflow(
            images,
            relocate_mode=mode,
            use_valid_paths=self._read_option_flag(widget, "chk_use_valid_paths", True),
            dest_sourceimages=self._read_option_flag(
                widget, "chk_dest_sourceimages", False
            ),
        )

    def _read_combo_mode(self, button, combo_name, mode_items):
        """Read a relocate/external combobox by index → mode key (safe default = first)."""
        try:
            idx = getattr(button.option_box.menu, combo_name).currentIndex()
        except AttributeError:
            return mode_items[0][1]
        return mode_items[idx][1] if 0 <= idx < len(mode_items) else mode_items[0][1]

    @classmethod
    def _read_option_flag(cls, button, name, default):
        """State of a checkbox in ``button``'s option box; ``default`` if absent."""
        return cls._menu_flag(
            getattr(getattr(button, "option_box", None), "menu", None), name, default
        )

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
        moved = (
            btk.normalize_texture_paths(external_mode, images=images)
            if external_mode in ("copy", "move")
            else 0
        )
        n = btk.normalize_texture_paths("relative", images=images)
        record()
        if moved or n:
            parts = []
            if moved:
                verb = "Copied" if external_mode == "copy" else "Moved"
                parts.append(f"{verb} <hl>{moved}</hl> external texture(s)")
            if n:
                parts.append(f"made <hl>{n}</hl> path(s) relative")
            msg = f"{'; '.join(parts)} ({scope_label})."
        else:
            msg = "Nothing changed — paths are already relative (or the .blend isn't saved)."
        self.sb.message_box(msg)
        self.ui.tbl000.init_slot()

    def make_paths_absolute(self):
        """Rewrite (selected, or all) ``//`` relative paths to absolute — inverse of
        Normalize Paths (engine ``normalize_texture_paths("absolute")``)."""
        import bpy

        # '//' resolves against the .blend itself; with no saved file Blender would
        # fall back to the process CWD and bake a bogus absolute path into the
        # datablock. Mirrors the Maya slot's unset-workspace guard.
        if not bpy.data.filepath:
            self.sb.message_box(
                "Save the .blend first — <hl>//</hl> paths have nothing to resolve against."
            )
            return
        images, scope_label = self._get_scope_images()
        if not images:
            self.sb.message_box("No textures to process.")
            return
        record = self._snapshot_for_tracking(images)
        n = btk.normalize_texture_paths("absolute", images=images)
        record()
        self.sb.message_box(
            f"Made <hl>{n}</hl> path(s) absolute ({scope_label})."
            if n
            else "Nothing changed — paths are already absolute (or the .blend isn't saved)."
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
            self.sb.message_box(
                "No Resolve Missing strategies enabled in the option-box."
            )
            return
        search_dir = self.sb.dir_dialog(
            title=(
                f"{self._DIALOG_MARK_SOURCE} SOURCE — SEARCH this folder (and subfolders) "
                f"for the missing texture(s)"
            ),
            start_dir=self._resolve_source_images_path(),
        )
        if not search_dir:
            return
        record = self._snapshot_for_tracking(images)
        n = btk.resolve_missing_textures(
            search_dir,
            stem=use_stem,
            texture=use_texture,
            fuzzy=use_fuzzy,
            images=images,
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
        self._select_rows_by_predicate(
            lambda img_name, path: img_name in missing, "broken paths"
        )

    def select_absolute_paths(self):
        """Select rows whose path is absolute (not a // project-relative path)."""
        self._select_rows_by_predicate(
            lambda img_name, path: (
                bool(path) and not path.startswith("//") and os.path.isabs(path)
            ),
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
            f"Selected <hl>{selected}</hl> {label}."
            if selected
            else f"No {label} found."
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
        # Open on the texture's own folder when it resolves, else the project's textures folder
        # (mirrors the Maya slot's start_dir fallback chain).
        current_dir = os.path.dirname(self._resolve_path(img.filepath) or "")
        start_dir = (
            current_dir
            if current_dir and os.path.isdir(current_dir)
            else self._resolve_source_images_path()
        )
        chosen = self.sb.file_dialog(
            file_types=[
                "*.png",
                "*.jpg",
                "*.jpeg",
                "*.tga",
                "*.tif",
                "*.tiff",
                "*.exr",
                "*.hdr",
                "*.bmp",
                "*.*",
            ],
            title=f"Select texture file for {img.name}",
            start_dir=start_dir,
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
            self.sb.message_box(
                f"Selected objects for <hl>{len(mat_names)}</hl> material(s)."
            )
        else:
            self.sb.message_box("No scene objects use the selected row's material(s).")

    def select_file_node(self, selection=None):
        """Select the Image Texture node(s) bound to the selected row's texture(s).

        Blender's answer to mayatk's *Select File Node*. Maya's ``file`` node is one object;
        Blender splits it into the image datablock (the row, which owns the path) and the
        ``ShaderNodeTexImage`` nodes referencing it — so a row maps to zero nodes (an unused
        texture), one, or many, and the count is worth reporting.
        """
        images = self._images_from_selection(selection)
        if not images:
            return
        count = btk.select_image_nodes(images)
        self.sb.message_box(
            f"Selected <hl>{count}</hl> Image Texture node(s)."
            if count
            else "No Image Texture node uses the selected texture(s)."
        )

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
        """Remove the selected texture datablock(s) from the .blend.

        Neither the file on disk nor the nodes using it are deleted: ``bpy.data.images.remove``
        drops only the datablock, and every ``ShaderNodeTexImage`` that referenced it survives
        with an empty texture slot. Both consequences are named in the confirm, because "Remove
        Texture" understates the second one and overstates the first.
        """
        import bpy

        images = self._images_from_selection(selection)
        if not images:
            return
        names = [i.name for i in images]
        orphaned = len(btk.image_texture_nodes(images))
        msg = (
            f"Remove the texture '{names[0]}' from the .blend?"
            if len(names) == 1
            else f"Remove {len(names)} textures from the .blend?"
        )
        if orphaned:
            msg += (
                f"<br><hl>{orphaned}</hl> Image Texture node(s) will be left with no texture."
            )
        msg += "<br>The file(s) on disk are not deleted."
        if self.sb.message_box(msg, "Yes", "No") != "Yes":
            return
        for img in images:
            try:
                bpy.data.images.remove(img)
            except (RuntimeError, ReferenceError) as e:
                self.logger.warning(f"Failed to remove image: {e}")
        self.sb.message_box(f"Removed <hl>{len(names)}</hl> texture(s).")
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------ cell editing
    def handle_cell_edit(self, row, col):
        """Editing a path cell repaths that row's texture; the Texture column renames the datablock."""
        import bpy

        table = self.ui.tbl000
        item = table.item(row, col)
        if not item:
            return
        new_value = item.text()
        img_item = table.item(row, 2)
        img_name = (
            (img_item.data(self.sb.QtCore.Qt.UserRole) or img_item.text())
            if img_item
            else None
        )
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
    def _find_and_copy_workflow(
        self,
        images,
        relocate_mode="copy",
        use_valid_paths=True,
        dest_sourceimages=False,
    ):
        """Run find/copy-or-move/repath with a re-entry guard.

        Modal dir dialogs occasionally deliver trailing release events that retrigger the slot,
        popping a second source-dir prompt. The guard protects against this (same pattern used by
        row_browse_for_file).
        """
        if getattr(self, "_find_copy_in_progress", False):
            return
        self._find_copy_in_progress = True
        try:
            self._do_find_and_copy_workflow(
                images,
                relocate_mode=relocate_mode,
                use_valid_paths=use_valid_paths,
                dest_sourceimages=dest_sourceimages,
            )
        finally:
            from qtpy.QtCore import QTimer

            QTimer.singleShot(
                250, lambda: setattr(self, "_find_copy_in_progress", False)
            )

    def _path_resolves(self, image):
        """True when the image's stored path already points at a file on disk.

        Resolved the way the engine resolves it (library-aware), so the count in the search
        dialog matches what ``find_and_copy_textures`` then treats as unresolved.
        """
        path = self._resolve_path(
            getattr(image, "filepath", "") or "",
            library=getattr(image, "library", None),
        )
        return bool(path) and os.path.isfile(path)

    def _do_find_and_copy_workflow(
        self,
        images,
        relocate_mode="copy",
        use_valid_paths=True,
        dest_sourceimages=False,
    ):
        """Collect sources, relocate them into one directory, repath the images.

        Two dialogs at most, and each is skippable: ``use_valid_paths`` sources every
        already-resolving image from its own path, so the *search* dialog only opens for what is
        unresolved (cancelling it skips exactly those, keeping the rest of the run);
        ``dest_sourceimages`` pins the destination to the project's textures folder, so the
        *destination* dialog never opens.

        Both dialogs are dir pickers on the same widget, one meaning "search here", the other
        "write here" — so their titles lead with SOURCE / DESTINATION and name the operation.
        Either can be the only dialog a run shows, and the title bar is all that tells them apart.
        """
        start_dir = self._resolve_source_images_path()
        verb = "MOVE" if relocate_mode == "move" else "COPY"

        unresolved = (
            [img for img in images if not self._path_resolves(img)]
            if use_valid_paths
            else list(images)
        )
        search_dir = None
        if unresolved:
            resolved_count = len(images) - len(unresolved)
            search_dir = self.sb.dir_dialog(
                title=(
                    f"{self._DIALOG_MARK_SOURCE} SOURCE — SEARCH this folder "
                    f"(and subfolders) for {len(unresolved)} unresolved texture(s)"
                    + ("   [Cancel = skip them]" if resolved_count else "")
                ),
                start_dir=start_dir,
            )
            # Cancel skips the unresolved images rather than aborting the run: with 48 of 50
            # paths valid, those 48 should still relocate. With nothing resolved there is no
            # run left to keep.
            if not search_dir and not resolved_count:
                return

        if dest_sourceimages:
            dest_dir = start_dir
            if not dest_dir:
                self.sb.message_box(
                    "'Always Relocate To The Textures Folder' is enabled but the project's "
                    "textures folder is unknown — save the .blend first."
                )
                return
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as e:
                self.sb.message_box(f"Cannot create <hl>{dest_dir}</hl>: {e}")
                return
        else:
            dest_dir = self.sb.dir_dialog(
                title=(
                    f"{self._DIALOG_MARK_DEST} DESTINATION — {verb} {len(images)} texture "
                    f"file(s) INTO this folder (this is the target, not a search folder)"
                ),
                start_dir=start_dir,
            )
            if not dest_dir:
                return

        record = self._snapshot_for_tracking(images)
        count = btk.find_and_copy_textures(
            images,
            search_dir,
            dest_dir,
            mode=relocate_mode,
            use_valid_paths=use_valid_paths,
        )
        record()
        self.sb.message_box(
            f"{'Moved' if relocate_mode == 'move' else 'Copied'} + repathed "
            f"<hl>{count}</hl> texture(s)."
            if count
            else "No textures to relocate — nothing resolved and nothing matched."
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
    def _resolve_path(path, library=None):
        """Resolve a raw path string (// project-relative included) to an absolute path.

        ``library`` is handed to ``bpy.path.abspath``: a datablock linked from a library .blend
        stores its ``//`` path relative to the LIBRARY file, so resolving it against the open
        file finds nothing. ``None`` (a local datablock) is a no-op — this mirrors the engine's
        own ``_abspath``, so the panel and ``find_and_copy_textures`` agree on what resolves.
        """
        import bpy

        try:
            return (
                os.path.normpath(bpy.path.abspath(path, library=library))
                if path
                else ""
            )
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
                resolver=self._footer_status_text,
                default_text="",
                truncate_kwargs={"length": 96, "mode": "middle"},
            )
        except Exception:
            return None

    def _footer_status_text(self) -> str:
        """Footer line: the texture directory every path command resolves against.

        Labelled, because a bare path in a status strip reads as "some path" — the label is
        what makes a wrong project obvious at a glance.

        The label is the resolved folder's own name, not a fixed word: the ``sourceImages``
        rule may map anywhere, and a footer reading TEXTURES over a path ending in
        ``/sourceimages`` would be the panel disagreeing with itself. Same derivation as
        mayatk's footer, so a project shared between the two reads identically in both.
        """
        path = self._resolve_source_images_path()
        if not path:
            return ""
        label = ptk.FileUtils.format_path(path, "dir").upper()
        return f"{label}: {path}" if label else path

    def _resolve_source_images_path(self) -> str:
        """The project's texture folder — Blender analogue of mayatk's
        ``EnvUtils.get_env_info("sourceimages")``: the workspace's ``sourceImages`` rule (or an
        existing ``sourceimages``/``textures`` folder), else the workspace root itself. Empty
        until the file has been saved (or a workspace is pinned)."""
        ws = btk.current_workspace()
        if ws is None:
            return ""
        return (
            ws.resolve_dir(("sourceImages",), ("sourceimages", "textures")) or ws.root
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("texture_path_editor", reload=True)
    ui.show(pos="screen", app_exec=True)
