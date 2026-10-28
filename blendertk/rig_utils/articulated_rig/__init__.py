# !/usr/bin/python
# coding=utf-8
"""Articulated Rig tool (Blender) -- mirror of mayatk's ``rig_utils/articulated_rig/``:
rig a prop of rigid parts on hinge, swivel, ball and slide joints (a desk lamp, a
magnifier arm, a boom).

``_articulated_rig.py`` is the engine (:class:`ArticulatedRig`): one armature whose
deform bones are the joints that ship. ``_solver_handler.py`` runs its end control's
solve -- blendertk's persistent handlers, where mayatk's ``_solver_expression.py``
writes a MEL expression -- and bakes it into the file at every save; ``grab_tool.py`` is
the viewport grab (:class:`ArticulatedRigGrab`); the panel is tentacle's
(``slots/tools/articulated_rig.py``, shared with Maya). The math -- the joint
model, the plan and the geometry rules -- is pythontk's (``ptk.ArticulationModel``,
``ptk.ArticulationPlan``, ``ptk.ArticulationAnalysis``), shared with mayatk and the Unity
and WebXR runtimes.
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_articulated_rig": ("ArticulatedRig",),
        "grab_tool": ("ArticulatedRigGrab",),
    },
)
