# !/usr/bin/python
# coding=utf-8
"""Game Shader — auto-build a Principled-BSDF material from a set of PBR textures.

Blender counterpart of mayatk's Game Shader. ``GameShader.create_network`` mirrors
``mtk.GameShader.create_network`` name-for-name: it resolves a **workflow profile** (Unity URP /
Unity HDRP / Unreal / glTF / Godot / …), runs the texture set through the SHARED
``ptk.MapFactory.prepare_maps`` map pipeline (format conversion, resize/optimize, packed-map
generation), resolves one source per shader input through the SHARED
``ptk.MapFactory.filter_redundant_maps``, and wires the survivors into a Principled BSDF — Base
Color, Metallic, Roughness (or glossiness→invert), Normal (with a DirectX green-flip), AO
multiplied into Base Color, Emission, Alpha, Bump/Height, Displacement, and the **combined
game-engine maps** (Albedo+Transparency, Unity Metallic-Smoothness, Unity HDRP MSAO mask, packed
ORM). A batch of several texture sets builds **one material per set**; an explicit Material Name
collapses to a single material.

The node-graph build itself is ``blendertk.MatUtils.create_pbr_material`` (in ``_mat_utils.py``) —
this module owns the profile/pipeline/reporting layer around it, exactly as mayatk splits
``GameShader`` from its ``connect_*`` wiring. Distinct from Shader Templates (parameter presets):
this builds the *texture* network. Served by ``BlenderUiHandler``
(``marking_menu.show("game_shader")``); Qt-only imports are deferred.

**Map pipeline needs an image library.** Blender bundles numpy but not Pillow, so
``btk.ensure_image_deps()`` provisions it on demand (the same call the Material Updater makes).
Without it the profile/format steps are skipped with a warning and the existing textures are wired
as-is — degraded, never a hard failure.

**One documented divergence from the Maya panel.** Maya's ``cmb004`` (shader type: Stingray PBS /
Standard Surface / OpenPBR) is absent here, not disabled — it picks a Maya shader NODE, and Blender
has exactly one surface node which (probed on 5.1.2) is already the OpenPBR model: Principled's
inputs map onto OpenPBR's nodedef, and Blender's own USD/MaterialX export emits
``ND_open_pbr_surface_surfaceshader`` for it. What the Maya combo really selects is the downstream
*target*, which on this panel is the Output Template combo (``cmb002``: Unity URP / HDRP / Unreal /
glTF / Godot). A Blender "shader type" combo would therefore be a second control for the same
decision, with two of its three items naming Maya shaders that are never created — so it is omitted
rather than faked.
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional, Union

import pythontk as ptk

import blendertk as btk
from blendertk.core_utils._core_utils import CoreUtils
from blendertk.mat_utils._mat_utils import MatUtils


class _GameShaderInternal(object):
    """Internal helpers for GameShader."""

    @staticmethod
    def _file(path: str) -> str:
        """Display name for a texture path (the table's Source column)."""
        return ptk.format_path(path, "file") if path else "—"

    # Map type → the types that, when present, take over its Principled input (so it is loaded
    # but never connected). Mirrors the precedence the wiring in ``create_pbr_material`` applies.
    _SHADOWED_BY = {
        # Registry-sourced: any normal type takes over Bump/Height's input, so a
        # type added there must shadow them here too or the report would call an
        # unconnected map "connected".
        "Bump": ptk.MapRegistry.NORMAL_TYPES,
        "Height": ptk.MapRegistry.NORMAL_TYPES,
        "Glossiness": ("Roughness",),
        "Smoothness": ("Roughness",),
    }

    @classmethod
    def _shadowed_by(
        cls, map_type: str, by_type: Dict[str, str], config: Dict[str, Any] = None
    ) -> str:
        """Why `map_type` was classified and kept but not connected.

        An `Opacity` map is the one type that can go unconnected without
        another map having taken its input: `Opacity: None` rules opacity out
        outright, and the generic note would blame a supersession that never
        happened.
        """
        if map_type == "Opacity" and (config or {}).get("opacity_mode") == "none":
            return "opacity ruled out (Opacity: None)"
        owner = next(
            (t for t in cls._SHADOWED_BY.get(map_type, ()) if t in by_type), None
        )
        return (
            f"{owner} already drives that input"
            if owner
            else "input already driven by another map"
        )

    @staticmethod
    def _extraction_note(dropped: Dict[str, tuple]) -> str:
        """Provenance note for channels recovered from a superseded packed map."""
        sources = [
            map_type
            for map_type, (_path, reason) in dropped.items()
            if reason == "superseded by separate maps"
        ]
        return f"extracted from {', '.join(sources)}" if sources else "extracted"

    def _ensure_map_pipeline(self) -> bool:
        """True when an image library is available for the map-preparation pipeline.

        The wiring step needs no image library (Blender loads the images itself), so a missing
        Pillow degrades to "wire the textures as they are" rather than failing the whole run.
        """
        if "PIL" in CoreUtils.ensure_image_deps():
            return True
        self.logger.warning(
            "Image library (Pillow) unavailable and could not be installed into Blender's "
            "Python — skipping map preparation (profile / format / packing). Existing textures "
            "will be wired as-is."
        )
        return False


class GameShader(ptk.LoggingMixin, _GameShaderInternal):
    """Build Principled-BSDF texture networks from PBR map sets (Blender mirror of mayatk's ``GameShader``)."""

    # Texture types whose connection produces an internal conversion node
    # (e.g. invert smoothness → roughness, split a packed channel map).
    CONVERSION_NOTES = {
        "Metallic_Smoothness": "smoothness → roughness (inverted)",
        "ORM": "split R/G/B → AO / Roughness / Metallic",
        "MRAO": "split R/G/B → Metallic / Roughness / AO",
        "MSAO": "smoothness → roughness; R/G channels split",
        "Albedo_Transparency": "alpha → opacity",
        "Glossiness": "glossiness → roughness (inverted)",
        "Smoothness": "smoothness → roughness (inverted)",
        "Ambient_Occlusion": "multiplied into Base Color",
        "Bump": "→ Bump node",
        "Height": "→ Bump node",
        "Displacement": "→ output Displacement",
    }

    def create_network(
        self,
        textures: List[str],
        name: str = "",
        prefix: str = "",
        suffix: str = "",
        config: Union[str, Dict[str, Any]] = None,
        progress_callback: Callable = None,
        **kwargs,
    ) -> Union[Optional[object], List[Optional[object]]]:
        """Create a PBR shader network with textures.

        Parameters:
            textures: List of texture file paths.
            name: Material name (auto-generated from the texture set when empty).
            prefix: Optional prefix prepended to the resolved material name.
            suffix: Optional suffix appended to the resolved material name.
            config: Configuration preset name (str) or dictionary.
            progress_callback: Optional callback(percent, message) for progress updates.
            **kwargs: Configuration overrides (e.g. normal_type, output_extension).

        Returns:
            The created material, or a list of materials in batch mode.
        """
        if not textures:
            self.logger.error("No textures given to create_network.")
            return None

        # Resolve Config
        cfg = ptk.MapRegistry().resolve_config(config, **kwargs)

        # Set defaults for missing keys
        defaults = {
            "normal_type": "OpenGL",
            "albedo_transparency": False,
            "metallic_smoothness": False,
            "mask_map": False,
            "orm_map": False,
            "opacity": False,
            "emissive": False,
            "ambient_occlusion": False,
            "convert_specgloss_to_pbr": False,
            "cleanup_base_color": False,
            "output_extension": "png",
        }
        for k, v in defaults.items():
            if k not in cfg:
                cfg[k] = v

        # Compact configuration banner: one boxed header + a 2-column table.
        # Gated: ``log_box`` / ``log_table`` write through ``log_raw``, which
        # bypasses level filtering BY DESIGN, so a caller that quieted this
        # logger (a batch driver, another tool running this as a step) would
        # otherwise still get the banner and the whole settings table.
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.log_box("Game Shader Network")
            self.log_table(
                [
                    ["Normal Type", cfg["normal_type"]],
                    ["Opacity", str(cfg["opacity"])],
                    ["Emissive", str(cfg["emissive"])],
                    ["Ambient Occlusion", str(cfg["ambient_occlusion"])],
                    ["Albedo Transparency", str(cfg["albedo_transparency"])],
                    ["Metallic Smoothness", str(cfg["metallic_smoothness"])],
                    ["Mask Map", str(cfg["mask_map"])],
                    ["ORM Map", str(cfg["orm_map"])],
                ],
                headers=["Option", "Value"],
            )

        # Check for large input size
        try:
            total_mb = sum(
                os.path.getsize(t) for t in textures if os.path.exists(t)
            ) / (1024 * 1024)
            if total_mb > 300:  # Warn if over 300MB
                warn_msg = (
                    f"Large input detected ({total_mb:.1f} MB). "
                    "Processing may take some time..."
                )
                self.logger.warning(warn_msg)
                if progress_callback:
                    progress_callback(0, warn_msg)
        except OSError as error:
            self.logger.debug(f"Could not calculate input size: {error}")

        def factory_progress(curr, total, msg):
            """Bridge callback to map Factory progress (0-50%) to UI."""
            if progress_callback and total:
                progress_callback(int((curr / total) * 50), f"Preparing Maps: {msg}")

        # Map preparation (format conversion / optimize / packing) needs an image library; without
        # one, fall through with the source textures so the wiring step still runs.
        group_by_set = not bool(name)
        if self._ensure_map_pipeline():
            prepared_data = ptk.MapFactory.prepare_maps(
                textures,
                logger=self.logger,
                group_by_set=group_by_set,
                max_workers=4,
                progress_callback=factory_progress,
                prefix=prefix,
                suffix=suffix,
                **cfg,
            )
        elif group_by_set:
            prepared_data = ptk.MapFactory.group_textures_by_set(
                textures, prefix=prefix, suffix=suffix
            )
        else:
            prepared_data = list(textures)

        if isinstance(prepared_data, dict):  # Batch mode
            total = len(prepared_data)
            self.logger.info(f"Batch processing {total} texture sets...")
            results, created = [], []

            for i, (set_name, set_textures) in enumerate(prepared_data.items(), 1):
                if progress_callback:  # Map 50-100 range
                    progress_callback(
                        50 + int((i / total) * 50), f"Building Network: {set_name}"
                    )
                # Isolate each set: one bad set must not abort the remaining ones.
                try:
                    mat = self._create_single_network(
                        set_textures,
                        set_name,  # Use set name for material name
                        prefix=prefix,
                        suffix=suffix,
                        config=cfg,
                    )
                except Exception as error:
                    self.logger.error(f"Set '{set_name}' failed: {error}")
                    mat = None
                results.append(mat)
                created.append(
                    [
                        set_name,
                        getattr(mat, "name", "—"),
                        "Success" if mat else "Failed",
                    ]
                )

            # Gated for the same reason as the config banner.
            if self.logger.isEnabledFor(logging.INFO):
                succeeded = sum(1 for r in results if r)
                self.logger.log_box(
                    "Batch Creation Summary",
                    [f"{succeeded}/{total} set(s) built"],
                    level="SUCCESS" if succeeded == total else "WARNING",
                )
                self.log_table(created, headers=["Set Name", "Material", "Status"])

            if progress_callback:
                progress_callback(100, "Completed")
            return results

        if progress_callback:
            progress_callback(75, "Building Network...")

        mat = self._create_single_network(
            prepared_data, name, prefix=prefix, suffix=suffix, config=cfg
        )

        if progress_callback:
            progress_callback(100, "Completed")
        return mat

    def _create_single_network(
        self,
        textures: List[str],
        name: str,
        prefix: str = "",
        suffix: str = "",
        config: Dict[str, Any] = None,
    ) -> Optional[object]:
        """Build one material from prepared textures, reporting per-map outcomes."""
        if not textures:
            self.logger.error("No valid textures after preparation.")
            return None

        if not name:
            name = ptk.MapFactory.get_base_texture_name(
                textures[0], prefix=prefix, suffix=suffix
            )
        # Idempotent affix application: strips any pre-existing occurrence of the configured
        # prefix/suffix from `name` before re-applying, so a filename like "Mat_brick_Albedo.png"
        # with prefix="Mat_" yields "Mat_brick", not "Mat_Mat_brick".
        name = ptk.StrUtils.apply_affix(name, prefix=prefix, suffix=suffix)

        # One source per slot, via the SHARED registry rules — the same call mayatk's
        # _resolve_map_conflicts makes, so the two DCCs can't drift on packed-vs-loose.
        # Resolved HERE (not inside create_pbr_material) so the rows below can report it, and
        # handed down as `plan=` so channel extraction isn't redone.
        plan = MatUtils.resolve_pbr_plan(textures, config=config)
        by_type = plan["by_type"]
        if not by_type:
            self.logger.error(f"No recognized map types for '{name}'.")
            return None

        normal_direction = (config or {}).get("normal_type") or "OpenGL"
        mat = MatUtils.create_pbr_material(
            textures,
            name=name,
            normal_direction=normal_direction,
            config=config,
            plan=plan,
        )
        if mat is None:
            self.logger.error(f"Failed to build material '{name}'.")
            return None

        # Per-map outcome rows: [status, type, file, note]
        rows: List[List[str]] = []
        extracted_note = self._extraction_note(plan["dropped"])

        # Report what a packed map (or an earlier duplicate) took over, so a missing map in the
        # shading network is never a silent drop.
        for map_type, (path, reason) in plan["dropped"].items():
            rows.append(["–", map_type, self._file(path), reason])

        # What create_pbr_material actually connected. A map whose input another map already
        # drives (Height beside a Normal, Glossiness beside a real Roughness) is skipped by the
        # wiring — reporting it as connected would be exactly the silent drop this table exists
        # to prevent.
        wired = plan.get("wired", set())

        connected = converted = failed = shadowed = 0
        for map_type, path in by_type.items():
            if map_type in plan["unhandled"]:
                rows.append(
                    [
                        "✗",
                        map_type,
                        self._file(path),
                        "no matching Principled input",
                    ]
                )
                failed += 1
                continue
            if map_type not in wired:
                rows.append(
                    [
                        "–",
                        map_type,
                        self._file(path),
                        self._shadowed_by(map_type, by_type, config),
                    ]
                )
                shadowed += 1
                continue
            note = (
                extracted_note
                if map_type in plan["extracted"]
                else self.CONVERSION_NOTES.get(map_type, "")
            )
            # Mirror create_pbr_material's own green-flip test exactly: an explicitly
            # DirectX-tagged map always flips, an ambiguous "Normal" follows the combo, and an
            # explicitly OpenGL-tagged map never flips.
            if map_type == "Normal_DirectX" or (
                map_type == "Normal" and normal_direction.lower() == "directx"
            ):
                note = "green channel flipped (DirectX → OpenGL)"
            connected += 1
            if note:
                converted += 1
            rows.append(["✓", map_type, self._file(path), note])

        for path in plan["unknown"]:
            rows.append(["✗", "Unknown", self._file(path), "unrecognized map type"])
            failed += 1

        # Gated — log_table bypasses level filtering. The material name is
        # the table's TITLE rather than a preceding ``info`` record: in batch
        # mode this runs once per set, and every record is its own paragraph,
        # so the name-then-table pair cost an extra blank-line-separated
        # section per material.
        if self.logger.isEnabledFor(logging.INFO):
            self.log_table(
                rows,
                headers=["", "Map", "Source", "Conversion"],
                title=f"Material: {name}",
            )

        # Clickable link — opens the material in the Shader Editor (the Blender analogue of
        # mayatk's "select the shader node in the Hypershade").
        link = self.logger.log_link(mat.name, "graph", node=mat.name)
        tail = f"{converted} converted"
        # Every "–" row: superseded by a packed map, or kept but shadowed on its input. Folded
        # into one tally so the summary accounts for every row in the table.
        skipped = len(plan["dropped"]) + shadowed
        if skipped:
            tail = f"{skipped} superseded, {tail}"
        if failed == 0:
            self.logger.success(f"{link} — {connected} connected, {tail}")
        else:
            self.logger.warning(
                f"{link} — {connected} connected, {failed} failed, {tail}"
            )
        return mat


class GameShaderSlots(GameShader):
    """Switchboard slot wiring for the Game Shader panel."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()

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
        # setup_logging_redirect is what enables clickable <a href="action://…"> links.
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt001)

        # Dispatch action:// links (e.g. graph the created material).
        if hasattr(self.ui.txt001, "anchorClicked"):
            self.ui.txt001.anchorClicked.connect(self._on_log_link_clicked)

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from blendertk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

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
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_graph_material",
            setText="Open in Editor",
            setToolTip="Open the created material in the Shader Editor.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Game Shader",
                body="Auto-build a Principled-BSDF material from a set of PBR texture files — "
                "each map is classified by filename and wired into the right input with the "
                "needed conversion nodes (Normal Map, glossiness→roughness, AO multiply, packed "
                "ORM split).",
                steps=[
                    "Set <b>Material Name</b> and an optional <b>Prefix / Suffix</b> "
                    "(affix-mode option box selects placement).",
                    "Pick an <b>Output Template</b> — the preset's tooltip describes its target "
                    "workflow (Unity URP/HDRP, Unreal, glTF, Godot …).",
                    "Pick the <b>Normal Map</b> direction (OpenGL / DirectX) and the output "
                    "<b>Ext</b> ('Profile default' defers per-map format to the template).",
                    "Press <b>Create Network</b> and select texture files; results stream into "
                    "the log panel.",
                ],
                notes=[
                    "Filenames must carry map types (BaseColor / Normal / Roughness / Metallic / "
                    "AO / Emissive / Opacity / ORM / MSAO / Metallic_Smoothness …).",
                    "Several texture sets in one selection build <b>one material per set</b>; "
                    "set a <b>Material Name</b> to merge them into one.",
                    "The template can <i>write new maps</i> (packing / format conversion) — that "
                    "step needs Pillow, installed into Blender's Python on first use.",
                    "Distinct from Shader Templates — that sets <i>parameters</i>; this wires "
                    "<i>textures</i>.",
                    "Click the material name in the log (or <b>Open in Editor</b>) to inspect "
                    "the resulting node graph.",
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

    @property
    def output_extension(self) -> str:
        """Selected output extension, or '' when 'Profile default' is chosen.

        An empty string signals the caller to defer per-map format to the selected
        workflow profile's template rather than forcing one container for all maps.

        Returns:
            (str) The file extension in lowercase (e.g., 'png', 'jpg'), or ''.
        """
        text = self.ui.cmb003.currentText().lower()
        return "" if text.startswith("profile") else text

    def cmb002_init(self, widget):
        """Initialize Presets"""
        if not widget.is_initialized:
            # Names + tooltips come from the OutputTemplates SSoT, shared with the
            # converter / compositor / mat_updater / scene exporter, so none of
            # them depends on the preset dict's internal shape.
            from qtpy import QtCore

            widget.clear()
            for name, description in ptk.OutputTemplates.profile_choices():
                widget.addItem(name)
                if description:
                    widget.setItemData(
                        widget.count() - 1, description, QtCore.Qt.ToolTipRole
                    )

    @property
    def opacity_mode(self) -> Optional[str]:
        """The opacity graph the panel asks for.

        Returns:
            str | None: ``"transparent"`` (alpha blend), ``"masked"`` (alpha
            cutout), or ``"none"`` -- opacity ruled out, which retires the
            set's opacity sources instead of letting them pick the graph.
            None for Auto: a usable opacity source then builds as transparent,
            as it always has.
        """
        if hasattr(self.ui, "cmb005"):
            text = self.ui.cmb005.currentText().lower()
            if "masked" in text:
                return "masked"
            if "transparent" in text:
                return "transparent"
            if "none" in text:
                return "none"
        return None

    def cmb003_init(self, widget):
        """Initialize Output Format.

        Selecting 'Profile default' defers each map's container/bit-depth to the
        selected workflow profile's output template; a concrete format forces that
        container for all maps.
        """
        if not widget.is_initialized:
            # format_choices appends the sentinel LAST, preserving the existing
            # format indices — combobox state is persisted by index, so moving it
            # to the front would silently shift every saved selection by one.
            widget.add(
                ptk.OutputTemplates.format_choices(
                    sentinel=ptk.OutputTemplates.PROFILE_DEFAULT_LABEL
                )
            )

    def txt000_init(self, widget):
        """Material-name field — clearable back to the auto-derived name."""
        widget.option_box.clear_option = True

    def txt002_init(self, widget):
        """Add a prefix/suffix/auto-mode picker to the affix field."""
        widget.option_box.set_affix(
            default="prefix",
            on_change=lambda _mode, w=widget: self._apply_affix_placeholder(w),
            settings_key="game_shader_affix",  # ``txt002`` alone is too generic
            convention_key="material",  # fourth state: the shared convention
        )
        self._apply_affix_placeholder(widget)

    @staticmethod
    def _apply_affix_placeholder(widget):
        mode = widget.option_box.affix_mode
        if mode == "prefix":
            widget.setPlaceholderText("Prefix")
            widget.setToolTip(
                "Prefix prepended to the material name.\n"
                'Example: "MAT_" + "brick" → "MAT_brick".'
            )
        elif mode == "suffix":
            widget.setPlaceholderText("Suffix")
            widget.setToolTip(
                "Suffix appended to the material name.\n"
                'Example: "brick" + "_MAT" → "brick_MAT".'
            )
        elif mode == "convention":
            # The field is showing (and locked to) the shared convention, so
            # the placeholder would never be seen — name the source instead, so
            # a user wondering why they cannot type has the answer in the tip.
            widget.setPlaceholderText("Scene Convention")
            widget.setToolTip(
                "Following the shared naming convention for materials.\n"
                "Edit it in the Naming panel (Suffix By Type); every tool set "
                "to this mode follows.\n"
                "Click the button beside the field to type your own instead."
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

        template_name = self.ui.cmb002.currentText()

        # 'Profile default' (empty ext) → let the workflow profile drive per-map
        # format; a concrete ext overrides it for all maps.
        ext = self.output_extension
        output_profile = template_name if not ext else None

        def progress_adapter(p, m):
            # Surface progress in the footer and keep the UI responsive during the
            # long network build.
            self.ui.footer.setText(f"{m} ({int(p)}%)" if m else f"{int(p)}%")
            self.sb.QtWidgets.QApplication.instance().processEvents()

        results = self.create_network(
            self.image_files,
            self.mat_name,
            prefix=self.mat_prefix,
            suffix=self.mat_suffix,
            config=template_name,
            normal_type=self.normal_map_type,
            opacity_mode=self.opacity_mode,
            cleanup_base_color=False,  # Can be exposed in UI later if needed
            output_extension=ext or None,
            output_profile=output_profile,
            progress_callback=progress_adapter,
        )

        made = [m for m in ptk.make_iterable(results) if m is not None]
        self.last_created_materials = made

        if not made:
            self.sb.message_box(
                "<hl>No PBR textures recognized</hl><br>Filenames must carry map types "
                "(BaseColor, Normal, Roughness, Metallic…)."
            )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("game_shader", reload=True)
    ui.show(pos="screen", app_exec=True)
