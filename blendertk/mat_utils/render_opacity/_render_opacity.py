# !/usr/bin/python
# coding=utf-8
"""Deprecated import path -- ``RenderOpacity`` is :class:`RenderEffects`.

Mirror of mayatk's alias: the per-object opacity tool grew into the per-object
render-effects tool (``opacity`` first channel, ``highlight`` second) and moved
to :mod:`blendertk.mat_utils.render_opacity.render_effects`. Reaching
``RenderOpacity`` here -- or as ``btk.RenderOpacity`` -- still returns that
class, but warns through ``ptk.Deprecation.attributes`` and stops working in
blendertk 0.11.0 (it resolved silently from 2026-09-05 to 2026-09-21). Import
``RenderEffects``.
"""

import pythontk as ptk

ptk.Deprecation.attributes(
    globals(),
    {
        "RenderOpacity": (
            "blendertk.mat_utils.render_opacity.render_effects.RenderEffects"
        )
    },
    remove_in="0.11.0",
)
