# !/usr/bin/python
# coding=utf-8
"""RizomUV bridge — engine (`_rizom_bridge.RizomUVBridge`) + parameter registry
(`parameters.PARAMS`) + Lua recipes (`scripts/*.lua` + `templates/wrapper.lua`). Mirror of mayatk's
``uv_utils.rizom_bridge`` subpackage layout -- both the one-way **send** and the **round-trip**
presets (`pack` / `unwrap_hard` / `unwrap_organic` / `optimize`) are ported. Its panel is
tentacle's shared RizomUV Bridge tool (``slots/tools/rizom_bridge.py``)."""
