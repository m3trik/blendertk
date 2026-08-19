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
**open** icon to open the scene (a foreign scene is baked and opened as a new file; the current
scene is highlighted + italicized), and the tri-state **display** icon to cycle Normal → Reference
→ Template. The row **context menu** is a flat 1:1 mirror of Maya's — Open / Rename / Delete /
Reference-Unreference / Unlink-and-Import / Open File Location — where **Unlink and Import** makes a
linked reference local *or* converts + imports a foreign (Maya / FBX) scene.

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
    _DISPLAY_MODE_CYCLE = {
        "off": "reference",
        "reference": "template",
        "template": "off",
    }

    # File-type classification for this panel (mirror of mayatk, inverted). NATIVE types list
    # + link directly (Blender links .blend); FOREIGN types are cross-DCC rows converted through
    # the maya_bridge before they can be linked. The header's Include Types row toggles each
    # extension; _INCLUDE_TYPES is the column order (shared across both panels).
    _INCLUDE_TYPES = ("ma", "mb", "fbx", "blend")
    NATIVE_EXTENSIONS = (".blend",)
    # Foreign types the bridge can bake into a linkable .blend. .ma/.mb go through a headless
    # Maya first; .fbx is already the bake's own input, so it needs no Maya at all.
    FOREIGN_EXTENSIONS = (".ma", ".mb", ".fbx")
    # Default-checked include types for this panel — its own native scene type.
    _INCLUDE_DEFAULTS = (".blend",)
    # Max file names listed verbatim in the delete confirmation (the rest fold into
    # an "...and N more" line).
    DELETE_PROMPT_MAX_NAMES = 10

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.reference_manager
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[reference_manager] ")
        self._recursive = (
            True  # search sub-folders too by default — mirrors Maya's chk000 (checked)
        )
        self._notes = dict(self.ui.settings.value("reference_notes") or {})
        self._suppress_note_save = False  # guard programmatic table edits
        self._setup_footer_actions()

    def _setup_footer_actions(self):
        """Add the footer bulk-clear button — a 1:1 mirror of mayatk's footer 'Un-Reference All'.

        Same widget + placement + label as Maya so the two panels' footers are identical; the
        callback is Blender's ``remove_all`` (remove every linked library), the analogue of Maya's
        ``btn_unreference_all``.
        """
        footer = getattr(self.ui, "footer", None)
        if footer is None or not hasattr(footer, "add_widget"):
            return
        btn = self.sb.QtWidgets.QPushButton("Un-Reference All", footer)
        btn.setToolTip("Remove all references (linked libraries) from the scene.")
        btn.setCursor(self.sb.QtGui.QCursor(self.sb.QtCore.Qt.ArrowCursor))
        btn.setFixedHeight(max(footer.height() - 2, 1))
        btn.clicked.connect(self.remove_all)
        footer.add_widget(btn, side="right", background=True)

    # ------------------------------------------------------------------ bpy availability
    @staticmethod
    def _has_bpy():
        try:
            import bpy  # noqa: F401

            return True
        except Exception:
            return False

    def _scenes_folder(self, workspace) -> str:
        """The workspace's ``scene`` rule folder — the ``{scenes}`` placeholder value.
        Falls back to the literal ``"scenes"`` (same lookup as ``save_scene_as`` and the
        folder-structure filter). Shared by the filter and the live preview."""
        try:
            scene_rule = ptk.Workspace.load(workspace).rules.get("scene")
        except Exception:
            scene_rule = None
        return (
            scene_rule
            if scene_rule and not os.path.isabs(scene_rule)
            else "scenes"
        )

    def _folder_structure_preview(self) -> str:
        """Live tooltip for ``txt_subfolder_structure`` — resolve the placeholders
        against the current workspace + scene so the hover shows the real Save dir
        (mirror of mayatk). Side-effect-free: the ``{scene}`` typo is corrected
        locally and surfaced as a note rather than mutating state."""
        menu = getattr(getattr(self.ui, "header", None), "menu", None)
        txt = getattr(menu, "txt_subfolder_structure", None) if menu else None
        pattern = txt.text().strip() if txt is not None else ""

        workspace = self._workspace_dir() or ""
        workspace_name = (
            os.path.basename(os.path.normpath(workspace))
            if workspace
            else "<workspace>"
        )
        suffix_w = getattr(menu, "txt_suffix", None) if menu else None
        suffix = (suffix_w.text() if suffix_w is not None else "") or ""
        case_w = getattr(menu, "cmb_case_style", None) if menu else None
        case_style = case_w.currentText() if case_w is not None else "None"

        # {name} = the current .blend base, formatted the way Save would.
        name_val = "<scene name>"
        if self._has_bpy():
            import bpy

            fp = bpy.data.filepath
            if fp:
                base = os.path.splitext(os.path.basename(fp))[0]
                if suffix and base.endswith(suffix):
                    base = base[: -len(suffix)]
                name_val = btk.format_scene_name(base, case_style, "")

        notes = ["e.g. {scenes} · {scenes}/{name} · {scenes}/{name}/versions"]
        if "{scene}" in pattern:
            notes.append("<b>{scene}</b> is not valid — did you mean <b>{scenes}</b>?")
        resolve_pattern = pattern.replace("{scene}", "{scenes}")

        context = {
            "scenes": self._scenes_folder(workspace) if workspace else "scenes",
            "name": name_val,
            "workspace": workspace_name,
            "suffix": suffix,
        }
        try:
            rel = ptk.StrUtils.replace_placeholders(resolve_pattern, **context)
            final = os.path.join(workspace, rel) if workspace else rel
        except ValueError:
            final = None

        # Fold the field's help text into the live tooltip (binding replaces the
        # static setToolTip): purpose + what each placeholder means + its value.
        return self.sb.tooltip.placeholder_preview(
            resolve_pattern,
            context,
            title="Folder Structure",
            body="Subfolder pattern for <b>Save To Workspace</b> — also drives the "
            "<b>Filter by Folder Structure</b> option.",
            descriptions={
                "scenes": "workspace scenes folder (scene file rule)",
                "name": "scene name — excludes the suffix",
                "workspace": "workspace folder name",
                "suffix": "the Suffix field above",
            },
            final=final,
            final_label="save dir →",
            notes=notes,
        )

    def _wire_structure_tooltip(self, menu) -> None:
        """Bind the live folder-structure preview once the menu widget's ``.tooltip``
        proxy is stamped. Menu registration is deferred (coalesced next-tick drain),
        so bind on the following tick with a small bounded retry (mirror of mayatk)."""
        from qtpy import QtCore

        def _bind(attempts_left=5):
            txt = getattr(menu, "txt_subfolder_structure", None)
            proxy = getattr(txt, "tooltip", None) if txt is not None else None
            if proxy is not None:
                proxy.bind(self._folder_structure_preview)
            elif attempts_left > 0:
                QtCore.QTimer.singleShot(0, lambda: _bind(attempts_left - 1))

        QtCore.QTimer.singleShot(0, _bind)

    # ------------------------------------------------------------------ header
    def header_init(self, widget):
        """Header refresh button, Naming presets, Filter/Display toggles, bulk Operations, help text.

        Items + layout are a 1:1 mirror of mayatk's header (Naming / Filter-Display / Operations).
        Workspace management (New / Mark As Workspace) and Recursive scanning live on the Root
        Directory field's option box — Maya keeps neither in this header.
        """
        # Gesture-scoped window: pin button + auto-hide on key_show release. Runs on every call
        # (declarative, cheap) and the signal is re-wired idempotently because the header QWidget
        # can outlive this slots instance — a bare .connect() on a second call (a genuine reload,
        # or the offscreen test harness's documented "drive *_init explicitly" pattern) would
        # leave a stale connection bound to a dead ``self`` alongside the live one. Goes through
        # ``_rewire_signal`` (drops only OUR prior connection): a blanket ``disconnect()`` makes
        # libpyside warn "Failed to disconnect (None) from signal" on the first, unconnected call.
        widget.config_buttons("refresh", "menu", "collapse", "pin")
        self._rewire_signal(widget, widget.refresh_requested, self._refresh, "hdr_refresh")

        # One-time menu build: repeated calls (see above) must not re-append every Naming /
        # Filter / Include-Types control — the user-reported duplicate header controls. Only the
        # widget CONSTRUCTION is guarded; config_buttons + the signal above stay outside so a
        # reload still re-targets them at the current ``self``.
        if widget.is_initialized:
            return
        # Save / load the header menu's naming + filter settings as named presets (mirror of Maya).
        widget.menu.add_presets = True
        widget.menu.presets.preset_dir = "blendertk/reference_manager"

        # Naming conventions for Save Scene (mirror of Maya's case / suffix / subfolder structure).
        widget.menu.add("Separator", setTitle="Naming:")
        widget.menu.add(
            "QComboBox",
            addItems=list(_CASE_STYLES),
            setObjectName="cmb_case_style",
            setToolTip="Case convention applied to the file name on Save.",
        )
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_suffix",
            setPlaceholderText="Suffix (e.g. _v01)…",
            setToolTip="Suffix appended to the file name on Save (excluded from case formatting).",
        )
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_subfolder_structure",
            setText="{scenes}",
            setPlaceholderText="Folder Structure (e.g. {scenes}/{name})…",
            setToolTip="Folder structure for Save — also drives the Folder-Structure filter.\n"
            "placeholders: {scenes}, {name}, {workspace}, {suffix}.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Save To Workspace",
            setObjectName="btn_save_scene",
            setToolTip="Save the current scene into the workspace using the naming conventions above.",
        ).clicked.connect(self.save_scene)
        # Live tooltip: hovering the Folder Structure field shows the placeholders
        # resolved against the current workspace + scene, plus the real save dir.
        self._wire_structure_tooltip(widget.menu)

        # Filter / Display options (mirror of Maya's header filter checkboxes). Each re-filters the
        # list; Show Notes Column is a view-only toggle (Notes hidden by default, like Maya).
        # (Ignore-Case + filter target + the enable toggle live on the Filter field's option box.)
        widget.menu.add("Separator", setTitle="Filter / Display:")
        widget.menu.add(
            "QCheckBox",
            setText="Filter by Suffix",
            setObjectName="chk_filter_suffix",
            setChecked=False,
            setToolTip="Show only files whose name ends with the Suffix above.",
        ).toggled.connect(lambda *_: self._refresh())
        widget.menu.add(
            "QCheckBox",
            setText="Filter by Folder Structure",
            setObjectName="chk_filter_folder_structure",
            setChecked=False,
            setToolTip="Show only files whose location matches the Subfolder pattern above.",
        ).toggled.connect(lambda *_: self._refresh())
        widget.menu.add(
            "QCheckBox",
            setText="Hide Suffix",
            setObjectName="chk_hide_suffix",
            setChecked=False,
            setToolTip="Hide the suffix from the displayed file name.",
        ).toggled.connect(lambda *_: self._refresh())
        widget.menu.add(
            "QCheckBox",
            setText="Hide Extension",
            setObjectName="chk_hide_extension",
            setChecked=False,
            setToolTip="Hide the .blend extension from the displayed file name.",
        ).toggled.connect(lambda *_: self._refresh())
        widget.menu.add(
            "QCheckBox",
            setText="Show Notes Column",
            setObjectName="chk_show_notes_column",
            setChecked=False,
            setToolTip="Show the Notes column (per-file comments / metadata). Hidden by default.",
        ).toggled.connect(lambda *_: self._apply_notes_column_visibility())

        # Foreign-scene conversion route (mirror across both panels). FBX (default):
        # instancing is native to the format on BOTH sides, so shared mesh data
        # survives with no sidecar replay in the path — and when FBX's texture
        # manifest does fail, the loss is VISIBLE (classic-model materials) and
        # structurally harmless. USD: richer material graphs plus native animation
        # and visibility, but instance relationships are rebuilt from the conversion
        # sidecar, and that rebuild currently fails SILENTLY (see .claude/BACKLOG.md)
        # — a scene that looks correct but no longer shares data. Opt in per scene.
        #
        # Item order is APPEND-ONLY: uitk persists a combo by INDEX, so reordering
        # would retroactively flip every stored pick. The default moves via
        # setCurrentIndex, never by moving items. The objectName was renamed off
        # `cmb_foreign_route` when the default changed, deliberately orphaning the
        # old key so the new default reaches profiles that had already stored one.
        widget.menu.add("Separator", setTitle="Foreign Scenes:")
        widget.menu.add(
            "QComboBox",
            addItems=["Convert via USD", "Convert via FBX"],
            setCurrentIndex=1,  # FBX
            setObjectName="cmb_conversion_route",
            setToolTip=(
                "Intermediate used when opening / importing / referencing a foreign "
                "scene.\n"
                "FBX (default): instancing is carried by the format itself, so a "
                "scene keeps its shared mesh data without a rebuild step.\n"
                "USD: richer materials, plus animation and visibility arrive "
                "natively — but instances are rebuilt from a sidecar. Prefer it for "
                "look-heavy scenes, and check instancing survived."
            ),
        )

        # Include Types — a single horizontal row of per-extension toggles (mirror across both
        # panels). Replaces the old single "Include Maya Scenes" toggle: .blend lists + links
        # natively; .ma/.mb list as import-only rows converted through the maya_bridge.
        self._add_include_types_row(widget.menu)
        # Re-filter on a suffix / subfolder edit when a dependent filter is active (mirror of Maya).
        widget.menu.txt_suffix.textChanged.connect(
            lambda *_: self._on_naming_field_changed()
        )
        widget.menu.txt_subfolder_structure.textChanged.connect(
            lambda *_: self._on_naming_field_changed()
        )

        # Bulk operations — a 1:1 mirror of Maya's Operations group. Maya's three are
        # Convert-to-Assembly / Unlink-and-Import-All / Un-Reference-All; Convert-to-Assembly has
        # no Blender analogue (dropped, ledgered), the other two map to Make Local All / Remove All.
        # Per-library Reload lives in the row menu; workspace management (New / Mark As Workspace)
        # lives on the Root Directory option box — Maya keeps neither in this header. Save Scene
        # lives in the Naming group above, beside the conventions it consumes (mirror of Maya).
        widget.menu.add("Separator", setTitle="Operations:")
        widget.menu.add(
            "QPushButton",
            setText="Unlink and Import All",
            setObjectName="btn_unlink_import_all",
            setToolTip="Make every linked library's data local (Maya's Unlink and Import All).",
        ).clicked.connect(self.make_local_all)
        widget.menu.add(
            "QPushButton",
            setText="Un-Reference All",
            setObjectName="btn_unreference_all",
            setToolTip="Remove every linked library and its data (Un-Reference All).",
        ).clicked.connect(self.remove_all)

        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Reference Manager",
                body="A workspace scene-file manager: browse a project's .blend files, open / save / "
                "rename / delete them, and link them as references (Blender libraries).",
                steps=[
                    "Set a <b>Root Directory</b> (▸ to browse / recent); pick a <b>Workspace</b>.",
                    "Click the row's <b>link</b> icon to link/unlink, the <b>open</b> icon to open the "
                    "scene, the <b>display</b> icon to cycle Normal → Reference → Template.",
                    "<b>Double-click</b> the name to rename; right-click for the action menu "
                    "(Open, Rename, Delete, Reference / Unreference, Unlink and Import, …).",
                    "<b>Double-click</b> the <b>Notes</b> column to annotate a file (saved with the panel).",
                ],
                sections=[
                    (
                        "Columns",
                        [
                            "<b>FILES</b> | link | open | display | <b>NOTES</b>. The current scene's row "
                            "is highlighted + italic.",
                        ],
                    ),
                    (
                        "Header menu",
                        [
                            "<b>Naming</b> (case / suffix / folder structure) drives "
                            "<b>Save To Workspace</b>, beside those conventions in the group.",
                            "<b>Filter by Suffix / Folder Structure</b> narrow the list; <b>Hide Suffix / "
                            "Extension</b> shorten the displayed name; <b>Show Notes Column</b> reveals Notes.",
                            "<b>Include Types</b> (ma / mb / fbx / blend) picks which file types list; "
                            ".blend links natively, a foreign (ma / mb / fbx) row's link icon bakes it "
                            "to a cached .blend and links that — right-click <b>Unlink and Import</b> "
                            "for a local copy instead.",
                            "<b>Operations</b>: <b>Unlink and Import All</b>, <b>Un-Reference All</b>.",
                        ],
                    ),
                    (
                        "Root Directory (▸ option box)",
                        [
                            "Browse / recent-dir history; <b>Open Directory</b>; <b>Set To Current "
                            "Workspace</b>; <b>Recursive Search</b> (scan sub-folders, on by default); "
                            "<b>New Workspace</b> / <b>Mark As Workspace</b> "
                            "(write a shared Maya/Blender workspace.mel project).",
                        ],
                    ),
                    (
                        "Filter field (▸ option box)",
                        [
                            "Toggle the filter on/off; <b>Ignore Case</b>; choose what it matches — "
                            "<b>Files</b>, <b>Notes</b>, or both.",
                        ],
                    ),
                ],
            )
        )

    def _add_include_types_row(self, menu):
        """Add the Include Types row — one checkbox per file type, side-by-side under a titled
        separator (mirror across both panels). Uses ``Menu.add_row`` so the single-column menu is
        not reflowed; each checkbox is exposed as ``menu.chk_include_<type>`` and re-filters on toggle.

        This panel's native scene type (.blend) defaults on; foreign types default off.
        """
        tooltip = {
            "ma": "List the workspace's Maya ASCII scenes (.ma) — referenced via a headless-Maya convert + .blend bake.",
            "mb": "List the workspace's Maya binary scenes (.mb) — referenced via a headless-Maya convert + .blend bake.",
            "fbx": "List the workspace's FBX files (.fbx) — referenced via a .blend bake (no Maya needed).",
            "blend": "List the workspace's Blender scenes (.blend) — linked natively.",
        }
        items = [
            (
                "QCheckBox",
                {
                    "setObjectName": f"chk_include_{t}",
                    "setText": t,
                    "setChecked": f".{t}" in self._INCLUDE_DEFAULTS,
                    "setToolTip": tooltip[t],
                },
            )
            for t in self._INCLUDE_TYPES
        ]
        for cb in menu.add_row(items, title="Include Types:", justify="expand"):
            cb.toggled.connect(lambda *_: self._refresh())

    def _included_extensions(self):
        """The set of extensions (``.ma`` … ``.blend``) whose Include Types checkbox is checked.

        Defensive: the header menu may not be built yet during a sibling ``*_init``; falls back to
        the panel defaults so an early refresh still lists this panel's native scenes.
        """
        header = getattr(self.ui, "header", None)
        menu = getattr(header, "menu", None) if header else None
        if menu is None:
            return set(self._INCLUDE_DEFAULTS)
        included = set()
        for t in self._INCLUDE_TYPES:
            chk = getattr(menu, f"chk_include_{t}", None)
            if chk is not None and chk.isChecked():
                included.add(f".{t}")
        return included

    # ------------------------------------------------------------------ fields
    def txt000_init(self, widget):
        """Root Directory — browse + recent-dir history + Open / Set-To-Current actions (mirror of Maya)."""
        if not getattr(widget, "is_initialized", False):
            # Recent-directory history dropdown (Maya's directory pin).
            try:
                widget.option_box.pin(
                    settings_key="reference_manager_directories",
                    single_click_restore=True,
                )
            except Exception:
                pass
            # Directory picker — folded into the option menu as "Set Directory…"
            # rather than a standalone folder icon (mirror of Maya's b000 row).
            from uitk.widgets.optionBox.options.browse import BrowseOption

            self._browse_option = BrowseOption(
                wrapped_widget=widget,
                mode="directory",
                title="Select Root Directory",
                tooltip="Browse for a root folder containing project workspaces.",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setText="Set Directory…",
                setObjectName="btn_set_dir",
                setToolTip="Browse for a root folder containing project workspaces.",
            ).clicked.connect(lambda *_: self._browse_option.browse())
            widget.option_box.menu.add(
                "QPushButton",
                setText="Open Directory",
                setObjectName="btn_open_dir",
                setToolTip="Open the root directory in the file manager.",
            ).clicked.connect(self._open_root_dir)
            widget.option_box.menu.add(
                "QPushButton",
                setText="Set To Current Workspace",
                setObjectName="btn_set_current_ws",
                setToolTip="Set the root to the folder of the currently-open file.",
            ).clicked.connect(self._set_root_to_current)
            # Recursive Search — same control, same placement, same default (on) as Maya's chk000.
            widget.option_box.menu.add(
                "QCheckBox",
                setText="Recursive Search",
                setObjectName="chk_recursive",
                setChecked=True,
                setToolTip="Also search sub-folders (off = the workspace folder only).",
            ).toggled.connect(self._on_recursive_toggled)
            # Workspace management (Blender-only; Maya handles projects via its own Project
            # window, so these have no place in the mirrored header) — lives here on the Root
            # Directory field's option box, beside the other root/workspace actions.
            widget.option_box.menu.add(
                "QPushButton",
                setText="New Workspace…",
                setObjectName="btn_new_workspace",
                setToolTip="Create a project workspace under the root — writes a workspace.mel\n"
                "(shared Maya/Blender project) plus the standard subfolders.",
            ).clicked.connect(self.new_workspace)
            widget.option_box.menu.add(
                "QPushButton",
                setText="Mark As Workspace",
                setObjectName="btn_mark_workspace",
                setToolTip="Write a workspace.mel describing the current workspace folder's existing\n"
                "layout, making it a shared Maya/Blender project (no files are moved).",
            ).clicked.connect(self.mark_workspace)
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
        self.ui.txt000.setText(
            btk.workspace_root(fp) or os.path.dirname(os.path.abspath(fp))
        )

    def cmb000_init(self, widget):
        """Workspace combo — project folders under the root (replaces Maya's workspace combo)."""
        widget.currentIndexChanged.connect(self._on_workspace_changed)
        self._populate_workspaces()

    def txt001_init(self, widget):
        """Filter field — enable toggle + ignore-case + target combo, plus live re-filter (mirror of Maya)."""
        if not getattr(widget, "is_initialized", False):
            widget.option_box.clear_option = True
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
                "QCheckBox",
                setText="Ignore Case",
                setObjectName="chk_ignore_case",
                setChecked=True,
                setToolTip="Match the filter text case-insensitively.",
            ).toggled.connect(lambda *_: self._refresh())
            widget.option_box.menu.add(
                "QComboBox",
                setObjectName="cmb_filter_target",
                addItems=["Filter: All", "Filter: Files", "Filter: Notes"],
                setToolTip="What the filter text matches against: file names, notes, or both.",
            ).currentIndexChanged.connect(lambda *_: self._refresh())
        widget.textChanged.connect(lambda *_: self._refresh())

    def tbl000_init(self, widget):
        """Table setup: (re)wire signals every show, one-time context-menu build, then populate.

        ``_wire_table_signals`` runs unconditionally because the ``tbl000`` QWidget can outlive
        this slots instance (a reload leaves ``is_initialized`` stamped on the persisted widget)
        — without the re-wire, ``itemDoubleClicked`` / ``itemChanged`` / the row action-column
        handlers / the context-menu actions stay bound to a dead ``self`` and silently no-op.
        The context menu's ITEMS (the ``Separator`` / ``QPushButton`` widgets) stay in the
        one-time block since they mutate the widget, which persists — building them twice is
        the user-reported duplicate context-menu entries. Mirror of channels' ``tbl000_init`` /
        ``_wire_table_signals`` (and the mayatk panel).

        Order matters: the one-time construction runs BEFORE ``_wire_table_signals`` (mirror
        of mayatk, where ``actions.add`` must not size a column ``setColumnCount`` hasn't
        created yet — a native crash on Qt 6.5). Here the columns arrive later via
        ``TableWidget.add`` and ``TableActions._reapply`` re-applies the sizing then.
        """
        if not widget.is_initialized:
            widget.is_initialized = True
            widget.refresh_on_show = True

            # Flat context menu — a 1:1 mirror of the mayatk panel's, label- and order-for-order.
            # The Blender-specific extras (Append / Reload / Relocate / Remove / per-reference
            # Display) are dropped for parity: the link icon toggles reference/remove, the display
            # icon cycles the override, and 'Unlink and Import' folds in the old 'Import (convert)'.
            widget.menu.add(
                "QPushButton",
                setText="Open",
                setObjectName="row_open",
                setToolTip="Open the selected file (a foreign scene is baked, then opened as a new file).",
            )
            widget.menu.add(
                "QPushButton",
                setText="Rename",
                setObjectName="row_rename",
                setToolTip="Rename the selected .blend on disk.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Delete",
                setObjectName="row_delete",
                setToolTip="Delete the selected .blend from disk.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Reference / Unreference",
                setObjectName="row_toggle_reference",
                setToolTip="Toggle the reference (linked library) state of the selected file.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Unlink and Import",
                setObjectName="row_unlink_import",
                setToolTip="Make an already-linked reference's data local, or, for a foreign\n"
                "(Maya / FBX) scene, convert + import its contents as local data.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Open File Location",
                setObjectName="row_location",
                setToolTip="Reveal the selected file in the OS file manager.",
            )

        self._wire_table_signals(widget)
        self._refresh_table_content(widget)

    @staticmethod
    def _rewire_signal(widget, signal, slot, key):
        """Connect *signal* to *slot*, first dropping ONLY this panel's prior connection for *key*.

        A blanket ``signal.disconnect()`` (no args) would also strip the widget's OWN internal
        connections (e.g. the table wires ``customContextMenuRequested`` → ``_show_context_menu``
        in its ``__init__``), so re-wiring stores the ``QMetaObject.Connection`` per (widget, key)
        and drops exactly the dead one — the QWidget can outlive this slots instance across a
        reload. Mirror of the mayatk panel.

        The stored connection is dropped through the STATIC ``QObject.disconnect``, which is the
        API that takes a ``Connection``: the signal-instance form expects a *slot* and, handed a
        Connection it can't match, emits a ``RuntimeWarning`` ("Failed to disconnect (…) from
        signal …") instead of raising — a warning no ``except`` can swallow. A Connection also
        goes falsy the moment it breaks (PySide drops the binding when the receiving slots
        instance is collected), so a dead one is skipped outright.
        """
        from qtpy import QtCore

        conns = getattr(widget, "_rm_signal_conns", None)
        if conns is None:
            conns = {}
            widget._rm_signal_conns = conns
        old = conns.get(key)
        if old:
            try:
                QtCore.QObject.disconnect(old)
            except (RuntimeError, TypeError):
                pass
        conns[key] = signal.connect(slot)

    def _wire_table_signals(self, widget):
        """(Re)wire tbl000's signals + action-column / context-menu handlers to this instance.

        Idempotent and safe to call on every ``tbl000_init`` (the QWidget can outlive the slots
        instance). ``_setup_action_columns`` / ``register_menu_action`` are themselves idempotent
        (dict-keyed — each call overwrites the prior entry, no accumulation); the raw Qt signals
        re-wire through ``_rewire_signal`` (precise per-connection disconnect). Mirror of the
        mayatk panel.
        """
        self._setup_action_columns(widget)
        self._rewire_signal(
            widget, widget.itemDoubleClicked, self._on_item_double_clicked, "dbl"
        )
        self._rewire_signal(widget, widget.itemChanged, self._on_item_changed, "chg")

        for obj_name, handler in (
            ("row_open", self.open_selected),
            ("row_rename", self.rename_selected),
            ("row_delete", self.delete_selected),
            ("row_toggle_reference", self.toggle_reference_selected),
            ("row_unlink_import", self.unlink_import_selected),
            ("row_location", self.open_location_selected),
        ):
            widget.register_menu_action(obj_name, (lambda h: lambda *_: h())(handler))

    def _setup_action_columns(self, widget):
        """Register the Reference / Open / Display-mode clickable icon columns (mirror of Maya)."""
        clr = self.ACTION_COLOR
        widget.actions.add(
            self.COL_REF,
            states={
                "unreferenced": {
                    "icon": "link",
                    "color": clr["off"],
                    "tooltip": "Not referenced — click to add reference",
                    "action": self._toggle_reference_at_row,
                },
                "referenced": {
                    "icon": "link",
                    "color": clr["referenced"],
                    "tooltip": "Referenced — click to remove reference",
                    "action": self._toggle_reference_at_row,
                },
            },
        )
        widget.actions.add(
            self.COL_OPEN,
            states={
                "default": {
                    "icon": "open_external",
                    "color": clr["off"],
                    "tooltip": "Open this scene.",
                    "action": self._open_scene_at_row,
                },
                "current": {
                    "icon": "open_external",
                    "color": clr["current"],
                    "tooltip": "Current scene.",
                    "action": self._open_scene_at_row,
                },
            },
        )
        widget.actions.add(
            self.COL_DISPLAY,
            states={
                "off": {
                    "icon": "grid",
                    "color": clr["off"],
                    "tooltip": "Display: Normal — click to lock (Reference).",
                    "action": self._cycle_display_mode_at_row,
                },
                "reference": {
                    "icon": "lock",
                    "color": clr["ref_lock"],
                    "tooltip": "Display: Reference (locked) — click for Template (wire).",
                    "action": self._cycle_display_mode_at_row,
                },
                "template": {
                    "icon": "grid",
                    "color": clr["template"],
                    "tooltip": "Display: Template (wire + locked) — click to restore Normal.",
                    "action": self._cycle_display_mode_at_row,
                },
                "unavailable": {
                    "icon": "grid",
                    "color": clr["unavailable"],
                    "tooltip": "Display overrides apply only to linked references.",
                },
            },
        )

    def _on_item_double_clicked(self, item):
        """Double-click the name → rename; the Notes cell → inline edit (mirror of Maya's editItem).

        Renames the double-clicked row directly (not the selection) so it also works on the current
        scene, whose name cell is intentionally non-selectable.
        """
        if item is None:
            return
        if item.column() == self.COL_NAME:
            path = self._row_path(item.row())
            # A foreign (Maya) row is import-only — don't rename the source .blend/.ma on disk.
            if path and not self._is_foreign(path):
                self._rename_path(path)
        elif item.column() == self.COL_NOTES:
            self.ui.tbl000.editItem(item)

    # ------------------------------------------------------------------ row action handlers
    def _row_path(self, row):
        """Absolute .blend path stored on the name cell of ``row`` (or None)."""
        item = self.ui.tbl000.item(row, self.COL_NAME)
        return item.data(self.sb.QtCore.Qt.UserRole) if item else None

    def _toggle_reference_at_row(self, row, col):
        """Link an unreferenced file, or remove the library of an already-linked one (Maya toggle).

        A file is either **open** or **referenced**, never both: referencing the currently-open
        scene first closes it (a new empty scene), since a file can't be referenced into itself.

        Foreign rows toggle identically (parity rule): the click bakes the source to a
        cached .blend and links THAT, and a second click removes the same library —
        ``_library_for_path`` resolves a source row through its bake sidecar, so the
        round trip works even in a session that did not perform the bake.
        """
        path = self._row_path(row)
        if not path:
            return
        lib = self._library_for_path(path)
        if lib is not None:
            btk.remove_library(lib)
        else:
            # Referencing the open scene into itself is invalid — close it first (guarded).
            if self._is_current(path) and not self._close_scene():
                return  # user declined discarding unsaved changes
            if self._is_foreign(path):
                if not self._reference_foreign_paths([path]):
                    return
            else:
                try:
                    btk.link_blend_file(path, link=True)
                except (RuntimeError, OSError) as e:
                    self.sb.message_box(str(e))
                    return
        self._refresh()

    def _open_scene_at_row(self, row, col):
        """Toggle Open at ``row``: open the scene, or **close** it (new empty scene) if it is
        already the current scene — a second click on the open row's Open icon closes it, the
        mirror of the reference icon's toggle.

        Open and Reference are mutually exclusive, but opening enforces that for free: loading a
        file replaces the whole session, so a reference to it (a library link in the *previous*
        scene) is discarded and the file becomes the open scene — no explicit un-reference needed
        (which would also wrongly persist if the user then declines the unsaved-changes prompt).
        """
        path = self._row_path(row)
        if not path:
            return
        if self._is_current(path):
            if self._close_scene():
                self.logger.info("Closed the scene (new empty scene).")
        else:
            self._open_path(path)
        self._refresh()

    def _is_current(self, path, current=None):
        """True if *path*'s scene is the one currently open (filepath-authoritative).

        A native row matches when the open .blend IS that file; a foreign row matches when the
        open .blend is that row's deterministic 'opened as new' scratch bake (see
        :meth:`_foreign_scratch_path`) — so a second Open click on either closes it. Pass a
        pre-computed *current* (normalized, from :meth:`_current_scene_path`) to reuse one bpy
        read across a whole table rebuild.
        """
        if not path:
            return False
        cur = current if current is not None else self._current_scene_path()
        if not cur:
            return False
        target = self._foreign_scratch_path(path) if self._is_foreign(path) else path
        return os.path.normpath(target).lower() == cur

    @staticmethod
    def _foreign_scratch_path(path):
        """Deterministic scratch .blend a foreign row is baked+opened into (see _open_foreign_as_new)."""
        import tempfile

        stem = os.path.splitext(os.path.basename(path))[0]
        return os.path.join(tempfile.gettempdir(), f"{stem}_opened.blend")

    def _confirm_discard_unsaved(self, verb="open"):
        """True if it's OK to replace the current scene — no unsaved changes, or the user
        confirmed discarding them. .venv-safe: without bpy there is nothing to lose.

        Asks the engine rather than reading ``bpy.data.is_dirty`` directly: the flag follows the
        undo stack, so one viewport click marks a brand-new empty scene dirty and this prompt
        fired with nothing to lose (see ``btk.scene_has_unsaved_changes``).
        """
        if not self._has_bpy():
            return True
        if not btk.scene_has_unsaved_changes():
            return True
        return (
            self.sb.message_box(
                f"The current file has unsaved changes — {verb} anyway?", "Yes", "No"
            )
            == "Yes"
        )

    def _close_scene(self):
        """Close the current scene (a new empty scene — Maya's file-new), guarding unsaved
        changes. Returns True if the scene was closed, False if the user declined."""
        if not self._confirm_discard_unsaved("close"):
            return False
        if btk.new_scene():
            return True
        self.sb.message_box("Failed to close the scene.")
        return False

    def _cycle_display_mode_at_row(self, row, col):
        """Cycle the linked reference's display: Normal → Reference → Template → Normal."""
        path = self._row_path(row)
        lib = self._library_for_path(path) if path else None
        table = self.ui.tbl000
        if (
            lib is None
        ):  # not linked (or removed between sync and click) — reset the cell silently
            table.actions.set(row, self.COL_DISPLAY, "unavailable")
            return
        new_mode = self._DISPLAY_MODE_CYCLE.get(
            btk.get_reference_display_mode(lib), "off"
        )
        if not btk.set_reference_display_mode(lib, new_mode):
            self.sb.message_box(
                "Display override had no effect — the reference has no objects to update."
            )
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
        field = getattr(
            self.ui, "txt000", None
        )  # may not exist yet during sibling *_init
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
        saved = self._get_workspace_history().get(
            os.path.normcase(os.path.normpath(root))
        )
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

        # Native scenes (.blend) — listed + linked directly, gated on the Include Types row.
        # Text filtering is applied below (not via find_blend_files) so it can honor the
        # ignore-case toggle and match against Notes as well as file names.
        included = opt["included_ext"]
        files = []
        if ".blend" in included:
            files = btk.find_blend_files(workspace, recursive=self._recursive)
            if workspace:
                # A marked (workspace.mel) project keeps scenes in its scene-rule folder — always
                # include it (deduped) so shared Maya/Blender projects list out of the box, and so a
                # scene rule pointing OUTSIDE the workspace root (which the recursive root scan would
                # miss) still lists. Mirror of mayatk, which always scans a workspace's scenes/
                # regardless of the Recursive toggle.
                scene_dir = btk.workspace_scenes_dir(workspace)
                if scene_dir:
                    seen = {os.path.normcase(p) for p in files}
                    files += [
                        p
                        for p in btk.find_blend_files(scene_dir, recursive=True)
                        if os.path.normcase(p) not in seen
                    ]
        # Cross-DCC: also list the workspace's foreign scenes for each checked foreign type
        # (.ma/.mb/.fbx). A foreign row's link icon bakes it to a cached .blend and links
        # that, so it carries the same referenced/unreferenced states as a native row; only
        # Open stays unavailable. Discovery uses the importer's own scan, restricted to the
        # checked extensions.
        foreign_ext = {e for e in self.FOREIGN_EXTENSIONS if e in included}
        if foreign_ext and workspace:
            from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport

            seen = {os.path.normcase(p) for p in files}
            files += [
                p
                for p in MayaSceneImport.find_scenes(
                    workspace, recursive=self._recursive, extensions=sorted(foreign_ext)
                )
                if os.path.normcase(p) not in seen
            ]
        raw_count = len(files)  # pre-filter, for the "hidden by filter" empty-state message
        files = self._apply_file_filters(files, workspace, opt)
        # Live reference state needs bpy; degrade gracefully under the .venv (no live status).
        # One list_libraries() pass — `linked` is derived from it so the two can't disagree.
        libs_by_path = {}
        if self._has_bpy():
            for r in btk.list_libraries():
                ap = r.get("abspath")
                if not ap:
                    continue
                libs_by_path[os.path.normpath(ap).lower()] = r["library"]
                # A foreign row links its BAKE, so also key the library by the source
                # scene the user sees — otherwise the row reads as unreferenced.
                source = self._bake_source(ap)
                if source:
                    libs_by_path[os.path.normpath(source).lower()] = r["library"]
        linked = set(libs_by_path)
        current = self._current_scene_path()

        self._suppress_note_save = True
        widget.setUpdatesEnabled(False)
        widget.clear()
        qt = self.sb.QtCore.Qt
        try:
            placeholder = None
            filter_active = (
                opt["filter_suffix"] or opt["filter_structure"] or bool(opt["filter_text"])
            )
            if not workspace:
                placeholder = "Set a root directory / workspace above…"
            elif not files:
                # Distinguish an empty workspace from one whose files an active filter
                # (or a restored preset) hid entirely — the latter otherwise reads as
                # "the panel is broken" (a real support case).
                placeholder = (
                    f"All {raw_count} file(s) hidden by the active filter — "
                    "check the header menu (Filter by Suffix / Folder Structure) or the filter field."
                    if raw_count and filter_active
                    else "No .blend files found"
                )

            if placeholder is not None:
                rows = [[(placeholder, ""), "", "", "", ""]]
            else:
                rows = [
                    [
                        (self._row_label(p, opt), p),
                        "",
                        "",
                        "",
                        self._notes.get(os.path.normpath(p).lower(), ""),
                    ]
                    for p in files
                ]
            # Columns: FILES | reference-toggle | open | display-mode | NOTES (1:1 with Maya).
            widget.add(rows, headers=["FILES:", "", "", "", "NOTES:"])

            if placeholder is None:
                for row, path in enumerate(files):
                    key = os.path.normpath(path).lower()
                    name_item = widget.item(row, self.COL_NAME)
                    note_item = widget.item(row, self.COL_NOTES)

                    is_linked = key in linked
                    # Currentness matches the Open toggle exactly (native file, or a foreign row's
                    # open scratch bake). Reuse the single `current` snapshot — no per-row bpy read.
                    is_current = self._is_current(path, current)
                    widget.actions.set(
                        row, self.COL_REF, "referenced" if is_linked else "unreferenced"
                    )
                    # A foreign row's Open bakes the scene and opens it as a new file (see
                    # _open_foreign_as_new), so it carries the same visible/clickable Open states
                    # as a native row — including the 'current' highlight when its scratch is open.
                    widget.actions.set(
                        row, self.COL_OPEN, "current" if is_current else "default"
                    )
                    if is_linked:
                        lib = libs_by_path.get(key)
                        mode = (
                            btk.get_reference_display_mode(lib)
                            if lib is not None
                            else "off"
                        )
                        widget.actions.set(row, self.COL_DISPLAY, mode)
                    else:
                        widget.actions.set(row, self.COL_DISPLAY, "unavailable")

                    if name_item:
                        name_item.setFlags(name_item.flags() & ~qt.ItemIsEditable)
                        # The row label can hide the suffix/extension, and a long name
                        # elides in the column, so the tooltip names the file in full
                        # (widget.add defaults it to the truncated label).
                        name_item.setToolTip(os.path.basename(path))
                        if (
                            is_current
                        ):  # current scene: italic + not selectable (mirror of Maya)
                            font = name_item.font()
                            font.setItalic(True)
                            name_item.setFont(font)
                            name_item.setFlags(name_item.flags() & ~qt.ItemIsSelectable)
                    if note_item and path:
                        note_item.setData(
                            qt.UserRole, path
                        )  # carry the path for note edits

            header = widget.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(self.COL_NAME, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(self.COL_NOTES, QtWidgets.QHeaderView.Stretch)
            # Pin the three icon action columns to the right by moving NOTES into
            # the middle (visual position 1, right after FILES). Visual-only —
            # logical indices are unchanged, so every COL_* reference stays valid.
            # Re-applied each populate because ``widget.add`` (setColumnCount(0))
            # resets the header's visual section order. Mirror of mayatk.
            header.moveSection(header.visualIndex(self.COL_NOTES), 1)
            # Leave the three action columns on the Fixed, row-height-square sizing that
            # TableActions.add()/_reapply() applied. Overriding them to ResizeToContents
            # collapses each icon-only column to ~0 px — and to nothing in the empty /
            # placeholder state (no icon set) — so the clickable link / open / display
            # columns vanish. Fixed squares keep them always visible, mirroring mayatk.
        finally:
            widget.setUpdatesEnabled(True)
            self._suppress_note_save = False

        self._apply_notes_column_visibility()

        n_linked = len(linked)
        if workspace:
            hidden = raw_count - len(files)
            msg = f"{len(files)} file(s); {n_linked} linked."
            if hidden > 0:
                msg += f" ({hidden} hidden by filter)"
            self.ui.footer.setText(msg)
        else:
            self.ui.footer.setText("Set a root directory.")

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
        menu = (
            getattr(header, "menu", None) if header else None
        )  # naming + display options
        filt = getattr(self.ui, "txt001", None)
        fbox = getattr(filt, "option_box", None) if filt is not None else None
        fmenu = (
            getattr(fbox, "menu", None) if fbox is not None else None
        )  # text-filter options

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
            "structure_pattern": txt("txt_subfolder_structure"),
            "filter_suffix": chk(menu, "chk_filter_suffix"),
            "filter_structure": chk(menu, "chk_filter_folder_structure"),
            "hide_suffix": chk(menu, "chk_hide_suffix"),
            "hide_extension": chk(menu, "chk_hide_extension"),
            "included_ext": self._included_extensions(),
            "ignore_case": chk(fmenu, "chk_ignore_case", True),
            "target": target_w.currentText() if target_w is not None else "Filter: All",
            "filter_text": (filt.text().strip() if filt is not None else "")
            if enabled
            else "",
        }

    def _apply_file_filters(self, files, workspace, opt):
        """Apply the suffix / folder-structure / text filters to the file list (mirror of Maya)."""
        suffix = opt["suffix"]
        # Filter by suffix — keep files whose name (sans extension) ends with the suffix.
        if opt["filter_suffix"] and suffix:
            files = [
                f
                for f in files
                if os.path.splitext(os.path.basename(f))[0].endswith(suffix)
            ]

        # Filter by folder structure — keep files whose location matches the resolved subfolder pattern.
        if opt["filter_structure"] and opt["structure_pattern"] and workspace:
            files = self._filter_by_folder_structure(
                files, workspace, opt["structure_pattern"], suffix
            )

        # Text filter — match file names and/or notes per the target (honoring ignore-case).
        text = opt["filter_text"]
        if text:
            include_files = opt["target"] in ("Filter: All", "Filter: Files")
            include_notes = opt["target"] in ("Filter: All", "Filter: Notes")
            patterns = text.replace(";", ",")  # ptk.filter_list splits on "," only
            name_matches = set()
            if include_files:
                names = [os.path.basename(f) for f in files]
                name_matches = set(
                    ptk.filter_list(names, inc=patterns, ignore_case=opt["ignore_case"])
                )
            kept = []
            for f in files:
                ok = include_files and os.path.basename(f) in name_matches
                if not ok and include_notes:
                    note = self._notes.get(os.path.normpath(f).lower(), "")
                    ok = bool(note) and self._note_matches(
                        note, patterns, opt["ignore_case"]
                    )
                if ok:
                    kept.append(f)
            files = kept
        return files

    def _filter_by_folder_structure(self, files, workspace, pattern, suffix):
        """Keep files whose directory matches the subfolder pattern ({scenes} / {name} / {workspace} / {suffix})."""
        workspace_name = os.path.basename(os.path.normpath(workspace))
        # Resolve {scenes} to the workspace's scene-rule folder — same lookup + fallback as
        # save_scene_as (and Maya's cmds.workspace(fileRuleEntry="scene")); without this a
        # "{scenes}/…" pattern (now the default) never matches and hides every file.
        scenes_folder = self._scenes_folder(workspace)
        kept = []
        for f in files:
            try:
                rel_dir = os.path.relpath(os.path.dirname(f), workspace)
            except ValueError:  # different drive on Windows
                continue
            base = os.path.splitext(os.path.basename(f))[0]
            name_for_path = (
                base[: -len(suffix)] if suffix and base.endswith(suffix) else base
            )
            try:
                expected = ptk.StrUtils.replace_placeholders(
                    pattern,
                    scenes=scenes_folder,
                    name=name_for_path,
                    workspace=workspace_name,
                    suffix=suffix,
                )
            except (
                ValueError
            ):  # malformed pattern (e.g. unbalanced brace) — don't crash the refresh
                continue
            rel_parts = os.path.normcase(os.path.normpath(rel_dir)).split(os.sep)
            exp_parts = os.path.normcase(os.path.normpath(expected)).split(os.sep)
            # Match if the file's directory ends with the expected structure (file may sit deeper).
            if (
                len(rel_parts) >= len(exp_parts)
                and rel_parts[-len(exp_parts) :] == exp_parts
            ):
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
        return bool(
            ptk.filter_list([note] + segments, inc=patterns, ignore_case=ignore_case)
        )

    def _format_display_name(self, path, opt):
        """Displayed file name with the suffix / extension optionally stripped (mirror of Maya)."""
        name = os.path.basename(path)
        if opt["hide_extension"]:
            name = os.path.splitext(name)[0]
        if opt["hide_suffix"] and opt["suffix"]:
            name = name.replace(opt["suffix"], "")
        return name

    def _row_label(self, path, opt):
        """Display label for a file row — the bare (optionally suffix/extension-stripped) file name.

        No cross-DCC origin tag is appended: the user can reveal the extension (Hide Extension off)
        to tell a foreign .ma/.mb/.fbx row from a native .blend, so a redundant '(Maya)' suffix is
        omitted (mirror of the Maya panel, which likewise drops its '(Blender)' tag)."""
        return self._format_display_name(path, opt)

    @classmethod
    def _is_foreign(cls, path):
        """True if *path* is a foreign (cross-DCC) scene for this panel — an import-only row."""
        return bool(path) and os.path.splitext(path)[1].lower() in cls.FOREIGN_EXTENSIONS

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
        for name in (
            "chk_filter_suffix",
            "chk_hide_suffix",
            "chk_filter_folder_structure",
        ):
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
        """The linked library datablock whose file is ``path`` (or None).

        A foreign row's library is the file's *bake*, not the row's own path, so each
        candidate is also matched through its bake sidecar (see ``bake_source``).
        """
        target = os.path.normpath(path).lower()
        for rec in btk.list_libraries():
            abspath = rec["abspath"]
            if not abspath:
                continue
            if os.path.normpath(abspath).lower() == target:
                return rec["library"]
            source = self._bake_source(abspath)
            if source and os.path.normpath(source).lower() == target:
                return rec["library"]
        return None

    @staticmethod
    def _bake_source(linked_path):
        """The foreign scene *linked_path* was baked from, or None if it is not a bake."""
        try:
            from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport
        except ImportError:  # bridge unavailable — no row can be bake-backed
            return None
        return MayaSceneImport.bake_source(linked_path)

    def _selected_libraries(self):
        return [
            lib
            for lib in map(self._library_for_path, self._selected_paths())
            if lib is not None
        ]

    # ------------------------------------------------------------------ scene file ops
    def open_selected(self):
        """Open the selected .blend (replaces the current file; confirms if unsaved)."""
        paths = self._selected_paths()
        if not paths:
            self.sb.message_box("Select a file in the list first.")
            return
        self._open_path(paths[0])

    def _open_path(self, path):
        """Open ``path`` (replaces the current file), confirming first if there are unsaved changes.

        A foreign (Maya / FBX) scene has no ``.blend`` to open, so it is baked and its bake is
        opened as a new, unsaved file (see :meth:`_open_foreign_as_new`).
        """
        if not self._confirm_discard_unsaved("open"):
            return
        if self._is_foreign(path):
            self._open_foreign_as_new(path)
            return
        if btk.open_scene(path):
            self.logger.info(f"Opened scene: {os.path.basename(path)}")
        else:
            self.sb.message_box("Failed to open the file.")

    def _foreign_route(self):
        """The conversion route from the header menu — ``"fbx"`` (default) / ``"usd"``.

        USD is returned only when explicitly selected, so a missing/unbuilt menu
        falls back to the same route the engine defaults to.
        """
        menu = getattr(getattr(self.ui, "header", None), "menu", None)
        combo = getattr(menu, "cmb_conversion_route", None) if menu else None
        if combo is not None and "USD" in combo.currentText():
            return "usd"
        return "fbx"

    def _resolve_conversion(self, path):
        """Route + smart-bake decision for converting *path*.

        Returns the kwargs to hand ``import_scene`` / ``bake_scene`` (``via`` +
        ``smart_bake``), or ``None`` if the user cancelled. The Bake-vs-Raw prompt
        exists to patch FBX's driven-animation hole — the USD route samples driven
        animation and visibility natively, so it never asks.
        """
        via = self._foreign_route()
        if via != "fbx":
            return {"via": via}  # smart_bake is FBX-only; the engine ignores it anyway
        smart_bake = self._resolve_smart_bake(path)
        if smart_bake is None:
            return None
        return {"via": "fbx", "smart_bake": smart_bake}

    def _resolve_smart_bake(self, path):
        """Decide how to convert a foreign scene: when it has *driven* animation a raw
        import would lose, prompt Bake vs Import Raw — the conversion-time counterpart
        of the unsaved-changes confirmation. Returns the ``smart_bake`` value to hand
        the bridge — ``True`` (bake) / ``False`` (import raw) / ``"auto"`` (no driven
        animation detected, or a non-scannable source — nothing to ask) — or ``None``
        if the user cancelled.

        Only ``.ma`` is text-scannable; ``.mb`` / ``.fbx`` fall through to ``"auto"``
        (the bridge's own Maya-side detection still bakes a ``.mb`` if warranted; an
        ``.fbx`` is already baked and has no Maya drivers)."""
        from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport

        if not MayaSceneImport.scene_has_complex_animation(path):
            return "auto"
        choice = self.sb.message_box(
            f"<hl>{os.path.basename(path)}</hl> has driven animation "
            "(constraints, set-driven keys, inherited visibility) that a raw import "
            "would lose.<br><br><b>Bake</b> it into keyframes? "
            "(<b>No</b> imports the raw contents.)",
            "Yes",
            "No",
            "Cancel",
        )
        # "Cancel" (and a closed dialog) -> None; Yes -> bake, No -> raw.
        return {"Yes": True, "No": False}.get(choice)

    def _open_foreign_as_new(self, path):
        """Bake a foreign (Maya / FBX) scene to a .blend and open it as a new, unsaved file.

        The 'open' counterpart of the link icon's bake-and-reference: a fresh headless Maya
        converts the scene to FBX (default) or USD per the header-menu route, and a headless
        Blender bakes that to a cached .blend (an .fbx source skips Maya). That cached bake is
        copied to a scratch .blend which is opened — so
        the user edits a throwaway document and saves it wherever they like, and the cache the
        link icon reuses is never touched. The bake costs a mayapy start + license on the first
        run for a .ma/.mb, hence the wait cursor.
        """
        if not self._has_bpy():
            self.sb.message_box("Opening a foreign scene needs a running Blender.")
            return
        # Resolve route + bake-vs-raw (may prompt on the FBX route) before the wait
        # cursor, so the modal shows a normal cursor.
        conv = self._resolve_conversion(path)
        if conv is None:
            return  # user cancelled
        import shutil

        from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport

        app = self.sb.QtWidgets.QApplication
        app.setOverrideCursor(self.sb.QtCore.Qt.WaitCursor)
        try:
            baked = MayaSceneImport().bake_scene(path, **conv)
        except FileNotFoundError as e:
            self.sb.message_box(f"Can't open — Maya not found:<br>{e}")
            return
        except Exception as e:  # noqa: BLE001 — surface the bake error to the user
            self.logger.warning(f"Foreign scene bake failed for {path}: {e}")
            self.sb.message_box(
                f"Open failed for <hl>{os.path.basename(path)}</hl>:<br>{e}"
            )
            return
        finally:
            app.restoreOverrideCursor()

        # Deterministic scratch path so a second Open click resolves this row as 'current' and
        # closes it (see _is_current / _foreign_scratch_path).
        scratch = self._foreign_scratch_path(path)
        try:
            shutil.copyfile(baked, scratch)
        except OSError:
            scratch = baked  # fall back to the cache if the scratch copy can't be written
        if btk.open_scene(scratch):
            self.sb.message_box(
                f"Opened <hl>{os.path.basename(path)}</hl> as a new scene "
                "(baked from the foreign source — save it where you like)."
            )
        else:
            self.sb.message_box("Failed to open the baked scene.")

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
            workspace,
            name,
            case=menu.cmb_case_style.currentText(),
            suffix=menu.txt_suffix.text(),
            subfolder=menu.txt_subfolder_structure.text().strip(),
        )
        if path:
            self.logger.info(f"Saved scene: {os.path.basename(path)}")
        else:
            self.sb.message_box("Failed to save the scene.")
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
        if new_path:
            self.logger.info(f"Renamed to: {os.path.basename(new_path)}")
        else:
            self.sb.message_box(
                "Rename failed — a file with that name may already exist, or the open "
                "file could not be saved."
            )
        self._refresh()

    @classmethod
    def _delete_prompt(cls, paths) -> str:
        """Confirmation text for deleting *paths* (mirror of mayatk's).

        Names each file in full: the row label can hide the suffix/extension, so a
        count alone ("Delete 1 file(s)?") gives no way to confirm WHICH file is about
        to be removed -- and deletion is permanent (no trash).

        Parameters:
            paths (list): Full paths of the files queued for deletion.

        Returns:
            str: HTML prompt naming the file(s).
        """
        names = [os.path.basename(p) for p in paths]
        if len(names) == 1:
            return f"Delete <hl>{names[0]}</hl> from disk?"
        shown = names[: cls.DELETE_PROMPT_MAX_NAMES]
        listed = "<br>".join(f"&bull; {n}" for n in shown)
        if len(names) > len(shown):
            listed += f"<br>&bull; ...and {len(names) - len(shown)} more"
        return f"Delete {len(names)} file(s) from disk?<br>{listed}"

    def delete_selected(self):
        """Delete the selected .blend file(s) from disk (confirmed)."""
        paths = [p for p in self._selected_paths() if os.path.isfile(p)]
        if not paths:
            self.sb.message_box("Select a file to delete.")
            return
        if self.sb.message_box(self._delete_prompt(paths), "Yes", "No") != "Yes":
            return
        done = sum(1 for p in paths if btk.delete_scene_file(p))
        self.logger.info(f"Deleted {done} of {len(paths)} file(s).")
        if done < len(paths):
            self.sb.message_box(
                f"Deleted <hl>{done}</hl> of {len(paths)} — some file(s) could not be removed."
            )
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

    # ------------------------------------------------------------------ cross-DCC import
    def _import_foreign_paths(self, paths):
        """Import each foreign scene in *paths* as LOCAL data (blocking).

        Folded into the row menu's 'Unlink and Import' (see :meth:`unlink_import_selected`) as
        the make-local counterpart for a not-yet-linked foreign row. A ``.ma``/``.mb`` goes
        through ``btk.MayaSceneImport.import_scene`` — a
        fresh mayapy converts it to FBX, which is imported and cleaned up (the same bridge
        the Scene menu's 'Import Maya Scene' uses); that takes tens of seconds (mayapy start
        + license), so a wait cursor covers it and a missing Maya install surfaces as a clear
        message rather than a raw traceback. An ``.fbx`` is imported directly — no Maya.
        """
        paths = [p for p in (paths or []) if p and self._is_foreign(p)]
        if not paths:
            return
        if not self._has_bpy():
            self.sb.message_box("Importing a foreign scene needs a running Blender.")
            return
        from blendertk.env_utils.fbx_utils import FbxUtils
        from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport

        def _import(path, conv):
            if os.path.splitext(path)[1].lower() == ".fbx":
                return FbxUtils.import_fbx(path)
            return MayaSceneImport().import_scene(path, **conv)

        # Resolve route + bake-vs-raw per scene (may prompt on the FBX route) BEFORE
        # the wait cursor. An .fbx needs no conversion; a cancelled scene is dropped.
        plan = []
        for path in paths:
            if os.path.splitext(path)[1].lower() == ".fbx":
                plan.append((path, {}))
            else:
                conv = self._resolve_conversion(path)
                if conv is not None:
                    plan.append((path, conv))
        if not plan:
            return

        app = self.sb.QtWidgets.QApplication
        app.setOverrideCursor(self.sb.QtCore.Qt.WaitCursor)
        total, failed = 0, 0
        try:
            for path, conv in plan:
                try:
                    total += len(_import(path, conv))
                except FileNotFoundError as e:
                    self.sb.message_box(f"Can't import — Maya not found:<br>{e}")
                    return
                except Exception as e:  # noqa: BLE001 — surface the conversion error to the user
                    failed += 1
                    self.logger.warning(f"Foreign scene import failed for {path}: {e}")
                    self.sb.message_box(
                        f"Import failed for <hl>{os.path.basename(path)}</hl>:<br>{e}"
                    )
        finally:
            app.restoreOverrideCursor()
        self.logger.info(
            f"Imported {total} object(s) from {len(plan) - failed} foreign scene(s)."
        )
        self._refresh()

    def _reference_foreign_paths(self, paths):
        """Bake each foreign scene in *paths* to a cached .blend and link it. True on success.

        Blender can only link a ``.blend``, so a foreign row is referenced through a bake
        (headless Maya → USD/FBX intermediate → headless Blender → cached .blend) rather
        than directly.
        Both stages are cached, so re-linking the same unchanged scene is instant; the
        first run costs a mayapy start + license, hence the wait cursor.
        """
        paths = [p for p in (paths or []) if p and self._is_foreign(p)]
        if not paths:
            return False
        if not self._has_bpy():
            self.sb.message_box("Referencing a foreign scene needs a running Blender.")
            return False
        from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport

        # Resolve route + bake-vs-raw per scene (may prompt on the FBX route) BEFORE
        # the wait cursor; a cancelled scene is dropped from the batch.
        plan = []
        for path in paths:
            conv = self._resolve_conversion(path)
            if conv is not None:
                plan.append((path, conv))
        if not plan:
            return False

        app = self.sb.QtWidgets.QApplication
        app.setOverrideCursor(self.sb.QtCore.Qt.WaitCursor)
        linked = 0
        try:
            for path, conv in plan:
                try:
                    linked += btk.link_blend_file(
                        MayaSceneImport().bake_scene(path, **conv), link=True
                    )
                except FileNotFoundError as e:
                    self.sb.message_box(f"Can't reference — Maya not found:<br>{e}")
                    return False
                except Exception as e:  # noqa: BLE001 — surface the bake error to the user
                    self.logger.warning(f"Foreign scene bake failed for {path}: {e}")
                    self.sb.message_box(
                        f"Reference failed for <hl>{os.path.basename(path)}</hl>:<br>{e}"
                    )
                    return False
        finally:
            app.restoreOverrideCursor()
        return bool(linked)

    # ------------------------------------------------------------------ reference ops
    def toggle_reference_selected(self):
        """Reference / Unreference the selected row(s) — the row-menu twin of the link icon
        (Maya's 'Reference / Unreference').

        Each selected file is toggled independently: an already-linked one has its library
        removed; a native file is linked; a foreign (Maya / FBX) file is baked and its bake
        linked (through the same path the link icon uses).
        """
        paths = self._selected_paths()
        if not paths:
            self.sb.message_box("Select a file in the list first.")
            return
        foreign_to_ref, changed = [], False
        for path in paths:
            lib = self._library_for_path(path)
            if lib is not None:
                if btk.remove_library(lib):
                    changed = True
            elif self._is_foreign(path):
                foreign_to_ref.append(path)
            else:
                try:
                    btk.link_blend_file(path, link=True)
                    changed = True
                except (RuntimeError, OSError) as e:
                    self.sb.message_box(str(e))
        if foreign_to_ref and self._reference_foreign_paths(foreign_to_ref):
            changed = True
        if changed:
            self._refresh()

    def unlink_import_selected(self):
        """Unlink and Import the selected row(s) — Maya's 'Unlink and Import', covering both cases.

        An already-linked reference has its data made local (unlink + import); a not-yet-linked
        foreign (Maya / FBX) row is converted and its contents imported as local data (the old
        'Import (convert)' behaviour, folded in here for parity with the Maya panel).
        """
        paths = self._selected_paths()
        if not paths:
            self.sb.message_box("Select a file in the list first.")
            return
        libs = self._selected_libraries()
        foreign = [
            p
            for p in paths
            if self._is_foreign(p) and self._library_for_path(p) is None
        ]
        if not libs and not foreign:
            self.sb.message_box(
                "Select a linked reference (to make local) or a foreign scene (to import)."
            )
            return
        if libs:
            total = sum(btk.make_library_local(lib) for lib in libs)
            self.logger.info(f"Made {total} datablock(s) local.")
        if foreign:
            self._import_foreign_paths(foreign)  # logs its own summary + refreshes
        else:
            self._refresh()

    # ------------------------------------------------------------------ bulk ops (header menu)
    def reload_all(self):
        """Reload every linked library from disk (Maya's Update References)."""
        recs = btk.list_libraries()
        if not recs:
            self.sb.message_box("No linked libraries to reload.")
            return
        done = sum(1 for rec in recs if btk.reload_library(rec["library"]))
        self.logger.info(f"Reloaded {done} of {len(recs)} library(ies).")
        self._refresh()

    def make_local_all(self):
        """Make every linked library's data local (Maya's Unlink-and-Import All)."""
        recs = btk.list_libraries()
        if not recs:
            self.sb.message_box("No linked libraries to make local.")
            return
        if (
            self.sb.message_box(f"Make {len(recs)} library(ies) local?", "Yes", "No")
            != "Yes"
        ):
            return
        total = sum(btk.make_library_local(rec["library"]) for rec in recs)
        self.logger.info(f"Made {total} datablock(s) local.")
        self._refresh()

    def remove_all(self):
        """Remove every linked library and its data (Maya's Un-Reference All)."""
        recs = btk.list_libraries()
        if not recs:
            self.sb.message_box("No linked libraries to remove.")
            return
        if (
            self.sb.message_box(
                f"Remove ALL {len(recs)} linked library(ies)?", "Yes", "No"
            )
            != "Yes"
        ):
            return
        done = sum(1 for rec in recs if btk.remove_library(rec["library"]))
        self.logger.info(f"Removed {done} of {len(recs)} library(ies).")
        if done < len(recs):
            self.sb.message_box(
                f"Removed <hl>{done}</hl> of {len(recs)} — some library(ies) could not be removed."
            )
        self._refresh()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("reference_manager", reload=True)
    ui.show(pos="screen", app_exec=True)
