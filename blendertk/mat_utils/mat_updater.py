# !/usr/bin/python
# coding=utf-8
"""Material Updater tool panel — Switchboard slot wiring for the co-located ``mat_updater.ui``.

Blender counterpart of mayatk's Material Updater: batch-reprocess the chosen materials' textures
(format conversion / max-size / optimize / packed-map generation) and repath their image nodes to
the results. The reprocessing engine is the SHARED pythontk factory, exposed here as
``blendertk.MatUpdater.update_materials`` (see ``mat_utils/_mat_utils.py``); this is the thin
driver. ``MatUpdaterSlots`` subclasses ``MatUpdater`` exactly like the Maya slot, so ``self.logger``
and ``self.update_materials`` are the engine's.

Pillow is provisioned on demand by ``btk.ensure_image_deps`` inside the engine (Blender bundles
numpy but not PIL). Maya-only knobs my lean engine doesn't honor (file-transfer mode, archive
folder, the ORM/MSAO shader-rewire) are dropped rather than shown as dead options — see the engine
docstring for the Blender divergence. Served by ``BlenderUiHandler``
(``marking_menu.show("mat_updater")``); the Qt-only imports are deferred into the call bodies.
"""
import pythontk as ptk

import blendertk as btk
from blendertk.mat_utils._mat_utils import MatUpdater


class MatUpdaterSlots(MatUpdater):
    """Switchboard slot wiring for the Material Updater panel."""

    msg_intro = "Batch-reprocess material textures (convert / resize / pack) and repath them."

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
        self.ui.txt001.setText(self.msg_intro)

    # ------------------------------------------------------------------ header (options)
    def header_init(self, widget):
        """Build the processing options in the header menu (mirror of the Maya panel's, minus the
        knobs the Blender engine doesn't honor)."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add(
            "QComboBox", setObjectName="cmb_selection_mode",
            addItems=["Selected Objects", "All Scene Materials", "Browse…"],
            setToolTip="Which materials to update:\n"
            "• Selected Objects — materials on the current selection.\n"
            "• All Scene Materials — every material in the scene.\n"
            "• Browse… — pick texture files; updates the materials that reference them.",
        )
        widget.menu.add("Separator", setTitle="Processing")

        cmb_format = widget.menu.add(
            "QComboBox", setObjectName="cmb_convert_format",
            setToolTip="Convert texture file format.",
        )
        cmb_format.addItem("Convert Format: None", None)
        for ext in ptk.ImgUtils.writable:
            cmb_format.addItem(f"Convert Format: {ext}", ext)

        cmb_size = widget.menu.add(
            "QComboBox", setObjectName="cmb_max_size", setToolTip="Clamp maximum texture size.",
        )
        cmb_size.addItem("Max Size: None", None)
        for size in (512, 1024, 2048, 4096, 8192):
            cmb_size.addItem(f"Max Size: {size}", size)

        cmb_scale = widget.menu.add(
            "QComboBox", setObjectName="cmb_mask_scale",
            setToolTip="Independent resolution scale for mask outputs.",
        )
        for scale in (1.0, 0.5, 0.25, 0.125):
            cmb_scale.addItem(f"Mask Scale: {scale}", scale)

        widget.menu.add(
            "QCheckBox", setObjectName="chk_force_packed", setText="Force Packed Maps",
            setToolTip="Emit packed maps (ORM / MSAO) on disk even when some source channels are "
            "missing (written for engine export — not wired into Blender's shader).",
        )
        widget.menu.add(
            "QCheckBox", setObjectName="chk_input_fallbacks", setText="Use Input Fallbacks",
            setChecked=True,
            setToolTip="Generate missing inputs from related ones (e.g. Base Color from Diffuse).",
        )
        widget.menu.add(
            "QCheckBox", setObjectName="chk_output_fallbacks", setText="Use Output Fallbacks",
            setChecked=True,
            setToolTip="Substitute missing outputs with alternatives (e.g. AO alone for a Mask "
            "Map). Disabled when Force Packed Maps is on.",
        )
        widget.menu.chk_force_packed.toggled.connect(
            lambda state: widget.menu.chk_output_fallbacks.setDisabled(state)
        )
        widget.menu.add(
            "QCheckBox", setObjectName="chk_dry_run", setText="Dry Run",
            setToolTip="Report the plan without writing files or repathing.",
        )

        widget.menu.add("Separator", setTitle="Output")
        widget.menu.add(
            "QLineEdit", setObjectName="txt_move_to",
            setPlaceholderText="Output Folder (optional)",
            setToolTip="Write processed textures here and repath to them (relative paths resolve "
            "against the .blend's folder). Blank = process in place.",
        )

        widget.set_help_text(
            fmt(
                title="Material Updater",
                body="Batch-process materials and their textures — format conversion, max-size "
                "enforcement, mask scaling, and packed-map (ORM / MSAO) generation — via the shared "
                "pythontk factory, then repath each material's image nodes to the results.",
                steps=[
                    "Pick a <b>preset</b> in the combo (fine-tune options in this menu).",
                    "Choose a <b>Selection Mode</b> above.",
                    "Press <b>Update</b>.",
                ],
                sections=[
                    ("Notes", [
                        "Needs Pillow — installed into Blender's Python on first run.",
                        "Packed maps land on disk for engine export; Blender's Principled BSDF "
                        "keeps separate Roughness / Metallic / AO inputs (no ORM rewire).",
                    ]),
                ],
            )
        )

    # ------------------------------------------------------------------ cmb001  preset
    def cmb001_init(self, widget):
        """Populate the workflow-preset combo."""
        if getattr(widget, "is_initialized", False):
            return
        widget.restore_state = True
        presets = ptk.MapRegistry().get_workflow_presets()
        widget.clear()
        for name, settings in presets.items():
            widget.addItem(name)
            description = settings.get("description")
            if description:
                widget.setItemData(widget.count() - 1, description, 3)  # Qt.ToolTipRole

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _browse_textures():
        """File dialog for the 'Browse…' scope — returns picked texture paths (or None)."""
        from qtpy import QtWidgets

        exts = " ".join(f"*.{e}" for e in ptk.ImgUtils.texture_file_types)
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            None, "Select textures whose materials should be updated", "",
            f"Textures ({exts});;All (*)",
        )
        return list(paths) or None

    # ------------------------------------------------------------------ b001  Update
    def b001(self):
        """Update Materials — assemble config from the panel and run the engine."""
        menu = self.ui.header.menu
        preset = self.ui.cmb001.currentText() or None
        mode = menu.cmb_selection_mode.currentText()

        if mode == "Selected Objects":
            selection = btk.selected_objects()
            if not selection:
                self.sb.message_box("<hl>Nothing selected</hl><br>Select object(s) to update.")
                return
            materials = btk.get_mats(selection)
            if not materials:
                self.sb.message_box(
                    "<hl>No materials</hl><br>The selected objects have no materials."
                )
                return
        elif mode.startswith("Browse"):
            paths = self._browse_textures()
            if not paths:
                return
            materials = btk.materials_for_textures(paths)
            if not materials:
                self.sb.message_box(
                    "<hl>No materials</hl><br>No scene materials reference the selected textures."
                )
                return
        else:  # All Scene Materials
            materials = None

        force_packed = menu.chk_force_packed.isChecked()
        config = {
            "preset": preset,
            "max_size": menu.cmb_max_size.currentData(),
            "mask_map_scale": menu.cmb_mask_scale.currentData(),
            "output_extension": menu.cmb_convert_format.currentData(),
            "move_to_folder": menu.txt_move_to.text().strip() or None,
            "force_packed_maps": force_packed,
            "use_input_fallbacks": menu.chk_input_fallbacks.isChecked(),
            "use_output_fallbacks": menu.chk_output_fallbacks.isChecked(),
            "dry_run": menu.chk_dry_run.isChecked(),
        }

        self.ui.txt001.clear()
        self.logger.info(f"Updating materials (preset: {preset})...")
        try:
            results = self.update_materials(materials=materials, config=config, verbose=True)
        except Exception as error:
            import traceback

            self.logger.error(f"Material update failed: {error}")
            self.ui.txt001.append(f"<font color='red'>{traceback.format_exc()}</font>")
            return

        updated = sum(r["updated"] for r in results.values())
        self.logger.info(
            f"Done — {updated} texture(s) repathed across {len(results)} material(s)."
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("mat_updater", reload=True)
    ui.show(pos="screen", app_exec=True)
