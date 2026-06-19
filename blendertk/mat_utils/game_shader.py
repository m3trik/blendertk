# !/usr/bin/python
# coding=utf-8
"""Game Shader tool panel — auto-build a Principled-BSDF material from a set of PBR textures.

Blender counterpart of mayatk's Game Shader (``GameShader.create_network``): pick the texture files
(or a folder) and the engine classifies each map (shared ``ptk.MapFactory``) and wires it into a
Principled BSDF — Base Color, Metallic, Roughness (or glossiness→invert), Normal (with a DirectX
green-flip), AO multiplied into Base Color, Emission, Alpha, Bump/Height, Displacement, and the
**combined game-engine maps** (Albedo+Transparency, Unity Metallic-Smoothness, Unity HDRP MSAO mask,
packed ORM). A folder holding several texture sets builds **one material per set** (batch orchestrator
``create_pbr_materials``); an explicit Material Name collapses to a single material. The engine is
``blendertk.MatUtils.create_pbr_material`` / ``create_pbr_materials`` (in ``_mat_utils.py``); this is
the thin driver. Distinct from Shader Templates (parameter presets) — this builds the *texture*
network. Needs no image library. Served by ``BlenderUiHandler`` (``marking_menu.show("game_shader")``);
Qt-only imports are deferred.

Layout mirrors mayatk's Game Shader (Material Name + Normal Map groups under ``main_grp``, the
collapsable output log, footer). Maya-only controls are dropped per the faithful-but-sensible rule:
the **shader-type** combo (Stingray PBS / Standard Surface — Blender has the one Principled BSDF), the
**AiBridge** Arnold toggle, and the **Output Template** / **Ext** map-export knobs (this flow wires
existing textures into a node graph; it does not bake or write output maps).
"""
import os

import pythontk as ptk

import blendertk as btk


class GameShaderSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Game Shader panel."""

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.game_shader
        self.set_log_level(log_level)
        try:
            self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            self.logger.setup_logging_redirect(self.ui.txt001)
        except Exception:
            pass
        self.ui.txt001.setText(
            "Pick PBR textures (or a folder) to auto-build a Principled material."
        )

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Game Shader",
                body="Auto-build a Principled-BSDF material from a set of PBR texture files — each "
                "map is classified by filename and wired into the right input with the needed "
                "conversion nodes (Normal Map, glossiness→roughness, AO multiply, packed ORM split).",
                steps=[
                    "Pick the <b>Normal direction</b> (OpenGL / DirectX).",
                    "<b>From Files…</b> to pick textures, or <b>From Folder…</b> for a whole folder.",
                    "Enable <b>Assign to Selection</b> to apply the result to the selected objects.",
                ],
                sections=[
                    ("Notes", [
                        "Filenames must carry map types (BaseColor / Normal / Roughness / "
                        "Metallic / AO / Emissive / Opacity / ORM / MSAO / Metallic_Smoothness …).",
                        "A folder with several texture sets builds <b>one material per set</b>; "
                        "set a <b>Material Name</b> to merge them into one.",
                        "Distinct from Shader Templates — that sets <i>parameters</i>; this wires "
                        "<i>textures</i>.",
                    ]),
                ],
            )
        )

    def cmb001_init(self, widget):
        """Normal-map direction (mayatk's normal_output_grp combo)."""
        if not getattr(widget, "is_initialized", False):
            widget.add(["OpenGL", "DirectX"], clear=True)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _pick_files():
        from qtpy import QtWidgets

        exts = " ".join(f"*.{e}" for e in ptk.ImgUtils.texture_file_types)
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            None, "Select PBR texture files", "", f"Textures ({exts});;All (*)"
        )
        return list(paths) or None

    @staticmethod
    def _pick_dir():
        from qtpy import QtWidgets

        return QtWidgets.QFileDialog.getExistingDirectory(None, "Select a texture folder") or None

    def _name_and_prefix(self):
        """The explicit material **name** (txt000) and the **prefix** affix (txt002), separately.

        Only an explicit name disables set-batching (mirrors mayatk's ``group_by_set = not
        bool(name)``); a prefix alone still batches and is prepended to each set's material name.
        """
        return (self.ui.txt000.text().strip() or None, self.ui.txt002.text().strip())

    def _build(self, files):
        """Classify + build, report, and optionally assign to the selection.

        Builds one material per detected texture set (folders may hold several); an explicit
        Material Name collapses them to a single material.
        """
        if not files:
            return
        self.ui.txt001.clear()
        normal_direction = self.ui.cmb001.currentText() or "OpenGL"
        name, prefix = self._name_and_prefix()
        results = btk.create_pbr_materials(
            files, name=name, normal_direction=normal_direction, prefix=prefix
        )
        made = [m for m in results.values() if m is not None]
        if not made:
            self.sb.message_box(
                "<hl>No PBR textures recognized</hl><br>Filenames must carry map types "
                "(BaseColor, Normal, Roughness, Metallic…)."
            )
            return
        for mat in made:
            self.logger.info(f"Created material <hl>{mat.name}</hl>.")
        if len(made) > 1:
            self.logger.info(f"Built {len(made)} materials from {len(made)} texture sets.")

        if self.ui.chk000.isChecked():
            selection = btk.selected_objects()
            if not selection:
                self.logger.warning("Assign to Selection is on, but nothing is selected.")
            elif len(made) > 1:
                # Ambiguous which of N materials to assign — skip (the Maya tool never assigns).
                self.logger.warning(
                    "Assign to Selection skipped — multiple materials were built (ambiguous target)."
                )
            else:
                btk.assign_mat(selection, made[0])
                self.logger.info(f"Assigned to {len(selection)} object(s).")

    # ------------------------------------------------------------------ slots
    def b000(self):
        """Create from Files…"""
        self._build(self._pick_files())

    def b001(self):
        """Create from Folder…"""
        folder = self._pick_dir()
        if not folder:
            return
        exts = set(ptk.ImgUtils.texture_file_types)
        files = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if os.path.splitext(f)[1].lstrip(".").lower() in exts
        ]
        self._build(files)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("game_shader", reload=True)
    ui.show(pos="screen", app_exec=True)
