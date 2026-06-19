# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable Maya-bridge parameters exposed to the panel.

Each entry maps a placeholder token (e.g. ``__FRAME_VIEW__``) to a widget spec. The slot scans the
selected template for these tokens, shows only the matching widgets, and substitutes the user
values into the template before launching Maya (via :func:`StrUtils.replace_delimited`).

Export-affecting knobs (``INCLUDE_MATERIALS`` / ``EMBED_TEXTURES`` / ``APPLY_UNIT_SCALE`` /
``INCLUDE_ANIMATION`` / ``TRIANGULATE``) are read by :class:`MayaBridge` to configure the
Blender-side FBX export; import-affecting knobs (``FRAME_VIEW``) are substituted into the Maya
import template. Each template references the subset it exposes.

Counterpart of :mod:`mayatk.env_utils.blender_bridge.parameters` (the Maya->Blender direction).

NOTE: ``uitk.bridge`` (Qt) is imported at module top -- this module is only imported by the slots
(which already require Qt). The engine (:mod:`_maya_bridge`) defers its ``parameters`` import into
call bodies so the engine surface still resolves under headless ``blender --background`` (no Qt).
"""
from __future__ import annotations

from typing import Any

from uitk.bridge import (
    AttributeSpec,
    python_literal,
    referenced_keys as _refkeys,
    defaults as _defaults,
    render_context as _render_context,
)


# Templates are executable Maya Python -- substitute user values as Python source literals.
_FORMATTER = python_literal


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    "INCLUDE_MATERIALS": AttributeSpec(
        key="INCLUDE_MATERIALS",
        label="Include Materials",
        kind="bool",
        default=True,
        tooltip=(
            "Carry materials/shading across. When off, the selection is exported with its material\n"
            "slots cleared (geometry only)."
        ),
    ),
    "EMBED_TEXTURES": AttributeSpec(
        key="EMBED_TEXTURES",
        label="Embed Textures",
        kind="bool",
        default=True,
        tooltip="Copy the texture files alongside the FBX so Maya resolves the maps.",
    ),
    "APPLY_UNIT_SCALE": AttributeSpec(
        key="APPLY_UNIT_SCALE",
        label="Apply Unit Scale",
        kind="bool",
        default=True,
        tooltip=(
            "Bake Blender units (m) into the FBX so Maya reads the correct real-world size.\n"
            "Off preserves the raw numeric values."
        ),
    ),
    "INCLUDE_ANIMATION": AttributeSpec(
        key="INCLUDE_ANIMATION",
        label="Include Animation",
        kind="bool",
        default=False,
        tooltip="Bake & export keyframes (off = static mesh hand-off).",
    ),
    "TRIANGULATE": AttributeSpec(
        key="TRIANGULATE",
        label="Triangulate",
        kind="bool",
        default=False,
        tooltip="Triangulate meshes on export.",
    ),
    "FRAME_VIEW": AttributeSpec(
        key="FRAME_VIEW",
        label="Frame in View",
        kind="bool",
        default=True,
        tooltip="After import, frame the imported objects in Maya's viewport (viewFit).",
    ),
}


def referenced_keys(script_text: str) -> "set[str]":
    """Registered keys present in *script_text* (delegates to uitk.bridge)."""
    return _refkeys(script_text, PARAMS)


def defaults() -> "dict[str, Any]":
    """Return ``{key: default}`` for every registered parameter."""
    return _defaults(PARAMS)


def render_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for ``StrUtils.replace_delimited`` using Python literals."""
    return _render_context(values, PARAMS, formatter=_FORMATTER)
