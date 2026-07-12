# !/usr/bin/python
# coding=utf-8
"""Blender Shot Manifest — DCC layer over pythontk's manifest engine.

Mirror of mayatk's ``anim_utils.shots.shot_manifest``.  The CSV/mapping/behaviors/
range/planning core is shared upstream (``pythontk.core_utils.engines.shots.manifest``);
this package holds the Blender adapter (:mod:`_shot_manifest` — ``BlenderShotManifest``)
and the co-located Manifest panel (discovered by ``BlenderUiHandler``).

Public surface is registered via the root ``DEFAULT_INCLUDE``; this ``__init__`` is
docstring-only per the no-import-side-effects rule.
"""
