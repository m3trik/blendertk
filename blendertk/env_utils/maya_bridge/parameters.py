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

from typing import Any

from uitk.bridge import AttributeSpec, Formatters, Parameters as _BridgeParams

# Default VALUES live with the Qt-free engine so ``params_defaults()`` still answers
# where this module cannot be imported (a DCC running headless has no Qt); the specs
# below read them, so the two can never drift.
from blendertk.env_utils.maya_bridge._maya_bridge import DEFAULTS


# Templates are executable Maya Python -- substitute user values as Python source literals.
_FORMATTER = Formatters.python_literal


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    # Shared across every hand-off bridge (uitk owns the one spec);
    # resolved by the DCC bridge-slots base.
    "SCOPE": _BridgeParams.scope_spec(default=DEFAULTS["SCOPE"]),
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
    # Shared with the Maya-side pull panel (uitk owns the one spec): both run the
    # SAME rebuild, so the control must not exist on only one of them.
    "SHADER_TYPE": _BridgeParams.shader_type_spec(default=DEFAULTS["SHADER_TYPE"]),
    "APPLY_UNIT_SCALE": AttributeSpec(
        key="APPLY_UNIT_SCALE",
        label="Apply Unit Scale",
        kind="bool",
        default=DEFAULTS["APPLY_UNIT_SCALE"],
        tooltip=(
            "Bake Blender units (m) into the FBX so Maya reads the correct real-world size.\n"
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


class Parameters:
    """Parameters — module namespace."""

    #: The parameter registry, exposed on the class so a bridge slot can hand
    #: this class to the shared base as its ``params_module`` (the base reads
    #: ``params_module.PARAMS`` and ``.referenced_keys``) — no module-level shim.
    PARAMS = PARAMS

    @staticmethod
    def referenced_keys(script_text: str) -> "set[str]":
        """Registered keys present in *script_text* (delegates to uitk.bridge)."""
        return _BridgeParams.referenced_keys(script_text, PARAMS)

    @staticmethod
    def defaults() -> "dict[str, Any]":
        """Return ``{key: default}`` for every registered parameter."""
        return _BridgeParams.defaults(PARAMS)

    @staticmethod
    def render_context(values: "dict[str, Any]") -> "dict[str, str]":
        """Format *values* for ``StrUtils.replace_delimited`` using Python literals."""
        return _BridgeParams.render_context(values, PARAMS, formatter=_FORMATTER)
