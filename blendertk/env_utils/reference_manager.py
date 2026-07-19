# !/usr/bin/python
# coding=utf-8
"""Reference Manager tool panel — Switchboard slot wiring for the co-located ``reference_manager.ui``.

Faithful Blender counterpart of mayatk's Reference Manager: a **workspace scene-file manager**, not
just a library linker. Mirrors the Maya panel's whole surface — a **Root Directory** (``txt000``), a
**Workspace** combo of project folders under it (``cmb000``), a **Filter** (``txt001``), and the
5-column scene-file **table** (``tbl000``) — **FILES | reference-toggle | open | display-mode |
NOTES** with the same clickable action-icon columns as Maya — plus open / save / rename / delete
scene, reference link / make-local / reload / relocate / remove, and per-reference **display modes**.

Mapping (Maya → Blender), all backed by :mod:`blendertk.EnvUtils`:
  * scene file (``.ma`` / ``.mb``) → ``.blend``; *open scene* → ``wm.open_mainfile``; *save scene*
    (naming conventions) → ``wm.save_as_mainfile`` via ``save_scene_as``; rename / delete → on disk.
  * Maya **workspace** (project dir) → a project folder under the root (``find_workspaces``).
  * Maya **file reference** → a linked **library** (File ▸ Link), or **Append** for a local copy;
    *import references* → ``make_library_local``; *update* → reload; *un-reference* → remove.
  * per-reference **display override** (normal / reference / template) → the linked objects'
    ``display_type`` + ``hide_select`` (``set_reference_display_mode``).
  * **Notes** column → per-file comments persisted in the panel's settings (DCC-agnostic);
    hidden by default with a *Show Notes Column* toggle, like Maya.
  * Naming presets (**case** / **suffix** / **subfolder** structure) drive *Save Scene*.
  * **Filter / Display** (header menu) → filter by name / notes (ignore-case + a target combo) or by
    suffix / folder-structure; optionally hide the suffix / extension in the displayed name.
  * **Workspace history** → the last workspace chosen per root directory is remembered (QSettings).

The three action-icon columns mirror Maya's: click the **link** icon to link/unlink a file, the
**open** icon to open the scene (the current scene is highlighted + italicized), and the tri-state
**display** icon to cycle Normal → Reference → Template. The row **context menu** adds the
Blender-specific extras (Append vs Link, Reload, Relocate, Make Local) and Rename / Delete.

Intentionally **not** mirrored (genuinely Maya-only): namespaces and assemblies
(``AssemblyManager`` / ``convert_references_to_assemblies`` — no Blender analogue).

Self-contained (``ptk.LoggingMixin`` only); ``import bpy`` and the Qt-only ``uitk`` helpers are
deferred into the call bodies (headless Blender ships no Qt; the workspace .venv ships no bpy, so the
table degrades gracefully — file list without live linked-status — when bpy is absent).
"""
import os

import pythontk as ptk

import blendertk as btk

# Naming-convention case styles for Save Scene (header menu). These are exactly the tokens
# pythontk.StrUtils.set_case honors — using "camel case"/"snake case" here would be a silent no-op.
_CASE_STYLES = ("None", "camel", "pascal", "title", "upper", "lower", "capitalize")
# Reference action verbs and display modes (mirror of mayatk's tri-state).
_DISPLAY_MODES = (("Normal", "off"), ("Reference (locked)", "reference"), ("Template (wire)", "template"))
# Max per-root workspace selections remembered (mirror of mayatk's workspace history cap).
_WORKSPACE_HISTORY_MAX = 20


class ReferenceManagerSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Reference Manager panel."""

    # Table columns — Name | Reference-toggle | Open | Display-mode | Notes (1:1 with mayatk).
    COL_NAME = 0
    COL_REF = 1
    COL_OPEN = 2
    COL_DISPLAY = 3
    COL_NOTES = 4

    # Action-icon colours, mirrored from the Maya panel.
    ACTION_COLOR = {
        "off": "#555555",
        "referenced": "#6b8fa3",
        "current": "#6898b8",
        "ref_lock": "#d4a84a",
        "template": "#6b8fa3",
        "unavailable": "#3a3a3a",
    }
    # Display-mode click cycle: Normal → Reference → Template → Normal.
    _DISPLAY_MODE_CYCLE = {"off": "reference", "reference": "template", "template": "off"}

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.reference_manager
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[reference_manager] ")
        self._recursive = False  # scan the workspace folder only by default (subfolders optional)
        self._notes = dict(self.ui.settings.value("reference_notes") or {})
        self._suppress_note_save = False  # guard programmatic table edits

    # ------------------------------------------------------------------ bpy availability
    @staticmethod
    def _has_bpy():
        try:
            import bpy  # noqa: F401

            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ header
    def header_init(self, widget):
        """Header refresh button, Recursive toggle, Naming presets, bulk Operations, help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("refresh", "menu", "collapse", "pin")
        widget.refresh_requested.connect(self._refresh)
        # Save / load the header menu's naming + filter settings as named presets (mirror of Maya).
        widget.menu.add_presets = True
        widget.menu.presets.preset_dir = "blendertk/reference_manager"

        widget.menu.add(
            "QCheckBox", setText="Recursive", setObjectName="chk_recursive", setChecked=False,
            setToolTip="Scan the workspace's subfolders too (off = the workspace folder only).",
        )
        widget.menu.chk_recursive.toggled.connect(self._on_recursive_toggled)

        # Naming conventions for Save Scene (mirror of Maya's case / suffix / subfolder structure).
        widget.menu.add("Separator", setTitle="Naming:")
        widget.menu.add(
            "QComboBox", addItems=list(_CASE_STYLES), setObjectName="cmb_case_style",
            setToolTip="Case convention applied to the name on Save.",
        )
        widget.menu.add(
            "QLineEdit", setObjectName="txt_suffix", setPlaceholderText="Suffix (e.g. _v01)…",
            setToolTip="Suffix appended to the name on Save.",
        )
        widget.menu.add(
            "QLineEdit", setObjectName="txt_subfolder", setPlaceholderText="Subfolder (e.g. scenes/{name})…",
            setToolTip="Subfolder pattern for Save / the folder-structure filter —\n"
            "placeholders: {name}, {workspace}, {suffix}.",
        )

        # Filter / Display options (mirror of Maya's header filter checkboxes). Each re-filters the
        # list; Show Notes Column is a view-only toggle (Notes hidden by default, like Maya).
        # (Ignore-Case + filter target + the enable toggle live on the Filter field's option box.)
        widget.menu.add("Separator", setTitle="Filter / Display:")
        widget.menu.add(
            "QCheckBox", setText="Filter by Suffix", setObjectName="chk_filter_suffix", setChecked=False,
            setToolTip="Show only files whose name ends with the Suffix above.",
        ).toggled.connect(lambda *_: self._refresh())
        widget.menu.add(
            "QCheckBox", setText="Filter by Folder Structure",
            setObjectName="chk_filter_folder_structure", setChecked=False,
            setToolTip="Show only files whose location matches the Subfolder pattern above.",
        ).toggled.connect(lambda *_: self._refresh())
        widget.menu.add(
            "QCheckBox", setText="Hide Suffix", setObjectName="chk_hide_suffix", setChecked=False,
            setToolTip="Hide the suffix from the displayed file name.",
        ).toggled.connect(lambda *_: self._refresh())
        widget.menu.add(
            "QCheckBox", setText="Hide Extension", setObjectName="chk_hide_extension", setChecked=False,
            setToolTip="Hide the .blend extension from the displayed file name.",
        ).toggled.connect(lambda *_: self._refresh())
        widget.menu.add(
            "QCheckBox", setText="Show Notes Column", setObjectName="chk_show_notes_column",
            setChecked=False,
            setToolTip="Show the Notes column (per-file comments / metadata). Hidden by default.",
        ).toggled.connect(lambda *_: self._apply_notes_column_visibility())
        # Re-filter on a suffix / subfolder edit when a dependent filter is active (mirror of Maya).
        widget.menu.txt_suffix.textChanged.connect(lambda *_: self._on_naming_field_changed())
        widget.menu.txt_subfolder.textChanged.connect(lambda *_: self._on_naming_field_changed())

        # Bulk operations (Blender analogues of Maya's Save / Unlink-and-Import-All / Un-Reference-All;
        # Convert-to-Assembly has no Blender analogue and is intentionally dropped).
        widget.menu.add("Separator", setTitle="Operations:")
        widget.menu.add(
            "QPushButton", setText="Save Scene…", setObjectName="btn_save_scene",
            setToolTip="Save the current scene into the workspace using the naming conventions above.",
        ).clicked.connect(self.save_scene)
        widget.menu.add(
            "QPushButton", setText="New Workspace…", setObjectName="btn_new_workspace",
            setToolTip="Create a project workspace under the root — writes a workspace.mel\n"
            "(shared Maya/Blender project) plus the standard subfolders.",
        ).clicked.connect(self.new_workspace)
        widget.menu.add(
            "QPushButton", setText="Mark As Workspace", setObjectName="btn_mark_workspace",
            setToolTip="Write a workspace.mel describing the current workspace folder's existing\n"
            "layout, making it a shared Maya/Blender project (no files are moved).",
        ).clicked.connect(self.mark_workspace)
        widget.menu.add(
            "QPushButton", setText="Reload All", setObjectName="btn_reload_all",
            setToolTip="Reload every linked library from disk (Maya's Update References).",
        ).clicked.connect(self.reload_all)
        widget.menu.add(
            "QPushButton", setText="Make Local All", setObjectName="btn_make_local_all",
            setToolTip="Make every linked library's data local (Maya's Unlink-and-Import All).",
        ).clicked.connect(self.make_local_all)
        widget.menu.add(
            "QPushButton", setText="Remove All", setObjectName="btn_remove_all",
            setToolTip="Remove every linked library and its data (Un-Reference All).",
        ).clicked.connect(self.remove_all)

        widget.set_help_text(
            fmt(
                title="Reference Manager",
                body="A workspace scene-file manager: browse a project's .blend files, open / save / "
                "rename / delete them, and link them as references (Blender libraries).",
                steps=[
                    "Set a <b>Root Directory</b> (▸ to browse / recent); pick a <b>Workspace</b>.",
                    "Click the row's <b>link</b> icon to link/unlink, the <b>open</b> icon to open the "
                    "scene, the <b>display</b> icon to cycle Normal → Reference → Template.",
                    "<b>Double-click</b> the name to rename; right-click for the full action menu "
                    "(Append, Reload, Relocate, Make Local, …).",
                    "<b>Double-click</b> the <b>Notes</b> column to annotate a file (saved with the panel).",
                ],
                sections=[
                    ("Columns", [
                        "<b>FILES</b> | link | open | display | <b>NOTES</b>. The current scene's row "
                        "is highlighted + italic.",
                    ]),
                    ("Header menu", [
                        "<b>Recursive</b> scans subfolders. <b>Naming</b> (case / suffix / subfolder) "
                        "drives <b>Save Scene</b>.",
                        "<b>Filter by Suffix / Folder Structure</b> narrow the list; <b>Hide Suffix / "
                        "Extension</b> shorten the displayed name; <b>Show Notes Column</b> reveals Notes.",
                        "<b>Operations</b>: Save Scene; New Workspace / Mark As Workspace (write "
                        "a shared Maya/Blender workspace.mel project); Reload All, Make Local "
                        "All, Remove All.",
                    ]),
                    ("Filter field (▸ option box)", [
                        "Toggle the filter on/off; <b>Ignore Case</b>; choose what it matches — "
                        "<b>Files</b>, <b>Notes</b>, or both.",
                    ]),
                ],
            )
        )

    # ------------------------------------------------------------------ fields
    def txt000_init(self, widget):
        """Root Directory — browse + recent-dir history + Open / Set-To-Current actions (mirror of Maya)."""
        if not getattr(widget, "is_initialized", False):
            # Recent-directory history dropdown (Maya's directory pin).
            try:
                widget.option_box.pin(
                    settings_key="reference_manager_directories", single_click_restore=True
                )
            except Exception:
                pass
            widget.option_box.browse(
                mode="directory", title="Select Root Directory",
                tooltip="Browse for a root folder containing project workspaces.",
            )
            widget.option_box.menu.add(
                "QPushButton", setText="Open Directory", setObjectName="btn_open_dir",
                setToolTip="Open the root directory in the file manager.",
            ).clicked.connect(self._open_root_dir)
            widget.option_box.menu.add(
                "QPushButton", setText="Set To Current Workspace", setObjectName="btn_set_current_ws",
                setToolTip="Set the root to the folder of the currently-open file.",
            ).clicked.connect(self._set_root_to_current)
        if hasattr(widget, "set_validator"):
            widget.set_validator("dir")
        last = self.ui.settings.value("root_dir") or ""
        if last:
            widget.setText(last)
        widget.textChanged.connect(self._on_root_changed)
        widget.returnPressed.connect(self._populate_workspaces)

    def _open_root_dir(self):
        """Reveal the root directory in the OS file manager (Maya's 'Open Directory')."""
        root = self._root_dir()
        if not (root and os.path.isdir(root)):
            self.sb.message_box("Set a valid root directory first.")
            return
        try:
            ptk.FileUtils.reveal_in_file_manager(root)
        except (FileNotFoundError, OSError) as e:
            self.sb.message_box(str(e))

    def _set_root_to_current(self):
        """Set the root to the current workspace — the marked (workspace.mel) project root when
        the open .blend belongs to one, else the .blend's own folder (Maya's 'Set To Current
        Workspace')."""
        import bpy

        fp = bpy.data.filepath
        if not fp:
            self.sb.message_box("Save the current file first — it has no folder yet.")
            return
        self.ui.txt000.setText(btk.workspace_root(fp) or os.path.dirname(os.path.abspath(fp)))

    def cmb000_init(self, widget):
        """Workspace combo — project folders under the root (replaces Maya's workspace combo)."""
        widget.currentIndexChanged.connect(self._on_workspace_changed)
        self._populate_workspaces()

    def txt001_init(self, widget):
        """Filter field — enable toggle + ignore-case + target combo, plus live re-filter (mirror of Maya)."""
        if not getattr(widget, "is_initialized", False):
            widget.option_box.set_toggle(
                icon="filter",
                tooltip_on="Filter enabled. Click to disable.",
                tooltip_off="Filter disabled. Click to enable.",
                initial=True,
                gate_wrapped=True,  # grey out the field while the filter is off
                on_toggled=lambda *_: self._refresh(),
                settings_key="reference_manager_filter",
            )
            widget.option_box.menu.add(
                "QCheckBox", setText="Ignore Case", setObjectName="chk_ignore_case", setChecked=True,
                setToolTip="Match the filter text case-insensitively.",
            ).toggled.connect(lambda *_: self._refresh())
            widget.option_box.menu.add(
                "QComboBox", setObjectName="cmb_filter_target",
                addItems=["Filter: All", "Filter: Files", "Filter: Notes"],
                setToolTip="What the filter text matches against: file names, notes, or both.",
            ).currentIndexChanged.connect(lambda *_: self._refresh())
        widget.textChanged.connect(lambda *_: self._refresh())

    def tbl000_init(self, widget):
        """One-time table setup (clickable action columns + context menu + signals), then populate."""
        if not widget.is_initialized:
            widget.refresh_on_show = True
            self._setup_action_columns(widget)
            widget.itemDoubleClicked.connect(self._on_item_double_clicked)
            widget.itemChanged.connect(self._on_item_changed)

            widget.menu.add("Separator", setTitle="Scene")
            widget.menu.add("QPushButton", setText="Open", setObjectName="row_open",
                            setToolTip="Open the selected .blend (replaces the current file).")
            widget.menu.add("QPushButton", setText="Rename…", setObjectName="row_rename",
                            setToolTip="Rename the selected .blend on disk.")
            widget.menu.add("QPushButton", setText="Delete", setObjectName="row_delete",
                            setToolTip="Delete the selected .blend from disk.")
            widget.menu.add("QPushButton", setText="Open File Location", setObjectName="row_location",
                            setToolTip="Reveal the selected file in the OS file manager.")
            widget.menu.add("Separator", setTitle="Reference")
            widget.menu.add("QPushButton", setText="Link", setObjectName="row_link",
                            setToolTip="Link the file's collections as a live reference.")
            widget.menu.add("QPushButton", setText="Append", setObjectName="row_append",
                            setToolTip="Append the file's collections as a local (editable) copy.")
            widget.menu.add("Separator", setTitle="Linked")
            widget.menu.add("QPushButton", setText="Reload", setObjectName="row_reload",
                            setToolTip="Reload the linked library from disk.")
            widget.menu.add("QPushButton", setText="Relocate…", setObjectName="row_relocate",
                            setToolTip="Point the library at a different .blend.")
            widget.menu.add("QPushButton", setText="Make Local", setObjectName="row_make_local",
                            setToolTip="Make the library's data local (Maya's Import Reference).")
            widget.menu.add("QPushButton", setText="Remove", setObjectName="row_remove",
                            setToolTip="Remove the library and everything linked from it.")
            widget.menu.add("Separator", setTitle="Display (linked)")
            for label, mode in _DISPLAY_MODES:
                name = f"row_display_{mode}"
                widget.menu.add("QPushButton", setText=label, setObjectName=name,
                                setToolTip=f"Set the linked reference's display to {label}.")
                widget.register_menu_action(name, (lambda m: lambda *_: self.set_display(m))(mode))

            for obj_name, handler in (
                ("row_open", self.open_selected), ("row_rename", self.rename_selected),
                ("row_delete", self.delete_selected), ("row_location", self.open_location_selected),
                ("row_link", lambda: self.reference_selected(link=True)),
                ("row_append", lambda: self.reference_selected(link=False)),
                ("row_reload", self.reload_selected), ("row_relocate", self.relocate_selected),
                ("row_make_local", self.make_local_selected), ("row_remove", self.remove_selected),
            ):
                widget.register_menu_action(obj_name, (lambda h: lambda *_: h())(handler))

        self._refresh_table_content(widget)

    def _setup_action_columns(self, widget):
        """Register the Reference / Open / Display-mode clickable icon columns (mirror of Maya)."""
        clr = self.ACTION_COLOR
        widget.actions.add(self.COL_REF, states={
            "unreferenced": {"icon": "link", "color": clr["off"],
                             "tooltip": "Not referenced — click to link.",
                             "action": self._toggle_reference_at_row},
            "referenced": {"icon": "link", "color": clr["referenced"],
                           "tooltip": "Linked — click to remove the reference.",
                           "action": self._toggle_reference_at_row},
        })
        widget.actions.add(self.COL_OPEN, states={
            "default": {"icon": "open_external", "color": clr["off"],
                        "tooltip": "Open this scene.", "action": self._open_scene_at_row},
            "current": {"icon": "open_external", "color": clr["current"],
                        "tooltip": "Current scene.", "action": self._open_scene_at_row},
        })
        widget.actions.add(self.COL_DISPLAY, states={
            "off": {"icon": "grid", "color": clr["off"],
                    "tooltip": "Display: Normal — click to lock (Reference).",
                    "action": self._cycle_display_mode_at_row},
            "reference": {"icon": "lock", "color": clr["ref_lock"],
                          "tooltip": "Display: Reference (locked) — click for Template (wire).",
                          "action": self._cycle_display_mode_at_row},
            "template": {"icon": "grid", "color": clr["template"],
                         "tooltip": "Display: Template (wire + locked) — click to restore Normal.",
                         "action": self._cycle_display_mode_at_row},
            "unavailable": {"icon": "grid", "color": clr["unavailable"],
                            "tooltip": "Display overrides apply only to linked references."},
        })

    def _on_item_double_clicked(self, item):
        """Double-click the name → rename; the Notes cell → inline edit (mirror of Maya's editItem).

        Renames the double-clicked row directly (not the selection) so it also works on the current
        scene, whose name cell is intentionally non-selectable.
        """
        if item is None:
            return
        if item.column() == self.COL_NAME:
            path = self._row_path(item.row())
            if path:
                self._rename_path(path)
        elif item.column() == self.COL_NOTES:
            self.ui.tbl000.editItem(item)

    # ------------------------------------------------------------------ row action handlers
    def _row_path(self, row):
        """Absolute .blend path stored on the name cell of ``row`` (or None)."""
        item = self.ui.tbl000.item(row, self.COL_NAME)
        return item.data(self.sb.QtCore.Qt.UserRole) if item else None

    def _toggle_reference_at_row(self, row, col):
        """Link an unreferenced file, or remove the library of an already-linked one (Maya toggle)."""
        path = self._row_path(row)
        if not path:
            return
        lib = self._library_for_path(path)
        if lib is not None:
            btk.remove_library(lib)
        else:
            try:
                btk.link_blend_file(path, link=True)
            except (RuntimeError, OSError) as e:
                self.sb.message_box(str(e))
                return
        self._refresh()

    def _open_scene_at_row(self, row, col):
        """Open the scene at ``row`` (with the unsaved-changes guard)."""
        path = self._row_path(row)
        if path:
            self._open_path(path)

    def _cycle_display_mode_at_row(self, row, col):
        """Cycle the linked reference's display: Normal → Reference → Template → Normal."""
        path = self._row_path(row)
        lib = self._library_for_path(path) if path else None
        table = self.ui.tbl000
        if lib is None:  # not linked (or removed between sync and click) — reset the cell silently
            table.actions.set(row, self.COL_DISPLAY, "unavailable")
            return
        new_mode = self._DISPLAY_MODE_CYCLE.get(btk.get_reference_display_mode(lib), "off")
        if not btk.set_reference_display_mode(lib, new_mode):
            self.sb.message_box("Display override had no effect — the reference has no objects to update.")
            return
        table.actions.set(row, self.COL_DISPLAY, new_mode)

    # ------------------------------------------------------------------ workspace + refresh
    def _on_root_changed(self, text):
        self.ui.settings.setValue("root_dir", text.strip())
        self._populate_workspaces()

    def _on_recursive_toggled(self, state):
        self._recursive = bool(state)
        self._refresh()

    def _root_dir(self):
        field = getattr(self.ui, "txt000", None)  # may not exist yet during sibling *_init
        return field.text().strip() if field is not None else ""

    def _populate_workspaces(self):
        """Fill the workspace combo with project folders under the root, then refresh the table."""
        combo = getattr(self.ui, "cmb000", None)
        if combo is None:
            return
        root = self._root_dir()
        workspaces = btk.find_workspaces(root) if root else []
        prev = combo.currentData() if combo.count() else None
        # One add() call — ComboBox.add clears by default, so a per-item loop would wipe each prior.
        items = [(os.path.basename(p.rstrip("/\\")) or p, p) for p in workspaces]
        combo.blockSignals(True)
        try:
            combo.add(items)
            # Prefer the last workspace remembered for this root; else keep the in-session selection.
            if not self._restore_workspace_index(combo) and prev:
                for i in range(combo.count()):
                    if combo.itemData(i) == prev:
                        combo.setCurrentIndex(i)
                        break
        finally:
            combo.blockSignals(False)
        self._refresh()

    def _on_workspace_changed(self, *_):
        """Remember the chosen workspace for the current root, then refresh the table."""
        combo = getattr(self.ui, "cmb000", None)
        if combo is not None and combo.currentIndex() >= 0:
            self._save_workspace_selection(self._root_dir(), combo.currentText())
        self._refresh()

    # ------------------------------------------------------------------ workspace history (per root)
    def _get_workspace_history(self):
        """Load the per-root last-selected-workspace map from panel settings."""
        return dict(self.ui.settings.value("workspace_history") or {})

    def _save_workspace_selection(self, root_dir, workspace_name):
        """Remember which workspace was last selected for ``root_dir`` (capped LRU)."""
        if not (root_dir and workspace_name):
            return
        history = self._get_workspace_history()
        history[os.path.normcase(os.path.normpath(root_dir))] = workspace_name
        if len(history) > _WORKSPACE_HISTORY_MAX:
            history = dict(list(history.items())[-_WORKSPACE_HISTORY_MAX:])
        self.ui.settings.setValue("workspace_history", history)

    def _restore_workspace_index(self, combo):
        """Select the workspace last used for the current root, if it's still present. True if restored."""
        root = self._root_dir()
        if not root:
            return False
        saved = self._get_workspace_history().get(os.path.normcase(os.path.normpath(root)))
        if saved:
            for i in range(combo.count()):
                if combo.itemText(i) == saved:
                    combo.setCurrentIndex(i)
                    return True
        return False

    def _workspace_dir(self):
        """The current workspace folder (combo selection), falling back to the root directory."""
        combo = getattr(self.ui, "cmb000", None)
        if combo is not None and combo.count() and combo.currentData():
            return combo.currentData()
        return self._root_dir()

    def new_workspace(self):
        """Create a marked workspace under the root (rules from the active template — see
        the Workspace Editor) — the counterpart of Maya's File ▸ Project Window ▸ New."""
        root = self._root_dir()
        if not (root and os.path.isdir(root)):
            self.sb.message_box("Set a valid root directory first.")
            return
        name = self.sb.input_dialog("New Workspace", "Workspace folder name:", "")
        name = (name or "").strip()
        if not name:
            return
        try:
            ws = btk.create_workspace(os.path.join(root, name))
        except OSError as e:
            self.sb.message_box(str(e))
            return
        self._populate_workspaces()
        if ws:
            self._select_workspace(ws.root)

    def mark_workspace(self):
        """Promote the current workspace folder to a shared Maya/Blender project — writes a
        workspace.mel describing its existing layout (no files are moved)."""
        ws_dir = self._workspace_dir()
        if not (ws_dir and os.path.isdir(ws_dir)):
            self.sb.message_box("Pick a workspace (or set a root directory) first.")
            return
        try:
            ws = btk.promote_workspace(ws_dir)
        except OSError as e:
            self.sb.message_box(str(e))
            return
        if ws is None:
            self.sb.message_box("Could not mark the workspace.")
            return
        self._populate_workspaces()
        self._select_workspace(ws.root)

    def _select_workspace(self, path):
        """Select the workspace combo entry whose data is ``path`` (after a repopulate)."""
        combo = getattr(self.ui, "cmb000", None)
        if combo is None:
            return
        target = os.path.normcase(os.path.normpath(path))
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and os.path.normcase(os.path.normpath(data)) == target:
                combo.setCurrentIndex(i)
                break

    def _refresh(self):
        table = getattr(self.ui, "tbl000", None)
        if table:
            table.init_slot()

    def _refresh_table_content(self, widget):
        """Scan the workspace, apply the header filter / display options, and (re)build the
        File · Status · Notes table (mirror of mayatk's filtered file list)."""
        from qtpy import QtWidgets

        workspace = self._workspace_dir()
        opt = self._filter_options()

        # Text filtering is applied below (not via find_blend_files) so it can honor the
        # ignore-case toggle and match against Notes as well as file names.
        files = btk.find_blend_files(workspace, recursive=self._recursive)
        if workspace and not self._recursive:
            # A marked (workspace.mel) project keeps scenes in its scene-rule folder — include
            # it so shared Maya/Blender projects list out of the box (mirror of mayatk, which
            # always scans a workspace's scenes/ regardless of the discovery toggle).
            scene_dir = btk.workspace_scenes_dir(workspace)
            if scene_dir:
                seen = {os.path.normcase(p) for p in files}
                files += [
                    p for p in btk.find_blend_files(scene_dir, recursive=True)
                    if os.path.normcase(p) not in seen
                ]
        files = self._apply_file_filters(files, workspace, opt)
        # Live reference state needs bpy; degrade gracefully under the .venv (no live status).
        # One list_libraries() pass — `linked` is derived from it so the two can't disagree.
        libs_by_path = {}
        if self._has_bpy():
            for r in btk.list_libraries():
                ap = r.get("abspath")
                if ap:
                    libs_by_path[os.path.normpath(ap).lower()] = r["library"]
        linked = set(libs_by_path)
        current = self._current_scene_path()

        self._suppress_note_save = True
        widget.setUpdatesEnabled(False)
        widget.clear()
        qt = self.sb.QtCore.Qt
        try:
            placeholder = None
            if not workspace:
                placeholder = "Set a root directory / workspace above…"
            elif not files:
                placeholder = "No .blend files found"

            if placeholder is not None:
                rows = [[(placeholder, ""), "", "", "", ""]]
            else:
                rows = [
                    [(self._format_display_name(p, opt), p), "", "", "",
                     self._notes.get(os.path.normpath(p).lower(), "")]
                    for p in files
                ]
            # Columns: FILES | reference-toggle | open | display-mode | NOTES (1:1 with Maya).
            widget.add(rows, headers=["FILES:", "", "", "", "NOTES:"])

            if placeholder is None:
                for row, path in enumerate(files):
                    key = os.path.normpath(path).lower()
                    is_linked = key in linked
                    is_current = bool(current) and key == current
                    widget.actions.set(row, self.COL_REF, "referenced" if is_linked else "unreferenced")
                    widget.actions.set(row, self.COL_OPEN, "current" if is_current else "default")
                    if is_linked:
                        lib = libs_by_path.get(key)
                        mode = btk.get_reference_display_mode(lib) if lib is not None else "off"
                        widget.actions.set(row, self.COL_DISPLAY, mode)
                    else:
                        widget.actions.set(row, self.COL_DISPLAY, "unavailable")

                    name_item = widget.item(row, self.COL_NAME)
                    note_item = widget.item(row, self.COL_NOTES)
                    if name_item:
                        name_item.setFlags(name_item.flags() & ~qt.ItemIsEditable)
                        if is_current:  # current scene: italic + not selectable (mirror of Maya)
                            font = name_item.font()
                            font.setItalic(True)
                            name_item.setFont(font)
                            name_item.setFlags(name_item.flags() & ~qt.ItemIsSelectable)
                    if note_item and path:
                        note_item.setData(qt.UserRole, path)  # carry the path for note edits

            header = widget.horizontalHeader()
            header.setSectionResizeMode(self.COL_NAME, QtWidgets.QHeaderView.Stretch)
            for c in (self.COL_REF, self.COL_OPEN, self.COL_DISPLAY):
                header.setSectionResizeMode(c, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(self.COL_NOTES, QtWidgets.QHeaderView.Stretch)
        finally:
            widget.setUpdatesEnabled(True)
            self._suppress_note_save = False

        self._apply_notes_column_visibility()

        n_linked = len(linked)
        self.ui.footer.setText(
            f"{len(files)} .blend file(s); {n_linked} linked." if workspace else "Set a root directory."
        )

    def _current_scene_path(self):
        """Normalized path of the currently-open .blend (or '' — needs bpy)."""
        if not self._has_bpy():
            return ""
        import bpy

        fp = bpy.data.filepath
        return os.path.normpath(fp).lower() if fp else ""

    # ------------------------------------------------------------------ filter / display options
    def _filter_options(self):
        """Snapshot the filter / display widgets (defensive: menus may not be built yet).

        Suffix / folder-structure / hide / notes options live on the header menu; the text-filter
        controls (enable toggle, ignore-case, target) live on the Filter field's option box — the
        same split as the Maya panel.
        """
        header = getattr(self.ui, "header", None)
        menu = getattr(header, "menu", None) if header else None  # naming + display options
        filt = getattr(self.ui, "txt001", None)
        fbox = getattr(filt, "option_box", None) if filt is not None else None
        fmenu = getattr(fbox, "menu", None) if fbox is not None else None  # text-filter options

        def chk(m, name, default=False):
            w = getattr(m, name, None) if m else None
            return w.isChecked() if w is not None else default

        def txt(name):
            w = getattr(menu, name, None) if menu else None
            return w.text().strip() if w is not None else ""

        # The filter on/off toggle gates only the text filter (suffix/structure always apply).
        enabled = True
        if fbox is not None:
            try:
                from uitk.widgets.optionBox.options.toggle import ToggleOption

                toggle = fbox.find_option(ToggleOption)
                enabled = toggle.is_on if toggle is not None else True
            except Exception:
                enabled = True

        target_w = getattr(fmenu, "cmb_filter_target", None) if fmenu else None
        return {
            "suffix": txt("txt_suffix"),
            "structure_pattern": txt("txt_subfolder"),
            "filter_suffix": chk(menu, "chk_filter_suffix"),
            "filter_structure": chk(menu, "chk_filter_folder_structure"),
            "hide_suffix": chk(menu, "chk_hide_suffix"),
            "hide_extension": chk(menu, "chk_hide_extension"),
            "ignore_case": chk(fmenu, "chk_ignore_case", True),
            "target": target_w.currentText() if target_w is not None else "Filter: All",
            "filter_text": (filt.text().strip() if filt is not None else "") if enabled else "",
        }

    def _apply_file_filters(self, files, workspace, opt):
        """Apply the suffix / folder-structure / text filters to the file list (mirror of Maya)."""
        suffix = opt["suffix"]
        # Filter by suffix — keep files whose name (sans extension) ends with the suffix.
        if opt["filter_suffix"] and suffix:
            files = [f for f in files
                     if os.path.splitext(os.path.basename(f))[0].endswith(suffix)]

        # Filter by folder structure — keep files whose location matches the resolved subfolder pattern.
        if opt["filter_structure"] and opt["structure_pattern"] and workspace:
            files = self._filter_by_folder_structure(files, workspace, opt["structure_pattern"], suffix)

        # Text filter — match file names and/or notes per the target (honoring ignore-case).
        text = opt["filter_text"]
        if text:
            include_files = opt["target"] in ("Filter: All", "Filter: Files")
            include_notes = opt["target"] in ("Filter: All", "Filter: Notes")
            patterns = text.replace(";", ",")  # ptk.filter_list splits on "," only
            name_matches = set()
            if include_files:
                names = [os.path.basename(f) for f in files]
                name_matches = set(ptk.filter_list(names, inc=patterns, ignore_case=opt["ignore_case"]))
            kept = []
            for f in files:
                ok = include_files and os.path.basename(f) in name_matches
                if not ok and include_notes:
                    note = self._notes.get(os.path.normpath(f).lower(), "")
                    ok = bool(note) and self._note_matches(note, patterns, opt["ignore_case"])
                if ok:
                    kept.append(f)
            files = kept
        return files

    def _filter_by_folder_structure(self, files, workspace, pattern, suffix):
        """Keep files whose directory matches the subfolder pattern ({name} / {workspace} / {suffix})."""
        workspace_name = os.path.basename(os.path.normpath(workspace))
        kept = []
        for f in files:
            try:
                rel_dir = os.path.relpath(os.path.dirname(f), workspace)
            except ValueError:  # different drive on Windows
                continue
            base = os.path.splitext(os.path.basename(f))[0]
            name_for_path = base[: -len(suffix)] if suffix and base.endswith(suffix) else base
            try:
                expected = ptk.StrUtils.replace_placeholders(
                    pattern, name=name_for_path, workspace=workspace_name, suffix=suffix
                )
            except ValueError:  # malformed pattern (e.g. unbalanced brace) — don't crash the refresh
                continue
            rel_parts = os.path.normcase(os.path.normpath(rel_dir)).split(os.sep)
            exp_parts = os.path.normcase(os.path.normpath(expected)).split(os.sep)
            # Match if the file's directory ends with the expected structure (file may sit deeper).
            if len(rel_parts) >= len(exp_parts) and rel_parts[-len(exp_parts):] == exp_parts:
                kept.append(f)
        return kept

    @staticmethod
    def _note_matches(note, patterns, ignore_case):
        """True if the note (or any comma/semicolon segment of it) matches the filter (mirror of Maya)."""
        segments = [note]
        for delim in (",", ";"):
            expanded = []
            for s in segments:
                expanded.extend(p.strip() for p in s.split(delim) if p.strip())
            segments = expanded
        return bool(ptk.filter_list([note] + segments, inc=patterns, ignore_case=ignore_case))

    def _format_display_name(self, path, opt):
        """Displayed file name with the suffix / extension optionally stripped (mirror of Maya)."""
        name = os.path.basename(path)
        if opt["hide_extension"]:
            name = os.path.splitext(name)[0]
        if opt["hide_suffix"] and opt["suffix"]:
            name = name.replace(opt["suffix"], "")
        return name

    def _apply_notes_column_visibility(self):
        """Show/hide the Notes column (index 2) per the header toggle — hidden by default, like Maya.

        View-only: the notes data is still loaded and remains filterable while the column is hidden.
        """
        header = getattr(self.ui, "header", None)
        menu = getattr(header, "menu", None) if header else None
        chk = getattr(menu, "chk_show_notes_column", None) if menu else None
        show = chk.isChecked() if chk is not None else False
        table = getattr(self.ui, "tbl000", None)
        if table is not None:
            table.setColumnHidden(self.COL_NOTES, not show)

    def _on_naming_field_changed(self):
        """Re-filter when a suffix / subfolder edit affects an active filter (mirror of Maya)."""
        header = getattr(self.ui, "header", None)
        menu = getattr(header, "menu", None) if header else None
        if not menu:
            return
        for name in ("chk_filter_suffix", "chk_hide_suffix", "chk_filter_folder_structure"):
            chk = getattr(menu, name, None)
            if chk is not None and chk.isChecked():
                self._refresh()
                return

    def _on_item_changed(self, item):
        """Persist a Notes-column edit, keyed by the row's file path."""
        if self._suppress_note_save or item is None or item.column() != self.COL_NOTES:
            return
        path = item.data(self.sb.QtCore.Qt.UserRole)
        if not path:
            return
        key = os.path.normpath(path).lower()
        text = item.text().strip()
        if text:
            self._notes[key] = text
        else:
            self._notes.pop(key, None)
        self.ui.settings.setValue("reference_notes", self._notes)

    # ------------------------------------------------------------------ selection helpers
    def _selected_paths(self):
        """Absolute .blend paths behind the selected (or current) rows."""
        table = self.ui.tbl000
        rows = {idx.row() for idx in table.selectedIndexes()}
        if not rows and table.currentRow() >= 0:
            rows = {table.currentRow()}
        paths = []
        for r in sorted(rows):
            item = table.item(r, 0)
            path = item.data(self.sb.QtCore.Qt.UserRole) if item else None
            if path and path not in paths:
                paths.append(path)
        return paths

    def _library_for_path(self, path):
        """The linked library datablock whose file is ``path`` (or None)."""
        target = os.path.normpath(path).lower()
        for rec in btk.list_libraries():
            if rec["abspath"] and rec["abspath"].lower() == target:
                return rec["library"]
        return None

    def _selected_libraries(self):
        return [lib for lib in map(self._library_for_path, self._selected_paths()) if lib is not None]

    # ------------------------------------------------------------------ scene file ops
    def open_selected(self):
        """Open the selected .blend (replaces the current file; confirms if unsaved)."""
        paths = self._selected_paths()
        if not paths:
            self.sb.message_box("Select a file in the list first.")
            return
        self._open_path(paths[0])

    def _open_path(self, path):
        """Open ``path`` (replaces the current file), confirming first if there are unsaved changes."""
        import bpy

        if getattr(bpy.data, "is_dirty", False):
            if self.sb.message_box(
                "The current file has unsaved changes — open anyway?", "Yes", "No"
            ) != "Yes":
                return
        if btk.open_scene(path):
            self.sb.message_box(f"Opened <hl>{os.path.basename(path)}</hl>.")
        else:
            self.sb.message_box("Failed to open the file.")

    def save_scene(self):
        """Save the current scene into the workspace with the header naming conventions."""
        workspace = self._workspace_dir()
        if not (workspace and os.path.isdir(workspace)):
            self.sb.message_box("Set a valid workspace folder first.")
            return
        menu = self.ui.header.menu
        name = self.sb.input_dialog("Save Scene", "Enter a name for the scene:", "")
        if not name:
            return
        path = btk.save_scene_as(
            workspace, name,
            case=menu.cmb_case_style.currentText(),
            suffix=menu.txt_suffix.text(),
            subfolder=menu.txt_subfolder.text().strip(),
        )
        self.sb.message_box(
            f"Saved <hl>{os.path.basename(path)}</hl>." if path else "Failed to save the scene."
        )
        self._refresh()

    def rename_selected(self):
        """Rename the selected .blend on disk."""
        paths = self._selected_paths()
        if not paths:
            self.sb.message_box("Select a file to rename.")
            return
        self._rename_path(paths[0])

    def _rename_path(self, old):
        """Prompt for a new name and rename ``old`` on disk, then refresh."""
        base = os.path.splitext(os.path.basename(old))[0]
        new_base = self.sb.input_dialog("Rename Scene", "Enter the new name:", base)
        if not new_base or new_base == base:
            return
        new_path = btk.rename_scene_file(old, new_base)
        self.sb.message_box(
            f"Renamed to <hl>{os.path.basename(new_path)}</hl>." if new_path
            else "Rename failed (a file with that name may already exist)."
        )
        self._refresh()

    def delete_selected(self):
        """Delete the selected .blend file(s) from disk (confirmed)."""
        paths = [p for p in self._selected_paths() if os.path.isfile(p)]
        if not paths:
            self.sb.message_box("Select a file to delete.")
            return
        if self.sb.message_box(f"Delete {len(paths)} file(s) from disk?", "Yes", "No") != "Yes":
            return
        done = sum(1 for p in paths if btk.delete_scene_file(p))
        self.sb.message_box(f"Deleted <hl>{done}</hl> file(s).")
        self._refresh()

    def open_location_selected(self):
        """Reveal the selected .blend in the OS file manager (any row)."""
        paths = self._selected_paths()
        if not paths:
            self.sb.message_box("Select a file in the list first.")
            return
        try:
            ptk.FileUtils.reveal_in_file_manager(paths[0])
        except (FileNotFoundError, OSError) as e:
            self.sb.message_box(str(e))

    # ------------------------------------------------------------------ reference ops
    def reference_selected(self, link=True):
        """Link (or Append) the selected .blend file(s) as references."""
        paths = self._selected_paths()
        if not paths:
            self.sb.message_box("Select a .blend file in the list first.")
            return
        verb = "Linked" if link else "Appended"
        total = 0
        for path in paths:
            try:
                total += btk.link_blend_file(path, link=link)
            except (RuntimeError, OSError) as e:
                self.sb.message_box(str(e))
        self.sb.message_box(f"{verb} <hl>{total}</hl> collection(s) from {len(paths)} file(s).")
        self._refresh()

    def reload_selected(self):
        """Reload the selected file's library from disk."""
        done = sum(1 for lib in self._selected_libraries() if btk.reload_library(lib))
        self.sb.message_box(
            f"Reloaded <hl>{done}</hl> library(ies)." if done else "No linked library for the selection."
        )
        self._refresh()

    def relocate_selected(self):
        """Point the selected file's library at a different .blend (native file browser)."""
        import bpy

        libs = self._selected_libraries()
        if not libs:
            self.sb.message_box("Select an already-linked row to relocate.")
            return
        try:
            bpy.ops.wm.lib_relocate("INVOKE_DEFAULT", library=libs[0].name)
        except RuntimeError as e:
            self.sb.message_box(str(e))

    def make_local_selected(self):
        """Make the selected file's library data local (Maya's per-reference Import)."""
        libs = self._selected_libraries()
        if not libs:
            self.sb.message_box("Select an already-linked row to make local.")
            return
        total = sum(btk.make_library_local(lib) for lib in libs)
        self.sb.message_box(f"Made <hl>{total}</hl> datablock(s) local.")
        self._refresh()

    def remove_selected(self):
        """Remove the selected file's library (and everything linked from it)."""
        libs = self._selected_libraries()
        if not libs:
            self.sb.message_box("No linked library for the selection.")
            return
        if self.sb.message_box(f"Remove {len(libs)} linked library(ies)?", "Yes", "No") != "Yes":
            return
        done = sum(1 for lib in libs if btk.remove_library(lib))
        self.sb.message_box(f"Removed <hl>{done}</hl> library(ies).")
        self._refresh()

    def set_display(self, mode):
        """Set the per-reference display mode (off / reference / template) on the selection."""
        libs = self._selected_libraries()
        if not libs:
            self.sb.message_box("Select an already-linked row to set its display mode.")
            return
        done = sum(1 for lib in libs if btk.set_reference_display_mode(lib, mode))
        self.sb.message_box(f"Set display to <hl>{mode}</hl> on {done} reference(s).")

    # ------------------------------------------------------------------ bulk ops (header menu)
    def reload_all(self):
        """Reload every linked library from disk (Maya's Update References)."""
        done = sum(1 for rec in btk.list_libraries() if btk.reload_library(rec["library"]))
        self.sb.message_box(
            f"Reloaded <hl>{done}</hl> library(ies)." if done else "No linked libraries to reload."
        )
        self._refresh()

    def make_local_all(self):
        """Make every linked library's data local (Maya's Unlink-and-Import All)."""
        recs = btk.list_libraries()
        if not recs:
            self.sb.message_box("No linked libraries to make local.")
            return
        if self.sb.message_box(f"Make {len(recs)} library(ies) local?", "Yes", "No") != "Yes":
            return
        total = sum(btk.make_library_local(rec["library"]) for rec in recs)
        self.sb.message_box(f"Made <hl>{total}</hl> datablock(s) local.")
        self._refresh()

    def remove_all(self):
        """Remove every linked library and its data (Maya's Un-Reference All)."""
        recs = btk.list_libraries()
        if not recs:
            self.sb.message_box("No linked libraries to remove.")
            return
        if self.sb.message_box(f"Remove ALL {len(recs)} linked library(ies)?", "Yes", "No") != "Yes":
            return
        done = sum(1 for rec in recs if btk.remove_library(rec["library"]))
        self.sb.message_box(f"Removed <hl>{done}</hl> library(ies).")
        self._refresh()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("reference_manager", reload=True)
    ui.show(pos="screen", app_exec=True)
