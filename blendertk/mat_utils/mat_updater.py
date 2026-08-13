# !/usr/bin/python
# coding=utf-8
"""Material Updater tool panel — Switchboard slot wiring for the co-located ``mat_updater.ui``.

Blender counterpart of mayatk's Material Updater, mirroring its structure 1:1 (same objectNames,
same header-menu layout, same method shapes) so the two panels are a true mirror of each other.
The reprocessing engine is the SHARED pythontk factory, exposed here as
``blendertk.MatUpdater.update_materials`` (see ``mat_utils/_mat_utils.py``); this is the thin
driver. ``MatUpdaterSlots`` subclasses ``MatUpdater`` exactly like the Maya slot, so ``self.logger``
and ``self.update_materials`` are the engine's.

Pillow is provisioned on demand by ``btk.ensure_image_deps`` inside the engine (Blender bundles
numpy but not PIL). One Maya concept doesn't survive the port (see the ``# TODO(blender-parity)``
tag at its wiring point): the ``cmb_transfer_mode`` File Management combo — the Blender engine
always writes processed textures straight to the Output Folder, so there is no separate
copy/move-the-original-source-file step to select a mode for. Everything else —
including "Discover Maps" sibling discovery and the clickable ``action://`` log links — is fully
wired: Blender's project-folder analogue is the .blend's own directory (``workspace``), the same
concept the engine already uses to resolve a relative Output Folder.

Served by ``BlenderUiHandler`` (``marking_menu.show("mat_updater")``); the Qt-only imports (``fmt``,
``QtCore``) are deferred into the call bodies — headless Blender ships no Qt binding.
"""

import pythontk as ptk
from uitk.switchboard import Cancelable

import blendertk as btk
from blendertk.mat_utils._mat_utils import MatUpdater


class MatUpdaterSlots(MatUpdater):
    """Switchboard slot wiring for the Material Updater panel."""

    msg_intro = "Reconfigure existing materials for a target workflow preset."

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.mat_updater
        self.set_log_level(log_level)
        # Mirror the Maya panel: redirect the engine logger into the text panel so the verbose
        # per-material run log appears inline. Best-effort — the panel still works without it.
        try:
            self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            self.logger.setup_logging_redirect(self.ui.txt001)
        except Exception:
            pass
        # Dispatch the engine's action:// links. The Maya twin links a material to
        # "select"; Blender's analogue is "graph" (open it in the Shader Editor),
        # matching what game_shader already emits for a created material.
        if hasattr(self.ui.txt001, "anchorClicked"):
            self.ui.txt001.anchorClicked.connect(self._on_log_link_clicked)

        # Mirror the Maya panel: show where textures resolve from. Maya's "sourceimages" project
        # folder has no Blender equivalent; the nearest analogue is the .blend's own folder
        # (``workspace`` — the same concept the engine resolves relative Output Folders against).
        try:
            workspace = btk.get_env_info("workspace")
            info = ptk.truncate(
                f"<br><font color='#888'>Workspace: {workspace}</font><br>",
                mode="middle",
            )
            self.ui.txt001.setText(self.msg_intro + info)
        except Exception:
            self.ui.txt001.setText(self.msg_intro)

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from blendertk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    # ------------------------------------------------------------------ header (options)
    def header_init(self, widget):
        """Format global options in the header menu (mirror of the Maya panel's, minus the
        Maya-only File Management transfer mode — see the TODO at its wiring point)."""
        # Selection Mode
        widget.menu.add(
            "QComboBox",
            setObjectName="cmb_selection_mode",
            addItems=["Selected Objects", "All Scene Materials", "Browse..."],
            setToolTip=(
                "Choose the texture/material source:\n"
                "• Selected Objects — materials on the current selection.\n"
                "• All Scene Materials — every material in the scene.\n"
                "• Browse... — pick texture files; updates materials that reference them."
            ),
        )
        # Dry Run — kept at the top so it's the first thing reached under the selection mode;
        # simulate the run without writing files or repathing image nodes.
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_dry_run",
            setText="Dry Run",
            setToolTip="Simulate the process without making changes.",
        )
        # Reconfiguration only — file format, max size, mask/secondary scale
        # and bit depth are NOT offered here. They duplicate the Map Converter's
        # Optimize tool, which owns image optimization for the whole pipeline;
        # this panel decides *which* maps a material gets and repaths to them.
        widget.menu.add("Separator", setTitle="Processing")
        # Missing Maps — the policy for a packed map (ORM / MSAO) the preset
        # calls for whose source channels aren't all resolvable. Same three
        # rules, wording and prefixed-combo presentation as the Map Packer's
        # control, so one vocabulary covers both ends of the pipeline.
        cmb_missing = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_missing_maps",
            setToolTip=(
                "What to do when a packed map (ORM, MSAO) the preset calls for is missing one "
                "or more of its source maps (and it can't be derived from the maps that are "
                "present). Packed maps are written to disk for engine export — Blender's "
                "Principled BSDF keeps separate Roughness / Metallic / AO inputs, so they are "
                "never rewired into the shader graph.\n"
                "Skip Map (default): the packed map isn't written. A gap whose fill is harmless "
                "still packs - an absent AO fills white - so this is about the ones that would "
                "bake in a wrong value, like black roughness or white (mirror) smoothness.\n"
                "Pack If 2+ Maps: pack once at least two source channels resolved - enough "
                "that the result is still a useful packed map rather than a single map wearing "
                "a packed name.\n"
                "Pack Anyway: always pack; missing channels are filled with their default value."
            ),
        )
        cmb_missing.add(
            [
                ("Skip Map", ptk.MapRegistry.MISSING_SKIP),
                ("Pack If 2+ Maps", ptk.MapRegistry.MISSING_MULTI),
                ("Pack Anyway", ptk.MapRegistry.MISSING_FORCE),
            ],
            prefix="Missing Maps:",
        )
        # Use Input Fallbacks
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_input_fallbacks",
            setText="Use Input Fallbacks",
            setChecked=True,
            setToolTip="Allow generating maps from alternative inputs (e.g. create Base Color from Existing Diffuse).",
        )
        # Use Output Fallbacks
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_output_fallbacks",
            setText="Use Output Fallbacks",
            setChecked=True,
            setToolTip="Allow substituting missing output maps with alternatives (e.g. use AO map alone if Mask Map cannot be generated). Ignored when Missing Maps is set to Pack Anyway.",
        )

        # Output fallbacks only have something to do while a packed map can
        # still fail to be written: 'Pack Anyway' always emits one, so there is
        # never a missing output left to substitute for.
        def _update_output_fallbacks_state():
            widget.menu.chk_output_fallbacks.setDisabled(
                cmb_missing.currentData() == ptk.MapRegistry.MISSING_FORCE
            )

        cmb_missing.currentIndexChanged.connect(
            lambda *_: _update_output_fallbacks_state()
        )
        _update_output_fallbacks_state()
        # Discover Maps in sourceimages — Blender has no ``sourceimages`` project-folder
        # convention; the nearest analogue is the .blend's own directory (the same "workspace"
        # concept the engine already resolves relative Output Folders against).
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_discover_sourceimages",
            setText="Discover Maps in sourceimages",
            setChecked=True,
            setToolTip=(
                "Pull in same-base-name textures found in the .blend file's own folder that "
                "aren't wired into the material (e.g. a Normal sitting on disk but never "
                "connected).\n"
                "Only map types missing from the material are added; connected textures are "
                "never replaced. No-op for an unsaved file."
            ),
        )
        widget.menu.add("Separator", setTitle="File Management")
        # File Transfer Mode
        # TODO(blender-parity): mayatk uses this to choose how SOURCE files are handled
        # (copy/move/leave) once Output Folder is set. The Blender engine has no separate
        # source-file transfer step — ``ptk.MapFactory.prepare_maps`` writes processed textures
        # straight to Output Folder, and repathing points each material at the result — so there
        # is no Blender-native behavior for this control to select between. Disabled rather than
        # dropped, to keep the header menu structurally identical to mayatk's.
        cmb_transfer = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_transfer_mode",
            setToolTip=(
                "Not used by the Blender engine — processed textures always land directly in "
                "Output Folder (no separate source-file copy/move step to choose a mode for)."
            ),
        )
        cmb_transfer.addItem("Copy All to Output", "copy")
        cmb_transfer.addItem("Move All to Output", "move")
        cmb_transfer.addItem("Use Existing Folders", "none")
        cmb_transfer.setEnabled(False)

        # Output Folder
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_move_to",
            setPlaceholderText="Output Folder (Optional)",
            setToolTip=(
                "Folder to write processed textures to and repath the materials' image nodes "
                "toward. Relative paths resolve against the .blend's own folder. Blank = "
                "process each texture in place."
            ),
        )

        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Material Updater",
                body="Reconfigure scene materials for a target workflow — resolve "
                "each material's texture set and generate the packed maps the "
                "preset calls for (ORM / MSAO) via the shared pythontk factory, "
                "then repath each material's image nodes to the results.<br>"
                "Image <i>optimization</i> — file format, resolution clamp, "
                "secondary scale, bit depth, archiving originals — is not done "
                "here: run the <b>Map Converter</b>'s <b>Optimize</b> tool for that.",
                steps=[
                    "Pick a <b>Selection Mode</b> (Selected materials / All "
                    "scene materials).",
                    "Open the header menu (▸) and configure the processing "
                    "and file-management options below.",
                    "Press <b>Update</b> to run.",
                ],
                sections=[
                    (
                        "Processing options",
                        [
                            "<b>Missing Maps</b> — what an ORM / MSAO does when a "
                            "source channel can't be resolved: <i>Skip Map</i> "
                            "writes nothing unless the gap is harmless (an absent "
                            "AO fills white), <i>Pack If 2+ Maps</i> packs once at "
                            "least two channels resolved, <i>Pack Anyway</i> packs "
                            "regardless (absent channels take their default fill). "
                            "Written to disk for engine export; never wired into the "
                            "Principled BSDF.",
                            "<b>Use Input Fallbacks</b> — generate missing inputs "
                            "from related ones (e.g. Base Color from Diffuse).",
                            "<b>Use Output Fallbacks</b> — substitute missing "
                            "outputs (e.g. AO alone for Mask Map). Disabled when "
                            "Missing Maps is set to Pack Anyway.",
                            "<b>Discover Maps in sourceimages</b> — gap-fill each "
                            "material with same-base-name textures sitting in "
                            "the .blend's own folder that were never connected. Only missing "
                            "map types are added; connected textures are kept.",
                            "<b>Dry Run</b> — preview the plan without writing files.",
                        ],
                    ),
                    (
                        "File management",
                        [
                            "<b>Transfer Mode</b> — not used by the Blender engine "
                            "(disabled); processed textures always land directly in Output Folder.",
                            "<b>Output Folder</b> — destination for processed textures.",
                        ],
                    ),
                    (
                        "Notes",
                        [
                            "Needs Pillow — installed into Blender's Python on first run.",
                        ],
                    ),
                ],
            )
        )

    @property
    def selection_mode(self):
        return self.ui.cmb_selection_mode.currentText()

    @property
    def move_to_folder(self):
        return self.ui.txt_move_to.text() or None

    def cmb001_init(self, widget):
        """Initialize Presets"""
        from qtpy import QtCore

        if not widget.is_initialized:
            widget.restore_state = True
            # Names + tooltips from the OutputTemplates SSoT (shared with
            # game_shader / the converter / compositor / scene exporter).
            widget.clear()
            for name, description in ptk.OutputTemplates.profile_choices():
                widget.addItem(name)
                if description:
                    widget.setItemData(
                        widget.count() - 1, description, QtCore.Qt.ToolTipRole
                    )

    @Cancelable(300)
    def b001(self):
        """Update Materials"""
        config_name = self.ui.cmb001.currentText()

        menu = self.ui.header.menu
        dry_run = menu.chk_dry_run.isChecked()
        missing_map_rule = menu.cmb_missing_maps.currentData()
        use_input_fallbacks = menu.chk_input_fallbacks.isChecked()
        use_output_fallbacks = menu.chk_output_fallbacks.isChecked()
        discover_sourceimages = menu.chk_discover_sourceimages.isChecked()

        # Resolve target materials from the header selection mode. `None` means "let
        # update_materials default to all scene materials".
        mode = self.selection_mode
        materials = None

        if mode == "Selected Objects":
            selection = btk.selected_objects()
            if not selection:
                self.logger.warning("No objects selected.")
                return
            materials = btk.get_mats(selection)
            if not materials:
                self.logger.warning("No materials found on the selected objects.")
                return
        elif mode == "Browse...":
            try:
                start_dir = btk.get_env_info("workspace") or ""
            except Exception:
                start_dir = ""
            paths = self.sb.file_dialog(
                file_types=[f"*.{ext}" for ext in ptk.ImgUtils.texture_file_types],
                title="Select textures whose materials should be updated:",
                start_dir=start_dir,
                allow_multiple=True,
            )
            if not paths:
                return
            materials = btk.materials_for_textures(paths)
            if not materials:
                self.logger.warning("No materials reference the selected textures.")
                return

        self.ui.txt001.clear()

        try:
            # Reconfiguration keys only. Image-optimization keys (max_size,
            # mask_map_scale, output_extension, old_files_folder) are
            # deliberately absent — the factory then leaves resolution, format
            # and bit depth alone, and the Map Converter's Optimize tool owns
            # that pass. ``update_materials`` still accepts them, so a script
            # can drive both in one call; the panel does not offer them.
            config = {
                "preset": config_name,
                "move_to_folder": self.move_to_folder,
                "missing_map_rule": missing_map_rule,
                "use_input_fallbacks": use_input_fallbacks,
                "use_output_fallbacks": use_output_fallbacks,
                "discover_sourceimages": discover_sourceimages,
                "dry_run": dry_run,
            }

            with self.sb.progress(text="Updating Materials") as update:
                self.update_materials(
                    materials=materials,
                    config=config,
                    verbose=True,
                    progress_callback=self.sb.progress_adapter(update),
                )
            # No completion line appended here — update_materials closes the
            # run with its own summary box (mirrors the scene exporter).
        except Exception as e:
            # Through the logger, not txt001.append: the handler is already
            # pointed at txt001, so this lands in the same place *and* picks up
            # ERROR colouring plus any file/ring sink the session attached.
            # Message and traceback go out as ONE record — a formatted
            # traceback is already a single multi-line string, and the widget
            # handler renders it with ``white-space:pre-wrap``, so it needs no
            # ``log_group`` to avoid per-frame paragraphs. Plain ``error``
            # also keeps the whole thing level-filtered, which ``log_group``
            # (via ``log_raw``) would not be.
            import traceback

            self.logger.error(
                f"Material update failed: {e}\n{traceback.format_exc()}"
            )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("mat_updater", reload=True)
    ui.show(pos="screen", app_exec=True)
