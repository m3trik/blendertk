# !/usr/bin/python
# coding=utf-8
"""Registry of user-tunable RizomUV parameters exposed to the bridge UI.

Mirror of mayatk's ``uv_utils.rizom_bridge.parameters`` -- same ``uitk.bridge`` machinery
(``AttributeSpec`` / ``lua_literal`` / placeholder substitution), scoped to the subset this port
actually drives: the one-way **send** load options (``LOAD_UVS`` / ``LOAD_UVW_PROPS`` /
``IMPORT_GROUPS`` / ``LOAD_TEXTURES``). mayatk additionally registers ~15 pack/unfold
(``ZomPack`` / ``ZomUnfold`` / ``ZomOptimize``) parameters that back its round-trip presets
(``pack`` / ``unwrap_hard`` / ``unwrap_organic`` / ``optimize``) -- that pipeline isn't ported to
Blender yet (see ``rizom_bridge_slots.RizomBridgeSlots``, which lists those presets in the combo
for structural parity but disables them), so their params aren't registered here either -- building
widgets for knobs that can never take effect would be dead UI.
TODO(blender-parity): once the round-trip pipeline is ported, pull the pack/unfold ``AttributeSpec``
entries back in verbatim from ``mayatk/mayatk/uv_utils/rizom_bridge/parameters.py``.

Each entry maps a Lua placeholder token (e.g. ``__LOAD_UVS__``) to a widget spec. The panel scans
the active preset's placeholder-discovery file (``scripts/send.lua``) for these tokens and shows
only the matching widgets, then substitutes the user's values before sending to RizomUV.
"""
from __future__ import annotations

from typing import Any

from uitk.bridge import (
    AttributeSpec,
    lua_literal,
    referenced_keys as _refkeys,
    defaults as _defaults,
    render_context as _render_context,
)


# Targets Lua scripts -- ``lua_literal`` produces lowercase ``true``/``false`` and bare numeric /
# string literals suitable for inlining into ``scripts/send.lua``-style bodies.
_FORMATTER = lua_literal


# Display order is iteration order over this dict -- matches mayatk's LOAD_UVS / LOAD_UVW_PROPS /
# IMPORT_GROUPS / LOAD_TEXTURES ordering.
PARAMS: "dict[str, AttributeSpec]" = {
    "LOAD_UVS": AttributeSpec(
        key="LOAD_UVS",
        label="Load UVs",
        kind="bool",
        default=True,
        tooltip=(
            "Load existing UVs along with positions (XYZUVW=true).\n"
            "Off = load positions only; RizomUV starts from a clean slate."
        ),
    ),
    "LOAD_UVW_PROPS": AttributeSpec(
        key="LOAD_UVW_PROPS",
        label="Load UVW Props",
        kind="bool",
        default=True,
        tooltip=(
            "Preserve UV-side metadata: seam/cut edges, pinned vertices,\n"
            "groups, and selection state. Off = mesh only, no metadata."
        ),
    ),
    "IMPORT_GROUPS": AttributeSpec(
        key="IMPORT_GROUPS",
        label="Import Groups",
        kind="bool",
        default=True,
        tooltip=(
            "Keep each object as a separate RizomUV island group.\n"
            "Off = every mesh imports as a flat list."
        ),
    ),
    "LOAD_TEXTURES": AttributeSpec(
        key="LOAD_TEXTURES",
        label="Load Textures",
        kind="bool",
        default=True,
        tooltip=(
            "Auto-collect file textures from the selection's materials and bind them in\n"
            "RizomUV (ZomLoadTexture) so they show on the model in the 3D view.\n"
            "Off = open with no textures."
        ),
    ),
}


def referenced_keys(script_text: str) -> "set[str]":
    """Registered keys present in *script_text* (delegates to uitk.bridge)."""
    return _refkeys(script_text, PARAMS)


def defaults() -> "dict[str, Any]":
    """Return ``{key: default}`` for every registered parameter."""
    return _defaults(PARAMS)


def render_context(values: "dict[str, Any]") -> "dict[str, str]":
    """Format *values* for placeholder substitution using Lua literals."""
    return _render_context(values, PARAMS, formatter=_FORMATTER)
