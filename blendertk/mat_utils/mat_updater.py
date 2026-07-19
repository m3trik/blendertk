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
numpy but not PIL). Two Maya concepts don't survive the port (see the ``# TODO(blender-parity)``
tags at their wiring point): the ``cmb_transfer_mode`` File Management combo (the Blender engine
always writes processed textures straight to the Output Folder — there is no separate
copy/move-the-original-source-file step to select a mode for), and the clickable ``action://`` log
links (mayatk's engine emits ``cls.logger.log_link(...)`` for "select material in viewport"; the
Blender engine doesn't emit those yet — the ``UiUtils.dispatch_log_link`` counterpart already
exists, so once the engine emits the links, wire them here like the other blendertk tools do).
Everything else —
including "Discover Maps" sibling discovery — is fully wired: Blender's project-folder analogue is
the .blend's own directory (``workspace``), the same concept the engine already uses to resolve a
relative Output Folder.

Served by ``BlenderUiHandler`` (``marking_menu.show("mat_updater")``); the Qt-only imports (``fmt``,
``QtCore``) are deferred into the call bodies — headless Blender ships no Qt binding.
"""
import pythontk as ptk
from uitk.switchboard import Cancelable

import blendertk as btk
from blendertk.mat_utils._mat_utils import MatUpdater


class MatUpdaterSlots(MatUpdater):
    """Switchboard slot wiring for the Material Updater panel."""

    msg_intro = "Batch-reprocess material textures (convert / resize / pack) and repath them."
    msg_completed = '<br><hl style="color:rgb(0, 255, 255);"><b>COMPLETED.</b></hl>'

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
        # TODO(blender-parity): mayatk wires txt001.anchorClicked -> UiUtils.dispatch_log_link for
        # clickable "select material" log links (via cls.logger.log_link(...) in the engine).
        # blendertk's UiUtils.dispatch_log_link counterpart now exists (used by Telescope Rig /
        # hierarchy_sync / scene_exporter); the only remaining gap is the MatUpdater engine
        # emitting the action:// links — once it does, wire txt001.anchorClicked here like those.

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

    # ------------------------------------------------------------------ header (options)
    def header_init(self, widget):
        """Format global options in the header menu (mirror of the Maya panel's, minus the
        Maya-only File Management transfer mode — see the TODO at its wiring point)."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

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
        widget.menu.add("Separator", setTitle="Processing")
        # Convert Format
        cmb_format = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_convert_format",
            setToolTip="Convert texture file formats.",
        )
        cmb_format.addItem("Convert Format: None", None)
        for ext in ptk.ImgUtils.writable:
            cmb_format.addItem(f"Convert Format: {ext}", ext)

        # Max Size
        cmb_size = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_max_size",
            setToolTip="Maximum texture size.",
        )
        cmb_size.addItem("Max Size: None", None)
        for size in [512, 1024, 2048, 4096, 8192]:
            cmb_size.addItem(f"Max Size: {size}", size)

        # Mask Map Scale
        cmb_scale = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_mask_scale",
            setToolTip="Scale factor for Mask Maps.",
        )
        for scale in [1.0, 0.5, 0.25, 0.125]:
            cmb_scale.addItem(f"Mask Scale: {scale}", scale)
        # Force Packed Maps
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_force_packed",
            setText="Force Packed Maps",
            setToolTip=(
                "Force generation of packed maps (ORM, MSAO) even if some source maps are "
                "missing (written to disk for engine export — Blender's Principled BSDF keeps "
                "separate Roughness / Metallic / AO inputs, so packed maps are never rewired "
                "into the shader graph)."
            ),
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
            setToolTip="Allow substituting missing output maps with alternatives (e.g. use AO map alone if Mask Map cannot be generated). Ignored if Force Packed Maps is enabled.",
        )
        # Connect Force Packed to disable Output Fallbacks
        widget.menu.chk_force_packed.toggled.connect(
            lambda state: widget.menu.chk_output_fallbacks.setDisabled(state)
        )
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

        # Archive Folder
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_old_files",
            setPlaceholderText="Archive To (Optional)",
            setToolTip="Optional: Folder (under Output Folder) to move original files into.",
        )
        widget.set_help_text(
            fmt(
                title="Material Updater",
                body="Batch-process scene materials and their textures — "
                "format conversion, max-size enforcement, mask scaling, and "
                "packed-map (ORM / MSAO) generation — via the shared pythontk factory, "
                "then repath each material's image nodes to the results.",
                steps=[
                    "Pick a <b>Selection Mode</b> (Selected materials / All "
                    "scene materials).",
                    "Open the header menu (▸) and configure the processing "
                    "and file-management options below.",
                    "Press <b>Update</b> to run.",
                ],
                sections=[
                    ("Processing options", [
                        "<b>Max Size</b> — clamp texture resolution.",
                        "<b>Mask Map Scale</b> — independent resolution for "
                        "mask outputs.",
                        "<b>Force Packed Maps</b> — emit ORM / MSAO regardless "
                        "of whether the source channels exist (uses input fallbacks). Written "
                        "to disk for engine export; never wired into the Principled BSDF.",
                        "<b>Use Input Fallbacks</b> — generate missing inputs "
                        "from related ones (e.g. Base Color from Diffuse).",
                        "<b>Use Output Fallbacks</b> — substitute missing "
                        "outputs (e.g. AO alone for Mask Map). Disabled when "
                        "Force Packed Maps is on.",
                        "<b>Discover Maps in sourceimages</b> — gap-fill each "
                        "material with same-base-name textures sitting in "
                        "the .blend's own folder that were never connected. Only missing "
                        "map types are added; connected textures are kept.",
                        "<b>Dry Run</b> — preview the plan without writing files.",
                    ]),
                    ("File management", [
                        "<b>Transfer Mode</b> — not used by the Blender engine "
                        "(disabled); processed textures always land directly in Output Folder.",
                        "<b>Output Folder</b> — destination for processed textures.",
                        "<b>Archive To</b> — optional folder to move original "
                        "files into for safekeeping.",
                    ]),
                    ("Notes", [
                        "Needs Pillow — installed into Blender's Python on first run.",
                    ]),
                ],
            )
        )

    @property
    def selection_mode(self):
        return self.ui.cmb_selection_mode.currentText()

    @property
    def move_to_folder(self):
        return self.ui.txt_move_to.text() or None

    @property
    def max_size(self):
        return self.ui.cmb_max_size.currentData()

    @property
    def mask_map_scale(self):
        return self.ui.cmb_mask_scale.currentData()

    @property
    def output_extension(self):
        return self.ui.cmb_convert_format.currentData()

    @property
    def old_files_folder(self):
        return self.ui.txt_old_files.text() or None

    def cmb001_init(self, widget):
        """Initialize Presets"""
        from qtpy import QtCore

        if not widget.is_initialized:
            widget.restore_state = True
            # Populate presets
            presets = ptk.MapRegistry().get_workflow_presets()
            widget.clear()
            for name, settings in presets.items():
                widget.addItem(name)
                description = settings.get("description")
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
        force_packed = menu.chk_force_packed.isChecked()
        use_input_fallbacks = menu.chk_input_fallbacks.isChecked()
        use_output_fallbacks = menu.chk_output_fallbacks.isChecked()
        discover_sourceimages = menu.chk_discover_sourceimages.isChecked()

        max_size = self.max_size

        # Resolve target materials from the header selection mode. `None` means "let
        # update_materials default to all scene materials".
        mode = self.selection_mode
        materials = None

        if mode == "Selected Objects":
            selection = btk.selected_objects()
            if not selection:
                self.ui.txt001.append("No objects selected.")
                return
            materials = btk.get_mats(selection)
            if not materials:
                self.ui.txt001.append(
                    "No materials found on the selected objects."
                )
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
                self.ui.txt001.append(
                    "No materials reference the selected textures."
                )
                return

        self.ui.txt001.clear()
        self.ui.txt001.append(f"Starting update with preset: {config_name}...")

        try:
            # Build config dictionary
            config = {
                "preset": config_name,
                "max_size": max_size,
                "mask_map_scale": self.mask_map_scale,
                "output_extension": self.output_extension,
                "move_to_folder": self.move_to_folder,
                "old_files_folder": self.old_files_folder,
                "force_packed_maps": force_packed,
                "use_input_fallbacks": use_input_fallbacks,
                "use_output_fallbacks": use_output_fallbacks,
                "discover_sourceimages": discover_sourceimages,
                "dry_run": dry_run,
            }

            with self.sb.progress(text="Updating Materials") as update:
                results = self.update_materials(
                    materials=materials,
                    config=config,
                    verbose=True,
                    progress_callback=self.sb.progress_adapter(update),
                )
            updated = sum(r["updated"] for r in results.values())
            self.ui.txt001.append(
                self.msg_completed
                + f" {updated} texture(s) repathed across {len(results)} material(s)."
            )
        except Exception as e:
            self.ui.txt001.append(f"<br><font color='red'>ERROR: {e}</font>")
            import traceback

            self.ui.txt001.append(traceback.format_exc())


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("mat_updater", reload=True)
    ui.show(pos="screen", app_exec=True)
