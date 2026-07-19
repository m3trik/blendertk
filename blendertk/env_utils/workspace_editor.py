# !/usr/bin/python
# coding=utf-8
"""blendertk Workspace Editor — the minimal take on Maya's File ▸ Project Window: one
project-root field, one file-rules table, and rule edits that write through to
``workspace.mel`` in real time (no Accept step).

Blender has no native project system, so this panel IS the manager; Maya needs no twin
(its Project Window is native — the tentacle Workspace tabs pair at the row level).

- **Project Root** — a single path field. Browsing/typing an existing workspace loads its
  rules and pins it as the current workspace (no separate Set-Project button); a fresh path
  shows the active template, and the project (marker + folders) is created — and pinned —
  the moment a rule is edited. Option-box icons: browse + open in file browser.
- **Rule table** — RULE ▸ LOCATION (LOCATION stretches to fill; the icon columns stay
  pinned right) plus per-row icon actions (reset the rule to its template default / remove
  it — ``TableWidget.actions`` columns). Every committed cell edit, add, remove, reset, or
  template load saves immediately (merge-preserving: hand-written ``workspace.mel`` lines
  survive; removed rows are deleted).
- **Nice Names ↔ File Rules view** (Maya's Edit ▸ View toggle, header menu) — rows show
  Maya's own Project Window labels (``ptk.RULE_NICE_NAMES``, same order) or the raw rule
  keys; raw view makes the rule column editable.
- **Header menu** — Add New File Rule, the view toggle, Reset/Clear Settings (Maya's Edit
  menu verbs, applied immediately), and the **template combo**: the canonical
  ``uitk.PresetManager.wire_combo`` selector (Refresh/Save icons + Rename/Open/Delete menu)
  over the same ``ptk.PresetStore`` the headless ``btk.workspace_template_*`` API reads —
  the ACTIVE template is what every subsequent new workspace is built from
  (``btk.create_workspace`` and fresh paths here).

The panel opens persistent (a header **hide** button, not a gesture ``pin``), so it stays
open while the user works.

Engine logic is Qt-free and lives upstream (``pythontk.file_utils.workspace`` +
``_env_utils``); this module is the thin Qt driver (uitk/qtpy imports deferred per the
headless rule — the panel surface must import without Qt or bpy).
"""
import os

import pythontk as ptk

import blendertk as btk


class WorkspaceEditorSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Workspace Editor (Project Window) panel."""

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.workspace_editor
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[workspace_editor] ")
        self._loaded_rules = {}  # rules as last written/read — the removal-diff baseline
        self._nice_view = True  # Maya's default: View in Nice names
        self._updating = False  # suppress write-through during programmatic populates
        self._preset_mgr = None  # uitk PresetManager over the workspace-template store

    # ------------------------------------------------------------------ header
    def header_init(self, widget):
        """Rule add, view toggle, reset/clear, template combo, help text."""
        from uitk.widgets.comboBox import ComboBox
        from uitk.widgets.mixins.tooltip_mixin import fmt
        from uitk.managers.preset_manager import PresetManager
        from blendertk.env_utils import _env_utils

        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self._load)
        widget.menu.add("Separator", setTitle="File Rules:")
        widget.menu.add(
            "QPushButton", setText="Add New File Rule", setObjectName="btn_add_rule",
            setToolTip="Append an empty rule row (Maya's Custom Data Locations ▸ Add new\n"
            "file rule); it saves once both cells are filled in.",
        ).clicked.connect(self.add_rule)
        widget.menu.add(
            "QCheckBox", setText="View File Rules", setObjectName="chk_raw_names",
            setChecked=False,
            setToolTip="Show raw file-rule keys instead of nice names, and make the rule\n"
            "column editable (Maya's Edit ▸ View in File Rules).",
        ).toggled.connect(self._on_view_toggled)
        widget.menu.add("Separator", setTitle="Edit:")
        widget.menu.add(
            "QPushButton", setText="Reset Settings", setObjectName="btn_reset",
            setToolTip="Restore the default file rules — the active template's — and save\n"
            "(Maya's Edit ▸ Reset Settings, applied immediately).",
        ).clicked.connect(self.reset_rules)
        widget.menu.add(
            "QPushButton", setText="Clear Settings", setObjectName="btn_clear",
            setToolTip="Remove every file rule from the project and save (Maya's Edit ▸\n"
            "Clear Settings, applied immediately; hand-written lines survive).",
        ).clicked.connect(self.clear_rules)
        widget.menu.add("Separator", setTitle="Templates:")
        combo = widget.menu.add(
            ComboBox, setObjectName="cmb_template",
            setToolTip="Workspace templates: picking one loads its rules into the project\n"
            "and makes it the ACTIVE default every new workspace is built from\n"
            "(btk.create_workspace, fresh paths here). Icons save the current\n"
            "rules as a template; the menu renames/deletes.",
        )
        store = _env_utils._workspace_template_store()
        self._preset_mgr = PresetManager(
            preset_dir=str(store.user_dir),
            value_provider=self._gather,
            value_applier=self._apply_template,
        )
        self._preset_mgr.wire_combo(combo, placeholder="Templates…")
        widget.set_help_text(
            fmt(
                title="Workspace Editor (Project Window)",
                body="Define a shared Maya/Blender project workspace — the mirror of "
                "Maya's File ▸ Project Window. File rules map a location (Scenes, "
                "Source Images, …) to a folder; both DCCs resolve through the rules, "
                "never through hardcoded folder names. Edits save to workspace.mel "
                "immediately — there is no Accept step.",
                steps=[
                    "Set the <b>Project Root</b> (▸ browses; an existing workspace "
                    "loads its rules and becomes the current workspace, a fresh path "
                    "shows the active template).",
                    "Edit the rule table (<b>double-click</b> a location cell; the row "
                    "icons reset a rule to its template default or remove it). The "
                    "first edit on a fresh path creates the project.",
                    "The header menu adds rules, toggles Nice Names ↔ File Rules, "
                    "and resets/clears the rules.",
                    "The <b>Templates</b> combo saves/loads named rule sets; the "
                    "active one defines how every new workspace is built.",
                ],
            )
        )

    # ------------------------------------------------------------------ fields
    def txt000_init(self, widget):
        """Project Root — one full path (browse + open-folder option-box actions)."""
        if not getattr(widget, "is_initialized", False):
            widget.option_box.browse(
                mode="directory", title="Select Project Root",
                tooltip="Browse for the project root folder (an existing workspace\n"
                "loads its rules).",
            )
            widget.option_box.add_action(
                callback=self.open_folder, icon="open_external",
                tooltip="Open the project root in the system file browser.",
            )
        last = self.ui.settings.value("workspace_root") or ""
        ws = btk.current_workspace()
        if ws is not None:
            last = ws.root
        if last and not widget.text():
            widget.setText(last)
        widget.textChanged.connect(self._on_root_changed)
        widget.returnPressed.connect(self._load)

    def tbl000_init(self, widget):
        if not getattr(widget, "is_initialized", False):
            widget.actions.add(
                column=2,
                states={
                    "reset": {
                        "icon": "undo",
                        "tooltip": "Reset this rule to its template default.",
                        "action": lambda row, col: self.reset_row(row),
                    }
                },
            )
            widget.actions.add(
                column=3,
                states={
                    "remove": {
                        "icon": "trash",
                        "tooltip": "Remove this rule (deleted from workspace.mel).",
                        "action": lambda row, col: self.remove_row(row),
                    }
                },
            )
            widget.itemChanged.connect(self._on_item_changed)
        self._load()

    # ------------------------------------------------------------------ root resolution
    def _root(self):
        """The project root path ('' when unset)."""
        widget = getattr(self.ui, "txt000", None)
        return widget.text().strip() if widget is not None else ""

    def _on_root_changed(self, text):
        path = (text or "").strip()
        self.ui.settings.setValue("workspace_root", path)
        ws = ptk.Workspace.load(path) if path else None
        if ws is not None and ws.is_marked:
            self._load()
            self._set_current(path)  # selecting an existing project root pins it
        elif self._loaded_rules:
            # Retargeted from an existing project to an unbuilt path: show a fresh
            # template-seeded definition (nothing written until a rule is edited).
            self._loaded_rules = {}
            self._populate(btk.workspace_template_rules())

    def _set_current(self, root):
        """Pin *root* as the session's current workspace — done automatically when a
        project root is selected or first built here (Maya's Set Project analogue), so the
        panel needs no explicit button."""
        if root and os.path.isdir(root):
            btk.set_current_workspace(root)

    # ------------------------------------------------------------------ table
    def _load(self):
        """(Re)load the rule table from the project's workspace.mel — or the active
        template for a project that isn't built yet."""
        table = getattr(self.ui, "tbl000", None)
        if table is None:
            return
        root = self._root()
        ws = ptk.Workspace.load(root) if root else None
        self._loaded_rules = dict(ws.rules) if ws is not None and ws.is_marked else {}
        self._populate(self._loaded_rules or btk.workspace_template_rules())

    def _ordered(self, rules):
        """Rules in Maya's Project Window display order (``RULE_NICE_NAMES`` order for the
        known vocabulary, then customs alphabetically)."""
        known = [k for k in ptk.RULE_NICE_NAMES if k in rules]
        custom = sorted(k for k in rules if k not in ptk.RULE_NICE_NAMES)
        return [(k, rules[k]) for k in known + custom]

    def _populate(self, rules):
        from qtpy import QtCore

        table = self.ui.tbl000
        ordered = self._ordered(rules)
        rows = [
            [ptk.RULE_NICE_NAMES.get(key, key) if self._nice_view else key, folder, "", ""]
            for key, folder in ordered
        ] or [["", "", "", ""]]  # Clear keeps the table shape + one blank starter row
        self._updating = True
        try:
            table.add(rows, headers=["RULE:", "LOCATION:", "", ""])  # add() clears
            for r in range(table.rowCount()):
                rule_item = table.item(r, 0)
                folder_item = table.item(r, 1)
                key = ordered[r][0] if r < len(ordered) else None
                if rule_item is not None:
                    if key is not None:
                        rule_item.setData(QtCore.Qt.UserRole, key)
                    flags = rule_item.flags()
                    if self._nice_view and key is not None:
                        rule_item.setFlags(flags & ~QtCore.Qt.ItemIsEditable)
                    else:
                        rule_item.setFlags(flags | QtCore.Qt.ItemIsEditable)
                if folder_item is not None:
                    folder_item.setFlags(folder_item.flags() | QtCore.Qt.ItemIsEditable)
                self._set_row_actions(r)
            self._apply_column_sizing()
        finally:
            self._updating = False

    def _apply_column_sizing(self):
        """LOCATION fills the row width; the reset/remove action icons stay pinned to the
        far right at row height. Re-applied after every ``add()`` (which resets column
        state — TableActions re-fixes its own columns, this re-stretches LOCATION)."""
        from qtpy import QtWidgets

        header = self.ui.tbl000.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)  # RULE
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)  # LOCATION

    def _set_row_actions(self, row):
        """Attach the per-row reset/remove action icons."""
        table = self.ui.tbl000
        table.actions.set(row, 2, "reset")
        table.actions.set(row, 3, "remove")

    def _key_at(self, row):
        """The raw rule key of *row* — from UserRole in nice-name view (labels aren't
        keys), from the cell text in file-rules view (where the key column is editable)."""
        from qtpy import QtCore

        item = self.ui.tbl000.item(row, 0)
        if item is None:
            return ""
        if self._nice_view:
            key = item.data(QtCore.Qt.UserRole) or item.text()
        else:
            key = item.text()
        return str(key or "").strip()

    def _gather(self):
        """The table's rules as a dict; blank/incomplete rows are skipped."""
        table = getattr(self.ui, "tbl000", None)
        if table is None:
            return {}
        rules = {}
        for r in range(table.rowCount()):
            key = self._key_at(r)
            folder_item = table.item(r, 1)
            folder = (folder_item.text() if folder_item else "").strip()
            if key and folder:
                rules[key] = folder
        return rules

    def _on_view_toggled(self, raw):
        """Nice Names ↔ File Rules (Maya's Edit ▸ View toggle) — re-renders the current
        table content; no write (the rules are unchanged)."""
        rules = self._gather()
        self._nice_view = not bool(raw)
        self._populate(rules)

    # ------------------------------------------------------------------ write-through
    def _write(self):
        """Save the table to the project's workspace.mel — the real-time Accept. Creates
        the project (marker + rule folders) on the first rule edit of a fresh path."""
        root = self._root()
        if not root:
            self.ui.footer.setStatusText("Set a project root to save rule edits.")
            return False
        rules = self._gather()
        removed = set(self._loaded_rules) - set(rules)
        if not rules and not removed:
            return False  # empty definition on an unbuilt path — nothing to write
        try:
            ws = ptk.Workspace(root, rules)
            ws.save(create_dirs=True, remove=removed)
        except OSError as e:
            self.sb.message_box(str(e))
            return False
        self._loaded_rules = dict(rules)
        self._set_current(root)  # a just-built project becomes the current workspace
        if self._preset_mgr is not None:
            self._preset_mgr.refresh_modified_state()
        self.ui.footer.setStatusText(
            f"Saved {os.path.basename(os.path.normpath(root))}"
        )
        return True

    def _on_item_changed(self, item):
        """A committed user cell edit saves immediately."""
        if not self._updating:
            self._write()

    # ------------------------------------------------------------------ row actions
    def add_rule(self):
        """Append an empty, editable rule row (saves once both cells are filled)."""
        from qtpy import QtCore, QtWidgets

        table = self.ui.tbl000
        r = table.rowCount()
        self._updating = True
        try:
            table.insertRow(r)
            for c in range(2):
                item = QtWidgets.QTableWidgetItem("")
                item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
                table.setItem(r, c, item)
            self._set_row_actions(r)
        finally:
            self._updating = False
        table.editItem(table.item(r, 0))

    def reset_row(self, row):
        """Reset *row*'s location to the active template's default for its rule."""
        key = self._key_at(row)
        default = btk.workspace_template_rules().get(key)
        if default is None:
            self.ui.footer.setStatusText(f"No template default for '{key or '(blank)'}'.")
            return
        item = self.ui.tbl000.item(row, 1)
        if item is not None and item.text() != default:
            item.setText(default)  # itemChanged → write-through

    def remove_row(self, row):
        """Remove *row* and delete its rule from workspace.mel."""
        table = self.ui.tbl000
        self._updating = True
        try:
            table.removeRow(row)
            for r in range(table.rowCount()):
                self._set_row_actions(r)  # re-sync action states to the shifted rows
        finally:
            self._updating = False
        self._write()

    def reset_rules(self):
        """Restore the default file rules — the active template's — and save."""
        self._populate(btk.workspace_template_rules())
        self._write()

    def clear_rules(self):
        """Remove every file rule and save (hand-written marker lines survive)."""
        self._populate({})
        self._write()

    # ------------------------------------------------------------------ project actions
    def open_folder(self):
        """Open the project root in the system file browser."""
        root = self._root()
        if not (root and os.path.isdir(root)):
            self.sb.message_box("The project doesn't exist yet — edit a rule to create it.")
            return
        try:
            ptk.FileUtils.reveal_in_file_manager(root)
        except (FileNotFoundError, OSError) as e:
            self.sb.message_box(str(e))

    # ------------------------------------------------------------------ templates
    def _apply_template(self, rules):
        """PresetManager value applier: load a template's rules into the project."""
        rules = {str(k): str(v) for k, v in rules.items() if k != "_meta"}
        self._populate(rules)
        self._write()
        return len(rules)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("workspace_editor", reload=True)
    ui.show(pos="screen", app_exec=True)
