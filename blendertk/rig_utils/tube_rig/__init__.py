# !/usr/bin/python
# coding=utf-8
"""Tube Rig tool (Blender) — mirror of mayatk's ``rig_utils/tube_rig/``.

``_tube_rig.py`` is the engine (:class:`TubeRig`), ``strategies.py`` the build
strategies, :class:`TubeRigBundle` and ``TUBE_STRATEGIES``, ``tube_path.py`` the
centerline geometry (:class:`TubePath`); ``tube_rig_slots.py`` + ``tube_rig.ui``
are the panel.
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_tube_rig": ("TubeRig",),
        "strategies": (
            "TubeRigBundle",
            "TubeStrategy",
            "FKChainStrategy",
            "SplineIKStrategy",
            "AnchorStrategy",
            "TUBE_STRATEGIES",
        ),
        "tube_path": ("TubePath",),
        "tube_rig_slots": ("TubeRigSlots",),
    },
)
