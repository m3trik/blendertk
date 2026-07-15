# !/usr/bin/python
# coding=utf-8
"""RizomUV bridge tool — co-located engine (`_rizom_bridge.RizomUVBridge`) + panel
(`rizom_bridge_slots.RizomBridgeSlots`) + `rizom_bridge.ui` + parameter registry
(`parameters.PARAMS`) + Lua recipes (`scripts/*.lua` + `templates/wrapper.lua`). Mirror of mayatk's
``uv_utils.rizom_bridge`` subpackage layout -- both the one-way **send** and the **round-trip**
presets (`pack` / `unwrap_hard` / `unwrap_organic` / `optimize`) are ported. Discovered by
``BlenderUiHandler`` (``marking_menu.show("rizom_bridge")``)."""
