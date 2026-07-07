# !/usr/bin/python
# coding=utf-8
"""Slots for the RizomUV bridge panel.

Mirror of mayatk's ``uv_utils.rizom_bridge.rizom_bridge_slots``. Subclass of uitk's
:class:`BridgeSlotsBase` directly (not through a Blender-flavored intermediate base) --
``REQUIRE_OUTPUT_DIR = False`` means it needs no DCC-side ``default_output_dir`` fallback, exactly
like ``blendertk.env_utils.maya_bridge.maya_bridge_slots.MayaBridgeSlots``. The panel machinery
(template combo, dynamic parameter widgets, user presets, log routing, per-template description)
lives upstream; this file owns only the Rizom-specific bits.

**Round-trip presets are not ported.** mayatk's ``pack`` / ``unwrap_hard`` / ``unwrap_organic`` /
``optimize`` presets export-duplicate -> run RizomUV headlessly -> re-import -> transfer UVs back
onto the originals -- a genuinely substantial pipeline (its own export/import/transfer machinery,
versioned Lua wrapper templates) that :mod:`_rizom_bridge` doesn't implement yet. They're still
listed in ``cmb000`` for structural (item-count) parity with mayatk, but disabled -- see
:meth:`cmb000_init`. Only ``send`` (one-way: export + open interactively in RizomUV, no UV
transfer back) is wired to a working engine call.
TODO(blender-parity): port the round-trip pipeline (reference: ``mayatk.uv_utils.rizom_bridge
._rizom_bridge.RizomUVBridge.process_with_rizomuv``), then drop the disabling in this file and
restore the pack/unfold ``AttributeSpec`` entries in ``parameters.py``.

Discovered by ``BlenderUiHandler`` (``marking_menu.show("rizom_bridge")``).
"""
from pathlib import Path

from uitk.bridge import BridgeSlotsBase

import blendertk as btk
from blendertk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge, _SCRIPT_DIR
from blendertk.uv_utils.rizom_bridge import parameters as _params


_PRESETS_ROOT = Path("blendertk/rizom_bridge")


class RizomBridgeSlots(BridgeSlotsBase):
    """Slots wired to ``rizom_bridge.ui`` via :class:`BridgeSlotsBase`.

    Discovered automatically by :class:`blendertk.ui_utils.blender_ui_handler.BlenderUiHandler`
    so ``self.sb.handlers.marking_menu.show("rizom_bridge")`` works from anywhere with no
    explicit registration.
    """

    UI_NAME = "rizom_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "rizom_bridge"

    # Placeholder-discovery scripts live under ``scripts/*.lua`` (currently just ``send.lua`` --
    # see the module docstring for why the round-trip scripts aren't bundled).
    TEMPLATE_EXTENSION = ".lua"

    # RizomUV round-trips through a temp FBX it manages internally -- there's no user-visible
    # artifact to point at, so skip the Output Dir row (mirrors mayatk).
    REQUIRE_OUTPUT_DIR = False

    # Narrower label column than the maya_bridge default (90px) -- Rizom's param labels are short
    # ("Load UVs", "Import Groups") so the tighter column hugs the values more cleanly.
    LABEL_MIN_WIDTH = 80

    # Rizom's header menu adds a UV-Editor shortcut and renames "Templates" to "Scripts"; the base
    # builds it from this data (handlers resolve to open_uv_editor + the base's
    # open_templates_folder / refresh_templates).
    HEADER_MENU_ITEMS = (
        (
            "Open UV Editor", "btnopen_uv_editor",
            "Open Blender's UV Editor for inspecting the result.", "open_uv_editor",
        ),
        (
            "Open Scripts Folder", "btn_open_scripts",
            "Reveal the bundled Lua preset folder in Explorer.",
            "open_templates_folder",
        ),
        (
            "Refresh Scripts", "btn_refresh_scripts",
            "Re-scan the scripts folder and rebuild the script combo.",
            "refresh_templates",
        ),
        ("Clear Log", "btn_clear_log", "Clear the log panel below.", "clear_log"),
    )
    HELP_SPEC = {
        "title": "RizomUV Bridge",
        "body": "Send the selected meshes to RizomUV for interactive UV work. RizomUV stays "
        "open -- save manually when you're done; there's no automatic round-trip back into "
        "Blender.",
        "steps": [
            "Select one or more mesh objects.",
            "Pick <b>send</b> from the dropdown (currently the only working preset).",
            "Tweak the load options that appear.",
            "Click <b>Process Selected</b>.",
        ],
        "sections": [
            ("Presets", [
                "<b>send</b> -- exports the selection to FBX and launches RizomUV detached "
                "with a load script, optionally binding the selection's textures.",
                "<b>pack / unwrap_hard / unwrap_organic / optimize</b> -- mayatk's headless "
                "round-trip presets. <i>Not yet ported to Blender</i> (greyed out) -- they "
                "need an export-duplicate / re-import / UV-transfer pipeline this bridge "
                "doesn't implement.",
            ]),
            ("Header menu", [
                "<b>Open UV Editor</b> -- open a new window with Blender's UV Editor to "
                "inspect the result.",
                "<b>Open Scripts Folder</b> -- reveal the bundled Lua preset folder in "
                "Explorer.",
                "<b>Refresh Scripts</b> -- re-scan the scripts folder and rebuild the script "
                "combo.",
                "<b>Clear Log</b> -- clear the log panel below.",
            ]),
        ],
        "notes": [
            "RizomUV (Rizom Lab) must be installed -- auto-discovered under "
            "<code>Program Files\\Rizom Lab</code>. Windows only.",
        ],
    }

    # ------------------------------------------------------------------
    # Required base-class hooks
    # ------------------------------------------------------------------

    @property
    def params_module(self):
        return _params

    @property
    def template_dir(self) -> Path:
        return _SCRIPT_DIR

    def make_bridge(self) -> RizomUVBridge:
        return RizomUVBridge()

    # Name of the pseudo-preset that runs the (only implemented) one-way send flow.
    SEND_PRESET = "send"

    # mayatk's round-trip presets, listed here only for combo item-count parity -- disabled in
    # cmb000_init/refresh_templates since _rizom_bridge has no engine support for them.
    # TODO(blender-parity): drop this once the round-trip pipeline is ported.
    ROUND_TRIP_PRESETS = ("optimize", "pack", "unwrap_hard", "unwrap_organic")

    def list_template_modes(self):
        """Return ``[(stem, ""), ...]`` for every mayatk RizomUV preset.

        Unlike mayatk (which globs ``scripts/*.lua`` -- every stem found is a real, runnable
        script), this port hardcodes the same five stems so the combo shows the same items mayatk
        does; only ``send`` backs a bundled script / working engine call. Rizom has no per-template
        mode dimension, so ``mode=""`` is emitted for every entry (elides the parens in the combo
        label, matching mayatk).
        """
        return [(stem, "") for stem in sorted((*self.ROUND_TRIP_PRESETS, self.SEND_PRESET))]

    def select_initial_template_index(self, pairs):
        """Bias the initial selection toward ``send`` -- the only preset this port can run."""
        for i, (template, _mode) in enumerate(pairs):
            if template == self.SEND_PRESET:
                return i
        return 0

    # ------------------------------------------------------------------
    # cmb000 -- grey out the unported round-trip presets
    # ------------------------------------------------------------------

    def cmb000_init(self, widget) -> None:
        """Populate + wire the preset combo (base), then disable the round-trip presets."""
        super().cmb000_init(widget)
        self._disable_round_trip_presets(widget)

    def refresh_templates(self) -> None:
        """Re-scan + rebuild the combo (base), re-applying the round-trip disable.

        The base's ``refresh_templates`` repopulates the combo directly (it doesn't re-run
        ``cmb000_init``), so the disabling has to be re-applied here too.
        """
        super().refresh_templates()
        self._disable_round_trip_presets(self.ui.cmb000)

    def _disable_round_trip_presets(self, widget) -> None:
        """Grey out every combo entry in :attr:`ROUND_TRIP_PRESETS` (Qt.ItemIsEnabled off).

        # TODO(blender-parity): port mayatk's export-duplicate / re-import / UV-transfer
        # round-trip pipeline, then delete this method and the item-flags workaround it applies.
        """
        model = widget.model()
        for i in range(widget.count()):
            data = widget.itemData(i)
            template = data[0] if isinstance(data, (tuple, list)) and data else None
            if template in self.ROUND_TRIP_PRESETS:
                item = model.item(i) if model is not None else None
                if item is None:
                    continue
                item.setFlags(item.flags() & ~self.sb.QtCore.Qt.ItemIsEnabled)
                item.setToolTip(
                    "Not yet ported to Blender — needs an export-duplicate / re-import / "
                    "UV-transfer round-trip pipeline. Use 'send' to open the selection in "
                    "RizomUV directly."
                )

    # ------------------------------------------------------------------
    # b000 -- the per-bridge send action
    # ------------------------------------------------------------------

    def b000(self):
        """Run the chosen preset. Only ``send`` is implemented; the round-trip presets are
        disabled in the combo (see :meth:`cmb000_init`)."""
        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning(
                "No preset chosen. Pick a preset from the dropdown above."
            )
            return
        preset, _mode = pair

        selection = btk.selected_objects()
        if not selection:
            self.bridge.logger.warning(
                "Nothing selected. Select one or more mesh objects before clicking "
                "'Process Selected'."
            )
            return

        if not self.bridge.rizom_path:
            self.bridge.logger.error(
                "RizomUV not found. Install RizomUV and ensure it is on PATH, "
                "or set RizomUVBridge.rizom_path manually."
            )
            return

        if preset != self.SEND_PRESET:
            # TODO(blender-parity): round-trip presets aren't ported (see
            # list_template_modes/ROUND_TRIP_PRESETS); the combo already disables these entries
            # so this should be unreachable -- kept as a defensive guard.
            self.bridge.logger.error(
                f"'{preset}' is not yet supported by Blender's RizomUV bridge."
            )
            return

        self.bridge.logger.info(
            f"--- {preset} on {len(selection)} object(s) ---"
        )

        params = self.collect_param_values()
        try:
            with self.sb.progress(text=f"Working: RizomUV {preset}"):
                self.bridge.send(
                    selection,
                    load_uvs=params.get("LOAD_UVS", True),
                    load_uvw_props=params.get("LOAD_UVW_PROPS", True),
                    import_groups=params.get("IMPORT_GROUPS", True),
                    load_textures=params.get("LOAD_TEXTURES", True),
                )
        except Exception:
            import traceback

            self.bridge.logger.error(
                "Bridge raised:\n" + traceback.format_exc()
            )
            return

    # ------------------------------------------------------------------
    # Rizom-specific helpers (only the bits not covered by the base)
    # ------------------------------------------------------------------

    def open_uv_editor(self):
        """Open Blender's UV Editor in a new window.

        Blender-native analogue of mayatk's ``mel.eval("TextureViewWindow;")`` -- Blender has no
        floating editor windows, so this duplicates the current window and switches its area to
        the UV editor (``blendertk.ui_utils.open_editor``).
        """
        if btk.open_editor("UV Editor") is None:
            self.bridge.logger.error("Could not open the UV Editor.")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("rizom_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
