# !/usr/bin/python
# coding=utf-8
"""Texture Path Editor tool panel — Switchboard slot wiring for the co-located
``texture_path_editor.ui``.

Blender counterpart of mayatk's Texture Path Editor, mirroring its structure: a header menu
(General / Path Management / Selection) over a three-column table (**Material · Texture Path ·
Image**) whose path cells are editable, with a per-row right-click menu. Maya file-node concepts
map onto Blender image datablocks — a row is one FILE image (``img.filepath`` is the path); the
Material column lists the material(s) whose shader graph references it. The path commands operate
on the selected rows if any, otherwise on every image (the same selection-aware scope as Maya).

Maya-only concepts that don't map are dropped (with a note): Maya's *namespaces* / separable
*file-node* selection (Blender has no node-name handle distinct from the image), the *Hypershade*
graph (→ Blender's Shader Editor), and *sourceimages* as the implicit search root (Blender has no
project workspace — Set Directory / Find & Copy / Resolve Missing prompt for a folder instead).

The engine lives in ``blendertk.MatUtils`` (``get_image_records`` / ``repath_image`` /
``resolve_missing_textures`` / ``normalize_texture_paths`` / ``reload_textures`` /
``select_by_material``); this is the thin Qt driver. Self-contained (``ptk.LoggingMixin`` only) so
blendertk carries no back-dependency on tentacle; ``import bpy`` and the Qt-only ``uitk`` helpers
are deferred into the call bodies (headless Blender ships no Qt binding).
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
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[texture_path_editor] ")

    # ------------------------------------------------------------------ header menu
    def header_init(self, widget):
        """Build the header menu (General / Path Management / Selection) + help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self.refresh_texture_table)

        widget.menu.add("Separator", setTitle="General")
        btn_open = widget.menu.add(
            "QPushButton", setText="Open Textures Folder",
            setObjectName="btn_open_textures_folder",
            setToolTip="Open the project's textures directory (<blend>/textures) in the file explorer.",
        )
        btn_open.clicked.connect(self.open_textures_folder)
        btn_reload = widget.menu.add(
            "QPushButton", setText="Reload Scene Textures",
            setObjectName="btn_reload_scene_textures",
            setToolTip="Force Blender to re-read every image datablock from disk.",
        )
        btn_reload.clicked.connect(self.reload_scene_textures)

        widget.menu.add("Separator", setTitle="Path Management")
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Set Directory…", setObjectName="tb_set_texture_directory",
            setToolTip="Repath every (selected, or all) image so its file lives under the chosen "
            "directory. The option box (▸) chooses leave / copy / move.",
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Find && Copy Textures…", setObjectName="tb_find_and_copy_textures",
            setToolTip="Search a folder recursively for the textures used by (selected, or all) "
            "images, relocate them into a destination, and repath. Option box (▸) toggles Copy / Move.",
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Normalize Paths", setObjectName="tb_normalize_paths",
            setToolTip="Rewrite (selected, or all) paths relative to the saved .blend (// paths). "
            "The option box (▸) controls external textures: leave / copy / move into the project.",
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Resolve Missing Textures", setObjectName="tb_resolve_missing_textures",
            setToolTip="Search a folder (recursively) for replacement files for missing (selected, "
            "or all) textures, matched by name.",
        )

        widget.menu.add("Separator", setTitle="Selection")
        btn_sel_obj = widget.menu.add(
            "QPushButton", setText="Select Textures for Selected Objects",
            setObjectName="btn_select_textures_for_objects",
            setToolTip="Highlight the rows for textures used by the materials on the current scene selection.",
        )
        btn_sel_obj.clicked.connect(self.select_textures_for_objects)
        btn_sel_broken = widget.menu.add(
            "QPushButton", setText="Select Broken Paths",
            setObjectName="btn_select_broken_paths",
            setToolTip="Highlight rows whose texture file is missing on disk.",
        )
        btn_sel_broken.clicked.connect(self.select_broken_paths)
        btn_sel_abs = widget.menu.add(
            "QPushButton", setText="Select Absolute Paths",
            setObjectName="btn_select_absolute_paths",
            setToolTip="Highlight rows whose path is absolute (candidates for Normalize Paths).",
        )
        btn_sel_abs.clicked.connect(self.select_absolute_paths)

        widget.set_help_text(
            fmt(
                title="Texture Path Editor",
                body="Inspect and fix image texture paths. Path commands operate on selected rows "
                "if any, otherwise on every image in the file.",
                sections=[
                    ("Path management (header menu)", [
                        "<b>Set Directory…</b> — repath to a chosen folder. Option box (▸): leave / copy / move.",
                        "<b>Find &amp; Copy Textures…</b> — search a folder for matching textures, copy or move them.",
                        "<b>Normalize Paths</b> — rewrite paths relative to the saved .blend. Option box (▸) handles external textures.",
                        "<b>Resolve Missing Textures</b> — search a folder for replacements for ⚠ textures (by name).",
                    ]),
                    ("Selection helpers (header menu)", [
                        "<b>Select Textures for Selected Objects</b> — rows for textures on the current selection.",
                        "<b>Select Broken Paths</b> — rows whose file is missing.",
                        "<b>Select Absolute Paths</b> — rows with absolute paths.",
                    ]),
                ],
                notes=[
                    "⚠ marks a path that does not exist on disk. Edit a path cell directly to repath that image.",
                    "<b>Right-click</b> a row for per-texture actions: Browse for File, Select In Scene, Shader Editor, Delete Image.",
                    "Make Relative / Copy to Project need the .blend to be saved.",
                ],
            )
        )

    def tb_set_texture_directory_init(self, widget):
        """Set Directory option-box: the relocate-mode combobox."""
        widget.option_box.menu.setTitle("Set Directory")
        widget.option_box.menu.add(
            "QComboBox", setObjectName="cmb_relocate_mode",
            addItems=[label for label, _key in self._RELOCATE_MODE_ITEMS],
            setToolTip="Behavior for the texture files when the directory changes:\n"
            "• Leave in place — only rewrite the path.\n"
            "• Copy — duplicate each texture into the chosen directory.\n"
            "• Move — relocate each texture into the chosen directory.",
        )

    def tb_find_and_copy_textures_init(self, widget):
        """Find & Copy option-box: the copy/move combobox (also swaps the button text)."""
        widget.option_box.menu.setTitle("Find & Copy Textures")
        cmb = widget.option_box.menu.add(
            "QComboBox", setObjectName="cmb_relocate_mode",
            addItems=[label for label, _key in self._FIND_MODE_ITEMS],
            setToolTip="How to relocate matched textures into the destination (Copy or Move).",
        )

        def _sync_text(idx):
            label = self._FIND_MODE_ITEMS[idx][0] if 0 <= idx < len(self._FIND_MODE_ITEMS) else "Copy"
            widget.setText(f"Find && {label} Textures…")

        cmb.currentIndexChanged.connect(_sync_text)
        _sync_text(cmb.currentIndex())

    def tb_normalize_paths_init(self, widget):
        """Normalize Paths option-box: the external-texture-mode combobox."""
        widget.option_box.menu.setTitle("Normalize Paths")
        widget.option_box.menu.add(
            "QComboBox", setObjectName="cmb_external_mode",
            addItems=[label for label, _key in self._NORMALIZE_MODE_ITEMS],
            setToolTip="Behavior for external textures (outside the .blend folder):\n"
            "• Leave untouched — only make in-project paths relative.\n"
            "• Copy into the project — duplicate the file in, then repath.\n"
            "• Move into the project — relocate the file in, then repath.",
        )

    def tb_resolve_missing_textures_init(self, widget):
        """Resolve Missing option-box: the search strategy checkboxes (safest-first cascade)."""
        widget.option_box.menu.setTitle("Resolve Missing Textures")
        widget.option_box.menu.add(
            "QCheckBox", setText="Stem  — exact name, any extension",
            setObjectName="chk_stem", setChecked=True,
            setToolTip="Match a file whose name (any extension) equals the missing texture's name.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Texture  — same map type + base name (safest fuzzy)",
            setObjectName="chk_texture", setChecked=True,
            setToolTip="Restrict candidates to files of the same map type (AO / Normal / Roughness "
            "/ …) and fuzzy-match the map-stripped base name — an _AO never repaths to a _Normal.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Fuzzy  — similar name (loose; may mismatch)",
            setObjectName="chk_fuzzy", setChecked=True,
            setToolTip="Loose name matching when no exact-stem / same-map file is found.",
        )

    # ------------------------------------------------------------------ table
    def tbl000_init(self, widget):
        """Build the row context menu once, then (re)populate the table."""
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self.handle_cell_edit)

            widget.menu.add("Separator", setTitle="Path Management")
            widget.menu.add(
                "QPushButton", setText="Browse for File…", setObjectName="row_browse_for_file",
                setToolTip="Pick a file to repath this row's image to (single selection).",
            )
            widget.menu.add("Separator", setTitle="Selection")
            widget.menu.add(
                "QPushButton", setText="Select In Scene", setObjectName="select_material",
                setToolTip="Select the scene objects using this row's material(s).",
            )
            widget.menu.add(
                "QPushButton", setText="Show in Shader Editor", setObjectName="row_show_in_shader_editor",
                setToolTip="Open Blender's Shader Editor (Blender's Hypershade analogue).",
            )
            widget.menu.add("Separator", setTitle="Edit")
            widget.menu.add(
                "QPushButton", setText="Delete Image", setObjectName="delete_image",
                setToolTip="Remove this image datablock from the file.",
            )

            def _bind(action_name, method):
                widget.register_menu_action(
                    action_name,
                    lambda selection, fn=method: fn(selection),
                    columns=self._ROW_SELECTION_COLUMNS,
                )

            _bind("row_browse_for_file", self.row_browse_for_file)
            _bind("select_material", self.select_material)
            _bind("row_show_in_shader_editor", self.row_show_in_shader_editor)
            _bind("delete_image", self.delete_image)

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

            self._setup_formatting(widget, records)
            widget.apply_formatting()
        finally:
            widget.blockSignals(False)
            widget.setUpdatesEnabled(True)

        missing = sum(1 for r in records if not r["exists"])
        self.ui.footer.setText(f"{len(records)} texture(s), {missing} missing.")

    def _setup_formatting(self, widget, records):
        """Mark the path cell invalid (red) when its file is missing; tooltip the abs path."""
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
            item.setToolTip(ap if exists else f"Missing file:\n{ap}")

        widget.set_column_formatter(1, format_if_invalid)

    # ------------------------------------------------------------------ scope
    def _scope_images(self):
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
    def open_textures_folder(self):
        """Open <blend>/textures (or the .blend folder) in the file explorer."""
        import bpy

        blenddir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""
        if not blenddir:
            self.sb.message_box("Save the .blend first — there is no project folder yet.")
            return
        target = os.path.join(blenddir, "textures")
        if not os.path.isdir(target):
            target = blenddir
        try:
            os.startfile(target)  # noqa: S606 — Windows-only convenience (matches the Maya slot)
        except (AttributeError, OSError):
            self.sb.message_box(f"Textures folder:<br><hl>{target}</hl>")

    def reload_scene_textures(self):
        """Force Blender to re-read every image from disk."""
        btk.reload_textures()
        self.sb.message_box("Reloaded scene textures from disk.")
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------ header slots — Path Management
    def tb_set_texture_directory(self, widget=None):
        """Repath images (selection or all) so their files live under a chosen directory."""
        images, scope_label = self._scope_images()
        if not images:
            self.sb.message_box("No textures to process.")
            return
        mode = self._read_combo_mode(widget, "cmb_relocate_mode", self._RELOCATE_MODE_ITEMS)
        target_dir = self._pick_dir(f"Set Texture Directory — {scope_label}")
        if not target_dir:
            return
        count = btk.set_texture_directory(images, target_dir, mode=mode)
        self.sb.message_box(f"Updated <hl>{count}</hl>/{len(images)} texture path(s).")
        self.ui.tbl000.init_slot()

    def tb_find_and_copy_textures(self, widget=None):
        """Search a folder for the images' textures, copy/move to a destination, repath."""
        images, _scope = self._scope_images()
        if not images:
            self.sb.message_box("No textures to process.")
            return
        mode = self._read_combo_mode(widget, "cmb_relocate_mode", self._FIND_MODE_ITEMS)
        self._find_and_copy_workflow(images, relocate_mode=mode)

    def tb_normalize_paths(self, widget=None):
        """Rewrite paths relative to the saved .blend; option box handles external textures."""
        external_mode = self._read_combo_mode(
            widget, "cmb_external_mode", self._NORMALIZE_MODE_ITEMS
        )
        if external_mode in ("copy", "move"):
            btk.normalize_texture_paths(external_mode)
        n = btk.normalize_texture_paths("relative")
        self.sb.message_box(
            f"Made <hl>{n}</hl> path(s) relative."
            if n
            else "Nothing changed — paths are already relative (or the .blend isn't saved)."
        )
        self.ui.tbl000.init_slot()

    def tb_resolve_missing_textures(self, widget=None):
        """Search a folder for replacements for missing textures (by name)."""
        menu = getattr(widget, "option_box", None)
        use_stem = bool(menu and menu.menu.chk_stem.isChecked())
        use_texture = bool(menu and menu.menu.chk_texture.isChecked())
        use_fuzzy = bool(menu and menu.menu.chk_fuzzy.isChecked())
        search_dir = self._pick_dir("Resolve Missing Textures — pick a search folder")
        if not search_dir:
            return
        n = btk.resolve_missing_textures(
            search_dir, stem=use_stem, texture=use_texture, fuzzy=use_fuzzy
        )
        self.sb.message_box(f"Resolved <hl>{n}</hl> missing texture(s).")
        self.ui.tbl000.init_slot()

    def _read_combo_mode(self, button, combo_name, mode_items):
        """Read a relocate/external combobox by index → mode key (safe default = first)."""
        try:
            idx = getattr(button.option_box.menu, combo_name).currentIndex()
        except AttributeError:
            return mode_items[0][1]
        return mode_items[idx][1] if 0 <= idx < len(mode_items) else mode_items[0][1]

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
        target_images = {
            img_name
            for img_name, mats in self._image_to_mats.items()
            if mat_names.intersection(mats)
        }
        self._select_rows(lambda img_name, path: img_name in target_images, "matching textures")

    def select_broken_paths(self):
        """Select rows whose texture file is missing."""
        missing = {r["name"] for r in btk.get_image_records() if not r["exists"]}
        self._select_rows(lambda img_name, path: img_name in missing, "broken paths")

    def select_absolute_paths(self):
        """Select rows whose path is absolute (not a // project-relative path)."""
        self._select_rows(
            lambda img_name, path: bool(path) and not path.startswith("//") and os.path.isabs(path),
            "absolute paths",
        )

    def _select_rows(self, predicate, label):
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

    # ------------------------------------------------------------------ row context slots
    def row_browse_for_file(self, selection=None):
        """Repath the selected row's image to a file chosen in a browser (single selection)."""
        images = self._images_from_selection(selection)
        if not images:
            return
        if len(images) > 1:
            self.sb.message_box("Browse for File: select a single row.")
            return
        img = images[0]
        new_path = self._pick_file(f"Repath '{img.name}'")
        if not new_path:
            return
        btk.repath_image(img, new_path)
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

    def row_show_in_shader_editor(self, selection=None):
        """Open Blender's Shader Editor (the Hypershade analogue)."""
        btk.open_editor("Shader Editor")

    def delete_image(self, selection=None):
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
            btk.repath_image(img, new_value)
            table.apply_formatting()
            missing = sum(1 for r in btk.get_image_records() if not r["exists"])
            self.ui.footer.setText(f"{table.rowCount()} texture(s), {missing} missing.")
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
        """Prompt for source + destination folders, then relocate/repath via the engine."""
        source_dir = self._pick_dir("Find & Copy — pick a folder to search recursively")
        if not source_dir:
            return
        dest_dir = self._pick_dir("Find & Copy — pick the destination folder")
        if not dest_dir:
            return
        count = btk.find_and_copy_textures(images, source_dir, dest_dir, mode=relocate_mode)
        self.sb.message_box(
            f"{relocate_mode.title()}d + repathed <hl>{count}</hl> texture(s)."
            if count
            else "No matching textures found in the search folder."
        )
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------ misc
    def refresh_texture_table(self):
        """Manual refresh trigger from the header refresh button."""
        table = getattr(self.ui, "tbl000", None)
        if table:
            table.init_slot()

    @staticmethod
    def _resolve_path(path):
        """Resolve a raw path string (// project-relative included) to an absolute path."""
        import bpy

        try:
            return os.path.normpath(bpy.path.abspath(path)) if path else ""
        except Exception:
            return os.path.normpath(path) if path else ""

    @staticmethod
    def _pick_dir(title):
        from qtpy import QtWidgets

        return QtWidgets.QFileDialog.getExistingDirectory(None, title) or None

    @staticmethod
    def _pick_file(title):
        from qtpy import QtWidgets

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, title, "",
            "Images (*.png *.jpg *.jpeg *.tga *.tif *.tiff *.exr *.hdr *.bmp);;All (*)",
        )
        return path or None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("texture_path_editor", reload=True)
    ui.show(pos="screen", app_exec=True)
