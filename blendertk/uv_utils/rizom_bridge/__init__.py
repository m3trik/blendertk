# !/usr/bin/python
# coding=utf-8
"""RizomUV bridge tool — co-located engine (`_rizom_bridge.RizomUVBridge`) + panel
(`rizom_bridge_slots.RizomBridgeSlots`) + `rizom_bridge.ui` + parameter registry
(`parameters.PARAMS`) + placeholder-discovery scripts (`scripts/*.lua`). Mirror of mayatk's
``uv_utils.rizom_bridge`` subpackage layout -- the round-trip presets (`pack` / `unwrap_hard` /
`unwrap_organic` / `optimize`) aren't ported yet; see `rizom_bridge_slots` for the gap. Discovered
by ``BlenderUiHandler`` (``marking_menu.show("rizom_bridge")``)."""
