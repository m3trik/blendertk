# !/usr/bin/python
# coding=utf-8
"""Game Shader tool panel — auto-build a Principled-BSDF material from a set of PBR textures.

Blender counterpart of mayatk's Game Shader (``GameShader.create_network``): pick a set of texture
files and the engine classifies each map (shared ``ptk.MapFactory``) and wires it into a Principled
BSDF — Base Color, Metallic, Roughness (or glossiness→invert), Normal (with a DirectX green-flip),
AO multiplied into Base Color, Emission, Alpha, Bump/Height, Displacement, and the **combined
game-engine maps** (Albedo+Transparency, Unity Metallic-Smoothness, Unity HDRP MSAO mask, packed
ORM). A batch of several texture sets builds **one material per set** (``create_pbr_materials``);
an explicit Material Name collapses to a single material. The engine is
``blendertk.MatUtils.create_pbr_material`` / ``create_pbr_materials`` (in ``_mat_utils.py``); this
is the thin driver. Distinct from Shader Templates (parameter presets) — this builds the *texture*
network. Needs no image library. Served by ``BlenderUiHandler``
(``marking_menu.show("game_shader")``); Qt-only imports are deferred.

Layout is a structural 1:1 mirror of mayatk's ``game_shader.ui`` (same objectNames, same grid).
Widgets with no Blender analogue are present but disabled (tooltip explains why) rather than
removed, so the two panels stay diffable side-by-side:
  - ``extension_grp`` (cmb003, output file format) and ``output_template_grp`` (cmb002, workflow
    presets) both belong to mayatk's ``ptk.MapFactory.prepare_maps`` output-writing pipeline —
    this flow only *wires* existing textures into the node graph, it never writes new map files.
  - ``chk000`` (AiBridge) is Maya/Arnold-specific render-bridge setup; Blender materials render
    natively in Cycles/EEVEE.
  - ``cmb004`` (shader type: Stingray PBS / Standard Surface / Open PBR) has no Blender analogue —
    there is a single Principled BSDF shader.
"""
import pythontk as ptk

import blendertk as btk


class GameShaderSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Game Shader panel."""

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.game_shader
        self.set_log_level(log_level)

        # Don't keep this window glued above other tools — user can use the
        # pin button to toggle stay-on-top when needed.
        if hasattr(self.ui, "set_flags"):
            self.ui.set_flags(WindowStaysOnTopHint=False)

        self.image_files = None
        self.last_created_materials = []

        self.ui.txt001.setText(
            "Pick PBR texture files to auto-build a Principled material."
        )

        # Route the shared logger into the txt001 QTextBrowser with HTML colorization.
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt001)

    # The current workspace / its texture folder (Maya `sourceimages` analogue) — rule-fed for
    # marked workspace.mel projects, "textures" convention for plain Blender folders.
    # Resolved lazily: needs bpy (so panel load stays bpy-free) and tracks the current file.
    @property
    def workspace_dir(self) -> str:
        return btk.get_env_info("workspace")

    @property
    def source_images_dir(self) -> str:
        return btk.source_images_dir()

    def header_init(self, widget):
        """Initialize the header widget."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_graph_material",
            setText="Open in Editor",
            setToolTip="Open the created material in the Shader Editor.",
        )
        widget.set_help_text(
            fmt(
                title="Game Shader",
                body="Auto-build a Principled-BSDF material from a set of PBR texture files — "
                "each map is classified by filename and wired into the right input with the "
                "needed conversion nodes (Normal Map, glossiness→roughness, AO multiply, packed "
                "ORM split).",
                steps=[
                    "Set <b>Material Name</b> and an optional <b>Prefix / Suffix</b> "
                    "(affix-mode option box selects placement).",
                    "Pick the <b>Normal Map</b> direction (OpenGL / DirectX).",
                    "Press <b>Create Network</b> and select texture files; results stream into "
                    "the log panel.",
                ],
                notes=[
                    "Filenames must carry map types (BaseColor / Normal / Roughness / Metallic / "
                    "AO / Emissive / Opacity / ORM / MSAO / Metallic_Smoothness …).",
                    "Several texture sets in one selection build <b>one material per set</b>; "
                    "set a <b>Material Name</b> to merge them into one.",
                    "Distinct from Shader Templates — that sets <i>parameters</i>; this wires "
                    "<i>textures</i>.",
                    "Use <b>Open in Editor</b> from the header menu to inspect the resulting "
                    "material's node graph.",
                ],
            )
        )

    def lbl_graph_material(self):
        """Graph the most recently created material in the Shader Editor."""
        if self.last_created_materials:
            btk.graph_materials(self.last_created_materials)
        else:
            self.logger.warning("No material has been created yet.")

    @property
    def mat_name(self) -> str:
        """Get the material name from the user input text field.

        Returns:
            (str)
        """
        return self.ui.txt000.text().strip()

    @property
    def mat_prefix(self) -> str:
        """Return the affix text when it resolves as a prefix, else empty string."""
        if not hasattr(self.ui, "txt002"):
            return ""
        prefix, _ = self.ui.txt002.option_box.resolve_affix(default="prefix")
        return prefix

    @property
    def mat_suffix(self) -> str:
        """Return the affix text when it resolves as a suffix, else empty string."""
        if not hasattr(self.ui, "txt002"):
            return ""
        _, suffix = self.ui.txt002.option_box.resolve_affix(default="prefix")
        return suffix

    @property
    def normal_map_type(self) -> str:
        """Get the normal map direction from the comboBox's current text.

        Returns:
            (str)
        """
        return self.ui.cmb001.currentText() or "OpenGL"

    # TODO(blender-parity): cmb002 (Output Template / workflow presets) and cmb003 (Ext / output
    # file format) drive mayatk's ptk.MapFactory.prepare_maps output-writing pipeline (packing +
    # format conversion of new map files). This flow only wires EXISTING textures into the node
    # graph — porting the map-writing pipeline is a substantial addition (image library, packed-
    # map writers, workflow-profile plumbing) out of scope here. Both are disabled in the .ui;
    # mayatk's shader_type / output_extension properties have no Blender counterpart.

    # TODO(blender-parity): chk000 (AiBridge) sets up a Maya/Arnold aiStandardSurface render
    # bridge for StingrayPBS. No Blender analogue — Blender materials render natively in
    # Cycles/EEVEE. Disabled in the .ui.

    # TODO(blender-parity): cmb004 (shader type: Stingray PBS / Standard Surface / Open PBR) has
    # no Blender analogue — there is a single Principled BSDF shader. Disabled in the .ui.

    def txt002_init(self, widget):
        """Add a prefix/suffix/auto-mode picker to the affix field."""
        widget.option_box.set_affix(
            default="prefix",
            on_change=lambda _mode, w=widget: self._apply_affix_placeholder(w),
        )
        self._apply_affix_placeholder(widget)

    @staticmethod
    def _apply_affix_placeholder(widget):
        mode = widget.option_box.affix_mode
        if mode == "prefix":
            widget.setPlaceholderText("Prefix")
            widget.setToolTip(
                'Prefix prepended to the material name.\n'
                'Example: "MAT_" + "brick" → "MAT_brick".'
            )
        elif mode == "suffix":
            widget.setPlaceholderText("Suffix")
            widget.setToolTip(
                'Suffix appended to the material name.\n'
                'Example: "brick" + "_MAT" → "brick_MAT".'
            )
        else:  # auto
            widget.setPlaceholderText("Affix")
            widget.setToolTip(
                "Affix — placement inferred from '_' position.\n"
                "  '_MAT' → suffix (appended)\n"
                "  'MAT_' → prefix (prepended)"
            )

    def b000(self):
        """Create Network — pick PBR texture files and build Principled material(s) from them."""
        image_files = self.sb.file_dialog(
            file_types=[f"*.{ext}" for ext in ptk.ImgUtils.texture_file_types],
            title="Select one or more image files to open.",
            start_dir=self.source_images_dir,
        )
        if not image_files:
            return

        self.image_files = image_files
        self.ui.txt001.clear()

        results = btk.create_pbr_materials(
            image_files,
            name=self.mat_name or None,
            normal_direction=self.normal_map_type,
            prefix=self.mat_prefix,
            suffix=self.mat_suffix,
        )
        made = [m for m in results.values() if m is not None]
        self.last_created_materials = made

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


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("game_shader", reload=True)
    ui.show(pos="screen", app_exec=True)
