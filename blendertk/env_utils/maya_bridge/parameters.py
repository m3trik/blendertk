# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable Maya-bridge parameters exposed to the panel.

Each entry maps a placeholder token (e.g. ``__FRAME_VIEW__``) to a widget spec. The slot scans the
selected template for these tokens, shows only the matching widgets, and substitutes the user
values into the template before launching Maya (via :func:`StrUtils.replace_delimited`).

Export-affecting knobs (``INCLUDE_MATERIALS`` / ``EMBED_TEXTURES`` / ``APPLY_UNIT_SCALE`` /
``INCLUDE_ANIMATION`` / ``TRIANGULATE``) are read by :class:`MayaBridge` to configure the
Blender-side FBX export; import-affecting knobs (``CLEAR_SCENE`` / ``FRAME_VIEW``) are substituted
into the Maya import template. Each template references the subset it exposes.

Counterpart of :mod:`mayatk.env_utils.blender_bridge.parameters` (the Maya->Blender direction).

NOTE: ``uitk.bridge`` (Qt) is imported at module top -- this module is only imported by the slots
(which already require Qt). The engine (:mod:`_maya_bridge`) defers its ``parameters`` import into
call bodies so the engine surface still resolves under headless ``blender --background`` (no Qt).
"""

from __future__ import annotations

from uitk.bridge import AttributeSpec, ParamRegistry, Parameters as _BridgeParams

# Default VALUES live with the Qt-free engine so ``params_defaults()`` still answers
# where this module cannot be imported (a DCC running headless has no Qt); the specs
# below read them, so the two can never drift.
from blendertk.env_utils.maya_bridge._maya_bridge import DEFAULTS


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    # Shared across every hand-off bridge (uitk owns the one spec);
    # resolved by the DCC bridge-slots base.
    "SCOPE": _BridgeParams.scope_spec(default=DEFAULTS["SCOPE"]),
    "CARRIER": _BridgeParams.carrier_spec(default=DEFAULTS["CARRIER"]),
    "INCLUDE_MATERIALS": AttributeSpec(
        key="INCLUDE_MATERIALS",
        label="Include Materials",
        kind="bool",
        default=DEFAULTS["INCLUDE_MATERIALS"],
        tooltip=(
            "Carry materials/shading across. When off, the selection is exported with its material\n"
            "slots cleared (geometry only)."
        ),
    ),
    "EMBED_TEXTURES": AttributeSpec(
        key="EMBED_TEXTURES",
        label="Embed Textures",
        kind="bool",
        default=DEFAULTS["EMBED_TEXTURES"],
        tooltip="Copy the texture files alongside the FBX so Maya resolves the maps.",
    ),
    # Which Maya shader the rebuild targets. The values are mayatk.GameShader's own
    # (Maya's vocabulary, so it lives with the bridge that targets Maya, not in
    # uitk); the Maya-side pull takes the same choice as an ``import_scene``
    # keyword. Stingray leads: the only family that DECLARES its texture slots, so a
    # material round-trips back out with its maps intact.
    "SHADER_TYPE": AttributeSpec(
        key="SHADER_TYPE",
        label="Rebuild Shader",
        kind="choice",
        default=DEFAULTS["SHADER_TYPE"],
        choices=[
            ("Stingray PBS", "stingray"),
            ("Standard Surface", "standard_surface"),
            ("OpenPBR Surface", "open_pbr"),
        ],
        tooltip=(
            "Which Maya shader the materials are rebuilt as:\n"
            "\u2022 Stingray PBS \u2014 the game shader; its declared texture slots\n"
            "  round-trip back out intact. Needs the shaderFX plugin.\n"
            "\u2022 Standard Surface \u2014 renders anywhere, no plugin needed.\n"
            "\u2022 OpenPBR Surface \u2014 the open PBR standard; needs a recent Maya 2025+.\n"
            "A type this Maya cannot build falls back to Standard Surface."
        ),
    ),
    "APPLY_UNIT_SCALE": AttributeSpec(
        key="APPLY_UNIT_SCALE",
        label="Apply Unit Scale",
        kind="bool",
        default=DEFAULTS["APPLY_UNIT_SCALE"],
        tooltip=(
            "Bake Blender units (m) into the payload so Maya reads the correct real-world\n"
            "size (FBX: apply_unit_scale; USD: the layer is written in centimeters).\n"
            "Off preserves the raw numeric values."
        ),
    ),
    "INCLUDE_ANIMATION": AttributeSpec(
        key="INCLUDE_ANIMATION",
        label="Include Animation",
        kind="bool",
        default=DEFAULTS["INCLUDE_ANIMATION"],
        tooltip="Bake & export keyframes (off = static mesh hand-off).",
    ),
    "INCLUDE_SCENE_DATA": AttributeSpec(
        key="INCLUDE_SCENE_DATA",
        label="Include Scene Data",
        kind="bool",
        default=DEFAULTS["INCLUDE_SCENE_DATA"],
        tooltip=(
            "Carry the scene's tool data across -- every record that means the same in\n"
            "Maya: the Shot Sequencer's shots (names, ranges, descriptions,\n"
            "memberships, markers, locked gaps and the samples planted on shot bounds)\n"
            "and the Emissive Groups (slots, defaults, face membership) -- rebuilt 1:1 on\n"
            "the far side.\n\n"
            "Neither FBX nor USD can hold them, so they travel as data beside the file\n"
            "(the same manifest the materials ride). Memberships are scoped to what is\n"
            "sent; the shots themselves always cross. A scene that already has shots or\n"
            "groups gains the sent ones beside its own instead of losing them, and\n"
            "anything renamed or re-slotted on the way is named in the log.\n\n"
            "Turn it off for a pure asset hand-off, or when the scene's data is not the\n"
            "receiving scene's business."
        ),
    ),
    "TRIANGULATE": AttributeSpec(
        key="TRIANGULATE",
        label="Triangulate",
        kind="bool",
        default=DEFAULTS["TRIANGULATE"],
        tooltip="Triangulate meshes on export.",
    ),
    "CLEAR_SCENE": AttributeSpec(
        key="CLEAR_SCENE",
        label="Clear Scene First",
        kind="bool",
        default=DEFAULTS["CLEAR_SCENE"],
        tooltip=(
            "Open a new (empty) Maya scene before importing (clean-slate hand-off). Off imports\n"
            "additively into the current scene."
        ),
    ),
    "FRAME_VIEW": AttributeSpec(
        key="FRAME_VIEW",
        label="Frame in View",
        kind="bool",
        # Off by default so the unified template's default behavior matches the old plain
        # "import" template (no selection change / no viewFit); opt in for the old
        # "import_and_frame" behavior.
        default=DEFAULTS["FRAME_VIEW"],
        tooltip=(
            "After import, select the new top-level objects and frame them in Maya's viewport\n"
            "(viewFit)."
        ),
    ),
}


class Parameters(ParamRegistry):
    """Parameters — module namespace.

    Declared as data: :class:`uitk.bridge.ParamRegistry` supplies
    ``referenced_keys`` / ``defaults`` / ``render_context`` (Python literals)
    over :data:`PARAMS`, and a bridge slot hands this class to the shared base
    as its ``params_module``.
    """

    PARAMS = PARAMS
