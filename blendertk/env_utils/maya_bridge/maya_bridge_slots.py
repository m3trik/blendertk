# !/usr/bin/python
# coding=utf-8
"""Slots for the Maya bridge panel.

Subclass of uitk's :class:`BridgeSlotsBase` -- the panel machinery (template combo, dynamic
parameter widgets, user presets, log routing, per-template description) lives upstream; this file
owns only the Maya-specific bits: the bridge factory, the ``(template, mode)`` listing, the header
menu, and the ``b000`` send action. Counterpart of mayatk's ``blender_bridge_slots`` (which goes
through ``MayaBridgeSlotsBase``; blendertk subclasses ``BridgeSlotsBase`` directly since
``REQUIRE_OUTPUT_DIR = False`` means it needs no DCC-side ``default_output_dir``).

Discovered by ``BlenderUiHandler`` (``marking_menu.show("maya_bridge")``). The Qt-only imports
(``BridgeSlotsBase``, ``parameters``, ``fmt``) live here, not in the engine -- so the engine surface
still resolves under headless ``blender --background`` (no Qt). This module is only imported when
the handler loads the panel, which always happens under Qt.
"""
from pathlib import Path

from uitk.bridge import BridgeSlotsBase

import blendertk as btk
from blendertk.env_utils.maya_bridge._maya_bridge import (
    MayaBridge,
    _TEMPLATE_DIR,
    list_template_modes,
)
from blendertk.env_utils.maya_bridge import parameters as _params


_PRESETS_ROOT = Path("blendertk/maya_bridge")


class MayaBridgeSlots(BridgeSlotsBase):
    """Slots wired to ``maya_bridge.ui`` via :class:`BridgeSlotsBase`."""

    UI_NAME = "maya_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "maya_bridge"
    REQUIRE_OUTPUT_DIR = False

    # Uses the base's default header menu (Open Templates / Refresh / Clear
    # Log); only the help differs, so it's declared as data.
    HELP_SPEC = {
        "title": "Maya Bridge",
        "body": "Send the selected objects to a fresh Maya. Blender exports the selection as "
        "FBX; Maya runs the chosen import template with your parameter values substituted in.",
        "steps": [
            "Select one or more objects.",
            "Pick an <b>import template</b> from the dropdown.",
            "Tweak the template's exposed parameters.",
            "Click <b>Send to Maya</b>.",
        ],
        "sections": [
            ("Templates", [
                "<b>import</b> — import the FBX into the current scene.",
                "<b>import_and_frame</b> — import, select the new objects, frame them.",
                "<b>new_scene</b> — open a new Maya scene, then import (clean slate).",
            ]),
        ],
        "notes": [
            "Add custom templates by dropping new <code>.py</code> files into the templates "
            "folder (use <code>__KEY__</code> tokens from <i>parameters.py</i>), then click "
            "<b>Refresh Templates</b>.",
            "A fresh Maya is launched every time; your running Maya is never touched.",
        ],
    }

    # ------------------------------------------------------------------ base-class hooks
    @property
    def params_module(self):
        return _params

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> MayaBridge:
        return MayaBridge()

    def list_template_modes(self):
        return list_template_modes()

    # ------------------------------------------------------------------ b000 -- send
    def b000(self):
        """Send the selected objects to Maya with the chosen template."""
        selection = btk.selected_objects()
        if not selection:
            self.bridge.logger.warning(
                "Nothing selected. Select one or more objects before clicking 'Send to Maya'."
            )
            return

        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning("No template chosen. Pick one from the dropdown above.")
            return
        template, mode = pair

        if not self.bridge.maya_path:
            self.bridge.logger.error(
                "Maya not found. Install Maya or set $MAYA_EXE / MayaBridge.maya_path."
            )
            return

        self.bridge.logger.info(f"--- {template} ({mode}) on {len(selection)} object(s) ---")
        try:
            self.bridge.send(
                objects=selection,
                template=template,
                mode=mode,
                params=self.collect_param_values(),
            )
        except Exception:
            import traceback

            self.bridge.logger.error("Bridge raised:\n" + traceback.format_exc())


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("maya_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
