# !/usr/bin/python
# coding=utf-8
"""Slots for the RizomUV bridge panel.

Mirror of mayatk's ``uv_utils.rizom_bridge.rizom_bridge_slots``. Subclass of uitk's
:class:`BridgeSlotsBase` directly (not through a Blender-flavored intermediate base) --
``REQUIRE_OUTPUT_DIR = False`` means it needs no DCC-side ``default_output_dir`` fallback, exactly
like ``blendertk.env_utils.maya_bridge.maya_bridge_slots.MayaBridgeSlots``. The panel machinery
(template combo, dynamic parameter widgets, user presets, log routing, per-template description)
lives upstream; this file owns only the Rizom-specific bits.

Two flows, matching mayatk:

* **pack / unwrap_hard / unwrap_organic / optimize** -- round-trip: Blender exports ``__RZTMP``
  copies, RizomUV runs the Lua preset headlessly, and the new UVs are transferred back onto the
  originals (:meth:`RizomUVBridge.process_with_rizomuv`).
* **send** -- one-way: exports the selection directly (no rename), optionally collects textures,
  then launches RizomUV detached; save manually inside RizomUV (:meth:`RizomUVBridge.send`).

Discovered by ``BlenderUiHandler`` (``marking_menu.show("rizom_bridge")``).
"""
from pathlib import Path

from uitk.bridge import BridgeSlotsBase

import blendertk as btk
from blendertk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge, _SCRIPT_DIR
from blendertk.uv_utils.rizom_bridge import parameters as _params


_PRESETS_ROOT = Path("blendertk/rizom_bridge")


class _VersionedParamsProxy:
    """Wraps the ``parameters`` module so ``referenced_keys`` is Rizom-version-aware.

    The base :meth:`BridgeSlotsBase._refresh_param_visibility` shows rows whose placeholder appears
    in the active template. For Rizom we need the panel to ALSO hide widgets gated above the
    installed Rizom version -- otherwise the user can dial knobs that get silently stripped from the
    script before send. Strips the lua first, then delegates. Everything except ``referenced_keys``
    falls through to the underlying module (``PARAMS`` / ``defaults`` / ``render_context`` /
    ``strip_unsupported``). Mirror of mayatk's proxy.
    """

    def __init__(self, slot, module):
        self._slot = slot
        self._mod = module

    def referenced_keys(self, script_text: str):
        version = self._slot.bridge.rizom_version
        return self._mod.referenced_keys(self._mod.strip_unsupported(script_text, version))

    def __getattr__(self, name):
        return getattr(self._mod, name)


class RizomBridgeSlots(BridgeSlotsBase):
    """Slots wired to ``rizom_bridge.ui`` via :class:`BridgeSlotsBase`.

    Discovered automatically by :class:`blendertk.ui_utils.blender_ui_handler.BlenderUiHandler`
    so ``self.sb.handlers.marking_menu.show("rizom_bridge")`` works from anywhere with no
    explicit registration.
    """

    UI_NAME = "rizom_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "rizom_bridge"

    # Rizom's recipes live under ``scripts/*.lua`` (send + the four round-trip presets). This
    # override is what lets ``_refresh_param_visibility`` find the ``__KEY__`` placeholders inside
    # each ``.lua`` body (the base defaults the extension to ``.py`` for marmoset/substance).
    TEMPLATE_EXTENSION = ".lua"

    # RizomUV round-trips through a temp FBX it manages internally -- there's no user-visible
    # artifact to point at, so skip the Output Dir row (mirrors mayatk).
    REQUIRE_OUTPUT_DIR = False

    # Narrower label column than the maya_bridge default (90px) -- Rizom's param labels are short
    # ("Spacing", "Mutations") so the tighter column hugs the values more cleanly.
    LABEL_MIN_WIDTH = 80

    # Rizom's header menu adds a UV-Editor shortcut and renames "Templates" to "Scripts"; the base
    # builds it from this data (handlers resolve to open_uv_editor + the base's
    # open_templates_folder / refresh_templates / clear_log).
    HEADER_MENU_ITEMS = (
        (
            "Open UV Editor", "btn_open_uv_editor",
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
        "body": "Round-trip selected meshes through RizomUV using a Lua preset, or one-way send "
        "them and continue working in RizomUV directly.",
        "steps": [
            "Select one or more mesh objects.",
            "Pick a <b>Lua preset</b> from the dropdown.",
            "Tweak the parameters that the preset exposes.",
            "Click <b>Process Selected</b>.",
        ],
        "sections": [
            ("Presets", [
                "<b>pack / unwrap_hard / unwrap_organic / optimize</b> -- round-trip: Blender "
                "exports <code>__RZTMP</code> copies, RizomUV runs the script headlessly, and the "
                "UVs are transferred back onto the originals.",
                "<b>send</b> -- one-way: exports the selection directly (no rename), optionally "
                "collects textures from the materials, then launches RizomUV detached. Save "
                "manually inside RizomUV when done.",
            ]),
            ("Header menu", [
                "<b>Open UV Editor</b> -- open a new window with Blender's UV Editor to inspect "
                "the result.",
                "<b>Open Scripts Folder</b> -- reveal the bundled Lua preset folder in Explorer.",
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
        return _VersionedParamsProxy(self, _params)

    @property
    def template_dir(self) -> Path:
        return _SCRIPT_DIR

    def make_bridge(self) -> RizomUVBridge:
        return RizomUVBridge()

    # Name of the pseudo-preset that runs the one-way send flow instead of the headless round-trip.
    SEND_PRESET = "send"

    def list_template_modes(self):
        """Return ``[(stem, ""), ...]`` for every bundled ``.lua`` script.

        Rizom has no per-template mode dimension, so ``mode=""`` is emitted for every entry (elides
        the parens in the combo label, matching mayatk)."""
        return [(p.stem, "") for p in sorted(_SCRIPT_DIR.glob("*.lua"))]

    # ------------------------------------------------------------------
    # b000 -- the per-bridge process action
    # ------------------------------------------------------------------

    def b000(self):
        """Run the chosen preset: round-trip, or one-way send when ``send`` is picked."""
        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning(
                "No preset chosen. Pick a Lua preset from the dropdown above."
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

        self.bridge.logger.info(f"--- {preset} on {len(selection)} object(s) ---")
        params = self.collect_param_values()
        try:
            with self.sb.progress(text=f"Working: RizomUV {preset}"):
                if preset == self.SEND_PRESET:
                    # One-way: open in RizomUV, no UV transfer back. Blender returns control
                    # immediately after RizomUV is launched.
                    self.bridge.send(
                        selection,
                        load_uvs=params.get("LOAD_UVS", True),
                        load_uvw_props=params.get("LOAD_UVW_PROPS", True),
                        import_groups=params.get("IMPORT_GROUPS", True),
                        load_textures=params.get("LOAD_TEXTURES", True),
                    )
                else:
                    self.bridge.process_with_rizomuv(
                        selection, preset=preset, params=params,
                    )
        except Exception:
            import traceback

            self.bridge.logger.error("Bridge raised:\n" + traceback.format_exc())
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
