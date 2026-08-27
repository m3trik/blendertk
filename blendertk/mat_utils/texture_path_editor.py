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
from functools import partial

import pythontk as ptk

import blendertk as btk


class TexturePathEditorSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Texture Path Editor panel."""

    # Table columns whose values a selection/context action reads (mirror of the Maya slot's
    # ``_ROW_SELECTION_COLUMNS`` — Maya's shader/path/file_node → Blender material/path/image).
    _ROW_SELECTION_COLUMNS = {"material": 0, "path": 1, "image": 2}

    #: Material-column label of a lightmap dependency row (no material, no
    #: image behind it -- the bake markers of the objects in the third column).
    _LIGHTMAP_ROW_LABEL = "<lightmap>"

    # Read-only class defaults for the lightmap state ``__init__`` sets: every
    # refresh / scope capture REASSIGNS them (never mutates in place), so a
    # driver built without ``__init__`` (the test harnesses) reads "none".
    _lightmap_rows: dict = {}
    _find_copy_lightmaps: tuple = ()

    # Set-Directory / Find-&-Copy relocate combobox items (label, mode-key). Order is the
    # contract — the combobox is populated in this order and the index maps back to the key.
    _RELOCATE_MODE_ITEMS = (
        ("Leave textures in place (path only)", "rewrite"),
        ("Copy textures to new directory", "copy"),
        ("Move textures to new directory", "move"),
    )
    # Colour markers leading the Find & Copy source / destination rows, and the Set Directory
    # picker's caption. Blue/amber rather than green/red: it survives the common colour-vision
    # deficiencies, and the words carry the meaning anyway — the glyph is redundancy, never the
    # only signal.
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
        # Find & Copy is a tool window that stays open while it works, so the
        # panel is KEPT: reopening it re-seeds the rows from live scene state
        # rather than throwing away the log the user is still reading and the
        # size they set. ``_find_copy_images`` is the scope it is currently
        # pointed at, captured when it opens (the row selection can change
        # behind a modeless window; a button that names a count must not
        # disagree with what it relocates). Mirrors mayatk's.
        self._find_copy_panel = None
        self._find_copy_images = []
        self._find_copy_mode = "copy"
        self._find_copy_scope_label = ""
        # Lightmap dependencies: the baked maps the bake markers name, which
        # no Image datablock references. ``_lightmap_rows`` maps a row's path
        # text to its dependency record (rebuilt with the table); the Find &
        # Copy scope carries its own list, captured with the images. Mirror
        # of mayatk's.
        self._lightmap_rows = {}
        self._find_copy_lightmaps = []
        self._footer_controller = self._create_footer_controller()
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[texture_path_editor] ")

    # ------------------------------------------------------------------ header menu
    def header_init(self, widget):
        """Build the header menu (General / Path Management / Selection) + help text.

        Plain action items are QPushButtons wired via ``clicked.connect``. The three items
        with per-button option-box flyouts (Set Directory, Normalize Paths, Resolve Missing
        Textures) are uitk ``PushButton`` (``tb_*``) auto-wired by name; their flyout
        contents are populated by the matching ``tb_*_init`` methods below. Find & Copy is a
        ``tb_*`` too but carries no flyout: every one of its options lives on its panel.
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

        chk_lightmaps = widget.menu.add(
            "QCheckBox",
            setText="Show Lightmap Dependencies",
            setObjectName="chk_show_lightmaps",
            setChecked=True,
            setToolTip=(
                "List the baked lightmaps the scene's bake markers name.\n"
                "A committed lightmap is a texture dependency with no Image "
                "datablock: the marker records the map and the folder it was "
                "baked into, and that folder goes stale when the project is "
                "reorganised or the scene is migrated — the export then ships "
                "unlit. Rows read red when the map is nowhere on disk and "
                "amber when it was found somewhere other than the recorded "
                "folder.\n"
                "Find & Copy relocates them with the textures and rewrites "
                "the markers; Normalize Paths / Make Paths Absolute re-spell "
                "the recorded folder (//-relative inside the project, so a "
                "teammate's copy on another drive resolves it); Select Broken "
                "Paths, Browse for File and a typed path apply too. Set "
                "Directory and Resolve Missing are image only."
            ),
        )
        chk_lightmaps.toggled.connect(lambda *_: self.refresh_texture_table())

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
            setToolTip=self.sb.tooltip.fmt(
                title="Find &amp; Copy Textures",
                body="Gather the files behind the (selected, or all) textures, "
                "relocate them into one destination, and repath. Paths become "
                "// relative when the destination is inside the .blend's own "
                "folder.",
                bullets=[
                    "Opens a panel with both folders on screen at once — "
                    "<b>Search in</b> and <b>Copy into</b>, each labelled — so "
                    "there is no order to remember and nothing to mistake one "
                    "for the other. Copy vs Move lives there too.",
                    "A path that already resolves is its own source, so the "
                    "search folder is only used for what is unresolved — "
                    "leave it empty and only the resolving paths relocate.",
                    "The panel stays open and reports into its own log, so a "
                    "second run with one value changed is one click away.",
                ],
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
                            "images use and relocate them into one destination. Opens a panel "
                            "carrying every option: Copy / Move, the folder to search, the "
                            "folder to write into, and whether to search for every texture or "
                            "only the unresolved ones.",
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
                    "Find &amp; Copy runs inside its own panel and reports there — the pane "
                    "at the bottom is the whole record of what it found, relocated and "
                    "repathed.",
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
        # Lightmap rows: the maps the bake markers name. Keyed by the path
        # text (the one column every selection payload carries) so the row
        # helpers can tell them from image rows; an EMPTY UserRole on the name
        # cells keeps the image commands from mistaking the label for a
        # datablock. Mirror of mayatk's.
        self._lightmap_rows = {
            self._lightmap_row_path(dep): dep for dep in self._lightmap_dependencies()
        }

        # Block signals across the rebuild — cellChanged is wired to handle_cell_edit, and
        # populating cells would otherwise fire it (spurious repath/rename on every refresh).
        widget.setUpdatesEnabled(False)
        widget.blockSignals(True)
        widget.clear()
        try:
            rows = []
            if not records and not self._lightmap_rows:
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
                for path, dep in self._lightmap_rows.items():
                    mat_label, node_label = self._lightmap_row_labels(dep)
                    # The path cell keeps its path in UserRole too: an edit
                    # replaces the text, and the cell-edit handler needs the
                    # row's identity to reach the record it repoints.
                    rows.append([(mat_label, ""), (path, path), (node_label, "")])
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
            dep = self._lightmap_rows.get(path)
            if dep is not None:
                # A lightmap row carries its own verdict: the engine already
                # resolved it the way the export will. Red = nowhere; amber =
                # found, but not where the marker says (stale hint -- the
                # export ships it and Resolve / Find & Copy heal it).
                found = dep.get("path")
                stale = bool(found) and dep.get("found_by") != "hint"
                widget.format_item(
                    item,
                    key="invalid" if not found else ("warning" if stale else "reset"),
                )
                objects = ", ".join(dep.get("objects") or [])
                if not found:
                    line = f"Missing lightmap:\n{path}"
                    if dep.get("note"):
                        line += f"\n{dep['note']}"
                elif stale:
                    line = f"Recorded folder no longer holds it; found at:\n{found}"
                else:
                    line = found
                item.setToolTip(
                    f"{line}\n\nBake marker on {len(dep.get('objects') or [])} "
                    f"object(s): {objects}"
                )
                return
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
            if self._lightmaps_from_selection(selection):
                # A lightmap-only selection is a valid scope for the commands
                # that take one; the image commands get an empty list and say
                # "nothing to do" themselves.
                return [], f"{len(selection)} selected lightmap row(s)"
            self.logger.warning(
                "Selected row(s) contain no valid images; nothing to do."
            )
            return [], "selected (no valid images)"
        images = [r["image"] for r in btk.get_image_records()]
        return images, f"all {len(images)} texture(s)"

    # ------------------------------------------------------------------ lightmap dependencies
    # Mirror of mayatk's: a committed lightmap is referenced by a bake marker
    # (map basename + the folder it was baked into), never by an Image
    # datablock, so every image command here was blind to it. The engine is
    # ``LightmapBaker`` (list / heal / relocate / repath / normalize); the
    # panel shows the records as rows and hands the relocation half to Find
    # & Copy.

    @staticmethod
    def _lightmap_baker():
        """The lightmap engine, imported on use (it drags the texture baker in)."""
        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        return LightmapBaker()

    def _show_lightmaps_enabled(self):
        """The header's "Show Lightmap Dependencies" toggle (default on)."""
        return self._menu_flag(self._header_menu(), "chk_show_lightmaps", True)

    def _lightmap_dependencies(self):
        """The scene's lightmap dependencies, or ``[]`` when the toggle hides them, the
        scene has none, or the engine cannot list them (a listing must never cost the
        table)."""
        if not self._show_lightmaps_enabled():
            return []
        try:
            return self._lightmap_baker().lightmap_dependencies()
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"Lightmap dependencies not listed: {e}")
            return []

    @staticmethod
    def _lightmap_row_path(dep):
        """The path a lightmap row shows: the marker's recorded folder + map (the STORED
        spelling, as the image rows show theirs; where it was actually found, when that is
        elsewhere, is the tooltip's job)."""
        folder = str(dep.get("dir") or "").replace("\\", "/").rstrip("/")
        return f"{folder}/{dep['map']}" if folder else dep["map"]

    def _lightmap_row_labels(self, dep):
        """``(material, node)`` labels for a lightmap row, read like a texture row.

        Material column: the material(s) the lightmapped objects wear -- an atlas by
        material is named after its material, and a per-object map still belongs to one.
        Texture column: the map's stem, what an Image datablock for it would be called
        (``OFFICE_ENV_LightMap``), since none exists. The objects themselves are the
        tooltip's: a row reading ``BASEBOARD_A (+45)`` identified nothing. Mirror of mayatk's.
        """
        import bpy

        mats = []
        for name in dep.get("objects") or []:
            obj = bpy.data.objects.get(name)
            for slot in getattr(obj, "material_slots", []) if obj is not None else []:
                if slot.material is not None and slot.material.name not in mats:
                    mats.append(slot.material.name)
        label = ", ".join(mats[:2])
        if len(mats) > 2:
            label += f" (+{len(mats) - 2})"
        return label or self._LIGHTMAP_ROW_LABEL, os.path.splitext(dep["map"])[0]

    def _lightmaps_from_selection(self, selection):
        """Lightmap dependency records behind ``selection`` (or the live row selection)."""
        table = getattr(self.ui, "tbl000", None)
        if selection is None and table is not None:
            selection = table.get_selection(
                columns=self._ROW_SELECTION_COLUMNS, include_current=True
            )
        found = []
        for entry in selection or []:
            path = str(self._selection_value(entry, "path") or "").strip()
            dep = self._lightmap_rows.get(path)
            if dep is not None and dep not in found:
                found.append(dep)
        return found

    def _get_scope_lightmaps(self):
        """Lightmap dependencies in scope, for the commands that take them.

        Mirrors :meth:`_get_scope_images`: the selected lightmap rows when the selection
        holds any (a selection of image rows alone takes no lightmaps along), otherwise every
        lightmap row the table shows -- so the header toggle that hides them also keeps them
        out of an "all" scope.
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
            return self._lightmaps_from_selection(selection)
        return list(self._lightmap_rows.values())

    @staticmethod
    def _lightmap_objects(lightmaps):
        """The object names the records name -- the engine's scope argument."""
        return list(
            dict.fromkeys(o for dep in lightmaps for o in (dep.get("objects") or []))
        )

    def _repath_lightmap(self, dep, folder):
        """Point every marker naming *dep*'s map at *folder*, then refresh.

        The manual counterpart of Find & Copy's relocation (Browse for File and a typed path
        on a lightmap row): files are not touched, the markers' recorded folder changes (in
        its portable spelling) and the FBX manifest is republished.
        """
        folder = str(folder or "").replace("\\", "/").rstrip("/")
        if not folder:
            self.sb.message_box("A lightmap path needs a folder.")
            return False
        try:
            count = self._lightmap_baker().repath_lightmaps(
                {dep["map"].lower(): folder}, dep.get("objects")
            )
        except Exception as e:  # noqa: BLE001
            self.sb.message_box(f"Failed to repath lightmap {dep['map']}: {e}")
            return False
        self.sb.message_box(
            f"<hl>{dep['map']}</hl>: lightmap folder -> {folder} ({count} marker(s))."
        )
        return True

    def _normalize_lightmaps(self, lightmaps, relative):
        """Re-spell the scoped lightmap markers' folders (Normalize / Make Absolute).

        Files never move: inside the project the recorded folder becomes ``//``-relative
        (``relative=True``) -- what lets a teammate's copy of the project, mounted elsewhere,
        still resolve it -- or is expanded to absolute (``relative=False``).
        """
        if not lightmaps:
            return 0
        try:
            count = self._lightmap_baker().normalize_lightmap_paths(
                self._lightmap_objects(lightmaps), relative=relative
            )
        except Exception as e:  # noqa: BLE001 — the texture half already ran
            self.logger.warning(f"Lightmap folders not rewritten: {e}")
            return 0
        if count:
            self.logger.info(
                f"{count} lightmap marker(s) now record their folder "
                f"{'relative to the project' if relative else 'as an absolute path'}."
            )
        return count

    def _browse_for_lightmap(self, dep):
        """Pick the file a lightmap row's markers should point at; repath them."""
        start_dir = os.path.dirname(dep["path"]) if dep.get("path") else ""
        if not (start_dir and os.path.isdir(start_dir)):
            start_dir = self._resolve_source_images_path()
        chosen = self.sb.file_dialog(
            file_types=["*.exr", "*.hdr", "*.png", "*.tif", "*.tiff", "*.*"],
            title=f"Select lightmap file {dep['map']}",
            start_dir=start_dir,
            filter_description="Lightmap Files",
            allow_multiple=False,
        )
        if not chosen:
            return
        if os.path.basename(chosen).lower() != dep["map"].lower():
            self.sb.message_box(
                f"Browse for File: the markers name <hl>{dep['map']}</hl>; pick that file "
                f"(chose {os.path.basename(chosen)}). A different map is a re-bake, not a "
                "repath."
            )
            return
        if self._repath_lightmap(dep, os.path.dirname(chosen)):
            self.ui.tbl000.init_slot()

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
        """Open the Find & Copy panel over the current scope.

        Every option lives on that one panel, so there is no option box: the mode, the
        search folder and the destination are read together, next to each other, at the
        moment they are used — and the panel stays up while it works, so what it did is on
        screen instead of behind a message box that has been dismissed.
        """
        images, scope = self._get_scope_images()
        lightmaps = self._get_scope_lightmaps()
        if not images and not lightmaps:
            self.sb.message_box("No textures to process.")
            return
        if lightmaps:
            scope = (f"{scope} + " if images else "") + f"{len(lightmaps)} lightmap(s)"
        self._find_and_copy_workflow(images, scope_label=scope, lightmaps=lightmaps)

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
        lightmaps = self._get_scope_lightmaps()
        if not images and not lightmaps:
            self.sb.message_box("No textures to process.")
            return

        external_mode = self._read_combo_mode(
            widget, "cmb_external_mode", self._NORMALIZE_MODE_ITEMS
        )
        record = self._snapshot_for_tracking(images)
        moved = (
            btk.normalize_texture_paths(external_mode, images=images)
            if images and external_mode in ("copy", "move")
            else 0
        )
        n = btk.normalize_texture_paths("relative", images=images) if images else 0
        record()
        # Lightmap rows: the marker's recorded folder takes the same portable
        # spelling (//-relative inside the project). Files never move.
        lm = self._normalize_lightmaps(lightmaps, relative=True)
        if moved or n or lm:
            parts = []
            if moved:
                verb = "Copied" if external_mode == "copy" else "Moved"
                parts.append(f"{verb} <hl>{moved}</hl> external texture(s)")
            if n:
                parts.append(f"made <hl>{n}</hl> path(s) relative")
            if lm:
                parts.append(f"re-spelled <hl>{lm}</hl> lightmap folder(s)")
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
        lightmaps = self._get_scope_lightmaps()
        if not images and not lightmaps:
            self.sb.message_box("No textures to process.")
            return
        record = self._snapshot_for_tracking(images)
        n = btk.normalize_texture_paths("absolute", images=images) if images else 0
        record()
        lm = self._normalize_lightmaps(lightmaps, relative=False)
        parts = []
        if n:
            parts.append(f"Made <hl>{n}</hl> path(s) absolute")
        if lm:
            parts.append(f"re-spelled <hl>{lm}</hl> lightmap folder(s)")
        self.sb.message_box(
            f"{'; '.join(parts)} ({scope_label})."
            if parts
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
        # A lightmap row is broken when the engine found its map nowhere (a
        # stale-but-found hint is amber, not broken -- it ships).
        self._select_rows_by_predicate(
            lambda img_name, path: (
                not self._lightmap_rows[path].get("path")
                if path in self._lightmap_rows
                else img_name in missing
            ),
            "broken paths",
        )

    def select_absolute_paths(self):
        """Select rows whose path is absolute (not a // project-relative path)."""
        # Lightmap rows qualify by their recorded folder's spelling, the same
        # test: an absolute folder inside the project is a Normalize candidate.
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
        lightmaps = self._lightmaps_from_selection(selection)
        if lightmaps:
            # A lightmap row: the file picked names the folder the markers
            # should record (the map itself is what the bake committed, so a
            # different basename is refused rather than silently rebound).
            if len(lightmaps) > 1 or self._images_from_selection(selection):
                self.sb.message_box("Browse for File: select a single row.")
                return
            self._browse_for_lightmap(lightmaps[0])
            return

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
        lightmaps = self._lightmaps_from_selection(selection)
        if lightmaps and not images:
            # A lightmap row selects the objects carrying its bake markers.
            names = self._lightmap_objects(lightmaps)
            objects = [o for o in (bpy.data.objects.get(n) for n in names) if o]
            for obj in objects:
                obj.select_set(True)
            if objects and bpy.context.view_layer:
                bpy.context.view_layer.objects.active = objects[0]
            self.sb.message_box(
                f"Selected <hl>{len(objects)}</hl> lightmapped object(s)."
                if objects
                else "No scene objects carry the selected row's bake marker."
            )
            return
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
            msg += f"<br><hl>{orphaned}</hl> Image Texture node(s) will be left with no texture."
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
        # A lightmap row: the path cell repoints the bake markers (folder only
        # -- the map is what the bake committed); the name cells are labels,
        # not datablocks. Identified through the path cell's UserRole, which
        # still holds the row's path after the text was edited.
        path_item = table.item(row, 1)
        row_key = (
            str(path_item.data(self.sb.QtCore.Qt.UserRole) or path_item.text()).strip()
            if path_item is not None
            else ""
        )
        lightmap = self._lightmap_rows.get(row_key)
        if lightmap is not None:
            if col != 1:
                self.sb.message_box("Lightmap rows carry no datablock to rename.")
                # Deferred: this runs inside cellChanged, and rebuilding the
                # table from within its own signal would delete the item
                # mid-dispatch.
                self.sb.QtCore.QTimer.singleShot(0, self.refresh_texture_table)
                return
            typed = new_value.strip().replace("\\", "/")
            if typed and os.path.basename(typed).lower() != lightmap["map"].lower():
                self.sb.message_box(
                    f"The bake markers name <hl>{lightmap['map']}</hl>; a path to a "
                    "different map is a re-bake, not a repath."
                )
                self.sb.QtCore.QTimer.singleShot(0, self.refresh_texture_table)
                return
            self._repath_lightmap(lightmap, os.path.dirname(typed))
            self.sb.QtCore.QTimer.singleShot(0, self.refresh_texture_table)
            return

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
        self, images, relocate_mode="copy", scope_label="", lightmaps=None
    ):
        """Open the Find & Copy panel over *images*, or re-seed and raise it.

        *lightmaps* are the lightmap dependency records in scope
        (:meth:`_get_scope_lightmaps`); they get their own opt-out row on the form and
        are relocated after the textures, by the same folders.

        The panel is kept between invocations rather than rebuilt: it is a tool window that
        stays open while the operation runs, so closing and recreating it would throw away
        the report being read and the size the user set. Every invocation re-seeds the rows,
        so the counts and hints always describe the scope the command was just issued for.

        The scope is captured HERE, not re-read when Run is pressed: the row selection can
        change behind a modeless window, and a button reading "Copy 12 texture(s)" that
        relocates three is the exact class of mismatch this panel exists to remove.
        Mirrors mayatk's ``_find_and_copy_workflow``.
        """
        self._find_copy_images = list(images)
        self._find_copy_mode = relocate_mode
        self._find_copy_scope_label = scope_label
        self._find_copy_lightmaps = list(lightmaps or [])

        panel = self._find_copy_panel
        if panel is None:
            panel = self.sb.form_panel(
                self._find_and_copy_scope_fields(),
                title="Find & Copy Textures",
                parent=getattr(self, "ui", None),
                # Callable: the mode lives ON the form, so a fixed verb here would
                # contradict the combo the moment it is changed.
                ok_text=self._find_and_copy_ok_text,
                validate=self._validate_find_and_copy,
                help_text=self._find_and_copy_help_text(),
                on_run=self._run_find_and_copy,
                # A tool window the user sizes once and reopens all week: the
                # size it comes back at is part of being a panel rather than
                # a dialog.
                settings=self._find_copy_settings(),
            )
            self._find_copy_panel = panel
        else:
            panel.set_fields(self._find_and_copy_scope_fields())

        panel.footer.setDefaultStatusText(
            f"Scope: {scope_label}" if scope_label else ""
        )
        panel.present()
        return panel

    def _path_resolves(self, image):
        """True when the image's stored path already points at a file on disk.

        Resolved the way the engine resolves it (library-aware), so the count the panel
        shows matches what ``find_and_copy_textures`` then treats as unresolved.
        """
        path = self._resolve_path(
            getattr(image, "filepath", "") or "",
            library=getattr(image, "library", None),
        )
        return bool(path) and os.path.isfile(path)

    def _find_copy_settings(self):
        """The panel's geometry store, or None when the switchboard has none.

        ``getattr``: the slot is constructed against a real Switchboard in
        production and against a stand-in in tests, and a panel that cannot
        remember its size is a smaller loss than a tool that will not open.
        """
        store = getattr(self.sb, "settings", None)
        if store is None:
            return None
        return store.branch("find_and_copy_textures")

    def _find_and_copy_scope_fields(self):
        """Field specs for the scope the panel is currently pointed at.

        Re-partitions on every call rather than caching: reopening the panel after a Resolve
        Missing pass must not still claim the textures it just fixed are unresolved.
        """
        images = self._find_copy_images
        return self._find_and_copy_fields(
            images,
            [img for img in images if not self._path_resolves(img)],
            self._resolve_source_images_path(),
            self._find_copy_mode,
            lightmaps=self._find_copy_lightmaps,
        )

    def _find_and_copy_fields(
        self, images, unresolved, start_dir, relocate_mode="copy", lightmaps=None
    ):
        """The Find & Copy rows: operation, search folder, destination, dry run.

        *lightmaps* (dependency records in scope) count toward the search hint: a missing
        lightmap is as much "something to find" as a broken image, and the search folder
        must switch on for it. They ride along unconditionally -- the scope decides.

        Mirrors mayatk's, down to the field names and the validator, so a project shared
        between the two reads the same. See that one for why the sequence of two native
        pickers had to go.

        Pure: no Qt, no scene writes. The panel renders whatever this returns, so what the
        form SAYS is testable on its own.

        Returns:
            list[dict]: specs for ``sb.form_panel`` — ``mode``, ``source_dir``,
            ``dest_dir``, ``dry_run``.
        """
        total = len(images)
        resolved_count = total - len(unresolved)
        mode_labels = {key: label for label, key in self._FIND_MODE_ITEMS}
        initial_mode = mode_labels.get(relocate_mode, self._FIND_MODE_ITEMS[0][0])

        # What the search folder is FOR: the images that do not resolve and
        # the lightmaps found nowhere -- one list, since one folder serves
        # both and the row must switch on for either. Mirrors mayatk.
        wanted = [
            os.path.basename(img.filepath or "") or img.name for img in unresolved
        ]
        wanted.extend(d["map"] for d in (lightmaps or []) if not d.get("path"))
        if wanted:
            listed = ", ".join(wanted[:3])
            if len(wanted) > 3:
                listed += f", +{len(wanted) - 3} more"
            skipped = "Leave it empty to skip them"
            if resolved_count:
                skipped += f" and relocate the {resolved_count} that already resolve"
            source_hint = (
                f"Searched recursively for {len(wanted)} unresolved texture(s): {listed}. "
                f"{skipped}. A path that already resolves is its own source and is never "
                "searched for — it is the file the scene is rendering."
            )
            # Short enough to survive a narrow field; counted off ``wanted``, never off
            # ``total`` (a lightmap-only scope has no image datablocks). Mirrors mayatk.
            source_placeholder = f"{len(wanted)} path(s) require a search dir"
        else:
            source_hint = (
                f"All {total} path(s) resolve — every texture is its own source, so nothing "
                "needs finding."
                if total
                else "Nothing in scope needs finding — every path already resolves."
            )
            source_placeholder = "No path requires a search dir"

        # Order is the reading order of the decision: what to do, where to look, where it
        # lands, and whether to commit. Mirrors mayatk's.
        return [
            {
                "name": "mode",
                "kind": "choice",
                "label": "Operation",
                "items": [label for label, _key in self._FIND_MODE_ITEMS],
                "value": initial_mode,
                "hint": (
                    "Copy duplicates each texture into the destination; Move removes the "
                    "original after a successful copy."
                ),
            },
            {
                "name": "source_dir",
                "kind": "dir",
                "label": f"{self._DIALOG_MARK_SOURCE} Search in",
                "hint": source_hint,
                "placeholder": source_placeholder,
                "enabled": bool(wanted),
            },
            {
                "name": "dest_dir",
                "kind": "dir",
                "label": f"{self._DIALOG_MARK_DEST} Copy into",
                "value": start_dir,
                "hint": (
                    f"The {total} texture(s) land HERE and the images are repointed at "
                    "them. Paths become // relative when this folder is inside the project."
                ),
            },
            {
                "name": "dry_run",
                "kind": "check",
                "label": "Dry run (preview only)",
                "hint": (
                    "Report exactly what would be relocated and repathed without touching a "
                    "file or a datablock, then arm <b>Apply</b> to commit the report on "
                    "screen. Preview and commit derive every source and destination through "
                    "the same engine call, so what Apply writes is what was previewed."
                ),
            },
        ]

    def _find_and_copy_ok_text(self, values) -> str:
        """Accept-button text — the verb chosen ON the form, and the real count.

        Ticking Dry Run retitles it to what it will actually do, which is not copy
        anything.
        """
        total = len(self._find_copy_images)
        lightmaps = len(self._find_copy_lightmaps)
        what = f"{total} texture(s)"
        if lightmaps:
            what = (
                f"{what} + {lightmaps} lightmap(s)"
                if total
                else f"{lightmaps} lightmap(s)"
            )
        if values.get("dry_run"):
            return f"Preview {what}"
        mode = values.get("mode") or self._FIND_MODE_ITEMS[0][0]
        return f"{mode} {what}"

    def _find_and_copy_help_text(self) -> str:
        """Rich text behind the panel header's ``?``."""
        return self.sb.tooltip.fmt(
            title="Find &amp; Copy Textures",
            body="Gather the files behind the scoped textures, relocate them into one "
            "destination, and repoint the images at them.",
            sections=[
                (
                    "The rows",
                    [
                        "<b>Operation</b> — Copy duplicates each texture; Move removes the "
                        "original after a successful copy.",
                        "<b>Search in</b> — searched recursively, and only for paths that "
                        "do not already resolve. Leave it empty to skip those and relocate "
                        "the rest.",
                        "<b>Copy into</b> — where the textures land. Created if it does not "
                        "exist; paths become // relative when it is inside the project.",
                        "<b>Dry run</b> — report what would happen without touching "
                        "anything, then press <b>Apply</b> in the footer to commit exactly "
                        "what was reported.",
                        "<b>Lightmaps</b> — any in the scope ride along: the baked maps "
                        "the bake markers name (no Image datablock references them, so "
                        "this is the one command that relocates them). Searched and "
                        "copied like the textures, then every marker is repointed at "
                        "the destination and the FBX manifest republished.",
                    ],
                ),
            ],
            notes=[
                "Source and destination may not be the same folder — nothing would move, "
                "and the run would report success.",
                "What the search did not find is named at the end of the report; those "
                "images keep their current path.",
            ],
        )

    def _run_find_and_copy(self, values):
        """The panel's Run — over whatever scope the panel is pointed at."""
        return self._run_find_and_copy_over(list(self._find_copy_images), values)

    def _run_find_and_copy_over(self, images, values):
        """Preview or commit *images*, reporting into the panel.

        Returns the call that WOULD commit when this pass was a preview, which is the
        panel's contract for arming its Apply button. That call is this same method with
        the preview switched off, over the images and answers AS THEY WERE PREVIEWED — so
        Apply commits the report on screen even if the row selection or the form has moved
        on since. Mirrors mayatk's.
        """
        panel = self._find_copy_panel
        dry_run = bool(values.get("dry_run"))
        # Adopt the panel's logger for the duration: every ``self.logger`` call in the
        # command then lands in the pane the user is watching, instead of a channel with
        # no sink attached to this window.
        self.use_logger(getattr(panel, "logger", None))
        try:
            planned = self._execute_find_and_copy(images, values, dry_run=dry_run)
        finally:
            self.use_logger(None)
        self.ui.tbl000.init_slot()
        if not (dry_run and planned):
            return None
        return partial(
            self._run_find_and_copy_over, images, dict(values, dry_run=False)
        )

    @staticmethod
    def _validate_find_and_copy(values):
        """Refuse a form that cannot do what it says, and say why.

        The reported mistake IS the first rule: aiming the destination at the folder being
        searched relocates nothing, reports success, and leaves the user believing the
        textures moved.
        """
        dest = values.get("dest_dir") or ""
        source = values.get("source_dir") or ""
        if not dest:
            return "Pick a destination — this is where the textures will land."
        if source and os.path.normcase(os.path.abspath(source)) == os.path.normcase(
            os.path.abspath(dest)
        ):
            return (
                "Search folder and destination are the same — nothing would move. The "
                "destination is where the textures LAND."
            )
        return ""

    def _execute_find_and_copy(self, images, answers, dry_run=False):
        """Collect sources, relocate them into one directory, repath the images.

        The answers arrive as a plain dict — from the panel, from a test, from anything
        that can name the values — so the work is reachable and checkable without a window
        in front of it. An empty source row skips the unresolved images and relocates the
        rest: with 48 of 50 paths valid, those 48 must not be lost to the two that are
        broken.

        ``dry_run`` reports the same decision without acting on it: the search still runs
        (reading the disk is the only way to know what would move), but no folder is
        created, no file is relocated and no datablock is touched. Both passes get their
        sources and destinations from ``btk.plan_find_and_copy_textures``, so a preview
        cannot promise a path the commit would not write.

        Reports through ``self.logger``, which the panel's Run swaps for its own — the pane
        IS the report, so there is no message box to dismiss before reading it.

        Parameters:
            images: The images to relocate textures for.
            answers: ``source_dir``, ``dest_dir``, ``mode``, ``dry_run``.
            dry_run: Report the plan; change nothing.

        Returns:
            bool: whether there was anything to do — the signal the panel's Apply button
            is armed from, so a preview that found nothing offers nothing to commit.
        """
        search_dir = (answers.get("source_dir") or "") or None
        dest_dir = answers.get("dest_dir") or ""
        # Normalized HERE rather than on the way out of the form: the same dict arrives
        # from the panel and from a test, and a mode read straight off the combo
        # ("Copy" / "Move") has to mean the same thing on every path in.
        relocate_mode = (
            "move" if str(answers.get("mode", "")).lower() == "move" else "copy"
        )
        # The engine's ``use_valid_paths`` default: a path that resolves is the
        # file the scene is rendering, so it is its own source and the search
        # folder only covers what does not resolve. Callable the other way
        # programmatically; the panel does not offer it.

        # The lightmaps captured with this scope ride along unconditionally:
        # the scope decides what is handled, whatever the paths may be.
        lightmaps = list(self._find_copy_lightmaps)
        plan = btk.plan_find_and_copy_textures(images, search_dir, dest_dir)

        # Name what the search did NOT find, by image (mirror of mayatk,
        # reported 2026-08-26): a run that found 46 of 48 read as a success --
        # the counts were right, but nothing said WHICH two kept their broken
        # path -- and the export then failed on textures the panel had
        # "just copied".
        unresolved = [img for img in images if not self._path_resolves(img)]
        if unresolved and search_dir:
            planned = {img for r in plan for img in r["images"]}
            not_found = [img for img in unresolved if img not in planned]
            if not_found:
                self.logger.log_group(
                    f"{len(not_found)} of {len(unresolved)} unresolved texture(s) not "
                    f"found under {search_dir} — their images keep their current path",
                    [
                        f"{img.name}:  {os.path.basename(img.filepath or '') or '<no path>'}"
                        for img in not_found
                    ],
                    level="warning",
                )

        if dry_run:
            return self._report_find_and_copy_plan(
                plan,
                dest_dir,
                relocate_mode,
                lightmap_plan=self._plan_lightmaps(
                    lightmaps, search_dir, dest_dir, relocate_mode
                ),
            )

        # Created if missing: the destination is typed as often as it is browsed, and a folder
        # that does not exist yet is a normal answer to "put them here".
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            self.logger.error(f"Cannot create '{dest_dir}': {e}")
            return False

        record = self._snapshot_for_tracking(images)
        count = (
            btk.find_and_copy_textures(images, search_dir, dest_dir, mode=relocate_mode)
            if images
            else 0
        )
        record()
        if count:
            self.logger.success(
                f"{'Moved' if relocate_mode == 'move' else 'Copied'} + repathed "
                f"{count} texture(s)."
            )
        elif images:
            self.logger.warning(
                "No textures to relocate — nothing resolved and nothing matched."
            )

        landed = (
            self._relocate_lightmaps(lightmaps, search_dir, dest_dir, relocate_mode)
            if lightmaps
            else False
        )

        # The honest close: what is STILL broken after this run, by image.
        if images:
            still = [img for img in images if not self._path_resolves(img)]
            if still:
                self.logger.log_group(
                    f"{len(still)} texture(s) still unresolved after this run — the "
                    "export's path check will fail on them",
                    [f"{img.name}:  {img.filepath or '<no path>'}" for img in still],
                    level="warning",
                )
        return bool(count) or landed

    #: Rows of a dry-run listing shown in full before it collapses to a count. Long enough
    #: to recognise the operation, short enough that the plan stays one screenful next to
    #: the form that produced it. Mirrors mayatk's.
    _PLAN_PREVIEW_ROWS = 12

    def _report_find_and_copy_plan(
        self, plan, dest_dir, relocate_mode, lightmap_plan=None
    ):
        """Report what a live pass WOULD do, having written nothing.

        Every line comes from the engine's own plan — the same records the commit acts on —
        and the lightmap plan the engine's own dry run returned, so this is the plan itself
        being described, not a second guess at it.
        """
        verb = "Move" if relocate_mode == "move" else "Copy"
        lightmaps_change = bool(
            lightmap_plan and (lightmap_plan["relocate"] or lightmap_plan["in_place"])
        )
        if not plan and not lightmaps_change:
            self.logger.warning(
                "Dry run — nothing would change: nothing resolved and nothing matched."
            )
            self._report_lightmap_plan(lightmap_plan, dest_dir, verb)
            return False
        if not plan:
            self._report_lightmap_plan(lightmap_plan, dest_dir, verb)
            self.logger.warning(
                f"Nothing has been written — press Apply to {verb.lower()} and repath "
                "exactly this."
            )
            return True

        moving = [r for r in plan if not r["in_place"]]
        in_place = [r for r in plan if r["in_place"]]

        if moving:
            self.logger.log_group(
                f"Dry run — would {verb.lower()} {len(moving)} texture(s) into {dest_dir}"
                + (f" ({len(in_place)} already there)" if in_place else ""),
                self._plan_lines(
                    f"{os.path.basename(r['source'])}  ←  {os.path.dirname(r['source'])}"
                    for r in moving
                ),
            )
        else:
            self.logger.info(
                f"Dry run — all {len(in_place)} texture(s) are already at the destination; "
                "only the stored paths would change."
            )

        repathed = [(img, r) for r in plan for img in r["images"]]
        self.logger.log_group(
            f"Would repath {len(repathed)} image(s)",
            self._plan_lines(
                f"{img.name}:  {img.filepath}  →  "
                f"{btk.to_project_relative(r['destination'])}"
                for img, r in repathed
            ),
        )
        self._report_lightmap_plan(lightmap_plan, dest_dir, verb)
        self.logger.warning(
            f"Nothing has been written — press Apply to {verb.lower()} and repath exactly "
            "this."
        )
        return True

    # -- lightmaps through Find & Copy (mirror of mayatk) ----------------------
    # The engine does the work (LightmapBaker.relocate_lightmaps: search, copy,
    # repoint the markers, republish the manifest); the panel scopes it to the
    # captured records and reports through the same pane.

    def _plan_lightmaps(self, lightmaps, source_dir, dest_dir, relocate_mode):
        """The engine's dry run over *lightmaps*, or ``None`` when none are in scope."""
        if not lightmaps:
            return None
        try:
            return self._lightmap_baker().relocate_lightmaps(
                dest_dir,
                source_dir=source_dir or "",
                mode=relocate_mode,
                objects=self._lightmap_objects(lightmaps),
                dry_run=True,
            )
        except Exception as e:  # noqa: BLE001 — a preview must not raise
            self.logger.error(f"Lightmaps not planned: {e}")
            return None

    def _report_lightmap_plan(self, plan, dest_dir, verb):
        """The dry-run lines for the lightmaps -- same shape as the texture ones."""
        if not plan:
            return
        if plan["relocate"]:
            self.logger.log_group(
                f"Dry run — would {verb.lower()} {len(plan['relocate'])} lightmap(s) into "
                f"{dest_dir} and repoint their bake markers"
                + (
                    f" ({len(plan['in_place'])} already there)"
                    if plan["in_place"]
                    else ""
                ),
                self._plan_lines(
                    f"{os.path.basename(src)}  ←  {os.path.dirname(src)}"
                    for src, _dst in plan["relocate"]
                ),
            )
        elif plan["in_place"]:
            self.logger.info(
                f"Dry run — all {len(plan['in_place'])} lightmap(s) are already at the "
                "destination; only the bake markers would change."
            )
        if plan["missing"]:
            self.logger.log_group(
                f"{len(plan['missing'])} lightmap(s) found nowhere — their markers would "
                "keep pointing at the recorded folder",
                [
                    f"{dep['map']}  (recorded in {dep['dir'] or '<no folder>'})"
                    + (f"  {dep['note']}" if dep.get("note") else "")
                    for dep in plan["missing"]
                ],
                level="warning",
            )

    def _relocate_lightmaps(self, lightmaps, source_dir, dest_dir, relocate_mode):
        """Relocate *lightmaps* for real and report; returns whether any landed."""
        try:
            result = self._lightmap_baker().relocate_lightmaps(
                dest_dir,
                source_dir=source_dir or "",
                mode=relocate_mode,
                objects=self._lightmap_objects(lightmaps),
            )
        except Exception as e:  # noqa: BLE001 — the textures already landed
            self.logger.error(f"Lightmaps not relocated: {e}")
            return False
        landed = len(result["copied"]) + len(result["in_place"])
        if landed:
            self.logger.success(
                f"Lightmaps — {len(result['copied'])} relocated, "
                f"{len(result['in_place'])} already at destination; "
                f"{result['updated']} bake marker(s) repointed, manifest republished."
            )
        failed = len(result["relocate"]) - len(result["copied"])
        if failed:
            self.logger.warning(
                f"{failed} lightmap(s) did not copy — see the system console."
            )
        if result["missing"]:
            self.logger.log_group(
                f"{len(result['missing'])} lightmap(s) found nowhere — the export's path "
                "check will fail on them",
                [
                    f"{dep['map']}  (recorded in {dep['dir'] or '<no folder>'})"
                    + (f"  {dep['note']}" if dep.get("note") else "")
                    for dep in result["missing"]
                ],
                level="warning",
            )
        return bool(landed)

    @classmethod
    def _plan_lines(cls, lines):
        """*lines* capped at :attr:`_PLAN_PREVIEW_ROWS`, with the remainder counted.

        A truncated listing that does not SAY it was truncated reads as the whole plan,
        which is the one thing a preview must never do.
        """
        listed = list(lines)
        if len(listed) <= cls._PLAN_PREVIEW_ROWS:
            return listed
        hidden = len(listed) - cls._PLAN_PREVIEW_ROWS
        return listed[: cls._PLAN_PREVIEW_ROWS] + [f"… and {hidden} more"]

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
