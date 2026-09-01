# !/usr/bin/python
# coding=utf-8
"""Slots for the Substance Painter bridge panel -- mirror of mayatk's
``mat_utils.substance_bridge.substance_bridge_slots``.

Thin subclass of :class:`blendertk.ui_utils.blender_bridge_slots_base.BlenderBridgeSlotsBase`
(itself a :class:`uitk.bridge.BridgeSlotsBase`). The panel machinery lives upstream.
Substance-specific extras live below: the ``b000`` send action (FBX export + Painter handoff
with optional RPC dispatch).

Assigned-mesh textures (``PAINTER_INCLUDE_TEXTURES``) are staged by the bridge itself (walks
the selection's material node trees and copies the resolved textures into the FBX output
folder), then passed each one via ``--mesh-map`` on launch. The companion
``PAINTER_TEXTURE_AFFIX`` widget is greyed out while INCLUDE_TEXTURES is off.
"""

import traceback
from pathlib import Path

from uitk.bridge.spec import KindFactory
from blendertk.ui_utils.blender_bridge_slots_base import BlenderBridgeSlotsBase

from blendertk.mat_utils.substance_bridge._substance_bridge import (
    SubstanceBridge,
    _TEMPLATE_DIR,
)
from blendertk.mat_utils.substance_bridge import parameters as _params


_PRESETS_ROOT = Path("blendertk/substance_bridge")


class SubstanceBridgeSlots(BlenderBridgeSlotsBase):
    """Slots wired to ``substance_bridge.ui`` via :class:`BlenderBridgeSlotsBase`.

    Discovered automatically by :class:`blendertk.ui_utils.BlenderUiHandler` so
    ``self.sb.handlers.marking_menu.show("substance_bridge")`` works from anywhere with no
    explicit registration.
    """

    UI_NAME = "substance_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "substance_bridge"
    # Fall back to a self-cleaning temp folder when no .blend/workspace dir resolves
    # (unsaved file) — the FBX + staged maps are transient hand-off artifacts Painter
    # reads once, so the user shouldn't be forced to pick a path.
    TEMP_OUTPUT_FALLBACK = True

    # The Output Dir is a per-run hand-off location, not durable config: leaving
    # it blank resolves to the current .blend file's folder (or a temp dir), which is
    # right far more often than whatever was typed in some earlier session. A
    # restored path silently outranks that per-run default, so the field starts
    # blank each session -- the recent-values button still holds the history.
    OUTPUT_DIR_PERSISTS = False

    # Header = the base panel-level utilities only (Clear Log). Template
    # management lives on the template combo's own menu; the Bake Source set
    # actions are the BAKE_SOURCE_SET param row (parameters.py) -- the base
    # auto-wires its buttons to the same-named methods below. The set's
    # CONTENTS decide whether a send ships a bake source; there is no
    # companion checkbox.

    HELP_SPEC = {
        "title": "Substance Bridge",
        "body": "Send selected objects to Substance Painter. Blender exports "
        "the selection as FBX; the template's metadata constants "
        "(<i>BRIDGE_MODES</i>, <i>LAUNCH_ARGS</i>, <i>RPC_SCRIPT</i>, "
        "<i>BUILD_MANIFEST</i>, <i>FBX_OPTIONS</i>) drive the launch "
        "line and optional RPC step.",
        "steps": [
            "Set the <b>Output Dir</b> (or leave blank to use the "
            ".blend file's directory; an unsaved file falls back to a temp folder).",
            "Select one or more mesh objects.",
            "Pick a <b>Template + Mode</b> from the dropdown.",
            "Tweak the template's exposed parameters.",
            "Click <b>Send to Painter</b>.",
        ],
        "sections": [
            (
                "Modes",
                [
                    "<b>send_to</b> — launches Painter for interactive work.",
                    "<b>roundtrip</b> — launches Painter with remote "
                    "scripting, sends the template's JS body via "
                    "JSON-RPC, and waits for completion.",
                ],
            ),
        ],
        "notes": [
            "<b>reimport</b> overwrites the FBX from the last send and "
            "reloads it in the already-running Painter (never launches a "
            "new one). Needs the <i>substance_rpc</i> Painter plugin, "
            "installed automatically on send. <b>First-run:</b> activate "
            "it once in Painter — <i>Python > Reload Plugins Folder</i> "
            "(or relaunch Painter), then tick <i>substance_rpc</i> in the "
            "<i>Python</i> menu (Painter remembers it). Without a reachable "
            "Painter the log shows the manual reload steps.",
            "<b>Bake Source</b> — define the set once with <b>Set From "
            "Selection</b> and every send ships it as a companion "
            "<i>&lt;name&gt;_source.fbx</i>, set as Painter's <i>Hipoly "
            "Mesh</i> in the baking options. The set's contents ARE the "
            "switch: no set, nothing shipped. It lives in the file (a "
            "collection), so it survives saves and restarts and is "
            "independent of the <b>Scope</b>. Hidden geometry needs no "
            "preparation: FBX carries it verbatim, so the export never "
            "touches your scene. The row's icon buttons <i>select</i> the "
            "set's members or <i>clear</i> it.",
            "<b>Map Resolution</b> and the bake source have no Painter "
            "command line any more, so they travel over the "
            "<i>substance_rpc</i> plugin. On a project that is already open "
            "they apply at once; on a fresh launch the plugin holds them "
            "and applies them the moment the New Project wizard finishes — "
            "so the first send after Painter starts waits briefly for the "
            "plugin's endpoint.",
            "Add custom templates by dropping new files into the "
            "templates folder (use <code>__KEY__</code> tokens from "
            "<i>parameters.py</i> for tunable values), then use <b>Refresh "
            "Templates</b> on the template dropdown's menu.",
        ],
    }

    def __init__(self, switchboard):
        super().__init__(switchboard)
        self._wire_texture_affix_dependency()

    def _configure_output_dir_options(self, edit) -> None:
        """Base buttons (recent history + browse) plus a clear button.

        Blank is a *meaningful* state for this field -- ``require_output_dir``
        reads it as "use the .blend file folder, or a temp dir" -- so unlike a
        browse-only path field the clear button reaches a real setting rather
        than just breaking the value. Pairs with ``OUTPUT_DIR_PERSISTS = False``:
        clearing it is how the user gets back to the per-run default within a
        session, and the next session starts there anyway.
        """
        super()._configure_output_dir_options(edit)
        edit.option_box.clear_option = True

    #: Why ``Texture Affix`` greys out. Held as an attribute so the wording
    #: is one string rather than one per call site.
    _AFFIX_DISABLED_REASON = (
        "Only applies to textures the send stages — turn <b>Include Textures</b> on."
    )

    def _wire_texture_affix_dependency(self) -> None:
        """Grey out the ``Texture Affix`` field while ``Include Textures`` is off.

        The affix only renames files the staging step copies, so it means nothing
        with staging off. Routed through ``set_param_enabled`` rather than the
        widget's own ``setEnabled``: the affix row is a text field WRAPPED in an
        option box, so disabling the field alone leaves its two icon buttons (the
        mode cycle, the clear) live beside a greyed-out value -- and gives the row
        no reason for the state. The base walks the row instead, which covers the
        whole cell.

        Both widgets only exist when the active template references them (e.g.
        ``import.py``); the lookup gracefully no-ops otherwise so the panel stays
        usable on templates that omit either.
        """
        include_widget = self._param_widgets.get("PAINTER_INCLUDE_TEXTURES")
        if include_widget is None or (
            "PAINTER_TEXTURE_AFFIX" not in self._param_widgets
        ):
            return

        def _sync(_value=None):
            enabled = bool(KindFactory.read_value(include_widget))
            self.set_param_enabled(
                "PAINTER_TEXTURE_AFFIX",
                enabled,
                "" if enabled else self._AFFIX_DISABLED_REASON,
            )

        KindFactory.connect_changed(include_widget, _sync)
        _sync()

    # ------------------------------------------------------------------
    # Required base-class hooks
    # ------------------------------------------------------------------

    @property
    def params_module(self):
        return _params.Parameters

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> SubstanceBridge:
        return SubstanceBridge()

    def list_template_modes(self):
        return SubstanceBridge.list_template_modes()

    def select_initial_template_index(self, pairs):
        """Default the panel to ``import (send_to)`` when it's available."""
        pref = ("import", "send_to")
        return pairs.index(pref) if pref in pairs else 0

    # ------------------------------------------------------------------
    # b000 -- the per-bridge send action
    # ------------------------------------------------------------------

    def b000(self):
        """Process the selected objects with the chosen template + mode."""
        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning(
                "No template chosen. Pick one from the dropdown above."
            )
            return
        template, mode = pair

        # Templates that don't export FBX (e.g. ``render``) operate on
        # the project already loaded in Painter and don't need a Blender
        # selection.
        meta = SubstanceBridge.parse_template(_TEMPLATE_DIR / f"{template}.py")
        needs_selection = meta.get("EXPORT_FBX", True)

        # Scope resolves via the shared bridge-slots base. Warn only when this
        # template actually needs geometry -- ``render`` operates on the project
        # already open in Painter.
        params = self.collect_param_values()
        selection = self.scoped_objects(params, warn=needs_selection)
        if needs_selection and not selection:
            return

        if not self.bridge.painter_path:
            # The spec's sentence, not a panel-local copy: the engine's ``APP``
            # already owns it, and the tentacle gate that greys this tool's
            # entry shows the same one.
            self.bridge.logger.error(self.bridge.APP.not_found_message)
            return

        output_dir = self.require_output_dir()
        if output_dir is None:
            return

        self.bridge.logger.info(
            f"--- {template} ({mode}) on {len(selection)} object(s) ---"
        )

        try:
            with self.sb.progress(text=f"Working: Substance {template} ({mode})"):
                result = self.bridge.send(
                    objects=selection,
                    template=template,
                    mode=mode,
                    output_dir=output_dir,
                    params=params,
                )
        except Exception:
            self.bridge.logger.error("Bridge raised:\n" + traceback.format_exc())
            return

        if result is None:
            return  # logger already explained why


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("substance_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
