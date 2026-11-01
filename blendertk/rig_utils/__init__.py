# !/usr/bin/python
# coding=utf-8
"""Rigging utilities — Blender port of mayatk's ``rig_utils``.

Mirrors mayatk's procedural-rig tools (telescope / wheel / tube / shadow /
articulated). Each rig is an engine module (or subpackage) here; its panel is tentacle's
shared ``slots/tools/<rig>.py``, opened from the tentacle rigging menu via
``marking_menu.show("<rig>")`` — the same split as mayatk's. Maya's joint/skinCluster
machinery has no faithful Blender analogue (Armature + vertex groups, handled in the rigging slot),
so only the DCC-agnostic procedural rigs live here.

Public engines are registered via the root ``DEFAULT_INCLUDE``; this file is a docstring-only
package marker (no import side effects).
"""
