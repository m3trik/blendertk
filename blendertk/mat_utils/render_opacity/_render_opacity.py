# !/usr/bin/python
# coding=utf-8
"""Deprecated import path -- ``RenderOpacity`` is :class:`RenderEffects`.

Mirror of mayatk's alias: the per-object opacity tool grew into the per-object
render-effects tool (``opacity`` first channel, ``highlight`` second) and moved
to :mod:`blendertk.mat_utils.render_opacity.render_effects`. Held for ONE
release; import ``RenderEffects`` for new code.
"""

from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

RenderOpacity = RenderEffects

__all__ = ["RenderOpacity", "RenderEffects"]
