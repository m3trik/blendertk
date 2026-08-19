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
``PAINTER_TEXTURE_PREFIX`` widget is greyed out while INCLUDE_TEXTURES is off.
"""

import traceback
from pathlib import Path

from uitk.bridge.spec import KindFactory
from uitk.widgets.mixins.tooltip_mixin import TooltipFormat
from blendertk.ui_utils.blender_bridge_slots_base import BlenderBridgeSlotsBase

from blendertk.mat_utils.substance_bridge._substance_bridge import (
    HighPolySet,
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

    # Header = the base panel-level utilities only (Clear Log). Template
    # management lives on the template combo's own menu; the Bake Source set
    # actions are the BAKE_SOURCE_SET param row (parameters.py) -- the base
    # auto-wires its buttons to the same-named methods below. The
    # ``PAINTER_HIGH_POLY`` checkbox only decides whether to ship the set.

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
            "<b>Export Bake Source</b> ships a companion "
            "<i>&lt;name&gt;_source.fbx</i> and sets it as Painter's "
            "<i>Hipoly Mesh</i> in the baking options. Define the set once "
            "with the <b>Bake Source</b> row's <b>Set From Selection</b> — "
            "it lives in the file (a collection), so it survives saves and "
            "restarts and is independent of the <b>Scope</b>. Hidden "
            "geometry needs no preparation: FBX carries it verbatim, so the "
            "export never touches your scene.",
            "<b>Map Resolution</b> and <b>Export Bake Source</b> have no "
            "Painter command line any more, so they travel over the "
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
        self._wire_texture_prefix_dependency()

    def _wire_texture_prefix_dependency(self) -> None:
        """Grey out the ``Texture Prefix`` field while ``Include Textures`` is off.

        Both widgets only exist when the active template references them (e.g.
        ``import.py``); the lookup gracefully no-ops otherwise so the panel stays usable on
        templates that omit either knob.
        """
        include_widget = self._param_widgets.get("PAINTER_INCLUDE_TEXTURES")
        prefix_widget = self._param_widgets.get("PAINTER_TEXTURE_PREFIX")
        if include_widget is None or prefix_widget is None:
            return

        def _sync(_value=None):
            prefix_widget.setEnabled(bool(KindFactory.read_value(include_widget)))

        KindFactory.connect_changed(include_widget, _sync)
        _sync()

    # ------------------------------------------------------------------
    # Bake Source set (param-row actions)
    # ------------------------------------------------------------------

    def live_param_tooltips(self):
        """Make the Bake Source row report the file's CURRENT members.

        The set is a stamped Collection in the .blend, not panel state, so it
        moves under an open panel -- a new file, a redefine, an unlink in the
        Outliner. A build-time tooltip would describe the set the panel opened
        on, which is exactly the case the user is trying to check. Mirrors
        ``MayaBridgeSlotsBase.live_param_tooltips``, including its extend-don't-
        replace contract: the hook is a registry, so a subclass that has to
        remember to merge is one that will forget.
        """
        tips = dict(super().live_param_tooltips() or {})
        tips["BAKE_SOURCE_SET"] = self._bake_source_tooltip
        return tips

    def _bake_source_tooltip(self) -> str:
        """The Bake Source row's static tooltip plus its live member list."""
        spec = self.params_module.PARAMS["BAKE_SOURCE_SET"]
        static = self.format_param_tooltip(spec)
        try:
            members = HighPolySet.members()
        except Exception:  # noqa: BLE001 -- a tooltip must never raise into Qt
            return static
        return static + TooltipFormat.stored_items(
            members,
            formatter=lambda o: o.name,
            noun="object(s) in this file's set",
            empty_text="No bake source defined in this file.",
        )

    def set_bake_source_from_selection(self) -> None:
        """Store the current selection as this file's high-poly bake source.

        Ticks ``Export High Poly`` on success -- defining the set is only
        ever done in order to ship it, so making the user find the checkbox
        afterwards would be a pure extra step.
        """
        members = HighPolySet.define()
        if not members:
            self.bridge.logger.warning(
                "Nothing selected; the high-poly set was cleared."
            )
            return
        self.bridge.logger.info(
            f"High-poly set: {len(members)} object(s) -> {HighPolySet.SET_NAME}"
        )
        widget = self._param_widgets.get("PAINTER_HIGH_POLY")
        if widget is not None:
            KindFactory.set_value(widget, True)

    def select_bake_source(self) -> None:
        """Select the high-poly set's members.

        Members outside the active view layer (an excluded collection) can't
        be selected at all -- ``select_set`` raises there -- and one whose
        ``hide_select`` is on silently refuses. Both are reported rather than
        forced: unhiding geometry behind the user's back to satisfy a
        *select* action would be the one thing this feature promises not to
        do. The export itself doesn't care either way.
        """
        import bpy

        members = HighPolySet.members()
        if not members:
            self.bridge.logger.warning("This file has no high-poly set.")
            return
        bpy.ops.object.select_all(action="DESELECT")
        selected = []
        for obj in members:
            try:
                obj.select_set(True)
            except RuntimeError:  # not in the active view layer
                continue
            if obj.select_get():
                selected.append(obj)
        if selected:
            bpy.context.view_layer.objects.active = selected[0]
        unreachable = len(members) - len(selected)
        self.bridge.logger.info(
            f"Selected {len(selected)} high-poly object(s)."
            + (
                f" {unreachable} could not be selected (hidden from selection "
                "or outside the active view layer); they still export."
                if unreachable
                else ""
            )
        )

    def clear_bake_source(self) -> None:
        """Remove the high-poly collection; its objects are left alone."""
        if not HighPolySet.exists():
            self.bridge.logger.warning("This file has no high-poly set.")
            return
        HighPolySet.clear()
        self.bridge.logger.info("High-poly set cleared.")

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
