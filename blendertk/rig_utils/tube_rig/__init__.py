# !/usr/bin/python
# coding=utf-8
"""Tube Rig tool (Blender) — mirror of mayatk's ``rig_utils/tube_rig/``.

``_tube_rig.py`` is the engine (:class:`TubeRig`), ``strategies.py`` the build
strategies, :class:`TubeRigBundle`, ``TUBE_STRATEGIES`` and the panel's mode
table (``RIG_MODES``), ``tube_path.py`` the centerline geometry
(:class:`TubePath`). The panel is tentacle's ``slots/tools/tube_rig.py``.
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
            "RigModeConfig",
            "RIG_MODES",
        ),
        "tube_path": ("TubePath",),
    },
)
