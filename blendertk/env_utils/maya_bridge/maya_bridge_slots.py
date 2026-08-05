# !/usr/bin/python
# coding=utf-8
"""Slots for the Maya bridge panel.

Subclass of :class:`blendertk.ui_utils.blender_bridge_slots_base.BlenderBridgeSlotsBase` -- the
panel machinery (template combo, dynamic parameter widgets, user presets, log routing,
per-template description) lives upstream in uitk's ``BridgeSlotsBase``; this file owns only the
Maya-specific bits: the bridge factory, the ``(template, mode)`` listing, the header menu, and the
``b000`` send action. Counterpart of mayatk's ``blender_bridge_slots``, which goes through
``MayaBridgeSlotsBase`` the same way.

Going through the Blender-flavored base is load-bearing even though its ``default_output_dir``
fallback is moot here (``REQUIRE_OUTPUT_DIR = False``): the base also owns
``resolve_scope_objects``, so inheriting ``BridgeSlotsBase`` directly resolves every Scope to an
empty set and the send silently reports "nothing selected".

Discovered by ``BlenderUiHandler`` (``marking_menu.show("maya_bridge")``). The Qt-only imports
(``BridgeSlotsBase``, ``parameters``, ``fmt``) live here, not in the engine -- so the engine surface
still resolves under headless ``blender --background`` (no Qt). This module is only imported when
the handler loads the panel, which always happens under Qt.
"""

from pathlib import Path

from blendertk.ui_utils.blender_bridge_slots_base import BlenderBridgeSlotsBase

from blendertk.env_utils.maya_bridge._maya_bridge import MayaBridge, _TEMPLATE_DIR
from blendertk.env_utils.maya_bridge import parameters as _params


_PRESETS_ROOT = Path("blendertk/maya_bridge")


class MayaBridgeSlots(BlenderBridgeSlotsBase):
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
            "Toggle the import options (clear scene, frame in view, materials, …).",
            "Click <b>Send to Maya</b>.",
        ],
        "sections": [
            (
                "Options",
                [
                    "<b>Clear Scene First</b> — open a new Maya scene before importing (clean slate). "
                    "Off imports additively.",
                    "<b>Frame in View</b> — after import, select &amp; frame the new objects (viewFit).",
                ],
            ),
        ],
        "notes": [
            "One <b>import</b> template ships, exposing every option above; the dropdown also "
            "picks up custom templates you drop into the templates folder (use "
            "<code>__KEY__</code> tokens from <i>parameters.py</i>), then click "
            "<b>Refresh Templates</b>.",
            "A fresh Maya is launched every time; your running Maya is never touched.",
        ],
    }

    # ------------------------------------------------------------------ base-class hooks
    @property
    def params_module(self):
        return _params.Parameters

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> MayaBridge:
        return MayaBridge()

    def list_template_modes(self):
        return MayaBridge.list_template_modes()

    # ------------------------------------------------------------------ b000 -- send
    def b000(self):
        """Send the selected objects to Maya with the chosen template."""
        # Scope (Selected / Entire Scene / Visible Only) resolves via the shared
        # bridge-slots base; it logs the scope-aware reason when empty.
        params = self.collect_param_values()
        selection = self.scoped_objects(params)
        if not selection:
            return

        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning(
                "No template chosen. Pick one from the dropdown above."
            )
            return
        template, mode = pair

        if not self.bridge.maya_path:
            self.bridge.logger.error(
                "Maya not found. Install Maya or set $MAYA_EXE / MayaBridge.maya_path."
            )
            return

        self.bridge.logger.info(
            f"--- {template} ({mode}) on {len(selection)} object(s) ---"
        )
        try:
            self.bridge.send(
                objects=selection,
                template=template,
                mode=mode,
                params=params,
            )
        except Exception:
            import traceback

            self.bridge.logger.error("Bridge raised:\n" + traceback.format_exc())


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("maya_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
