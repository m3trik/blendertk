# !/usr/bin/python
# coding=utf-8
"""Slots for the Marmoset Toolbag bridge panel -- mirror of mayatk's
``mat_utils.marmoset_bridge.marmoset_bridge_slots``.

Thin subclass of :class:`blendertk.ui_utils.blender_bridge_slots_base.BlenderBridgeSlotsBase` (which
itself subclasses uitk's :class:`BridgeSlotsBase`) -- the panel machinery (widget construction,
presets, log routing, Output Dir row with .blend-dir fallback, startup info, template
description) lives upstream. This file owns only Marmoset-specific bits, mirroring mayatk's
``MarmosetBridgeSlots``.
"""

import traceback
from pathlib import Path

from blendertk.ui_utils.blender_bridge_slots_base import BlenderBridgeSlotsBase

from blendertk.mat_utils.marmoset_bridge._marmoset_bridge import (
    MarmosetBridge,
    MarmosetEngine,
    SEND_TO,
    ROUND_TRIP,
    _TEMPLATE_DIR,
)
from blendertk.mat_utils.marmoset_bridge import parameters as _params


_PRESETS_ROOT = Path("blendertk/marmoset_bridge")


class MarmosetBridgeSlots(BlenderBridgeSlotsBase):
    """Slots wired to ``marmoset_bridge.ui`` via :class:`BlenderBridgeSlotsBase`.

    Discovered automatically by :class:`blendertk.ui_utils.BlenderUiHandler` so
    ``self.sb.handlers.marking_menu.show("marmoset_bridge")`` works from anywhere with no
    explicit registration.
    """

    UI_NAME = "marmoset_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "marmoset_bridge"
    # Fall back to a self-cleaning temp folder when no .blend/workspace dir resolves
    # (unsaved file) — the FBX + baked maps are transient hand-off artifacts Toolbag
    # reads once, so the user shouldn't be forced to pick a path.
    TEMP_OUTPUT_FALLBACK = True
    # A roundtrip runs Toolbag blocking and puts its only durable output (the
    # maps) beside the .blend, so its hand-off artifacts are intermediates of
    # the run itself. Left to the .blend-dir default they silted up the
    # project beside the scene file; with the field blank the bridge stages
    # them in a temp dir and removes it when the run succeeds. A folder named
    # here still wins -- and is still kept. Mirror of mayatk's slots.
    TRANSIENT_OUTPUT_MODES = (ROUND_TRIP,)

    HELP_SPEC = {
        "title": "Marmoset Bridge",
        "body": "Send selected objects to Marmoset Toolbag. Blender exports the "
        "selection as FBX with a <i>MatManifest</i> JSON sidecar; Toolbag runs "
        "the rendered template with your parameter values substituted in.",
        "steps": [
            "Set the <b>Output Dir</b> (or leave blank to use the .blend "
            "file's directory; an unsaved file falls back to a temp folder).",
            "Select one or more mesh objects.",
            "Pick a <b>Template + Mode</b> from the dropdown.",
            "Tweak the template's exposed parameters.",
            "Click <b>Send to Marmoset</b>.",
        ],
        "sections": [
            (
                "Modes",
                [
                    "<b>send_to</b> — opens Toolbag for interactive work.",
                    "<b>roundtrip</b> — runs Toolbag headless, then "
                    "re-surfaces generated maps as clickable links in the "
                    "log panel below. Only a bake with <b>Assign Material</b> "
                    "on changes the scene (see below).",
                ],
            ),
        ],
        "notes": [
            "For the <b>bake</b> template: select the bake <i>source</i> "
            "geometry once and use the <b>Bake Source</b> row's <b>Set From "
            "Selection</b> (the set saves with the .blend and is shared "
            "with the Substance bridge). Then select the bake <i>target</i> "
            "meshes and Send -- the source rides along automatically, hidden "
            "or not, and pairs explicitly, no name suffixes required. The "
            "Suffix rows grey out while the set exists; clear it to fall back "
            "to naming (with <b>Include Children</b> on, one suffixed parent "
            "tags every mesh under it).",
            "A <b>bake (roundtrip)</b> with <b>Assign Material</b> on wires "
            "the baked maps into one material per texture set -- restoring "
            "the source's packed-map layout -- and puts it in the slots where "
            "the source material was on the bake-target meshes. Re-baking "
            "<i>replaces</i> that material and its maps rather than stacking a "
            "new one beside it.",
            "Add custom templates by dropping new files into the "
            "templates folder (use <code>__KEY__</code> tokens from "
            "<i>parameters.py</i> for tunable values), then click "
            "<b>Refresh Templates</b> in the header menu.",
        ],
    }

    # ------------------------------------------------------------------
    # Bake Source set (the actions live on the shared base; this bridge adds
    # what the set means for ITS send -- mirror of mayatk's slots)
    # ------------------------------------------------------------------

    BAKE_SOURCE_DEFINED_NOTE = (
        "Bake sends now export it as the bake source "
        "(the Source/Target Suffix fallback is inactive while it exists)."
    )
    BAKE_SOURCE_CLEARED_NOTE = (
        "Pairing falls back to the Source/Target Suffix convention."
    )

    #: Rows that only apply when the file has NO Bake Source set -- the
    #: name-suffix pairing fallback. An explicit set classifies both sides
    #: outright, so these are greyed while one exists rather than sitting
    #: there implying they still steer the bake.
    SUFFIX_FALLBACK_KEYS = ("HIGH_SUFFIX", "LOW_SUFFIX", "SUFFIX_INCLUDE_CHILDREN")

    _SUFFIX_DISABLED_REASON = (
        "Inactive: this file has a Bake Source set, which pairs the two "
        "sides explicitly.\nThe name-suffix fallback applies only without "
        "one -- use the Bake Source row's Clear to fall back to it."
    )

    def _refresh_param_enablement(self) -> None:
        """Grey the suffix-fallback rows while a Bake Source set exists.

        The registry's own supersessions (Auto Maps / Auto cage) are applied by
        the base; this adds the one that keys off LIVE file state.
        """
        super()._refresh_param_enablement()
        try:
            has_set = self._bake_source_set().exists()
        except ImportError:  # no bpy: the panel is being built outside Blender
            has_set = False
        for key in self.SUFFIX_FALLBACK_KEYS:
            self.set_param_enabled(
                key, not has_set, self._SUFFIX_DISABLED_REASON if has_set else ""
            )

    # ------------------------------------------------------------------
    # Required base-class hooks
    # ------------------------------------------------------------------

    @property
    def params_module(self):
        return _params.Parameters

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> MarmosetBridge:
        return MarmosetBridge()

    def list_template_modes(self):
        return MarmosetEngine.list_template_modes()

    def select_initial_template_index(self, pairs):
        """Prefer 'bake (roundtrip)' then 'bake (send_to)', else first entry."""
        for pref in (("bake", ROUND_TRIP), ("bake", SEND_TO)):
            if pref in pairs:
                return pairs.index(pref)
        return 0

    # ------------------------------------------------------------------
    # b000 -- the per-bridge send action
    # ------------------------------------------------------------------

    def b000(self):
        """Process selected objects with the chosen template + mode."""
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

        if not self.bridge.toolbag_path:
            # The spec's sentence, not a panel-local copy: the engine's ``APP``
            # already owns it, and the tentacle gate that greys this tool's
            # entry shows the same one.
            self.bridge.logger.error(self.bridge.APP.not_found_message)
            return

        output_dir = self.require_output_dir(mode)
        if output_dir is None:
            return

        self.bridge.logger.info(
            f"--- {template} ({mode}) on {len(selection)} object(s) ---"
        )

        try:
            with self.sb.progress(text=f"Working: Marmoset {template} ({mode})"):
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

    ui = BlenderUiHandler.instance().get("marmoset_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
