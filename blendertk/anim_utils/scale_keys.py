# !/usr/bin/python
# coding=utf-8
"""Dedicated scale-keys module to keep AnimUtils lean and testable (mirror of mayatk's
``anim_utils.scale_keys`` / ``ScaleKeys``).

Blender key timing is plain ``keyframe_points`` math, so mayatk's heavy segment / overlap /
speed-retime machinery has little benefit here — this mirrors the **module + class name**
(``ScaleKeys``) but keeps the body thin. The shared fcurve helpers live in ``_anim_utils``;
they are imported lazily inside the call body to avoid an import cycle (``_anim_utils``
re-imports ``scale_keys`` so ``AnimUtils.scale_keys`` / ``btk.scale_keys`` keep resolving).
"""


def scale_keys(objects, factor, pivot=None):
    """Scale key times by ``factor`` about ``pivot`` (defaults to each action's first key)."""
    from blendertk.anim_utils._anim_utils import _actions, _slot_fcurves, _key_range

    for action, slot in _actions(objects):
        fcurves = _slot_fcurves(action, slot)
        rng = _key_range(fcurves)
        if not rng:
            continue
        p = rng[0] if pivot is None else pivot
        for fc in fcurves:
            for k in fc.keyframe_points:
                k.co.x = p + (k.co.x - p) * factor
                k.handle_left.x = p + (k.handle_left.x - p) * factor
                k.handle_right.x = p + (k.handle_right.x - p) * factor
            fc.update()


class ScaleKeys:
    """Namespace mirror of mayatk's ``ScaleKeys`` (``scale_keys`` also exposed module-level)."""

    scale_keys = staticmethod(scale_keys)
