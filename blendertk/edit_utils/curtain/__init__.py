# !/usr/bin/python
# coding=utf-8
"""Curtain tool — procedural draped cloth (Blender).

Mirror of mayatk's ``edit_utils/curtain/``: ``_curtain.py`` is the engine
(:class:`Rail`, :class:`CurtainMesh`, :class:`CurtainRig`; ``CurtainUtils`` is
the retired entry point, until 0.14.0) over the vendored drape math in
``_curtain_drape.py`` (code-identical with mayatk's copy); ``curtain_slots.py``
+ ``curtain.ui`` are the panel; ``presets/`` holds the shipped presets.
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_curtain": ("Rail", "CurtainMesh", "CurtainRig", "CurtainUtils"),
        "curtain_slots": ("CurtainSlots",),
    },
)
