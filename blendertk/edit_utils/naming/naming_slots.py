# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Naming panel — Blender port of mayatk's ``NamingSlots``.

Batch find / rename / convert-case / strip-chars / suffix-by-location / suffix-by-type, each with an
option box (▸). Engine is :class:`~blendertk.edit_utils.naming._naming.Naming`. Mirrors mayatk's
``NamingSlots`` 1:1 — same objectNames, same widget tree, same scoping: the header ``Scope`` combo
(Selection / Scene / Directory / Files) governs every operation and ``Dry Run`` previews any of
them. The file scopes run the name operations on file stems through :class:`pythontk.FileNaming`
(DCC-agnostic, so identical to mayatk); the two scene-only suffix operations report that they do
not apply. Every operation reports into the output panel (``txt002``) through ``self.logger``.

The Qt-only ``uitk`` imports (``Signals``/``fmt``) load with this slots module, which is only
imported in a Qt context (the UI handler / panel open), never by the headless engine path. ``import
bpy`` is deferred into method bodies per blendertk convention.
"""

import os
from typing import List

import pythontk as ptk
from uitk import Signals

import blendertk as btk
from blendertk.edit_utils.naming._naming import Naming


class NamingSlots(Naming):
    """Switchboard slots for the Naming panel."""

    SCOPES = ("Selection", "Scene", "Directory", "Files")

    # Suffix-by-type option box: display groups of (engine keyword, field
    # objectName) — 1:1 with mayatk's table (the parity sweep unrolls it). The
    # objectNames are persisted user settings: never renumber them.
    SUFFIX_GROUPS = (
        (
            "Transforms",
            (
                ("group_suffix", "tb003_txt000"),
                ("locator_suffix", "tb003_txt001"),
                ("joint_suffix", "tb003_txt002"),
                ("ik_handle_suffix", "tb003_txt008"),
                ("constraint_suffix", "tb003_txt014"),
            ),
        ),
        (
            "Shapes",
            (
                ("mesh_suffix", "tb003_txt003"),
                ("nurbs_curve_suffix", "tb003_txt004"),
                ("nurbs_surface_suffix", "tb003_txt009"),
                ("camera_suffix", "tb003_txt005"),
                ("light_suffix", "tb003_txt006"),
            ),
        ),
        (
            "Deformers",
            (
                ("cluster_suffix", "tb003_txt010"),
                ("lattice_suffix", "tb003_txt011"),
                ("skin_cluster_suffix", "tb003_txt012"),
                ("blend_shape_suffix", "tb003_txt013"),
            ),
        ),
        (
            "Shading",
            (
                ("material_suffix", "tb003_txt015"),
                ("shading_group_suffix", "tb003_txt016"),
                ("texture_suffix", "tb003_txt017"),
            ),
        ),
        (
            "Scene",
            (
                ("display_layer_suffix", "tb003_txt007"),
                ("set_suffix", "tb003_txt018"),
            ),
        ),
    )
    SUFFIX_FIELDS = {kw: name for _g, fields in SUFFIX_GROUPS for kw, name in fields}

    # Suffix fields with no Blender *object* type behind them. Kept (disabled) for
    # structural parity with mayatk's option box — see the parity ledger
    # (tentacle/docs/parity_map.py, "naming_slots"). Materials / images are real
    # engine targets (Naming.type_key) but never part of a selection scope.
    _BLENDER_NA = {
        "ik_handle_suffix": "IK handles are bone constraints, not objects.",
        "constraint_suffix": "Constraints live on objects, they are not objects.",
        "cluster_suffix": "A cluster is a Hook modifier, not an object.",
        "skin_cluster_suffix": "Skinning is an Armature modifier, not an object.",
        "blend_shape_suffix": "Blend shapes are shape keys, not objects.",
        "material_suffix": "Materials are datablocks, never in a selection scope.",
        "shading_group_suffix": "Blender has no shading group node.",
        "texture_suffix": "Images are datablocks, never in a selection scope.",
        "display_layer_suffix": "Display layers are Maya-only (a collection is a membership group, not an object type).",
        "set_suffix": "Sets are Maya-only (a collection is a membership group, not an object type).",
    }

    msg_intro = (
        "<u>Naming</u><br>• Pick a <b>Scope</b> in the header menu: the current "
        "selection, the whole scene, or files on disk.<br>• <b>Find</b> lists matches "
        "(and selects them, or browses for files); the other operations report "
        "every <code>old → new</code> change here.<br>• Tick <b>Dry Run</b> to "
        "preview any operation without changing anything."
    )

    def __init__(self, switchboard, log_level="INFO"):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.naming
        self._files: List[str] = []  # working set for the Directory / Files scopes
        self._last_dir = ""

        self.ui.txt002.setText(self.msg_intro)
        self.ui.txt002.restore_state = False  # a report, not a setting

        # Route the engine's report (old → new groups, summaries, warnings)
        # into the output panel. setup_logging_redirect is what enables the
        # clickable action:// select-links on object names.
        self.logger.setLevel(log_level)
        self.logger.hide_logger_name(True)
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt002)
        if hasattr(self.ui.txt002, "anchorClicked"):
            self.ui.txt002.anchorClicked.connect(self._on_log_link_clicked)

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the output panel."""
        from blendertk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu with tool description and workflow instructions."""
        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.menu.add("Separator", setTitle="Scope")
        widget.menu.add(
            "QComboBox",
            addItems=list(self.SCOPES),
            setObjectName="cmb_scope",
            setToolTip=self.sb.tooltip.fmt(
                title="Scope",
                body="What every operation acts on.",
                bullets=[
                    "<b>Selection</b> — the current selection.",
                    "<b>Scene</b> — every object in the current scene.",
                    "<b>Directory</b> — the files in a directory you pick "
                    "(not its sub-directories).",
                    "<b>Files</b> — files you pick.",
                ],
                notes=[
                    "With a file scope, <b>Find</b> opens the browser and the "
                    "matched files become the working set for Rename, Convert "
                    "Case and Strip Chars. Only the file name is changed — never "
                    "the extension. Suffix by Type / Location are scene-only.",
                ],
            ),
        )
        widget.menu.add(
            "QCheckBox",
            setText="Dry Run",
            setObjectName="chk_dry_run",
            setToolTip=self.sb.tooltip.fmt(
                title="Dry Run",
                body="Preview every operation in the output panel without "
                "renaming anything — scene objects and files alike.",
            ),
        )

        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Naming",
                body="Batch find, rename, and suffix scene objects or files. Each "
                "operation button has an option box (▸) for its parameters; the "
                "output panel at the bottom lists every change.",
                sections=[
                    (
                        "Operations",
                        [
                            "<b>Find</b> — select objects by name pattern "
                            "(wildcards or regex; case-sensitivity, Empties-only, "
                            "and regex toggles in the option box). With a file "
                            "scope it opens the browser and lists the matches.",
                            "<b>Rename</b> — replace matched names with a new "
                            "pattern. Option box: retain existing type suffix.",
                            "<b>Convert Case</b> — upper / lower / title / "
                            "capitalize / swapcase the names.",
                            "<b>Strip Chars</b> — remove leading or trailing "
                            "characters.",
                            "<b>Suffix by Location</b> — auto-number objects by "
                            "distance from a reference point (alphabetical or "
                            "integer). Scene only.",
                            "<b>Suffix by Type</b> — append type-based suffixes "
                            "(<code>_GEO</code>, <code>_GRP</code>, "
                            "<code>_JNT</code>, …). Suffix strings are "
                            "editable in the option box. Scene only.",
                        ],
                    ),
                    (
                        "Header menu",
                        [
                            "<b>Scope</b> — <i>Selection</i>, <i>Scene</i>, "
                            "<i>Directory</i> or <i>Files</i>. Applies to every "
                            "operation.",
                            "<b>Dry Run</b> — preview any operation in the output "
                            "panel; nothing is renamed.",
                        ],
                    ),
                ],
            )
        )

    # ------------------------------------------------------------------
    # Scope helpers
    # ------------------------------------------------------------------

    @property
    def scope(self) -> str:
        return self.ui.header.menu.cmb_scope.currentText()

    @property
    def dry_run(self) -> bool:
        return self.ui.header.menu.chk_dry_run.isChecked()

    @property
    def file_scope(self) -> bool:
        return self.scope in ("Directory", "Files")

    def _scene_targets(self) -> List:
        """Scene objects in scope, or an empty list (already reported)."""
        if self.scope == "Scene":
            objects = self.scene_objects()
            if not objects:
                self.logger.warning("The scene has no objects.")
        else:
            objects = btk.selected_objects()
            if not objects:
                self.logger.warning(
                    "Nothing selected. Select objects, or set Scope to 'Scene' "
                    "in the header menu to operate on the whole scene."
                )
        return objects

    def _browse(self) -> List[str]:
        """Open the Directory / Files browser; returns the chosen files (expanded)."""
        start = self._last_dir
        if not start:
            try:
                import bpy

                start = os.path.dirname(bpy.data.filepath)
            except ImportError:  # Qt-only harness — the file scopes never need bpy
                start = ""
        if self.scope == "Directory":
            chosen = self.sb.dir_dialog(
                title="Naming — select a directory", start_dir=start
            )
            paths = [chosen] if chosen else []
        else:
            paths = (
                self.sb.file_dialog(title="Naming — select files", start_dir=start)
                or []
            )
        files = ptk.FileNaming.expand(paths)
        if files:
            self._last_dir = os.path.dirname(files[0])
        return files

    def _file_targets(self, browse: bool = False) -> List[str]:
        """The file working set, browsing for one when asked or when there is none."""
        if not browse:  # drop files renamed or removed outside the tool
            self._files = [f for f in self._files if os.path.isfile(f)]
        if browse or not self._files:
            self._files = self._browse()
            if not self._files:
                self.logger.warning("No files chosen.")
        return self._files

    def _log_directories(self, files: List[str]) -> None:
        """One clickable directory line per distinct directory in *files*."""
        for d in sorted({os.path.dirname(f) for f in files}):
            self.logger.info(f"Directory: {self.log_link(d, 'open', path=d)}")

    def _run_on_files(self, operation, *args, **kwargs) -> bool:
        """Run a ``ptk.FileNaming`` operation on the working set when a file scope is active.

        Returns True when the file scope handled the call (even with nothing
        chosen), so the caller skips its scene branch.
        """
        if not self.file_scope:
            return False
        files = self._file_targets()
        if files:
            self._log_directories(files)
            result = operation(
                files, *args, dry_run=self.dry_run, logger=self.logger, **kwargs
            )
            if not self.dry_run and result:  # follow the renames
                renamed = dict(result)
                self._files = [renamed.get(f, f) for f in self._files]
        return True

    def _scene_only(self, operation: str) -> bool:
        """True (after reporting) when a scene-only operation was run in a file scope."""
        if self.file_scope:
            self.logger.notice(
                f"{operation} applies to scene objects only — set Scope to "
                "'Selection' or 'Scene' in the header menu."
            )
            return True
        return False

    @property
    def valid_suffixes(self):
        """The current Suffix By Type strings (non-empty), from the tb003 option box."""
        try:
            m = self.ui.tb003.option_box.menu
            suffixes = [getattr(m, name).text() for name in self.SUFFIX_FIELDS.values()]
            return [s for s in suffixes if s]
        except (AttributeError, RuntimeError):
            # Fallback if widgets not initialized or accessed before tb003 exists
            return [default for _kw, default, _l, _k in self.SUFFIX_TYPES]

    # ------------------------------------------------------------------
    # Find
    # ------------------------------------------------------------------

    def txt000_init(self, widget):
        """Initialize Find"""
        widget.setToolTip(
            self.sb.tooltip.fmt(
                title="Search by Name",
                bullets=[
                    "<code>startswith*</code> — names that start with the given characters.",
                    "<code>*endswith</code> — names that end with the given characters.",
                    "<code>*contains*</code> — names that contain the given characters.",
                    "Combine terms with <code>|</code> for multiple searches "
                    "(e.g. <code>start*|*end</code>).",
                ],
                notes=[
                    "Scene scopes search the whole scene and select the matches; "
                    "file scopes open the browser and keep the matches as the "
                    "working set (an empty pattern keeps every chosen file).",
                    "Enable <b>Regular Expression</b> in the option box for advanced "
                    "patterns. Capture groups defined here can be referenced from the "
                    "Rename field as <code>\\1</code>, <code>\\2</code> or "
                    "<code>\\g&lt;name&gt;</code>.",
                    "<b>Ignore Case</b> in the option box makes the search case-insensitive.",
                    "Each pipe-separated term supplies the text that Rename replaces, "
                    "so terms here pair positionally with the Rename field's.",
                ],
            )
        )
        widget.restore_state = False  # Don't persist the search text across sessions.
        widget.option_box.menu.setTitle("Find")
        # Add clear button to the menu option box
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Ignore Case",
            setObjectName="chk000",
            setToolTip="Search case insensitive.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Regular Expression",
            setObjectName="chk001",
            setToolTip="When checked, regular expression syntax is used instead of the default '*' and '|' wildcards.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Empties Only",
            setObjectName="chk007",
            setToolTip="Limit the search to Empty objects (Blender's locator analogue; scene scopes).",
        )
        widget.option_box.set_action(
            callback=widget.returnPressed.emit,
            icon="search",
            tooltip="Find matching objects (same as pressing Enter).",
        )

    @Signals("returnPressed")
    def txt000(self, widget):
        """Find: select scene objects (or browse for files) whose name matches the pattern."""
        # An asterisk denotes startswith*, *endswith, *contains*
        regex = widget.ui.txt000.option_box.menu.chk001.isChecked()
        ign_case = widget.ui.txt000.option_box.menu.chk000.isChecked()
        empties_only = widget.ui.txt000.option_box.menu.chk007.isChecked()
        text = widget.text()

        self.ui.txt002.clear()
        if self.file_scope:
            files = self._file_targets(browse=True)
            if not files:
                return
            hits = ptk.FileNaming.find(files, text, regex=regex, ignore_case=ign_case)
            self._files = hits
            self._log_directories(files)
            self._report_found(
                "Find", "file", text, [os.path.basename(f) for f in hits], len(files)
            )
            return

        if not text:
            return
        import bpy

        # Filter objects based on the empties_only option (Blender's locator analogue)
        objects = [
            o for o in bpy.data.objects if (o.type == "EMPTY" or not empties_only)
        ]
        obj_names = [o.name for o in objects]
        found_names = set(
            ptk.find_str(text, obj_names, regex=regex, ignore_case=ign_case)
        )
        found = [o for o, name in zip(objects, obj_names) if name in found_names]

        bpy.ops.object.select_all(action="DESELECT")
        for o in found:
            o.select_set(True)
        if found:
            bpy.context.view_layer.objects.active = found[0]
        self._report_found(
            "Find",
            "Empty" if empties_only else "object",
            text,
            [self.log_link(o.name, "select", node=o.name) for o in found],
            len(objects),
        )

    def _report_found(self, title, unit, text, items, total) -> None:
        """Report a Find result as one group + footer line."""
        plural = unit if len(items) == 1 else f"{unit}s"
        pattern = f" matching '{text}'" if text else ""
        if not items:
            self.logger.warning(f"No {plural}{pattern} (searched {total}).")
            self.ui.footer.setText(f"No {plural}{pattern}")
            return
        shown = items[: ptk.RenamePlan.MAX_REPORT_ITEMS]
        if len(items) > len(shown):
            shown.append(f"… +{len(items) - len(shown)} more")
        self.logger.log_group(
            f"{title} — {len(items)} of {total} {plural}{pattern}",
            shown,
            level="SUCCESS",
        )
        self.ui.footer.setText(f"Found {len(items)} {plural}{pattern}")

    # ------------------------------------------------------------------
    # Rename
    # ------------------------------------------------------------------

    def txt001_init(self, widget):
        """Initialize Rename"""
        widget.setToolTip(
            self.sb.tooltip.fmt(
                title="Rename",
                body="The new name pattern for the matched objects or files. The "
                "asterisk marks the part of the existing name that is <b>kept</b> — "
                "one asterisk replaces that side, a doubled one keeps the whole name "
                "and adds to it.",
                bullets=[
                    "<code>string</code> — replace the whole name.",
                    "<code>*string*</code> — replace only the part matched by Find.",
                    "<code>string*</code> — replace the prefix (drops everything through the match).",
                    "<code>*string</code> — replace the suffix (drops everything from the match on).",
                    "<code>string**</code> — add a prefix, keeping the whole name.",
                    "<code>**string</code> — add a suffix, keeping the whole name.",
                    "empty — strip the part matched by Find.",
                ],
                notes=[
                    "Pipe-separated terms pair with Find's: <code>*_L|*_R</code> in "
                    "Find with <code>*_lt|*_rt</code> here renames each side "
                    "differently. A single term applies to every Find term.",
                    "With <b>Regular Expression</b> enabled, Find's capture groups "
                    "are available here as <code>\\1</code>, <code>\\2</code> or "
                    "<code>\\g&lt;name&gt;</code>.",
                    "<b>Replace prefix</b> / <b>replace suffix</b> fall back to "
                    "adding when Find is empty or does not appear in a name.",
                ],
            )
        )
        widget.restore_state = False  # Don't persist the rename text across sessions.
        widget.option_box.menu.setTitle("Rename")
        # Add clear button to the menu option box
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Retain Suffix",
            setObjectName="chk002",
            setToolTip="Retain the suffix of the selected object(s) if it matches one defined in Suffix By Type.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Ignore Find",
            setObjectName="chk008",
            setToolTip="Ignore the find field and rename all matched objects.",
        )
        widget.option_box.set_action(
            callback=widget.returnPressed.emit,
            icon="edit",
            tooltip="Rename matched objects (same as pressing Enter).",
        )

    # The LineEdit text parameter is not emitted on `returnPressed`
    @Signals("returnPressed")
    def txt001(self, widget):
        """Rename: rename matched objects or files (find → replace, with regex / suffix options)."""
        # An asterisk denotes startswith*, *endswith, *contains*
        find = widget.ui.txt000.text()
        to = widget.text()
        regex = widget.ui.txt000.option_box.menu.chk001.isChecked()
        ign_case = widget.ui.txt000.option_box.menu.chk000.isChecked()
        retain_suffix = widget.ui.txt001.option_box.menu.chk002.isChecked()
        ignore_find = widget.ui.txt001.option_box.menu.chk008.isChecked()

        # Get current valid suffixes from property if retain_suffix is enabled
        valid_suffixes = self.valid_suffixes if retain_suffix else None
        fltr = "" if ignore_find else find

        self.ui.txt002.clear()
        if self._run_on_files(
            ptk.FileNaming.rename,
            to,
            fltr,
            regex=regex,
            ignore_case=ign_case,
            retain_suffix=retain_suffix,
            valid_suffixes=valid_suffixes,
        ):
            return

        objects = self._scene_targets()
        if not objects:
            return
        self.rename(
            objects,
            to,
            fltr,
            regex=regex,
            ignore_case=ign_case,
            retain_suffix=retain_suffix,
            valid_suffixes=valid_suffixes,
            dry_run=self.dry_run,
        )

    # ------------------------------------------------------------------
    # Convert Case
    # ------------------------------------------------------------------

    def tb000_init(self, widget):
        """Initialize Convert Case"""
        widget.option_box.menu.setTitle("Convert Case")
        widget.option_box.menu.add(
            "QComboBox",
            addItems=["capitalize", "upper", "lower", "swapcase", "title"],
            setObjectName="cmb001",
            setToolTip="Set desired python case operator.",
        )

    def tb000(self, widget):
        """Convert Case"""
        case = widget.option_box.menu.cmb001.currentText()

        self.ui.txt002.clear()
        if self._run_on_files(ptk.FileNaming.set_case, case):
            return

        objects = self._scene_targets()
        if not objects:
            return
        self.set_case(objects, case, dry_run=self.dry_run)

    # ------------------------------------------------------------------
    # Suffix By Location
    # ------------------------------------------------------------------

    def tb001_init(self, widget):
        """Initialize Suffix By Location"""
        widget.option_box.menu.setTitle("Suffix By Location")
        # Reference point is a choice between two named origins, not a modifier.
        ref = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_reference",
            setToolTip="Scene Origin: measure from the world origin (0,0,0).\nFirst Object: measure from the first selected object.",
        )
        ref.addItems(["Scene Origin", "First Object"])
        ref.setCurrentText("Scene Origin")  # preserve prior default (checkbox off)
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Alphabetical",
            setObjectName="chk005",
            setToolTip="Use an alphabet character as a suffix when there is less than 26 objects, else use integers.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Integers",
            setObjectName="chk002",
            setChecked=True,
            setToolTip="Strip any trailing integers. ie. '123' of 'cube123'",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Defined Suffixes",
            setObjectName="chk003",
            setChecked=True,
            setToolTip="Strip any suffixes found in the 'Suffix By Type' settings (e.g. '_GRP', '_LOC').",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Independent Groups",
            setObjectName="chk007",
            setToolTip="Group objects by name type and suffix them independently.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Reverse",
            setObjectName="chk004",
            setToolTip="Reverse the naming order. (Farthest object first)",
        )

    def tb001(self, widget):
        """Suffix By Location"""
        first_obj_as_ref = (
            widget.option_box.menu.cmb_reference.currentText() == "First Object"
        )
        alphabetical = widget.option_box.menu.chk005.isChecked()
        strip_trailing_ints = widget.option_box.menu.chk002.isChecked()
        strip_defined_suffixes = widget.option_box.menu.chk003.isChecked()
        reverse = widget.option_box.menu.chk004.isChecked()
        independent_groups = widget.option_box.menu.chk007.isChecked()

        self.ui.txt002.clear()
        if self._scene_only("Suffix By Location"):
            return
        objects = self._scene_targets()
        if not objects:
            return
        self.append_location_based_suffix(
            objects,
            first_obj_as_ref=first_obj_as_ref,
            alphabetical=alphabetical,
            strip_trailing_ints=strip_trailing_ints,
            strip_defined_suffixes=strip_defined_suffixes,
            valid_suffixes=self.valid_suffixes,
            reverse=reverse,
            independent_groups=independent_groups,
            dry_run=self.dry_run,
        )

    # ------------------------------------------------------------------
    # Strip Chars
    # ------------------------------------------------------------------

    def tb002_init(self, widget):
        """Initialize Strip Chars"""
        widget.option_box.menu.setTitle("Strip Chars")
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Num Chars:",
            setObjectName="s000",
            setValue=1,
            setToolTip="The number of characters to delete.",
        )
        widget.option_box.menu.add(
            "QComboBox",
            addItems=["Leading", "Trailing"],
            setCurrentText="Trailing",
            setObjectName="cmb002",
            setToolTip="Which end of the name to delete characters from.",
        )

    def tb002(self, widget):
        """Strip Chars: remove a number of leading/trailing characters from the names in scope."""
        kwargs = {
            "num_chars": widget.option_box.menu.s000.value(),
            "trailing": widget.option_box.menu.cmb002.currentText() == "Trailing",
        }
        self.ui.txt002.clear()
        if self._run_on_files(ptk.FileNaming.strip_chars, **kwargs):
            return

        objects = self._scene_targets()
        if not objects:
            return
        self.strip_chars(objects, dry_run=self.dry_run, **kwargs)

    # ------------------------------------------------------------------
    # Suffix By Type
    # ------------------------------------------------------------------

    def tb003_init(self, widget):
        """Initialize Suffix By Type"""
        widget.option_box.menu.setTitle("Suffix By Type")
        defaults = {
            kw: (default, label) for kw, default, label, _k in self.SUFFIX_TYPES
        }
        for group, fields in self.SUFFIX_GROUPS:
            widget.option_box.menu.add("Separator", setTitle=group)
            for kw, name in fields:
                default, label = defaults[kw]
                na = self._BLENDER_NA.get(kw)
                widget.option_box.menu.add(
                    "QLineEdit",
                    setPlaceholderText=f"{label} Suffix",
                    setText=default,
                    setObjectName=name,
                    setEnabled=na is None,
                    setToolTip=(
                        f"No Blender equivalent: {na}"
                        if na
                        else f"Suffix for {label.lower()}s. Leave empty to skip this type."
                    ),
                )
        widget.option_box.menu.add(
            "Separator",
            setTitle="Suffix Options",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Padding",
            setObjectName="tb003_chk004",
            setChecked=True,
            setToolTip="Strip orphaned trailing underscores and, only when underscores were at the end, also strip exposed trailing digits. Preserves intentional '_02' numbering.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Integers",
            setObjectName="tb003_chk002",
            setChecked=False,
            setToolTip="Strip any trailing integers. ie. '123' of 'cube123'",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Underscores",
            setObjectName="tb003_chk003",
            setChecked=False,
            setToolTip="Strip any trailing underscores after stripping integers (e.g. 'cube_01_' -> 'cube').",
        )

    def tb003(self, widget):
        """Suffix By Type"""
        m = widget.option_box.menu
        kwargs = {
            kw: getattr(m, name).text() for kw, name in self.SUFFIX_FIELDS.items()
        }
        kwargs.update(
            strip_trailing_ints=m.tb003_chk002.isChecked(),
            strip_trailing_underscores=m.tb003_chk003.isChecked(),
            strip_trailing_padding=m.tb003_chk004.isChecked(),
            dry_run=self.dry_run,
        )
        self.ui.txt002.clear()
        if self._scene_only("Suffix By Type"):
            return
        objects = self._scene_targets()
        if not objects:
            return
        self.suffix_by_type(objects, **kwargs)


# --------------------------------------------------------------------------------------------


if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("naming", reload=True)
    ui.show(pos="screen", app_exec=True)

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
